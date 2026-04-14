from sprag import page

from .server import BlogIndexController
from .web import BlogIndexScreen


blog_index = page(
    path="/blog",
    controller=BlogIndexController,
    screen=BlogIndexScreen,
    mode="document",
    metadata={"title": "Blog"},
)
