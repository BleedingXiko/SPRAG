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
        self.call_action("add_item", {"text": text}).then(self.on_added)

    def on_add(self, event, target):
        event.prevent_default()
        self.set_state({"adding": True})
```

## Constructor

`__init__` supports field assignments, conditionals, local variables, and method calls. The only restriction is that `self.state` and `self.screen` are owned by the runtime and cannot be assigned directly.

```python
class GameModule(Module):
    def __init__(self):
        super().__init__()
        self.timer_id = None
        self.config = {"difficulty": "normal", "rounds": 5}
        if some_condition:
            self.mode = "advanced"
```

Heavy setup (DOM access, event listeners, server calls) belongs in `on_start()`.

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

### `call_action(action, payload)`

Calls a server `@action` and returns a Promise-like result:

```python
def on_increment(self, event, target):
    self.call_action("increment", {"count": self.state["count"]}).then(self.on_result)

def on_result(self, result):
    self.set_state(result.value)
```

Use `result.value` to read the action payload and update Module state explicitly.

Returns a Promise when you need to handle the response:

```python
def on_save(self, event, target):
    result = self.call_action("save", {"text": self.state["text"]})
    result.then(self._on_saved)
```

## Reading JSON payloads

Browser payloads are plain JSON objects. Use dict-style access in Module code:

```python
payload = browser.window.__SPRAG_PAYLOAD__ or {}
auth = payload.get("auth")
count = payload.get("count", 0)
```

That spelling type-checks cleanly and compiles to JavaScript property access.

## DOM access

- **`self.element`** — the DOM node this Module is attached to (passed from `hydrate()`)

## Child Components

Most interactive pages should let `hydrate(...)` wire Module/Component ownership for you:

```python
# web.py
class MyScreen(Screen):
    modules = [SidebarModule]

    def render(self, data):
        module = self.module(SidebarModule)
        module.set_state(data)
        return hydrate(SidebarComponent, module=module)
```

For advanced ownership patterns, `adopt_component(...)` is also real on the underlying Ragot/SPRAG Module surface, but `hydrate(...)` is still the default and safest authoring path.

## Sockets

These methods use SPRAG's shared realtime bridge when the page runs in websocket mode. They do not require a Socket.IO client.

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
    self.call_action("get_items", {}).then(self._on_items_refetched)

def _on_items_refetched(self, result):
    self.set_state(result.value or {})
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

## Page metadata

Update the page title, description, or canonical URL dynamically from the browser:

```python
def on_start(self):
    self.set_metadata({"title": "My Page — App"})

def _on_article_loaded(self, result):
    article = (result.value or {}).get("article") or {}
    self.set_metadata({
        "title": article.get("title", ""),
        "description": article.get("summary", ""),
    })
```

`set_metadata(metadata, options={})` merges the dict onto the active page head. The same keys supported in the static `page(metadata={...})` manifest work here: `title`, `description`, `canonical`, `og:*`. Use it for routes where the document title depends on data fetched after hydration.

## Batching state updates

`batch_state(fn)` calls `fn(state)` with the current mutable state and fires exactly one re-render when the mutator returns. Use it when you need multiple fields updated atomically, reading from the current state:

```python
class CounterModule(Module):
    def on_start(self):
        self.delegate(self.element, "click", "[data-role='reset']", self.on_reset)

    def on_reset(self, event, target):
        self.batch_state(self._reset)

    def _reset(self, state):
        self.set_state({
            "count": 0,
            "total": state.get("total", 0) + 1,
            "last_reset": True,
        })
```

Pass a method reference — nested `def` inside a method is not supported in browser codegen.

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

Auto-cleaned on `on_stop()`. The callback receives `(state, meta, store)` — trailing args can be omitted if unused.

## Page and Mount Providers

`page(..., providers={...})` and `mount(..., providers={...})` start browser provider Modules before the page hydrates or the mount starts. Resolve them from another browser Module with `self.provider(key)`.

```python
class ToastProvider(Module):
    def on_start(self):
        self.last_message = ""

    def push(self, message):
        self.last_message = message


class InboxModule(Module):
    def on_start(self):
        toast = self.provider("toast")
        toast.push("Inbox ready")
```

Declare the provider on the surface:

```python
inbox = page(
    path="/inbox",
    controller=InboxController,
    screen=InboxScreen,
    providers={"toast": ToastProvider},
)
```
