---
title: CLI Reference
description: Complete reference for the `sprag` command-line interface.
order: 50
---

# CLI Reference

This page collects the full `sprag` CLI surface in one place. Command names and flags below mirror `sprag/dev/cli.py`.

## `sprag new`

Create a new SPRAG project.

**Usage**

```bash
sprag new <name> [--output-dir PATH] [--template default|bare|labs]
```

**Flags**

- `name` — project directory name to create
- `--output-dir` — parent directory for the new project
- `--template` — scaffold template; current options are `default`, `bare`, and `labs`

**Example**

```bash
sprag new myapp --template bare
```

## `sprag dev`

Build a local preview, watch the project for changes, and start the dev server.

**Usage**

```bash
sprag dev [static] [--port 8000] [--host 127.0.0.1] [--interval 1.0] [--server-mode wsgi|websocket]
```

**Flags**

- `static` — optional mode that builds and serves a pure static site preview
- `--port` — port to bind
- `--host` — host/interface to bind
- `--interval` — file-watch polling interval in seconds
- `--server-mode` — force `wsgi` or `websocket`; otherwise SPRAG resolves it from the app
- `--app` — explicit app import target
- `--project-root` — project root to load from
- `--output` — preview build directory used by the dev server

**Example**

```bash
sprag dev --host 0.0.0.0 --port 9000 --server-mode websocket
sprag dev static --port 9000
```

If you use websocket mode from a `spragkit` install, also install `gevent-websocket`.

`sprag dev static` serves only the generated static files, matching hosts such as GitHub Pages. It does not expose SPRAG action, websocket, or dev reload endpoints.

## `sprag build`

Build the app into a deployable artifact.

**Usage**

```bash
sprag build [static] [--output dist]
```

**Flags**

- `static` — optional mode that emits a pure static site instead of a full dist bundle
- `--output` — output directory
- `--app` — explicit app import target
- `--project-root` — project root to load from

**Examples**

```bash
sprag build
sprag build static --output dist
```

## `sprag pack`

Optimize an existing `dist/` directory for production deployment.

**Usage**

```bash
sprag pack [--dist dist] [--zip] [--dry-run] [--verbose]
```

**Flags**

- `--dist` — dist directory to optimize
- `--zip` — also create a ZIP archive
- `--dry-run` — preview changes without writing them
- `--verbose` — print detailed optimization logs
- `--skip-images` — skip image optimization
- `--skip-minify` — skip CSS/JS minification
- `--skip-bytecode` — skip Python bytecode compilation
- `--skip-gzip` — skip pre-gzip compression
- `--skip-fingerprint` — skip content-hash fingerprinting
- `--no-webp` — skip WebP generation
- `--no-srcset` — skip responsive image variants
- `--image-quality` — image compression quality
- `--image-max-width` — max width for generated image variants

**Example**

```bash
sprag pack --dist dist --zip --skip-images
```

## `sprag add route`

Scaffold a new route under `app/routes/`.

**Usage**

```bash
sprag add route <name> [--mode document|hybrid]
```

**Flags**

- `name` — route name such as `dashboard` or `admin/reports`
- `--mode` — route mode; `hybrid` is the default
- `--project-root` — project root to scaffold into

**Example**

```bash
sprag add route dashboard --mode hybrid
```

## `sprag add mount`

Scaffold a browser-owned mount under `app/mounts/`.

**Usage**

```bash
sprag add mount <name>
```

**Flags**

- `name` — mount name such as `admin/console`
- `--project-root` — project root to scaffold into

**Example**

```bash
sprag add mount dashboard
```

## `sprag add content`

Scaffold a markdown-backed content collection with index and article routes.

**Usage**

```bash
sprag add content <name>
```

**Flags**

- `name` — collection name such as `posts` or `docs/guides`
- `--project-root` — project root to scaffold into

**Example**

```bash
sprag add content posts
```

See [Content Collections](/docs/framework/content-collections) for the generated file layout and `static_paths` behavior.

## `sprag routes`

List discovered routes, mounts, actions, and schemas.

**Usage**

```bash
sprag routes
```

**Flags**

- `--app` — explicit app import target
- `--project-root` — project root to load from
- `--output` — output directory used for generated preview data

**Example**

```bash
sprag routes
```

## `sprag inspect`

Inspect the built output for a concrete route or mount path.

**Usage**

```bash
sprag inspect /path [--rebuild] [--open-files]
```

**Flags**

- `target` — concrete route or mount path, such as `/counter`
- `--rebuild` — rebuild preview output before inspecting
- `--open-files` — print generated file paths instead of dumping compiled source
- `--app` — explicit app import target
- `--project-root` — project root to load from
- `--output` — preview build directory to inspect

**Example**

```bash
sprag inspect /counter --rebuild
```

## `sprag doctor`

Run structural and build diagnostics against the current SPRAG app.

**Usage**

```bash
sprag doctor [--verbose]
```

**Flags**

- `--verbose` — include traceback details for failing checks
- `--app` — explicit app import target
- `--project-root` — project root to load from
- `--output` — preview output directory used during checks

**Example**

```bash
sprag doctor --verbose
```
