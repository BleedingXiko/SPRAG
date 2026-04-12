"""Shared SPRAG websocket bridge helpers.

This module owns the runtime socket bridge contract used by browser
``Module.on_socket(...)`` / ``Module.emit_socket(...)`` and server-side
controller ``build_events(handler)`` declarations.
"""

from __future__ import annotations

import ast
import inspect
import itertools
import json
import logging
from dataclasses import dataclass, field

from gevent.lock import BoundedSemaphore
from specter import Controller as SpecterController
from specter import SocketIngress, registry

from .request import Request
from .session import resolve_session_id
from .server import controller_context

logger = logging.getLogger(__name__)
_SOCKET_RUNTIME_METHODS = {
    "on_socket",
    "off_socket",
    "emit_socket",
    "join_topic",
    "leave_topic",
}
_SOCKET_RUNTIME_EVENT_PREFIX = "sprag:socket:"


def controller_uses_socket_bridge(controller_cls) -> bool:
    """Return ``True`` when a controller declares socket ingress."""
    if controller_cls is None:
        return False
    build_events = getattr(controller_cls, "build_events", None)
    base_build_events = getattr(SpecterController, "build_events", None)
    return build_events is not None and build_events is not base_build_events


def browser_class_uses_socket_runtime(browser_cls) -> bool:
    """Return ``True`` when a browser class touches SPRAG's socket runtime."""
    if browser_cls is None:
        return False
    try:
        tree = ast.parse(inspect.getsource(browser_cls))
    except (OSError, TypeError, SyntaxError):
        return False

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and node.attr in _SOCKET_RUNTIME_METHODS
        ):
            return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith(_SOCKET_RUNTIME_EVENT_PREFIX)
        ):
            return True
    return False


def surface_uses_socket_runtime(surface) -> bool:
    """Return ``True`` when a page or mount uses the browser socket runtime."""
    if surface is None:
        return False

    browser_classes = []
    module_cls = getattr(surface, "module", None)
    if module_cls is not None:
        browser_classes.append(module_cls)

    screen_cls = getattr(surface, "screen", None)
    if screen_cls is not None:
        browser_classes.extend(getattr(screen_cls, "modules", []) or [])

    return any(browser_class_uses_socket_runtime(browser_cls) for browser_cls in browser_classes)


def surface_socket_enabled(app=None, controller_cls=None, surface=None) -> bool:
    """Return ``True`` when the current surface should boot the socket client."""
    return controller_uses_socket_bridge(controller_cls) or surface_uses_socket_runtime(surface)


class SpragSocketIngress(SocketIngress):
    """Socket ingress that scopes controller request/app context per message."""

    def __init__(self, app):
        super().__init__(socketio=None, name="socket_ingress")
        self._sprag_app = app

    def dispatch_message(self, message: dict, *, connection=None):
        event_name = message.get("event")
        if not isinstance(event_name, str) or not event_name.strip():
            raise ValueError("SPRAG socket messages require a non-empty 'event'.")

        route = message.get("route") or getattr(connection, "route", None) or "/"
        headers = {}
        if connection is not None:
            headers["X-SPRAG-Socket-Id"] = connection.id
            headers["X-SPRAG-Session-Id"] = connection.session_id

        request = Request(
            path=route,
            headers=headers,
            method="SOCKET",
            body=json.dumps(message, sort_keys=True).encode("utf-8"),
            session_id=getattr(connection, "session_id", None),
        )
        with controller_context(request=request, app=self._sprag_app):
            return super().dispatch(event_name.strip(), message.get("payload"))

    def dispatch_default_error_message(self, exc: Exception, *, connection=None):
        route = getattr(connection, "route", None) or "/"
        headers = {}
        if connection is not None:
            headers["X-SPRAG-Socket-Id"] = connection.id
            headers["X-SPRAG-Session-Id"] = connection.session_id
        request = Request(
            path=route,
            headers=headers,
            method="SOCKET",
            session_id=getattr(connection, "session_id", None),
        )
        with controller_context(request=request, app=self._sprag_app):
            return super().dispatch_default_error(exc)


@dataclass
class SpragSocketConnection:
    """One live browser websocket client."""

    id: str
    websocket: object
    route: str = "/"
    session_id: str | None = None
    topics: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class SocketTarget:
    route: str | None = None
    client_id: str | None = None
    session_id: str | None = None
    topic: str | None = None


class SpragSocketBridge:
    """Process-local websocket bridge for SPRAG browser/runtime sockets."""

    registry_keys = ("socket_ingress", "socket_transport")

    def __init__(self, app):
        self.name = "socket_transport"
        self._app = app
        self._ingress = SpragSocketIngress(app)
        self._connections: dict[str, SpragSocketConnection] = {}
        self._next_id = itertools.count(1)
        self._lock = BoundedSemaphore(1)

    @property
    def ingress(self) -> SpragSocketIngress:
        return self._ingress

    def provide_registry(self):
        registry.provide("socket_ingress", self._ingress, owner=None, replace=True)
        registry.provide("socket_transport", self, owner=None, replace=True)

    def clear_registry(self):
        for key in reversed(self.registry_keys):
            if registry.has(key):
                registry.unregister(key)

    def connect(self, websocket) -> SpragSocketConnection:
        session_id, _created = resolve_session_id(getattr(websocket, "environ", {}).get("HTTP_COOKIE"))
        connection = SpragSocketConnection(
            id=f"sprag-socket-{next(self._next_id)}",
            websocket=websocket,
            session_id=session_id,
        )
        with self._lock:
            self._connections[connection.id] = connection
        self._send(
            connection,
            {
                "type": "hello",
                "id": connection.id,
                "session_id": connection.session_id,
            },
        )
        return connection

    def disconnect(self, connection: SpragSocketConnection):
        with self._lock:
            self._connections.pop(connection.id, None)
        try:
            if getattr(connection.websocket, "closed", False):
                return
            connection.websocket.close()
        except Exception:
            logger.debug("[SPRAG] failed to close websocket %s", connection.id, exc_info=True)

    def handle_websocket(self, websocket):
        connection = self.connect(websocket)
        try:
            while not getattr(websocket, "closed", False):
                message = websocket.receive()
                if message is None:
                    break
                self.handle_message(connection, message)
        except Exception as exc:
            logger.debug("[SPRAG] websocket %s crashed: %s", connection.id, exc, exc_info=True)
            self._ingress.dispatch_default_error_message(exc, connection=connection)
        finally:
            self.disconnect(connection)

    def handle_message(self, connection: SpragSocketConnection, raw_message):
        if isinstance(raw_message, bytes):
            raw_message = raw_message.decode("utf-8", errors="replace")

        try:
            message = json.loads(raw_message)
        except Exception as exc:
            self._send_error(connection, f"Invalid SPRAG socket JSON: {exc}")
            return

        if not isinstance(message, dict):
            self._send_error(connection, "SPRAG socket messages must be JSON objects.")
            return

        message_type = message.get("type") or "emit"
        if message_type == "hello":
            route = message.get("route")
            if isinstance(route, str) and route.strip():
                connection.route = route
            self._send(
                connection,
                {
                    "type": "ready",
                    "id": connection.id,
                    "route": connection.route,
                    "session_id": connection.session_id,
                },
            )
            return
        if message_type == "ping":
            self._send(connection, {"type": "pong"})
            return
        if message_type == "topic":
            self._handle_topic_message(connection, message)
            return
        if message_type != "emit":
            self._send_error(connection, f"Unsupported SPRAG socket message type {message_type!r}.")
            return

        route = message.get("route")
        if isinstance(route, str) and route.strip():
            connection.route = route
        else:
            message["route"] = connection.route

        try:
            self._ingress.dispatch_message(message, connection=connection)
        except Exception as exc:
            logger.debug("[SPRAG] socket dispatch failed: %s", exc, exc_info=True)
            self._ingress.dispatch_default_error_message(exc, connection=connection)
            self._send_error(connection, f"{exc.__class__.__name__}: {exc}")

    def emit(self, event_name, payload=None, *, route=None, client_id=None, session_id=None, topic=None):
        """Emit a server event to connected SPRAG websocket clients."""
        if not isinstance(event_name, str) or not event_name.strip():
            raise TypeError("socket_transport.emit(event_name, ...) requires a non-empty string.")

        target = self._resolve_target(
            route=route,
            client_id=client_id,
            session_id=session_id,
            topic=topic,
        )
        envelope = {
            "type": "event",
            "event": event_name.strip(),
            "payload": payload,
        }

        delivered = False
        for connection in self._matching_connections(target):
            delivered = self._send(connection, envelope) or delivered
        return delivered

    def close(self):
        with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()
        for connection in connections:
            try:
                if not getattr(connection.websocket, "closed", False):
                    connection.websocket.close()
            except Exception:
                logger.debug("[SPRAG] failed closing websocket %s", connection.id, exc_info=True)
        self._ingress.clear()

    def _resolve_target(self, *, route=None, client_id=None, session_id=None, topic=None) -> SocketTarget:
        def _clean(value):
            if value is None:
                return None
            if not isinstance(value, str):
                raise TypeError("socket target filters must be strings when provided.")
            value = value.strip()
            return value or None

        return SocketTarget(
            route=_clean(route),
            client_id=_clean(client_id),
            session_id=_clean(session_id),
            topic=_clean(topic),
        )

    def _matching_connections(self, target: SocketTarget):
        with self._lock:
            connections = list(self._connections.values())
        for connection in connections:
            if target.client_id is not None and connection.id != target.client_id:
                continue
            if target.route is not None and connection.route != target.route:
                continue
            if target.session_id is not None and connection.session_id != target.session_id:
                continue
            if target.topic is not None and target.topic not in connection.topics:
                continue
            yield connection

    def _handle_topic_message(self, connection: SpragSocketConnection, message: dict):
        action = message.get("action")
        topic = message.get("topic")
        if not isinstance(topic, str) or not topic.strip():
            self._send_error(connection, "SPRAG socket topic messages require a non-empty 'topic'.")
            return
        topic = topic.strip()
        if action == "join":
            connection.topics.add(topic)
        elif action == "leave":
            connection.topics.discard(topic)
        else:
            self._send_error(connection, f"Unsupported SPRAG socket topic action {action!r}.")
            return
        self._send(
            connection,
            {
                "type": "topic",
                "action": action,
                "topic": topic,
            },
        )

    def _send(self, connection: SpragSocketConnection, envelope: dict) -> bool:
        try:
            connection.websocket.send(json.dumps(envelope, sort_keys=True))
            return True
        except Exception:
            logger.debug("[SPRAG] failed sending websocket event to %s", connection.id, exc_info=True)
            self.disconnect(connection)
            return False

    def _send_error(self, connection: SpragSocketConnection, message: str):
        self._send(
            connection,
            {
                "type": "error",
                "error": message,
            },
        )
