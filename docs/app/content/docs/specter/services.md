---
title: Services
description: Long-lived server-side processes with managed lifecycle, concurrency, and event subscriptions.
order: 21
---

# Services

A Service is a long-lived server-side object with a managed lifecycle. Services run background work, subscribe to events, and own child processes.

## Basic shape

```python
from sprag import Service

class MetricsService(Service):
    def on_start(self):
        self.interval(self._collect, 60)  # Every 60 seconds
        self.listen("order:completed", self._on_order)

    def on_stop(self):
        pass  # Cleanup is automatic for managed resources

    def _collect(self):
        stats = gather_metrics()
        self.emit("metrics:updated", stats)

    def _on_order(self, data):
        record_order(data)
```

## Lifecycle

| Method | When it runs |
|---|---|
| `on_start()` | After the service is initialized and the app is booted |
| `on_stop()` | During app shutdown, before process exit |

## Managed concurrency

These methods create resources that are automatically cleaned up on stop:

```python
# Periodic timer — calls fn every N seconds
self.interval(fn, seconds)

# One-shot timer — calls fn after N seconds
self.timeout(fn, seconds)

# Spawn a greenlet — managed cooperative thread
self.spawn(fn, *args)
```

## Events

The internal bus lets services communicate without direct coupling:

```python
# Listen for an event
self.listen("order:completed", self._on_order)

# Emit an event
self.emit("metrics:updated", {"cpu": 42.5})
```

Events are synchronous within the gevent event loop.

## Child ownership

Adopt child services to tie their lifecycle to yours:

```python
def on_start(self):
    worker = WorkerService()
    self.adopt(worker)  # worker.on_stop() called when parent stops
```

## Cleanup hooks

Register cleanup functions for resources you manage manually:

```python
def on_start(self):
    self.conn = open_connection()
    self.add_cleanup(lambda: self.conn.close())
```

## Providing to the app

Register services as providers so controllers can access them:

```python
from sprag import App

app = App(
    routes="app.routes",
    shell=app_shell,
    providers={
        "metrics": MetricsService(),
        "email": EmailService(api_key=os.environ["EMAIL_KEY"]),
    },
)
```

## Resolving from a controller

```python
class DashboardController(Controller):
    route = "/dashboard"

    def load(self):
        metrics = self.service("metrics")
        return {"stats": metrics.current_stats()}
```
