import json
import tempfile
import unittest
from pathlib import Path

from sprag.dev.typegen import emit_project_types, render_project_types


class TypegenContractTests(unittest.TestCase):
    def test_render_project_types_emits_manifest_literals(self):
        content = render_project_types(
            {
                "routes": [
                    {
                        "path": "/counter",
                        "name": "counter",
                        "actions": ["increment", "reset"],
                        "modules": {"chart": {"src": "/chart.js"}},
                    }
                ],
                "mounts": [
                    {
                        "path": "/search",
                        "name": "search",
                        "actions": ["query"],
                        "modules": {},
                    }
                ],
                "stores": [{"name": "session"}],
            }
        )

        self.assertIn("RoutePath = Literal['/counter']", content)
        self.assertIn("MountPath = Literal['/search']", content)
        self.assertIn("ActionName = Literal['increment', 'query', 'reset']", content)
        self.assertIn("StoreName = Literal['session']", content)
        self.assertIn("ModuleAlias = Literal['chart']", content)
        self.assertIn("CounterAction = Literal['increment', 'reset']", content)
        self.assertIn("SearchAction = Literal['query']", content)

    def test_emit_project_types_writes_project_module_and_removes_stale_types_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sprag_dir = root / ".sprag"
            sprag_dir.mkdir()
            manifest = {
                "routes": [{"path": "/", "name": "home", "actions": ["refresh"]}],
                "mounts": [],
                "stores": [],
            }
            manifest_path = sprag_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            stale = sprag_dir / "types.pyi"
            stale.write_text("ActionName = str\n", encoding="utf-8")

            output = emit_project_types(manifest_path)

            self.assertEqual(output, sprag_dir / "sprag_project_types.pyi")
            self.assertTrue((sprag_dir / "__init__.py").exists())
            self.assertTrue((sprag_dir / "py.typed").exists())
            self.assertFalse(stale.exists())
            self.assertIn("ActionName = Literal['refresh']", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
