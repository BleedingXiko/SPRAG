---
title: Advanced DOM & Interop
description: DOM helpers, third-party interop, and lifecycle-safe manual DOM operations.
order: 35
---

# Advanced DOM & Interop

SPRAG components should own most rendering through `render()`. This page covers the supported `dom.*` helper surface when you need direct DOM work.

## Query and visibility

Use the query helpers and visibility helpers for lightweight DOM control:

```python
from sprag import Module, dom

class PanelModule(Module):
    def on_start(self):
        btn = dom.query("[data-role='toggle']", self.element)
        panel = dom.query(".panel", self.element)
        self.on(btn, "click", lambda event: dom.toggle(panel))
```

Available helpers:

- `dom.query(selector, parent=None)`
- `dom.query_all(selector, parent=None)`
- `dom.show(element)`
- `dom.hide(element)`
- `dom.toggle(element)`

## Styling and attributes

```python
dom.css(panel, {"display": "grid", "gap": "8px"})
dom.attr(panel, {"data-state": "open", "aria-hidden": "false"})
```

Available helpers:

- `dom.css(element, styles)`
- `dom.attr(element, attrs)`
- `dom.clear(element)`

## DOM mutation

```python
item = dom.query(".item", self.element)
list_el = dom.query(".list", self.element)

dom.append(list_el, item)
dom.prepend(list_el, item)
dom.insert_before(list_el, item, dom.query(".sentinel", list_el))
dom.remove(item)
dom.batch_append(list_el, [item])
dom.clear_pool("messages")
```

Available helpers:

- `dom.append(parent, child)`
- `dom.prepend(parent, child)`
- `dom.insert_before(parent, child, reference)`
- `dom.remove(element)`
- `dom.batch_append(parent, children)`
- `dom.clear_pool(key=None)`

## Animations and icons

```python
icon = dom.create_icon("<svg>...</svg>", class_name="icon")
dom.animate_in(icon, duration=200)
dom.animate_out(icon, duration=150)
```

Available helpers:

- `dom.create_icon(svg_string, class_name="icon")`
- `dom.animate_in(element, **options)`
- `dom.animate_out(element, **options)`

## Third-party interop

Integrate third-party libraries in lifecycle hooks and always clean up in `on_stop()`:

```python
from sprag import Module, imports

class ChartModule(Module):
    def on_start(self):
        self.chart = imports.ChartLib.create(self.element)

    def on_stop(self):
        if self.chart:
            self.chart.destroy()
```

## Lower-level Ragot helpers

If you need to drop below the `dom.*` helper surface, the shipped Ragot runtime also includes lower-level primitives such as `createLazyLoader(...)` and `createInfiniteScroll(...)`. SPRAG's normal authoring path usually reaches those through `ui.LazyImage`, `@infinite_scroll`, and virtual-scroll integration, but they are part of the underlying runtime.

## Vanilla JavaScript with Ragot

For complex browser-only behavior, put a normal JavaScript module in
`app/static/` and import Ragot from the vendored runtime:

```javascript
// app/static/js/gallery.mjs
import { Component, createElement, VirtualScroller } from "/vendor/ragot.esm.min.js";

class GalleryRail extends Component {
    render() {
        return createElement("div", { className: "gallery-rail" });
    }
}

export function mountGallery(root) {
    const rail = new GalleryRail();
    rail.mount(root);
    return rail;
}
```

Attach it to a page or mount as a module script:

```python
from sprag import page, script

gallery = page(
    path="/gallery",
    controller=GalleryController,
    screen=GalleryScreen,
    mode="hybrid",
    js=[script("app/static/js/gallery.mjs", module=True)],
)
```

During builds, SPRAG copies `app/static/js/gallery.mjs` to
`/static/js/gallery.mjs` and rewrites imports that point at
`ragot.esm.min.js` to the emitted runtime location. That means the source can
use `/vendor/ragot.esm.min.js`, while static and packaged builds still get the
correct relative import path.
