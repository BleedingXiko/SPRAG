"""Type stubs for sprag.runtime.mount."""

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Union


@dataclass(frozen=True)
class Mount:
    """Server-known URL that boots a Ragot-owned client app."""

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
) -> Mount: ...
