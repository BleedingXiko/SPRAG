"""SPRAG request object for controller access to HTTP request data."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Request:
    """Immutable snapshot of an incoming HTTP request."""

    path: str
    query: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    method: str = "GET"
    body: bytes = b""
