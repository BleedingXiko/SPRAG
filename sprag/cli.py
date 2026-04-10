"""SPRAG CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

from . import __version__
from .http_server import SERVER_MODES, resolve_server_mode, serve_sprag_app
from .loader import load_app
from .package import build_dist_bundle
from .scaffold import (
    DEFAULT_ROUTE_MODE,
    ROUTE_MODES,
    available_templates,
    scaffold_project,
    scaffold_mount,
    scaffold_route,
)
from .server import bus
from .stores import store_fingerprint


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.func(args)


_SUBCOMMAND_HELP = {
    "build": "Build the app into a deployable artifact",
    "routes": "List all discovered routes with actions and schemas",
    "dev": "Start the dev server with file watching",
}


def _build_parser():
    parser = argparse.ArgumentParser(prog="sprag", description="SPRAG framework CLI")
    parser.add_argument("--version", action="version", version=f"sprag {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, help_text in _SUBCOMMAND_HELP.items():
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--app", dest="app_target", default=None)
        sub.add_argument("--project-root", default=os.getcwd())
        sub.add_argument("--output", default="dist" if name == "build" else ".sprag")
        if name == "dev":
            sub.add_argument("--port", type=int, default=8000)
            sub.add_argument("--interval", type=float, default=1.0)
            sub.add_argument(
                "--server-mode",
                choices=SERVER_MODES,
                default=None,
                help=(
                    "Server transport mode. 'wsgi' uses plain gevent WSGI; "
                    "'websocket' uses a GhostHub-style websocket-capable gevent handler."
                ),
            )
        sub.set_defaults(func=globals()[f"cmd_{name}"])

    new_parser = subparsers.add_parser("new", help="Create a new SPRAG project")
    new_parser.add_argument("name")
    new_parser.add_argument("--output-dir", default=os.getcwd())
    new_parser.add_argument(
        "--template",
        default="default",
        help=(
            "Template to scaffold from (default: 'default'). "
            f"Available: {', '.join(available_templates()) or 'none'}"
        ),
    )
    new_parser.set_defaults(func=cmd_new)

    add_parser = subparsers.add_parser("add", help="Add a route or mount to the current project")
    add_parser.add_argument(
        "kind_or_name",
        help=(
            "Use 'route <name>' or 'mount <name>'. Back-compat: a bare name "
            "is treated as 'route <name>'."
        ),
    )
    add_parser.add_argument("name", nargs="?", help="Route or mount name, e.g. 'dashboard' or 'admin/users'")
    add_parser.add_argument("--project-root", default=os.getcwd())
    add_parser.add_argument(
        "--mode",
        choices=ROUTE_MODES,
        default=None,
        help=(
            f"Render mode for the new route (default: {DEFAULT_ROUTE_MODE!r}). "
            "'document' scaffolds a pure SSR route; 'hybrid' adds a Module "
            "and a working @action round trip."
        ),
    )
    add_parser.set_defaults(func=cmd_add)

    return parser


def cmd_routes(args):
    from .server import Controller as SpragController
    from .web import Screen as SpragScreen

    app_target, app = _load_cli_app(args)
    pages = app.pages()
    mounts = app.mounts()
    print(f"[SPRAG] app: {app_target}")

    warnings = []
    for module_name, page in pages:
        print(f"{page.path} [{page.mode}] -> {page.controller.__name__} / {page.screen.__name__}")
        actions = page.controller.sprag_actions()
        for action_name, action_fn in sorted(actions.items()):
            meta = getattr(action_fn, "_sprag_action_meta", None) or {}
            schema = meta.get("schema")
            if schema is not None:
                fields_desc = ", ".join(
                    f"{fname}: {f.type.__name__}{' [required]' if f.required else ''}"
                    for fname, f in schema._fields.items()
                ) if hasattr(schema, "_fields") else ""
                print(f"  @{action_name}({fields_desc})")
            else:
                print(f"  @{action_name}()")

        if not (isinstance(page.controller, type) and issubclass(page.controller, SpragController)):
            warnings.append(f"  {module_name}: controller {page.controller.__name__} is not a sprag.Controller subclass")
        if not (isinstance(page.screen, type) and issubclass(page.screen, SpragScreen)):
            warnings.append(f"  {module_name}: screen {page.screen.__name__} is not a sprag.Screen subclass")
        controller_route = getattr(page.controller, "route", None)
        if controller_route is not None and controller_route != page.path:
            warnings.append(
                f"  {module_name}: controller.route={controller_route!r} does not match page.path={page.path!r}"
            )

    for module_name, mount in mounts:
        boot_name = mount.boot.__name__ if mount.boot else "(none)"
        module_name_js = mount.module.__name__ if mount.module else "(none)"
        print(f"{mount.path} [mount] -> {mount.component.__name__} / {module_name_js} / {boot_name}")
        if mount.boot is not None:
            actions = mount.boot.sprag_actions()
            for action_name, action_fn in sorted(actions.items()):
                meta = getattr(action_fn, "_sprag_action_meta", None) or {}
                schema = meta.get("schema")
                if schema is not None:
                    fields_desc = ", ".join(
                        f"{fname}: {f.type.__name__}{' [required]' if f.required else ''}"
                        for fname, f in schema._fields.items()
                    ) if hasattr(schema, "_fields") else ""
                    print(f"  @{action_name}({fields_desc})")
                else:
                    print(f"  @{action_name}()")

    if warnings:
        print()
        print("[SPRAG] warnings:")
        for w in warnings:
            print(w)


def cmd_build(args):
    app_target, app = _load_cli_app(args)
    dist = build_dist_bundle(
        app_target,
        app,
        output_dir=Path(args.output),
        project_root=Path(args.project_root),
    )
    print(f"[SPRAG] app: {app_target}")
    print(json.dumps(dist, indent=2, sort_keys=True))


def cmd_dev(args):
    app_target, app = _load_cli_app(args)
    setattr(app, "_sprag_dev_reload", True)
    output_dir = Path(args.output)
    _build_once(app, output_dir)
    resolved_server_mode = resolve_server_mode(app, args.server_mode)

    stop_event = threading.Event()
    watcher = threading.Thread(
        target=_watch_loop,
        args=(app, output_dir, Path(args.project_root), args.interval, stop_event),
        daemon=True,
    )
    watcher.start()

    pages = app.pages()
    mounts = app.mounts()
    banner = [
        f"[SPRAG] app: {app_target}",
        f"[SPRAG] dev server running at http://127.0.0.1:{args.port}/",
        f"[SPRAG] server mode: {resolved_server_mode}",
        "",
        "  Routes:",
    ]
    if pages:
        path_width = max(len(pg.path) for _m, pg in pages)
        mode_width = max(len(pg.mode) for _m, pg in pages)
        for _module_name, pg in pages:
            banner.append(
                f"    {pg.path.ljust(path_width)}  [{pg.mode.ljust(mode_width)}]  -> {pg.controller.__name__}"
            )
    else:
        banner.append("    (none)")
    banner.append("")
    banner.append("  Mounts:")
    if mounts:
        path_width = max(len(mt.path) for _m, mt in mounts)
        for _module_name, mt in mounts:
            module_name_js = mt.module.__name__ if mt.module else "(none)"
            banner.append(
                f"    {mt.path.ljust(path_width)}  [mount]  -> {mt.component.__name__} / {module_name_js}"
            )
    else:
        banner.append("    (none)")
    banner.append("")
    banner.append("[SPRAG] pages render dynamically on each request")

    try:
        serve_sprag_app(
            app,
            output_dir,
            host="127.0.0.1",
            port=args.port,
            banner=banner,
            server_mode=args.server_mode,
        )
    except KeyboardInterrupt:
        print("\n[SPRAG] stopping dev server")
    finally:
        stop_event.set()


def cmd_new(args):
    target_dir = Path(args.output_dir).resolve() / args.name
    if target_dir.exists() and any(target_dir.iterdir()):
        raise SystemExit(f"[SPRAG] target directory already exists and is not empty: {target_dir}")
    created = scaffold_project(target_dir, args.name, template=args.template)
    print(f"[SPRAG] created project at {target_dir} (template: {args.template})")
    for path in created:
        print(path.relative_to(target_dir))


def cmd_add(args):
    project_root = Path(args.project_root).resolve()
    if not (project_root / "app" / "__init__.py").exists():
        raise SystemExit(
            f"[SPRAG] not inside a SPRAG project (no app/__init__.py at {project_root})"
        )

    kind, name = _parse_add_target(args)
    normalized = name.strip("/")

    if kind == "mount":
        if args.mode is not None:
            raise SystemExit("[SPRAG] --mode is only valid for 'sprag add route', not 'sprag add mount'.")
        created = scaffold_mount(project_root, name)
        print(f"[SPRAG] created mount '{normalized}' at app/mounts/{normalized}/")
        for path in created:
            print(f"  {path.relative_to(project_root)}")
        print()
        print("Edit your mount:")
        print(f"  app/mounts/{normalized}/server.py   # boot data")
        print(f"  app/mounts/{normalized}/web.py      # root Component")
        print(f"  app/mounts/{normalized}/modules.py  # root Module")
        print(f"  app/mounts/{normalized}/mount.py    # mount manifest")
        print()
        print("Then run: sprag dev")
        return

    mode = args.mode or DEFAULT_ROUTE_MODE
    created = scaffold_route(project_root, name, mode=mode)
    print(f"[SPRAG] created {mode}-mode route '{normalized}' at app/routes/{normalized}/")
    for path in created:
        print(f"  {path.relative_to(project_root)}")
    print()
    print("Edit your route:")
    print(f"  app/routes/{normalized}/server.py      # controller logic")
    print(f"  app/routes/{normalized}/web.py         # screen layout")
    print(f"  app/routes/{normalized}/components.py  # UI components")
    if mode == "hybrid":
        print(f"  app/routes/{normalized}/modules.py     # browser-side Module")
    print()
    print("Then run: sprag dev")


def _parse_add_target(args):
    if args.kind_or_name in {"route", "mount"}:
        if not args.name:
            raise SystemExit(f"[SPRAG] missing name for 'sprag add {args.kind_or_name}'.")
        return args.kind_or_name, args.name
    if args.name is not None:
        raise SystemExit(
            "[SPRAG] expected 'sprag add route <name>' or 'sprag add mount <name>'. "
            "For backward compatibility, 'sprag add <name>' is also accepted."
        )
    return "route", args.kind_or_name


def _load_cli_app(args):
    project_root = os.path.abspath(args.project_root)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    app_target, app = load_app(args.app_target)
    return app_target, app


def _build_once(app, output_dir):
    start = time.monotonic()
    manifest = app.build(output_dir)
    elapsed = time.monotonic() - start
    print(
        f"[SPRAG] built {len(manifest['routes'])} route(s), {len(manifest.get('mounts', []))} mount(s)"
        f" with {len(manifest['errors'])} error(s) into {output_dir}"
        f" ({elapsed:.2f}s)"
    )
    return manifest


def _watch_loop(app, output_dir, project_root, interval, stop_event):
    import traceback

    last_mtimes = _collect_mtimes(project_root)
    build_id = 0
    while not stop_event.is_set():
        time.sleep(interval)
        current_mtimes = _collect_mtimes(project_root)
        changed = _diff_mtimes(last_mtimes, current_mtimes)
        if not changed:
            continue
        last_mtimes = current_mtimes
        for path in changed:
            print(f"[SPRAG] changed: {path}")
        try:
            app.invalidate_pages()
            _build_once(app, output_dir)
            build_id += 1
            _emit_dev_rebuild_event(
                ok=True,
                build_id=build_id,
                changed=changed,
            )
        except Exception as exc:  # pragma: no cover - dev loop resilience
            print(f"[SPRAG] rebuild failed: {exc.__class__.__name__}: {exc}")
            _emit_dev_rebuild_event(
                ok=False,
                build_id=build_id,
                changed=changed,
                error=f"{exc.__class__.__name__}: {exc}",
            )
            traceback.print_exc()


def _collect_mtimes(project_root):
    mtimes = {}
    for path in project_root.rglob("*.py"):
        if ".sprag" in path.parts or "__pycache__" in path.parts:
            continue
        try:
            mtimes[str(path.relative_to(project_root))] = path.stat().st_mtime
        except OSError:
            continue
    return mtimes


def _diff_mtimes(old, new):
    changed = []
    for path, mtime in new.items():
        if path not in old or old[path] != mtime:
            changed.append(path)
    for path in old:
        if path not in new:
            changed.append(f"{path} (deleted)")
    return sorted(changed)


def _emit_dev_rebuild_event(*, ok, build_id, changed, error=None):
    payload = {
        "event": "sprag:dev.rebuild",
        "payload": {
            "ok": ok,
            "build_id": build_id,
            "changed": list(changed or []),
            "store_fingerprint": store_fingerprint(),
        },
    }
    if error:
        payload["payload"]["error"] = error
    bus.emit("sprag:broadcast", payload)

if __name__ == "__main__":
    main()
