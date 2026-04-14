---
title: Routes
description: File-based routing, route modes, dynamic segments, and the page manifest.
order: 11
---

# Routes

SPRAG uses file-based routing. Each directory under `app/routes/` becomes a URL path.

## File-based discovery

```
app/routes/
├── home/       → /
├── counter/    → /counter
├── about/      → /about
└── blog/
    └── [slug]/ → /blog/:slug
```

Route directories are discovered automatically. No manual registration.

## The page manifest

Every route needs a `page.py` that declares its shape:

```python
from sprag import page
from .server import MyController
from .web import MyScreen

my_page = page(
    path="/my-route",
    controller=MyController,
    screen=MyScreen,
    mode="hybrid",
)
```

### Parameters

| Parameter | Required | Description |
|---|---|---|
| `path` | Yes | URL path for this route |
| `controller` | Yes | Controller class that handles data and actions |
| `screen` | Yes | Screen class that renders the page |
| `mode` | No | `"document"`, `"hybrid"` (default), or `"spa"` |
| `shell` | No | Override the app-level shell for this route |
| `css` | No | Route-specific CSS files |
| `modules` | No | JS import aliases: `{"alias": "path/to/module.js"}` |
| `static_paths` | No | Function returning path params for static builds |
| `metadata` | No | Dict of metadata (title, description, etc.) |

## Route modes

- **`document`** — Pure SSR. The server renders HTML and sends it. No JavaScript is loaded. Best for content pages, marketing pages, and anything that doesn't need interactivity.

- **`hybrid`** — SSR first, then hydrate. The server renders the initial HTML for a fast first paint, then the browser loads JavaScript to make it interactive. This is the default and the right choice for most pages.

- **`spa`** — No SSR body. The server sends a shell, and the browser mounts everything. Use when the page content is entirely dynamic (dashboards, editors, etc.).

## Dynamic routes

Use brackets in directory names for dynamic segments:

- `[slug]` — matches a single path segment. Access via `self.request.params["slug"]`.
- `[...segments]` — catch-all, matches any number of segments. Access via `self.request.params["segments"]` (a list).

### Static path expansion

For static builds, dynamic routes need to know all possible values upfront. Provide a `static_paths` function:

```python
from .server import BlogController
from .web import BlogScreen
from app.content import blog_static_paths

blog_page = page(
    path="/blog/:slug",
    controller=BlogController,
    screen=BlogScreen,
    mode="document",
    static_paths=blog_static_paths,
)
```

The function returns a list of dicts mapping param names to values:

```python
def blog_static_paths():
    return [{"slug": "first-post"}, {"slug": "second-post"}]
```

## Scaffolding

```bash
# Add a new hybrid route
sprag add route dashboard --mode hybrid

# Add a document-mode route
sprag add route about --mode document
```

## Listing routes

```bash
sprag routes
```

This prints all discovered routes, their modes, and tags like `[socket]` for controllers that use the socket bridge.
