---
title: Stores
description: Cross-runtime shared state — one declaration, works on server and browser.
order: 13
---

# Stores

`store()` is SPRAG's cross-runtime state primitive. Declare it once, use it on both server and browser with the same API.

## Declaration

```python
from sprag import store

counter_store = store("counter", initial={"count": 0})
```

This single declaration works in both runtimes:

- **Server**: backed by a Specter `Model`
- **Browser**: compiled to a Ragot `createStateStore` shim via `stores.js` — reactive, local to the tab

## API

The full store API is identical on both sides:

```python
# Read
value = counter_store.get("count")      # Single path/key
state = counter_store.get_state()       # Full state snapshot
snap = counter_store.snapshot()         # Deep snapshot

# Write
counter_store.set("count", 42)           # Set a path/key
counter_store.patch({"count": 99})       # Root-level merge
counter_store.delete("count")            # Remove a path/key

def bump(state):
    state["count"] = state.get("count", 0) + 1

counter_store.update(bump)               # Atomic mutator
counter_store.reset()                    # Reset to declared initial state

# Subscribe to changes
counter_store.subscribe(lambda state: print(state), immediate=True)
counter_store.listen("count", lambda value: print(value))

# Select a derived value
double = counter_store.select(lambda s: s.get("count", 0) * 2, default=0)
```

## Server-side usage

In a `Service`, subscribe to store changes in `on_start()`:

```python
from sprag import Service, store

counter_store = store("counter", initial={"count": 0})

class CounterService(Service):
    def on_start(self):
        counter_store.subscribe(self._on_change)

    def _on_change(self, state):
        if state["count"] > 100:
            self.emit("counter:overflow", state)
```

## Browser-side usage

In a `Module`, use `self.subscribe()` to bind store changes to your lifecycle:

```python
from sprag import Module, store

counter_store = store("counter", initial={"count": 0})

class CounterModule(Module):
    def on_start(self):
        self.subscribe(counter_store, self._on_store)

    def _on_store(self, state):
        self.set_state({"count": state["count"]})
```

`self.subscribe()` auto-cleans on Module stop — no manual unsubscribe needed.

## When to use stores

**Use `store()`** for state that needs to be shared across routes or between multiple components/modules in the same page. It gives you one authoring surface backed by Specter on the server and a generated store shim in the browser.

**Use controller state** (the dict from `load()`) for per-page state that lives within a single route. This is simpler and covers most cases.

**Use raw Specter** (`create_model`, `create_store`) for server-only persistence patterns that don't need a browser counterpart — user sessions, caches, background job state.
