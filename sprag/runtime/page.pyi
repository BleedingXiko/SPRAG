"""Type stubs for sprag.runtime.page."""

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Union


@dataclass(frozen=True)
class Page:
    """Server-rendered route manifest.

    Binds ``path`` to a Controller and Screen. Use ``mode="hybrid"`` for SSR
    plus browser hydration, or ``mode="document"`` for static document output.
    """

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
) -> Page:
    """Declare a route page.

    Use this in ``app/routes/.../page.py`` with ``path``, ``controller``, and
    ``screen``. Add ``css``, ``js``, ``modules``, ``metadata``, or
    ``providers`` when the route needs surface-specific assets or services.
    """
    ...
