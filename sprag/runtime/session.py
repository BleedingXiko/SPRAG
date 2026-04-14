"""Session and auth helpers for SPRAG runtime requests."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from email.utils import formatdate
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .request import Request


SESSION_COOKIE_NAME = "SPRAG_SID"
_SESSION_META_KEY = "__sprag_session__"


def _generate_session_id() -> str:
    return secrets.token_urlsafe(24)


def parse_cookie_header(raw_cookie: str | None) -> dict[str, str]:
    if not raw_cookie:
        return {}
    jar = SimpleCookie()
    try:
        jar.load(raw_cookie)
    except Exception:
        return {}
    return {key: morsel.value for key, morsel in jar.items()}


def resolve_session_id(raw_cookie: str | None) -> tuple[str, bool]:
    cookies = parse_cookie_header(raw_cookie)
    session_id = cookies.get(SESSION_COOKIE_NAME)
    if isinstance(session_id, str) and session_id.strip():
        return session_id.strip(), False
    return _generate_session_id(), True


@dataclass(frozen=True)
class SessionPolicy:
    idle_ttl_seconds: int | None = None
    absolute_ttl_seconds: int | None = None
    remember_me_ttl_seconds: int | None = None

    def __post_init__(self):
        for field_name in (
            "idle_ttl_seconds",
            "absolute_ttl_seconds",
            "remember_me_ttl_seconds",
        ):
            value = getattr(self, field_name)
            if value is not None and int(value) < 1:
                raise ValueError(
                    f"SessionPolicy.{field_name} must be a positive integer or None."
                )


def session_cookie_header(session_id: str, *, max_age: int | None = None) -> tuple[str, str]:
    header = f"{SESSION_COOKIE_NAME}={session_id}; Path=/; HttpOnly; SameSite=Lax"
    if max_age is not None:
        clamped = max(0, int(max_age))
        header += f"; Max-Age={clamped}; Expires={formatdate(time.time() + clamped, usegmt=True)}"
    return ("Set-Cookie", header)


class RequestSession:
    """Mutable server-side session state scoped to one request."""

    def __init__(
        self,
        session_id: str | None = None,
        data: dict | None = None,
        *,
        cookie_present: bool = False,
        request: "Request | None" = None,
    ):
        self._id = str(session_id or _generate_session_id())
        self._data = dict(data or {})
        self._cookie_present = bool(cookie_present)
        self._force_cookie = not self._cookie_present
        self._dirty = False
        self._rotate_from: str | None = None
        self._invalidate_from: str | None = None
        self._request: Request | None = None
        self.bind(request)

    @property
    def id(self) -> str:
        return self._id

    @property
    def rotated_from(self) -> str | None:
        return self._rotate_from

    @property
    def invalidated_from(self) -> str | None:
        return self._invalidate_from

    def bind(self, request: "Request | None"):
        self._request = request
        if request is not None:
            request.session = self
            request.session_id = self._id
        return self

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        self._dirty = True
        return value

    def patch(self, values: dict | None):
        if not values:
            return self
        if not isinstance(values, dict):
            raise TypeError("request.session.patch(...) requires a dict.")
        self._data.update(values)
        self._dirty = True
        return self

    def delete(self, key):
        if key in self._data:
            value = self._data.pop(key)
            self._dirty = True
            return value
        return None

    def clear(self):
        if self._data:
            self._data.clear()
            self._dirty = True
        return self

    def snapshot(self) -> dict:
        return dict(self._data)

    def rotate(self) -> str:
        previous = self._id
        self._id = _generate_session_id()
        self._rotate_from = previous
        self._invalidate_from = None
        self._dirty = True
        self._force_cookie = True
        self._sync_request()
        return self._id

    def invalidate(self) -> str:
        previous = self._id
        self._id = _generate_session_id()
        self._data.clear()
        self._dirty = False
        self._rotate_from = None
        self._invalidate_from = previous
        self._force_cookie = True
        self._sync_request()
        return self._id

    def should_set_cookie(self) -> bool:
        return self._force_cookie

    def dirty(self) -> bool:
        return self._dirty

    def has_data(self) -> bool:
        return bool(self._data)

    def mark_committed(self):
        self._cookie_present = True
        self._force_cookie = False
        self._dirty = False
        self._rotate_from = None
        self._invalidate_from = None
        self._sync_request()

    def _sync_request(self):
        if self._request is not None:
            self._request.session_id = self._id
            self._request.session = self


class InMemorySessionStore:
    """Default in-process session store for server-side auth demos."""

    name = "session_store"

    def __init__(self):
        from specter import create_store

        self._store = create_store("sprag.sessions")

    def load(self, session_id):
        snapshot = self._store.get(str(session_id))
        return dict(snapshot) if isinstance(snapshot, dict) else {}

    def save(self, session: RequestSession):
        snapshot = session.snapshot()
        if snapshot:
            self._store.set({session.id: snapshot})
            return
        self._store.delete(session.id)

    def delete(self, session_id):
        self._store.delete(str(session_id))

    def rotate(self, session: RequestSession):
        previous = session.rotated_from

        def _atomic_rotate(draft):
            if previous and previous in draft:
                del draft[previous]
            snapshot = session.snapshot()
            if snapshot:
                draft[session.id] = snapshot
            return draft

        self._store.update(_atomic_rotate)


class AnonymousAuthService:
    """Default auth adapter that exposes an anonymous public snapshot."""

    name = "auth"

    def load_user(self, session, request):
        return None

    def load_active_profile(self, user, session, request):
        return None

    def authorize(
        self,
        user,
        active_profile,
        session,
        request,
        *,
        roles=None,
        permissions=None,
    ) -> bool:
        return not roles and not permissions

    def public_snapshot(self, user, session, request) -> dict:
        return {}

    def login_session(self, user, session, request, extra_session=None) -> None:
        raise RuntimeError(
            "Controller.login(...) requires an app-level 'auth' provider."
        )

    def set_active_profile(
        self,
        profile,
        user,
        session,
        request,
        extra_session=None,
    ) -> None:
        raise RuntimeError(
            "Controller.set_active_profile(...) requires an app-level 'auth' provider."
        )


_DEFAULT_SESSION_STORE = InMemorySessionStore()
_DEFAULT_AUTH_SERVICE = AnonymousAuthService()


def resolve_session_store(app=None):
    providers = getattr(app, "providers", None) or {}
    return providers.get("session_store") or _DEFAULT_SESSION_STORE


def resolve_auth_service(app=None):
    providers = getattr(app, "providers", None) or {}
    return providers.get("auth") or _DEFAULT_AUTH_SERVICE


def resolve_session_policy(app=None) -> SessionPolicy:
    policy = getattr(app, "session_policy", None)
    if policy is None:
        return SessionPolicy()
    if isinstance(policy, SessionPolicy):
        return policy
    if isinstance(policy, dict):
        return SessionPolicy(**policy)
    raise TypeError(
        "App.session_policy must be a SessionPolicy, dict, or None."
    )


def _policy_enabled(policy: SessionPolicy) -> bool:
    return any(
        value is not None
        for value in (
            policy.idle_ttl_seconds,
            policy.absolute_ttl_seconds,
            policy.remember_me_ttl_seconds,
        )
    )


def _session_meta(session: RequestSession) -> dict:
    meta = session.get(_SESSION_META_KEY)
    return dict(meta) if isinstance(meta, dict) else {}


def _replace_session_meta(session: RequestSession, meta: dict) -> dict:
    clean = {key: value for key, value in dict(meta or {}).items() if value is not None}
    current = _session_meta(session)
    if current != clean:
        session.set(_SESSION_META_KEY, clean)
    return clean


def _session_cookie_max_age(session: RequestSession, policy: SessionPolicy) -> int | None:
    meta = _session_meta(session)
    if not meta.get("remember_me") or policy.remember_me_ttl_seconds is None:
        return None
    absolute_expiry = meta.get("absolute_expiry")
    if absolute_expiry is None:
        return int(policy.remember_me_ttl_seconds)
    remaining = int(absolute_expiry) - int(time.time())
    return max(0, remaining)


def _reset_session_runtime(session: RequestSession, *, policy: SessionPolicy, now: int) -> None:
    if not _policy_enabled(policy):
        return
    _replace_session_meta(
        session,
        {
            "created_at": now,
            "last_seen": now,
            "remember_me": False,
        },
    )


def stamp_login_session(
    session: RequestSession,
    *,
    policy: SessionPolicy,
    remember: bool = False,
    now: int | None = None,
) -> dict:
    timestamp = int(time.time() if now is None else now)
    persistent = bool(remember and policy.remember_me_ttl_seconds is not None)
    absolute_expiry = None
    candidates = []
    if policy.absolute_ttl_seconds is not None:
        candidates.append(timestamp + int(policy.absolute_ttl_seconds))
    if persistent and policy.remember_me_ttl_seconds is not None:
        candidates.append(timestamp + int(policy.remember_me_ttl_seconds))
    if candidates:
        absolute_expiry = min(candidates)
    return _replace_session_meta(
        session,
        {
            "created_at": timestamp,
            "last_seen": timestamp,
            "absolute_expiry": absolute_expiry,
            "remember_me": persistent,
        },
    )


def apply_session_policy(
    session: RequestSession,
    *,
    app=None,
    now: int | None = None,
) -> RequestSession:
    policy = resolve_session_policy(app)
    timestamp = int(time.time() if now is None else now)
    meta = _session_meta(session)
    if not meta and not _policy_enabled(policy):
        return session

    created_at = int(meta.get("created_at") or timestamp)
    last_seen = int(meta.get("last_seen") or created_at)
    remember_me = bool(meta.get("remember_me"))
    absolute_expiry = meta.get("absolute_expiry")
    if absolute_expiry is not None:
        absolute_expiry = int(absolute_expiry)

    if policy.absolute_ttl_seconds is not None:
        policy_expiry = created_at + int(policy.absolute_ttl_seconds)
        absolute_expiry = min(absolute_expiry, policy_expiry) if absolute_expiry is not None else policy_expiry
    if remember_me and policy.remember_me_ttl_seconds is not None:
        remember_expiry = created_at + int(policy.remember_me_ttl_seconds)
        absolute_expiry = min(absolute_expiry, remember_expiry) if absolute_expiry is not None else remember_expiry

    expired_for_idle = (
        policy.idle_ttl_seconds is not None
        and last_seen + int(policy.idle_ttl_seconds) <= timestamp
    )
    expired_for_absolute = absolute_expiry is not None and absolute_expiry <= timestamp
    if expired_for_idle or expired_for_absolute:
        session.invalidate()
        _reset_session_runtime(session, policy=policy, now=timestamp)
        return session

    _replace_session_meta(
        session,
        {
            "created_at": created_at,
            "last_seen": timestamp,
            "absolute_expiry": absolute_expiry,
            "remember_me": remember_me,
        },
    )
    return session


def hydrate_request(
    request: "Request | None",
    *,
    app=None,
    raw_cookie: str | None = None,
) -> "Request":
    from .request import Request

    if request is None:
        request = Request(path="/", method="GET")

    cookies = dict(getattr(request, "cookies", None) or {})
    if not cookies:
        cookie_header = raw_cookie
        if cookie_header is None:
            headers = getattr(request, "headers", {}) or {}
            cookie_header = headers.get("Cookie") or headers.get("cookie")
        cookies = parse_cookie_header(cookie_header)
    request.cookies = cookies

    session = getattr(request, "session", None)
    cookie_session_id = cookies.get(SESSION_COOKIE_NAME)
    explicit_session_id = getattr(request, "session_id", None)
    cookie_present = bool(cookie_session_id or explicit_session_id)
    resolved_session_id = (
        getattr(session, "id", None)
        or explicit_session_id
        or (cookie_session_id.strip() if isinstance(cookie_session_id, str) and cookie_session_id.strip() else None)
        or _generate_session_id()
    )

    if session is None or getattr(session, "id", None) != resolved_session_id:
        store = resolve_session_store(app)
        snapshot = store.load(resolved_session_id)
        session = RequestSession(
            resolved_session_id,
            snapshot,
            cookie_present=cookie_present,
            request=request,
        )
    else:
        session.bind(request)
    request.session = session
    request.session_id = session.id

    apply_session_policy(session, app=app)

    auth = resolve_auth_service(app)
    request.user = auth.load_user(session, request)
    request.active_profile = auth.load_active_profile(request.user, session, request)
    return request


def commit_request_session(
    request: "Request | None",
    *,
    app=None,
    allow_cookie_write: bool = True,
) -> list[tuple[str, str]]:
    if request is None or getattr(request, "session", None) is None:
        return []

    session = request.session
    store = resolve_session_store(app)

    if session.invalidated_from:
        store.delete(session.invalidated_from)
        if session.has_data() or session.dirty():
            store.save(session)
    elif session.rotated_from:
        if session.has_data():
            store.rotate(session)
        else:
            store.delete(session.rotated_from)
    elif session.has_data() or session.dirty():
        store.save(session)

    headers: list[tuple[str, str]] = []
    if allow_cookie_write and session.should_set_cookie():
        headers.append(
            session_cookie_header(
                session.id,
                max_age=_session_cookie_max_age(session, resolve_session_policy(app)),
            )
        )
    session.mark_committed()
    return headers


def build_auth_snapshot(request: "Request | None", *, app=None) -> dict:
    from .request import Request

    hydrated = hydrate_request(request or Request(path="/", method="GET"), app=app)
    auth = resolve_auth_service(app)
    public = auth.public_snapshot(hydrated.user, hydrated.session, hydrated) or {}
    viewer = public.get("viewer")
    active_profile = public.get("active_profile")

    return {
        "authenticated": bool(public.get("authenticated", hydrated.user is not None)),
        "viewer": dict(viewer) if isinstance(viewer, dict) else None,
        "active_profile": dict(active_profile) if isinstance(active_profile, dict) else None,
        "session": {"id": hydrated.session.id},
    }


def inject_auth_data(data, request: "Request | None", *, app=None) -> dict:
    payload = data if isinstance(data, dict) else {"value": data}
    result = dict(payload)
    result["__sprag_auth__"] = build_auth_snapshot(request, app=app)
    return result
