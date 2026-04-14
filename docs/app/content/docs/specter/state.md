---
title: State Primitives
description: "create_store vs create_model vs create_cache — when to use each, semantics, and subscription patterns."
order: 24
---

# State Primitives

Specter provides three state primitives for server-side data. Each fits a different shape of problem.

## Which one?

| Primitive | Shape | Snapshots | Best for |
|---|---|---|---|
| `create_store` | Flat key-value | Shallow copy | Shared mutable counters, flags, connection state |
| `create_model` | Nested graph | Deep copy | Complex runtime state with path-based access |
| `create_cache` | Computed values | N/A | Expensive data with TTL and invalidation |

## `create_store`

Flat shared state with atomic updates and shallow snapshots.

```python
from sprag import create_store

connections = create_store("connections", {"count": 0, "active": []})

# Read
snapshot = connections.get()

# Write — shallow merge
connections.set({"count": 1})

# Atomic mutation under lock
connections.update(lambda draft: draft.update({"count": draft["count"] + 1}))
```

### Subscriptions

```python
# Fire on every change
connections.subscribe(lambda snapshot, store: print(snapshot))

# Fire immediately with current state, then on changes
connections.subscribe(on_change, immediate=True)
```

### Bus integration

Pass `emit_events=True` to automatically emit `'{name}:changed'` on the internal bus after every write:

```python
connections = create_store("connections", {"count": 0}, emit_events=True)
```

## `create_model`

Nested state with dot-path access and selector subscriptions.

```python
from sprag import create_model

tv = create_model("tv", {
    "playback": {"url": None, "position": 0},
    "casting": False,
})

# Path-based read/write
tv.set("playback.url", "/media/movie.mp4")
url = tv.get("playback.url")

# Bulk patch
tv.patch({"casting": True, "playback": {"url": "/media/new.mp4", "position": 0}})
```

### Selector subscriptions

Subscribe to a specific path — the callback only fires when that value changes:

```python
tv.subscribe(
    lambda value, model: print(f"Now playing: {value}"),
    selector=lambda s: s["playback"]["url"],
)
```

Snapshots are deep copies, so subscribers always see a consistent view.

## `create_cache`

TTL-based cache for expensive computed data.

```python
from sprag import create_cache

drives = create_cache("drives", ttl=300, depends_on=["storage:mount_changed"])

# Atomic cache-miss computation
result = drives.get_or_compute(scan_drives)
```

### Invalidation

- `drives.invalidate()` clears the cache and emits `'drives:invalidated'` on the bus.
- `depends_on=[...]` automatically invalidates when those bus events fire.
- `ttl=0` means no automatic expiry — manual invalidation only.

## Ownership

All three primitives support `own()` for lifecycle-managed cleanup:

```python
class MediaService(Service):
    def on_start(self):
        self.playback = create_model("playback", {"url": None})
        self.own(self.playback)  # cleaned up when service stops
```

## `store()` vs raw primitives

For state that needs to exist on both server and browser, use `store()` from `sprag`. It wraps `create_model` on the server and emits a `createStateStore` shim for the browser. Use raw `create_store` / `create_model` / `create_cache` only for server-only state.
