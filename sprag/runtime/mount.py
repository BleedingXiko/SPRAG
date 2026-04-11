"""Client app mount manifests for SPRAG."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Mount:
    """Server-known URL that boots a Ragot-owned client app."""

    path: str
    component: type
    module: Optional[type] = None
    boot: Optional[type] = None
    name: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    shell: object = None
    providers: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.path, str) or not self.path.startswith("/"):
            raise ValueError("Mount.path must start with '/'.")
        normalized = self.path.rstrip("/") or "/"
        object.__setattr__(self, "path", normalized)
        object.__setattr__(self, "metadata", dict(self.metadata or {}))


def mount(path, *, component, module=None, boot=None, name=None, metadata=None, shell=None, css=None, providers=None):
    """Declare a client app mount.

    A mount is not a route mode. It is a server URL that returns a boot
    document and lets Ragot create the root Component/Module in the browser.
    """
    if css is not None:
        from .shell import shell as build_shell

        shell = build_shell(shell, css=css)

    return Mount(
        path=path,
        component=component,
        module=module,
        boot=boot,
        name=name,
        metadata=metadata or {},
        shell=shell,
        providers=providers or {},
    )
