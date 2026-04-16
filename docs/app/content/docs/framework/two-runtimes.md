---
title: Two Runtimes
description: The core concept — one Python codebase compiles to two runtimes, server and browser.
order: 10
---

# Two Runtimes

This is the most important concept in SPRAG. Understanding it unlocks everything else.

## The compile boundary

When you run `sprag build`, SPRAG walks your route directories and separates files by runtime:

- **Server files** (`server.py`) — run as Python under Specter/gevent/Flask. These are your controllers, services, and data layer.
- **Browser files** (`components.py`, `modules.py`) — compile from Python to Ragot ESM JavaScript. These become your UI and client-side behavior.

The compile boundary is at the class level. A `Component` or `Module` subclass is always browser code. A `Controller` or `Service` subclass is always server code. There's no ambiguity.

## Why one language

- **No context switching.** The same patterns (lifecycle hooks, state, events) work on both sides. A `Module.on_start()` looks just like a `Service.on_start()`.
- **IDE works everywhere.** Autocomplete, go-to-definition, and type checking work on both server and browser code — it's all Python.
- **Shared mental model.** State, events, and lifecycle follow the same conventions. Learn them once.

## Symmetry table

These patterns are intentionally identical across runtimes:

| Concept | Server (`Service`) | Browser (`Module`) |
|---|---|---|
| Start hook | `on_start(self)` | `on_start(self)` |
| Stop hook | `on_stop(self)` | `on_stop(self)` |
| Periodic timer | `self.interval(fn, seconds)` | `self.interval(fn, seconds)` |
| One-shot timer | `self.timeout(fn, seconds)` | `self.timeout(fn, seconds)` |
| Listen to event | `self.listen(event, fn)` | `self.listen(event, fn)` |
| Emit event | `self.emit(event, data)` | `self.emit(event, data)` |
| Shared state | `store("x").set(k, v)` | `store("x").set(k, v)` |
| Teardown | `self.add_cleanup(fn)` | `self.add_cleanup(fn)` |

## What can't cross

Each runtime owns its boundary:

- **DOM** — browser only. Components render `ui.*` trees; the server never touches the DOM.
- **Sockets** — the server *sends* socket events, the browser *receives* them. The Module has `on_socket()`; the Controller has `emit_socket()`.
- **HTTP** — the server handles requests. The browser calls `call_action()` to invoke server actions over HTTP.
- **File system** — server only. The browser has no access.

## Escape hatches

SPRAG wraps Specter (server) and Ragot (browser), but both are fully available:

- **Raw Specter**: `create_store`, `create_model`, `create_cache`, `Handler`, `Watcher`, `ManagedProcess` — for server-only patterns that don't fit the SPRAG surface.
- **Raw Ragot**: `module(src, export=...)` with `imports.*` in your Module for third-party JS interop; `browser.*` for `globalThis.*` access.

Use the SPRAG surface for 90% of your work. Drop to the raw runtime when you need something specific.

---

Both runtimes are open source under the Apache 2.0 license:
- [Specter](https://github.com/BleedingXiko/SPECTER) — the Python server runtime
- [Ragot](https://github.com/BleedingXiko/RAGOT) — the browser runtime
