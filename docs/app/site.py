from sprag import ui


def card(title, href, description, meta=None):
    return ui.a(
        ui.h3(title),
        ui.p(description),
        ui.div(meta or "", class_="card-meta") if meta else None,
        href=href,
        class_="card",
    )


def blog_card(title, href, description, meta=None):
    return ui.a(
        ui.h3(title),
        ui.p(description),
        ui.div(meta, class_="blog-card-meta") if meta else None,
        href=href,
        class_="blog-card",
    )


def docs_sidebar(sections, current_path):
    section_elements = []
    for section in sections:
        items = [
            ui.li(ui.a(
                doc.title,
                href=doc.url_path,
                class_="sidebar-link is-active" if doc.url_path == current_path else "sidebar-link",
            ))
            for doc in section["items"]
        ]
        section_elements.append(
            ui.div(
                ui.div(section["label"], class_="sidebar-section-label"),
                ui.ul(items, class_="sidebar-section-links"),
                class_="sidebar-section",
            )
        )
    return ui.aside(*section_elements, class_="docs-sidebar")


def docs_shell(*, sections, current_path, kicker, title, description, body):
    sidebar = docs_sidebar(sections, current_path)
    return ui.div(
        sidebar,
        ui.div(
            ui.header(
                ui.div(kicker, class_="docs-kicker") if kicker else None,
                ui.h1(title),
                ui.p(description) if description else None,
                class_="docs-header",
            ),
            body,
            class_="docs-content",
        ),
        class_="docs-layout",
    )
