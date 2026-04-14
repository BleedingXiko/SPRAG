from sprag import Controller

from app.content import docs_collection


class HomeController(Controller):
    route = "/"

    def load(self):
        docs = docs_collection()
        return {
            "__sprag_meta__": {"title": "SPRAG - Full-stack Python Web Framework"},
            "features": [
                {
                    "title": "Two Runtimes",
                    "description": "Server logic runs as Python. Browser UI compiles to JavaScript. Same language, same file, zero context switching.",
                },
                {
                    "title": "Python-to-JS Codegen",
                    "description": "Write Components and Modules in Python. The build step compiles them to optimized Ragot ESM JavaScript.",
                },
                {
                    "title": "Cross-Runtime State",
                    "description": "store() works identically on server and browser. One API for get, set, patch, and subscribe.",
                },
                {
                    "title": "File-Based Routing",
                    "description": "Routes live under app/routes/. Each directory is a route with its own controller, page manifest, and components.",
                },
                {
                    "title": "Realtime Built In",
                    "description": "WebSocket support with socket bridge, topics, and signal-then-refetch. No extra configuration needed.",
                },
                {
                    "title": "SSR + Hydration",
                    "description": "Choose document mode for pure SSR, hybrid for SSR plus hydration, or spa for full client rendering.",
                },
            ],
            "docs": docs[:6],
        }
