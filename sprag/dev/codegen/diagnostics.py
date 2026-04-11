"""Targeted pre-compile diagnostics for browser codegen."""

from __future__ import annotations

import ast

from .mappings import JSCodegenError


_STATEMENT_HINTS = {
    ast.AnnAssign: "Use a plain assignment inside browser methods; keep type annotations on signatures or class fields.",
    ast.With: "Use explicit setup/cleanup and try/finally in browser code.",
    ast.AsyncWith: "Use explicit async setup/cleanup and try/finally in browser code.",
    ast.Delete: "Assign a replacement value explicitly, or use a store/path helper such as store.delete(...).",
    ast.Assert: "Use an explicit if check and handle the failure path directly.",
    ast.Import: "Move imports to module scope.",
    ast.ImportFrom: "Move imports to module scope.",
    ast.FunctionDef: "Lift nested functions to module scope or rewrite them as lambdas when possible.",
    ast.AsyncFunctionDef: "Lift nested async functions to module scope.",
    ast.ClassDef: "Lift nested classes to module scope.",
}

_EXPRESSION_HINTS = {
    ast.Set: "Rewrite this as a list/tuple for now; set literals are not supported in browser codegen yet.",
    ast.Slice: "Rewrite slices using explicit helper logic; Python slice syntax is not supported in browser codegen yet.",
    ast.Yield: "Move generator logic to server code or materialise the iterable eagerly.",
    ast.YieldFrom: "Move generator logic to server code or materialise the iterable eagerly.",
}


def lint_browser_method(
    function_def,
    *,
    source,
    source_file,
    class_name,
    method_name,
    line_offset=0,
):
    for stmt in function_def.body:
        _lint_stmt(
            stmt,
            source=source,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
            line_offset=line_offset,
        )


def _lint_stmt(node, *, source, source_file, class_name, method_name, line_offset):
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
    )


def _lint_expr(node, *, source, source_file, class_name, method_name, line_offset):
    if isinstance(node, (ast.Constant, ast.Name)):
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
