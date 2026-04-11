import unittest

from sprag import Component, Module, ui
from sprag.dev.codegen.components import compile_component_class
from sprag.dev.codegen.modules import compile_module_class
from sprag.dev.codegen.mappings import JSCodegenError


class SupportedComponent(Component):
    def render(self, props=None):
        label = f"count {self.state.get('count', 0)}"
        return ui.div(label, class_="card")


class SupportedModule(Module):
    def on_start(self):
        total = 0
        for i in range(3):
            total += i
        if total > 1:
            self.set_state({"total": total})


class BareReturnModule(Module):
    def on_start(self):
        if True:
            return
        self.set_state({"ready": True})


class TopicHelpersModule(Module):
    def on_start(self):
        self.join_topic("room:alpha")
        self.leave_topic("room:alpha")


class UnsupportedWithModule(Module):
    def on_start(self):
        with open("ignored.txt") as handle:
            self.set_state({"value": handle.read()})


class UnsupportedAnnotatedComponent(Component):
    def render(self, props=None):
        count: int = 1
        return ui.div(str(count))


class CodegenDiagnosticsTests(unittest.TestCase):
    def test_compile_component_success_regression(self):
        compiled = compile_component_class(SupportedComponent)
        self.assertIn("export class SupportedComponent extends Component", compiled)
        self.assertIn('createElement("div"', compiled)

    def test_compile_module_success_regression(self):
        compiled = compile_module_class(SupportedModule)
        self.assertIn("export class SupportedModule extends Module", compiled)
        self.assertIn("for (let i = 0; i < 3; i++)", compiled)

    def test_compile_module_supports_bare_return(self):
        compiled = compile_module_class(BareReturnModule)
        self.assertIn("return undefined;", compiled)

    def test_compile_module_supports_topic_helpers(self):
        compiled = compile_module_class(TopicHelpersModule)
        self.assertIn("joinTopic(topic)", compiled)
        self.assertIn("leaveTopic(topic)", compiled)
        self.assertIn('this.joinTopic("room:alpha")', compiled)
        self.assertIn('this.leaveTopic("room:alpha")', compiled)

    def test_module_diagnostic_includes_context_and_hint(self):
        with self.assertRaises(JSCodegenError) as ctx:
            compile_module_class(UnsupportedWithModule)
        message = str(ctx.exception)
        self.assertIn("Unsupported statement in browser codegen: With.", message)
        self.assertIn("UnsupportedWithModule.on_start", message)
        self.assertIn("with open(", message)
        self.assertIn("Hint: Use explicit setup/cleanup and try/finally in browser code.", message)

    def test_component_diagnostic_includes_context_and_hint(self):
        with self.assertRaises(JSCodegenError) as ctx:
            compile_component_class(UnsupportedAnnotatedComponent)
        message = str(ctx.exception)
        self.assertIn("Unsupported statement in browser codegen: AnnAssign.", message)
        self.assertIn("UnsupportedAnnotatedComponent.render", message)
        self.assertIn("count: int = 1", message)
        self.assertIn("Hint: Use a plain assignment inside browser methods", message)


if __name__ == "__main__":
    unittest.main()
