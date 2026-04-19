---
title: Background Jobs
description: Processing long-running work with queues, progress tracking, and cancellation.
order: 42
---

# Background Jobs

Use background jobs for anything that takes more than ~200ms: file processing, email sends, external API calls, data imports.

## Quick start

### 1. Define a queue

```python
from sprag import QueueService

class ExportQueue(QueueService):
    name = "exports"
    worker_count = 2

    def handle_item(self, item):
        self.report_progress(current=1, total=3, message="Starting export...")

        data = fetch_data(item["query"])
        self.check_cancelled()

        self.report_progress(current=2, total=3, message="Writing CSV...")
        path = write_csv(data)

        return {"download_url": f"/downloads/{path}"}
```

### 2. Register with the app

```python
app = App(
    routes="app.routes",
    shell=app_shell,
    providers={"exports": ExportQueue()},
)
```

### 3. Enqueue from a controller

```python
@action(schema=Schema("start_export", {
    "query": Field(str, required=True),
}))
def start_export(self, query):
    result = self.enqueue("exports", {"query": query})
    return result
```

### 4. Track from the browser

```python
class ExportModule(Module):
    def on_start(self):
        self.on_socket("sprag:queue.job.changed", self._on_job)
        self.delegate(self.element, "click", "[data-role='export']", self.on_export)

    def on_export(self, event, target):
        event.prevent_default()
        self.call_action("start_export", {"query": self.state["query"]}).then(self._on_started)

    def _on_started(self, result):
        job = (result.value or {}).get("job") or {}
        self.set_state({"job_id": job.get("id")})

    def _on_job(self, data):
        if data.get("job_id") == self.state.get("job_id"):
            self.call_action("job_status", {"job_id": data["job_id"]}).then(self._on_status)

    def _on_status(self, result):
        self.set_state(result.value or {})
```

## Progress reporting

Inside `handle_item()`, call `report_progress()` at meaningful checkpoints:

```python
def handle_item(self, item):
    total = len(item["files"])
    for i, file in enumerate(item["files"]):
        self.check_cancelled()
        process_file(file)
        self.report_progress(
            current=i + 1,
            total=total,
            message=f"Processing {file['name']}..."
        )
    return {"processed": total}
```

Each `report_progress()` emits a `sprag:queue.job.changed` socket signal, so the browser can show live updates.

## Cancellation

### Browser side

```python
def on_cancel(self, event, target):
    event.prevent_default()
    self.call_action("cancel_job", {"job_id": self.state["job_id"]})
```

### Server side

```python
@action(schema=Schema("cancel_job", {"job_id": Field(str, required=True)}))
def cancel_job(self, job_id):
    return self.request_job_cancel("exports", job_id)
```

### Worker side

Call `self.check_cancelled()` at safe points in your processing loop. It raises an exception if cancellation was requested, cleanly stopping the job.

```python
def handle_item(self, item):
    for chunk in process_chunks(item):
        self.check_cancelled()  # Raises if cancelled
        # ... continue processing
```

## Failure handling

If `handle_item()` raises an unhandled exception, the job is automatically marked as failed. Use `self.fail_job(error=...)` for controlled failures:

```python
def handle_item(self, item):
    if not validate(item):
        self.fail_job(error="Invalid input data")
        return
    # ... process
```

## Deferred actions vs queues

For simple one-off async work, use `@action(defer=True)` instead of a full queue:

```python
@action(defer=True)
def send_welcome_email(self, email):
    send_email(email, template="welcome")
    return {"sent": True}
```

Use a `QueueService` when you need bounded concurrency, progress, cancellation, or job history. Use `@action(defer=True)` for fire-and-forget work.
