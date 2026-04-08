"""Server-side SPRAG surface over SPECTER."""

from __future__ import annotations

import inspect

from specter import Controller as SPECTERController
from specter import Field, Outcome, Schema
from specter import Service as SPECTERService
from specter import registry

# -- HTTP / routing ----------------------------------------------------------
from specter import HTTPError, Router, expect_json, json_endpoint, require_fields, route

# -- Operations --------------------------------------------------------------
from specter import Operation, OperationError

# -- State -------------------------------------------------------------------
from specter import Cache, Model, Store, create_cache, create_model, create_store

# -- Communication -----------------------------------------------------------
from specter import bus

# -- Workers -----------------------------------------------------------------
from specter import QueueService

# -- Realtime ----------------------------------------------------------------
from specter import Handler, SocketIngress

# -- System ------------------------------------------------------------------
from specter import ManagedProcess, Watcher, WatcherError, start_process

# -- Orchestration -----------------------------------------------------------
from specter import ServiceManager, boot


def _server_only(name):
    """Raise on browser-only primitives invoked in a Service/Controller."""
    raise RuntimeError(
        f"sprag.{name} is browser-only — there is no DOM/socket surface "
        "on a server-side Service or Controller. The stub exists so the "
        "error is loud instead of an AttributeError."
    )


class Service(SPECTERService):
    """SPRAG Service — Specter ``Service`` plus the cross-runtime bridge.

    SPRAG's pitch is "one Python language that mirrors both runtimes 1:1".
    The intersection of ``sprag.Module`` and ``sprag.Service`` is the
    canonical symmetric surface — the same call shape works on either side:

    - ``self.listen(event, fn)`` / ``self.emit(event, data)``
    - ``self.set_state(partial)`` / ``self.watch_state(fn)``
    - ``self.subscribe(store, fn)``  (auto-cleanup-tied store subscription)
    - ``self.timeout(fn, seconds)`` / ``self.interval(fn, seconds)``
    - ``self.add_cleanup(fn)`` / ``self.adopt(child)``

    Specter ``Service`` already provides all of those except ``set_state``,
    ``watch_state``, and the 2-arg form of ``subscribe`` — which Specter
    expresses by going through the underlying ``Store`` (``self.state.set``,
    ``self.state.watch``, ``store.subscribe``). The shims below adapt those
    to the Module-shaped surface so cross-runtime user code reads the same
    on both sides.
    """

    # ---- Cross-runtime state bridge (mirrors Module shape) ----------------
    #
    # ``set_state(partial)`` is provided natively by Specter Service with the
    # exact Module shape (shallow merge into ``self.state``) so it passes
    # through without an override. The two adapters below close the remaining
    # gap: Specter's state callbacks are ``fn(state, service)`` (two args),
    # while the SPRAG cross-runtime contract — matching the Module side — is
    # ``fn(snapshot)`` (one arg).

    def watch_state(self, fn):
        """Watch local state changes. Mirrors ``Module.watch_state``.

        Adapts Specter's ``watch(fn)`` (callback ``(state, service)``) to the
        SPRAG one-arg ``fn(snapshot)`` shape used on the browser side. Fires
        immediately with the current snapshot at registration time and is
        auto-unsubscribed on Service teardown.

        Goes through ``SPECTERService.subscribe`` directly rather than
        ``self.watch`` so the SPRAG ``subscribe`` override below does not
        recursively re-adapt the callback.
        """
        return SPECTERService.subscribe(
            self,
            lambda state, _service: fn(state),
            immediate=True,
            owner=self,
        )

    def subscribe(self, target, fn=None, *, immediate=False, owner=None):
        """Two shapes — pick whichever the situation needs:

        - ``subscribe(fn)`` — subscribe to self-state changes. The callback
          receives ``(snapshot)``, matching Module's one-arg convention.
          (Specter's native callback shape is ``(state, service)``; the
          adapter strips the second arg.)
        - ``subscribe(store, fn)`` — subscribe to a separate store with
          auto-cleanup tied to this Service. ``store`` may be a SPRAG
          ``StoreBridge`` or any object with a ``.subscribe`` method (Specter
          ``Store`` / ``Model``). This is the cross-runtime symmetry shape:
          the same call works on ``sprag.Module``.
        """
        if fn is None:
            # 1-arg: self-state subscribe, adapted to fn(snapshot).
            user_fn = target
            return super().subscribe(
                lambda state, _service: user_fn(state),
                immediate=immediate,
                owner=owner,
            )
        if not hasattr(target, "subscribe"):
            raise TypeError(
                f"subscribe(store, fn): first argument must be a store-like "
                f"object, got {type(target).__name__}"
            )
        unsub = target.subscribe(fn)
        if callable(unsub):
            self.add_cleanup(unsub)
        return unsub

    # ---- Browser-only stubs (loud errors instead of AttributeError) -------

    def on(self, *args, **kwargs):
        _server_only("Service.on")

    def off(self, *args, **kwargs):
        _server_only("Service.off")

    def delegate(self, *args, **kwargs):
        _server_only("Service.delegate")

    def on_socket(self, *args, **kwargs):
        _server_only("Service.on_socket")

    def off_socket(self, *args, **kwargs):
        _server_only("Service.off_socket")

    def call_action(self, *args, **kwargs):
        _server_only("Service.call_action")


class Controller(SPECTERController):
    """SPRAG controller with route/action convenience."""

    route = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.request = None
        self.app = None

    @classmethod
    def sprag_actions(cls):
        actions = {}
        for attr_name in dir(cls):
            value = getattr(cls, attr_name)
            if callable(value) and getattr(value, "_sprag_action", False):
                meta = getattr(value, "_sprag_action_meta", None) or {}
                action_name = meta.get("name", attr_name)
                actions[action_name] = value
        return actions

    def service(self, name):
        """Resolve a named service from the Specter registry."""
        return registry.require(name)

    def load(self):
        """Return route data for SSR or browser boot."""
        return {}


class ActionDispatchError(RuntimeError):
    """Structured action-dispatch failure for SPRAG bridge responses."""

    def __init__(self, message, *, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def dispatch_controller_action(pages, *, route_path, action_name, payload=None, request=None, app=None, mounts=None):
    """Dispatch a declared controller action for a discovered SPRAG surface."""
    if not route_path:
        raise ActionDispatchError("Missing SPRAG route path.", status_code=400)
    if not action_name:
        raise ActionDispatchError("Missing SPRAG action name.", status_code=400)

    controller_class = _resolve_surface_controller(pages, mounts or [], route_path)
    actions = controller_class.sprag_actions()
    action = actions.get(action_name)
    if action is None:
        raise ActionDispatchError(
            f"Unknown action {action_name!r} for route {route_path!r}.",
            status_code=404,
        )

    controller = controller_class()
    controller.request = request
    controller.app = app
    bound_action = getattr(controller, action_name)

    meta = getattr(action, "_sprag_action_meta", None) or {}
    schema = meta.get("schema")
    if schema is not None and isinstance(payload, dict):
        outcome = schema.validate(payload)
        if not outcome.ok:
            raise ActionDispatchError(
                f"Validation failed for action {action_name!r}: {outcome.error}",
                status_code=400,
            )
        payload = outcome.value

    try:
        args, kwargs = _bind_action_payload(bound_action, payload)
    except TypeError as exc:
        raise ActionDispatchError(
            f"Invalid payload for action {action_name!r}: {exc}",
            status_code=400,
        ) from exc

    try:
        result = bound_action(*args, **kwargs)
    except ActionDispatchError:
        raise
    except Exception as exc:
        raise ActionDispatchError(
            f"{exc.__class__.__name__}: {exc}",
            status_code=500,
        ) from exc

    if isinstance(result, Outcome):
        return result
    return Outcome.success(result)


def _resolve_surface_controller(pages, mounts, route_path):
    for _module_name, page in pages:
        if page.path == route_path:
            return page.controller
    for _module_name, mount in mounts:
        if mount.path == route_path and mount.boot is not None:
            return mount.boot
    raise ActionDispatchError(f"Unknown route {route_path!r}.", status_code=404)


def _resolve_route_page(pages, route_path):
    for _module_name, page in pages:
        if page.path == route_path:
            return page
    raise ActionDispatchError(f"Unknown route {route_path!r}.", status_code=404)


def _bind_action_payload(bound_action, payload):
    signature = inspect.signature(bound_action)
    if payload is None:
        binding = signature.bind()
    elif isinstance(payload, dict):
        binding = signature.bind(**payload)
    elif isinstance(payload, list):
        binding = signature.bind(*payload)
    else:
        binding = signature.bind(payload)
    return binding.args, binding.kwargs
