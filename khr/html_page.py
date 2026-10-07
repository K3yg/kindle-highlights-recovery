"""A self-contained, offline HTML page for one book's highlights.

No external requests (no web fonts, no CDNs). Dates are formatted in the
reader's own language by the browser.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from .export import BookExport

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="kindle-highlights-recovery">
<title>__TITLE__ — highlights</title>
<style>
  :root{
    --bg:#f5efe4; --bg2:#faf6ee; --ink:#2b2620; --muted:#857a68;
    --accent:#a8541f; --line:#e2d7c3; --card:#fffdf8; --shadow:rgba(70,50,20,.08);
    --serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,"Times New Roman",serif;
  }
  html[data-theme="dark"]{
    --bg:#17140f; --bg2:#1e1a14; --ink:#ece3d3; --muted:#9b8f79;
    --accent:#d98a4e; --line:#332c22; --card:#201c16; --shadow:rgba(0,0,0,.4);
  }
  *{box-sizing:border-box}
  body{margin:0; background:var(--bg); color:var(--ink); font-family:var(--serif);
    font-size:20px; line-height:1.7; transition:background .3s,color .3s}
  .wrap{max-width:760px; margin:0 auto; padding:0 24px 120px}
  header.cover{text-align:center; padding:84px 0 36px}
  .kicker{font-size:13px; letter-spacing:.32em; text-transform:uppercase; color:var(--muted)}
  h1{font-weight:600; font-size:clamp(42px,8vw,76px); line-height:1.05; margin:.2em 0 .15em}
  .author{font-style:italic; font-size:23px; color:var(--muted); margin:0}
  .rule{width:64px; height:1px; background:var(--accent); margin:32px auto 0; opacity:.7}
  .count{margin-top:18px; font-size:14px; letter-spacing:.12em; text-transform:uppercase; color:var(--muted)}

  .toolbar{position:sticky; top:0; z-index:10; display:flex; gap:10px; align-items:center;
    padding:14px 0; margin-bottom:8px; background:linear-gradient(var(--bg) 75%, transparent)}
  .search{flex:1; position:relative}
  .search input{width:100%; padding:11px 16px 11px 40px; border:1px solid var(--line);
    background:var(--card); color:var(--ink); border-radius:999px; font:inherit; font-size:16px; outline:none}
  .search input:focus{border-color:var(--accent)}
  .search svg{position:absolute; left:14px; top:50%; transform:translateY(-50%);
    width:17px; height:17px; stroke:var(--muted); fill:none; stroke-width:2}
  button{font:inherit; cursor:pointer}
  .pill{flex:none; height:44px; padding:0 16px; border-radius:999px; border:1px solid var(--accent);
    background:transparent; color:var(--accent); font-size:15px; white-space:nowrap}
  .pill:hover,.pill.done{background:var(--accent); color:var(--bg)}
  .icon{flex:none; width:44px; height:44px; border-radius:50%; border:1px solid var(--line);
    background:var(--card); color:var(--ink); font-size:18px}
  .icon:hover{background:var(--bg2)}
  .mode{text-align:right; font-size:12px; letter-spacing:.1em; text-transform:uppercase;
    color:var(--muted); margin:0 4px 22px}

  .quotes{display:flex; flex-direction:column; gap:30px}
  figure.q{margin:0; position:relative; background:var(--card); border:1px solid var(--line);
    border-radius:14px; padding:36px 38px 22px; box-shadow:0 14px 34px -22px var(--shadow)}
  figure.q::before{content:"\201C"; position:absolute; top:-.15em; left:16px; font-size:104px;
    line-height:1; color:var(--accent); opacity:.16; pointer-events:none}
  figure.q.note::before{content:none}
  blockquote{margin:0; font-size:21px; line-height:1.62; white-space:pre-line}
  figure.q.long blockquote{font-size:19px}
  figure.q.note blockquote{font-style:italic}
  .annot{margin:16px 0 0; padding:10px 14px; border-left:3px solid var(--accent);
    background:var(--bg2); font-size:17px; font-style:italic; border-radius:0 8px 8px 0}
  figcaption{margin-top:18px; padding-top:12px; border-top:1px solid var(--line);
    display:flex; flex-wrap:wrap; align-items:center; gap:6px 14px;
    font-size:12.5px; letter-spacing:.07em; text-transform:uppercase; color:var(--muted)}
  .pg{color:var(--accent); font-weight:600}
  .copy{margin-left:auto; border:none; background:none; color:var(--muted); font-size:12.5px;
    letter-spacing:.07em; text-transform:uppercase; padding:4px 6px; border-radius:6px}
  .copy:hover{color:var(--accent); background:var(--bg2)}
  mark{background:linear-gradient(transparent 55%, #f4d58a 55%); color:inherit}
  html[data-theme="dark"] mark{background:linear-gradient(transparent 55%, #6b5320 55%)}
  .empty{text-align:center; color:var(--muted); font-style:italic; padding:60px 0; display:none}
  footer{text-align:center; margin-top:70px; color:var(--muted); font-size:14px; font-style:italic}
  @media (max-width:600px){ body{font-size:18px} figure.q{padding:28px 22px 20px}
    blockquote{font-size:19px} .pill{padding:0 12px} }
  @media print{ .toolbar,.mode,.copy{display:none} figure.q{box-shadow:none; break-inside:avoid} }
</style>
</head>
<body>
<div class="wrap">
  <header class="cover">
    <div class="kicker">Highlights</div>
    <h1>__TITLE__</h1>
    <p class="author">__AUTHOR__</p>
    <div class="rule"></div>
    <div class="count"><span id="n"></span></div>
  </header>

  <div class="toolbar">
    <label class="search">
      <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><line x1="16.5" y1="16.5" x2="21" y2="21"/></svg>
      <input id="q" type="search" placeholder="Search highlights…" autocomplete="off">
    </label>
    <button class="pill" id="copyall" title="Copy every visible highlight, text only">Copy all</button>
    <button class="icon" id="sort" title="Toggle reading order / date order" aria-label="Sort">⇅</button>
    <button class="icon" id="theme" title="Toggle dark mode" aria-label="Theme">☾</button>
  </div>
  <div class="mode" id="mode">Reading order</div>

  <main class="quotes" id="list"></main>
  <p class="empty" id="empty">No highlights match.</p>
  <footer>Recovered from a Kindle with kindle-highlights-recovery</footer>
</div>

<script>
const DATA = __DATA__;
const $ = id => document.getElementById(id);
const fmt = new Intl.DateTimeFormat(undefined,
  {weekday:'short', day:'numeric', month:'long', year:'numeric', hour:'2-digit', minute:'2-digit'});
let term = '', byDate = false;

function esc(s){ return s.replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function hi(s){
  let h = esc(s);
  if (!term) return h;
  const re = new RegExp('(' + term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
  return h.replace(re, '<mark>$1</mark>');
}
function visible(){
  let items = DATA.filter(d => !term ||
    (d.text + ' ' + d.notes.join(' ')).toLowerCase().includes(term));
  if (byDate) items = items.slice().sort((a, b) => (a.created || '').localeCompare(b.created || ''));
  return items;
}
function render(){
  const items = visible(), list = $('list');
  list.innerHTML = '';
  for (const d of items){
    const f = document.createElement('figure');
    f.className = 'q' + (d.kind === 'note' ? ' note' : '') + (d.text.length > 240 ? ' long' : '');
    const meta = [];
    if (d.page) meta.push('<span class="pg">p. ' + esc(d.page) + '</span>');
    if (d.chapter) meta.push('<span>' + esc(d.chapter) + '</span>');
    if (d.created) meta.push('<span>' + esc(fmt.format(new Date(d.created))) + '</span>');
    f.innerHTML = '<blockquote>' + hi(d.text) + '</blockquote>' +
      d.notes.map(n => '<p class="annot">' + hi(n) + '</p>').join('') +
      '<figcaption>' + meta.join('') + '<button class="copy">Copy</button></figcaption>';
    f.querySelector('.copy').onclick = e => copy(d.text, e.target, 'Copy');
    list.appendChild(f);
  }
  $('empty').style.display = items.length ? 'none' : 'block';
  const total = DATA.length;
  $('n').textContent = (items.length === total ? total : items.length + ' of ' + total) +
    (total === 1 ? ' highlight' : ' highlights');
}
function copy(text, btn, label){
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = 'Copied ✓'; btn.classList.add('done');
    setTimeout(() => { btn.textContent = label; btn.classList.remove('done'); }, 1500);
  });
}
$('q').oninput = e => { term = e.target.value.trim().toLowerCase(); render(); };
$('sort').onclick = () => { byDate = !byDate; $('mode').textContent = byDate ? 'Date order' : 'Reading order'; render(); };
$('copyall').onclick = e => {
  const items = visible();
  copy(items.map(d => d.text).join('\n\n'), e.target, 'Copy all');
};
const theme = localStorage.getItem('khr-theme');
if (theme === 'dark') { document.documentElement.dataset.theme = 'dark'; $('theme').textContent = '☀'; }
$('theme').onclick = () => {
  const dark = document.documentElement.dataset.theme === 'dark';
  document.documentElement.dataset.theme = dark ? 'light' : 'dark';
  $('theme').textContent = dark ? '☾' : '☀';
  localStorage.setItem('khr-theme', dark ? 'light' : 'dark');
};
render();
</script>
</body>
</html>
"""


def write(export: BookExport, path: Path) -> Path:
    data = [{
        "kind": q.kind, "text": q.text, "page": q.page, "chapter": q.chapter,
        "created": q.created.isoformat() if q.created else "", "notes": q.notes,
    } for q in export.quotes]
    # "</" inside the JSON would end the <script> element early.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    subs = {
        "__DATA__": payload,
        "__TITLE__": html.escape(export.title or "Untitled"),
        "__AUTHOR__": html.escape(export.author_line),
    }
    # One pass, so text inside a highlight can never be mistaken for a slot.
    page = re.sub(r"__(?:DATA|TITLE|AUTHOR)__", lambda m: subs[m.group(0)], TEMPLATE)
    path.write_text(page, encoding="utf-8")
    return path


INDEX = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kindle highlights</title>
<style>
  body{margin:0; background:#f5efe4; color:#2b2620; line-height:1.5;
    font-family:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif}
  .wrap{max-width:820px; margin:0 auto; padding:64px 24px 96px}
  h1{font-weight:600; font-size:46px; margin:0 0 6px; text-align:center}
  .sub{text-align:center; color:#857a68; letter-spacing:.12em; text-transform:uppercase; font-size:13px; margin-bottom:40px}
  a.card{display:flex; justify-content:space-between; align-items:baseline; gap:16px; text-decoration:none;
    color:inherit; background:#fffdf8; border:1px solid #e2d7c3; border-radius:12px; padding:18px 22px; margin-bottom:12px}
  a.card:hover{border-color:#a8541f}
  .t{font-size:21px} .a{color:#857a68; font-style:italic; font-size:16px}
  .n{color:#a8541f; white-space:nowrap; font-size:15px}
</style>
</head>
<body><div class="wrap">
<h1>Kindle highlights</h1>
<div class="sub">__SUMMARY__</div>
__CARDS__
</div></body></html>
"""


def write_index(outdir: Path) -> Path | None:
    """index.html linking every book exported into outdir (this run or earlier)."""
    books = []
    for jf in sorted(outdir.glob("*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or "quotes" not in data or "asin" not in data:
            continue
        if not jf.with_suffix(".html").is_file():
            continue
        n = sum(1 for q in data["quotes"] if q.get("type") == "highlight")
        books.append((data.get("title") or jf.stem, "; ".join(data.get("authors") or []),
                      n, jf.with_suffix(".html").name))
    if not books:
        return None
    books.sort(key=lambda b: b[0].lower())
    cards = "\n".join(
        f'<a class="card" href="{html.escape(href)}"><span><span class="t">{html.escape(t)}</span>'
        f'{"<br><span class=a>" + html.escape(a) + "</span>" if a else ""}</span>'
        f'<span class="n">{n} highlight{"" if n == 1 else "s"}</span></a>'
        for t, a, n, href in books)
    total = sum(b[2] for b in books)
    summary = f"{len(books)} book{'' if len(books) == 1 else 's'} · {total} highlights"
    subs = {"__CARDS__": cards, "__SUMMARY__": summary}
    page = re.sub(r"__(?:CARDS|SUMMARY)__", lambda m: subs[m.group(0)], INDEX)
    path = outdir / "index.html"
    path.write_text(page, encoding="utf-8")
    return path
