import { $, createElement, clear, append } from '../vendor/ragot.esm.min.js';
import { provideOwned } from './registry.js';

export async function resolveSurfaceImports(currentSurface, resolveJSImportSrc) {
    const declared = (currentSurface && currentSurface.modules) || {};
    const resolved = {};
    for (const [alias, spec] of Object.entries(declared)) {
        const src = resolveJSImportSrc(spec && spec.src);
        const exportName = (spec && spec.export) || 'default';
        let namespace = null;
        try {
            namespace = await import(src);
        } catch (error) {
            const detail = error && error.message ? error.message : String(error);
            throw new Error(
                `[SPRAG] Failed to import JS alias "${alias}" from "${src}": ${detail}`,
            );
        }
        const value = exportName === 'default'
            ? namespace.default
            : namespace[exportName];
        if (value === undefined) {
            throw new Error(
                `[SPRAG] JS alias "${alias}" could not resolve export "${exportName}" from "${src}".`,
            );
        }
        resolved[alias] = value;
    }
    window.__SPRAG_IMPORTS__ = resolved;
    return resolved;
}

export function renderBootError(error) {
    const message = error && error.message ? error.message : String(error);
    const stack = error && error.stack ? error.stack : '';
    const target = $('#app-root') || document.body;
    if (!target) {
        return;
    }
    clear(target);
    append(target,
        createElement('section', {
            dataset: { spragBootError: '' },
            style: {
                maxWidth: '52rem',
                margin: '3rem auto',
                padding: '1.25rem',
                border: '1px solid #d7b2b2',
                borderRadius: '12px',
                background: '#fff4f4',
                color: '#5f1d1d',
                fontFamily: 'ui-sans-serif, system-ui, sans-serif',
            },
        },
            createElement('h1', {
                style: { margin: '0 0 0.75rem', fontSize: '1.25rem' },
                textContent: 'SPRAG boot error',
            }),
            createElement('p', {
                style: { margin: '0', whiteSpace: 'pre-wrap' },
                textContent: message,
            }),
            stack ? createElement('pre', {
                style: {
                    margin: '1rem 0 0',
                    padding: '0.75rem',
                    overflowX: 'auto',
                    whiteSpace: 'pre-wrap',
                    borderRadius: '8px',
                    background: '#3b1111',
                    color: '#ffe6e6',
                    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                    fontSize: '0.8rem',
                    lineHeight: '1.45',
                },
                textContent: stack,
            }) : null,
        ),
    );
}

function mountRecord(root, record) {
    root.records.push(record);
    if (record.module) {
        root.adopt(record.module);
    } else if (record.component) {
        root.adopt(record.component);
    }
    return record;
}

function createComponent(ComponentClass, state, props) {
    return new ComponentClass(state || {}, {
        props: props || {},
        module: null,
    });
}

function wireModuleAndComponent(root, module, component, target, registryId) {
    module.actions = root.actionClient;
    module.route = root.surface;
    module.socket = root.socket;
    module.component = component;
    module.element = target;
    component.module = module;
    const syncFn = typeof module.syncComponent === 'function'
        ? (ownedComponent, state) => module.syncComponent(ownedComponent, state)
        : (ownedComponent, state) => ownedComponent.setState(state);
    module.adoptComponent(component, {
        startArgs: [target],
        sync: syncFn,
    });
    module.start();
    provideOwned(`${registryId.module}:${registryId.id}`, module, module);
    provideOwned(`${registryId.component}:${registryId.id}`, component, module);
}

export function mountHydrationEntries(root) {
    const hydration = (root.payload && root.payload.hydration) || [];
    for (const entry of hydration) {
        mountHydrationEntry(root, entry);
    }
}

export function mountHydrationEntry(root, entry) {
    const target = $(`[data-sprag-hydrate-id="${entry.id}"]`);
    if (!target) {
        return null;
    }

    const ComponentClass = root.componentRegistry[entry.component];
    if (!ComponentClass) {
        console.warn('[SPRAG] Missing generated component for', entry.component);
        return null;
    }

    const ModuleClass = entry.module ? root.moduleRegistry[entry.module] : null;
    const component = createComponent(ComponentClass, entry.state, entry.props);
    clear(target);

    if (ModuleClass) {
        const module = new ModuleClass(entry.module_state || {});
        wireModuleAndComponent(
            root,
            module,
            component,
            target,
            { id: entry.id, module: entry.module, component: entry.component },
        );
        return mountRecord(root, {
            type: 'hydration',
            id: entry.id,
            module,
            component,
        });
    }

    component.mount(target);
    provideOwned(`${entry.component}:${entry.id}`, component, component);
    return mountRecord(root, {
        type: 'hydration',
        id: entry.id,
        module: null,
        component,
    });
}

export function mountClientSurface(root) {
    const target = $('#app-root');
    if (!target) {
        return null;
    }

    const mount = root.payload && root.payload.mount;
    const boot = (root.payload && (root.payload.boot || root.payload.routeData)) || {};
    const ComponentClass = mount && root.componentRegistry[mount.component];
    if (!ComponentClass) {
        console.warn('[SPRAG] Missing generated component for mount', mount && mount.component);
        return null;
    }

    const ModuleClass = mount && mount.module ? root.moduleRegistry[mount.module] : null;
    const component = createComponent(ComponentClass, boot, boot);
    clear(target);

    if (ModuleClass) {
        const module = new ModuleClass(boot || {});
        wireModuleAndComponent(
            root,
            module,
            component,
            target,
            { id: mount.path, module: mount.module, component: mount.component },
        );
        return mountRecord(root, {
            type: 'mount',
            path: mount.path,
            module,
            component,
        });
    }

    component.mount(target);
    provideOwned(`${mount.component}:${mount.path}`, component, component);
    return mountRecord(root, {
        type: 'mount',
        path: mount.path,
        module: null,
        component,
    });
}
