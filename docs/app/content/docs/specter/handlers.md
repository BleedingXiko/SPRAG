---
title: Handlers & SocketIngress
description: Class-based socket event binding, priority ordering, and multi-subscriber fanout.
order: 25
---

# Handlers & SocketIngress

Specter provides two primitives for socket event handling: `Handler` for class-based event ownership, and `SocketIngress` for multi-subscriber fanout when multiple parts of your app need to react to the same socket event.

## Handler

A `Handler` binds socket events to methods on a class with managed lifecycle.

```python
from sprag import Handler

class PresenceHandler(Handler):
    name = "presence"
    events = {
        "heartbeat": "handle_heartbeat",
        "disconnect": "handle_disconnect",
    }

    def handle_heartbeat(self, data=None):
        record_heartbeat(data)
        return {"ok": True}

    def handle_disconnect(self):
        clear_presence()
```

### Lifecycle

| Method | When |
|---|---|
| `setup(socketio)` | Registers events from the `events` dict, then calls `on_setup()` |
| `teardown()` | Calls `on_teardown()`, runs cleanup callbacks |

Both are idempotent.

### Event priorities

Handlers can declare priority ordering for use with `SocketIngress`:

```python
class PresenceHandler(Handler):
    name = "presence"
    events = {"heartbeat": "handle_heartbeat"}
    event_priorities = {"heartbeat": 10}  # lower runs first
```

Priority only matters when `SocketIngress` is active.

## SocketIngress

Flask-SocketIO stores one callback per event name. If multiple handlers need to react to the same event, they'd overwrite each other. `SocketIngress` solves this by registering a single dispatcher per event and fanning out to ordered subscribers.

### How it works

1. `SocketIngress` registers itself as the sole listener for each event on Socket.IO
2. Multiple handlers subscribe through the ingress
3. On event, the ingress dispatches to all subscribers in priority order

### Priority rules

- Lower numeric priority runs first
- Ties preserve subscription order
- Dispatch continues even if one subscriber raises (errors are logged)

### Error handling

`SocketIngress` also provides shared error handling via `subscribe_error_default(...)` and `on_error_default(...)`, which attach to Socket.IO's error handler through a shared dispatcher.

### Usage

You typically don't interact with `SocketIngress` directly. When you register handlers through a `Controller` or `ServiceManager`, the ingress is set up automatically if multiple handlers bind to the same events.

```python
from sprag import Controller

class ChatController(Controller):
    route = "/chat"

    def build_events(self, handler):
        handler.on("message", self._on_message)
        handler.on("typing", self._on_typing)

    def _on_message(self, data=None):
        broadcast_message(data)

    def _on_typing(self, data=None):
        notify_typing(data)
```

If another controller also listens to `"message"`, `SocketIngress` handles the fanout transparently.
