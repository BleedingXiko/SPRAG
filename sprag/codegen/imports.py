"""Detect which optional Ragot symbols a compiled JS file references.

The detector is regex-based with context-aware lookbehinds so that method
calls of the same name (e.g. ``this.element.append(x)``) do not trigger a
false import of Ragot's ``append`` helper.
"""

from __future__ import annotations

import re


# Ragot symbols that appear as function calls in compiled JS. Detected via
# a lookbehind-guarded ``name(`` pattern so method calls like
# ``this.element.append(x)`` do NOT accidentally import Ragot's ``append``.
_RAGOT_BARE_FUNCTIONS = {
    # Rendering
    "renderList",
    "renderGrid",
    "morphDOM",
    "clearPool",
    # Scrolling / loading
    "createInfiniteScroll",
    "createLazyLoader",
    # State
    "createStateStore",
    "createSelector",
    # Events
    "delegateEvent",
    # Bootstrap
    "createApp",
    # DOM helpers
    "css",
    "attr",
    "show",
    "hide",
    "toggle",
    "clear",
    "createIcon",
    "animateIn",
    "animateOut",
    # DOM ops -- only flagged when called as bare functions
    "batchAppend",
    "append",
    "prepend",
    "insertBefore",
    "remove",
}

# Ragot symbols that appear as bare identifiers (classes, singletons)
# rather than function calls. Detected via word-boundary regex.
_RAGOT_IDENTIFIERS = {
    "bus",
    "ragotRegistry",
    "VirtualScroller",
}


def _detect_ragot_imports(compiled_js: str) -> set[str]:
    """Scan compiled JS for references to optional Ragot imports.

    Uses context-aware regex patterns so method calls of the same name
    (e.g. ``element.append(x)``) do not trigger a false import of Ragot's
    ``append`` helper.
    """
    found: set[str] = set()

    for name in _RAGOT_BARE_FUNCTIONS:
        # Bare call: name( not preceded by a dot, word char, or $
        pattern = rf"(?<![\w$.]){re.escape(name)}\s*\("
        if re.search(pattern, compiled_js):
            found.add(name)

    for name in _RAGOT_IDENTIFIERS:
        # Identifier: word-boundary with no $ on either side
        pattern = rf"(?<![\w$]){re.escape(name)}(?![\w$])"
        if re.search(pattern, compiled_js):
            found.add(name)

    # Special-case $ and $$ since they are not word characters.
    # $$ must be checked first because `$$(` also contains `$(`.
    if re.search(r"(?<![\w$])\$\$\s*\(", compiled_js):
        found.add("$$")
    # $ but not $$ — the (?<![\w$]) lookbehind already guarantees no preceding $.
    if re.search(r"(?<![\w$])\$\s*\(", compiled_js):
        found.add("$")

    return found
