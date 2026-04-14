"""Compile a SPRAG ``Component`` Python class into its emitted JS source.

A Component owns a ``render(self, props)`` that returns a UI tree, plus
optional helper methods (callable from event handlers), optional
lifecycle method overrides (``on_start``/``on_stop``/``unmount``), and
decorator-tagged ``@animate``/``@virtual_scroll``/``@infinite_scroll``
sugar that gets merged into the synthesised lifecycle methods.

Phase 2 adds three rendering primitives that need cooperation between
``render()`` and ``onStart()``:

  - ``ui.For(items, key, render)``     -> renderList
  - ``ui.Grid(items, key, render, ...)`` -> renderGrid
  - ``ui.LazyImage(src, placeholder=)`` -> createLazyLoader

Each primitive emits a placeholder element in ``render()`` (carrying a
``data-sprag-mount=N`` attribute) and registers a side-effect with the
render context. The synthesised ``onStart`` then iterates the registered
mounts and emits one ``renderList`` / ``renderGrid`` call per For/Grid,
plus a single ``createLazyLoader`` install if any LazyImage was used.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

from .diagnostics import lint_browser_method
from .expressions import _compile_expr
from .dependencies import used_browser_class_refs, used_js_import_aliases
from .imports import _detect_ragot_imports
from .mappings import JSCodegenError, _map_name
from .modules import (
    _browser_class_imports,
    _detect_used_stores,
    _emit_env_helper_prelude,
    _method_source,
    _method_source_info,
    collect_env_helper_refs_for_class,
)
from .source_maps import (
    GeneratedArtifact,
    GeneratedLineMapping,
    build_source_map,
    count_lines,
    mappings_for_text,
)
from .statements import _compile_statements_with_mappings
from .stores_scan import collect_store_refs_for_class


def compile_component_class(component_class, *, declared_import_aliases=None) -> str:
    return compile_component_artifact(
        component_class,
        declared_import_aliases=declared_import_aliases,
    ).code


def compile_component_artifact(component_class, *, declared_import_aliases=None) -> GeneratedArtifact:
    render_source, render_file, render_start_line = _method_source_info(component_class.render)
    render_ast = ast.parse(render_source)
    function_def = render_ast.body[0]
    lint_browser_method(
        function_def,
        source=render_source,
        source_file=render_file,
        class_name=component_class.__name__,
        method_name="render",
        line_offset=render_start_line,
        disallow_component_subscribe=True,
    )

    # Stores referenced in the source file (``from app.stores import counter``).
    store_refs = collect_store_refs_for_class(component_class)
    browser_class_refs = used_browser_class_refs(component_class)
    js_import_aliases = used_js_import_aliases(component_class)
    env_helper_refs = collect_env_helper_refs_for_class(component_class)
    declared_import_aliases = set(declared_import_aliases or ())

    # ----- Render-context collector -----
    # The render env carries an ``__sprag_mounts__`` list. When
    # _compile_ui_call sees a ui.For/ui.Grid/ui.LazyImage call, it appends
    # an entry to the list and returns a placeholder createElement. After
    # render() compiles, this list drives the synthesised onStart prologue.
    mounts: list[dict] = []
    render_env: dict = {"props": "props", "__sprag_mounts__": mounts}
    if store_refs:
        render_env["__sprag_stores__"] = store_refs
    if browser_class_refs:
        render_env["__sprag_classes__"] = browser_class_refs
    if js_import_aliases or declared_import_aliases:
        render_env["__sprag_import_aliases__"] = declared_import_aliases
    if env_helper_refs:
        render_env["__sprag_env_helpers__"] = env_helper_refs

    def _seed_env() -> dict:
        env = {}
        if store_refs:
            env["__sprag_stores__"] = store_refs
        if browser_class_refs:
            env["__sprag_classes__"] = browser_class_refs
        if js_import_aliases or declared_import_aliases:
            env["__sprag_import_aliases__"] = declared_import_aliases
        if env_helper_refs:
            env["__sprag_env_helpers__"] = env_helper_refs
        return env

    body_lines = []
    render_line_mappings: list[GeneratedLineMapping | None] = []
    return_expr = "createElement('div', {}, 'Unsupported component render')"

    for stmt in function_def.body:
        stmt_line = render_start_line + getattr(stmt, "lineno", 1) - 1
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            target = stmt.targets[0].id
            if target == "props":
                render_env[target] = "props"
                continue
            compiled_value = _compile_expr(stmt.value, render_env)
            render_env[target] = target
            line = f"        const {target} = {compiled_value};"
            body_lines.append(line)
            render_line_mappings.extend(mappings_for_text(line, source_line=stmt_line, name="render"))
            continue
        if isinstance(stmt, ast.Return):
            return_expr = _compile_expr(stmt.value, render_env)
            render_line_mappings.extend(
                mappings_for_text(
                    f"        return {return_expr};",
                    source_line=stmt_line,
                    name="render",
                )
            )
            continue
        raise JSCodegenError(
            f"Unsupported component statement in {component_class.__name__}.render: {ast.dump(stmt)}",
            source_file=render_file,
            class_name=component_class.__name__,
            method_name="render",
            line=render_start_line + getattr(stmt, "lineno", 1) - 1,
            source_line=render_source.splitlines()[getattr(stmt, "lineno", 1) - 1] if render_source.splitlines() else None,
        )

    # ---------- Decorator metadata ----------
    user_lifecycle: dict[str, object] = {}  # py_name -> method
    infinite_scroll_methods: list[tuple[str, dict]] = []  # (js_name, config)

    for name, value in component_class.__dict__.items():
        if not callable(value) or name.startswith("__") or name == "render":
            continue
        is_cfg = getattr(value, "_sprag_infinite_scroll", None)
        if is_cfg is not None:
            infinite_scroll_methods.append((_map_name(name), is_cfg))
        if name in ("on_start", "on_stop", "unmount"):
            user_lifecycle[name] = value

    animate_config = getattr(component_class, "_sprag_animate", None)
    vs_config = getattr(component_class, "_sprag_virtual_scroll", None)

    # @virtual_scroll requires renderChunk + totalItems methods on the class.
    # Validate now so the user gets a clear error instead of broken JS.
    if vs_config is not None:
        method_set = {n for n, v in component_class.__dict__.items() if callable(v)}
        if "chunk" not in method_set:
            raise JSCodegenError(
                f"@virtual_scroll on {component_class.__name__} requires a chunk(self, i) method."
            )
        if "total" not in method_set:
            raise JSCodegenError(
                f"@virtual_scroll on {component_class.__name__} requires a total(self) method."
            )
        if vs_config["pool_size"] > 0 and "recycle" not in method_set:
            raise JSCodegenError(
                f"@virtual_scroll(pool_size={vs_config['pool_size']}) on {component_class.__name__} "
                "requires a recycle(self, el, i) method (Ragot's onRecycle is mandatory when poolSize > 0)."
            )

    # ---------- Compile non-render methods ----------
    method_names = {
        _map_name(n)
        for n, v in component_class.__dict__.items()
        if callable(v) and not n.startswith("__")
    }

    extra_methods: list[tuple[str, list[GeneratedLineMapping | None], int | None, str]] = []

    def _lifecycle_body(py_name, prologue_lines, epilogue_lines=None):
        """Compile a lifecycle hook body with prologue/epilogue injected.

        ``prologue_lines`` runs before any user body, ``epilogue_lines``
        runs after. Used for on_start (prologue: animateIn + mount-point
        setup + virtual/infinite scroll installs) and unmount (prologue:
        animateOut + virtual-scroll teardown).
        """
        if py_name in user_lifecycle:
            source, source_file, source_start_line = _method_source_info(user_lifecycle[py_name])
            fn_ast = ast.parse(source).body[0]
            lint_browser_method(
                fn_ast,
                source=source,
                source_file=source_file,
                class_name=component_class.__name__,
                method_name=py_name,
                line_offset=source_start_line,
                disallow_component_subscribe=True,
            )
            try:
                body, body_mappings = _compile_statements_with_mappings(
                    fn_ast.body,
                    method_names=method_names,
                    env=_seed_env(),
                    source_line_offset=source_start_line - 1,
                    source_name=py_name,
                )
            except JSCodegenError as exc:
                raise exc.with_context(
                    source_file=source_file,
                    class_name=component_class.__name__,
                    method_name=py_name,
                    line=exc.line if exc.line is not None else source_start_line,
                    source_line=exc.source_line if exc.source_line is not None else source.splitlines()[0],
                ) from exc
        else:
            body = None
            body_mappings = []

        parts = []
        mappings = []
        if prologue_lines:
            prologue = "\n".join(prologue_lines)
            parts.append(prologue)
            mappings.extend(
                mappings_for_text(
                    prologue,
                    source_line=source_start_line if py_name in user_lifecycle else None,
                    name=py_name if py_name in user_lifecycle else None,
                )
            )
        if body is not None:
            parts.append(body)
            mappings.extend(body_mappings)
        if epilogue_lines:
            epilogue = "\n".join(epilogue_lines)
            parts.append(epilogue)
            mappings.extend(
                mappings_for_text(
                    epilogue,
                    source_line=source_start_line if py_name in user_lifecycle else None,
                    name=py_name if py_name in user_lifecycle else None,
                )
            )
        if not parts:
            return None
        return "\n".join(parts), mappings, (source_start_line if py_name in user_lifecycle else None)

    # Compile arbitrary helper methods (event handlers, @virtual_scroll
    # chunk()/total()/measure() methods, etc.) so they all survive on the
    # emitted class.
    for name, value in component_class.__dict__.items():
        if not callable(value) or name.startswith("__") or name == "render":
            continue
        if name in ("on_start", "on_stop", "unmount"):
            continue  # handled by lifecycle synthesis below
        source, source_file, source_start_line = _method_source_info(value)
        fn_ast = ast.parse(source).body[0]
        lint_browser_method(
            fn_ast,
            source=source,
            source_file=source_file,
            class_name=component_class.__name__,
            method_name=name,
            line_offset=source_start_line,
            disallow_component_subscribe=True,
        )
        js_name = _map_name(name)
        is_async = isinstance(fn_ast, ast.AsyncFunctionDef)
        try:
            body, body_mappings = _compile_statements_with_mappings(
                fn_ast.body,
                method_names=method_names,
                env=_seed_env(),
                source_line_offset=source_start_line - 1,
                source_name=name,
            )
        except JSCodegenError as exc:
            raise exc.with_context(
                source_file=source_file,
                class_name=component_class.__name__,
                method_name=name,
                line=exc.line if exc.line is not None else source_start_line,
                source_line=exc.source_line if exc.source_line is not None else source.splitlines()[0],
            ) from exc
        params = ", ".join(arg.arg for arg in fn_ast.args.args[1:])
        async_prefix = "async " if is_async else ""
        extra_methods.append((
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

    # ---------- Synthesise onStart ----------
    on_start_prologue: list[str] = []

    if animate_config:
        class_name = json.dumps(animate_config["class_name"])
        on_start_prologue.append(f"        animateIn(this.element, {class_name});")

    # Mount-point setup (renderList/renderGrid for ui.For/ui.Grid; a single
    # createLazyLoader install if any ui.LazyImage was used). These need to
    # run on initial mount *and* after any later component re-render so the
    # placeholder nodes regain their client-side owners.
    mount_setup_lines, mount_imports = _emit_mount_setup(mounts, method_names=method_names)
    if mount_setup_lines:
        extra_methods.extend(
            [
                ("    __spragSyncMounts() {\n"
                "        if (!this.element || !this._isMounted) {\n"
                "            return;\n"
                "        }\n"
                + "\n".join(mount_setup_lines)
                + "\n"
                "    }",
                mappings_for_text("    __spragSyncMounts() {", source_line=None, name=None)
                + mappings_for_text(
                    "        if (!this.element || !this._isMounted) {\n"
                    "            return;\n"
                    "        }\n"
                    + "\n".join(mount_setup_lines),
                    source_line=None,
                    name=None,
                )
                + mappings_for_text("    }", source_line=None, name=None),
                None,
                "__spragSyncMounts",
                ),
                ("    setStateSync(next) {\n"
                "        super.setStateSync(next);\n"
                "        this.__spragSyncMounts();\n"
                "    }",
                mappings_for_text(
                    "    setStateSync(next) {\n"
                    "        super.setStateSync(next);\n"
                    "        this.__spragSyncMounts();\n"
                    "    }",
                    source_line=None,
                    name=None,
                ),
                None,
                "setStateSync",
                ),
                ("    _performUpdate() {\n"
                "        super._performUpdate();\n"
                "        this.__spragSyncMounts();\n"
                "    }",
                mappings_for_text(
                    "    _performUpdate() {\n"
                    "        super._performUpdate();\n"
                    "        this.__spragSyncMounts();\n"
                    "    }",
                    source_line=None,
                    name=None,
                ),
                None,
                "_performUpdate",
                ),
            ]
        )
        on_start_prologue.append("        this.__spragSyncMounts();")

    # @virtual_scroll: instantiate the VirtualScroller against this.element.
    # The user's chunk()/total()/etc. methods are bound as the VS callbacks.
    if vs_config is not None:
        on_start_prologue.extend(_emit_virtual_scroll_setup(vs_config, component_class))

    # @infinite_scroll on a Component method.
    for js_name, cfg in infinite_scroll_methods:
        on_start_prologue.extend(_emit_infinite_scroll_setup(js_name, cfg))

    # ---------- Synthesise unmount ----------
    unmount_prologue: list[str] = []
    unmount_epilogue: list[str] = []
    if vs_config is not None:
        unmount_prologue.append(
            "        if (this.virtualScroll) { this.virtualScroll.unmount(); this.virtualScroll = null; this._sprVS = null; }"
        )
    if animate_config:
        class_name = json.dumps(animate_config["class_name"])
        unmount_epilogue.extend(
            [
                "        const __spragEl = this.element;",
                "        const __spragDone = () => super.unmount();",
                f"        if (__spragEl) {{ animateOut(__spragEl, {class_name}).then(__spragDone); }} else {{ __spragDone(); }}",
            ]
        )
    elif vs_config is not None:
        unmount_epilogue.append("        super.unmount();")

    on_start_body = _lifecycle_body("on_start", on_start_prologue)
    if on_start_body is not None:
        on_start_text, on_start_mappings, start_line = on_start_body
        extra_methods.append((
            f"    onStart() {{\n{on_start_text}\n    }}",
            mappings_for_text("    onStart() {", source_line=start_line, name="on_start" if start_line is not None else None)
            + on_start_mappings
            + mappings_for_text("    }", source_line=start_line, name="on_start" if start_line is not None else None),
            start_line,
            "on_start",
        ))

    on_stop_body = _lifecycle_body("on_stop", [])
    if on_stop_body is not None:
        on_stop_text, on_stop_mappings, stop_line = on_stop_body
        extra_methods.append((
            f"    onStop() {{\n{on_stop_text}\n    }}",
            mappings_for_text("    onStop() {", source_line=stop_line, name="on_stop" if stop_line is not None else None)
            + on_stop_mappings
            + mappings_for_text("    }", source_line=stop_line, name="on_stop" if stop_line is not None else None),
            stop_line,
            "on_stop",
        ))

    unmount_body = _lifecycle_body("unmount", unmount_prologue, unmount_epilogue)
    if unmount_body is not None:
        unmount_text, unmount_mappings, unmount_line = unmount_body
        extra_methods.append((
            f"    unmount() {{\n{unmount_text}\n    }}",
            mappings_for_text("    unmount() {", source_line=unmount_line, name="unmount" if unmount_line is not None else None)
            + unmount_mappings
            + mappings_for_text("    }", source_line=unmount_line, name="unmount" if unmount_line is not None else None),
            unmount_line,
            "unmount",
        ))

    method_code = [chunk for chunk, _, _, _ in extra_methods]
    methods_block = "\n\n".join(method_code)
    if methods_block:
        methods_block = "\n\n" + methods_block + "\n"

    all_code = "\n".join(body_lines) + "\n" + return_expr + "\n" + "\n".join(method_code)
    extra_imports = _detect_ragot_imports(all_code) | mount_imports
    base_imports = "Component, createElement"
    if extra_imports:
        base_imports += ", " + ", ".join(sorted(extra_imports))

    used_stores = _detect_used_stores(all_code, store_refs)
    store_import_line = ""
    if used_stores:
        names = ", ".join(sorted(used_stores))
        store_import_line = f"import {{ {names} }} from '../stores.js';\n"
    class_import_lines = _browser_class_imports(
        all_code,
        browser_class_refs,
        current_class=component_class,
        kind="components",
    )
    env_helper_prelude = _emit_env_helper_prelude(all_code)

    source_file = inspect.getsourcefile(component_class) or inspect.getfile(component_class)
    source_content = Path(source_file).read_text(encoding="utf-8")
    generated_filename = f"{component_class.__name__}.js"
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
    _append(store_import_line)
    _append("\n")
    _append(class_import_lines)
    _append(env_helper_prelude)
    _append(f"export class {component_class.__name__} extends Component {{\n")
    _append(
        "    constructor(initialState = {}, options = {}) {\n"
        "        super(initialState);\n"
        "        this.props = options.props || {};\n"
        "        this.module = options.module || null;\n"
        "        this.refs = {};\n"
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
    render_start_generated = len(line_mappings) + 1
    render_body = "\n".join(body_lines)
    render_method = (
        "    render(propsOverride = null) {\n"
        "        const props = propsOverride || this.props || {};\n"
        + (render_body + "\n" if render_body else "")
        + f"        return {return_expr};\n"
        "    }"
    )
    render_method_mappings = (
        mappings_for_text("    render(propsOverride = null) {", source_line=render_start_line, name="render")
        + mappings_for_text("        const props = propsOverride || this.props || {};", source_line=render_start_line, name="render")
        + render_line_mappings
        + mappings_for_text("    }", source_line=render_start_line, name="render")
    )
    _append(render_method, explicit_mappings=render_method_mappings)
    method_spans.append(
        {
            "name": "render",
            "source_line": render_start_line,
            "generated_start_line": render_start_generated,
            "generated_end_line": render_start_generated + count_lines(render_method) - 1,
        }
    )
    if methods_block:
        _append("\n")
        for chunk, chunk_mappings, source_line, name in extra_methods:
            _append("\n")
            start_line = len(line_mappings) + 1
            _append(chunk, explicit_mappings=chunk_mappings)
            if source_line is not None:
                method_spans.append(
                    {
                        "name": name,
                        "source_line": source_line,
                        "generated_start_line": start_line,
                        "generated_end_line": start_line + count_lines(chunk) - 1,
                    }
                )
        _append("\n")
    _append("}\n")

    code = "".join(rendered_parts)
    source_map = build_source_map(
        generated_file=generated_filename,
        source_file=source_file,
        source_content=source_content,
        line_mappings=line_mappings,
        extra={
            "x_sprag": {
                "class": component_class.__name__,
                "kind": "component",
                "methods": method_spans,
            }
        },
    )
    code += f"//# sourceMappingURL={generated_filename}.map\n"
    return GeneratedArtifact(code=code, source_map=source_map)


# ---------------------------------------------------------------------------
# Mount-point setup (ui.For / ui.Grid / ui.LazyImage)
# ---------------------------------------------------------------------------


def _emit_mount_setup(mounts: list[dict], *, method_names) -> tuple[list[str], set[str]]:
    """Emit the renderList / renderGrid / createLazyLoader prologue lines.

    Returns ``(lines, extra_imports)`` so the caller can merge the
    additional Ragot imports needed by the synthesised JS into the file's
    import set without re-scanning the prologue afterwards.
    """
    if not mounts:
        return [], set()

    lines: list[str] = []
    imports: set[str] = set()

    # Recompile env: in onStart we resolve ``props`` against ``this.props``
    # because the render() locals are out of scope at this point. The
    # throwaway ``__sprag_mounts__`` collector lets nested ui.LazyImage
    # calls inside a render lambda compile (their <img> emit is what we
    # want; the loader install is handled below by ``has_lazy``).
    nested_mounts: list[dict] = []
    onstart_env = {"props": "this.props", "__sprag_mounts__": nested_mounts}

    has_lazy = False
    for entry in mounts:
        if entry["tag"] == "LazyImage":
            has_lazy = True
            continue

        node = entry["node"]
        index = entry["index"]
        kind = entry["tag"]  # "For" or "Grid"

        # ui.For / ui.Grid signature: positional items + keyword key/render/pool_key/grid options.
        if not node.args:
            raise JSCodegenError(f"ui.{kind}(...) requires the items argument")
        items_expr = _compile_expr(node.args[0], onstart_env, method_names=method_names)

        key_expr = "(item, i) => String(i)"
        render_expr = "(item) => item"
        pool_key_expr = None
        grid_options: dict[str, str] = {}

        for kw in node.keywords:
            if kw.arg == "key":
                key_expr = _compile_key_argument(kw.value, onstart_env, method_names=method_names)
            elif kw.arg == "render":
                render_expr = _compile_expr(kw.value, onstart_env, method_names=method_names)
            elif kw.arg == "pool_key":
                pool_key_expr = _compile_expr(kw.value, onstart_env, method_names=method_names)
            elif kind == "Grid" and kw.arg in ("columns", "column_width", "gap", "apply_grid_styles"):
                js_key = {
                    "columns": "columns",
                    "column_width": "columnWidth",
                    "gap": "gap",
                    "apply_grid_styles": "applyGridStyles",
                }[kw.arg]
                grid_options[js_key] = _compile_expr(kw.value, onstart_env, method_names=method_names)
            else:
                raise JSCodegenError(f"Unknown ui.{kind}(...) argument: {kw.arg}")

        opts_chunks: list[str] = []
        if pool_key_expr is not None:
            opts_chunks.append(f"poolKey: {pool_key_expr}")
        for k, v in grid_options.items():
            opts_chunks.append(f"{k}: {v}")
        opts_block = "{ " + ", ".join(opts_chunks) + " }" if opts_chunks else "{}"

        target_query = f'this.element.querySelector(\'[data-sprag-mount="{index}"]\')'
        fn = "renderGrid" if kind == "Grid" else "renderList"
        imports.add(fn)
        lines.append(
            f"        {fn}({target_query}, {items_expr}, {key_expr}, {render_expr}, undefined, {opts_block});"
        )

    # Lazy images may also appear nested inside For/Grid render lambdas.
    if any(e["tag"] == "LazyImage" for e in nested_mounts):
        has_lazy = True

    if has_lazy:
        # One createLazyLoader install per component; the loader observes
        # any [data-src] elements that the component renders, regardless of
        # how many ui.LazyImage calls produced them.
        imports.add("createLazyLoader")
        lines.extend(
            [
                "        if (!this._sprLazy) {",
                "            this._sprLazy = createLazyLoader(this, { selector: '[data-src]' });",
                "        } else if (typeof this._sprLazy.refresh === 'function') {",
                "            this._sprLazy.refresh();",
                "        }",
            ]
        )

    return lines, imports


def _compile_key_argument(value_node, env, *, method_names):
    """Compile the ``key=`` argument of ui.For / ui.Grid.

    Accepts:
      - a callable expression (lambda or function reference) -> used as-is
      - a string literal field name -> wrapped into ``(item) => item[<name>]``
    """
    if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
        field = json.dumps(value_node.value)
        return f"(item) => item[{field}]"
    return _compile_expr(value_node, env, method_names=method_names)


# ---------------------------------------------------------------------------
# @virtual_scroll setup
# ---------------------------------------------------------------------------


def _emit_virtual_scroll_setup(cfg: dict, component_class) -> list[str]:
    """Emit the VirtualScroller instantiation prologue.

    The user's ``chunk``/``total``/``measure``/``placeholder``/``recycle``/
    ``evicted`` methods are bound as the VS callbacks. Only the methods
    that exist on the class are wired -- the rest fall back to Ragot's
    defaults (offsetHeight, plain placeholder div, etc.).
    """
    method_set = {n for n, v in component_class.__dict__.items() if callable(v)}

    pairs: list[str] = [
        f"chunkSize: {cfg['chunk_size']}",
        f"maxChunks: {cfg['max_chunks']}",
        f"initialChunks: {cfg['initial_chunks']}",
        f"rootMargin: {json.dumps(cfg['root_margin'])}",
        f"axis: {json.dumps(cfg['axis'])}",
        "renderChunk: (i) => this.chunk(i)",
        "totalItems: () => this.total()",
    ]
    if cfg["root"]:
        pairs.append(f"root: document.querySelector({json.dumps(cfg['root'])})")
    if cfg["container_class"]:
        pairs.append(f"containerClass: {json.dumps(cfg['container_class'])}")
    if "measure" in method_set:
        pairs.append("measureChunk: (el, i) => this.measure(el, i)")
    if "placeholder" in method_set:
        pairs.append("buildPlaceholder: (i, px) => this.placeholder(i, px)")
    if "evicted" in method_set:
        pairs.append("onChunkEvicted: (i) => this.evicted(i)")
    if cfg["pool_size"] > 0:
        pairs.append(f"poolSize: {cfg['pool_size']}")
        pairs.append("onRecycle: (el, i) => this.recycle(el, i)")
    if cfg["child_pool_size"] > 0:
        pairs.append(f"childPoolSize: {cfg['child_pool_size']}")

    options_block = ",\n            ".join(pairs)
    return [
        "        this.virtualScroll = new VirtualScroller({",
        f"            {options_block}",
        "        });",
        "        this._sprVS = this.virtualScroll;",
        "        this.virtualScroll.mount(this.element);",
    ]


# ---------------------------------------------------------------------------
# @infinite_scroll setup (Component-side; the Module-side variant lives in modules.py)
# ---------------------------------------------------------------------------


def _emit_infinite_scroll_setup(method_js_name: str, cfg: dict) -> list[str]:
    """Emit a ``createInfiniteScroll`` install bound to a method.

    The decorated method is wired as ``onLoadMore``. ``cfg["at"]`` is
    treated as a CSS selector and resolved at mount time. Cleanup is
    automatic via Ragot's ``addCleanup`` (the host is the owner).
    """
    sentinel_query = f"this.element.querySelector({json.dumps(cfg['at'])})"
    pairs = [
        f"sentinel: {sentinel_query}",
        f"rootMargin: {json.dumps(cfg['root_margin'])}",
        f"onLoadMore: () => this.{method_js_name}()",
    ]
    if cfg["root"]:
        pairs.append(f"root: document.querySelector({json.dumps(cfg['root'])})")
    if cfg["top_at"]:
        pairs.append(
            f"topSentinel: this.element.querySelector({json.dumps(cfg['top_at'])})"
        )
    block = ", ".join(pairs)
    return [f"        createInfiniteScroll(this, {{ {block} }});"]
