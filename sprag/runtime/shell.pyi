"""Type stubs for sprag.runtime.shell."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

from .assets import ModuleImport, SurfaceAssets


@dataclass(frozen=True)
class Shell:
    """Shared document chrome and surface assets for pages and mounts."""

    template: Optional[str] = ...
    css: tuple[str, ...] = ...
    js: tuple[object, ...] = ...
    modules: dict[str, ModuleImport] = ...
    slot: str = ...


def shell(
    base: Any = ...,
    *,
    template: Optional[str] = ...,
    css: Any = ...,
    js: Any = ...,
    modules: Any = ...,
    slot: str = ...,
) -> Shell: ...
def apply_shell(
    body_html: str,
    *,
    app: Any = ...,
    surface_shell: Any = ...,
    project_root: Optional[Union[str, Path]] = ...,
    app_shell: Any = ...,
    document_path: Optional[str] = ...,
) -> tuple[str, SurfaceAssets]: ...
