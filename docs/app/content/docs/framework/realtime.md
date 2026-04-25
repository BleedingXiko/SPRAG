---
title: Realtime
description: WebSocket communication — server push, browser subscription, topics, and the signal-then-refetch pattern.
order: 16
---

# Realtime

SPRAG's realtime layer connects server events to browser handlers over WebSockets. The design follows a simple principle: **signal then refetch**.

The built-in bridge uses the browser's native `WebSocket` API and SPRAG's server-side websocket endpoint. Socket.IO is not part of SPRAG's realtime runtime.

## Prerequisites

WebSocket server mode requires `gevent-websocket`. If you installed SPRAG with `pip install spragkit`, install it manually:

```bash
pip install gevent-websocket
```

When `server_mode="websocket"` is detected, `sprag build` already adds `gevent-websocket` to `dist/requirements.txt` for production bundles.

## The pattern

Instead of pushing full state snapshots over the socket, the server emits a lightweight signal. The browser receives it and calls a server action to fetch authoritative data:

```
Server: emit_socket("items_changed", {})
Browser: on_socket("items_changed") → call_action("get_items", {}).then(...)
```

This keeps the socket channel thin and ensures the browser always has validated, authoritative state.

## Server → browser

From any controller, push a socket event:

```python
# Broadcast to all connected clients
self.emit_socket("items_changed", {"source": "bulk_import"})

# Target a specific session
self.emit_socket("notification", {
    "message": "Your export is ready"
}, session_id=self.request.session_id)

# Target a topic (room)
self.emit_socket("room_update", {
    "action": "new_message"
}, topic="room:42")
```

## Browser subscription

In a Module's `on_start()`, subscribe to socket events:

```python
class ChatModule(Module):
    def on_start(self):
        self.on_socket("new_message", self._on_message)

    def _on_message(self, data):
        # Refetch authoritative state from server
        self.call_action("get_messages", {}).then(self._on_messages)

    def _on_messages(self, result):
        self.set_state(result.value or {})
```

## Topics (rooms)

Topics partition socket events so clients only receive what's relevant:

```python
class ChatModule(Module):
    def on_start(self):
        room_id = self.state.get("room_id")
        self.join_topic(f"room:{room_id}")
        self.on_socket("room_update", self._on_update)

    def on_stop(self):
        room_id = self.state.get("room_id")
        self.leave_topic(f"room:{room_id}")
```

On the server, target the topic when emitting:

```python
self.emit_socket("room_update", data, topic=f"room:{room_id}")
```

## Convenience: refetch on socket

For the common signal-then-refetch pattern, use the shorthand:

```python
class ItemsModule(Module):
    def on_start(self):
        # When "items_changed" arrives, automatically call the "get_items" action
        self.refetch_on_socket("items_changed", action="get_items")
```

On the server side, there's a matching shortcut:

```python
# Emit the signal and include the refetch hint in one call
self.emit_socket_refetch("get_items", event="items_changed")
```

## Session targeting

To push events to a specific user's browser session:

```python
# In an action — target the current user
self.emit_socket("export_ready", {
    "url": download_url
}, session_id=self.request.session_id)
```

Store the `session_id` when you need to push from a background job or service later.

## Diagnostics

```bash
sprag routes
```

Use this to confirm the route exists and to inspect the action names and schemas your browser code is expected to call.
