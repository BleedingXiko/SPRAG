# SPRAG

**One Python language, two runtimes.**

SPRAG is a Python web framework that mirrors a server runtime ([Specter](#under-the-hood)) and a browser runtime ([Ragot](#under-the-hood)) into a single authoring surface. You write controllers, components, browser modules, services, and shared stores in plain Python. SPRAG compiles the browser-facing parts to JavaScript at build time, leaves the server parts as Specter, and ships an SSR-then-hydrate boot path that needs zero glue from you.

There is no template language, no separate JS package to install, and no client/server context switch. The same `self.set_state`, `self.listen`, `self.timeout`, `self.subscribe` calls work on either side of the wire.

> **Status: pre-alpha.** The end-to-end shape works today — `sprag new` produces projects that build, dev-serve, dispatch typed actions, hydrate modules/components, hydrate stores, compose shared browser classes, serve client app mounts, and bridge bus events to the browser over SSE. The surface is still moving and the public API is not yet pinned.

---

## The 60-Second Tour

A SPRAG app is a directory of routes and optional mounts. Each route is a small handful of Python files. Here is the entire interactive counter that ships in the scaffold:

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
            ui.button("Increment", type="button", data_role="increment", class_="btn"),
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

That `CounterModule.on_start` and `on_click` code? It runs in the browser. SPRAG's codegen compiles it from Python to JavaScript at build time. Same primitives, same mental model, same language.

---

## Quick Start

```bash
# 1. Install (from a local checkout while pre-alpha)
python3 -m venv .venv
. .venv/bin/activate
pip install -e /path/to/SPRAG

# 2. Scaffold a new app
sprag new myapp
cd myapp

# 3. Run it
sprag dev --port 8000
```

Open <http://127.0.0.1:8000/>. Edit anything under `app/`, save — the dev server rebuilds and serves the new version.

The default scaffold ships with three real routes (a landing page, an interactive counter, an about page), a shared layout shell, a cross-route store, and a polished CSS theme. For a smaller starting point use `sprag new myapp --template=bare`; for a broad feature canary use `sprag new labs-demo --template=labs`.

When you're ready to ship:

```bash
sprag build
cd dist
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python3 server.py --port 8000
```

That's the entire production handoff.

---

## The Mental Model

### Two runtimes, one source tree

SPRAG mirrors Specter and Ragot 1:1. You write classes that subclass SPRAG's Python stubs:

| Server (Specter)         | Browser (Ragot)          | What it does                            |
|--------------------------|--------------------------|-----------------------------------------|
| `Controller`             | —                        | Page data + `@action`s                  |
| `Service`                | `Module`                 | Long-lived behaviour, events, state     |
| `Component` (SSR)        | `Component` (hydrated)   | UI tree, props, render/morphdom         |
| `store(name)`            | `store(name)`            | Cross-runtime shared state              |
| `bus.emit / bus.on`      | `self.emit / self.listen`| Event bus, bridged via SSE              |

The cross-runtime methods — `set_state`, `listen`, `emit`, `timeout`, `interval`, `subscribe`, `add_cleanup`, `adopt` — exist on both sides with the same Python signature. SPRAG's codegen routes them to the right runtime.

### Routes and mounts

SPRAG has two server-known surfaces:

- **Route** — a server-rendered page declared with `page(...)`. Routes have render modes.
- **Mount** — a client app entry declared with `mount(...)`. The server returns a boot document and Ragot owns the root `Component` / `Module` in the browser.

A route is declared with `mode="document"` or `mode="hybrid"`.

- **`document`** — pure SSR. Renders to HTML on the server, no JavaScript module is loaded for that route. Use it for landing pages, marketing, docs, blog posts.
- **`hybrid`** — SSR for the first paint, then Ragot hydrates the registered modules in the browser. Use it for anything that needs interactivity.

A mount has no `--mode` and is not a route mode. Use it when you want a Ragot app mounted at a URL:

```bash
sprag add mount dashboard
sprag add mount admin/tools
```

`dashboard` maps to `/dashboard`; `admin/tools` maps to `/admin/tools`. Mounts scaffold under `app/mounts/...` and generate the same normal authoring pieces: `server.py` for boot data, `web.py` for the root `Component`, `modules.py` for the root `Module`, and `mount.py` for the manifest.

Mounts are exact app entries for now. `/dashboard` serves the mount; `/dashboard/deep` is not implicitly routed unless you add a real server surface later. This keeps mount behavior honest: SPRAG serves the app entry, Ragot owns the client app.

You can mix document routes, hybrid routes, and mounts in one project.

### A route in five or six files

```
app/routes/counter/
├── __init__.py        # Python package
├── server.py          # Controller + @actions  (server)
├── components.py      # Component subclasses    (both runtimes)
├── modules.py         # Module subclasses       (browser only, hybrid routes)
├── web.py             # Screen — composes the page
└── page.py            # page(...) — binds path + controller + screen
```

`sprag add route <name> --mode=document` writes a five-file SSR route. `sprag add route <name> --mode=hybrid` writes the six-file interactive shape with `modules.py`. Nested routes work too: `sprag add route admin/users --mode=hybrid` creates the intermediate package files. The older `sprag add <name>` form still works as a route alias.

A mount uses the same naming sugar:

```
app/mounts/dashboard/
├── __init__.py        # Python package
├── server.py          # Boot controller / initial data
├── web.py             # Root Component
├── modules.py         # Root Module
└── mount.py           # mount(...) — binds path + root classes
```

`sprag add mount dashboard` maps to `/dashboard`. Mounts have no `--mode`.

### Composition

The six-file route shape is a starting point, not a ceiling. Route discovery walks every `*.page` module under `app.routes`, so nested packages like `app/routes/admin/users/page.py` work naturally. Shared app code can live outside routes and be imported like normal Python:

```python
# app/shared/components.py
from sprag import Component, ui


class Badge(Component):
    def render(self, props=None):
        props = props or self.props
        return ui.span(props["label"], class_="badge")
```

```python
# app/features/filters/modules.py
from sprag import Module


class FilterModule(Module):
    def on_start(self):
        self.listen("filter:set", self.on_filter)

    def on_filter(self, payload):
        self.set_state({"filter": payload["filter"]})
```

```python
# app/routes/admin/users/components.py
from sprag import Component, ui

from app.shared.components import Badge


class UsersCard(Component):
    def render(self, props=None):
        return ui.div(
            ui.h1("Users"),
            Badge().render({"label": self.state["label"]}),
        )
```

```python
# app/routes/admin/users/modules.py
from sprag import Module

from app.features.filters.modules import FilterModule


class UsersModule(Module):
    def on_start(self):
        self.filters = FilterModule(state={"filter": "all"})
        self.adopt(self.filters)
```

SPRAG recursively emits imported browser-side `Component` / `Module` subclasses that are referenced from generated code, and generated modules import one another before calling Ragot's normal `adopt(...)`. Keep browser class names unique for now; SPRAG fails loudly on duplicate generated class names until module-qualified JS output names land.

Nested component render overrides like `Badge().render({"label": "Live"})` are supported in both SSR and generated browser code.

Generated `Module` constructors preserve simple `self.field = ...` assignments from `__init__`, which is enough for composition handles like `self.filters = None`. Put lifecycle work in `on_start()`, where Ragot ownership and DOM state exist.

### Server actions, typed and validated

```python
from sprag import Schema, Field, action


class PostsController(Controller):
    @action(schema=Schema("create_post", {
        "title": Field(str, required=True),
        "body": Field(str, required=True),
    }))
    def create_post(self, title, body):
        post = self.db.posts.insert({"title": title, "body": body})
        return {"post": post}
```

From a browser-side `Module`:

```python
self.call_action("create_post", {"title": "Hello", "body": "..."}).then(
    lambda result: self.emit("posts:created", result.value)
)
```

The schema validates the payload before your method runs. The result comes back as a structured object with `ok`, `value`, `error`, and `status`. No JSON parsing, no envelope handling, no manual fetch.

Actions can be as small as a counter increment or as stateful as a service-backed workflow. A hybrid module can delegate per-element events, read `data-*` attributes, call an action with typed payload, then update module state from `result.value`.

### Cross-runtime stores

A single declaration in `app/stores.py`:

```python
from sprag import store

cart = store("cart", initial={"items": [], "total": 0})
```

…is one Python object that exists on both runtimes. On the server it backs a Specter store; in any browser-side `Module` or `Component`, the codegen rewrites the import to the generated `stores.js` shim and gives you a Ragot `createStateStore` with the same surface:

```python
# Either runtime — same Python.
cart.set({"total": 99})
cart.update(lambda s: {"items": s["items"] + [item]})
cart.subscribe(lambda snapshot: print(snapshot))
```

The server's current snapshot is shipped in the document as `window.__SPRAG_STORES__` and the generated shim hydrates the browser store from it on first paint, so there is no flash.

### Bus events, bridged

`bus.emit("sprag:broadcast", {"event": "...", "payload": ...})` on the server reaches every connected browser via an SSE stream. The browser-side `self.listen("...", fn)` picks it up. Services, queue workers, watchers, and controller actions can all publish into the same bridge.

### Templates and forcing functions

SPRAG ships more than one scaffold:

- `default` — the normal starter app with home, counter, about, shared layout, and a cross-route store.
- `bare` — only the minimal package skeleton. Use this when you want to design the app shape yourself.
- `labs` — a runnable framework canary with routes for actions, stores, queues, watchers, operations, animation, and virtual scrolling.

Local exploratory apps should live under `.sandbox/` while developing SPRAG itself. Template-generated sandboxes can be deleted and regenerated; hand-built sandboxes like a mini app are fine too, but they are test artifacts, not framework source.

---

## CLI

```
sprag new <name>              Scaffold a new project
sprag new <name> --template=labs
sprag add route <route>       Add a document route
sprag add route <route> --mode=hybrid
sprag add mount <name>        Add a Ragot-owned client app entry
sprag dev                     Dev server with file watching + live rebuild
sprag build                   Build a deployable dist/ artifact
sprag routes                  List discovered routes, mounts, actions, and schemas
sprag --version
```

Useful flags:
- `--project-root` — point at a specific app directory
- `--app module:attr` — explicit app target (defaults: `app:app`)
- `--port` — dev/dist server port
- `--output-dir` — build output directory
- `--template` — project template for `sprag new`
- `--mode` — route mode for `sprag add route`

---

## Justified Decorators

SPRAG's rule is: **decorators only when neither runtime provides the primitive imperatively**. Most things you'd reach for a decorator for in another framework are already methods on `Module` / `Component` / `Service` (`self.listen`, `self.timeout`, etc.). The handful of decorators that *do* exist all encode something the imperative API can't express in one line:

- `@action(schema=...)` — registers a server-side action and binds it to a `Schema` for validation. (Server only.)
- `@debounce(seconds)` / `@throttle(seconds)` — wrap a method with auto-cancelling timer state. (Browser.)
- `@animate(class_name=...)` — wraps `mount` / `unmount` with Ragot's `animateIn` / `animateOut`. (Browser.)
- `@virtual_scroll(...)` — wraps a `Component` in a `VirtualScroller`. The class authors `chunk(self, i)` and `total(self)` and SPRAG synthesises `onStart`. (Browser.)
- `@infinite_scroll(at=...)` — installs a `createInfiniteScroll` against a sentinel selector. (Browser.)
- `ref(selector)` — class-level descriptor that captures a DOM element into `self.refs.X` on mount. (Browser.)

That's the entire decorator surface. Anything else is just a method call.

---

## Under the Hood

SPRAG sits on top of two purpose-built runtimes you can drop into directly when you need to:

- **[Specter](https://github.com/) (`specter-runtime`)** — a Python backend runtime with lifecycle-managed `Service`s, typed `Schema` + `Outcome`s, a global event bus, queue workers, a polling `Watcher` primitive, managed subprocesses, a dependency-injection registry, and a `boot()` orchestrator. SPRAG's `Service` subclass adds the cross-runtime symmetry methods (`watch_state`, `subscribe(target, fn)`, etc.) on top.
- **Ragot** — a browser ESM runtime focused on direct DOM ownership: in-place reconciliation (`renderList`, `renderGrid`, `morphDOM`), keyed updates, virtual scrolling, infinite scroll, lazy image loading, proxy-based state stores with selector memoisation, and a lifecycle-aware `Module` / `Component` system.

SPRAG re-exports both surfaces so anything either runtime can do, your app can do — but the framework's job is to wrap the common patterns in conventions you don't have to reinvent.

The browser runtime is vendored in `sprag/assets/`, so app developers don't install Ragot separately.

---

## Project Layout

A scaffolded SPRAG app:

```
myapp/
├── app/
│   ├── __init__.py      # exposes `app = App(routes="app.routes")`
│   ├── _shared.py       # shell() layout helper + theme CSS
│   ├── stores.py        # cross-route store declarations
│   ├── mounts/          # optional client app mounts
│   └── routes/
│       ├── home/
│       ├── counter/
│       └── about/
├── requirements.txt
└── README.md
```

A built `dist/`:

```
dist/
├── public/              # compiled browser assets (Ragot + your modules/components)
├── app/                 # your shipped Python source
├── sprag/               # vendored framework runtime
├── server.py            # entrypoint
└── requirements.txt     # specter-runtime + your backend deps
```

Backend dependencies forward automatically: your app's `requirements.txt` becomes `dist/requirements.txt` with `sprag` swapped out for `specter-runtime`.

---

## Roadmap

The end-to-end shape works today. The current focus is **deepening the integration** between SPRAG and the two runtimes so authors never need to drop into raw Specter or Ragot imports.

What's landing next, in priority order:

1. **First-class shell primitive.** Today routes and mounts both get an HTML document wrapper, but the wrapper itself is still generated internally or hand-rolled in shared helpers. Shell should become a SPRAG primitive for document HTML, shared CSS, SSR wrappers, and mount roots.
2. **Specter symmetry pass round 2.** The `Service` ↔ `Module` cross-runtime API is in place. Next: making sure every Specter primitive (`QueueService`, `Watcher`, `Operation`, `SocketIngress`) is reachable through the same `from sprag import ...` surface and doesn't require users to know about Specter at all.
3. **Model bridge.** Specter has a path-style `Model`; Ragot has a path-style state API. A `model("name")` factory that mirrors them the same way `store("name")` does today.
4. **Diagnostic CLI.** `sprag doctor` (project health), `sprag inspect <route>` (compiled-JS introspection), `sprag generate component|store|service` (targeted scaffolds).
5. **Hot reload across both runtimes.** Today the dev server rebuilds on change; the next step is preserving browser state across rebuilds where possible.
6. **Packaging polish.** Versioning, PyPI release, runtime version pinning between SPRAG, Specter, and the vendored Ragot bundle.

What is **not** on the roadmap, by design: a template language, a CSS-in-JS solution, a virtual DOM, a router DSL, or a build system separate from `sprag build`. SPRAG's job is to make Python the only thing you need.

---

## Working on SPRAG Itself

If you're contributing to the framework rather than building an app with it:

```bash
git clone <this-repo>
cd SPRAG
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

The repo's `requirements.txt` includes `-e .` so the framework installs editable into the venv.

Smoke apps live in `.sandbox/`. If a sandbox is generated from a template and you want the change to persist, edit the template under `sprag/templates/` and regenerate the sandbox. Hand-built exploratory apps can also live in `.sandbox/`; keep them there so experiments do not leave stray apps elsewhere on the machine.

```bash
.venv/bin/python -m sprag.cli new smoke-app --output-dir .sandbox
cd .sandbox/smoke-app
../../.venv/bin/python -m sprag.cli dev --port 8000
```

### Repo layout

```
sprag/                Framework source
├── assets/           Vendored Ragot browser runtime
├── cli.py            CLI entry
├── scaffold.py       Project + route scaffolding templates
├── codegen/          Python -> JavaScript compilation (split package)
├── http_server.py    WSGI app, action dispatch, SSE bridge
├── runtime.py        Per-request page rendering
├── render.py         HTML rendering for SPRAG authoring trees
├── stores.py         Cross-runtime store bridge
├── server.py         Specter re-exports + Service symmetry shim
├── web.py            Browser-side authoring stubs (Module/Component/Screen)
└── ui.py             ui.* tag factory + ui.For/Grid/LazyImage primitives
.sandbox/             Local smoke/test area (gitignored)
pyproject.toml
requirements.txt
```

### Three things to keep straight

There are three distinct environments and they should never blur together:

1. **Framework repo** (this repo) — where SPRAG itself is developed.
2. **App repo** — a separate project that depends on SPRAG and uses it to build a real app.
3. **Dist artifact** — the built output of `sprag build`. This is what gets deployed.

A normal SPRAG user only ever touches #2 and #3. #1 is for framework contributors.

---

## License

TBD — pre-alpha.
