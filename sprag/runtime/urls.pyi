"""Type stubs for sprag.runtime.urls."""

from typing import Optional


def join_url(base_url: str = ..., *parts: object, trailing_slash: bool = ...) -> str:
    """Join URL path parts without losing external schemes or anchors."""
    ...
def relative_url(document_path: Optional[str], target_url: str) -> str: ...
def relativize_html_urls(html: str, document_path: Optional[str]) -> str: ...
