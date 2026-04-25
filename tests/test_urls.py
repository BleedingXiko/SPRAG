import unittest

from sprag import join_url
from sprag.runtime.urls import relative_url, relativize_html_urls


class UrlHelperTests(unittest.TestCase):
    def test_join_url_handles_route_prefixes(self):
        self.assertEqual(join_url("/docs", "guides", "forms"), "/docs/guides/forms")
        self.assertEqual(join_url("docs/", "/guides/"), "/docs/guides")
        self.assertEqual(join_url("/", "docs"), "/docs")
        self.assertEqual(join_url("", "docs"), "/docs")
        self.assertEqual(join_url("/docs", trailing_slash=True), "/docs/")

    def test_join_url_preserves_absolute_bases(self):
        self.assertEqual(
            join_url("https://example.test/base", "docs", "intro"),
            "https://example.test/base/docs/intro",
        )
        self.assertEqual(
            join_url("//cdn.example.test/assets", "app.css"),
            "//cdn.example.test/assets/app.css",
        )

    def test_relative_url_handles_root_relative_targets(self):
        self.assertEqual(relative_url("/docs/getting-started", "/static/logo.png"), "../../static/logo.png")
        self.assertEqual(relative_url("/docs/getting-started", "/docs"), "..")
        self.assertEqual(relative_url("/docs/getting-started", "/"), "../../")
        self.assertEqual(relative_url("/", "/docs"), "docs")
        self.assertEqual(relative_url("/docs", "https://example.test/docs"), "https://example.test/docs")

    def test_relativize_html_urls_rewrites_only_root_relative_attributes(self):
        html = (
            '<a href="/docs">Docs</a>'
            '<img src="/static/logo.png">'
            '<form action="/search"></form>'
            '<a href="https://example.test/docs">External</a>'
            '<script src="//cdn.example.test/app.js"></script>'
        )

        rewritten = relativize_html_urls(html, "/docs/guides/forms")

        self.assertIn('href="../.."', rewritten)
        self.assertIn('src="../../../static/logo.png"', rewritten)
        self.assertIn('action="../../../search"', rewritten)
        self.assertIn('href="https://example.test/docs"', rewritten)
        self.assertIn('src="//cdn.example.test/app.js"', rewritten)
