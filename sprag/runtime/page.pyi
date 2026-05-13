"""Type stubs for sprag.runtime.page."""

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Union


@dataclass(frozen=True)
class Page:
    """Route manifest that binds a URL path to a Controller and Screen."""

    path: str
    controller: type
    screen: type
    mode: str = ...
    name: Optional[str] = ...
    metadata: dict = ...
    shell: Any = ...
    modules: dict = ...
    static_paths: Any = ...
    providers: dict = ...


def page(
    *,
    path: str,
    controller: type,
    screen: type,
    mode: str = ...,
    name: Optional[str] = ...,
    metadata: Optional[Mapping[str, Any]] = ...,
    shell: Any = ...,
    css: Optional[Union[str, list]] = ...,
    js: Optional[Union[str, list]] = ...,
    modules: Optional[Mapping[str, Any]] = ...,
    static_paths: Any = ...,
    providers: Optional[Mapping[str, Any]] = ...,
) -> Page: ...
