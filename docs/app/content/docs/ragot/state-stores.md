---
title: State Stores
description: Deep dive into createStateStore — selectors, actions, and cross-component shared state.
order: 34
---

# State Stores

Use a state store when you need to share mutable state across multiple independent modules or components. While local `Module` state is perfect for feature-local orchestration, `createStateStore` provides a robust primitive for app-wide synchronisation.

> **`store()` vs `createStateStore`**: `store()` is the cross-runtime bridge — one declaration works on both server and browser. `createStateStore` is the browser-only Ragot primitive for SPA/mount use cases where no server-side state exists.

## Creating a Store

```python
from sprag import createStateStore

player_store = createStateStore({
    "current_url": None,
    "is_playing": False,
    "volume": 0.8,
}, {"name": "player"})
```

The second argument is an options dict. `name` is used in log/error messages.

## Reading and Writing

```python
# Full state (returns a proxy — mutations are tracked automatically)
state = player_store.get_state()

# Dot-path read with optional fallback
volume = player_store.get("volume")
name = player_store.get("user.name", "Anonymous")
```

Writes support shallow merging, dot-path setting, atomic batches, and conditional set:

```python
# Shallow merge (alias: patch)
player_store.set_state({"is_playing": True})

# Dot-path set
player_store.set("volume", 1.0)

# Atomic batch — all mutations fire exactly one subscriber notification
def update_player(state, store):
    state["is_playing"] = True
    state["volume"] = 0.5

player_store.batch(update_player)

# Conditional set — only writes if current value matches expected
player_store.compare_and_set("volume", 0.5, 0.8)
```

## Actions

Encapsulate state transitions into named actions for better maintainability:

```python
player_store.register_actions({
    "play": lambda store: store.set("is_playing", True),
    "pause": lambda store: store.set("is_playing", False),
    "seek": lambda store, time: store.set("position", time),
})

# Dispatch from anywhere
player_store.dispatch("play")
player_store.dispatch("seek", 120.5)

# Direct access to bound action functions
player_store.actions.increment()
```

## Subscriptions and Selectors

Subscribe to changes to react in your modules. Use **selectors** to avoid unnecessary work — the callback only fires when the selected slice changes.

```python
class PlayerModule(Module):
    def on_start(self):
        # Full state subscription
        unsub = player_store.subscribe(self._on_change)
        self.add_cleanup(unsub)

        # Selector subscription — only fires when volume changes
        unsub2 = player_store.subscribe(
            self._on_volume,
            {"selector": lambda s: s["volume"]}
        )
        self.add_cleanup(unsub2)

    def _on_change(self, state, meta, store):
        self.set_state({"player": state})

    def _on_volume(self, volume, meta, store, prev_volume):
        self.component.set_state({"v": volume})
```

### Subscriber Signatures

| Subscription Mode | Callback Arguments |
|---|---|
| No selector | `(state_proxy, change_meta, store)` |
| With selector | `(slice, change_meta, store, prev_slice)` |

### Subscribe Options

| Option | Default | Description |
|---|---|---|
| `selector` | `None` | Function `(state) -> slice`. Callback only fires when slice changes. |
| `equals` | `Object.is` | Custom equality function for the selected slice. |
| `immediate` | `False` | If true, fires the subscriber immediately with current state. |

## Memoised Selectors

Use `createSelector` to compute derived state with memoisation. Recomputes only when an input selector's output changes.

```python
from sprag import createSelector

select_visible = createSelector(
    [lambda s: s["items"], lambda s: s["filter"]],
    lambda items, filter_val: [i for i in items if filter_val == "all" or i["type"] == filter_val]
)

# Use as a subscribe selector
player_store.subscribe(on_change, {"selector": select_visible})
```

## Full Store API

| Method | Description |
|---|---|
| `get_state()` | Returns the proxied mutable state |
| `get(path, fallback)` | Dot-path read |
| `set(path, value)` | Dot-path write |
| `set_state(partial)` | Shallow merge (alias: `patch`) |
| `batch(mutator)` | Grouped mutations, one notification |
| `compare_and_set(path, expected, next)` | Conditional write — only if current equals expected |
| `subscribe(listener, options)` | Subscribe to changes; returns unsubscribe function |
| `register_actions(definitions)` | Register named action functions |
| `dispatch(name, *args)` | Call a registered action by name |
| `actions` | Direct access to bound action functions |
| `list_actions()` | Returns list of registered action names |
| `create_selector(inputs, fn)` | Create a memoised selector scoped to this store |
| `get_version()` | Current change version counter |
| `get_last_change()` | Last change metadata |
