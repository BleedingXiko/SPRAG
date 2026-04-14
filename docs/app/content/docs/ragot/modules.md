---
title: Modules
description: Non-visual browser lifecycle — server calls, sockets, timers, and state management.
order: 32
---

# Modules

A Module owns non-visual browser lifecycle: server communication, socket events, timers, store subscriptions, and child Components. It's the brain of an interactive page.

## Basic shape

```python
from sprag import Module

class TodoModule(Module):
    def on_start(self):
        self.delegate(self.element, "click", "[data-role='add']", self.on_add)
        self.delegate(self.element, "submit", "form", self.on_submit)

    def on_submit(self, event, target):
        event.prevent_default()
        text = self.element.querySelector("[name='text']").value
        self.dispatch("add_item", {"text": text})

    def on_add(self, event, target):
        event.prevent_default()
        self.set_state({"adding": True})
```

## Lifecycle

| Method | When |
|---|---|
| `on_start()` | After hydration, Module is attached to the DOM |
| `on_stop()` | Before the page tears down |

## State

```python
# Read current state
count = self.state.get("count", 0)

# Update state (triggers Component re-render)
self.set_state({"count": count + 1})

# Watch for state changes
self.watch_state(lambda state: print("state changed:", state))
```

## Server calls

### `dispatch(action, payload)`

Calls a server `@action` and applies the returned state:

```python
def on_increment(self, event, target):
    self.dispatch("increment", {"count": self.state["count"]})
```

The response from the server replaces the Module's state, which triggers a re-render.

### `call_action(action, payload)`

Same as `dispatch()` but returns a Promise — use when you need to handle the response:

```python
def on_save(self, event, target):
    result = self.call_action("save", {"text": self.state["text"]})
    # result is the server response
```

## DOM access

- **`self.element`** — the DOM node this Module is attached to (passed from `hydrate()`)

## Child Components

Adopt Components to tie their lifecycle to yours:

```python
def on_start(self):
    sidebar = SidebarComponent(self.element.querySelector(".sidebar"))
    self.adopt_component(sidebar)
```

## Sockets

```python
def on_start(self):
    # Listen for socket events
    self.on_socket("items_changed", self._on_items)

    # Emit a socket event
    self.emit_socket("join", {"room": "lobby"})

    # Join/leave a topic (room)
    self.join_topic("room:lobby")

def on_stop(self):
    self.leave_topic("room:lobby")

def _on_items(self, data):
    self.call_action("get_items", {})
```

### Refetch shorthand

```python
def on_start(self):
    # Automatically call "get_items" when "items_changed" arrives
    self.refetch_on_socket("items_changed", action="get_items")
```

## Uploads

```python
# Form-based upload
def on_submit(self, event, target):
    event.prevent_default()
    self.upload_form("avatar", event, self.on_progress)

# Programmatic upload
def on_drop(self, event, target):
    event.prevent_default()
    file = event.dataTransfer.files[0]
    self.upload("process", file, {"resize": True}, self.on_progress)

def on_progress(self, progress):
    self.set_state({"upload_percent": progress.percent})
```

## Navigation

```python
def on_click(self, event, target):
    event.prevent_default()
    self.navigate("/other-page")
```

## Timers

```python
def on_start(self):
    self.interval(self._poll, 30)   # Every 30 seconds
    self.timeout(self._delayed, 5)  # After 5 seconds
```

Auto-cleaned on `on_stop()`.

## Store subscriptions

```python
from sprag import store

counter_store = store("counter", initial={"count": 0})

class MyModule(Module):
    def on_start(self):
        self.subscribe(counter_store, self._on_store)

    def _on_store(self, state, meta, s):
        self.set_state({"count": state["count"]})
```

Auto-cleaned on `on_stop()`.
