"""Server-side SPRAG surface over SPECTER."""

from __future__ import annotations

import inspect
import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from specter import Controller as SPECTERController
from specter import Field, Outcome, Schema
from specter import QueueService as SPECTERQueueService
from specter import Service as SPECTERService
from specter import registry
from gevent.queue import Empty

# -- HTTP / routing ----------------------------------------------------------
from specter import HTTPError, Router, expect_json, json_endpoint, require_fields, route

# -- Operations --------------------------------------------------------------
from specter import Operation, OperationError

# -- State -------------------------------------------------------------------
from specter import Cache, Model, Store, create_cache, create_model, create_store

# -- Communication -----------------------------------------------------------
from specter import bus

# -- Realtime ----------------------------------------------------------------
from specter import Handler, SocketIngress

# -- System ------------------------------------------------------------------
from specter import ManagedProcess, Watcher, WatcherError, start_process

# -- Orchestration -----------------------------------------------------------
from specter import ServiceManager, boot

from .observability import log_runtime_event
from .routing import match_page_route
from .session import (
    hydrate_request,
    resolve_auth_service,
    resolve_session_policy,
    stamp_login_session,
)


_UNSET = object()
_current_request = ContextVar("sprag_current_request", default=_UNSET)
_current_app = ContextVar("sprag_current_app", default=_UNSET)
_current_queue_job = ContextVar("sprag_current_queue_job", default=None)
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_JOB_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActionResult:
    """Structured browser-action response payload."""

    ok: bool
    value: object = None
    error: str | None = None
    status: int = 200
    redirect: "Redirect | None" = None


class Redirect(Exception):
    """First-class redirect contract for page loads and browser actions."""

    def __init__(self, location, *, status=302, replace=None):
        if not isinstance(location, str) or not location.strip():
            raise ValueError("redirect(location, ...) requires a non-empty string location.")
        if status not in _REDIRECT_STATUSES:
            raise ValueError(
                "redirect(status=...) must be one of 301, 302, 303, 307, or 308."
            )
        if replace is not None and not isinstance(replace, bool):
            raise ValueError("redirect(replace=...) must be True, False, or None.")
        super().__init__(location)
        self.location = location
        self.status = int(status)
        self.replace = replace

    @property
    def browser_replace(self) -> bool:
        if self.replace is not None:
            return self.replace
        return self.status in {301, 308}

    def as_payload(self) -> dict:
        return {
            "location": self.location,
            "status": self.status,
            "replace": self.browser_replace,
        }


def redirect(location, *, status=302, replace=None) -> Redirect:
    """Return a first-class redirect response for page loads or actions."""
    return Redirect(location, status=status, replace=replace)


def action(fn=None, *, schema=None, name=None):
    """Mark a controller method as a route action."""

    def decorator(method):
        method._sprag_action = True
        method._sprag_action_meta = {
            "name": name or method.__name__,
            "schema": schema,
        }
        return method

    if fn is not None:
        return decorator(fn)
    return decorator


@dataclass(frozen=True)
class _AuthRequirement:
    roles: tuple[str, ...] | None = None
    permissions: tuple[str, ...] | None = None
    require_active_profile: bool = False
    redirect_to: str = "/login"
    next_param: str = "next"


def _normalize_auth_values(values, *, label):
    if values is None:
        return None
    if isinstance(values, str):
        normalized = (values.strip(),)
    else:
        try:
            normalized = tuple(str(value).strip() for value in values)
        except TypeError as exc:
            raise TypeError(
                f"requires_auth(..., {label}=...) expects a string or iterable of strings."
            ) from exc
    cleaned = tuple(value for value in normalized if value)
    return cleaned or None


def requires_auth(
    target=None,
    *,
    roles=None,
    permissions=None,
    require_active_profile=False,
    redirect_to="/login",
    next_param="next",
):
    """Guard a controller class, ``load()``, or ``@action`` with auth."""

    requirement = _AuthRequirement(
        roles=_normalize_auth_values(roles, label="roles"),
        permissions=_normalize_auth_values(permissions, label="permissions"),
        require_active_profile=bool(require_active_profile),
        redirect_to=str(redirect_to or "/login"),
        next_param=str(next_param or "next"),
    )

    def decorator(subject):
        if inspect.isclass(subject):
            subject._sprag_auth_requirement = requirement
            return subject
        subject._sprag_auth_requirement = requirement
        return subject

    if target is not None:
        return decorator(target)
    return decorator


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

    def emit_socket(self, event, data=None, *, route=None, client_id=None, session_id=None, topic=None):
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
        return bool(
            transport.emit(
                event,
                data,
                route=route,
                client_id=client_id,
                session_id=session_id,
                topic=topic,
            )
        )

    def call_action(self, *args, **kwargs):
        _server_only("Service.call_action")


class _JobCancelled(RuntimeError):
    """Internal control-flow signal for cooperative queue cancellation."""


class QueueService(SPECTERQueueService):
    """SPRAG queue convention layer on top of Specter's raw worker queue.

    Specter's ``QueueService`` gives SPRAG the lifecycle-owned worker pool.
    This subclass adds the author-facing conventions the framework was
    missing: structured job records, progress updates, cancellation requests,
    a stable action result shape, and optional targeted socket invalidation so
    the browser can refetch authoritative job state.
    """

    signal_event = "sprag:queue.job.changed"

    def __init__(
        self,
        name,
        *,
        worker_count=1,
        maxsize=0,
        poll_interval=0.5,
        initial_state=None,
        job_history_limit=24,
    ):
        state = {
            "jobs": {},
            "job_order": [],
            "active_jobs": [],
        }
        if initial_state:
            state.update(initial_state)
        super().__init__(
            name,
            worker_count=worker_count,
            maxsize=maxsize,
            poll_interval=poll_interval,
            initial_state=state,
        )
        self.job_history_limit = max(1, int(job_history_limit or 24))

    def emit_socket(self, event, data=None, *, route=None, client_id=None, session_id=None, topic=None):
        """Emit a websocket event to connected browser clients."""
        transport = registry.resolve("socket_transport")
        if transport is None:
            return False
        return bool(
            transport.emit(
                event,
                data,
                route=route,
                client_id=client_id,
                session_id=session_id,
                topic=topic,
            )
        )

    def enqueue_job(
        self,
        payload,
        *,
        job_id=None,
        label=None,
        meta=None,
        route=None,
        session_id=None,
        client_id=None,
        topic=None,
    ):
        """Queue structured work and return the standard SPRAG action payload."""
        job_id = str(job_id or uuid.uuid4().hex[:12])
        label = str(label or f"Job {job_id}").strip()
        if not label:
            label = f"Job {job_id}"
        target = {
            "route": route,
            "session_id": session_id,
            "client_id": client_id,
            "topic": topic,
        }
        job = {
            "id": job_id,
            "label": label,
            "status": "queued",
            "message": f"Queued {label}.",
            "progress": {
                "current": 0,
                "total": None,
                "percent": 0,
            },
            "result": None,
            "error": None,
            "cancel_requested": False,
            "created_at": time.time(),
            "updated_at": time.time(),
            "meta": dict(meta or {}),
            "target": target,
        }
        accepted = super().enqueue(
            {"__sprag_job__": True, "job_id": job_id, "payload": payload},
            block=False,
        )
        if not accepted:
            rejected = dict(job)
            rejected["status"] = "rejected"
            rejected["message"] = f"Queue is full. Could not queue {label}."
            rejected["updated_at"] = time.time()
            log_runtime_event(
                "queue.job.enqueued",
                level="warning",
                queue=self.name,
                job_id=job_id,
                label=label,
                accepted=False,
                route=route,
                session_id=session_id,
                client_id=client_id,
                topic=topic,
            )
            return self.job_action_result(job=rejected, accepted=False)

        self._merge_job_state(job_id, lambda _existing: job)
        self.emit_job_signal(job_id)
        log_runtime_event(
            "queue.job.enqueued",
            queue=self.name,
            job_id=job_id,
            label=label,
            accepted=True,
            route=route,
            session_id=session_id,
            client_id=client_id,
            topic=topic,
        )
        return self.job_action_result(job_id=job_id, accepted=True)

    def job_action_result(self, *, job_id=None, job=None, accepted=True, message=None):
        """Return the documented action payload for queue actions."""
        snapshot = dict(job or self.get_job(job_id) or {})
        queue = self.queue_snapshot()
        if message is None:
            message = snapshot.get("message")
        return {
            "accepted": bool(accepted),
            "job": snapshot or None,
            "queue": queue,
            "message": message,
        }

    def queue_snapshot(self):
        """Return queue-level diagnostics paired with job action results."""
        state = self.get_state()
        active_jobs = list(state.get("active_jobs", []))
        return {
            "pending": self.pending_count(),
            "active": len(active_jobs),
            "active_jobs": active_jobs,
            "workers": self.worker_count,
        }

    def get_job(self, job_id):
        """Return a snapshot of a tracked job by id, if present."""
        if job_id is None:
            return None
        jobs = self.get_state().get("jobs", {})
        job = jobs.get(str(job_id))
        return dict(job) if isinstance(job, dict) else None

    def latest_job(self, *, session_id=None):
        """Return the most recently queued job, optionally scoped to a session."""
        state = self.get_state()
        jobs = state.get("jobs", {})
        for job_id in reversed(state.get("job_order", [])):
            job = jobs.get(job_id)
            if not isinstance(job, dict):
                continue
            target = job.get("target") or {}
            if session_id is None or target.get("session_id") == session_id:
                return dict(job)
        return None

    def report_progress(
        self,
        *,
        current=None,
        total=None,
        percent=None,
        message=None,
        result=None,
        job_id=None,
    ):
        """Update the in-flight job snapshot from inside ``handle_item``."""
        resolved_job_id = self._require_current_job_id(job_id=job_id)

        def update(job):
            progress = dict(job.get("progress") or {})
            if current is not None:
                progress["current"] = current
            if total is not None:
                progress["total"] = total
            if percent is None and progress.get("total") not in (None, 0) and progress.get("current") is not None:
                percent_value = int(round((float(progress["current"]) / float(progress["total"])) * 100))
                progress["percent"] = max(0, min(100, percent_value))
            elif percent is not None:
                progress["percent"] = max(0, min(100, int(percent)))
            job["progress"] = progress
            if result is not None:
                job["result"] = result
            if message is not None:
                job["message"] = message
            if job.get("status") == "queued":
                job["status"] = "running"
            return job

        self._merge_job_state(resolved_job_id, update)
        self.emit_job_signal(resolved_job_id)
        return self.get_job(resolved_job_id)

    def request_cancel(self, job_id, *, message=None):
        """Mark a queued or running job for cooperative cancellation."""
        snapshot = self.get_job(job_id)
        if snapshot is None:
            return self.job_action_result(
                job={"id": str(job_id), "status": "missing"},
                accepted=False,
                message=f"Unknown job {job_id}.",
            )
        if snapshot.get("status") in _JOB_TERMINAL_STATUSES:
            return self.job_action_result(job=snapshot, accepted=False)

        def update(job):
            job["cancel_requested"] = True
            job["status"] = "cancelling"
            job["message"] = message or f"Cancelling {job['label']}..."
            return job

        self._merge_job_state(job_id, update)
        self.emit_job_signal(job_id)
        log_runtime_event(
            "queue.job.cancel_requested",
            queue=self.name,
            job_id=job_id,
            label=snapshot.get("label"),
            status="cancelling",
        )
        return self.job_action_result(job_id=job_id, accepted=True)

    def cancel_requested(self, job_id=None):
        """Return whether cancellation has been requested for a tracked job."""
        snapshot = self.get_job(self._require_current_job_id(job_id=job_id))
        return bool(snapshot and snapshot.get("cancel_requested"))

    def check_cancelled(self, *, job_id=None, message=None):
        """Raise an internal cancellation signal when the current job was cancelled."""
        if self.cancel_requested(job_id=job_id):
            raise _JobCancelled(message or "Job cancelled.")

    def complete_job(self, *, result=None, message=None, job_id=None):
        """Mark a tracked job complete."""
        resolved_job_id = self._require_current_job_id(job_id=job_id)

        def update(job):
            job["status"] = "completed"
            job["cancel_requested"] = False
            job["error"] = None
            progress = dict(job.get("progress") or {})
            progress["percent"] = 100
            if progress.get("total") not in (None, 0) and progress.get("current") is None:
                progress["current"] = progress["total"]
            job["progress"] = progress
            job["result"] = result
            if message is not None:
                job["message"] = message
            else:
                job["message"] = f"Completed {job['label']}."
            return job

        self._merge_job_state(resolved_job_id, update)
        self._set_active_job(resolved_job_id, active=False)
        self.emit_job_signal(resolved_job_id)
        snapshot = self.get_job(resolved_job_id)
        log_runtime_event(
            "queue.job.completed",
            queue=self.name,
            job_id=resolved_job_id,
            label=snapshot.get("label") if snapshot else None,
            duration_ms=self._job_duration_ms(snapshot),
            result=result,
        )
        return snapshot

    def fail_job(self, *, error, message=None, job_id=None):
        """Mark a tracked job failed."""
        resolved_job_id = self._require_current_job_id(job_id=job_id)

        def update(job):
            job["status"] = "failed"
            job["error"] = str(error)
            job["message"] = message or str(error)
            return job

        self._merge_job_state(resolved_job_id, update)
        self._set_active_job(resolved_job_id, active=False)
        self.emit_job_signal(resolved_job_id)
        snapshot = self.get_job(resolved_job_id)
        log_runtime_event(
            "queue.job.failed",
            level="error",
            queue=self.name,
            job_id=resolved_job_id,
            label=snapshot.get("label") if snapshot else None,
            duration_ms=self._job_duration_ms(snapshot),
            error=error,
        )
        return snapshot

    def cancel_job(self, *, message=None, job_id=None):
        """Mark a tracked job cancelled."""
        resolved_job_id = self._require_current_job_id(job_id=job_id)

        def update(job):
            job["status"] = "cancelled"
            job["cancel_requested"] = True
            job["message"] = message or f"Cancelled {job['label']}."
            return job

        self._merge_job_state(resolved_job_id, update)
        self._set_active_job(resolved_job_id, active=False)
        self.emit_job_signal(resolved_job_id)
        snapshot = self.get_job(resolved_job_id)
        log_runtime_event(
            "queue.job.cancelled",
            queue=self.name,
            job_id=resolved_job_id,
            label=snapshot.get("label") if snapshot else None,
            duration_ms=self._job_duration_ms(snapshot),
        )
        return snapshot

    def emit_job_signal(self, job_id, *, event=None, payload=None):
        """Emit a targeted socket invalidation for a tracked job when possible."""
        job = self.get_job(job_id)
        if job is None:
            return False
        target = job.get("target") or {}
        envelope = {"job_id": job_id}
        if payload:
            envelope.update(payload)
        return self.emit_socket(
            event or self.signal_event,
            envelope,
            route=target.get("route"),
            session_id=target.get("session_id"),
            client_id=target.get("client_id"),
            topic=target.get("topic"),
        )

    def _worker_loop(self):
        while self.running:
            try:
                item = self.queue.get(timeout=self.poll_interval)
            except Empty:
                continue

            self.set_state({"queue_size": self.queue.qsize()})

            try:
                if isinstance(item, dict) and item.get("__sprag_job__"):
                    self._handle_job_envelope(item)
                else:
                    self.handle_item(item)
            except Exception as exc:
                log_runtime_event(
                    "queue.worker.error",
                    level="error",
                    queue=self.name,
                    error=exc,
                )
                logger.error(
                    f"[SPRAG] Queue worker error in '{self.name}': {exc}",
                    exc_info=True,
                )
            finally:
                try:
                    self.queue.task_done()
                except Exception:
                    pass
                self.set_state({"queue_size": self.queue.qsize()})

    def _handle_job_envelope(self, item):
        job_id = item["job_id"]
        payload = item.get("payload")
        token = _current_queue_job.set(job_id)
        try:
            snapshot = self.get_job(job_id)
            if snapshot is None:
                return
            self._set_active_job(job_id, active=True)
            if snapshot.get("cancel_requested"):
                self.cancel_job(job_id=job_id, message=f"Cancelled {snapshot['label']}.")
                return

            def mark_running(job):
                if job.get("status") == "queued":
                    job["status"] = "running"
                if not job.get("message"):
                    job["message"] = f"Running {job['label']}..."
                return job

            self._merge_job_state(job_id, mark_running)
            self.emit_job_signal(job_id)
            snapshot = self.get_job(job_id)
            log_runtime_event(
                "queue.job.started",
                queue=self.name,
                job_id=job_id,
                label=snapshot.get("label") if snapshot else None,
            )
            result = self.handle_item(payload)
            if self.cancel_requested(job_id=job_id):
                self.cancel_job(job_id=job_id)
                return
            if self.get_job(job_id).get("status") not in _JOB_TERMINAL_STATUSES:
                self.complete_job(job_id=job_id, result=result)
        except _JobCancelled as exc:
            self.cancel_job(job_id=job_id, message=str(exc) or None)
        except Exception as exc:
            self.fail_job(job_id=job_id, error=f"{exc.__class__.__name__}: {exc}")
            raise
        finally:
            _current_queue_job.reset(token)

    def _set_active_job(self, job_id, *, active):
        state = self.get_state()
        active_jobs = list(state.get("active_jobs", []))
        if active and job_id not in active_jobs:
            active_jobs.append(job_id)
            self.set_state({"active_jobs": active_jobs})
        if not active and job_id in active_jobs:
            active_jobs = [value for value in active_jobs if value != job_id]
            self.set_state({"active_jobs": active_jobs})

    def _merge_job_state(self, job_id, update):
        state = self.get_state()
        jobs = dict(state.get("jobs", {}))
        job = dict(jobs.get(job_id, {}))
        job = update(job) or job
        job["updated_at"] = time.time()
        jobs[job_id] = job
        job_order = [value for value in state.get("job_order", []) if value != job_id]
        job_order.append(job_id)
        if len(job_order) > self.job_history_limit:
            trim_count = len(job_order) - self.job_history_limit
            for stale_id in job_order[:trim_count]:
                stale = jobs.get(stale_id)
                if stale and stale.get("status") in _JOB_TERMINAL_STATUSES:
                    jobs.pop(stale_id, None)
            job_order = [value for value in job_order if value in jobs]
        self.set_state({"jobs": jobs, "job_order": job_order})
        return dict(job)

    def _require_current_job_id(self, *, job_id=None):
        resolved = str(job_id) if job_id is not None else _current_queue_job.get()
        if not resolved:
            raise RuntimeError(
                f"{type(self).__name__} job helper requires an active queued job "
                "or an explicit job_id."
            )
        return resolved

    def _job_duration_ms(self, snapshot):
        if not isinstance(snapshot, dict):
            return None
        created_at = snapshot.get("created_at")
        updated_at = snapshot.get("updated_at")
        if created_at is None or updated_at is None:
            return None
        return max(0, int(round((float(updated_at) - float(created_at)) * 1000)))


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

    def emit_socket(self, event, data=None, *, route=None, client_id=None, session_id=None, topic=None):
        """Emit a websocket event to connected browser clients."""
        transport = registry.resolve("socket_transport")
        if transport is None:
            return False
        if route is None:
            route = getattr(self, "route", None)
        return bool(
            transport.emit(
                event,
                data,
                route=route,
                client_id=client_id,
                session_id=session_id,
                topic=topic,
            )
        )

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

    def queue(self, name):
        """Resolve a named queue service and validate its SPRAG surface."""
        service = self.service(name)
        if not isinstance(service, QueueService):
            raise ActionDispatchError(
                f"Service {name!r} is not a SPRAG QueueService.",
                status_code=500,
            )
        return service

    def enqueue(self, queue_name, payload, *, job_id=None, label=None, meta=None):
        """Queue background work through the standard SPRAG job contract."""
        queue = self.queue(queue_name)
        request = self.request
        return queue.enqueue_job(
            payload,
            job_id=job_id,
            label=label,
            meta=meta,
            route=getattr(self, "route", None),
            session_id=getattr(request, "session_id", None) if request is not None else None,
        )

    def job_status(self, queue_name, job_id):
        """Return the standard job payload for a tracked background job."""
        queue = self.queue(queue_name)
        snapshot = queue.get_job(job_id)
        if snapshot is None:
            raise ActionDispatchError(
                f"Unknown job {job_id!r} for queue {queue_name!r}.",
                status_code=404,
            )
        return queue.job_action_result(job_id=job_id, accepted=True)

    def request_job_cancel(self, queue_name, job_id):
        """Ask a tracked background job to cancel cooperatively."""
        queue = self.queue(queue_name)
        snapshot = queue.get_job(job_id)
        if snapshot is None:
            raise ActionDispatchError(
                f"Unknown job {job_id!r} for queue {queue_name!r}.",
                status_code=404,
            )
        return queue.request_cancel(job_id)

    def load(self):
        """Return route data for SSR or browser boot."""
        return {}

    def redirect(self, location, *, status=302, replace=None):
        """Return a first-class redirect response from ``load()`` or an action."""
        return redirect(location, status=status, replace=replace)

    def login(
        self,
        user,
        *,
        viewer=None,
        active_profile=None,
        extra_session=None,
        remember=False,
    ):
        """Rotate the session id and persist auth state through the auth provider."""
        request = hydrate_request(self.request, app=self.app)
        auth = resolve_auth_service(self.app)
        request.session.rotate()
        stamp_login_session(
            request.session,
            policy=resolve_session_policy(self.app),
            remember=remember,
        )
        session_payload = dict(extra_session or {})
        if viewer is not None and "viewer" not in session_payload:
            session_payload["viewer"] = viewer
        auth.login_session(
            user,
            request.session,
            request,
            extra_session=session_payload or None,
        )
        request.user = user
        if active_profile is not None:
            auth.set_active_profile(
                active_profile,
                user,
                request.session,
                request,
                extra_session=dict(extra_session or {}) or None,
            )
        request.active_profile = auth.load_active_profile(user, request.session, request)
        return user

    def set_active_profile(self, profile, *, extra_session=None):
        """Update the provider-owned active profile without rotating the session id."""
        request = hydrate_request(self.request, app=self.app)
        auth = resolve_auth_service(self.app)
        auth.set_active_profile(
            profile,
            request.user,
            request.session,
            request,
            extra_session=dict(extra_session or {}) or None,
        )
        request.active_profile = auth.load_active_profile(
            request.user,
            request.session,
            request,
        )
        return request.active_profile

    def require_auth(
        self,
        *,
        roles=None,
        permissions=None,
        require_active_profile=False,
        redirect_to="/login",
        next_param="next",
    ):
        """Imperatively enforce the same auth requirements as ``@requires_auth``."""
        request = hydrate_request(self.request, app=self.app)
        requirement = _AuthRequirement(
            roles=_normalize_auth_values(roles, label="roles"),
            permissions=_normalize_auth_values(permissions, label="permissions"),
            require_active_profile=bool(require_active_profile),
            redirect_to=str(redirect_to or "/login"),
            next_param=str(next_param or "next"),
        )
        _enforce_auth_requirement(requirement, request, app=self.app, method_name=None)
        return request.user

    def logout(self):
        """Invalidate the current session and drop the resolved request user."""
        request = hydrate_request(self.request, app=self.app)
        request.session.invalidate()
        request.user = None
        request.active_profile = None
        return None


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

    request = hydrate_request(request, app=app)
    controller = _resolve_surface_controller(pages, mounts or [], route_path, app=app)
    controller_class = controller.__class__
    actions = controller_class.sprag_actions()
    action = actions.get(action_name)
    if action is None:
        raise ActionDispatchError(
            f"Unknown action {action_name!r} for route {route_path!r}.",
            status_code=404,
        )

    bound_action = getattr(controller, action.__name__)
    with controller_context(request=request, app=app):
        authorize_controller_method(controller, action.__name__)

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
    except Redirect as exc:
        return ActionResult(ok=True, status=exc.status, redirect=exc)
    except ActionDispatchError:
        raise
    except Exception as exc:
        raise ActionDispatchError(
            f"{exc.__class__.__name__}: {exc}",
            status_code=500,
        ) from exc

    return _coerce_action_result(result)


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


def _evaluate_auth_requirement(requirement, request, *, app=None):
    if requirement is None:
        return None

    user = getattr(request, "user", None)
    if user is None:
        return {
            "status_code": 401,
            "message": "Authentication required.",
            "redirect_location": _auth_redirect_location(requirement, request),
        }

    active_profile = getattr(request, "active_profile", None)
    if requirement.require_active_profile and active_profile is None:
        return {
            "status_code": 403,
            "message": "Active profile required.",
            "redirect_location": None,
        }

    if requirement.roles or requirement.permissions:
        auth = resolve_auth_service(app)
        allowed = bool(
            auth.authorize(
                user,
                active_profile,
                request.session,
                request,
                roles=requirement.roles,
                permissions=requirement.permissions,
            )
        )
        if not allowed:
            return {
                "status_code": 403,
                "message": "Forbidden.",
                "redirect_location": None,
            }
    return None


def _enforce_auth_requirement(requirement, request, *, app=None, method_name=None):
    failure = _evaluate_auth_requirement(requirement, request, app=app)
    if failure is None:
        return
    if (
        failure["status_code"] == 401
        and getattr(request, "method", "GET") in {"GET", "BUILD"}
        and method_name in {None, "load"}
    ):
        raise redirect(failure["redirect_location"])
    raise ActionDispatchError(failure["message"], status_code=failure["status_code"])


def authorize_controller_method(controller, method_name):
    """Enforce a controller method's resolved auth requirement."""
    requirement = _controller_auth_requirement(controller, method_name)
    if requirement is None:
        return
    request = hydrate_request(controller.request, app=controller.app)
    _enforce_auth_requirement(
        requirement,
        request,
        app=controller.app,
        method_name=method_name,
    )


def _controller_auth_requirement(controller, method_name):
    controller_class = controller.__class__
    method = getattr(controller_class, method_name, None)
    requirement = getattr(method, "_sprag_auth_requirement", None)
    if requirement is not None:
        return requirement
    class_requirement = getattr(controller_class, "_sprag_auth_requirement", None)
    if class_requirement is None:
        return None
    if method_name == "load" or method_name in {
        value.__name__ for value in controller_class.sprag_actions().values()
    }:
        return class_requirement
    return None


def _auth_redirect_location(requirement, request):
    target = _request_location(request)
    parts = urlsplit(requirement.redirect_to or "/login")
    query = list(parse_qsl(parts.query, keep_blank_values=True))
    query.append((requirement.next_param or "next", target))
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/login", urlencode(query), parts.fragment))


def _request_location(request):
    path = getattr(request, "path", None) or "/"
    query = getattr(request, "query", None) or {}
    if not query:
        return path
    encoded = urlencode(query, doseq=True)
    return f"{path}?{encoded}"


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


def _coerce_action_result(result) -> ActionResult:
    if isinstance(result, Redirect):
        return ActionResult(ok=True, status=result.status, redirect=result)
    if isinstance(result, Outcome):
        redirect_response = result.value if isinstance(result.value, Redirect) else None
        value = None if redirect_response is not None else result.value
        return ActionResult(
            ok=result.ok,
            value=value,
            error=result.error,
            status=redirect_response.status if redirect_response is not None else result.status,
            redirect=redirect_response,
        )
    return ActionResult(ok=True, value=result, status=200)
