"""Browser-side class dependency discovery for generated JS.

SPRAG source is normal Python, so authors can compose browser code by
importing shared ``Component`` / ``Module`` classes from any app package.
The codegen still has to make those classes available in the flat generated
JS output. This module finds local names in a class's source file that resolve
to SPRAG browser classes, so the emit step can recursively compile them and
the individual compiled file can import them.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import sys
from pathlib import Path


def collect_browser_class_refs_for_class(cls) -> dict[str, type]:
    """Return ``{local_name: browser_class}`` visible in ``cls``'s source file."""
    from ..web import Component, Module

    try:
        source_file = inspect.getsourcefile(cls)
    except (TypeError, OSError):
        return {}
    if not source_file:
        return {}

    path = Path(source_file)
    if not path.exists():
        return {}

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return {}

    cls_module = sys.modules.get(cls.__module__)
    refs: dict[str, type] = {}

    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module_name = _resolve_relative_module(node, cls_module)
            if not module_name:
                continue
            module = sys.modules.get(module_name)
            if module is None:
                try:
                    module = importlib.import_module(module_name)
                except Exception:
                    continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                local_name = alias.asname or alias.name
                value = getattr(module, alias.name, None)
                if _is_browser_class(value, Component, Module):
                    refs[local_name] = value
            continue

        # Same-file helper classes are common once a route grows beyond a
        # single Module. Make ``class ChildModule(Module): ...`` usable from
        # ``ParentModule`` without requiring an artificial self-import.
        if isinstance(node, ast.ClassDef) and cls_module is not None:
            value = getattr(cls_module, node.name, None)
            if _is_browser_class(value, Component, Module):
                refs[node.name] = value

    return refs


def used_browser_class_refs(cls) -> dict[str, type]:
    """Return visible browser classes whose local names are used by ``cls``."""
    refs = {
        local_name: ref
        for local_name, ref in collect_browser_class_refs_for_class(cls).items()
        if ref is not cls
    }
    if not refs:
        return {}

    used_names = _used_names_in_class(cls)
    return {
        local_name: ref
        for local_name, ref in refs.items()
        if local_name in used_names
    }


def _used_names_in_class(cls) -> set[str]:
    try:
        source = inspect.getsource(cls)
    except (OSError, TypeError):
        return set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def _is_browser_class(value, component_base, module_base) -> bool:
    return (
        isinstance(value, type)
        and value not in {component_base, module_base}
        and (issubclass(value, component_base) or issubclass(value, module_base))
    )


def _resolve_relative_module(node: ast.ImportFrom, cls_module) -> str | None:
    """Resolve ``from .x import y`` against the class's package."""
    if node.level == 0:
        return node.module
    if cls_module is None:
        return None
    package = getattr(cls_module, "__package__", None) or ""
    if not package:
        return None
    parts = package.split(".")
    if node.level - 1 > 0:
        parts = parts[: -(node.level - 1)]
    base = ".".join(parts)
    if node.module:
        return f"{base}.{node.module}" if base else node.module
    return base or None
