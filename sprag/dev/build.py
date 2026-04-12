"""SPRAG web build pipeline."""

from __future__ import annotations

import inspect
import json
import posixpath
import shutil
from pathlib import Path

from .codegen import (
    build_browser_entry,
    emit_generated_files,
    emit_ragot_runtime,
    emit_stores_shim,
)
from .codegen.dependencies import used_browser_class_refs, used_js_import_aliases
from .codegen.mappings import JSCodegenError
from ..runtime.assets import (
    AssetRegistry,
    render_css_links,
    render_script_tags,
    resolve_project_root,
    serialize_module_imports,
)
from ..runtime.request import Request
from ..runtime.routing import build_entries_for_page, normalize_route_path
from ..runtime.rendering import (
    build_document_html,
    build_mount_html,
    build_redirect_html,
    load_controller_data,
    load_mount_data,
    render_screen,
    serializable_hydration,
    store_snapshots,
)
from ..runtime.socket_bridge import surface_socket_enabled
from ..runtime.shell import apply_shell, resolve_effective_surface_modules
from ..runtime.stores import declared_stores


def build_web_preview(pages, output_dir: Path, *, app=None, mounts=None) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    mounts = mounts or []
    route_manifest = []
    mount_manifest = []
    build_errors = []
    root_document = None
    seen_outputs = set()
    asset_registry = AssetRegistry(project_root=resolve_project_root(app))
    asset_registry.include_static_tree()

    for module_name, page in pages:
        try:
            build_entries = build_entries_for_page(page)
        except Exception as exc:
            build_errors.append(
                {
                    "path": page.path,
                    "stage": "static_paths",
                    "error": f"{exc.__class__.__name__}: {exc}",
                }
            )
            continue

        route_slug = _route_slug(page.path)
        route_actions = sorted(page.controller.sprag_actions().keys())
        page_modules = serialize_module_imports(
            resolve_effective_surface_modules(app=app, surface=page)
        )

        for build_entry in build_entries:
            actual_path = normalize_route_path(build_entry.path)
            output_path = _route_web_path(actual_path)
            if output_path in seen_outputs:
                build_errors.append(
                    {
                        "path": actual_path,
                        "stage": "route_conflict",
                        "error": f"Duplicate static output path {output_path!r} while building {page.path!r}.",
                    }
                )
                continue
            seen_outputs.add(output_path)

            page_dir = output_dir / _route_dir(path=actual_path)
            page_dir.mkdir(parents=True, exist_ok=True)

            build_request = Request(path=actual_path, params=build_entry.params, method="BUILD")
            data, data_error, redirect = load_controller_data(page, request=build_request, app=app)
            hydration = []
            render_error = None
            page_meta = _resolved_surface_metadata(page.metadata, data)
            if redirect is None:
                body_html, hydration, render_error = render_screen(page, data)
                body_html, shell_assets = apply_shell(
                    body_html,
                    app=app,
                    surface_shell=getattr(page, "shell", None),
                    document_path=actual_path,
                )
                asset_registry.include(shell_assets)
                shell_head = render_css_links(shell_assets.css, document_path=actual_path)
                shell_scripts = render_script_tags(shell_assets.js, document_path=actual_path)
            else:
                body_html = ""
                shell_head = ""
                shell_scripts = ""

            if data_error:
                build_errors.append({"path": actual_path, "stage": "load", "error": data_error})
            if render_error:
                build_errors.append(
                    {"path": actual_path, "stage": "render", "error": render_error}
                )
            _validate_surface_import_aliases(
                actual_path,
                declared_modules=page_modules,
                browser_classes=_route_browser_classes(hydration, page),
            )

            script_path = _relative_web_path(page_dir, output_dir / "app.js")
            document_html = (
                build_redirect_html(redirect.location)
                if redirect is not None
                else build_document_html(
                    title=_resolved_page_title(page, data, actual_path),
                    body_html=body_html,
                    route_data=data,
                    route_info={
                        "path": actual_path,
                        "mode": page.mode,
                        "name": page.name or route_slug,
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
                        "modules": page_modules,
                        "providers": {k: v.__name__ for k, v in page.providers.items()},
                    },
                    hydration=hydration,
                    script_path=script_path,
                    store_snapshot=store_snapshots(),
                    metadata=page_meta,
                    head_html=shell_head,
                    extra_script_html=shell_scripts,
                )
            )
            (page_dir / "index.html").write_text(document_html, encoding="utf-8")
            if actual_path == "/":
                root_document = document_html

            route_manifest.append(
                {
                    "module": module_name,
                    "path": actual_path,
                    "pattern": page.path,
                    "params": build_entry.params,
                    "mode": page.mode,
                    "name": page.name or route_slug,
                    "controller": page.controller.__name__,
                    "screen": page.screen.__name__,
                    "actions": route_actions,
                    "hydration": hydration,
                    "output": output_path,
                    "modules": page_modules,
                    "providers": {k: v.__name__ for k, v in page.providers.items()},
                    "_provider_classes": list(page.providers.values()),
                }
            )

    for module_name, mt in mounts:
        mount_slug = mt.name or _route_slug(mt.path)
        mount_modules = serialize_module_imports(
            resolve_effective_surface_modules(app=app, surface=mt)
        )
        mount_dir = output_dir / _route_dir(path=mt.path)
        mount_dir.mkdir(parents=True, exist_ok=True)
        mount_actions = sorted(mt.boot.sprag_actions().keys()) if mt.boot else []

        build_request = Request(path=mt.path, method="BUILD")
        data, data_error, redirect = load_mount_data(mt, request=build_request, app=app)
        if data_error:
            build_errors.append({"path": mt.path, "stage": "mount_load", "error": data_error})
        mount_meta = _resolved_surface_metadata(mt.metadata, data)
        if redirect is None:
            body_html, shell_assets = apply_shell(
                '<div id="app-root"></div>',
                app=app,
                surface_shell=getattr(mt, "shell", None),
                document_path=mt.path,
            )
            asset_registry.include(shell_assets)
            shell_head = render_css_links(shell_assets.css, document_path=mt.path)
            shell_scripts = render_script_tags(shell_assets.js, document_path=mt.path)
        else:
            body_html = ""
            shell_head = ""
            shell_scripts = ""
        _validate_surface_import_aliases(
            mt.path,
            declared_modules=mount_modules,
            browser_classes=_mount_browser_classes(mt),
        )

        script_path = _relative_web_path(mount_dir, output_dir / "app.js")
        document_html = (
            build_redirect_html(redirect.location)
            if redirect is not None
            else build_mount_html(
                title=mount_meta.get("title") or mt.name or mt.path,
                mount_info={
                    "path": mt.path,
                    "name": mount_slug,
                    "component": mt.component.__name__,
                    "module": mt.module.__name__ if mt.module else None,
                    "boot": mt.boot.__name__ if mt.boot else None,
                    "actions": mount_actions,
                    "action_endpoint": "/__sprag__/actions",
                    "upload_endpoint": "/__sprag__/uploads",
                    "events_endpoint": "/__sprag__/events",
                    "socket_bridge": surface_socket_enabled(
                        app,
                        mt.boot,
                        surface=mt,
                    ),
                    "dev_reload": bool(getattr(app, "_sprag_dev_reload", False)),
                    "modules": mount_modules,
                    "providers": {k: v.__name__ for k, v in mt.providers.items()},
                },
                boot_data=data,
                script_path=script_path,
                store_snapshot=store_snapshots(),
                body_html=body_html,
                metadata=mount_meta,
                head_html=shell_head,
                extra_script_html=shell_scripts,
            )
        )
        (mount_dir / "index.html").write_text(document_html, encoding="utf-8")
        if mt.path == "/":
            root_document = document_html

        mount_manifest.append(
            {
                "source": module_name,
                "path": mt.path,
                "name": mount_slug,
                "component": mt.component.__name__,
                "module": mt.module.__name__ if mt.module else None,
                "boot": mt.boot.__name__ if mt.boot else None,
                "actions": mount_actions,
                "root_component_class": mt.component,
                "root_module_class": mt.module,
                "output": _route_web_path(mt.path),
                "modules": mount_modules,
                "providers": {k: v.__name__ for k, v in mt.providers.items()},
                "_provider_classes": list(mt.providers.values()),
            }
        )

    asset_registry.emit(output_dir)
    manifest = {
        "routes": route_manifest,
        "mounts": mount_manifest,
        "assets": _serializable_assets(asset_registry.assets()),
        "errors": build_errors,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(_serializable_manifest(manifest), indent=2, sort_keys=True), encoding="utf-8"
    )
    emit_generated_files(
        output_dir,
        _collect_hydration_entries(route_manifest),
        mount_entries=mount_manifest,
        route_entries=route_manifest,
    )
    emit_stores_shim(output_dir, declared_stores())
    emit_ragot_runtime(output_dir, Path(__file__).resolve().parent.parent)
    (output_dir / "app.js").write_text(build_browser_entry(manifest), encoding="utf-8")
    if root_document:
        (output_dir / "index.html").write_text(root_document, encoding="utf-8")
    else:
        (output_dir / "index.html").write_text(_root_index_html(manifest), encoding="utf-8")
    preview_dir = output_dir / "__sprag__"
    if preview_dir.exists():
        shutil.rmtree(preview_dir)
    return manifest


def _root_index_html(manifest):
    routes = manifest.get("routes", [])
    mounts = manifest.get("mounts", [])
    if not routes and not mounts:
        body = "<main><h1>SPRAG</h1><p>No routes or mounts discovered.</p></main>"
    else:
        route_links = "".join(
            f'<li><a href="{route["output"]}">{route["path"]}</a> <small>({route["mode"]})</small></li>'
            for route in routes
        )
        mount_links = "".join(
            f'<li><a href="{mount["output"]}">{mount["path"]}</a> <small>(mount)</small></li>'
            for mount in mounts
        )
        body = (
            "<main><h1>SPRAG Preview</h1>"
            "<p>This preview page lists discovered routes and mounts.</p>"
            f"<ul>{route_links}{mount_links}</ul></main>"
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SPRAG Preview</title>
</head>
<body>{body}</body>
</html>
"""

def _route_slug(path):
    if path == "/":
        return "index"
    return path.strip("/").replace("/", "__")


def _route_dir(path):
    if path == "/":
        return ""
    return path.strip("/")


def _route_web_path(path):
    if path == "/":
        return "/"
    return f"/{path.strip('/')}/"


def _relative_web_path(from_dir, to_file):
    return posixpath.relpath(str(to_file), str(from_dir))


def _serializable_manifest(manifest):
    return {
        "errors": manifest["errors"],
        "assets": manifest.get("assets", []),
        "mounts": [
            {
                key: value
                for key, value in mount.items()
                if key not in {"root_component_class", "root_module_class", "_provider_classes"}
            }
            for mount in manifest.get("mounts", [])
        ],
        "routes": [
            {
                **{k: v for k, v in route.items() if k != "_provider_classes"},
                "hydration": serializable_hydration(route["hydration"]),
            }
            for route in manifest["routes"]
        ],
    }


def _collect_hydration_entries(routes):
    entries = []
    for route in routes:
        entries.extend(route["hydration"])
    return entries


def _serializable_assets(assets) -> list[dict]:
    return [
        {
            "kind": asset.kind,
            "web_path": asset.web_path,
            "external": asset.external,
            "module": asset.module,
        }
        for asset in assets
    ]


def _resolved_page_title(page, data, fallback_path: str) -> str:
    metadata = _resolved_surface_metadata(page.metadata, data)
    return metadata.get("title") or page.name or fallback_path


def _resolved_surface_metadata(static_metadata, data) -> dict:
    metadata = dict(static_metadata or {})
    if isinstance(data, dict):
        dynamic = data.get("__sprag_meta__")
        if isinstance(dynamic, dict):
            metadata.update(dynamic)
    return metadata


def _route_browser_classes(hydration, page) -> set[type]:
    roots = {
        entry.get("component_class")
        for entry in hydration
        if entry.get("component_class") is not None
    }
    roots.update(
        entry.get("module_class")
        for entry in hydration
        if entry.get("module_class") is not None
    )
    roots.update(page.providers.values())
    return _expand_browser_classes(roots)


def _mount_browser_classes(mount) -> set[type]:
    roots = {mount.component}
    if mount.module is not None:
        roots.add(mount.module)
    roots.update(mount.providers.values())
    return _expand_browser_classes(roots)


def _expand_browser_classes(roots) -> set[type]:
    queue = [cls for cls in roots if isinstance(cls, type)]
    seen = set()
    while queue:
        cls = queue.pop(0)
        if cls in seen:
            continue
        seen.add(cls)
        queue.extend(used_browser_class_refs(cls).values())
    return seen


def _validate_surface_import_aliases(surface_path, *, declared_modules, browser_classes) -> None:
    declared_aliases = set((declared_modules or {}).keys())
    for browser_class in sorted(browser_classes, key=lambda value: value.__name__):
        missing = sorted(used_js_import_aliases(browser_class) - declared_aliases)
        if not missing:
            continue
        alias = missing[0]
        raise JSCodegenError(
            f"Unknown SPRAG JS import alias `{alias}`; declare it via page(..., modules={{...}}) or mount(..., modules={{...}}). Surface: {surface_path}",
            source_file=inspect.getsourcefile(browser_class) or inspect.getfile(browser_class),
            class_name=browser_class.__name__,
            suggestion=(
                "Add the alias on App(..., modules=...), shell(..., modules=...), "
                "page(..., modules=...), or mount(..., modules=...)."
            ),
        )
