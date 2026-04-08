"""SPRAG web build pipeline."""

from __future__ import annotations

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
from .request import Request
from .runtime import (
    build_document_html,
    build_mount_html,
    load_controller_data,
    load_mount_data,
    render_screen,
    serializable_hydration,
    store_snapshots,
)
from .shell import apply_shell
from .stores import declared_stores


def build_web_preview(pages, output_dir: Path, *, app=None, mounts=None) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    mounts = mounts or []
    route_manifest = []
    mount_manifest = []
    build_errors = []
    root_document = None

    for module_name, page in pages:
        route_slug = _route_slug(page.path)
        page_dir = output_dir / _route_dir(path=page.path)
        page_dir.mkdir(parents=True, exist_ok=True)
        route_actions = sorted(page.controller.sprag_actions().keys())

        build_request = Request(path=page.path, method="BUILD")
        data, data_error = load_controller_data(page, request=build_request, app=app)
        body_html, hydration, render_error = render_screen(page, data)
        body_html, shell_head = apply_shell(
            body_html,
            app=app,
            surface_shell=getattr(page, "shell", None),
        )

        if data_error:
            build_errors.append({"path": page.path, "stage": "load", "error": data_error})
        if render_error:
            build_errors.append(
                {"path": page.path, "stage": "render", "error": render_error}
            )

        script_path = _relative_web_path(page_dir, output_dir / "app.js")
        document_html = build_document_html(
            title=page.metadata.get("title") or page.name or page.path,
            body_html=body_html,
            route_data=data,
            route_info={
                "path": page.path,
                "mode": page.mode,
                "name": page.name or route_slug,
                "controller": page.controller.__name__,
                "actions": route_actions,
                "action_endpoint": "/__sprag__/actions",
                "events_endpoint": "/__sprag__/events",
            },
            hydration=hydration,
            script_path=script_path,
            store_snapshot=store_snapshots(),
            head_html=shell_head,
        )
        (page_dir / "index.html").write_text(document_html, encoding="utf-8")
        if page.path == "/":
            root_document = document_html

        route_manifest.append(
            {
                "module": module_name,
                "path": page.path,
                "mode": page.mode,
                "name": page.name or route_slug,
                "controller": page.controller.__name__,
                "screen": page.screen.__name__,
                "actions": route_actions,
                "hydration": hydration,
                "output": _route_web_path(page.path),
            }
        )

    for module_name, mt in mounts:
        mount_slug = mt.name or _route_slug(mt.path)
        mount_dir = output_dir / _route_dir(path=mt.path)
        mount_dir.mkdir(parents=True, exist_ok=True)
        mount_actions = sorted(mt.boot.sprag_actions().keys()) if mt.boot else []

        build_request = Request(path=mt.path, method="BUILD")
        data, data_error = load_mount_data(mt, request=build_request, app=app)
        if data_error:
            build_errors.append({"path": mt.path, "stage": "mount_load", "error": data_error})
        body_html, shell_head = apply_shell(
            '<div id="app-root"></div>',
            app=app,
            surface_shell=getattr(mt, "shell", None),
        )

        script_path = _relative_web_path(mount_dir, output_dir / "app.js")
        document_html = build_mount_html(
            title=mt.metadata.get("title") or mt.name or mt.path,
            mount_info={
                "path": mt.path,
                "name": mount_slug,
                "component": mt.component.__name__,
                "module": mt.module.__name__ if mt.module else None,
                "boot": mt.boot.__name__ if mt.boot else None,
                "actions": mount_actions,
                "action_endpoint": "/__sprag__/actions",
                "events_endpoint": "/__sprag__/events",
            },
            boot_data=data,
            script_path=script_path,
            store_snapshot=store_snapshots(),
            body_html=body_html,
            head_html=shell_head,
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
            }
        )

    manifest = {"routes": route_manifest, "mounts": mount_manifest, "errors": build_errors}
    (output_dir / "manifest.json").write_text(
        json.dumps(_serializable_manifest(manifest), indent=2, sort_keys=True), encoding="utf-8"
    )
    emit_generated_files(
        output_dir,
        _collect_hydration_entries(route_manifest),
        mount_entries=mount_manifest,
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
        "mounts": [
            {
                key: value
                for key, value in mount.items()
                if key not in {"root_component_class", "root_module_class"}
            }
            for mount in manifest.get("mounts", [])
        ],
        "routes": [
            {**route, "hydration": serializable_hydration(route["hydration"])}
            for route in manifest["routes"]
        ],
    }


def _collect_hydration_entries(routes):
    entries = []
    for route in routes:
        entries.extend(route["hydration"])
    return entries
