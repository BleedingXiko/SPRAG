# SPRAG Project Instructions

Use SPRAG's Python-first split intentionally:

- Server code lives in `app/routes/**/server.py` and uses `Controller`, `@action`, and services.
- Browser code lives in `app/routes/**/web.py`, `components.py`, and `modules.py` and uses `Screen`, `Component`, `Module`, `ui`, and `dom`.
- Route manifests live in `app/routes/**/page.py`; client app mounts live in `app/mounts/**/mount.py`.
- Shared stores are declared once with `store("name", initial={...})`, usually in `app/stores.py`.

Keep the runtime boundary clear:

- Put authoritative data loading in `Controller.load()`.
- Put mutations and form/file actions behind `@action`.
- Call actions from browser `Module` code with `self.call_action("action_name", payload)`.
- Use `Controller.emit_socket(...)` or `emit_socket_refetch(...)` for server-to-browser updates.
- Never call browser-only `Component`, `Module`, `dom`, `browser`, or `imports` helpers from plain server Python.

SPRAG UI idioms:

- Return `ui.*` trees from `Screen.render()` and `Component.render()`.
- Use `ui.For(...)`, `ui.Grid(...)`, and `ui.LazyImage(...)` for keyed lists, grids, and lazy images.
- Use `class_=` for CSS classes because `class` is reserved in Python.
- Use `ref(".selector")` on browser classes when a DOM element is needed after mount.

Prefer small, framework-native changes over custom plumbing. If adding a new hybrid route, create a `Controller`, a `Screen`, optional `Component`/`Module`, and a `page(...)` manifest.
