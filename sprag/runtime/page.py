"""Route page manifests."""

from dataclasses import dataclass, field
from typing import Optional

from .assets import normalize_module_imports


@dataclass(frozen=True)
class Page:
    """Route manifest that binds a URL path to a Controller and Screen."""

    path: str
    controller: type
    screen: type
    mode: str = "hybrid"
    name: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    shell: object = None
    modules: dict = field(default_factory=dict)
    static_paths: object = None
    providers: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.path.startswith("/"):
            raise ValueError(f"SPRAG page path must start with '/': {self.path!r}")
        if self.mode not in {"document", "hybrid"}:
            raise ValueError(
                f"SPRAG page mode must be one of document|hybrid: {self.mode!r}"
            )
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        object.__setattr__(self, "modules", normalize_module_imports(self.modules))

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
    js=None,
    modules=None,
    static_paths=None,
    providers=None,
):
    """Create a route page manifest."""
    if css is not None or js is not None:
        from .shell import shell as build_shell

        shell = build_shell(shell, css=css, js=js)
    return Page(
        path=path,
        controller=controller,
        screen=screen,
        mode=mode,
        name=name,
        metadata=metadata or {},
        shell=shell,
        modules=modules or {},
        static_paths=static_paths,
        providers=providers or {},
    )
