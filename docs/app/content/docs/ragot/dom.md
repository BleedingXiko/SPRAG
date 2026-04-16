---
title: Advanced DOM & Interop
description: Hydration, static adoption, third-party interop, and the fine details of morphDOM reconciliation.
order: 35
---

# Advanced DOM & Interop

While SPRAG components handle most rendering automatically via the `render()` method, advanced applications often need to cross the boundary between managed components and manual DOM control.

## Static Adoption (Hydration)

Sometimes you have a DOM node rendered by the server (e.g., via a standard template or another service) that you want to "upgrade" to a Ragot component without destroying existing state or causing a flicker.

```python
class HydratedComponent(Component):
    def on_start(self):
        # Manually take ownership of an existing node
        self.element = browser.document.getElementById("existing-node")
        
        # Manually find refs that weren't created by render()
        self.refs.button = self.element.querySelector("button")
        
        # Now you can use normal Component features
        self.on(self.refs.button, "click", self._on_click)
```

**Note**: When adopting static nodes, avoid calling `.mount()`. Set `self.element` directly and call `.start()` manually if necessary.

## Third-Party Library Integration

Integrating "unmanaged" libraries (like D3.js, Three.js, or Google Maps) requires careful lifecycle handling. Use `refs` to get the container, and perform your own cleanup.

```python
class ChartComponent(Component):
    canvas_dir = ref(".chart-container")

    def render(self):
        return ui.div(class_="chart-container")

    def on_start(self):
        # Initialize the external library
        self.chart = imports.ChartLib.init(self.canvas_dir)

    def on_stop(self):
        # Critical: Cleanup the external library to prevent leaks
        self.chart.destroy()
```

## The MorphDOM Guarantee

Ragot uses `morphDOM` to patch the DOM. Understanding its guarantees is key to high-performance UI:

### Stateful Element Preservation
`morphDOM` preserves the internal state of elements like `<input>`, `<video>`, `<audio>`, and `<iframe>` as long as their identity (tag name + index/key) remains constant. This means if you re-render a list of inputs, focus and cursor position are preserved.

### Keyed vs. Unkeyed Siblings
**Never mix** keyed and unkeyed siblings in the same parent. 
- If one child has `data-ragot-key`, all should have it. 
- Mixing them causes the diffing algorithm to lose track of the DOM structure, leading to full element destruction instead of patching.

## Manual Reconciliation with `setStateSync`

By default, `setState` is batched via `requestAnimationFrame`. If you need a DOM update to happen **immediately** (e.g., to measure an element's size before the next frame), use `set_state_sync`:

```python
def expand_and_measure(self):
    self.set_state_sync({"expanded": True})
    
    # The DOM is now updated. Measure safely.
    height = self.element.offsetHeight
    self.set_state({"intrinsicHeight": height})
```

## Event Delegation

For performance-critical lists (thousands of items), avoid adding one listener per child. Use `delegate` in a `Module` to handle events at the parent level:

```python
class ListModule(Module):
    def on_start(self):
        # Handle clicks on any .item-btn inside self.element
        self.delegate(self.element, "click", ".item-btn", self._on_item_click)

    def _on_item_click(self, event, matched_target):
        # matched_target is the element that matched ".item-btn"
        item_id = matched_target.dataset.id
        print(f"Clicked item {item_id}")

## Custom Observation & Lazy Loads

While `ui.LazyImage` covers most cases, `create_lazy_loader` and `create_infinite_scroll` give you direct control over the `IntersectionObserver` and the performance queue.

### Advanced Lazy Loading
Control the concurrency and lifecycle of complex assets (e.g. videos or high-res textures):

```python
self.loader = create_lazy_loader({
    "selector": ".lazy-asset",
    "root_margin": "500px",
    "concurrency": 2, # Strict limit to prevent bandwidth saturation
    "on_load": lambda el: self._init_heavy_asset(el),
    "on_error": lambda el, retry: self._handle_retry(el, retry)
})
```

### Manual Infinite Scroll Control
Use `create_infinite_scroll` to implement custom windowing logic where elements are both added **and** evicted to keep DOM size constant:

```python
self.scroller = create_infinite_scroll({
    "sentinel": self.refs.footer,
    "top_sentinel": self.refs.header,
    "on_load_more": self._fetch_forward,
    "on_evict_chunk": lambda i: self._evict_dom_nodes(i), # Manual DOM cleanup
    "visible_chunks": 3 
})
```

This allows you to build custom virtual lists without relying on a pre-built component, giving you full control over transition animations and DOM structure during shifts.
```
