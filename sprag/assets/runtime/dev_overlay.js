/**
 * SPRAG dev error overlay.
 *
 * Shows build errors, boot errors, and runtime exceptions in a dismissible
 * overlay panel so the developer never has to check the terminal or DevTools
 * console just to discover something broke.
 *
 * Only active when the surface payload includes `dev_reload: true` (i.e.,
 * `sprag dev` is running).
 */

const OVERLAY_ID = '__sprag_dev_overlay__';
const MAX_ERRORS = 50;

let _overlayEl = null;
let _errors = [];
let _dismissed = false;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/** Push an error into the overlay and (re-)render it. */
export function pushError(entry) {
    if (!_isDevMode()) {
        return;
    }
    _errors.push({
        timestamp: Date.now(),
        kind: entry.kind || 'runtime',
        title: entry.title || 'Error',
        message: entry.message || '',
        stack: entry.stack || '',
        source: entry.source || null,
    });
    if (_errors.length > MAX_ERRORS) {
        _errors = _errors.slice(-MAX_ERRORS);
    }
    _dismissed = false;
    _render();
}

/** Clear all errors and hide the overlay. */
export function clearErrors() {
    _errors = [];
    _dismissed = false;
    _hide();
}

/** Return a shallow copy of the current error list. */
export function getErrors() {
    return _errors.slice();
}

/** Whether the overlay is currently visible. */
export function isVisible() {
    return _overlayEl !== null && _overlayEl.style.display !== 'none';
}

// ---------------------------------------------------------------------------
// Error-boundary wrapper for component/module lifecycle
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
// Unhandled error / rejection catcher
// ---------------------------------------------------------------------------

let _globalListenersInstalled = false;

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
// Rendering
// ---------------------------------------------------------------------------

function _render() {
    if (!_errors.length || _dismissed) {
        _hide();
        return;
    }
    _ensureOverlay();
    _overlayEl.innerHTML = _buildHTML();
    _overlayEl.style.display = 'block';
    _attachListeners();
}

function _hide() {
    if (_overlayEl) {
        _overlayEl.style.display = 'none';
    }
}

function _ensureOverlay() {
    if (_overlayEl && document.body.contains(_overlayEl)) {
        return;
    }
    _overlayEl = document.createElement('div');
    _overlayEl.id = OVERLAY_ID;
    _overlayEl.setAttribute('style', [
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
    ].join('; '));
    document.body.appendChild(_overlayEl);
}

function _buildHTML() {
    const count = _errors.length;
    const plural = count === 1 ? '' : 's';
    const latestKind = _errors[_errors.length - 1].kind;
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

    for (let i = _errors.length - 1; i >= 0; i -= 1) {
        const err = _errors[i];
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

        if (err.source) {
            html += `
    <div style="margin-top: 0.5rem; color: #888; font-size: 12px;">Source: ${_escapeHTML(err.source)}</div>`;
        }
        if (err.stack) {
            html += `
    <details style="margin-top: 0.5rem;">
      <summary style="cursor: pointer; color: #888; font-size: 12px;">Stack trace</summary>
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

function _attachListeners() {
    const btn = _overlayEl && _overlayEl.querySelector('#__sprag_overlay_dismiss__');
    if (btn) {
        btn.onclick = () => {
            _dismissed = true;
            _hide();
        };
    }
    // Support Escape key to dismiss
    if (!_overlayEl._escHandler) {
        _overlayEl._escHandler = (event) => {
            if (event.key === 'Escape' && isVisible()) {
                _dismissed = true;
                _hide();
            }
        };
        document.addEventListener('keydown', _overlayEl._escHandler);
    }
}

function _escapeHTML(text) {
    if (!text) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
