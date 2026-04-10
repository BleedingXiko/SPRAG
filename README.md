# SPRAG

One Python language, two runtimes.

SPRAG is a Python-first web framework for building server-rendered, hydrated, mounted, and realtime apps without splitting your app across Python and JavaScript. You write `Controller`s, `Component`s, `Module`s, stores, services, and route surfaces in Python. SPRAG keeps the server runtime as Python, compiles the browser-facing pieces to JavaScript at build time, and ships them together as one framework-owned app shape.

It is not a template language. It is not a Python wrapper around fetch calls. It is not “Python for the backend plus a separate frontend you still have to wire by hand.”

It is a full-stack framework with:

- file-discovered routes and mounts
- SSR-first pages with optional hydration
- typed server actions
- one `store(...)` API mirrored across server and browser
- plain HTML/CSS shells
- queues, watchers, SSE, and websocket flows
- static output for dynamic content routes
- a real `dist/` artifact you can run

> **Status: pre-alpha.** The shape is real and working. The API is not pinned yet.

## The Pitch

SPRAG has three core ideas:

1. **The server and browser should feel like one framework.** `set_state`, `listen`, `emit`, `subscribe`, `timeout`, `interval`, and `adopt` follow the same mental model across runtimes.
2. **SSR should be the default, not an afterthought.** Document routes are pure server HTML. Hybrid routes render first, then hydrate in place. Mounts get a boot document and a browser-owned app root.
3. **The framework should own the plumbing.** Actions, event bridges, route discovery, store hydration, shell composition, and deployable build output are all part of the runtime story.

## In One Route

This is the basic SPRAG loop: load on the server, render in Python, hydrate behavior in Python, call a typed action, update state.

```python
# app/routes/counter/server.py
from sprag import Controller, Field, Schema, action


class CounterController(Controller):
    route = "/counter"

    def load(self):
        return {"count": 0}

    @action(schema=Schema("increment", {"count": Field(int, required=True)}))
    def increment(self, count):
        return {"count": count + 1}
```

```python
# app/routes/counter/components.py
from sprag import Component, ui


class CounterCard(Component):
    def render(self, props=None):
        return ui.div(
            ui.div(str(self.state["count"]), class_="counter-display"),
            ui.button("Increment", type="button", data_role="increment"),
            class_="counter-card",
        )
```

```python
# app/routes/counter/modules.py
from sprag import Module


class CounterModule(Module):
    def __init__(self, screen=None, state=None):
        super().__init__(screen=screen, state=state or {"count": 0})

    def on_start(self):
        self.delegate(self.element, "click", "[data-role='increment']", self.on_click)

    def on_click(self, event):
        event.prevent_default()
        self.call_action("increment", {"count": self.state["count"]}).then(self.on_result)

    def on_result(self, result):
        self.set_state(result.value)
```

```python
# app/routes/counter/web.py
from sprag import Screen, hydrate

from .components import CounterCard
from .modules import CounterModule


class CounterScreen(Screen):
    modules = [CounterModule]

    def render(self, data):
        counter = self.module(CounterModule)
        return hydrate(CounterCard, module=counter)
```

```python
# app/routes/counter/page.py
from sprag import page

from .server import CounterController
from .web import CounterScreen


counter = page(
    path="/counter",
    controller=CounterController,
    screen=CounterScreen,
    mode="hybrid",
)
```

The browser module above is still Python. SPRAG compiles it into the client bundle, ships the route data, wires the action bridge, and hydrates the component in place.

## What Makes SPRAG Different

### Routes, mounts, and shells are first-class

SPRAG has two server-known surface types:

- `page(...)` for routes
- `mount(...)` for browser-owned app entries

Routes can be:

- `mode="document"` for pure SSR
- `mode="hybrid"` for SSR + hydration

Mounts are not a route mode. A mount returns a boot document and lets the browser own the root app after load.

The shared frame stays in plain HTML and CSS:

```python
# app/__init__.py
from sprag import App, shell


app = App(
    routes="app.routes",
    shell=shell(template="app/shell.html", css=["app/shell.css"]),
)
```

```html
<!-- app/shell.html -->
<div class="shell">
  <header class="nav">My App</header>
  <main>{{ sprag_slot }}</main>
</div>
```

For per-route or per-mount styling, use `css=[...]` on the surface itself.
Keep `shell=` for full shell overrides or shell composition.

```python
page(
    path="/counter",
    controller=CounterController,
    screen=CounterScreen,
    mode="hybrid",
    css=["app/routes/counter/counter.css"],
)
```

### One store API, mirrored across both runtimes

```python
# app/stores.py
from sprag import store


session = store(
    "session",
    initial={"user": {"name": "Ada"}, "prefs": {"theme": "dark"}},
)
```

```python
# same source on server or browser
session.set("user.name", "Grace")
session.patch({"prefs": {"theme": "light"}})
name = session.select("user.name")
session.subscribe(
    lambda user: print(user["name"]),
    selector=lambda s: s["user"],
    immediate=True,
)
```

On the server that is backed by Specter state. In browser code SPRAG rewrites the import to a generated stores shim and hydrates it from `window.__SPRAG_STORES__`. You declare it once.

### Dynamic route patterns are part of the build model

SPRAG supports route patterns like `[slug]` and `[...segments]`, and the static build expects you to declare the concrete paths it should emit.

```python
# app/routes/docs/[...segments]/page.py
from sprag import page

from app.content import docs_static_paths

from .server import DocsArticleController
from .web import DocsArticleScreen


docs_article = page(
    path="/docs/[...segments]",
    controller=DocsArticleController,
    screen=DocsArticleScreen,
    mode="document",
    static_paths=docs_static_paths,
)
```

```python
# app/content.py
from pathlib import Path

from sprag import load_markdown_tree


CONTENT_ROOT = Path(__file__).resolve().parent / "content"


def docs_collection():
    return load_markdown_tree(CONTENT_ROOT / "docs", base_url="/docs")


def docs_static_paths():
    return [{"segments": list(doc.path_parts)} for doc in docs_collection()]
```

That means the same framework can serve dynamic route patterns in dev and emit concrete static HTML for them at build time.

### Realtime is framework-level, not bolted on

SSE is built into the HTTP app through the bus bridge:

```python
from sprag import QueueService, bus


class LabJobQueue(QueueService):
    def handle_item(self, item):
        bus.emit(
            "sprag:broadcast",
            {"event": "lab:job.done", "payload": {"id": item["id"]}},
        )
```

```python
class QueueDemoModule(Module):
    def on_start(self):
        self.listen("lab:job.done", self.on_job_done)
```

Websocket ingress is also part of the framework surface:

```python
class SocketDemoController(Controller):
    route = "/socket-demo"

    def build_events(self, handler):
        handler.on("lab:socket.ping", self.handle_ping)
```

```python
class SocketDemoModule(Module):
    def on_start(self):
        self.on_socket("lab:socket.pong", self.on_pong)

    def send_ping(self):
        self.emit_socket("lab:socket.ping", {"origin": "browser"})
```

If an app declares real socket ingress, SPRAG's `server_mode="auto"` can promote it to websocket transport automatically.

## Quick Start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e /path/to/SPRAG

sprag new myapp
cd myapp
sprag add content guides
sprag dev --port 8000
```

Open `http://127.0.0.1:8000/`.

Before chasing framework bugs, run a structural check:

```bash
sprag doctor
```

If something looks wrong in hydration or browser behavior, inspect the generated surface:

```bash
sprag inspect /counter --rebuild
sprag inspect /counter --open-files
```

Build a deployable artifact:

```bash
sprag build
cd dist
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 server.py --port 8000
```

The dist bundle contains:

- your app package
- the SPRAG runtime
- compiled browser assets under `public/`
- a runnable `server.py`
- a rewritten `requirements.txt` with `specter-runtime`

## Showcase

The `labs` template is the clearest picture of the framework's current power. It is not a toy marketing app. It is basically a running canary for the real surface area.

```bash
sprag new labs-demo --template=labs
cd labs-demo
sprag dev --port 8000
```

It includes:

- `counter` for the basic `Controller` + `Module` + `Component` loop
- `virtual-scroll` for `@virtual_scroll` over a growing data set
- `store-demo` for a flat shared `store(...)`
- `nested-store-demo` for path-based nested state and selector subscriptions
- `queue-demo` for `QueueService` plus SSE fanout to the browser
- `watcher-demo` for service-owned polling and broadcast updates
- `operation-demo` for `Operation.run` success and failure paths
- `animation-demo` for `@animate` and DOM-driven transitions
- `socket-demo` for shared websocket transport and controller ingress
- `lifecycle-mount` for a browser-owned mounted app with child teardown

## Project Shape

A normal app:

```text
myapp/
├── app/
│   ├── __init__.py
│   ├── shell.html
│   ├── shell.css
│   ├── stores.py
│   ├── mounts/
│   └── routes/
│       ├── home/
│       ├── counter/
│       └── about/
├── requirements.txt
└── README.md
```

A typical hybrid route:

```text
app/routes/counter/
├── __init__.py
├── server.py
├── components.py
├── modules.py
├── web.py
└── page.py
```

SPRAG discovers surfaces by walking `app.routes` and `app.mounts`, including dynamic path directories like `[slug]` and `[...segments]`.

## CLI

### Scaffolding

```bash
sprag new <name>
sprag new <name> --template=docs
sprag new <name> --template=labs
sprag add route <name> --mode=document
sprag add route <name> --mode=hybrid
sprag add mount <name>
sprag add content <name>
```

### Build and serve

```bash
sprag dev
sprag dev --port 8000
sprag build
sprag routes
```

### Diagnostics

```bash
sprag doctor
sprag doctor --verbose
sprag inspect /counter
sprag inspect /counter --rebuild
sprag inspect /counter --open-files
sprag inspect /lifecycle-mount --open-files
```

`sprag add content <name>` scaffolds a markdown-backed collection under `app/content/<name>/` plus a document index route and catch-all article route under `app/routes/<name>/`. It is the fast way to turn a `bare` app into a real content site without hand-wiring `app/content_support.py`, static paths, and the article route shape yourself.

`sprag doctor` is the fast health check for the current app. It verifies project shape, app loading, route and mount importability, subclass sanity, buildability, and transport dependencies, then prints a short green/red checklist.

`sprag inspect <path>` is the practical "what did SPRAG compile this into?" tool. It accepts a concrete route or mount path, reads `.sprag/manifest.json`, and prints the matched surface metadata, hydration entries, generated file paths, and the compiled JS for just that surface.

Use `--rebuild` when you want inspect output from a fresh preview build. Use `--open-files` when you only want the generated file paths without dumping the compiled source.

## Under The Hood

SPRAG sits on top of two runtimes and tries to make them feel like one framework:

- **Specter** on the server for controllers, services, queues, watchers, operations, schemas, and orchestration
- **Ragot** in the browser for components, modules, DOM ownership, stores, hydration, virtual scrolling, and animation

SPRAG's job is to turn those into one coherent authoring model instead of exposing them as two unrelated systems.
