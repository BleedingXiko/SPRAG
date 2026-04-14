---
title: What is SPRAG
description: One Python codebase, two runtimes — server and browser — with no JavaScript to write.
order: 0
---

# What is SPRAG

SPRAG is a full-stack Python web framework. You write Python for everything — server logic, browser UI, browser behavior, and shared state. At build time, your browser-side Python classes compile to JavaScript. At runtime, the server side runs under Specter (Python/gevent/Flask).

## The pitch

1. **One language.** Define server controllers and browser components in the same Python file. No context switching, no separate build chain.
2. **Two runtimes.** Server classes run as Python under Specter. Browser classes (`Component`, `Module`) compile to Ragot ESM JavaScript at `sprag build` time.
3. **Zero JS to write.** UI factories (`ui.div`, `ui.button`, ...), event handling, state management, sockets, uploads — all authored in Python and compiled for you.

## Quick tour

```bash
# Create a new project
sprag new myapp

# Start the dev server (rebuilds on file changes)
cd myapp && sprag dev

# Open http://localhost:8000 — click the counter
```

Edit `app/routes/counter/server.py`, save, and the browser updates. That's the full loop.

## Where to go next

- [Installation](/docs/getting-started/installation) — prerequisites and setup
- [First App](/docs/getting-started/first-app) — walk through the counter demo end to end
- [Two Runtimes](/docs/framework/two-runtimes) — the core concept behind SPRAG
