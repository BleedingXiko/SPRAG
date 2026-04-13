import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import gevent

from sprag import Controller, Field, QueueService, Request, Schema, Screen, action, page
from sprag.runtime.http.wsgi import SpragWSGIApp
from sprag.runtime.server import registry


class ObservabilityScreen(Screen):
    def render(self, data):
        return None


class ObservabilityController(Controller):
    route = "/observability"

    @action(schema=Schema("ping", {"label": Field(str, required=True)}))
    def ping(self, label):
        return {"label": label.upper()}


class ObservabilityQueue(QueueService):
    def __init__(self):
        super().__init__("observability_queue", worker_count=1, poll_interval=0.01)

    def handle_item(self, item):
        gevent.sleep(0.02)
        return {"label": item["label"]}


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        page_obj = page(
            path="/observability",
            controller=ObservabilityController,
            screen=ObservabilityScreen,
            mode="hybrid",
        )
        self.fake_app = SimpleNamespace(
            pages=lambda: [("app.routes.observability.page", page_obj)],
            mounts=lambda: [],
        )
        self.tempdir = tempfile.TemporaryDirectory()
        self.wsgi = SpragWSGIApp(self.fake_app, Path(self.tempdir.name))
        self.queue = ObservabilityQueue().start()
        registry.provide("observability_queue", self.queue, replace=True)

    def tearDown(self):
        try:
            self.queue.stop()
        finally:
            registry.unregister("observability_queue")
            self.tempdir.cleanup()

    def test_action_requests_emit_structured_logs(self):
        body = json.dumps(
            {
                "route": "/observability",
                "action": "ping",
                "payload": {"label": "hello"},
            }
        ).encode("utf-8")
        environ = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/__sprag__/actions",
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": str(len(body)),
            "wsgi.input": io.BytesIO(body),
            "QUERY_STRING": "",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "8000",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "wsgi.url_scheme": "http",
        }
        started = {}

        def start_response(status, headers):
            started["status"] = status
            started["headers"] = headers

        with self.assertLogs("sprag.runtime", level="INFO") as captured:
            response = b"".join(self.wsgi(environ, start_response))

        payload = json.loads(response.decode("utf-8"))
        self.assertEqual(started["status"], "200 OK")
        self.assertTrue(payload["ok"])

        events = [json.loads(record.getMessage()) for record in captured.records]
        action_event = next(event for event in events if event["event"] == "request.action")
        self.assertEqual(action_event["route"], "/observability")
        self.assertEqual(action_event["action"], "ping")
        self.assertEqual(action_event["status"], 200)
        self.assertEqual(action_event["payload"]["keys"], ["label"])
        self.assertTrue(action_event["request_id"])

    def test_queue_lifecycle_emits_structured_logs(self):
        controller = ObservabilityController()
        controller.request = Request(path="/observability", session_id="session-123")

        with self.assertLogs("sprag.runtime", level="INFO") as captured:
            result = controller.enqueue(
                "observability_queue",
                {"label": "Job 1"},
                label="Job 1",
            )
            gevent.sleep(0.08)

        self.assertTrue(result["accepted"])
        events = [json.loads(record.getMessage()) for record in captured.records]
        event_names = [event["event"] for event in events]
        self.assertIn("queue.job.enqueued", event_names)
        self.assertIn("queue.job.started", event_names)
        self.assertIn("queue.job.completed", event_names)

        enqueue_event = next(event for event in events if event["event"] == "queue.job.enqueued")
        completed_event = next(event for event in events if event["event"] == "queue.job.completed")
        self.assertEqual(enqueue_event["queue"], "observability_queue")
        self.assertEqual(enqueue_event["session_id"], "session-123")
        self.assertEqual(completed_event["label"], "Job 1")
        self.assertGreaterEqual(completed_event["duration_ms"], 0)


if __name__ == "__main__":
    unittest.main()
