"""High-level emitters for the SPRAG build pipeline.

These functions are the public surface of the codegen package: the
SPRAG compiler invokes them to write the Ragot runtime, the generated
component / module sources, and the browser entry point that wires
hydration, the action client, and the event-source bus bridge.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ...runtime.stores import StoreBridge
from .components import compile_component_artifact
from .dependencies import used_browser_class_refs
from .mappings import JSCodegenError
from .modules import compile_module_artifact


def emit_ragot_runtime(output_dir: Path, project_root: Path) -> None:
    vendor_dir = output_dir / "vendor"
    vendor_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir = output_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
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
    for source in (assets_dir / "runtime").glob("*.js"):
        shutil.copyfile(source, runtime_dir / source.name)


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
        artifact = compile_component_artifact(
            component_class,
            declared_import_aliases=declared_import_aliases,
        )
        (components_dir / f"{name}.js").write_text(artifact.code, encoding="utf-8")
        if artifact.source_map:
            (components_dir / f"{name}.js.map").write_text(artifact.source_map, encoding="utf-8")

    for name, module_class in module_classes.items():
        artifact = compile_module_artifact(
            module_class,
            declared_import_aliases=declared_import_aliases,
        )
        (modules_dir / f"{name}.js").write_text(artifact.code, encoding="utf-8")
        if artifact.source_map:
            (modules_dir / f"{name}.js.map").write_text(artifact.source_map, encoding="utf-8")

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
        reset() {
            _store.batch((state) => {
                for (const key of Object.keys(state)) {
                    delete state[key];
                }
                Object.assign(state, JSON.parse(JSON.stringify(initial)));
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
        listen(path, listener) {
            return bridge.subscribe(listener, { selector: path });
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
            f"_hydrated[{name_js}] !== undefined "
            f"? {{ ...{initial_js}, ..._hydrated[{name_js}] }} : {initial_js}"
            f");"
        )
    lines.append("")
    (generated_dir / "stores.js").write_text("\n".join(lines), encoding="utf-8")


def emit_manifest_module(output_dir: Path, manifest: dict) -> None:
    generated_dir = output_dir / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    serializable = _serializable_manifest(manifest)
    (generated_dir / "manifest.js").write_text(
        "const manifest = " + json.dumps(serializable, indent=2, sort_keys=True) + ";\n"
        "export default manifest;\n",
        encoding="utf-8",
    )


def build_browser_entry(manifest: dict) -> str:
    serializable = _serializable_manifest(manifest)
    return f"""import {{ componentRegistry, moduleRegistry }} from './generated/index.js';
import './generated/stores.js';
import {{ startSurfaceBoot }} from './runtime/boot.js';

const manifest = {json.dumps(serializable, indent=2, sort_keys=True)};

startSurfaceBoot({{
    manifest,
    componentRegistry,
    moduleRegistry,
}});
"""


def build_surface_entry(surface_ref: dict) -> str:
    return f"""import manifest from '../generated/manifest.js';
import {{ componentRegistry, moduleRegistry }} from '../generated/index.js';
import '../generated/stores.js';
import {{ startSurfaceBoot }} from '../runtime/boot.js';

startSurfaceBoot({{
    manifest,
    surfaceRef: {json.dumps(surface_ref, indent=2, sort_keys=True)},
    componentRegistry,
    moduleRegistry,
}});
"""


def emit_surface_entries(output_dir: Path, manifest: dict) -> None:
    surfaces_dir = output_dir / "surfaces"
    surfaces_dir.mkdir(parents=True, exist_ok=True)
    for route in manifest.get("routes", []):
        filename = surface_entry_filename("route", route["path"])
        (surfaces_dir / filename).write_text(
            build_surface_entry({"kind": "route", "path": route["path"]}),
            encoding="utf-8",
        )
    for mount in manifest.get("mounts", []):
        filename = surface_entry_filename("mount", mount["path"])
        (surfaces_dir / filename).write_text(
            build_surface_entry({"kind": "mount", "path": mount["path"]}),
            encoding="utf-8",
        )


def surface_entry_filename(kind: str, path: str) -> str:
    slug = path.strip("/").replace("/", "__") or "index"
    return f"{kind}__{slug}.js"


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
