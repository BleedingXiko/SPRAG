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

export function startSurfaceBoot({
    componentRegistry,
    manifest,
    moduleRegistry,
    surfaceRef = null,
}) {
    let activeRoot = null;

    async function boot() {
        if (activeRoot && !activeRoot.stopped) {
            return activeRoot;
        }
        try {
            const payload = window.__SPRAG_PAYLOAD__ || {};
            const surface = resolveSurface(manifest, payload, surfaceRef);
            if (!surface) {
                throw new Error('[SPRAG] Could not resolve the current surface.');
            }

            window.__SPRAG_MANIFEST__ = manifest || {};
            window.__SPRAG_ENV__ = payload.env || {};
            window.__SPRAG_IMPORTS__ = window.__SPRAG_IMPORTS__ || {};

            const navigation = createNavigationRuntime({ manifest, surface });
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
                componentRegistry,
                manifest,
                metadata,
                moduleRegistry,
                navigation,
                payload,
                surface,
                uploadClient,
            });

            const teardown = (reason = 'teardown') => {
                if (activeRoot === root) {
                    activeRoot = null;
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

            root.boot();
            activeRoot = root;
            return root;
        } catch (error) {
            if (activeRoot) {
                activeRoot.stop('boot-error');
                activeRoot = null;
            }
            renderBootError(error);
            console.error(error);
            return null;
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            void boot();
        }, { once: true });
    } else {
        void boot();
    }

    window.addEventListener('pagehide', () => {
        if (activeRoot) {
            activeRoot.stop('pagehide');
            activeRoot = null;
        }
    });
    window.addEventListener('pageshow', (event) => {
        if (event.persisted && !activeRoot) {
            void boot();
        }
    });

    return {
        boot,
        teardown(reason = 'teardown') {
            if (activeRoot) {
                activeRoot.stop(reason);
                activeRoot = null;
            }
        },
    };
}
