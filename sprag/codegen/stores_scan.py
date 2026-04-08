"""Detect SPRAG store references inside a Python source file.

When the codegen compiles a Module/Component class, it needs to know which
local names in the surrounding source file resolve to ``StoreBridge``
instances so that calls like ``counter.set({...})`` can be routed to the
right Ragot store method (and a JS import added at the top of the
generated file).

The scan is intentionally simple:

1. Read the file the class lives in.
2. Walk top-level ``ImportFrom`` statements.
3. For each imported name, try to resolve the actual Python value through
   the already-loaded module set (``sys.modules``). We do **not** import
   anything new here — at codegen time the user's source has already been
   imported by ``discover_pages``, so the modules referenced via
   ``from app.stores import counter`` are live.
4. Anything that resolves to a ``StoreBridge`` instance is recorded in the
   returned ``{python_local_name: store_name}`` map.

The map is then threaded through ``_compile_statements`` /
``_compile_expr`` so call sites can be translated.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import sys
from pathlib import Path

from ..stores import StoreBridge


def collect_store_refs_for_class(cls) -> dict[str, str]:
    """Return ``{local_name: store_name}`` for every store imported in ``cls``'s file.

    Returns an empty dict if the file can't be located, can't be parsed, or
    contains no store imports — the caller treats this as "no stores", and
    compilation proceeds normally.
    """
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

    refs: dict[str, str] = {}
    cls_module = sys.modules.get(cls.__module__)

    for node in tree.body:
        # ``from X import a, b as c``
        if isinstance(node, ast.ImportFrom):
            module_name = _resolve_relative_module(node, cls_module)
            if not module_name:
                continue
            module = sys.modules.get(module_name)
            if module is None:
                # Best-effort: try to import. If the user's project has the
                # module on its path it'll resolve; otherwise we silently
                # skip and the user gets an undefined-reference error in JS,
                # which is the right failure shape.
                try:
                    module = importlib.import_module(module_name)
                except Exception:
                    continue
            for alias in node.names:
                src_name = alias.name
                local_name = alias.asname or alias.name
                value = getattr(module, src_name, None)
                if isinstance(value, StoreBridge):
                    refs[local_name] = value.name
            continue
        # ``import X`` / ``import X as Y`` — also support ``module.counter`` access
        # at the call site by recording the module under its alias. We don't
        # currently rewrite ``module.counter.set(...)``; the convention is
        # ``from app.stores import counter`` so the local name is the bridge.
        # If a real-world need shows up, extend this branch.
    return refs


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
    # ``from . import x`` -> level 1 strips zero parts; ``from ..`` strips one.
    if node.level - 1 > 0:
        parts = parts[: -(node.level - 1)]
    base = ".".join(parts)
    if node.module:
        return f"{base}.{node.module}" if base else node.module
    return base or None
