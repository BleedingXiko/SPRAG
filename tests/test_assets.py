import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import sprag
from sprag import Component, Module, Screen, module, mount, page, script, ui
from sprag.dev.codegen import build_browser_entry, compile_module_class
from sprag.dev.codegen.emit import emit_stores_shim
from sprag.dev.build import build_web_preview
from sprag.runtime.rendering import render_mount
from sprag.runtime.stores import StoreBridge


class AssetController(sprag.Controller):
    route = "/docs/nested"

    def load(self):
        return {"message": "asset test"}


class AssetScreen(Screen):
    def render(self, data=None):
        return ui.main(
            data.get("message", "missing"),
            ui.a("Docs", href="/docs"),
            ui.img(src="/static/images/logo.svg", alt="Logo"),
        )


class AssetRootComponent(Component):
    def render(self, props=None):
        return ui.div("Mount asset test")


class AssetProviderConsumer(Module):
    def on_start(self):
        self.surface_provider = self.provider("surfaceProvider")


class DummyApp:
    def __init__(self, project_root):
        self.project_root = str(project_root)
        self._sprag_dev_reload = False
        self._sprag_static_build = False
        self.server_mode = "auto"


class AssetContractTests(unittest.TestCase):
    def test_emit_stores_shim_produces_valid_esm(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is required for JS syntax validation")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vendor_dir = root / "vendor"
            vendor_dir.mkdir(parents=True, exist_ok=True)
            (vendor_dir / "ragot.esm.min.js").write_text(
                "export function createStateStore(initial){\n"
                "  let state = initial;\n"
                "  return {\n"
                "    getState(){ return state; },\n"
                "    setState(next){ state = next; },\n"
                "    subscribe(){ return () => {}; },\n"
                "  };\n"
                "}\n"
                "export function createSelector(_inputs, projector){\n"
                "  return (state) => projector(state);\n"
                "}\n",
                encoding="utf-8",
            )
            emit_stores_shim(
                root,
                [
                    StoreBridge("session", initial={"counter": 0}),
                    StoreBridge("lab_tally", initial={"count": 0}),
                ],
            )

            stores_path = root / "generated" / "stores.js"
            result = subprocess.run(
                [
                    node,
                    "--input-type=module",
                    "-e",
                    (
                        "import("
                        + json.dumps(stores_path.as_uri())
                        + ").then(() => process.exit(0)).catch((error) => {"
                        + "console.error(error); process.exit(1);"
                        + "})"
                    ),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode,
                0,
                msg=f"Generated stores.js failed to import:\n{result.stderr}",
            )

    def test_build_web_preview_emits_surface_assets_and_static_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app" / "shell.html").parent.mkdir(parents=True, exist_ok=True)
            (root / "app" / "routes" / "docs").mkdir(parents=True, exist_ok=True)
            (root / "app" / "static" / "vendor").mkdir(parents=True, exist_ok=True)
            (root / "app" / "static" / "images").mkdir(parents=True, exist_ok=True)

            (root / "app" / "shell.html").write_text(
                "<html><body>{{ sprag_slot }}</body></html>",
                encoding="utf-8",
            )
            (root / "app" / "shell.css").write_text("body { color: red; }\n", encoding="utf-8")
            (root / "app" / "routes" / "docs" / "module-helper.mjs").write_text(
                "window.__SPRAG_TEST_MODULE__ = true;\n",
                encoding="utf-8",
            )
            (root / "app" / "static" / "vendor" / "dayjs.mjs").write_text(
                "export default function dayjs() { return { format() { return '2026-04-12'; } }; }\n",
                encoding="utf-8",
            )
            (root / "app" / "static" / "vendor" / "widget.js").write_text(
                "window.__SPRAG_WIDGET__ = true;\n",
                encoding="utf-8",
            )
            (root / "app" / "static" / "images" / "logo.svg").write_text(
                "<svg></svg>\n",
                encoding="utf-8",
            )

            manifest = build_web_preview(
                [
                    (
                        "app.routes.docs.page",
                        page(
                            path="/docs/nested",
                            controller=AssetController,
                            screen=AssetScreen,
                            mode="document",
                            shell="app/shell.html",
                            css=["app/shell.css"],
                            js=[
                                "app/static/vendor/widget.js",
                                script("app/routes/docs/module-helper.mjs", module=True),
                            ],
                            modules={
                                "dayjs": module("app/static/vendor/dayjs.mjs"),
                                "nanoid": module(
                                    "https://cdn.example.test/nanoid.mjs",
                                    export="nanoid",
                                ),
                            },
                        ),
                    )
                ],
                root / "dist",
                app=DummyApp(root),
                mounts=[],
            )

            html = (root / "dist" / "docs" / "nested" / "index.html").read_text(encoding="utf-8")
            self.assertIn('href=".."', html)
            self.assertIn('src="../../static/images/logo.svg"', html)
            self.assertIn('href="../../assets/app/shell.css"', html)
            self.assertIn('src="../../static/vendor/widget.js"', html)
            self.assertIn('type="module" src="../../assets/app/routes/docs/module-helper.mjs"', html)

            self.assertTrue((root / "dist" / "assets" / "app" / "shell.css").exists())
            self.assertTrue((root / "dist" / "assets" / "app" / "routes" / "docs" / "module-helper.mjs").exists())
            self.assertTrue((root / "dist" / "static" / "vendor" / "dayjs.mjs").exists())
            self.assertTrue((root / "dist" / "static" / "vendor" / "widget.js").exists())
            self.assertTrue((root / "dist" / "static" / "images" / "logo.svg").exists())

            asset_paths = {asset["web_path"] for asset in manifest["assets"]}
            self.assertIn("/assets/app/shell.css", asset_paths)
            self.assertIn("/assets/app/routes/docs/module-helper.mjs", asset_paths)
            self.assertIn("/static/vendor/dayjs.mjs", asset_paths)
            self.assertIn("/static/vendor/widget.js", asset_paths)
            self.assertIn("/static/images/logo.svg", asset_paths)

            declared_modules = manifest["routes"][0]["modules"]
            self.assertEqual(
                declared_modules["dayjs"],
                {"src": "/static/vendor/dayjs.mjs", "export": "default"},
            )
            self.assertEqual(
                declared_modules["nanoid"],
                {
                    "src": "https://cdn.example.test/nanoid.mjs",
                    "export": "nanoid",
                },
            )

    def test_render_mount_includes_declared_local_and_external_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app" / "static" / "vendor").mkdir(parents=True, exist_ok=True)
            (root / "app" / "static" / "vendor" / "widget.js").write_text(
                "window.__SPRAG_WIDGET__ = true;\n",
                encoding="utf-8",
            )

            document = render_mount(
                mount(
                    "/lab",
                    component=AssetRootComponent,
                    js=[
                        "app/static/vendor/widget.js",
                        script("https://cdn.example.test/widget.mjs", module=True),
                    ],
                ),
                app=DummyApp(root),
            ).html

            self.assertIn('src="/static/vendor/widget.js"', document)
            self.assertIn('type="module" src="https://cdn.example.test/widget.mjs"', document)
            self.assertLess(
                document.index('src="/static/vendor/widget.js"'),
                document.index('src="/surfaces/mount__lab.js"'),
            )
            imports_document = render_mount(
                mount(
                    "/lab-imports",
                    component=AssetRootComponent,
                    modules={"nanoid": module("https://cdn.example.test/nanoid.mjs", export="nanoid")},
                ),
                app=DummyApp(root),
            ).html
            self.assertIn('"modules": {"nanoid": {', imports_document)
            self.assertIn('"export": "nanoid"', imports_document)
            self.assertIn('"src": "https://cdn.example.test/nanoid.mjs"', imports_document)

    def test_build_web_preview_emits_source_maps_for_generated_browser_classes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            build_web_preview(
                [],
                root / "dist",
                app=DummyApp(root),
                mounts=[
                    (
                        "app.mounts.lab",
                        mount(
                            "/lab",
                            component=AssetRootComponent,
                        ),
                    )
                ],
            )

            component_map = root / "dist" / "generated" / "components" / "AssetRootComponent.js.map"
            self.assertTrue(component_map.exists())

            component_payload = json.loads(component_map.read_text(encoding="utf-8"))
            self.assertEqual(component_payload["x_sprag"]["class"], "AssetRootComponent")
            self.assertEqual(component_payload["x_sprag"]["methods"][0]["name"], "render")

    def test_build_web_preview_emits_thin_surface_entries_and_runtime_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            build_web_preview(
                [
                    (
                        "app.routes.docs.page",
                        page(
                            path="/docs",
                            controller=AssetController,
                            screen=AssetScreen,
                            mode="document",
                            modules={
                                "dayjs": module("/static/vendor/dayjs.mjs"),
                            },
                        ),
                    )
                ],
                root / "dist",
                app=DummyApp(root),
                mounts=[],
            )

            entry_path = root / "dist" / "surfaces" / "route__docs.js"
            hydration_path = root / "dist" / "runtime" / "hydration.js"
            sockets_path = root / "dist" / "runtime" / "sockets.js"
            manifest_module = root / "dist" / "generated" / "manifest.js"

            self.assertTrue(entry_path.exists())
            self.assertTrue(hydration_path.exists())
            self.assertTrue(sockets_path.exists())
            self.assertTrue(manifest_module.exists())

            entry = entry_path.read_text(encoding="utf-8")
            self.assertIn("import manifest from '../generated/manifest.js';", entry)
            self.assertIn("import { startSurfaceBoot } from '../runtime/boot.js';", entry)
            self.assertIn("surfaceRef:", entry)
            self.assertNotIn("function createActionClient", entry)

            hydration_runtime = hydration_path.read_text(encoding="utf-8")
            self.assertIn("async function resolveSurfaceImports(currentSurface, resolveJSImportSrc)", hydration_runtime)
            self.assertIn("await import(src)", hydration_runtime)
            self.assertIn("window.__SPRAG_IMPORTS__ = resolved;", hydration_runtime)
            self.assertIn("spragBootError", hydration_runtime)

            socket_runtime = sockets_path.read_text(encoding="utf-8")
            self.assertIn("type: 'topic'", socket_runtime)
            self.assertIn("encodeTopicMessage('join', topic)", socket_runtime)
            self.assertIn("encodeTopicMessage('leave', normalized)", socket_runtime)

            surface_root_runtime = (root / "dist" / "runtime" / "surface_root.js").read_text(encoding="utf-8")
            self.assertIn("!this.surface.static", surface_root_runtime)
            self.assertIn("this.surface.dev_reload", surface_root_runtime)
            self.assertIn("typeof window.EventSource === 'function'", surface_root_runtime)

    def test_static_build_marks_surfaces_static_and_disables_socket_bridge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = DummyApp(root)
            app._sprag_static_build = True

            build_web_preview(
                [
                    (
                        "app.routes.docs.page",
                        page(
                            path="/docs",
                            controller=AssetController,
                            screen=AssetScreen,
                            mode="document",
                        ),
                    )
                ],
                root / "dist",
                app=app,
                mounts=[],
            )

            html = (root / "dist" / "docs" / "index.html").read_text(encoding="utf-8")
            self.assertIn('"static": true', html)
            self.assertIn('"socket_bridge": false', html)

    def test_browser_entry_stays_thin_when_built_directly(self):
        browser_entry = build_browser_entry({"routes": [], "mounts": [], "errors": []})
        self.assertIn("import { startSurfaceBoot } from './runtime/boot.js';", browser_entry)
        self.assertIn("const manifest =", browser_entry)
        self.assertIn("startSurfaceBoot({", browser_entry)
        self.assertNotIn("async function resolveSurfaceImports", browser_entry)
        self.assertNotIn("data-sprag-boot-error", browser_entry)

    def test_compile_module_emits_browser_provider_helper(self):
        compiled = compile_module_class(AssetProviderConsumer)
        self.assertIn("Module, ragotRegistry", compiled)
        self.assertIn("provider(name)", compiled)
        self.assertIn("return ragotRegistry.require(name);", compiled)
        self.assertIn('this.surfaceProvider = this.provider("surfaceProvider");', compiled)


if __name__ == "__main__":
    unittest.main()
