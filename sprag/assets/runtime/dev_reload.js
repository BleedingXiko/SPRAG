import { bus } from '../vendor/ragot.esm.min.js';
import { pushError, clearErrors } from './dev_overlay.js';

function currentStoreSnapshots() {
    const payload = window.__SPRAG_PAYLOAD__ || {};
    const stores = payload.stores || {};
    const bridges = (typeof window !== 'undefined' && window.__SPRAG_STORE_BRIDGES__) || {};
    const snapshot = JSON.parse(JSON.stringify(stores || {}));
    for (const [name, bridge] of Object.entries(bridges)) {
        if (!bridge || typeof bridge.snapshot !== 'function') {
            continue;
        }
        try {
            snapshot[name] = bridge.snapshot();
        } catch (error) {
            console.warn('[SPRAG] Failed to snapshot store during hot reload', name, error);
        }
    }
    return snapshot;
}

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

function persistDevReloadState(root, eventPayload) {
    const path = (root.surface && root.surface.path) || '/';
    const key = window.__SPRAG_HOT_RELOAD_KEY__ || `sprag:reload:${path}`;
    const windowPayload = window.__SPRAG_PAYLOAD__ || {};
    const fingerprint = (eventPayload && eventPayload.store_fingerprint) || (windowPayload.fingerprints && windowPayload.fingerprints.store) || null;
    const surfaceFingerprint = (windowPayload.fingerprints && windowPayload.fingerprints.surface) || null;
    if (!fingerprint || !window.sessionStorage) {
        return false;
    }
    const cache = {
        path,
        build_id: eventPayload && eventPayload.build_id !== undefined ? eventPayload.build_id : null,
        changed: eventPayload && Array.isArray(eventPayload.changed) ? eventPayload.changed : [],
        saved_at: Date.now(),
        store_fingerprint: fingerprint,
        surface_fingerprint: surfaceFingerprint,
        surface_kind: root.payload && root.payload.mount ? 'mount' : 'route',
        stores: currentStoreSnapshots(),
        metadata: cloneSerializable(
            (root.metadata && root.metadata.snapshot && root.metadata.snapshot()) || window.__SPRAG_METADATA_STATE__ || windowPayload.metadata || {},
            {},
        ),
    };
    if (root.payload && root.payload.mount) {
        cache.boot_data = root.currentMountBootData();
    } else {
        cache.hydration = root.currentHydrationSnapshots();
    }
    try {
        window.sessionStorage.setItem(key, JSON.stringify(cache));
        if (window.__SPRAG_PAYLOAD__) {
            window.__SPRAG_PAYLOAD__.stores = cache.stores;
        }
        return true;
    } catch (error) {
        console.warn('[SPRAG] Failed to persist hot reload snapshot', error);
        return false;
    }
}

export function registerDevReloadListener(root) {
    return bus.on('sprag:dev.rebuild', (payload) => {
        if (!payload || payload.ok === false) {
            const errorText = (payload && payload.error) || 'Unknown build error';
            console.warn('[SPRAG] Rebuild failed; keeping current page live.', errorText);
            pushError({
                kind: 'build',
                title: 'Rebuild failed',
                message: errorText,
            });
            return;
        }
        clearErrors();
        persistDevReloadState(root, payload);
        window.location.reload();
    });
}
