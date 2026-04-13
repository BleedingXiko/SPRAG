"""SPRAG request object for controller access to HTTP request data."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class UploadedFile:
    """Uploaded multipart file exposed to SPRAG controllers."""

    name: str
    filename: str
    content_type: str | None = None
    data: bytes = b""
    headers: dict = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.data)

    def read(self) -> bytes:
        return self.data

    def text(self, encoding: str = "utf-8", errors: str = "strict") -> str:
        return self.data.decode(encoding, errors)

    def save(self, path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.data)
        return destination


@dataclass
class Request:
    """Request snapshot plus per-request runtime session/auth state."""

    path: str
    params: dict = field(default_factory=dict)
    query: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    method: str = "GET"
    body: bytes = b""
    session_id: str | None = None
    cookies: dict = field(default_factory=dict)
    session: object | None = None
    user: object = None
    content_type: str | None = None
    form: dict = field(default_factory=dict)
    files: dict = field(default_factory=dict)

    def file(self, name: str, default=None):
        value = self.files.get(name, default)
        if isinstance(value, list):
            return value[0] if value else default
        return value

    def files_list(self, name: str) -> list[UploadedFile]:
        value = self.files.get(name)
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]
