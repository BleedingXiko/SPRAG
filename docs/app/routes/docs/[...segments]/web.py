from sprag import Screen, ssr

from .components import DocsArticlePage


class DocsArticleScreen(Screen):
    def render(self, data):
        return ssr(DocsArticlePage, **data)
