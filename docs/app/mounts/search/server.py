from sprag import Controller


class SearchBoot(Controller):
    route = "/search"

    def load(self):
        return {"title": "Search the docs"}
