"""Type stubs for sprag.runtime.mount."""

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Union


@dataclass(frozen=True)
class Mount:
    """Client-owned app mounted at a server URL.

    Use a Mount when the browser owns the whole surface from boot. Provide the
    root Component and optional Module/boot Controller.
    """

    path: str
    component: type
    module: Optional[type] = ...
    boot: Optional[type] = ...
    name: Optional[str] = ...
    metadata: dict = ...
    shell: Any = ...
    modules: dict = ...
    providers: dict = ...


def mount(
    path: str,
    *,
    component: type,
    module: Optional[type] = ...,
    boot: Optional[type] = ...,
    name: Optional[str] = ...,
    metadata: Optional[Mapping[str, Any]] = ...,
    shell: Any = ...,
    css: Optional[Union[str, list]] = ...,
    js: Optional[Union[str, list]] = ...,
    modules: Optional[Mapping[str, Any]] = ...,
    providers: Optional[Mapping[str, Any]] = ...,
) -> Mount:
    """Declare a client-owned app mount.

    Use this in ``app/mounts/.../mount.py`` with ``path`` and ``component``.
    Add ``module`` for browser lifecycle logic and ``boot`` for server data.
    """
    ...
