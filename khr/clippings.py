"""Read and append to a Kindle-format "My Clippings.txt".

The file is only ever appended to: entries already present are left exactly
as they are. A new file is created with a UTF-8 byte order mark, like the
one the Kindle writes, so Windows editors don't mistake it for ANSI.

Duplicates are detected by the highlight text (ignoring whitespace) plus the
page. Locations are deliberately not used: the device writes real Kindle
locations, but the annotation database only has raw KFX positions, so the
same highlight carries different numbers depending on who wrote it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .export import BookExport

SEPARATOR = "=========="
NEWLINE = "\r\n"
BOM = "﻿"

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]

# "page 12" in the languages the Kindle writes clippings in.
PAGE_RE = re.compile(r"\b(?:page|página|pagina|seite|pagina|страница|ページ)\s*(\d+)", re.I)


@dataclass
class Entry:
    title: str
    meta: str
    text: str

    @property
    def page(self) -> str:
        m = PAGE_RE.search(self.meta)
        return m.group(1) if m else ""


def text_key(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def parse(path: str | Path) -> list[Entry]:
    path = Path(path)
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    entries = []
    for block in raw.split(SEPARATOR):
        lines = [l.replace("\r", "").replace(BOM, "").strip() for l in block.split("\n")]
        # Title, then the "- Your Highlight ..." line, then the text. Don't rely
        # on exact line positions: hand-edited or tool-written files sometimes
        # carry extra blank lines.
        filled = [i for i, l in enumerate(lines) if l]
        if len(filled) < 2:
            continue
        title, meta = lines[filled[0]], lines[filled[1]]
        text = "\n".join(lines[filled[1] + 1:]).strip()
        entries.append(Entry(title, meta, text))
    return entries


def format_date(dt: datetime) -> str:
    """'Saturday, August 15, 2026 3:04:42 AM' — independent of the system locale."""
    hour12 = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return (f"{WEEKDAYS[dt.weekday()]}, {MONTHS[dt.month - 1]} {dt.day}, {dt.year} "
            f"{hour12}:{dt.minute:02d}:{dt.second:02d} {ampm}")


def _entry_lines(title_line: str, kind: str, page: str, created, text: str) -> list[str]:
    label = "Your Note" if kind == "note" else "Your Highlight"
    meta = f"- {label} on page {page}" if page else f"- {label}"
    if created:
        meta += f" | Added on {format_date(created)}"
    # One clipping is one line of text on the device; keep it that way.
    return [title_line, meta, "", re.sub(r"\s*\n\s*", " ", text).strip()]


def new_entries(export: BookExport, existing: list[Entry]) -> list[list[str]]:
    seen: dict[str, set[str]] = {}
    for e in existing:
        seen.setdefault(text_key(e.text), set()).add(e.page)

    def is_dup(text: str, page: str) -> bool:
        pages = seen.get(text_key(text))
        if pages is None:
            return False
        # No page on either side: matching text alone counts as a duplicate.
        return not page or "" in pages or page in pages

    title_line = f"{export.title} ({export.author_line})" if export.authors else export.title
    out = []
    for q in export.quotes:
        items = [(q.kind, q.text)] + [("note", n) for n in q.notes]
        for kind, text in items:
            if is_dup(text, q.page):
                continue
            seen.setdefault(text_key(text), set()).add(q.page)
            out.append(_entry_lines(title_line, kind, q.page, q.created, text))
    return out


def append(path: str | Path, export: BookExport) -> int:
    """Append the book's missing entries; returns how many were added."""
    path = Path(path)
    entries = new_entries(export, parse(path))
    if not entries:
        return 0
    size = path.stat().st_size if path.exists() else 0
    needs_sep = False
    if size:
        with open(path, "rb") as f:
            f.seek(max(0, size - 32))
            needs_sep = SEPARATOR not in f.read().decode("utf-8", errors="replace")
    with open(path, "a", encoding="utf-8" if size else "utf-8-sig", newline="") as f:
        if needs_sep:
            f.write(NEWLINE + SEPARATOR + NEWLINE)
        for lines in entries:
            f.write(NEWLINE.join(lines) + NEWLINE + SEPARATOR + NEWLINE)
    return len(entries)
