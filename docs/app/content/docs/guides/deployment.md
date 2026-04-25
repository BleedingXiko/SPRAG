---
title: Deployment
description: Building, optimizing, and deploying SPRAG apps — static hosting, WSGI, and WebSocket modes.
order: 40
---

# Deployment

SPRAG supports multiple deployment targets: static file hosting, WSGI servers, and WebSocket servers.

## Build

### Full dist (server + assets)

```bash
sprag build
```

Emits a self-contained deployable bundle into `dist/`:

- `dist/public/` — static assets (HTML, CSS, JS, images)
- `dist/app/` — shipped application code
- `dist/sprag/` — shipped SPRAG runtime
- `dist/server.py` — runnable server entry point
- `dist/requirements.txt` — runtime Python dependencies

Any files in your project's `public/` folder are merged into `dist/public/` automatically — no manual copying needed.

### Static site (SSG)

```bash
sprag build static
```

Emits a pure static site into `dist/` — HTML, JS, CSS, and assets only. No Python server code is included. Use this for CDN, GitHub Pages, or Netlify deployments where you don't need server-side actions.

Your project's `public/` folder (if present) is merged into the output root alongside the generated pages.

## Production optimization

```bash
sprag pack
```

This post-processes `dist/` with:

| Optimization | Flag to skip |
|---|---|
| CSS/JS minification (terser, cleancss) | `--skip-minify` |
| Python bytecode compilation | `--skip-bytecode` |
| Image optimization (Pillow) | `--skip-images` |
| Pre-gzip compression | `--skip-gzip` |
| Content-hash fingerprinting | `--skip-fingerprint` |

If images are found and Pillow is not installed, `sprag pack` will prompt you to install it into your active venv before continuing.

If terser or cleancss are not installed, you'll be prompted to `npm install --save-dev` them locally (no global install).

Optional ZIP output:

```bash
sprag pack --zip
```

## Static hosting

Serve `dist/` (from `sprag build static`) or `dist/public/` (from `sprag build`) from any static host:

- **Netlify** — build command: `sprag build static`, publish directory: `dist`
- **GitHub Pages** — push `dist/` (static build) to the `gh-pages` branch
- **S3 + CloudFront** — upload `dist/` (static build), set index document to `index.html`
- **Any CDN** — just serve the files

No server process needed. Every page is pre-rendered HTML.

### Path-prefixed hosts and base URLs

If your static site is served below a path prefix, such as `https://user.github.io/project/`, keep links in your app root-relative:

```python
from sprag import join_url

DOCS_BASE_URL = join_url("/", "docs")
ui.a("Install", href=join_url(DOCS_BASE_URL, "getting-started", "installation"))
```

Two systems handle path-prefixed hosting for you:

1. **Static build (HTML).** `sprag build static` rewrites rendered internal `href`, `src`, and `action` attributes on every generated page to relative URLs. `/docs` and `/static/images/logo.png` resolve correctly from any page depth.
2. **Browser runtime (JS).** At boot, SPRAG derives the deployment prefix from `window.location.pathname` and exposes it as `window.__SPRAG_BASE__`. `join_url()` in compiled browser code prepends it automatically, so dynamically rendered links (search results, hydrated state changes, programmatic `navigate(...)` calls) all point at the correct URL.

Use `base_url` on content collections for the **app route prefix** (`/docs`, `/blog`, etc.), not the deployment host prefix. The deployment prefix is the runtime's job.

`join_url()` is the same import on both sides — call it from server code, from `Component.render()`, from `Module` methods. The Python implementation composes the path; the browser implementation composes and prefixes.

## Server hosting

For apps that need server-side actions or dynamic data, ship the output of `sprag build` and run the generated server entrypoint:

```bash
python3 server.py --port 8000
```

`sprag build` writes that runnable entrypoint to `dist/server.py` alongside `dist/public/`, `dist/app/`, and `dist/sprag/`.

## WebSocket mode

For real-time features, enable WebSocket mode:

```python
app = App(
    routes="app.routes",
    server_mode="websocket",
)
```

This starts a Socket.IO server alongside the WSGI app. The browser loads the Socket.IO client automatically.

## Environment variables

- **`SPRAG_PUBLIC_*`** — inlined into the browser bundle at build time. Use for public API keys, feature flags, etc.
- **Server vars** — loaded from `.env` at runtime. Use for secrets, database URLs, etc.

```bash
# .env
SPRAG_PUBLIC_API_URL=https://api.example.com
DATABASE_URL=postgres://...
SECRET_KEY=...
```

Only `SPRAG_PUBLIC_*` vars are visible to the browser. Everything else stays server-side.
