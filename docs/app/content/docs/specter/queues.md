---
title: Queues
description: Background job processing with bounded concurrency, progress reporting, and cancellation.
order: 23
---

# Queues

`QueueService` processes background jobs with bounded concurrency, progress tracking, and cancellation support. Use it for anything that takes more than ~200ms — file processing, email sends, external API calls.

## Define a queue

```python
from sprag import QueueService

class ImageQueue(QueueService):
    name = "image_processing"
    worker_count = 4  # Process up to 4 jobs concurrently

    def handle_item(self, item):
        file_path = item["file_path"]
        resize_to = item.get("resize_to", (800, 600))

        self.report_progress(percent=10, message="Loading image...")
        image = load_image(file_path)

        self.check_cancelled()  # Raises if cancelled

        self.report_progress(percent=50, message="Resizing...")
        resized = resize(image, resize_to)

        self.report_progress(percent=90, message="Saving...")
        output_path = save(resized)

        self.complete_job(result={"output": output_path})
```

## Register with the app

```python
app = App(
    routes="app.routes",
    shell=app_shell,
    providers={
        "image_processing": ImageQueue(),
    },
)
```

## Enqueue from a controller

```python
@action(schema=Schema("upload_image", {
    "file_path": Field(str, required=True),
}))
def upload_image(self, file_path):
    result = self.enqueue("image_processing", {
        "file_path": file_path,
        "resize_to": (1200, 800),
    })
    return {"job_id": result["job_id"], "accepted": result["accepted"]}
```

## Progress and status

### From the server

```python
@action(schema=Schema("job_status", {"job_id": Field(str, required=True)}))
def job_status(self, job_id):
    status = self.job_status("image_processing", job_id)
    return {"job": status}
```

### From the browser

Poll via action calls, or subscribe to the automatic socket signal:

```python
class UploadModule(Module):
    def on_start(self):
        self.on_socket("sprag:queue.job.changed", self._on_job_update)

    def _on_job_update(self, data):
        if data.get("job_id") == self.state.get("job_id"):
            self.call_action("job_status", {"job_id": data["job_id"]})
```

The `sprag:queue.job.changed` signal fires automatically on every state transition (queued → running → complete/failed).

## Progress reporting

Inside `handle_item()`:

```python
self.report_progress(percent=50, message="Halfway there...")
```

This updates the job's status and emits the `sprag:queue.job.changed` socket signal, so the browser can show live progress.

## Cancellation

### Browser requests cancellation

```python
class UploadModule(Module):
    def on_cancel(self, event, target):
        self.call_action("cancel_job", {"job_id": self.state["job_id"]})
```

### Server handles the request

```python
@action(schema=Schema("cancel_job", {"job_id": Field(str, required=True)}))
def cancel_job(self, job_id):
    self.cancel_job("image_processing", job_id)
    return {"cancelled": True}
```

### Worker checks for cancellation

```python
def handle_item(self, item):
    for chunk in process_chunks(item):
        self.check_cancelled()  # Raises if cancel was requested
        self.report_progress(percent=chunk.percent)
    self.complete_job(result=chunk.result)
```

## Queues vs deferred actions

| | `@action(defer=True)` | `QueueService` |
|---|---|---|
| Concurrency | Unbounded greenlets | Bounded worker pool |
| Progress | No built-in reporting | `report_progress()` |
| Cancellation | Not supported | `check_cancelled()` |
| History | No | Job status tracking |
| Best for | One-off async work | Managed concurrent processing |
