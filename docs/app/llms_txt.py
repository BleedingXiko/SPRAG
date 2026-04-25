"""Generate /llms.txt and /llms-full.txt from the docs content tree.

Follows the https://llmstxt.org convention:
  - llms.txt       — index: title + one-line description + URL per doc, grouped
  - llms-full.txt  — full concat: every doc's raw markdown body, separated

Called at app boot; writes into docs/public/ which sprag build copies verbatim
to the static output root.
"""

from __future__ import annotations

import re
from pathlib import Path

from sprag import join_url

from app.content import SECTION_LABELS, SECTION_ORDER, docs_by_section, docs_collection


_PROJECT_NAME = "SPRAG"
_PROJECT_TAGLINE = (
    "Full-stack Python web framework. Server + browser UI + state in one language. "
    "Python Component/Module classes compile to Ragot ESM JavaScript at build time; "
    "server runs under Specter (gevent/Flask)."
)


def write_llms_txt(public_dir: Path, *, site_url: str | None = None) -> None:
    """Render both llms.txt and llms-full.txt into ``public_dir``."""
    public_dir = Path(public_dir)
    public_dir.mkdir(parents=True, exist_ok=True)

    base = site_url or ""
    (public_dir / "llms.txt").write_text(_render_index(base), encoding="utf-8")
    (public_dir / "llms-full.txt").write_text(_render_full(base), encoding="utf-8")


def _render_index(base: str) -> str:
    lines = [
        f"# {_PROJECT_NAME}",
        "",
        f"> {_PROJECT_TAGLINE}",
        "",
    ]
    for section in docs_by_section():
        label = section["label"]
        if not section["items"]:
            continue
        lines.append(f"## {label}")
        lines.append("")
        for doc in section["items"]:
            desc = (doc.description or "").strip().replace("\n", " ")
            suffix = f": {desc}" if desc else ""
            url = join_url(base, doc.url_path) if base else doc.url_path
            lines.append(f"- [{doc.title}]({url}){suffix}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_full(base: str) -> str:
    parts = [
        f"# {_PROJECT_NAME}",
        "",
        _PROJECT_TAGLINE,
        "",
    ]

    docs = docs_collection()
    grouped: dict[str, list] = {}
    for doc in docs:
        key = doc.path_parts[0] if len(doc.path_parts) > 1 else ""
        grouped.setdefault(key, []).append(doc)

    for key in SECTION_ORDER:
        items = grouped.get(key)
        if not items:
            continue
        label = SECTION_LABELS.get(key, key.replace("-", " ").title())
        parts.append("")
        parts.append(f"# {label}")
        parts.append("")
        for doc in items:
            parts.append(f"## {doc.title}")
            source = join_url(base, doc.url_path) if base else doc.url_path
            parts.append(f"<!-- source: {source} -->")
            parts.append("")
            parts.append(_clean_body(doc.body))
            parts.append("")

    text = "\n".join(parts)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.rstrip() + "\n"


def _clean_body(body: str) -> str:
    """Trim the markdown body for LLM consumption without changing semantics."""
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    # Drop a leading H1 — the section + doc title already head the block
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx < len(lines) and lines[idx].lstrip().startswith("# "):
        idx += 1
    trimmed = lines[idx:]

    return "\n".join(trimmed).strip()
