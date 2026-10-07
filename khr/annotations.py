"""Read annotations from the Kindle's account-wide annotation database.

Since firmware ~5.19 the Kindle keeps highlights, notes and bookmarks in a
single SQLite file instead of per-book sidecars:

    system/ksdk/.annotations/amzn1.account.<ID>/ksdk_annotation_v1.db

The same annotation may appear in several tables (the synced copy, a staging
copy, unsynced local edits, device-only rows). We read every table we know,
let the most local copy win, and drop anything a local edit marks as deleted.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Read in this order; a later table overrides an earlier one for the same id.
# (table name, column that holds the book id)
SOURCES = (
    ("server_view", "dataset_id"),
    ("staging_server_view", "dataset_id"),
    ("nonsyncable_annotations", "book_id"),
    ("local_edit", "dataset_id"),
)

KIND_BY_TYPE = {"HIGHLIGHT": "highlight", "NOTE": "note", "BOOKMARK": "bookmark"}
KIND_BY_DATASET = {1: "highlight", 2: "bookmark", 3: "note"}


@dataclass
class Annotation:
    id: str
    asin: str
    kind: str                      # "highlight" | "note" | "bookmark"
    start: int
    end: int
    created: datetime | None = None
    modified: datetime | None = None
    note: str = ""
    color: str = ""

    @property
    def sort_key(self):
        return (self.start, self.end)


@dataclass
class BookAnnotations:
    asin: str
    highlights: list[Annotation] = field(default_factory=list)
    notes: list[Annotation] = field(default_factory=list)
    bookmarks: list[Annotation] = field(default_factory=list)

    @property
    def last_modified(self) -> datetime | None:
        stamps = [a.modified or a.created for a in self.highlights + self.notes]
        stamps = [s for s in stamps if s]
        return max(stamps) if stamps else None


def asin_from_book_id(book_id: str | None) -> str:
    """'B01ABCDEFG-PDOC-CR!XYZ-0' -> 'B01ABCDEFG'."""
    return (book_id or "").split("-", 1)[0]


def _ms_to_datetime(value) -> datetime | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000.0).astimezone()
    except (OverflowError, OSError, ValueError):
        return None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _parse_payload(raw: str, dataset, asin: str, ann_id: str) -> Annotation | None:
    try:
        p = json.loads(raw)
    except (TypeError, ValueError):
        return None
    kind = KIND_BY_TYPE.get(str(p.get("type", "")).upper()) or KIND_BY_DATASET.get(dataset)
    if not kind:
        return None
    start = (p.get("start_position") or {}).get("shortPosition")
    end = (p.get("end_position") or {}).get("shortPosition", start)
    if not isinstance(start, int):
        return None
    if not isinstance(end, int):
        end = start
    meta = {}
    if p.get("json_metadata"):
        try:
            meta = json.loads(p["json_metadata"]) or {}
        except (TypeError, ValueError):
            meta = {}
    return Annotation(
        id=ann_id,
        asin=asin,
        kind=kind,
        start=min(start, end),
        end=max(start, end),
        created=_ms_to_datetime(p.get("created_time")),
        modified=_ms_to_datetime(p.get("last_modified") or p.get("created_time")),
        note=str(meta.get("note_text") or ""),
        color=str(meta.get("mchl_color") or meta.get("color") or ""),
    )


def load(db_path: str | Path) -> dict[str, BookAnnotations]:
    """Load every annotation in the database, grouped by ASIN."""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        found: dict[str, Annotation] = {}
        deleted: set[str] = set()
        for table, book_col in SOURCES:
            cols = _columns(conn, table)
            if not {"annotation_id", "serialized_payload", book_col} <= cols:
                continue
            select = ["annotation_id", book_col, "serialized_payload"]
            select.append("dataset" if "dataset" in cols else "NULL")
            select.append("action" if "action" in cols else "NULL")
            sql = f"SELECT {', '.join(select)} FROM {table}"
            for ann_id, book_id, payload, dataset, action in conn.execute(sql):
                # Unsynced deletions show up as local edits. The exact action
                # codes aren't documented, so match on the word itself.
                if action is not None and "DEL" in str(action).upper():
                    deleted.add(ann_id)
                    found.pop(ann_id, None)
                    continue
                ann = _parse_payload(payload, dataset, asin_from_book_id(book_id), ann_id)
                if ann and ann.asin:
                    found[ann_id] = ann
    finally:
        conn.close()

    result: dict[str, BookAnnotations] = {}
    for ann_id, ann in found.items():
        if ann_id in deleted:
            continue
        book = result.setdefault(ann.asin, BookAnnotations(ann.asin))
        {"highlight": book.highlights, "note": book.notes,
         "bookmark": book.bookmarks}[ann.kind].append(ann)
    for book in result.values():
        for group in (book.highlights, book.notes, book.bookmarks):
            group.sort(key=lambda a: a.sort_key)
    return result
