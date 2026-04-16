---
title: Codegen
description: What Python constructs compile to JavaScript — the complete reference for browser-side code.
order: 17
---

# Codegen

When you write `Component` and `Module` subclasses in Python, `sprag build` compiles them to Ragot ESM JavaScript. This page documents exactly what Python constructs are supported, what they compile to, and what will raise an error.

## The rule

If it's in a `Component` or `Module` subclass, it compiles to JS. Everything else runs as Python on the server. The compile boundary is at the class level — there's no ambiguity.

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
if (result := self.call_action("check", {})):
    self.set_state({"result": result})
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

Module `__init__` supports field assignments only:

```python
class MyModule(Module):
    def __init__(self, screen=None, state=None):
        super().__init__(screen=screen, state=state or {})
        self.draft = None
        self.child = ChildModule()
```

`super().__init__()` calls are stripped. Other statements raise an error.

## `env()` — environment variables

Access `SPRAG_PUBLIC_*` environment variables inlined at build time:

```python
api_url = env("SPRAG_PUBLIC_API_URL")
debug = env("SPRAG_PUBLIC_DEBUG", "false", cast=bool)
all_env = public_env()
```

## What's NOT supported

These Python constructs will raise `JSCodegenError` at compile time:

| Construct | Alternative |
|---|---|
| Type annotations: `x: int = 5` | Use `x = 5` |
| `with` statement | Use explicit try/finally |
| `del x` | Assign `None` or use `store.delete()` |
| `assert x` | Use `if not x:` |
| Nested function/class defs | Lift to module scope |
| `for/else`, `while/else` | Remove the else branch |
| `yield` / `yield from` | Materialise eagerly |
| Set literals: `{1, 2, 3}` | Use list or set comprehension |
| `**` (power), `//` (floor div) | Use `Math.pow()`, `Math.floor()` |
| Bitwise ops (except `\|`) | Not supported |
| Multi-generator comprehensions | Use nested loops |
| `isinstance()` checks | Not supported in browser code |

Every error includes the source file, class name, line number, and a suggestion.

## Inspecting output

To see what your Python compiles to:

```bash
sprag inspect /counter --rebuild
```

This shows the generated JavaScript with comments mapping back to your Python source lines.
