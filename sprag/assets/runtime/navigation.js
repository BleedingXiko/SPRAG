function trimTrailingSlash(value) {
    if (!value || value === '/') {
        return '';
    }
    return value.replace(/\/+$/, '');
}

export function normalizePath(value) {
    if (!value || value === '/') {
        return '/';
    }
    return '/' + String(value).replace(/^\/+|\/+$/g, '');
}

export function normalizeComparablePath(value) {
    const normalized = normalizePath(value);
    return normalized === '/' ? '/' : normalized.replace(/\/+$/, '');
}

function deriveBasePrefix(currentPathname, surfacePath) {
    const actual = trimTrailingSlash(currentPathname || '/');
    const currentSurface = trimTrailingSlash(normalizePath(surfacePath || '/'));
    if (!currentSurface) {
        return actual;
    }
    if (actual === currentSurface) {
        return '';
    }
    if (actual.endsWith(currentSurface)) {
        return actual.slice(0, actual.length - currentSurface.length);
    }
    return '';
}

function buildInternalPathMap(manifest) {
    const pathMap = new Map([['/', '/']]);
    for (const entry of [...((manifest && manifest.routes) || []), ...((manifest && manifest.mounts) || [])]) {
        const canonical = entry.output || entry.path || '/';
        pathMap.set(normalizeComparablePath(canonical), canonical);
        if (entry.path) {
            pathMap.set(normalizeComparablePath(entry.path), canonical);
        }
    }
    return pathMap;
}

export function createNavigationRuntime({ manifest, surface }) {
    const pathMap = buildInternalPathMap(manifest || {});
    const basePrefix = deriveBasePrefix(
        typeof window !== 'undefined' ? window.location.pathname : '/',
        surface && surface.path,
    );

    function withSpragBase(path) {
        const normalized = normalizePath(path || '/');
        const prefix = trimTrailingSlash(basePrefix || '');
        if (!prefix) {
            return normalized;
        }
        return normalized === '/' ? `${prefix}/` : `${prefix}${normalized}`;
    }

    function resolveJSImportSrc(src) {
        if (!src) {
            return src;
        }
        if (
            src.startsWith('http://')
            || src.startsWith('https://')
            || src.startsWith('//')
            || src.startsWith('data:')
            || src.startsWith('blob:')
        ) {
            return src;
        }
        if (src.startsWith('/')) {
            return withSpragBase(src);
        }
        return src;
    }

    function rewriteInternalLinks() {
        if (typeof document === 'undefined') {
            return;
        }
        for (const anchor of document.querySelectorAll('a[href]')) {
            const rawHref = anchor.getAttribute('href');
            if (!rawHref || rawHref.startsWith('#') || rawHref.startsWith('mailto:') || rawHref.startsWith('tel:')) {
                continue;
            }
            try {
                const parsed = new URL(rawHref, window.location.origin);
                if (parsed.origin !== window.location.origin) {
                    continue;
                }
                const canonical = pathMap.get(normalizeComparablePath(parsed.pathname));
                if (!canonical) {
                    continue;
                }
                anchor.setAttribute('href', `${withSpragBase(canonical)}${parsed.search}${parsed.hash}`);
            } catch (_error) {
                // Ignore invalid href values.
            }
        }
    }

    function resolveNavigationTarget(target) {
        if (target === null || target === undefined || target === '') {
            throw new Error('[SPRAG] navigate(...) requires a non-empty target.');
        }
        const rawTarget = String(target);
        if (
            rawTarget.startsWith('#')
            || rawTarget.startsWith('mailto:')
            || rawTarget.startsWith('tel:')
            || rawTarget.startsWith('javascript:')
            || rawTarget.startsWith('data:')
        ) {
            return rawTarget;
        }
        try {
            const parsed = new URL(rawTarget, window.location.origin);
            if (parsed.origin !== window.location.origin) {
                return parsed.toString();
            }
            const canonical = pathMap.get(normalizeComparablePath(parsed.pathname)) || parsed.pathname || '/';
            return `${withSpragBase(canonical)}${parsed.search}${parsed.hash}`;
        } catch (_error) {
            return rawTarget;
        }
    }

    function navigate(target, options = {}) {
        const resolved = resolveNavigationTarget(target);
        const replace = typeof options === 'boolean'
            ? options
            : Boolean(options && options.replace);
        if (replace) {
            window.location.replace(resolved);
        } else {
            window.location.assign(resolved);
        }
        return resolved;
    }

    return {
        basePrefix,
        navigate,
        normalizeComparablePath,
        resolveJSImportSrc,
        resolveNavigationTarget,
        rewriteInternalLinks,
        withSpragBase,
    };
}
