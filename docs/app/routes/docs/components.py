from sprag import Component, ui

from app.site import card, docs_shell


class DocsIndexPage(Component):
    def render(self, props=None):
        props = props or self.props
        sections = props.get("sections", [])
        docs = props.get("docs", [])
        doc_cards = [
            card(doc["title"], doc["url_path"], doc["description"])
            for doc in docs
        ]
        return docs_shell(
            sections=sections,
            current_path="/docs",
            kicker="Documentation",
            title="SPRAG Documentation",
            description="Learn how to build full-stack Python web apps with SPRAG.",
            body=ui.div(doc_cards, class_="card-grid"),
        )
