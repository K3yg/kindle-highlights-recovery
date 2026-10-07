"""Glue: annotations + book file -> HTML / Markdown / JSON / My Clippings.txt."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import clippings, export, html_page
from .annotations import BookAnnotations
from .book import BookDRMError, BookError, file_contains_asin, open_book

FORMATS = ("html", "md", "json")


@dataclass
class Result:
    asin: str
    status: str                    # ok | drm | missing | error | empty
    title: str = ""
    message: str = ""
    quotes: int = 0
    added_to_clippings: int = 0
    files: list[Path] = field(default_factory=list)


def find_local_books(paths: list[Path], asins: set[str]) -> dict[str, Path]:
    """Map ASIN -> .kfx file by looking for the ASIN inside each file.

    paths may mix .kfx files and folders (searched recursively).
    """
    files = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files += sorted(p.rglob("*.kfx"))
        elif p.suffix.lower() == ".kfx":
            files.append(p)
    found: dict[str, Path] = {}
    for f in files:
        try:
            data = f.read_bytes()
        except OSError:
            continue
        for asin in asins - found.keys():
            if asin.encode("ascii") in data:
                found[asin] = f
    return found


def _unique_stem(outdir: Path, title: str, asin: str, taken: set[str]) -> str:
    stem = export.slugify(title)
    if stem == "book":
        # e.g. a title in a non-Latin script: keep files of different books
        # apart across runs too, not just within this one.
        stem = f"book-{asin[:10].lower()}"
    if stem in taken:
        stem = f"{stem}-{asin[:6].lower()}"
    taken.add(stem)
    return stem


def export_book(ann: BookAnnotations, kfx: Path, outdir: Path,
                clippings_file: Path | None = None, formats=FORMATS,
                taken: set[str] | None = None) -> Result:
    if taken is None:
        taken = set()
    if not file_contains_asin(kfx, ann.asin):
        return Result(ann.asin, "error", message=f"{kfx.name} is not the book {ann.asin}")
    try:
        book = open_book(kfx)
    except BookDRMError as exc:
        return Result(ann.asin, "drm", message=str(exc))
    except BookError as exc:
        return Result(ann.asin, "error", message=str(exc))

    exp = export.build(ann, book)
    res = Result(ann.asin, "ok", title=exp.title, quotes=len(exp.quotes))
    if not exp.quotes:
        res.status = "empty"
        res.message = "No highlight could be matched to the book's text."
        return res
    if exp.unresolved:
        res.message = f"{exp.unresolved} highlight(s) matched no text and were skipped."

    outdir.mkdir(parents=True, exist_ok=True)
    stem = _unique_stem(outdir, exp.title, ann.asin, taken)
    if "html" in formats:
        res.files.append(html_page.write(exp, outdir / f"{stem}.html"))
    if "md" in formats:
        res.files.append(export.write_markdown(exp, outdir / f"{stem}.md"))
    if "json" in formats:
        res.files.append(export.write_json(exp, outdir / f"{stem}.json"))
    if clippings_file is not None:
        res.added_to_clippings = clippings.append(clippings_file, exp)
    return res
