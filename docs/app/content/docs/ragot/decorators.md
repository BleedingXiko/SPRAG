---
title: Decorators
description: Structural transforms — debounce, throttle, animate, virtual scroll, and infinite scroll.
order: 34
---

# Decorators

SPRAG provides browser-side decorators that compile to Ragot structural patterns. They transform how your methods or components behave at the JavaScript level.

## `@debounce(seconds)`

Trailing-edge coalesced call. The decorated method waits until `seconds` have passed since the last invocation before executing. Each new call resets the timer.

```python
from sprag import Module, debounce

class SearchModule(Module):
    def on_start(self):
        self.delegate(self.element, "input", "[name='query']", self.on_input)

    @debounce(0.3)
    def on_input(self, event, target):
        self.call_action("search", {"query": target.value})
```

Good for: search-as-you-type, autosave, resize handlers.

Cleanup is automatic — the pending timer is cleared when the Module stops.

## `@throttle(seconds)`

Leading-edge rate gate. The decorated method executes immediately on the first call, then ignores subsequent calls until `seconds` have passed.

```python
from sprag import Module, throttle

class ScrollModule(Module):
    def on_start(self):
        self.on(browser.window, "scroll", self.on_scroll)

    @throttle(0.1)
    def on_scroll(self, event):
        scroll_y = browser.scrollY
        self.set_state({"scroll_position": scroll_y})
```

Good for: scroll handlers, mousemove tracking, window resize.

## `@animate(class_name)`

Wraps component mount/unmount with CSS transition classes. On mount, adds the class and triggers `animateIn`. On unmount, triggers `animateOut` and waits for the transition to complete before removing the element.

```python
from sprag import Component, ui, animate

@animate("fade")
class Toast(Component):
    def render(self, props=None):
        return ui.div(
            ui.p(self.state.get("message", "")),
            class_="toast",
        )
```

Pair with CSS:

```css
.fade-enter { opacity: 0; }
.fade-enter-active { opacity: 1; transition: opacity 0.3s; }
.fade-exit-active { opacity: 0; transition: opacity 0.3s; }
```

## `@virtual_scroll(chunk=N, ...)`

Wraps a Component in a Ragot `VirtualScroller`. The decorated component must provide chunk-based rendering methods instead of rendering the full list directly.

```python
from sprag import Component, ui, virtual_scroll

@virtual_scroll(chunk=50)
class LargeList(Component):
    def render(self, props=None):
        return ui.div(class_="large-list")

    def total(self):
        return len(self.state.get("items", []))

    def chunk(self, i):
        items = self.state.get("items", [])
        start = i * 50
        stop = min(start + 50, len(items))
        return ui.div(
            ui.For(
                items[start:stop],
                key=lambda item: item["id"],
                render=lambda item: ui.div(item["text"], class_="list-item"),
            )
        )
```

Good for: lists with hundreds or thousands of items.

### Parameters

| Parameter | Default | Description |
|---|---|---|
| `chunk` | *required* | Number of items per chunk |
| `max_chunks` | `5` | Maximum number of rendered chunks at a time |
| `initial_chunks` | `1` | Number of chunks to render on first mount |
| `root` | `None` | CSS selector for the scroll root (defaults to viewport) |
| `root_margin` | `"1200px 0px"` | IntersectionObserver rootMargin — controls the preload buffer |
| `container_class` | `None` | CSS class applied to the scroller container element |
| `pool_size` | `0` | Enable chunk pooling with this many reusable chunk containers. When > 0, the component must define a `recycle(self, chunk_el, chunk_index)` method. |
| `child_pool_size` | `0` | Enable child-level pooling within chunks |
| `axis` | `"auto"` | Scroll axis: `"auto"`, `"vertical"`, or `"horizontal"` |

The decorated component must define:

- `chunk(self, i)` — returns the DOM element for chunk `i`
- `total(self)` — returns the total item count

`chunk` may also accept Ragot's load context as a second argument:
`chunk(self, i, load_ctx)`. This is useful for async chunks; call
`load_ctx.is_current()` before committing delayed work.

Optional methods map directly to Ragot `VirtualScroller` callbacks:

| SPRAG method | Ragot option | Use |
|---|---|---|
| `measure(self, el, i)` | `measureChunk(el, i)` | Return the rendered chunk size in pixels |
| `placeholder(self, i, px)` | `buildPlaceholder(i, px)` | Return the placeholder element for an evicted chunk |
| `recycle(self, el, i)` | `onRecycle(el, i)` | Fully patch a pooled chunk element for its new index |
| `evicted(self, i)` | `onChunkEvicted(i)` | React after a chunk leaves the active window |

Ragot's default measurement uses the chunk element's height. For horizontal virtual scrolling, define `measure(self, el, i)` and return the chunk width, for example `el.offset_width`.

The scroller handle is available at `self.virtual_scroll` in Python (emitted as `this.virtualScroll` in JS).

## `@infinite_scroll(at="selector", ...)`

`@infinite_scroll` is a method decorator. It wires a `createInfiniteScroll` observer and uses the decorated method as the load-more callback.

```python
from sprag import Component, ui, infinite_scroll

class Feed(Component):
    def render(self, props=None):
        items = self.state.get("items", [])
        return ui.div(
            ui.For(
                items,
                key=lambda i: i["id"],
                render=lambda i: ui.div(i["text"], class_="feed-item"),
            ),
            ui.div(class_="sentinel"),  # Trigger element
        )

    @infinite_scroll(at=".sentinel")
    def load_more(self):
        self.emit("load_more", {"offset": len(self.state.get("items", []))})
```

### Parameters

| Parameter | Default | Description |
|---|---|---|
| `at` | *required* | CSS selector for the bottom sentinel element, or a `ref()` name |
| `root` | `None` | CSS selector for the scroll root (defaults to viewport) |
| `root_margin` | `"600px"` | IntersectionObserver rootMargin |
| `top_at` | `None` | CSS selector for a top sentinel — enables bidirectional scrolling |
| `visible_chunks` | `None` | Explicit visible-chunks set for DOM eviction control |

## When to use decorators

Decorators are **structural transforms** — they change the fundamental behavior of a method or component. Use them when the timing or scrolling behavior is an inherent property of the method, not something you want to control per-call.

If you need per-call control over debouncing or throttling, implement the timing logic manually in your method instead.
