from sprag import Controller

from app.content import blog_collection, slim_doc


class BlogIndexController(Controller):
    route = "/blog"

    def load(self):
        return {
            "__sprag_meta__": {"title": "Blog"},
            "title": "Blog",
            "description": (
                "Post pages are rendered from Markdown files and expanded at build time "
                "through the slug route."
            ),
            "posts": [slim_doc(post) for post in blog_collection()],
        }
