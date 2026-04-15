from sprag import Controller

from app.content import blog_collection, docs_by_section, docs_collection, slim_doc, slim_doc_card, slim_nav_sections


class HomeController(Controller):
    route = "/"

    def load(self):
        docs = docs_collection()
        posts = blog_collection()
        sections = docs_by_section()
        return {
            "__sprag_meta__": {"title": "SPRAG - Full-stack Python Web Framework"},
            "features": [
                {
                    "icon": "01",
                    "title": "Two Runtimes, One Language",
                    "description": "Server logic runs as Python under Specter. Browser UI compiles to Ragot JavaScript. Same file, zero context switching.",
                },
                {
                    "icon": "02",
                    "title": "Python-to-JS Codegen",
                    "description": "Write Components and Modules in Python. The build step compiles them to optimized ESM JavaScript automatically.",
                },
                {
                    "icon": "03",
                    "title": "Cross-Runtime State",
                    "description": "store() works identically on server and browser. One API for get, set, patch, and subscribe across both runtimes.",
                },
                {
                    "icon": "04",
                    "title": "File-Based Routing",
                    "description": "Routes live under app/routes/. Each directory is a self-contained route with controller, page manifest, and components.",
                },
                {
                    "icon": "05",
                    "title": "Realtime Built In",
                    "description": "WebSocket support with socket bridge, topics, and signal-then-refetch pattern. No extra configuration required.",
                },
                {
                    "icon": "06",
                    "title": "SSR + Hydration Modes",
                    "description": "Choose document mode for pure SSR, hybrid for SSR plus hydration, or spa for full client rendering per route.",
                },
            ],
            "sections": slim_nav_sections(sections),
            "docs": [slim_doc_card(doc) for doc in docs],
            "posts": [slim_doc_card(post) for post in posts],
        }
