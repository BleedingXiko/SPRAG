---
title: Installation
description: Prerequisites and setup for SPRAG.
order: 1
---

# Installation

## Prerequisites

- **Python 3.9+**
- **pip** — or any Python package manager (uv, poetry, etc.)

## Install SPRAG

```bash
pip install spragkit
```

### Optional: websocket mode

If you plan to use SPRAG's realtime layer in development, also install:

```bash
pip install gevent-websocket
```

Production `sprag build` bundles handle this dependency for you when the app runs in websocket mode.

Or install from source during pre-alpha:

```bash
git clone https://github.com/BleedingXiko/SPRAG.git
cd SPRAG
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Verify

```bash
sprag --version
```

Then scaffold and run a project to confirm everything works:

```bash
sprag new hello
cd hello
sprag dev
```

Or scaffold into the current directory (useful if you already created the directory and a venv):

```bash
mkdir hello && cd hello
python -m venv .venv && source .venv/bin/activate
pip install spragkit
sprag new .
sprag dev
```

Open `http://localhost:8000` in your browser. You should see the default SPRAG app with a working counter.
