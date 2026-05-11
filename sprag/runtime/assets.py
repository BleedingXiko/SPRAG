"""Shared asset resolution for SPRAG surfaces and builds."""

from __future__ import annotations

import hashlib
import html
import importlib
import posixpath
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from .urls import relative_url


@dataclass(frozen=True)
class Script:
    """Author-facing script asset declaration."""

    src: str
    module: bool = False


def script(src, *, module: bool = False) -> Script:
    """Declare a script asset for ``page(js=[...])`` / ``mount(js=[...])``."""
    return Script(src=str(src), module=bool(module))


@dataclass(frozen=True)
class ModuleImport:
    """Author-facing ESM import declaration."""

    src: str
    export: str = "default"


def module(src, export: str = "default") -> ModuleImport:
    """Declare an ESM import for ``modules={...}`` surfaces."""
    export_name = str(export or "default")
    if not export_name:
        raise ValueError("SPRAG module(..., export=...) requires a non-empty export name.")
    return ModuleImport(src=str(src), export=export_name)


@dataclass(frozen=True)
class Asset:
    """Resolved static or surface asset."""

    kind: str
    web_path: str
    source_path: Path | None = None
    module: bool = False
    external: bool = False


@dataclass(frozen=True)
class ResolvedModuleImport:
    """Resolved browser ESM import for a specific surface alias."""

    alias: str
    src: str
    export: str = "default"
    source_path: Path | None = None
    external: bool = False


@dataclass(frozen=True)
class SurfaceAssets:
    """Resolved CSS/JS assets for a single page or mount surface."""

    css: tuple[Asset, ...] = field(default_factory=tuple)
    js: tuple[Asset, ...] = field(default_factory=tuple)
    modules: tuple[ResolvedModuleImport, ...] = field(default_factory=tuple)
    static: tuple[Asset, ...] = field(default_factory=tuple)

    def all(self) -> tuple[Asset, ...]:
        module_assets = tuple(
            Asset(
                kind="module",
                web_path=item.src,
                source_path=item.source_path,
                external=item.external,
            )
            for item in self.modules
        )
        return self.css + self.js + module_assets + self.static


class AssetRegistry:
    """Build-time registry for emitted surface and static assets."""

    def __init__(self, *, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self._assets: dict[tuple[str, str], Asset] = {}

    def include(self, assets: SurfaceAssets):
        for asset in assets.all():
            key = (asset.kind, asset.web_path)
            existing = self._assets.get(key)
            if existing is None:
                self._assets[key] = asset
                continue
            if existing != asset:
                raise ValueError(
                    f"Conflicting SPRAG asset registrations for {asset.web_path!r}: "
                    f"{existing!r} vs {asset!r}"
                )

    def include_static_tree(self):
        self.include(SurfaceAssets(static=discover_static_assets(self.project_root)))

    def assets(self) -> tuple[Asset, ...]:
        return tuple(self._assets.values())

    def emit(self, output_dir: str | Path):
        emit_assets(output_dir, self.assets())


def resolve_project_root(app=None, explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).resolve()
    if app is not None and getattr(app, "project_root", None):
        return Path(app.project_root).resolve()
    if app is not None and getattr(app, "routes", None):
        package_name = app.routes.split(".", 1)[0]
        try:
            package = importlib.import_module(package_name)
            package_file = getattr(package, "__file__", None)
            if package_file:
                return Path(package_file).resolve().parent.parent
        except Exception:
            pass
    return Path.cwd().resolve()


def resolve_surface_assets(
    *,
    project_root: str | Path,
    css: Iterable[str | Path] | None = None,
    js: Iterable[str | Path | Script] | None = None,
) -> SurfaceAssets:
    root = Path(project_root).resolve()
    return SurfaceAssets(
        css=tuple(_resolve_stylesheet(spec, root) for spec in (css or ())),
        js=tuple(_resolve_script(spec, root) for spec in (js or ())),
    )


def normalize_module_imports(
    modules: Mapping[str, str | Path | ModuleImport] | None,
) -> dict[str, ModuleImport]:
    if not modules:
        return {}
    normalized = {}
    for alias, value in modules.items():
        if not isinstance(alias, str) or not alias or not alias.isidentifier():
            raise ValueError(
                f"SPRAG modules aliases must be valid Python identifiers, got {alias!r}."
            )
        if isinstance(value, ModuleImport):
            normalized[alias] = value
        elif isinstance(value, (str, Path)):
            normalized[alias] = ModuleImport(src=str(value), export="default")
        else:
            raise TypeError(
                f"Unsupported SPRAG module import for alias {alias!r}: {value!r}"
            )
    return normalized


def resolve_module_imports(
    *,
    project_root: str | Path,
    modules: Mapping[str, str | Path | ModuleImport] | None = None,
) -> tuple[ResolvedModuleImport, ...]:
    root = Path(project_root).resolve()
    resolved = []
    for alias, spec in normalize_module_imports(modules).items():
        raw = spec.src
        if _is_external_path(raw):
            resolved.append(
                ResolvedModuleImport(
                    alias=alias,
                    src=raw,
                    export=spec.export,
                    external=True,
                )
            )
            continue
        source_path = _resolve_local_path(root, raw)
        resolved.append(
            ResolvedModuleImport(
                alias=alias,
                src=_asset_web_path(source_path, root),
                export=spec.export,
                source_path=source_path,
                external=False,
            )
        )
    return tuple(resolved)


def serialize_module_imports(
    imports: Iterable[ResolvedModuleImport],
) -> dict[str, dict[str, str]]:
    return {
        item.alias: {"src": item.src, "export": item.export}
        for item in imports
    }


def discover_static_assets(project_root: str | Path) -> tuple[Asset, ...]:
    root = Path(project_root).resolve()
    static_root = root / "app" / "static"
    if not static_root.exists():
        return ()

    assets = []
    for path in sorted(static_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(static_root)
        assets.append(
            Asset(
                kind="static",
                source_path=path.resolve(),
                web_path=f"/static/{relative.as_posix()}",
            )
        )
    return tuple(assets)


def emit_assets(output_dir: str | Path, assets: Iterable[Asset]):
    """Copy resolved local assets into the build output directory."""
    target_root = Path(output_dir)
    seen = set()
    for asset in assets:
        if asset.external or asset.source_path is None or asset.web_path in seen:
            continue
        seen.add(asset.web_path)
        target_path = target_root / asset.web_path.lstrip("/")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset.source_path, target_path)
        _rewrite_copied_js_ragot_imports(target_path, asset.web_path)


_JS_IMPORT_SPEC_RE = re.compile(
    r"""(?P<head>\bimport\s*(?:\(\s*)?(?:[^'";]*?\s+from\s*)?)(?P<quote>['"])(?P<spec>[^'"]+)(?P=quote)""",
    re.M,
)


def _rewrite_copied_js_ragot_imports(target_path: Path, web_path: str) -> None:
    if target_path.suffix.lower() not in {".js", ".mjs"}:
        return
    try:
        content = target_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return
    rewritten = _rewrite_ragot_import_specs(content, web_path)
    if rewritten != content:
        target_path.write_text(rewritten, encoding="utf-8")


def _rewrite_ragot_import_specs(content: str, web_path: str) -> str:
    current_dir = posixpath.dirname(web_path.lstrip("/"))
    if current_dir:
        runtime_spec = posixpath.relpath("vendor/ragot.esm.min.js", current_dir)
    else:
        runtime_spec = "vendor/ragot.esm.min.js"
    if not runtime_spec.startswith("."):
        runtime_spec = "./" + runtime_spec

    def replace(match: re.Match) -> str:
        spec = match.group("spec")
        if _is_external_path(spec):
            return match.group(0)
        spec_path = spec.split("#", 1)[0].split("?", 1)[0]
        if posixpath.basename(spec_path) != "ragot.esm.min.js":
            return match.group(0)
        return f"{match.group('head')}{match.group('quote')}{runtime_spec}{match.group('quote')}"

    return _JS_IMPORT_SPEC_RE.sub(replace, content)


def render_css_links(assets: Iterable[Asset], *, document_path: str | None = None) -> str:
    chunks = []
    for asset in assets:
        href = _relative_asset_href(document_path, asset.web_path)
        label = html.escape(asset.web_path, quote=True)
        escaped_href = html.escape(href, quote=True)
        chunks.append(
            f'<link rel="stylesheet" href="{escaped_href}" data-sprag-css="{label}">'
        )
    return "\n".join(chunks)


def render_preload_hints(
    css_assets: Iterable[Asset] = (),
    *,
    script_path: str | None = None,
    document_path: str | None = None,
) -> str:
    """Render ``<link rel="preload">`` / ``<link rel="modulepreload">`` hints."""
    chunks = []
    if script_path:
        escaped = html.escape(script_path, quote=True)
        chunks.append(f'<link rel="modulepreload" href="{escaped}">')
    for asset in css_assets:
        href = _relative_asset_href(document_path, asset.web_path)
        escaped = html.escape(href, quote=True)
        chunks.append(f'<link rel="preload" as="style" href="{escaped}">')
    return "\n  ".join(chunks)


def render_script_tags(assets: Iterable[Asset], *, document_path: str | None = None) -> str:
    chunks = []
    for asset in assets:
        src = _relative_asset_href(document_path, asset.web_path)
        label = html.escape(asset.web_path, quote=True)
        escaped_src = html.escape(src, quote=True)
        if asset.module:
            chunks.append(
                f'<script type="module" src="{escaped_src}" data-sprag-js="{label}"></script>'
            )
        else:
            chunks.append(
                f'<script defer src="{escaped_src}" data-sprag-js="{label}"></script>'
            )
    return "\n".join(chunks)


def _resolve_stylesheet(spec: str | Path, project_root: Path) -> Asset:
    raw = str(spec)
    if _is_external_path(raw):
        return Asset(kind="css", web_path=raw, external=True)
    resolved = _resolve_local_path(project_root, spec)
    return Asset(
        kind="css",
        source_path=resolved,
        web_path=_asset_web_path(resolved, project_root),
    )


def _resolve_script(spec: str | Path | Script, project_root: Path) -> Asset:
    if isinstance(spec, Script):
        raw = spec.src
        module = spec.module
    else:
        raw = str(spec)
        module = False

    if _is_external_path(raw):
        return Asset(kind="js", web_path=raw, module=module, external=True)

    resolved = _resolve_local_path(project_root, raw)
    return Asset(
        kind="js",
        source_path=resolved,
        web_path=_asset_web_path(resolved, project_root),
        module=module,
    )


def _resolve_local_path(project_root: Path, path: str | Path) -> Path:
    next_path = Path(path)
    if next_path.is_absolute():
        return next_path.resolve()
    return (project_root / next_path).resolve()


def _asset_web_path(source_path: Path, project_root: Path) -> str:
    root = project_root.resolve()
    static_root = root / "app" / "static"
    try:
        static_relative = source_path.relative_to(static_root)
        return f"/static/{static_relative.as_posix()}"
    except ValueError:
        pass

    try:
        relative = source_path.relative_to(root)
        return f"/assets/{relative.as_posix()}"
    except ValueError:
        digest = hashlib.sha1(str(source_path).encode("utf-8")).hexdigest()[:12]
        name = source_path.name or "asset"
        return f"/assets/_external/{digest}-{name}"


def _relative_asset_href(document_path: str | None, asset_path: str) -> str:
    return relative_url(document_path, asset_path)


def _is_external_path(value: str) -> bool:
    return value.startswith(("http://", "https://", "//"))
