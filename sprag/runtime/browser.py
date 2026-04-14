"""Web authoring primitives that compile to Ragot.

SPRAG mirrors two runtimes 1:1 — Specter (server-side ``Service``/``Controller``)
and Ragot (browser-side ``Module``/``Component``). The same imperative API
surface — ``self.listen``, ``self.emit``, ``self.on``, ``self.timeout``,
``self.set_state``, ``self.subscribe``, ``self.add_cleanup``, etc. — is
written in Python and routed to the right runtime by the codegen.

The classes in this module are the **browser-side** authoring stubs. Their
methods do nothing useful when called in plain Python: they raise
``RuntimeError``. They exist so that:

1. IDE autocomplete and type-checking work for SPRAG authors.
2. The codegen has a stable surface to compile against.
3. The "one language, two runtimes" symmetry is visible in the source.

Decorators in this module exist **only** when they encode a transformation
that neither runtime provides as a primitive. They never duplicate an
imperative method that already exists on ``Module`` / ``Component`` /
``Service``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# Justified decorators (each one earns its keep)
# ---------------------------------------------------------------------------


def debounce(seconds):
    """Decorator: coalesce rapid calls into a single call after ``seconds`` of quiet.

    Compiles to a trailing-edge debounce wrapper that uses Ragot's
    ``this.timeout(...)`` so the pending timer is auto-cancelled on module
    teardown. Per-method state is stored on ``this._sprDebounce`` keyed by
    method name.

    The Python signature takes **seconds** (float) — matching ``time.sleep``,
    ``datetime.timedelta``, and the Specter convention. The codegen multiplies
    by 1000 when emitting the Ragot ``timeout`` call.

    Justification: neither runtime provides debounce as a primitive. The
    decorator wraps the method body with state tracking + auto-cancelling
    cleanup, which the imperative API cannot express in one line.
    """
    if not isinstance(seconds, (int, float)) or seconds < 0:
        raise ValueError("debounce(seconds) requires a non-negative number")

    def decorator(fn):
        fn._sprag_debounce_ms = int(seconds * 1000)
        return fn
    return decorator


def throttle(seconds):
    """Decorator: leading-edge throttle — fires at most once per ``seconds``.

    Compiles to a timestamp-based guard; no timer is scheduled, so there is
    nothing to clean up. State is stored on ``this._sprThrottle`` keyed by
    method name.

    The Python signature takes **seconds** (float). The codegen multiplies by
    1000 when emitting the Ragot ``Date.now()`` comparison.

    Justification: same as ``@debounce`` — neither runtime provides this as a
    primitive.
    """
    if not isinstance(seconds, (int, float)) or seconds < 0:
        raise ValueError("throttle(seconds) requires a non-negative number")

    def decorator(fn):
        fn._sprag_throttle_ms = int(seconds * 1000)
        return fn
    return decorator


def animate(class_name="is-visible"):
    """Component class decorator: wrap ``mount``/``unmount`` with Ragot ``animateIn``/``animateOut``.

    Justification: structural mount/unmount transformation. ``animateIn``
    schedules an rAF class toggle, ``animateOut`` returns a promise that
    resolves on ``transitionend``. There is no imperative one-liner equivalent.
    """
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
    """Component class decorator: wrap the component in a Ragot ``VirtualScroller``.

    Justification: structural component transformation. The decorated class
    authors a normal SPRAG ``Component`` whose ``render`` returns a plain
    container; the codegen synthesises an ``onStart`` that instantiates a
    ``VirtualScroller`` against ``this.element`` and binds the user's
    ``chunk`` / ``total`` / ``measure`` / ``placeholder`` / ``recycle`` /
    ``evicted`` methods as the scroller's callbacks.

    The component must define:

    - ``chunk(self, i)`` — returns the DOM element for chunk ``i``
    - ``total(self)`` — returns the total item count

    Optional methods: ``measure``, ``placeholder``, ``recycle`` (REQUIRED if
    ``pool_size > 0``), ``evicted``.

    Decorated components get a public scroller handle at
    ``self.virtual_scroll`` in Python authoring code (emitted as
    ``this.virtualScroll`` in JS). Framework-private storage stays internal.
    """
    if not isinstance(chunk, int) or chunk <= 0:
        raise ValueError("virtual_scroll(chunk=...) must be a positive integer")

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
    """Method decorator: wire ``createInfiniteScroll`` for the host class.

    Works on both ``Module`` methods and ``Component`` methods. The decorated
    method becomes the ``onLoadMore`` callback. The host's synthesised
    ``onStart`` instantiates ``createInfiniteScroll`` against the host
    (auto-cleanup via ``addCleanup``).

    Justification: mount-time install with cleanup wiring. The imperative
    ``self.add_cleanup`` is available, but the install is structural enough —
    sentinel resolution, observer setup, and the bidirectional ``top_at``
    case — that wrapping it in a decorator removes a real footgun.

    Args:
        at: Sentinel selector. Either a CSS string (resolved at mount time
            via ``this.element.querySelector(...)``) or a string referencing
            a ``ref()`` descriptor name on the host class.
        root: Optional CSS selector for the scroll root.
        root_margin: IntersectionObserver rootMargin (default ``"600px"``).
        top_at: Optional top sentinel for bidirectional scrolling.
        visible_chunks: Optional explicit visible-chunks set.
    """
    if not isinstance(at, str) or not at:
        raise ValueError("infinite_scroll(at=...) requires a non-empty selector or ref name")

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
    """Class-level descriptor that captures a DOM element into ``self.refs.X`` on mount.

    Justification: declarative ref capture tied to mount lifecycle. Removes a
    class of bugs around *when* the lookup happens (before vs after the
    component is in the DOM). Not a method-duplicating decorator — there is
    no ``self.ref(name)`` imperative equivalent that runs at mount time.
    """

    def __init__(self, selector):
        if not isinstance(selector, str) or not selector:
            raise ValueError("ref() requires a non-empty CSS selector string")
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
    """Class-level descriptor: capture a DOM element into ``self.refs.X`` on mount.

    Usage::

        class SearchModule(Module):
            input = ref(".search-input")
            results = ref(".search-results")

            def on_start(self):
                dom.show(self.refs.results)
    """
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
    """Chainable stub for generated-only browser/import namespaces."""

    def __init__(self, path: str):
        self._path = path

    def __getattr__(self, name):
        return _JSNamespaceStub(f"{self._path}.{name}")

    def __call__(self, *args, **kwargs):
        _browser_only(self._path)

    def __bool__(self):
        _browser_only(self._path)

    def __repr__(self):
        return f"<generated-only {self._path}>"


browser = _JSNamespaceStub("browser")
imports = _JSNamespaceStub("imports")


@dataclass
class Module:
    """Browser-side Module — Python mirror of Ragot ``Module``.

    A SPRAG ``Module`` subclass is compiled into a Ragot ``Module`` subclass
    by the codegen. The methods below mirror Ragot's lifecycle/event/state
    surface; calling them in plain Python raises ``RuntimeError`` — they only
    do real work in the emitted JS.

    Methods that share a name with ``sprag.Service`` (``listen``, ``emit``,
    ``add_cleanup``, ``adopt``, ``set_state``, ``subscribe``) are SPRAG's
    cross-runtime symmetry surface: write the same call in Python, run on
    either side.

    **Hybrid ownership model.** When a Module is attached to a Component via
    SPRAG's ``hydrate(...)`` mount, the SPRAG runtime wires them up using
    Ragot's canonical ``adoptComponent(component, { sync })`` pattern: the
    Module becomes the lifecycle owner, the Component is mounted as an
    adopted child, and a ``watchState`` subscription is registered so that
    every ``self.set_state(...)`` automatically flows into
    ``self.component.set_state(...)``. **User code should only call
    ``self.set_state(...)`` — never dual-call set_state on both sides.**

    The runtime also sets:

    - ``self.element`` — the DOM element hosting the adopted component. This
      is what ``self.delegate(self.element, ...)`` should target when you
      want to bubble events up from the component's subtree.
    - ``self.component`` — back-reference to the adopted Component. Use for
      imperative access (refs, direct method calls); do NOT use it to push
      state, which is what the automatic sync handles.

    **Custom state sync.** If you need a non-trivial mapping between module
    state and component state, define a ``sync_component(self, component,
    state)`` method on your Module subclass. The runtime detects it and
    routes every state change through it instead of the default
    ``component.set_state(state)`` shallow merge.
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
    """Browser-side Component — Python mirror of Ragot ``Component``.

    A SPRAG ``Component`` subclass is compiled into a Ragot ``Component``
    subclass by the codegen. ``render(self, props)`` is the only method the
    user **must** implement; the rest are stubs.

    Note that ``Component`` does not have ``on_socket``, ``watch_state``, or
    ``adopt_component`` — those live on ``Module``. This matches Ragot exactly.
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
        """Tear down this component (override to add cleanup)."""

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
    if props is None and module is not None:
        props = dict(getattr(module, "state", {}) or {})
    return HydrateMount(component=component, module=module, props=props or {})
