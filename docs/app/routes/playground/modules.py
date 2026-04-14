from sprag import Module


class PlaygroundModule(Module):
    def on_start(self):
        self.delegate(self.element, "click", "[data-role='increment']", self.on_increment)
        self.delegate(self.element, "click", "[data-role='reset']", self.on_reset)

    def on_increment(self, event, target):
        event.prevent_default()
        self.dispatch("increment", {"count": self.state.get("count", 0)})

    def on_reset(self, event, target):
        event.prevent_default()
        self.dispatch("reset", {})
