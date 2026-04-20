"""Markdown + frontmatter helpers for static docs and blog surfaces."""

from __future__ import annotations

import ast
import html
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ContentDocument:
    source_path: Path
    url_path: str
    slug: str
    path_parts: tuple[str, ...]
    metadata: dict = field(default_factory=dict)
    body: str = ""
    html: str = ""
    excerpt: str = ""

    @property
    def title(self) -> str:
        return self.metadata.get("title") or self.slug.replace("-", " ").title()

    @property
    def description(self) -> str:
        return self.metadata.get("description") or self.excerpt

    @property
    def order(self) -> int:
        value = self.metadata.get("order", 9999)
        try:
            return float(value)
        except (TypeError, ValueError):
            return 9999.0


def load_markdown_document(path, *, url_path=None, slug=None, path_parts=None) -> ContentDocument:
    source_path = Path(path)
    metadata, body = _split_frontmatter(source_path.read_text(encoding="utf-8"))
    resolved_slug = slug or slugify(source_path.stem)
    resolved_parts = tuple(path_parts or (resolved_slug,))
    resolved_url = url_path or "/" + "/".join(resolved_parts)
    html_body = render_markdown(body)
    excerpt = _extract_excerpt(body)
    return ContentDocument(
        source_path=source_path,
        url_path=_normalize_url_path(resolved_url),
        slug=resolved_slug,
        path_parts=resolved_parts,
        metadata=metadata,
        body=body,
        html=html_body,
        excerpt=excerpt,
    )


def load_markdown_tree(root, *, base_url="/") -> list[ContentDocument]:
    root_path = Path(root)
    docs = []
    for source_path in sorted(root_path.rglob("*.md")):
        relative = source_path.relative_to(root_path).with_suffix("")
        raw_parts = list(relative.parts)
        if raw_parts and raw_parts[-1] == "index":
            raw_parts = raw_parts[:-1]
        slug = slugify(raw_parts[-1] if raw_parts else source_path.stem)
        url_parts = tuple(slugify(part) for part in raw_parts if part)
        docs.append(
            load_markdown_document(
                source_path,
                url_path=_join_url(base_url, *url_parts),
                slug=slug,
                path_parts=url_parts,
            )
        )
    return sorted(docs, key=lambda doc: (doc.order, doc.url_path))


def slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", str(value).strip().lower()).strip("-")
    return text or "index"


def render_markdown(markdown_text: str) -> str:
    lines = markdown_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue
        if stripped.startswith("```"):
            language = stripped[3:].strip()
            index += 1
            code_lines = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            class_attr = f' class="language-{html.escape(language, quote=True)}"' if language else ""
            blocks.append(
                f"<pre><code{class_attr}>{html.escape(chr(10).join(code_lines))}</code></pre>"
            )
            continue
        if _is_table_start(lines, index):
            table_html, index = _render_table(lines, index)
            blocks.append(table_html)
            continue
        if stripped.startswith("#"):
            level = min(len(stripped) - len(stripped.lstrip("#")), 6)
            heading = stripped[level:].strip()
            blocks.append(f"<h{level}>{_render_inline(heading)}</h{level}>")
            index += 1
            continue
        if stripped.startswith("> "):
            quote_lines = []
            while index < len(lines) and lines[index].strip().startswith("> "):
                quote_lines.append(lines[index].strip()[2:])
                index += 1
            quote_html = "".join(f"<p>{_render_inline(part)}</p>" for part in quote_lines if part)
            blocks.append(f"<blockquote>{quote_html}</blockquote>")
            continue
        if stripped.startswith(("- ", "* ")):
            items = []
            while index < len(lines) and lines[index].strip().startswith(("- ", "* ")):
                items.append(lines[index].strip()[2:])
                index += 1
            items_html = "".join(f"<li>{_render_inline(item)}</li>" for item in items)
            blocks.append(f"<ul>{items_html}</ul>")
            continue

        paragraph_lines = []
        while index < len(lines):
            current = lines[index].strip()
            if not current:
                break
            if current.startswith(("```", "#", "> ", "- ", "* ")):
                break
            paragraph_lines.append(current)
            index += 1
        blocks.append(f"<p>{_render_inline(' '.join(paragraph_lines))}</p>")
    return "\n".join(blocks)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    source = text.replace("\r\n", "\n").replace("\r", "\n")
    if not source.startswith("---\n"):
        return {}, source.strip()
    end = source.find("\n---\n", 4)
    if end == -1:
        return {}, source.strip()
    raw_meta = source[4:end]
    body = source[end + 5 :]
    return _parse_frontmatter(raw_meta), body.strip()


def _parse_frontmatter(block: str) -> dict:
    metadata = {}
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        metadata[key.strip()] = _parse_frontmatter_value(raw_value.strip())
    return metadata


def _parse_frontmatter_value(raw_value: str):
    if not raw_value:
        return ""
    if raw_value.lower() in {"true", "false"}:
        return raw_value.lower() == "true"
    if raw_value.lower() in {"null", "none"}:
        return None
    if raw_value.startswith("[") and raw_value.endswith("]"):
        try:
            parsed = ast.literal_eval(raw_value)
        except Exception:
            parsed = [part.strip() for part in raw_value[1:-1].split(",") if part.strip()]
        return list(parsed)
    if (raw_value.startswith('"') and raw_value.endswith('"')) or (
        raw_value.startswith("'") and raw_value.endswith("'")
    ):
        return raw_value[1:-1]
    try:
        return int(raw_value)
    except ValueError:
        pass
    try:
        return float(raw_value)
    except ValueError:
        pass
    return raw_value


def _is_table_start(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    header = lines[index].strip()
    separator = lines[index + 1].strip()
    if "|" not in header or "|" not in separator:
        return False
    separator_cells = [cell.strip() for cell in separator.strip("|").split("|")]
    if not separator_cells or any(not cell for cell in separator_cells):
        return False
    return all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator_cells)


def _render_table(lines: list[str], index: int) -> tuple[str, int]:
    header_cells = _split_table_row(lines[index])
    index += 2  # header + separator
    body_rows = []
    while index < len(lines):
        current = lines[index].strip()
        if not current or "|" not in current:
            break
        body_rows.append(_split_table_row(lines[index]))
        index += 1

    header_html = "".join(f"<th>{_render_inline(cell)}</th>" for cell in header_cells)
    body_html = "".join(
        "<tr>" + "".join(f"<td>{_render_inline(cell)}</td>" for cell in row) + "</tr>"
        for row in body_rows
    )
    table = f"<table><thead><tr>{header_html}</tr></thead>"
    if body_rows:
        table += f"<tbody>{body_html}</tbody>"
    table += "</table>"
    return table, index


def _split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _extract_excerpt(markdown_text: str) -> str:
    for paragraph in markdown_text.split("\n\n"):
        cleaned = paragraph.strip()
        if not cleaned or cleaned.startswith(("#", "```", "> ", "- ", "* ")):
            continue
        text = re.sub(r"`([^`]+)`", r"\1", cleaned)
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)
        text = text.replace("**", "").replace("*", "")
        return text.strip()
    return ""


def _render_inline(text: str) -> str:
    placeholders = {}

    def stash(value: str) -> str:
        key = f"__SPRAG_INLINE_{len(placeholders)}__"
        placeholders[key] = value
        return key

    escaped = html.escape(text)
    escaped = re.sub(
        r"`([^`]+)`",
        lambda match: stash(f"<code>{match.group(1)}</code>"),
        escaped,
    )
    escaped = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda match: stash(
            f'<a href="{html.escape(match.group(2), quote=True)}">{match.group(1)}</a>'
        ),
        escaped,
    )
    def _resolve(s: str) -> str:
        for k, v in placeholders.items():
            s = s.replace(k, v)
        return s

    escaped = re.sub(r"\*\*([^*]+)\*\*", lambda match: stash(f"<strong>{_resolve(match.group(1))}</strong>"), escaped)
    escaped = re.sub(r"\*([^*]+)\*", lambda match: stash(f"<em>{_resolve(match.group(1))}</em>"), escaped)

    for key, value in placeholders.items():
        escaped = escaped.replace(key, value)
    return escaped


def _join_url(base_url: str, *parts: str) -> str:
    clean_parts = [part.strip("/") for part in parts if part and part.strip("/")]
    prefix = "/" if not base_url else "/" + base_url.strip("/")
    if not clean_parts:
        return prefix
    return prefix.rstrip("/") + "/" + "/".join(clean_parts)


def _normalize_url_path(path: str) -> str:
    if not path or path == "/":
        return "/"
    return "/" + path.strip("/")
