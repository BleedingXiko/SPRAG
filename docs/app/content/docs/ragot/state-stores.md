---
title: State Stores
description: Deep dive into createStateStore — browser-side shared state and selectors.
order: 34
---

# State Stores

Use a state store when you need to share mutable state across multiple independent modules or components. While local `Module` state is perfect for feature-local orchestration, `createStateStore` provides a browser-side primitive for app-wide synchronization.

> **`store()` vs `createStateStore`**: `store()` is the cross-runtime SPRAG bridge (server + browser). `createStateStore` is browser-only.

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

Writes support shallow merging, path setting, atomic batches, and conditional set:

```python
# Shallow merge (alias: patch)
player_store.set_state({"is_playing": True})

# Dot-path set
player_store.set("volume", 1.0)

# Atomic batch — all mutations fire exactly one subscriber notification
def update_player(state):
    state["is_playing"] = True
    state["volume"] = 0.5

player_store.batch(update_player)

# Conditional set — only writes if current value matches expected
player_store.compare_and_set("volume", 0.5, 0.8)
```

## Actions

Register named actions when you want reusable store-local transitions:

```python
player_store.register_actions({
    "play": lambda store: store.set("is_playing", True),
    "pause": lambda store: store.set("is_playing", False),
    "seek": lambda store, time: store.set("position", time),
})

player_store.dispatch("play")
player_store.dispatch("seek", 120.5)
```

## Subscriptions and Selectors

Subscribe to changes to react in your modules. Use selectors to narrow updates.

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

You can ignore trailing callback arguments if you do not need them.

### Subscribe Options

| Option | Default | Description |
|---|---|---|
| `selector` | `None` | Path/selector used to narrow updates |
| `equals` | runtime default | Optional equality function for selector updates |
| `immediate` | `False` | If true, fires the subscriber immediately |

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
| `get_state()` | Returns the current store state |
| `get(path, fallback)` | Reads a path or key with an optional fallback |
| `set(path, value)` | Writes a path or key |
| `set_state(partial)` | Shallow merge of root state |
| `patch(partial)` | Alias for `set_state(partial)` |
| `batch(mutator)` | Groups multiple mutations into one notification cycle |
| `compare_and_set(path, expected, next)` | Writes only if the current value matches `expected` |
| `subscribe(listener, options)` | Subscribes to store changes and returns an unsubscribe function |
| `register_actions(definitions)` | Registers named action functions on the store |
| `dispatch(name, *args)` | Invokes a registered action by name |
| `create_selector(inputs, fn)` | Builds a memoized selector from input selectors |

## Raw Ragot extras

The shipped Ragot runtime also exposes a few lower-level helpers beyond the SPRAG browser stub surface:

| Method | Description |
|---|---|
| `actions` | Object containing the registered action callables |
| `list_actions()` | Returns registered action names |
| `get_version()` | Returns the current store version counter |
| `get_last_change()` | Returns the last emitted change metadata |
