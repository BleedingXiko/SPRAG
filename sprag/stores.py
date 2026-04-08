"""SPRAG store bridge — one Python declaration, two runtimes.

A store declared via ``store(name, initial=...)`` returns a single object
that the SPRAG codegen routes to the right runtime:

- **On the server**, it wraps a Specter ``create_store(name, initial)`` and
  delegates ``set / update / get_state / subscribe`` directly. Other
  server-side code (Controllers, Workers, Services) uses it as plain Python.

- **In a browser-side Module/Component**, the codegen sees the imported
  ``StoreBridge`` reference and emits a JS import from the generated
  ``stores.js`` shim. Methods translate to Ragot's ``createStateStore``
  surface:

      get_state  ->  getState
      set        ->  patch       (Ragot's merge-patch matches Specter Store.set)
      update     ->  batch       (atomic mutator under store lock)
      subscribe  ->  subscribe

The bridged surface is the **intersection** of the two runtime APIs:
shallow merge writes, atomic mutators, snapshot reads, subscribe.
Path-based access is intentionally not bridged here — Specter ``Store`` is
flat. A future ``model()`` factory will bridge Specter ``Model`` to
Ragot's path-style API on the same principles.
"""

from __future__ import annotations

from typing import Any, Callable, Optional


# Module-level registry of every store declared via ``store(...)``.
# The build pipeline iterates this after route discovery (which transitively
# imports any modules referencing stores) to:
#   1. emit the JS ``stores.js`` shim
#   2. snapshot current state into ``window.__SPRAG_STORES__`` for hydration
_STORE_REGISTRY: list["StoreBridge"] = []
_STORE_BY_NAME: dict[str, "StoreBridge"] = {}


# Translation table used by the codegen to map SPRAG store method names to
# Ragot ``createStateStore`` method names. Kept here, next to the
# StoreBridge definition, so the bridged surface and its JS counterpart
# stay in lockstep — adding a method requires updating both ends in one
# place.
STORE_METHOD_JS = {
    "get_state": "getState",
    "set": "patch",
    "update": "batch",
    "subscribe": "subscribe",
}


class StoreBridge:
    """Server-side handle to a SPRAG store. Browser-side this becomes a JS import.

    Methods on this object delegate to a Specter ``Store`` lazily created on
    first use. The same method calls, when seen by the codegen inside a
    Module/Component file, are routed to the corresponding Ragot store
    method via ``STORE_METHOD_JS``.
    """

    __slots__ = ("name", "initial", "_impl")

    def __init__(self, name: str, initial: Optional[dict] = None):
        if not isinstance(name, str) or not name:
            raise ValueError("store(name=...) requires a non-empty string")
        if initial is not None and not isinstance(initial, dict):
            raise TypeError("store(initial=...) must be a dict")
        self.name = name
        self.initial = dict(initial or {})
        self._impl = None  # lazy Specter Store

    # ---- Server-side backing store (lazy) ---------------------------------

    def _backing(self):
        if self._impl is None:
            from specter import create_store

            self._impl = create_store(self.name, dict(self.initial))
        return self._impl

    # ---- Bridged surface (matches Ragot intersection) ---------------------

    def get_state(self) -> dict:
        """Return a snapshot of current state. Mirrors Ragot ``store.getState()``."""
        return self._backing().snapshot()

    def set(self, partial: dict) -> dict:
        """Shallow merge into current state. Mirrors Ragot ``store.patch(partial)``."""
        store = self._backing()
        store.set(partial)
        return store.snapshot()

    def update(self, mutator: Callable[[dict], Any]) -> dict:
        """Atomic mutate under the store lock. Mirrors Ragot ``store.batch(mutator)``."""
        store = self._backing()
        store.update(mutator)
        return store.snapshot()

    def subscribe(self, fn: Callable, *, immediate: bool = False):
        """Listen for state changes. Callback receives ``(snapshot)``."""
        return self._backing().subscribe(
            lambda snapshot, store: fn(snapshot),
            immediate=immediate,
        )

    def __repr__(self):
        return f"<StoreBridge name={self.name!r}>"


def store(name: str, *, initial: Optional[dict] = None) -> StoreBridge:
    """Declare a SPRAG store: one Python object, mirrored on both runtimes.

    Usage::

        # app/stores.py
        from sprag import store

        counter = store("counter", initial={"count": 0})

        # later, in either a Service OR a Module — the same source compiles
        # to a Specter Store call on the server and a Ragot store call in
        # the browser:
        from app.stores import counter
        counter.update(lambda s: {"count": s["count"] + 1})
        counter.subscribe(lambda snapshot: print(snapshot))

    Re-declaring the same name with the same initial state is idempotent
    (returns the existing bridge); re-declaring with different initial state
    raises ``ValueError`` so that drift between two declarations is loud.
    """
    existing = _STORE_BY_NAME.get(name)
    if existing is not None:
        if existing.initial != dict(initial or {}):
            raise ValueError(
                f"SPRAG store {name!r} already declared with different initial state "
                f"(existing={existing.initial!r}, new={initial!r})"
            )
        return existing
    bridge = StoreBridge(name, initial=initial)
    _STORE_REGISTRY.append(bridge)
    _STORE_BY_NAME[name] = bridge
    return bridge


def declared_stores() -> list[StoreBridge]:
    """Return every store declared so far. Used by the build pipeline."""
    return list(_STORE_REGISTRY)


def reset_store_registry() -> None:
    """Clear the registry. Test-only — production code should never call this."""
    _STORE_REGISTRY.clear()
    _STORE_BY_NAME.clear()
