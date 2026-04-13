function appendFormValue(target, name, value) {
    if (Object.prototype.hasOwnProperty.call(target, name)) {
        if (Array.isArray(target[name])) {
            target[name].push(value);
        } else {
            target[name] = [target[name], value];
        }
        return;
    }
    target[name] = value;
}

function resolveFormElement(source) {
    if (!source) {
        throw new Error('[SPRAG] form_data(...) requires a form element or DOM event.');
    }
    if (source.tagName === 'FORM') {
        return source;
    }
    if (source.currentTarget && source.currentTarget.tagName === 'FORM') {
        return source.currentTarget;
    }
    const candidate = source.target || source.currentTarget || source;
    if (candidate && typeof candidate.closest === 'function') {
        const form = candidate.closest('form');
        if (form) {
            return form;
        }
    }
    throw new Error('[SPRAG] form_data(...) could not resolve a parent <form>.');
}

function collectFormSnapshot(form, options = {}) {
    const data = {};
    const checkboxCounts = new Map();
    const errorOnFiles = options.errorOnFiles !== false;

    for (const element of Array.from(form.elements || [])) {
        if (!element || !element.name || element.disabled) {
            continue;
        }
        const type = String(element.type || '').toLowerCase();
        if (type === 'checkbox') {
            checkboxCounts.set(element.name, (checkboxCounts.get(element.name) || 0) + 1);
        }
    }

    for (const element of Array.from(form.elements || [])) {
        if (!element || !element.name || element.disabled) {
            continue;
        }
        const name = element.name;
        const tagName = String(element.tagName || '').toUpperCase();
        const type = String(element.type || '').toLowerCase();

        if (type === 'file') {
            if (errorOnFiles && element.files && element.files.length > 0) {
                throw new Error(
                    '[SPRAG] form_data(...) does not support file inputs yet. Use the dedicated upload path.',
                );
            }
            continue;
        }

        if (type === 'submit' || type === 'button' || type === 'reset') {
            continue;
        }

        if (type === 'checkbox') {
            const isBoolean = (checkboxCounts.get(name) || 0) === 1
                && ((!element.hasAttribute || !element.hasAttribute('value')) || element.value === 'on');
            if (isBoolean) {
                data[name] = !!element.checked;
            } else if (element.checked) {
                appendFormValue(data, name, element.value);
            } else if (!Object.prototype.hasOwnProperty.call(data, name)) {
                data[name] = [];
            }
            continue;
        }

        if (type === 'radio') {
            if (element.checked) {
                data[name] = element.value;
            }
            continue;
        }

        if (tagName === 'SELECT' && element.multiple) {
            data[name] = Array.from(element.selectedOptions || []).map((option) => option.value);
            continue;
        }

        appendFormValue(data, name, element.value);
    }

    return data;
}

function uploadProgressPayload(event) {
    const loaded = Number((event && event.loaded) || 0);
    const total = Number((event && event.total) || 0);
    const percent = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0;
    return {
        loaded,
        total,
        percent,
        length_computable: !!(event && event.lengthComputable),
    };
}

export function formDataSprag(source) {
    const form = resolveFormElement(source);
    return collectFormSnapshot(form, { errorOnFiles: true });
}

export function createUploadClient({ currentRoute, navigate, withSpragBase }) {
    const knownActions = new Set((currentRoute && currentRoute.actions) || []);
    const endpoint = withSpragBase((currentRoute && currentRoute.upload_endpoint) || '/__sprag__/uploads');

    return {
        submit(name, source, onProgress = null) {
            if (!name) {
                return Promise.reject(new Error('[SPRAG] Upload action name is required.'));
            }
            if (knownActions.size && !knownActions.has(name)) {
                return Promise.reject(
                    new Error(`[SPRAG] Unknown upload action "${name}" for route "${(currentRoute && currentRoute.path) || 'unknown'}".`),
                );
            }

            const form = resolveFormElement(source);
            const body = new FormData(form);
            body.append('__sprag_route', (currentRoute && currentRoute.path) || '/');
            body.append('__sprag_action', name);
            body.append('__sprag_payload', JSON.stringify(collectFormSnapshot(form, { errorOnFiles: false })));

            return new Promise((resolve, reject) => {
                const xhr = new XMLHttpRequest();
                xhr.open('POST', endpoint, true);
                xhr.setRequestHeader('Accept', 'application/json');

                xhr.upload.addEventListener('progress', (event) => {
                    if (typeof onProgress === 'function') {
                        onProgress(uploadProgressPayload(event));
                    }
                });

                xhr.onerror = () => {
                    const message =
                        `[SPRAG] Upload "${name}" could not reach "${endpoint}". `
                        + 'This usually means you are viewing a static build and this example needs a live SPRAG server.';
                    const error = new Error(message);
                    error.status = 0;
                    error.response = {
                        ok: false,
                        code: 'SPRAG_SERVER_UNAVAILABLE',
                        error: message,
                    };
                    reject(error);
                };

                xhr.onload = () => {
                    const contentType = xhr.getResponseHeader('content-type') || '';
                    let result = null;
                    if (contentType.includes('application/json')) {
                        try {
                            result = JSON.parse(xhr.responseText || '{}');
                        } catch (_error) {
                            result = {
                                ok: false,
                                error: `[SPRAG] Upload "${name}" returned invalid JSON.`,
                            };
                        }
                    } else {
                        result = {
                            ok: false,
                            error: `[SPRAG] Expected JSON response for upload "${name}" but received status ${xhr.status}.`,
                        };
                    }

                    if (typeof onProgress === 'function') {
                        onProgress({
                            loaded: 0,
                            total: 0,
                            percent: xhr.status >= 200 && xhr.status < 300 ? 100 : 0,
                            length_computable: false,
                        });
                    }

                    if (xhr.status < 200 || xhr.status >= 300 || !result.ok) {
                        const error = new Error(result.error || `[SPRAG] Upload "${name}" failed.`);
                        error.status = xhr.status;
                        error.response = result;
                        reject(error);
                        return;
                    }

                    if (result.redirect && result.redirect.location) {
                        navigate(result.redirect.location, { replace: !!result.redirect.replace });
                    }

                    resolve(result);
                };

                xhr.send(body);
            });
        },
    };
}
