"""Route and mount discovery helpers."""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import re
import pkgutil
import sys
from pathlib import Path

from .mount import Mount
from .page import Page
from .routing import is_dynamic_path


def discover_pages(routes_package: str) -> list[tuple[str, Page]]:
    pages = []
    for module_name, module in _discover_surface_modules(routes_package, leaf_name="page"):
        for attr_name in dir(module):
            value = getattr(module, attr_name)
            if isinstance(value, Page):
                pages.append((module_name, value))

    seen_paths = {}
    for module_name, pg in pages:
        if pg.path in seen_paths:
            raise ValueError(
                f"Duplicate SPRAG route path {pg.path!r}: "
                f"defined in {seen_paths[pg.path]} and {module_name}"
            )
        seen_paths[pg.path] = module_name

    return sorted(pages, key=lambda item: (is_dynamic_path(item[1].path), item[1].path))


def discover_mounts(mounts_package: str) -> list[tuple[str, Mount]]:
    mounts = []
    if not _package_exists(mounts_package):
        return mounts

    for module_name, module in _discover_surface_modules(mounts_package, leaf_name="mount"):
        for attr_name in dir(module):
            value = getattr(module, attr_name)
            if isinstance(value, Mount):
                mounts.append((module_name, value))

    seen_paths = {}
    for module_name, mt in mounts:
        if mt.path in seen_paths:
            raise ValueError(
                f"Duplicate SPRAG mount path {mt.path!r}: "
                f"defined in {seen_paths[mt.path]} and {module_name}"
            )
        seen_paths[mt.path] = module_name

    return sorted(mounts, key=lambda item: item[1].path)


def discover_surfaces(
    routes_package: str,
    mounts_package: str,
) -> tuple[list[tuple[str, Page]], list[tuple[str, Mount]]]:
    pages = discover_pages(routes_package)
    mounts = discover_mounts(mounts_package)
    validate_surface_paths(pages, mounts)
    return pages, mounts


def validate_surface_paths(
    pages: list[tuple[str, Page]],
    mounts: list[tuple[str, Mount]],
) -> None:
    """Reject ambiguous route/mount ownership.

    Routes may live beside other routes in nested paths, but mounts claim
    their path subtree so a mount cannot overlap a route or another mount.
    """

    seen_routes = {}
    for module_name, pg in pages:
        if pg.path in seen_routes:
            raise ValueError(
                f"Duplicate SPRAG route path {pg.path!r}: "
                f"defined in {seen_routes[pg.path]} and {module_name}"
            )
        seen_routes[pg.path] = module_name

    for mount_module, mt in mounts:
        for route_module, pg in pages:
            if _mount_claims_route(mt.path, pg.path):
                raise ValueError(
                    f"SPRAG path conflict: mount {mt.path!r} in {mount_module} "
                    f"overlaps route {pg.path!r} in {route_module}."
                )

    for index, (left_module, left) in enumerate(mounts):
        for right_module, right in mounts[index + 1:]:
            if _paths_overlap(left.path, right.path):
                raise ValueError(
                    f"SPRAG path conflict: mount {left.path!r} in {left_module} "
                    f"overlaps mount {right.path!r} in {right_module}."
                )


def _paths_overlap(left: str, right: str) -> bool:
    left = left.rstrip("/") or "/"
    right = right.rstrip("/") or "/"
    if left == right:
        return True
    if left == "/":
        return True
    if right == "/":
        return True
    return right.startswith(left + "/") or left.startswith(right + "/")


def _mount_claims_route(mount_path: str, route_path: str) -> bool:
    mount_path = mount_path.rstrip("/") or "/"
    route_path = route_path.rstrip("/") or "/"
    if mount_path == "/":
        return True
    return route_path == mount_path or route_path.startswith(mount_path + "/")


def _package_exists(package_name: str) -> bool:
    try:
        return importlib.util.find_spec(package_name) is not None
    except ModuleNotFoundError:
        return False


def _discover_surface_modules(package_name: str, *, leaf_name: str):
    package = importlib.import_module(package_name)
    package_paths = list(getattr(package, "__path__", []))
    if not package_paths:
        return []

    discovered = []
    for root_path in package_paths:
        root = Path(root_path)
        prefix = package_name + "."
        for module_info in pkgutil.walk_packages([str(root)], prefix=prefix):
            if module_info.name.endswith(f".{leaf_name}"):
                discovered.append((module_info.name, importlib.import_module(module_info.name)))

        # pkgutil drops route packages whose directory names contain dots
        # (for example `[...segments]`). Walk the filesystem as a fallback
        # and load any missing surface modules through a safe synthetic name.
        for source_path in sorted(root.rglob(f"{leaf_name}.py")):
            relative = source_path.relative_to(root)
            if not any("." in part for part in relative.parts[:-1]):
                continue
            synthetic_name = _synthetic_module_name(package_name, relative)
            if source_path.name == "__init__.py":
                continue
            if source_path.parent.name == "__pycache__":
                continue
            discovered.append((synthetic_name, _load_surface_module(package_name, root, relative)))

    seen = {}
    unique = []
    for module_name, module in discovered:
        if module_name in seen:
            continue
        seen[module_name] = True
        unique.append((module_name, module))
    return unique


def _load_surface_module(package_name: str, root: Path, relative: Path):
    raw_parts = relative.parts
    package_parts = raw_parts[:-1]
    parent_name = package_name
    current_dir = root

    for part in package_parts:
        current_dir = current_dir / part
        safe_part = _safe_module_segment(part)
        parent_name = parent_name + "." + safe_part
        if parent_name in sys.modules:
            continue
        init_path = current_dir / "__init__.py"
        if init_path.exists():
            spec = importlib.util.spec_from_file_location(
                parent_name,
                init_path,
                submodule_search_locations=[str(current_dir)],
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[parent_name] = module
            spec.loader.exec_module(module)
        else:
            spec = importlib.machinery.ModuleSpec(parent_name, loader=None, is_package=True)
            module = importlib.util.module_from_spec(spec)
            module.__path__ = [str(current_dir)]
            sys.modules[parent_name] = module

    module_name = parent_name + "." + _safe_module_segment(relative.stem)
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, root / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _synthetic_module_name(package_name: str, relative: Path) -> str:
    parts = [_safe_module_segment(part) for part in relative.with_suffix("").parts]
    return package_name + "." + ".".join(parts)


def _safe_module_segment(segment: str) -> str:
    if segment.isidentifier():
        return segment
    if segment.startswith("[...") and segment.endswith("]"):
        name = segment[4:-1]
        return f"__sprag_catchall_{_sanitize_segment(name)}__"
    if segment.startswith("[") and segment.endswith("]"):
        name = segment[1:-1]
        return f"__sprag_param_{_sanitize_segment(name)}__"
    return f"__sprag_{_sanitize_segment(segment)}__"


def _sanitize_segment(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_")
    return cleaned or "segment"
