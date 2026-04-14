---
title: Deployment
description: Building, optimizing, and deploying SPRAG apps — static hosting, WSGI, and WebSocket modes.
order: 40
---

# Deployment

SPRAG supports multiple deployment targets: static file hosting, WSGI servers, and WebSocket servers.

## Build

```bash
sprag build
```

This emits the full site into `dist/`:

- `dist/public/` — static assets (HTML, CSS, JS, images)
- `dist/generated/` — compiled components, modules, stores
- `dist/vendor/` — Ragot runtime
- `dist/runtime/` — SPRAG bridge files
- `dist/manifest.json` — route/mount/asset manifest

## Production optimization

```bash
sprag pack
```

This post-processes `dist/` with:

| Optimization | Flag to skip |
|---|---|
| CSS/JS minification (terser, cleancss) | `--skip-minify` |
| Python bytecode compilation | `--skip-bytecode` |
| Image optimization | `--skip-images` |
| Pre-gzip compression | (always runs) |
| Content-hash fingerprinting | (always runs) |

Optional ZIP output:

```bash
sprag pack --zip
```

## Static hosting

For document-mode and hybrid-mode sites, serve `dist/public/` from any static host:

- **Netlify** — set build command to `sprag build && sprag pack`, publish directory to `dist/public/`
- **GitHub Pages** — push `dist/public/` to the `gh-pages` branch
- **S3 + CloudFront** — upload `dist/public/`, set index document to `index.html`
- **Any CDN** — just serve the files

No server process needed. Every page is pre-rendered HTML.

## WSGI hosting

For apps that need server-side actions, sockets, or dynamic data:

```python
# wsgi.py
from app import app

app.boot()
application = app.serve()
```

Run with Gunicorn and the gevent worker:

```bash
gunicorn wsgi:application -k gevent -w 4
```

## WebSocket mode

For real-time features, enable WebSocket mode:

```python
app = App(
    routes="app.routes",
    shell=app_shell,
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
