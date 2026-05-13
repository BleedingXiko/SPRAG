"""Type stubs for sprag.runtime.content."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union


@dataclass(frozen=True)
class ContentDocument:
    """Markdown content document.

    Use ``metadata`` for frontmatter, ``body`` for source markdown, ``html``
    for rendered output, and ``url_path``/``slug``/``path_parts`` for routing.
    Strip server-only fields before returning it from ``load()``.
    """

    source_path: Path
    url_path: str
    slug: str
    path_parts: tuple[str, ...]
    metadata: dict[str, Any] = ...
    body: str = ...
    html: str = ...
    excerpt: str = ...

    @property
    def title(self) -> str: ...
    @property
    def description(self) -> str: ...
    @property
    def order(self) -> float: ...


def load_markdown_document(
    path: Union[str, Path],
    *,
    url_path: Optional[str] = ...,
    slug: Optional[str] = ...,
    path_parts: Optional[tuple[str, ...]] = ...,
) -> ContentDocument:
    """Load one Markdown file into a ``ContentDocument``."""
    ...
def load_markdown_tree(root: Union[str, Path], *, base_url: str = ...) -> list[ContentDocument]:
    """Load every Markdown file under ``root`` as sorted content documents."""
    ...
def slugify(value: str) -> str:
    """Convert text into a URL-safe slug."""
    ...
def render_markdown(markdown_text: str) -> str:
    """Render Markdown text to HTML."""
    ...
