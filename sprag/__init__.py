"""Public SPRAG framework surface."""

__version__ = "0.1.14"

from .runtime import dom
from .runtime.app import App
from .runtime.assets import ModuleImport, Script, module, script
from .runtime.content import ContentDocument, load_markdown_document, load_markdown_tree, slugify
from .runtime.env import env, public_env
from .runtime.mount import Mount, mount
from .runtime.page import Page, page
from .runtime.request import Request, UploadedFile
from .runtime.session import AnonymousAuthService, InMemorySessionStore, SessionPolicy
from .runtime.shell import Shell, shell
from .runtime.server import (
    Redirect,
    redirect,
    socket_target,
    action,
    requires_auth,
    # Core (already exposed)
    Controller,
    Field,
    Outcome,
    Schema,
    Service,
    registry,
    # HTTP / routing
    HTTPError,
    Router,
    expect_json,
    json_endpoint,
    require_fields,
    route,
    # Operations
    Operation,
    OperationError,
    # State
    Cache,
    Model,
    Store,
    create_cache,
    create_model,
    create_store,
    # Communication
    bus,
    # Workers
    QueueService,
    # Realtime
    Handler,
    SocketIngress,
    # System
    ManagedProcess,
    Watcher,
    WatcherError,
    start_process,
    # Orchestration
    ServiceManager,
    boot,
)
from .runtime.stores import StoreBridge, declared_stores, store
from .runtime.ui import ui
from .runtime.urls import join_url
from .runtime.browser import (
    Component,
    Module,
    Screen,
    animate,
    browser,
    createSelector,
    createStateStore,
    debounce,
    hydrate,
    infinite_scroll,
    imports,
    ref,
    ssr,
    throttle,
    virtual_scroll,
)

__all__ = [
    "__version__",
    # App
    "App",
    "ContentDocument",
    "Mount",
    "Page",
    "Request",
    "Redirect",
    "Shell",
    "UploadedFile",
    "AnonymousAuthService",
    "InMemorySessionStore",
    "SessionPolicy",
    "action",
    "mount",
    "load_markdown_document",
    "load_markdown_tree",
    "page",
    "redirect",
    "socket_target",
    "requires_auth",
    "env",
    "public_env",
    # Web authoring
    "Component",
    "Module",
    "Screen",
    "dom",
    "hydrate",
    "ssr",
    "ui",
    # State bridge (one declaration, two runtimes)
    "StoreBridge",
    "declared_stores",
    "store",
    "ModuleImport",
    "Script",
    "module",
    "script",
    "slugify",
    "join_url",
    # Client-side decorators (justified — see sprag/browser.py)
    "animate",
    "browser",
    "createSelector",
    "createStateStore",
    "debounce",
    "infinite_scroll",
    "imports",
    "ref",
    "throttle",
    "virtual_scroll",
    # Core server
    "Controller",
    "Field",
    "Outcome",
    "Schema",
    "Service",
    "registry",
    # HTTP / routing
    "HTTPError",
    "Router",
    "expect_json",
    "json_endpoint",
    "require_fields",
    "route",
    "shell",
    # Operations
    "Operation",
    "OperationError",
    # State
    "Cache",
    "Model",
    "Store",
    "create_cache",
    "create_model",
    "create_store",
    # Communication
    "bus",
    # Workers
    "QueueService",
    # Realtime
    "Handler",
    "SocketIngress",
    # System
    "ManagedProcess",
    "Watcher",
    "WatcherError",
    "start_process",
    # Orchestration
    "ServiceManager",
    "boot",
]
