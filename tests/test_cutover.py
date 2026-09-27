"""
U9 cutover (2026-09-27): "/" serves the React app, and the classic UI is archived,
not deleted (archive/web-static-legacy/, with a README that says how to restore it).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parent.parent


class TestRoot(unittest.TestCase):

    def setUp(self):
        from web.server import app
        self.client = TestClient(app)

    def test_root_sends_you_to_the_react_app(self):
        with patch("web.server.FRONTEND_DIST", _ROOT / "web"):  # any existing directory
            r = self.client.get("/", follow_redirects=False)
        self.assertIn(r.status_code, (302, 307))
        self.assertEqual(r.headers["location"], "/app/")
        self.assertEqual(r.headers["cache-control"], "no-store")  # a cached "/" outlived the cutover once

    def test_without_a_build_it_says_how_to_make_one(self):
        with patch("web.server.FRONTEND_DIST", Path(tempfile.gettempdir()) / "no-such-dist-dir"):
            r = self.client.get("/")
        self.assertEqual(r.status_code, 503)
        self.assertIn("npm run build", r.text)

    def test_the_classic_ui_is_archived_with_a_way_back(self):
        archive = _ROOT / "archive" / "web-static-legacy"
        for name in ("index.html", "app.js", "style.css", "README.md"):
            self.assertTrue((archive / name).is_file(), name)
        self.assertFalse((_ROOT / "web" / "static" / "index.html").exists())
        self.assertIn("git mv archive/web-static-legacy/index.html web/static/index.html",
                      (archive / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
