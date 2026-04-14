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

Occasionally you need to interop with third-party JavaScript. SPRAG provides escape hatches:

### Module imports

Declare JS dependencies on the page manifest and use them in your Module:

```python
# page.py
playground = page(
    path="/playground",
    controller=PlaygroundController,
    screen=PlaygroundScreen,
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

Add `--rebuild` to force a fresh compile:

```bash
sprag inspect /counter --rebuild
```
