"""File-backed app shell primitive for SPRAG surfaces."""

from __future__ import annotations

import html
import importlib
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
) -> tuple[str, str]:
    """Return ``(body_html, head_html)`` after applying the effective shell."""
    effective = effective_shell(app_shell if app_shell is not None else getattr(app, "shell", None), surface_shell)
    if effective is None:
        return body_html, ""

    root = _project_root(app, project_root)
    wrapped_body = _render_shell_template(effective, body_html, root)
    head_html = _render_css(effective.css, root)
    return wrapped_body, head_html


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


def _render_css(paths: Iterable[str], project_root: Path) -> str:
    chunks = []
    for css_path in paths:
        resolved = _resolve_path(project_root, css_path)
        css_text = resolved.read_text(encoding="utf-8")
        label = html.escape(str(css_path), quote=True)
        chunks.append(f'<style data-sprag-css="{label}">\n{css_text}\n</style>')
    return "\n".join(chunks)


def _resolve_path(project_root: Path, path: str | Path) -> Path:
    next_path = Path(path)
    if next_path.is_absolute():
        return next_path
    return project_root / next_path


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
