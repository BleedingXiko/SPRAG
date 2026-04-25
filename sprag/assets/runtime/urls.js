// SPRAG URL composition helper.
//
// Mirrors sprag.runtime.urls.join_url in Python, with one browser-only
// behavior: root-relative results are prefixed with window.__SPRAG_BASE__
// when the app is hosted under a path prefix (e.g. GitHub Pages /project/).

const EXTERNAL_RE = /^(?:https?:)?\/\//i;

function _stripSlashes(value) {
    return String(value).replace(/^\/+|\/+$/g, '');
}

function _isExternal(value) {
    return EXTERNAL_RE.test(value);
}

function _applySpragBase(path) {
    const base = (typeof window !== 'undefined' && window.__SPRAG_BASE__) || '';
    if (!base || !path || path.charAt(0) !== '/') {
        return path;
    }
    const trimmed = base.replace(/\/+$/, '');
    return path === '/' ? `${trimmed}/` : `${trimmed}${path}`;
}

export function joinUrl(base = '/', ...parts) {
    const rawParts = [];
    for (const part of parts) {
        if (part === null || part === undefined) continue;
        const cleaned = _stripSlashes(part);
        if (cleaned.length > 0) rawParts.push(cleaned);
    }
    const baseStr = String(base == null ? '/' : base).trim();

    if (_isExternal(baseStr)) {
        if (rawParts.length === 0) return baseStr;
        return baseStr.replace(/\/+$/, '') + '/' + rawParts.join('/');
    }

    const prefix = !baseStr ? '/' : '/' + _stripSlashes(baseStr);
    const composed = rawParts.length === 0
        ? prefix
        : prefix.replace(/\/+$/, '') + '/' + rawParts.join('/');

    return _applySpragBase(composed);
}
