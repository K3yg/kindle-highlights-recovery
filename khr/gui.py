"""Point-and-click interface (Tkinter, ships with Python).

On Windows the app finds the Kindle by itself as soon as it's plugged in and
unlocked, and lists every book on it. Tick books (or double-click one) to
export. On other systems, or without the Kindle at hand, point it at a folder
holding the files copied off the device.
"""
from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__, annotations, device, html_page, i18n, pipeline
from .book import title_from_filename
from .i18n import t

CHECK, EMPTY = "☑", "☐"
POLL_MS = 3000
RESULT_STATUS = {"ok": "st_ok", "drm": "st_drm", "missing": "st_missing",
                 "error": "st_error", "empty": "st_empty"}


def open_path(path: Path):
    if sys.platform == "win32":
        os.startfile(str(path))                       # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


@dataclass
class Row:
    key: str
    title: str
    asin: str | None = None
    dev: device.DeviceBook | None = None     # device mode
    kfx: Path | None = None                  # files mode
    status: str = ""                         # i18n key, or "" when nothing to say
    checking: bool = False                   # still identifying this book
    checked: bool = False

    def counts(self, books) -> tuple[int, int]:
        b = books.get(self.asin) if self.asin else None
        return (len(b.highlights), len(b.notes)) if b else (0, 0)

    def exportable(self, books) -> bool:
        return bool(self.asin and sum(self.counts(books)) and (self.dev or self.kfx))


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        settings = i18n.load_settings()
        default_out = Path.home() / "Documents" / "Kindle Highlights"
        self.out = tk.StringVar(value=settings.get("out") or str(default_out))
        self.search = tk.StringVar()
        self.lang = tk.StringVar(value=i18n.LANGUAGES[i18n.get()])
        self.mode = "device" if device.supported() else "files"
        self.books: dict[str, annotations.BookAnnotations] = {}
        self.rows: list[Row] = []
        self.iids: dict[str, Row] = {}
        self.cache: device.Cache | None = None
        self.logq: queue.Queue = queue.Queue()
        self.busy = False
        self.status_msg = ("waiting" if device.supported() else "no_device_support", {})
        self.details = False
        self.read_failed = False       # don't auto-retry a read that failed

        # Sizes below are in 96-dpi pixels; scale them for high-dpi screens.
        self.k = max(1.0, root.winfo_fpixels("1i") / 96)
        root.geometry(f"{self.px(880)}x{self.px(660)}")
        root.minsize(self.px(720), self.px(520))
        self._style()
        self._build()
        self._texts()
        self.root.after(100, self._drain_log)
        if device.supported():
            self.root.after(300, self._poll)

    # ---------------------------------------------------------------- layout
    def _style(self):
        st = ttk.Style()
        try:
            st.theme_use("vista" if sys.platform == "win32" else "clam")
        except tk.TclError:
            pass
        base = "Segoe UI" if sys.platform == "win32" else "TkDefaultFont"
        st.configure("Title.TLabel", font=(base, 16, "bold"))
        st.configure("Status.TLabel", font=(base, 10))
        st.configure("Hint.TLabel", foreground="#666")
        st.configure("Link.TLabel", foreground="#1a5fb4")
        st.configure("Primary.TButton", font=(base, 10, "bold"), padding=(14, 6))
        st.configure("Treeview", rowheight=self.px(28), font=(base, 10))
        st.configure("Treeview.Heading", font=(base, 10, "bold"))

    def _build(self):
        r = self.root
        head = ttk.Frame(r)
        head.pack(fill="x", padx=16, pady=(14, 4))
        self.w_title = ttk.Label(head, style="Title.TLabel")
        self.w_title.pack(side="left")
        lang_box = ttk.Combobox(head, textvariable=self.lang, state="readonly", width=11,
                                values=list(i18n.LANGUAGES.values()))
        lang_box.pack(side="right")
        lang_box.bind("<<ComboboxSelected>>", self._change_lang)
        self.w_lang = ttk.Label(head)
        self.w_lang.pack(side="right", padx=6)

        stat = ttk.Frame(r)
        stat.pack(fill="x", padx=16, pady=(4, 6))
        self.w_refresh = ttk.Button(stat, command=self._refresh)
        if device.supported():
            self.w_refresh.pack(side="right")
        self.w_status = ttk.Label(stat, style="Status.TLabel", wraplength=self.px(640), justify="left")
        self.w_status.pack(side="left", fill="x", expand=True)
        self.w_progress = ttk.Progressbar(r, mode="determinate")

        self.w_search = ttk.Entry(r, textvariable=self.search)
        self.w_search.pack(fill="x", padx=16, pady=(0, 6))
        self.search.trace_add("write", lambda *_: self._render())
        self._placeholder(self.w_search)

        box = ttk.Frame(r)
        box.pack(fill="both", expand=True, padx=16)
        self.tree = ttk.Treeview(box, columns=("sel", "title", "hl", "status"),
                                 show="headings", selectmode="none")
        for col, width, anchor, stretch in (("sel", 44, "center", False), ("title", 460, "w", True),
                                            ("hl", 100, "center", False),
                                            ("status", 170, "center", False)):
            self.tree.column(col, width=self.px(width), anchor=anchor, stretch=stretch)
        self.tree.heading("sel", text=EMPTY, command=self._toggle_all)
        self.tree.tag_configure("off", foreground="#9a9a9a")
        self.tree.tag_configure("on", background="#eaf2fd")
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Button-1>", self._click)
        self.tree.bind("<Double-1>", self._double)

        actions = ttk.Frame(r)
        actions.pack(fill="x", padx=16, pady=8)
        self.w_all = ttk.Button(actions, command=lambda: self._set_all(True))
        self.w_all.pack(side="left")
        self.w_none = ttk.Button(actions, command=lambda: self._set_all(False))
        self.w_none.pack(side="left", padx=6)
        self.w_export = ttk.Button(actions, style="Primary.TButton", command=self._export)
        self.w_export.pack(side="right")
        self.w_hint = ttk.Label(r, style="Hint.TLabel", wraplength=self.px(820), justify="left")
        self.w_hint.pack(fill="x", padx=16)

        foot = ttk.Frame(r)
        foot.pack(fill="x", padx=16, pady=(8, 4))
        self.w_save = ttk.Label(foot)
        self.w_save.pack(side="left")
        ttk.Entry(foot, textvariable=self.out).pack(side="left", fill="x", expand=True, padx=6)
        self.w_change = ttk.Button(foot, command=self._pick_out)
        self.w_change.pack(side="left")
        self.w_open = ttk.Button(foot, command=self._open_out)
        self.w_open.pack(side="left", padx=(6, 0))

        links = ttk.Frame(r)
        links.pack(fill="x", padx=16, pady=(2, 10))
        self.w_files = ttk.Label(links, style="Link.TLabel", cursor="hand2")
        self.w_files.pack(side="left")
        self.w_files.bind("<Button-1>", lambda e: self._open_files())
        self.w_details = ttk.Label(links, style="Link.TLabel", cursor="hand2")
        self.w_details.pack(side="right")
        self.w_details.bind("<Button-1>", lambda e: self._toggle_details())
        self.log = tk.Text(r, height=8, wrap="word", state="disabled", font=("Consolas", 9))

    def _placeholder(self, entry: ttk.Entry):
        """Grey hint text inside the search box while it's empty."""
        self.w_search_hint = tk.Label(entry, fg="#999", bg="white", cursor="xterm",
                                      font=("Segoe UI", 10) if sys.platform == "win32" else None)
        self.w_search_hint.place(x=6, rely=0.5, anchor="w")
        self.w_search_hint.bind("<Button-1>", lambda e: entry.focus_set())

        def update(*_):
            if self.search.get():
                self.w_search_hint.place_forget()
            else:
                self.w_search_hint.place(x=6, rely=0.5, anchor="w")
        self.search.trace_add("write", update)

    def _texts(self):
        """(Re)apply every visible string in the current language."""
        self.root.title(f"{t('app_title')} {__version__}")
        self.w_title.configure(text=t("app_title"))
        self.w_lang.configure(text=t("language") + ":")
        self.w_refresh.configure(text=t("refresh"))
        self.w_search_hint.configure(text=t("search"))
        for col, key in (("title", "col_title"), ("hl", "col_hl"), ("status", "col_status")):
            self.tree.heading(col, text=t(key))
        self.w_all.configure(text=t("select_all"))
        self.w_none.configure(text=t("select_none"))
        self.w_hint.configure(text=t("hint"))
        self.w_save.configure(text=t("save_to"))
        self.w_change.configure(text=t("change"))
        self.w_open.configure(text=t("open_folder"))
        self.w_files.configure(text=t("use_files"))
        self.w_details.configure(text=t("details_hide" if self.details else "details_show"))
        key, fmt = self.status_msg
        self.w_status.configure(text=t(key, **fmt))
        self._render()

    def _change_lang(self, _event=None):
        code = next(c for c, name in i18n.LANGUAGES.items() if name == self.lang.get())
        i18n.set_language(code)
        self._texts()

    def _toggle_details(self):
        self.details = not self.details
        if self.details:
            self.log.pack(fill="both", padx=16, pady=(0, 10))
        else:
            self.log.pack_forget()
        self._texts()

    # ---------------------------------------------------------------- helpers
    def px(self, n: int) -> int:
        return int(n * self.k)

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

    def set_status(self, key: str, **fmt):
        self.status_msg = (key, fmt)
        self.ui(lambda: self.w_status.configure(text=t(key, **fmt)))

    def progress(self, done: int | None, total: int | None = None):
        """done=None hides the bar; total=0 makes it spin."""
        def apply():
            if done is None:
                self.w_progress.stop()
                self.w_progress.pack_forget()
                return
            if not self.w_progress.winfo_ismapped():
                self.w_progress.pack(fill="x", padx=16, pady=(0, 8), before=self.w_search)
            if total:
                self.w_progress.stop()
                self.w_progress.configure(mode="determinate", maximum=total, value=done)
            else:
                self.w_progress.configure(mode="indeterminate")
                self.w_progress.start(12)
        self.ui(apply)

    def set_busy(self, busy: bool):
        self.busy = busy

        def apply():
            for w in (self.w_refresh, self.w_change):
                w.state(["disabled"] if busy else ["!disabled"])
            self.root.configure(cursor="watch" if busy else "")
            self._update_export()
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
                key = "not_found" if isinstance(exc, device.DeviceError) else "error_msg"
                self.set_status(key, err=str(exc))
            finally:
                self.progress(None)
                self.set_busy(False)
        threading.Thread(target=wrapper, daemon=True).start()

    def out_dir(self) -> Path:
        return Path(self.out.get()).expanduser()

    def _pick_out(self):
        d = filedialog.askdirectory(initialdir=self.out.get())
        if d:
            self.out.set(d)
            i18n.save_settings(out=d)

    def _open_out(self):
        d = self.out_dir()
        d.mkdir(parents=True, exist_ok=True)
        open_path(d)

    # ---------------------------------------------------------------- table
    def _sorted_rows(self) -> list[Row]:
        def key(row):
            has = row.exportable(self.books) or row.checking
            return (0 if has else 1, row.title.lower())
        return sorted(self.rows, key=key)

    def _cells(self, row: Row):
        hl, notes = row.counts(self.books)
        if row.checking:
            count, status = "…", t("st_checking")
        else:
            count = (f"{hl}" + (f" + {notes} ✎" if notes else "")) if hl or notes else "—"
            status = t(row.status) if row.status else ("" if hl or notes else t("st_none"))
        ok = row.exportable(self.books)
        mark = (CHECK if row.checked else EMPTY) if ok else ""
        tags = ("off",) if not ok and not row.checking else (("on",) if row.checked else ())
        return (mark, row.title, count, status), tags

    def _render(self):
        if not hasattr(self, "tree"):
            return
        self.tree.delete(*self.tree.get_children())
        self.iids.clear()
        q = self.search.get().strip().lower()
        for row in self._sorted_rows():
            if q and q not in row.title.lower():
                continue
            values, tags = self._cells(row)
            iid = self.tree.insert("", "end", values=values, tags=tags)
            self.iids[iid] = row
        self._update_export()

    def _update_export(self):
        n = sum(1 for r in self.rows if r.checked and r.exportable(self.books))
        self.w_export.configure(text=t("export_none") if not n else
                                t("export_1") if n == 1 else t("export_n", n=n))
        self.w_export.state(["!disabled"] if n and not self.busy else ["disabled"])
        for w in (self.w_all, self.w_none):
            w.state(["!disabled"] if self.rows else ["disabled"])

    def _visible(self) -> list[Row]:
        return [self.iids[i] for i in self.tree.get_children()]

    def _click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        row = self.iids.get(self.tree.identify_row(event.y))
        if row and row.exportable(self.books):
            row.checked = not row.checked
            self._render()

    def _double(self, event):
        row = self.iids.get(self.tree.identify_row(event.y))
        if row and row.exportable(self.books) and not self.busy:
            self.run_bg(self._export_bg, [row])

    def _set_all(self, on: bool):
        for row in self._visible():
            if row.exportable(self.books):
                row.checked = on
        self._render()

    def _toggle_all(self):
        rows = [r for r in self._visible() if r.exportable(self.books)]
        self._set_all(any(not r.checked for r in rows))

    # ---------------------------------------------------------------- device
    def _poll(self):
        """Look for the Kindle every few seconds until it shows up."""
        if self.mode == "device" and not self.rows and not self.busy and not self.read_failed:
            def check():
                if device.connected() and not self.busy and not self.rows:
                    self.ui(self._refresh)
            threading.Thread(target=check, daemon=True).start()
        self.root.after(POLL_MS, self._poll)

    def _refresh(self):
        self.mode = "device"
        self.run_bg(self._read_device_bg)

    def _read_device_bg(self):
        work = self.out_dir() / ".kindle"
        self.read_failed = True
        self.set_status("reading_db")
        self.progress(0, 0)
        self.say("→ " + t("reading_db"))
        self.books = annotations.load(device.pull_database(work))
        annotated = {a for a, b in self.books.items() if b.highlights or b.notes}

        self.set_status("listing")
        self.say("→ " + t("listing"))
        on_device = device.list_books()
        rows = {}
        for d in on_device:
            named = next((a for a in device.asins_in_name(d.path) if a in annotated), None)
            rows[d.path] = Row(d.path, d.display_title, asin=named, dev=d, checking=not named)
        self.rows = list(rows.values())
        self.ui(self._render)

        def step(done, total):
            self.set_status("identifying", i=done, n=total)
            self.progress(done, total)
            self.ui(self._render)

        self.cache = device.identify(on_device, annotated, work, progress=step, log=self.say)
        for d in on_device:
            row = rows[d.path]
            row.asin, row.checking = d.asin, False
            row.title = d.display_title
        found = {d.asin for d in on_device if d.asin}
        gone = annotated - found
        if gone:                      # highlighted once, since removed from the Kindle
            self.say(f"  {t('st_not_on_kindle')}: {len(gone)} ({', '.join(sorted(gone))})")
        self.read_failed = False
        with_hl = sum(1 for r in self.rows if r.exportable(self.books))
        self.set_status("ready", n=len(on_device), h=with_hl)
        self.say("✓ " + t("ready", n=len(on_device), h=with_hl))
        self.ui(self._render)

    # ---------------------------------------------------------------- files
    def _open_files(self):
        if self.busy:
            return
        folder = filedialog.askdirectory(title=t("pick_folder"))
        if not folder:
            return
        folder = Path(folder)
        dbs = sorted((p for p in folder.rglob("ksdk_annotation*")
                      if re.fullmatch(r"ksdk_annotation_v\d+(\.db)?", p.name)),
                     key=lambda p: p.stat().st_mtime, reverse=True)
        db = dbs[0] if dbs else None
        if db is None:
            if not messagebox.askyesno(t("app_title"), t("no_db_in_folder")):
                return
            picked = filedialog.askopenfilename(
                title=t("pick_db"), initialdir=str(folder),
                filetypes=[("ksdk_annotation_v1.db", "ksdk_annotation*"), ("*", "*")])
            if not picked:
                return
            db = Path(picked)
        self.mode = "files"
        self.run_bg(self._open_files_bg, db, folder)

    def _open_files_bg(self, db: Path, folder: Path):
        self.set_status("listing")
        self.progress(0, 0)
        self.books = annotations.load(db)
        annotated = {a for a, b in self.books.items() if b.highlights or b.notes}
        local = pipeline.find_local_books([folder], annotated)
        self.rows = []
        for asin in annotated:
            kfx = local.get(asin)
            title = title_from_filename(kfx.name)[0] if kfx else asin
            self.rows.append(Row(asin, title, asin=asin, kfx=kfx,
                                 status="" if kfx else "st_missing"))
        self.set_status("ready_files", h=len(local))
        self.say("✓ " + t("ready_files", h=len(local)))
        self.ui(self._render)

    # ---------------------------------------------------------------- export
    def _export(self):
        chosen = [r for r in self._sorted_rows() if r.checked and r.exportable(self.books)]
        if not chosen:
            messagebox.showinfo(t("app_title"), t("nothing_selected"))
            return
        self.run_bg(self._export_bg, chosen)

    def _export_bg(self, chosen: list[Row]):
        out = self.out_dir()
        out.mkdir(parents=True, exist_ok=True)
        i18n.save_settings(out=str(out))
        lang = i18n.get()
        clip = out / "My Clippings.txt"
        if self.mode == "device" and not clip.exists():
            src = device.pull_clippings(out / ".kindle")
            if src:
                shutil.copyfile(src, clip)
                self.say("→ " + t("log_from_clippings"))

        done, taken, last = 0, set(), None
        for i, row in enumerate(chosen):
            self.set_status("exporting", title=row.title)
            self.progress(i, len(chosen))
            self.say(f"\n{row.title}")
            row.status = "st_working"
            self.ui(self._render)
            if row.dev is not None:
                try:
                    kfx = device.pull_book(row.dev, self.cache, log=self.say)
                except device.DeviceError as exc:
                    self.say(f"  ✗ {exc}")
                    kfx = None
            else:
                kfx = row.kfx
            if not kfx:
                row.status = "st_missing"
                self.ui(self._render)
                continue
            res = pipeline.export_book(self.books[row.asin], kfx, out, clip,
                                       pipeline.FORMATS, taken, lang)
            if res.title:
                row.title = res.title
                if row.dev is not None and self.cache:
                    self.cache.set_title(row.dev, res.title)
            row.status = RESULT_STATUS.get(res.status, "st_error")
            row.checked = row.checked and res.status != "ok"
            if res.status == "ok":
                done += 1
                last = res.files[0] if res.files else None
                self.say("  " + t("log_ok", q=res.quotes, c=res.added_to_clippings))
                if res.unresolved:
                    self.say("  " + t("log_skipped", n=res.unresolved))
            elif res.status == "drm":
                self.say("  " + t("log_drm"))
            elif res.message:
                self.say(f"  {res.message}")
            self.ui(self._render)

        index = html_page.write_index(out, lang)
        self.set_status("done", ok=done, n=len(chosen))
        self.say("\n" + t("log_summary", ok=done, n=len(chosen), out=out))
        page = index if done > 1 else last
        if page:
            webbrowser.open(page.resolve().as_uri())


def main() -> int:
    if sys.platform == "win32":
        try:                                  # crisp text on scaled displays
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
