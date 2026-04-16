---
title: Components
description: DOM-owning browser classes — render ui trees, handle events, manage refs.
order: 31
---

# Components

A Component owns a DOM subtree. It renders a `ui.*` tree, re-renders when state changes, and manages event listeners.

## Basic shape

```python
from sprag import Component, ui

class TodoItem(Component):
    def render(self, props=None):
        props = props or self.props
        return ui.li(
            ui.input(type="checkbox", checked=props.get("done")),
            ui.span(props["text"], class_="done" if props.get("done") else None),
            ui.button("Delete", data_role="delete"),
            class_="todo-item",
        )

    def on_start(self):
        self.on(self.element, "click", self._on_click)

    def _on_click(self, event):
        if event.target.dataset.role == "delete":
            self.emit("delete", {"id": self.props["id"]})
```

## State and props

- **`self.state`** — mutable internal state. Updated via `set_state()`.
- **`self.props`** — immutable data passed from the parent. Read-only.
- **`self.refs`** — captured DOM references (see below).

## Rendering

`render(props=None)` returns a `ui.*` element tree. It's called:

1. On first mount
2. After `set_state()` (batched via requestAnimationFrame)
3. After `set_state_sync()` (immediate)

```python
def render(self, props=None):
    count = self.state.get("count", 0)
    return ui.div(
        ui.h2(f"Count: {count}"),
        ui.button("+1", data_role="increment"),
    )
```

`render()` compiles to JavaScript — it supports variable assignments, `if`/`elif`/`else` blocks, and a `return` statement. Full control flow (loops, try/except) belongs in helper methods or `on_start()`.

```python
# If statements work — useful for conditional returns
def render(self, props=None):
    if self.state.get("loading"):
        return ui.div("Loading...")
    return ui.div("Ready")

# Inline ternaries work too
def render(self, props=None):
    label = "Loading..." if self.state.get("loading") else "Ready"
    return ui.div(label)
```

Updates use morphDOM diffing — only changed DOM nodes are touched.

## State updates

```python
# Batched — coalesces multiple calls into one re-render
self.set_state({"count": self.state["count"] + 1})

# Immediate — renders synchronously
self.set_state_sync({"loading": True})
```

Use `set_state()` for normal updates. Use `set_state_sync()` only when you need the DOM to update before the next line runs (rare).

## Lifecycle

| Method | When |
|---|---|
| `on_start()` | After first mount into the DOM |
| `on_stop()` | Before removal from the DOM |

## Event listeners

```python
def on_start(self):
    # Direct listener — auto-cleaned on stop
    self.on(self.element, "click", self._on_click)

    # Delegated listener — matches child elements
    self.delegate(self.element, "click", "[data-role]", self._on_action)
```

`self.on(element, event, handler)` registers a listener that's automatically removed on `on_stop()`. No manual cleanup needed.

`self.delegate(parent, event, selector, handler)` uses event delegation — the handler fires when a click on a child matches the CSS selector. The handler receives `(event, matched_target)`.

## Refs

Capture DOM references with the `ref()` descriptor:

```python
from sprag import Component, ui, ref

class Editor(Component):
    input_el = ref(".editor-input")

    def on_start(self):
        self.input_el.focus()

    def render(self, props=None):
        return ui.div(
            ui.input(class_="editor-input", placeholder="Type here..."),
        )
```

`ref(selector)` captures the first element matching the CSS selector after each render.

## Component vs Module

**Component** owns DOM — it renders trees and handles visual updates.
**Module** owns logic — it manages server calls, sockets, timers, and state flow.

For interactive pages, the typical pattern is a Module that adopts a Component:

```python
# web.py
class MyScreen(Screen):
    modules = [MyModule]

    def render(self, data):
        module = self.module(MyModule)
        module.set_state(data)
        return hydrate(MyComponent, module=module)
```

The Module handles `dispatch()`, socket events, and state management. The Component handles rendering.
