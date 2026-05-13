"""URL helpers shared by SPRAG content, shells, and static builds."""

from __future__ import annotations

import posixpath
import re
from urllib.parse import urlsplit, urlunsplit


_URL_ATTR_RE = re.compile(
    r'(?P<prefix>\b(?:href|src|action)=)(?P<quote>["\'])(?P<url>/[^"\']*)(?P=quote)'
)


def join_url(base_url: str = "/", *parts: object, trailing_slash: bool = False) -> str:
    """Join URL path parts without losing external schemes or anchors."""
    clean_parts = [str(part).strip("/") for part in parts if part is not None and str(part).strip("/")]
    base = str(base_url or "/").strip()

    if _is_external_base(base):
        result = _join_external_base(base, clean_parts)
    else:
        prefix = "/" if not base else "/" + base.strip("/")
        result = prefix if not clean_parts else prefix.rstrip("/") + "/" + "/".join(clean_parts)

    if trailing_slash and not result.endswith("/"):
        result += "/"
    return result


def relative_url(document_path: str | None, target_url: str) -> str:
    """Return ``target_url`` relative to ``document_path`` when it is root-relative."""
    if document_path is None or not _is_root_relative_url(target_url):
        return target_url
    if target_url == "/":
        clean_document = str(document_path).strip("/")
        if not clean_document:
            return "./"
        depth = clean_document.count("/") + 1
        return "../" * depth
    if not document_path or document_path == "/":
        return target_url.lstrip("/")

    clean_target = target_url.lstrip("/")
    clean_document = str(document_path).strip("/")
    return posixpath.relpath(clean_target, start=clean_document)


def relativize_html_urls(html: str, document_path: str | None) -> str:
    """Rewrite root-relative href/src/action attributes for static output."""
    if not document_path:
        return html

    def _rewrite(match: re.Match) -> str:
        target = match.group("url")
        if not _is_root_relative_url(target):
            return match.group(0)
        return (
            f'{match.group("prefix")}{match.group("quote")}'
            f'{relative_url(document_path, target)}'
            f'{match.group("quote")}'
        )

    return _URL_ATTR_RE.sub(_rewrite, html)


def _join_external_base(base: str, parts: list[str]) -> str:
    split = urlsplit(base)
    base_path = split.path or "/"
    if parts:
        path = join_url(base_path, *parts)
    else:
        path = base_path if base_path.startswith("/") else f"/{base_path}"
    return urlunsplit((split.scheme, split.netloc, path, split.query, split.fragment))


def _is_external_base(value: str) -> bool:
    split = urlsplit(value)
    return bool(split.scheme in {"http", "https"} and split.netloc) or value.startswith("//")


def _is_root_relative_url(value: str) -> bool:
    return value.startswith("/") and not value.startswith("//")
