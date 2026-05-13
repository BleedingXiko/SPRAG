"""Type stubs for sprag.runtime.server.

Only SPRAG-defined server symbols are described here. Specter re-exports stay
owned by Specter's own type surface.
"""

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, TypeVar, Union, overload

from specter import Cache as Cache
from specter import Controller as SPECTERController
from specter import Field as Field
from specter import Handler as Handler
from specter import HTTPError as HTTPError
from specter import ManagedProcess as ManagedProcess
from specter import Model as Model
from specter import Operation as Operation
from specter import OperationError as OperationError
from specter import Outcome as Outcome
from specter import QueueService as SPECTERQueueService
from specter import Router as Router
from specter import Schema as Schema
from specter import Service as SPECTERService
from specter import ServiceManager as ServiceManager
from specter import SocketIngress as SocketIngress
from specter import Store as Store
from specter import WatcherError as WatcherError
from specter import boot as boot
from specter import bus as bus
from specter import create_cache as create_cache
from specter import create_model as create_model
from specter import create_store as create_store
from specter import expect_json as expect_json
from specter import json_endpoint as json_endpoint
from specter import registry as registry
from specter import require_fields as require_fields
from specter import route as route
from specter import start_process as start_process

F = TypeVar("F", bound=Callable[..., Any])
C = TypeVar("C", bound=type)
T = TypeVar("T")

AuthValues = Optional[Union[Iterable[str], str]]
SocketTargetMap = Mapping[str, Optional[str]]
Unsubscribe = Callable[[], Any]
TimerHandle = Any


@dataclass(frozen=True)
class ActionResult:
    """Return payload from action dispatch and browser action calls."""

    ok: bool
    value: object = ...
    error: Optional[str] = ...
    status: int = ...
    redirect: Optional["Redirect"] = ...


class Redirect(Exception):
    """Redirect response for page loads and browser actions."""

    location: str
    status: int
    replace: Optional[bool]

    def __init__(
        self,
        location: str,
        *,
        status: int = ...,
        replace: Optional[bool] = ...,
    ) -> None: ...
    @property
    def browser_replace(self) -> bool: ...
    def as_payload(self) -> dict[str, Any]: ...


def redirect(
    location: str,
    *,
    status: int = ...,
    replace: Optional[bool] = ...,
) -> Redirect: ...


@overload
def action(fn: F, *, schema: Any = ..., name: Optional[str] = ..., defer: bool = ..., derive: bool = ...) -> F:
    """Expose a Controller method to browser ``call_action(...)``."""
    ...
@overload
def action(fn: None = ..., *, schema: Any = ..., name: Optional[str] = ..., defer: bool = ..., derive: bool = ...) -> Callable[[F], F]: ...


@overload
def requires_auth(
    target: C,
    *,
    roles: AuthValues = ...,
    permissions: AuthValues = ...,
    require_active_profile: bool = ...,
    redirect_to: str = ...,
    next_param: str = ...,
) -> C: ...
@overload
def requires_auth(
    target: F,
    *,
    roles: AuthValues = ...,
    permissions: AuthValues = ...,
    require_active_profile: bool = ...,
    redirect_to: str = ...,
    next_param: str = ...,
) -> F: ...
@overload
def requires_auth(
    target: None = ...,
    *,
    roles: AuthValues = ...,
    permissions: AuthValues = ...,
    require_active_profile: bool = ...,
    redirect_to: str = ...,
    next_param: str = ...,
) -> Callable[[F], F]:
    """Guard a Controller, ``load()``, or ``@action`` with auth requirements."""
    ...


def socket_target(
    *,
    route: Optional[str] = ...,
    client_id: Optional[str] = ...,
    session_id: Optional[str] = ...,
    topic: Optional[str] = ...,
) -> dict[str, Optional[str]]: ...


class Service(SPECTERService):
    """Server-side lifecycle service.

    Use this for long-lived server state, timers, cleanup, bus events, and
    store subscriptions. Common calls are ``self.set_state(...)``,
    ``self.watch_state(...)``, ``self.subscribe(...)``, ``self.interval(...)``,
    and ``self.add_cleanup(...)``.
    """

    name: str
    state: dict[str, Any]
    running: bool

    def __init__(
        self,
        name: str,
        initial_state: Optional[dict[str, Any]] = ...,
    ) -> None: ...
    def start(self) -> "Service": ...
    def stop(self) -> "Service": ...
    def set_state(self, new_state: dict[str, Any]) -> dict[str, Any]: ...
    def add_cleanup(self, fn: Callable[[], Any]) -> "Service": ...
    def interval(self, fn: Callable[[], Any], seconds: float) -> TimerHandle: ...
    def timeout(self, fn: Callable[[], Any], seconds: float) -> TimerHandle: ...
    def adopt(self, child: "Service", start: bool = ...) -> "Service": ...
    @overload
    def own(self, resource: None, stop_method: Optional[str] = ...) -> None: ...
    @overload
    def own(self, resource: T, stop_method: Optional[str] = ...) -> T: ...
    def watch_state(self, fn: Callable[[dict[str, Any]], Any]) -> Unsubscribe: ...
    def subscribe(
        self,
        target: Any,
        fn: Optional[Callable[..., Any]] = ...,
        *,
        immediate: bool = ...,
        owner: Any = ...,
    ) -> Any: ...
    def on(self, *args: Any, **kwargs: Any) -> None: ...
    def off(self, *args: Any, **kwargs: Any) -> None: ...
    def delegate(self, *args: Any, **kwargs: Any) -> None: ...
    def on_socket(self, *args: Any, **kwargs: Any) -> None: ...
    def off_socket(self, *args: Any, **kwargs: Any) -> None: ...
    def emit_socket(
        self,
        event: str,
        data: Any = ...,
        *,
        target: Optional[SocketTargetMap] = ...,
        route: Optional[str] = ...,
        client_id: Optional[str] = ...,
        session_id: Optional[str] = ...,
        topic: Optional[str] = ...,
    ) -> bool: ...
    def emit_socket_refetch(
        self,
        action: str,
        payload: Optional[dict[str, Any]] = ...,
        *,
        event: str = ...,
        target: Optional[SocketTargetMap] = ...,
        route: Optional[str] = ...,
        client_id: Optional[str] = ...,
        session_id: Optional[str] = ...,
        topic: Optional[str] = ...,
    ) -> bool: ...
    def call_action(self, *args: Any, **kwargs: Any) -> None: ...


class Watcher:
    """Server watcher for polling or streaming external state."""

    name: str

    def __init__(
        self,
        name: str,
        *,
        poll: Optional[Callable[[], Any]] = ...,
        stream: Optional[Callable[[], Iterable[Any]]] = ...,
        interval: float = ...,
        retry: bool = ...,
        max_backoff: float = ...,
        dedupe: bool = ...,
        transform: Optional[Callable[[Any], Any]] = ...,
    ) -> None: ...
    def start(self) -> "Watcher": ...
    def stop(self) -> "Watcher": ...
    def subscribe(self, fn: Callable[[Any, "Watcher"], Any]) -> Unsubscribe: ...
    @property
    def running(self) -> bool: ...
    @property
    def last_value(self) -> Any: ...
    def health(self) -> dict[str, Any]: ...


class QueueService(SPECTERQueueService):
    """Background worker queue with job state and browser-friendly results.

    Subclass this for server jobs. Use ``enqueue(...)`` from a Controller,
    update jobs with ``progress_job(...)``, and expose state with
    ``queue_snapshot()`` or ``job_status(...)``.
    """

    signal_event: str
    job_history_limit: int

    def __init__(
        self,
        name: str,
        *,
        worker_count: int = ...,
        maxsize: int = ...,
        poll_interval: float = ...,
        initial_state: Optional[dict[str, Any]] = ...,
        job_history_limit: int = ...,
    ) -> None: ...
    def emit_socket(
        self,
        event: str,
        data: Any = ...,
        *,
        target: Optional[SocketTargetMap] = ...,
        route: Optional[str] = ...,
        client_id: Optional[str] = ...,
        session_id: Optional[str] = ...,
        topic: Optional[str] = ...,
    ) -> bool: ...
    def emit_socket_refetch(
        self,
        action: str,
        payload: Optional[dict[str, Any]] = ...,
        *,
        event: str = ...,
        target: Optional[SocketTargetMap] = ...,
        route: Optional[str] = ...,
        client_id: Optional[str] = ...,
        session_id: Optional[str] = ...,
        topic: Optional[str] = ...,
    ) -> bool: ...
    def enqueue_job(
        self,
        payload: Any,
        *,
        job_id: Optional[str] = ...,
        label: Optional[str] = ...,
        meta: Optional[dict[str, Any]] = ...,
        route: Optional[str] = ...,
        session_id: Optional[str] = ...,
        client_id: Optional[str] = ...,
        topic: Optional[str] = ...,
    ) -> dict[str, Any]: ...
    def job_action_result(
        self,
        *,
        job_id: Optional[str] = ...,
        job: Optional[dict[str, Any]] = ...,
        accepted: bool = ...,
        message: Optional[str] = ...,
    ) -> dict[str, Any]: ...
    def queue_snapshot(self) -> dict[str, Any]: ...
    def get_job(self, job_id: Optional[str]) -> Optional[dict[str, Any]]: ...
    def latest_job(self, *, session_id: Optional[str] = ...) -> Optional[dict[str, Any]]: ...
    def report_progress(
        self,
        *,
        current: Any = ...,
        total: Any = ...,
        percent: Optional[int] = ...,
        message: Optional[str] = ...,
        result: Any = ...,
        job_id: Optional[str] = ...,
    ) -> dict[str, Any]: ...
    def request_cancel(self, job_id: str, *, message: Optional[str] = ...) -> dict[str, Any]: ...
    def cancel_requested(self, job_id: Optional[str] = ...) -> bool: ...
    def check_cancelled(self, *, job_id: Optional[str] = ..., message: Optional[str] = ...) -> None: ...
    def complete_job(self, *, result: Any = ..., message: Optional[str] = ..., job_id: Optional[str] = ...) -> dict[str, Any]: ...
    def fail_job(self, *, error: Any, message: Optional[str] = ..., job_id: Optional[str] = ...) -> dict[str, Any]: ...
    def cancel_job(self, *, message: Optional[str] = ..., job_id: Optional[str] = ...) -> dict[str, Any]: ...
    def emit_job_signal(self, job_id: str, *, event: Optional[str] = ..., payload: Optional[dict[str, Any]] = ...) -> bool: ...


class Controller(SPECTERController):
    """Server-side route controller.

    Implement ``load()`` to return JSON-safe page data. Decorate methods with
    ``@action`` so browser Modules can call them with ``self.call_action(...)``.
    Use ``self.request`` for params, query, form data, files, session, and auth.
    """

    route: Optional[str]

    def __init__(self, **kwargs: Any) -> None: ...
    @property
    def request(self) -> Any: ...
    @request.setter
    def request(self, value: Any) -> None: ...
    @property
    def app(self) -> Any: ...
    @app.setter
    def app(self, value: Any) -> None: ...
    def bind_app(self, app: Any) -> "Controller": ...
    def emit_socket(
        self,
        event: str,
        data: Any = ...,
        *,
        target: Optional[SocketTargetMap] = ...,
        route: Optional[str] = ...,
        client_id: Optional[str] = ...,
        session_id: Optional[str] = ...,
        topic: Optional[str] = ...,
    ) -> bool: ...
    def emit_socket_refetch(
        self,
        action: str,
        payload: Optional[dict[str, Any]] = ...,
        *,
        event: str = ...,
        target: Optional[SocketTargetMap] = ...,
        route: Optional[str] = ...,
        client_id: Optional[str] = ...,
        session_id: Optional[str] = ...,
        topic: Optional[str] = ...,
    ) -> bool: ...
    def emit_to_session(self, event: str, data: Any = ..., *, topic: Optional[str] = ...) -> bool: ...
    def emit_to_caller(self, event: str, data: Any = ...) -> bool: ...
    def refetch_session(self, action: str, payload: Optional[dict[str, Any]] = ..., *, event: str = ...) -> bool: ...
    @classmethod
    def sprag_actions(cls) -> dict[str, Callable[..., Any]]: ...
    def service(self, name: str) -> Any: ...
    def queue(self, name: str) -> QueueService: ...
    def enqueue(
        self,
        queue_name: str,
        payload: Any,
        *,
        job_id: Optional[str] = ...,
        label: Optional[str] = ...,
        meta: Optional[dict[str, Any]] = ...,
    ) -> dict[str, Any]: ...
    def job_status(self, queue_name: str, job_id: str) -> dict[str, Any]: ...
    def request_job_cancel(self, queue_name: str, job_id: str) -> dict[str, Any]: ...
    def load(self) -> dict[str, Any]: ...
    def redirect(self, location: str, *, status: int = ..., replace: Optional[bool] = ...) -> Redirect: ...
    def login(
        self,
        user: Any,
        *,
        viewer: Any = ...,
        active_profile: Any = ...,
        extra_session: Optional[dict[str, Any]] = ...,
        remember: bool = ...,
    ) -> Any: ...
    def set_active_profile(self, profile: Any, *, extra_session: Optional[dict[str, Any]] = ...) -> Any: ...
    def require_auth(
        self,
        *,
        roles: AuthValues = ...,
        permissions: AuthValues = ...,
        require_active_profile: bool = ...,
        redirect_to: str = ...,
        next_param: str = ...,
    ) -> Any: ...
    def logout(self) -> None: ...


class ActionDispatchError(RuntimeError):
    """Structured action-dispatch failure for SPRAG bridge responses."""

    status_code: int
    traceback_text: Optional[str]

    def __init__(
        self,
        message: str,
        *,
        status_code: int = ...,
        traceback_text: Optional[str] = ...,
    ) -> None: ...


def dispatch_controller_action(
    pages: Iterable[tuple[str, Any]],
    *,
    route_path: str,
    action_name: str,
    payload: Any = ...,
    request: Any = ...,
    app: Any = ...,
    mounts: Optional[Iterable[tuple[str, Any]]] = ...,
) -> ActionResult: ...
