"""Command line: python -m khr {gui,sync,list,export}."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import __version__, annotations, device, html_page, pipeline
from .book import title_from_filename

STATUS_TEXT = {
    "ok": "ok", "drm": "DRM-protected, skipped", "missing": "book file not found",
    "error": "error", "empty": "no text matched",
}


def _formats(value: str) -> tuple[str, ...]:
    chosen = tuple(f.strip().lower() for f in value.split(",") if f.strip())
    bad = [f for f in chosen if f not in pipeline.FORMATS]
    if bad:
        raise argparse.ArgumentTypeError(f"unknown format(s): {', '.join(bad)}")
    return chosen


def _report(results: list[pipeline.Result], clippings_file: Path | None,
            out: Path | None = None) -> int:
    print()
    ok = 0
    for r in results:
        name = r.title or r.asin
        line = f"  {STATUS_TEXT.get(r.status, r.status):<26} {name}"
        if r.status == "ok":
            ok += 1
            line = f"  {r.quotes:>4} highlights        {name}"
        print(line)
        if r.message:
            print(f"       {r.message}")
    if clippings_file is not None:
        added = sum(r.added_to_clippings for r in results)
        print(f"\nMy Clippings.txt: {added} new entr{'y' if added == 1 else 'ies'} -> {clippings_file}")
    index = html_page.write_index(out) if out is not None and out.is_dir() else None
    if index:
        print(f"Index of all exported books: {index}")
    return 0 if ok or not results else 1


def _select(books: dict, asins: list[str] | None) -> list:
    if not asins:
        return sorted(books.values(), key=lambda b: -len(b.highlights))
    missing = [a for a in asins if a not in books]
    if missing:
        print(f"Not in the database: {', '.join(missing)}", file=sys.stderr)
    return [books[a] for a in asins if a in books]


def cmd_list(args) -> int:
    books = annotations.load(args.db)
    local = pipeline.find_local_books(args.books, set(books)) if args.books else {}
    print(f"{'ASIN':<34} {'hl':>4} {'notes':>5}  book file")
    for b in sorted(books.values(), key=lambda b: -len(b.highlights)):
        f = local.get(b.asin)
        name = title_from_filename(f.name)[0] if f else "-"
        print(f"{b.asin:<34} {len(b.highlights):>4} {len(b.notes):>5}  {name}")
    return 0


def cmd_export(args) -> int:
    books = annotations.load(args.db)
    chosen = _select(books, args.asin)
    local = pipeline.find_local_books(args.books, {b.asin for b in chosen})
    clip = None if args.no_clippings else (args.clippings or args.out / "My Clippings.txt")
    results, taken = [], set()
    for b in chosen:
        if not b.highlights and not b.notes:
            continue
        kfx = local.get(b.asin)
        if not kfx:
            results.append(pipeline.Result(b.asin, "missing"))
            continue
        print(f"Exporting {kfx.name}…")
        results.append(pipeline.export_book(b, kfx, args.out, clip, args.format, taken))
    return _report(results, clip, args.out)


def cmd_sync(args) -> int:
    out: Path = args.out
    work = out / ".kindle"                        # raw copies from the device
    print("Copying the annotation database from the Kindle…")
    db = device.pull_database(work)
    books = annotations.load(db)
    chosen = [b for b in _select(books, args.asin) if b.highlights or b.notes]
    print(f"{len(books)} books have annotations; exporting {len(chosen)}.")

    clip = None if args.no_clippings else out / "My Clippings.txt"
    listing = device.list_books()
    if clip is not None and not clip.exists():
        # Start from the device's own file, so entries it wrote before the
        # firmware change are kept; we only append what's missing.
        src = device.pull_clippings(work)
        if src:
            out.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, clip)
            print("Started from the Kindle's own My Clippings.txt.")

    results, taken = [], set()
    for b in chosen:
        title = listing.titles.get(b.asin, b.asin)
        print(f"{title}")
        kfx = device.pull_book(b.asin, title, listing.books, work)
        if not kfx:
            results.append(pipeline.Result(b.asin, "missing", title=title,
                                           message="No matching .kfx on the Kindle."))
            continue
        results.append(pipeline.export_book(b, kfx, out, clip, args.format, taken))
    return _report(results, clip, out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="khr",
        description="Recover Kindle highlights the device stopped writing to My Clippings.txt.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="command")

    sub.add_parser("gui", help="open the graphical interface")

    def common(p):
        p.add_argument("--asin", nargs="+", help="only these books (default: all)")
        p.add_argument("--out", type=Path, default=Path("kindle-highlights"),
                       help="output folder (default: ./kindle-highlights)")
        p.add_argument("--format", type=_formats, default=pipeline.FORMATS,
                       help="comma-separated: html,md,json (default: all)")
        p.add_argument("--no-clippings", action="store_true",
                       help="don't write My Clippings.txt")

    p = sub.add_parser("sync", help="Windows: read everything straight from a connected Kindle")
    common(p)

    p = sub.add_parser("list", help="list books in an annotation database")
    p.add_argument("--db", type=Path, required=True, help="ksdk_annotation_v1.db")
    p.add_argument("--books", type=Path, nargs="*", help=".kfx files or folders, to show titles")

    p = sub.add_parser("export", help="export from files you copied off the Kindle")
    p.add_argument("--db", type=Path, required=True, help="ksdk_annotation_v1.db")
    p.add_argument("--books", type=Path, nargs="+", required=True,
                   help=".kfx files and/or folders containing them")
    p.add_argument("--clippings", type=Path, help="My Clippings.txt to update "
                   "(default: <out>/My Clippings.txt)")
    common(p)

    args = ap.parse_args(argv)
    if args.command in (None, "gui"):
        from .gui import main as gui_main
        return gui_main()
    try:
        return {"sync": cmd_sync, "list": cmd_list, "export": cmd_export}[args.command](args)
    except (device.DeviceError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
