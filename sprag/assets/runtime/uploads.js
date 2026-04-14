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

// ---------------------------------------------------------------------------
// Chunked upload helpers
// ---------------------------------------------------------------------------

let _cachedNegotiation = null;

function negotiateChunkConfig(baseEndpoint) {
    if (_cachedNegotiation) {
        return Promise.resolve(_cachedNegotiation);
    }
    return fetch(baseEndpoint + '/negotiate', {
        method: 'GET',
        headers: { 'Accept': 'application/json' },
    })
        .then(function (res) { return res.json(); })
        .then(function (data) {
            _cachedNegotiation = data;
            return data;
        });
}

function initUploadSession(baseEndpoint, spec) {
    return fetch(baseEndpoint + '/init', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify(spec),
    }).then(function (res) { return res.json(); });
}

function sendChunk(baseEndpoint, uploadId, fileIndex, chunkIndex, blob) {
    var body = new FormData();
    body.append('upload_id', uploadId);
    body.append('file_index', String(fileIndex));
    body.append('chunk_index', String(chunkIndex));
    body.append('chunk', blob);
    return fetch(baseEndpoint + '/chunk', {
        method: 'POST',
        headers: { 'Accept': 'application/json' },
        body: body,
    }).then(function (res) { return res.json(); });
}

function cancelUpload(baseEndpoint, uploadId) {
    return fetch(baseEndpoint + '/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({ upload_id: uploadId }),
    }).then(function (res) { return res.json(); });
}

function chunkedUpload(baseEndpoint, opts) {
    var route = opts.route;
    var action = opts.action;
    var payload = opts.payload || {};
    var files = opts.files; // Array of { name, file } where file is a File/Blob
    var onProgress = opts.onProgress || null;

    var fileSpecs = files.map(function (f) {
        return {
            name: f.name,
            filename: f.file.name || 'blob',
            content_type: f.file.type || null,
            size: f.file.size,
        };
    });

    return initUploadSession(baseEndpoint, {
        route: route,
        action: action,
        payload: payload,
        files: fileSpecs,
    }).then(function (initResult) {
        if (!initResult.ok) {
            var err = new Error(initResult.error || '[SPRAG] Failed to init chunked upload.');
            err.response = initResult;
            throw err;
        }

        var uploadId = initResult.upload_id;
        var chunkSize = initResult.chunk_size;
        var chunksExpected = initResult.chunks_expected;
        var chunksSent = 0;

        // Build ordered list of all chunks to send.
        var queue = [];
        for (var fi = 0; fi < files.length; fi++) {
            var file = files[fi].file;
            var fileChunks = Math.max(1, Math.ceil(file.size / chunkSize));
            for (var ci = 0; ci < fileChunks; ci++) {
                var start = ci * chunkSize;
                var end = Math.min(start + chunkSize, file.size);
                queue.push({ fileIndex: fi, chunkIndex: ci, blob: file.slice(start, end) });
            }
        }

        // Send chunks sequentially.
        function sendNext(idx) {
            if (idx >= queue.length) {
                // Should not happen — last chunk auto-finalizes.
                return Promise.reject(new Error('[SPRAG] All chunks sent but no finalization received.'));
            }
            var item = queue[idx];
            return sendChunk(baseEndpoint, uploadId, item.fileIndex, item.chunkIndex, item.blob).then(function (result) {
                chunksSent++;
                if (typeof onProgress === 'function') {
                    var totalBytes = 0;
                    var sentBytes = 0;
                    for (var i = 0; i < files.length; i++) { totalBytes += files[i].file.size; }
                    for (var j = 0; j <= idx; j++) { sentBytes += queue[j].blob.size; }
                    onProgress({
                        loaded: sentBytes,
                        total: totalBytes,
                        percent: totalBytes > 0 ? Math.min(100, Math.round((sentBytes / totalBytes) * 100)) : 0,
                        phase: result.finalized ? 'finalizing' : 'uploading',
                        file_name: files[item.fileIndex].file.name || 'blob',
                    });
                }
                if (result.finalized) {
                    return result;
                }
                if (!result.ok) {
                    var chunkErr = new Error(result.error || '[SPRAG] Chunk upload failed.');
                    chunkErr.response = result;
                    throw chunkErr;
                }
                return sendNext(idx + 1);
            });
        }

        return sendNext(0);
    });
}

function extractFilesFromForm(form, chunkThreshold) {
    var largeFiles = [];
    var hasLarge = false;
    var inputs = form.querySelectorAll('input[type="file"]');
    for (var i = 0; i < inputs.length; i++) {
        var input = inputs[i];
        var fieldName = input.name || 'file';
        for (var j = 0; j < (input.files || []).length; j++) {
            var file = input.files[j];
            largeFiles.push({ name: fieldName, file: file });
            if (file.size >= chunkThreshold) {
                hasLarge = true;
            }
        }
    }
    return { files: largeFiles, hasLarge: hasLarge };
}

export function createUploadClient({ currentRoute, navigate, withSpragBase }) {
    const knownActions = new Set((currentRoute && currentRoute.actions) || []);
    const endpoint = withSpragBase((currentRoute && currentRoute.upload_endpoint) || '/__sprag__/uploads');
    const chunkedEndpoint = withSpragBase('/__sprag__/uploads');

    return {
        upload(name, file, payload, onProgress) {
            if (!name) {
                return Promise.reject(new Error('[SPRAG] Upload action name is required.'));
            }
            if (knownActions.size && !knownActions.has(name)) {
                return Promise.reject(
                    new Error(`[SPRAG] Unknown upload action "${name}" for route "${(currentRoute && currentRoute.path) || 'unknown'}".`),
                );
            }

            var route = (currentRoute && currentRoute.path) || '/';
            var files = [{ name: 'file', file: file }];

            return negotiateChunkConfig(chunkedEndpoint).then(function (config) {
                if (file.size < config.threshold) {
                    // Small file: use single-POST with FormData.
                    var body = new FormData();
                    body.append('__sprag_route', route);
                    body.append('__sprag_action', name);
                    body.append('__sprag_payload', JSON.stringify(payload || {}));
                    body.append('file', file);

                    return new Promise(function (resolve, reject) {
                        var xhr = new XMLHttpRequest();
                        xhr.open('POST', endpoint, true);
                        xhr.setRequestHeader('Accept', 'application/json');

                        xhr.upload.addEventListener('progress', function (event) {
                            if (typeof onProgress === 'function') {
                                onProgress({
                                    loaded: event.loaded || 0,
                                    total: event.total || 0,
                                    percent: event.total > 0 ? Math.min(100, Math.round((event.loaded / event.total) * 100)) : 0,
                                    phase: 'uploading',
                                    file_name: file.name || 'blob',
                                });
                            }
                        });

                        xhr.onerror = function () {
                            var error = new Error('[SPRAG] Upload could not reach server.');
                            error.status = 0;
                            error.response = { ok: false, code: 'SPRAG_SERVER_UNAVAILABLE', error: error.message };
                            reject(error);
                        };

                        xhr.onload = function () {
                            var result = null;
                            try { result = JSON.parse(xhr.responseText || '{}'); } catch (_e) {
                                result = { ok: false, error: '[SPRAG] Invalid JSON response.' };
                            }
                            if (xhr.status < 200 || xhr.status >= 300 || !result.ok) {
                                var err = new Error(result.error || '[SPRAG] Upload failed.');
                                err.status = xhr.status;
                                err.response = result;
                                reject(err);
                                return;
                            }
                            if (result.redirect && result.redirect.location) {
                                navigate(result.redirect.location, { replace: !!result.redirect.replace });
                            }
                            resolve(result);
                        };

                        xhr.send(body);
                    });
                }

                // Large file: chunked path.
                return chunkedUpload(chunkedEndpoint, {
                    route: route,
                    action: name,
                    payload: payload || {},
                    files: files,
                    onProgress: onProgress,
                });
            });
        },

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
            const route = (currentRoute && currentRoute.path) || '/';
            const formPayload = collectFormSnapshot(form, { errorOnFiles: false });

            // Check if any file exceeds chunked threshold — if so, use chunked path.
            const extracted = extractFilesFromForm(form, 0); // threshold=0 to collect all
            if (extracted.files.length > 0) {
                return negotiateChunkConfig(chunkedEndpoint).then(function (config) {
                    var hasLarge = false;
                    for (var i = 0; i < extracted.files.length; i++) {
                        if (extracted.files[i].file.size >= config.threshold) {
                            hasLarge = true;
                            break;
                        }
                    }
                    if (hasLarge) {
                        return chunkedUpload(chunkedEndpoint, {
                            route: route,
                            action: name,
                            payload: formPayload,
                            files: extracted.files,
                            onProgress: onProgress,
                        }).then(function (result) {
                            if (result.redirect && result.redirect.location) {
                                navigate(result.redirect.location, { replace: !!result.redirect.replace });
                            }
                            return result;
                        });
                    }
                    // Below threshold — fall through to single-POST.
                    return singlePostUpload(form, route, name, formPayload, onProgress);
                });
            }

            return singlePostUpload(form, route, name, formPayload, onProgress);
        },
    };

    function singlePostUpload(form, route, name, formPayload, onProgress) {
            const body = new FormData(form);
            body.append('__sprag_route', route);
            body.append('__sprag_action', name);
            body.append('__sprag_payload', JSON.stringify(formPayload));

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
    }
}
