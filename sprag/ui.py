"""Small Python UI tree builder for SPRAG.

Authoring a SPRAG ``Component.render(props)`` is just calling factory
functions on ``ui``: ``ui.div(...)``, ``ui.h1(...)``, etc. Any unknown
attribute on ``ui`` becomes an ``ElementNode`` factory, which makes the
authoring surface free-form (works with custom tags too).

In addition to plain element factories, ``ui`` exposes a small set of
**rendering primitives** that compile to Ragot's keyed list / grid /
lazy-load engines on the client side and render directly into the SSR
HTML on the server side:

- ``ui.For(items, key=..., render=..., pool_key=...)`` -- keyed list,
  emits a ``<div data-sprag-mount=N>`` placeholder that the generated
  ``onStart`` reconciles via ``renderList``.
- ``ui.Grid(items, ..., columns=..., column_width=..., gap=...)`` -- same
  but the placeholder is reconciled via ``renderGrid`` (which sets the
  appropriate CSS Grid styles on the container).
- ``ui.LazyImage(src, placeholder=..., **attrs)`` -- emits an
  ``<img data-src=src>`` element; the component's generated ``onStart``
  installs a ``createLazyLoader`` that swaps in ``src`` on intersect.

These primitives are deliberately small wrappers over Ragot's primitives:
SPRAG's job is to make the *authoring* feel native to Python, not to
reinvent the rendering engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class ElementNode:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)


@dataclass
class ForNode:
    """Server-side placeholder for ``ui.For`` / ``ui.Grid``.

    Materialized at SSR time into a ``<div data-sprag-mount=N>`` element
    whose children are the SSR-rendered items (so the first paint is
    fully populated). The client-side ``renderList`` / ``renderGrid``
    call sees the existing keyed children and reconciles in place.

    ``grid_opts`` distinguishes a ``ui.Grid`` from a ``ui.For``:
    ``None`` means flat list, ``dict`` means CSS-grid layout.
    """

    items: Any
    key: Any = None  # str field name, callable, or None (= positional)
    render: Optional[Callable] = None
    pool_key: Optional[str] = None
    grid_opts: Optional[dict] = None  # None == ui.For, dict == ui.Grid


@dataclass
class LazyImageNode:
    """Server-side placeholder for ``ui.LazyImage``.

    Materialized into an ``<img>`` whose ``src`` is the (optional)
    placeholder and whose ``data-src`` is the real image URL. The
    component's generated ``onStart`` installs a single
    ``createLazyLoader`` that observes ``[data-src]`` elements and
    swaps in ``src`` on intersect.
    """

    src: str
    placeholder: Optional[str] = None
    attrs: dict = field(default_factory=dict)


def _resolve_key(key, item, index):
    """Apply a ui.For / ui.Grid ``key=`` argument against an item.

    Accepts a callable (called with the item), a string (treated as a
    field name on a mapping), or ``None`` (falls back to the positional
    index).
    """
    if key is None:
        return str(index)
    if callable(key):
        return str(key(item))
    if isinstance(key, str):
        try:
            return str(item[key])
        except (KeyError, TypeError):
            return str(index)
    return str(index)


class UIFactory:
    # ----- Rendering primitives (special-cased before __getattr__) -----

    def For(self, items, *, key=None, render=None, pool_key=None):
        """Keyed list. Compiles to Ragot's ``renderList`` on the client."""
        return ForNode(items=items, key=key, render=render, pool_key=pool_key)

    def Grid(
        self,
        items,
        *,
        key=None,
        render=None,
        pool_key=None,
        columns=None,
        column_width=None,
        gap=None,
        apply_grid_styles=True,
    ):
        """Keyed grid. Compiles to Ragot's ``renderGrid`` on the client."""
        return ForNode(
            items=items,
            key=key,
            render=render,
            pool_key=pool_key,
            grid_opts={
                "columns": columns,
                "column_width": column_width,
                "gap": gap,
                "apply_grid_styles": apply_grid_styles,
            },
        )

    def LazyImage(self, src, *, placeholder=None, **attrs):
        """Lazy-loaded image. Compiles to a ``createLazyLoader`` install."""
        return LazyImageNode(src=src, placeholder=placeholder, attrs=attrs)

    # ----- Generic element factory (any tag name) -----

    def __getattr__(self, tag):
        def create(*children, **attrs):
            normalized = []
            for child in children:
                if child is None:
                    continue
                if isinstance(child, (list, tuple)):
                    normalized.extend([item for item in child if item is not None])
                else:
                    normalized.append(child)
            return ElementNode(tag=tag, attrs=attrs, children=normalized)

        return create


ui = UIFactory()
