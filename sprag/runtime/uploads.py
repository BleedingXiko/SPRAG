"""Chunked upload session manager for SPRAG v2 uploads.

Files below threshold use the existing single-POST path. Files at or above
threshold use a chunked protocol: negotiate -> init -> chunk* -> auto-finalize.

Built as a Specter ``Service`` — gevent-native concurrency, lifecycle-managed
stale cleanup, and deterministic teardown of temp directories.
"""

from __future__ import annotations

import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from gevent.lock import BoundedSemaphore

from specter import Service

from .request import UploadedFile


@dataclass
class UploadFileSpec:
    """Describes one file expected in a chunked upload session."""

    name: str
    filename: str
    content_type: str | None = None
    chunks_expected: int = 0


@dataclass
class UploadSession:
    """Tracks state for a single chunked upload in progress."""

    upload_id: str
    route: str
    action: str
    payload: dict
    file_specs: list[UploadFileSpec]
    chunk_size: int
    chunks_expected: int
    temp_dir: Path
    created_at: float
    session_id: str | None = None
    chunks_received: set = field(default_factory=set)

    @property
    def is_complete(self) -> bool:
        return len(self.chunks_received) >= self.chunks_expected


class UploadSessionManager(Service):
    """Specter Service that manages chunked upload sessions.

    Chunks are stored as individual files on disk under a per-session temp
    directory. When the last chunk lands, the caller assembles the files and
    dispatches the controller action.

    Uses a gevent ``BoundedSemaphore`` for session state access and an
    ``interval`` timer for periodic stale-session cleanup.
    """

    def __init__(
        self,
        *,
        temp_root: str | Path | None = None,
        chunk_size: int = 2 * 1024 * 1024,
        stale_timeout: int = 3600,
    ):
        super().__init__("sprag.uploads", {"active_sessions": 0})
        self._temp_root = Path(temp_root) if temp_root else Path("/tmp/sprag_uploads")
        self._chunk_size = chunk_size
        self._stale_timeout = stale_timeout
        self._sessions: dict[str, UploadSession] = {}
        self._lock = BoundedSemaphore(1)

    def on_start(self):
        self.interval(self._cleanup_stale, 60.0)

    def on_stop(self):
        with self._lock:
            all_ids = list(self._sessions.keys())
        for uid in all_ids:
            self._remove_session(uid)

    @property
    def chunk_size(self) -> int:
        return self._chunk_size

    @property
    def threshold(self) -> int:
        return self._chunk_size

    def negotiate(self) -> dict:
        """Return negotiation payload for the browser client."""
        return {
            "chunk_size": self._chunk_size,
            "threshold": self.threshold,
        }

    def init_session(
        self,
        *,
        route: str,
        action: str,
        payload: dict,
        file_specs: list[dict],
        session_id: str | None = None,
    ) -> UploadSession:
        """Create a new upload session and return it."""
        upload_id = uuid.uuid4().hex
        temp_dir = self._temp_root / upload_id
        temp_dir.mkdir(parents=True, exist_ok=True)

        specs = []
        total_chunks = 0
        for spec in file_specs:
            size = spec.get("size", 0)
            file_chunks = max(1, -(-size // self._chunk_size))  # ceil division
            specs.append(UploadFileSpec(
                name=spec["name"],
                filename=spec["filename"],
                content_type=spec.get("content_type"),
                chunks_expected=file_chunks,
            ))
            total_chunks += file_chunks

        session = UploadSession(
            upload_id=upload_id,
            route=route,
            action=action,
            payload=payload,
            file_specs=specs,
            chunk_size=self._chunk_size,
            chunks_expected=total_chunks,
            temp_dir=temp_dir,
            created_at=time.time(),
            session_id=session_id,
        )

        with self._lock:
            self._sessions[upload_id] = session
            self.set_state({"active_sessions": len(self._sessions)})

        return session

    def receive_chunk(
        self,
        upload_id: str,
        file_index: int,
        chunk_index: int,
        data: bytes,
    ) -> tuple[UploadSession, bool]:
        """Store a chunk on disk and return (session, is_complete)."""
        with self._lock:
            session = self._sessions.get(upload_id)
            if session is None:
                raise KeyError(f"Unknown upload session: {upload_id}")

        chunk_path = session.temp_dir / f"{file_index}_{chunk_index}"
        chunk_path.write_bytes(data)

        chunk_key = (file_index, chunk_index)
        with self._lock:
            session.chunks_received.add(chunk_key)
            is_complete = session.is_complete

        return session, is_complete

    def assemble_files(self, upload_id: str) -> list[UploadedFile]:
        """Read chunks from disk and build UploadedFile instances."""
        with self._lock:
            session = self._sessions.get(upload_id)
            if session is None:
                raise KeyError(f"Unknown upload session: {upload_id}")

        files = []
        for file_idx, spec in enumerate(session.file_specs):
            parts = []
            for chunk_idx in range(spec.chunks_expected):
                chunk_path = session.temp_dir / f"{file_idx}_{chunk_idx}"
                parts.append(chunk_path.read_bytes())
            data = b"".join(parts)
            files.append(UploadedFile(
                name=spec.name,
                filename=spec.filename,
                content_type=spec.content_type,
                data=data,
            ))

        return files

    def cancel(self, upload_id: str) -> None:
        """Remove session and its temp directory."""
        self._remove_session(upload_id)

    def get_session(self, upload_id: str) -> UploadSession | None:
        with self._lock:
            return self._sessions.get(upload_id)

    def _remove_session(self, upload_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(upload_id, None)
            self.set_state({"active_sessions": len(self._sessions)})
        if session is not None and session.temp_dir.exists():
            shutil.rmtree(session.temp_dir, ignore_errors=True)

    def _cleanup_stale(self):
        """Remove sessions older than the stale timeout."""
        cutoff = time.time() - self._stale_timeout
        stale_ids = []
        with self._lock:
            for uid, session in self._sessions.items():
                if session.created_at < cutoff:
                    stale_ids.append(uid)
        for uid in stale_ids:
            self._remove_session(uid)
