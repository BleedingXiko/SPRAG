---
title: Specter Overview
description: The server-side runtime under SPRAG — when and why to reach for raw Specter.
order: 20
---

# Specter Overview

Specter is the server-side runtime that powers SPRAG. It provides cooperative concurrency via gevent, a service lifecycle, an internal event bus, and persistent state primitives.

## SPRAG re-exports Specter

Everything you need from Specter is available through the `sprag` import:

```python
# Use this
from sprag import Controller, Service, Schema, Field, action, bus, registry

# Not this
from specter import Controller, Service, ...
```

The SPRAG surface wraps Specter with conventions (routes, actions, stores) that handle most use cases. You don't need to think about Specter for typical page-building work.

## When to reach for raw Specter

Drop to raw Specter when you need:

- **Custom HTTP routes** beyond the page/action model — `build_routes(router)` on your Controller
- **Socket event handlers** — `build_events(handler)` for raw socket processing
- **Service-to-service communication** — the internal `bus` for decoupled event dispatch
- **Server-only stores** — `create_store`, `create_model` for persistence that doesn't need a browser counterpart
- **Watchers** — `Watcher` for file system or resource monitoring
- **Managed processes** — `ManagedProcess` for supervised subprocesses
- **Caches** — `create_cache` for in-memory TTL caches

## Available escape hatches

```python
from sprag import (
    create_store,      # Key-value store with persistence
    create_model,      # Structured model with fields
    create_cache,      # TTL cache
    Handler,           # Raw HTTP handler
    SocketIngress,     # Raw socket event handler
    Watcher,           # File/resource watcher
    ManagedProcess,    # Supervised subprocess
    bus,               # Internal event bus
    registry,          # Service/provider registry
)
```

These are the same primitives SPRAG itself uses internally. Using them directly gives you full control when the SPRAG surface doesn't fit.

## Decision Matrix

Use this to decide which primitive is right for the job.

| Situation | Use | Do Not Default To |
|---|---|---|
| Background lifecycle with timers/listeners | `Service` | module-level greenlets |
| Queue-backed worker pipeline | `QueueService` | manual queue + ad-hoc worker loops |
| Feature spans routes + sockets + state | `Controller` | split logic across unrelated modules |
| Class-based socket event ownership | `Handler` | free-function handler registrars |
| One socket event with multiple backend listeners | `SocketIngress` | registering competing handlers directly on Socket.IO |
| Polling external state | `Watcher(poll=...)` | hand-rolled `while True` loops |
| Stream-following external source | `Watcher(stream=...)` or `ManagedProcess` | unmanaged reader greenlets |
| Shared mutable flat state | `create_store` | global dict + manual locks |
| Nested runtime state | `create_model` | deep dict mutation scattered in services |
| Expensive data with expiry | `create_cache` | perpetual stale globals |
| Broadcast internal notifications | `bus` | import chains for fanout |
| Provision shared services | `registry.provide` | ad-hoc globals |

## Common Pitfalls

1. **Synchronous Bus**: `bus.emit` is synchronous; long listeners block the emitter greenlet. Spawn a new greenlet if necessary.
2. **Locking in Cache**: `Cache.get_or_compute` executes the factory while holding the cache lock. Keep factories fast.
3. **Socket Unregistration**: Direct registrations on Flask-SocketIO via `socketio.on` are not fully unregisterable. Use `Handler` or `SocketIngress` for clean teardown.
4. **Schema Exceptions**: `Schema.require` raises `HTTPError` or `SchemaError`; ensure you handle these or use a `json_endpoint`.
5. **Lifecycle State**: `Service.spawn`, `interval`, and `timeout` require the service to be running (`start()` has been called).
6. **Registry Timeouts**: `registry.wait_for` can block indefinitely if the dependency never arrives. Always provide a `timeout`.
