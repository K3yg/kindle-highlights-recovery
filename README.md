# Kindle Highlights Recovery

**Get back the Kindle highlights that stopped showing up in `My Clippings.txt`.**

Since a firmware update in mid-2026 (around 5.19), Kindles no longer append new
highlights to `documents/My Clippings.txt`. Nothing is lost: the device now
keeps every highlight, note and bookmark in a SQLite database. This tool reads
that database, matches each highlight against the book's text, and gives you:

- an up-to-date **`My Clippings.txt`** in the Kindle's own format, ready for
  Readwise, Clippings.io, Obsidian plugins and other importers
- a **readable HTML page per book**, with search, sorting, dark mode and
  "copy all", plus an `index.html` that links every book
- **Markdown** and **JSON** exports

Everything runs on your computer and nothing is sent anywhere.

> **Works with:** books you added yourself (Send to Kindle, sideloaded KFX).
> **Doesn't work with:** books bought from the Kindle Store. Those are DRM-protected,
> so their text can't be decoded. They're listed, and skipped with a message.

---

## Contents

- [Quick start (Windows)](#quick-start-windows)
- [macOS and Linux](#macos-and-linux)
- [Command line](#command-line)
- [What you get](#what-you-get)
- [How it works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [Privacy](#privacy)
- [Development](#development)
- [Credits and license](#credits-and-license)

---

## Quick start (Windows)

On Windows the app reads the Kindle directly over USB. You don't need to copy
anything by hand.

1. **Install Python 3.10 or newer** from [python.org](https://www.python.org/downloads/).
   In the installer, tick **"Add python.exe to PATH"**.
2. **Download this project.** Click **Code → Download ZIP** on GitHub and unzip it
   somewhere permanent, such as `Documents\kindle-highlights-recovery`. You can
   also clone it:
   ```
   git clone https://github.com/K3yg/kindle-highlights-recovery.git
   ```
3. **Double-click `install.bat`.** It installs the four libraries the book decoder
   needs and adds a **Kindle Highlights Recovery** shortcut to your Desktop and
   Start Menu.
4. **Plug in your Kindle with a data cable and unlock it.** Windows can't see
   the Kindle's storage while the lock screen is showing.
5. Open **Kindle Highlights Recovery** and:
   1. click **Read Kindle (USB)**. Your books with highlights are listed by title.
   2. click the books you want, or the ☐ header to select them all.
   3. click **Export selected**.

The results go to `Documents\Kindle Highlights` (you can change this at the top
of the window). When it finishes, the book page or the index opens in your
browser.

The first time, the app starts from the Kindle's own `My Clippings.txt`, so the
entries it wrote before the firmware change are kept. After that it only adds
what's missing and never duplicates an entry.

## macOS and Linux

Exporting works on any system. Only the automatic copying from the device is
Windows-only, so on macOS and Linux you copy two things off the Kindle yourself.

### 1. Copy the files

Connect the Kindle and copy:

| What | Where on the Kindle |
|---|---|
| The annotation database | `system/ksdk/.annotations/amzn1.account.<ID>/ksdk_annotation_v1.db` |
| Your books (`.kfx`) | `documents/` (sometimes in subfolders such as `documents/Downloads/`) |

`.annotations` is a **hidden folder**. On macOS press <kbd>⌘</kbd> <kbd>⇧</kbd> <kbd>.</kbd>
in the file browser to show hidden files. On Linux press <kbd>Ctrl</kbd> <kbd>H</kbd>.

Recent Kindles connect over MTP, which macOS doesn't support natively. Use
[OpenMTP](https://openmtp.ganeshrvel.com/) (free) to browse the device.
On Linux, most file managers support MTP through `gvfs`. You can also use `jmtpfs`.

Put everything in one folder, for example:

```
~/kindle-backup/
├── ksdk_annotation_v1.db
├── My Clippings.txt        (optional, from documents/; copy it into your output folder to keep old entries)
├── Some Book - Author.kfx
└── Another Book - Author.kfx
```

### 2. Install and run

```bash
git clone https://github.com/K3yg/kindle-highlights-recovery.git
cd kindle-highlights-recovery
python3 -m pip install -r requirements.txt

# Graphical app: click "…or open files" and pick the database, then the folder.
python3 -m khr gui

# Or from the terminal:
python3 -m khr export --db ~/kindle-backup/ksdk_annotation_v1.db --books ~/kindle-backup
```

The GUI uses Tkinter. If it's missing on Linux, install your distribution's
`python3-tk` package.

## Command line

Run these from the project folder.

```bash
python -m khr gui                                  # the graphical app (also the default)

# Windows, Kindle connected: copy what's needed and export everything
python -m khr sync
python -m khr sync --asin B0EXAMPLE123 --out D:\Highlights

# Files you copied yourself (any OS)
python -m khr list   --db ksdk_annotation_v1.db --books ./books
python -m khr export --db ksdk_annotation_v1.db --books ./books ./more-books/Book.kfx
```

Options for `sync` and `export`:

| Option | Meaning |
|---|---|
| `--asin A B …` | Only these books. `list` shows the ASINs. Default: every book. |
| `--out DIR` | Output folder. Default: `./kindle-highlights` |
| `--format html,md,json` | Which files to write. Default: all three. |
| `--no-clippings` | Don't touch `My Clippings.txt`. |
| `--clippings FILE` | `export` only: update this file instead of `<out>/My Clippings.txt`. |

## What you get

```
Kindle Highlights/
├── index.html              every exported book, with highlight counts
├── My Clippings.txt        Kindle format, UTF-8 with BOM, only ever appended to
├── the-book-title.html     reading page: search, date/reading order, dark mode, copy all
├── the-book-title.md       Markdown: quotes, notes, pages
└── the-book-title.json     everything, for scripts
```

- **Notes** are attached to the highlight they were written on. A note with no
  highlight is kept as a standalone entry.
- **Page numbers** come from the book's own page list, so they match what the
  Kindle shows. A book without a page list has no page numbers.
- **Kindle locations** ("Location 1234") are not written. The database doesn't
  store them and they can't be derived exactly, so a guessed number would look
  real but be wrong.
- **Duplicates.** Entries are matched on their text (ignoring spacing) plus the
  page, including entries the Kindle wrote itself in another language.

## How it works

1. **Annotations.** `ksdk_annotation_v1.db` stores each highlight as a pair of
   positions inside the book, not as text. The same annotation can appear in
   several tables (synced, staging, unsynced edits, device-only). The tool reads
   them all, keeps the most local copy and drops anything deleted on the device.
2. **Text.** The `.kfx` book is decoded with
   [kfxlib](vendor/README.md), and the text between the two positions is
   extracted. Separate paragraphs stay on separate lines.
3. **Pages and chapters** come from the book's navigation data.
4. **Matching books.** A book file is matched to its annotations by the book ID
   (ASIN) inside the file. On the device, the app first guesses from file and
   folder names, then confirms the ID in the copied file.

## Troubleshooting

**"No Kindle found"**: unlock the Kindle and make sure the cable carries data
(some cables only charge). Wait a few seconds after plugging in. If it still
fails, open File Explorer: the Kindle should appear under *This PC*.

**"Annotation folder not found"**: the Kindle may be on older firmware that
still writes `My Clippings.txt` itself, in which case you don't need this tool.
Otherwise, unlock it and try again.

**"DRM-protected, skipped"**: it's a Kindle Store purchase, and its text can't
be decoded.

**"book file not found" / "no .kfx found"**: the book isn't on the device any
more (archived or deleted), or it's in a format other than KFX (PDF, AZW3, MOBI).

**A database other than `ksdk_annotation_v1.db`** (for example `_v2`): Amazon
changed the format again. Please [open an issue](https://github.com/K3yg/kindle-highlights-recovery/issues).

## Privacy

Everything runs locally and the tool makes no network requests. The HTML pages
don't load web fonts or scripts either. The annotation database contains your
Amazon account ID, so don't share it publicly. This repository's `.gitignore`
excludes databases, books and exports.

## Development

```bash
python -m unittest -v        # no Kindle or book needed
```

| Module | Role |
|---|---|
| `khr/annotations.py` | reads the annotation database |
| `khr/book.py` | opens a KFX book via kfxlib, resolves positions to text, pages, TOC |
| `khr/export.py` | combines both, attaches notes, writes JSON and Markdown |
| `khr/clippings.py` | reads and appends to `My Clippings.txt` |
| `khr/html_page.py` | book pages and the index |
| `khr/device.py`, `khr/kindle_mtp.ps1` | Windows: copying files from the Kindle over MTP |
| `khr/gui.py`, `khr/__main__.py` | the app and the command line |

Contributions are welcome. A macOS/Linux device backend (for example via
libmtp) and other export formats would be especially useful.

## Credits and license

- **kfxlib** by John Howell (jhowell), from the
  [KFX Input](https://www.mobileread.com/forums/showthread.php?t=291290) calibre
  plugin, does the actual KFX decoding. It's vendored unmodified under the GPL v3.
  See [vendor/README.md](vendor/README.md).
- [aakar/KFX-Highlights](https://github.com/aakar/KFX-Highlights) first
  documented where the annotations moved to. This project is an independent,
  cross-platform implementation and shares no code with it.

Licensed under the **GNU General Public License v3.0**. See [LICENSE](LICENSE).
This project isn't affiliated with Amazon. Kindle is a trademark of Amazon.com, Inc.
