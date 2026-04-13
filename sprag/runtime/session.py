"""Session and auth helpers for SPRAG runtime requests."""

from __future__ import annotations

import secrets
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .request import Request


SESSION_COOKIE_NAME = "SPRAG_SID"


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


def session_cookie_header(session_id: str) -> tuple[str, str]:
    return (
        "Set-Cookie",
        f"{SESSION_COOKIE_NAME}={session_id}; Path=/; HttpOnly; SameSite=Lax",
    )


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
        self._sessions: dict[str, dict] = {}

    def load(self, session_id):
        snapshot = self._sessions.get(str(session_id))
        return dict(snapshot or {})

    def save(self, session: RequestSession):
        snapshot = session.snapshot()
        if snapshot:
            self._sessions[session.id] = snapshot
            return
        self._sessions.pop(session.id, None)

    def delete(self, session_id):
        self._sessions.pop(str(session_id), None)

    def rotate(self, session: RequestSession):
        previous = session.rotated_from
        if previous:
            self.delete(previous)
        self.save(session)


class AnonymousAuthService:
    """Default auth adapter that exposes an anonymous public snapshot."""

    name = "auth"

    def load_user(self, session, request):
        return None

    def public_snapshot(self, user, session, request) -> dict:
        return {}

    def login_session(self, user, session, request, extra_session=None) -> None:
        raise RuntimeError(
            "Controller.login(...) requires an app-level 'auth' provider."
        )


_DEFAULT_SESSION_STORE = InMemorySessionStore()
_DEFAULT_AUTH_SERVICE = AnonymousAuthService()


def resolve_session_store(app=None):
    providers = getattr(app, "providers", None) or {}
    return providers.get("session_store") or _DEFAULT_SESSION_STORE


def resolve_auth_service(app=None):
    providers = getattr(app, "providers", None) or {}
    return providers.get("auth") or _DEFAULT_AUTH_SERVICE


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

    auth = resolve_auth_service(app)
    request.user = auth.load_user(session, request)
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
        headers.append(session_cookie_header(session.id))
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
