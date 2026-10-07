"""Copy files off a USB-connected Kindle (Windows, via MTP + PowerShell).

On macOS and Linux, copy the files by hand instead (see the README) and use
the offline mode: the rest of the tool is cross-platform.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .book import file_contains_asin, title_from_filename

SCRIPT = Path(__file__).resolve().parent / "kindle_mtp.ps1"
STOPWORDS = {"o", "a", "os", "as", "de", "do", "da", "dos", "das", "e", "um", "uma",
             "the", "of", "an", "and", "el", "la", "le", "les", "der", "die", "das"}


class DeviceError(Exception):
    pass


def supported() -> bool:
    return sys.platform == "win32"


def _run(action: str, timeout: int = 600, **params) -> dict:
    if not supported():
        raise DeviceError("Reading the Kindle directly is only supported on Windows. "
                          "Copy the files by hand and use offline mode.")
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
           "-File", str(SCRIPT), "-Action", action]
    for key, value in params.items():
        cmd += [f"-{key}", str(value)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=flags)
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    line = next((l for l in reversed(out.splitlines()) if l.startswith("{")), "")
    try:
        data = json.loads(line)
    except ValueError:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise DeviceError(err or out or f"PowerShell failed ({proc.returncode})")
    if not data.get("ok"):
        raise DeviceError(data.get("error") or "Unknown device error")
    return data


def status() -> None:
    _run("status", timeout=60)


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
    try:
        data = _run("pull-file", Path="My Clippings", Dest=dest)
    except DeviceError:
        try:
            data = _run("pull-file", Path="My Clippings.txt", Dest=dest)
        except DeviceError:
            return None
    return Path(data["path"])


@dataclass
class DeviceBook:
    path: str                 # relative to documents/, as the device names it
    size: int

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass
class Listing:
    books: list[DeviceBook]
    titles: dict[str, str]    # ASIN -> title, from "<Title>_<ASIN>.sdr" folders


def list_books() -> Listing:
    items = _run("list-books", timeout=300).get("items", [])
    titles, books = {}, []
    sdr_names = {i["path"].rsplit("/", 1)[-1][:-4] for i in items if i.get("type") == "sdr"}
    for it in items:
        name = it["path"].rsplit("/", 1)[-1]
        if it.get("type") == "sdr":
            m = re.fullmatch(r"(.*)_([A-Z0-9]{10,})\.sdr", name)
            if m:
                titles[m.group(2)] = m.group(1)
            continue
        kind = (it.get("type") or "").upper()
        stem = name[:-4] if name.lower().endswith(".kfx") else name
        # Explorer hides the extension but reports the type ("KFX File"). If
        # the type is missing, fall back to "has a matching .sdr folder".
        if "KFX" in kind or name.lower().endswith(".kfx") or (not kind and stem in sdr_names):
            if stem != "My Clippings":
                books.append(DeviceBook(it["path"], int(it.get("size") or 0)))
    return Listing(books, titles)


def tokens(text: str) -> set[str]:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return {t for t in re.split(r"[^a-z0-9]+", text) if len(t) > 1 and t not in STOPWORDS}


def candidates(asin: str, title: str, books: list[DeviceBook], limit: int = 3) -> list[DeviceBook]:
    """Device files most likely to be the book with this ASIN, best first."""
    direct = [b for b in books if asin in b.name]
    if direct:
        return direct
    want = tokens(title)
    if not want:
        return []
    scored = []
    for b in books:
        have = tokens(title_from_filename(b.name)[0]) | tokens(b.name)
        score = len(want & have) / len(want)
        if score >= 0.5:
            scored.append((score, -len(have), b))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return [b for _, _, b in scored[:limit]]


def pull_book(asin: str, title: str, books: list[DeviceBook], dest: Path, log=print) -> Path | None:
    """Copy the .kfx for asin into dest, checking it really is that book."""
    for b in candidates(asin, title, books):
        local = dest / (b.name if b.name.lower().endswith(".kfx") else b.name + ".kfx")
        if not (local.is_file() and file_contains_asin(local, asin)):
            log(f"  copying {b.name} from the Kindle…")
            data = _run("pull-file", Path=b.path, Dest=dest)
            got = Path(data["path"])
            if got != local:
                os.replace(got, local)
        if file_contains_asin(local, asin):
            return local
        log(f"  {b.name} is a different book, trying the next match")
    return None
