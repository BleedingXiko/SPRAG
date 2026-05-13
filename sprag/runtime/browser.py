"""Browser-side SPRAG authoring primitives.

These classes are Python stubs for code that SPRAG compiles to JavaScript.
Runtime signatures live beside them in ``browser.pyi`` for editor support.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# Justified decorators (each one earns its keep)
# ---------------------------------------------------------------------------


def debounce(seconds):
    """Run the decorated method after ``seconds`` of quiet."""
    if not isinstance(seconds, (int, float)) or seconds < 0:
        raise ValueError(
            "debounce(seconds) expects a non-negative int or float number of seconds; "
            f"got {seconds!r}."
        )

    def decorator(fn):
        fn._sprag_debounce_ms = int(seconds * 1000)
        return fn
    return decorator


def throttle(seconds):
    """Run the decorated method at most once per ``seconds``."""
    if not isinstance(seconds, (int, float)) or seconds < 0:
        raise ValueError(
            "throttle(seconds) expects a non-negative int or float number of seconds; "
            f"got {seconds!r}."
        )

    def decorator(fn):
        fn._sprag_throttle_ms = int(seconds * 1000)
        return fn
    return decorator


def animate(class_name="is-visible"):
    """Animate a Component by toggling ``class_name`` on mount/unmount."""
    def decorator(cls):
        cls._sprag_animate = {"class_name": class_name}
        return cls
    return decorator


def virtual_scroll(
    *,
    chunk,
    max_chunks=5,
    initial_chunks=1,
    root=None,
    root_margin="1200px 0px",
    container_class=None,
    pool_size=0,
    child_pool_size=0,
    axis="auto",
):
    """Virtualize a Component list. Define ``chunk(i)`` and ``total()``."""
    if not isinstance(chunk, int) or chunk <= 0:
        raise ValueError(
            "virtual_scroll(chunk=...) expects a positive integer chunk size; "
            f"got {chunk!r}."
        )

    def decorator(cls):
        cls._sprag_virtual_scroll = {
            "chunk_size": int(chunk),
            "max_chunks": int(max_chunks),
            "initial_chunks": int(initial_chunks),
            "root": root,
            "root_margin": root_margin,
            "container_class": container_class,
            "pool_size": int(pool_size),
            "child_pool_size": int(child_pool_size),
            "axis": axis,
        }
        return cls
    return decorator


def infinite_scroll(
    *,
    at,
    root=None,
    root_margin="600px",
    top_at=None,
    visible_chunks=None,
):
    """Call the decorated method when the ``at`` sentinel enters view."""
    if not isinstance(at, str) or not at:
        raise ValueError(
            "infinite_scroll(at=...) expects a non-empty CSS selector or ref name string; "
            f"got {at!r}."
        )

    def decorator(fn):
        fn._sprag_infinite_scroll = {
            "at": at,
            "root": root,
            "root_margin": root_margin,
            "top_at": top_at,
            "visible_chunks": visible_chunks,
        }
        return fn
    return decorator


class RefDescriptor:
    """Descriptor behind ``ref(selector)``."""

    def __init__(self, selector):
        if not isinstance(selector, str) or not selector:
            raise ValueError(
                "ref(selector) expects a non-empty CSS selector string; "
                f"got {selector!r}."
            )
        self.selector = selector
        self._name = None

    def __set_name__(self, owner, name):
        self._name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        refs = getattr(obj, "refs", None)
        if isinstance(refs, dict) and self._name is not None:
            return refs.get(self._name)
        return None


def ref(selector):
    """Capture a DOM element into ``self.refs.<name>`` on mount."""
    return RefDescriptor(selector)


# ---------------------------------------------------------------------------
# Browser-side authoring classes
#
# These mirror the Ragot ``Module`` and ``Component`` classes 1:1. Every method
# below is a stub: it raises ``RuntimeError`` if invoked in plain Python. They
# exist so that:
#
#   - IDE autocomplete shows the full mirrored surface
#   - Type checkers can verify ``self.X(...)`` calls in user code
#   - The codegen has a stable contract to compile against
#
# Source-of-truth: ragot/core/lifecycle.js (Module ~398-865, Component ~906-1221).
# ---------------------------------------------------------------------------


def _browser_only(name):
    raise RuntimeError(
        f"sprag.{name} is only available in generated browser code; "
        "this stub exists for IDE/type-checking support."
    )


class _JSNamespaceStub:
    """Generated-only browser/import namespace."""

    def __init__(self, path: str):
        self._path = path

    def __getattr__(self, name):
        return _JSNamespaceStub(f"{self._path}.{name}")

    def __getitem__(self, key):
        return _JSNamespaceStub(f"{self._path}[{key!r}]")

    def __call__(self, *args, **kwargs):
        _browser_only(self._path)

    def __bool__(self):
        _browser_only(self._path)

    def __repr__(self):
        return f"<generated-only {self._path}>"


browser = _JSNamespaceStub("browser")
imports = _JSNamespaceStub("imports")


def createStateStore(initial_state=None, options=None):
    """Create a browser state store with proxy-tracked updates."""
    _browser_only("createStateStore")


def createSelector(input_selectors, result_func):
    """Create a memoized selector from store-derived inputs."""
    _browser_only("createSelector")


@dataclass
class Module:
    """Browser-side logic for an interactive surface.

    Subclass this when you need DOM events, server actions, sockets, timers,
    stores, or non-visual state. Put setup in ``on_start()`` and cleanup in
    ``on_stop()``. The usual first calls are ``self.delegate(...)``,
    ``self.set_state(...)``, ``self.call_action(...)``, and ``self.on_socket(...)``.

    In ``Screen.render()``, get the typed instance with
    ``module = self.module(MyModule)`` and return ``hydrate(MyComponent, module=module)``.
    """

    screen: Optional["Screen"] = None
    state: dict = field(default_factory=dict)
    stores: list = field(default_factory=list)
    # Populated by the runtime after adoption. Stubs so Python-side code
    # (and IDEs) can reference them without surprise.
    element: Optional[object] = None
    component: Optional[object] = None

    # -- DOM events ---------------------------------------------------------
    def on(self, target, event, fn):
        """Bind a DOM event listener with auto-cleanup on teardown."""
        _browser_only("Module.on")

    def off(self, target, event, fn):
        """Remove a DOM event listener bound via ``on``."""
        _browser_only("Module.off")

    def delegate(self, target, event, selector, fn):
        """Bind a delegated DOM event listener with auto-cleanup."""
        _browser_only("Module.delegate")

    # -- Bus (mirrored on Service) -----------------------------------------
    def listen(self, event, fn):
        """Subscribe to a bus event. **Same shape on Specter ``Service``.**"""
        _browser_only("Module.listen")

    def emit(self, event, data=None):
        """Publish a bus event. **Same shape on Specter ``Service``.**"""
        _browser_only("Module.emit")

    # -- Managed timers (seconds; codegen ×1000 for Ragot) -----------------
    def timeout(self, fn, seconds):
        """One-shot timer with auto-cancel on teardown. ``seconds`` is a float."""
        _browser_only("Module.timeout")

    def interval(self, fn, seconds):
        """Recurring timer with auto-cancel on teardown. ``seconds`` is a float."""
        _browser_only("Module.interval")

    def clear_timeout(self, handle):
        """Cancel a pending ``timeout``."""
        _browser_only("Module.clear_timeout")

    def clear_interval(self, handle):
        """Cancel a running ``interval``."""
        _browser_only("Module.clear_interval")

    # -- Lifecycle ownership (mirrored on Service) -------------------------
    def add_cleanup(self, fn):
        """Register a cleanup callback to fire on teardown."""
        _browser_only("Module.add_cleanup")

    def adopt(self, child):
        """Take ownership of a child resource (cleaned up with this module)."""
        _browser_only("Module.adopt")

    def adopt_component(self, component):
        """Take ownership of a child Component."""
        _browser_only("Module.adopt_component")

    # -- State (mirrored on Service via set_state/subscribe) ---------------
    def set_state(self, new_state):
        """Merge into the local state dict."""
        self.state = {**self.state, **new_state}
        return self.state

    def batch_state(self, fn):
        """Coalesce multiple ``set_state`` calls into a single notification."""
        _browser_only("Module.batch_state")

    def watch_state(self, fn):
        """Subscribe to local state changes; returns an unsubscribe function."""
        _browser_only("Module.watch_state")

    def subscribe(self, store, fn):
        """Subscribe to a store. **Same shape on Specter ``Service``.**"""
        _browser_only("Module.subscribe")

    def create_selector(self, deps, fn):
        """Build a memoised derived value over store dependencies."""
        _browser_only("Module.create_selector")

    def provider(self, name):
        """Resolve a named page/mount browser provider from Ragot's registry."""
        _browser_only("Module.provider")

    # -- Server push --------------------------------------------------------
    def on_socket(self, event, fn):
        """Subscribe to a socket event from the server."""
        _browser_only("Module.on_socket")

    def off_socket(self, event, fn):
        """Unsubscribe from a socket event."""
        _browser_only("Module.off_socket")

    def emit_socket(self, event, payload=None):
        """Emit a websocket event through SPRAG's shared runtime socket."""
        _browser_only("Module.emit_socket")

    def refetch_on_socket(self, event="sprag:refetch", action=None, on_result=None, on_error=None):
        """Subscribe to a socket event and refetch authoritative action state."""
        _browser_only("Module.refetch_on_socket")

    def join_topic(self, topic):
        """Join a named socket topic on the shared runtime socket."""
        _browser_only("Module.join_topic")

    def leave_topic(self, topic):
        """Leave a named socket topic on the shared runtime socket."""
        _browser_only("Module.leave_topic")

    # -- Actions (SPRAG-specific bridge to controllers) --------------------
    def call_action(self, name, payload=None):
        """Call a server-side controller action; returns a Promise in JS."""
        _browser_only("Module.call_action")

    def action_error_message(self, error, fallback=None):
        """Resolve a user-facing message from a rejected action/upload error."""
        _browser_only("Module.action_error_message")

    def form_data(self, source):
        """Read a form or form event into a plain JSON-safe dict."""
        _browser_only("Module.form_data")

    def upload_form(self, name, source, on_progress=None):
        """Submit a multipart form upload with progress; returns a Promise in JS."""
        _browser_only("Module.upload_form")

    def upload(self, name, file, payload=None, on_progress=None):
        """Programmatic file upload (drag-drop, File API); returns a Promise in JS.

        Accepts a ``File``/``Blob`` directly. Automatically uses chunked
        upload for files above the negotiated threshold.
        """
        _browser_only("Module.upload")

    def navigate(self, target, options=None):
        """Navigate the browser to another route or URL."""
        _browser_only("Module.navigate")

    def set_metadata(self, metadata, options=None):
        """Update the browser document title/meta/canonical tags."""
        _browser_only("Module.set_metadata")


@dataclass
class Component:
    """Browser-side DOM renderer for an interactive surface.

    Subclass this for markup and local DOM behavior. Implement
    ``render(props=None)`` to return a ``ui.*`` tree. Use ``self.props`` for
    inputs, ``self.state`` for local state, ``self.refs`` for ``ref(...)``
    captures, and ``self.set_state(...)`` to re-render.

    Pair with a ``Module`` when server calls, sockets, timers, or shared
    state belong outside the visual component.
    """

    props: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    refs: dict = field(default_factory=dict)
    virtual_scroll: Optional[object] = None

    # -- Required user override --------------------------------------------
    def render(self, props=None):
        """Return a UI tree for this component. Must be implemented by user code."""
        raise NotImplementedError("SPRAG components must implement render().")

    # -- Lifecycle ----------------------------------------------------------
    def mount(self, parent):
        """Mount this component into ``parent``."""
        _browser_only("Component.mount")

    def mount_before(self, sibling):
        """Mount this component before ``sibling``."""
        _browser_only("Component.mount_before")

    def unmount(self):
        """Programmatically remove this component from the DOM."""
        _browser_only("Component.unmount")

    # -- State --------------------------------------------------------------
    def set_state(self, new_state):
        """rAF-batched morphDOM re-render. Mirrors Ragot ``Component.setState``."""
        self.state = {**self.state, **new_state}
        return self.state

    def set_state_sync(self, new_state):
        """Immediate morphDOM re-render. Mirrors Ragot ``Component.setStateSync``.

        Distinct from ``set_state``: ``set_state`` schedules an rAF-batched
        re-render, while ``set_state_sync`` cancels any pending rAF and
        applies the update synchronously. Use sparingly — the rAF batch is
        almost always what you want.
        """
        self.state = {**self.state, **new_state}
        return self.state

    # -- DOM events ---------------------------------------------------------
    def on(self, target, event, fn):
        """Bind a DOM event listener with auto-cleanup on teardown."""
        _browser_only("Component.on")

    def off(self, target, event, fn):
        """Remove a DOM event listener bound via ``on``."""
        _browser_only("Component.off")

    def delegate(self, target, event, selector, fn):
        """Bind a delegated DOM event listener with auto-cleanup."""
        _browser_only("Component.delegate")

    # -- Bus ----------------------------------------------------------------
    def listen(self, event, fn):
        """Subscribe to a bus event."""
        _browser_only("Component.listen")

    def emit(self, event, data=None):
        """Publish a bus event."""
        _browser_only("Component.emit")

    # -- Managed timers (seconds; codegen ×1000 for Ragot) -----------------
    def timeout(self, fn, seconds):
        """One-shot timer with auto-cancel on teardown. ``seconds`` is a float."""
        _browser_only("Component.timeout")

    def interval(self, fn, seconds):
        """Recurring timer with auto-cancel on teardown. ``seconds`` is a float."""
        _browser_only("Component.interval")

    # -- Lifecycle ownership ------------------------------------------------
    def add_cleanup(self, fn):
        """Register a cleanup callback to fire on teardown."""
        _browser_only("Component.add_cleanup")

    def adopt(self, child):
        """Take ownership of a child resource."""
        _browser_only("Component.adopt")

    def create_selector(self, deps, fn):
        """Build a memoised derived value over store dependencies."""
        _browser_only("Component.create_selector")

    def form_data(self, source):
        """Read a form or form event into a plain JSON-safe dict."""
        _browser_only("Component.form_data")

    def upload_form(self, name, source, on_progress=None):
        """Submit a multipart form upload with progress; returns a Promise in JS."""
        _browser_only("Component.upload_form")

    def upload(self, name, file, payload=None, on_progress=None):
        """Programmatic file upload (drag-drop, File API); returns a Promise in JS.

        Accepts a ``File``/``Blob`` directly. Automatically uses chunked
        upload for files above the negotiated threshold.
        """
        _browser_only("Component.upload")

    def action_error_message(self, error, fallback=None):
        """Resolve a user-facing message from a rejected action/upload error."""
        _browser_only("Component.action_error_message")

    def navigate(self, target, options=None):
        """Navigate the browser to another route or URL."""
        _browser_only("Component.navigate")

    def set_metadata(self, metadata, options=None):
        """Update the browser document title/meta/canonical tags."""
        _browser_only("Component.set_metadata")


@dataclass
class Screen:
    """Server-rendered view class for a page.

    Declare browser modules with ``modules = [MyModule]``. In ``render(data)``,
    call ``self.module(MyModule)`` to get the typed instance, seed it with
    ``module.set_state(data)``, then return ``hydrate(Component, module=module)``
    or plain ``ui.*`` markup.
    """

    data: dict = field(default_factory=dict)
    modules: list = field(default_factory=list)

    def __post_init__(self):
        self._module_instances = {}
        module_types = getattr(self.__class__, "modules", [])
        for module_type in module_types:
            self._module_instances[module_type] = module_type(screen=self)

    def module(self, module_type):
        return self._module_instances[module_type]

    def render(self, data):
        raise NotImplementedError("SPRAG screens must implement render(data).")

    @classmethod
    def sample_data(cls):
        return {}


@dataclass(frozen=True)
class SSRMount:
    component: object
    props: dict


@dataclass(frozen=True)
class HydrateMount:
    component: object
    module: Optional[object] = None
    props: dict = field(default_factory=dict)


def ssr(component, **props):
    return SSRMount(component=component, props=props)


def hydrate(component, *, module=None, props=None):
    """Mount a browser Component, optionally owned by a Module.

    If ``props`` is omitted and ``module`` is provided, the component starts
    with the module's current state.
    """
    if props is None and module is not None:
        props = dict(getattr(module, "state", {}) or {})
    return HydrateMount(component=component, module=module, props=props or {})
