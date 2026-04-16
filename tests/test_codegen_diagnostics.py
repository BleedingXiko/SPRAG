import unittest
import json
import inspect

from sprag import Component, Module, browser, imports, ui
from sprag.dev.codegen.components import compile_component_artifact, compile_component_class
from sprag.dev.codegen.modules import compile_module_artifact, compile_module_class
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


class RefetchHelpersModule(Module):
    def on_start(self):
        self.refetch_on_socket("sprag:refetch", "status", self.on_status)

    def on_status(self, result, payload=None):
        self.set_state({"status": result.value["message"]})


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
        self.assertIn("//# sourceMappingURL=BareReturnModule.js.map", compiled)

    def test_compile_module_supports_topic_helpers(self):
        compiled = compile_module_class(TopicHelpersModule)
        self.assertIn("joinTopic(topic)", compiled)
        self.assertIn("leaveTopic(topic)", compiled)
        self.assertIn('this.joinTopic("room:alpha")', compiled)
        self.assertIn('this.leaveTopic("room:alpha")', compiled)

    def test_compile_module_supports_refetch_helpers(self):
        compiled = compile_module_class(RefetchHelpersModule)
        self.assertIn("refetchOnSocket(event = 'sprag:refetch'", compiled)
        self.assertIn('this.refetchOnSocket("sprag:refetch", "status", this.onStatus.bind(this));', compiled)

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

    def test_compile_module_artifact_emits_source_map_metadata(self):
        artifact = compile_module_artifact(SupportedModule)
        payload = json.loads(artifact.source_map)
        self.assertEqual(payload["file"], "SupportedModule.js")
        self.assertTrue(payload["sources"])
        self.assertIn("on_start", payload["names"])
        self.assertEqual(payload["x_sprag"]["class"], "SupportedModule")
        self.assertEqual(payload["x_sprag"]["kind"], "module")
        self.assertEqual(payload["x_sprag"]["methods"][0]["name"], "on_start")
        self.assertGreater(payload["x_sprag"]["methods"][0]["generated_start_line"], 0)

    def test_compile_component_artifact_emits_source_map_metadata(self):
        artifact = compile_component_artifact(SupportedComponent)
        payload = json.loads(artifact.source_map)
        self.assertEqual(payload["file"], "SupportedComponent.js")
        self.assertIn("render", payload["names"])
        self.assertEqual(payload["x_sprag"]["class"], "SupportedComponent")
        self.assertEqual(payload["x_sprag"]["kind"], "component")
        self.assertEqual(payload["x_sprag"]["methods"][0]["name"], "render")

    def test_module_source_map_tracks_statement_lines(self):
        artifact = compile_module_artifact(SupportedModule)
        payload = json.loads(artifact.source_map)
        generated_lines = artifact.code.splitlines()
        mappings = _decode_mappings(payload["mappings"])
        source_start_line = inspect.getsourcelines(SupportedModule.on_start)[1]

        total_line = generated_lines.index("        let total = 0;")
        set_state_line = next(
            i for i, line in enumerate(generated_lines) if 'this.setState({ "total": total });' in line
        )
        self.assertEqual(mappings[total_line]["source_line"], source_start_line + 1)
        self.assertEqual(mappings[set_state_line]["source_line"], source_start_line + 5)

    def test_component_source_map_tracks_render_statement_lines(self):
        artifact = compile_component_artifact(SupportedComponent)
        payload = json.loads(artifact.source_map)
        generated_lines = artifact.code.splitlines()
        mappings = _decode_mappings(payload["mappings"])
        source_start_line = inspect.getsourcelines(SupportedComponent.render)[1]

        label_line = next(i for i, line in enumerate(generated_lines) if "const label =" in line)
        return_line = next(i for i, line in enumerate(generated_lines) if "return createElement(" in line)
        self.assertEqual(mappings[label_line]["source_line"], source_start_line + 1)
        self.assertEqual(mappings[return_line]["source_line"], source_start_line + 2)

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
        self.assertIn("Python `with` statements cannot be compiled to JavaScript.", message)

    def test_component_diagnostic_includes_context_and_hint(self):
        with self.assertRaises(JSCodegenError) as ctx:
            compile_component_class(UnsupportedAnnotatedComponent)
        message = str(ctx.exception)
        self.assertIn("Unsupported statement in browser codegen: AnnAssign.", message)
        self.assertIn("UnsupportedAnnotatedComponent.render", message)
        self.assertIn("count: int = 1", message)
        self.assertIn("Hint: Use a plain assignment inside browser methods", message)

_BASE64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def _decode_mappings(encoded):
    previous_source = 0
    previous_source_line = 0
    previous_source_column = 0
    previous_name = 0
    decoded = []
    for line in encoded.split(";"):
        if not line:
            decoded.append(None)
            continue
        fields = _decode_vlq_segments(line)
        generated_column = fields[0]
        previous_source += fields[1]
        previous_source_line += fields[2]
        previous_source_column += fields[3]
        entry = {
            "generated_column": generated_column,
            "source": previous_source,
            "source_line": previous_source_line + 1,
            "source_column": previous_source_column,
        }
        if len(fields) > 4:
            previous_name += fields[4]
            entry["name_index"] = previous_name
        decoded.append(entry)
    return decoded


def _decode_vlq_segments(segment):
    values = []
    value = 0
    shift = 0
    for char in segment:
        digit = _BASE64.index(char)
        continuation = digit & 32
        digit &= 31
        value += digit << shift
        if continuation:
            shift += 5
            continue
        values.append(_from_vlq_signed(value))
        value = 0
        shift = 0
    return values


def _from_vlq_signed(value):
    is_negative = value & 1
    value >>= 1
    return -value if is_negative else value


if __name__ == "__main__":
    unittest.main()
