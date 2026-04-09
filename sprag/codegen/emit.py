"""High-level emitters for the SPRAG build pipeline.

These functions are the public surface of the codegen package: the
SPRAG compiler invokes them to write the Ragot runtime, the generated
component / module sources, and the browser entry point that wires
hydration, the action client, and the event-source bus bridge.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..stores import StoreBridge
from .components import compile_component_class
from .dependencies import used_browser_class_refs
from .mappings import JSCodegenError
from .modules import compile_module_class


def emit_ragot_runtime(output_dir: Path, project_root: Path) -> None:
    vendor_dir = output_dir / "vendor"
    vendor_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = Path(__file__).resolve().parent.parent / "assets"
    runtime_source = assets_dir / "ragot.esm.min.js"
    (vendor_dir / "ragot.esm.min.js").write_text(
        runtime_source.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (vendor_dir / "RAGOT_LICENSE").write_text(
        (assets_dir / "RAGOT_LICENSE").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (vendor_dir / "RAGOT_NOTICE").write_text(
        (assets_dir / "RAGOT_NOTICE").read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def emit_generated_files(output_dir: Path, hydration_entries: list[dict], *, mount_entries=None) -> None:
    mount_entries = mount_entries or []
    generated_dir = output_dir / "generated"
    components_dir = generated_dir / "components"
    modules_dir = generated_dir / "modules"
    components_dir.mkdir(parents=True, exist_ok=True)
    modules_dir.mkdir(parents=True, exist_ok=True)

    component_classes = {}
    module_classes = {}
    for entry in hydration_entries:
        component_class = entry.get("component_class")
        module_class = entry.get("module_class")
        if component_class:
            _register_browser_class(component_classes, component_class, "Component")
        if module_class:
            _register_browser_class(module_classes, module_class, "Module")

    for entry in mount_entries:
        component_class = entry.get("root_component_class")
        module_class = entry.get("root_module_class")
        if component_class:
            _register_browser_class(component_classes, component_class, "Component")
        if module_class:
            _register_browser_class(module_classes, module_class, "Module")

    _collect_browser_dependencies(component_classes, module_classes)

    for name, component_class in component_classes.items():
        (components_dir / f"{name}.js").write_text(
            compile_component_class(component_class),
            encoding="utf-8",
        )

    for name, module_class in module_classes.items():
        (modules_dir / f"{name}.js").write_text(
            compile_module_class(module_class),
            encoding="utf-8",
        )

    (generated_dir / "index.js").write_text(
        _registry_source(sorted(component_classes), sorted(module_classes)),
        encoding="utf-8",
    )


def _collect_browser_dependencies(component_classes: dict[str, type], module_classes: dict[str, type]) -> None:
    """Recursively include shared browser classes referenced by generated classes."""
    from ..web import Component, Module

    queue = list(component_classes.values()) + list(module_classes.values())
    seen = set()
    while queue:
        cls = queue.pop(0)
        if cls in seen:
            continue
        seen.add(cls)
        for dep in used_browser_class_refs(cls).values():
            if issubclass(dep, Component):
                should_visit = dep.__name__ not in component_classes
                _register_browser_class(component_classes, dep, "Component")
                if should_visit:
                    queue.append(dep)
            elif issubclass(dep, Module):
                should_visit = dep.__name__ not in module_classes
                _register_browser_class(module_classes, dep, "Module")
                if should_visit:
                    queue.append(dep)


def _register_browser_class(target: dict[str, type], cls: type, kind: str) -> None:
    existing = target.get(cls.__name__)
    if existing is not None and existing is not cls:
        raise JSCodegenError(
            f"Generated {kind} name collision: {cls.__name__!r} is defined by "
            f"{existing.__module__}.{existing.__name__} and {cls.__module__}.{cls.__name__}. "
            "Use unique browser class names until SPRAG grows module-qualified JS output names."
        )
    target[cls.__name__] = cls


# JS source for the per-store bridge wrapper. One copy is emitted into
# ``stores.js`` and shared by every declared store. The wrapper exposes
# the same method names as ``sprag.stores.StoreBridge`` so the codegen's
# Python -> JS translation table is (nearly) identity, with the wrapper
# filling the gaps where Ragot ``createStateStore`` does not provide a
# direct equivalent (``delete``, ``clear``, ``select``-with-memoization).
_STORES_SHIM_RUNTIME = """function createStoreBridge(name, initial) {
    const _store = createStateStore(initial, { name });
    const _selectorCache = new WeakMap();

    function _normalizePath(path) {
        if (Array.isArray(path)) return path.map(String).filter(Boolean);
        if (typeof path === 'string') return path.split('.').map(s => s.trim()).filter(Boolean);
        return [];
    }

    function _cloneValue(value) {
        if (value === null || value === undefined) return value;
        if (typeof value !== 'object') return value;
        return JSON.parse(JSON.stringify(value));
    }

    function _valuesEqual(left, right) {
        if (Object.is(left, right)) return true;
        if (left === null || right === null) return left === right;
        if (typeof left !== 'object' || typeof right !== 'object') return false;
        try {
            return JSON.stringify(left) === JSON.stringify(right);
        } catch (_error) {
            return false;
        }
    }

    function _selectValue(selector) {
        if (selector === undefined || selector === null) return _store.getState();
        if (typeof selector === 'string' || Array.isArray(selector)) {
            return _store.get(selector);
        }
        if (typeof selector === 'function') {
            return selector(_store.getState());
        }
        return _store.getState();
    }

    return {
        name,
        get(path, fallback) {
            if (path === undefined || path === null) return _store.getState();
            return _store.get(path, fallback);
        },
        getState() {
            return _store.getState();
        },
        snapshot() {
            // JSON round-trip mirrors Specter Model.snapshot()'s deep-copy
            // semantics so callers cannot accidentally mutate live state.
            return JSON.parse(JSON.stringify(_store.getState()));
        },
        set(path, value) {
            return _store.set(path, value);
        },
        patch(partial) {
            return _store.patch(partial);
        },
        update(mutator) {
            return _store.batch(mutator);
        },
        delete(path) {
            const keys = _normalizePath(path);
            if (keys.length === 0) return false;
            const finalKey = keys[keys.length - 1];
            const root = _store.getState();
            let cursor = root;
            for (let i = 0; i < keys.length - 1; i += 1) {
                if (cursor == null) return false;
                cursor = cursor[keys[i]];
            }
            if (cursor == null || !(finalKey in cursor)) return false;
            delete cursor[finalKey];
            return true;
        },
        clear() {
            _store.batch((state) => {
                for (const key of Object.keys(state)) {
                    delete state[key];
                }
            });
        },
        subscribe(listener, options) {
            const resolved = options || {};
            const selector = resolved.selector;
            const immediate = !!resolved.immediate;

            if (selector === undefined && resolved.equals === undefined) {
                return _store.subscribe(listener, resolved);
            }

            let lastValue = _cloneValue(_selectValue(selector));
            if (immediate) {
                listener(_cloneValue(lastValue));
            }

            return _store.subscribe(() => {
                const nextValue = _cloneValue(_selectValue(selector));
                const equals = typeof resolved.equals === 'function'
                    ? resolved.equals
                    : _valuesEqual;
                if (equals(nextValue, lastValue)) {
                    return;
                }
                lastValue = _cloneValue(nextValue);
                listener(_cloneValue(nextValue));
            }, { immediate: false });
        },
        select(selector, fallback) {
            if (typeof selector === 'string' || Array.isArray(selector)) {
                return _store.get(selector, fallback);
            }
            if (typeof selector !== 'function') return fallback;
            let memo = _selectorCache.get(selector);
            if (!memo) {
                memo = createSelector([(s) => s], (s) => selector(s));
                _selectorCache.set(selector, memo);
            }
            const result = memo(_store.getState());
            return result === undefined ? fallback : result;
        },
        _store
    };
}
"""


def emit_stores_shim(output_dir: Path, stores: list[StoreBridge]) -> None:
    """Emit ``generated/stores.js`` — one bridge wrapper per declared store.

    Each entry hydrates from ``window.__SPRAG_STORES__[name]`` if present
    (the SSR snapshot) or falls back to the declared initial state. The
    shim exports each store under its declared name so generated
    Module/Component files can ``import { session } from '../stores.js';``
    and use it directly.

    The shim is always written, even when no stores are declared, so the
    browser entry can ``import './generated/stores.js'`` unconditionally.
    """
    generated_dir = output_dir / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)

    if not stores:
        (generated_dir / "stores.js").write_text(
            "// No SPRAG stores declared.\nexport {};\n", encoding="utf-8"
        )
        return

    lines = [
        "import { createStateStore, createSelector } from '../vendor/ragot.esm.min.js';",
        "",
        _STORES_SHIM_RUNTIME,
        "const _hydrated = (typeof window !== 'undefined' && window.__SPRAG_STORES__) || {};",
        "",
    ]
    for bridge in stores:
        name_js = json.dumps(bridge.name)
        initial_js = json.dumps(bridge.initial, sort_keys=True)
        lines.append(
            f"export const {bridge.name} = createStoreBridge("
            f"{name_js}, "
            f"_hydrated[{name_js}] !== undefined ? _hydrated[{name_js}] : {initial_js}"
            f");"
        )
    lines.append("")
    (generated_dir / "stores.js").write_text("\n".join(lines), encoding="utf-8")


def build_browser_entry(manifest: dict) -> str:
    serializable = _serializable_manifest(manifest)
    return f"""import {{ componentRegistry, moduleRegistry }} from './generated/index.js';
import {{ bus, ragotRegistry }} from './vendor/ragot.esm.min.js';
// Side-effect import: ``stores.js`` reads window.__SPRAG_STORES__ at module
// load and creates one bridge wrapper per declared SPRAG store.
import './generated/stores.js';

const manifest = {json.dumps(serializable, indent=2, sort_keys=True)};
window.__SPRAG_MANIFEST__ = manifest;
const route = window.__SPRAG_PAGE__ || {{}};
const mount = window.__SPRAG_MOUNT__ || null;
const surface = mount || route;

function trimTrailingSlash(value) {{
    if (!value || value === '/') {{
        return '';
    }}
    return value.replace(/\/+$/, '');
}}

function normalizePath(value) {{
    if (!value || value === '/') {{
        return '/';
    }}
    return '/' + String(value).replace(/^\/+|\/+$/g, '');
}}

function normalizeComparablePath(value) {{
    const normalized = normalizePath(value);
    return normalized === '/' ? '/' : normalized.replace(/\/+$/, '');
}}

function deriveBasePrefix(currentPathname, surfacePath) {{
    const actual = trimTrailingSlash(currentPathname || '/');
    const currentSurface = trimTrailingSlash(normalizePath(surfacePath || '/'));
    if (!currentSurface) {{
        return actual;
    }}
    if (actual === currentSurface) {{
        return '';
    }}
    if (actual.endsWith(currentSurface)) {{
        return actual.slice(0, actual.length - currentSurface.length);
    }}
    return '';
}}

const spragBasePrefix = deriveBasePrefix(window.location.pathname, surface.path || '/');
window.__SPRAG_BASE__ = spragBasePrefix || '';

function withSpragBase(path) {{
    const normalized = normalizePath(path || '/');
    const prefix = trimTrailingSlash(window.__SPRAG_BASE__ || '');
    if (!prefix) {{
        return normalized;
    }}
    return normalized === '/' ? `${{prefix}}/` : `${{prefix}}${{normalized}}`;
}}

const spragInternalPathMap = new Map([['/', '/']]);
for (const entry of [...(manifest.routes || []), ...(manifest.mounts || [])]) {{
    const canonical = entry.output || entry.path || '/';
    spragInternalPathMap.set(normalizeComparablePath(canonical), canonical);
    if (entry.path) {{
        spragInternalPathMap.set(normalizeComparablePath(entry.path), canonical);
    }}
}}

function rewriteInternalLinks() {{
    if (typeof document === 'undefined') {{
        return;
    }}
    for (const anchor of document.querySelectorAll('a[href]')) {{
        const rawHref = anchor.getAttribute('href');
        if (!rawHref || rawHref.startsWith('#') || rawHref.startsWith('mailto:') || rawHref.startsWith('tel:')) {{
            continue;
        }}
        try {{
            const parsed = new URL(rawHref, window.location.origin);
            if (parsed.origin !== window.location.origin) {{
                continue;
            }}
            const canonical = spragInternalPathMap.get(normalizeComparablePath(parsed.pathname));
            if (!canonical) {{
                continue;
            }}
            anchor.setAttribute('href', `${{withSpragBase(canonical)}}${{parsed.search}}${{parsed.hash}}`);
        }} catch (_error) {{
            // Ignore invalid or non-URL href values.
        }}
    }}
}}

function createActionClient(currentRoute) {{
    const knownActions = new Set(currentRoute.actions || []);
    const endpoint = withSpragBase(currentRoute.action_endpoint || '/__sprag__/actions');

    return {{
        async call(name, payload = {{}}) {{
            if (!name) {{
                throw new Error('[SPRAG] Action name is required.');
            }}
            if (knownActions.size && !knownActions.has(name)) {{
                throw new Error(`[SPRAG] Unknown action "${{name}}" for route "${{currentRoute.path || 'unknown'}}".`);
            }}

            let response = null;
            try {{
                response = await fetch(endpoint, {{
                    method: 'POST',
                    headers: {{
                        'Accept': 'application/json',
                        'Content-Type': 'application/json'
                    }},
                    body: JSON.stringify({{
                        route: currentRoute.path,
                        action: name,
                        payload
                    }})
                }});
            }} catch (_error) {{
                const message =
                    `[SPRAG] Action "${{name}}" could not reach "${{endpoint}}". ` +
                    'This usually means you are viewing a static build and this example needs a live SPRAG server.';
                const error = new Error(message);
                error.status = 0;
                error.response = {{
                    ok: false,
                    code: 'SPRAG_SERVER_UNAVAILABLE',
                    error: message,
                }};
                throw error;
            }}

            const contentType = response.headers.get('content-type') || '';
            const result = contentType.includes('application/json')
                ? await response.json()
                : {{
                    ok: false,
                    error: `[SPRAG] Expected JSON response for action "${{name}}" but received status ${{response.status}}.`
                }};

            if (!response.ok || !result.ok) {{
                const error = new Error(result.error || `[SPRAG] Action "${{name}}" failed.`);
                error.status = response.status;
                error.response = result;
                throw error;
            }}

            return result;
        }}
    }};
}}

const actionClient = createActionClient(route);
window.__SPRAG_ACTIONS__ = actionClient;

const spragRoots = [];
let spragEventSource = null;
let spragSocket = null;
let spragSocketRegistryKey = null;
let spragBooted = false;

function provideRuntimeRoot(key, value, owner) {{
    ragotRegistry.provide(key, value, owner || null, {{ replace: true }});
    return key;
}}

function registerRuntimeRoot(root) {{
    spragRoots.push(root);
    return root;
}}

function stopRuntimeRoot(root) {{
    try {{
        if (root.module && typeof root.module.stop === 'function') {{
            root.module.stop();
        }} else if (root.component && typeof root.component.unmount === 'function') {{
            root.component.unmount();
        }}
    }} catch (error) {{
        console.warn('[SPRAG] Error while stopping Ragot root', error);
    }} finally {{
        for (const key of root.registryKeys || []) {{
            try {{
                ragotRegistry.unregister(key);
            }} catch (error) {{
                console.warn('[SPRAG] Error while unregistering Ragot root', key, error);
            }}
        }}
    }}
}}

function teardownSpragRuntime(reason = 'teardown') {{
    if (!spragBooted && spragRoots.length === 0 && !spragEventSource && !spragSocket) return;

    if (spragEventSource) {{
        spragEventSource.close();
        if (window.__SPRAG_EVENT_SOURCE__ === spragEventSource) {{
            window.__SPRAG_EVENT_SOURCE__ = null;
        }}
        spragEventSource = null;
    }}

    if (spragSocket) {{
        spragSocket.close();
        if (window.__SPRAG_SOCKET__ === spragSocket) {{
            window.__SPRAG_SOCKET__ = null;
        }}
        spragSocket = null;
    }}
    if (spragSocketRegistryKey) {{
        try {{
            ragotRegistry.unregister(spragSocketRegistryKey);
        }} catch (error) {{
            console.warn('[SPRAG] Error while unregistering shared socket bridge', error);
        }}
        spragSocketRegistryKey = null;
    }}

    while (spragRoots.length > 0) {{
        stopRuntimeRoot(spragRoots.pop());
    }}

    spragBooted = false;
    bus.emit('sprag:teardown', {{ reason }});
}}

window.__SPRAG_TEARDOWN__ = teardownSpragRuntime;

function mountHydrationEntry(entry) {{
    const target = document.querySelector(`[data-sprag-hydrate-id="${{entry.id}}"]`);
    if (!target) return;

    const ComponentClass = componentRegistry[entry.component];
    if (!ComponentClass) {{
        console.warn('[SPRAG] Missing generated component for', entry.component);
        return;
    }}

    const moduleName = entry.module;
    const ModuleClass = moduleName ? moduleRegistry[moduleName] : null;
    const component = new ComponentClass(entry.state || {{}}, {{
        props: entry.props || {{}},
        module: null, // filled in below once the module exists
    }});

    // Clear the SSR'd inner HTML so the component's mount can append its
    // own element. (Component.mount appends — it does not replace.)
    target.innerHTML = '';

    if (ModuleClass) {{
        // Ragot's canonical hybrid pattern: the Module is the lifecycle
        // owner; it adopts the Component and wires a state-sync callback.
        // ``adoptComponent`` mounts the component immediately and registers
        // a ``watchState`` subscription, so every ``module.setState(...)``
        // automatically flows into ``component.setState(...)``.
        const module = new ModuleClass(entry.module_state || {{}});
        module.actions = actionClient;
        module.route = route;
        module.socket = spragSocket;
        module.component = component;          // back-ref for imperative access
        module.element = target;               // self.element inside Module == hydrate container
        component.module = module;              // Component -> Module back-ref
        // Users may define ``sync_component(self, component, state)`` on their
        // Module for custom state routing. Default: push the full module state
        // into the component (shallow merge via Component.setState).
        const syncFn = typeof module.syncComponent === 'function'
            ? (c, s) => module.syncComponent(c, s)
            : (c, s) => c.setState(s);
        module.adoptComponent(component, {{
            startArgs: [target],
            sync: syncFn,
        }});
        module.start();
        const moduleKey = provideRuntimeRoot(entry.module + ':' + entry.id, module, module);
        const componentKey = provideRuntimeRoot(entry.component + ':' + entry.id, component, module);
        registerRuntimeRoot({{
            type: 'hydration',
            id: entry.id,
            module,
            component,
            registryKeys: [moduleKey, componentKey],
        }});
    }} else {{
        component.mount(target);
        const componentKey = provideRuntimeRoot(entry.component + ':' + entry.id, component, component);
        registerRuntimeRoot({{
            type: 'hydration',
            id: entry.id,
            module: null,
            component,
            registryKeys: [componentKey],
        }});
    }}
}}

function mountClientApp(entry) {{
    const target = document.querySelector('#app-root');
    if (!target) return;

    const ComponentClass = componentRegistry[entry.component];
    if (!ComponentClass) {{
        console.warn('[SPRAG] Missing generated component for mount', entry.component);
        return;
    }}

    const boot = window.__SPRAG_BOOT__ || {{}};
    const ModuleClass = entry.module ? moduleRegistry[entry.module] : null;
    const component = new ComponentClass(boot || {{}}, {{
        props: boot || {{}},
        module: null,
    }});
    target.innerHTML = '';

    if (ModuleClass) {{
        const module = new ModuleClass(boot || {{}});
        module.actions = actionClient;
        module.route = entry;
        module.socket = spragSocket;
        module.component = component;
        module.element = target;
        component.module = module;
        const syncFn = typeof module.syncComponent === 'function'
            ? (c, s) => module.syncComponent(c, s)
            : (c, s) => c.setState(s);
        module.adoptComponent(component, {{
            startArgs: [target],
            sync: syncFn,
        }});
        module.start();
        const moduleKey = provideRuntimeRoot(entry.module + ':' + entry.path, module, module);
        const componentKey = provideRuntimeRoot(entry.component + ':' + entry.path, component, module);
        registerRuntimeRoot({{
            type: 'mount',
            path: entry.path,
            module,
            component,
            registryKeys: [moduleKey, componentKey],
        }});
    }} else {{
        component.mount(target);
        const componentKey = provideRuntimeRoot(entry.component + ':' + entry.path, component, component);
        registerRuntimeRoot({{
            type: 'mount',
            path: entry.path,
            module: null,
            component,
            registryKeys: [componentKey],
        }});
    }}
}}

function connectBusBridge(route) {{
    const endpoint = withSpragBase(route.events_endpoint || '/__sprag__/events');
    const source = new EventSource(endpoint);
    source.onmessage = (event) => {{
        try {{
            const data = JSON.parse(event.data);
            const eventName = data.event || 'server:message';
            bus.emit(eventName, data.payload !== undefined ? data.payload : data);
        }} catch (e) {{
            bus.emit('server:message', event.data);
        }}
    }};
    source.onerror = () => {{
        bus.emit('server:connection:error');
    }};
    window.__SPRAG_EVENT_SOURCE__ = source;
    spragEventSource = source;
    return source;
}}

function createSocketUrl(path) {{
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${{protocol}}//${{window.location.host}}${{withSpragBase(path)}}`;
}}

function createSharedSocketClient(surface) {{
    const socketPath = '/__sprag__/socket';
    const listeners = new Map();
    const outboundQueue = [];
    let ws = null;
    let reconnectTimer = null;
    let closed = false;

    function listenerSet(event) {{
        let handlers = listeners.get(event);
        if (!handlers) {{
            handlers = new Set();
            listeners.set(event, handlers);
        }}
        return handlers;
    }}

    function dispatch(event, payload) {{
        const handlers = listeners.get(event);
        if (!handlers) return;
        for (const handler of Array.from(handlers)) {{
            try {{
                handler(payload);
            }} catch (error) {{
                console.warn(`[SPRAG] Socket handler for "${{event}}" failed.`, error);
            }}
        }}
    }}

    function flushQueue() {{
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        while (outboundQueue.length > 0) {{
            ws.send(outboundQueue.shift());
        }}
    }}

    function scheduleReconnect() {{
        if (closed || reconnectTimer) return;
        reconnectTimer = window.setTimeout(() => {{
            reconnectTimer = null;
            connect();
        }}, 1000);
    }}

    function connect() {{
        if (closed) return;
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {{
            return;
        }}

        ws = new WebSocket(createSocketUrl(socketPath));
        ws.onopen = () => {{
            ws.send(JSON.stringify({{
                type: 'hello',
                route: surface.path || '/',
            }}));
            flushQueue();
            bus.emit('sprag:socket:open', {{ path: surface.path || '/' }});
        }};
        ws.onmessage = (event) => {{
            try {{
                const message = JSON.parse(event.data);
                if (message && message.type === 'event' && message.event) {{
                    dispatch(message.event, message.payload);
                    return;
                }}
                if (message && message.type === 'error') {{
                    dispatch('sprag:socket:error', message);
                    bus.emit('sprag:socket:error', message);
                    return;
                }}
                if (message && message.type === 'ready') {{
                    bus.emit('sprag:socket:ready', message);
                    return;
                }}
                bus.emit('sprag:socket:message', message);
            }} catch (_error) {{
                bus.emit('sprag:socket:message', event.data);
            }}
        }};
        ws.onerror = () => {{
            bus.emit('sprag:socket:error', {{
                error: 'socket-error',
                path: surface.path || '/',
            }});
        }};
        ws.onclose = () => {{
            bus.emit('sprag:socket:close', {{ path: surface.path || '/' }});
            if (!closed) {{
                scheduleReconnect();
            }}
        }};
    }}

    const socket = {{
        on(event, handler) {{
            if (!event || typeof handler !== 'function') {{
                return this;
            }}
            listenerSet(event).add(handler);
            return this;
        }},
        off(event, handler) {{
            const handlers = listeners.get(event);
            if (!handlers) {{
                return this;
            }}
            handlers.delete(handler);
            if (handlers.size === 0) {{
                listeners.delete(event);
            }}
            return this;
        }},
        emit(event, payload = null) {{
            if (!event) {{
                return false;
            }}
            const encoded = JSON.stringify({{
                type: 'emit',
                event,
                payload,
                route: surface.path || '/',
            }});
            if (ws && ws.readyState === WebSocket.OPEN) {{
                ws.send(encoded);
                return true;
            }}
            outboundQueue.push(encoded);
            connect();
            return false;
        }},
        close() {{
            closed = true;
            if (reconnectTimer) {{
                window.clearTimeout(reconnectTimer);
                reconnectTimer = null;
            }}
            listeners.clear();
            outboundQueue.length = 0;
            if (ws && ws.readyState !== WebSocket.CLOSED) {{
                ws.close();
            }}
        }},
        connect() {{
            connect();
            return this;
        }},
    }};

    return socket;
}}

function connectSocketBridge(surface) {{
    if (!surface || !surface.socket_bridge) {{
        return null;
    }}
    if (typeof window.WebSocket !== 'function') {{
        console.warn('[SPRAG] This browser does not support WebSocket; socket bridge disabled.');
        return null;
    }}
    const socket = createSharedSocketClient(surface);
    window.__SPRAG_SOCKET__ = socket;
    spragSocketRegistryKey = provideRuntimeRoot('sprag.socket', socket, null);
    spragSocket = socket;
    return socket;
}}

function boot() {{
    if (spragBooted) return;
    try {{
        rewriteInternalLinks();
        const socket = connectSocketBridge(surface);
        // Stores hydrate via the side-effect import of
        // './generated/stores.js' above — each store bridge reads its
        // window.__SPRAG_STORES__[name] entry at module-load time, so by
        // the time boot() runs every store is live.
        if (mount) {{
            mountClientApp(mount);
            if (socket && typeof socket.connect === 'function') {{
                socket.connect();
            }}
            connectBusBridge(mount);
            spragBooted = true;
            return;
        }}
        const hydration = window.__SPRAG_HYDRATION__ || [];
        hydration.forEach(mountHydrationEntry);
        if (socket && typeof socket.connect === 'function') {{
            socket.connect();
        }}
        connectBusBridge(route);
        spragBooted = true;
    }} catch (error) {{
        teardownSpragRuntime('boot-error');
        throw error;
    }}
}}

if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', boot, {{ once: true }});
}} else {{
    boot();
}}

window.addEventListener('pagehide', () => teardownSpragRuntime('pagehide'));
window.addEventListener('pageshow', (event) => {{
    if (event.persisted && !spragBooted) {{
        boot();
    }}
}});
"""


def _registry_source(component_names, module_names) -> str:
    component_imports = "\n".join(
        f"import {{ {name} }} from './components/{name}.js';" for name in component_names
    )
    module_imports = "\n".join(
        f"import {{ {name} }} from './modules/{name}.js';" for name in module_names
    )
    component_pairs = ",\n    ".join(f"{name}" for name in component_names)
    module_pairs = ",\n    ".join(f"{name}" for name in module_names)
    return f"""{component_imports}
{module_imports}

export const componentRegistry = {{
    {component_pairs}
}};

export const moduleRegistry = {{
    {module_pairs}
}};
"""


def _serializable_manifest(manifest):
    routes = []
    for route in manifest.get("routes", []):
        next_route = dict(route)
        next_route["hydration"] = []
        for entry in route.get("hydration", []):
            next_route["hydration"].append(
                {
                    "id": entry["id"],
                    "component": entry["component"],
                    "module": entry["module"],
                    "props": entry["props"],
                    "state": entry["state"],
                    "module_state": entry["module_state"],
                }
            )
        routes.append(next_route)
    mounts = []
    for mount in manifest.get("mounts", []):
        mounts.append(
            {
                key: value
                for key, value in mount.items()
                if key not in {"root_component_class", "root_module_class"}
            }
        )
    return {"errors": manifest.get("errors", []), "mounts": mounts, "routes": routes}
