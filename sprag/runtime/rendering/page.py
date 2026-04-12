"""Per-request page rendering for SPRAG.

This module contains the rendering logic that runs on every GET request
in the live server, as well as at build time for pre-rendered HTML.
"""

from __future__ import annotations

import hashlib
import html
import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path

from .tree import render_tree
from ..assets import render_css_links, render_preload_hints, render_script_tags, serialize_module_imports
from ..env import public_env
from ..request import Request
from ..routing import normalize_route_path
from ..socket_bridge import surface_socket_enabled
from ..server import Redirect, controller_context
from ..shell import apply_shell, resolve_effective_surface_modules
from ..stores import declared_stores, store_fingerprint


@dataclass
class PageResult:
    """Result of rendering a single page."""

    html: str
    body_html: str
    data: dict
    hydration: list[dict]
    data_error: str | None = None
    render_error: str | None = None
    redirect: Redirect | None = None


@dataclass
class MountResult:
    """Result of rendering a client app mount boot document."""

    html: str
    data: dict
    data_error: str | None = None
    redirect: Redirect | None = None


def render_page(page, *, request: Request | None = None, app=None, script_path: str = "/app.js") -> PageResult:
    """Render a page through its controller and screen, returning full HTML."""
    request = request or Request(path=normalize_route_path(page.path), method="GET")
    with _ensure_app_booted(app):
        data, data_error, redirect = load_controller_data(page, request=request, app=app)
        if redirect is not None:
            return PageResult(
                html=build_redirect_html(redirect.location),
                body_html="",
                data={},
                hydration=[],
                data_error=data_error,
                render_error=None,
                redirect=redirect,
            )
        body_html, hydration, render_error = render_screen(page, data)
        page_meta = _resolved_surface_metadata(page.metadata, data)
        surface_modules = serialize_module_imports(
            resolve_effective_surface_modules(app=app, surface=page)
        )
        body_html, shell_assets = apply_shell(
            body_html,
            app=app,
            surface_shell=getattr(page, "shell", None),
        )
        shell_head = render_css_links(shell_assets.css)
        shell_scripts = render_script_tags(shell_assets.js)
        preload_html = render_preload_hints(shell_assets.css, script_path=script_path)

        route_slug = page.name or _route_slug(page.path)
        route_actions = sorted(page.controller.sprag_actions().keys())

        document = build_document_html(
            title=page_meta.get("title") or page.name or request.path,
            body_html=body_html,
            route_data=data,
            route_info={
                "path": request.path,
                "mode": page.mode,
                "name": route_slug,
                "controller": page.controller.__name__,
                "actions": route_actions,
                "action_endpoint": "/__sprag__/actions",
                "upload_endpoint": "/__sprag__/uploads",
                "events_endpoint": "/__sprag__/events",
                "socket_bridge": surface_socket_enabled(
                    app,
                    page.controller,
                    surface=page,
                ),
                "dev_reload": bool(getattr(app, "_sprag_dev_reload", False)),
                "modules": surface_modules,
                "providers": {key: value.__name__ for key, value in page.providers.items()},
            },
            hydration=hydration,
            script_path=script_path,
            store_snapshot=store_snapshots(),
            metadata=page_meta,
            head_html=shell_head,
            extra_script_html=shell_scripts,
            preload_html=preload_html,
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
    with _ensure_app_booted(app):
        data, data_error, redirect = load_mount_data(mount, request=request, app=app)
        if redirect is not None:
            return MountResult(
                html=build_redirect_html(redirect.location),
                data={},
                data_error=data_error,
                redirect=redirect,
            )

        mount_slug = mount.name or _route_slug(mount.path)
        boot_actions = sorted(mount.boot.sprag_actions().keys()) if mount.boot else []
        surface_modules = serialize_module_imports(
            resolve_effective_surface_modules(app=app, surface=mount)
        )
        mount_info = {
            "path": mount.path,
            "name": mount_slug,
            "component": mount.component.__name__,
            "module": mount.module.__name__ if mount.module else None,
            "boot": mount.boot.__name__ if mount.boot else None,
            "actions": boot_actions,
            "action_endpoint": "/__sprag__/actions",
            "upload_endpoint": "/__sprag__/uploads",
            "events_endpoint": "/__sprag__/events",
            "socket_bridge": surface_socket_enabled(
                app,
                mount.boot,
                surface=mount,
            ),
            "dev_reload": bool(getattr(app, "_sprag_dev_reload", False)),
            "modules": surface_modules,
            "providers": {key: value.__name__ for key, value in mount.providers.items()},
        }

        mount_meta = _resolved_surface_metadata(mount.metadata, data)
        body_html, shell_assets = apply_shell(
            '<div id="app-root"></div>',
            app=app,
            surface_shell=getattr(mount, "shell", None),
        )
        shell_head = render_css_links(shell_assets.css)
        shell_scripts = render_script_tags(shell_assets.js)
        preload_html = render_preload_hints(shell_assets.css, script_path=script_path)

        document = build_mount_html(
            title=mount_meta.get("title") or mount.name or mount.path,
            mount_info=mount_info,
            boot_data=data,
            script_path=script_path,
            store_snapshot=store_snapshots(),
            body_html=body_html,
            metadata=mount_meta,
            head_html=shell_head,
            extra_script_html=shell_scripts,
            preload_html=preload_html,
        )

        return MountResult(html=document, data=data, data_error=data_error)


@contextmanager
def _ensure_app_booted(app):
    if app is None or not hasattr(app, "boot") or getattr(app, "_booted", False):
        yield
        return
    app.boot()
    try:
        yield
    finally:
        if getattr(app, "_booted", False):
            app.shutdown()


def load_controller_data(
    page, *, request: Request | None = None, app=None
) -> tuple[dict, str | None, Redirect | None]:
    """Load route data through the lifecycle-owned page controller."""
    if app is not None and hasattr(app, "controller_for_page"):
        controller = app.controller_for_page(page)
    else:
        controller = page.controller()
    try:
        with controller_context(request=request, app=app):
            data = controller.load()
        if isinstance(data, Redirect):
            return {}, None, data
        return (data if isinstance(data, dict) else {"value": data}), None, None
    except Redirect as exc:
        return {}, None, exc
    except Exception as exc:
        return {}, f"{exc.__class__.__name__}: {exc}", None


def load_mount_data(
    mount, *, request: Request | None = None, app=None
) -> tuple[dict, str | None, Redirect | None]:
    """Load boot data through the lifecycle-owned mount controller."""
    if mount.boot is None:
        return {}, None, None
    if app is not None and hasattr(app, "controller_for_mount"):
        controller = app.controller_for_mount(mount)
    else:
        controller = mount.boot()
    try:
        with controller_context(request=request, app=app):
            data = controller.load()
        if isinstance(data, Redirect):
            return {}, None, data
        return (data if isinstance(data, dict) else {"value": data}), None, None
    except Redirect as exc:
        return {}, None, exc
    except Exception as exc:
        return {}, f"{exc.__class__.__name__}: {exc}", None


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
    metadata: dict | None = None,
    head_html: str = "",
    extra_script_html: str = "",
    preload_html: str = "",
):
    """Build a full HTML document for a SPRAG page.

    ``store_snapshot`` is a ``{store_name: snapshot}`` mapping captured at
    render time. It is injected into ``window.__SPRAG_PAYLOAD__.stores`` so
    the generated ``stores.js`` shim can hydrate each store bridge from the
    same state the server just rendered against.
    """
    store_snap = store_snapshot or {}
    head_bits = _join_head_html(preload_html, _render_metadata_tags(metadata), head_html)
    escaped_title = html.escape(str(title))
    dev_reload = bool(route_info.get("dev_reload"))
    hot_reload_script_tag = ""
    if dev_reload:
        store_contract_fingerprint = store_fingerprint()
        surface_fingerprint = _surface_fingerprint("route", route_info, hydration=hydration)
        hot_reload_script = _build_hot_reload_restore_script(
            route_info.get("path") or "/",
            store_contract_fingerprint,
            surface_fingerprint,
        )
        hot_reload_script_tag = f'\n  <script>{hot_reload_script}</script>'

    payload = {
        "page": route_info,
        "routeData": _json_safe(route_data),
        "hydration": serializable_hydration(hydration),
        "stores": store_snap,
        "env": public_env(),
    }
    if dev_reload:
        payload["fingerprints"] = {
            "store": store_contract_fingerprint,
            "surface": surface_fingerprint,
        }

    extra_scripts = f"\n  {extra_script_html}" if extra_script_html else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  {head_bits}
</head>
<body>
  <div id="app-root">{body_html}</div>
  <script>
    window.__SPRAG_PAYLOAD__ = {json.dumps(payload, sort_keys=True)};
  </script>{hot_reload_script_tag}{extra_scripts}
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
    body_html: str | None = None,
    metadata: dict | None = None,
    head_html: str = "",
    extra_script_html: str = "",
    preload_html: str = "",
):
    """Build the HTML boot document for a client app mount."""
    store_snap = store_snapshot or {}
    head_bits = _join_head_html(preload_html, _render_metadata_tags(metadata), head_html)
    escaped_title = html.escape(str(title))
    dev_reload = bool(mount_info.get("dev_reload"))
    hot_reload_script_tag = ""
    if dev_reload:
        store_contract_fingerprint = store_fingerprint()
        surface_fingerprint = _surface_fingerprint("mount", mount_info)
        hot_reload_script = _build_hot_reload_restore_script(
            mount_info.get("path") or "/",
            store_contract_fingerprint,
            surface_fingerprint,
        )
        hot_reload_script_tag = f'\n  <script>{hot_reload_script}</script>'

    payload = {
        "mount": mount_info,
        "page": mount_info,
        "boot": _json_safe(boot_data),
        "routeData": _json_safe(boot_data),
        "hydration": [],
        "stores": store_snap,
        "env": public_env(),
    }
    if dev_reload:
        payload["fingerprints"] = {
            "store": store_contract_fingerprint,
            "surface": surface_fingerprint,
        }
    extra_scripts = f"\n  {extra_script_html}" if extra_script_html else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  {head_bits}
</head>
<body>
  {body_html or '<div id="app-root"></div>'}
  <script>
    window.__SPRAG_PAYLOAD__ = {json.dumps(payload, sort_keys=True)};
  </script>{hot_reload_script_tag}{extra_scripts}
  <script type="module" src="{script_path}"></script>
</body>
</html>
"""


def build_redirect_html(location: str, *, title: str = "Redirecting") -> str:
    """Build a minimal static-safe redirect document."""
    escaped_title = html.escape(str(title))
    escaped_location = html.escape(str(location), quote=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="0; url={escaped_location}">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
</head>
<body>
  <p>Redirecting to <a href="{escaped_location}">{escaped_location}</a>...</p>
</body>
</html>
"""


def store_snapshots() -> dict:
    """Return ``{name: snapshot}`` for every declared store.

    Called at render time so the document can ship a hydration payload
    matching the exact state the server-side rendering observed. Stores that
    have never been touched still emit their declared initial state.
    """
    return {bridge.name: bridge.snapshot() for bridge in declared_stores()}


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


def _build_hot_reload_restore_script(
    surface_path: str,
    store_contract_fingerprint: str,
    surface_fingerprint: str,
) -> str:
    cache_key = f"sprag:reload:{surface_path or '/'}"
    return f"""(function() {{
    var cacheKey = {json.dumps(cache_key)};
    var expectedStoreFingerprint = {json.dumps(store_contract_fingerprint)};
    var expectedSurfaceFingerprint = {json.dumps(surface_fingerprint)};
    window.__SPRAG_HOT_RELOAD_KEY__ = cacheKey;
    try {{
        if (!window.sessionStorage) return;
        var raw = window.sessionStorage.getItem(cacheKey);
        if (!raw) return;
        var cached = JSON.parse(raw);
        if (
            !cached
            || cached.store_fingerprint !== expectedStoreFingerprint
            || cached.surface_fingerprint !== expectedSurfaceFingerprint
        ) {{
            window.sessionStorage.removeItem(cacheKey);
            return;
        }}
        if (cached.stores && typeof cached.stores === 'object') {{
            window.__SPRAG_PAYLOAD__.stores = cached.stores;
        }}
        if (
            cached.surface_kind === 'route'
            && Array.isArray(cached.hydration)
            && window.__SPRAG_PAYLOAD__
            && Array.isArray(window.__SPRAG_PAYLOAD__.hydration)
        ) {{
            var byId = {{}};
            for (var i = 0; i < cached.hydration.length; i += 1) {{
                var saved = cached.hydration[i];
                if (saved && saved.id) {{
                    byId[saved.id] = saved;
                }}
            }}
            window.__SPRAG_PAYLOAD__.hydration = window.__SPRAG_PAYLOAD__.hydration.map(function(entry) {{
                var restored = byId[entry.id];
                if (!restored) return entry;
                return {{
                    ...entry,
                    props: restored.props !== undefined ? restored.props : entry.props,
                    state: restored.state !== undefined ? restored.state : entry.state,
                    module_state: (
                        restored.module_state !== undefined
                            ? restored.module_state
                            : entry.module_state
                    ),
                }};
            }});
        }}
        if (
            cached.surface_kind === 'mount'
            && cached.boot_data
            && typeof cached.boot_data === 'object'
            && window.__SPRAG_PAYLOAD__
        ) {{
            window.__SPRAG_PAYLOAD__.boot = cached.boot_data;
            window.__SPRAG_PAYLOAD__.routeData = cached.boot_data;
        }}
        window.sessionStorage.removeItem(cacheKey);
    }} catch (_error) {{
        try {{
            if (window.sessionStorage) {{
                window.sessionStorage.removeItem(cacheKey);
            }}
        }} catch (_innerError) {{}}
    }}
}})();"""


def _surface_fingerprint(kind: str, info: dict, *, hydration: list[dict] | None = None) -> str:
    payload = {
        "kind": kind,
        "path": info.get("path"),
        "name": info.get("name"),
        "mode": info.get("mode"),
        "controller": info.get("controller"),
        "component": info.get("component"),
        "module": info.get("module"),
        "boot": info.get("boot"),
        "actions": sorted(info.get("actions") or []),
        "hydration": [
            {
                "id": entry.get("id"),
                "component": entry.get("component"),
                "module": entry.get("module"),
            }
            for entry in (hydration or [])
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _resolved_surface_metadata(static_metadata, data) -> dict:
    metadata = dict(static_metadata or {})
    if isinstance(data, dict):
        dynamic = data.get("__sprag_meta__")
        if isinstance(dynamic, dict):
            metadata.update(dynamic)
    return metadata


def _render_metadata_tags(metadata) -> str:
    if not metadata:
        return ""

    tags = []
    for key, value in metadata.items():
        if key == "title" or value is None or value == "":
            continue
        content = _metadata_content(value)
        if not content:
            continue
        escaped_content = html.escape(content, quote=True)
        if key == "canonical":
            tags.append(f'<link rel="canonical" href="{escaped_content}">')
            continue
        escaped_key = html.escape(str(key), quote=True)
        attr = "property" if str(key).startswith("og:") else "name"
        tags.append(f'<meta {attr}="{escaped_key}" content="{escaped_content}">')
    return "\n  ".join(tags)


def _metadata_content(value) -> str:
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value if item is not None and item != "")
    return str(value)


def _join_head_html(*chunks: str) -> str:
    return "\n  ".join(chunk for chunk in chunks if chunk)


def _json_safe(value):
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _route_slug(path):
    if path == "/":
        return "index"
    return path.strip("/").replace("/", "__")
