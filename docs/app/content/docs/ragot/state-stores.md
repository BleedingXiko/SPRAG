---
title: State Stores
description: Deep dive into createStateStore — selectors, actions, and cross-component shared state.
order: 34
---

# State Stores

Use a state store when you need to share mutable state across multiple independent modules or components. While local `Module` state is perfect for feature-local orchestration, `createStateStore` provides a robust primitive for app-wide synchronization.

## Creating a Store

```python
from sprag import createStateStore

player_store = createStateStore({
    "current_url": None,
    "is_playing": False,
    "volume": 0.8,
}, {"name": "player"})
```

## Reading and Writing

You can read the entire state or specific paths:

```python
state = player_store.get_state()
volume = player_store.get("volume")
```

Writes support shallow merging, path-based setting, and atomic batches:

```python
# Shallow merge
player_store.set_state({"is_playing": True})

# Path-based set
player_store.set("volume", 1.0)

# Atomic batch mutation
def update_player(draft):
    draft["is_playing"] = True
    draft["volume"] = 0.5

player_store.batch(update_player)
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
```

## Subscriptions and Selectors

Subscribe to changes to react in your modules. Use **selectors** to avoid unnecessary re-renders — the callback only fires when the selected slice changes.

```python
class VolumeIndicator(Component):
    def on_start(self):
        # Only fires when volume changes
        self.unsub = player_store.subscribe(
            lambda volume, meta, store, prev: self.set_state({"v": volume}),
            selector=lambda s: s["volume"]
        )
        self.add_cleanup(self.unsub)
```

### Subscriber Signatures

| Subscription Mode | Callback Arguments |
|---|---|
| No selector | `(state_proxy, change_meta, store)` |
| With selector | `(slice, change_meta, store, prev_slice)` |

## Composition with Registry

For app-wide visibility, provide your store in the registry:

```python
# Startup code
ragot_registry.provide("player", player_store, root_module)

# Usage in a far-away module
class MiniPlayer(Module):
    def on_start(self):
        player = ragot_registry.require("player")
        player.subscribe(...)
```

## Memoized Selectors

Use `create_selector` to compute derived state with memoization:

```python
from sprag import create_selector

select_visible_items = create_selector(
    [lambda s: s["items"], lambda s: s["filter"]],
    lambda items, filter_val: [i for i in items if filter_val == "all" or i["type"] == filter_val]
)

# Use in subscription
player_store.subscribe(on_change, selector=select_visible_items)
```
