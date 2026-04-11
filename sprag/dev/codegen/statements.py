"""Compile Python AST statement nodes into JavaScript statement strings.

Handles assignments, augmented assignments, expressions, returns,
``if``/``elif``/``else``, ``for`` (including ``range``-special-cased),
and ``try``/``except``/``finally``.
"""

from __future__ import annotations

import ast
from itertools import count

from .expressions import _compile_comp_target, _compile_expr
from .mappings import JSCodegenError, _compile_binop


_AST_MATCH = getattr(ast, "Match", None)
_AST_MATCH_AS = getattr(ast, "MatchAs", None)
_AST_MATCH_CLASS = getattr(ast, "MatchClass", None)
_AST_MATCH_MAPPING = getattr(ast, "MatchMapping", None)
_AST_MATCH_OR = getattr(ast, "MatchOr", None)
_AST_MATCH_SEQUENCE = getattr(ast, "MatchSequence", None)
_AST_MATCH_SINGLETON = getattr(ast, "MatchSingleton", None)
_AST_MATCH_STAR = getattr(ast, "MatchStar", None)
_AST_MATCH_VALUE = getattr(ast, "MatchValue", None)
_MATCH_TEMP_COUNTER = count()


def _compile_statements(statements, *, method_names=None, env=None, indent=8):
    if env is None:
        env = {}
    pad = " " * indent
    lines = []
    for stmt in statements:
        lines.extend(_declare_namedexpr_bindings(stmt, env, pad))
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            target = stmt.targets[0].id
            compiled_value = _compile_expr(stmt.value, env, method_names=method_names)
            # Use ``let`` rather than ``const`` because Python has no const
            # and its assignments are rebindable. Without this, ``x = 0``
            # followed by ``x += 1`` or a reassignment inside a loop would
            # trip JS's const guard at runtime. Re-binding a name that is
            # already declared in this scope emits a bare assignment, so
            # the code compiles to idiomatic JS.
            if target in env:
                lines.append(f"{pad}{target} = {compiled_value};")
            else:
                env[target] = target
                lines.append(f"{pad}let {target} = {compiled_value};")
            continue
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], (ast.Tuple, ast.List))
        ):
            # ``a, b = pair`` -> ``let [a, b] = pair;`` (one level deep).
            pattern, names = _compile_comp_target(stmt.targets[0])
            compiled_value = _compile_expr(stmt.value, env, method_names=method_names)
            # If all names are already declared in this scope, emit a bare
            # destructuring assignment; otherwise declare them fresh.
            if all(name in env for name in names):
                lines.append(f"{pad}{pattern} = {compiled_value};")
            else:
                for name in names:
                    env[name] = name
                lines.append(f"{pad}let {pattern} = {compiled_value};")
            continue
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Attribute)
        ):
            target = _compile_expr(stmt.targets[0], env, method_names=method_names)
            compiled_value = _compile_expr(stmt.value, env, method_names=method_names)
            lines.append(f"{pad}{target} = {compiled_value};")
            continue
        if isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
            target = _compile_expr(stmt.target, env, method_names=method_names)
            if isinstance(stmt.op, ast.BitOr):
                value = _compile_expr(stmt.value, env, method_names=method_names)
                lines.append(f"{pad}{target} = {{ ...{target}, ...{value} }};")
                continue
            op = _compile_binop(stmt.op)
            value = _compile_expr(stmt.value, env, method_names=method_names)
            lines.append(f"{pad}{target} {op}= {value};")
            continue
        if isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Attribute):
            target = _compile_expr(stmt.target, env, method_names=method_names)
            if isinstance(stmt.op, ast.BitOr):
                value = _compile_expr(stmt.value, env, method_names=method_names)
                lines.append(f"{pad}{target} = {{ ...{target}, ...{value} }};")
                continue
            op = _compile_binop(stmt.op)
            value = _compile_expr(stmt.value, env, method_names=method_names)
            lines.append(f"{pad}{target} {op}= {value};")
            continue
        if isinstance(stmt, ast.Expr):
            lines.append(f"{pad}{_compile_expr(stmt.value, env, method_names=method_names)};")
            continue
        if isinstance(stmt, ast.Return):
            if stmt.value is None:
                lines.append(f"{pad}return undefined;")
            else:
                lines.append(
                    f"{pad}return {_compile_expr(stmt.value, env, method_names=method_names)};"
                )
            continue
        if isinstance(stmt, ast.If):
            lines.append(_compile_if(stmt, env, method_names=method_names, indent=indent))
            continue
        if isinstance(stmt, ast.For):
            lines.append(_compile_for(stmt, env, method_names=method_names, indent=indent))
            continue
        if isinstance(stmt, ast.While):
            lines.append(_compile_while(stmt, env, method_names=method_names, indent=indent))
            continue
        if isinstance(stmt, ast.Break):
            lines.append(f"{pad}break;")
            continue
        if isinstance(stmt, ast.Continue):
            lines.append(f"{pad}continue;")
            continue
        if isinstance(stmt, ast.Try):
            lines.append(_compile_try(stmt, env, method_names=method_names, indent=indent))
            continue
        if _AST_MATCH is not None and isinstance(stmt, _AST_MATCH):
            lines.append(_compile_match(stmt, env, method_names=method_names, indent=indent))
            continue
        if isinstance(stmt, ast.Pass):
            # ``pass`` emits nothing (same intent as Python: an explicit no-op).
            continue
        raise JSCodegenError(f"Unsupported module statement: {ast.dump(stmt)}")
    return "\n".join(lines) or f"{pad}return undefined;"


def _compile_if(node, env, *, method_names=None, indent=8):
    pad = " " * indent
    test = _compile_expr(node.test, env, method_names=method_names)
    body = _compile_statements(node.body, method_names=method_names, env=dict(env), indent=indent + 4)
    result = f"{pad}if ({test}) {{\n{body}\n{pad}}}"
    if node.orelse:
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            result += " else " + _compile_if(node.orelse[0], env, method_names=method_names, indent=indent).lstrip()
        else:
            else_body = _compile_statements(node.orelse, method_names=method_names, env=dict(env), indent=indent + 4)
            result += f" else {{\n{else_body}\n{pad}}}"
    return result


def _compile_for(node, env, *, method_names=None, indent=8):
    pad = " " * indent
    target_js, target_names = _compile_comp_target(node.target)
    inner_env = dict(env)
    for name in target_names:
        inner_env[name] = name

    # Special case: for i in range(...). We emit a C-style loop instead of
    # materialising an array, but only when the target is a plain name --
    # ``for i, j in range(...)`` would be a Python error anyway.
    if (
        isinstance(node.iter, ast.Call)
        and isinstance(node.iter.func, ast.Name)
        and node.iter.func.id == "range"
        and isinstance(node.target, ast.Name)
    ):
        args = node.iter.args
        if len(args) == 1:
            limit = _compile_expr(args[0], env, method_names=method_names)
            header = f"for (let {target_js} = 0; {target_js} < {limit}; {target_js}++)"
        elif len(args) == 2:
            start = _compile_expr(args[0], env, method_names=method_names)
            limit = _compile_expr(args[1], env, method_names=method_names)
            header = f"for (let {target_js} = {start}; {target_js} < {limit}; {target_js}++)"
        elif len(args) == 3:
            start = _compile_expr(args[0], env, method_names=method_names)
            limit = _compile_expr(args[1], env, method_names=method_names)
            step = _compile_expr(args[2], env, method_names=method_names)
            header = f"for (let {target_js} = {start}; {target_js} < {limit}; {target_js} += {step})"
        else:
            raise JSCodegenError("range() expects 1-3 arguments")
    else:
        iter_expr = _compile_expr(node.iter, env, method_names=method_names)
        header = f"for (const {target_js} of {iter_expr})"

    body = _compile_statements(node.body, method_names=method_names, env=inner_env, indent=indent + 4)
    return f"{pad}{header} {{\n{body}\n{pad}}}"


def _compile_while(node, env, *, method_names=None, indent=8):
    pad = " " * indent
    test = _compile_expr(node.test, env, method_names=method_names)
    body = _compile_statements(
        node.body, method_names=method_names, env=dict(env), indent=indent + 4
    )
    if node.orelse:
        # Python's ``while ... else`` has no clean JS equivalent. Reject
        # loudly rather than silently dropping the else branch.
        raise JSCodegenError("while/else is not supported.")
    return f"{pad}while ({test}) {{\n{body}\n{pad}}}"


def _compile_try(node, env, *, method_names=None, indent=8):
    pad = " " * indent
    try_body = _compile_statements(node.body, method_names=method_names, env=dict(env), indent=indent + 4)
    result = f"{pad}try {{\n{try_body}\n{pad}}}"

    for handler in node.handlers:
        exc_name = handler.name or "e"
        inner_env = dict(env)
        inner_env[exc_name] = exc_name
        handler_body = _compile_statements(handler.body, method_names=method_names, env=inner_env, indent=indent + 4)
        result += f" catch ({exc_name}) {{\n{handler_body}\n{pad}}}"

    if node.finalbody:
        finally_body = _compile_statements(node.finalbody, method_names=method_names, env=dict(env), indent=indent + 4)
        result += f" finally {{\n{finally_body}\n{pad}}}"

    return result


def _compile_match(node, env, *, method_names=None, indent=8):
    pad = " " * indent
    match_id = next(_MATCH_TEMP_COUNTER)
    subject_name = f"__spragMatch{match_id}"
    matched_name = f"__spragMatched{match_id}"
    subject_expr = _compile_expr(node.subject, env, method_names=method_names)

    lines = [
        f"{pad}const {subject_name} = {subject_expr};",
        f"{pad}let {matched_name} = false;",
    ]

    for case in node.cases:
        case_env = dict(env)
        test_js, bindings = _compile_match_pattern(
            case.pattern,
            subject_name,
            env,
            method_names=method_names,
        )
        lines.append(f"{pad}if (!{matched_name}) {{")
        lines.append(f"{pad}    if ({test_js}) {{")
        for name, expr in bindings:
            case_env[name] = name
            lines.append(f"{pad}        let {name} = {expr};")
        if case.guard is not None:
            guard_js = _compile_expr(case.guard, case_env, method_names=method_names)
            lines.append(f"{pad}        if ({guard_js}) {{")
            lines.append(f"{pad}            {matched_name} = true;")
            case_body = _compile_statements(
                case.body,
                method_names=method_names,
                env=dict(case_env),
                indent=indent + 12,
            )
            lines.append(case_body)
            lines.append(f"{pad}        }}")
        else:
            lines.append(f"{pad}        {matched_name} = true;")
            case_body = _compile_statements(
                case.body,
                method_names=method_names,
                env=dict(case_env),
                indent=indent + 8,
            )
            lines.append(case_body)
        lines.append(f"{pad}    }}")
        lines.append(f"{pad}}}")

    return "\n".join(lines)


def _compile_match_pattern(pattern, subject_js, env, *, method_names=None):
    if _AST_MATCH_AS is not None and isinstance(pattern, _AST_MATCH_AS):
        # ``case _`` arrives as MatchAs() with no name/pattern.
        if pattern.pattern is None and pattern.name is None:
            return "true", []
        if pattern.pattern is None:
            return "true", [(pattern.name, subject_js)]
        test_js, bindings = _compile_match_pattern(
            pattern.pattern,
            subject_js,
            env,
            method_names=method_names,
        )
        if pattern.name is not None:
            bindings = list(bindings) + [(pattern.name, subject_js)]
        return test_js, bindings

    if _AST_MATCH_VALUE is not None and isinstance(pattern, _AST_MATCH_VALUE):
        value_js = _compile_expr(pattern.value, env, method_names=method_names)
        return f"{subject_js} === {value_js}", []

    if _AST_MATCH_SINGLETON is not None and isinstance(pattern, _AST_MATCH_SINGLETON):
        value_js = _compile_expr(ast.Constant(pattern.value), env, method_names=method_names)
        return f"{subject_js} === {value_js}", []

    if _AST_MATCH_SEQUENCE is not None and isinstance(pattern, _AST_MATCH_SEQUENCE):
        if any(_AST_MATCH_STAR is not None and isinstance(item, _AST_MATCH_STAR) for item in pattern.patterns):
            raise JSCodegenError(
                "match/case sequence star patterns are not supported in browser codegen yet."
            )
        tests = [f"Array.isArray({subject_js})", f"{subject_js}.length === {len(pattern.patterns)}"]
        bindings = []
        for index, item in enumerate(pattern.patterns):
            item_subject = f"{subject_js}[{index}]"
            item_test, item_bindings = _compile_match_pattern(
                item,
                item_subject,
                env,
                method_names=method_names,
            )
            tests.append(f"({item_test})")
            bindings.extend(item_bindings)
        return " && ".join(tests), bindings

    if _AST_MATCH_MAPPING is not None and isinstance(pattern, _AST_MATCH_MAPPING):
        if pattern.rest is not None:
            raise JSCodegenError(
                "match/case mapping rest patterns (`**rest`) are not supported in browser codegen yet."
            )
        tests = [
            f"{subject_js} != null",
            f"typeof {subject_js} === \"object\"",
            f"!Array.isArray({subject_js})",
        ]
        bindings = []
        for key, value_pattern in zip(pattern.keys, pattern.patterns):
            key_js = _compile_expr(key, env, method_names=method_names)
            tests.append(f"Object.prototype.hasOwnProperty.call({subject_js}, {key_js})")
            item_subject = f"{subject_js}[{key_js}]"
            item_test, item_bindings = _compile_match_pattern(
                value_pattern,
                item_subject,
                env,
                method_names=method_names,
            )
            tests.append(f"({item_test})")
            bindings.extend(item_bindings)
        return " && ".join(tests), bindings

    if _AST_MATCH_OR is not None and isinstance(pattern, _AST_MATCH_OR):
        tests = []
        for option in pattern.patterns:
            option_test, option_bindings = _compile_match_pattern(
                option,
                subject_js,
                env,
                method_names=method_names,
            )
            if option_bindings:
                raise JSCodegenError(
                    "match/case OR patterns with bindings are not supported in browser codegen yet."
                )
            tests.append(f"({option_test})")
        return " || ".join(tests), []

    if _AST_MATCH_CLASS is not None and isinstance(pattern, _AST_MATCH_CLASS):
        raise JSCodegenError(
            "match/case class patterns are not supported in browser codegen yet."
        )

    if _AST_MATCH_STAR is not None and isinstance(pattern, _AST_MATCH_STAR):
        raise JSCodegenError(
            "match/case star patterns are not supported in browser codegen yet."
        )

    raise JSCodegenError(f"Unsupported match/case pattern: {ast.dump(pattern)}")


def _declare_namedexpr_bindings(node, env, pad):
    lines = []
    for name in _iter_namedexpr_targets(node):
        if name in env:
            continue
        env[name] = name
        lines.append(f"{pad}let {name};")
    return lines


def _iter_namedexpr_targets(node):
    if _AST_MATCH is not None and isinstance(node, _AST_MATCH):
        return
    if isinstance(node, (ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp, ast.Lambda)):
        return
    if isinstance(node, ast.NamedExpr):
        if isinstance(node.target, ast.Name):
            yield node.target.id
        return
    for child in ast.iter_child_nodes(node):
        yield from _iter_namedexpr_targets(child)
