"""Generate /static/search-index.json from the docs content tree.

Written into ``app/static/`` at app boot so it's served as a normal static
asset by the dev server and copied to ``dist/static/`` by the build.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.content import SECTION_LABELS, docs_collection


_HEADING_RE = re.compile(r"^#{1,4}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_HTML_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def write_search_index(static_dir: Path) -> None:
    static_dir = Path(static_dir)
    static_dir.mkdir(parents=True, exist_ok=True)

    docs = []
    for doc in docs_collection():
        section_key = doc.path_parts[0] if len(doc.path_parts) > 1 else ""
        section = SECTION_LABELS.get(
            section_key, section_key.replace("-", " ").title() or "Overview"
        )
        docs.append({
            "title": doc.title,
            "url": doc.url_path,
            "section": section,
            "description": (doc.description or "").strip(),
            "headings": _extract_headings(doc.body),
            "body": _strip_markdown(doc.body).lower(),
        })

    payload = {"docs": docs}
    (static_dir / "search-index.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _extract_headings(body: str) -> list[str]:
    # Skip the leading H1 (it's the doc title; already a separate field).
    found = _HEADING_RE.findall(body)
    return [h.strip() for h in found[1:] if h.strip()]


def _strip_markdown(body: str) -> str:
    text = _CODE_FENCE_RE.sub(" ", body)
    text = _INLINE_CODE_RE.sub(" ", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _HTML_RE.sub(" ", text)
    text = _HEADING_RE.sub(r"\1", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()
