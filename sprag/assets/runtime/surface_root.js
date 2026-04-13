import { bus } from '../vendor/ragot.esm.min.js';
import { RuntimeOwner, providerRegistryKeys, provideOwned } from './registry.js';
import { mountClientSurface, mountHydrationEntries } from './hydration.js';
import { createEventSourceBridge, createSurfaceSocketClient } from './sockets.js';

function cloneSerializable(value, fallback = null) {
    if (value === undefined) {
        return fallback;
    }
    try {
        return JSON.parse(JSON.stringify(value));
    } catch (_error) {
        return fallback;
    }
}

export class SurfaceRoot extends RuntimeOwner {
    constructor({
        actionClient,
        componentRegistry,
        manifest,
        metadata,
        moduleRegistry,
        navigation,
        payload,
        surface,
        uploadClient,
    }) {
        super(`sprag.surface.${(surface && surface.path) || 'unknown'}`);
        this.actionClient = actionClient;
        this.componentRegistry = componentRegistry || {};
        this.manifest = manifest || {};
        this.metadata = metadata;
        this.moduleRegistry = moduleRegistry || {};
        this.navigation = navigation;
        this.payload = payload || {};
        this.records = [];
        this.eventSource = null;
        this.socket = null;
        this.surface = surface || {};
        this.uploadClient = uploadClient;
    }

    boot() {
        provideOwned('sprag.route', this.surface, this);
        provideOwned('sprag.actions', this.actionClient, this);

        if (this.surface && this.surface.socket_bridge && typeof window.WebSocket === 'function') {
            this.socket = createSurfaceSocketClient({
                surface: this.surface,
                withSpragBase: this.navigation.withSpragBase,
            });
            this.socket.connect();
            window.__SPRAG_SOCKET__ = this.socket;
            provideOwned('sprag.socket', this.socket, this);
            this.adopt(this.socket);
        }

        const providers = (this.surface && this.surface.providers) || {};
        for (const [key, className] of Object.entries(providers)) {
            const ProviderClass = this.moduleRegistry[className];
            if (!ProviderClass) {
                continue;
            }
            const instance = new ProviderClass();
            instance.actions = this.actionClient;
            instance.route = this.surface;
            instance.socket = this.socket;
            if (typeof instance.start === 'function') {
                instance.start();
            }
            for (const registryKey of providerRegistryKeys(key, instance)) {
                provideOwned(registryKey, instance, instance);
            }
            this.adopt(instance);
        }

        if (this.payload && this.payload.mount) {
            mountClientSurface(this);
        } else {
            mountHydrationEntries(this);
        }

        this.eventSource = createEventSourceBridge({
            surface: this.surface,
            withSpragBase: this.navigation.withSpragBase,
        });
        window.__SPRAG_EVENT_SOURCE__ = this.eventSource;
        this.adopt(this.eventSource);

        return this;
    }

    currentHydrationSnapshots() {
        const hydration = [];
        for (const record of this.records) {
            if (!record || record.type !== 'hydration' || !record.id) {
                continue;
            }
            hydration.push({
                id: record.id,
                props: cloneSerializable(record.component && record.component.props, null),
                state: cloneSerializable(record.component && record.component.state, null),
                module_state: cloneSerializable(record.module && record.module.state, null),
            });
        }
        return hydration;
    }

    currentMountBootData() {
        const mountRoot = this.records.find((record) => record && record.type === 'mount');
        if (!mountRoot) {
            return null;
        }
        const snapshot = {};
        const componentProps = cloneSerializable(mountRoot.component && mountRoot.component.props, null);
        const componentState = cloneSerializable(mountRoot.component && mountRoot.component.state, null);
        const moduleState = cloneSerializable(mountRoot.module && mountRoot.module.state, null);
        if (componentProps && typeof componentProps === 'object') {
            Object.assign(snapshot, componentProps);
        }
        if (componentState && typeof componentState === 'object') {
            Object.assign(snapshot, componentState);
        }
        if (moduleState && typeof moduleState === 'object') {
            Object.assign(snapshot, moduleState);
        }
        return Object.keys(snapshot).length > 0 ? snapshot : null;
    }

    stop(reason = 'teardown') {
        super.stop(reason);
        if (window.__SPRAG_RUNTIME_ROOT__ === this) {
            window.__SPRAG_RUNTIME_ROOT__ = null;
        }
        if (window.__SPRAG_SOCKET__ === this.socket) {
            window.__SPRAG_SOCKET__ = null;
        }
        if (window.__SPRAG_EVENT_SOURCE__ === this.eventSource) {
            window.__SPRAG_EVENT_SOURCE__ = null;
        }
        if (window.__SPRAG_ACTIONS__ === this.actionClient) {
            window.__SPRAG_ACTIONS__ = null;
        }
        if (window.__SPRAG_UPLOADS__ === this.uploadClient) {
            window.__SPRAG_UPLOADS__ = null;
        }
        if (window.__SPRAG_TEARDOWN__ && window.__SPRAG_TEARDOWN__.__spragRoot === this) {
            window.__SPRAG_TEARDOWN__ = null;
        }
        bus.emit('sprag:teardown', { reason });
    }
}
