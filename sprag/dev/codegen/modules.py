"""Compile a SPRAG ``Module`` Python class into its emitted JS source.

A SPRAG ``Module`` is a Ragot ``Module`` subclass authored in Python. The
canonical surface is **imperative** — ``self.on``, ``self.listen``,
``self.delegate``, ``self.timeout``, ``self.set_state`` — written exactly
the same way it would be written in Ragot JS, and routed through the
expression compiler.

The codegen here only synthesises framework prologue code that **cannot**
be expressed at the call site:

  - ``ref()`` descriptors → ``this.refs.X = this.element.querySelector(...)``
    captures emitted into ``onStart`` so handler bodies can use them.
  - ``@infinite_scroll(...)`` → ``createInfiniteScroll(this, ...)`` install
    emitted into ``onStart`` so the host owns the cleanup.
  - ``@debounce(seconds)`` / ``@throttle(seconds)`` → method-body wraps that
    install state-tracking + auto-cancelling timer guards. The decorator
    converts seconds → ms at decoration time; this module emits the ms
    value verbatim.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from pathlib import Path

from ...runtime.env import env as sprag_env
from ...runtime.env import public_env as sprag_public_env
from .diagnostics import lint_browser_method
from .expressions import _compile_expr  # noqa: F401  (re-export for tests)
from .dependencies import used_browser_class_refs, used_js_import_aliases
from .imports import _detect_ragot_imports
from .mappings import JSCodegenError, _map_name
from .module_helpers import (
    check_helper_name_collisions,
    collect_module_helpers,
    compile_module_helpers_prelude,
    referenced_helper_names_in_class,
    select_used_helpers,
)
from .source_maps import GeneratedArtifact, GeneratedLineMapping, build_source_map, count_lines, mappings_for_text
from .statements import _compile_statements, _compile_statements_with_mappings
from .stores_scan import collect_store_refs_for_class


_SERVER_ONLY_SYMBOLS = {
    "Cache",
    "Controller",
    "Handler",
    "ManagedProcess",
    "Model",
    "Operation",
    "OperationError",
    "QueueService",
    "Router",
    "Service",
    "ServiceManager",
    "SocketIngress",
    "Store",
    "Watcher",
    "WatcherError",
    "boot",
    "bus",
    "create_cache",
    "create_model",
    "create_store",
    "expect_json",
    "json_endpoint",
    "registry",
    "require_fields",
    "route",
    "start_process",
}

_ENV_HELPER_PREAMBLE = """const __SPRAG_ENV_MISSING__ = Symbol('sprag.env.missing');

function __spragPublicEnv() {
    return (typeof window !== 'undefined' && window.__SPRAG_ENV__) || {};
}

function __spragEnv(name, fallback = __SPRAG_ENV_MISSING__, options = {}) {
    const source = __spragPublicEnv();
    const hasValue = Object.prototype.hasOwnProperty.call(source, name);
    if (!hasValue) {
        if (options.required || fallback === __SPRAG_ENV_MISSING__) {
            throw new Error(`[SPRAG] Missing public environment variable "${name}".`);
        }
        return fallback;
    }

    const raw = source[name];
    const cast = options.cast || null;
    if (cast === null || cast === 'str') {
        return raw;
    }
    if (cast === 'bool') {
        const normalized = String(raw).trim().toLowerCase();
        if (['1', 'true', 'yes', 'on'].includes(normalized)) return true;
        if (['0', 'false', 'no', 'off'].includes(normalized)) return false;
        throw new Error(`[SPRAG] Could not cast public env "${name}" to bool.`);
    }
    if (cast === 'int') {
        const value = Number(raw);
        if (!Number.isFinite(value)) {
            throw new Error(`[SPRAG] Could not cast public env "${name}" to int.`);
        }
        return Math.trunc(value);
    }
    if (cast === 'float') {
        const value = Number(raw);
        if (!Number.isFinite(value)) {
            throw new Error(`[SPRAG] Could not cast public env "${name}" to float.`);
        }
        return value;
    }
    throw new Error(`[SPRAG] Unsupported browser env cast "${cast}".`);
}
"""


def _method_source(method):
    source = inspect.getsource(method)
    return textwrap.dedent(source)


def _method_source_info(method):
    source_lines, start_line = inspect.getsourcelines(method)
    source = textwrap.dedent("".join(source_lines))
    source_file = inspect.getsourcefile(method) or inspect.getfile(method)
    return source, source_file, start_line


def collect_env_helper_refs_for_class(browser_class) -> dict[str, str]:
    """Return local names that resolve to SPRAG env helper functions."""
    module = inspect.getmodule(browser_class)
    if module is None:
        return {}
    refs = {}
    for name, value in vars(module).items():
        if value is sprag_env:
            refs[name] = "env"
        elif value is sprag_public_env:
            refs[name] = "public_env"
    return refs


def compile_module_class(module_class, *, declared_import_aliases=None) -> str:
    return compile_module_artifact(
        module_class,
        declared_import_aliases=declared_import_aliases,
    ).code


def compile_module_artifact(module_class, *, declared_import_aliases=None) -> GeneratedArtifact:
    from ...runtime.browser import RefDescriptor  # local import to avoid circular dep

    store_refs = collect_store_refs_for_class(module_class)
    server_only_symbols = _server_only_imports_for_class(module_class)
    if server_only_symbols:
        joined = ", ".join(server_only_symbols)
        raise JSCodegenError(
            f"{module_class.__name__} imports server-only SPRAG symbol(s): {joined}. "
            "Move that code into a Controller or Service; browser Modules can only use the Ragot-side SPRAG surface.",
            source_file=inspect.getsourcefile(module_class) or inspect.getfile(module_class),
            class_name=module_class.__name__,
        )

    browser_class_refs = used_browser_class_refs(module_class)
    js_import_aliases = used_js_import_aliases(module_class)
    env_helper_refs = collect_env_helper_refs_for_class(module_class)
    declared_import_aliases = set(declared_import_aliases or ())

    def _seed_env(*, source=None, source_file=None, method_name=None, line_offset=0) -> dict:
        env = {
            "__sprag_source": source or "",
            "__sprag_source_file": source_file,
            "__sprag_class_name": module_class.__name__,
            "__sprag_method_name": method_name,
            "__sprag_line_offset": line_offset,
        }
        if store_refs:
            env["__sprag_stores__"] = store_refs
        if browser_class_refs:
            env["__sprag_classes__"] = browser_class_refs
        if js_import_aliases or declared_import_aliases:
            env["__sprag_import_aliases__"] = declared_import_aliases
        if env_helper_refs:
            env["__sprag_env_helpers__"] = env_helper_refs
        return env

    refs: list[tuple[str, str]] = []
    ref_names: set[str] = set()
    infinite_scrolls: list[tuple[str, dict]] = []
    has_user_on_start = False

    for name, value in module_class.__dict__.items():
        if isinstance(value, RefDescriptor):
            refs.append((name, value.selector))
            ref_names.add(name)
            continue
        if not callable(value) or name.startswith("__") or name == "__init__":
            continue
        if name == "on_start":
            has_user_on_start = True
        is_cfg = getattr(value, "_sprag_infinite_scroll", None)
        if is_cfg is not None:
            infinite_scrolls.append((_map_name(name), is_cfg))

    method_names = {
        _map_name(name)
        for name, value in module_class.__dict__.items()
        if callable(value) and not name.startswith("__")
    }
    constructor_extras, constructor_mappings = _compile_constructor_extras(
        module_class,
        method_names=method_names,
        env=_seed_env(),
    )

    method_chunks: list[tuple[str, list[GeneratedLineMapping | None], int | None, str]] = []
    for name, value in module_class.__dict__.items():
        if isinstance(value, RefDescriptor):
            continue
        if not callable(value) or name.startswith("__") or name == "__init__":
            continue

        source, source_file, source_start_line = _method_source_info(value)
        method_ast = ast.parse(source)
        function_def = method_ast.body[0]
        lint_browser_method(
            function_def,
            source=source,
            source_file=source_file,
            class_name=module_class.__name__,
            method_name=name,
            line_offset=source_start_line,
        )
        js_name = _map_name(name)
        is_async = isinstance(function_def, ast.AsyncFunctionDef)

        try:
            body, body_mappings = _compile_statements_with_mappings(
                function_def.body,
                method_names=method_names,
                env=_seed_env(
                    source=source,
                    source_file=source_file,
                    method_name=name,
                    line_offset=source_start_line,
                ),
                source_line_offset=source_start_line - 1,
                source_name=name,
            )
        except JSCodegenError as exc:
            raise exc.with_context(
                source_file=source_file,
                class_name=module_class.__name__,
                method_name=name,
                line=exc.line if exc.line is not None else source_start_line,
                source_line=exc.source_line if exc.source_line is not None else source.splitlines()[0],
            ) from exc

        if name == "on_start":
            setup_lines = _emit_module_setup(
                refs, infinite_scrolls, ref_names=ref_names, indent=8
            )
            if setup_lines:
                body = "\n".join(setup_lines) + "\n" + body
                body_mappings = (
                    mappings_for_text(
                        "\n".join(setup_lines),
                        source_line=source_start_line,
                        name=name,
                    )
                    + body_mappings
                )

        debounce_ms = getattr(value, "_sprag_debounce_ms", None)
        throttle_ms = getattr(value, "_sprag_throttle_ms", None)
        if debounce_ms is not None:
            body, body_mappings = _wrap_debounce_with_mappings(
                body,
                body_mappings,
                js_name,
                debounce_ms,
                indent=8,
                source_line=source_start_line,
                source_name=name,
            )
        elif throttle_ms is not None:
            body, body_mappings = _wrap_throttle_with_mappings(
                body,
                body_mappings,
                js_name,
                throttle_ms,
                indent=8,
                source_line=source_start_line,
                source_name=name,
            )

        async_prefix = "async " if is_async else ""
        params = ", ".join(arg.arg for arg in function_def.args.args[1:])
        method_chunks.append((
            f"    {async_prefix}{js_name}({params}) {{\n{body}\n    }}",
            mappings_for_text(
                f"    {async_prefix}{js_name}({params}) {{",
                source_line=source_start_line,
                name=name,
            )
            + body_mappings
            + mappings_for_text("    }", source_line=source_start_line, name=name),
            source_start_line,
            name,
        ))

    if not has_user_on_start and (refs or infinite_scrolls):
        setup_lines = _emit_module_setup(
            refs, infinite_scrolls, ref_names=ref_names, indent=8
        )
        body = "\n".join(setup_lines)
        method_chunks.append((
            f"    onStart() {{\n{body}\n    }}",
            mappings_for_text("    onStart() {", source_line=None, name=None)
            + mappings_for_text(body, source_line=None, name=None)
            + mappings_for_text("    }", source_line=None, name=None),
            None,
            "on_start",
        ))

    method_code = [chunk for chunk, _, _, _ in method_chunks]
    methods_block = "\n\n".join(method_code) if method_code else "    onStart() {}\n"

    # Compile the module-level helper prelude BEFORE running the import
    # detectors below — helpers can reference stores, ragot primitives,
    # other browser classes, joinUrl, and env helpers just like methods,
    # and the file needs the matching import lines.
    source_file = inspect.getsourcefile(module_class) or inspect.getfile(module_class)
    module_helpers = collect_module_helpers(source_file)
    check_helper_name_collisions(
        module_helpers,
        store_names=set(store_refs.keys()),
        class_names=set(browser_class_refs.keys()),
        source_file=source_file,
    )
    helper_seed = referenced_helper_names_in_class(module_class, module_helpers)
    used_helpers = select_used_helpers(module_helpers, helper_seed)
    module_helpers_prelude, module_helpers_mappings = compile_module_helpers_prelude(
        module_helpers,
        used_helpers,
        seed_env=_seed_env,
        source_file=source_file,
    )

    # Run import/runtime detection over both method bodies and helper prelude
    # so a store/class/imports reference inside a helper still triggers the
    # matching import line.
    import_scan_source = methods_block + "\n" + module_helpers_prelude

    extra_imports = _detect_ragot_imports(import_scan_source)
    base_imports = "Module, ragotRegistry"
    if extra_imports:
        base_imports += ", " + ", ".join(sorted(extra_imports))

    used_stores = _detect_used_stores(import_scan_source, store_refs)
    store_import_line = ""
    if used_stores:
        names = ", ".join(sorted(used_stores))
        store_import_line = f"import {{ {names} }} from '../stores.js';\n"
    sprag_runtime_import_line = ""
    if _references_joinUrl(import_scan_source):
        sprag_runtime_import_line = "import { joinUrl } from '../../runtime/urls.js';\n"
    class_import_lines = _browser_class_imports(
        import_scan_source,
        browser_class_refs,
        current_class=module_class,
        kind="modules",
    )
    env_helper_prelude = _emit_env_helper_prelude(import_scan_source)
    source_content = Path(source_file).read_text(encoding="utf-8")
    generated_filename = f"{module_class.__name__}.js"
    line_mappings: list[GeneratedLineMapping | None] = []
    method_spans: list[dict[str, object]] = []
    rendered_parts: list[str] = []

    def _append(
        text: str,
        *,
        source_line: int | None = None,
        name: str | None = None,
        explicit_mappings: list[GeneratedLineMapping | None] | None = None,
    ):
        rendered_parts.append(text)
        if explicit_mappings is not None:
            line_mappings.extend(explicit_mappings)
            return
        mapping = GeneratedLineMapping(source_line=source_line, name=name) if source_line is not None else None
        line_mappings.extend([mapping] * count_lines(text))

    _append(f"import {{ {base_imports} }} from '../../vendor/ragot.esm.min.js';\n")
    _append(sprag_runtime_import_line)
    _append(store_import_line)
    _append("\n")
    _append(class_import_lines)
    _append(env_helper_prelude)
    if module_helpers_prelude:
        _append(module_helpers_prelude, explicit_mappings=module_helpers_mappings)
    _append(f"export class {module_class.__name__} extends Module {{\n")
    _append("    constructor(initialState = {}) {\n")
    _append("        super(initialState);\n")
    _append("        this.component = null;\n")
    _append("        this.actions = null;\n")
    _append("        this.route = null;\n")
    _append("        this.socket = null;\n")
    init = module_class.__dict__.get("__init__")
    if constructor_extras:
        init_start_line = None
        if init is not None:
            _, _, init_start_line = _method_source_info(init)
        if init_start_line is not None:
            start_line = len(line_mappings) + 1
            _append(
                constructor_extras + ("\n" if not constructor_extras.endswith("\n") else ""),
                explicit_mappings=constructor_mappings + mappings_for_text("\n", source_line=init_start_line, name="__init__"),
            )
            method_spans.append(
                {
                    "name": "__init__",
                    "source_line": init_start_line,
                    "generated_start_line": start_line,
                    "generated_end_line": start_line + count_lines(constructor_extras) - 1,
                }
            )
        else:
            _append(constructor_extras + ("\n" if not constructor_extras.endswith("\n") else ""))
    _append("    }\n\n")
    _append(
        "    _spragSocket() {\n"
        "        return this.socket || window.__SPRAG_SOCKET__ || null;\n"
        "    }\n\n"
        "    onSocket(event, handler) {\n"
        "        const socket = this._spragSocket();\n"
        "        if (!socket) {\n"
        "            console.warn('[SPRAG] Module.on_socket(...) called before the shared socket bridge was ready.');\n"
        "            return this;\n"
        "        }\n"
        "        return super.onSocket(socket, event, handler);\n"
        "    }\n\n"
        "    offSocket(event, handler) {\n"
        "        const socket = this._spragSocket();\n"
        "        if (!socket) {\n"
        "            return this;\n"
        "        }\n"
        "        return super.offSocket(socket, event, handler);\n"
        "    }\n\n"
        "    emitSocket(event, payload = null) {\n"
        "        const socket = this._spragSocket();\n"
        "        if (!socket || typeof socket.emit !== 'function') {\n"
        "            console.warn('[SPRAG] Module.emit_socket(...) called before the shared socket bridge was ready.');\n"
        "            return false;\n"
        "        }\n"
        "        return socket.emit(event, payload);\n"
        "    }\n\n"
        "    refetchOnSocket(event = 'sprag:refetch', action = null, onResult = null, onError = null) {\n"
        "        const handler = (payload = {}) => {\n"
        "            const actionName = payload && typeof payload.action === 'string' && payload.action.trim()\n"
        "                ? payload.action.trim()\n"
        "                : (typeof action === 'string' && action.trim() ? action.trim() : null);\n"
        "            if (!actionName) {\n"
        "                console.warn('[SPRAG] Module.refetch_on_socket(...) could not resolve an action name.');\n"
        "                return Promise.resolve(null);\n"
        "            }\n"
        "            const actionPayload = payload && payload.payload && typeof payload.payload === 'object'\n"
        "                ? payload.payload\n"
        "                : {};\n"
        "            return this.callAction(actionName, actionPayload)\n"
        "                .then((result) => {\n"
        "                    if (typeof onResult === 'function') {\n"
        "                        onResult.call(this, result, payload);\n"
        "                    }\n"
        "                    return result;\n"
        "                })\n"
        "                .catch((error) => {\n"
        "                    if (typeof onError === 'function') {\n"
        "                        return onError.call(this, error, payload);\n"
        "                    }\n"
        "                    throw error;\n"
        "                });\n"
        "        };\n"
        "        this.onSocket(event, handler);\n"
        "        return handler;\n"
        "    }\n\n"
        "    joinTopic(topic) {\n"
        "        const socket = this._spragSocket();\n"
        "        if (!socket || typeof socket.joinTopic !== 'function') {\n"
        "            console.warn('[SPRAG] Module.join_topic(...) called before the shared socket bridge was ready.');\n"
        "            return false;\n"
        "        }\n"
        "        return socket.joinTopic(topic);\n"
        "    }\n\n"
        "    leaveTopic(topic) {\n"
        "        const socket = this._spragSocket();\n"
        "        if (!socket || typeof socket.leaveTopic !== 'function') {\n"
        "            return false;\n"
        "        }\n"
        "        return socket.leaveTopic(topic);\n"
        "    }\n\n"
        "    provider(name) {\n"
        "        return ragotRegistry.require(name);\n"
        "    }\n\n"
        "    callAction(name, payload = {}) {\n"
        "        if (!this.actions || typeof this.actions.call !== 'function') {\n"
        "            return Promise.reject(new Error('[SPRAG] Action client unavailable.'));\n"
        "        }\n"
        "        return this.actions.call(name, payload);\n"
        "    }\n\n"
        "    actionErrorMessage(error, fallback = '') {\n"
        "        const helper = typeof window !== 'undefined' ? window.__SPRAG_ACTION_ERROR_MESSAGE__ : null;\n"
        "        if (typeof helper === 'function') {\n"
        "            return helper(error, fallback);\n"
        "        }\n"
        "        if (error && typeof error.message === 'string' && error.message.trim()) {\n"
        "            return error.message.trim();\n"
        "        }\n"
        "        return fallback || '[SPRAG] Action failed.';\n"
        "    }\n\n"
        "    formData(source) {\n"
        "        const helper = typeof window !== 'undefined' ? window.__SPRAG_FORM_DATA__ : null;\n"
        "        if (typeof helper !== 'function') {\n"
        "            throw new Error('[SPRAG] Form helper unavailable.');\n"
        "        }\n"
        "        return helper(source);\n"
        "    }\n\n"
        "    uploadForm(name, source, onProgress = null) {\n"
        "        const helper = typeof window !== 'undefined' ? window.__SPRAG_UPLOADS__ : null;\n"
        "        if (!helper || typeof helper.submit !== 'function') {\n"
        "            return Promise.reject(new Error('[SPRAG] Upload client unavailable.'));\n"
        "        }\n"
        "        return helper.submit(name, source, onProgress);\n"
        "    }\n\n"
        "    upload(name, file, payload = null, onProgress = null) {\n"
        "        const helper = typeof window !== 'undefined' ? window.__SPRAG_UPLOADS__ : null;\n"
        "        if (!helper || typeof helper.upload !== 'function') {\n"
        "            return Promise.reject(new Error('[SPRAG] Upload client unavailable.'));\n"
        "        }\n"
        "        return helper.upload(name, file, payload, onProgress);\n"
        "    }\n\n"
        "    navigate(target, options = {}) {\n"
        "        const navigator = typeof window !== 'undefined' ? window.__SPRAG_NAVIGATE__ : null;\n"
        "        if (typeof navigator !== 'function') {\n"
        "            throw new Error('[SPRAG] Browser navigator unavailable.');\n"
        "        }\n"
        "        return navigator(target, options);\n"
        "    }\n\n"
        "    setMetadata(metadata = {}, options = {}) {\n"
        "        const helper = typeof window !== 'undefined' ? window.__SPRAG_SET_METADATA__ : null;\n"
        "        if (typeof helper !== 'function') {\n"
        "            throw new Error('[SPRAG] Metadata helper unavailable.');\n"
        "        }\n"
        "        return helper(metadata, options);\n"
        "    }\n\n"
    )
    if method_chunks:
        for chunk, chunk_mappings, source_line, name in method_chunks:
            start_line = len(line_mappings) + 1
            text = chunk + "\n\n"
            _append(
                text,
                explicit_mappings=chunk_mappings + mappings_for_text("\n\n", source_line=source_line, name=name if source_line is not None else None),
            )
            if source_line is not None:
                method_spans.append(
                    {
                        "name": name,
                        "source_line": source_line,
                        "generated_start_line": start_line,
                        "generated_end_line": start_line + count_lines(text) - 1,
                    }
                )
    else:
        _append("    onStart() {}\n")
    _append("}\n")

    code = "".join(rendered_parts)
    source_map = build_source_map(
        generated_file=generated_filename,
        source_file=source_file,
        source_content=source_content,
        line_mappings=line_mappings,
        extra={
            "x_sprag": {
                "class": module_class.__name__,
                "kind": "module",
                "methods": method_spans,
            }
        },
    )
    code += f"//# sourceMappingURL={generated_filename}.map\n"
    return GeneratedArtifact(code=code, source_map=source_map)


def _emit_env_helper_prelude(compiled_js: str) -> str:
    if "__spragEnv(" not in compiled_js and "__spragPublicEnv(" not in compiled_js:
        return ""
    return _ENV_HELPER_PREAMBLE + "\n\n"


def _browser_class_imports(compiled_js: str, refs: dict[str, type], *, current_class, kind: str) -> str:
    """Emit JS imports for visible browser classes that appear in compiled JS."""
    import re
    from ...runtime.browser import Component, Module

    lines = []
    for local_name, ref in sorted(refs.items()):
        if ref is current_class:
            continue
        pattern = rf"(?<![\w$]){re.escape(local_name)}(?![\w$])"
        if not re.search(pattern, compiled_js):
            continue
        class_name = ref.__name__
        if local_name == class_name:
            imported = class_name
        else:
            imported = f"{class_name} as {local_name}"
        if kind == "modules" and issubclass(ref, Component):
            path = f"../components/{class_name}.js"
        elif kind == "components" and issubclass(ref, Module):
            path = f"../modules/{class_name}.js"
        else:
            path = f"./{class_name}.js"
        lines.append(f"import {{ {imported} }} from '{path}';")
    return "\n".join(lines) + ("\n" if lines else "")


def _server_only_imports_for_class(module_class) -> list[str]:
    """Return server-only ``sprag`` symbols imported by a Module source file.

    The Python import would be valid at build time because source modules run
    in Python, but a browser ``Module`` must not carry Specter-only concepts
    over the runtime boundary. Rejecting it in codegen keeps SPRAG honest:
    server code lives in Controllers/Services, browser code lives in Modules.
    """
    module = inspect.getmodule(module_class)
    if module is None:
        return []
    try:
        source = inspect.getsource(module)
    except (OSError, TypeError):
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    imported: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module not in {"sprag", "sprag.runtime.server"}:
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            if alias.name in _SERVER_ONLY_SYMBOLS:
                imported.add(alias.asname or alias.name)
    return sorted(imported)


def _compile_constructor_extras(module_class, *, method_names, env) -> tuple[str, list[GeneratedLineMapping | None]]:
    """Compile safe ``__init__`` field initializers into the JS constructor.

    SPRAG owns the Module constructor shape in generated JS because Ragot
    expects ``constructor(initialState = {})``. We still preserve the common
    composition pattern ``self.child = None`` / ``self.child = ChildModule()``
    so browser-side object fields do not disappear across the Python→JS
    boundary. Runtime setup belongs in ``on_start`` where Ragot lifecycle
    ownership exists.
    """
    init = module_class.__dict__.get("__init__")
    if init is None:
        return "", []
    source, source_file, source_start_line = _method_source_info(init)
    function_def = ast.parse(source).body[0]
    statements = []
    for stmt in function_def.body:
        if _is_super_init_call(stmt):
            continue
        if isinstance(stmt, ast.Pass):
            continue
        # Block direct assignment to fields owned by the framework.
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Attribute)
            and isinstance(stmt.targets[0].value, ast.Name)
            and stmt.targets[0].value.id == "self"
            and stmt.targets[0].attr in {"state", "screen"}
        ):
            raise JSCodegenError(
                f"Cannot assign self.{stmt.targets[0].attr} in __init__ — "
                f"this field is owned by the SPRAG runtime.",
                source_file=source_file,
                class_name=module_class.__name__,
                method_name="__init__",
                line=source_start_line + getattr(stmt, "lineno", 1) - 1,
                source_line=source.splitlines()[getattr(stmt, "lineno", 1) - 1] if source.splitlines() else None,
            )
        statements.append(stmt)
    if not statements:
        return "", []
    constructor_env = dict(env)
    constructor_env.setdefault("state", "initialState")
    constructor_env.update(
        {
            "__sprag_source": source,
            "__sprag_source_file": source_file,
            "__sprag_class_name": module_class.__name__,
            "__sprag_method_name": "__init__",
            "__sprag_line_offset": source_start_line,
        }
    )
    return _compile_statements_with_mappings(
        statements,
        method_names=method_names,
        env=constructor_env,
        indent=8,
        source_line_offset=source_start_line - 1,
        source_name="__init__",
    )


def _is_super_init_call(stmt) -> bool:
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return False
    call = stmt.value
    if not isinstance(call.func, ast.Attribute) or call.func.attr != "__init__":
        return False
    value = call.func.value
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "super"
    )


def _emit_module_setup(refs, infinite_scrolls=None, *, ref_names=None, indent=8):
    """Emit the framework setup prologue for a Module's ``onStart``.

    Order:
      1. ref captures (so subsequent code may use ``this.refs.X``)
      2. createInfiniteScroll installs (cleanup via ``addCleanup``)
    """
    pad = " " * indent
    lines = []
    ref_names = ref_names or set()
    for name, selector in refs:
        lines.append(
            f"{pad}this.refs[{json.dumps(name)}] = "
            f"this.element.querySelector({json.dumps(selector)});"
        )
    for js_name, cfg in infinite_scrolls or []:
        # ``at`` is either a ref name (use this.refs[name]) or a CSS selector
        # (resolve at mount time via this.element.querySelector(...)).
        if cfg["at"] in ref_names:
            sentinel_expr = f"this.refs[{json.dumps(cfg['at'])}]"
        else:
            sentinel_expr = f"this.element.querySelector({json.dumps(cfg['at'])})"
        pairs = [
            f"sentinel: {sentinel_expr}",
            f"rootMargin: {json.dumps(cfg['root_margin'])}",
            f"onLoadMore: () => this.{js_name}()",
        ]
        if cfg["root"]:
            pairs.append(f"root: document.querySelector({json.dumps(cfg['root'])})")
        if cfg["top_at"]:
            top_expr = (
                f"this.refs[{json.dumps(cfg['top_at'])}]"
                if cfg["top_at"] in ref_names
                else f"this.element.querySelector({json.dumps(cfg['top_at'])})"
            )
            pairs.append(f"topSentinel: {top_expr}")
        block = ", ".join(pairs)
        lines.append(f"{pad}createInfiniteScroll(this, {{ {block} }});")
    return lines


def _wrap_debounce(body, js_name, ms, *, indent=8):
    """Wrap a compiled method body with a trailing-edge debounce.

    Uses Ragot's ``this.timeout(...)`` so the pending timer is auto-cancelled
    on module teardown. Per-method state is stored on ``this._sprDebounce``.
    The ``ms`` value is in milliseconds (converted by the decorator).
    """
    pad = " " * indent
    key = json.dumps(js_name)
    # Re-indent user body by 4 more spaces for the inner closure.
    inner_body = _reindent(body, extra=4)
    return (
        f"{pad}if (this._sprDebounce === undefined) this._sprDebounce = {{}};\n"
        f"{pad}if (this._sprDebounce[{key}] !== undefined) "
        f"this.clearTimeout(this._sprDebounce[{key}]);\n"
        f"{pad}this._sprDebounce[{key}] = this.timeout(() => {{\n"
        f"{pad}    this._sprDebounce[{key}] = undefined;\n"
        f"{inner_body}\n"
        f"{pad}}}, {ms});"
    )


def _wrap_debounce_with_mappings(
    body,
    body_mappings,
    js_name,
    ms,
    *,
    indent=8,
    source_line,
    source_name,
):
    wrapped = _wrap_debounce(body, js_name, ms, indent=indent)
    pad = " " * indent
    key = json.dumps(js_name)
    prefix = [
        f"{pad}if (this._sprDebounce === undefined) this._sprDebounce = {{}};",
        f"{pad}if (this._sprDebounce[{key}] !== undefined) this.clearTimeout(this._sprDebounce[{key}]);",
        f"{pad}this._sprDebounce[{key}] = this.timeout(() => {{",
        f"{pad}    this._sprDebounce[{key}] = undefined;",
    ]
    suffix = [f"{pad}}}, {ms});"]
    mappings = []
    for line in prefix:
        mappings.extend(mappings_for_text(line, source_line=source_line, name=source_name))
    mappings.extend(body_mappings)
    for line in suffix:
        mappings.extend(mappings_for_text(line, source_line=source_line, name=source_name))
    return wrapped, mappings


def _wrap_throttle(body, js_name, ms, *, indent=8):
    """Wrap a compiled method body with a leading-edge throttle.

    No timer is scheduled — a timestamp guard short-circuits re-entrant
    calls within the throttle window. State is on ``this._sprThrottle``.
    The ``ms`` value is in milliseconds (converted by the decorator).
    """
    pad = " " * indent
    key = json.dumps(js_name)
    return (
        f"{pad}if (this._sprThrottle === undefined) this._sprThrottle = {{}};\n"
        f"{pad}const __now = Date.now();\n"
        f"{pad}if (this._sprThrottle[{key}] !== undefined && "
        f"__now - this._sprThrottle[{key}] < {ms}) return;\n"
        f"{pad}this._sprThrottle[{key}] = __now;\n"
        f"{body}"
    )


def _wrap_throttle_with_mappings(
    body,
    body_mappings,
    js_name,
    ms,
    *,
    indent=8,
    source_line,
    source_name,
):
    wrapped = _wrap_throttle(body, js_name, ms, indent=indent)
    pad = " " * indent
    key = json.dumps(js_name)
    prefix = [
        f"{pad}if (this._sprThrottle === undefined) this._sprThrottle = {{}};",
        f"{pad}const __now = Date.now();",
        f"{pad}if (this._sprThrottle[{key}] !== undefined && __now - this._sprThrottle[{key}] < {ms}) return;",
        f"{pad}this._sprThrottle[{key}] = __now;",
    ]
    mappings = []
    for line in prefix:
        mappings.extend(mappings_for_text(line, source_line=source_line, name=source_name))
    mappings.extend(body_mappings)
    return wrapped, mappings


def _reindent(body, *, extra):
    """Indent every non-empty line of ``body`` by ``extra`` additional spaces."""
    prefix = " " * extra
    return "\n".join(prefix + line if line.strip() else line for line in body.split("\n"))


def _references_joinUrl(compiled_js: str) -> bool:
    """Return True when compiled JS calls the SPRAG runtime ``joinUrl``."""
    import re
    return bool(re.search(r"(?<![\w$.])joinUrl\s*\(", compiled_js))


def _detect_used_stores(compiled_js: str, store_refs: dict[str, str]) -> set[str]:
    """Return the set of store JS names that appear as bare identifiers in ``compiled_js``.

    ``store_refs`` is the {python_local: store_name} map from the source
    file. We scan the compiled JS for each known store name surrounded by
    JS word boundaries (no preceding ``.`` or ``$``) so that property
    access like ``foo.counter`` does not pull in an unrelated store import.
    """
    import re

    used: set[str] = set()
    for store_name in set(store_refs.values()):
        pattern = rf"(?<![\w$.]){re.escape(store_name)}(?![\w$])"
        if re.search(pattern, compiled_js):
            used.add(store_name)
    return used
