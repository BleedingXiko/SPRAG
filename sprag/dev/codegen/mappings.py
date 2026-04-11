"""Name and operator mappings between Python and JavaScript.

These are pure lookup tables and tiny helpers with no dependencies on the
rest of the codegen package, so they sit at the bottom of the dependency
graph and can be imported freely.
"""

from __future__ import annotations

import ast


class JSCodegenError(RuntimeError):
    """Raised when the codegen encounters an unsupported Python construct."""

    def __init__(
        self,
        message,
        *,
        source_file=None,
        class_name=None,
        method_name=None,
        line=None,
        source_line=None,
        suggestion=None,
    ):
        super().__init__(message)
        self.message = message
        self.source_file = source_file
        self.class_name = class_name
        self.method_name = method_name
        self.line = line
        self.source_line = source_line
        self.suggestion = suggestion

    def with_context(
        self,
        *,
        source_file=None,
        class_name=None,
        method_name=None,
        line=None,
        source_line=None,
        suggestion=None,
    ):
        return JSCodegenError(
            self.message,
            source_file=self.source_file if source_file is None else source_file,
            class_name=self.class_name if class_name is None else class_name,
            method_name=self.method_name if method_name is None else method_name,
            line=self.line if line is None else line,
            source_line=self.source_line if source_line is None else source_line,
            suggestion=self.suggestion if suggestion is None else suggestion,
        )

    def __str__(self):
        parts = [self.message]
        location_bits = []
        if self.source_file:
            location_bits.append(str(self.source_file))
        if self.line is not None:
            location_bits.append(f"line {self.line}")
        owner = ".".join(bit for bit in (self.class_name, self.method_name) if bit)
        if owner:
            location_bits.append(owner)
        if location_bits:
            parts.append("Location: " + ", ".join(location_bits))
        if self.source_line:
            parts.append("Source: " + self.source_line.strip())
        if self.suggestion:
            parts.append("Hint: " + self.suggestion)
        return "\n".join(parts)


# Maps Pythonic snake_case ``dom.X()`` calls to the underlying Ragot helper
# name. Functions not in this map use their attribute name verbatim.
_DOM_METHOD_MAP = {
    "query": "$",
    "query_all": "$$",
    "animate_in": "animateIn",
    "animate_out": "animateOut",
    "batch_append": "batchAppend",
    "insert_before": "insertBefore",
    "create_icon": "createIcon",
    "clear_pool": "clearPool",
}


def _map_name(name):
    """Translate a snake_case Python identifier to camelCase JS.

    SPRAG is a 1:1 mirror of Specter (server) and Ragot (browser): the same
    imperative API surface (``self.listen``, ``self.on``, ``self.timeout``,
    ``self.set_state``, ``self.subscribe``, ...) is written in Python and
    routed to the right runtime by the codegen. The mapping table below
    covers framework names that need a fixed translation; any other
    snake_case identifier (e.g. user-authored methods like
    ``on_counter_change``) falls through to a generic snake-to-camel
    conversion so JS users see idiomatic camelCase on every surface.
    Identifiers that are already camelCase, ALL_CAPS, or dunder pass
    through unchanged.
    """
    mapping = {
        # Lifecycle
        "on_start": "onStart",
        "on_stop": "onStop",
        # State
        "set_state": "setState",
        "set_state_sync": "setStateSync",
        "batch_state": "batchState",
        "watch_state": "watchState",
        # Actions (SPRAG-specific bridge)
        "call_action": "callAction",
        # DOM events
        "prevent_default": "preventDefault",
        "stop_propagation": "stopPropagation",
        # Ragot Module: managed timers
        "timeout": "timeout",
        "interval": "interval",
        "clear_timeout": "clearTimeout",
        "clear_interval": "clearInterval",
        # Ragot Module: ownership
        "adopt": "adopt",
        "adopt_component": "adoptComponent",
        "add_cleanup": "addCleanup",
        # Ragot Component: lifecycle override
        "unmount": "unmount",
        "mount_before": "mountBefore",
        # Ragot Module: sockets
        "on_socket": "onSocket",
        "off_socket": "offSocket",
        "emit_socket": "emitSocket",
        # Ragot: state utilities
        "create_selector": "createSelector",
        "create_state_store": "createStateStore",
        # DOM
        "query_selector": "querySelector",
        "query_selector_all": "querySelectorAll",
        "add_event_listener": "addEventListener",
        "remove_event_listener": "removeEventListener",
        "class_list": "classList",
        "inner_html": "innerHTML",
        "text_content": "textContent",
        "offset_height": "offsetHeight",
        "offset_width": "offsetWidth",
        # Common Python string methods
        "upper": "toUpperCase",
        "lower": "toLowerCase",
        "strip": "trim",
        # Ragot helpers (also present in _DOM_METHOD_MAP for dom.X() calls;
        # duplicated here so bare attribute access also name-maps correctly)
        "query_all": "queryAll",
        "animate_in": "animateIn",
        "animate_out": "animateOut",
        "batch_append": "batchAppend",
        "insert_before": "insertBefore",
        "create_icon": "createIcon",
        "clear_pool": "clearPool",
    }
    if name in mapping:
        return mapping[name]
    # Generic snake_case -> camelCase for everything else. Skip dunder
    # (``__init__``), leading-underscore privates (``_helper``), and names
    # without an internal underscore (already camel/lower/ALL_CAPS).
    if name.startswith("_") or "_" not in name:
        return name
    head, *tail = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _compile_binop(node):
    mapping = {
        ast.Add: "+",
        ast.Sub: "-",
        ast.Mult: "*",
        ast.Div: "/",
        ast.Mod: "%",
    }
    operator = mapping.get(type(node))
    if operator is None:
        raise JSCodegenError(f"Unsupported binary operator: {ast.dump(node)}")
    return operator


def _compile_cmpop(op):
    mapping = {
        ast.Eq: "===",
        ast.NotEq: "!==",
        ast.Lt: "<",
        ast.LtE: "<=",
        ast.Gt: ">",
        ast.GtE: ">=",
        ast.Is: "===",
        ast.IsNot: "!==",
        ast.In: None,  # handled specially by callers
        ast.NotIn: None,
    }
    result = mapping.get(type(op))
    if result is None:
        if isinstance(op, ast.In):
            return "includes"
        if isinstance(op, ast.NotIn):
            return "!includes"
        raise JSCodegenError(f"Unsupported comparison operator: {ast.dump(op)}")
    return result
