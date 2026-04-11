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

from .diagnostics import lint_browser_method
from .expressions import _compile_expr
from .dependencies import used_browser_class_refs
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
from .statements import _compile_statements
from .stores_scan import collect_store_refs_for_class


def compile_component_class(component_class) -> str:
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
    )

    # Stores referenced in the source file (``from app.stores import counter``).
    store_refs = collect_store_refs_for_class(component_class)
    browser_class_refs = used_browser_class_refs(component_class)
    env_helper_refs = collect_env_helper_refs_for_class(component_class)

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
    if env_helper_refs:
        render_env["__sprag_env_helpers__"] = env_helper_refs

    def _seed_env() -> dict:
        env = {}
        if store_refs:
            env["__sprag_stores__"] = store_refs
        if browser_class_refs:
            env["__sprag_classes__"] = browser_class_refs
        if env_helper_refs:
            env["__sprag_env_helpers__"] = env_helper_refs
        return env

    body_lines = []
    return_expr = "createElement('div', {}, 'Unsupported component render')"

    for stmt in function_def.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            target = stmt.targets[0].id
            if target == "props":
                render_env[target] = "props"
                continue
            compiled_value = _compile_expr(stmt.value, render_env)
            render_env[target] = target
            body_lines.append(f"        const {target} = {compiled_value};")
            continue
        if isinstance(stmt, ast.Return):
            return_expr = _compile_expr(stmt.value, render_env)
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

    extra_methods = []

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
            )
            try:
                body = _compile_statements(
                    fn_ast.body, method_names=method_names, env=_seed_env()
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

        parts = []
        if prologue_lines:
            parts.append("\n".join(prologue_lines))
        if body is not None:
            parts.append(body)
        if epilogue_lines:
            parts.append("\n".join(epilogue_lines))
        if not parts:
            return None
        return "\n".join(parts)

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
        )
        js_name = _map_name(name)
        is_async = isinstance(fn_ast, ast.AsyncFunctionDef)
        try:
            body = _compile_statements(
                fn_ast.body, method_names=method_names, env=_seed_env()
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
        extra_methods.append(f"    {async_prefix}{js_name}({params}) {{\n{body}\n    }}")

    # ---------- Synthesise onStart ----------
    on_start_prologue: list[str] = []

    if animate_config:
        class_name = json.dumps(animate_config["class_name"])
        on_start_prologue.append(f"        animateIn(this.element, {class_name});")

    # Mount-point setup (renderList/renderGrid for ui.For/ui.Grid; a single
    # createLazyLoader install if any ui.LazyImage was used).
    mount_setup_lines, mount_imports = _emit_mount_setup(mounts, method_names=method_names)
    on_start_prologue.extend(mount_setup_lines)

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
        extra_methods.append(f"    onStart() {{\n{on_start_body}\n    }}")

    on_stop_body = _lifecycle_body("on_stop", [])
    if on_stop_body is not None:
        extra_methods.append(f"    onStop() {{\n{on_stop_body}\n    }}")

    unmount_body = _lifecycle_body("unmount", unmount_prologue, unmount_epilogue)
    if unmount_body is not None:
        extra_methods.append(f"    unmount() {{\n{unmount_body}\n    }}")

    methods_block = "\n\n".join(extra_methods)
    if methods_block:
        methods_block = "\n\n" + methods_block + "\n"

    all_code = "\n".join(body_lines) + "\n" + return_expr + "\n" + "\n".join(extra_methods)
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

    return f"""import {{ {base_imports} }} from '../../vendor/ragot.esm.min.js';
{store_import_line}
{class_import_lines}
{env_helper_prelude}export class {component_class.__name__} extends Component {{
    constructor(initialState = {{}}, options = {{}}) {{
        super(initialState);
        this.props = options.props || {{}};
        this.module = options.module || null;
        this.refs = {{}};
    }}

    formData(source) {{
        const helper = typeof window !== 'undefined' ? window.__SPRAG_FORM_DATA__ : null;
        if (typeof helper !== 'function') {{
            throw new Error('[SPRAG] Form helper unavailable.');
        }}
        return helper(source);
    }}

    navigate(target, options = {{}}) {{
        const navigator = typeof window !== 'undefined' ? window.__SPRAG_NAVIGATE__ : null;
        if (typeof navigator !== 'function') {{
            throw new Error('[SPRAG] Browser navigator unavailable.');
        }}
        return navigator(target, options);
    }}

    render(propsOverride = null) {{
        const props = propsOverride || this.props || {{}};
{chr(10).join(body_lines)}
        return {return_expr};
    }}{methods_block}}}
"""


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
        lines.append("        this._sprLazy = createLazyLoader(this, { selector: '[data-src]' });")

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
