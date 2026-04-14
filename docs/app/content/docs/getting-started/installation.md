---
title: Installation
description: Prerequisites and setup for SPRAG.
order: 1
---

# Installation

## Prerequisites

- **Python 3.11+** — SPRAG uses modern Python features throughout
- **pip** — or any Python package manager (uv, poetry, etc.)
- **gevent** — the server runtime uses gevent for cooperative concurrency

## Install SPRAG

```bash
pip install spragkit
```

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

Open `http://localhost:8000` in your browser. You should see the default SPRAG app with a working counter.
