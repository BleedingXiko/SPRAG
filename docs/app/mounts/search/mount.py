from sprag import mount

from .modules import SearchModule
from .server import SearchBoot
from .web import SearchApp


search = mount(
    path="/search",
    component=SearchApp,
    module=SearchModule,
    boot=SearchBoot,
    metadata={"title": "Search"},
)
