from sprag import App, shell


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
