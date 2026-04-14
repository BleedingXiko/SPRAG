from sprag import Controller

from app.content import docs_by_section, docs_collection


class DocsIndexController(Controller):
    route = "/docs"

    def load(self):
        return {
            "__sprag_meta__": {"title": "Documentation"},
            "sections": docs_by_section(),
            "docs": docs_collection(),
        }
