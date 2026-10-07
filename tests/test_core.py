"""Tests that need no Kindle and no real book: python -m unittest -v"""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from khr import annotations, clippings, html_page
from khr.annotations import Annotation, BookAnnotations
from khr.book import Block, clean_title, page_at, resolve_text, title_from_filename
from khr.export import BookExport, Quote, attach_notes

ASIN = "B0FAKEASIN0000000000000000000001"


class TextResolution(unittest.TestCase):
    blocks = [Block(10, "Call me Ishmael."), Block(26, "Some years ago\nnever mind."),
              Block(60, "Chapter two ￼ begins.")]

    def test_inside_one_block_inclusive_end(self):
        self.assertEqual(resolve_text(self.blocks, 18, 24), "Ishmael")

    def test_spanning_blocks_keeps_paragraph_break(self):
        self.assertEqual(resolve_text(self.blocks, 18, 35), "Ishmael.\nSome years")

    def test_whitespace_and_object_replacement_cleaned(self):
        self.assertEqual(resolve_text(self.blocks, 26, 52), "Some years ago never mind.")
        self.assertEqual(resolve_text(self.blocks, 60, 81), "Chapter two begins.")

    def test_out_of_range_and_gaps(self):
        self.assertEqual(resolve_text(self.blocks, 0, 5), "")
        self.assertEqual(resolve_text(self.blocks, 54, 58), "")
        self.assertEqual(resolve_text([], 0, 10), "")


class Pages(unittest.TestCase):
    pages = [(1, "i"), (100, "1"), (250, "2")]

    def test_lookup(self):
        self.assertEqual(page_at(self.pages, 0), "")
        self.assertEqual(page_at(self.pages, 100), "1")
        self.assertEqual(page_at(self.pages, 249), "1")
        self.assertEqual(page_at(self.pages, 9999), "2")
        self.assertEqual(page_at([], 5), "")


class Titles(unittest.TestCase):
    def test_sideload_names(self):
        self.assertEqual(title_from_filename("cortico, O - Aluisio Azevedo"),
                         ("O cortico", ["Aluisio Azevedo"]))
        self.assertEqual(title_from_filename("Pride and Prejudice_ A Novel - Jane Austen")[0],
                         "Pride and Prejudice: A Novel")
        self.assertEqual(title_from_filename("Pride and Prejudice_B0FAKEASIN00000000.kfx")[0],
                         "Pride and Prejudice")

    def test_clean_title_only_strips_real_author(self):
        self.assertEqual(clean_title("Emma - Jane Austen", ["Jane Austen"]), "Emma")
        self.assertEqual(clean_title("War - and Peace", ["Leo Tolstoy"]), "War - and Peace")


def ann(id, kind, start, end, note=""):
    return Annotation(id, ASIN, kind, start, end, datetime(2026, 1, 2, 3, 4, 5).astimezone(), None, note)


class Notes(unittest.TestCase):
    def test_note_goes_to_narrowest_containing_highlight(self):
        wide, narrow = ann("h1", "highlight", 10, 100), ann("h2", "highlight", 40, 60)
        inside, loose = ann("n1", "note", 60, 60, "mine"), ann("n2", "note", 500, 500, "alone")
        attached, rest = attach_notes([wide, narrow], [inside, loose])
        self.assertEqual([n.id for n in attached["h2"]], ["n1"])
        self.assertNotIn("h1", attached)
        self.assertEqual([n.id for n in rest], ["n2"])


def make_db(path: Path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE nonsyncable_annotations (annotation_id, book_id, dataset, "
                "start_position, end_position, created_time, modified_time, serialized_payload)")
    con.execute("CREATE TABLE local_edit (annotation_id, action, dataset, dataset_id, "
                "serialized_payload, dirty_flag, created_time, modified_time)")

    def payload(kind, start, end, meta=None):
        return json.dumps({"type": kind, "created_time": 1782000000000,   # June 2026
                           "start_position": {"shortPosition": start},
                           "end_position": {"shortPosition": end},
                           "json_metadata": json.dumps(meta or {})})
    book = f"{ASIN}-PDOC-CR!FAKE-0"
    rows = [("a", 1, payload("HIGHLIGHT", 30, 10, {"mchl_color": "yellow"})),
            ("b", 3, payload("NOTE", 20, 20, {"note_text": "a thought"})),
            ("c", 2, payload("BOOKMARK", 5, 5)),
            ("d", 1, payload("HIGHLIGHT", 40, 50))]
    for aid, ds, p in rows:
        con.execute("INSERT INTO nonsyncable_annotations VALUES (?,?,?,?,?,?,?,?)",
                    (aid, book, ds, None, None, None, None, p))
    con.execute("INSERT INTO local_edit VALUES (?,?,?,?,?,?,?,?)",
                ("d", "DELETE", 1, book, payload("HIGHLIGHT", 40, 50), 1, None, None))
    con.commit()
    con.close()


class Database(unittest.TestCase):
    def test_load_groups_orders_and_honours_deletions(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "ksdk_annotation_v1.db"
            make_db(db)
            books = annotations.load(db)
        b = books[ASIN]
        self.assertEqual([(h.id, h.start, h.end, h.color) for h in b.highlights], [("a", 10, 30, "yellow")])
        self.assertEqual([(n.id, n.note) for n in b.notes], [("b", "a thought")])
        self.assertEqual(len(b.bookmarks), 1)
        self.assertEqual(b.highlights[0].created.year, 2026)


def export(*quotes):
    return BookExport(ASIN, "Moby-Dick", ["Herman Melville"], list(quotes))


def quote(text, page="12", notes=()):
    return Quote("highlight", text, page, "", 0, 0, datetime(2026, 8, 15, 15, 4, 42), notes=list(notes))


class Clippings(unittest.TestCase):
    def test_date_is_locale_independent(self):
        self.assertEqual(clippings.format_date(datetime(2026, 8, 15, 3, 4, 42)),
                         "Saturday, August 15, 2026 3:04:42 AM")
        self.assertEqual(clippings.format_date(datetime(2026, 8, 15, 12, 0, 0)),
                         "Saturday, August 15, 2026 12:00:00 PM")

    def test_new_file_has_one_bom_crlf_and_dedups(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "My Clippings.txt"
            exp = export(quote("Call me Ishmael."), quote("Two\nparagraphs", "13", ["a note"]))
            self.assertEqual(clippings.append(f, exp), 3)
            raw = f.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            self.assertIn(b"- Your Highlight on page 12 | Added on Saturday, August 15, 2026 3:04:42 PM\r\n", raw)
            self.assertIn(b"- Your Note on page 13", raw)
            self.assertIn("Two paragraphs".encode(), raw)          # one line per clipping
            self.assertEqual(clippings.append(f, exp), 0)           # nothing new
            self.assertEqual(f.read_bytes().count(b"\xef\xbb\xbf"), 1)
            # spacing differences are still the same highlight
            self.assertEqual(clippings.append(f, export(quote("Call me  Ishmael ."))), 0)
            # same text on another page is a different clipping
            self.assertEqual(clippings.append(f, export(quote("Call me Ishmael.", "99"))), 1)

    def test_dedups_against_entries_the_device_wrote_in_portuguese(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "My Clippings.txt"
            f.write_bytes(("﻿Moby-Dick (Herman Melville)\r\n"
                           "- Seu destaque na página 12 | posição 180-181 | Adicionado: sábado\r\n"
                           "\r\nCall me Ishmael.\r\n==========\r\n").encode("utf-8"))
            self.assertEqual(clippings.parse(f)[0].page, "12")
            self.assertEqual(clippings.append(f, export(quote("Call me Ishmael."))), 0)

    def test_tolerates_extra_blank_lines(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "My Clippings.txt"
            f.write_bytes(b"\r\n\r\nMoby-Dick (Herman Melville)\r\r\n\r\n"
                          b"- Your Highlight on page 7 | Added on Monday\r\n\r\n\r\n"
                          b"Call me Ishmael.\r\n==========\r\n")
            e = clippings.parse(f)[0]
            self.assertEqual((e.title, e.page, e.text),
                             ("Moby-Dick (Herman Melville)", "7", "Call me Ishmael."))


class Html(unittest.TestCase):
    def test_page_escapes_title_and_script_breakouts(self):
        with tempfile.TemporaryDirectory() as d:
            exp = BookExport(ASIN, "<b>Bold</b> __DATA__", [], [quote("ends </script><script>alert(1)")])
            page = html_page.write(exp, Path(d) / "x.html").read_text(encoding="utf-8")
        self.assertIn("&lt;b&gt;Bold&lt;/b&gt; __DATA__", page)
        self.assertNotIn("</script><script>alert", page)
        self.assertEqual(page.count("const DATA = "), 1)


if __name__ == "__main__":
    unittest.main()
