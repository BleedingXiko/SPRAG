from sprag import Controller, Field, Schema, action


class PlaygroundController(Controller):
    route = "/playground"

    def load(self):
        return {"count": 0}

    @action(schema=Schema("increment", {"count": Field(int, required=True)}))
    def increment(self, count):
        return {"count": count + 1}

    @action(schema=Schema("reset", {}))
    def reset(self):
        return {"count": 0}
