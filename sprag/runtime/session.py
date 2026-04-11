"""Internal session helpers for SPRAG runtime requests."""

from __future__ import annotations

import secrets
from http.cookies import SimpleCookie


SESSION_COOKIE_NAME = "SPRAG_SID"


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
    return secrets.token_urlsafe(24), True


def session_cookie_header(session_id: str) -> tuple[str, str]:
    return (
        "Set-Cookie",
        f"{SESSION_COOKIE_NAME}={session_id}; Path=/; HttpOnly; SameSite=Lax",
    )
