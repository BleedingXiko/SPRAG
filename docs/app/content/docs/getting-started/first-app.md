---
title: First App
description: Walk through the default counter app from scaffold to build.
order: 2
---

# First App

This guide walks through the default scaffold end to end — from `sprag new` to a built artifact.

## Scaffold and run

```bash
sprag new myapp
cd myapp
sprag dev
```

Open `http://localhost:8000/counter`. You'll see a counter with an increment button.

## What's happening

### The server: `app/routes/counter/server.py`

```python
from sprag import Controller, Field, Schema, action

class CounterController(Controller):
    route = "/counter"

    def load(self):
        return {"count": 0}

    @action(schema=Schema("increment", {"count": Field(int, required=True)}))
    def increment(self, count):
        return {"count": count + 1}
```

- `load()` returns the initial data for the page. This runs on the server during SSR and is sent to the browser as the boot payload.
- `@action` declares a named mutation. The browser can call it with `dispatch("increment", {count: N})`.

### The browser module: `app/routes/counter/modules.py`

```python
from sprag import Module

class CounterModule(Module):
    def on_start(self):
        self.delegate(self.element, "click", "[data-role='increment']", self.on_increment)

    def on_increment(self, event, target):
        event.prevent_default()
        self.dispatch("increment", {"count": self.state["count"]})
```

- `Module` owns non-visual lifecycle — event listeners, server calls, state.
- `self.dispatch("increment", payload)` calls the server `@action` and applies the returned state.
- This Python compiles to Ragot ESM JavaScript at build time.

### The browser component: `app/routes/counter/components.py`

```python
from sprag import Component, ui

class CounterCard(Component):
    def render(self, props=None):
        return ui.div(
            ui.span(str(self.state["count"])),
            ui.button("Increment", data_role="increment"),
        )
```

- `Component` owns a DOM subtree. `render()` returns a `ui.*` tree.
- When state changes, the component re-renders via morphDOM diffing.

## Make a change

Add a reset button. In `components.py`:

```python
ui.button("Reset", data_role="reset"),
```

In `modules.py`, add a handler:

```python
self.delegate(self.element, "click", "[data-role='reset']", self.on_reset)

def on_reset(self, event, target):
    event.prevent_default()
    self.set_state({"count": 0})
```

Save both files. The dev server rebuilds and the browser updates.

## Build for production

```bash
sprag build
```

This emits the full site into `dist/`. Open `dist/counter/index.html` in a browser — the counter still works, fully static.

For production optimization:

```bash
sprag pack
```

This minifies, gzips, fingerprints, and optimizes the `dist/` output.
