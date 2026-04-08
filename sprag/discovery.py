"""Route discovery helpers."""

from __future__ import annotations

import importlib
import pkgutil

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

