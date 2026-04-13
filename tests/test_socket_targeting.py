import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from sprag import Component, Controller, Module, Schema, Screen, action, mount, page, socket_target
from sprag.dev.codegen import build_browser_entry
from sprag.runtime.http.wsgi import SpragWSGIApp
from sprag.runtime.socket_bridge import SpragSocketBridge, surface_socket_enabled


class RealtimeScreen(Screen):
    def render(self, data):
        return None


class PlainModule(Module):
    def on_start(self):
        self.set_state({"ready": True})


class SocketAwareModule(Module):
    def on_start(self):
        self.on_socket("lab:ping", self.on_ping)

    def on_ping(self, payload=None):
        self.set_state({"last": payload})


class PlainScreen(Screen):
    modules = [PlainModule]

    def render(self, data):
        return None


class SocketAwareScreen(Screen):
    modules = [SocketAwareModule]

    def render(self, data):
        return None


class PassiveController(Controller):
    route = "/plain"

    def load(self):
        return {}


class MountRoot(Component):
    def render(self, props=None):
        return None


class RealtimeController(Controller):
    route = "/realtime"
    last_socket_session_id = None

    def load(self):
        return {"session_id": self.request.session_id}

    @action(schema=Schema("session", {}))
    def session(self):
        return {"session_id": self.request.session_id}

    def build_events(self, handler):
        handler.on("lab:session.probe", self.handle_probe)

    def handle_probe(self, payload=None):
        RealtimeController.last_socket_session_id = self.request.session_id


class FakeWebSocket:
    def __init__(self, cookie=""):
        self.environ = {"HTTP_COOKIE": cookie}
        self.sent = []
        self.closed = False

    def send(self, message):
        self.sent.append(json.loads(message))

    def close(self):
        self.closed = True


class RealtimeTargetingTests(unittest.TestCase):
    def setUp(self):
        realtime_page = page(
            path="/realtime",
            controller=RealtimeController,
            screen=RealtimeScreen,
            mode="document",
        )
        self.fake_app = SimpleNamespace(
            pages=lambda: [("app.routes.realtime.page", realtime_page)],
            mounts=lambda: [],
        )
        self.wsgi = SpragWSGIApp(self.fake_app, Path("."))

    def _wsgi_call(self, *, method, path, body=b"", content_type=None, cookie=None):
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "CONTENT_LENGTH": str(len(body)),
            "wsgi.input": io.BytesIO(body),
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "8000",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "wsgi.url_scheme": "http",
        }
        if content_type:
            environ["CONTENT_TYPE"] = content_type
        if cookie:
            environ["HTTP_COOKIE"] = cookie

        started = {}

        def start_response(status, headers):
            started["status"] = status
            started["headers"] = dict(headers)

        response = b"".join(self.wsgi(environ, start_response))
        return started["status"], started["headers"], response

    def test_page_and_action_requests_receive_session_cookie(self):
        status, headers, body = self._wsgi_call(method="GET", path="/realtime")
        self.assertEqual(status, "200 OK")
        self.assertIn("SPRAG_SID=", headers["Set-Cookie"])
        html = body.decode("utf-8")
        self.assertIn("SPRAG_SID", headers["Set-Cookie"])
        self.assertIn("window.__SPRAG_PAYLOAD__", html)

        action_body = json.dumps(
            {"route": "/realtime", "action": "session", "payload": {}}
        ).encode("utf-8")
        status, headers, body = self._wsgi_call(
            method="POST",
            path="/__sprag__/actions",
            body=action_body,
            content_type="application/json",
        )
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(status, "200 OK")
        self.assertTrue(payload["value"]["session_id"])
        self.assertIn("SPRAG_SID=", headers["Set-Cookie"])

    def test_existing_session_cookie_is_preserved_for_page_and_action(self):
        status, headers, body = self._wsgi_call(
            method="GET",
            path="/realtime",
            cookie="SPRAG_SID=known-page",
        )
        self.assertEqual(status, "200 OK")
        self.assertNotIn("Set-Cookie", headers)

        action_body = json.dumps(
            {"route": "/realtime", "action": "session", "payload": {}}
        ).encode("utf-8")
        status, headers, body = self._wsgi_call(
            method="POST",
            path="/__sprag__/actions",
            body=action_body,
            content_type="application/json",
            cookie="SPRAG_SID=known-action",
        )
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(status, "200 OK")
        self.assertEqual(payload["value"]["session_id"], "known-action")
        self.assertNotIn("Set-Cookie", headers)

    def test_socket_bridge_matches_session_client_and_topic_filters(self):
        bridge = SpragSocketBridge(SimpleNamespace())
        ws1 = FakeWebSocket("SPRAG_SID=session-a")
        ws2 = FakeWebSocket("SPRAG_SID=session-a")
        ws3 = FakeWebSocket("SPRAG_SID=session-b")
        conn1 = bridge.connect(ws1)
        conn2 = bridge.connect(ws2)
        conn3 = bridge.connect(ws3)

        for connection, ws in ((conn1, ws1), (conn2, ws2), (conn3, ws3)):
            bridge.handle_message(connection, json.dumps({"type": "hello", "route": "/realtime"}))
            ws.sent.clear()

        bridge.emit("lab:event", {"scope": "session"}, route="/realtime", session_id="session-a")
        self.assertEqual([message["payload"]["scope"] for message in ws1.sent], ["session"])
        self.assertEqual([message["payload"]["scope"] for message in ws2.sent], ["session"])
        self.assertEqual(ws3.sent, [])
        ws1.sent.clear()
        ws2.sent.clear()
        ws3.sent.clear()

        bridge.emit("lab:event", {"scope": "client"}, route="/realtime", client_id=conn2.id)
        self.assertEqual(ws1.sent, [])
        self.assertEqual([message["payload"]["scope"] for message in ws2.sent], ["client"])
        self.assertEqual(ws3.sent, [])
        ws2.sent.clear()

        bridge.handle_message(
            conn1,
            json.dumps({"type": "topic", "action": "join", "topic": "upload:alpha"}),
        )
        ws1.sent.clear()
        bridge.emit(
            "lab:event",
            {"scope": "topic"},
            route="/realtime",
            session_id="session-a",
            topic="upload:alpha",
        )
        self.assertEqual([message["payload"]["scope"] for message in ws1.sent], ["topic"])
        self.assertEqual(ws2.sent, [])
        self.assertEqual(ws3.sent, [])
        ws1.sent.clear()

        bridge.handle_message(
            conn1,
            json.dumps({"type": "topic", "action": "leave", "topic": "upload:alpha"}),
        )
        ws1.sent.clear()
        bridge.emit(
            "lab:event",
            {"scope": "after-leave"},
            route="/realtime",
            session_id="session-a",
            topic="upload:alpha",
        )
        self.assertEqual(ws1.sent, [])

        bridge.handle_message(
            conn1,
            json.dumps({"type": "topic", "action": "join", "topic": "upload:alpha"}),
        )
        ws1.sent.clear()
        bridge.disconnect(conn1)
        bridge.emit(
            "lab:event",
            {"scope": "after-disconnect"},
            route="/realtime",
            session_id="session-a",
            topic="upload:alpha",
        )
        self.assertEqual(ws1.sent, [])

    def test_emit_socket_accepts_reusable_target_mapping(self):
        bridge = SpragSocketBridge(SimpleNamespace())
        ws1 = FakeWebSocket("SPRAG_SID=session-a")
        ws2 = FakeWebSocket("SPRAG_SID=session-b")
        conn1 = bridge.connect(ws1)
        conn2 = bridge.connect(ws2)

        for connection, ws in ((conn1, ws1), (conn2, ws2)):
            bridge.handle_message(connection, json.dumps({"type": "hello", "route": "/realtime"}))
            ws.sent.clear()

        delivered = bridge.emit(
            "lab:event",
            {"scope": "target"},
            target=socket_target(route="/realtime", session_id="session-a"),
        )
        self.assertTrue(delivered)
        self.assertEqual([message["payload"]["scope"] for message in ws1.sent], ["target"])
        self.assertEqual(ws2.sent, [])

    def test_controller_emit_socket_refetch_uses_blessed_envelope(self):
        bridge = SpragSocketBridge(SimpleNamespace())
        bridge.provide_registry()
        ws1 = FakeWebSocket("SPRAG_SID=session-a")
        ws2 = FakeWebSocket("SPRAG_SID=session-b")
        conn1 = bridge.connect(ws1)
        conn2 = bridge.connect(ws2)
        controller = RealtimeController()
        try:
            for connection, ws in ((conn1, ws1), (conn2, ws2)):
                bridge.handle_message(connection, json.dumps({"type": "hello", "route": "/realtime"}))
                ws.sent.clear()

            delivered = controller.emit_socket_refetch(
                "session",
                {"origin": "test"},
                target=socket_target(route="/realtime", session_id="session-a"),
            )
            self.assertTrue(delivered)
            self.assertEqual(
                ws1.sent,
                [
                    {
                        "event": "sprag:refetch",
                        "payload": {"action": "session", "payload": {"origin": "test"}},
                        "type": "event",
                    }
                ],
            )
            self.assertEqual(ws2.sent, [])
        finally:
            bridge.clear_registry()

    def test_socket_ingress_request_exposes_session_id(self):
        bridge = SpragSocketBridge(SimpleNamespace())
        bridge.provide_registry()
        controller = RealtimeController()
        try:
            controller.build_handler(bridge)
            ws = FakeWebSocket("SPRAG_SID=socket-session")
            conn = bridge.connect(ws)
            bridge.handle_message(conn, json.dumps({"type": "hello", "route": "/realtime"}))
            bridge.handle_message(
                conn,
                json.dumps(
                    {
                        "type": "emit",
                        "event": "lab:session.probe",
                        "route": "/realtime",
                        "payload": {},
                    }
                ),
            )
            self.assertEqual(RealtimeController.last_socket_session_id, "socket-session")
        finally:
            bridge.clear_registry()

    def test_browser_entry_is_a_thin_composition_layer(self):
        browser_entry = build_browser_entry({"routes": [], "mounts": [], "errors": []})
        self.assertIn("import { startSurfaceBoot } from './runtime/boot.js';", browser_entry)
        self.assertIn("import './generated/stores.js';", browser_entry)
        self.assertIn("startSurfaceBoot({", browser_entry)
        self.assertNotIn("encodeTopicMessage('join'", browser_entry)
        self.assertNotIn("function createActionClient", browser_entry)

    def test_surface_socket_enabled_only_for_surfaces_that_use_socket_runtime(self):
        plain_page = page(
            path="/plain",
            controller=PassiveController,
            screen=PlainScreen,
            mode="hybrid",
        )
        socket_page = page(
            path="/socket-aware",
            controller=PassiveController,
            screen=SocketAwareScreen,
            mode="hybrid",
        )
        socket_mount = mount(
            "/socket-mount",
            component=MountRoot,
            module=SocketAwareModule,
        )

        self.assertFalse(
            surface_socket_enabled(
                SimpleNamespace(server_mode="websocket"),
                PassiveController,
                surface=plain_page,
            )
        )
        self.assertTrue(surface_socket_enabled(None, PassiveController, surface=socket_page))
        self.assertTrue(surface_socket_enabled(None, PassiveController, surface=socket_mount))
        self.assertTrue(surface_socket_enabled(None, RealtimeController, surface=plain_page))


if __name__ == "__main__":
    unittest.main()
