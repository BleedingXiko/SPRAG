"""Route and mount discovery helpers."""

from __future__ import annotations

import importlib
import importlib.util
import pkgutil

from .mount import Mount
from .page import Page


def discover_pages(routes_package: str) -> list[tuple[str, Page]]:
    pages = []
    package = importlib.import_module(routes_package)

    for module_info in pkgutil.walk_packages(package.__path__, prefix=f"{routes_package}."):
        if not module_info.name.endswith(".page"):
            continue
        module = importlib.import_module(module_info.name)
        for attr_name in dir(module):
            value = getattr(module, attr_name)
            if isinstance(value, Page):
                pages.append((module_info.name, value))

    seen_paths = {}
    for module_name, pg in pages:
        if pg.path in seen_paths:
            raise ValueError(
                f"Duplicate SPRAG route path {pg.path!r}: "
                f"defined in {seen_paths[pg.path]} and {module_name}"
            )
        seen_paths[pg.path] = module_name

    return sorted(pages, key=lambda item: item[1].path)


def discover_mounts(mounts_package: str) -> list[tuple[str, Mount]]:
    mounts = []
    if not _package_exists(mounts_package):
        return mounts

    package = importlib.import_module(mounts_package)

    for module_info in pkgutil.walk_packages(package.__path__, prefix=f"{mounts_package}."):
        if not module_info.name.endswith(".mount"):
            continue
        module = importlib.import_module(module_info.name)
        for attr_name in dir(module):
            value = getattr(module, attr_name)
            if isinstance(value, Mount):
                mounts.append((module_info.name, value))

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
