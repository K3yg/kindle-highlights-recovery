"""Open a KFX book with kfxlib and turn annotation positions into text.

A Kindle annotation is stored as a range of KFX positions. The book's text is
split into blocks (paragraphs, headings, ...), each starting at a position and
occupying one position per character. Resolving a highlight means slicing the
blocks that overlap its range.

kfxlib comes from John Howell's "KFX Input" calibre plugin (GPL v3), vendored
as vendor/KFX Input.zip and imported straight from the zip.
"""
from __future__ import annotations

import bisect
import contextlib
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

OBJECT_REPLACEMENT = "￼"


class BookError(Exception):
    """The book couldn't be read."""


class BookDRMError(BookError):
    """The book is DRM-protected (a purchased Kindle book)."""


# --------------------------------------------------------------------------
# kfxlib loading


def kfxlib_zip() -> Path:
    candidates = []
    if os.environ.get("KHR_KFXLIB"):
        candidates.append(Path(os.environ["KHR_KFXLIB"]))
    base = getattr(sys, "_MEIPASS", None)  # PyInstaller bundle
    if base:
        candidates.append(Path(base) / "vendor" / "KFX Input.zip")
    candidates.append(Path(__file__).resolve().parent.parent / "vendor" / "KFX Input.zip")
    for c in candidates:
        if c.is_file():
            return c
    raise BookError("kfxlib not found: expected vendor/KFX Input.zip "
                    "(or set KHR_KFXLIB to its path)")


_kfx = None


def _kfxlib():
    global _kfx
    if _kfx is None:
        path = str(kfxlib_zip())
        if path not in sys.path:
            sys.path.insert(0, path)
        from kfxlib import yj_book                    # noqa: E402
        from kfxlib.ion import IonAnnotation         # noqa: E402
        from kfxlib.yj_container import YJFragment   # noqa: E402
        _kfx = (yj_book, IonAnnotation, YJFragment)
    return _kfx


@contextlib.contextmanager
def _quiet():
    """kfxlib logs plenty of harmless warnings on the root logger."""
    root = logging.getLogger()
    level = root.level
    root.setLevel(logging.ERROR)
    try:
        yield
    finally:
        root.setLevel(level)


def _wrap_errors(exc: Exception) -> BookError:
    if "DRM" in type(exc).__name__ or "DRM" in str(exc)[:200]:
        return BookDRMError("This book is DRM-protected (a purchased Kindle book); "
                            "its text can't be decoded.")
    return BookError(f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# pure helpers (unit-tested without a real book)


@dataclass
class Block:
    start: int
    text: str

    @property
    def end(self) -> int:          # inclusive
        return self.start + len(self.text) - 1


def resolve_text(blocks: list[Block], start: int, end: int) -> str:
    """Text covered by positions start..end (inclusive).

    Blocks are separate paragraphs/elements, so a highlight that spans
    several of them gets a line break between the pieces.
    """
    if not blocks or end < start:
        return ""
    starts = [b.start for b in blocks]
    i = max(0, bisect.bisect_right(starts, start) - 1)
    pieces = []
    while i < len(blocks) and blocks[i].start <= end:
        b = blocks[i]
        if b.end >= start:
            a = max(start, b.start) - b.start
            z = min(end, b.end) - b.start + 1
            piece = b.text[a:z]
            piece = re.sub(r"\s+", " ", piece.replace(OBJECT_REPLACEMENT, " ")).strip()
            if piece:
                pieces.append(piece)
        i += 1
    return "\n".join(pieces)


def page_at(pages: list[tuple[int, str]], pos: int) -> str:
    """Label of the page containing pos; pages is sorted by start position."""
    if not pages:
        return ""
    i = bisect.bisect_right([p for p, _ in pages], pos) - 1
    return pages[i][1] if i >= 0 else ""


def clean_title(title: str, authors: list[str]) -> str:
    """Undo what "Send to Kindle" does to a personal document's title.

    Personal documents take their title from the uploaded file name, which
    often carries " - Author" at the end and "_" where a ":" used to be.
    """
    t = (title or "").strip()
    for a in authors or []:
        suffix = f" - {a}"
        if a and t.endswith(suffix):
            t = t[: -len(suffix)].rstrip()
            break
    t = t.replace("_ ", ": ")
    return t.rstrip("_ ").strip()


def title_from_filename(name: str) -> tuple[str, list[str]]:
    """'cortico, O - Aluisio Azevedo' -> ('O cortico', ['Aluisio Azevedo']).

    Sideloaded books are often named "Title, Article - Author"; move the
    trailing article back to the front.
    """
    stem = Path(name).stem if name.lower().endswith(".kfx") else name
    stem = re.sub(r"_[A-Z0-9]{10,}$", "", stem)          # trailing _ASIN
    title, authors = stem, []
    if " - " in stem:
        title, author = stem.rsplit(" - ", 1)
        authors = [author.strip()]
    m = re.match(r"^(.*), (O|A|Os|As|Um|Uma|The|An|El|La|Le|Les|Der|Die|Das)$", title.strip())
    if m:
        title = f"{m.group(2)} {m.group(1)}"
    return clean_title(title, authors), authors


# --------------------------------------------------------------------------
# reading a real book


@dataclass
class TocEntry:
    pos: int
    label: str
    depth: int


@dataclass
class Book:
    path: Path
    title: str
    authors: list[str]
    blocks: list[Block] = field(default_factory=list)
    pages: list[tuple[int, str]] = field(default_factory=list)
    toc: list[TocEntry] = field(default_factory=list)

    def text(self, start: int, end: int) -> str:
        return resolve_text(self.blocks, start, end)

    def page(self, pos: int) -> str:
        return page_at(self.pages, pos)

    def chapter(self, pos: int) -> str:
        """Deepest table-of-contents entry at or before pos."""
        best = None
        for e in self.toc:
            if e.pos <= pos and (best is None or e.pos >= best.pos):
                best = e
        return best.label if best else ""


def _unwrap(value, IonAnnotation, YJFragment):
    for _ in range(4):
        if isinstance(value, YJFragment) or isinstance(value, IonAnnotation):
            value = value.value
        else:
            break
    return value


def _read_blocks(path: Path) -> list[Block]:
    yj_book, _, _ = _kfxlib()
    raw = yj_book.YJ_Book(str(path)).convert_to_json_content()
    data = json.loads(raw.decode("utf-8")).get("data", [])
    blocks = [Block(e["position"], e.get("content") or "")
              for e in data if e.get("type") == 1 and e.get("content")]
    blocks.sort(key=lambda b: b.start)
    return blocks


def _read_navigation(path: Path) -> tuple[list[tuple[int, str]], list[TocEntry]]:
    yj_book, IonAnnotation, YJFragment = _kfxlib()
    book = yj_book.YJ_Book(str(path))
    book.decode_book(set_approximate_pages=0)

    eid_start = {}
    for chunk in book.collect_content_position_info():
        eid_start.setdefault(chunk.eid, chunk.pid - chunk.eid_offset)

    def target_pos(entry):
        target = _unwrap(entry.get("$246"), IonAnnotation, YJFragment) or {}
        eid = target.get("$155")
        if eid is None or eid not in eid_start:
            return None
        return eid_start[eid] + int(target.get("$143", 0) or 0)

    def label_of(entry):
        rep = _unwrap(entry.get("$241"), IonAnnotation, YJFragment) or {}
        return str(rep.get("$244", "")).strip()

    pages, toc = [], []
    nav = book.fragments.get("$389", first=True)
    if nav is None:
        return pages, toc
    readers = _unwrap(nav, IonAnnotation, YJFragment)
    for reading_order in readers if isinstance(readers, list) else [readers]:
        reading_order = _unwrap(reading_order, IonAnnotation, YJFragment)
        for container in reading_order.get("$392", []):
            # Inline containers come wrapped in an IonAnnotation; only what's
            # left after unwrapping and still isn't a struct is a reference.
            container = _unwrap(container, IonAnnotation, YJFragment)
            if not hasattr(container, "get"):
                container = book.fragments.get(ftype="$391", fid=container)
            data = _unwrap(container, IonAnnotation, YJFragment)
            if not hasattr(data, "get"):
                continue
            kind = data.get("$235")
            if kind == "$237":                       # page list
                for entry in data.get("$247", []):
                    entry = _unwrap(entry, IonAnnotation, YJFragment)
                    pos = target_pos(entry)
                    if pos is not None:
                        pages.append((pos, label_of(entry)))
            elif kind == "$212":                     # table of contents
                def walk(items, depth):
                    for item in items:
                        item = _unwrap(item, IonAnnotation, YJFragment)
                        pos = target_pos(item)
                        if pos is not None:
                            toc.append(TocEntry(pos, label_of(item), depth))
                        walk(item.get("$247", []), depth + 1)
                walk(data.get("$247", []), 0)
    pages.sort(key=lambda p: p[0])
    return pages, toc


def _read_metadata(path: Path) -> tuple[str, list[str]]:
    yj_book, _, _ = _kfxlib()
    try:
        md = yj_book.YJ_Book(str(path)).get_metadata()
        authors = [a for a in (md.authors or []) if a]
        if md.title:
            return clean_title(md.title, authors), authors
    except Exception:
        pass
    return title_from_filename(path.name)


def open_book(path: str | Path) -> Book:
    path = Path(path)
    if not path.is_file():
        raise BookError(f"Book file not found: {path}")
    with _quiet():
        try:
            blocks = _read_blocks(path)
        except Exception as exc:
            raise _wrap_errors(exc) from exc
        try:
            pages, toc = _read_navigation(path)
        except Exception:
            pages, toc = [], []                  # text still works without pages
        title, authors = _read_metadata(path)
    return Book(path, title, authors, blocks, pages, toc)


def file_contains_asin(path: str | Path, asin: str) -> bool:
    try:
        return asin.encode("ascii") in Path(path).read_bytes()
    except OSError:
        return False
