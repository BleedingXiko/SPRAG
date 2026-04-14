---
title: Introducing SPRAG
description: A full-stack Python framework where one codebase powers two runtimes — server and browser.
date: 2026-04-14
---

# Introducing SPRAG

SPRAG is a full-stack Python web framework. You write Python for everything — server logic, browser UI components, browser behavior, and shared state. No JavaScript. No separate build chain.

## Two runtimes, one language

The idea is simple: Python classes that subclass `Controller` or `Service` run on the server under Specter (gevent/Flask). Python classes that subclass `Component` or `Module` compile to Ragot ESM JavaScript at build time. Same patterns, same lifecycle hooks, same state API — different runtimes.

This means one `.py` file can define the server action that handles a form submission *and* the browser component that renders the form. Your IDE works on both. Your mental model is one thing.

## Try it

```bash
pip install sprag
sprag new hello
cd hello
sprag dev
```

Open `http://localhost:8000`, click the counter, and read the source. That's SPRAG.

## Learn more

- [Getting Started](/docs/getting-started/installation) — prerequisites and setup
- [First App](/docs/getting-started/first-app) — walk through the counter demo
- [Two Runtimes](/docs/framework/two-runtimes) — the core concept explained
