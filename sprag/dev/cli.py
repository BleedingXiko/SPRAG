"""SPRAG CLI."""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
import tempfile
import threading
import time
import traceback
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import __version__
from ..runtime.http import SERVER_MODES, resolve_server_mode, serve_sprag_app
from ..runtime.loader import load_app
from ..runtime.routing import is_dynamic_path
from .package import build_dist_bundle, build_static_site
from .scaffold import (
    DEFAULT_ROUTE_MODE,
    ROUTE_MODES,
    available_templates,
    scaffold_content,
    scaffold_project,
    scaffold_mount,
    scaffold_route,
)
from ..runtime.server import bus
from ..runtime.stores import store_fingerprint


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.func(args)


_SUBCOMMAND_HELP = {
    "build": "Build the app into a deployable artifact (pass 'static' for SSG-only output)",
    "pack": "Optimize a built dist for production deployment",
    "routes": "List all discovered routes with actions and schemas",
    "dev": "Start the dev server with file watching",
    "doctor": "Run structural diagnostics against the current SPRAG app",
}


def _build_parser():
    parser = argparse.ArgumentParser(prog="sprag", description="SPRAG framework CLI")
    parser.add_argument("--version", action="version", version=_version_string())
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, help_text in _SUBCOMMAND_HELP.items():
        sub = subparsers.add_parser(name, help=help_text)
        if name == "pack":
            sub.add_argument("--dist", default="dist", help="Path to the dist directory to optimize")
            sub.add_argument("--zip", action="store_true", help="Create a ZIP archive of the packed dist")
            sub.add_argument("--dry-run", action="store_true", help="Preview without writing changes")
            sub.add_argument("--verbose", action="store_true", help="Detailed logging")
            sub.add_argument("--skip-images", action="store_true", help="Skip image optimization")
            sub.add_argument("--skip-minify", action="store_true", help="Skip CSS/JS minification")
            sub.add_argument("--skip-bytecode", action="store_true", help="Skip bytecode compilation")
            sub.add_argument("--skip-gzip", action="store_true", help="Skip pre-gzip compression")
            sub.add_argument("--skip-fingerprint", action="store_true", help="Skip content-hash fingerprinting")
            sub.add_argument("--no-webp", action="store_true", help="Skip WebP variant generation")
            sub.add_argument("--no-srcset", action="store_true", help="Skip responsive image variants")
            sub.add_argument("--image-quality", type=int, default=80, help="Image compression quality (1-100)")
            sub.add_argument("--image-max-width", type=int, default=1920, help="Max image width in pixels")
            sub.set_defaults(func=cmd_pack)
            continue
        sub.add_argument("--app", dest="app_target", default=None)
        sub.add_argument("--project-root", default=os.getcwd())
        sub.add_argument("--output", default="dist" if name == "build" else ".sprag")
        if name == "build":
            sub.add_argument(
                "mode",
                nargs="?",
                choices=["static"],
                default=None,
                help="'static' emits a pure SSG site (HTML/JS/CSS + public/) with no server code",
            )
        if name == "dev":
            sub.add_argument(
                "mode",
                nargs="?",
                choices=["static"],
                default=None,
                help="'static' builds and serves a pure static site preview",
            )
            sub.add_argument("--port", type=int, default=8000)
            sub.add_argument("--host", default="127.0.0.1")
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
        if name == "doctor":
            sub.add_argument(
                "--verbose",
                action="store_true",
                help="Print traceback details for failing checks.",
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
            "Use 'route <name>', 'mount <name>', or 'content <name>'. "
            "Back-compat: a bare name is treated as 'route <name>'."
        ),
    )
    add_parser.add_argument(
        "name",
        nargs="?",
        help="Route, mount, or content name, e.g. 'dashboard' or 'admin/guides'",
    )
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

    inspect_parser = subparsers.add_parser("inspect", help="Inspect the built output for a route or mount")
    inspect_parser.add_argument("target", help="Concrete route or mount path, for example '/counter'")
    inspect_parser.add_argument("--app", dest="app_target", default=None)
    inspect_parser.add_argument("--project-root", default=os.getcwd())
    inspect_parser.add_argument("--output", default=".sprag")
    inspect_parser.add_argument(
        "--open-files",
        action="store_true",
        help="Print generated file paths without dumping compiled source.",
    )
    inspect_parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild the preview output before inspecting it.",
    )
    inspect_parser.set_defaults(func=cmd_inspect)

    return parser


def _local_npm_bin(binary: str) -> str | None:
    """Return path to binary in ./node_modules/.bin/, or None."""
    local = Path(os.getcwd()) / "node_modules" / ".bin" / binary
    return str(local) if local.exists() else None


def _prompt_install_npm_tool(binary: str, npm_package: str, description: str) -> None:
    """Prompt the user to install a missing npm tool if running interactively."""
    import shutil
    import subprocess

    if _local_npm_bin(binary) or shutil.which(binary):
        return
    if not sys.stdin.isatty():
        return
    try:
        answer = input(
            f"\n  {binary} not found — install it for {description}? [y/N] "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if answer != "y":
        return
    print(f"  Running: npm install --save-dev {npm_package}")
    result = subprocess.run(["npm", "install", "--save-dev", npm_package], check=False)
    if result.returncode != 0:
        print(f"  [!] npm install failed — continuing without {binary}")
    else:
        print(f"  [+] {binary} installed")


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff"}


def _prompt_install_pillow(dist_dir: Path) -> None:
    """Prompt the user to install Pillow in the active venv if images are present."""
    import subprocess

    try:
        from PIL import Image  # noqa: F401
        return  # already available
    except ImportError:
        pass

    # Static dist has assets at dist/ directly; full dist has them at dist/public/.
    is_static = not (dist_dir / "server.py").exists()
    assets_dir = dist_dir if is_static else dist_dir / "public"
    if not assets_dir.exists():
        return
    has_images = any(
        p.suffix.lower() in IMAGE_SUFFIXES
        for p in assets_dir.rglob("*")
        if p.is_file()
    )
    if not has_images:
        return

    if not sys.stdin.isatty():
        return

    pip_cmd = f"{sys.executable} -m pip install Pillow"
    try:
        answer = input(
            f"\n  Pillow not installed — required for image optimization.\n"
            f"  Install it now? ({pip_cmd}) [y/N] "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if answer != "y":
        return
    print(f"  Running: {pip_cmd}")
    result = subprocess.run([sys.executable, "-m", "pip", "install", "Pillow"], check=False)
    if result.returncode != 0:
        print("  [!] pip install failed — continuing without image optimization")
    else:
        print("  [+] Pillow installed")


def cmd_pack(args):
    from .pack import SpragPack

    _prompt_install_npm_tool("cleancss", "clean-css-cli", "better CSS minification")
    _prompt_install_npm_tool("terser", "terser", "better JS minification")
    if not args.skip_images:
        _prompt_install_pillow(Path(args.dist).resolve())

    dist_dir = Path(args.dist).resolve()
    packer = SpragPack(
        dist_dir,
        zip_output=args.zip,
        dry_run=args.dry_run,
        verbose=args.verbose,
        skip_images=args.skip_images,
        skip_minify=args.skip_minify,
        skip_bytecode=args.skip_bytecode,
        skip_gzip=args.skip_gzip,
        skip_fingerprint=args.skip_fingerprint,
        generate_webp=not args.no_webp,
        generate_srcset=not args.no_srcset,
        image_quality=args.image_quality,
        image_max_width=args.image_max_width,
    )
    packer.execute()


def cmd_routes(args):
    from ..runtime.server import Controller as SpragController
    from ..runtime.browser import Screen as SpragScreen

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
            defer_tag = " [defer]" if meta.get("defer") else ""
            if schema is not None:
                fields_desc = ", ".join(
                    f"{fname}: {f.type.__name__}{' [required]' if f.required else ''}"
                    for fname, f in schema._fields.items()
                ) if hasattr(schema, "_fields") else ""
                print(f"  @{action_name}({fields_desc}){defer_tag}")
            else:
                print(f"  @{action_name}(){defer_tag}")

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
                defer_tag = " [defer]" if meta.get("defer") else ""
                if schema is not None:
                    fields_desc = ", ".join(
                        f"{fname}: {f.type.__name__}{' [required]' if f.required else ''}"
                        for fname, f in schema._fields.items()
                    ) if hasattr(schema, "_fields") else ""
                    print(f"  @{action_name}({fields_desc}){defer_tag}")
                else:
                    print(f"  @{action_name}(){defer_tag}")

    if warnings:
        print()
        print("[SPRAG] warnings:")
        for w in warnings:
            print(w)


def _print_payload_warnings(payload_warnings):
    if not payload_warnings:
        return
    print()
    for w in payload_warnings:
        print(
            f"[SPRAG] payload warning: {w['path']} — load() returned {w['size_kb']} KB "
            f"(threshold: 50 KB). See /docs/guides/payload-design"
        )


def cmd_build(args):
    app_target, app = _load_cli_app(args)
    project_root = Path(args.project_root).resolve()
    output_dir = _resolve_cli_path(args.output, project_root)

    if getattr(args, "mode", None) == "static":
        result = build_static_site(
            app_target,
            app,
            output_dir=output_dir,
            project_root=project_root,
        )
        print(f"[SPRAG] app: {app_target}")
        print(f"[SPRAG] static site → {result['dist_dir']}")
        if result["errors"]:
            print(json.dumps({"errors": result["errors"]}, indent=2))
        _print_payload_warnings(result.get("payload_warnings", []))
    else:
        dist = build_dist_bundle(
            app_target,
            app,
            output_dir=output_dir,
            project_root=project_root,
        )
        print(f"[SPRAG] app: {app_target}")
        print(json.dumps(dist, indent=2, sort_keys=True))
        _print_payload_warnings(dist.get("payload_warnings", []))


def cmd_dev(args):
    _configure_runtime_logging()
    app_target, app = _load_cli_app(args)
    project_root = Path(args.project_root).resolve()
    output_dir = _resolve_cli_path(args.output, project_root)

    if getattr(args, "mode", None) == "static":
        _cmd_dev_static(args, app_target, app, project_root, output_dir)
        return

    setattr(app, "_sprag_dev_reload", True)
    _build_once(app, output_dir)
    resolved_server_mode = resolve_server_mode(app, args.server_mode)
    base_url = f"http://{args.host}:{args.port}"

    stop_event = threading.Event()
    watcher = threading.Thread(
        target=_watch_loop,
        args=(app, output_dir, project_root, args.interval, stop_event),
        daemon=True,
    )
    watcher.start()

    pages = app.pages()
    mounts = app.mounts()
    banner = [
        f"[SPRAG] app: {app_target}",
        f"[SPRAG] dev server running at {base_url}/",
        f"[SPRAG] server mode: {resolved_server_mode}",
        "",
        "  Routes:",
    ]
    if pages:
        path_width = max(len(pg.path) for _m, pg in pages)
        mode_width = max(len(pg.mode) for _m, pg in pages)
        for _module_name, pg in pages:
            banner.append(_dev_surface_banner_line(base_url, pg.path, label=pg.mode.ljust(mode_width), width=path_width))
    else:
        banner.append("    (none)")
    banner.append("")
    banner.append("  Mounts:")
    if mounts:
        path_width = max(len(mt.path) for _m, mt in mounts)
        for _module_name, mt in mounts:
            banner.append(_dev_surface_banner_line(base_url, mt.path, label="mount", width=path_width))
    else:
        banner.append("    (none)")
    banner.append("")
    banner.append("[SPRAG] pages render dynamically on each request")

    try:
        serve_sprag_app(
            app,
            output_dir,
            host=args.host,
            port=args.port,
            banner=banner,
            server_mode=args.server_mode,
        )
    except KeyboardInterrupt:
        print("\n[SPRAG] stopping dev server")
    finally:
        stop_event.set()


def _cmd_dev_static(args, app_target, app, project_root: Path, output_dir: Path):
    if args.server_mode is not None:
        raise SystemExit("[SPRAG] dev static serves files only; --server-mode is not supported.")

    _build_static_once(app_target, app, output_dir, project_root)
    base_url = f"http://{args.host}:{args.port}"

    stop_event = threading.Event()
    watcher = threading.Thread(
        target=_watch_loop,
        args=(app, output_dir, project_root, args.interval, stop_event),
        kwargs={
            "rebuild": lambda current_app: _build_static_once(
                app_target,
                current_app,
                output_dir,
                project_root,
            ),
            "emit_events": False,
        },
        daemon=True,
    )
    watcher.start()

    banner = [
        f"[SPRAG] app: {app_target}",
        f"[SPRAG] static dev server running at {base_url}/",
        f"[SPRAG] serving static files from {output_dir}",
        "[SPRAG] static mode serves no SPRAG server endpoints; refresh the browser after rebuilds",
    ]
    try:
        _serve_static_dir(output_dir, host=args.host, port=args.port, banner=banner)
    except KeyboardInterrupt:
        print("\n[SPRAG] stopping static dev server")
    finally:
        stop_event.set()


def _configure_runtime_logging():
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("sprag.runtime").setLevel(logging.INFO)


def _dev_surface_banner_line(base_url: str, path: str, *, label: str, width: int) -> str:
    if is_dynamic_path(path):
        return f"    {path.ljust(width)}  [{label}]  -> pattern"
    return f"    {path.ljust(width)}  [{label}]  -> {_join_base_url(base_url, path)}"


def _join_base_url(base_url: str, path: str) -> str:
    normalized_path = "/" if path in {"", "/"} else "/" + str(path).strip("/")
    return f"{base_url}{normalized_path}"


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

    if kind == "content":
        if args.mode is not None:
            raise SystemExit("[SPRAG] --mode is only valid for 'sprag add route', not 'sprag add content'.")
        created = scaffold_content(project_root, name)
        print(f"[SPRAG] created content collection '{normalized}' at app/routes/{normalized}/ and app/content/{normalized}/")
        for path in created:
            print(f"  {path.relative_to(project_root)}")
        print()
        print("Edit your content:")
        print(f"  app/content/{normalized}/getting-started.md          # starter Markdown page")
        print(f"  app/routes/{normalized}/server.py                   # collection index data")
        print(f"  app/routes/{normalized}/[...segments]/server.py     # article route data")
        print(f"  app/content_support.py                              # shared markdown helpers")
        print()
        print(f"Then run: sprag dev and open {('/' + normalized).replace('//', '/')}")
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


def cmd_doctor(args):
    from ..runtime.mount import Mount
    from ..runtime.page import Page
    from ..runtime.server import Controller as SpragController
    from ..runtime.browser import Component as SpragComponent
    from ..runtime.browser import Module as SpragModule
    from ..runtime.browser import Screen as SpragScreen

    checks = []
    project_root = Path(args.project_root).resolve()
    app_target = None
    app = None
    pages = []
    mounts = []
    surfaces_loaded = False

    _append_check(checks, "project shape", *_doctor_project_shape(project_root))

    try:
        app_target, app = _load_cli_app(args)
    except Exception as exc:
        _append_check(
            checks,
            "app discovery",
            False,
            f"{exc.__class__.__name__}: {exc}",
            details=_exception_details(exc, args.verbose),
        )
    else:
        _append_check(checks, "app discovery", True, f"loaded {app_target}")

    if app is None:
        _append_check(checks, "surface importability", False, "skipped because app discovery failed")
        _append_check(checks, "subclass sanity", False, "skipped because app discovery failed")
        _append_check(checks, "buildability", False, "skipped because app discovery failed")
        _append_check(checks, "transport deps", False, "skipped because app discovery failed")
    else:
        try:
            pages = app.pages()
            mounts = app.mounts()
        except Exception as exc:
            _append_check(
                checks,
                "surface importability",
                False,
                f"{exc.__class__.__name__}: {exc}",
                details=_exception_details(exc, args.verbose),
            )
        else:
            _append_check(
                checks,
                "surface importability",
                True,
                f"{len(pages)} route(s), {len(mounts)} mount(s)",
            )
            surfaces_loaded = True

        if not surfaces_loaded:
            _append_check(checks, "subclass sanity", False, "skipped because surface importability failed")
        elif pages or mounts:
            issues = []
            for module_name, page in pages:
                if not isinstance(page, Page):
                    issues.append(f"{module_name}: exported value is not a Page")
                    continue
                if not (isinstance(page.controller, type) and issubclass(page.controller, SpragController)):
                    issues.append(
                        f"{module_name}: controller {_display_name(page.controller)} is not a sprag.Controller subclass"
                    )
                if not (isinstance(page.screen, type) and issubclass(page.screen, SpragScreen)):
                    issues.append(
                        f"{module_name}: screen {_display_name(page.screen)} is not a sprag.Screen subclass"
                    )
                controller_route = getattr(page.controller, "route", None)
                if controller_route is not None and controller_route != page.path:
                    issues.append(
                        f"{module_name}: controller.route={controller_route!r} does not match page.path={page.path!r}"
                    )
            for module_name, mount in mounts:
                if not isinstance(mount, Mount):
                    issues.append(f"{module_name}: exported value is not a Mount")
                    continue
                if not (isinstance(mount.component, type) and issubclass(mount.component, SpragComponent)):
                    issues.append(
                        f"{module_name}: component {_display_name(mount.component)} is not a sprag.Component subclass"
                    )
                if mount.module is not None and not (
                    isinstance(mount.module, type) and issubclass(mount.module, SpragModule)
                ):
                    issues.append(
                        f"{module_name}: module {_display_name(mount.module)} is not a sprag.Module subclass"
                    )
                if mount.boot is not None and not (
                    isinstance(mount.boot, type) and issubclass(mount.boot, SpragController)
                ):
                    issues.append(
                        f"{module_name}: boot {_display_name(mount.boot)} is not a sprag.Controller subclass"
                    )

            if issues:
                _append_check(
                    checks,
                    "subclass sanity",
                    False,
                    f"{len(issues)} issue(s)",
                    details=issues,
                )
            else:
                _append_check(checks, "subclass sanity", True, "all routes and mounts look sane")
        else:
            _append_check(checks, "subclass sanity", True, "no routes or mounts discovered")

        try:
            with tempfile.TemporaryDirectory(prefix="sprag-doctor-") as tmp:
                manifest = app.build(Path(tmp) / "preview")
        except Exception as exc:
            _append_check(
                checks,
                "buildability",
                False,
                f"{exc.__class__.__name__}: {exc}",
                details=_exception_details(exc, args.verbose),
            )
        else:
            build_errors = manifest.get("errors", [])
            if build_errors:
                _append_check(
                    checks,
                    "buildability",
                    False,
                    f"{len(build_errors)} build error(s)",
                    details=[_format_build_error(error) for error in build_errors],
                )
            else:
                _append_check(
                    checks,
                    "buildability",
                    True,
                    f"preview build succeeded with {len(manifest.get('routes', []))} route(s) and {len(manifest.get('mounts', []))} mount(s)",
                )

            payload_warnings = manifest.get("payload_warnings", [])
            if payload_warnings:
                _append_check(
                    checks,
                    "payload sizes",
                    False,
                    f"{len(payload_warnings)} route(s) with oversized load() payload (>50 KB)",
                    details=[
                        f"{w['path']} — {w['size_kb']} KB (see /docs/guides/payload-design)"
                        for w in payload_warnings
                    ],
                )
            else:
                _append_check(checks, "payload sizes", True, "all load() payloads are under 50 KB")

        try:
            resolved_server_mode = resolve_server_mode(app)
            if resolved_server_mode == "websocket":
                from geventwebsocket.handler import WebSocketHandler  # noqa: F401

                detail = "server mode websocket; gevent-websocket available"
            else:
                detail = f"server mode {resolved_server_mode}; no extra transport package required"
            _append_check(checks, "transport deps", True, detail)
        except Exception as exc:
            _append_check(
                checks,
                "transport deps",
                False,
                f"{exc.__class__.__name__}: {exc}",
                details=_exception_details(exc, args.verbose),
            )

    failed = [check for check in checks if not check["ok"]]
    print(f"[SPRAG] doctor for {project_root}")
    if app_target:
        print(f"[SPRAG] app: {app_target}")
    for check in checks:
        status = "ok" if check["ok"] else "fail"
        print(f"[{status}] {check['name']}: {check['message']}")
        for detail in check.get("details", []):
            print(f"  - {detail}")
    print()
    if failed:
        print(f"[SPRAG] doctor found {len(failed)} failing check(s).")
        raise SystemExit(1)
    print(f"[SPRAG] doctor healthy ({len(checks)}/{len(checks)} checks passed).")


def cmd_inspect(args):
    from ..runtime.routing import match_page_route, normalize_route_path

    app_target, app = _load_cli_app(args)
    output_dir = _resolve_cli_path(args.output, Path(args.project_root).resolve())
    manifest_path = output_dir / "manifest.json"
    built_fresh = False

    if args.rebuild or not manifest_path.exists():
        output_dir.mkdir(parents=True, exist_ok=True)
        app.build(output_dir)
        built_fresh = True

    if not manifest_path.exists():
        raise SystemExit(f"[SPRAG] inspect could not find {manifest_path}. Run with --rebuild.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    target_path = normalize_route_path(args.target)

    route = next((entry for entry in manifest.get("routes", []) if entry.get("path") == target_path), None)
    if route is not None:
        print(f"[SPRAG] inspect route {target_path}")
        print(f"[SPRAG] app: {app_target}")
        print(f"[SPRAG] build output: {output_dir.resolve()} ({'rebuilt' if built_fresh else 'existing manifest'})")
        _print_surface_metadata(
            kind="route",
            entry=route,
            output_dir=output_dir,
            open_files=args.open_files,
        )
        return

    mount = next((entry for entry in manifest.get("mounts", []) if entry.get("path") == target_path), None)
    if mount is not None:
        print(f"[SPRAG] inspect mount {target_path}")
        print(f"[SPRAG] app: {app_target}")
        print(f"[SPRAG] build output: {output_dir.resolve()} ({'rebuilt' if built_fresh else 'existing manifest'})")
        _print_surface_metadata(
            kind="mount",
            entry=mount,
            output_dir=output_dir,
            open_files=args.open_files,
        )
        return

    matched = match_page_route(app.pages(), target_path)
    if matched is not None:
        raise SystemExit(
            f"[SPRAG] {target_path!r} matches route pattern {matched.page.path!r}, "
            "but that concrete path is not present in the built manifest. "
            "For dynamic routes, make sure page(..., static_paths=...) includes this path, then rerun inspect with --rebuild."
        )

    raise SystemExit(f"[SPRAG] no route or mount found for {target_path!r}.")


def _parse_add_target(args):
    if args.kind_or_name in {"route", "mount", "content"}:
        if not args.name:
            raise SystemExit(f"[SPRAG] missing name for 'sprag add {args.kind_or_name}'.")
        return args.kind_or_name, args.name
    if args.name is not None:
        raise SystemExit(
            "[SPRAG] expected 'sprag add route <name>', 'sprag add mount <name>', "
            "or 'sprag add content <name>'. "
            "For backward compatibility, 'sprag add <name>' is also accepted."
        )
    return "route", args.kind_or_name


def _load_cli_app(args):
    project_root = os.path.abspath(args.project_root)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    app_target, app = load_app(args.app_target)
    return app_target, app


def _resolve_cli_path(path_value, project_root: Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return project_root / path


def _build_once(app, output_dir):
    start = time.monotonic()
    manifest = app.build(output_dir)
    elapsed = time.monotonic() - start
    print(
        f"[SPRAG] built {len(manifest['routes'])} route(s), {len(manifest.get('mounts', []))} mount(s)"
        f" with {len(manifest['errors'])} error(s) into {output_dir}"
        f" ({elapsed:.2f}s)"
    )
    _print_payload_warnings(manifest.get("payload_warnings", []))
    return manifest


def _build_static_once(app_target, app, output_dir, project_root):
    start = time.monotonic()
    result = build_static_site(
        app_target,
        app,
        output_dir=output_dir,
        project_root=project_root,
    )
    elapsed = time.monotonic() - start
    print(
        f"[SPRAG] built static site with {len(result['routes'])} route(s), "
        f"{len(result.get('mounts', []))} mount(s)"
        f" and {len(result['errors'])} error(s) into {output_dir}"
        f" ({elapsed:.2f}s)"
    )
    _print_payload_warnings(result.get("payload_warnings", []))
    return result


def _serve_static_dir(directory: Path, *, host: str, port: int, banner=None):
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer((host, port), handler)
    if banner:
        for line in banner:
            print(line)
    try:
        server.serve_forever()
    finally:
        server.server_close()


DEV_WATCH_SUFFIXES = {
    ".avif",
    ".css",
    ".gif",
    ".html",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".png",
    ".py",
    ".svg",
    ".toml",
    ".txt",
    ".webp",
}


def _watch_loop(app, output_dir, project_root, interval, stop_event, *, rebuild=None, emit_events=True):
    import traceback

    rebuild = rebuild or (lambda current_app: _build_once(current_app, output_dir))
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
            importlib.invalidate_caches()
            _purge_project_modules(project_root)
            app.invalidate_pages()
            rebuild(app)
            build_id += 1
            if emit_events:
                _emit_dev_rebuild_event(
                    ok=True,
                    build_id=build_id,
                    changed=changed,
                )
        except Exception as exc:  # pragma: no cover - dev loop resilience
            print(f"[SPRAG] rebuild failed: {exc.__class__.__name__}: {exc}")
            if emit_events:
                _emit_dev_rebuild_event(
                    ok=False,
                    build_id=build_id,
                    changed=changed,
                    error=f"{exc.__class__.__name__}: {exc}",
                )
            traceback.print_exc()


def _collect_mtimes(project_root):
    mtimes = {}
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in DEV_WATCH_SUFFIXES:
            continue
        if _is_ignored_dev_watch_path(path):
            continue
        try:
            mtimes[str(path.relative_to(project_root))] = path.stat().st_mtime
        except OSError:
            continue
    return mtimes


def _is_ignored_dev_watch_path(path: Path) -> bool:
    ignored_parts = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".sprag",
        "__pycache__",
        "node_modules",
        "dist",
    }
    return any(part in ignored_parts for part in path.parts)


def _purge_project_modules(project_root: Path) -> None:
    """Drop imported app modules so dev rebuilds compile the current files."""
    root = project_root.resolve()
    framework_root = Path(__file__).resolve().parents[1]
    for name, module in list(sys.modules.items()):
        module_file = getattr(module, "__file__", None)
        if not module_file:
            continue
        try:
            path = Path(module_file).resolve()
            if path.is_relative_to(framework_root):
                continue
            if path.is_relative_to(root):
                del sys.modules[name]
        except (OSError, ValueError):
            continue


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


def _doctor_project_shape(project_root: Path):
    missing = []
    required = [
        project_root / "app" / "__init__.py",
        project_root / "app" / "routes" / "__init__.py",
    ]
    for path in required:
        if not path.exists():
            missing.append(str(path.relative_to(project_root)))

    mounts_dir = project_root / "app" / "mounts"
    mounts_init = mounts_dir / "__init__.py"
    if mounts_dir.exists() and not mounts_init.exists():
        missing.append(str(mounts_init.relative_to(project_root)))

    if missing:
        return False, f"missing {len(missing)} required path(s)", missing
    detail = "found app package and routes package"
    if mounts_init.exists():
        detail += ", plus mounts package"
    return True, detail, []


def _append_check(checks, name, ok, message, details=None):
    checks.append(
        {
            "name": name,
            "ok": bool(ok),
            "message": message,
            "details": list(details or []),
        }
    )


def _exception_details(exc, verbose):
    if not verbose:
        return []
    return [line.rstrip("\n") for line in traceback.format_exception(exc)]


def _display_name(value):
    return getattr(value, "__name__", repr(value))


def _format_build_error(error):
    path = error.get("path") or "(unknown path)"
    stage = error.get("stage") or "build"
    message = error.get("error") or "unknown error"
    return f"{path} [{stage}] {message}"


def _print_surface_metadata(*, kind, entry, output_dir: Path, open_files: bool):
    if kind == "route":
        lines = [
            f"module: {entry.get('module')}",
            f"pattern: {entry.get('pattern')}",
            f"path: {entry.get('path')}",
            f"mode: {entry.get('mode')}",
            f"controller: {entry.get('controller')}",
            f"screen: {entry.get('screen')}",
            f"actions: {', '.join(entry.get('actions') or []) or '(none)'}",
            f"js modules: {', '.join(sorted((entry.get('modules') or {}).keys())) or '(none)'}",
            f"output: {entry.get('output')}",
        ]
        hydration = entry.get("hydration") or []
    else:
        lines = [
            f"source: {entry.get('source')}",
            f"path: {entry.get('path')}",
            f"name: {entry.get('name')}",
            f"component: {entry.get('component')}",
            f"module: {entry.get('module') or '(none)'}",
            f"boot: {entry.get('boot') or '(none)'}",
            f"actions: {', '.join(entry.get('actions') or []) or '(none)'}",
            f"js modules: {', '.join(sorted((entry.get('modules') or {}).keys())) or '(none)'}",
            f"output: {entry.get('output')}",
        ]
        hydration = [
            {
                "id": "app-root",
                "component": entry.get("component"),
                "module": entry.get("module"),
            }
        ]

    for line in lines:
        print(line)

    print()
    print("hydration:")
    if hydration:
        for item in hydration:
            print(
                f"  - id={item.get('id')} component={item.get('component') or '(none)'} module={item.get('module') or '(none)'}"
            )
    else:
        print("  - (none)")

    files = _surface_generated_files(kind=kind, entry=entry, output_dir=output_dir)
    print()
    print("generated files:")
    if files:
        for path in files:
            print(f"  - {path}")
    else:
        print("  - (none)")

    source_locations = _surface_source_locations(files)
    if source_locations:
        print()
        print("source locations:")
        for item in source_locations:
            print(
                f"  - {item['class']} ({item['kind']}): {item['source_file']}"
            )
            for method in item["methods"]:
                generated = method.get("generated_start_line")
                if generated is None:
                    print(f"    {method['name']} -> line {method['source_line']}")
                else:
                    print(
                        f"    {method['name']} -> source line {method['source_line']} "
                        f"(generated line {generated})"
                    )

    if open_files or not files:
        return

    for path in files:
        print()
        print(f"===== {path} =====")
        print(path.read_text(encoding='utf-8'))


def _surface_generated_files(*, kind, entry, output_dir: Path):
    files = []
    seen = set()

    def _push(path: Path):
        resolved = path.resolve()
        if resolved in seen or not path.exists():
            return
        seen.add(resolved)
        files.append(resolved)

    if kind == "route":
        for item in entry.get("hydration") or []:
            component_name = item.get("component")
            module_name = item.get("module")
            if component_name:
                _push(output_dir / "generated" / "components" / f"{component_name}.js")
                _push(output_dir / "generated" / "components" / f"{component_name}.js.map")
            if module_name:
                _push(output_dir / "generated" / "modules" / f"{module_name}.js")
                _push(output_dir / "generated" / "modules" / f"{module_name}.js.map")
    else:
        component_name = entry.get("component")
        module_name = entry.get("module")
        if component_name:
            _push(output_dir / "generated" / "components" / f"{component_name}.js")
            _push(output_dir / "generated" / "components" / f"{component_name}.js.map")
        if module_name:
            _push(output_dir / "generated" / "modules" / f"{module_name}.js")
            _push(output_dir / "generated" / "modules" / f"{module_name}.js.map")

    return files


def _surface_source_locations(files):
    items = []
    for path in files:
        if path.suffix != ".map":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        sprag = payload.get("x_sprag") or {}
        methods = sprag.get("methods") or []
        sources = payload.get("sources") or []
        if not sprag or not methods or not sources:
            continue
        items.append(
            {
                "class": sprag.get("class") or path.stem,
                "kind": sprag.get("kind") or "browser",
                "source_file": sources[0],
                "methods": methods,
            }
        )
    return items

def _version_string():
    parts = [f"sprag {__version__}"]
    try:
        import specter
        parts.append(f"specter {specter.__version__}")
    except Exception:
        parts.append("specter (not installed)")
    ragot_bundle = Path(__file__).resolve().parent.parent / "assets" / "ragot.esm.min.js"
    if ragot_bundle.exists():
        import re
        head = ragot_bundle.read_text(encoding="utf-8")[:500]
        match = re.search(r"@version\s+([\d.]+)", head)
        if match:
            parts.append(f"ragot {match.group(1)}")
        else:
            parts.append("ragot (bundled)")
    else:
        parts.append("ragot (bundle missing)")
    return " | ".join(parts)


if __name__ == "__main__":
    main()
