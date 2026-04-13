import { bus } from '../vendor/ragot.esm.min.js';

function createSocketUrl(path, withSpragBase) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}${withSpragBase(path)}`;
}

export function createSurfaceSocketClient({ surface, withSpragBase }) {
    const socketPath = '/__sprag__/socket';
    const listeners = new Map();
    const outboundQueue = [];
    const joinedTopics = new Set();
    let ws = null;
    let reconnectTimer = null;
    let closed = false;

    function listenerSet(event) {
        let handlers = listeners.get(event);
        if (!handlers) {
            handlers = new Set();
            listeners.set(event, handlers);
        }
        return handlers;
    }

    function dispatch(event, payload) {
        const handlers = listeners.get(event);
        if (!handlers) {
            return;
        }
        for (const handler of Array.from(handlers)) {
            try {
                handler(payload);
            } catch (error) {
                console.warn(`[SPRAG] Socket handler for "${event}" failed.`, error);
            }
        }
    }

    function flushQueue() {
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            return;
        }
        while (outboundQueue.length > 0) {
            ws.send(outboundQueue.shift());
        }
    }

    function scheduleReconnect() {
        if (closed || reconnectTimer) {
            return;
        }
        reconnectTimer = window.setTimeout(() => {
            reconnectTimer = null;
            connect();
        }, 1000);
    }

    function normalizeTopic(topic) {
        if (topic === null || topic === undefined) {
            return null;
        }
        const raw = String(topic).trim();
        return raw || null;
    }

    function encodeTopicMessage(action, topic) {
        return JSON.stringify({
            type: 'topic',
            action,
            topic,
            route: (surface && surface.path) || '/',
        });
    }

    function connect() {
        if (closed) {
            return;
        }
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            return;
        }

        ws = new WebSocket(createSocketUrl(socketPath, withSpragBase));
        ws.onopen = () => {
            ws.send(JSON.stringify({
                type: 'hello',
                route: (surface && surface.path) || '/',
            }));
            for (const topic of Array.from(joinedTopics)) {
                ws.send(encodeTopicMessage('join', topic));
            }
            flushQueue();
            bus.emit('sprag:socket:open', { path: (surface && surface.path) || '/' });
        };
        ws.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                if (message && message.type === 'event' && message.event) {
                    dispatch(message.event, message.payload);
                    return;
                }
                if (message && message.type === 'error') {
                    dispatch('sprag:socket:error', message);
                    bus.emit('sprag:socket:error', message);
                    return;
                }
                if (message && message.type === 'ready') {
                    bus.emit('sprag:socket:ready', message);
                    return;
                }
                bus.emit('sprag:socket:message', message);
            } catch (_error) {
                bus.emit('sprag:socket:message', event.data);
            }
        };
        ws.onerror = () => {
            bus.emit('sprag:socket:error', {
                error: 'socket-error',
                path: (surface && surface.path) || '/',
            });
        };
        ws.onclose = () => {
            bus.emit('sprag:socket:close', { path: (surface && surface.path) || '/' });
            if (!closed) {
                scheduleReconnect();
            }
        };
    }

    const socket = {
        on(event, handler) {
            if (!event || typeof handler !== 'function') {
                return this;
            }
            listenerSet(event).add(handler);
            return this;
        },
        off(event, handler) {
            const handlers = listeners.get(event);
            if (!handlers) {
                return this;
            }
            handlers.delete(handler);
            if (handlers.size === 0) {
                listeners.delete(event);
            }
            return this;
        },
        emit(event, payload = null) {
            if (!event) {
                return false;
            }
            const encoded = JSON.stringify({
                type: 'emit',
                event,
                payload,
                route: (surface && surface.path) || '/',
            });
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(encoded);
                return true;
            }
            outboundQueue.push(encoded);
            connect();
            return false;
        },
        joinTopic(topic) {
            const normalized = normalizeTopic(topic);
            if (!normalized) {
                return false;
            }
            joinedTopics.add(normalized);
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(encodeTopicMessage('join', normalized));
                return true;
            }
            connect();
            return false;
        },
        leaveTopic(topic) {
            const normalized = normalizeTopic(topic);
            if (!normalized) {
                return false;
            }
            joinedTopics.delete(normalized);
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(encodeTopicMessage('leave', normalized));
                return true;
            }
            return false;
        },
        close() {
            closed = true;
            if (reconnectTimer) {
                window.clearTimeout(reconnectTimer);
                reconnectTimer = null;
            }
            listeners.clear();
            outboundQueue.length = 0;
            joinedTopics.clear();
            if (ws && ws.readyState !== WebSocket.CLOSED) {
                ws.close();
            }
        },
        connect() {
            connect();
            return this;
        },
    };
    socket.stop = socket.close;
    return socket;
}

export function createEventSourceBridge({ surface, withSpragBase }) {
    const endpoint = withSpragBase((surface && surface.events_endpoint) || '/__sprag__/events');
    const source = new EventSource(endpoint);
    source.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            const eventName = data.event || 'server:message';
            bus.emit(eventName, data.payload !== undefined ? data.payload : data);
        } catch (_error) {
            bus.emit('server:message', event.data);
        }
    };
    source.onerror = () => {
        bus.emit('server:connection:error');
    };
    source.stop = () => {
        source.close();
    };
    return source;
}
