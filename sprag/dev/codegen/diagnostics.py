"""Targeted pre-compile diagnostics for browser codegen."""

from __future__ import annotations

import ast

from .mappings import JSCodegenError


_STATEMENT_HINTS = {
    ast.With: (
        "Python `with` statements cannot be compiled to JavaScript. "
        "Acquire the resource in on_start() and release it in on_stop() or via self.add_cleanup(fn), "
        "or use try/finally for inline scope."
    ),
    ast.AsyncWith: (
        "Python `async with` statements cannot be compiled to JavaScript. "
        "Acquire the resource in on_start() and release it in on_stop() or via self.add_cleanup(fn), "
        "or use try/finally for inline scope."
    ),
    ast.Delete: "Assign a replacement value explicitly, or use a store/path helper such as store.delete(...).",
    ast.Assert: "Use an explicit if check and handle the failure path directly.",
    ast.Import: "Move imports to module scope.",
    ast.ImportFrom: "Move imports to module scope.",
    ast.FunctionDef: "Lift nested functions to module scope or rewrite them as lambdas when possible.",
    ast.AsyncFunctionDef: "Lift nested async functions to module scope.",
    ast.ClassDef: "Lift nested classes to module scope.",
    ast.Raise: "Use explicit error handling patterns; raise is not supported in browser codegen.",
    ast.Global: "Browser methods do not support global declarations; pass values through state or arguments.",
    ast.Nonlocal: "Browser methods do not support nonlocal declarations; use state or closures instead.",
}
if hasattr(ast, "AsyncFor"):
    _STATEMENT_HINTS[ast.AsyncFor] = "Async for loops are not supported in browser codegen; use await with an eagerly materialised iterable."

_EXPRESSION_HINTS = {
    ast.Set: "Rewrite this as a list/tuple for now; set literals are not supported in browser codegen yet.",
    ast.Yield: "Move generator logic to server code or materialise the iterable eagerly.",
    ast.YieldFrom: "Move generator logic to server code or materialise the iterable eagerly.",
}

# BinOp operators that the codegen cannot lower to JS.
_UNSUPPORTED_BINOP = {}
for _op_cls, _hint in [
    (ast.FloorDiv, "Use int(a / b) or Math.floor via browser.Math.floor(...) instead of //."),
    (ast.Pow, "Use browser.Math.pow(base, exp) instead of **."),
    (ast.LShift, "Bitwise left shift (<<) is not supported in browser codegen."),
    (ast.RShift, "Bitwise right shift (>>) is not supported in browser codegen."),
    (ast.BitAnd, "Bitwise AND (&) is not supported in browser codegen."),
    (ast.BitXor, "Bitwise XOR (^) is not supported in browser codegen."),
    (ast.MatMult, "Matrix multiply (@) is not supported in browser codegen."),
]:
    _UNSUPPORTED_BINOP[_op_cls] = _hint

# UnaryOp operators that the codegen cannot lower.
_UNSUPPORTED_UNARYOP = {
    ast.Invert: "Bitwise NOT (~) is not supported in browser codegen.",
}


def lint_browser_method(
    function_def,
    *,
    source,
    source_file,
    class_name,
    method_name,
    line_offset=0,
    disallow_component_subscribe=False,
):
    if disallow_component_subscribe:
        _lint_disallowed_component_calls(
            function_def,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
    for stmt in function_def.body:
        _lint_stmt(
            stmt,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )


def _lint_disallowed_component_calls(
    function_def,
    *,
    source,
    source_file,
    class_name,
    method_name,
    line_offset,
):
    for node in ast.walk(function_def):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "self":
            continue
        if node.func.attr != "subscribe":
            continue
        _raise(
            "Component.subscribe(...) is not part of SPRAG's browser contract.",
            node,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
            suggestion=(
                "Subscribe in a Module, then pass the derived state into the Component "
                "through props or component state."
            ),
        )


def _lint_stmt(node, *, source, source_file, class_name, method_name, line_offset):
    if isinstance(node, ast.AnnAssign):
        if node.value is None:
            _raise(
                "Annotation-only local variables are not supported in browser codegen.",
                node,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
                suggestion="Give the variable an initial value, e.g. `items: list[str] = []`.",
            )
        _lint_assign_target(
            node.target,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        _lint_expr(
            node.value,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        return
    if isinstance(node, ast.Assign):
        for target in node.targets:
            _lint_assign_target(
                target,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
            )
        _lint_expr(
            node.value,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        return
    if isinstance(node, ast.AugAssign):
        _lint_assign_target(
            node.target,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        _lint_expr(
            node.value,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        return
    if isinstance(node, ast.Expr):
        _lint_expr(
            node.value,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        return
    if isinstance(node, ast.Return):
        if node.value is not None:
            _lint_expr(
                node.value,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
            )
        return
    if isinstance(node, ast.If):
        _lint_expr(
            node.test,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        for stmt in node.body + node.orelse:
            _lint_stmt(
                stmt,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
            )
        return
    if isinstance(node, ast.For):
        _lint_assign_target(
            node.target,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        _lint_expr(
            node.iter,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        for stmt in node.body:
            _lint_stmt(
                stmt,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
            )
        if node.orelse:
            _raise(
                "for/else is not supported in browser codegen.",
                node,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
                suggestion="Rewrite the else branch as an explicit post-loop condition.",
            )
        return
    if isinstance(node, ast.While):
        _lint_expr(
            node.test,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        for stmt in node.body:
            _lint_stmt(
                stmt,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
            )
        if node.orelse:
            _raise(
                "while/else is not supported in browser codegen.",
                node,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
                suggestion="Rewrite the else branch as an explicit post-loop condition.",
            )
        return
    if isinstance(node, ast.Try):
        if node.orelse:
            _raise(
                "try/else is not supported in browser codegen.",
                node,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
                suggestion="Move the else branch after the try block and guard it explicitly.",
            )
        for stmt in node.body + node.finalbody:
            _lint_stmt(
                stmt,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
            )
        for handler in node.handlers:
            if handler.type is not None:
                _lint_expr(
                    handler.type,
                    source=source,
                    source_file=source_file,
                    class_name=class_name,
                    method_name=method_name,
                    line_offset=line_offset,
                )
            for stmt in handler.body:
                _lint_stmt(
                    stmt,
                    source=source,
                    source_file=source_file,
                    class_name=class_name,
                    method_name=method_name,
                    line_offset=line_offset,
                )
        return
    if isinstance(node, (ast.Break, ast.Continue, ast.Pass)):
        return
    _raise(
        f"Unsupported statement in browser codegen: {node.__class__.__name__}.",
        node,
        source=source,
        source_file=source_file,
        class_name=class_name,
        method_name=method_name,
        line_offset=line_offset,
        suggestion=_STATEMENT_HINTS.get(type(node)),
    )


def _lint_assign_target(node, *, source, source_file, class_name, method_name, line_offset):
    if isinstance(node, (ast.Name, ast.Attribute)):
        return
    if isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            if not isinstance(elt, ast.Name):
                _raise(
                    "Nested unpacking is not supported in browser codegen.",
                    elt,
                    source=source,
                    source_file=source_file,
                    class_name=class_name,
                    method_name=method_name,
                    line_offset=line_offset,
                )
        return
    _raise(
        f"Unsupported assignment target in browser codegen: {node.__class__.__name__}.",
        node,
        source=source,
        source_file=source_file,
        class_name=class_name,
        method_name=method_name,
        line_offset=line_offset,
        suggestion=(
            "Browser state is immutable from the component's perspective — subscript assignment does not trigger "
            "re-render. Use self.set_state({...}) or self.patch({...}) instead."
        ) if isinstance(node, ast.Subscript) else None,
    )


def _lint_expr(node, *, source, source_file, class_name, method_name, line_offset):
    if isinstance(node, (ast.Constant, ast.Name)):
        return
    if isinstance(node, ast.Slice):
        if node.step is not None:
            _raise(
                "Slice steps are not supported in browser codegen.",
                node,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
                suggestion="Use start/end slicing only, e.g. `items[start:end]`.",
            )
        for part in (node.lower, node.upper):
            if part is not None:
                _lint_expr(
                    part,
                    source=source,
                    source_file=source_file,
                    class_name=class_name,
                    method_name=method_name,
                    line_offset=line_offset,
                )
        return
    if isinstance(node, ast.Attribute):
        _lint_expr(
            node.value,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        return
    if isinstance(node, ast.Subscript):
        _lint_expr(
            node.value,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        _lint_expr(
            node.slice,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        return
    if isinstance(node, ast.BinOp):
        hint = _UNSUPPORTED_BINOP.get(type(node.op))
        if hint is not None:
            _raise(
                f"Unsupported operator in browser codegen: {node.op.__class__.__name__}.",
                node,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
                suggestion=hint,
            )
        _lint_expr(
            node.left,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        _lint_expr(
            node.right,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        return
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            _lint_expr(
                value,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
            )
        return
    if isinstance(node, ast.IfExp):
        _lint_expr(
            node.test,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        _lint_expr(
            node.body,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        _lint_expr(
            node.orelse,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        return
    if isinstance(node, (ast.List, ast.Tuple)):
        for elt in node.elts:
            _lint_expr(
                elt,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
            )
        return
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values):
            if key is not None:
                _lint_expr(
                    key,
                    source=source,
                    source_file=source_file,
                    class_name=class_name,
                    method_name=method_name,
                    line_offset=line_offset,
                )
            _lint_expr(
                value,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
            )
        return
    if isinstance(node, (ast.ListComp, ast.DictComp, ast.GeneratorExp, ast.SetComp)):
        if len(node.generators) > 1:
            _raise(
                "Multi-generator comprehensions are not supported in browser codegen.",
                node,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
                suggestion="Rewrite as nested loops or chain filter/map calls.",
            )
        for generator in node.generators:
            _lint_assign_target(
                generator.target,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
            )
            _lint_expr(
                generator.iter,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
            )
            for if_node in generator.ifs:
                _lint_expr(
                    if_node,
                    source=source,
                    source_file=source_file,
                    class_name=class_name,
                    method_name=method_name,
                    line_offset=line_offset,
                )
        value_nodes = []
        if hasattr(node, "elt"):
            value_nodes.append(node.elt)
        if hasattr(node, "key"):
            value_nodes.append(node.key)
        if hasattr(node, "value"):
            value_nodes.append(node.value)
        for value_node in value_nodes:
            _lint_expr(
                value_node,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
            )
        return
    if isinstance(node, ast.Call):
        _lint_expr(
            node.func,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        for arg in node.args:
            _lint_expr(
                arg,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
            )
        for keyword in node.keywords:
            _lint_expr(
                keyword.value,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
            )
        return
    if isinstance(node, ast.Await):
        _lint_expr(
            node.value,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        return
    if isinstance(node, ast.Lambda):
        _lint_expr(
            node.body,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        return
    if isinstance(node, ast.NamedExpr):
        if not isinstance(node.target, ast.Name):
            _raise(
                "Walrus operator only supports simple name targets in browser codegen.",
                node,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
            )
        _lint_expr(
            node.value,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        return
    if isinstance(node, ast.Compare):
        _lint_expr(
            node.left,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        for comparator in node.comparators:
            _lint_expr(
                comparator,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
            )
        return
    if isinstance(node, ast.UnaryOp):
        hint = _UNSUPPORTED_UNARYOP.get(type(node.op))
        if hint is not None:
            _raise(
                f"Unsupported operator in browser codegen: {node.op.__class__.__name__}.",
                node,
                source=source,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                line_offset=line_offset,
                suggestion=hint,
            )
        _lint_expr(
            node.operand,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )
        return
    if isinstance(node, ast.JoinedStr):
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                _lint_expr(
                    value.value,
                    source=source,
                    source_file=source_file,
                    class_name=class_name,
                    method_name=method_name,
                    line_offset=line_offset,
                )
        return
    _raise(
        f"Unsupported expression in browser codegen: {node.__class__.__name__}.",
        node,
        source=source,
        source_file=source_file,
        class_name=class_name,
        method_name=method_name,
        line_offset=line_offset,
        suggestion=_EXPRESSION_HINTS.get(type(node)),
    )


def _raise(message, node, *, source, source_file, class_name, method_name, line_offset, suggestion=None):
    line = getattr(node, "lineno", None)
    source_line = None
    if line is not None:
        lines = source.splitlines()
        if 1 <= line <= len(lines):
            source_line = lines[line - 1]
        line = line_offset + line - 1
    raise JSCodegenError(
        message,
        source_file=source_file,
        class_name=class_name,
        method_name=method_name,
        line=line,
        source_line=source_line,
        suggestion=suggestion,
    )
