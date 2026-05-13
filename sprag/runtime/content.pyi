"""Type stubs for sprag.runtime.content."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union


@dataclass(frozen=True)
class ContentDocument:
    """Loaded Markdown document with frontmatter, URL metadata, and rendered HTML."""

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
) -> ContentDocument: ...
def load_markdown_tree(root: Union[str, Path], *, base_url: str = ...) -> list[ContentDocument]: ...
def slugify(value: str) -> str: ...
def render_markdown(markdown_text: str) -> str: ...
