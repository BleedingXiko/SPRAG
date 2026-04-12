"""Deployable dist bundle creation for SPRAG."""

from __future__ import annotations

import importlib
import json
import shutil
import textwrap
from pathlib import Path

from .. import __file__ as sprag_init_file
from .build import build_web_preview
from ..runtime.http import resolve_server_mode


def build_dist_bundle(app_target, app, *, output_dir: Path, project_root: Path | None = None) -> dict:
    """Build a runnable dist artifact with app code and vendored runtimes."""
    output_dir = output_dir.resolve()
    _replace_dir(output_dir)

    public_dir = output_dir / "public"
    did_boot = not getattr(app, "_booted", False)
    if did_boot:
        app.boot()
    try:
        manifest = build_web_preview(app.pages(), public_dir, app=app, mounts=app.mounts())
    finally:
        if did_boot:
            app.shutdown()
    serializable_routes = _serializable_routes(manifest["routes"])
    serializable_mounts = _serializable_mounts(manifest.get("mounts", []))

    app_package = _app_package_name(app)
    app_project_root = Path(project_root).resolve() if project_root else _project_root_for_package(app_package)
    package_names = [app_package, "sprag"]
    for package_name in package_names:
        target_dir = output_dir / package_name
        if package_name == "sprag":
            _replace_sprag_runtime_dir(target_dir)
        else:
            source_dir = _package_dir(package_name)
            _replace_dir(target_dir, source_dir=source_dir)

    _write_text(output_dir / "server.py", _dist_server_source(app_target))
    server_mode = resolve_server_mode(app)
    dist_requirements = _dist_requirements(app_project_root, server_mode=server_mode)
    _write_text(output_dir / "requirements.txt", dist_requirements)
    _write_text(output_dir / "README.md", _dist_readme(app_target))
    _write_text(
        output_dir / "build.json",
        json.dumps(
            {
                "app_target": app_target,
                "app_package": app_package,
                "app_project_root": ".",
                "public_dir": "public",
                "server_entry": "server.py",
                "server_mode": server_mode,
                "routes": serializable_routes,
                "mounts": serializable_mounts,
                "errors": manifest["errors"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )

    return {
        "dist_dir": str(output_dir),
        "public_dir": str(public_dir),
        "server": str(output_dir / "server.py"),
        "routes": serializable_routes,
        "mounts": serializable_mounts,
        "errors": manifest["errors"],
        "packages": package_names,
    }


def _app_package_name(app):
    routes = getattr(app, "routes", None)
    if not routes or "." not in routes:
        raise RuntimeError(
            "SPRAG dist build could not infer the app package. "
            "Expected app.routes to look like 'app.routes'."
        )
    return routes.split(".", 1)[0]


def _package_dir(package_name):
    module = importlib.import_module(package_name)
    return Path(module.__file__).resolve().parent


def _project_root_for_package(package_name):
    return _package_dir(package_name).resolve().parent


def _replace_dir(target_dir: Path, *, source_dir: Path | None = None):
    if target_dir.exists():
        shutil.rmtree(target_dir)
    if source_dir is None:
        target_dir.mkdir(parents=True, exist_ok=True)
        return
    shutil.copytree(
        source_dir,
        target_dir,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )


def _replace_sprag_runtime_dir(target_dir: Path):
    sprag_root = Path(sprag_init_file).resolve().parent
    _replace_dir(target_dir)
    shutil.copy2(sprag_root / "__init__.py", target_dir / "__init__.py")
    shutil.copytree(
        sprag_root / "runtime",
        target_dir / "runtime",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )


def _write_text(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _serializable_routes(routes):
    serializable = []
    for route in routes:
        next_route = dict(route)
        next_route["hydration"] = []
        for entry in route.get("hydration", []):
            next_route["hydration"].append(
                {
                    "id": entry["id"],
                    "component": entry["component"],
                    "module": entry["module"],
                    "props": entry["props"],
                    "state": entry["state"],
                    "module_state": entry["module_state"],
                }
            )
        serializable.append(next_route)
    return serializable


def _serializable_mounts(mounts):
    return [
        {
            key: value
            for key, value in mount.items()
            if key not in {"root_component_class", "root_module_class"}
        }
        for mount in mounts
    ]


def _dist_server_source(app_target):
    return textwrap.dedent(
        f"""\
        from __future__ import annotations

        import argparse
        import sys
        from pathlib import Path

        ROOT = Path(__file__).resolve().parent
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))

        from sprag.runtime.http import resolve_server_mode, serve_sprag_app
        from sprag.runtime.loader import load_app


        def main(argv=None):
            parser = argparse.ArgumentParser(prog="sprag-dist")
            parser.add_argument("--host", default="127.0.0.1")
            parser.add_argument("--port", type=int, default=8000)
            parser.add_argument("--server-mode", choices=["auto", "wsgi", "websocket"], default=None)
            args = parser.parse_args(argv)

            app_target, app = load_app({app_target!r})
            resolved_server_mode = resolve_server_mode(app, args.server_mode)
            banner = [
                f"[SPRAG dist] app: {{app_target}}",
                f"[SPRAG dist] serving public assets from {{ROOT / 'public'}}",
                f"[SPRAG dist] server mode: {{resolved_server_mode}}",
                f"[SPRAG dist] server running at http://{{args.host}}:{{args.port}}/",
            ]
            serve_sprag_app(
                app,
                ROOT / "public",
                host=args.host,
                port=args.port,
                banner=banner,
                server_mode=args.server_mode,
            )


        if __name__ == "__main__":
            main()
        """
    )


def _dist_requirements(project_root: Path, *, server_mode: str = "wsgi"):
    requirements = []
    seen = set()

    for requirement in _project_requirement_lines(project_root):
        normalized = requirement.strip()
        if not normalized or normalized.startswith("#"):
            continue
        lowered = normalized.lower()
        if lowered == "sprag" or lowered.startswith("sprag==") or lowered.startswith("sprag>="):
            continue
        if lowered == "specter-runtime":
            if lowered not in seen:
                requirements.append("specter-runtime")
                seen.add(lowered)
            continue
        if lowered.startswith("-e "):
            editable_target = normalized[3:].strip()
            if "sprag" in editable_target.lower():
                continue
        if lowered not in seen:
            requirements.append(normalized)
            seen.add(lowered)

    if "specter-runtime" not in seen:
        requirements.insert(0, "specter-runtime")
        seen.add("specter-runtime")

    if server_mode == "websocket" and "gevent-websocket" not in seen:
        requirements.append("gevent-websocket")
        seen.add("gevent-websocket")

    return "\n".join(requirements) + "\n"


def _project_requirement_lines(project_root: Path):
    requirements_path = project_root / "requirements.txt"
    if not requirements_path.exists():
        return []
    return requirements_path.read_text(encoding="utf-8").splitlines()


def _dist_readme(app_target):
    return textwrap.dedent(
        f"""\
        # SPRAG Dist

        This folder is the deployable build artifact for the SPRAG app target `{app_target}`.

        It contains:

        - your shipped app code
        - the SPRAG runtime
        - the compiled Ragot-powered browser assets
        - the Python runtime requirements, including `specter-runtime`

        ## Run

        ```bash
        python3 -m venv .venv
        . .venv/bin/activate
        pip install -r requirements.txt
        python3 server.py --port 8000
        ```

        Then open `http://127.0.0.1:8000/`.

        ## What Is In Here

        - `public/`: compiled frontend assets
        - `server.py`: runnable server entrypoint
        - `app/`: shipped application code
        - `sprag/`: shipped SPRAG runtime
        - `requirements.txt`: runtime Python dependencies

        This is the folder you copy to a host, VM, or container image for deployment.
        """
    )
