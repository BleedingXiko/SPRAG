"""File-backed app shell primitive for SPRAG surfaces."""

from __future__ import annotations

import html
import hashlib
import importlib
import posixpath
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


DEFAULT_SLOT = "{{ sprag_slot }}"
ALT_SLOT = "<sprag-slot></sprag-slot>"


@dataclass(frozen=True)
class Shell:
    """Shared document chrome and CSS for pages and mounts.

    A shell is intentionally server-side document composition: it wraps
    rendered route HTML or the mount root target before Ragot boots.
    """

    template: str | None = None
    css: tuple[str, ...] = field(default_factory=tuple)
    slot: str = DEFAULT_SLOT


@dataclass(frozen=True)
class ShellAsset:
    """Resolved stylesheet asset for a shell."""

    source_path: Path
    web_path: str


def shell(base=None, *, template=None, css=None, slot=DEFAULT_SLOT) -> Shell:
    """Create or extend a SPRAG shell.

    Usage::

        base_shell = shell(template="app/shell.html", css=["app/shell.css"])
        route_shell = shell(base_shell, css=["app/routes/counter/counter.css"])
    """
    if isinstance(base, Shell):
        base_template = base.template
        base_css = base.css
        base_slot = base.slot
    elif base is None:
        base_template = None
        base_css = ()
        base_slot = slot
    else:
        base_template = str(base)
        base_css = ()
        base_slot = slot

    css_items = _normalize_css(css)
    return Shell(
        template=template if template is not None else base_template,
        css=base_css + css_items,
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
) -> tuple[str, str, tuple[ShellAsset, ...]]:
    """Return ``(body_html, head_html, assets)`` after applying the effective shell."""
    effective = effective_shell(app_shell if app_shell is not None else getattr(app, "shell", None), surface_shell)
    if effective is None:
        return body_html, "", ()

    root = _project_root(app, project_root)
    wrapped_body = _render_shell_template(effective, body_html, root)
    assets = _resolve_css_assets(effective.css, root)
    head_html = _render_css_links(assets, document_path=document_path)
    return wrapped_body, head_html, assets


def emit_shell_assets(output_dir: str | Path, assets: Iterable[ShellAsset]):
    """Copy resolved shell stylesheets into the build output directory."""
    target_root = Path(output_dir)
    seen = set()
    for asset in assets:
        if asset.web_path in seen:
            continue
        seen.add(asset.web_path)
        target_path = target_root / asset.web_path.lstrip("/")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset.source_path, target_path)


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
        slot=surface.slot or base.slot,
    )


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


def _render_css_links(assets: Iterable[ShellAsset], *, document_path: str | None = None) -> str:
    chunks = []
    for asset in assets:
        href = asset.web_path
        if document_path is not None:
            href = _relative_asset_href(document_path, href)
        label = html.escape(asset.web_path, quote=True)
        escaped_href = html.escape(href, quote=True)
        chunks.append(
            f'<link rel="stylesheet" href="{escaped_href}" data-sprag-css="{label}">'
        )
    return "\n".join(chunks)


def _resolve_css_assets(paths: Iterable[str], project_root: Path) -> tuple[ShellAsset, ...]:
    assets = []
    for css_path in paths:
        resolved = _resolve_path(project_root, css_path).resolve()
        assets.append(ShellAsset(source_path=resolved, web_path=_asset_web_path(resolved, project_root)))
    return tuple(assets)


def _resolve_path(project_root: Path, path: str | Path) -> Path:
    next_path = Path(path)
    if next_path.is_absolute():
        return next_path
    return project_root / next_path


def _asset_web_path(source_path: Path, project_root: Path) -> str:
    root = project_root.resolve()
    try:
        relative = source_path.relative_to(root)
        return f"/assets/{relative.as_posix()}"
    except ValueError:
        digest = hashlib.sha1(str(source_path).encode("utf-8")).hexdigest()[:12]
        name = source_path.name or "stylesheet.css"
        return f"/assets/_external/{digest}-{name}"


def _relative_asset_href(document_path: str, asset_path: str) -> str:
    if not asset_path.startswith("/"):
        return asset_path
    if not document_path or document_path == "/":
        return asset_path.lstrip("/")
    return posixpath.relpath(asset_path.lstrip("/"), start=document_path.strip("/"))


def _project_root(app, explicit: str | Path | None) -> Path:
    if explicit is not None:
        return Path(explicit).resolve()
    if app is not None and getattr(app, "project_root", None):
        return Path(app.project_root).resolve()
    if app is not None and getattr(app, "routes", None):
        package_name = app.routes.split(".", 1)[0]
        try:
            package = importlib.import_module(package_name)
            package_file = getattr(package, "__file__", None)
            if package_file:
                return Path(package_file).resolve().parent.parent
        except Exception:
            pass
    return Path.cwd().resolve()


def _normalize_css(css) -> tuple[str, ...]:
    if css is None:
        return ()
    if isinstance(css, (str, Path)):
        return (str(css),)
    return tuple(str(item) for item in css)
