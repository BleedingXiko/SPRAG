import unittest

from sprag import Component, Module, browser, imports, ui
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


class BrowserNamespaceModule(Module):
    def on_start(self):
        Chart = browser.Chart
        browser.Alpine.store("theme", {"ready": True})
        self.set_state({"chart": Chart})


class JSImportsModule(Module):
    def on_start(self):
        dayjs = imports.dayjs
        self.set_state({"today": dayjs().format("YYYY-MM-DD")})


class MetadataHelpersModule(Module):
    def on_start(self):
        self.set_metadata(
            {
                "title": "Live Title",
                "description": "Live description",
                "canonical": None,
            },
            {"replace": True},
        )


class ActionErrorHelpersModule(Module):
    def on_start(self):
        self.set_state({"message": self.action_error_message(None, "Fallback message")})


class MetadataHelpersComponent(Component):
    def render(self, props=None):
        return ui.button("Update metadata")

    def on_click(self):
        self.set_metadata({"og:title": "Updated OG title"})


class DynamicMountsComponent(Component):
    def render(self, props=None):
        items = self.state.get("items", [])
        return ui.div(
            ui.For(
                items,
                key="id",
                render=lambda item: ui.div(item["label"]),
            ),
            ui.LazyImage(
                "/static/demo.png",
                placeholder="/static/demo-placeholder.png",
                alt="Demo",
            ),
        )


class InvalidComponentSubscription(Component):
    def render(self, props=None):
        return ui.button("Subscribe")

    def on_start(self):
        self.subscribe(counter, self.on_counter)

    def on_counter(self, value):
        return value


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

    def test_compile_module_lowers_browser_namespace(self):
        compiled = compile_module_class(BrowserNamespaceModule)
        self.assertIn("globalThis.Chart", compiled)
        self.assertIn('globalThis.Alpine.store("theme"', compiled)

    def test_compile_module_lowers_declared_js_import_aliases(self):
        compiled = compile_module_class(
            JSImportsModule,
            declared_import_aliases={"dayjs"},
        )
        self.assertIn("(globalThis.__SPRAG_IMPORTS__ || {}).dayjs", compiled)

    def test_compile_module_supports_set_metadata_helper(self):
        compiled = compile_module_class(MetadataHelpersModule)
        self.assertIn("setMetadata(metadata = {}, options = {})", compiled)
        self.assertIn("window.__SPRAG_SET_METADATA__", compiled)
        self.assertIn('this.setMetadata({ "title": "Live Title"', compiled)

    def test_compile_module_supports_action_error_message_helper(self):
        compiled = compile_module_class(ActionErrorHelpersModule)
        self.assertIn("actionErrorMessage(error, fallback = '')", compiled)
        self.assertIn("window.__SPRAG_ACTION_ERROR_MESSAGE__", compiled)
        self.assertIn('this.actionErrorMessage(null, "Fallback message")', compiled)

    def test_compile_component_supports_set_metadata_helper(self):
        compiled = compile_component_class(MetadataHelpersComponent)
        self.assertIn("setMetadata(metadata = {}, options = {})", compiled)
        self.assertIn("window.__SPRAG_SET_METADATA__", compiled)
        self.assertIn('this.setMetadata({ "og:title": "Updated OG title" });', compiled)

    def test_component_dynamic_mounts_resync_after_rerender(self):
        compiled = compile_component_class(DynamicMountsComponent)
        self.assertIn("__spragSyncMounts()", compiled)
        self.assertIn("renderList(", compiled)
        self.assertIn("super.setStateSync(next);", compiled)
        self.assertIn("super._performUpdate();", compiled)
        self.assertIn("this.__spragSyncMounts();", compiled)
        self.assertIn("this._sprLazy.refresh()", compiled)

    def test_component_subscribe_fails_with_module_guidance(self):
        with self.assertRaises(JSCodegenError) as ctx:
            compile_component_class(InvalidComponentSubscription)
        message = str(ctx.exception)
        self.assertIn("Component.subscribe(...) is not part of SPRAG's browser contract.", message)
        self.assertIn("InvalidComponentSubscription.on_start", message)
        self.assertIn("self.subscribe(counter, self.on_counter)", message)
        self.assertIn("Subscribe in a Module", message)

    def test_compile_module_rejects_unknown_js_import_alias(self):
        with self.assertRaises(JSCodegenError) as ctx:
            compile_module_class(JSImportsModule)
        message = str(ctx.exception)
        self.assertIn("Unknown SPRAG JS import alias `dayjs`", message)
        self.assertIn("declare it via page(..., modules={...}) or mount(..., modules={...})", message)

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
