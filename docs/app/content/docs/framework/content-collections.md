---
title: Content Collections
description: Scaffold markdown-backed route trees with automatic static path expansion.
order: 11.5
---

# Content Collections

Content collections are SPRAG's markdown-backed route scaffold. They create a route tree, a matching `app/content/<name>/` directory, and the `static_paths` wiring needed for static builds.

## What you get

Run:

```bash
sprag add content posts
```

SPRAG scaffolds:

```text
app/content/posts/
  getting-started.md
app/content_support.py
app/routes/posts/
  page.py
  server.py
  web.py
  [...segments]/
    page.py
    server.py
    web.py
```

- `app/content/posts/` holds your markdown files.
- `app/routes/posts/page.py` serves the collection index at `/posts`.
- `app/routes/posts/[...segments]/page.py` serves individual markdown documents.
- `app/content_support.py` adds reusable helpers for loading markdown, looking up a document, and generating `static_paths`.

## When to use this vs a hand-rolled dynamic route

Use a content collection when:

- your route tree is backed by markdown or filesystem content
- you want static path expansion generated from those files
- you want a quick docs/blog/guides scaffold without writing the route plumbing yourself

Use a hand-rolled `[slug]` or `[...segments]` route when:

- the route reads from a live API or database
- path discovery depends on runtime data rather than files in the repo
- you need custom loading rules that do not map cleanly to a markdown tree

## What `sprag add content` wires for you

The generated article route uses `content_static_paths()` automatically:

```python
from app.content_support import content_static_paths

def posts_static_paths():
    return content_static_paths("posts", base_url="/posts")
```

That helper walks `app/content/posts/`, turns each markdown document into a URL path, and feeds those params into the catch-all route's `static_paths=...`.

The generated article controller also exposes a ready-to-render payload:

- `doc` for the current markdown document
- `docs` for the full collection
- `route_path` and `current_path` for navigation/UI
- `collection_path` so the scaffold can point you to the source folder

## End-to-end flow

1. Scaffold the collection:

```bash
sprag add content posts
```

2. Add a markdown file such as `app/content/posts/hello-world.md`.

3. Confirm the routes:

```bash
sprag routes
```

You should see `/posts` and `/posts/[...segments]`.

4. Build the static site:

```bash
sprag build static
```

SPRAG expands the collection through `static_paths` and emits the corresponding HTML files under `dist/`.

## Authoring notes

- Collection names may be nested, such as `docs/guides`, which scaffolds both nested routes and nested content folders.
- The scaffold creates document-mode pages because markdown content is usually server-rendered first.
- `app/content_support.py` is created once; later `sprag add content ...` runs reuse it.

If you already know you need a custom controller contract or live-service-backed routing, start from [Routes](/docs/framework/routes) instead.
