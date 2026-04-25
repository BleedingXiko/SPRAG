from sprag import Component, ui


class SearchApp(Component):
    def render(self, props=None):
        return ui.main(
            ui.div(
                ui.h1("Search the docs"),
                ui.input(
                    type="search",
                    name="q",
                    placeholder="Search the docs...",
                    autocomplete="off",
                    spellcheck="false",
                    class_="search-input",
                    data_role="search-input",
                ),
                class_="search-header",
            ),
            ui.p("", class_="search-status", data_role="search-status"),
            ui.ul(class_="search-results", data_role="search-results"),
            class_="search-app",
        )
