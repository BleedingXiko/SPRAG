"""Type stubs for sprag.runtime.session."""

from dataclasses import dataclass
from typing import Any, Optional

from .request import Request


@dataclass(frozen=True)
class SessionPolicy:
    """Cookie lifetime settings for SPRAG request sessions.

    Set idle, absolute, and remember-me TTLs in seconds on ``App`` when the
    default session cookie lifetime needs to change.
    """

    idle_ttl_seconds: Optional[int] = ...
    absolute_ttl_seconds: Optional[int] = ...
    remember_me_ttl_seconds: Optional[int] = ...


class RequestSession:
    """Mutable per-request session store.

    Use ``get/set/patch/delete/clear`` for JSON-safe session values. Call
    ``rotate()`` after login and ``invalidate()`` on logout.
    """

    def __init__(
        self,
        session_id: Optional[str] = ...,
        data: Optional[dict[str, Any]] = ...,
        *,
        cookie_present: bool = ...,
        request: Optional[Request] = ...,
    ) -> None: ...
    @property
    def id(self) -> str: ...
    @property
    def rotated_from(self) -> Optional[str]: ...
    @property
    def invalidated_from(self) -> Optional[str]: ...
    def bind(self, request: Optional[Request]) -> "RequestSession": ...
    def get(self, key: str, default: Any = ...) -> Any: ...
    def set(self, key: str, value: Any) -> Any: ...
    def patch(self, values: Optional[dict[str, Any]]) -> "RequestSession": ...
    def delete(self, key: str) -> Any: ...
    def clear(self) -> "RequestSession": ...
    def snapshot(self) -> dict[str, Any]: ...
    def rotate(self) -> str: ...
    def invalidate(self) -> str: ...
    def should_set_cookie(self) -> bool: ...
    def dirty(self) -> bool: ...
    def has_data(self) -> bool: ...
    def mark_committed(self) -> None: ...


class InMemorySessionStore:
    """Default in-process session persistence.

    Good for local development and demos. Provide your own ``session_store``
    provider on ``App`` when sessions must survive process restarts.
    """

    name: str

    def __init__(self) -> None: ...
    def load(self, session_id: str) -> dict[str, Any]: ...
    def save(self, session: RequestSession) -> None: ...
    def delete(self, session_id: str) -> None: ...
    def rotate(self, session: RequestSession) -> None: ...


class AnonymousAuthService:
    """Fallback auth provider for anonymous apps.

    Replace this with an ``auth`` provider on ``App`` to load users, profiles,
    authorization rules, login sessions, and browser-safe auth snapshots.
    """

    name: str

    def load_user(self, session: RequestSession, request: Request) -> Any: ...
    def load_active_profile(self, user: Any, session: RequestSession, request: Request) -> Any: ...
    def authorize(
        self,
        user: Any,
        active_profile: Any,
        session: RequestSession,
        request: Request,
        *,
        roles: Any = ...,
        permissions: Any = ...,
    ) -> bool: ...
    def public_snapshot(self, user: Any, session: RequestSession, request: Request) -> dict[str, Any]: ...
    def login_session(
        self,
        user: Any,
        session: RequestSession,
        request: Request,
        extra_session: Optional[dict[str, Any]] = ...,
    ) -> None: ...
    def set_active_profile(
        self,
        profile: Any,
        user: Any,
        session: RequestSession,
        request: Request,
        extra_session: Optional[dict[str, Any]] = ...,
    ) -> None: ...
