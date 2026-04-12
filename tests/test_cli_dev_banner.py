import unittest

from sprag.dev.cli import _dev_surface_banner_line, _join_base_url


class DevBannerTests(unittest.TestCase):
    def test_join_base_url_handles_root_and_nested_paths(self):
        self.assertEqual(_join_base_url("http://127.0.0.1:8000", "/"), "http://127.0.0.1:8000/")
        self.assertEqual(
            _join_base_url("http://127.0.0.1:8000", "/counter"),
            "http://127.0.0.1:8000/counter",
        )

    def test_dev_banner_line_uses_clickable_url_for_concrete_path(self):
        line = _dev_surface_banner_line(
            "http://127.0.0.1:8000",
            "/counter",
            label="hybrid",
            width=len("/counter"),
        )
        self.assertIn("http://127.0.0.1:8000/counter", line)

    def test_dev_banner_line_marks_dynamic_paths_as_patterns(self):
        line = _dev_surface_banner_line(
            "http://127.0.0.1:8000",
            "/blog/[slug]",
            label="hybrid",
            width=len("/blog/[slug]"),
        )
        self.assertIn("-> pattern", line)
        self.assertNotIn("http://127.0.0.1:8000/blog/[slug]", line)


if __name__ == "__main__":
    unittest.main()
