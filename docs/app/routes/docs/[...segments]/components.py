from sprag import Component, ui

from app.site import docs_shell


class DocsArticlePage(Component):
    def render(self, props=None):
        props = props or self.props
        doc = props.get("doc")
        sections = props.get("sections", [])
        current_path = props.get("current_path", "")
        section_label = doc.path_parts[0].replace("-", " ").title() if doc and len(doc.path_parts) > 1 else "Docs"
        return docs_shell(
            sections=sections,
            current_path=current_path,
            kicker=section_label if doc else None,
            title=doc.title if doc else "Page not found",
            description=doc.description if doc else "This docs page does not exist.",
            body=ui.article(ui.HTML(doc.html), class_="prose") if doc else ui.p("No content."),
        )
