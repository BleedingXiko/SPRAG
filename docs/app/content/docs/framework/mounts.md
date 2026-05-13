---
title: Mounts
description: Browser-owned client apps — when to use a mount instead of a page route.
order: 11.8
---

# Mounts

A mount is a browser-owned client app mounted at a server URL. Use it when the browser owns the entire surface from boot — a search overlay, an admin console, a rich editor — rather than a server-rendered page that hydrates.

## Page route vs mount

| | `page(...)` | `mount(...)` |
|---|---|---|
| First paint | Server-rendered HTML | Browser renders from boot |
| SSR | Yes | No |
| Interactive | After hydration | From boot |
| Use for | Most routes | Full client apps |

If you need a fast first paint or SSR for SEO, use a `page(...)` route. Use a mount when the browser owns everything from the start.

## Scaffold

```bash
sprag add mount search
```

This creates:

```
app/mounts/search/
├── mount.py       # Manifest
├── web.py         # Root Component
├── modules.py     # Root Module (optional)
└── server.py      # Boot controller (optional)
```

## Manifest

```python
# app/mounts/search/mount.py
from sprag import mount
from .web import SearchApp
from .modules import SearchModule
from .server import SearchBoot

search = mount(
    path="/search",
    component=SearchApp,
    module=SearchModule,
    boot=SearchBoot,
    metadata={"title": "Search"},
)
```

### Parameters

| Parameter | Required | Description |
|---|---|---|
| `path` | Yes | URL path for this mount |
| `component` | Yes | Root Component class |
| `module` | No | Root Module class for lifecycle logic |
| `boot` | No | Controller providing initial data via `load()` |
| `metadata` | No | Dict of metadata (title, description, etc.) |
| `shell` | No | Override the app-level shell |
| `css` | No | Mount-specific CSS files |
| `js` | No | Extra scripts to include |
| `modules` | No | JS import aliases: `{"alias": "path/to/module.js"}` |
| `providers` | No | Browser provider Modules |

## Boot controller

The `boot` controller's `load()` runs on the server and seeds initial state into the mount, just like a page controller. Use it to supply data the mount needs on first load.

```python
# app/mounts/search/server.py
from sprag import Controller

class SearchBoot(Controller):
    route = "/search"

    def load(self):
        return {"title": "Search the docs"}
```

The return dict becomes the Module's initial `self.state`. If you don't need server data, omit `boot` — the mount still works with an empty initial state.

## Root Component and Module

The root Component renders the mount's DOM. The root Module owns lifecycle: event delegation, server calls, state.

```python
# app/mounts/search/web.py
from sprag import Component, ui

class SearchApp(Component):
    def render(self, props=None):
        return ui.div(
            ui.input(
                type="text",
                placeholder="Search…",
                data_role="search-input",
                class_="search-input",
            ),
            ui.div(data_role="search-status", class_="search-status"),
            ui.ul(data_role="search-results", class_="search-results"),
            class_="search-app",
        )
```

```python
# app/mounts/search/modules.py
from sprag import Module, debounce

class SearchModule(Module):
    def on_start(self):
        self.delegate(self.element, "input", "[data-role='search-input']", self.on_input)

    @debounce(0.15)
    def on_input(self, event, target):
        self.call_action("search", {"query": target.value}).then(self._on_results)

    def _on_results(self, result):
        self.set_state(result.value or {})
```

## Providers

Pass browser-side provider Modules the same way as with `page(...)`:

```python
search = mount(
    path="/search",
    component=SearchApp,
    module=SearchModule,
    providers={"toast": ToastProvider},
)
```

Resolve them inside any Module with `self.provider("toast")`.
