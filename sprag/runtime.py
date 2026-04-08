"""Per-request page rendering for SPRAG.

This module contains the rendering logic that runs on every GET request
in the live server, as well as at build time for pre-rendered HTML.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .render import render_tree
from .request import Request
from .stores import declared_stores


@dataclass
class PageResult:
    """Result of rendering a single page."""

    html: str
    body_html: str
    data: dict
    hydration: list[dict]
    data_error: str | None = None
    render_error: str | None = None


@dataclass
class MountResult:
    """Result of rendering a client app mount boot document."""

    html: str
    data: dict
    data_error: str | None = None


def render_page(page, *, request: Request | None = None, app=None, script_path: str = "/app.js") -> PageResult:
    """Render a page through its controller and screen, returning full HTML."""
    data, data_error = load_controller_data(page, request=request, app=app)
    body_html, hydration, render_error = render_screen(page, data)

    route_slug = page.name or _route_slug(page.path)
    route_actions = sorted(page.controller.sprag_actions().keys())

    document = build_document_html(
        title=page.metadata.get("title") or page.name or page.path,
        body_html=body_html,
        route_data=data,
        route_info={
            "path": page.path,
            "mode": page.mode,
            "name": route_slug,
            "controller": page.controller.__name__,
            "actions": route_actions,
            "action_endpoint": "/__sprag__/actions",
            "events_endpoint": "/__sprag__/events",
            "socket_endpoint": getattr(page.controller, "socket_endpoint", None),
        },
        hydration=hydration,
        script_path=script_path,
        store_snapshot=store_snapshots(),
    )

    return PageResult(
        html=document,
        body_html=body_html,
        data=data,
        hydration=hydration,
        data_error=data_error,
        render_error=render_error,
    )


def render_mount(mount, *, request: Request | None = None, app=None, script_path: str = "/app.js") -> MountResult:
    """Render the boot document for a client app mount."""
    data, data_error = load_mount_data(mount, request=request, app=app)

    mount_slug = mount.name or _route_slug(mount.path)
    boot_actions = sorted(mount.boot.sprag_actions().keys()) if mount.boot else []
    mount_info = {
        "path": mount.path,
        "name": mount_slug,
        "component": mount.component.__name__,
        "module": mount.module.__name__ if mount.module else None,
        "boot": mount.boot.__name__ if mount.boot else None,
        "actions": boot_actions,
        "action_endpoint": "/__sprag__/actions",
        "events_endpoint": "/__sprag__/events",
    }

    document = build_mount_html(
        title=mount.metadata.get("title") or mount.name or mount.path,
        mount_info=mount_info,
        boot_data=data,
        script_path=script_path,
        store_snapshot=store_snapshots(),
    )

    return MountResult(html=document, data=data, data_error=data_error)


def load_controller_data(page, *, request: Request | None = None, app=None) -> tuple[dict, str | None]:
    """Instantiate a controller and call load(), returning (data, error)."""
    controller = page.controller()
    controller.request = request
    controller.app = app
    try:
        data = controller.load()
        return (data if isinstance(data, dict) else {"value": data}), None
    except Exception as exc:
        return {}, f"{exc.__class__.__name__}: {exc}"


def load_mount_data(mount, *, request: Request | None = None, app=None) -> tuple[dict, str | None]:
    """Instantiate a mount boot controller and call load(), returning (data, error)."""
    if mount.boot is None:
        return {}, None
    controller = mount.boot()
    controller.request = request
    controller.app = app
    try:
        data = controller.load()
        return (data if isinstance(data, dict) else {"value": data}), None
    except Exception as exc:
        return {}, f"{exc.__class__.__name__}: {exc}"


def render_screen(page, data) -> tuple[str, list[dict], str | None]:
    """Instantiate a screen and render it, returning (html, hydration, error)."""
    screen = page.screen(data=data)
    try:
        tree = screen.render(data)
        rendered = render_tree(tree)
        return rendered.html, rendered.hydration, None
    except Exception as exc:
        error_html = (
            "<main><h1>SPRAG render error</h1>"
            f"<pre>{exc.__class__.__name__}: {exc}</pre></main>"
        )
        return error_html, [], f"{exc.__class__.__name__}: {exc}"


def build_document_html(
    *,
    title,
    body_html,
    route_data,
    route_info,
    hydration,
    script_path,
    store_snapshot: dict | None = None,
):
    """Build a full HTML document for a SPRAG page.

    ``store_snapshot`` is a ``{store_name: snapshot}`` mapping captured at
    render time. It is injected as ``window.__SPRAG_STORES__`` so the
    generated ``stores.js`` shim can hydrate each Ragot ``createStateStore``
    from the same state the server just rendered against.
    """
    snapshot = store_snapshot or {}
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
</head>
<body>
  <div id="app-root">{body_html}</div>
  <script>
    window.__SPRAG_PAGE__ = {json.dumps(route_info, sort_keys=True)};
    window.__SPRAG_ROUTE_DATA__ = {json.dumps(route_data, sort_keys=True)};
    window.__SPRAG_HYDRATION__ = {json.dumps(serializable_hydration(hydration), sort_keys=True)};
    window.__SPRAG_STORES__ = {json.dumps(snapshot, sort_keys=True)};
  </script>
  <script type="module" src="{script_path}"></script>
</body>
</html>
"""


def build_mount_html(
    *,
    title,
    mount_info,
    boot_data,
    script_path,
    store_snapshot: dict | None = None,
):
    """Build the HTML boot document for a client app mount."""
    snapshot = store_snapshot or {}
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
</head>
<body>
  <div id="app-root"></div>
  <script>
    window.__SPRAG_MOUNT__ = {json.dumps(mount_info, sort_keys=True)};
    window.__SPRAG_PAGE__ = {json.dumps(mount_info, sort_keys=True)};
    window.__SPRAG_BOOT__ = {json.dumps(boot_data, sort_keys=True)};
    window.__SPRAG_ROUTE_DATA__ = {json.dumps(boot_data, sort_keys=True)};
    window.__SPRAG_HYDRATION__ = [];
    window.__SPRAG_STORES__ = {json.dumps(snapshot, sort_keys=True)};
  </script>
  <script type="module" src="{script_path}"></script>
</body>
</html>
"""


def store_snapshots() -> dict:
    """Return ``{name: snapshot}`` for every declared store.

    Called at render time so the document can ship a hydration payload
    matching the exact state the server-side rendering observed. Stores that
    have never been touched still emit their declared initial state.
    """
    return {bridge.name: bridge.get_state() for bridge in declared_stores()}


def serializable_hydration(hydration_entries):
    """Strip non-serializable fields from hydration entries."""
    return [
        {
            "id": entry["id"],
            "component": entry["component"],
            "module": entry["module"],
            "props": entry["props"],
            "state": entry["state"],
            "module_state": entry["module_state"],
        }
        for entry in hydration_entries
    ]


def _route_slug(path):
    if path == "/":
        return "index"
    return path.strip("/").replace("/", "__")
