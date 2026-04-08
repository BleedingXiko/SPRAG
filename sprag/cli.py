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
from .http_server import serve_sprag_app
from .loader import load_app
from .package import build_dist_bundle
from .scaffold import (
    DEFAULT_ROUTE_MODE,
    ROUTE_MODES,
    available_templates,
    scaffold_project,
    scaffold_route,
)


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

    add_parser = subparsers.add_parser("add", help="Add a new route to the current project")
    add_parser.add_argument("route_name", help="Route name, e.g. 'dashboard' or 'admin/users'")
    add_parser.add_argument("--project-root", default=os.getcwd())
    add_parser.add_argument(
        "--mode",
        choices=ROUTE_MODES,
        default=DEFAULT_ROUTE_MODE,
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
    output_dir = Path(args.output)
    _build_once(app, output_dir)

    stop_event = threading.Event()
    watcher = threading.Thread(
        target=_watch_loop,
        args=(app, output_dir, Path(args.project_root), args.interval, stop_event),
        daemon=True,
    )
    watcher.start()

    pages = app.pages()
    banner = [
        f"[SPRAG] app: {app_target}",
        f"[SPRAG] dev server running at http://127.0.0.1:{args.port}/",
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
    banner.append("[SPRAG] pages render dynamically on each request")

    try:
        serve_sprag_app(
            app,
            output_dir,
            host="127.0.0.1",
            port=args.port,
            banner=banner,
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

    created = scaffold_route(project_root, args.route_name, mode=args.mode)
    normalized = args.route_name.strip("/")
    print(f"[SPRAG] created {args.mode}-mode route '{normalized}' at app/routes/{normalized}/")
    for path in created:
        print(f"  {path.relative_to(project_root)}")
    print()
    print("Edit your route:")
    print(f"  app/routes/{normalized}/server.py      # controller logic")
    print(f"  app/routes/{normalized}/web.py         # screen layout")
    print(f"  app/routes/{normalized}/components.py  # UI components")
    if args.mode == "hybrid":
        print(f"  app/routes/{normalized}/modules.py     # browser-side Module")
    print()
    print("Then run: sprag dev")


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
        f"[SPRAG] built {len(manifest['routes'])} route(s)"
        f" with {len(manifest['errors'])} error(s) into {output_dir}"
        f" ({elapsed:.2f}s)"
    )
    return manifest


def _watch_loop(app, output_dir, project_root, interval, stop_event):
    import traceback

    last_mtimes = _collect_mtimes(project_root)
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
        except Exception as exc:  # pragma: no cover - dev loop resilience
            print(f"[SPRAG] rebuild failed: {exc.__class__.__name__}: {exc}")
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

if __name__ == "__main__":
    main()
