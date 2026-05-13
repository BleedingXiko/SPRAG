"""SPRAG request object for controller access to HTTP request data."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class UploadedFile:
    """Uploaded multipart file available in controller actions.

    Use ``read()`` for bytes, ``text()`` for decoded text, ``save(path)`` to
    persist it, and ``size`` for the byte count.
    """

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

    @classmethod
    def from_path(cls, path, *, name: str, filename: str, content_type: str | None = None) -> "UploadedFile":
        """Construct an UploadedFile by reading assembled bytes from disk."""
        data = Path(path).read_bytes()
        return cls(name=name, filename=filename, content_type=content_type, data=data)


@dataclass
class Request:
    """Current HTTP request for ``Controller.load()`` and actions.

    Read route params from ``params``, query values from ``query``, form fields
    from ``form``, uploads from ``file(...)``/``files_list(...)``, and auth
    context from ``session``, ``user``, and ``active_profile``.
    """

    path: str
    params: dict = field(default_factory=dict)
    query: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    method: str = "GET"
    body: bytes = b""
    request_id: str | None = None
    session_id: str | None = None
    cookies: dict = field(default_factory=dict)
    session: object | None = None
    user: object = None
    active_profile: object = None
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
