import { Module } from '../vendor/ragot.esm.min.js';
import { actionErrorMessageSprag, createActionClient } from './actions.js';
import { registerDevReloadListener } from './dev_reload.js';
import { resolveSurfaceImports, renderBootError } from './hydration.js';
import { MetadataManager } from './metadata.js';
import { createNavigationRuntime } from './navigation.js';
import { SurfaceRoot } from './surface_root.js';
import { createUploadClient, formDataSprag } from './uploads.js';

function resolveSurface(manifest, payload, surfaceRef) {
    const liveSurface = payload.mount || payload.page || null;
    if (!surfaceRef) {
        return liveSurface;
    }

    const collection = surfaceRef.kind === 'mount'
        ? (manifest && manifest.mounts) || []
        : (manifest && manifest.routes) || [];
    const matched = collection.find((entry) => entry.path === surfaceRef.path) || null;
    if (liveSurface && matched) {
        return { ...matched, ...liveSurface };
    }
    return liveSurface || matched;
}

class BootModule extends Module {
    constructor({
        componentRegistry,
        manifest,
        moduleRegistry,
        surfaceRef = null,
    }) {
        super();
        this._componentRegistry = componentRegistry;
        this._manifest = manifest;
        this._moduleRegistry = moduleRegistry;
        this._surfaceRef = surfaceRef;
        this._activeRoot = null;
    }

    onStart() {
        this.on(window, 'pagehide', () => {
            if (this._activeRoot) {
                this._activeRoot.stop('pagehide');
                this._activeRoot = null;
            }
        });

        this.on(window, 'pageshow', (event) => {
            if (event.persisted && !this._activeRoot) {
                void this.boot();
            }
        });

        void this.boot();
    }

    async boot() {
        if (this._activeRoot && !this._activeRoot.stopped) {
            return this._activeRoot;
        }
        try {
            const payload = window.__SPRAG_PAYLOAD__ || {};
            const surface = resolveSurface(this._manifest, payload, this._surfaceRef);
            if (!surface) {
                throw new Error('[SPRAG] Could not resolve the current surface.');
            }

            window.__SPRAG_MANIFEST__ = this._manifest || {};
            window.__SPRAG_ENV__ = payload.env || {};
            window.__SPRAG_IMPORTS__ = window.__SPRAG_IMPORTS__ || {};

            const navigation = createNavigationRuntime({ manifest: this._manifest, surface });
            window.__SPRAG_BASE__ = navigation.basePrefix || '';
            window.__SPRAG_NAVIGATE__ = navigation.navigate;

            const metadata = new MetadataManager({
                surface,
                payload,
                bootTitle: (typeof document !== 'undefined' && document.title) || '',
            });
            window.__SPRAG_METADATA_STATE__ = metadata.snapshot();
            window.__SPRAG_SET_METADATA__ = metadata.set.bind(metadata);
            metadata.apply(payload.metadata || metadata.snapshot());

            window.__SPRAG_ACTION_ERROR_MESSAGE__ = actionErrorMessageSprag;
            window.__SPRAG_FORM_DATA__ = formDataSprag;

            const actionClient = createActionClient({
                currentRoute: surface,
                navigate: navigation.navigate,
                withSpragBase: navigation.withSpragBase,
            });
            const uploadClient = createUploadClient({
                currentRoute: surface,
                navigate: navigation.navigate,
                withSpragBase: navigation.withSpragBase,
            });
            window.__SPRAG_ACTIONS__ = actionClient;
            window.__SPRAG_UPLOADS__ = uploadClient;

            navigation.rewriteInternalLinks();
            await resolveSurfaceImports(surface, navigation.resolveJSImportSrc);

            const root = new SurfaceRoot({
                actionClient,
                componentRegistry: this._componentRegistry,
                manifest: this._manifest,
                metadata,
                moduleRegistry: this._moduleRegistry,
                navigation,
                payload,
                surface,
                uploadClient,
            });

            const teardown = (reason = 'teardown') => {
                if (this._activeRoot === root) {
                    this._activeRoot = null;
                }
                root.stop(reason);
            };
            teardown.__spragRoot = root;
            window.__SPRAG_TEARDOWN__ = teardown;
            window.__SPRAG_RUNTIME_ROOT__ = root;

            const unsubscribe = registerDevReloadListener(root);
            if (typeof unsubscribe === 'function') {
                root.addCleanup(unsubscribe);
            }

            root.start();
            this._activeRoot = root;
            this.adopt(root);
            return root;
        } catch (error) {
            if (this._activeRoot) {
                this._activeRoot.stop('boot-error');
                this._activeRoot = null;
            }
            renderBootError(error);
            console.error(error);
            return null;
        }
    }

    teardown(reason = 'teardown') {
        if (this._activeRoot) {
            this._activeRoot.stop(reason);
            this._activeRoot = null;
        }
    }
}

export function startSurfaceBoot({
    componentRegistry,
    manifest,
    moduleRegistry,
    surfaceRef = null,
}) {
    const boot = new BootModule({
        componentRegistry,
        manifest,
        moduleRegistry,
        surfaceRef,
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            boot.start();
        }, { once: true });
    } else {
        boot.start();
    }

    return {
        boot() {
            return boot.boot();
        },
        teardown(reason = 'teardown') {
            boot.teardown(reason);
        },
    };
}
