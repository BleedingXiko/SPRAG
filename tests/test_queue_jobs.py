import time
import unittest

import gevent

from sprag import Controller, QueueService, Request
from sprag.runtime.server import ActionDispatchError, registry


class DemoJobQueue(QueueService):
    def __init__(self):
        super().__init__("demo_job_queue", worker_count=1, poll_interval=0.01)

    def handle_item(self, item):
        label = item["label"]
        for step in range(3):
            gevent.sleep(0.02)
            self.check_cancelled(message="Cancelled " + label + ".")
            self.report_progress(
                current=step + 1,
                total=3,
                message=f"Working on {label} ({step + 1}/3)...",
            )
        return {"label": label, "finished_at": time.time()}


class DemoController(Controller):
    route = "/jobs"


class QueueJobTests(unittest.TestCase):
    def setUp(self):
        self.queue = DemoJobQueue().start()
        registry.provide("job_queue", self.queue, replace=True)
        self.controller = DemoController()
        self.controller.request = Request(path="/jobs", session_id="session-123")

    def tearDown(self):
        try:
            self.queue.stop()
        finally:
            registry.unregister("job_queue")

    def test_enqueue_tracks_progress_and_completion(self):
        result = self.controller.enqueue("job_queue", {"label": "Job 1"}, label="Job 1")
        self.assertTrue(result["accepted"])
        job = result["job"]
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["target"]["session_id"], "session-123")
        job_id = job["id"]

        gevent.sleep(0.12)

        snapshot = self.controller.job_status("job_queue", job_id)
        self.assertTrue(snapshot["accepted"])
        self.assertEqual(snapshot["job"]["status"], "completed")
        self.assertEqual(snapshot["job"]["progress"]["percent"], 100)
        self.assertEqual(snapshot["job"]["result"]["label"], "Job 1")
        self.assertEqual(snapshot["queue"]["active"], 0)

    def test_cancel_marks_job_cancelled(self):
        result = self.controller.enqueue("job_queue", {"label": "Job 2"}, label="Job 2")
        job_id = result["job"]["id"]

        cancel = self.controller.request_job_cancel("job_queue", job_id)
        self.assertTrue(cancel["accepted"])
        self.assertEqual(cancel["job"]["status"], "cancelling")

        gevent.sleep(0.08)

        snapshot = self.controller.job_status("job_queue", job_id)
        self.assertEqual(snapshot["job"]["status"], "cancelled")
        self.assertEqual(snapshot["queue"]["active"], 0)

    def test_missing_job_status_raises_404(self):
        with self.assertRaises(ActionDispatchError) as ctx:
            self.controller.job_status("job_queue", "missing-job")
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
