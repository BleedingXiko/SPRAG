from sprag import ui

from .urls import BLOG_BASE_URL, DOCS_BASE_URL


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


def docs_sidebar(sections, current_path, id_=None):
    # Nav links shown at top of sidebar on mobile (hidden on desktop via CSS)
    nav_links = ui.div(
        ui.a("Docs", href=DOCS_BASE_URL, class_="sidebar-nav-link"),
        ui.a("Blog", href=BLOG_BASE_URL, class_="sidebar-nav-link"),
        ui.a("GitHub", href="https://github.com/BleedingXiko/SPRAG",
             target="_blank", class_="sidebar-nav-link"),
        class_="sidebar-nav",
    )

    section_elements = []
    for section in sections:
        items = [
            ui.li(ui.a(
                doc["title"],
                href=doc["url_path"],
                class_="sidebar-link is-active" if doc["url_path"] == current_path else "sidebar-link",
            ))
            for doc in section["items"]
        ]
        section_elements.append(
            ui.details(
                ui.summary(section["label"], class_="sidebar-section-header"),
                ui.ul(items, class_="sidebar-section-links"),
                class_="sidebar-section",
                open=True,
            )
        )
    return ui.aside(nav_links, *section_elements, class_="docs-sidebar", id=id_)


def docs_shell(*, sections, current_path, kicker, title, description, body):
    sidebar = docs_sidebar(sections, current_path, id_="docs-sidebar")
    toggle_svg = ui.HTML(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 6h16M4 12h16M4 18h16"/></svg>'
    )
    return ui.div(
        sidebar,
        ui.div(
            ui.div(class_="sidebar-backdrop",
                   onclick="document.getElementById('docs-layout').classList.remove('sidebar-toggled')"),
            ui.button(
                toggle_svg,
                class_="sidebar-toggle",
                aria_label="Menu",
                onclick="document.getElementById('docs-layout').classList.toggle('sidebar-toggled')",
            ),
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
        id="docs-layout",
    )
