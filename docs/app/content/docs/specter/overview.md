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
