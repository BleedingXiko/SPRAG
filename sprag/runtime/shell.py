"""File-backed app shell primitive for SPRAG surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .assets import (
    ModuleImport,
    ResolvedModuleImport,
    Script,
    SurfaceAssets,
    normalize_module_imports,
    resolve_module_imports,
    resolve_project_root,
    resolve_surface_assets,
)
from .urls import relativize_html_urls


DEFAULT_SLOT = "{{ sprag_slot }}"
ALT_SLOT = "<sprag-slot></sprag-slot>"


@dataclass(frozen=True)
class Shell:
    """Shared document chrome and surface assets for pages and mounts.

    A shell is intentionally server-side document composition: it wraps
    rendered route HTML or the mount root target before Ragot boots.
    """

    template: str | None = None
    css: tuple[str, ...] = field(default_factory=tuple)
    js: tuple[object, ...] = field(default_factory=tuple)
    modules: dict[str, ModuleImport] = field(default_factory=dict)
    slot: str = DEFAULT_SLOT


def shell(base=None, *, template=None, css=None, js=None, modules=None, slot=DEFAULT_SLOT) -> Shell:
    """Create or extend a SPRAG shell.

    Usage::

        base_shell = shell(template="app/shell.html", css=["app/shell.css"])
        route_shell = shell(base_shell, css=["app/routes/counter/counter.css"])
    """
    if isinstance(base, Shell):
        base_template = base.template
        base_css = base.css
        base_js = base.js
        base_modules = base.modules
        base_slot = base.slot
    elif base is None:
        base_template = None
        base_css = ()
        base_js = ()
        base_modules = {}
        base_slot = slot
    else:
        base_template = str(base)
        base_css = ()
        base_js = ()
        base_modules = {}
        base_slot = slot

    css_items = _normalize_css(css)
    js_items = _normalize_js(js)
    module_items = normalize_module_imports(modules)
    return Shell(
        template=template if template is not None else base_template,
        css=base_css + css_items,
        js=base_js + js_items,
        modules={**base_modules, **module_items},
        slot=slot if slot != DEFAULT_SLOT else base_slot,
    )


def apply_shell(
    body_html: str,
    *,
    app=None,
    surface_shell=None,
    project_root: str | Path | None = None,
    app_shell=None,
    document_path: str | None = None,
) -> tuple[str, SurfaceAssets]:
    """Return ``(body_html, assets)`` after applying the effective shell."""
    effective = effective_shell(app_shell if app_shell is not None else getattr(app, "shell", None), surface_shell)
    root = resolve_project_root(app, project_root)
    if effective is None:
        return body_html, SurfaceAssets()

    wrapped_body = _render_shell_template(effective, body_html, root)
    if document_path:
        wrapped_body = relativize_html_urls(wrapped_body, document_path)
    assets = resolve_surface_assets(project_root=root, css=effective.css, js=effective.js)
    return wrapped_body, assets


def effective_shell(app_shell, surface_shell) -> Shell | None:
    if app_shell is None and surface_shell is None:
        return None
    if app_shell is None:
        return _coerce_shell(surface_shell)
    if surface_shell is None:
        return _coerce_shell(app_shell)

    base = _coerce_shell(app_shell)
    surface = _coerce_shell(surface_shell)
    return Shell(
        template=surface.template or base.template,
        css=base.css + surface.css,
        js=base.js + surface.js,
        modules={**base.modules, **surface.modules},
        slot=surface.slot or base.slot,
    )


def resolve_effective_surface_modules(
    *,
    app=None,
    surface=None,
    project_root: str | Path | None = None,
    app_shell=None,
    surface_shell=None,
) -> tuple[ResolvedModuleImport, ...]:
    root = resolve_project_root(app, project_root)
    merged = {}
    if app is not None:
        merged.update(normalize_module_imports(getattr(app, "modules", None)))
    effective = effective_shell(
        app_shell if app_shell is not None else getattr(app, "shell", None),
        surface_shell if surface_shell is not None else getattr(surface, "shell", None),
    )
    if effective is not None:
        merged.update(effective.modules)
    if surface is not None:
        merged.update(normalize_module_imports(getattr(surface, "modules", None)))
    return resolve_module_imports(project_root=root, modules=merged)


def _coerce_shell(value) -> Shell:
    if isinstance(value, Shell):
        return value
    if isinstance(value, (str, Path)):
        return Shell(template=str(value))
    raise TypeError(f"Unsupported SPRAG shell value: {value!r}")


def _render_shell_template(shell_spec: Shell, body_html: str, project_root: Path) -> str:
    if not shell_spec.template:
        return body_html
    template_path = _resolve_path(project_root, shell_spec.template)
    template = template_path.read_text(encoding="utf-8")
    slot = shell_spec.slot or DEFAULT_SLOT
    if slot in template:
        return template.replace(slot, body_html)
    if ALT_SLOT in template:
        return template.replace(ALT_SLOT, body_html)
    raise ValueError(
        f"SPRAG shell template {template_path} must include {slot!r} "
        f"or {ALT_SLOT!r}."
    )


def _resolve_path(project_root: Path, path: str | Path) -> Path:
    next_path = Path(path)
    if next_path.is_absolute():
        return next_path
    return project_root / next_path


def _normalize_css(css) -> tuple[str, ...]:
    if css is None:
        return ()
    if isinstance(css, (str, Path)):
        return (str(css),)
    return tuple(str(item) for item in css)


def _normalize_js(js) -> tuple[object, ...]:
    if js is None:
        return ()
    if isinstance(js, (str, Path, Script)):
        return (str(js) if isinstance(js, Path) else js,)
    items = []
    for item in js:
        items.append(str(item) if isinstance(item, Path) else item)
    return tuple(items)
