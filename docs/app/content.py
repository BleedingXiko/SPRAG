from functools import lru_cache
from pathlib import Path

from sprag import load_markdown_tree

from .urls import BLOG_BASE_URL, DOCS_BASE_URL


CONTENT_ROOT = Path(__file__).resolve().parent / "content"

SECTION_ORDER = ["", "getting-started", "framework", "reference", "specter", "ragot", "guides"]
SECTION_LABELS = {
    "": "Overview",
    "getting-started": "Getting Started",
    "framework": "Framework",
    "reference": "Reference",
    "specter": "Specter",
    "ragot": "Ragot",
    "guides": "Guides",
}


@lru_cache(maxsize=1)
def docs_collection():
    return load_markdown_tree(CONTENT_ROOT / "docs", base_url=DOCS_BASE_URL)


@lru_cache(maxsize=1)
def blog_collection():
    posts = load_markdown_tree(CONTENT_ROOT / "blog", base_url=BLOG_BASE_URL)
    return sorted(
        posts,
        key=lambda post: str(post.metadata.get("date", "")),
        reverse=True,
    )


def docs_by_section():
    docs = docs_collection()
    grouped = {}
    for doc in docs:
        key = doc.path_parts[0] if len(doc.path_parts) > 1 else ""
        grouped.setdefault(key, []).append(doc)
    sections = []
    for key in SECTION_ORDER:
        if key in grouped:
            sections.append({
                "label": SECTION_LABELS.get(key, key.replace("-", " ").title()),
                "items": grouped[key],
            })
    return sections


def slim_doc(doc):
    """Strip server-only fields before sending a doc to the browser payload."""
    return {
        "title": doc.title,
        "description": doc.description,
        "excerpt": doc.excerpt,
        "html": doc.html,
        "url_path": doc.url_path,
        "slug": doc.slug,
        "path_parts": list(doc.path_parts),
        "metadata": doc.metadata,
    }


def slim_doc_card(doc):
    """Minimal doc shape for listing pages — title, url_path, description, metadata."""
    return {
        "title": doc.title,
        "description": doc.description,
        "url_path": doc.url_path,
        "metadata": doc.metadata,
    }


def slim_nav_sections(sections):
    """Strip doc content from nav items — sidebar only needs title + url_path."""
    return [
        {
            "label": section["label"],
            "items": [{"title": doc.title, "url_path": doc.url_path} for doc in section["items"]],
        }
        for section in sections
    ]


def docs_static_paths():
    return [{"segments": list(doc.path_parts)} for doc in docs_collection()]


def blog_static_paths():
    return [{"slug": post.slug} for post in blog_collection()]


def find_doc(segments):
    wanted = tuple(part for part in (segments or []) if part)
    for doc in docs_collection():
        if doc.path_parts == wanted:
            return doc
    return None


def find_post(slug):
    wanted = str(slug).strip("/")
    for post in blog_collection():
        if post.slug == wanted:
            return post
    return None
