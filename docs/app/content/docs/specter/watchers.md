---
title: Watchers & ManagedProcess
description: Polling and streaming observation loops with retry/backoff, plus supervised subprocesses.
order: 26
---

# Watchers & ManagedProcess

Specter provides two primitives for observing external state: `Watcher` for polling or stream-following loops, and `ManagedProcess` for supervised subprocesses.

## Watcher

A `Watcher` runs a background loop that either polls a function on an interval or iterates items from a stream source.

### Poll mode

Sample a value periodically:

```python
from sprag import Watcher

watcher = Watcher(
    "hdmi-status",
    poll=read_hdmi_status,
    interval=2.0,
    dedupe=True,
)
watcher.subscribe(lambda value, w: handle_status(value))
watcher.start()
```

With `dedupe=True`, subscribers are only notified when the polled value changes.

### Stream mode

Iterate items yielded by a stream source:

```python
watcher = Watcher(
    "log-tailer",
    stream=tail_log_file,
)
watcher.subscribe(lambda line, w: process_line(line))
watcher.start()
```

The `stream` callable should return an iterable (generator, file object, etc.).

### Retry and backoff

When the polling or streaming function raises:

- **`retry=True`** (default): the watcher retries with exponential backoff up to `max_backoff` seconds
- **`retry=False`**: the watcher exits after the first failure

```python
watcher = Watcher(
    "flaky-api",
    poll=check_api,
    interval=10.0,
    retry=True,
    max_backoff=120.0,
)
```

### Lifecycle

- `start()` and `stop()` are idempotent
- `stop()` signals the background loop, waits with a timeout, then kills if needed

### Ownership

Watchers should be owned by a service for automatic cleanup:

```python
class MonitorService(Service):
    def on_start(self):
        self.watcher = Watcher("disk", poll=check_disk, interval=30.0)
        self.own(self.watcher)
        self.watcher.start()
```

## ManagedProcess

`ManagedProcess` wraps `subprocess.Popen` with lifecycle management and stream watching.

```python
from subprocess import PIPE
from sprag import ManagedProcess

proc = ManagedProcess("cloudflared")
proc.start(
    ["cloudflared", "tunnel", "--url", "http://127.0.0.1:5000"],
    stdout=PIPE,
    stderr=PIPE,
)

proc.watch_stream("stdout", handle_stdout)
proc.watch_stream("stderr", handle_stderr)
```

### Stream watching

`watch_stream(name, callback)` spawns a managed greenlet that reads lines from the named stream (`"stdout"` or `"stderr"`) and passes each line to the callback. Readers are cleaned up on stop.

### Shutdown sequence

`stop()` follows a graceful shutdown sequence:

1. Send `SIGTERM`
2. Wait for the process to exit (with timeout)
3. If still running, send `SIGKILL`

### Ownership

Attach a process to a lifecycle owner for automatic cleanup:

```python
proc = ManagedProcess("tunnel")
proc.attach(self)  # process stops when owner stops
proc.start(["cloudflared", "tunnel", ...])
```
