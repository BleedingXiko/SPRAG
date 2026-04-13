"""App boot and build scaffolding."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from specter import registry

from .assets import normalize_module_imports
from .discovery import discover_surfaces
from .session import AnonymousAuthService, InMemorySessionStore, SessionPolicy
from .socket_bridge import SpragSocketBridge, controller_uses_socket_bridge


@dataclass
class App:
    providers: dict = field(default_factory=dict)
    routes: str = "app.routes"
    mounts_package: str = "app.mounts"
    project_root: Optional[str] = None
    shell: object = None
    modules: dict = field(default_factory=dict)
    server_mode: str = "auto"
    session_policy: SessionPolicy = field(default_factory=SessionPolicy)

    def __post_init__(self):
        if self.server_mode not in {"auto", "wsgi", "websocket"}:
            raise ValueError(
                "App(server_mode=...) must be 'auto', 'wsgi', or 'websocket', "
                f"got {self.server_mode!r}."
            )
        self.providers = dict(self.providers or {})
        self.providers.setdefault("session_store", InMemorySessionStore())
        self.providers.setdefault("auth", AnonymousAuthService())
        self.session_policy = (
            self.session_policy
            if isinstance(self.session_policy, SessionPolicy)
            else SessionPolicy(**dict(self.session_policy or {}))
        )
        self.modules = normalize_module_imports(self.modules)
        self._pages = None
        self._mounts = None
        self._controllers = {}
        self._controller_registry_keys = []
        self._socket_bridge = None
        self._booted = False

    def _provider_registry_keys(self, key, provider):
        keys = [key]
        legacy_name = getattr(provider, "name", None)
        if isinstance(legacy_name, str) and legacy_name and legacy_name not in keys:
            keys.append(legacy_name)
        return keys

    def pages(self):
        self._ensure_surfaces()
        return self._pages

    def mounts(self):
        self._ensure_surfaces()
        return self._mounts

    def _ensure_surfaces(self):
        if self._pages is None or self._mounts is None:
            self._pages, self._mounts = discover_surfaces(self.routes, self.mounts_package)

    def invalidate_pages(self):
        self._shutdown_controllers()
        self._pages = None
        self._mounts = None

    def boot(self):
        """Provide app providers/controllers into Specter's registry and start them."""
        if self._booted:
            return
        self._ensure_surfaces()
        self._ensure_socket_runtime()
        for name, svc in self.providers.items():
            for registry_key in self._provider_registry_keys(name, svc):
                registry.provide(registry_key, svc, owner=None, replace=True)
            if not getattr(svc, "running", True) and hasattr(svc, "start"):
                svc.start()
        self._ensure_controllers()
        self._booted = True

    def shutdown(self):
        """Stop controllers/providers in reverse order and clear registry entries."""
        self._shutdown_controllers()
        for name, svc in reversed(list(self.providers.items())):
            if getattr(svc, "running", False) and hasattr(svc, "stop"):
                svc.stop()
            for registry_key in reversed(self._provider_registry_keys(name, svc)):
                if registry.has(registry_key):
                    registry.unregister(registry_key)
        self._shutdown_socket_runtime()
        self._booted = False

    def controller_for_page(self, page):
        """Return the lifecycle-owned controller instance for a page."""
        return self._ensure_controller(("page", page.path), page.controller)

    def controller_for_mount(self, mount):
        """Return the lifecycle-owned boot controller instance for a mount."""
        if mount.boot is None:
            return None
        return self._ensure_controller(("mount", mount.path), mount.boot)

    def _ensure_controllers(self):
        for _module_name, page in self._pages or []:
            self.controller_for_page(page)
        for _module_name, mount in self._mounts or []:
            if mount.boot is not None:
                self.controller_for_mount(mount)

    def _ensure_controller(self, key, controller_cls):
        controller = self._controllers.get(key)
        if controller is not None:
            return controller

        controller = controller_cls()
        if hasattr(controller, "bind_app"):
            controller.bind_app(self)
        else:
            controller.app = self

        registry_key = _controller_registry_key(key, controller)
        registry.provide(registry_key, controller, owner=controller, replace=True)
        self._controller_registry_keys.append(registry_key)

        if not controller.running:
            controller.start()

        if controller_uses_socket_bridge(controller_cls):
            controller.build_handler(self.socket_bridge())

        self._controllers[key] = controller
        return controller

    def _shutdown_controllers(self):
        for controller in reversed(list(self._controllers.values())):
            if getattr(controller, "running", False):
                controller.stop()
        self._controllers.clear()

        for key in reversed(self._controller_registry_keys):
            if registry.has(key):
                registry.unregister(key)
        self._controller_registry_keys.clear()

    def build(self, output_dir=".sprag"):
        from ..dev.build import build_web_preview

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        self.invalidate_pages()
        did_boot = not self._booted
        if did_boot:
            self.boot()
        try:
            pages = self.pages()
            return build_web_preview(pages, output_path, app=self, mounts=self.mounts())
        finally:
            if did_boot:
                self.shutdown()

    def serve(
        self,
        *,
        host="127.0.0.1",
        port=8000,
        build_dir=".sprag",
        max_workers=16,
        server_mode: str | None = None,
    ):
        """Boot providers, build assets, and start the application server."""
        from .http import serve_sprag_app

        self.boot()
        self.build(build_dir)
        serve_sprag_app(
            self,
            build_dir,
            host=host,
            port=port,
            max_workers=max_workers,
            server_mode=server_mode,
        )

    def socket_bridge(self):
        """Return the app-owned socket transport bridge."""
        self._ensure_socket_runtime()
        return self._socket_bridge

    def _ensure_socket_runtime(self):
        if self._socket_bridge is None:
            self._socket_bridge = SpragSocketBridge(self)
        self._socket_bridge.provide_registry()

    def _shutdown_socket_runtime(self):
        if self._socket_bridge is None:
            return
        self._socket_bridge.close()
        self._socket_bridge.clear_registry()


def _controller_registry_key(key, controller):
    kind, path = key
    normalized = path.strip("/").replace("/", ".") or "root"
    return f"sprag.controller.{kind}.{normalized}.{controller.__class__.__name__}"
