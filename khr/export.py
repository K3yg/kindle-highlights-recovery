"""Combine a book's annotations with its text, and write JSON / Markdown."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .annotations import Annotation, BookAnnotations
from .book import Book


@dataclass
class Quote:
    kind: str                     # "highlight" or "note" (a note with no highlight)
    text: str
    page: str
    chapter: str
    start: int
    end: int
    created: datetime | None
    color: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class BookExport:
    asin: str
    title: str
    authors: list[str]
    quotes: list[Quote]
    unresolved: int = 0           # highlights whose positions matched no text

    @property
    def author_line(self) -> str:
        return "; ".join(self.authors)

    @property
    def highlight_count(self) -> int:
        return sum(1 for q in self.quotes if q.kind == "highlight")


def attach_notes(highlights: list[Annotation], notes: list[Annotation]):
    """Pair each note with the highlight it was written on.

    The Kindle anchors a note at a single position inside (usually at the end
    of) the highlighted span. A note inside several spans goes to the
    narrowest one. Returns ({highlight id: [note, ...]}, [unattached notes]).
    """
    attached: dict[str, list[Annotation]] = {}
    loose = []
    for n in notes:
        hosts = [h for h in highlights if h.start <= n.start <= h.end]
        if hosts:
            host = min(hosts, key=lambda h: h.end - h.start)
            attached.setdefault(host.id, []).append(n)
        else:
            loose.append(n)
    return attached, loose


def build(annotations: BookAnnotations, book: Book) -> BookExport:
    attached, loose = attach_notes(annotations.highlights, annotations.notes)
    quotes, unresolved = [], 0
    for h in annotations.highlights:
        text = book.text(h.start, h.end)
        if not text:
            unresolved += 1
            continue
        quotes.append(Quote(
            kind="highlight", text=text, page=book.page(h.start),
            chapter=book.chapter(h.start), start=h.start, end=h.end,
            created=h.created, color=h.color,
            notes=[n.note for n in attached.get(h.id, []) if n.note.strip()],
        ))
    for n in loose:
        if n.note.strip():
            quotes.append(Quote(
                kind="note", text=n.note.strip(), page=book.page(n.start),
                chapter=book.chapter(n.start), start=n.start, end=n.end,
                created=n.created,
            ))
    quotes.sort(key=lambda q: (q.start, q.end))
    return BookExport(annotations.asin, book.title, book.authors, quotes, unresolved)


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()[:80] or "book"


def write_json(export: BookExport, path: Path) -> Path:
    data = {
        "asin": export.asin,
        "title": export.title,
        "authors": export.authors,
        "quotes": [{
            "type": q.kind, "text": q.text, "page": q.page or None,
            "chapter": q.chapter or None, "start": q.start, "end": q.end,
            "created": q.created.isoformat() if q.created else None,
            "color": q.color or None, "notes": q.notes,
        } for q in export.quotes],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_markdown(export: BookExport, path: Path) -> Path:
    out = [f"# {export.title}", ""]
    if export.authors:
        out += [f"*{export.author_line}*", ""]
    for q in export.quotes:
        if q.kind == "note":
            out.append(f"**Note:** {q.text}")
        else:
            out += [f"> {line}" if line else ">" for line in q.text.split("\n")]
            for n in q.notes:
                out.append(f"\n**Note:** {n}")
        meta = [f"p. {q.page}" if q.page else "", q.created.strftime("%Y-%m-%d") if q.created else ""]
        meta = " · ".join(m for m in meta if m)
        if meta:
            out.append(f"\n<sub>{meta}</sub>")
        out.append("")
    path.write_text("\n".join(out), encoding="utf-8")
    return path
