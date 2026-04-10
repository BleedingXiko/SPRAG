"""App loading helpers for CLI-friendly SPRAG workflows."""

from __future__ import annotations

import importlib


DEFAULT_APP_TARGETS = (
    "app:app",
    "app.app:app",
)


def load_app(target=None):
    """Load a SPRAG App instance from ``module:attribute`` or auto-detect one."""
    candidates = (target,) if target else DEFAULT_APP_TARGETS
    last_error = None

    for candidate in candidates:
        if not candidate:
            continue
        try:
            return candidate, _load_target(candidate)
        except Exception as exc:  # pragma: no cover - used in CLI resolution
            last_error = exc

    candidate_list = ", ".join(DEFAULT_APP_TARGETS)
    if target:
        raise RuntimeError(f"Unable to load SPRAG app target {target!r}: {last_error}")
    raise RuntimeError(
        "Unable to auto-detect a SPRAG app. "
        f"Tried: {candidate_list}. "
        "Create an `app` package exporting `app = App(...)`, or pass `--app module:attr`."
    ) from last_error


def _load_target(target):
    if ":" not in target:
        raise ValueError(
            f"SPRAG app target must look like 'module:attribute', got {target!r}"
        )
    module_name, attr_name = target.split(":", 1)
    module = importlib.import_module(module_name)
    app = getattr(module, attr_name)
    return app
