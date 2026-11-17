"""Interface strings in English and Portuguese, plus saved user settings.

Use ``t("key", **fmt)`` for the current language. The language starts as the
one saved in the settings file, else the system's, else English.
"""
from __future__ import annotations

import json
import locale
import os
import sys
from pathlib import Path

LANGUAGES = {"en": "English", "pt": "Português"}

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        # window
        "app_title": "Kindle Highlights Recovery",
        "language": "Language",
        "search": "Search books…",
        "col_title": "Book",
        "col_hl": "Highlights",
        "col_status": "Status",
        "select_all": "Select all",
        "select_none": "Clear",
        "export_1": "Export 1 book",
        "export_n": "Export {n} books",
        "export_none": "Export",
        "refresh": "↻ Read Kindle again",
        "save_to": "Saving to:",
        "change": "Change…",
        "open_folder": "Open folder",
        "details_show": "Show details ▾",
        "details_hide": "Hide details ▴",
        "use_files": "No Kindle at hand? Use files copied from it…",
        "hint": "Tick the books you want and click Export, or double-click a book to export just that one.",
        # states
        "waiting": "Plug in your Kindle with a USB cable and unlock it. It's detected automatically.",
        "no_device_support": "Reading the Kindle over USB only works on Windows. "
                             "Copy the files off the Kindle (see README) and choose them below.",
        "reading_db": "Reading highlights from the Kindle…",
        "listing": "Looking at the books on the Kindle…",
        "identifying": "Identifying books ({i} of {n})… only slow the first time.",
        "ready": "{n} books on the Kindle, {h} with highlights.",
        "ready_files": "{h} books with highlights in the chosen folder.",
        "exporting": "Exporting {title}…",
        "done": "Done: {ok} of {n} exported.",
        "not_found": "Kindle not found: {err}",
        "error_msg": "Error: {err}",
        # row status
        "st_none": "no highlights",
        "st_drm": "purchased (DRM) 🔒",
        "st_ok": "✓ exported",
        "st_error": "error",
        "st_empty": "no text matched",
        "st_missing": "book file missing",
        "st_working": "exporting…",
        "st_checking": "checking…",
        "st_not_on_kindle": "not on the Kindle",
        # dialogs & log
        "pick_folder": "Choose the folder with the files copied from the Kindle",
        "pick_db": "Choose the annotation database (ksdk_annotation_v1.db)",
        "no_db_in_folder": "No annotation database (ksdk_annotation_v1.db) in that folder. Choose it now?",
        "nothing_selected": "Tick at least one book first.",
        "log_from_clippings": "Started from the Kindle's own My Clippings.txt.",
        "log_copying": "copying {name} from the Kindle…",
        "log_ok": "✓ {q} highlights · {c} new in My Clippings.txt",
        "log_skipped": "{n} highlight(s) matched no text and were skipped.",
        "log_drm": "This book was bought from the Kindle Store and is DRM-protected; its text can't be decoded.",
        "log_summary": "{ok} of {n} exported → {out}",
    },
    "pt": {
        "app_title": "Recuperar Grifos do Kindle",
        "language": "Idioma",
        "search": "Buscar livros…",
        "col_title": "Livro",
        "col_hl": "Grifos",
        "col_status": "Situação",
        "select_all": "Marcar todos",
        "select_none": "Limpar",
        "export_1": "Exportar 1 livro",
        "export_n": "Exportar {n} livros",
        "export_none": "Exportar",
        "refresh": "↻ Ler o Kindle de novo",
        "save_to": "Salvando em:",
        "change": "Alterar…",
        "open_folder": "Abrir pasta",
        "details_show": "Mostrar detalhes ▾",
        "details_hide": "Ocultar detalhes ▴",
        "use_files": "Sem o Kindle por perto? Usar arquivos copiados dele…",
        "hint": "Marque os livros e clique em Exportar, ou dê dois cliques num livro para exportar só ele.",
        "waiting": "Conecte o Kindle no cabo USB e desbloqueie a tela. Ele é detectado sozinho.",
        "no_device_support": "Ler o Kindle pelo USB só funciona no Windows. "
                             "Copie os arquivos do Kindle (veja o README) e escolha-os abaixo.",
        "reading_db": "Lendo os grifos do Kindle…",
        "listing": "Vendo os livros do Kindle…",
        "identifying": "Identificando livros ({i} de {n})… só demora na primeira vez.",
        "ready": "{n} livros no Kindle, {h} com grifos.",
        "ready_files": "{h} livros com grifos na pasta escolhida.",
        "exporting": "Exportando {title}…",
        "done": "Pronto: {ok} de {n} exportados.",
        "not_found": "Kindle não encontrado: {err}",
        "error_msg": "Erro: {err}",
        "st_none": "sem grifos",
        "st_drm": "comprado (DRM) 🔒",
        "st_ok": "✓ exportado",
        "st_error": "erro",
        "st_empty": "texto não encontrado",
        "st_missing": "arquivo do livro ausente",
        "st_working": "exportando…",
        "st_checking": "verificando…",
        "st_not_on_kindle": "não está no Kindle",
        "pick_folder": "Escolha a pasta com os arquivos copiados do Kindle",
        "pick_db": "Escolha o banco de anotações (ksdk_annotation_v1.db)",
        "no_db_in_folder": "Não achei o banco de anotações (ksdk_annotation_v1.db) nessa pasta. Escolher agora?",
        "nothing_selected": "Marque pelo menos um livro antes.",
        "log_from_clippings": "Comecei a partir do My Clippings.txt do próprio Kindle.",
        "log_copying": "copiando {name} do Kindle…",
        "log_ok": "✓ {q} grifos · {c} novos no My Clippings.txt",
        "log_skipped": "{n} grifo(s) não bateram com o texto e foram pulados.",
        "log_drm": "Este livro foi comprado na loja Kindle e tem DRM; o texto não pode ser lido.",
        "log_summary": "{ok} de {n} exportados → {out}",
    },
}

# Strings used inside the exported HTML pages and Markdown.
PAGE: dict[str, dict[str, str]] = {
    "en": {
        "html_lang": "en", "kicker": "Highlights", "title_suffix": "highlights",
        "search": "Search highlights…", "copy_all": "Copy all",
        "copy_all_tip": "Copy every visible highlight, text only", "copy": "Copy",
        "copied": "Copied ✓", "sort_tip": "Toggle reading order / date order",
        "theme_tip": "Toggle dark mode", "reading_order": "Reading order",
        "date_order": "Date order", "no_match": "No highlights match.",
        "footer": "Recovered from a Kindle with kindle-highlights-recovery",
        "of": "of", "one": "highlight", "many": "highlights", "untitled": "Untitled",
        "index_title": "Kindle highlights", "book": "book", "books": "books",
        "note": "Note", "page_abbr": "p.",
    },
    "pt": {
        "html_lang": "pt-BR", "kicker": "Grifos", "title_suffix": "grifos",
        "search": "Buscar nos grifos…", "copy_all": "Copiar tudo",
        "copy_all_tip": "Copiar todos os grifos visíveis, só o texto", "copy": "Copiar",
        "copied": "Copiado ✓", "sort_tip": "Alternar ordem de leitura / por data",
        "theme_tip": "Alternar modo escuro", "reading_order": "Ordem de leitura",
        "date_order": "Ordem por data", "no_match": "Nenhum grifo encontrado.",
        "footer": "Recuperado de um Kindle com kindle-highlights-recovery",
        "of": "de", "one": "grifo", "many": "grifos", "untitled": "Sem título",
        "index_title": "Grifos do Kindle", "book": "livro", "books": "livros",
        "note": "Nota", "page_abbr": "p.",
    },
}


def settings_path() -> Path:
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"])
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "kindle-highlights-recovery" / "settings.json"


def load_settings() -> dict:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(**values) -> None:
    data = load_settings()
    data.update(values)
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass                                   # settings are a convenience only


def system_language() -> str:
    names = []
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(var):
            names.append(os.environ[var])
    try:
        names.append(locale.getlocale()[0] or "")
    except ValueError:
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            if lcid & 0xFF == 0x16:            # LANG_PORTUGUESE
                return "pt"
        except (AttributeError, OSError):
            pass
    for n in names:
        if n.lower().startswith(("pt", "portuguese")):
            return "pt"
    return "en"


def normalize(lang: str | None) -> str:
    lang = (lang or "").lower()[:2]
    return lang if lang in LANGUAGES else "en"


_current = normalize(load_settings().get("lang") or system_language())


def get() -> str:
    return _current


def set_language(lang: str, persist: bool = True) -> None:
    global _current
    _current = normalize(lang)
    if persist:
        save_settings(lang=_current)


def t(key: str, **fmt) -> str:
    text = STRINGS[_current].get(key) or STRINGS["en"][key]
    return text.format(**fmt) if fmt else text


def page_strings(lang: str | None = None) -> dict[str, str]:
    return PAGE[normalize(lang or _current)]
