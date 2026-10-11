"""5.6.32 (lot 7, R17) : traduction des écrans métier en anglais, allemand et espagnol.

Les écrans métier sont écrits en français dans le code (des centaines de modules). Plutôt que de modifier chaque gabarit, la page
produite est traduite à la sortie, d'après un dictionnaire de phrases relevées sur les écrans réels (``agent/locales/ecrans_<langue>.json``) :
- textes entre balises (hors script, style, textarea, pre, code) et attributs visibles (placeholder, title, aria-label, alt, value des boutons) ;
- correspondance exacte de la phrase, espaces normalisés ; les nombres sont des variables (« 6 tâche(s) » ↔ « # tâche(s) ») ;
- le champ « message » des réponses JSON (notifications) ;
- les données (noms, objets de courriels, contenu des dossiers) ne sont jamais traduites : elles ne figurent pas au dictionnaire.
Une phrase absente du dictionnaire reste en français (jamais de traduction approximative). Les textes produits par les scripts du
navigateur et les messages des catalogues 5.6.31 suivent leur propre mécanisme (i18n5631).
"""
import json
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'locales'
_LOCK = threading.Lock()
_CACHE = {}
SKIP = re.compile(r'(<(script|style|textarea|pre|code)\b[^>]*>.*?</\2\s*>)', re.S | re.I)
TEXT = re.compile(r'(^|>)([^<>]+)(?=<|$)')   # aussi en bord de segment (texte suivi d'un <textarea> mis à part)
ATTR = re.compile(r'\b(placeholder|title|aria-label|alt|data-confirm)="([^"]*)"')
BUTTON_VALUE = re.compile(r'(<input\b[^>]*\btype="(?:submit|button)"[^>]*\bvalue=")([^"]*)(")', re.I)
DIGITS = re.compile(r'\d+')
PREFIXES = (('Page : ', None),)
LOCAL_PREFIXES = ('Workspace / ', 'Arbeitsbereich / ', 'Espacio de trabajo / ')


def dictionary(lang):
    with _LOCK:
        if lang not in _CACHE:
            try:
                raw = json.loads((ROOT / ('ecrans_%s.json' % lang)).read_text(encoding='utf-8'))
            except (OSError, ValueError):
                raw = {}
            _CACHE[lang] = {k: v for k, v in raw.items() if (not k.startswith('_') or k.startswith('_prefix:')) and isinstance(v, str) and v}
        return _CACHE[lang]


def phrase(text, table):
    """Traduction d'une phrase (espaces conservés autour) ; None si elle n'est pas au dictionnaire."""
    stripped = text.strip()
    if not stripped or not re.search(r'[A-Za-zÀ-ÿ]', stripped):
        return None
    norm = re.sub(r'\s+', ' ', stripped)
    numbers = DIGITS.findall(norm)
    out = table.get(DIGITS.sub('#', norm))
    if out is None:
        for local in LOCAL_PREFIXES:   # fil d'Ariane déjà en partie traduit (catalogues 5.6.31) : « Workspace / Vérification des citations »
            if norm.startswith(local) and len(norm) > len(local):
                rest = phrase(norm[len(local):], table)
                if rest is not None:
                    return text[:len(text) - len(text.lstrip())] + local + rest.strip() + text[len(text.rstrip()):]
        for fr, local in PREFIXES:   # préfixes fixes suivis d'une donnée (« Page : /dossiers »)
            if norm.startswith(fr) and table.get('_prefix:' + fr):
                return text[:len(text) - len(text.lstrip())] + table['_prefix:' + fr] + norm[len(fr):] + text[len(text.rstrip()):]
        return None
    if numbers:
        it = iter(numbers)
        out = re.sub('#', lambda m: next(it, '#'), out)
    lead = text[:len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    return lead + out + trail


def _unescape(value):
    from html import unescape
    return unescape(value)


def _escape(value, quote=False):
    from html import escape
    return escape(value, quote=quote)


def translate_html(html, lang):
    if lang in ('fr', '', None):
        return html
    table = dictionary(lang)
    if not table:
        return html

    def text_sub(m):
        got = phrase(_unescape(m.group(2)), table)
        return m.group(1) + _escape(got) if got is not None else m.group(0)

    def attr_sub(m):
        got = phrase(_unescape(m.group(2)), table)
        return '%s="%s"' % (m.group(1), _escape(got, True)) if got is not None else m.group(0)

    def value_sub(m):
        got = phrase(_unescape(m.group(2)), table)
        return m.group(1) + _escape(got, True) + m.group(3) if got is not None else m.group(0)

    parts = SKIP.split(html)
    out = []
    i = 0
    while i < len(parts):
        chunk = parts[i]
        if i % 3 == 0:
            chunk = TEXT.sub(text_sub, chunk)
            chunk = ATTR.sub(attr_sub, chunk)
            chunk = BUTTON_VALUE.sub(value_sub, chunk)
            out.append(chunk)
            i += 1
        else:
            out.append(chunk)   # bloc script/style/textarea… laissé tel quel
            i += 2
    return ''.join(out)


def translate_json(body, lang):
    """Champ « message » (notifications) d'une réponse JSON."""
    if lang in ('fr', '', None) or '"message"' not in body:
        return body
    table = dictionary(lang)
    if not table:
        return body
    try:
        data = json.loads(body)
    except ValueError:
        return body
    if isinstance(data, dict) and isinstance(data.get('message'), str):
        got = phrase(data['message'], table)
        if got is not None:
            data['message'] = got
            return json.dumps(data, ensure_ascii=False)
    return body


def apply(body, kind, lang):
    """Point de sortie unique des réponses (web.py) : HTML et JSON traduits si la langue n'est pas le français."""
    if lang in ('fr', '', None) or not isinstance(body, str):
        return body
    try:
        if kind.startswith('text/html'):
            return translate_html(body, lang)
        if kind.startswith('application/json'):
            return translate_json(body, lang)
    except Exception:   # noqa: BLE001 — une traduction ne doit jamais empêcher l'affichage
        return body
    return body


def coverage(lang, phrases):
    table = dictionary(lang)
    keys = {DIGITS.sub('#', re.sub(r'\s+', ' ', p.strip())) for p in phrases}
    return sum(1 for k in keys if k in table), len(keys)
