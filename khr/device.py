"""Copy files off a USB-connected Kindle (Windows, via MTP + PowerShell).

On macOS and Linux, copy the files by hand instead (see the README) and use
the offline mode: the rest of the tool is cross-platform.

Which file is which book
------------------------
Annotations are keyed by the book's ASIN, but sideloaded and Send-to-Kindle
files are usually named "Title - Author.kfx", with no ASIN in sight. The only
reliable link is the ASIN written *inside* the file, so a book is identified
by copying it once and searching its bytes. Copies are kept in a cache folder
(with an index keyed by device path and size), so later runs copy only books
that are new or changed. Files whose name already carries an ASIN are
identified from the name alone.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .book import title_from_filename

SCRIPT = Path(__file__).resolve().parent / "kindle_mtp.ps1"
# Store ASINs ("B0" + 8) and personal-document ids (32 chars).
ASIN_RE = re.compile(r"(?<![A-Za-z0-9])(B[0-9A-Z]{9}|[0-9A-Z]{32})(?![A-Za-z0-9])")
BATCH = 4                    # files per PowerShell call while identifying


class DeviceError(Exception):
    pass


def supported() -> bool:
    return sys.platform == "win32"


def _command(action: str, **params) -> list[str]:
    if not supported():
        raise DeviceError("Reading the Kindle directly is only supported on Windows. "
                          "Copy the files by hand and use offline mode.")
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
           "-File", str(SCRIPT), "-Action", action]
    for key, value in params.items():
        cmd += [f"-{key}", str(value)]
    return cmd


def _parse_final(out: str, err: str, code: int) -> dict:
    line = next((l for l in reversed(out.splitlines()) if l.startswith("{")), "")
    try:
        data = json.loads(line)
    except ValueError:
        raise DeviceError(err.strip() or out.strip() or f"PowerShell failed ({code})")
    if not data.get("ok"):
        raise DeviceError(data.get("error") or "Unknown device error")
    return data


def _run(action: str, timeout: int = 600, **params) -> dict:
    cmd = _command(action, **params)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=flags)
    return _parse_final(proc.stdout.decode("utf-8", errors="replace"),
                        proc.stderr.decode("utf-8", errors="replace"), proc.returncode)


def _run_stream(action: str, on_event: Callable[[dict], None], **params) -> dict:
    """Like _run, but calls on_event for every {"event": ...} line as it arrives."""
    cmd = _command(action, **params)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=flags)
    lines = []
    for raw in proc.stdout:
        line = raw.decode("utf-8", errors="replace").strip()
        lines.append(line)
        if line.startswith('{"') and '"event"' in line:
            try:
                on_event(json.loads(line))
            except ValueError:
                pass
    err = proc.stderr.read().decode("utf-8", errors="replace")
    proc.wait()
    finals = [l for l in lines if l.startswith("{") and '"event"' not in l]
    return _parse_final("\n".join(finals), err, proc.returncode)


def status() -> None:
    _run("status", timeout=60)


def connected() -> bool:
    try:
        status()
        return True
    except (DeviceError, subprocess.SubprocessError, OSError):
        return False


def pull_database(dest: Path) -> Path:
    """Copy the annotation database(s) into dest; return the main .db file."""
    dest.mkdir(parents=True, exist_ok=True)
    data = _run("pull-db", Dest=dest)
    names = [f["name"] for f in data.get("files", [])]
    dbs = sorted(n for n in names if re.fullmatch(r"ksdk_annotation_v\d+(\.db)?", n))
    if not dbs:
        raise DeviceError(f"Copied {names}, but none is the annotation database.")
    main = dest / dbs[-1]                     # highest version
    if main.suffix != ".db":
        fixed = main.with_name(main.name + ".db")
        os.replace(main, fixed)
        main = fixed
    return main


def pull_clippings(dest: Path) -> Path | None:
    """Copy documents/My Clippings.txt off the Kindle, if it has one."""
    for name in ("My Clippings.txt", "My Clippings"):
        try:
            return Path(_run("pull-file", Path=name, Dest=dest)["path"])
        except DeviceError:
            continue
    return None


# --------------------------------------------------------------------------
# the book list


@dataclass
class DeviceBook:
    path: str                 # relative to documents/, as the device names it
    size: int
    asin: str | None = None   # set by identify(); None = no annotated book
    title: str = ""

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def display_title(self) -> str:
        return self.title or title_from_filename(self.name)[0] or self.name


def parse_listing(items: list[dict]) -> list[DeviceBook]:
    """Book files from the list-books output (pure, unit-tested)."""
    sdr_names = {i["path"][:-4] for i in items if i.get("type") == "sdr"}
    books = []
    for it in items:
        if it.get("folder") or it.get("type") == "sdr":
            continue
        name = it["path"].rsplit("/", 1)[-1]
        kind = (it.get("type") or "").upper()
        stem = it["path"][:-4] if name.lower().endswith(".kfx") else it["path"]
        # Explorer hides the extension but reports the type ("KFX File"). If
        # the type is missing, fall back to "has a matching .sdr folder".
        if "KFX" in kind or name.lower().endswith(".kfx") or (not kind and stem in sdr_names):
            if not name.startswith("My Clippings"):
                books.append(DeviceBook(it["path"], int(it.get("size") or 0)))
    books.sort(key=lambda b: b.display_title.lower())
    return books


def list_books() -> list[DeviceBook]:
    return parse_listing(_run("list-books", timeout=300).get("items", []))


def asins_in_name(path: str) -> list[str]:
    return ASIN_RE.findall(path)


def asin_in_file(path: Path, asins: set[str]) -> str | None:
    """The annotated ASIN this file is, by searching its bytes."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    hits = {a: data.count(a.encode("ascii")) for a in asins}
    hits = {a: n for a, n in hits.items() if n}
    return max(hits, key=hits.get) if hits else None


class Cache:
    """Local copies of device books, keyed by device path + size."""

    def __init__(self, work: Path):
        self.dir = work / "books"
        self.file = work / "index.json"
        try:
            self.data = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        if not isinstance(self.data, dict):
            self.data = {}

    def save(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")

    def local(self, book: DeviceBook) -> Path | None:
        entry = self.data.get(book.path) or {}
        if entry.get("size") != book.size or not entry.get("local"):
            return None
        p = self.dir / entry["local"]
        return p if p.is_file() else None

    def title(self, book: DeviceBook) -> str:
        entry = self.data.get(book.path) or {}
        return entry.get("title", "") if entry.get("size") == book.size else ""

    def set_title(self, book: DeviceBook, title: str):
        entry = self.data.get(book.path)
        if entry is not None and title:
            entry["title"] = title
            self.save()

    def _target_name(self, book: DeviceBook) -> str:
        name = book.name if book.name.lower().endswith(".kfx") else book.name + ".kfx"
        used = {e.get("local") for p, e in self.data.items() if p != book.path}
        stem, n = name[:-4], 2
        while name in used:
            name, n = f"{stem} ({n}).kfx", n + 1
        return name

    def fetch(self, books: list[DeviceBook], log: Callable[[str], None] = print,
              on_file: Callable[[DeviceBook, Path | None], None] | None = None):
        """Copy books that aren't cached yet; calls on_file as each one lands."""
        todo = [b for b in books if not self.local(b)]
        for b in books:
            if b not in todo and on_file:
                on_file(b, self.local(b))
        stage = self.dir / ".incoming"
        while todo:
            # One batch never holds two files with the same name: they'd land
            # on top of each other in the staging folder.
            batch, names = [], set()
            for b in list(todo):
                if len(batch) < BATCH and b.name not in names:
                    batch.append(b)
                    names.add(b.name)
                    todo.remove(b)
            by_rel = {b.path: b for b in batch}
            stage.mkdir(parents=True, exist_ok=True)
            fd, listfile = tempfile.mkstemp(suffix=".txt", prefix="khr-")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(by_rel))

            def landed(ev):
                b = by_rel.get(ev.get("rel"))
                if b is None:
                    return
                got = None
                if ev.get("path") and not ev.get("error"):
                    target = self._target_name(b)
                    self.dir.mkdir(parents=True, exist_ok=True)
                    os.replace(ev["path"], self.dir / target)
                    self.data[b.path] = {"size": b.size, "local": target}
                    self.save()
                    got = self.dir / target
                else:
                    log(f"  ✗ {b.name}: {ev.get('error')}")
                if on_file:
                    on_file(b, got)

            for b in batch:
                log(f"  copying {b.name}…")
            try:
                _run_stream("pull-files", landed, ListFile=listfile, Dest=stage)
            finally:
                os.unlink(listfile)


def identify(books: list[DeviceBook], asins: set[str], work: Path,
             progress: Callable[[int, int], None] | None = None,
             log: Callable[[str], None] = print) -> Cache:
    """Set .asin on every book that has annotations.

    Copies (once) only what's needed: books whose name has no ASIN, and only
    until every annotated ASIN has been found.
    """
    cache = Cache(work)
    unknown: list[DeviceBook] = []
    for b in books:
        b.title = cache.title(b)
        in_name = asins_in_name(b.path)
        hit = next((a for a in in_name if a in asins), None)
        if hit:
            b.asin = hit
        elif in_name:
            b.asin = None                       # an ASIN with no annotations
        else:
            unknown.append(b)

    # Cached copies first: free to check.
    for b in [b for b in unknown if cache.local(b)]:
        b.asin = asin_in_file(cache.local(b), asins)
        unknown.remove(b)

    found = {b.asin for b in books if b.asin}
    total, done = len(unknown), 0
    if progress:
        progress(0, total)
    while unknown and not asins <= found:
        batch, unknown = unknown[:BATCH], unknown[BATCH:]

        def landed(b, local):
            nonlocal done
            done += 1
            if local:
                b.asin = asin_in_file(local, asins - found) or asin_in_file(local, asins)
                if b.asin:
                    found.add(b.asin)
            if progress:
                progress(done, total)
        cache.fetch(batch, log=log, on_file=landed)
    return cache


def pull_book(book: DeviceBook, cache: Cache, log: Callable[[str], None] = print) -> Path | None:
    """Local copy of the book (copying it off the Kindle if needed)."""
    local = cache.local(book)
    if local:
        return local
    log(f"  copying {book.name}…")
    got: list[Path | None] = []
    cache.fetch([book], log=lambda m: None, on_file=lambda b, p: got.append(p))
    return got[0] if got else None
