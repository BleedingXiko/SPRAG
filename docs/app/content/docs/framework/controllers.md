---
title: Controllers
description: Server-side request handling — load, actions, HTTP routes, and socket events.
order: 12
---

# Controllers

A Controller is the server-side entry point for a route. It handles the initial page load, declares actions that the browser can call, and optionally sets up custom HTTP routes and socket events.

## Basic shape

```python
from sprag import Controller, Field, Schema, action

class TodoController(Controller):
    route = "/todos"

    def load(self):
        return {"items": [], "filter": "all"}

    @action(schema=Schema("add_item", {"text": Field(str, required=True)}))
    def add_item(self, text):
        items = self.request.data.get("items", [])
        items.append({"text": text, "done": False})
        return {"items": items}
```

## `load()`

Called on every page request. Returns a dict that becomes:

- The data passed to `Screen.render()` for SSR
- The `window.__SPRAG_PAYLOAD__` sent to the browser for hydration

```python
def load(self):
    user = self.request.user
    return {"profile": user.profile, "settings": user.settings}
```

If `load()` returns a redirect, the server sends a 302 before rendering:

```python
def load(self):
    if not self.request.user:
        return self.redirect("/login")
    return {"user": self.request.user}
```

## Actions

Actions are named server mutations that the browser can call via `dispatch()` or `call_action()`.

```python
@action(schema=Schema("update", {
    "id": Field(int, required=True),
    "text": Field(str, required=True),
}))
def update(self, id, text):
    # mutate state, return new state
    return {"items": updated_items}
```

The schema validates the incoming payload automatically. Invalid data returns a structured 400 response.

### Deferred actions

For work that takes longer than a request cycle:

```python
@action(defer=True)
def process_file(self, file_id):
    # This runs in a background greenlet
    # The browser gets an immediate acknowledgment
    result = expensive_operation(file_id)
    return {"status": "done", "result": result}
```

## Request context

Inside any controller method, `self.request` gives you the current request:

| Property | Description |
|---|---|
| `self.request.params` | URL params (`slug`, `segments`, etc.) |
| `self.request.query` | Query string as a dict |
| `self.request.data` | Current page state (from load or previous action) |
| `self.request.session` | Session dict |
| `self.request.session_id` | Session ID string |
| `self.request.user` | Authenticated user (or `None`) |
| `self.request.cookies` | Request cookies |
| `self.request.file(name)` | Uploaded file by field name |
| `self.request.files_list(name)` | List of uploaded files |

## Auth guards

```python
from sprag import requires_auth

class AdminController(Controller):
    route = "/admin"

    @requires_auth(redirect_to="/login")
    def load(self):
        return {"users": get_all_users()}

    @requires_auth
    @action(schema=Schema("delete_user", {"id": Field(int, required=True)}))
    def delete_user(self, id):
        remove_user(id)
        return {"users": get_all_users()}
```

Apply `@requires_auth` at the class level to guard everything, or per-method.

## Custom HTTP routes

For endpoints that aren't page loads or actions (APIs, webhooks, etc.):

```python
def build_routes(self, router):
    @router.route("/api/health")
    def health():
        return {"status": "ok"}

    @router.route("/api/items", methods=["POST"])
    def create_item():
        data = request.json
        return {"created": True}
```

## Socket events

For handling real-time WebSocket events:

```python
def build_events(self, handler):
    handler.on("chat_message", self._on_chat)

def _on_chat(self, data, session_id=None):
    # Process incoming socket event
    self.emit_socket("new_message", {
        "text": data["text"],
        "from": session_id,
    })
```

### Pushing to the browser

```python
# Broadcast to all connected clients
self.emit_socket("update", {"count": new_count})

# Target a specific session
self.emit_socket("notification", {"msg": "Done!"}, session_id=sid)

# Target a topic (room)
self.emit_socket("room_update", data, topic="room:123")
```

## Redirects

```python
# From load()
def load(self):
    return self.redirect("/somewhere")

# From an action
@action(schema=Schema("submit", {...}))
def submit(self, **data):
    save(data)
    return self.redirect("/success")
```

The browser follows redirects automatically when using `dispatch()`.
