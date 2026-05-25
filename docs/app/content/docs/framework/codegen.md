---
title: Codegen
description: What Python constructs compile to JavaScript — the complete reference for browser-side code.
order: 17
---

# Codegen

When you write `Component` and `Module` subclasses in Python, `sprag build` compiles them to Ragot ESM JavaScript. This page documents exactly what Python constructs are supported, what they compile to, and what will raise an error.

## The rule

If it's in a `Component` or `Module` subclass — or at the top level of a browser source file alongside one — it compiles to JS. Everything else runs as Python on the server. The compile boundary is at the file's class boundary.

## Module-level helpers

Top-level `def` and simple `name = expr` / `name: T = expr` declarations in browser source files (`components.py`, `modules.py`, `web.py`) compile to JS alongside the class that uses them. Useful when two classes in the same file want to share a small helper without spinning up a whole `Module` for one or two functions.

```python
# app/routes/cart/modules.py
from sprag import Module


CURRENCY = "$"


def format_price(cents):
    return CURRENCY + str(cents / 100)


def line_total(item):
    return format_price(item["price_cents"] * item["qty"])


class CartModule(Module):
    def on_result(self, result):
        self.set_state({
            "items": result.value["items"],
            "subtotal_label": format_price(result.value["subtotal_cents"]),
        })


class CheckoutModule(Module):
    def on_render_line(self, item):
        return line_total(item)
```

Both classes get a `function format_price` / `const CURRENCY` prelude in their generated JS, ordered so dependencies appear first. Each generated class file gets its own copy of just the helpers it actually references — unused helpers are dropped.

**Rules and limits:**

- Top-level `def` becomes a JS `function`. `async def` is not collected — make it a method on the class instead.
- Top-level `name = expr` and `name: T = expr` become `const name = ...`. Decorated helpers are skipped (decorators apply to methods, not free functions).
- Helpers can reference each other, the stores declared in `app/stores.py`, `imports.foo` aliases, `dom.*` / `ui.*` factories, and other compiled browser classes — same as a class method.
- Helper names must not collide with imported stores or imported browser classes — both end up at file scope in the generated JS, so a clash would be a duplicate declaration. The build fails with a clear "rename the helper" message.
- `from .x import helper` doesn't surface `helper` as compilable — you either duplicate the helper or share via a JS shim plugged in through `page(modules={...})`.
- A helper that uses Python the codegen can't lower (e.g. `**kwargs` unpack) raises `JSCodegenError` with the file path and helper name.

## Statements

### Supported everywhere (methods, on_start, on_stop, etc.)

| Python | JavaScript |
|---|---|
| `x = value` | `let x = value` (first use) or `x = value` (reassign) |
| `a, b = pair` | `const [a, b] = pair` |
| `self.foo = value` | `this.foo = value` |
| `x += 5` | `x += 5` |
| `self.count += 1` | `this.count += 1` |
| `return value` | `return value` |
| `if / elif / else` | `if / else if / else` |
| `for x in items` | `for (const x of items)` |
| `for i in range(n)` | `for (let i = 0; i < n; i++)` (optimized, no array) |
| `while condition` | `while (condition)` |
| `try / except / finally` | `try / catch / finally` |
| `break` / `continue` | `break` / `continue` |
| `pass` | (nothing) |
| `match / case` | `switch`-like chain (Python 3.10+) |

### Restricted in `render()`

Component `render()` methods are more restricted — only these statements are allowed:

- Variable assignments: `x = expression`
- Attribute assignments: `self.foo = value`
- `if`/`elif`/`else` blocks
- Return statements: `return ui.div(...)`

No `for`, `while`, or `try` blocks. Use `ui.For()` for iteration. `if`/`elif`/`else` blocks and ternary expressions both work:

```python
# If statements work — useful for conditional returns
def render(self, props=None):
    if self.state.get("loading"):
        return ui.div("Loading...")
    return ui.div("Content")

# Ternaries work too — terser for single-expression cases
def render(self, props=None):
    return ui.div("Loading...") if self.state.get("loading") else ui.div("Content")
```

```python
# Wrong — for loop in render()
def render(self, props=None):
    items = []
    for item in self.state.get("items", []):
        items.append(ui.li(item["text"]))
    return ui.ul(items)

# Right — ui.For()
def render(self, props=None):
    return ui.ul(
        ui.For(
            self.state.get("items", []),
            key=lambda i: i["id"],
            render=lambda i: ui.li(i["text"]),
        )
    )
```

Render-locals declared above the `return` work naturally inside `ui.For` / `ui.Grid` / `ui.LazyImage` callbacks and arguments — the codegen captures them so the mount machinery sees the same values render() saw:

```python
def render(self, props=None):
    tabs = props.get("tabs", [])           # render-local
    active = props.get("active_tab", "home")  # render-local
    return ui.div(
        ui.For(
            tabs,                              # captured
            key="id",
            render=lambda tab: ui.button(
                tab["label"],
                aria_selected=(tab["id"] == active),  # captured
                class_="tab is-active" if tab["id"] == active else "tab",
            ),
        ),
    )
```

Each render() refreshes the captured values, so updates from `self.set_state(...)` flow through correctly.

## Expressions

### Literals and collections

| Python | JavaScript |
|---|---|
| `None` | `null` |
| `True` / `False` | `true` / `false` |
| `42`, `3.14` | `42`, `3.14` |
| `"hello"` | `"hello"` |
| `f"count: {n}"` | `` `count: ${n}` `` |
| `[1, 2, 3]` | `[1, 2, 3]` |
| `(1, 2, 3)` | `[1, 2, 3]` (tuples become arrays) |
| `{"key": value}` | `{"key": value}` |
| `{**a, **b}` | `{...a, ...b}` |

### Operators

| Python | JavaScript |
|---|---|
| `+`, `-`, `*`, `/`, `%` | `+`, `-`, `*`, `/`, `%` |
| `a \| b` (dict merge) | `{...a, ...b}` |
| `==`, `!=` | `===`, `!==` |
| `<`, `<=`, `>`, `>=` | `<`, `<=`, `>`, `>=` |
| `is`, `is not` | `===`, `!==` |
| `in`, `not in` | `.includes()` |
| `and`, `or` | `&&`, `\|\|` |
| `not x` | `!x` |
| `-x`, `+x` | `-x`, `+x` |
| `a if cond else b` | `cond ? a : b` |

### Comprehensions (single generator only)

```python
[x * 2 for x in items if x > 0]      # list comp
{k: v for k, v in pairs}              # dict comp
{x for x in items}                     # set comp → new Set(...)
```

Multi-generator comprehensions are not supported.

### Lambda

```python
key=lambda item: item["id"]
```

Compiles to an arrow function: `(item) => item["id"]`.

### Walrus operator

```python
if (count := self.state.get("count", 0)) > 10:
    self.set_state({"is_large": True})
```

Works in statements. Not supported inside comprehensions or lambdas.

## Builtin functions

| Python | JavaScript |
|---|---|
| `len(x)` | `(x).length` |
| `str(x)` | `String(x)` |
| `int(x)` | `Math.trunc(Number(x))` |
| `float(x)` | `Number(x)` |
| `bool(x)` | `Boolean(x)` |
| `abs(x)` | `Math.abs(x)` |
| `min(...)` | `Math.min(...)` |
| `max(...)` | `Math.max(...)` |
| `round(x)` | `Math.round(x)` |
| `print(...)` | `console.log(...)` |
| `range(n)` | Materialized array (but `for i in range(n)` optimizes to C-style loop) |
| `sum(items)` | `.reduce((a, b) => a + b, 0)` |

## Name mapping

Python snake_case identifiers are converted to JavaScript camelCase:

```python
self.set_state(...)       → this.setState(...)
self.on_socket(...)       → this.onSocket(...)
self.call_action(...)     → this.callAction(...)
self.add_cleanup(...)     → this.addCleanup(...)
self.adopt_component(...) → this.adoptComponent(...)
```

User-defined method names follow the same rule: `on_counter_change` → `onCounterChange`.

## Special namespaces

### `imports.*` — declared JS modules

Access third-party JavaScript declared in `page(modules={...})`:

```python
# page.py
my_page = page(..., modules={"chart": "/vendor/chart.esm.js"})

# modules.py
class ChartModule(Module):
    def on_start(self):
        lib = imports.chart
        self.chart = lib.create(self.element)
```

Every alias used via `imports.*` must be declared in `modules={}` — missing aliases are a hard build error.

### `browser.*` — global scope

Access `globalThis.*` for browser APIs:

```python
class ScrollModule(Module):
    def on_start(self):
        self.width = browser.innerWidth
        browser.console.log("started")
```

### `dom.*` — Ragot DOM helpers

```python
dom.query(".selector")         # $(selector)
dom.query_all(".items")        # $$(selector)
dom.animate_in(el, "fade")     # animateIn(el, "fade")
dom.animate_out(el, "fade")    # animateOut(el, "fade")
```

## Timer units

Python timer methods use **seconds**. The codegen automatically converts to milliseconds:

```python
self.timeout(self.tick, 0.5)   # → this.timeout(() => this.tick(), 500)
self.interval(self.poll, 30)   # → this.interval(() => this.poll(), 30000)
```

## Method references as callbacks

When you pass `self.method` as a callback, it's automatically bound:

```python
self.timeout(self.tick, 1)     # → this.timeout(() => this.tick(), 1000)
self.on(el, "click", self.handle)  # → this.on(el, "click", (...args) => this.handle(...args))
```

## `__init__` (Module only)

Module `__init__` can contain normal compilable statements. The main restriction is that `self.state` and `self.screen` are runtime-owned:

```python
class MyModule(Module):
    def __init__(self, screen=None, state=None):
        super().__init__(screen=screen, state=state or {})
        self.draft = None
        self.config = {"mode": "compact"}
        if state and state.get("expanded"):
            self.open = True
```

`super().__init__()` calls are stripped. Direct assignment to `self.state` or `self.screen` raises an error.

## `env()` — environment variables

Access `SPRAG_PUBLIC_*` environment variables inlined at build time:

```python
api_url = env("SPRAG_PUBLIC_API_URL")
debug = env("SPRAG_PUBLIC_DEBUG", "false", cast=bool)
all_env = public_env()
```

## `join_url()` — URL composition

`join_url()` is the single URL helper for both runtimes. Import it from `sprag` and call it the same way in server code and in browser code:

```python
from sprag import join_url

href = join_url("/docs", "getting-started", "installation")
```

On the server it returns the composed path. In compiled browser code it calls a runtime helper that *also* prepends `window.__SPRAG_BASE__` — the deployment prefix derived at boot. That way one call site produces the right URL whether the app is hosted at the host root or under a path prefix like `https://example.com/project/`.

Use it for any internal `href`, `src`, redirect target, or `navigate(...)` argument that is not a static literal owned by the static-build HTML rewriter.

## What's NOT supported

These Python constructs will raise `JSCodegenError` at compile time:

| Construct | Alternative |
|---|---|
| Type annotations inside a method body: `x: int = 5` | Drop the annotation — `x = 5` works. (Module-level `RATE: float = 0.5` IS supported as a helper constant.) |
| `with` statement | Use explicit try/finally |
| `del x` | Assign `None` or use `store.delete()` |
| `assert x` | Use `if not x:` |
| Nested function/class defs *inside* a method | Lift to module scope — module-level `def`s compile to JS automatically |
| `for/else`, `while/else` | Remove the else branch |
| `yield` / `yield from` | Materialise eagerly |
| Set literals: `{1, 2, 3}` | Use list or set comprehension |
| `**` (power), `//` (floor div) | Use `Math.pow()`, `Math.floor()` |
| Bitwise ops (except `\|`) | Not supported |
| Multi-generator comprehensions | Use nested loops |
| `isinstance()` checks | Not supported in browser code |
| Subscript assignment: `self.state["k"] = v` | Use `self.set_state({...})` or `self.patch({...})` |
| `ui.For()` / `ui.Grid()` / `ui.LazyImage()` outside `render()` | Move the call directly into `render()` — helper methods aren't scanned for mount-point wiring |
| `f(*args)` — positional splat in a call | Pass arguments individually |
| `f(**kwargs)` — keyword splat in a call | Spread into a dict literal first: `f({**kwargs, ...})` and have the callee accept a dict |
| `def f(*args, **kwargs)` — splat parameters | Use explicit named parameters; positional/keyword variadics aren't lowered to JS |

Every error includes the source file, class name, line number, and a suggestion.

## Inspecting output

To see what your Python compiles to:

```bash
sprag inspect /counter --rebuild
```

This shows the generated JavaScript with comments mapping back to your Python source lines.
