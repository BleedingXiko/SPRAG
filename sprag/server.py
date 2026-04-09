"""Server-side SPRAG surface over SPECTER."""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from contextvars import ContextVar

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

from .routing import match_page_route


_UNSET = object()
_current_request = ContextVar("sprag_current_request", default=_UNSET)
_current_app = ContextVar("sprag_current_app", default=_UNSET)


@contextmanager
def controller_context(*, request=None, app=None):
    """Expose per-request state to lifecycle-owned controllers safely."""
    request_token = _current_request.set(request)
    app_token = _current_app.set(app)
    try:
        yield
    finally:
        _current_app.reset(app_token)
        _current_request.reset(request_token)


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

    def emit_socket(self, event, data=None, *, route=None, client_id=None):
        """Emit a websocket event to connected browser clients.

        Controllers default to their declared ``route`` when no explicit
        route filter is provided, which keeps per-surface socket traffic
        scoped by default.
        """
        transport = registry.resolve("socket_transport")
        if transport is None:
            return False
        if route is None:
            route = getattr(self, "route", None)
        return bool(transport.emit(event, data, route=route, client_id=client_id))

    def call_action(self, *args, **kwargs):
        _server_only("Service.call_action")


class Controller(SPECTERController):
    """SPRAG controller with route/action convenience."""

    route = None

    def __init__(self, **kwargs):
        if "name" not in kwargs:
            kwargs["name"] = getattr(self.__class__, "name", self.__class__.__name__)
        super().__init__(**kwargs)
        self._sprag_request_override = None
        self._sprag_app = None

    @property
    def request(self):
        """Current request scoped to this load/action call."""
        request = _current_request.get()
        if request is not _UNSET:
            return request
        return self._sprag_request_override

    @request.setter
    def request(self, value):
        # Compatibility for manually instantiated controllers. The SPRAG
        # runtime uses ``controller_context`` so lifecycle-owned controllers
        # do not leak request state between concurrent requests.
        self._sprag_request_override = value

    @property
    def app(self):
        """Owning SPRAG app, scoped to the current call when provided."""
        app = _current_app.get()
        if app is not _UNSET:
            return app
        return self._sprag_app

    @app.setter
    def app(self, value):
        self._sprag_app = value

    def bind_app(self, app):
        """Attach the owning SPRAG app without starting a request scope."""
        self._sprag_app = app
        return self

    def emit_socket(self, event, data=None, *, route=None, client_id=None):
        """Emit a websocket event to connected browser clients."""
        transport = registry.resolve("socket_transport")
        if transport is None:
            return False
        if route is None:
            route = getattr(self, "route", None)
        return bool(transport.emit(event, data, route=route, client_id=client_id))

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

    controller = _resolve_surface_controller(pages, mounts or [], route_path, app=app)
    controller_class = controller.__class__
    actions = controller_class.sprag_actions()
    action = actions.get(action_name)
    if action is None:
        raise ActionDispatchError(
            f"Unknown action {action_name!r} for route {route_path!r}.",
            status_code=404,
        )

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
        with controller_context(request=request, app=app):
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


def _resolve_surface_controller(pages, mounts, route_path, *, app=None):
    matched_page = match_page_route(pages, route_path)
    if matched_page is not None:
        page = matched_page.page
        if app is not None and hasattr(app, "controller_for_page"):
            return app.controller_for_page(page)
        return page.controller()
    for _module_name, mount in mounts:
        if mount.path == route_path and mount.boot is not None:
            if app is not None and hasattr(app, "controller_for_mount"):
                return app.controller_for_mount(mount)
            return mount.boot()
    raise ActionDispatchError(f"Unknown route {route_path!r}.", status_code=404)


def _resolve_route_page(pages, route_path):
    matched_page = match_page_route(pages, route_path)
    if matched_page is not None:
        return matched_page.page
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
