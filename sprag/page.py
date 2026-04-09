"""Route page manifests."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Page:
    path: str
    controller: type
    screen: type
    mode: str = "hybrid"
    name: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    shell: object = None
    static_paths: object = None

    def __post_init__(self):
        if not self.path.startswith("/"):
            raise ValueError(f"SPRAG page path must start with '/': {self.path!r}")
        if self.mode not in {"document", "hybrid", "spa"}:
            raise ValueError(
                f"SPRAG page mode must be one of document|hybrid|spa: {self.mode!r}"
            )

def page(
    *,
    path,
    controller,
    screen,
    mode="hybrid",
    name=None,
    metadata=None,
    shell=None,
    css=None,
    static_paths=None,
):
    """Create a route page manifest."""
    if css is not None:
        from .shell import shell as build_shell

        shell = build_shell(shell, css=css)
    return Page(
        path=path,
        controller=controller,
        screen=screen,
        mode=mode,
        name=name,
        metadata=metadata or {},
        shell=shell,
        static_paths=static_paths,
    )
