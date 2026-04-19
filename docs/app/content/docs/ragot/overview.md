---
title: Ragot Overview
description: The browser runtime that SPRAG's codegen targets — what it is and when to reach for it directly.
order: 30
---

# Ragot Overview

Ragot is the browser-side JavaScript runtime that SPRAG compiles to. You never import Ragot directly — you write Python `Component` and `Module` subclasses, and `sprag build` compiles them to Ragot ESM JavaScript.

## What Ragot provides

Under the hood, your compiled code uses:

- **morphDOM diffing** for efficient DOM updates when state changes
- **Keyed list reconciliation** (`renderList`) for `ui.For()` loops
- **Grid layout engine** (`renderGrid`) for `ui.Grid()` layouts
- **Lazy loading** (`createLazyLoader`) for `ui.LazyImage()` images
- **Socket.IO client** for real-time communication
- **Animation helpers** (`animateIn`/`animateOut`) for CSS transitions
- **Virtual scrolling** for large lists

You don't interact with any of these directly. They're the compilation targets for SPRAG's Python surface.

## When to reach for raw Ragot

Occasionally you need a browser-only escape hatch. SPRAG exposes those through Python authoring stubs:

### Module imports

Declare JS dependencies on the page manifest and use them in your Module:

```python
# page.py
analytics_page = page(
    path="/analytics",
    controller=AnalyticsController,
    screen=AnalyticsScreen,
    mode="hybrid",
    modules={"chart": "/vendor/chart.esm.js"},
)
```

```python
# modules.py — the import is available via imports.*
class ChartModule(Module):
    def on_start(self):
        chart_lib = imports.chart
        self.chart = chart_lib.create(self.element.querySelector(".chart"))
```

### Browser globals

Access `globalThis.*` via `browser.*`:

```python
class TimerModule(Module):
    def on_start(self):
        self.width = browser.innerWidth
        browser.console.log("Module started")
```

## Inspecting compiled output

To see what your Python compiles to:

```bash
sprag inspect /counter
```

This shows the generated JavaScript with source location comments mapping back to your Python.

## Decision Matrix

Use this to decide which primitive is right for the job.

| Situation | Use | Do Not Default To |
|---|---|---|
| Parent owns child component lifecycle | `adoptComponent` | registry lookup |
| Parent owns child module lifecycle | `adopt` | bus events |
| Parent pushes module state into child UI | `adoptComponent(..., { sync })` | registry indirection |
| Shared writable state across independent modules | `createStateStore` | bus-only state transfer |
| App startup service registration | `ragotRegistry.provide` | manual globals |
| Service may appear later | `waitForCancellable` | plain `waitFor` |
| One event, many independent listeners | `bus` | registry call chains |

## Common Pitfalls

1. **`watchState` signature**: The first argument must be a function.
2. **`subscribe` signature**: The first argument must be a function.
3. **`adopt()` defaults**: `adopt()` defaults to the `stop` method; components usually need `unmount`.
4. **Detached mounting**: Mounting into detached containers breaks measurements and observers.
5. **Keyed siblings**: Mixing keyed and unkeyed siblings in `morphDOM` causes ordering issues. Use `data-ragot-key` consistently.
6. **Socket validation**: Calling `onSocket` with a non-socket first argument logs a warning and skips binding.
7. **Pending `waitFor`**: Awaiting `waitFor(...)` without cancellation in lifecycle owners can leak pending handles.
8. **Registry for ownership**: Using the registry for parent-owned child wiring makes ownership and teardown ambiguous.
9. **Logic split**: Implementing the same behavior through both direct calls and bus events causes split logic paths.

Add `--rebuild` to force a fresh compile:

```bash
sprag inspect /counter --rebuild
```
