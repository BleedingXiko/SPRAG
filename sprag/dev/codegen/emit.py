"""High-level emitters for the SPRAG build pipeline.

These functions are the public surface of the codegen package: the
SPRAG compiler invokes them to write the Ragot runtime, the generated
component / module sources, and the browser entry point that wires
hydration, the action client, and the event-source bus bridge.
"""

from __future__ import annotations

import json
from pathlib import Path

from ...runtime.stores import StoreBridge
from .components import compile_component_class
from .dependencies import used_browser_class_refs
from .mappings import JSCodegenError
from .modules import compile_module_class


def emit_ragot_runtime(output_dir: Path, project_root: Path) -> None:
    vendor_dir = output_dir / "vendor"
    vendor_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = Path(__file__).resolve().parents[2] / "assets"
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


def emit_generated_files(output_dir: Path, hydration_entries: list[dict], *, mount_entries=None, route_entries=None) -> None:
    mount_entries = mount_entries or []
    route_entries = route_entries or []
    generated_dir = output_dir / "generated"
    components_dir = generated_dir / "components"
    modules_dir = generated_dir / "modules"
    components_dir.mkdir(parents=True, exist_ok=True)
    modules_dir.mkdir(parents=True, exist_ok=True)

    component_classes = {}
    module_classes = {}
    declared_import_aliases = set()
    for entry in hydration_entries:
        component_class = entry.get("component_class")
        module_class = entry.get("module_class")
        if component_class:
            _register_browser_class(component_classes, component_class, "Component")
        if module_class:
            _register_browser_class(module_classes, module_class, "Module")

    for entry in mount_entries:
        declared_import_aliases.update((entry.get("modules") or {}).keys())
        component_class = entry.get("root_component_class")
        module_class = entry.get("root_module_class")
        if component_class:
            _register_browser_class(component_classes, component_class, "Component")
        if module_class:
            _register_browser_class(module_classes, module_class, "Module")
        for provider_class in entry.get("_provider_classes", []):
            _register_browser_class(module_classes, provider_class, "Module")

    for entry in route_entries:
        declared_import_aliases.update((entry.get("modules") or {}).keys())
        for browser_class in entry.get("_browser_classes", []):
            from ...runtime.browser import Component, Module

            if isinstance(browser_class, type) and issubclass(browser_class, Component):
                _register_browser_class(component_classes, browser_class, "Component")
            elif isinstance(browser_class, type) and issubclass(browser_class, Module):
                _register_browser_class(module_classes, browser_class, "Module")
        for provider_class in entry.get("_provider_classes", []):
            _register_browser_class(module_classes, provider_class, "Module")

    _collect_browser_dependencies(component_classes, module_classes)

    for name, component_class in component_classes.items():
        (components_dir / f"{name}.js").write_text(
            compile_component_class(
                component_class,
                declared_import_aliases=declared_import_aliases,
            ),
            encoding="utf-8",
        )

    for name, module_class in module_classes.items():
        (modules_dir / f"{name}.js").write_text(
            compile_module_class(
                module_class,
                declared_import_aliases=declared_import_aliases,
            ),
            encoding="utf-8",
        )

    (generated_dir / "index.js").write_text(
        _registry_source(sorted(component_classes), sorted(module_classes)),
        encoding="utf-8",
    )


def _collect_browser_dependencies(component_classes: dict[str, type], module_classes: dict[str, type]) -> None:
    """Recursively include shared browser classes referenced by generated classes."""
    from ...runtime.browser import Component, Module

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
    const _globalStores = (typeof window !== 'undefined'
        ? ((window.__SPRAG_PAYLOAD__ = window.__SPRAG_PAYLOAD__ || {}).stores = window.__SPRAG_PAYLOAD__.stores || {})
        : {});
    const _globalBridges = (typeof window !== 'undefined'
        ? (window.__SPRAG_STORE_BRIDGES__ = window.__SPRAG_STORE_BRIDGES__ || {})
        : {});

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

    function _syncGlobalSnapshot() {
        if (typeof window === 'undefined') return;
        _globalStores[name] = JSON.parse(JSON.stringify(_store.getState()));
    }

    _globalBridges[name] = null;
    _syncGlobalSnapshot();
    _store.subscribe(() => {
        _syncGlobalSnapshot();
    });

    const bridge = {
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
    _globalBridges[name] = bridge;
    return bridge;
}
"""


def emit_stores_shim(output_dir: Path, stores: list[StoreBridge]) -> None:
    """Emit ``generated/stores.js`` — one bridge wrapper per declared store.

    Each entry hydrates from ``window.__SPRAG_PAYLOAD__.stores[name]`` if
    present (the SSR snapshot) or falls back to the declared initial state. The
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
        "const _payload = (typeof window !== 'undefined' && window.__SPRAG_PAYLOAD__) || {};",
        "const _hydrated = _payload.stores || {};",
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
// Side-effect import: ``stores.js`` reads window.__SPRAG_PAYLOAD__ at module
// load and creates one bridge wrapper per declared SPRAG store.
import './generated/stores.js';

const manifest = {json.dumps(serializable, indent=2, sort_keys=True)};
window.__SPRAG_MANIFEST__ = manifest;
const payload = window.__SPRAG_PAYLOAD__ || {{}};
window.__SPRAG_ENV__ = payload.env || {{}};
window.__SPRAG_IMPORTS__ = window.__SPRAG_IMPORTS__ || {{}};
const route = payload.page || {{}};
const mount = payload.mount || null;
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
const spragBootTitle = (typeof document !== 'undefined' && document.title) || '';
let spragMetadataState = {{}};

function withSpragBase(path) {{
    const normalized = normalizePath(path || '/');
    const prefix = trimTrailingSlash(window.__SPRAG_BASE__ || '');
    if (!prefix) {{
        return normalized;
    }}
    return normalized === '/' ? `${{prefix}}/` : `${{prefix}}${{normalized}}`;
}}

function metadataContent(value) {{
    if (Array.isArray(value)) {{
        return value
            .filter((item) => item !== null && item !== undefined && item !== '')
            .map((item) => String(item))
            .join(', ');
    }}
    if (value === null || value === undefined) {{
        return '';
    }}
    return String(value);
}}

function normalizeMetadataObject(input) {{
    const next = {{}};
    if (!input || typeof input !== 'object') {{
        return next;
    }}
    for (const [rawKey, value] of Object.entries(input)) {{
        const key = String(rawKey || '').trim();
        if (!key) {{
            continue;
        }}
        const content = metadataContent(value);
        if (!content) {{
            continue;
        }}
        next[key] = content;
    }}
    return next;
}}

function listManagedHeadElements() {{
    if (typeof document === 'undefined' || !document.head) {{
        return [];
    }}
    return Array.from(document.head.querySelectorAll('[data-sprag-head="true"]'));
}}

function managedHeadElementsForKey(key) {{
    return listManagedHeadElements().filter((element) => element.getAttribute('data-sprag-head-key') === key);
}}

function ensureManagedHeadElement(key) {{
    const matches = managedHeadElementsForKey(key);
    const first = matches[0] || null;
    for (const duplicate of matches.slice(1)) {{
        duplicate.remove();
    }}

    let element = first;
    if (key === 'canonical') {{
        if (!element || element.tagName !== 'LINK') {{
            if (element) {{
                element.remove();
            }}
            element = document.createElement('link');
            element.setAttribute('rel', 'canonical');
            document.head.appendChild(element);
        }}
    }} else {{
        if (!element || element.tagName !== 'META') {{
            if (element) {{
                element.remove();
            }}
            element = document.createElement('meta');
            document.head.appendChild(element);
        }}
        const attr = key.startsWith('og:') ? 'property' : 'name';
        const staleAttr = attr === 'property' ? 'name' : 'property';
        element.removeAttribute(staleAttr);
        element.setAttribute(attr, key);
    }}

    element.setAttribute('data-sprag-head', 'true');
    element.setAttribute('data-sprag-head-key', key);
    return element;
}}

function applyMetadataSprag(metadata) {{
    const normalized = normalizeMetadataObject(metadata);
    const nextKeys = new Set(Object.keys(normalized).filter((key) => key !== 'title'));
    for (const element of listManagedHeadElements()) {{
        const key = element.getAttribute('data-sprag-head-key') || '';
        if (!nextKeys.has(key)) {{
            element.remove();
        }}
    }}

    for (const [key, content] of Object.entries(normalized)) {{
        if (key === 'title') {{
            continue;
        }}
        const element = ensureManagedHeadElement(key);
        if (key === 'canonical') {{
            element.setAttribute('href', content);
        }} else {{
            element.setAttribute('content', content);
        }}
    }}

    if (typeof document !== 'undefined') {{
        const resolvedTitle = normalized.title || spragBootTitle || surface.name || surface.path || document.title || '';
        if (resolvedTitle) {{
            document.title = resolvedTitle;
        }}
    }}

    spragMetadataState = normalized;
    window.__SPRAG_METADATA_STATE__ = {{ ...spragMetadataState }};
    if (window.__SPRAG_PAYLOAD__) {{
        window.__SPRAG_PAYLOAD__.metadata = {{ ...spragMetadataState }};
    }}
    return spragMetadataState;
}}

function setMetadataSprag(metadata = {{}}, options = {{}}) {{
    const replace = typeof options === 'boolean'
        ? options
        : Boolean(options && options.replace);
    const input = metadata && typeof metadata === 'object' ? metadata : {{}};
    const next = replace ? {{}} : {{ ...spragMetadataState }};
    for (const [rawKey, value] of Object.entries(input)) {{
        const key = String(rawKey || '').trim();
        if (!key) {{
            continue;
        }}
        if (value === null || value === undefined || value === '') {{
            delete next[key];
            continue;
        }}
        const content = metadataContent(value);
        if (!content) {{
            delete next[key];
            continue;
        }}
        next[key] = content;
    }}
    return applyMetadataSprag(next);
}}

spragMetadataState = normalizeMetadataObject(payload.metadata || {{}});
window.__SPRAG_METADATA_STATE__ = {{ ...spragMetadataState }};
window.__SPRAG_SET_METADATA__ = setMetadataSprag;

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

function appendFormValue(target, name, value) {{
    if (Object.prototype.hasOwnProperty.call(target, name)) {{
        if (Array.isArray(target[name])) {{
            target[name].push(value);
        }} else {{
            target[name] = [target[name], value];
        }}
        return;
    }}
    target[name] = value;
}}

function resolveFormElement(source) {{
    if (!source) {{
        throw new Error('[SPRAG] form_data(...) requires a form element or DOM event.');
    }}
    if (source.tagName === 'FORM') {{
        return source;
    }}
    if (source.currentTarget && source.currentTarget.tagName === 'FORM') {{
        return source.currentTarget;
    }}
    const candidate = source.target || source.currentTarget || source;
    if (candidate && typeof candidate.closest === 'function') {{
        const form = candidate.closest('form');
        if (form) {{
            return form;
        }}
    }}
    throw new Error('[SPRAG] form_data(...) could not resolve a parent <form>.');
}}

function collectFormSnapshot(form, options = {{}}) {{
    const data = {{}};
    const checkboxCounts = new Map();
    const errorOnFiles = options.errorOnFiles !== false;

    for (const element of Array.from(form.elements || [])) {{
        if (!element || !element.name || element.disabled) {{
            continue;
        }}
        const type = String(element.type || '').toLowerCase();
        if (type === 'checkbox') {{
            checkboxCounts.set(element.name, (checkboxCounts.get(element.name) || 0) + 1);
        }}
    }}

    for (const element of Array.from(form.elements || [])) {{
        if (!element || !element.name || element.disabled) {{
            continue;
        }}
        const name = element.name;
        const tagName = String(element.tagName || '').toUpperCase();
        const type = String(element.type || '').toLowerCase();

        if (type === 'file') {{
            if (errorOnFiles && element.files && element.files.length > 0) {{
                throw new Error(
                    '[SPRAG] form_data(...) does not support file inputs yet. Use the dedicated upload path.'
                );
            }}
            continue;
        }}

        if (type === 'submit' || type === 'button' || type === 'reset') {{
            continue;
        }}

        if (type === 'checkbox') {{
            const isBoolean = (checkboxCounts.get(name) || 0) === 1
                && ((!element.hasAttribute || !element.hasAttribute('value')) || element.value === 'on');
            if (isBoolean) {{
                data[name] = !!element.checked;
            }} else if (element.checked) {{
                appendFormValue(data, name, element.value);
            }} else if (!Object.prototype.hasOwnProperty.call(data, name)) {{
                data[name] = [];
            }}
            continue;
        }}

        if (type === 'radio') {{
            if (element.checked) {{
                data[name] = element.value;
            }}
            continue;
        }}

        if (tagName === 'SELECT' && element.multiple) {{
            data[name] = Array.from(element.selectedOptions || []).map((option) => option.value);
            continue;
        }}

        appendFormValue(data, name, element.value);
    }}

    return data;
}}

function formDataSprag(source) {{
    const form = resolveFormElement(source);
    return collectFormSnapshot(form, {{ errorOnFiles: true }});
}}

window.__SPRAG_FORM_DATA__ = formDataSprag;

function resolveNavigationTarget(target) {{
    if (target === null || target === undefined || target === '') {{
        throw new Error('[SPRAG] navigate(...) requires a non-empty target.');
    }}
    const rawTarget = String(target);
    if (
        rawTarget.startsWith('#')
        || rawTarget.startsWith('mailto:')
        || rawTarget.startsWith('tel:')
        || rawTarget.startsWith('javascript:')
        || rawTarget.startsWith('data:')
    ) {{
        return rawTarget;
    }}
    try {{
        const parsed = new URL(rawTarget, window.location.origin);
        if (parsed.origin !== window.location.origin) {{
            return parsed.toString();
        }}
        const canonical = spragInternalPathMap.get(normalizeComparablePath(parsed.pathname)) || parsed.pathname || '/';
        return `${{withSpragBase(canonical)}}${{parsed.search}}${{parsed.hash}}`;
    }} catch (_error) {{
        return rawTarget;
    }}
}}

function navigateSprag(target, options = {{}}) {{
    const resolved = resolveNavigationTarget(target);
    const replace = typeof options === 'boolean'
        ? options
        : Boolean(options && options.replace);
    if (replace) {{
        window.location.replace(resolved);
    }} else {{
        window.location.assign(resolved);
    }}
    return resolved;
}}

window.__SPRAG_NAVIGATE__ = navigateSprag;

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

            if (result.redirect && result.redirect.location) {{
                navigateSprag(result.redirect.location, {{ replace: !!result.redirect.replace }});
            }}

            return result;
        }}
    }};
}}

const actionClient = createActionClient(route);
window.__SPRAG_ACTIONS__ = actionClient;

function actionErrorMessageSprag(error, fallback = '') {{
    const response = error && error.response && typeof error.response === 'object'
        ? error.response
        : null;
    const responseMessage = response && typeof response.error === 'string'
        ? response.error.trim()
        : '';
    if (responseMessage) {{
        return responseMessage;
    }}
    const directMessage = error && typeof error.message === 'string'
        ? error.message.trim()
        : '';
    if (directMessage) {{
        return directMessage;
    }}
    const fallbackMessage = fallback === null || fallback === undefined
        ? ''
        : String(fallback).trim();
    return fallbackMessage || '[SPRAG] Action failed.';
}}

window.__SPRAG_ACTION_ERROR_MESSAGE__ = actionErrorMessageSprag;

function uploadProgressPayload(event) {{
    const loaded = Number((event && event.loaded) || 0);
    const total = Number((event && event.total) || 0);
    const percent = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0;
    return {{
        loaded,
        total,
        percent,
        length_computable: !!(event && event.lengthComputable),
    }};
}}

function createUploadClient(currentRoute) {{
    const knownActions = new Set(currentRoute.actions || []);
    const endpoint = withSpragBase(currentRoute.upload_endpoint || '/__sprag__/uploads');

    return {{
        submit(name, source, onProgress = null) {{
            if (!name) {{
                return Promise.reject(new Error('[SPRAG] Upload action name is required.'));
            }}
            if (knownActions.size && !knownActions.has(name)) {{
                return Promise.reject(
                    new Error(`[SPRAG] Unknown upload action "${{name}}" for route "${{currentRoute.path || 'unknown'}}".`)
                );
            }}

            const form = resolveFormElement(source);
            const body = new FormData(form);
            body.append('__sprag_route', currentRoute.path || '/');
            body.append('__sprag_action', name);
            body.append('__sprag_payload', JSON.stringify(collectFormSnapshot(form, {{ errorOnFiles: false }})));

            return new Promise((resolve, reject) => {{
                const xhr = new XMLHttpRequest();
                xhr.open('POST', endpoint, true);
                xhr.setRequestHeader('Accept', 'application/json');

                xhr.upload.addEventListener('progress', (event) => {{
                    if (typeof onProgress === 'function') {{
                        onProgress(uploadProgressPayload(event));
                    }}
                }});

                xhr.onerror = () => {{
                    const message =
                        `[SPRAG] Upload "${{name}}" could not reach "${{endpoint}}". ` +
                        'This usually means you are viewing a static build and this example needs a live SPRAG server.';
                    const error = new Error(message);
                    error.status = 0;
                    error.response = {{
                        ok: false,
                        code: 'SPRAG_SERVER_UNAVAILABLE',
                        error: message,
                    }};
                    reject(error);
                }};

                xhr.onload = () => {{
                    const contentType = xhr.getResponseHeader('content-type') || '';
                    let result = null;
                    if (contentType.includes('application/json')) {{
                        try {{
                            result = JSON.parse(xhr.responseText || '{{}}');
                        }} catch (_error) {{
                            result = {{
                                ok: false,
                                error: `[SPRAG] Upload "${{name}}" returned invalid JSON.`
                            }};
                        }}
                    }} else {{
                        result = {{
                            ok: false,
                            error: `[SPRAG] Expected JSON response for upload "${{name}}" but received status ${{xhr.status}}.`
                        }};
                    }}

                    if (typeof onProgress === 'function') {{
                        onProgress({{
                            loaded: 0,
                            total: 0,
                            percent: xhr.status >= 200 && xhr.status < 300 ? 100 : 0,
                            length_computable: false,
                        }});
                    }}

                    if (xhr.status < 200 || xhr.status >= 300 || !result.ok) {{
                        const error = new Error(result.error || `[SPRAG] Upload "${{name}}" failed.`);
                        error.status = xhr.status;
                        error.response = result;
                        reject(error);
                        return;
                    }}

                    if (result.redirect && result.redirect.location) {{
                        navigateSprag(result.redirect.location, {{ replace: !!result.redirect.replace }});
                    }}

                    resolve(result);
                }};

                xhr.send(body);
            }});
        }}
    }};
}}

const uploadClient = createUploadClient(route);
window.__SPRAG_UPLOADS__ = uploadClient;

function resolveJSImportSrc(src) {{
    if (!src) {{
        return src;
    }}
    if (
        src.startsWith('http://')
        || src.startsWith('https://')
        || src.startsWith('//')
        || src.startsWith('data:')
        || src.startsWith('blob:')
    ) {{
        return src;
    }}
    if (src.startsWith('/')) {{
        return withSpragBase(src);
    }}
    return src;
}}

async function resolveSurfaceImports(currentSurface) {{
    const declared = (currentSurface && currentSurface.modules) || {{}};
    const resolved = {{}};
    for (const [alias, spec] of Object.entries(declared)) {{
        const src = resolveJSImportSrc(spec && spec.src);
        const exportName = (spec && spec.export) || 'default';
        let namespace = null;
        try {{
            namespace = await import(src);
        }} catch (error) {{
            const detail = error && error.message ? error.message : String(error);
            throw new Error(
                `[SPRAG] Failed to import JS alias "${{alias}}" from "${{src}}": ${{detail}}`
            );
        }}
        const value = exportName === 'default'
            ? namespace.default
            : namespace[exportName];
        if (value === undefined) {{
            throw new Error(
                `[SPRAG] JS alias "${{alias}}" could not resolve export "${{exportName}}" from "${{src}}".`
            );
        }}
        resolved[alias] = value;
    }}
    window.__SPRAG_IMPORTS__ = resolved;
    return resolved;
}}

function renderBootError(error) {{
    const message = error && error.message ? error.message : String(error);
    const target = document.querySelector('#app-root') || document.body;
    if (!target) {{
        return;
    }}
    target.innerHTML = `
        <section data-sprag-boot-error style="max-width: 52rem; margin: 3rem auto; padding: 1.25rem; border: 1px solid #d7b2b2; border-radius: 12px; background: #fff4f4; color: #5f1d1d; font-family: ui-sans-serif, system-ui, sans-serif;">
          <h1 style="margin: 0 0 0.75rem; font-size: 1.25rem;">SPRAG boot error</h1>
          <p style="margin: 0; white-space: pre-wrap;">${{message}}</p>
        </section>
    `;
}}

const spragRoots = [];
let spragEventSource = null;
let spragSocket = null;
let spragSocketRegistryKey = null;
let spragDevReloadUnsub = null;
let spragBooted = false;

function provideRuntimeRoot(key, value, owner) {{
    ragotRegistry.provide(key, value, owner || null, {{ replace: true }});
    return key;
}}

function providerRegistryKeys(key, instance) {{
    const keys = [key];
    const legacyName = instance && typeof instance.name === 'string' ? instance.name.trim() : '';
    if (legacyName && !keys.includes(legacyName)) {{
        keys.push(legacyName);
    }}
    return keys;
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
    if (typeof spragDevReloadUnsub === 'function') {{
        try {{
            spragDevReloadUnsub();
        }} catch (error) {{
            console.warn('[SPRAG] Error while unregistering dev reload listener', error);
        }}
        spragDevReloadUnsub = null;
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

    const boot = payload.boot || payload.routeData || {{}};
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

function currentStoreSnapshots() {{
    const payload = window.__SPRAG_PAYLOAD__ || {{}};
    const stores = payload.stores || {{}};
    const bridges = (typeof window !== 'undefined' && window.__SPRAG_STORE_BRIDGES__) || {{}};
    const snapshot = JSON.parse(JSON.stringify(stores || {{}}));
    for (const [name, bridge] of Object.entries(bridges)) {{
        if (!bridge || typeof bridge.snapshot !== 'function') {{
            continue;
        }}
        try {{
            snapshot[name] = bridge.snapshot();
        }} catch (error) {{
            console.warn('[SPRAG] Failed to snapshot store during hot reload', name, error);
        }}
    }}
    return snapshot;
}}

function cloneSerializable(value, fallback = null) {{
    if (value === undefined) {{
        return fallback;
    }}
    try {{
        return JSON.parse(JSON.stringify(value));
    }} catch (_error) {{
        return fallback;
    }}
}}

function currentHydrationSnapshots() {{
    const hydration = [];
    for (const root of spragRoots) {{
        if (!root || root.type !== 'hydration' || !root.id) {{
            continue;
        }}
        hydration.push({{
            id: root.id,
            props: cloneSerializable(root.component && root.component.props, null),
            state: cloneSerializable(root.component && root.component.state, null),
            module_state: cloneSerializable(root.module && root.module.state, null),
        }});
    }}
    return hydration;
}}

function currentMountBootData() {{
    const mountRoot = spragRoots.find((root) => root && root.type === 'mount');
    if (!mountRoot) {{
        return null;
    }}
    const snapshot = {{}};
    const componentProps = cloneSerializable(mountRoot.component && mountRoot.component.props, null);
    const componentState = cloneSerializable(mountRoot.component && mountRoot.component.state, null);
    const moduleState = cloneSerializable(mountRoot.module && mountRoot.module.state, null);
    if (componentProps && typeof componentProps === 'object') {{
        Object.assign(snapshot, componentProps);
    }}
    if (componentState && typeof componentState === 'object') {{
        Object.assign(snapshot, componentState);
    }}
    if (moduleState && typeof moduleState === 'object') {{
        Object.assign(snapshot, moduleState);
    }}
    return Object.keys(snapshot).length > 0 ? snapshot : null;
}}

function persistDevReloadState(eventPayload) {{
    const path = (surface && surface.path) || '/';
    const key = window.__SPRAG_HOT_RELOAD_KEY__ || `sprag:reload:${{path}}`;
    const windowPayload = window.__SPRAG_PAYLOAD__ || {{}};
    const fingerprint = (eventPayload && eventPayload.store_fingerprint) || (windowPayload.fingerprints && windowPayload.fingerprints.store) || null;
    const surfaceFingerprint = (windowPayload.fingerprints && windowPayload.fingerprints.surface) || null;
    if (!fingerprint || !window.sessionStorage) {{
        return false;
    }}
    const cache = {{
        path,
        build_id: eventPayload && eventPayload.build_id !== undefined ? eventPayload.build_id : null,
        changed: eventPayload && Array.isArray(eventPayload.changed) ? eventPayload.changed : [],
        saved_at: Date.now(),
        store_fingerprint: fingerprint,
        surface_fingerprint: surfaceFingerprint,
        surface_kind: mount ? 'mount' : 'route',
        stores: currentStoreSnapshots(),
        metadata: cloneSerializable(
            window.__SPRAG_METADATA_STATE__ || windowPayload.metadata || {{}},
            {{}},
        ),
    }};
    if (mount) {{
        cache.boot_data = currentMountBootData();
    }} else {{
        cache.hydration = currentHydrationSnapshots();
    }}
    try {{
        window.sessionStorage.setItem(key, JSON.stringify(cache));
        if (window.__SPRAG_PAYLOAD__) {{
            window.__SPRAG_PAYLOAD__.stores = cache.stores;
        }}
        return true;
    }} catch (error) {{
        console.warn('[SPRAG] Failed to persist hot reload snapshot', error);
        return false;
    }}
}}

function registerDevReloadListener() {{
    if (typeof spragDevReloadUnsub === 'function') {{
        return;
    }}
    const unsubscribe = bus.on('sprag:dev.rebuild', (payload) => {{
        if (!payload || payload.ok === false) {{
            console.warn('[SPRAG] Rebuild failed; keeping current page live.', payload && payload.error);
            return;
        }}
        persistDevReloadState(payload);
        window.location.reload();
    }});
    spragDevReloadUnsub = typeof unsubscribe === 'function' ? unsubscribe : null;
}}

function createSocketUrl(path) {{
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${{protocol}}//${{window.location.host}}${{withSpragBase(path)}}`;
}}

function createSharedSocketClient(surface) {{
    const socketPath = '/__sprag__/socket';
    const listeners = new Map();
    const outboundQueue = [];
    const joinedTopics = new Set();
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

    function normalizeTopic(topic) {{
        if (topic === null || topic === undefined) {{
            return null;
        }}
        const raw = String(topic).trim();
        return raw || null;
    }}

    function encodeTopicMessage(action, topic) {{
        return JSON.stringify({{
            type: 'topic',
            action,
            topic,
            route: surface.path || '/',
        }});
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
            for (const topic of Array.from(joinedTopics)) {{
                ws.send(encodeTopicMessage('join', topic));
            }}
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
        joinTopic(topic) {{
            const normalized = normalizeTopic(topic);
            if (!normalized) {{
                return false;
            }}
            joinedTopics.add(normalized);
            if (ws && ws.readyState === WebSocket.OPEN) {{
                ws.send(encodeTopicMessage('join', normalized));
                return true;
            }}
            connect();
            return false;
        }},
        leaveTopic(topic) {{
            const normalized = normalizeTopic(topic);
            if (!normalized) {{
                return false;
            }}
            joinedTopics.delete(normalized);
            if (ws && ws.readyState === WebSocket.OPEN) {{
                ws.send(encodeTopicMessage('leave', normalized));
                return true;
            }}
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
            joinedTopics.clear();
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

async function boot() {{
    if (spragBooted) return;
    try {{
        applyMetadataSprag(payload.metadata || spragMetadataState);
        rewriteInternalLinks();
        registerDevReloadListener();
        await resolveSurfaceImports(surface);
        const socket = connectSocketBridge(surface);

        provideRuntimeRoot('sprag.route', surface, null);
        provideRuntimeRoot('sprag.actions', actionClient, null);

        const providers = surface.providers || {{}};
        for (const ObjectEntry of Object.entries(providers)) {{
            const key = ObjectEntry[0];
            const className = ObjectEntry[1];
            const ProviderClass = moduleRegistry[className];
            if (ProviderClass) {{
                const instance = new ProviderClass();
                instance.actions = actionClient;
                instance.socket = spragSocket;
                instance.route = surface;
                if (typeof instance.start === 'function') instance.start();
                const registryKeys = providerRegistryKeys(key, instance).map((registryKey) =>
                    provideRuntimeRoot(registryKey, instance, instance)
                );
                registerRuntimeRoot({{
                    type: 'provider',
                    module: instance,
                    registryKeys
                }});
            }}
        }}

        // Stores hydrate via the side-effect import of
        // './generated/stores.js' above — each store bridge reads its
        // window.__SPRAG_PAYLOAD__.stores[name] entry at module-load time, so by
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
        const payload = window.__SPRAG_PAYLOAD__ || {{}};
        const hydration = payload.hydration || [];
        hydration.forEach(mountHydrationEntry);
        if (socket && typeof socket.connect === 'function') {{
            socket.connect();
        }}
        connectBusBridge(route);
        spragBooted = true;
    }} catch (error) {{
        teardownSpragRuntime('boot-error');
        renderBootError(error);
        console.error(error);
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
        if "_provider_classes" in next_route:
            del next_route["_provider_classes"]
        if "_browser_classes" in next_route:
            del next_route["_browser_classes"]
        routes.append(next_route)
    mounts = []
    for mount in manifest.get("mounts", []):
        mounts.append(
            {
                key: value
                for key, value in mount.items()
                if key not in {"root_component_class", "root_module_class", "_provider_classes"}
            }
        )
    return {"errors": manifest.get("errors", []), "mounts": mounts, "routes": routes}
