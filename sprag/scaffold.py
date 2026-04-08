"""Project + route scaffolding.

Project templates live under ``sprag/templates/<name>/`` with a ``.tmpl``
suffix so that ``.py`` template sources don't get picked up by package
discovery. Route templates (for ``sprag add``) live under
``sprag/templates/_route/<mode>/`` — the leading underscore keeps them
out of ``available_templates()`` so they don't show up as user-facing
project templates.

Both flows share ``_render_template_dir``, which walks a template tree,
strips the ``.tmpl`` suffix, and substitutes ``{{key}}`` tokens from a
variable dict into file contents.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .discovery import discover_surfaces, validate_surface_paths
from .mount import Mount
from .page import Page

TEMPLATES_ROOT = Path(__file__).parent / "templates"
TEMPLATE_SUFFIX = ".tmpl"
ROUTE_TEMPLATES_ROOT = TEMPLATES_ROOT / "_route"
MOUNT_TEMPLATES_ROOT = TEMPLATES_ROOT / "_mount"
ROUTE_MODES = ("document", "hybrid")
DEFAULT_ROUTE_MODE = "document"


def available_templates() -> list[str]:
    """Return the sorted list of project templates shipped with SPRAG."""
    if not TEMPLATES_ROOT.exists():
        return []
    return sorted(
        p.name for p in TEMPLATES_ROOT.iterdir() if p.is_dir() and not p.name.startswith("_")
    )


def _render_template_dir(
    template_dir: Path,
    target_dir: Path,
    variables: dict[str, str],
) -> list[Path]:
    """Walk ``template_dir``, materialise every ``*.tmpl`` file into ``target_dir``.

    Strips the ``.tmpl`` suffix and replaces every ``{{key}}`` token from
    ``variables`` in file contents. Non-``.tmpl`` files are ignored so
    editor/OS cruft in the template tree doesn't leak through.
    """
    if not template_dir.is_dir():
        raise SystemExit(f"[SPRAG] template directory not found: {template_dir}")

    target_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    for source in sorted(template_dir.rglob("*")):
        if source.is_dir():
            continue
        if source.suffix != TEMPLATE_SUFFIX:
            continue

        relative = source.relative_to(template_dir)
        out_name = relative.name[: -len(TEMPLATE_SUFFIX)]
        destination = target_dir / relative.parent / out_name
        destination.parent.mkdir(parents=True, exist_ok=True)

        content = source.read_text(encoding="utf-8")
        for key, value in variables.items():
            content = content.replace("{{" + key + "}}", value)
        destination.write_text(content, encoding="utf-8")
        created.append(destination)

    return created


def scaffold_project(
    target_dir: Path,
    project_name: str,
    template: str = "default",
) -> list[Path]:
    """Materialise a starter project from a named template."""
    template_dir = TEMPLATES_ROOT / template
    if not template_dir.is_dir():
        raise SystemExit(
            f"[SPRAG] unknown template: {template!r} "
            f"(available: {', '.join(available_templates()) or 'none'})"
        )
    return _render_template_dir(template_dir, target_dir, {"project_name": project_name})


# ---------------------------------------------------------------------------
# `sprag add <route>` — single-route scaffolding
# ---------------------------------------------------------------------------


def scaffold_route(
    project_root: Path,
    route_name: str,
    mode: str = DEFAULT_ROUTE_MODE,
) -> list[Path]:
    """Scaffold a new route inside an existing SPRAG project.

    ``mode`` selects which route template set to materialise:

    - ``"document"`` — pure SSR. Five files, no ``modules.py``.
    - ``"hybrid"`` — SSR + browser hydration. Six files, includes
      ``modules.py`` and a working ``@action`` → ``Module`` round trip.
    """
    if mode not in ROUTE_MODES:
        raise SystemExit(
            f"[SPRAG] unknown route mode: {mode!r} (expected one of: {', '.join(ROUTE_MODES)})"
        )

    normalized = route_name.strip("/").strip()
    if not normalized:
        raise ValueError("route name must not be empty")

    segments = [seg for seg in normalized.split("/") if seg]
    for seg in segments:
        if not seg.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"invalid route segment: {seg!r}")

    class_name = "".join(_pascal_case(seg) for seg in segments)
    route_path = "/" + "/".join(segments)
    var_name = segments[-1].replace("-", "_")

    routes_root = project_root / "app" / "routes"
    if not routes_root.exists():
        raise SystemExit(f"[SPRAG] routes directory not found: {routes_root}")

    route_dir = routes_root / Path(*segments)
    if route_dir.exists():
        raise SystemExit(f"[SPRAG] route already exists: {route_dir}")
    _validate_new_surface(project_root, route_path, kind="route")

    created: list[Path] = []

    # Ensure intermediate package __init__.py files exist (don't overwrite).
    current = routes_root
    for seg in segments[:-1]:
        current = current / seg
        current.mkdir(parents=True, exist_ok=True)
        init_file = current / "__init__.py"
        if not init_file.exists():
            init_file.write_text(f'"""{seg} route group."""\n', encoding="utf-8")
            created.append(init_file)

    route_dir.mkdir(parents=True, exist_ok=True)

    template_dir = ROUTE_TEMPLATES_ROOT / mode
    variables = {
        "route_name": normalized,
        "class_name": class_name,
        "route_path": route_path,
        "var_name": var_name,
    }
    created.extend(_render_template_dir(template_dir, route_dir, variables))

    return created


def scaffold_mount(project_root: Path, mount_name: str) -> list[Path]:
    """Scaffold a client app mount inside an existing SPRAG project."""
    normalized = mount_name.strip("/").strip()
    if not normalized:
        raise ValueError("mount name must not be empty")

    segments = [seg for seg in normalized.split("/") if seg]
    for seg in segments:
        if not seg.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"invalid mount segment: {seg!r}")

    class_name = "".join(_pascal_case(seg) for seg in segments)
    mount_path = "/" + "/".join(segments)
    var_name = segments[-1].replace("-", "_")

    app_root = project_root / "app"
    if not app_root.exists():
        raise SystemExit(f"[SPRAG] app directory not found: {app_root}")

    mounts_root = app_root / "mounts"
    mount_dir = mounts_root / Path(*segments)
    if mount_dir.exists():
        raise SystemExit(f"[SPRAG] mount already exists: {mount_dir}")
    _validate_new_surface(project_root, mount_path, kind="mount")

    created: list[Path] = []

    mounts_root.mkdir(parents=True, exist_ok=True)
    mounts_init = mounts_root / "__init__.py"
    if not mounts_init.exists():
        mounts_init.write_text('"""SPRAG client app mounts."""\n', encoding="utf-8")
        created.append(mounts_init)

    current = mounts_root
    for seg in segments[:-1]:
        current = current / seg
        current.mkdir(parents=True, exist_ok=True)
        init_file = current / "__init__.py"
        if not init_file.exists():
            init_file.write_text(f'"""{seg} mount group."""\n', encoding="utf-8")
            created.append(init_file)

    mount_dir.mkdir(parents=True, exist_ok=True)

    variables = {
        "mount_name": normalized,
        "class_name": class_name,
        "mount_path": mount_path,
        "var_name": var_name,
    }
    created.extend(_render_template_dir(MOUNT_TEMPLATES_ROOT, mount_dir, variables))

    return created


def _pascal_case(segment: str) -> str:
    parts = segment.replace("-", "_").split("_")
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def _validate_new_surface(project_root: Path, path: str, *, kind: str) -> None:
    """Run scaffold-time path checks against discovered manual/generated surfaces."""
    inserted = False
    project_root_str = str(project_root.resolve())
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)
        inserted = True
    try:
        try:
            pages, mounts = discover_surfaces("app.routes", "app.mounts")
        except ModuleNotFoundError as exc:
            if exc.name not in {"app", "app.routes", "app.mounts"}:
                raise
            return
        if kind == "route":
            candidate_pages = pages + [("__new__", Page(path=path, controller=object, screen=object))]
            candidate_mounts = mounts
        else:
            candidate_pages = pages
            candidate_mounts = mounts + [
                ("__new__", Mount(path=path, component=object, module=None, boot=None))
            ]
        validate_surface_paths(candidate_pages, candidate_mounts)
    except ValueError as exc:
        raise SystemExit(f"[SPRAG] {exc}") from exc
    finally:
        if inserted:
            try:
                sys.path.remove(project_root_str)
            except ValueError:
                pass
