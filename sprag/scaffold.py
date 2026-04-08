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

from pathlib import Path

TEMPLATES_ROOT = Path(__file__).parent / "templates"
TEMPLATE_SUFFIX = ".tmpl"
ROUTE_TEMPLATES_ROOT = TEMPLATES_ROOT / "_route"
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


def _pascal_case(segment: str) -> str:
    parts = segment.replace("-", "_").split("_")
    return "".join(p[:1].upper() + p[1:] for p in parts if p)
