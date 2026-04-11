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

from ...runtime.env import env as sprag_env
from ...runtime.env import public_env as sprag_public_env
from .diagnostics import lint_browser_method
from .expressions import _compile_expr  # noqa: F401  (re-export for tests)
from .dependencies import used_browser_class_refs
from .imports import _detect_ragot_imports
from .mappings import JSCodegenError, _map_name
from .statements import _compile_statements
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


def compile_module_class(module_class) -> str:
    from ...runtime.browser import RefDescriptor  # local import to avoid circular dep

    # Stores referenced in the source file (``from app.stores import counter``).
    # Stashed on the per-call env so _compile_expr can route store-method
    # calls to the right Ragot equivalent and emit JS imports for them.
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
    env_helper_refs = collect_env_helper_refs_for_class(module_class)

    def _seed_env() -> dict:
        env = {}
        if store_refs:
            env["__sprag_stores__"] = store_refs
        if browser_class_refs:
            env["__sprag_classes__"] = browser_class_refs
        if env_helper_refs:
            env["__sprag_env_helpers__"] = env_helper_refs
        return env

    # ---------- Pass 1: metadata collection ----------
    refs: list[tuple[str, str]] = []
    ref_names: set[str] = set()
    infinite_scrolls: list[tuple[str, dict]] = []  # (js_method_name, config)
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

    # ---------- Pass 2: compile methods ----------
    method_names = {
        _map_name(name)
        for name, value in module_class.__dict__.items()
        if callable(value) and not name.startswith("__")
    }
    constructor_extras = _compile_constructor_extras(
        module_class,
        method_names=method_names,
        env=_seed_env(),
    )

    method_chunks = []
    for name, value in module_class.__dict__.items():
        if isinstance(value, RefDescriptor):
            continue
        if not callable(value) or name.startswith("__"):
            continue
        if name == "__init__":
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
            body = _compile_statements(
                function_def.body, method_names=method_names, env=_seed_env()
            )
        except JSCodegenError as exc:
            raise exc.with_context(
                source_file=source_file,
                class_name=module_class.__name__,
                method_name=name,
                line=exc.line if exc.line is not None else source_start_line,
                source_line=exc.source_line if exc.source_line is not None else source.splitlines()[0],
            ) from exc

        # Inject framework setup prologue into user-supplied on_start
        if name == "on_start":
            setup_lines = _emit_module_setup(
                refs, infinite_scrolls, ref_names=ref_names, indent=8
            )
            if setup_lines:
                body = "\n".join(setup_lines) + "\n" + body

        # Wrap body with debounce / throttle if the decorator was applied.
        # The decorator stores the value already converted to milliseconds.
        debounce_ms = getattr(value, "_sprag_debounce_ms", None)
        throttle_ms = getattr(value, "_sprag_throttle_ms", None)
        if debounce_ms is not None:
            body = _wrap_debounce(body, js_name, debounce_ms, indent=8)
        elif throttle_ms is not None:
            body = _wrap_throttle(body, js_name, throttle_ms, indent=8)

        async_prefix = "async " if is_async else ""
        params = ", ".join(arg.arg for arg in function_def.args.args[1:])
        method_chunks.append(
            f"    {async_prefix}{js_name}({params}) {{\n{body}\n    }}"
        )

    # ---------- Synthesize onStart if needed ----------
    if not has_user_on_start and (refs or infinite_scrolls):
        setup_lines = _emit_module_setup(
            refs, infinite_scrolls, ref_names=ref_names, indent=8
        )
        body = "\n".join(setup_lines)
        method_chunks.append(f"    onStart() {{\n{body}\n    }}")

    methods_block = "\n\n".join(method_chunks) if method_chunks else "    onStart() {}\n"
    extra_imports = _detect_ragot_imports(methods_block)
    base_imports = "Module"
    if extra_imports:
        base_imports += ", " + ", ".join(sorted(extra_imports))

    # Detect which declared stores actually appear in the compiled JS so
    # the generated file imports only what it uses.
    used_stores = _detect_used_stores(methods_block, store_refs)
    store_import_line = ""
    if used_stores:
        names = ", ".join(sorted(used_stores))
        store_import_line = f"import {{ {names} }} from '../stores.js';\n"
    class_import_lines = _browser_class_imports(
        methods_block,
        browser_class_refs,
        current_class=module_class,
        kind="modules",
    )
    env_helper_prelude = _emit_env_helper_prelude(methods_block)

    return f"""import {{ {base_imports} }} from '../../vendor/ragot.esm.min.js';
{store_import_line}
{class_import_lines}
{env_helper_prelude}export class {module_class.__name__} extends Module {{
    constructor(initialState = {{}}) {{
        super(initialState);
        this.component = null;
        this.actions = null;
        this.route = null;
        this.socket = null;
{constructor_extras}
    }}

    _spragSocket() {{
        return this.socket || window.__SPRAG_SOCKET__ || null;
    }}

    onSocket(event, handler) {{
        const socket = this._spragSocket();
        if (!socket) {{
            console.warn('[SPRAG] Module.on_socket(...) called before the shared socket bridge was ready.');
            return this;
        }}
        return super.onSocket(socket, event, handler);
    }}

    offSocket(event, handler) {{
        const socket = this._spragSocket();
        if (!socket) {{
            return this;
        }}
        return super.offSocket(socket, event, handler);
    }}

    emitSocket(event, payload = null) {{
        const socket = this._spragSocket();
        if (!socket || typeof socket.emit !== 'function') {{
            console.warn('[SPRAG] Module.emit_socket(...) called before the shared socket bridge was ready.');
            return false;
        }}
        return socket.emit(event, payload);
    }}

    callAction(name, payload = {{}}) {{
        if (!this.actions || typeof this.actions.call !== 'function') {{
            return Promise.reject(new Error('[SPRAG] Action client unavailable.'));
        }}
        return this.actions.call(name, payload);
    }}

{methods_block}
}}
"""


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


def _compile_constructor_extras(module_class, *, method_names, env) -> str:
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
        return ""
    source, source_file, source_start_line = _method_source_info(init)
    function_def = ast.parse(source).body[0]
    statements = []
    for stmt in function_def.body:
        if _is_super_init_call(stmt):
            continue
        if isinstance(stmt, ast.Pass):
            continue
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Attribute)
            and isinstance(stmt.targets[0].value, ast.Name)
            and stmt.targets[0].value.id == "self"
            and stmt.targets[0].attr not in {"state", "screen"}
        ):
            statements.append(stmt)
            continue
        raise JSCodegenError(
            f"Unsupported __init__ statement in browser Module {module_class.__name__}: "
            f"{ast.dump(stmt)}. Generated Module constructors support field assignments "
            "like `self.child = None`; put lifecycle work in on_start().",
            source_file=source_file,
            class_name=module_class.__name__,
            method_name="__init__",
            line=source_start_line + getattr(stmt, "lineno", 1) - 1,
            source_line=source.splitlines()[getattr(stmt, "lineno", 1) - 1] if source.splitlines() else None,
        )
    if not statements:
        return ""
    constructor_env = dict(env)
    constructor_env.setdefault("state", "initialState")
    return _compile_statements(
        statements,
        method_names=method_names,
        env=constructor_env,
        indent=8,
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


def _reindent(body, *, extra):
    """Indent every non-empty line of ``body`` by ``extra`` additional spaces."""
    prefix = " " * extra
    return "\n".join(prefix + line if line.strip() else line for line in body.split("\n"))


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
