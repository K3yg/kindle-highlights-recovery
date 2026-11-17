"""Book identification and localisation, without a Kindle: python -m unittest -v"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from khr import device, html_page, i18n
from khr.export import BookExport, Quote

A1 = "5IOZM7DCMSG9NZE79UY3F2RW3KNY6J0D"
A2 = "R5XETLSP8NFLQ0ORGS04FTNQ4Y5WJ8FZ"


class Listing(unittest.TestCase):
    def test_keeps_kfx_books_and_drops_clippings_and_folders(self):
        items = [
            {"path": "Noites brancas - Fiodor Dostoievski.kfx", "type": "KFX File", "size": 10},
            {"path": "Sidarta - Hermann Hesse", "type": "KFX File", "size": 5},
            {"path": "Sidarta - Hermann Hesse.sdr", "folder": True, "type": "sdr"},
            {"path": "My Clippings.kfx", "type": "KFX File", "size": 1},
            {"path": "My Clippings.txt", "type": "Text Document", "size": 1},
            {"path": "Orphan", "type": "", "size": 3},
            {"path": "With Sidecar", "type": "", "size": 3},
            {"path": "With Sidecar.sdr", "folder": True, "type": "sdr"},
        ]
        books = device.parse_listing(items)
        self.assertEqual([b.path for b in books],
                         ["Noites brancas - Fiodor Dostoievski.kfx", "Sidarta - Hermann Hesse",
                          "With Sidecar"])
        self.assertEqual(books[0].display_title, "Noites brancas")

    def test_asins_in_name(self):
        self.assertEqual(device.asins_in_name(f"Book_{A1}.kfx"), [A1])
        self.assertEqual(device.asins_in_name("Downloads/Items01/B0ABCDEF12/x.kfx"), ["B0ABCDEF12"])
        self.assertEqual(device.asins_in_name("Noites brancas - Fiodor Dostoievski.kfx"), [])


class Identify(unittest.TestCase):
    """identify() must map files to ASINs by content and copy each file once."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.docs, self.work = root / "docs", root / "work"
        self.docs.mkdir()
        content = {"Alpha - Ann.kfx": f"xx{A1}yy{A1}", "Beta - Bob.kfx": f"zz{A2}",
                   "Gamma - Cy.kfx": "no asin", f"Delta_{A2[:-1]}Q.kfx": "named"}
        for name, text in content.items():
            (self.docs / name).write_text(text, encoding="ascii")
        self.copied = []

        def stream(action, on_event, **p):
            for rel in Path(p["ListFile"]).read_text(encoding="utf-8").splitlines():
                self.copied.append(rel)
                dst = Path(p["Dest"]) / rel
                dst.write_bytes((self.docs / rel).read_bytes())
                on_event({"event": "file", "rel": rel, "path": str(dst)})
            return {"ok": True}
        patch = mock.patch.object(device, "_run_stream", stream)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def books(self):
        return [device.DeviceBook(f.name, f.stat().st_size) for f in sorted(self.docs.iterdir())]

    def test_maps_by_content_and_caches(self):
        books = self.books()
        device.identify(books, {A1, A2}, self.work, log=lambda m: None)
        got = {b.name: b.asin for b in books}
        self.assertEqual(got["Alpha - Ann.kfx"], A1)
        self.assertEqual(got["Beta - Bob.kfx"], A2)
        self.assertIsNone(got[f"Delta_{A2[:-1]}Q.kfx"])     # an ASIN with no highlights
        self.assertNotIn(f"Delta_{A2[:-1]}Q.kfx", self.copied)

        first = len(self.copied)
        books = self.books()
        cache = device.identify(books, {A1, A2}, self.work, log=lambda m: None)
        self.assertEqual(len(self.copied), first)            # nothing copied again
        self.assertEqual({b.name: b.asin for b in books}, got)
        alpha = next(b for b in books if b.asin == A1)
        self.assertEqual(device.pull_book(alpha, cache).read_text(), f"xx{A1}yy{A1}")

    def test_stops_copying_once_every_asin_is_found(self):
        books = self.books()
        with mock.patch.object(device, "BATCH", 1):
            device.identify(books, {A1}, self.work, log=lambda m: None)
        self.assertEqual(self.copied, ["Alpha - Ann.kfx"])

    def test_changed_file_is_copied_again(self):
        device.identify(self.books(), {A1, A2}, self.work, log=lambda m: None)
        (self.docs / "Alpha - Ann.kfx").write_text(f"new{A1}longer", encoding="ascii")
        self.copied.clear()
        device.identify(self.books(), {A1, A2}, self.work, log=lambda m: None)
        self.assertIn("Alpha - Ann.kfx", self.copied)


class Localisation(unittest.TestCase):
    def test_every_language_has_every_key(self):
        for table in (i18n.STRINGS, i18n.PAGE):
            for lang in table:
                self.assertEqual(set(table[lang]), set(table["en"]), lang)

    def test_html_page_in_portuguese(self):
        exp = BookExport("X", "Livro </script>", ["Autora"],
                         [Quote("highlight", "um __TITLE__ grifo", "3", "", 0, 1, None)])
        with tempfile.TemporaryDirectory() as d:
            page = html_page.write(exp, Path(d) / "x.html", "pt").read_text(encoding="utf-8")
            (Path(d) / "x.json").write_text(json.dumps(
                {"asin": "X", "title": "Livro", "quotes": [{"type": "highlight"}]}), encoding="utf-8")
            index = html_page.write_index(Path(d), "pt").read_text(encoding="utf-8")
        self.assertIn('lang="pt-BR"', page)
        self.assertIn("Buscar nos grifos…", page)
        self.assertIn("um __TITLE__ grifo", page)
        self.assertNotIn("__S_", page)
        self.assertIn("1 livro · 1 grifo", index)


if __name__ == "__main__":
    unittest.main()
