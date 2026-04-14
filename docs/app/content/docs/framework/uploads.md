---
title: Uploads
description: File uploads from the browser — form-based, programmatic, chunked, and progress tracking.
order: 15
---

# Uploads

SPRAG provides two upload methods in the browser Module, both with progress tracking and automatic chunked upload for large files.

## Form-based upload

Use `upload_form()` when you have a standard file input in a form:

```python
class UploadModule(Module):
    def on_start(self):
        self.delegate(self.element, "submit", "form", self.on_submit)

    def on_submit(self, event, target):
        event.prevent_default()
        self.upload_form("avatar", event, self.on_progress)

    def on_progress(self, progress):
        self.set_state({
            "percent": progress.percent,
            "phase": progress.phase,
        })
```

The first argument (`"avatar"`) is the action name on the server. The `event` is the form submit event — SPRAG extracts the file inputs automatically.

## Programmatic upload

Use `upload()` for drag-and-drop or any case where you have a File object directly:

```python
def on_drop(self, event, target):
    event.prevent_default()
    file = event.dataTransfer.files[0]
    self.upload("process_image", file, {"resize": True}, self.on_progress)
```

The signature is `upload(action_name, file, payload_dict, on_progress)`.

## Progress object

Both methods call your progress callback with an object containing:

| Property | Description |
|---|---|
| `percent` | Upload progress 0–100 |
| `loaded` | Bytes uploaded so far |
| `total` | Total file size in bytes |
| `phase` | `"uploading"`, `"processing"`, or `"complete"` |
| `file_name` | Original filename |

## Server side

Handle uploaded files in your controller action:

```python
@action()
def avatar(self):
    file = self.request.file("file")
    file.save(f"uploads/{file.filename}")
    return {"avatar_url": f"/uploads/{file.filename}"}
```

### UploadedFile

The `file()` method returns an `UploadedFile` with:

| Property/Method | Description |
|---|---|
| `filename` | Original filename |
| `size` | File size in bytes |
| `content_type` | MIME type |
| `text()` | Read file content as text |
| `save(path)` | Save to disk |

For multiple files, use `self.request.files_list("files")` to get a list.

## Chunked uploads

Files above 2MB are automatically chunked — split into pieces, uploaded in sequence, and reassembled on the server. This is transparent to both the browser author and the server action. The progress callback still reports overall progress.

## Post-upload processing

For expensive processing after upload (image resizing, video transcoding), use a deferred action or queue:

```python
@action(defer=True)
def process_image(self, **data):
    file = self.request.file("file")
    resize = data.get("resize", False)
    # Runs in background — browser gets immediate acknowledgment
    result = process(file, resize=resize)
    return {"processed_url": result.url}
```

For bounded concurrent processing with progress, use a `QueueService` instead.
