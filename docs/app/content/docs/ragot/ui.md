---
title: UI Primitives
description: Element factories, keyed lists, grids, and lazy images — the building blocks of Component render trees.
order: 33
---

# UI Primitives

The `ui` module provides element factories for building DOM trees in Python. These compile to efficient JavaScript DOM operations.

## Element factories

Every HTML tag is available as `ui.<tagname>()`:

```python
from sprag import ui

ui.div(
    ui.h1("Hello"),
    ui.p("World"),
    class_="container",
)
```

Children are positional arguments. Attributes are keyword arguments.

### Attribute conventions

Python keyword arguments map to HTML attributes with these conversions:

| Python | HTML |
|---|---|
| `class_` | `class` |
| `for_` | `for` |
| `data_role` | `data-role` |
| `data_user_id` | `data-user-id` |
| `aria_label` | `aria-label` |
| `aria_hidden` | `aria-hidden` |

Underscores in `data_*` and `aria_*` prefixes become hyphens. `class_` and `for_` avoid Python keyword conflicts.

### Children

Children can be strings, numbers, `ui.*` elements, lists, or `None`:

```python
ui.div(
    "Text node",
    ui.span("Nested element"),
    [ui.li("Item 1"), ui.li("Item 2")],  # Lists are flattened
    None,  # None values are ignored
)
```

## `ui.For` — keyed lists

Use `ui.For` whenever you render a list of items. It uses keyed reconciliation for efficient updates:

```python
def render(self, props=None):
    items = self.state.get("items", [])
    return ui.ul(
        ui.For(
            items,
            key=lambda item: item["id"],
            render=lambda item: ui.li(item["text"]),
        )
    )
```

| Parameter | Description |
|---|---|
| `items` | The list to iterate over |
| `key` | Function that returns a unique key for each item |
| `render` | Function that returns a `ui.*` tree for each item |

**Important:** Don't use list comprehensions for dynamic lists:

```python
# Bad — no keyed reconciliation, full re-render on every change
ui.ul([ui.li(item["text"]) for item in items])

# Good — keyed, efficient updates
ui.ul(ui.For(items, key=lambda i: i["id"], render=lambda i: ui.li(i["text"])))
```

## `ui.Grid` — grid layout

Renders items in a CSS grid with keyed reconciliation:

```python
ui.Grid(
    items,
    key=lambda item: item["id"],
    render=lambda item: ui.div(
        ui.img(src=item["thumbnail"]),
        ui.h3(item["title"]),
        class_="card",
    ),
    columns=3,           # Fixed column count
    column_width="250px", # Or auto-fit with min width
    gap="1rem",
)
```

| Parameter | Description |
|---|---|
| `items` | List of items |
| `key` | Key function |
| `render` | Render function |
| `columns` | Fixed number of columns |
| `column_width` | Min column width for auto-fit (alternative to `columns`) |
| `gap` | CSS gap between items |

Use `columns` for a fixed layout or `column_width` for responsive auto-fit. Don't set both.

## `ui.LazyImage` — lazy loading

Loads images only when they scroll into view:

```python
ui.LazyImage(
    src="/images/photo.jpg",
    placeholder="/images/placeholder.svg",
    alt="A photo",
    class_="gallery-image",
)
```

Uses `IntersectionObserver` under the hood. The placeholder is shown until the real image loads.

## `ui.HTML` — raw HTML

Injects trusted HTML directly into the DOM:

```python
ui.HTML(rendered_markdown)
```

Only use this with content you trust. It does not sanitise input.
