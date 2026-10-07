"""Point-and-click interface (Tkinter, ships with Python).

Two ways in:
  * Read Kindle   - Windows: copy everything straight off a USB-connected Kindle.
  * Open files... - any OS: use an annotation database and .kfx files you
                    copied off the device yourself.
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__, annotations, device, html_page, pipeline
from .book import title_from_filename

CHECK, EMPTY = "☑", "☐"
STATUS = {"ok": "✓ done", "drm": "DRM 🔒", "missing": "no book file",
          "error": "error", "empty": "no text"}


def open_path(path: Path):
    if sys.platform == "win32":
        os.startfile(str(path))                       # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f"Kindle Highlights Recovery {__version__}")
        root.geometry("820x600")
        root.minsize(700, 520)

        self.out = tk.StringVar(value=str(Path.home() / "Documents" / "Kindle Highlights"))
        self.source = None          # ("device", listing, work_dir) | ("files", {asin: kfx})
        self.books: dict[str, annotations.BookAnnotations] = {}
        self.titles: dict[str, str] = {}
        self.rows: dict[str, str] = {}   # asin -> tree item id
        self.logq: queue.Queue = queue.Queue()
        self.busy = False
        self._build()
        self.root.after(100, self._drain_log)

    # ---------------------------------------------------------------- layout
    def _build(self):
        pad = {"padx": 12, "pady": 6}
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="Save to:").pack(side="left")
        ttk.Entry(top, textvariable=self.out).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(top, text="Browse…", command=self._pick_out).pack(side="left")

        bar = ttk.Frame(self.root)
        bar.pack(fill="x", **pad)
        self.b_device = ttk.Button(bar, text="1 · Read Kindle (USB)", command=self._read_device)
        self.b_device.pack(side="left")
        if not device.supported():
            self.b_device.state(["disabled"])
        self.b_files = ttk.Button(bar, text="…or open files", command=self._open_files)
        self.b_files.pack(side="left", padx=6)
        self.b_export = ttk.Button(bar, text="2 · Export selected", command=self._export)
        self.b_export.pack(side="left", padx=(18, 0))
        self.b_export.state(["disabled"])
        ttk.Button(bar, text="Open folder", command=self._open_out).pack(side="right")

        box = ttk.LabelFrame(self.root, text="Books with highlights")
        box.pack(fill="both", expand=True, **pad)
        cols = ("sel", "title", "hl", "notes", "status")
        self.tree = ttk.Treeview(box, columns=cols, show="headings", height=11)
        for col, text, width, anchor in (("sel", EMPTY, 40, "center"), ("title", "Title", 430, "w"),
                                         ("hl", "Highlights", 80, "center"),
                                         ("notes", "Notes", 60, "center"),
                                         ("status", "Status", 120, "center")):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor=anchor, stretch=(col == "title"))
        self.tree.heading("sel", text=EMPTY, command=self._toggle_all)
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Button-1>", self._click)
        self.tree.bind("<space>", lambda e: self._toggle(self.tree.focus()))

        ttk.Label(self.root, foreground="#666", wraplength=780, justify="left",
                  text="Click a row to select it. Books you sent with Send to Kindle "
                       "export fine; purchased books are DRM-protected and can't be decoded."
                  ).pack(anchor="w", padx=12)
        self.log = tk.Text(self.root, height=9, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", padx=12, pady=(6, 4))
        self.status = ttk.Label(self.root, anchor="w",
                                text="Plug in and unlock your Kindle, then click “Read Kindle”."
                                if device.supported() else
                                "Copy the files off your Kindle (see README), then click “open files”.")
        self.status.pack(fill="x", padx=12, pady=(0, 8))

    # ---------------------------------------------------------------- helpers
    def say(self, msg: str):
        self.logq.put(msg)

    def _drain_log(self):
        while not self.logq.empty():
            msg = self.logq.get_nowait()
            self.log.configure(state="normal")
            self.log.insert("end", msg + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.root.after(100, self._drain_log)

    def ui(self, fn, *args):
        self.root.after(0, fn, *args)

    def set_status(self, text: str):
        self.ui(lambda: self.status.configure(text=text))

    def set_busy(self, busy: bool):
        self.busy = busy

        def apply():
            for b in (self.b_files, self.b_export) + ((self.b_device,) if device.supported() else ()):
                b.state(["disabled"] if busy else ["!disabled"])
            if not busy and not self.books:
                self.b_export.state(["disabled"])
            self.root.configure(cursor="watch" if busy else "")
        self.ui(apply)

    def run_bg(self, fn, *args):
        if self.busy:
            return
        self.set_busy(True)

        def wrapper():
            try:
                fn(*args)
            except Exception as exc:                  # surface, don't crash the UI
                self.say(f"✗ {exc}")
                self.set_status(str(exc))
            finally:
                self.set_busy(False)
        threading.Thread(target=wrapper, daemon=True).start()

    def out_dir(self) -> Path:
        return Path(self.out.get()).expanduser()

    def _pick_out(self):
        d = filedialog.askdirectory(initialdir=self.out.get())
        if d:
            self.out.set(d)

    def _open_out(self):
        d = self.out_dir()
        d.mkdir(parents=True, exist_ok=True)
        open_path(d)

    # ---------------------------------------------------------------- table
    def _fill(self, status_for):
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        order = sorted(self.books.values(), key=lambda b: -len(b.highlights))
        for b in order:
            if not b.highlights and not b.notes:
                continue
            title = self.titles.get(b.asin, b.asin)
            iid = self.tree.insert("", "end", values=(EMPTY, title, len(b.highlights),
                                                      len(b.notes), status_for(b.asin)))
            self.rows[b.asin] = iid
        if self.rows:
            self.b_export.state(["!disabled"])

    def _click(self, event):
        if self.tree.identify_region(event.x, event.y) == "cell":
            self._toggle(self.tree.identify_row(event.y))

    def _toggle(self, iid):
        if not iid:
            return
        vals = list(self.tree.item(iid, "values"))
        vals[0] = EMPTY if vals[0] == CHECK else CHECK
        self.tree.item(iid, values=vals)

    def _toggle_all(self):
        items = self.tree.get_children()
        on = any(self.tree.item(i, "values")[0] == EMPTY for i in items)
        for i in items:
            vals = list(self.tree.item(i, "values"))
            vals[0] = CHECK if on else EMPTY
            self.tree.item(i, values=vals)
        self.tree.heading("sel", text=CHECK if on else EMPTY)

    def _row_status(self, asin: str, text: str):
        def apply():
            iid = self.rows.get(asin)
            if iid:
                vals = list(self.tree.item(iid, "values"))
                vals[4] = text
                self.tree.item(iid, values=vals)
        self.ui(apply)

    # ---------------------------------------------------------------- sources
    def _read_device(self):
        self.run_bg(self._read_device_bg)

    def _read_device_bg(self):
        work = self.out_dir() / ".kindle"
        self.set_status("Reading the Kindle…")
        self.say("→ Copying the annotation database…")
        db = device.pull_database(work)
        self.books = annotations.load(db)
        self.say("→ Listing books on the Kindle…")
        listing = device.list_books()
        self.titles = {a: listing.titles.get(a, a) for a in self.books}
        self.source = ("device", listing, work)
        n = sum(1 for b in self.books.values() if b.highlights or b.notes)
        self.ui(self._fill, lambda asin: "")
        self.say(f"✓ {n} books with annotations. Select some and click Export.")
        self.set_status(f"{n} books found on the Kindle.")

    def _open_files(self):
        db = filedialog.askopenfilename(
            title="Choose the annotation database (ksdk_annotation_v1.db)",
            filetypes=[("Kindle annotation database", "ksdk_annotation*"), ("All files", "*")])
        if not db:
            return
        folder = filedialog.askdirectory(
            title="Choose the folder with your .kfx books", initialdir=str(Path(db).parent))
        if not folder:
            return
        self.run_bg(self._open_files_bg, Path(db), Path(folder))

    def _open_files_bg(self, db: Path, folder: Path):
        self.set_status("Reading files…")
        self.books = annotations.load(db)
        self.say(f"→ Looking for books in {folder}…")
        local = pipeline.find_local_books([folder], set(self.books))
        self.titles = {a: (title_from_filename(local[a].name)[0] if a in local else a)
                       for a in self.books}
        self.source = ("files", local)
        self.ui(self._fill, lambda asin: "ready" if asin in local else "no .kfx found")
        self.say(f"✓ Found book files for {len(local)} of {len(self.books)} books.")
        self.set_status("Select books and click Export.")

    # ---------------------------------------------------------------- export
    def _export(self):
        chosen = [a for a, iid in self.rows.items() if self.tree.item(iid, "values")[0] == CHECK]
        if not chosen:
            messagebox.showinfo("Nothing selected", "Click the books you want to export first.")
            return
        self.run_bg(self._export_bg, chosen)

    def _export_bg(self, chosen: list[str]):
        out = self.out_dir()
        out.mkdir(parents=True, exist_ok=True)
        clip = out / "My Clippings.txt"
        kind = self.source[0]
        if kind == "device" and not clip.exists():
            src = device.pull_clippings(self.source[2])
            if src:
                shutil.copyfile(src, clip)
                self.say("→ Started from the Kindle's own My Clippings.txt.")

        done, taken, last = 0, set(), None
        for asin in chosen:
            title = self.titles.get(asin, asin)
            self.set_status(f"Exporting {title}…")
            self.say(f"\n{title}")
            self._row_status(asin, "working…")
            if kind == "device":
                _, listing, work = self.source
                kfx = device.pull_book(asin, title, listing.books, work, log=self.say)
            else:
                kfx = self.source[1].get(asin)
            if not kfx:
                self.say("  ✗ no matching .kfx file")
                self._row_status(asin, STATUS["missing"])
                continue
            res = pipeline.export_book(self.books[asin], kfx, out, clip, pipeline.FORMATS, taken)
            if res.title and res.title != title:
                self.titles[asin] = res.title
                self.ui(lambda a=asin, t=res.title: self._set_title(a, t))
            self._row_status(asin, STATUS.get(res.status, res.status))
            if res.status == "ok":
                done += 1
                last = res.files[0] if res.files else None
                self.say(f"  ✓ {res.quotes} highlights · {res.added_to_clippings} new in My Clippings.txt")
            if res.message:
                self.say(f"  {res.message}")

        index = html_page.write_index(out)
        self.set_status(f"Done: {done} of {len(chosen)} exported to {out}")
        self.say(f"\n===== {done} of {len(chosen)} exported → {out}")
        page = index if done > 1 else last
        if page:
            webbrowser.open(page.resolve().as_uri())

    def _set_title(self, asin: str, title: str):
        iid = self.rows.get(asin)
        if iid:
            vals = list(self.tree.item(iid, "values"))
            vals[1] = title
            self.tree.item(iid, values=vals)


def main() -> int:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista" if sys.platform == "win32" else "clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
