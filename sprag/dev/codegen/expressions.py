"""Compile Python AST expression nodes into JavaScript expression strings.

This module owns the recursive ``_compile_expr`` and the two namespace
dispatchers ``_compile_ui_call`` (for ``ui.tag(...)`` factory calls) and
``_compile_dom_call`` (for ``dom.helper(...)`` calls).
"""

from __future__ import annotations

import ast
import json

from ...runtime.attrs import normalize_attr_key
from ...runtime.stores import STORE_METHOD_JS, STORE_METHODS_OPTIONS_KWARGS
from .diagnostics import _raise
from .mappings import (
    JSCodegenError,
    _DOM_METHOD_MAP,
    _compile_binop,
    _compile_cmpop,
    _map_name,
)


def _compile_expr(node, env, method_names=None):
    method_names = method_names or set()
    if isinstance(node, ast.Constant):
        return json.dumps(node.value)
    if isinstance(node, ast.Name):
        if node.id == "self":
            return "this"
        if node.id == "browser":
            return "globalThis"
        if node.id == "imports":
            return "(globalThis.__SPRAG_IMPORTS__ || {})"
        if node.id == "join_url":
            return "joinUrl"
        # Store references resolve to their JS store name (the same name
        # the generated stores.js shim exports). This is what makes
        # ``self.subscribe(counter, fn)`` compile cleanly: the ``counter``
        # arg becomes the JS variable ``counter`` imported from stores.js.
        store_refs = env.get("__sprag_stores__") or {}
        if node.id in store_refs:
            return store_refs[node.id]
        return env.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        special_js_namespace = _compile_special_js_namespace_attr(node, env)
        if special_js_namespace is not None:
            return special_js_namespace
        return f"{_compile_expr(node.value, env, method_names=method_names)}.{_map_name(node.attr)}"
    if isinstance(node, ast.Subscript):
        value = _compile_expr(node.value, env, method_names=method_names)
        if isinstance(node.slice, ast.Slice):
            return f"{value}.slice({_compile_slice_args(node.slice, env, method_names=method_names)})"
        return (
            f"{value}"
            f"[{_compile_slice(node.slice, env, method_names=method_names)}]"
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        # In browser codegen we reserve ``|`` for Python's dict-merge
        # spelling and lower it to object spread. Bitwise-or is still
        # intentionally unsupported here.
        left = _compile_expr(node.left, env, method_names=method_names)
        right = _compile_expr(node.right, env, method_names=method_names)
        return f"({{ ...{left}, ...{right} }})"
    if isinstance(node, ast.BinOp):
        operator = _compile_binop(node.op)
        return (
            f"({_compile_expr(node.left, env, method_names=method_names)} "
            f"{operator} "
            f"{_compile_expr(node.right, env, method_names=method_names)})"
        )
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        return " || ".join(
            f"({_compile_expr(value, env, method_names=method_names)})" for value in node.values
        )
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
        return " && ".join(
            f"({_compile_expr(value, env, method_names=method_names)})" for value in node.values
        )
    if isinstance(node, ast.IfExp):
        return (
            f"({_compile_expr(node.test, env, method_names=method_names)} ? "
            f"{_compile_expr(node.body, env, method_names=method_names)} : "
            f"{_compile_expr(node.orelse, env, method_names=method_names)})"
        )
    if isinstance(node, ast.List):
        return (
            "["
            + ", ".join(_compile_expr(item, env, method_names=method_names) for item in node.elts)
            + "]"
        )
    if isinstance(node, ast.Tuple):
        return (
            "["
            + ", ".join(_compile_expr(item, env, method_names=method_names) for item in node.elts)
            + "]"
        )
    if isinstance(node, ast.Dict):
        chunks = []
        for key, value in zip(node.keys, node.values):
            # ``{**other, ...}`` -> ``{...other, ...}``. ast represents the
            # spread as a key of None whose value is the spread expression.
            if key is None:
                spread_expr = _compile_expr(value, env, method_names=method_names)
                chunks.append(f"...{spread_expr}")
                continue
            key_expr = _compile_expr(key, env, method_names=method_names)
            if key_expr.startswith('"'):
                chunks.append(f"{key_expr}: {_compile_expr(value, env, method_names=method_names)}")
            else:
                chunks.append(
                    f"[{key_expr}]: {_compile_expr(value, env, method_names=method_names)}"
                )
        return "{ " + ", ".join(chunks) + " }"
    if isinstance(node, ast.ListComp):
        chain, target_js, next_env = _compile_comprehension_chain(
            node, env, method_names=method_names
        )
        elt_js = _compile_expr(node.elt, next_env, method_names=method_names)
        return f"{chain}.map(({target_js}) => {elt_js})"
    if isinstance(node, ast.DictComp):
        chain, target_js, next_env = _compile_comprehension_chain(
            node, env, method_names=method_names
        )
        key_js = _compile_expr(node.key, next_env, method_names=method_names)
        value_js = _compile_expr(node.value, next_env, method_names=method_names)
        return (
            f"Object.fromEntries({chain}.map(({target_js}) => "
            f"[{key_js}, {value_js}]))"
        )
    if isinstance(node, ast.GeneratorExp):
        # JS generators would require a larger runtime/lowering story than
        # SPRAG has today. Lower eagerly through the same comprehension chain
        # as list comprehensions so authors can still write the natural Python
        # spelling anywhere an iterable/array-like value is acceptable.
        chain, target_js, next_env = _compile_comprehension_chain(
            node, env, method_names=method_names
        )
        elt_js = _compile_expr(node.elt, next_env, method_names=method_names)
        return f"{chain}.map(({target_js}) => {elt_js})"
    if isinstance(node, ast.SetComp):
        chain, target_js, next_env = _compile_comprehension_chain(
            node, env, method_names=method_names
        )
        elt_js = _compile_expr(node.elt, next_env, method_names=method_names)
        return f"new Set({chain}.map(({target_js}) => {elt_js}))"
    if isinstance(node, ast.Call):
        env_helpers = env.get("__sprag_env_helpers__") or {}
        if isinstance(node.func, ast.Name) and env_helpers.get(node.func.id) == "env":
            return _compile_env_call(node, env, method_names=method_names)
        if isinstance(node.func, ast.Name) and env_helpers.get(node.func.id) == "public_env":
            return _compile_public_env_call(node)
        # Python builtins that map to JS equivalents. These are compiled
        # before the generic callee path so the user can write idiomatic
        # Python (``len(xs)``, ``str(x)``, ``print(x)``) and have it land
        # on the right JS shape. ``isinstance`` is intentionally omitted --
        # Python's type system and JS's don't line up cleanly, and there's
        # no one-liner that's correct in every case.
        if isinstance(node.func, ast.Name) and node.func.id in _BUILTIN_CALLS:
            return _compile_builtin_call(node, env, method_names=method_names)
        if isinstance(node.func, ast.Attribute):
            python_method = _compile_python_method_call(
                node,
                env,
                method_names=method_names,
            )
            if python_method is not None:
                return python_method
        if isinstance(node.func, ast.Name) and node.func.id in (env.get("__sprag_classes__") or {}):
            args = []
            for arg in node.args:
                compiled = _compile_expr(arg, env, method_names=method_names)
                if _is_bound_method_reference(arg, method_names):
                    compiled = f"{compiled}.bind(this)"
                args.append(compiled)
            for keyword in node.keywords:
                args.append(_compile_expr(keyword.value, env, method_names=method_names))
            return f"new {node.func.id}({', '.join(args)})"
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ui"
        ):
            return _compile_ui_call(node, env, method_names=method_names)
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "dom"
        ):
            return _compile_dom_call(node, env, method_names=method_names)
        # self.timeout(fn, seconds) / self.interval(fn, seconds): the Python
        # signature is in seconds; Ragot's timeout/interval take ms. Convert
        # at the call site so the user never has to think about it.
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in ("timeout", "interval")
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and len(node.args) == 2
        ):
            fn_arg = node.args[0]
            sec_arg = node.args[1]
            # Wrap method-reference callbacks in an arrow so the user can
            # write self.timeout(self.tick, 0.5) and get the natural shape.
            if _is_bound_method_reference(fn_arg, method_names):
                callee_method = _compile_expr(fn_arg, env, method_names=method_names)
                fn_js = f"() => {callee_method}()"
            else:
                fn_js = _compile_expr(fn_arg, env, method_names=method_names)
            # Numeric literal: fold the ×1000 at compile time.
            if isinstance(sec_arg, ast.Constant) and isinstance(sec_arg.value, (int, float)):
                ms_js = str(int(sec_arg.value * 1000))
            else:
                ms_js = f"({_compile_expr(sec_arg, env, method_names=method_names)} * 1000)"
            js_method = node.func.attr  # timeout / interval map identity
            return f"this.{js_method}({fn_js}, {ms_js})"
        # self.subscribe(store, fn) — lifecycle-managed store subscription.
        # The Python API mirrors Specter's store.subscribe(fn, owner=self).
        # In the browser this must compile to store.subscribe(fn) +
        # this.addCleanup(unsub) so the subscription tears down with the Module.
        # Ragot's Module.subscribe(fn, options) is for Module-local state only
        # and silently no-ops when its first arg is not a function.
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "subscribe"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and len(node.args) == 2
            and isinstance(node.args[0], ast.Name)
            and (env.get("__sprag_stores__") or {}).get(node.args[0].id)
        ):
            store_refs = env["__sprag_stores__"]
            store_js_name = store_refs[node.args[0].id]
            fn_arg = node.args[1]
            fn_js = _compile_expr(fn_arg, env, method_names=method_names)
            if _is_bound_method_reference(fn_arg, method_names):
                fn_js = f"{fn_js}.bind(this)"
            return f"this.addCleanup({store_js_name}.subscribe({fn_js}))"
        # Store method translation. The Python local name is mapped to the
        # JS bridge name via env, and the SPRAG method name is translated
        # to its JS counterpart via the STORE_METHOD_JS table (nearly
        # identity by design — the stores.js shim wraps Ragot
        # ``createStateStore`` in a bridge object whose method names match
        # SPRAG's exactly so users never have to learn Ragot's names).
        #
        # For methods listed in STORE_METHODS_OPTIONS_KWARGS (currently just
        # ``subscribe``), keyword args are folded into a trailing JS options
        # object literal so ``store.subscribe(fn, selector=sel, immediate=True)``
        # compiles to ``store.subscribe(fn, { selector: sel, immediate: true })``
        # — matching the JS bridge's ``(listener, options)`` shape.
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and (env.get("__sprag_stores__") or {}).get(node.func.value.id)
            and node.func.attr in STORE_METHOD_JS
        ):
            store_refs = env["__sprag_stores__"]
            store_js_name = store_refs[node.func.value.id]
            js_method = STORE_METHOD_JS[node.func.attr]
            args = []
            for arg in node.args:
                compiled = _compile_expr(arg, env, method_names=method_names)
                if _is_bound_method_reference(arg, method_names):
                    compiled = f"{compiled}.bind(this)"
                args.append(compiled)
            if node.func.attr in STORE_METHODS_OPTIONS_KWARGS:
                if node.keywords:
                    options_chunks = []
                    for keyword in node.keywords:
                        value_js = _compile_expr(
                            keyword.value, env, method_names=method_names
                        )
                        if _is_bound_method_reference(keyword.value, method_names):
                            value_js = f"{value_js}.bind(this)"
                        options_chunks.append(f"{keyword.arg}: {value_js}")
                    args.append("{ " + ", ".join(options_chunks) + " }")
            else:
                for keyword in node.keywords:
                    args.append(
                        _compile_expr(keyword.value, env, method_names=method_names)
                    )
            return f"{store_js_name}.{js_method}({', '.join(args)})"
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            obj = _compile_expr(node.func.value, env, method_names=method_names)
            key = _compile_expr(node.args[0], env, method_names=method_names)
            default = (
                _compile_expr(node.args[1], env, method_names=method_names)
                if len(node.args) > 1
                else "undefined"
            )
            return f"(({obj}[{key}] ?? {default}))"
        callee = _compile_expr(node.func, env, method_names=method_names)
        args = []
        for arg in node.args:
            if isinstance(arg, ast.Starred):
                # `f(*xs)` -> `f(...xs)`. JS spread on iterables matches
                # Python's positional unpack semantics.
                inner = _compile_expr(arg.value, env, method_names=method_names)
                args.append(f"...{inner}")
                continue
            compiled = _compile_expr(arg, env, method_names=method_names)
            if _is_bound_method_reference(arg, method_names):
                compiled = f"{compiled}.bind(this)"
            args.append(compiled)
        for keyword in node.keywords:
            if keyword.arg is None:
                raise JSCodegenError(
                    "`**kwargs` unpack in a function call is not supported in "
                    "browser codegen — spread into a dict literal first, e.g. "
                    "`f({**kwargs, ...})` and have the callee accept a single dict."
                )
            args.append(_compile_expr(keyword.value, env, method_names=method_names))
        return f"{callee}({', '.join(args)})"
    if isinstance(node, ast.Await):
        return f"await {_compile_expr(node.value, env, method_names=method_names)}"
    if isinstance(node, ast.Lambda):
        _ensure_no_namedexpr(node, context="lambda bodies")
        params = [arg.arg for arg in node.args.args]
        body = _compile_expr(node.body, env, method_names=method_names)
        # An arrow returning an object literal must be paren-wrapped, or
        # JS parses ``{ ... }`` as a block (yielding undefined).
        if isinstance(node.body, ast.Dict):
            body = f"({body})"
        return f"({', '.join(params)}) => {body}"
    if isinstance(node, ast.NamedExpr):
        if not isinstance(node.target, ast.Name):
            raise JSCodegenError(
                "Walrus operator only supports simple name targets in browser codegen."
            )
        target = node.target.id
        env[target] = target
        value = _compile_expr(node.value, env, method_names=method_names)
        return f"({target} = {value})"
    if isinstance(node, ast.Compare):
        result = _compile_expr(node.left, env, method_names=method_names)
        for op, comparator in zip(node.ops, node.comparators):
            right = _compile_expr(comparator, env, method_names=method_names)
            if isinstance(op, ast.In):
                result = f"{right}.includes({result})"
            elif isinstance(op, ast.NotIn):
                result = f"!{right}.includes({result})"
            else:
                result = f"({result} {_compile_cmpop(op)} {right})"
        return result
    if isinstance(node, ast.UnaryOp):
        operand = _compile_expr(node.operand, env, method_names=method_names)
        if isinstance(node.op, ast.Not):
            return f"!({operand})"
        if isinstance(node.op, ast.USub):
            return f"-({operand})"
        if isinstance(node.op, ast.UAdd):
            return f"+({operand})"
        raise JSCodegenError(f"Unsupported unary operator: {ast.dump(node.op)}")
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value.replace("`", "\\`").replace("${", "\\${"))
            else:
                expr = value.value if isinstance(value, ast.FormattedValue) else value
                parts.append(f"${{{_compile_expr(expr, env, method_names=method_names)}}}")
        return "`" + "".join(parts) + "`"
    raise JSCodegenError(f"Unsupported expression: {ast.dump(node)}")


def _compile_special_js_namespace_attr(node, env):
    root_name, attrs = _attribute_chain(node)
    if root_name not in {"browser", "imports"}:
        return None

    if root_name == "imports" and attrs:
        declared = env.get("__sprag_import_aliases__")
        alias = attrs[0]
        if declared is not None and alias not in declared:
            raise JSCodegenError(
                f"Unknown SPRAG JS import alias `{alias}`; declare it via page(..., modules={{...}}) or mount(..., modules={{...}})",
                suggestion=(
                    "Declare the alias on App(..., modules=...), shell(..., modules=...), "
                    "page(..., modules=...), or mount(..., modules=...)."
                ),
            )

    base = "globalThis" if root_name == "browser" else "(globalThis.__SPRAG_IMPORTS__ || {})"
    return base + "".join(f".{attr}" for attr in attrs)


def _attribute_chain(node):
    attrs = []
    cursor = node
    while isinstance(cursor, ast.Attribute):
        attrs.append(cursor.attr)
        cursor = cursor.value
    if isinstance(cursor, ast.Name):
        return cursor.id, list(reversed(attrs))
    return None, []


_PRIMITIVE_TAGS = {"For", "Grid", "LazyImage"}

# Python builtins that compile to a JS expression shape. Each entry is a
# callable that takes the list of compiled argument strings and returns the
# JS expression. Only the common cases are covered -- anything weirder
# should be written imperatively.
_BUILTIN_CALLS = {
    "len": lambda args: f"({args[0]}).length",
    "str": lambda args: f"String({args[0]})",
    "int": lambda args: f"Math.trunc(Number({args[0]}))",
    "float": lambda args: f"Number({args[0]})",
    "bool": lambda args: f"Boolean({args[0]})",
    "abs": lambda args: f"Math.abs({args[0]})",
    "min": lambda args: f"Math.min({', '.join(args)})",
    "max": lambda args: f"Math.max({', '.join(args)})",
    "round": lambda args: f"Math.round({args[0]})",
    "print": lambda args: f"console.log({', '.join(args)})",
    "range": lambda args: _range_to_js(args),
    "sum": lambda args: _sum_to_js(args),
    "getattr": lambda args: _getattr_to_js(args),
}

_BROWSER_ENV_CASTS = {
    "str": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
}


def _compile_env_call(node, env, *, method_names):
    if not node.args:
        raise JSCodegenError("env(...) requires at least the variable name.")
    if len(node.args) > 2:
        raise JSCodegenError(
            "Browser env(...) supports at most two positional args: name and default."
        )
    supported_keywords = {"cast", "required"}
    for keyword in node.keywords:
        if keyword.arg is None or keyword.arg not in supported_keywords:
            raise JSCodegenError(
                f"Unsupported env(...) keyword in browser codegen: {keyword.arg!r}"
            )

    key_js = _compile_expr(node.args[0], env, method_names=method_names)
    fallback_js = (
        _compile_expr(node.args[1], env, method_names=method_names)
        if len(node.args) == 2
        else "__SPRAG_ENV_MISSING__"
    )
    cast_js = "null"
    required_js = "false"
    for keyword in node.keywords:
        if keyword.arg == "cast":
            cast_js = _compile_env_cast(keyword.value)
        elif keyword.arg == "required":
            required_js = _compile_expr(keyword.value, env, method_names=method_names)
    return f"__spragEnv({key_js}, {fallback_js}, {{ cast: {cast_js}, required: {required_js} }})"


def _compile_public_env_call(node):
    if node.args or node.keywords:
        raise JSCodegenError("public_env() does not take arguments in browser code.")
    return "__spragPublicEnv()"


def _compile_env_cast(node):
    if isinstance(node, ast.Name) and node.id in _BROWSER_ENV_CASTS:
        return json.dumps(_BROWSER_ENV_CASTS[node.id])
    if isinstance(node, ast.Constant) and node.value is None:
        return "null"
    raise JSCodegenError(
        "Browser env(..., cast=...) only supports str, int, float, bool, or None."
    )


def _range_to_js(args):
    # ``range(n)`` / ``range(a, b)`` / ``range(a, b, step)``. Materialises
    # a JS array so the result is iterable in the same places a list would
    # be -- slower than a generator, but matches Python semantics closely
    # enough for the small loops that survive into browser-side code.
    if len(args) == 1:
        return (
            f"Array.from({{ length: ({args[0]}) }}, (_, __i) => __i)"
        )
    if len(args) == 2:
        return (
            f"Array.from({{ length: (({args[1]}) - ({args[0]})) }}, "
            f"(_, __i) => ({args[0]}) + __i)"
        )
    if len(args) == 3:
        return (
            f"Array.from({{ length: Math.max(0, Math.ceil((({args[1]}) - ({args[0]})) / ({args[2]}))) }}, "
            f"(_, __i) => ({args[0]}) + __i * ({args[2]}))"
        )
    raise JSCodegenError("range() expects 1-3 arguments")


def _compile_builtin_call(node, env, *, method_names):
    handler = _BUILTIN_CALLS[node.func.id]
    args = [_compile_expr(arg, env, method_names=method_names) for arg in node.args]
    return handler(args)


def _compile_python_method_call(node, env, *, method_names):
    attr = node.func.attr
    if node.keywords:
        return None
    receiver_type = infer_expr_py_type(node.func.value, env)
    if receiver_type not in {"list", "str"}:
        return None
    obj = _compile_expr(node.func.value, env, method_names=method_names)
    args = [_compile_expr(arg, env, method_names=method_names) for arg in node.args]

    if receiver_type == "list" and attr == "append":
        _expect_arg_count(attr, args, 1)
        return f"{obj}.push({args[0]})"
    if receiver_type == "list" and attr == "extend":
        _expect_arg_count(attr, args, 1)
        return f"{obj}.push(...{args[0]})"
    if receiver_type == "list" and attr == "insert":
        _expect_arg_count(attr, args, 2)
        return f"{obj}.splice({args[0]}, 0, {args[1]})"
    if receiver_type == "list" and attr == "pop" and not args:
        return f"{obj}.pop()"
    if receiver_type == "list" and attr == "pop" and len(args) == 1:
        return f"{obj}.splice({args[0]}, 1)[0]"
    if receiver_type == "list" and attr == "clear" and not args:
        return f"{obj}.splice(0, {obj}.length)"
    if receiver_type == "list" and attr == "index":
        if len(args) not in {1, 2}:
            raise JSCodegenError("index() expects 1-2 arguments in browser codegen.")
        return f"{obj}.indexOf({', '.join(args)})"
    if receiver_type == "list" and attr == "count":
        _expect_arg_count(attr, args, 1)
        return f"(({obj}) || []).filter((__value) => __value === {args[0]}).length"
    if receiver_type == "list" and attr == "copy":
        _expect_arg_count(attr, args, 0)
        return f"{obj}.slice()"
    if receiver_type == "list" and attr == "remove":
        _expect_arg_count(attr, args, 1)
        return f"{obj}.splice({obj}.indexOf({args[0]}), 1)"

    if receiver_type == "str" and attr == "startswith":
        if len(args) not in {1, 2}:
            raise JSCodegenError("startswith() expects 1-2 arguments in browser codegen.")
        return f"{obj}.startsWith({', '.join(args)})"
    if receiver_type == "str" and attr == "endswith":
        if len(args) not in {1, 2}:
            raise JSCodegenError("endswith() expects 1-2 arguments in browser codegen.")
        return f"{obj}.endsWith({', '.join(args)})"
    if receiver_type == "str" and attr == "find":
        if len(args) not in {1, 2}:
            raise JSCodegenError("find() expects 1-2 arguments in browser codegen.")
        return f"{obj}.indexOf({', '.join(args)})"
    if receiver_type == "str" and attr == "rfind":
        if len(args) not in {1, 2}:
            raise JSCodegenError("rfind() expects 1-2 arguments in browser codegen.")
        return f"{obj}.lastIndexOf({', '.join(args)})"
    if receiver_type == "str" and attr == "lstrip":
        _expect_arg_count(attr, args, 0)
        return f"{obj}.trimStart()"
    if receiver_type == "str" and attr == "rstrip":
        _expect_arg_count(attr, args, 0)
        return f"{obj}.trimEnd()"
    if receiver_type == "str" and attr == "replace" and len(args) == 2:
        return f"{obj}.replaceAll({args[0]}, {args[1]})"
    if receiver_type == "str" and attr == "replace" and len(args) > 2:
        raise JSCodegenError("replace(old, new, count) is not supported in browser codegen.")
    if receiver_type == "str" and attr == "join":
        _expect_arg_count(attr, args, 1)
        return f"{args[0]}.join({obj})"

    return None


def _expect_arg_count(name, args, count):
    if len(args) != count:
        raise JSCodegenError(f"{name}() expects {count} argument(s) in browser codegen.")


def assign_inferred_py_type(env, target, value_node, annotation_node=None):
    types = env.setdefault("__sprag_py_types__", {})
    py_type = infer_annotation_py_type(annotation_node) or infer_expr_py_type(value_node, env)
    if py_type is None:
        types.pop(target, None)
        return
    types[target] = py_type


def infer_annotation_py_type(node):
    if node is None:
        return None
    if isinstance(node, ast.Name):
        if node.id in {"list", "List"}:
            return "list"
        if node.id == "str":
            return "str"
    if isinstance(node, ast.Subscript):
        return infer_annotation_py_type(node.value)
    if isinstance(node, ast.Attribute):
        if node.attr in {"list", "List"}:
            return "list"
        if node.attr == "str":
            return "str"
    return None


def infer_expr_py_type(node, env):
    if isinstance(node, ast.Name):
        return (env.get("__sprag_py_types__") or {}).get(node.id)
    if isinstance(node, (ast.List, ast.Tuple, ast.ListComp, ast.GeneratorExp)):
        return "list"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return "str"
    if isinstance(node, ast.JoinedStr):
        return "str"
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
        return infer_expr_py_type(node.value, env)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            if node.func.id in {"list", "range"}:
                return "list"
            if node.func.id == "str":
                return "str"
        if isinstance(node.func, ast.Attribute):
            receiver_type = infer_expr_py_type(node.func.value, env)
            if receiver_type == "str":
                if node.func.attr in {
                    "strip",
                    "lower",
                    "upper",
                    "lstrip",
                    "rstrip",
                    "replace",
                    "replace_all",
                    "slice",
                    "substring",
                    "toLowerCase",
                    "toUpperCase",
                    "trim",
                    "trimStart",
                    "trimEnd",
                }:
                    return "str"
                if node.func.attr == "split":
                    return "list"
            if receiver_type == "list":
                if node.func.attr in {"copy", "slice", "filter", "map"}:
                    return "list"
    return None


def _sum_to_js(args):
    # Python's sum(iterable, start=0) shape maps cleanly enough to a JS
    # reduce for the common numeric cases that survive into browser code.
    if len(args) == 1:
        start = "0"
    elif len(args) == 2:
        start = args[1]
    else:
        raise JSCodegenError("sum() expects 1-2 arguments")
    return f"(({args[0]}) || []).reduce((__sum, __value) => __sum + __value, {start})"


def _getattr_to_js(args):
    if len(args) == 2:
        default = "undefined"
    elif len(args) == 3:
        default = args[2]
    else:
        raise JSCodegenError("getattr() expects 2-3 arguments")
    return f"(({args[0]})?.[{args[1]}] ?? {default})"


def _compile_comprehension_chain(node, env, *, method_names):
    _ensure_no_namedexpr(node, context="comprehensions")
    if len(node.generators) != 1:
        raise JSCodegenError(
            f"Only one-generator {node.__class__.__name__} nodes are supported."
        )
    generator = node.generators[0]
    target_js, target_names = _compile_comp_target(generator.target)
    next_env = dict(env)
    for name in target_names:
        next_env[name] = name
    iter_expr = _compile_expr(generator.iter, env, method_names=method_names)
    chain = f"(({iter_expr}) || [])"
    if generator.ifs:
        conds = " && ".join(
            f"({_compile_expr(cond, next_env, method_names=method_names)})"
            for cond in generator.ifs
        )
        chain = f"{chain}.filter(({target_js}) => {conds})"
    return chain, target_js, next_env


def _ensure_no_namedexpr(node, *, context):
    for child in ast.walk(node):
        if isinstance(child, ast.NamedExpr):
            raise JSCodegenError(
                f"Walrus operator inside {context} is not supported in browser codegen yet."
            )


def _compile_comp_target(target):
    """Compile a comprehension/for target into (JS pattern, list of bound names).

    Supports plain ``x`` and simple tuple/list unpacking ``(x, y)`` /
    ``[x, y]`` (one level deep). Nested unpacking is rejected loudly so
    the user gets a clean error rather than broken JS.
    """
    if isinstance(target, ast.Name):
        return target.id, [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names = []
        for el in target.elts:
            if not isinstance(el, ast.Name):
                raise JSCodegenError("Nested unpacking is not supported.")
            names.append(el.id)
        return "[" + ", ".join(names) + "]", names
    raise JSCodegenError(f"Unsupported target: {ast.dump(target)}")


def _compile_mount_args_in_render_env(node, env, *, method_names=None) -> dict:
    """Eagerly compile a ui.For / ui.Grid / ui.LazyImage's argument expressions
    in the render env, so render-local variables flow into the stash.

    Returned dict has only the args relevant to the mount kind; the caller
    (``compile_component_artifact``) stitches them into a
    ``this._sprMountArgs[N] = {...}`` line that runs as part of render(),
    and ``__spragSyncMounts`` reads the values back.
    """
    tag = node.func.attr
    out: dict = {}

    if tag == "LazyImage":
        if not node.args:
            return out
        out["src"] = _compile_expr(node.args[0], env, method_names=method_names)
        for kw in node.keywords:
            if kw.arg is None:
                # **kwargs unpack — let the LazyImage compiler raise the proper
                # error when it runs over the same AST. Skip here.
                continue
            if kw.arg == "placeholder":
                out["placeholder"] = _compile_expr(kw.value, env, method_names=method_names)
            else:
                out[f"attr_{kw.arg}"] = _compile_expr(kw.value, env, method_names=method_names)
        return out

    # ui.For / ui.Grid
    if not node.args:
        return out
    out["items"] = _compile_expr(node.args[0], env, method_names=method_names)

    for kw in node.keywords:
        if kw.arg is None:
            continue  # let the main compiler raise on **kwargs
        if kw.arg == "key":
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                field = json.dumps(kw.value.value)
                out["key"] = f"(item) => item[{field}]"
            else:
                out["key"] = _compile_expr(kw.value, env, method_names=method_names)
        elif kw.arg == "render":
            out["render"] = _compile_expr(kw.value, env, method_names=method_names)
        elif kw.arg == "pool_key":
            out["pool_key"] = _compile_expr(kw.value, env, method_names=method_names)
        elif tag == "Grid" and kw.arg in ("columns", "column_width", "gap", "apply_grid_styles"):
            out[f"grid_{kw.arg}"] = _compile_expr(kw.value, env, method_names=method_names)
    return out


def _compile_ui_call(node, env, *, method_names=None):
    tag = node.func.attr

    # Phase 2 rendering primitives are special: they don't compile to a
    # createElement call directly. Instead they emit a placeholder element
    # carrying ``data-sprag-mount=N`` and register a side-effect with the
    # render context (the ``__sprag_mounts__`` collector on env). The
    # corresponding renderList / renderGrid / createLazyLoader call is
    # synthesised into the component's onStart by ``compile_component_class``.
    if tag in _PRIMITIVE_TAGS:
        ctx = env.get("__sprag_mounts__")
        if ctx is None:
            _raise(
                f"ui.{tag}(...) is only valid inside a Component.render() body.",
                node,
                source=env.get("__sprag_source", ""),
                source_file=env.get("__sprag_source_file"),
                class_name=env.get("__sprag_class_name"),
                method_name=env.get("__sprag_method_name"),
                line_offset=env.get("__sprag_line_offset", 0),
                suggestion=(
                    f"Move the ui.{tag}(...) call directly into render(). It declares a Ragot mount point "
                    "and must appear in the render tree — helper methods aren't scanned for mount-point wiring."
                ),
            )
        mount_index = len(ctx)
        # Store the AST node *and* eagerly-compiled args. Args are compiled
        # here, in the render env (which sees render-local vars), then
        # stashed on `this._sprMountArgs[N]` from render() before the return.
        # __spragSyncMounts reads them back — that's how render-locals like
        # ``items = self.state["items"]`` reach the renderList call.
        entry = {
            "tag": tag,
            "index": mount_index,
            "node": node,
            "render_env_args": _compile_mount_args_in_render_env(
                node, env, method_names=method_names
            ),
        }
        ctx.append(entry)

        if tag == "LazyImage":
            # ui.LazyImage(src, placeholder=..., **attrs) -> a real <img>
            # element so the SSR pre-paint and the runtime morphDOM diff
            # both see an <img>. The single createLazyLoader install is
            # registered once per component (regardless of LazyImage count)
            # by compile_component_class.
            return _compile_lazy_image(node, env, method_names=method_names)

        # ui.For / ui.Grid -> placeholder div with data-sprag-mount=N
        kind = "grid" if tag == "Grid" else "list"
        return (
            f'createElement("div", {{ '
            f'"data-sprag-mount": "{mount_index}", '
            f'"data-sprag-mount-kind": "{kind}" }})'
        )

    child_args = []
    for arg in node.args:
        if isinstance(arg, ast.Starred):
            # `ui.div(*children, ...)` -> `createElement("div", {...},
            # ...children)`. JS createElement accepts a variadic children
            # tail, and spread maps directly.
            inner = _compile_expr(arg.value, env, method_names=method_names)
            child_args.append(f"...{inner}")
        else:
            child_args.append(_compile_expr(arg, env, method_names=method_names))
    option_chunks = []
    for keyword in node.keywords:
        if keyword.arg is None:
            raise JSCodegenError(
                f"`**kwargs` unpack is not supported in ui.{tag}(...) — pass "
                "attributes by name (e.g. `class_=`, `data_role=`).",
            )
        key = normalize_attr_key(keyword.arg)
        option_chunks.append(
            f'"{key}": {_compile_expr(keyword.value, env, method_names=method_names)}'
        )
    options = "{ " + ", ".join(option_chunks) + " }" if option_chunks else "{}"
    return f'createElement("{tag}", {options}, {", ".join(child_args)})'


def _compile_lazy_image(node, env, *, method_names=None):
    """Compile ``ui.LazyImage(src, placeholder=..., **attrs)`` into an <img>.

    The ``src`` argument becomes ``data-src`` (the lazy loader swaps it in
    on intersect); the optional ``placeholder`` becomes the immediate ``src``
    so users see something during the lazy phase.
    """
    if not node.args:
        raise JSCodegenError("ui.LazyImage requires a src argument")
    src_expr = _compile_expr(node.args[0], env, method_names=method_names)

    placeholder_expr = None
    other_attrs = []
    for kw in node.keywords:
        if kw.arg is None:
            raise JSCodegenError(
                "`**kwargs` unpack is not supported in ui.LazyImage(...) — "
                "pass attributes by name.",
            )
        if kw.arg == "placeholder":
            placeholder_expr = _compile_expr(kw.value, env, method_names=method_names)
        else:
            value_expr = _compile_expr(kw.value, env, method_names=method_names)
            other_attrs.append(f'"{normalize_attr_key(kw.arg)}": {value_expr}')

    attr_parts = [f'"data-src": {src_expr}']
    if placeholder_expr is not None:
        attr_parts.append(f'"src": {placeholder_expr}')
    attr_parts.extend(other_attrs)
    return 'createElement("img", { ' + ", ".join(attr_parts) + " })"


def _compile_dom_call(node, env, *, method_names=None):
    """Compile ``dom.X(...)`` into a bare Ragot helper call."""
    attr = node.func.attr
    js_name = _DOM_METHOD_MAP.get(attr, attr)
    pos_args = []
    for arg in node.args:
        if isinstance(arg, ast.Starred):
            inner = _compile_expr(arg.value, env, method_names=method_names)
            pos_args.append(f"...{inner}")
        else:
            pos_args.append(_compile_expr(arg, env, method_names=method_names))
    option_chunks = []
    for keyword in node.keywords:
        if keyword.arg is None:
            raise JSCodegenError(
                f"`**kwargs` unpack is not supported in dom.{attr}(...) — pass "
                "options by name.",
            )
        option_chunks.append(
            f"{keyword.arg}: {_compile_expr(keyword.value, env, method_names=method_names)}"
        )
    if option_chunks:
        pos_args.append("{ " + ", ".join(option_chunks) + " }")
    return f"{js_name}({', '.join(pos_args)})"


def _compile_slice(node, env, *, method_names=None):
    if isinstance(node, ast.Constant):
        return json.dumps(node.value)
    return _compile_expr(node, env, method_names=method_names)


def _compile_slice_args(node, env, *, method_names=None):
    if node.step is not None:
        raise JSCodegenError("Slice steps are not supported in browser codegen.")
    args = []
    if node.lower is not None:
        args.append(_compile_expr(node.lower, env, method_names=method_names))
    elif node.upper is not None:
        args.append("0")
    if node.upper is not None:
        args.append(_compile_expr(node.upper, env, method_names=method_names))
    return ", ".join(args)


def _is_bound_method_reference(node, method_names):
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and _map_name(node.attr) in method_names
    )
