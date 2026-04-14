from functools import lru_cache
from pathlib import Path

from sprag import load_markdown_tree


CONTENT_ROOT = Path(__file__).resolve().parent / "content"

SECTION_ORDER = ["", "getting-started", "framework", "specter", "ragot", "guides"]
SECTION_LABELS = {
    "": "Overview",
    "getting-started": "Getting Started",
    "framework": "Framework",
    "specter": "Specter",
    "ragot": "Ragot",
    "guides": "Guides",
}


@lru_cache(maxsize=1)
def docs_collection():
    return load_markdown_tree(CONTENT_ROOT / "docs", base_url="/docs")


@lru_cache(maxsize=1)
def blog_collection():
    posts = load_markdown_tree(CONTENT_ROOT / "blog", base_url="/blog")
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
