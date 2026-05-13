"""Type stubs for sprag.runtime.env."""

from pathlib import Path
from typing import Any, Callable, Optional, TypeVar, Union

T = TypeVar("T")


def load_project_env(module_name: Optional[str] = ..., *, override: bool = ...) -> tuple[Path, ...]: ...
def load_env(root: Union[str, Path], *, override: bool = ..., files: Any = ...) -> tuple[Path, ...]: ...
def parse_env_file(path: Union[str, Path]) -> dict[str, str]: ...
def env(name: str, default: Any = ..., *, cast: Optional[Callable[[str], T]] = ..., required: bool = ...) -> Union[Any, T]:
    """Read an environment variable.

    Use ``default`` for missing values, ``required=True`` to fail loudly, and
    ``cast=...`` to convert the string before returning it.
    """
    ...
def public_env(prefix: str = ...) -> dict[str, str]:
    """Return env vars safe to expose to the browser by prefix."""
    ...
def candidate_env_roots(module_name: Optional[str] = ...) -> tuple[Path, ...]: ...
