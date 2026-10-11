"""5.6.31 (cahier des charges additionnel, R17 / LG01–LG06) : mécanisme commun de traduction de l'interface.

- Quatre catalogues à clés stables (``agent/locales/fr.json``, ``en``, ``de``, ``es``) ; le français est la langue source et de repli.
- La langue est résolue pour CHAQUE requête (préférence enregistrée de l'utilisateur, sinon choix de session par cookie, sinon langue du
  cabinet, sinon français) et gardée dans un contexte propre au fil de la requête : deux avocats peuvent lire l'interface dans deux
  langues au même moment, sans variable globale partagée.
- Messages complets avec variables nommées (``{nom}``) et pluriels (``{"one": …, "other": …}``) ; jamais d'assemblage de fragments.
- ``t()`` rend du texte brut ; ``th()`` rend du HTML échappé, variables comprises : une traduction ne peut pas insérer de code.
- Une clé absente retombe sur le français et est notée pour le diagnostic ; ``check()`` exige les quatre langues et les mêmes variables.
- La langue de l'interface est distincte de la langue de rédaction, de la langue de recherche et du droit applicable au dossier (LG06).
"""
from functools import lru_cache
from html import escape
import json
import os
from pathlib import Path
import re
import string
import threading

LANGS = ('fr', 'en', 'de', 'es')
NAMES = {'fr': 'Français', 'en': 'English', 'de': 'Deutsch', 'es': 'Español'}
DEFAULT = 'fr'
COOKIE = 'axh_lang'
ROOT = Path(__file__).resolve().parent / 'locales'
_ctx = threading.local()
_missing = set()
_lock = threading.Lock()


@lru_cache(maxsize=8)
def catalog(lang):
    path = ROOT / (lang + '.json')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if not k.startswith('_')}


def clean(lang):
    lang = str(lang or '').strip().lower()[:5].split('-')[0]
    return lang if lang in LANGS else ''


def set_lang(lang):
    _ctx.lang = clean(lang) or DEFAULT


def current():
    return getattr(_ctx, 'lang', DEFAULT)


class _Vars(dict):
    def __missing__(self, key):
        return '{' + key + '}'


def _plural(entry, n, lang):
    if not isinstance(entry, dict):
        return entry
    if n is None:
        return entry.get('other') or next(iter(entry.values()))
    one = (n in (0, 1)) if lang == 'fr' else (n == 1)
    return entry.get('one' if one else 'other') or entry.get('other', '')


def t(key, lang=None, n=None, **values):
    """Texte traduit (non échappé) ; ``n`` choisit la forme du pluriel et reste disponible comme ``{n}``."""
    lang = clean(lang) or current()
    entry = catalog(lang).get(key)
    if entry is None:
        with _lock:
            _missing.add((lang, key))
        entry = catalog(DEFAULT).get(key, key)
    text = _plural(entry, n, lang)
    if n is not None:
        values.setdefault('n', n)
    return str(text).format_map(_Vars(values)) if values else str(text)


def th(key, lang=None, n=None, **values):
    """Texte traduit échappé pour le HTML ; les variables sont échappées avant insertion."""
    lang = clean(lang) or current()
    entry = catalog(lang).get(key)
    if entry is None:
        with _lock:
            _missing.add((lang, key))
        entry = catalog(DEFAULT).get(key, key)
    text = escape(str(_plural(entry, n, lang)), quote=False)
    if n is not None:
        values.setdefault('n', n)
    safe = _Vars({k: escape(str(v), quote=True) for k, v in values.items()})
    return text.format_map(safe) if values else text


def missing():
    with _lock:
        return sorted(_missing)


def _fields(text):
    if isinstance(text, dict):
        out = set()
        for value in text.values():
            out |= _fields(value)
        return out
    return {name for _, name, _, _ in string.Formatter().parse(str(text)) if name}


def check():
    """Contrôle de catalogue (R17 étape 5) : chaque message source existe dans les quatre langues avec les mêmes variables."""
    source = catalog(DEFAULT)
    problems = []
    for lang in LANGS[1:]:
        other = catalog(lang)
        for key, text in source.items():
            if key not in other:
                problems.append((lang, key, 'absent'))
            elif _fields(other[key]) != _fields(text):
                problems.append((lang, key, 'variables'))
            elif isinstance(text, dict) != isinstance(other[key], dict):
                problems.append((lang, key, 'pluriel'))
        for key in other:
            if key not in source:
                problems.append((lang, key, 'inconnu'))
    return problems


# --------------------------------------------------------------------------- préférences
def _prefs_path(cfg):
    return Path((cfg or {}).get('state_dir') or '.') / 'langues5631.json'


def _prefs(cfg):
    try:
        data = json.loads(_prefs_path(cfg).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def preference(cfg, owner):
    return clean(_prefs(cfg).get('users', {}).get(str(owner or ''), ''))


def cabinet_default(cfg):
    return clean(_prefs(cfg).get('cabinet', '')) or clean(((cfg or {}).get('interface') or {}).get('langue', '')) or DEFAULT


def save_preference(cfg, owner, lang, cabinet=False):
    """Préférence personnelle (ou langue proposée aux nouveaux comptes du cabinet) ; écriture atomique, fichier privé."""
    lang = clean(lang)
    if not lang:
        from .common import Stop
        raise Stop('langue_inconnue')
    path = _prefs_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        data = _prefs(cfg)
        if cabinet:
            data['cabinet'] = lang
        else:
            data.setdefault('users', {})[str(owner or 'cabinet')[:200]] = lang
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    return lang


def cookie_lang(env):
    raw = str((env or {}).get('HTTP_COOKIE') or '')
    m = re.search(r'(?:^|;\s*)' + COOKIE + r'=([A-Za-z-]{2,5})', raw)
    return clean(m.group(1)) if m else ''


def resolve(env, cfg, owner=''):
    """Préférence enregistrée de l'utilisateur, sinon choix de session (cookie), sinon langue du cabinet, sinon français."""
    return preference(cfg, owner) or cookie_lang(env) or cabinet_default(cfg)


def begin(env, cfg, owner='', role='administrateur'):
    """À appeler au début de chaque requête authentifiée : le contexte d'un fil n'hérite jamais de la requête précédente."""
    _ctx.role = role or 'administrateur'
    _ctx.owner = owner or ''
    set_lang(resolve(env, cfg, owner))
    return current()


def role():
    """Rôle de la requête en cours (filtre visuel du menu ; le serveur garde son propre contrôle des droits)."""
    return getattr(_ctx, 'role', 'administrateur')


def cookie_header(lang, prefix='/'):
    return ('Set-Cookie', '%s=%s; Path=%s; Max-Age=31536000; SameSite=Lax; Secure' % (COOKIE, clean(lang) or DEFAULT, prefix or '/'))


# --------------------------------------------------------------------------- formats
def format_date(value, lang=None):
    """Date longue lisible (``samedi 10 octobre 2026``) ; la valeur stockée n'est pas modifiée."""
    lang = clean(lang) or current()
    days = t('date.days', lang).split(',')
    months = t('date.months', lang).split(',')
    try:
        weekday, day, month, year = value.weekday(), value.day, value.month, value.year
    except AttributeError:
        return str(value)
    return t('date.long', lang, weekday=days[weekday], day=day, month=months[month - 1], year=year)


def format_number(value, lang=None, decimals=2):
    lang = clean(lang) or current()
    text = ('{:,.%df}' % decimals).format(float(value))
    sep_thousands, sep_decimal = {'fr': (' ', ','), 'de': ('.', ','), 'es': ('.', ','), 'en': (',', '.')}[lang]
    return text.replace(',', '\0').replace('.', sep_decimal).replace('\0', sep_thousands)


def js_messages(prefix_keys, lang=None):
    """Sous-ensemble du catalogue transmis aux scripts (messages dont le navigateur a besoin)."""
    lang = clean(lang) or current()
    base = catalog(DEFAULT)
    return {k: t(k, lang) for k in base if any(k.startswith(p) for p in prefix_keys)}


def menu_html(prefix, lang=None):
    """Choix de la langue : noms des langues (pas de drapeaux), formulaire envoyé en JSON par v5631.js."""
    lang = clean(lang) or current()
    options = ''.join('<option value="%s" lang="%s"%s>%s</option>' % (code, code, ' selected' if code == lang else '', escape(NAMES[code]))
                      for code in LANGS)
    return ('<label class="l5631-lang"><span>' + th('lang.label', lang) + '</span><select id="l5631-lang" data-url="' +
            escape(prefix + '/api440/m5631/langue', quote=True) + '">' + options + '</select></label>')
