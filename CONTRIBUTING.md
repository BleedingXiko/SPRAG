# Contributing to SPRAG

SPRAG is still pre-alpha, so the most helpful contributions are the ones that make the repo clearer, safer to refactor, and easier to validate.

## Good First Contribution Areas

- fix stale docs, comments, or examples after refactors
- add or tighten smoke tests for scaffold, build, render, `doctor`, and `inspect`
- improve packaging and release verification
- reduce accidental public API surface or clean up internal boundaries

## Ground Rules

- keep the runtime and dev package boundary honest
- prefer `from sprag import ...` for public examples
- avoid reintroducing root-level compatibility wrappers unless there is a concrete release need
- keep changes focused; small, reviewable PRs are preferred

## Local Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Core Validation

Use these checks before opening a PR:

```bash
.venv/bin/python -m sprag --version
.venv/bin/python -m sprag.dev.cli new sprag_contrib_check --output-dir .sandbox
cd .sandbox/sprag_contrib_check
../../.venv/bin/python -m sprag build
../../.venv/bin/python -m sprag doctor
../../.venv/bin/python -m sprag inspect /counter --open-files
```

If you touch packaging, also verify the build metadata and distribution contents.

## PR Notes

- explain the user-visible or maintainer-visible reason for the change
- mention the validation you ran
- call out any intentional follow-up work you left for later

