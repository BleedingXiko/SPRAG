"""Type stubs for sprag.runtime.assets."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Script:
    """Browser script asset declaration.

    Use ``script("path.js")`` for classic scripts and
    ``script("path.js", module=True)`` for module scripts.
    """

    src: str
    module: bool = ...


@dataclass(frozen=True)
class ModuleImport:
    """ESM import alias for browser-authored Python.

    Declare in ``page(..., modules={...})``, ``mount(..., modules={...})``, or
    shell/app modules, then access it through ``imports.alias`` in Module code.
    """

    src: str
    export: str = ...


@dataclass(frozen=True)
class Asset:
    """Resolved static or surface asset."""

    kind: str
    web_path: str
    source_path: Optional[Path] = ...
    module: bool = ...
    external: bool = ...


@dataclass(frozen=True)
class ResolvedModuleImport:
    """Resolved browser ESM import for a specific surface alias."""

    alias: str
    src: str
    export: str = ...
    source_path: Optional[Path] = ...
    external: bool = ...


@dataclass(frozen=True)
class SurfaceAssets:
    """Resolved CSS/JS assets for a single page or mount surface."""

    css: tuple[Asset, ...] = ...
    js: tuple[Asset, ...] = ...
    modules: tuple[ResolvedModuleImport, ...] = ...
    static: tuple[Asset, ...] = ...

    def all(self) -> tuple[Asset, ...]: ...


def script(src: str, *, module: bool = ...) -> Script:
    """Declare a JS file to load on a page, mount, or shell."""
    ...
def module(src: str, export: str = ...) -> ModuleImport:
    """Declare an ESM import alias for ``imports.<alias>`` browser code."""
    ...
