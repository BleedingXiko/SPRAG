"""Environment loading/helpers for SPRAG apps."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


DEFAULT_ENV_FILES = (".env", ".env.local")
DEFAULT_PUBLIC_PREFIX = "SPRAG_PUBLIC_"
_MISSING = object()
_LOADED_ROOTS: set[Path] = set()

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_project_env(module_name: str | None = None, *, override: bool = False) -> tuple[Path, ...]:
    """Load ``.env`` files for the current SPRAG app root."""
    loaded: list[Path] = []
    for root in candidate_env_roots(module_name):
        if root in _LOADED_ROOTS:
            continue
        loaded.extend(load_env(root, override=override))
        _LOADED_ROOTS.add(root)
    return tuple(loaded)


def load_env(root: str | Path, *, override: bool = False, files=None) -> tuple[Path, ...]:
    """Load env files from ``root`` into ``os.environ``."""
    root_path = Path(root).resolve()
    files = tuple(files or DEFAULT_ENV_FILES)
    merged: dict[str, str] = {}
    loaded: list[Path] = []

    for name in files:
        env_path = root_path / name
        if not env_path.is_file():
            continue
        merged.update(parse_env_file(env_path))
        loaded.append(env_path)

    for key, value in merged.items():
        if override or key not in os.environ:
            os.environ[key] = value

    return tuple(loaded)


def parse_env_file(path: str | Path) -> dict[str, str]:
    """Parse a simple dotenv file."""
    env_path = Path(path)
    parsed: dict[str, str] = {}
    for lineno, raw_line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise ValueError(f"Invalid env line in {env_path}:{lineno}: {raw_line!r}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not _ENV_KEY_RE.match(key):
            raise ValueError(f"Invalid env key in {env_path}:{lineno}: {key!r}")
        parsed[key] = _parse_env_value(value)
    return parsed


def env(name: str, default=_MISSING, *, cast=None, required: bool = False):
    """Read an environment variable.

    Use ``default`` for missing values, ``required=True`` to fail loudly, and
    ``cast=...`` to convert the string before returning it.
    """
    if name in os.environ:
        raw_value = os.environ[name]
    elif required or default is _MISSING:
        raise KeyError(f"Missing required environment variable {name!r}.")
    else:
        return default

    if cast is None or cast is str:
        return raw_value
    if cast is bool:
        return _coerce_bool(name, raw_value)
    try:
        return cast(raw_value)
    except Exception as exc:
        raise ValueError(
            f"Could not cast environment variable {name!r} with {getattr(cast, '__name__', cast)!r}: {exc}"
        ) from exc


def public_env(prefix: str = DEFAULT_PUBLIC_PREFIX) -> dict[str, str]:
    """Return env vars safe to expose to the browser by prefix."""
    return {
        key: value
        for key, value in os.environ.items()
        if isinstance(key, str) and key.startswith(prefix)
    }


def candidate_env_roots(module_name: str | None = None) -> tuple[Path, ...]:
    """Return likely project roots for dotenv loading."""
    roots: list[Path] = []

    def add(pathlike):
        if not pathlike:
            return
        path = Path(pathlike).resolve()
        if path.exists() and path not in roots:
            roots.append(path)

    add(Path.cwd())
    if module_name:
        root_name = module_name.split(".", 1)[0]
        for entry in sys.path:
            if not entry:
                continue
            base = Path(entry).resolve()
            if (base / root_name).is_dir() or (base / f"{root_name}.py").is_file():
                add(base)
    return tuple(roots)


def _parse_env_value(value: str) -> str:
    if not value:
        return ""
    if value[0] == value[-1] and value[0] in {"'", '"'} and len(value) >= 2:
        inner = value[1:-1]
        if value[0] == '"':
            return bytes(inner, "utf-8").decode("unicode_escape")
        return inner
    if " #" in value:
        value = value.split(" #", 1)[0]
    return value.strip()


def _coerce_bool(name: str, raw_value: str) -> bool:
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"Could not cast environment variable {name!r} to bool: {raw_value!r}."
    )
