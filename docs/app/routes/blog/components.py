from sprag import Component, ui

from app.site import blog_card


class BlogIndexPage(Component):
    def render(self, props=None):
        props = props or self.props
        posts = props["posts"]
        post_cards = [
            blog_card(
                post.title,
                post.url_path,
                post.description,
                post.metadata.get("date", ""),
            )
            for post in posts
        ]
        return ui.div(
            ui.header(
                ui.h1("Blog"),
                ui.p("Updates, guides, and thoughts on building with SPRAG."),
                class_="blog-header",
            ),
            ui.div(post_cards, class_="blog-grid"),
            class_="blog-page",
        )
