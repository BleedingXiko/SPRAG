from pathlib import Path

from sprag import App, shell

from app.llms_txt import write_llms_txt
from app.search_index import write_search_index


_PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_SITE_URL = "https://bleedingxiko.github.io/SPRAG"

app_shell = shell(template="app/shell.html", css=["app/shell.css"])

app = App(
    routes="app.routes",
    shell=app_shell,
    metadata={
        "icons": [
            {"href": "/static/images/favicon.ico", "rel": "icon", "type": "image/x-icon", "sizes": "48x48"},
            {"href": "/static/images/favicon-32x32.png", "rel": "icon", "type": "image/png", "sizes": "32x32"},
            {"href": "/static/images/apple-touch-icon.png", "rel": "apple-touch-icon", "sizes": "180x180"},
            {"href": "/static/images/icon-192x192.png", "rel": "icon", "type": "image/png", "sizes": "192x192"},
            {"href": "/static/images/icon-512x512.png", "rel": "icon", "type": "image/png", "sizes": "512x512"},
        ],
    },
)

# Regenerate generated docs artifacts on every boot.
# public/ is copied verbatim to the static build output; app/static/ is
# served at /static/ in dev and copied to dist/static/ in build.
write_llms_txt(_PUBLIC_DIR, site_url=_SITE_URL)
write_search_index(_STATIC_DIR)
