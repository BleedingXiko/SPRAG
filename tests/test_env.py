import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from sprag import env, public_env
from sprag import Component, Module, ui
from sprag.dev.codegen.components import compile_component_class
from sprag.dev.codegen.modules import compile_module_class
from sprag.runtime.env import load_env, parse_env_file
from sprag.runtime.loader import load_app
from sprag.runtime.rendering.page import build_document_html


class BrowserEnvComponent(Component):
    def render(self, props=None):
        site_name = env("SPRAG_PUBLIC_SITE_NAME", "Fallback")
        values = public_env()
        return ui.div(f"{site_name}:{values.get('SPRAG_PUBLIC_SITE_NAME', 'missing')}")


class BrowserEnvModule(Module):
    def on_start(self):
        enabled = env("SPRAG_PUBLIC_ENABLED", False, cast=bool)
        self.set_state({"enabled": enabled})


class EnvSupportTests(unittest.TestCase):
    def test_parse_env_file_supports_quotes_comments_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                textwrap.dedent(
                    """
                    APP_NAME=SPRAG
                    export FEATURE_FLAG=true
                    QUOTED="hello world"
                    SECRET='value # kept'
                    PLAIN=value # comment
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            parsed = parse_env_file(env_file)

        self.assertEqual(parsed["APP_NAME"], "SPRAG")
        self.assertEqual(parsed["FEATURE_FLAG"], "true")
        self.assertEqual(parsed["QUOTED"], "hello world")
        self.assertEqual(parsed["SECRET"], "value # kept")
        self.assertEqual(parsed["PLAIN"], "value")

    def test_load_env_prefers_process_env_and_local_file_over_base(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"FROM_PROCESS": "outer"}, clear=True):
            root = Path(tmp)
            (root / ".env").write_text(
                "FROM_FILE=base\nFROM_PROCESS=base-ignored\nSHARED=base\n",
                encoding="utf-8",
            )
            (root / ".env.local").write_text(
                "SHARED=local\nLOCAL_ONLY=yes\n",
                encoding="utf-8",
            )

            loaded = load_env(root)

            self.assertEqual([path.name for path in loaded], [".env", ".env.local"])
            self.assertEqual(os.environ["FROM_FILE"], "base")
            self.assertEqual(os.environ["FROM_PROCESS"], "outer")
            self.assertEqual(os.environ["SHARED"], "local")
            self.assertEqual(os.environ["LOCAL_ONLY"], "yes")

    def test_env_helper_supports_defaults_and_casts(self):
        with mock.patch.dict(os.environ, {"FEATURE_FLAG": "true", "PORT": "8000"}, clear=True):
            self.assertTrue(env("FEATURE_FLAG", cast=bool))
            self.assertEqual(env("PORT", cast=int), 8000)
            self.assertEqual(env("MISSING", "fallback"), "fallback")
            with self.assertRaises(KeyError):
                env("NOT_SET", required=True)

    def test_load_app_auto_loads_dotenv_before_import(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            (root / ".env").write_text("APP_NAME=from-dotenv\n", encoding="utf-8")
            (root / "sampleapp.py").write_text(
                "import os\napp = os.environ.get('APP_NAME')\n",
                encoding="utf-8",
            )

            sys.path.insert(0, str(root))
            try:
                sys.modules.pop("sampleapp", None)
                target, loaded = load_app("sampleapp:app")
            finally:
                sys.path.remove(str(root))
                sys.modules.pop("sampleapp", None)

        self.assertEqual(target, "sampleapp:app")
        self.assertEqual(loaded, "from-dotenv")

    def test_public_env_is_included_in_render_payload(self):
        with mock.patch.dict(
            os.environ,
            {
                "SPRAG_PUBLIC_API_URL": "https://example.test",
                "SECRET_KEY": "keep-me-private",
            },
            clear=True,
        ):
            html = build_document_html(
                title="Env test",
                body_html="<main>ok</main>",
                route_data={},
                route_info={"path": "/", "mode": "document", "name": "home", "actions": []},
                hydration=[],
                script_path="/app.js",
                store_snapshot={},
            )

        self.assertIn("SPRAG_PUBLIC_API_URL", html)
        self.assertIn("https://example.test", html)
        self.assertNotIn("SECRET_KEY", html)
        self.assertNotIn("keep-me-private", html)
        self.assertIn("window.__SPRAG_PAYLOAD__", html)

    def test_public_env_helper_filters_non_public_values(self):
        with mock.patch.dict(
            os.environ,
            {
                "SPRAG_PUBLIC_SITE_NAME": "SPRAG",
                "DATABASE_URL": "postgres://secret",
            },
            clear=True,
        ):
            self.assertEqual(public_env(), {"SPRAG_PUBLIC_SITE_NAME": "SPRAG"})

    def test_component_codegen_supports_browser_env_helpers(self):
        compiled = compile_component_class(BrowserEnvComponent)
        self.assertIn("function __spragPublicEnv()", compiled)
        self.assertIn('__spragEnv("SPRAG_PUBLIC_SITE_NAME", "Fallback"', compiled)
        self.assertIn("__spragPublicEnv()", compiled)

    def test_module_codegen_supports_browser_env_casts(self):
        compiled = compile_module_class(BrowserEnvModule)
        self.assertIn("function __spragEnv(name, fallback = __SPRAG_ENV_MISSING__", compiled)
        self.assertIn("cast: \"bool\"", compiled)
        self.assertIn('__spragEnv("SPRAG_PUBLIC_ENABLED", false', compiled)


if __name__ == "__main__":
    unittest.main()
