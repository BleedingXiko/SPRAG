from sprag import Controller

from app.content import docs_by_section, docs_collection, slim_doc_card, slim_nav_sections


class DocsIndexController(Controller):
    route = "/docs"

    def load(self):
        return {
            "__sprag_meta__": {"title": "Documentation"},
            "sections": slim_nav_sections(docs_by_section()),
            "docs": [slim_doc_card(doc) for doc in docs_collection()],
        }
