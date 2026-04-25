from sprag import Component, ui

from app.urls import BLOG_BASE_URL


class BlogArticlePage(Component):
    def render(self, props=None):
        props = props or self.props
        post = props.get("post")
        return ui.div(
            ui.header(
                ui.a("Back to blog", href=BLOG_BASE_URL, class_="article-back"),
                ui.h1(post["title"] if post else "Post not found"),
                ui.p(post["description"] if post else "This blog post does not exist."),
                ui.div(post["metadata"].get("date", ""), class_="article-meta") if post else None,
                class_="article-header",
            ),
            ui.article(ui.HTML(post["html"]), class_="prose") if post else ui.p("No content."),
            class_="article-page",
        )
