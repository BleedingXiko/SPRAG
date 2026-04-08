"""DOM helper namespace for SPRAG client-side code.

All functions in this module are no-op stubs at runtime. They exist so Python
tooling (type checkers, IDE autocomplete, linters) can reason about client-side
code that compiles to the Ragot browser runtime.

When the SPRAG codegen encounters `dom.X(...)` inside a ``Module`` or
``Component`` method, it rewrites the call into a bare Ragot helper call in
the emitted JavaScript (e.g. ``dom.show(el)`` -> ``show(el)``,
``dom.query(".btn")`` -> ``$(".btn")``). These Python functions are never
executed — they exist only so the surrounding Python parses and type-checks.

Usage::

    from sprag import Module, dom

    class SearchModule(Module):
        def on_start(self):
            dom.show(self.refs.results)
            dom.css(self.refs.input, {"outline": "2px solid blue"})
            button = dom.query(".search-btn")
            dom.animate_in(button, duration=200)

Naming convention: snake_case on the Python side, camelCase (or symbol) in
the emitted JS. The mapping lives in ``sprag/codegen/mappings.py::_DOM_METHOD_MAP``.
"""

from __future__ import annotations


def _noop(*args, **kwargs):  # pragma: no cover - stubs never execute at runtime
    return None


# ---------------------------------------------------------------------------
# DOM queries
# ---------------------------------------------------------------------------

def query(selector, parent=None):
    """Compiles to Ragot ``$(selector, parent)`` — ``querySelector`` helper."""
    return _noop(selector, parent)


def query_all(selector, parent=None):
    """Compiles to Ragot ``$$(selector, parent)`` — ``querySelectorAll`` helper."""
    return _noop(selector, parent)


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------

def show(element):
    """Compiles to Ragot ``show(element)``."""
    return _noop(element)


def hide(element):
    """Compiles to Ragot ``hide(element)``."""
    return _noop(element)


def toggle(element):
    """Compiles to Ragot ``toggle(element)``."""
    return _noop(element)


# ---------------------------------------------------------------------------
# Styling / attributes
# ---------------------------------------------------------------------------

def css(element, styles):
    """Compiles to Ragot ``css(element, styles)``.

    ``styles`` is a dict of CSS property -> value.
    """
    return _noop(element, styles)


def attr(element, attrs):
    """Compiles to Ragot ``attr(element, attrs)``.

    ``attrs`` is a dict of attribute name -> value. Handles event handler
    keys on the Ragot side.
    """
    return _noop(element, attrs)


# ---------------------------------------------------------------------------
# Clearing
# ---------------------------------------------------------------------------

def clear(element):
    """Compiles to Ragot ``clear(element)`` — empties the element."""
    return _noop(element)


# ---------------------------------------------------------------------------
# Icons
# ---------------------------------------------------------------------------

def create_icon(svg_string, class_name="icon"):
    """Compiles to Ragot ``createIcon(svgString, className)``.

    Wraps *trusted* SVG markup in a ``<span>`` element. Ragot does not ship
    an icon registry — the caller is expected to provide the raw SVG string,
    typically from an app-level icon module. Never pass user-supplied markup.
    """
    return _noop(svg_string, class_name)


# ---------------------------------------------------------------------------
# Animations
# ---------------------------------------------------------------------------

def animate_in(element, **options):
    """Compiles to Ragot ``animateIn(element, options)``."""
    return _noop(element, options)


def animate_out(element, **options):
    """Compiles to Ragot ``animateOut(element, options)``."""
    return _noop(element, options)


# ---------------------------------------------------------------------------
# DOM mutation
# ---------------------------------------------------------------------------

def append(parent, child):
    """Compiles to Ragot ``append(parent, child)``."""
    return _noop(parent, child)


def prepend(parent, child):
    """Compiles to Ragot ``prepend(parent, child)``."""
    return _noop(parent, child)


def insert_before(parent, child, reference):
    """Compiles to Ragot ``insertBefore(parent, child, reference)``."""
    return _noop(parent, child, reference)


def remove(element):
    """Compiles to Ragot ``remove(element)``."""
    return _noop(element)


def batch_append(parent, children):
    """Compiles to Ragot ``batchAppend(parent, children)``."""
    return _noop(parent, children)


def clear_pool(key=None):
    """Compiles to Ragot ``clearPool(key)`` — drops pooled list elements."""
    return _noop(key)
