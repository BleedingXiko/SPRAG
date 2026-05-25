/**
 * SPRAG dev error overlay.
 *
 * Shows build errors, boot errors, and runtime exceptions in a dismissible
 * overlay panel so the developer never has to check the terminal or DevTools
 * console just to discover something broke.
 *
 * Only active when the surface payload includes `dev_reload: true` (i.e.,
 * `sprag dev` is running). Errors are surfaced via a Ragot `Module` whose
 * lifecycle owns the overlay DOM node and the Escape-key listener. The
 * page-level error / unhandledrejection listeners are installed once per
 * page (they need to catch errors that fire before any Module has started)
 * and they route through the active overlay via the module-level
 * `pushError(...)` indirection below.
 */

import { Module, createElement, attr, append, remove } from '../vendor/ragot.esm.min.js';

const OVERLAY_ID = '__sprag_dev_overlay__';
const MAX_ERRORS = 50;
const STACK_FRAME_RE = /^\s*at\s+(?:(.*?)\s+\()?(.+?\.js):(\d+):(\d+)\)?\s*$/;
const SAFARI_STACK_FRAME_RE = /^\s*(.*?)@(.+?\.js):(\d+):(\d+)\s*$/;

let _activeOverlay = null;
let _pendingErrors = [];
let _globalListenersInstalled = false;
const _sourceMapCache = new Map();

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/** Create a DevOverlayModule. Callers should `.start()` then `.adopt()` it. */
export function createDevOverlay() {
    return new DevOverlayModule();
}

/**
 * Install page-level error / unhandledrejection listeners.
 *
 * These cannot be owned by a Module because they need to be alive before any
 * Module has started (to catch early boot errors). They're installed once per
 * page load; dev reloads do a full `location.reload()` so there's no accrual
 * concern. Errors flow through `pushError(...)` which routes to the active
 * DevOverlayModule or buffers until one starts.
 */
export function installGlobalCatchers() {
    if (_globalListenersInstalled || typeof window === 'undefined') {
        return;
    }
    _globalListenersInstalled = true;

    window.addEventListener('error', (event) => {
        if (!_isDevMode()) {
            return;
        }
        const error = event.error;
        pushError({
            kind: 'runtime',
            title: 'Uncaught error',
            message: error && error.message ? error.message : event.message || 'Unknown error',
            stack: error && error.stack ? error.stack : '',
            source: event.filename
                ? `${event.filename}:${event.lineno}:${event.colno}`
                : null,
        });
    });

    window.addEventListener('unhandledrejection', (event) => {
        if (!_isDevMode()) {
            return;
        }
        const reason = event.reason;
        pushError({
            kind: 'runtime',
            title: 'Unhandled promise rejection',
            message: reason && reason.message ? reason.message : String(reason || 'Unknown rejection'),
            stack: reason && reason.stack ? reason.stack : '',
        });
    });
}

/** Push an error into the active overlay (or buffer if none yet). */
export function pushError(entry) {
    if (!_isDevMode()) {
        return;
    }
    if (_activeOverlay) {
        _activeOverlay.pushError(entry);
    } else {
        _pendingErrors.push(entry);
        if (_pendingErrors.length > MAX_ERRORS) {
            _pendingErrors = _pendingErrors.slice(-MAX_ERRORS);
        }
    }
}

/** Clear all errors and hide the overlay. */
export function clearErrors() {
    _pendingErrors = [];
    if (_activeOverlay) {
        _activeOverlay.clearErrors();
    }
}

/** Return a shallow copy of the current error list. */
export function getErrors() {
    return _activeOverlay ? _activeOverlay.getErrors() : _pendingErrors.slice();
}

/** Whether the overlay is currently visible. */
export function isVisible() {
    return _activeOverlay ? _activeOverlay.isVisible() : false;
}

// ---------------------------------------------------------------------------
// Error-boundary wrappers for component/module lifecycle
// ---------------------------------------------------------------------------

/**
 * Wrap a function so that any thrown error is pushed to the overlay
 * and also re-thrown (so Ragot's own lifecycle accounting is not confused).
 */
export function guardLifecycle(fn, context) {
    return function guarded(...args) {
        try {
            return fn.apply(this, args);
        } catch (error) {
            pushError({
                kind: 'runtime',
                title: `${context || 'Component'} error`,
                message: error && error.message ? error.message : String(error),
                stack: error && error.stack ? error.stack : '',
                source: context || null,
            });
            throw error;
        }
    };
}

/**
 * Wrap a component or module class so that key lifecycle methods are guarded.
 * Returns the same class (mutated) for convenience.
 */
export function guardClass(cls, label) {
    const proto = cls && cls.prototype;
    if (!proto) {
        return cls;
    }
    const methods = ['onStart', 'onStop', 'render', 'onMount', 'onUnmount'];
    for (const name of methods) {
        if (typeof proto[name] === 'function') {
            proto[name] = guardLifecycle(proto[name], `${label || cls.name}.${name}`);
        }
    }
    return cls;
}

// ---------------------------------------------------------------------------
// DevOverlayModule — owns the overlay DOM node and Escape-key listener
// ---------------------------------------------------------------------------

class DevOverlayModule extends Module {
    constructor() {
        super();
        this._label = 'sprag.dev_overlay';
        this.errors = [];
        this.dismissed = false;
        this.overlayEl = null;
    }

    onStart() {
        _activeOverlay = this;

        // Escape-to-dismiss is lifecycle-owned: registered via Module.on
        // so it's torn down automatically on stop().
        this.on(document, 'keydown', (event) => {
            if (event.key === 'Escape' && this.isVisible()) {
                this.dismissed = true;
                this._hide();
            }
        });

        // Flush anything that fired before the overlay was started.
        if (_pendingErrors.length > 0) {
            const pending = _pendingErrors;
            _pendingErrors = [];
            for (const entry of pending) {
                this.pushError(entry);
            }
        }
    }

    onStop() {
        if (_activeOverlay === this) {
            _activeOverlay = null;
        }
        if (this.overlayEl) {
            remove(this.overlayEl);
            this.overlayEl = null;
        }
        this.errors = [];
        this.dismissed = false;
    }

    pushError(entry) {
        const normalized = {
            timestamp: Date.now(),
            kind: entry.kind || 'runtime',
            title: entry.title || 'Error',
            message: entry.message || '',
            stack: entry.stack || '',
            source: entry.source || null,
            mappedFrames: [],
            mappedStack: '',
            sourceLineText: '',
        };
        this.errors.push(normalized);
        if (this.errors.length > MAX_ERRORS) {
            this.errors = this.errors.slice(-MAX_ERRORS);
        }
        this.dismissed = false;
        this._render();
        void _enrichError(normalized).then((changed) => {
            if (changed && this.errors.includes(normalized) && !this.dismissed) {
                this._render();
            }
        });
    }

    clearErrors() {
        this.errors = [];
        this.dismissed = false;
        this._hide();
    }

    getErrors() {
        return this.errors.slice();
    }

    isVisible() {
        return this.overlayEl !== null && this.overlayEl.style.display !== 'none';
    }

    _render() {
        if (!this.errors.length || this.dismissed) {
            this._hide();
            return;
        }
        this._ensureOverlay();
        this.overlayEl.innerHTML = _buildHTML(this.errors);
        this.overlayEl.style.display = 'block';
        const btn = this.overlayEl.querySelector('#__sprag_overlay_dismiss__');
        if (btn) {
            btn.onclick = () => {
                this.dismissed = true;
                this._hide();
            };
        }
    }

    _hide() {
        if (this.overlayEl) {
            this.overlayEl.style.display = 'none';
        }
    }

    _ensureOverlay() {
        if (this.overlayEl && document.body.contains(this.overlayEl)) {
            return;
        }
        this.overlayEl = createElement('div');
        attr(this.overlayEl, {
            id: OVERLAY_ID,
            style: [
                'position: fixed',
                'inset: 0',
                'z-index: 2147483647',
                'background: rgba(0, 0, 0, 0.75)',
                'overflow-y: auto',
                'font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                'font-size: 13px',
                'color: #e8e8e8',
                'padding: 0',
                'margin: 0',
            ].join('; '),
        });
        append(document.body, this.overlayEl);
    }
}

// ---------------------------------------------------------------------------
// Dev-mode detection
// ---------------------------------------------------------------------------

function _isDevMode() {
    if (typeof window === 'undefined') {
        return false;
    }
    const payload = window.__SPRAG_PAYLOAD__;
    if (payload) {
        const page = payload.page || payload.mount || null;
        if (page && page.dev_reload) {
            return true;
        }
    }
    return false;
}

// ---------------------------------------------------------------------------
// Source-map enrichment
// ---------------------------------------------------------------------------

async function _enrichError(entry) {
    if (!entry || !entry.stack || entry.mappedStack) {
        return false;
    }
    const mapped = await _mapStack(entry.stack);
    if (!mapped.frames.length) {
        return false;
    }
    entry.mappedFrames = mapped.frames;
    entry.mappedStack = mapped.stack;
    const firstFrame = mapped.frames.find((frame) => frame.mapped) || null;
    if (firstFrame && firstFrame.sourceLineText) {
        entry.sourceLineText = firstFrame.sourceLineText;
    }
    return true;
}

async function _mapStack(stack) {
    const lines = String(stack || '').split('\n');
    const mappedFrames = [];
    const mappedLines = [];

    for (const line of lines) {
        const frame = _parseStackFrame(line);
        if (!frame) {
            mappedLines.push(line);
            continue;
        }

        const mapped = await _mapFrame(frame);
        if (!mapped) {
            mappedLines.push(line);
            continue;
        }

        mappedFrames.push(mapped);
        mappedLines.push(_formatMappedStackLine(mapped));
    }

    return {
        frames: mappedFrames,
        stack: mappedLines.join('\n'),
    };
}

function _parseStackFrame(line) {
    const chrome = String(line || '').match(STACK_FRAME_RE);
    if (chrome) {
        return {
            raw: line,
            prefix: line.match(/^\s*/)[0] || '',
            functionName: chrome[1] || '',
            url: chrome[2],
            line: Number(chrome[3]),
            column: Number(chrome[4]),
            style: 'chrome',
        };
    }

    const safari = String(line || '').match(SAFARI_STACK_FRAME_RE);
    if (safari) {
        return {
            raw: line,
            prefix: line.match(/^\s*/)[0] || '',
            functionName: safari[1] || '',
            url: safari[2],
            line: Number(safari[3]),
            column: Number(safari[4]),
            style: 'safari',
        };
    }

    return null;
}

async function _mapFrame(frame) {
    const sourceMap = await _loadSourceMap(frame.url);
    if (!sourceMap) {
        return null;
    }
    const original = _lookupOriginalLocation(sourceMap, frame.line);
    if (!original) {
        return null;
    }

    const method = _methodForGeneratedLine(sourceMap, frame.line);
    const functionName = original.name || method || frame.functionName || '';
    return {
        ...frame,
        mapped: true,
        functionName,
        source: original.source,
        sourceLine: original.line,
        sourceColumn: original.column,
        sourceLineText: original.sourceLineText,
        method,
    };
}

async function _loadSourceMap(generatedUrl) {
    if (typeof fetch !== 'function') {
        return null;
    }

    const href = _absoluteURL(generatedUrl);
    if (!href) {
        return null;
    }
    if (_sourceMapCache.has(href)) {
        return _sourceMapCache.get(href);
    }

    const promise = _fetchSourceMap(href).catch(() => null);
    _sourceMapCache.set(href, promise);
    return promise;
}

async function _fetchSourceMap(generatedHref) {
    let mapHref = `${generatedHref}.map`;
    let response = await fetch(mapHref, { cache: 'no-store' });
    if (!response.ok) {
        const sourceResponse = await fetch(generatedHref, { cache: 'no-store' });
        if (!sourceResponse.ok) {
            return null;
        }
        const sourceText = await sourceResponse.text();
        const match = sourceText.match(/\/\/[#@]\s*sourceMappingURL=([^\s]+)/);
        if (!match) {
            return null;
        }
        mapHref = new URL(match[1], generatedHref).href;
        response = await fetch(mapHref, { cache: 'no-store' });
        if (!response.ok) {
            return null;
        }
    }

    const payload = await response.json();
    if (!payload || payload.version !== 3 || typeof payload.mappings !== 'string') {
        return null;
    }
    payload.__spragDecodedMappings = _decodeMappings(payload.mappings);
    return payload;
}

function _absoluteURL(value) {
    try {
        return new URL(value, window.location.href).href.split('#')[0];
    } catch (_error) {
        return null;
    }
}

function _lookupOriginalLocation(sourceMap, generatedLine) {
    const decoded = sourceMap.__spragDecodedMappings || [];
    const mapping = decoded[generatedLine - 1];
    if (!mapping) {
        return null;
    }
    const source = (sourceMap.sources || [])[mapping.sourceIndex] || '';
    const sourceContent = (sourceMap.sourcesContent || [])[mapping.sourceIndex] || '';
    const sourceLineText = sourceContent
        ? (sourceContent.split('\n')[mapping.sourceLine] || '').trim()
        : '';
    return {
        source,
        line: mapping.sourceLine + 1,
        column: mapping.sourceColumn + 1,
        name: mapping.nameIndex === null ? '' : (sourceMap.names || [])[mapping.nameIndex] || '',
        sourceLineText,
    };
}

function _methodForGeneratedLine(sourceMap, generatedLine) {
    const methods = sourceMap.x_sprag && Array.isArray(sourceMap.x_sprag.methods)
        ? sourceMap.x_sprag.methods
        : [];
    const method = methods.find((entry) => (
        generatedLine >= entry.generated_start_line
        && generatedLine <= entry.generated_end_line
    ));
    if (!method) {
        return '';
    }
    const className = sourceMap.x_sprag && sourceMap.x_sprag.class
        ? sourceMap.x_sprag.class
        : '';
    return className ? `${className}.${method.name}` : method.name;
}

function _decodeMappings(mappings) {
    const decoded = [];
    let previousSourceIndex = 0;
    let previousSourceLine = 0;
    let previousSourceColumn = 0;
    let previousNameIndex = 0;

    for (const line of String(mappings || '').split(';')) {
        if (!line) {
            decoded.push(null);
            continue;
        }
        const segment = line.split(',')[0];
        const fields = _decodeVlqSegment(segment);
        if (fields.length < 4) {
            decoded.push(null);
            continue;
        }
        previousSourceIndex += fields[1];
        previousSourceLine += fields[2];
        previousSourceColumn += fields[3];
        let nameIndex = null;
        if (fields.length >= 5) {
            previousNameIndex += fields[4];
            nameIndex = previousNameIndex;
        }
        decoded.push({
            sourceIndex: previousSourceIndex,
            sourceLine: previousSourceLine,
            sourceColumn: previousSourceColumn,
            nameIndex,
        });
    }

    return decoded;
}

function _decodeVlqSegment(segment) {
    const values = [];
    let value = 0;
    let shift = 0;

    for (const char of segment) {
        let digit = _BASE64_CHARS.indexOf(char);
        if (digit < 0) {
            continue;
        }
        const continuation = (digit & 32) !== 0;
        digit &= 31;
        value += digit << shift;
        if (continuation) {
            shift += 5;
            continue;
        }
        values.push(_fromVlqSigned(value));
        value = 0;
        shift = 0;
    }

    return values;
}

const _BASE64_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

function _fromVlqSigned(value) {
    const negative = (value & 1) === 1;
    const shifted = value >> 1;
    return negative ? -shifted : shifted;
}

function _formatMappedStackLine(frame) {
    const fn = frame.functionName ? `${frame.functionName} ` : '';
    return `${frame.prefix}at ${fn}(${frame.source}:${frame.sourceLine}:${frame.sourceColumn})`;
}

function _formatMappedSource(frame) {
    if (!frame) {
        return '';
    }
    const fn = frame.functionName ? ` in ${frame.functionName}` : '';
    return `${frame.source}:${frame.sourceLine}:${frame.sourceColumn}${fn}`;
}

export const __spragDevOverlayInternals = {
    decodeMappings: _decodeMappings,
    parseStackFrame: _parseStackFrame,
    lookupOriginalLocation: _lookupOriginalLocation,
    methodForGeneratedLine: _methodForGeneratedLine,
};

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function _buildHTML(errors) {
    const count = errors.length;
    const plural = count === 1 ? '' : 's';
    const latestKind = errors[errors.length - 1].kind;
    const kindLabel = latestKind === 'build' ? 'Build' : latestKind === 'boot' ? 'Boot' : 'Runtime';

    let html = `
<div style="max-width: 56rem; margin: 2rem auto; padding: 1.5rem;">
  <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
    <h1 style="margin: 0; font-size: 1.1rem; font-weight: 600; color: #ff6b6b;">
      SPRAG ${kindLabel} Error${plural} (${count})
    </h1>
    <button id="__sprag_overlay_dismiss__" style="
      background: transparent; border: 1px solid #555; border-radius: 4px;
      color: #aaa; padding: 4px 12px; cursor: pointer; font-size: 12px;
      font-family: inherit;
    ">Dismiss</button>
  </div>`;

    for (let i = errors.length - 1; i >= 0; i -= 1) {
        const err = errors[i];
        const time = new Date(err.timestamp).toLocaleTimeString();
        const kindBadge = err.kind === 'build'
            ? '<span style="background: #b35900; padding: 1px 6px; border-radius: 3px; font-size: 11px;">BUILD</span>'
            : err.kind === 'boot'
                ? '<span style="background: #7a1f1f; padding: 1px 6px; border-radius: 3px; font-size: 11px;">BOOT</span>'
                : err.kind === 'action'
                    ? '<span style="background: #1f3a7a; padding: 1px 6px; border-radius: 3px; font-size: 11px;">ACTION</span>'
                    : '<span style="background: #555; padding: 1px 6px; border-radius: 3px; font-size: 11px;">RUNTIME</span>';

        html += `
  <div style="background: #1a1a1a; border: 1px solid #333; border-radius: 8px; padding: 1rem; margin-bottom: 0.75rem;">
    <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 0.5rem;">
      ${kindBadge}
      <span style="color: #ff8a8a; font-weight: 600;">${_escapeHTML(err.title)}</span>
      <span style="color: #666; margin-left: auto; font-size: 11px;">${time}</span>
    </div>
    <pre style="margin: 0; white-space: pre-wrap; word-break: break-word; color: #ddd; line-height: 1.5;">${_escapeHTML(err.message)}</pre>`;

        const firstMappedFrame = err.mappedFrames && err.mappedFrames.length
            ? err.mappedFrames[0]
            : null;
        if (firstMappedFrame) {
            html += `
    <div style="margin-top: 0.85rem; padding: 0.75rem; border: 1px solid #365f45; border-radius: 6px; background: #102018;">
      <div style="color: #7ee0a3; font-size: 11px; letter-spacing: 0; text-transform: uppercase; margin-bottom: 0.35rem;">Source mapped location</div>
      <div style="color: #f0fff5; font-weight: 600; word-break: break-word;">${_escapeHTML(_formatMappedSource(firstMappedFrame))}</div>
      ${err.sourceLineText ? `<pre style="margin: 0.6rem 0 0; white-space: pre-wrap; word-break: break-word; color: #c8f5d7; font-size: 12px; line-height: 1.45;">${_escapeHTML(err.sourceLineText)}</pre>` : ''}
    </div>`;
        }

        if (err.source) {
            html += `
    <div style="margin-top: 0.5rem; color: #888; font-size: 12px;">Source: ${_escapeHTML(err.source)}</div>`;
        }
        if (err.mappedStack) {
            html += `
    <div style="margin-top: 0.75rem; color: #aaa; font-size: 12px;">Mapped stack</div>
    <pre style="margin: 0.35rem 0 0; white-space: pre-wrap; word-break: break-word; color: #b8d7ff; font-size: 12px; line-height: 1.45;">${_escapeHTML(err.mappedStack)}</pre>`;
        }
        if (err.stack) {
            html += `
    <details style="margin-top: 0.5rem;">
      <summary style="cursor: pointer; color: #888; font-size: 12px;">${err.mappedStack ? 'Raw stack trace' : 'Stack trace'}</summary>
      <pre style="margin: 0.5rem 0 0; white-space: pre-wrap; word-break: break-word; color: #999; font-size: 12px; line-height: 1.4;">${_escapeHTML(err.stack)}</pre>
    </details>`;
        }
        html += '\n  </div>';
    }

    html += `
  <div style="text-align: center; color: #555; font-size: 11px; margin-top: 0.5rem;">
    Press <kbd style="background: #333; padding: 1px 5px; border-radius: 3px;">Esc</kbd> to dismiss
  </div>
</div>`;

    return html;
}

function _escapeHTML(text) {
    if (!text) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
