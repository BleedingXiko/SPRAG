import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from sprag import Component, Controller, Screen, module, mount, page, script, ui
from sprag.dev.codegen import build_browser_entry
from sprag.dev.codegen.emit import emit_stores_shim
from sprag.dev.build import build_web_preview
from sprag.runtime.rendering import render_mount
from sprag.runtime.stores import StoreBridge


class AssetController(Controller):
    route = "/docs/nested"

    def load(self):
        return {"message": "asset test"}


class AssetScreen(Screen):
    def render(self, data=None):
        return ui.main(data.get("message", "missing"))


class AssetRootComponent(Component):
    def render(self, props=None):
        return ui.div("Mount asset test")


class DummyApp:
    def __init__(self, project_root):
        self.project_root = str(project_root)
        self._sprag_dev_reload = False
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
                document.index('src="/app.js"'),
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

    def test_browser_entry_waits_for_surface_module_imports_and_renders_boot_errors(self):
        browser_entry = build_browser_entry(
            {
                "routes": [
                    {
                        "path": "/docs",
                        "modules": {
                            "dayjs": {"src": "/static/vendor/dayjs.mjs", "export": "default"}
                        },
                        "hydration": [],
                    }
                ],
                "mounts": [],
                "errors": [],
            }
        )
        self.assertIn("async function resolveSurfaceImports(currentSurface)", browser_entry)
        self.assertIn("await import(src)", browser_entry)
        self.assertIn("await resolveSurfaceImports(surface);", browser_entry)
        self.assertIn("window.__SPRAG_IMPORTS__ = resolved;", browser_entry)
        self.assertIn("data-sprag-boot-error", browser_entry)


if __name__ == "__main__":
    unittest.main()
