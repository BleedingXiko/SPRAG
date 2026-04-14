from sprag import Screen, ssr

from .components import BlogIndexPage


class BlogIndexScreen(Screen):
    def render(self, data):
        return ssr(BlogIndexPage, **data)
