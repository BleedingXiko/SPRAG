"""SPRAG store bridge — one Python declaration, two runtimes.

A store declared via ``store(name, initial=...)`` returns a single object
that the SPRAG codegen routes to the right runtime:

- **On the server**, it wraps a Specter ``create_model(name, initial)`` —
  Specter's nested, dot-path state primitive — and delegates the bridged
  surface directly. (Specter ``Model`` is a strict superset of ``Store``:
  flat dicts work fine, and the same primitive carries nested ops when
  the user wants them.) Other server-side code (Controllers, Workers,
  Services) uses the bridge as plain Python.

- **In a browser-side Module/Component**, the codegen sees the imported
  ``StoreBridge`` reference and emits a JS import from the generated
  ``stores.js`` shim. The shim wraps Ragot ``createStateStore`` in a
  matching bridge object so the same method names work on both sides:

      get        ->  bridge.get        (path-style or full snapshot)
      set        ->  bridge.set        (dot-path write)
      patch      ->  bridge.patch      (shallow merge at root)
      update     ->  bridge.update     (atomic mutator under lock)
      delete     ->  bridge.delete     (delete a nested path)
      clear      ->  bridge.clear      (reset to empty)
      reset      ->  bridge.reset      (clear + re-seed from initial)
      snapshot   ->  bridge.snapshot   (deep snapshot of full state)
      get_state  ->  bridge.getState   (alias of snapshot for ergonomics)
      subscribe  ->  bridge.subscribe  (selector + immediate options)
      listen     ->  bridge.listen     (path-scoped subscribe shorthand)
      select     ->  bridge.select     (memoized derived read)

The bridged surface is intentionally Ragot-shaped — the JS shim fills the
gaps Ragot's store does not expose directly (``delete``, ``clear``,
``select``-as-memo) so the SPRAG user never has to learn Ragot's method
names. The translation table used by the codegen is identity for the
bridge methods; only ``get_state -> getState`` is renamed to match Ragot's
existing ``getState``.

This is the **single** SPRAG cross-runtime state primitive. ``store(...)``
is a framework bridge backed by Specter ``Model`` on the server and the
generated Ragot store shim in the browser. If an author intentionally
wants to drop below SPRAG, the raw Specter ``Model`` / ``Store`` /
``create_model`` / ``create_store`` exports remain the escape hatch.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable, Optional, Union

logger = logging.getLogger(__name__)


# Module-level registry of every store declared via ``store(...)``.
# The build pipeline iterates this after route discovery (which transitively
# imports any modules referencing stores) to:
#   1. emit the JS ``stores.js`` shim
#   2. snapshot current state into ``window.__SPRAG_PAYLOAD__.stores`` for hydration
_STORE_REGISTRY: list["StoreBridge"] = []
_STORE_BY_NAME: dict[str, "StoreBridge"] = {}


# Translation table used by the codegen to map SPRAG store method names to
# their JS bridge counterparts. Kept here, next to the StoreBridge
# definition, so the bridged surface and its JS shim stay in lockstep —
# adding a method requires updating both ends in one place. The table is
# nearly identity by design: the JS shim wraps Ragot ``createStateStore``
# in a bridge object whose methods match these names exactly so SPRAG users
# never have to learn Ragot's store method names.
STORE_METHOD_JS = {
    "get": "get",
    "set": "set",
    "patch": "patch",
    "update": "update",
    "delete": "delete",
    "clear": "clear",
    "reset": "reset",
    "snapshot": "snapshot",
    "get_state": "getState",
    "subscribe": "subscribe",
    "listen": "listen",
    "select": "select",
}


# Methods on a StoreBridge whose call sites should compile their kwargs
# into a trailing JS options object literal rather than positional args.
# ``subscribe`` is the canonical case: SPRAG (and Specter) take
# ``subscribe(fn, *, selector=None, immediate=False)`` while the JS
# bridge takes ``subscribe(listener, options)``.
STORE_METHODS_OPTIONS_KWARGS = {"subscribe"}


class StoreBridge:
    """Server-side handle to a SPRAG store. Browser-side this becomes a JS import.

    Methods on this object delegate to a Specter ``Model`` lazily created on
    first use. The same method calls, when seen by the codegen inside a
    Module/Component file, are routed to the corresponding bridge method on
    the JS side via ``STORE_METHOD_JS``.
    """

    __slots__ = ("name", "initial", "debug", "_impl")

    def __init__(self, name: str, initial: Optional[dict] = None, *, debug: bool = False):
        if not isinstance(name, str) or not name:
            raise ValueError(
                "store(name=...) expects a non-empty string store name; "
                f"got {name!r}."
            )
        if initial is not None and not isinstance(initial, dict):
            raise TypeError(
                "store(initial=...) expects a dict or None; "
                f"got {type(initial).__name__}: {initial!r}."
            )
        self.name = name
        self.initial = dict(initial or {})
        self.debug = bool(debug)
        self._impl = None  # lazy Specter Model

    # ---- Server-side backing store (lazy) ---------------------------------

    def _backing(self):
        if self._impl is None:
            from specter import create_model

            self._impl = create_model(self.name, dict(self.initial))
        return self._impl

    # ---- Debug logging -----------------------------------------------------

    def _log_mutation(self, method: str, *args):
        if not self.debug:
            return
        detail = ", ".join(repr(a) for a in args)
        logger.info("[SPRAG store:%s] %s(%s)", self.name, method, detail)

    # ---- Bridged surface --------------------------------------------------

    def get(self, path=None, default=None):
        """Read a nested path or the full state. Mirrors ``bridge.get`` on the JS side."""
        if path is None:
            return self._backing().snapshot()
        return self._backing().get(path, default)

    def get_state(self) -> dict:
        """Return a full snapshot. Mirrors Ragot ``store.getState()``."""
        return self._backing().snapshot()

    def snapshot(self) -> dict:
        """Return a deep snapshot of the entire store. Mirrors ``bridge.snapshot``."""
        return self._backing().snapshot()

    def set(self, path, value):
        """Set a nested path. Mirrors ``bridge.set(path, value)`` on the JS side."""
        self._log_mutation("set", path, value)
        return self._backing().set(path, value)

    def patch(self, partial: dict) -> dict:
        """Shallow merge at the root. Mirrors ``bridge.patch(partial)`` on the JS side."""
        self._log_mutation("patch", partial)
        return self._backing().patch(partial)

    def update(self, mutator: Callable[[dict], Any]) -> dict:
        """Atomic mutate under the store lock. Mirrors ``bridge.update(mutator)`` on the JS side."""
        self._log_mutation("update", "<mutator>")
        return self._backing().update(mutator)

    def delete(self, path):
        """Delete a nested path if present. Mirrors ``bridge.delete(path)`` on the JS side."""
        self._log_mutation("delete", path)
        return self._backing().delete(path)

    def clear(self) -> None:
        """Reset the store to an empty dict. Mirrors ``bridge.clear()`` on the JS side."""
        self._log_mutation("clear")
        self._backing().clear()

    def reset(self) -> dict:
        """Clear the store and re-seed from the declared initial state.

        Shorthand for ``store.clear(); store.patch(store.initial)`` — the
        single operation that brings a store back to its known-good state.
        """
        self._log_mutation("reset")
        self._backing().clear()
        if self.initial:
            return self._backing().patch(dict(self.initial))
        return {}

    def subscribe(
        self,
        fn: Callable,
        *,
        selector: Optional[Callable] = None,
        immediate: bool = False,
    ):
        """Listen for state changes, optionally narrowed by a selector.

        Mirrors ``bridge.subscribe(listener, {selector, immediate})`` on
        the JS side. When ``selector`` is provided the callback only fires
        if the selected slice changes.
        """
        return self._backing().subscribe(
            lambda snapshot, _model: fn(snapshot),
            selector=selector,
            immediate=immediate,
        )

    def listen(self, path: str, fn: Callable) -> Callable:
        """Subscribe to changes at a specific path. Sugar for a path-selector subscribe.

        Instead of::

            session.subscribe(
                lambda val: handle(val),
                selector=lambda s: s["prefs"]["theme"],
            )

        Write::

            session.listen("prefs.theme", handle)

        The callback receives the value at ``path`` (not the full snapshot).
        Returns the unsubscribe function.
        """
        return self.subscribe(fn, selector=path, immediate=False)

    def select(self, selector: Union[str, Callable], default: Any = None) -> Any:
        """Read derived state via a path string or selector callable.

        Mirrors ``bridge.select`` on the JS side, which memoizes selector
        callables via Ragot's ``createSelector`` keyed by function identity
        so repeated reads with a stable selector reference are cheap.
        """
        return self._backing().select(selector, default=default)

    def __repr__(self):
        return f"<StoreBridge name={self.name!r}>"


def store(name: str, *, initial: Optional[dict] = None, debug: bool = False) -> StoreBridge:
    """Declare a SPRAG store: one Python object, mirrored on both runtimes.

    Usage::

        # app/stores.py
        from sprag import store

        session = store("session", initial={
            "user": {"name": "alice"},
            "counter": 0,
        })

        # later, in either a Service OR a Module — the same source compiles
        # to a Specter Model call on the server and a Ragot bridge call in
        # the browser:
        from app.stores import session
        session.set("user.name", "bob")
        session.subscribe(
            lambda user: print(user),
            selector=lambda s: s["user"],
            immediate=True,
        )

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
        if debug:
            existing.debug = True
        return existing
    bridge = StoreBridge(name, initial=initial, debug=debug)
    _STORE_REGISTRY.append(bridge)
    _STORE_BY_NAME[name] = bridge
    return bridge


def declared_stores() -> list[StoreBridge]:
    """Return every store declared so far. Used by the build pipeline."""
    return list(_STORE_REGISTRY)


def store_fingerprint(stores: Optional[list[StoreBridge]] = None) -> str:
    """Return a stable fingerprint for the declared store contract.

    The fingerprint is intentionally derived from store names plus declared
    initial snapshots, not the current live values. It is used by dev-time
    hot reload restore logic to invalidate cached browser snapshots when the
    store surface changes shape between rebuilds.
    """
    stores = stores if stores is not None else declared_stores()
    payload = [
        {"name": bridge.name, "initial": bridge.initial}
        for bridge in sorted(stores, key=lambda bridge: bridge.name)
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def reset_store_registry() -> None:
    """Clear the registry. Test-only — production code should never call this."""
    _STORE_REGISTRY.clear()
    _STORE_BY_NAME.clear()
