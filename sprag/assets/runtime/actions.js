export function createActionClient({ currentRoute, navigate, withSpragBase }) {
    const knownActions = new Set((currentRoute && currentRoute.actions) || []);
    const endpoint = withSpragBase((currentRoute && currentRoute.action_endpoint) || '/__sprag__/actions');

    return {
        async call(name, payload = {}) {
            if (!name) {
                throw new Error('[SPRAG] Action name is required.');
            }
            if (knownActions.size && !knownActions.has(name)) {
                throw new Error(`[SPRAG] Unknown action "${name}" for route "${(currentRoute && currentRoute.path) || 'unknown'}".`);
            }

            let response = null;
            try {
                response = await fetch(endpoint, {
                    method: 'POST',
                    headers: {
                        Accept: 'application/json',
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        route: (currentRoute && currentRoute.path) || '/',
                        action: name,
                        payload,
                    }),
                });
            } catch (_error) {
                const message =
                    `[SPRAG] Action "${name}" could not reach "${endpoint}". `
                    + 'This usually means you are viewing a static build and this example needs a live SPRAG server.';
                const error = new Error(message);
                error.status = 0;
                error.response = {
                    ok: false,
                    code: 'SPRAG_SERVER_UNAVAILABLE',
                    error: message,
                };
                throw error;
            }

            const contentType = response.headers.get('content-type') || '';
            const result = contentType.includes('application/json')
                ? await response.json()
                : {
                    ok: false,
                    error: `[SPRAG] Expected JSON response for action "${name}" but received status ${response.status}.`,
                };

            if (!response.ok || !result.ok) {
                const error = new Error(result.error || `[SPRAG] Action "${name}" failed.`);
                error.status = response.status;
                error.response = result;
                throw error;
            }

            if (result.redirect && result.redirect.location) {
                navigate(result.redirect.location, { replace: !!result.redirect.replace });
            }

            return result;
        },
    };
}

export function actionErrorMessageSprag(error, fallback = '') {
    const response = error && error.response && typeof error.response === 'object'
        ? error.response
        : null;
    const responseMessage = response && typeof response.error === 'string'
        ? response.error.trim()
        : '';
    if (responseMessage) {
        return responseMessage;
    }
    const directMessage = error && typeof error.message === 'string'
        ? error.message.trim()
        : '';
    if (directMessage) {
        return directMessage;
    }
    const fallbackMessage = fallback === null || fallback === undefined
        ? ''
        : String(fallback).trim();
    return fallbackMessage || '[SPRAG] Action failed.';
}
