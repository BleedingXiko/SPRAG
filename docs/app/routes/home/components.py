from sprag import Component, ui

class HomePage(Component):
    def render(self, props=None):
        props = props or self.props
        features = props.get("features", [])
        feature_cards = [
            ui.div(
                ui.h3(f["title"]),
                ui.p(f["description"]),
                class_="feature-card",
            )
            for f in features
        ]
        return ui.div(
            ui.section(
                ui.div(
                    ui.div("Python web framework", class_="landing-badge"),
                    ui.h1(
                        "Full-stack Python. ",
                        ui.span("No JavaScript required."),
                        class_="landing-title",
                    ),
                    ui.p(
                        "Write Components, Modules, and Controllers in Python. "
                        "SPRAG compiles your browser code to JavaScript at build time.",
                        class_="landing-subtitle",
                    ),
                    ui.div(
                        ui.a("Get Started", href="/docs/getting-started/installation", class_="btn btn-primary"),
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
                    class_="landing-features-heading",
                ),
                ui.div(feature_cards, class_="features-grid"),
                class_="landing-features",
            ),
            ui.section(
                ui.div(
                    ui.h2("Write Python, run in the browser"),
                    ui.p("Component classes compile to Ragot ESM modules at build time."),
                    class_="landing-example-header",
                ),
                ui.div(
                    ui.div("components.py", class_="landing-code-tab"),
                    ui.pre(ui.code(
                        "from sprag import Component, ui\n"
                        "\n"
                        "class Counter(Component):\n"
                        "    def render(self, props=None):\n"
                        '        count = self.state.get("count", 0)\n'
                        "        return ui.div(\n"
                        "            ui.span(str(count)),\n"
                        '            ui.button("Increment", data_role="inc"),\n'
                        "        )\n"
                        "\n"
                        "    def on_start(self):\n"
                        '        self.on(self.element, "click", self._click)\n'
                        "\n"
                        "    def _click(self, event):\n"
                        '        if event.target.dataset.role == "inc":\n'
                        '            self.dispatch("increment", {})',
                    )),
                    class_="landing-code",
                ),
                class_="landing-example",
            ),
        )
