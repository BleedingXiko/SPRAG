"""App boot and build scaffolding."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from specter import registry

from .compiler import build_web_preview
from .discovery import discover_pages


@dataclass
class App:
    services: list = field(default_factory=list)
    routes: str = "app.routes"
    project_root: Optional[str] = None

    def __post_init__(self):
        self._pages = None
        self._booted = False

    def pages(self):
        if self._pages is None:
            self._pages = discover_pages(self.routes)
        return self._pages

    def invalidate_pages(self):
        self._pages = None

    def boot(self):
        """Provide services into Specter's registry and start them."""
        if self._booted:
            return
        for svc in self.services:
            registry.provide(svc.name, svc, owner=None)
            if not svc.running:
                svc.start()
        self._booted = True

    def shutdown(self):
        """Stop services in reverse order and clear registry entries."""
        for svc in reversed(self.services):
            if svc.running:
                svc.stop()
            if registry.has(svc.name):
                registry.unregister(svc.name)
        self._booted = False

    def build(self, output_dir=".sprag"):
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        self.invalidate_pages()
        did_boot = not self._booted
        if did_boot:
            self.boot()
        try:
            pages = self.pages()
            return build_web_preview(pages, output_path, app=self)
        finally:
            if did_boot:
                self.shutdown()

    def serve(self, *, host="127.0.0.1", port=8000, build_dir=".sprag", max_workers=16):
        """Boot services, build assets, and start the application server."""
        from .http_server import serve_sprag_app

        self.boot()
        self.build(build_dir)
        serve_sprag_app(self, build_dir, host=host, port=port, max_workers=max_workers)
