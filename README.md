# SPRAG

**One Python language, two runtimes.**

Write your entire web app in Python — server logic, UI components, browser behavior, state, realtime — and SPRAG compiles, ships, and runs it as a single coherent application. No JavaScript to write. No frontend build chain to maintain. No "API layer" between your server and your UI.

```bash
pip install spragkit
sprag new myapp && cd myapp && sprag dev
```

> **Status: pre-alpha.** The framework is real and working. The API surface is not pinned yet.

## Documentation

**→ [bleedingxiko.github.io/SPRAG](https://bleedingxiko.github.io/SPRAG)**

The docs site is a SPRAG app built with the `docs` template — dogfooding the framework. Start with [Installation](https://bleedingxiko.github.io/SPRAG/docs/getting-started/installation), then [First App](https://bleedingxiko.github.io/SPRAG/docs/getting-started/first-app).

## 60-Second Example

A hybrid route: server-rendered HTML, browser hydration, typed action.

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

The `Module` is Python. SPRAG compiles it to JavaScript at build time, ships it with the route, wires the action bridge, and hydrates the component in place. No handoff. No separate frontend.

## What It Is

- **File-discovered routes** under `app/routes/`, with `document` (pure SSR) and `hybrid` (SSR + hydration) modes plus standalone `mount(...)` SPAs
- **Browser code is compiled Python** — `Component` and `Module` classes are real Python that SPRAG compiles to Ragot ESM JavaScript at build time
- **Cross-runtime state** — `store(...)` works identically on server and browser
- **Typed actions** — server mutations go through schema-validated dispatch
- **Realtime built in** — SSE, websockets, queues, watchers as framework primitives
- **`sprag build`** produces a deployable artifact; **`sprag build static`** produces a pre-rendered static site; **`sprag pack`** optimizes for production

## CLI

```bash
sprag new myapp [--template default|bare|docs|labs]
sprag dev [--port 8000]
sprag build               # full app (server + client)
sprag build static        # pre-rendered static site
sprag pack                # minify, bytecode-compile, optimize images
sprag types               # generate .sprag/sprag_project_types.pyi from the build manifest
sprag add route|mount|content <name>
sprag routes              # list all surfaces
sprag inspect /path       # show compiled output for a route
sprag doctor              # structural health check
```

See the [CLI reference](https://bleedingxiko.github.io/SPRAG/docs/reference/cli) for the full surface.

## Templates

| Template | What you get |
|---|---|
| `default` | Counter demo + about page — minimal hybrid app |
| `bare` | Skeleton with one route, no demos |
| `docs` | Markdown-backed docs/blog site (the docs site itself) |
| `labs` | Every framework primitive exercised in a real app — counter, virtual scroll, queues, watchers, sockets, uploads, auth, forms, animation, mounts |

## Under the Hood

SPRAG sits on two runtimes:

- **[Specter](https://github.com/BleedingXiko/SPECTER)** on the server — controllers, services, queues, watchers, operations, lifecycle
- **[Ragot](https://github.com/BleedingXiko/RAGOT)** in the browser — components, modules, DOM ownership, stores, hydration

SPRAG makes them feel like one framework: `set_state`, `subscribe`, `timeout`, `adopt` follow the same model on both sides.

## License

Apache-2.0. See [LICENSE](LICENSE) and [CONTRIBUTING.md](CONTRIBUTING.md).
