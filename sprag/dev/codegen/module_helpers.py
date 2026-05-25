"""Compile top-level Python defs and constants in a browser source file to JS.

Browser source files (``components.py``, ``modules.py``, ``web.py``) often
contain small free functions and module-level constants that are shared
by multiple ``Component`` / ``Module`` classes in the same file. Without
this module, those names emit as bare identifiers in the generated JS and
ReferenceError at runtime.

This module:
  1. Walks the source file's AST and collects top-level ``def`` and
     ``name = expr`` / ``name: T = expr`` assignments.
  2. Given the set of names actually referenced from a compiled class
     (transitively, so helpers that call helpers come along too), emits
     the JS for each — ``def`` becomes ``function``, assignments become
     ``const``.
  3. The result is spliced into each generated class's ``.js`` file as a
     prelude after imports, before ``export class``.

A helper that raises ``JSCodegenError`` during compilation surfaces the
error to the user — the same way a class method would. Helpers that
reference unknown identifiers (e.g. ``datetime`` imported at the top of
the file) compile to bare JS identifiers that ``ReferenceError`` at
runtime, matching how methods on ``Component``/``Module`` handle the
same situation — bare names always fall through to identifier emission,
and authors are expected to keep server-only dependencies out of
browser source files.

Design decisions (locked in for v0.x):

* **async def at module level is not picked up.** Only ``ast.FunctionDef``
  is collected, not ``ast.AsyncFunctionDef``. If a helper needs to
  ``await``, promote it to a method on the ``Module`` so it can use
  ``self.call_action(...)`` and the rest of the async surface. A future
  release could lift this restriction once we have a story for what
  ``await`` means inside a stateless helper.

* **Imports are not re-exported as helpers.** A ``from .x import helper``
  at the top of a browser file does not surface ``helper`` as a
  compilable helper. The compiler would need to follow the import,
  parse the source file, and compile from there — significant extra
  machinery. Users who want to share helpers across files today should
  either (a) duplicate the helper in each file, or (b) write a JS shim
  and plug it in via ``page(modules={"alias": "shared.js"})``.

* **Each class file gets its own copy of the helpers it uses.** Two
  classes in the same source file that both reference ``format_price``
  each emit a ``function format_price(...)`` in their generated JS.
  Pulling helpers into a shared sidecar module would deduplicate but
  add a layer of indirection (and an extra fetch). The inline copy is
  small and gzip-friendly; revisit if a real app's bundle size proves
  it's a problem.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Callable

from .expressions import _compile_expr
from .mappings import JSCodegenError
from .source_maps import mappings_for_text
from .statements import _compile_statements_with_mappings


def collect_module_helpers(source_file: str | None) -> dict[str, ast.AST]:
    """Return ``{name: FunctionDef | Assign | AnnAssign}`` for top-level helpers."""
    if not source_file:
        return {}
    path = Path(source_file)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    helpers: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            if node.decorator_list:
                # Decorated helpers (e.g. @debounce) are method-shaped; skip.
                continue
            helpers[node.name] = node
        elif isinstance(node, ast.Assign):
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            helpers[node.targets[0].id] = node
        elif isinstance(node, ast.AnnAssign):
            if not isinstance(node.target, ast.Name) or node.value is None:
                continue
            helpers[node.target.id] = node
    return helpers


def _names_referenced(node: ast.AST) -> set[str]:
    """Return all bare-name loads inside ``node``."""
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }


def check_helper_name_collisions(
    helpers: dict[str, ast.AST],
    *,
    store_names: set[str],
    class_names: set[str],
    source_file: str | None = None,
) -> None:
    """Raise JSCodegenError if a helper name collides with a file-scope import.

    Store imports (``import { X } from '../stores.js'``) and browser class
    imports both land at file scope in the generated JS. A top-level helper
    with the same name would emit a duplicate declaration and break ESM
    parsing. Surface this at build time with a clear message rather than
    letting the runtime fail with a cryptic SyntaxError.
    """
    for name in helpers:
        if name in store_names:
            raise JSCodegenError(
                f"Top-level helper `{name}` collides with the same-named store. "
                "Rename the helper (e.g. `_format_{name}` or `make_{name}`) — "
                "stores are imported at file scope in the generated JS so the "
                "two would clash.",
                source_file=source_file,
            )
        if name in class_names:
            raise JSCodegenError(
                f"Top-level helper `{name}` collides with an imported browser "
                f"class of the same name. Rename the helper — both end up at "
                "file scope in the generated JS.",
                source_file=source_file,
            )


def select_used_helpers(
    helpers: dict[str, ast.AST],
    seed_names: set[str],
) -> list[str]:
    """Return helper names referenced (transitively) by ``seed_names``,
    ordered so dependencies appear before dependents.
    """
    order: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visited or name not in helpers or name in visiting:
            return
        visiting.add(name)
        for ref in _names_referenced(helpers[name]):
            if ref != name and ref in helpers:
                visit(ref)
        visiting.discard(name)
        visited.add(name)
        order.append(name)

    for name in seed_names:
        visit(name)
    return order


def referenced_helper_names_in_class(cls, helpers: dict[str, ast.AST]) -> set[str]:
    """Return helper names referenced by ``cls``'s own source."""
    if not helpers:
        return set()
    try:
        source = inspect.getsource(cls)
    except (OSError, TypeError):
        return set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id in helpers
    }


def compile_module_helpers_prelude(
    helpers: dict[str, ast.AST],
    used: list[str],
    *,
    seed_env: Callable[..., dict],
    source_file: str | None = None,
) -> tuple[str, list]:
    """Emit JS source for the given helpers in order.

    Returns ``(code, line_mappings)`` ready to splice into the generated
    class file. JSCodegenErrors from helper compilation are surfaced with
    the source file + helper name attached, the same way class methods
    surface their own codegen errors.
    """
    if not used:
        return "", []

    chunks: list[str] = []
    mappings: list = []

    for name in used:
        node = helpers[name]
        try:
            piece, piece_mappings = _compile_one_helper(node, name, seed_env=seed_env)
        except JSCodegenError as exc:
            raise exc.with_context(
                source_file=source_file,
                class_name=None,
                method_name=f"<top-level helper `{name}`>",
                line=exc.line if exc.line is not None else getattr(node, "lineno", None),
            ) from exc
        chunks.append(piece)
        mappings.extend(piece_mappings)

    if chunks:
        chunks.append("\n")
        mappings.append(None)
    return "".join(chunks), mappings


def _compile_one_helper(node: ast.AST, name: str, *, seed_env: Callable[..., dict]) -> tuple[str, list]:
    if isinstance(node, ast.FunctionDef):
        env = seed_env(method_name=name, line_offset=node.lineno - 1)
        # Helper params shadow any same-name env entry inside the body.
        params = [arg.arg for arg in node.args.args]
        for param in params:
            env[param] = param
        body, body_mappings = _compile_statements_with_mappings(
            node.body,
            env=env,
            indent=4,
            source_line_offset=node.lineno - 1,
            source_name=name,
        )
        header = f"function {name}({', '.join(params)}) {{"
        footer = "}\n"
        text = f"{header}\n{body}\n{footer}"
        out_mappings: list = []
        out_mappings.extend(mappings_for_text(header + "\n", source_line=node.lineno, name=name))
        out_mappings.extend(body_mappings)
        out_mappings.append(None)  # body trailing newline
        out_mappings.extend(mappings_for_text(footer, source_line=node.lineno, name=name))
        return text, out_mappings

    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        value_node = node.value
        env = seed_env(method_name=name, line_offset=node.lineno - 1)
        value = _compile_expr(value_node, env)
        line = f"const {name} = {value};\n"
        return line, mappings_for_text(line, source_line=node.lineno, name=name)

    raise JSCodegenError(f"unsupported helper node: {type(node).__name__}")
