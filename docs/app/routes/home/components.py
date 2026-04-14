from sprag import Component, ui

from app.site import card, blog_card


class HomePage(Component):
    def render(self, props=None):
        props = props or self.props
        features = props.get("features", [])
        sections = props.get("sections", [])
        docs = props.get("docs", [])
        posts = props.get("posts", [])
        feature_cards = [
            ui.div(
                ui.div(f["icon"], class_="feature-num"),
                ui.h3(f["title"]),
                ui.p(f["description"]),
                class_="feature-card",
            )
            for f in features
        ]
        section_cards = [
            ui.a(
                ui.div(s["label"], class_="section-card-label"),
                ui.div(
                    str(len(s["items"])) + " articles",
                    class_="section-card-count",
                ),
                href=s["items"][0].url_path if s["items"] else "/docs",
                class_="section-card",
            )
            for s in sections
        ]
        doc_cards = [
            card(doc.title, doc.url_path, doc.description)
            for doc in docs
        ]
        post_cards = [
            blog_card(
                post.title,
                post.url_path,
                post.description,
                post.metadata.get("date", ""),
            )
            for post in posts
        ]
        return ui.div(
            ui.section(
                ui.div(
                    ui.div("Open-source Python framework", class_="landing-badge"),
                    ui.h1(
                        "Full-stack Python.",
                        ui.br(),
                        ui.span("No JavaScript required."),
                        class_="landing-title",
                    ),
                    ui.p(
                        "Write Components, Modules, and Controllers in Python. "
                        "SPRAG compiles your browser code to JavaScript at build time. "
                        "One language across server and browser.",
                        class_="landing-subtitle",
                    ),
                    ui.div(
                        ui.a("Get Started", href="/docs/getting-started/installation", class_="btn btn-primary"),
                        ui.a("Read the Docs", href="/docs", class_="btn"),
                        ui.a("GitHub", href="https://github.com/BleedingXiko/SPRAG", class_="btn", target="_blank"),
                        class_="landing-actions",
                    ),
                    class_="landing-hero",
                ),
                class_="landing",
            ),
            ui.section(
                ui.div(
                    ui.h2("Everything you need"),
                    ui.p("One framework, both runtimes, no glue code."),
                    class_="section-title",
                ),
                ui.div(feature_cards, class_="features-grid"),
                class_="landing-features",
            ),
            ui.section(
                ui.div(
                    ui.h2("Documentation"),
                    ui.p("Browse by topic"),
                    class_="section-title",
                ),
                ui.div(section_cards, class_="sections-grid"),
                class_="landing-sections",
            ),
            ui.section(
                ui.div(
                    ui.h2("All docs"),
                    ui.div(
                        ui.a("View all docs", href="/docs", class_="section-link"),
                    ),
                    class_="section-title",
                ),
                ui.div(doc_cards, class_="card-grid"),
                class_="landing-docs",
            ),
            ui.section(
                ui.div(
                    ui.h2("Blog"),
                    ui.div(
                        ui.a("View all posts", href="/blog", class_="section-link"),
                    ),
                    class_="section-title",
                ),
                ui.div(post_cards, class_="blog-grid"),
                class_="landing-blog",
            ),
            ui.section(
                ui.div(
                    ui.h2("One file. Both runtimes."),
                    ui.p("Server and browser code live side by side. The build separates them."),
                    class_="section-title",
                ),
                ui.div(
                    ui.div(
                        ui.div("server.py", class_="landing-code-tab"),
                        ui.pre(ui.code(
                            "from sprag import Controller, action, Schema, Field\n"
                            "\n"
                            "class TodoController(Controller):\n"
                            '    route = "/todos"\n'
                            "\n"
                            "    def load(self):\n"
                            '        return {"items": db.get_todos()}\n'
                            "\n"
                            "    @action(schema=Schema(\n"
                            '        "add", {"text": Field(str, required=True)}\n'
                            "    ))\n"
                            "    def add(self, text):\n"
                            "        db.insert(text)\n"
                            '        return {"items": db.get_todos()}',
                        )),
                        class_="landing-code",
                    ),
                    ui.div(
                        ui.div("components.py", class_="landing-code-tab"),
                        ui.pre(ui.code(
                            "from sprag import Component, ui\n"
                            "\n"
                            "class TodoList(Component):\n"
                            "    def render(self, props=None):\n"
                            '        items = self.state.get("items", [])\n'
                            "        return ui.div(\n"
                            "            ui.ul([ui.li(i) for i in items]),\n"
                            '            ui.input(data_role="input"),\n'
                            '            ui.button("Add", data_role="add"),\n'
                            "        )\n"
                            "\n"
                            "    def on_start(self):\n"
                            '        self.delegate(self.element, "click",\n'
                            '            "[data-role=add]", self._add)\n'
                            "\n"
                            "    def _add(self, event, target):\n"
                            '        inp = dom.query("[data-role=input]")\n'
                            '        self.dispatch("add", {"text": inp.value})',
                        )),
                        class_="landing-code",
                    ),
                    class_="landing-code-pair",
                ),
                class_="landing-example",
            ),
            class_="landing",
        )
