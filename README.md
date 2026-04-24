# SPRAG

**One Python language, two runtimes.**

Write your entire web app in Python — server logic, UI components, browser behavior, state management, realtime events — and SPRAG compiles, ships, and runs it as a single coherent application.

No JavaScript to write. No frontend build chain to maintain. No "API layer" between your server and your UI.

```bash
pip install spragkit
sprag new myapp && cd myapp && sprag dev
```

> **Status: pre-alpha.** The framework is real and working. The API surface is not pinned yet.

**[Read the docs →](https://bleedingxiko.github.io/SPRAG/docs)**

---

## What SPRAG Actually Is

SPRAG is a full-stack Python web framework where server controllers, browser components, browser modules, state stores, realtime events, and deployment artifacts are all authored in Python and managed by one toolchain.

- **Routes** are file-discovered under `app/routes/`
- **SSR is the default** — document routes are pure server HTML, hybrid routes render first then hydrate
- **Browser code is compiled Python** — your `Module` and `Component` classes are real Python that SPRAG compiles to JavaScript at build time
- **State is declared once** — `store(...)` works identically on server and browser
- **Actions are typed** — server mutations go through schema-validated action dispatch
- **Realtime is built in** — SSE, websockets, queues, watchers, and broadcast events are framework primitives
- **`sprag build` produces a deployable artifact** — `sprag pack` optimizes it for production

### Not This

SPRAG is not a template language, not a Python wrapper around REST calls, not "Flask + React you still wire yourself," and not a virtual DOM framework.

---

## Install

```bash
pip install spragkit
```

PyPI package: `spragkit`. Import package: `sprag`.

Requires Python 3.9+. Runtime dependency: `specter-runtime`.

---

## 60-Second Example

A hybrid route with server-rendered HTML, browser hydration, and a typed action:

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

The `Module` above is Python. SPRAG compiles it to JavaScript, ships it with the route, wires the action bridge, and hydrates the component in place. No handoff. No separate frontend.

---

## Core Concepts

### Routes and Mounts

SPRAG has two surface types:

| Surface | Purpose | Render |
|---|---|---|
| `page(mode="document")` | Pure SSR page | Server HTML, no JS |
| `page(mode="hybrid")` | SSR + hydration | Server HTML, then browser takes over |
| `mount(...)` | Browser-owned app | Boot document, browser owns the root |

Routes live under `app/routes/`. Mounts live under `app/mounts/`. Both are file-discovered.

### Shared State

One declaration, two runtimes:

```python
# app/stores.py
from sprag import store

session = store("session", initial={
    "user": {"name": "Ada"},
    "prefs": {"theme": "dark"},
})
```

```python
# works identically on server or browser
session.set("user.name", "Grace")
session.patch({"prefs": {"theme": "light"}})
session.subscribe(
    lambda user: print(user["name"]),
    selector=lambda s: s["user"],
    immediate=True,
)
```

Server-side it backs a Specter model. Browser-side SPRAG rewrites the import to a generated shim hydrated from `window.__SPRAG_PAYLOAD__.stores`.

### Shells

The shared frame is plain HTML and CSS:

```python
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

Per-surface extras live on the surface itself: use `css=[...]` for route- or mount-specific styles, `js=[...]` for extra scripts, and put pass-through files under `app/static/`. Files in `app/static/` are published at `/static/...`; other declared local assets land under `/assets/...`.

### JavaScript Interop

SPRAG ships two JS integration patterns.

Classic/global libraries go through `js=[...]` and `browser.*`:

```python
from sprag import Module, browser, page

chart_page = page(
    path="/charts",
    controller=ChartController,
    screen=ChartScreen,
    js=["app/static/vendor/chart.umd.js"],
)

class ChartModule(Module):
    def on_start(self):
        Chart = browser.Chart
        canvas = self.element.query_selector("canvas")
        self.chart = Chart(canvas, {"type": "line"})

    def on_stop(self):
        if self.chart and self.chart.destroy:
            self.chart.destroy()
```

ESM libraries go through `modules={...}` and `imports.*`:

```python
from sprag import Module, imports, module, page

analytics = page(
    path="/analytics",
    controller=AnalyticsController,
    screen=AnalyticsScreen,
    modules={
        "dayjs": module("app/static/vendor/dayjs.mjs"),
        "nanoid": module("https://cdn.example/nanoid.mjs", export="nanoid"),
    },
)

class AnalyticsModule(Module):
    def on_start(self):
        dayjs = imports.dayjs
        nanoid = imports.nanoid
        self.set_state({
            "today": dayjs().format("YYYY-MM-DD"),
            "id": nanoid(),
        })
```

The authoring contract is:

- Put vendored JS, CSS, workers, WASM, and tiny adapters under `app/static/`.
- Use `js=[...]` for deferred classic scripts that expose globals.
- Use `modules={alias: module(src, export=...)}` for browser `import()`-loaded ESM.
- Use `browser.*` to read globals from classic scripts; it lowers to `globalThis.*`.
- Use `imports.*` to read declared ESM aliases; undeclared aliases fail at build time.
- Create third-party instances in `on_start()` and tear them down in `on_stop()`.

### Environment Variables

SPRAG has a built-in env convention that works on both runtimes.

Server-side:

```python
from sprag import env

app_name = env("APP_NAME", "SPRAG")
debug = env("DEBUG", False, cast=bool)
port = env("PORT", 8000, cast=int)
```

Browser-side:

```python
from sprag import Module, env, public_env

class SettingsModule(Module):
    def on_start(self):
        api_url = env("SPRAG_PUBLIC_API_URL", "/api")
        flags = public_env()
        self.set_state({
            "api_url": api_url,
            "site_name": flags.get("SPRAG_PUBLIC_SITE_NAME", "SPRAG"),
        })
```

Same authoring rule as the rest of SPRAG:

- `env(...)` is the Python spelling on both server and browser.
- On the server it reads process env after SPRAG loads `.env` / `.env.local`.
- In browser-authored `Module` / `Component` code it reads from the public env payload shipped as `window.__SPRAG_ENV__`.

- `.env` and `.env.local` are loaded automatically when SPRAG imports your app.
- Existing process env wins over file values.
- `.env.local` overrides `.env`.
- Only vars prefixed with `SPRAG_PUBLIC_` are exposed to the browser.
- `public_env()` returns the full public env mapping.
- `cast=` supports `bool`, `int`, `float`, and `str`.
- Use plain `env("SECRET_KEY", required=True)` on the server for secrets; do not prefix secrets with `SPRAG_PUBLIC_`.

### File Uploads

Keep ordinary forms on the JSON action path, and use the dedicated multipart upload path when native file inputs are involved:

```python
class AssetModule(Module):
    def on_submit(self, event):
        event.prevent_default()
        self.upload_form("upload_asset", event, self.on_progress).then(self.on_uploaded)
```

```python
class AssetController(Controller):
    route = "/assets"

    @action(schema=Schema("upload_asset", {"title": Field(str, required=True)}))
    def upload_asset(self, title):
        primary = self.request.file("asset")
        extras = self.request.files_list("gallery")
        return {"title": title, "filename": primary.filename, "extra_count": len(extras)}
```

files on `self.request.files`, `self.request.file(name)`, and
`upload_form(...)` sends `multipart/form-data` to a separate upload endpoint, keeps non-file fields JSON-safe for schema validation, and exposes uploaded files on `self.request.files`, `self.request.file(name)`, and `self.request.files_list(name)`. The intended realtime pairing is: store the authoritative upload snapshot on the server, emit a lightweight socket signal, and let the browser refetch status through a normal action.

### Background Jobs

`QueueService` now has a first-class SPRAG job contract on top of Specter's
`QueueService` has a first-class SPRAG job contract on top of Specter's raw queue workers:

```python
class BuildQueue(QueueService):
    def handle_item(self, item):
        total = 3
        for step in range(total):
            self.check_cancelled()
            self.report_progress(
                current=step + 1,
                total=total,
                message=f"Building {item['label']} ({step + 1}/{total})...",
            )
        return {"label": item["label"]}


class BuildController(Controller):
    route = "/jobs"

    @action(name="start", schema=Schema("start", {"label": Field(str, required=True)}))
    def queue_start(self, label):
        return self.enqueue("build_queue", {"label": label}, label=label)

    @action(schema=Schema("status", {"job_id": Field(str, required=True)}))
    def status(self, job_id):
        return self.job_status("build_queue", job_id)

    @action(schema=Schema("cancel", {"job_id": Field(str, required=True)}))
    def cancel(self, job_id):
        return self.request_job_cancel("build_queue", job_id)
```

`self.enqueue(...)` returns one stable payload shape: `{"accepted": ..., "job": ..., "queue": ..., "message": ...}`. The queue owns the authoritative job record (`queued`/`running`/`cancelling`/`completed`/`failed`/`cancelled`), progress, result/error fields, and targeted socket invalidation metadata. The intended browser pattern is: call the action, listen for `sprag:queue.job.changed`, then refetch `status(...)` for the job.

### Dynamic Routes and Content

File-based dynamic params and catch-all segments:

```
app/routes/blog/[slug]/page.py       -> /blog/my-post
app/routes/docs/[...segments]/page.py -> /docs/getting-started/install
```

Static builds expand dynamic routes via `page(..., static_paths=...)`:

```python
docs = page(
    path="/docs/[...segments]",
    controller=DocsController,
    screen=DocsScreen,
    mode="document",
    static_paths=lambda: [{"segments": list(d.path_parts)} for d in docs_collection()],
)
```

Markdown content loading is built in via `load_markdown_tree()` and `load_markdown_document()`.

### Realtime

SSE broadcast through the bus bridge:

```python
class LabJobQueue(QueueService):
    def handle_item(self, item):
        bus.emit("sprag:broadcast", {"event": "lab:job.done", "payload": item})
```

```python
class JobModule(Module):
    def on_start(self):
        self.listen("lab:job.done", self.on_job_done)
```

Websocket ingress through the shared socket bridge:

```python
class ChatController(Controller):
    def build_events(self, handler):
        handler.on("chat:message", self.handle_message)
```

```python
class ChatModule(Module):
    def on_start(self):
        self.on_socket("chat:reply", self.on_reply)

    def send(self, text):
        self.emit_socket("chat:message", {"text": text})
```

Targeting:

```python
self.emit_socket("upload:changed", {"reason": "stored"}, session_id=self.request.session_id)
self.emit_socket("room:notice", {"room": "alpha"}, topic="room:alpha")
self.emit_socket("private:notice", {"ok": True}, client_id="sprag-socket-7")
```

Browser Modules can opt into topics on the shared socket:

```python
class UploadModule(Module):
    def on_start(self):
        self.join_topic("upload:123")
        self.on_socket("upload:changed", self.on_changed)

    def on_changed(self, payload):
        self.call_action("status", {}).then(self.on_status)
```

Recommended pattern: treat sockets as a selective invalidation channel, not the source of truth. Mutate authoritative server state first, emit a small signal event, then refetch the current snapshot through normal SPRAG actions or route loads. Topic names should be app-namespaced strings such as `upload:123` or `room:alpha`.

SSE remains the broadcast-only path in this release.

If any surface declares socket ingress, `server_mode="auto"` promotes to websocket transport automatically.

### Auth and Sessions

SPRAG includes built-in session management and authentication:

```python
from sprag import App, SessionPolicy, InMemorySessionStore, AnonymousAuthService

app = App(
    routes="app.routes",
    session_store=InMemorySessionStore(),
    auth_service=AnonymousAuthService(),
    session_policy=SessionPolicy(
        idle_ttl_seconds=3600,
        absolute_ttl_seconds=86400,
        remember_me_ttl_seconds=2592000,
    ),
)
```

Protect routes with `@requires_auth`:

```python
from sprag import requires_auth

class DashboardController(Controller):
    route = "/dashboard"

    @requires_auth(roles=["admin"])
    def load(self):
        return {"user": self.request.user}
```

### Redirects

First-class redirects with fluent API:

```python
from sprag import redirect

class AuthController(Controller):
    route = "/auth"

    @action
    def login(self):
        # Server-side redirect
        return redirect("/dashboard", status=302)

    @action
    def login_replace(self):
        # Browser history replacement
        return redirect("/dashboard", replace=True)
```

---

## CLI

### Create

```bash
sprag new myapp                      # default template
sprag new myapp --template=bare      # minimal skeleton
sprag new myapp --template=docs      # static docs/blog site
sprag new myapp --template=labs      # full framework showcase
```

### Develop

```bash
sprag dev                            # dev server with hot reload
sprag dev --port 3000
sprag dev static                     # serve the pure static build locally
sprag routes                         # list all routes, mounts, and actions
```

### Scaffold

```bash
sprag add route dashboard --mode=hybrid
sprag add route about --mode=document
sprag add mount admin-panel
sprag add content guides             # markdown collection + routes
```

### Build and Deploy

```bash
sprag build                          # compile to dist/
sprag build static                   # compile a pure static site
sprag pack                           # optimize dist for production
sprag pack --zip                     # optimize + archive
```

`sprag pack` runs:
- CSS/JS minification (terser/cleancss if installed, regex fallback otherwise)
- Python bytecode compilation with source stripping
- Image optimization with WebP + responsive variants (requires Pillow)
- Pre-gzip compression of static assets
- Build validation

```bash
sprag pack --skip-bytecode           # minify + images + gzip only
sprag pack --skip-images             # no image optimization
sprag pack --image-quality 60        # aggressive image compression
sprag pack --no-webp --no-srcset     # skip variant generation
```

### Diagnostics

```bash
sprag doctor                         # structural health check
sprag doctor --verbose               # with tracebacks
sprag inspect /counter --rebuild   # show compiled output for a route
sprag inspect /counter --open-files # just the generated file paths
```

---

## Project Shape

```
myapp/
├── app/
│   ├── shell.html           # shared layout
│   ├── shell.css           # shared styles
│   ├── stores.py          # cross-runtime state
│   ├── routes/
│   │   ├── home/         # document route
│   │   ├── counter/      # hybrid route
│   │   └── blog/[slug]/ # dynamic route
│   ├── mounts/
│   │   └── dashboard/    # browser-owned mount
│   └── content/
│       └── docs/         # markdown content
└── requirements.txt
```

Each hybrid route:

```
app/routes/counter/
├── page.py            # page(...) declaration
├── server.py          # Controller + @actions
├── components.py    # Component classes (both runtimes)
└── modules.py        # Module classes (browser, compiled to JS)
```

---

## Browser Codegen

Browser `Module` and `Component` code is compiled Python — not an embedded Python interpreter. SPRAG compiles the subset of Python that maps cleanly to JavaScript and fails at build time when a construct would produce misleading behavior.

**Supported:**

- Control flow: `if`/`elif`/`else`, `for`/`while`, `break`/`continue`, `try`/`except`/`finally`
- Comprehensions: list, dict, set, generator (one-generator with `if` filters)
- Destructuring: tuple unpacking in assigns and loop targets
- Dict operations: spread `{**a}`, merge `a | b`, augmented merge `a |= b`
- Pattern matching: `match/case` with literal, wildcard, capture, guard, sequence, mapping, `as`, and `|` patterns
- Walrus operator: simple-name targets in expression contexts
- String methods: `.upper()`, `.lower()`, `.strip()` map to JS equivalents
- Builtins: `len`, `str`, `int`, `float`, `bool`, `abs`, `min`, `max`, `round`, `print`, `range`, `sum`
- Async: `async def`, `await`
- Decorators: `@debounce`, `@throttle`, `@animate`, `@virtual_scroll`, `@infinite_scroll`, `@ref()`

**Deliberately rejected** (clear `JSCodegenError` at build time):

- Walrus inside comprehensions or lambda bodies
- `match/case` class patterns, `*rest`, `**rest`, binding OR patterns
- Server-only imports in browser code
- Python constructs with no honest JS equivalent

---

## The Labs Template

The `labs` template is the framework's running test surface — every primitive SPRAG exposes gets exercised in a real scaffolded app.

```bash
sprag new labs --template=labs && cd labs && sprag dev
```

Includes: counter, virtual scroll, flat store, nested store with selectors, queue + SSE, watcher polling, operation success/failure, CSS animation, websocket roundtrip, cross-wired queue-to-store flow, and a lifecycle mount.

---

## Under the Hood

SPRAG sits on two runtimes:

- **[Specter](https://github.com/BleedingXiko/SPECTER)** on the server — controllers, services, queues, watchers, operations, lifecycle management, and orchestration
- **Ragot** in the browser — components, modules, DOM ownership, stores, hydration, virtual scrolling, animation, and teardown

SPRAG makes them feel like one framework. `set_state`, `listen`, `emit`, `subscribe`, `timeout`, `interval`, and `adopt` follow the same mental model on both sides.

---

## Documentation

Full documentation lives in `docs/` and is published to GitHub Pages:

**[bleedingxiko.github.io/SPRAG](https://bleedingxiko.github.io/SPRAG)**

The docs site is itself a SPRAG app built with the `docs` template — dogfooding the framework. Topics covered:

- **Getting Started** — installation, first app, project structure
- **Framework** — two runtimes, routes, controllers, stores, auth, uploads, realtime, codegen
- **Specter** — services, schemas, queues (server runtime)
- **Ragot** — components, modules, ui primitives, decorators (browser runtime)
- **Guides** — deployment, forms, background jobs
