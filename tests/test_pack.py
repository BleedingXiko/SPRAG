import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from sprag.dev.pack import SpragPack


class PackContractTests(unittest.TestCase):
    def test_pack_minifies_local_mjs_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public = root / "public"
            public.mkdir(parents=True, exist_ok=True)
            (root / "server.py").write_text("print('server')\n", encoding="utf-8")
            module_path = public / "app.mjs"
            module_path.write_text(
                "// comment\nexport function demo() {\n    console.log('ready');\n}\n",
                encoding="utf-8",
            )

            packer = SpragPack(
                root,
                skip_images=True,
                skip_bytecode=True,
                skip_gzip=True,
            )
            packer.terser_bin = None
            packer.workers = 1
            packer._phase_minify()

            bundled = module_path.read_text(encoding="utf-8")
            self.assertNotIn("// comment", bundled)
            self.assertIn("export function demo()", bundled)
            self.assertEqual(packer.stats["minified_js"], 1)

    def test_pack_pregzip_includes_mjs_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public = root / "public"
            public.mkdir(parents=True, exist_ok=True)
            (root / "server.py").write_text("print('server')\n", encoding="utf-8")
            module_path = public / "bundle.mjs"
            module_path.write_text("export const payload = '" + ("x" * 4096) + "';\n", encoding="utf-8")

            packer = SpragPack(
                root,
                skip_images=True,
                skip_minify=True,
                skip_bytecode=True,
            )
            packer._phase_pregzip()

            self.assertTrue((public / "bundle.mjs.gz").exists())
            self.assertGreaterEqual(packer.stats["gzipped_files"], 1)

    def test_pack_validation_counts_mjs_assets_as_js(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public = root / "public"
            public.mkdir(parents=True, exist_ok=True)
            (root / "server.py").write_text("print('server')\n", encoding="utf-8")
            (public / "index.html").write_text("<html></html>\n", encoding="utf-8")
            (public / "bundle.mjs").write_text("export const demo = true;\n", encoding="utf-8")

            packer = SpragPack(
                root,
                skip_images=True,
                skip_minify=True,
                skip_bytecode=True,
                skip_gzip=True,
            )

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                packer._phase_validate()

            output = buffer.getvalue()
            self.assertIn("Dist contains 1 HTML, 1 JS files", output)


if __name__ == "__main__":
    unittest.main()
