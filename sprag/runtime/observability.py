"""Small structured logging helpers for SPRAG runtime boundaries."""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path

LOGGER_NAME = "sprag.runtime"
logger = logging.getLogger(LOGGER_NAME)
logger.addHandler(logging.NullHandler())


def ensure_request_id(request) -> str | None:
    """Attach a stable request id to a request object when possible."""
    if request is None:
        return None
    request_id = getattr(request, "request_id", None)
    if request_id:
        return str(request_id)
    headers = getattr(request, "headers", {}) or {}
    candidate = headers.get("X-Request-Id") or headers.get("x-request-id")
    request_id = str(candidate).strip() if candidate else uuid.uuid4().hex[:12]
    try:
        request.request_id = request_id
    except Exception:
        pass
    return request_id


def log_runtime_event(event: str, *, level: str = "info", **fields):
    """Emit a JSON log line with a stable event name and plain fields."""
    payload = {
        "ts": round(time.time(), 6),
        "event": str(event),
    }
    for key, value in fields.items():
        if value is not None:
            payload[str(key)] = _coerce_value(value)
    logger.log(_level_number(level), json.dumps(payload, sort_keys=True, default=_json_default))
    return payload


def log_request_event(event: str, *, request=None, level: str = "info", **fields):
    """Emit a runtime event decorated with request-scoped fields."""
    request_fields = {}
    if request is not None:
        request_fields = {
            "request_id": ensure_request_id(request),
            "path": getattr(request, "path", None),
            "method": getattr(request, "method", None),
            "session_id": getattr(request, "session_id", None),
        }
    request_fields.update(fields)
    return log_runtime_event(event, level=level, **request_fields)


def _level_number(level: str) -> int:
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        resolved = getattr(logging, level.upper(), None)
        if isinstance(resolved, int):
            return resolved
    return logging.INFO


def _coerce_value(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BaseException):
        return f"{type(value).__name__}: {value}"
    if isinstance(value, dict):
        return {str(key): _coerce_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_coerce_value(item) for item in value]
    return repr(value)


def _json_default(value):
    return _coerce_value(value)
