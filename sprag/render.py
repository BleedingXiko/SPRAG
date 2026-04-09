"""HTML preview rendering for SPRAG authoring trees."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field

from .attrs import normalize_attr_key
from .ui import HTMLNode, ElementNode, ForNode, LazyImageNode, _resolve_key
from .web import Component, HydrateMount, SSRMount

VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass
class RenderResult:
    html: str
    hydration: list[dict] = field(default_factory=list)


def render_tree(node) -> RenderResult:
    hydration = []
    # ``mount_seq`` numbers ui.For/Grid/LazyImage placeholders so the
    # generated client-side onStart can find them via [data-sprag-mount=N].
    # See sprag/codegen/components.py for the matching emit.
    html_output = _render_node(node, hydration, {"next_id": 1, "mount_seq": 0})
    return RenderResult(html=html_output, hydration=hydration)


def _render_node(node, hydration, context):
    if node is None:
        return ""
    if isinstance(node, HydrateMount):
        hydrate_id = f"sprag-h{context['next_id']}"
        context["next_id"] += 1
        component_html = _render_component(node.component, node.props, hydration, context)
        entry = {
            "id": hydrate_id,
            "component": _component_name(node.component),
            "module": _component_name(node.module) if node.module else None,
            "component_class": node.component if isinstance(node.component, type) else None,
            "module_class": node.module.__class__ if node.module else None,
            "props": _jsonable_props(node.props),
            "state": _initial_component_state(node.component, node.props),
            "module_state": _jsonable_state(getattr(node.module, "state", {})),
        }
        hydration.append(entry)
        attrs = {
            "data-sprag-hydrate": entry["component"],
            "data-sprag-hydrate-id": hydrate_id,
            "data-sprag-module": entry["module"] or "",
            "data-sprag-props": json.dumps(entry["props"], sort_keys=True),
        }
        return _wrap_html("div", attrs, component_html)
    if isinstance(node, SSRMount):
        return _render_component(node.component, node.props, hydration, context)
    if isinstance(node, ForNode):
        # Allocate a stable mount index in document order so the generated
        # JS can pair this placeholder with its renderList/renderGrid call.
        mount_index = context["mount_seq"]
        context["mount_seq"] += 1

        # SSR-render each item now so the first paint is fully populated.
        # ``data-_list-key`` is the same dataset key Ragot's renderList writes
        # when it owns the children, so the client-side reconciliation pass
        # treats these as already-keyed and skips re-creating unchanged nodes.
        items = list(node.items or [])
        child_html_parts = []
        for index, item in enumerate(items):
            key = _resolve_key(node.key, item, index)
            child_node = node.render(item) if node.render else item
            if child_node is None:
                continue
            child_html = _render_node(child_node, hydration, context)
            # Inject the list key into the outermost tag so reconciliation works.
            child_html_parts.append(_inject_list_key(child_html, key))

        placeholder_attrs = {"data-sprag-mount": str(mount_index)}
        if node.grid_opts is not None:
            placeholder_attrs["data-sprag-mount-kind"] = "grid"
            grid_styles = _grid_inline_styles(node.grid_opts)
            if grid_styles:
                placeholder_attrs["style"] = grid_styles
        else:
            placeholder_attrs["data-sprag-mount-kind"] = "list"
        if node.pool_key:
            placeholder_attrs["data-sprag-pool"] = node.pool_key

        return _wrap_html("div", placeholder_attrs, "".join(child_html_parts))
    if isinstance(node, LazyImageNode):
        attrs = dict(node.attrs)
        attrs["data-src"] = node.src
        if node.placeholder:
            attrs["src"] = node.placeholder
        attrs_html = _attrs_to_html(attrs)
        return f"<img{attrs_html}>"
    if isinstance(node, HTMLNode):
        return node.html
    if isinstance(node, ElementNode):
        attrs = _attrs_to_html(node.attrs)
        rendered_children = "".join(_render_node(child, hydration, context) for child in node.children)
        if node.tag in VOID_TAGS:
            return f"<{node.tag}{attrs}>"
        return f"<{node.tag}{attrs}>{rendered_children}</{node.tag}>"
    if isinstance(node, str):
        return html.escape(node)
    if isinstance(node, (int, float, bool)):
        return html.escape(str(node))
    return html.escape(repr(node))


def _render_component(component, props, hydration, context):
    if isinstance(component, Component):
        instance = component
    elif isinstance(component, type) and issubclass(component, Component):
        instance = component(props=props, state=_initial_component_state(component, props))
    elif callable(component):
        return _render_node(component(props), hydration, context)
    else:
        raise TypeError(f"Unsupported SPRAG component type: {component!r}")
    return _render_node(instance.render(props), hydration, context)


def _wrap_html(tag, attrs, inner_html):
    return f"<{tag}{_attrs_to_html(attrs)}>{inner_html}</{tag}>"


def _attrs_to_html(attrs):
    chunks = []
    for key, value in attrs.items():
        if value is None:
            continue
        key = normalize_attr_key(key)
        if value is True:
            chunks.append(f" {key}")
            continue
        if callable(value):
            continue
        chunks.append(f' {key}="{html.escape(str(value), quote=True)}"')
    return "".join(chunks)


def _inject_list_key(rendered_html: str, key: str) -> str:
    """Splice ``data-_list-key="<key>"`` into the first tag of an SSR'd child.

    Ragot's renderList uses ``el.dataset._listKey`` (which DOM-level becomes
    ``data-_list-key``) to recognise existing children. By stamping the key
    server-side, the client-side reconciliation skips re-rendering items that
    are unchanged across the SSR -> hydration boundary.
    """
    if not rendered_html or not rendered_html.startswith("<"):
        return rendered_html
    end = rendered_html.find(">")
    if end == -1:
        return rendered_html
    head = rendered_html[:end]
    tail = rendered_html[end:]
    # Self-closing void tag handling: keep the trailing slash if present.
    if head.endswith("/"):
        head = head[:-1].rstrip()
        return f'{head} data-_list-key="{html.escape(key, quote=True)}"/>{tail[1:]}'
    return f'{head} data-_list-key="{html.escape(key, quote=True)}"{tail}'


def _grid_inline_styles(grid_opts: dict) -> str:
    """Translate ui.Grid options into the inline styles that match what
    Ragot's ``renderGrid`` would set on the container.

    SSR mirrors the client-side CSS so the layout is correct on first
    paint, before the client has a chance to reconcile.
    """
    if not grid_opts.get("apply_grid_styles", True):
        return ""
    parts = ["display: grid"]
    column_width = grid_opts.get("column_width")
    columns = grid_opts.get("columns")
    if column_width:
        parts.append(f"grid-template-columns: repeat(auto-fill, minmax({column_width}, 1fr))")
    elif columns:
        parts.append(f"grid-template-columns: repeat({columns}, 1fr)")
    gap = grid_opts.get("gap")
    if gap is not None:
        parts.append(f"gap: {gap}")
    return "; ".join(parts)


def _component_name(value):
    if value is None:
        return None
    if isinstance(value, type):
        return value.__name__
    return value.__class__.__name__


def _jsonable_props(props):
    safe = {}
    for key, value in (props or {}).items():
        if callable(value):
            continue
        safe[key] = value
    return safe


def _jsonable_state(state):
    return dict(state or {})


def _initial_component_state(component, props):
    if isinstance(component, type) and issubclass(component, Component):
        return dict(props or {})
    if isinstance(component, Component):
        return dict(component.state or props or {})
    return dict(props or {})
