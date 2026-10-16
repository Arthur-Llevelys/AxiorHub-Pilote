"""5.6.32 (lot 7, R17) : traduction des écrans métier en anglais, allemand et espagnol.

Les écrans métier sont écrits en français dans le code (des centaines de modules). Plutôt que de modifier chaque gabarit, la page
produite est traduite à la sortie, d'après un dictionnaire de phrases relevées sur les écrans réels (``agent/locales/ecrans_<langue>.json``) :
- textes entre balises (hors script, style, textarea, pre, code) et attributs visibles (placeholder, title, aria-label, alt, value des boutons) ;
- correspondance exacte de la phrase, espaces normalisés ; les nombres sont des variables (« 6 tâche(s) » ↔ « # tâche(s) ») ;
- le champ « message » des réponses JSON (notifications) ;
- les données (noms, objets de courriels, contenu des dossiers) ne sont jamais traduites : elles ne figurent pas au dictionnaire.
Une phrase absente du dictionnaire reste en français (jamais de traduction approximative). Les messages des catalogues 5.6.31 suivent
leur propre mécanisme (i18n5631).

5.6.34 : les textes écrits ensuite par les scripts du navigateur (boutons, états, compteurs, confirmations) sont traduits par
``static/v5634t.js`` avec ce même dictionnaire. Deux règles communes s'ajoutent : modèles « %s » (une donnée insérée, laissée telle
quelle : « Dépôt impossible : %s ») et phrases composées de segments séparés par « · », traduits un à un.
"""
import json
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'locales'
_LOCK = threading.Lock()
_CACHE = {}
SKIP = re.compile(r'(<(script|style|textarea|pre|code|section(?=[^>]*\btranslate="no"))\b[^>]*>.*?</\2\s*>)', re.S | re.I)   # 5.6.53 : section translate="no" (document en français)
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


_TEMPLATES = {}


def templates(table):
    """Modèles « %s » / « # » du dictionnaire, compilés une fois par langue."""
    key = id(table)
    with _LOCK:
        if key not in _TEMPLATES or _TEMPLATES[key][0] is not table:
            out = []
            for k, v in table.items():
                if '%s' not in k:
                    continue
                parts = re.split(r'(%s|#)', k)
                src = ''.join('(.+?)' if p == '%s' else r'(\d+)' if p == '#' else re.escape(p) for p in parts)
                out.append((re.compile('^' + src + '$'), v, [p for p in parts if p in ('%s', '#')]))
            _TEMPLATES[key] = (table, out)
        return _TEMPLATES[key][1]


def _template(norm, table):
    for rx, value, kinds in templates(table):
        m = rx.match(norm)
        if not m:
            continue
        groups = iter(zip(m.groups(), kinds))

        def fill(tok):
            got, kind = next(groups, (None, None))
            if got is None:
                return tok.group(0)
            return (phrase(got, table) or got).strip() if kind == '%s' else got
        return re.sub(r'%s|#', fill, value)
    return None


def _segments(norm, table):
    if ' · ' not in norm:
        return None
    changed, out = False, []
    for seg in norm.split(' · '):
        got = _single(seg, table)
        changed = changed or got is not None
        out.append(got if got is not None else seg)
    return ' · '.join(out) if changed else None


def _sentences(norm, table):
    parts = [s.strip() for s in re.findall(r'[^.!?…]+(?:[.!?…]+|$)\s*', norm) if s.strip()]
    if len(parts) < 2:
        return None
    got = [_single(s, table) for s in parts]
    return ' '.join(g if g is not None else s for g, s in zip(got, parts)) if any(g is not None for g in got) else None


def _single(norm, table):
    numbers = DIGITS.findall(norm)
    out = table.get(DIGITS.sub('#', norm))
    if out is None:
        return _template(norm, table)
    it = iter(numbers)
    return re.sub('#', lambda m: next(it, '#'), out) if numbers else out


def signature(lang):
    """Empreinte courte du dictionnaire d'une langue : le navigateur renouvelle sa copie dès qu'il change (v5634t.js)."""
    import hashlib
    try:
        return hashlib.sha256((ROOT / ('ecrans_%s.json' % lang)).read_bytes()).hexdigest()[:12]
    except OSError:
        return ''


def phrase(text, table):
    """Traduction d'une phrase (espaces conservés autour) ; None si elle n'est pas au dictionnaire."""
    stripped = text.strip()
    if not stripped or not re.search(r'[A-Za-zÀ-ÿ]', stripped):
        return None
    norm = re.sub(r'\s+', ' ', stripped)
    numbers = DIGITS.findall(norm)
    out = table.get(DIGITS.sub('#', norm))
    if out is None:
        for local in LOCAL_PREFIXES:   # fil d'Ariane d'abord : sinon la règle des segments en traduirait seulement la fin
            if norm.startswith(local) and len(norm) > len(local):
                rest = phrase(norm[len(local):], table)
                if rest is not None:
                    return text[:len(text) - len(text.lstrip())] + local + rest.strip() + text[len(text.rstrip()):]
        out = _template(norm, table) or _segments(norm, table) or _sentences(norm, table)
        deco = re.match(r'^([^\w«“„"(\[]+?\s+)(.+)$', norm) if out is None else None   # puce, séparateur ou icône (« ✏️ Modifier »)
        if deco:   # puce ou séparateur en tête (« · tout le cabinet · sources : … »)
            rest = phrase(deco.group(2), table)
            out = deco.group(1) + rest.strip() if rest is not None else None
        tail = re.match(r'^(.+?)(\s*[·—–:]\s*)$', norm) if out is None else None
        if tail:   # séparateur en fin (« 2 brouillon(s) préparé(s) · »)
            head = phrase(tail.group(1), table)
            out = head.strip() + tail.group(2) if head is not None else None
        if out is not None:
            return text[:len(text) - len(text.lstrip())] + out + text[len(text.rstrip()):]
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
            if parts[i + 1].lower() == 'textarea':   # 5.6.34 : contenu laissé tel quel, mais placeholder / title / aria-label traduits
                end = chunk.find('>') + 1
                chunk = ATTR.sub(attr_sub, chunk[:end]) + chunk[end:]
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
