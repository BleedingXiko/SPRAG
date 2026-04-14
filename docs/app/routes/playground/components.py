from sprag import Component, ui


class PlaygroundPage(Component):
    def render(self, props=None):
        count = self.state.get("count", 0)
        return ui.div(
            ui.header(
                ui.h1("Playground"),
                ui.p(
                    "A hybrid route with server-backed state. "
                    "Each button click calls a server @action via dispatch()."
                ),
                class_="playground-header",
            ),
            ui.div(
                ui.div("Server-backed counter", class_="playground-demo-header"),
                ui.div(
                    ui.div(str(count), class_="playground-count"),
                    ui.div(
                        ui.button(
                            "Increment",
                            type="button",
                            class_="btn btn-primary",
                            data_role="increment",
                        ),
                        ui.button(
                            "Reset",
                            type="button",
                            class_="btn",
                            data_role="reset",
                        ),
                        class_="playground-actions",
                    ),
                    class_="playground-demo-body",
                ),
                class_="playground-demo",
            ),
            class_="playground-page",
        )
