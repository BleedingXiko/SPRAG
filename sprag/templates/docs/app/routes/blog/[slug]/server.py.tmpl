from sprag import Controller

from app.content import blog_collection, find_post


class BlogArticleController(Controller):
    route = "/blog/[slug]"

    def load(self):
        slug = self.request.params.get("slug")
        post = find_post(slug)
        title = post.title if post else "Blog"
        return {
            "__sprag_meta__": {"title": title},
            "post": post,
            "posts": blog_collection(),
        }
