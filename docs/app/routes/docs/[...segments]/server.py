from sprag import Controller

from app.content import docs_by_section, find_doc, slim_doc, slim_nav_sections


class DocsArticleController(Controller):
    route = "/docs/[...segments]"

    def load(self):
        segments = tuple(self.request.params.get("segments") or [])
        doc = find_doc(segments)
        title = doc.title if doc else "Documentation"
        return {
            "__sprag_meta__": {"title": title},
            "doc": slim_doc(doc) if doc else None,
            "sections": slim_nav_sections(docs_by_section()),
            "current_path": "/docs/" + "/".join(segments),
        }
