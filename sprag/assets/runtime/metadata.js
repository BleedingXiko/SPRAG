import { $$, createElement, append, remove, attr } from '../vendor/ragot.esm.min.js';

function metadataContent(value) {
    if (Array.isArray(value)) {
        return value
            .filter((item) => item !== null && item !== undefined && item !== '')
            .map((item) => String(item))
            .join(', ');
    }
    if (value === null || value === undefined) {
        return '';
    }
    return String(value);
}

function normalizeMetadataObject(input) {
    const next = {};
    if (!input || typeof input !== 'object') {
        return next;
    }
    for (const [rawKey, value] of Object.entries(input)) {
        const key = String(rawKey || '').trim();
        if (!key) {
            continue;
        }
        const content = metadataContent(value);
        if (!content) {
            continue;
        }
        next[key] = content;
    }
    return next;
}

function listManagedHeadElements() {
    if (typeof document === 'undefined' || !document.head) {
        return [];
    }
    return $$('[data-sprag-head="true"]', document.head);
}

function managedHeadElementsForKey(key) {
    return listManagedHeadElements().filter(
        (element) => element.getAttribute('data-sprag-head-key') === key,
    );
}

function ensureManagedHeadElement(key) {
    const matches = managedHeadElementsForKey(key);
    const first = matches[0] || null;
    for (const duplicate of matches.slice(1)) {
        remove(duplicate);
    }

    let element = first;
    if (key === 'canonical') {
        if (!element || element.tagName !== 'LINK') {
            if (element) {
                remove(element);
            }
            element = createElement('link', { rel: 'canonical' });
            append(document.head, element);
        }
    } else {
        if (!element || element.tagName !== 'META') {
            if (element) {
                remove(element);
            }
            element = createElement('meta');
            append(document.head, element);
        }
        const nameAttr = key.startsWith('og:') ? 'property' : 'name';
        const staleAttr = nameAttr === 'property' ? 'name' : 'property';
        element.removeAttribute(staleAttr);
        attr(element, { [nameAttr]: key });
    }

    attr(element, {
        'data-sprag-head': 'true',
        'data-sprag-head-key': key,
    });
    return element;
}

export class MetadataManager {
    constructor({ surface, payload, bootTitle }) {
        this.surface = surface || {};
        this.payload = payload || {};
        this.bootTitle = bootTitle || '';
        this.state = normalizeMetadataObject((payload && payload.metadata) || {});
    }

    apply(metadata) {
        const normalized = normalizeMetadataObject(metadata);
        const nextKeys = new Set(Object.keys(normalized).filter((key) => key !== 'title'));
        for (const element of listManagedHeadElements()) {
            const key = element.getAttribute('data-sprag-head-key') || '';
            if (!nextKeys.has(key)) {
                remove(element);
            }
        }

        for (const [key, content] of Object.entries(normalized)) {
            if (key === 'title') {
                continue;
            }
            const element = ensureManagedHeadElement(key);
            if (key === 'canonical') {
                attr(element, { href: content });
            } else {
                attr(element, { content });
            }
        }

        if (typeof document !== 'undefined') {
            const resolvedTitle = normalized.title || this.bootTitle || this.surface.name || this.surface.path || document.title || '';
            if (resolvedTitle) {
                document.title = resolvedTitle;
            }
        }

        this.state = normalized;
        window.__SPRAG_METADATA_STATE__ = { ...this.state };
        if (window.__SPRAG_PAYLOAD__) {
            window.__SPRAG_PAYLOAD__.metadata = { ...this.state };
        }
        return this.state;
    }

    set(metadata = {}, options = {}) {
        const replace = typeof options === 'boolean'
            ? options
            : Boolean(options && options.replace);
        const input = metadata && typeof metadata === 'object' ? metadata : {};
        const next = replace ? {} : { ...this.state };
        for (const [rawKey, value] of Object.entries(input)) {
            const key = String(rawKey || '').trim();
            if (!key) {
                continue;
            }
            if (value === null || value === undefined || value === '') {
                delete next[key];
                continue;
            }
            const content = metadataContent(value);
            if (!content) {
                delete next[key];
                continue;
            }
            next[key] = content;
        }
        return this.apply(next);
    }

    snapshot() {
        return { ...this.state };
    }
}
