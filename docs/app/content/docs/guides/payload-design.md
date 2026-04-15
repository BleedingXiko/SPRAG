---
title: Payload Design
description: What goes in load(), what stays on the server, and how to avoid bloating your pages.
order: 35
---

# Payload Design

Everything returned from `load()` becomes `window.__SPRAG_PAYLOAD__` — a JSON blob inlined into every page's HTML. Keeping it lean directly reduces page weight, time-to-first-byte, and the size of your build output.

## What gets serialized

SPRAG serializes the return value of `load()` using Python's standard JSON encoder. **Dataclasses, Pydantic models, and ORM objects serialize all their fields** — including ones you never intended to send to the browser.

A common example: returning a `ContentDocument` object directly.

```python
# Sends body (raw markdown), html (rendered), source_path (filesystem path),
# excerpt, metadata, path_parts, slug, url_path — whether you use them or not.
def load(self):
    return {"doc": find_doc(segments)}
```

If your sidebar loads the full document tree, that's every doc's content on every page:

```python
# 246 KB of docs content inlined on every single page.
def load(self):
    return {"sections": docs_by_section()}
```

## The fix: only send what the browser needs

Slim down objects before returning them. A sidebar only needs `title` and `url_path`. An article page only needs `title`, `description`, `html`, and `path_parts` — not the raw `body` or the server-side `source_path`.

```python
def slim_doc(doc):
    return {
        "title": doc.title,
        "description": doc.description,
        "html": doc.html,
        "url_path": doc.url_path,
        "slug": doc.slug,
        "path_parts": list(doc.path_parts),
        "metadata": doc.metadata,
    }

def slim_nav(sections):
    return [
        {
            "label": section["label"],
            "items": [{"title": doc.title, "url_path": doc.url_path} for doc in section["items"]],
        }
        for section in sections
    ]
```

Then in `load()`:

```python
def load(self):
    segments = tuple(self.request.params.get("segments") or [])
    doc = find_doc(segments)
    return {
        "doc": slim_doc(doc) if doc else None,
        "sections": slim_nav(docs_by_section()),
        "current_path": "/docs/" + "/".join(segments),
    }
```

On a real docs site this reduced per-page payload from **261 KB → 11 KB** and total build output from **9.6 MB → 1.5 MB** — an 84% reduction.

## Fields that should never leave the server

Some fields have no place in a browser payload regardless of size:

| Field | Why |
|---|---|
| `source_path` | Absolute filesystem path — leaks server directory structure |
| `body` | Raw markdown source — the rendered `html` is already there |
| Database connection objects | Will fail to serialize or expose internals |
| Secret keys, tokens | Will appear in page source |

## `document` mode pages

In `document` mode there is no JavaScript hydration — the payload is not consumed by the browser at all. Despite this, SPRAG still emits it (for consistency with hybrid mode and for potential client-side navigation). Keeping it lean still matters for page weight, but you can be confident none of it runs in the browser.

## General rules

- Send scalars and flat dicts where possible.
- Slice lists to what the page actually renders — `docs[:5]` not `docs_collection()`.
- When passing a collection for a sidebar or nav, strip it to `title` + `url_path`. The full content is not needed.
- Avoid passing the same data multiple times under different keys.
