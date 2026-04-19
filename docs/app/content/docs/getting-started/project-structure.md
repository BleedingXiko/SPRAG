---
title: Project Structure
description: What each file in a SPRAG project does and when you need it.
order: 3
---

# Project Structure

A scaffolded SPRAG project looks like this:

```
myapp/
├── app/
│   ├── __init__.py          # App declaration
│   ├── routes/
│   │   └── counter/
│   │       ├── page.py      # Route manifest
│   │       ├── server.py    # Controller (server runtime)
│   │       ├── web.py       # Screen (SSR layout + hydration)
│   │       ├── components.py # Component classes (browser runtime)
│   │       └── modules.py   # Module classes (browser runtime)
│   ├── static/              # Served at /static/... (registered via AssetRegistry)
│   └── shell.html           # Outer HTML shell wrapping all pages
├── public/                  # Copied verbatim to build output root
├── requirements.txt
└── .env
```

## Route files

Each route lives in its own directory under `app/routes/`. The directory name becomes the URL path.

| File | Runtime | Purpose |
|---|---|---|
| `page.py` | Build | Route manifest — declares path, controller, screen, mode |
| `server.py` | Server | Controller class — `load()`, `@action`, HTTP/socket bindings |
| `web.py` | Server | Screen class — SSR render function, wires Component to Module |
| `components.py` | Browser | Component classes — own DOM subtrees, produce `ui.*` trees |
| `modules.py` | Browser | Module classes — own logic, events, sockets, server calls |

Not every file is required. A `document` mode route can still render fine without browser-owned Components/Modules. A simple page might not need a Module.

## Route modes

Set the mode in `page.py`:

- **`document`** — Server-first rendering. Use for content pages that don't need browser-owned Module logic.
- **`hybrid`** — SSR for the first paint, then the browser hydrates with JavaScript. The best of both worlds — fast initial load with full interactivity. This is the default.

For a browser-owned client app, use `mount(...)` under `app/mounts/` instead of a page route.

## The shell

`app/shell.html` wraps every page. It contains the outer HTML structure (header, nav, footer) with a `{{ sprag_slot }}` placeholder where the page content goes.

## Static assets

Put images, fonts, and other static files in `app/static/`. SPRAG discovers them automatically and serves them at `/static/...`:

```
app/
├── static/
│   └── images/
│       ├── favicon.ico
│       ├── logo.png
│       └── hero.jpg
```

Reference them in components, shell templates, or CSS:

```python
ui.img(src="/static/images/logo.png", alt="Logo")
```

```html
<!-- in shell.html -->
<img src="/static/images/logo.png" alt="Logo">
```

To add favicon and icon `<link>` tags to the document `<head>`, use the `icons` metadata key on `App` or on individual pages — see the [Routes](/docs/framework/routes) docs.

## The public folder

The `public/` directory at the project root is for files that should be copied verbatim to the build output root — `robots.txt`, `favicon.ico`, `sitemap.xml`, Open Graph images, etc. Unlike `app/static/`, these files are not registered with the asset pipeline; they land directly at the output root with no path prefix.

```
public/
├── robots.txt
├── favicon.ico
└── og-image.png
```

Both `sprag build` and `sprag build static` merge this folder into the output automatically.

## App declaration

`app/__init__.py` wires everything together:

```python
from sprag import App, shell

app_shell = shell(template="app/shell.html", css=["app/shell.css"])
app = App(routes="app.routes", shell=app_shell)
```

## CLI commands

| Command | What it does |
|---|---|
| `sprag dev` | Dev server with file watching and hot rebuild |
| `sprag build` | Build full dist bundle into `dist/` (server + assets) |
| `sprag build static` | Build a pure SSG static site into `dist/` (no server code) |
| `sprag pack` | Production optimization of `dist/` |
| `sprag routes` | List all discovered routes and actions |
| `sprag inspect /path` | Show compiled JS output for a route |
| `sprag doctor` | Structural diagnostics |
| `sprag add route <name>` | Scaffold a new route |
| `sprag add mount <name>` | Scaffold a new mount |
| `sprag add content <name>` | Scaffold a new content collection |
