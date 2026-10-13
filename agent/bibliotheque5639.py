"""5.6.39 : bibliothèque de modèles (Corporate) — statuts, pactes, LOI, protocoles, cessions et contrats, en droit français, allemand,
espagnol et anglais ; modèles personnalisables par le cabinet et modèles Word importés.

Chaque modèle est une trame : lignes de texte, variables {{cle}}, clauses optionnelles [[si cle]] … [[sinon]] … [[fin]] (sur des lignes seules,
ou dans une ligne), articles numérotés « ## ». L'avocat peut :
  - préparer un document à partir d'une trame (variables pré-remplies depuis la société choisie, clauses cochées) : dépôt dans le dossier
    (JURIDIQUE), téléchargement Word, ou rédaction par l'IA qui reçoit la trame comme base ;
  - personnaliser la trame (texte, variables, clauses) ou la remplacer par son propre modèle Word (mêmes balises) ;
  - ajouter ses propres modèles (texte ou Word).
Le Pilote (rédaction de documents, docrequest520) reçoit la trame correspondante lorsque la demande la désigne clairement.

5.6.40 (audit 5.6.39) :
  B02 un seul « modèle effectif » (Word choisi, sinon texte personnalisé, sinon texte d'origine) pour le téléchargement, le dépôt et l'IA ;
      la demande IA garde un instantané immuable (texte complet, source, version, empreinte, sections attendues) ;
  B03 variables et clauses d'un modèle Word relevées et proposées dans le formulaire (libellés modifiables, fautes de frappe signalées) ;
  B04 clauses validées par un seul contrôle (texte et Word) à l'enregistrement et à la préparation : jamais de document tronqué ;
  B05 aucune coupe silencieuse : trame trop longue refusée avant la mise en file, sections manquantes signalées après rédaction ;
  B06 expiration réelle des téléchargements (24 h) et purge périodique ;
  B07 remplissage Word fragment par fragment (mise en forme des balises conservée) ;
  P02 catalogue construit en une lecture, sans création de table à chaque affichage ;
  E01 droit applicable par défaut réglé pour le cabinet, indépendant de la langue de l'interface ;
  E02 préparation en quatre étapes avec aperçu calculé par le même moteur que le document final.

Aucun document n'est signé ni envoyé ; les modèles étrangers portent la mention « à faire valider par un conseil local ».
"""
import base64
from datetime import date, datetime, timedelta, timezone
import difflib
import hashlib
from html import escape as e
import io
import json
import re
import uuid
import zipfile
from xml.sax.saxutils import escape as xml_escape

from .common import Stop, fold, load_matters, matter_display
from .erreurs5634 import Qualified
from . import bibliotheque_fr5639 as _fr
from . import bibliotheque_intl5639 as _intl
from . import bibliotheque_word5640 as W5

BUILTIN = {t['id']: t for t in _fr.TEMPLATES + _intl.TEMPLATES}
LANGS = ('fr', 'en', 'de', 'es')
DROITS = {'FR': 'Droit français', 'DE': 'Droit allemand', 'ES': 'Droit espagnol', 'EN': 'Droit anglais (Angleterre et pays de Galles)'}
LANGUES = {'fr': 'français', 'de': 'allemand', 'es': 'espagnol', 'en': 'anglais'}
DROIT_OF_LANG = {'fr': 'FR', 'de': 'DE', 'es': 'ES', 'en': 'EN'}
LANG_OF_DROIT = {v: k for k, v in DROIT_OF_LANG.items()}
CATEGORIES = {'statuts': 'Statuts', 'pacte': 'Pactes d’associés et d’actionnaires', 'operation': 'Opérations : LOI, protocoles, cessions',
              'contrat': 'Contrats'}
MISSING = {'fr': '[À COMPLÉTER : %s]', 'de': '[ZU ERGÄNZEN: %s]', 'es': '[A COMPLETAR: %s]', 'en': '[TO BE COMPLETED: %s]'}
ARTICLE = {'fr': 'Article %d — %s', 'de': '§ %d %s', 'es': 'Artículo %d. %s', 'en': '%d. %s'}
NOTICE = {
    'fr': 'Projet établi à partir de la bibliothèque de modèles AxiorHub : trame de travail à relire, compléter et adapter par l’avocat.',
    'de': 'Entwurf auf Grundlage der AxiorHub-Mustersammlung: von einem in Deutschland zugelassenen Berater zu prüfen und anzupassen.',
    'es': 'Borrador elaborado a partir de la biblioteca de modelos de AxiorHub: debe ser revisado y adaptado por un abogado colegiado en España.',
    'en': 'Draft prepared from the AxiorHub template library: to be reviewed and adapted by a lawyer qualified in England and Wales.',
}
TAG = re.compile(r'\{\{\s*(\w+)\s*\}\}')
BLOCK = re.compile(r'\[\[si\s+(!?)(\w+)\]\]')
INLINE = re.compile(r'\[\[si\s+(!?)(\w+)\]\]((?:(?!\[\[si\s).)*?)(?:\[\[sinon\]\]((?:(?!\[\[si\s).)*?))?\[\[fin\]\]', re.S)
MULTILINE = {'associes', 'parties', 'parteien', 'gesellschafter', 'socios', 'partes', 'shareholders', 'parties_list', 'chiffres', 'expose', 'origine',
             'prestations', 'services', 'leistungen', 'servicios'}
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
HELP_KEY = 'aide_axiorhub'   # bloc d'aide des trames Word exportées : jamais vrai, retiré à la génération
TTL_HOURS = 24               # B06 : durée de validité d'un document préparé en téléchargement
TRAME_RETENTION_DAYS = 30    # B06 : instantané non référencé par une demande de rédaction
LIMIT_TRAME = 45000          # B05 : trame transmise en entier à la rédaction IA (le reste du contexte : pièces du dossier, consignes)
SETTING_DROIT = 'bibliotheque5640:droit'   # E01 : droit applicable par défaut du cabinet
META_VERSION = 2             # 5.6.42 (audit 5.6.41, F02) : version des métadonnées d'un modèle Word (distincte de la version du modèle)
_READY = set()


def schema(desk):
    """Tables et migrations, une fois par base et par processus (P02 : jamais à chaque lecture d'un modèle)."""
    key = _key(desk)
    if key in _READY:   # P02 : aucune instruction de création à chaque lecture une fois la base initialisée
        return
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS bibliotheque5639(id TEXT PRIMARY KEY, base TEXT NOT NULL DEFAULT '', titre TEXT NOT NULL DEFAULT '',
      langue TEXT NOT NULL DEFAULT 'fr', droit TEXT NOT NULL DEFAULT 'FR', categorie TEXT NOT NULL DEFAULT 'contrat', texte TEXT NOT NULL DEFAULT '',
      word TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS bibliotheque5639_fichiers(jeton TEXT PRIMARY KEY, owner TEXT NOT NULL, nom TEXT NOT NULL, donnees BLOB NOT NULL,
      created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS bibliotheque5639_trames(jeton TEXT PRIMARY KEY, modele TEXT NOT NULL, titre TEXT NOT NULL, langue TEXT NOT NULL,
      texte TEXT NOT NULL, created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS bibliotheque5642_liens(jeton TEXT NOT NULL, demande TEXT NOT NULL, created TEXT NOT NULL,
      PRIMARY KEY(jeton, demande));
    CREATE INDEX IF NOT EXISTS bibliotheque5642_liens_demande ON bibliotheque5642_liens(demande);
    CREATE INDEX IF NOT EXISTS bibliotheque5642_trames_created ON bibliotheque5639_trames(created);
    ''')
    columns = {
        'bibliotheque5639': (('meta', "TEXT NOT NULL DEFAULT '{}'"), ('version', 'INTEGER NOT NULL DEFAULT 1')),
        'bibliotheque5639_fichiers': (('expires_at', "TEXT NOT NULL DEFAULT ''"),),
        'bibliotheque5639_trames': (('droit', "TEXT NOT NULL DEFAULT ''"), ('source', "TEXT NOT NULL DEFAULT 'texte'"),
                                    ('version', "TEXT NOT NULL DEFAULT ''"), ('empreinte', "TEXT NOT NULL DEFAULT ''"),
                                    ('empreinte_source', "TEXT NOT NULL DEFAULT ''"), ('sections', "TEXT NOT NULL DEFAULT '[]'")),
    }
    for table, cols in columns.items():
        have = {r[1] for r in desk.db.execute('PRAGMA table_info(%s)' % table)}
        for name, decl in cols:
            if name not in have:
                desk.db.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, name, decl))
    # B06 : lignes antérieures à la 5.6.40 : expiration dérivée de leur date de création (migration bornée)
    for row in desk.db.execute("SELECT jeton, created FROM bibliotheque5639_fichiers WHERE expires_at='' LIMIT 500").fetchall():
        desk.db.execute('UPDATE bibliotheque5639_fichiers SET expires_at=? WHERE jeton=?', (_expiry(row['created']), row['jeton']))
    desk.db.execute('CREATE INDEX IF NOT EXISTS bibliotheque5642_fichiers_expire ON bibliotheque5639_fichiers(expires_at)')
    desk.db.commit()
    _READY.add(key)


def _key(desk):
    return str((getattr(desk, 'c', None) or {}).get('state_dir') or id(desk.db))


def _query(desk, sql, args=()):
    """Lecture ; si la base a été remplacée (restauration) depuis l'initialisation, les tables sont recréées puis la lecture refaite."""
    import sqlite3
    try:
        return desk.db.execute(sql, args).fetchall()
    except sqlite3.OperationalError as exc:
        if 'no such' not in str(exc):
            raise
        _READY.discard(_key(desk))
        schema(desk)
        return desk.db.execute(sql, args).fetchall()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value):
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _expiry(created):
    moment = _parse_time(created) or datetime.now(timezone.utc)
    return (moment + timedelta(hours=TTL_HOURS)).isoformat()


def _lang():
    try:
        from .i18n5631 import current
        lang = current()
    except Exception:   # noqa: BLE001 — hors requête web (tâche de fond) : français
        lang = 'fr'
    return lang if lang in LANGS else 'fr'


def default_law(desk):
    """E01 : droit applicable proposé par défaut, réglé pour le cabinet (français si rien n'est réglé), jamais déduit de la langue."""
    try:
        value = str(desk.settings(SETTING_DROIT, 'FR') or 'FR')
    except Exception:   # noqa: BLE001
        value = 'FR'
    return value if value in DROITS else 'FR'


# ------------------------------------------------------------------------------------------------ catalogue
def _parse_keys(lines):
    """Variables et clauses présentes dans une trame texte (blocs et conditions en ligne)."""
    text = '\n'.join(lines)
    tags = list(dict.fromkeys(TAG.findall(text)))
    keys = []
    for m in W5.MARK.finditer(text):
        token = m.group(1)
        if re.match(r'si\s', token):
            key = token[2:].strip().lstrip('!')
            if key not in keys and not key.startswith('aide'):
                keys.append(key)
    return tags, keys


def _meta(row):
    try:
        return json.loads(row['meta'] or '{}') if row is not None and 'meta' in row.keys() else {}
    except ValueError:
        return {}


def _build(ident, row):
    """Modèle prêt à l'emploi depuis le modèle d'origine et la ligne du cabinet déjà lue (P02 : aucune lecture supplémentaire)."""
    base = BUILTIN.get(ident)
    if base is None and row is None:
        raise Stop('modele_bibliotheque_absent')
    t = dict(base) if base else {'id': ident, 'langue': row['langue'], 'droit': row['droit'], 'categorie': row['categorie'], 'formes': (),
                                 'titre': row['titre'], 'resume': {}, 'mots': (), 'variables': [], 'clauses': [], 'fixes': {}, 'texte': []}
    t.update(origine='cabinet' if base is None else 'axiorhub', personnalise=False, word='', version=1, meta={})
    if row is not None:
        if row['texte']:
            t['texte'] = row['texte'].split('\n')
            t['personnalise'] = base is not None
        t['word'] = row['word'] or ''
        t['version'] = int(row['version']) if 'version' in row.keys() and row['version'] else 1
        t['meta'] = _meta(row)
        if base is None:
            t['titre'] = row['titre']
    labels = t['meta'].get('libelles') or {}
    # variables et clauses ajoutées par le cabinet dans son texte et dans son modèle Word (B03) : proposées dans le formulaire
    tags, keys = _parse_keys(t['texte'])
    if t['word']:
        tags += [k for k in t['meta'].get('variables') or [] if k not in tags]
        keys += [k for k in t['meta'].get('clauses') or [] if k not in keys]
    known_v = {k for k, _, _ in t['variables']} | set(t['fixes'])
    known_c = {k for k, _, _, _ in t['clauses']}
    t['variables'] = [(k, (labels.get(k) or {}).get('libelle') or lab, (labels.get(k) or {}).get('defaut') or default)
                      for k, lab, default in t['variables']]
    t['variables'] += [(k, (labels.get(k) or {}).get('libelle') or k.replace('_', ' '), (labels.get(k) or {}).get('defaut') or '')
                       for k in tags if k not in known_v]
    t['clauses'] = list(t['clauses']) + [(k, (labels.get('clause_' + k) or {}).get('libelle') or k.replace('_', ' '), False, '')
                                         for k in keys if k not in known_c]
    return t


def template(desk, ident):
    """Modèle prêt à l'emploi : modèle d'origine, éventuellement personnalisé (texte, modèle Word) par le cabinet, ou modèle ajouté."""
    schema(desk)
    ident = str(ident or '')
    rows = _query(desk, 'SELECT * FROM bibliotheque5639 WHERE id=?', (ident,))
    t = _build(ident, rows[0] if rows else None)
    if needs_meta(t):   # 5.6.42 (F02) : modèle Word enregistré avant la 5.6.40 : champs et clauses relevés à la première ouverture
        hydrate(desk, t)
        rows = _query(desk, 'SELECT * FROM bibliotheque5639 WHERE id=?', (ident,))
        t = _build(ident, rows[0] if rows else None)
    return t


def needs_meta(t):
    """5.6.42 (F02) : modèle Word dont les métadonnées n'ont pas été calculées par la version actuelle (ni en erreur déjà signalée)."""
    meta = t.get('meta') or {}
    return bool(t.get('word')) and int(meta.get('meta_version') or 0) < META_VERSION and \
        (meta.get('hydratation') or {}).get('etat') != 'erreur'


def _word_meta(t, raw, base=None):
    """Métadonnées d'un modèle Word lu : variables, clauses, empreinte ; libellés et personnalisations du cabinet conservés."""
    tags, clauses = W5.word_keys(raw)
    meta = dict(base if base is not None else t.get('meta') or {})
    before_v, before_c = list(meta.get('variables') or []), list(meta.get('clauses') or [])
    meta.update(variables=tags, clauses=clauses, empreinte=_sha(raw), meta_version=META_VERSION,
                hydratation={'etat': 'ok', 'date': _now()},
                nouvelles=[k for k in tags if k not in before_v] + ['clause_' + k for k in clauses if k not in before_c] if before_v or before_c else [],
                disparues=sorted(set(before_v) - set(tags)) + ['clause_' + k for k in before_c if k not in clauses])
    return meta


def hydrate(desk, t):
    """5.6.42 (audit 5.6.41, F02) : reconstruit les métadonnées d'un modèle Word existant — identité, version, libellés et personnalisations
    conservés ; idempotent (un second passage ne duplique rien) ; lecture de la source HORS transaction ; une source illisible est
    enregistrée comme telle avec sa cause (la fiche propose de la retrouver ou de la réimporter)."""
    from .corporate5636 import load_template, check_docx
    meta = dict(t.get('meta') or {})
    try:
        raw = load_template(desk, t['word'])
        check_docx(raw)
    except Stop as exc:
        meta['hydratation'] = {'etat': 'erreur', 'cause': str(exc)[:80], 'date': _now()}
        desk.db.execute('UPDATE bibliotheque5639 SET meta=? WHERE id=?', (json.dumps(meta, ensure_ascii=False), t['id']))
        desk.db.commit()
        return False
    meta = _word_meta(t, raw, meta)
    meta['nouvelles'], meta['disparues'] = [], meta.get('disparues') if t['meta'].get('variables') else []
    desk.db.execute('UPDATE bibliotheque5639 SET meta=? WHERE id=?', (json.dumps(meta, ensure_ascii=False), t['id']))
    desk.db.commit()
    return True


def hydrate_pending(desk, limit=20):
    """5.6.42 (F02) : passage de maintenance borné sur les anciens modèles Word (reprise naturelle au passage suivant)."""
    schema(desk)
    done = 0
    for row in _query(desk, "SELECT id FROM bibliotheque5639 WHERE word<>'' LIMIT 200"):
        t = _build(row['id'], _query(desk, 'SELECT * FROM bibliotheque5639 WHERE id=?', (row['id'],))[0])
        if needs_meta(t):
            hydrate(desk, t)
            done += 1
            if done >= limit:
                break
    return done


def view_token(t):
    """5.6.42 (audit 5.6.41, F03) : version affichée d'un modèle (version et empreinte du Word) ; l'aperçu et la préparation lancés depuis
    une même page doivent porter sur cette version."""
    return '%s:%s' % (t.get('version', 1), ((t.get('meta') or {}).get('empreinte') or '')[:16] if t.get('word') else '')


def catalog(desk):
    """P02 : une seule lecture groupée des modèles du cabinet, puis construction en mémoire."""
    schema(desk)
    rows = {r['id']: r for r in _query(desk, 'SELECT * FROM bibliotheque5639')}
    out = []
    for ident in list(BUILTIN) + [i for i in rows if i not in BUILTIN]:
        try:
            out.append(_build(ident, rows.get(ident)))
        except Stop:
            continue
    return out


# ------------------------------------------------------------------------------------------------ rendu
def check_structure(lines, unit='Ligne'):
    """B04 : structure des clauses d'une trame texte ; erreur explicite (ligne et cause) au lieu d'un document tronqué."""
    errors = W5.validate(lines)
    if errors:
        raise Qualified('modele_conditions_desequilibrees', precision=W5.describe(errors, unit))


def apply_conditions(lines, flags):
    """Clauses optionnelles : blocs [[si cle]] / [[si !cle]] … [[sinon]] … [[fin]] sur des lignes seules, puis forme en ligne."""
    out, stack, active = [], [], True
    for line in lines:
        s = line.strip()
        m = BLOCK.fullmatch(s)
        if m:
            cond = bool(flags.get(m.group(2))) != bool(m.group(1))
            stack.append([active, cond])
            active = active and cond
            continue
        if s == '[[sinon]]' and stack:
            parent, cond = stack[-1]
            active = parent and not cond
            continue
        if s == '[[fin]]' and stack:
            active = stack.pop()[0]
            continue
        if active:
            out.append(inline_conditions(line, flags))
    return out


def inline_conditions(text, flags):
    return W5.apply_inline(text, flags) if '[[' in text else text


def values_for(t, data, lang=None):
    """Valeur de chaque variable : saisie, sinon valeur par défaut du modèle ; constantes du modèle (forme, textes applicables)."""
    out = {}
    for key, _, default in t['variables']:
        v = data.get(key)
        out[key] = str(v).strip() if v is not None and str(v).strip() else default
    declared = set(out)
    for key, v in t['fixes'].items():   # constantes du modèle (forme, textes applicables) : jamais remplacées par une donnée d'une autre société
        if key not in declared:
            out[key] = v
    return out


def flags_for(t, data):
    flags = {}
    for key, _, default, _ in t['clauses']:
        if 'clause_' + key in data:
            flags[key] = data['clause_' + key] is True or str(data['clause_' + key]) in ('1', 'on', 'true', 'True')
        else:
            flags[key] = bool(default)
    return flags


def _filler(t, values):
    lang = t['langue'] if t['langue'] in LANGS else 'fr'
    labels = {k: lab for k, lab, _ in t['variables']}

    def fill(text):
        for _ in range(3):
            text = TAG.sub(lambda m: values.get(m.group(1)) or MISSING[lang] % labels.get(m.group(1), m.group(1).replace('_', ' ')), text)
        return text
    return fill


def render(t, values, flags):
    """Trame → paragraphes (texte, style) : clauses appliquées, articles numérotés, variables remplacées (« à compléter » sinon)."""
    lang = t['langue'] if t['langue'] in LANGS else 'fr'
    fill = _filler(t, values)
    n = 0
    paras = []
    for line in apply_conditions(t['texte'], flags):
        if not line.strip():
            continue
        if line.startswith('## '):
            n += 1
            paras.append((ARTICLE[lang] % (n, fill(line[3:].strip())), 'article'))
            continue
        style, body = ('titre', line[2:]) if line.startswith('# ') else ('liste', line[2:]) if line.startswith('- ') else ('texte', line)
        for part in fill(body).split('\n'):
            if part.strip():
                paras.append((part.strip(), style))
    return paras


def plain_text(paras):
    return '\n'.join(('  – ' + x) if s == 'liste' else ('\n' + x) if s in ('article', 'titre') else x for x, s in paras).strip()


# ------------------------------------------------------------------------------------------------ Word
def _para_xml(text, style):
    ppr = {'titre': '<w:pPr><w:jc w:val="center"/><w:spacing w:after="120"/></w:pPr>',
           'article': '<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="80"/></w:pPr>',
           'liste': '<w:pPr><w:ind w:left="567" w:hanging="283"/><w:jc w:val="both"/><w:spacing w:after="60"/></w:pPr>',
           'note': '<w:pPr><w:spacing w:before="360"/></w:pPr>'}.get(style, '<w:pPr><w:jc w:val="both"/><w:spacing w:after="120"/></w:pPr>')
    rpr = {'titre': '<w:rPr><w:b/><w:sz w:val="26"/></w:rPr>', 'article': '<w:rPr><w:b/></w:rPr>',
           'note': '<w:rPr><w:i/><w:sz w:val="18"/></w:rPr>'}.get(style, '')
    body = ('– ' if style == 'liste' else '') + text
    return '<w:p>%s<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r></w:p>' % (ppr, rpr, xml_escape(body))


def build_docx(paras, lang='fr', notice=True):
    """Document Word autonome (Garamond 12, A4, marges usuelles) ; la mention « projet à relire » termine le document."""
    items = list(paras) + ([(NOTICE.get(lang, NOTICE['fr']), 'note')] if notice else [])
    body = ''.join(_para_xml(x, s) for x, s in items)
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="%s"><w:body>%s<w:sectPr><w:pgSz w:w="11906" '
                'w:h="16838"/><w:pgMar w:top="1418" w:right="1418" w:bottom="1418" w:left="1418" w:header="709" w:footer="709" w:gutter="0"/>'
                '</w:sectPr></w:body></w:document>') % (W, body)
    lang_tag = {'fr': 'fr-FR', 'de': 'de-DE', 'es': 'es-ES', 'en': 'en-GB'}.get(lang, 'fr-FR')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="%s"><w:docDefaults><w:rPrDefault><w:rPr>'
              '<w:rFonts w:ascii="Garamond" w:hAnsi="Garamond" w:cs="Garamond"/><w:sz w:val="24"/><w:lang w:val="%s"/></w:rPr></w:rPrDefault>'
              '</w:docDefaults></w:styles>') % (W, lang_tag)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/'
                   'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType='
                   '"application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
                   'officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/_rels/document.xml.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://'
                   'schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
                   'officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr('word/document.xml', document)
        z.writestr('word/styles.xml', styles)
    return out.getvalue()


HELP = {
    'fr': ['[[si aide_axiorhub]]', 'TRAME AXIORHUB — mode d’emploi (ce paragraphe est retiré des documents préparés) : modifiez librement le texte et la '
           'mise en forme. {{variable}} est remplacée par la donnée saisie ; un paragraphe [[si clause]] … [[fin]] (sur des lignes seules) n’est '
           'conservé que si la clause est cochée ; un paragraphe commençant par « ## » devient un article numéroté. Importez ensuite ce fichier '
           'dans AxiorHub (« Importer mon modèle Word »).', '[[fin]]'],
}


def template_docx(t):
    """Trame Word modifiable par le cabinet : texte brut avec ses {{variables}}, [[clauses]] et « ## » ; réimportable tel quel."""
    lines = HELP['fr'] + list(t['texte'])
    paras = []
    for line in lines:
        if not line.strip():
            continue
        paras.append((line, 'titre' if line.startswith('# ') else 'texte'))
    return build_docx(paras, t['langue'], notice=False)


def fill_word(raw, t, values, flags):
    """Modèle Word du cabinet rempli (B07 : fragment par fragment, mise en forme des balises conservée ; B04 : structure validée)."""
    return _fill_word(raw, t, values, flags)[0]


def _fill_word(raw, t, values, flags):
    lang = t['langue'] if t['langue'] in LANGS else 'fr'
    fill = _filler(t, values)
    try:
        return W5.fill(raw, flags, lambda name: fill('{{%s}}' % name), lambda n, title: ARTICLE[lang] % (n, title))
    except ValueError as exc:
        raise Qualified('modele_conditions_desequilibrees', precision='Modèle Word : %s' % exc) from None


# ------------------------------------------------------------------------------------------------ modèle effectif (B02)
def _sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else str(data).encode('utf-8')).hexdigest()


def effective(desk, t, data):
    """B02 : le modèle effectivement utilisé, identique pour le téléchargement, le dépôt et l'IA — Word du cabinet s'il est choisi, sinon texte
    personnalisé, sinon texte d'origine. Un Word illisible ou mal structuré est refusé avec une explication, jamais remplacé en silence."""
    values = values_for(t, data)
    flags = flags_for(t, data)
    if t.get('word'):
        from .corporate5636 import load_template, check_docx
        try:
            raw = load_template(desk, t['word'])
            check_docx(raw)
        except Stop as exc:
            raise Qualified('modele_word_illisible', precision='Le modèle Word du cabinet ne peut pas être lu (%s). Importez-le de nouveau, ou '
                                                              'retirez-le pour utiliser la trame texte (« Enregistrer le modèle Word » avec un '
                                                              'champ vide).' % exc) from None
        stored = (t.get('meta') or {}).get('empreinte') or ''
        if stored and stored != _sha(raw):   # 5.6.42 (audit 5.6.41, F03) : la source a changé depuis l'enregistrement du modèle
            meta = _word_meta(t, raw)
            desk.db.execute('UPDATE bibliotheque5639 SET meta=?, version=version+1, updated=? WHERE id=?',
                            (json.dumps(meta, ensure_ascii=False), _now(), t['id']))
            desk.db.commit()
            changes = ', '.join(('{{%s}}' % k) if not k.startswith('clause_') else '[[si %s]]' % k[7:] for k in meta['nouvelles'])
            gone = ', '.join(('{{%s}}' % k) if not k.startswith('clause_') else '[[si %s]]' % k[7:] for k in meta['disparues'])
            raise Qualified('modele_word_modifie',
                            precision='Le modèle Word a été modifié depuis son enregistrement : la fiche est actualisée (version %d)%s%s. '
                                      'Rechargez la page, vérifiez les informations et les clauses, puis relancez.' % (
                                          int(t.get('version', 1)) + 1, (' ; nouveaux : ' + changes) if changes else '',
                                          (' ; disparus : ' + gone) if gone else ''))
        docx, sections = _fill_word(raw, t, values, flags)
        return {'docx': docx, 'texte': W5.docx_text(docx), 'sections': sections, 'source': 'word', 'empreinte_source': _sha(raw),
                'version': t.get('version', 1), 'values': values, 'flags': flags}
    check_structure(t['texte'])
    paras = render(t, values, flags)
    fill = _filler(t, values)
    sections = [fill(line[3:].strip()) for line in apply_conditions(t['texte'], flags) if line.startswith('## ')]
    return {'docx': build_docx(paras, t['langue']), 'texte': plain_text(paras), 'sections': sections, 'source': 'texte',
            'empreinte_source': _sha('\n'.join(t['texte'])), 'version': t.get('version', 1), 'values': values, 'flags': flags}


def missing_mentions(t, texte):
    """Mentions « à compléter » qui restent dans le document (aperçu : mises en évidence)."""
    lang = t['langue'] if t['langue'] in LANGS else 'fr'
    head = MISSING[lang].split('%s')[0]
    out = []
    for m in re.finditer(re.escape(head) + r'([^\]]*)\]', texte):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


# ------------------------------------------------------------------------------------------------ société → variables
def prefill(desk, cid):
    """Données de la société choisie, sous les noms de variables de toutes les langues de la bibliothèque."""
    if not cid:
        return {}
    from . import corporate5636 as co
    c = co.company(desk, cid)
    rows, total = co.holdings(desk, cid)
    heads = co.officers(desk, cid)
    forme = co.FORMS.get(c['forme'], ('', 'titres'))
    holders = '\n'.join('%s — %d %s' % (r['name'], r['quantity'], forme[1]) for r in rows)
    head = heads[0]['name'] if heads else ''
    try:
        capital = co.money(c['capital']).replace(' €', '').replace(' €', '').replace(' €', '')
    except Exception:   # noqa: BLE001
        capital = c['capital']
    city = re.sub(r'^.*\d{5}\s+', '', c['siege'] or '').strip()
    base = {'denomination': c['denomination'], 'forme_long': forme[0], 'siege': c['siege'], 'capital': capital, 'nominal': c['nominal'],
            'nombre_titres': str(total or ''), 'objet': c['objet'], 'duree': c['duree'], 'rcs_ville': c['rcs'], 'siren': c['siren'],
            'associes': holders, 'parties': holders, 'premier_dirigeant': head, 'dirigeant': head, 'ville': city, 'titres': forme[1],
            'date_acte': date.today().strftime('%d/%m/%Y')}
    base.update({'firma': c['denomination'], 'sitz': city, 'stammkapital': capital, 'gesellschafter': holders, 'parteien': holders,
                 'denominacion': c['denomination'], 'domicilio': c['siege'], 'socios': holders, 'partes': holders, 'sociedad': c['denomination'],
                 'company_name': c['denomination'], 'registered_office': c['siege'], 'shareholders': holders})
    return {k: v for k, v in base.items() if v}


# ------------------------------------------------------------------------------------------------ actions
def _filename(t, values):
    who = values.get('denomination') or values.get('firma') or values.get('denominacion') or values.get('company_name') or ''
    name = '%s - %s%s.docx' % (date.today().isoformat(), t['titre'], (' - ' + who) if who and not who.startswith('[') else '')
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name)[:150]


def generate(desk, data, owner='cabinet', dav=None):
    """Préparer un document : dans le dossier (JURIDIQUE), en téléchargement, par l'IA avec la trame comme base, ou aperçu (E02)."""
    t = template(desk, data.get('modele'))
    where = str(data.get('destination') or 'telecharger')
    cid = str(data.get('societe') or '')
    if cid:   # données de la société pour les variables laissées vides (appel direct de l'API)
        data = {**prefill(desk, cid), **{k: v for k, v in data.items() if v not in (None, '')}}
    seen = str(data.get('version_vue') or '')
    if seen and seen != view_token(t):   # 5.6.42 (F03) : jamais un contenu différent de celui affiché sans en informer
        raise Qualified('modele_modifie_depuis_affichage',
                        precision='Le modèle a changé depuis l’affichage de cette page (version %s → %s) : rechargez la page, vérifiez les '
                                  'options et relancez l’aperçu.' % (seen.split(':')[0], view_token(t).split(':')[0]))
    if where == 'ia':
        return _ask_ai(desk, t, data, owner)
    eff = effective(desk, t, data)
    if where == 'apercu':   # E02 : aperçu par le même moteur que le document final, sans rien enregistrer
        missing = missing_mentions(t, eff['texte'])
        return {'text': eff['texte'], 'noreload': True, 'warnings': (['%d mention(s) à compléter : %s' % (len(missing), ', '.join(missing[:8]))]
                                                                      if missing else []),
                'message': 'Aperçu à jour (%s, version %s).' % ('modèle Word du cabinet' if eff['source'] == 'word' else 'trame texte', eff['version'])}
    raw, values, flags = eff['docx'], eff['values'], eff['flags']
    name = _filename(t, values)
    if where == 'dossier':
        matter = _matter(desk, data, cid)
        from .dav import DAV
        client = dav or DAV(desk.c['nextcloud'])
        folder = '%s/JURIDIQUE' % matter['path'].rstrip('/')
        client.ensure_folder(folder, matter['path'])
        stem, n = name[:-5], 1
        while True:
            target = folder + '/' + (name if n == 1 else '%s (%d).docx' % (stem, n))
            try:
                client.put_file(target, raw, DOCX_MIME)
                break
            except Stop as exc:
                if str(exc) != 'http_412' or n > 20:
                    raise
                n += 1
        desk.audit('bibliotheque5639_document', {'modele': t['id'], 'dossier': matter['id'], 'owner': owner, 'source': eff['source'],
                                                 'clauses': sorted(k for k, v in flags.items() if v)})
        return {'path': target, 'message': 'Document « %s » déposé dans le dossier %s (JURIDIQUE) : projet à relire.' % (name, matter_display(matter))}
    purge(desk)
    jeton = uuid.uuid4().hex
    now = _now()
    desk.db.execute('INSERT INTO bibliotheque5639_fichiers(jeton, owner, nom, donnees, created, expires_at) VALUES(?,?,?,?,?,?)',
                    (jeton, owner, name, raw, now, _expiry(now)))
    desk.db.commit()
    desk.audit('bibliotheque5639_telechargement', {'modele': t['id'], 'owner': owner, 'source': eff['source']})
    goto = '?vue=bibliotheque&modele=%s%s&fichier=%s' % (t['id'], ('&societe=' + cid) if re.fullmatch(r'[\w-]+', cid or '') else '', jeton)
    return {'goto': goto, 'message': 'Document Word prêt : cliquez « Télécharger le document ».'}


def _file_row(desk, jeton, owner=None):
    rows = _query(desk, 'SELECT jeton, owner, nom, created, expires_at FROM bibliotheque5639_fichiers WHERE jeton=?', (str(jeton or ''),))
    if not rows or (owner is not None and rows[0]['owner'] != owner):
        return None
    return rows[0]


def download(desk, jeton, owner='cabinet', now=None):
    """B06 : document préparé, au seul utilisateur qui l'a préparé, et seulement avant son expiration (contrôlée à chaque lecture)."""
    schema(desk)
    row = _file_row(desk, jeton, owner)
    if row is None:
        raise Stop('document_bibliotheque_expire')
    moment = now or datetime.now(timezone.utc)
    expires = _parse_time(row['expires_at']) or _parse_time(_expiry(row['created']))
    if expires is None or expires <= moment:
        desk.db.execute('DELETE FROM bibliotheque5639_fichiers WHERE jeton=?', (row['jeton'],))
        desk.db.commit()
        raise Stop('document_bibliotheque_expire')
    data = desk.db.execute('SELECT donnees FROM bibliotheque5639_fichiers WHERE jeton=?', (row['jeton'],)).fetchone()['donnees']
    return {'download': {'filename': row['nom'], 'mime': DOCX_MIME, 'base64': base64.b64encode(data).decode()}}


PURGE_BATCH = 500   # 5.6.42 (audit 5.6.41, performance) : maintenance bornée, reprise au passage suivant
LAST_PURGE = {}      # dernier résultat par base (journal de maintenance sans contenu de dossier)


def _references(desk):
    """5.6.42 (F07) : jetons des instantanés référencés — relation explicite demande–instantané, et marqueurs [trame:…] des demandes plus
    anciennes. Seule l'absence de la table des demandes au premier démarrage vaut « aucune référence » ; toute autre erreur de lecture est
    propagée : elle ne vaut jamais autorisation de supprimer."""
    import sqlite3
    referenced = {r['jeton'] for r in desk.db.execute('SELECT jeton FROM bibliotheque5642_liens').fetchall()}
    try:
        rows = desk.db.execute("SELECT request FROM docreq520 WHERE request LIKE '%[trame:%'").fetchall()
    except sqlite3.OperationalError as exc:
        if 'no such table' not in str(exc):
            raise
        rows = []
    for r in rows:
        referenced.update(re.findall(r'\[trame:([0-9a-f]{16})\]', r['request']))
    return referenced


def purge(desk, now=None):
    """B06 : documents temporaires expirés effacés ; instantanés de trames gardés tant qu'une demande de rédaction s'y réfère (preuve de la
    base fournie à l'IA), effacés trente jours après leur création sinon. Les documents déposés dans les dossiers ne sont jamais touchés.
    5.6.42 (audit 5.6.41, F07 et performance) : seuls les candidats expirés sont lus (index), par lots bornés ; les références sont établies
    AVANT toute suppression d'instantané, dans une même transaction ; si elles ne peuvent pas l'être, aucun instantané n'est supprimé et
    la purge est différée au passage suivant."""
    schema(desk)
    moment = now or datetime.now(timezone.utc)
    gone, deferred = 0, ''
    horizon = (moment + timedelta(seconds=1)).isoformat()
    for row in desk.db.execute('SELECT jeton, created, expires_at FROM bibliotheque5639_fichiers WHERE expires_at<? LIMIT ?',
                               (horizon, PURGE_BATCH)).fetchall():
        expires = _parse_time(row['expires_at']) or _parse_time(_expiry(row['created']))
        if expires is None or expires <= moment:
            desk.db.execute('DELETE FROM bibliotheque5639_fichiers WHERE jeton=?', (row['jeton'],))
            gone += 1
    desk.db.commit()
    limit = moment - timedelta(days=TRAME_RETENTION_DAYS)
    candidates = desk.db.execute('SELECT jeton, created FROM bibliotheque5639_trames WHERE created<? LIMIT ?',
                                 ((limit + timedelta(seconds=1)).isoformat(), PURGE_BATCH)).fetchall()
    if candidates:
        try:
            desk.db.execute('BEGIN IMMEDIATE')   # aucune demande ne peut se lier à un instantané pendant la décision
        except Exception:   # noqa: BLE001 — transaction déjà ouverte par l'appelant
            pass
        try:
            referenced = _references(desk)
        except Exception as exc:   # noqa: BLE001 — lecture impossible : on conserve tout
            desk.db.rollback()
            deferred = type(exc).__name__
        else:
            for row in candidates:
                created = _parse_time(row['created'])
                if row['jeton'] not in referenced and created is not None and created < limit:
                    desk.db.execute('DELETE FROM bibliotheque5639_trames WHERE jeton=?', (row['jeton'],))
                    gone += 1
            desk.db.commit()
    LAST_PURGE[_key(desk)] = {'date': moment.isoformat(timespec='seconds'), 'supprimes': gone,
                              'differee': bool(deferred), 'cause': deferred}
    if deferred:
        try:
            desk.audit('bibliotheque5642_purge_differee', {'cause': deferred})
        except Exception:   # noqa: BLE001
            pass
    return gone


def _matter(desk, data, cid):
    matters = {m['id']: m for m in load_matters(desk.c)}
    mid = str(data.get('matter') or '')
    if not mid and cid:
        from . import corporate5636 as co
        mid = co.company(desk, cid)['matter']
    if not mid or mid not in matters:
        raise Stop('dossier_requis')
    return matters[mid]


def _ask_ai(desk, t, data, owner):
    """Rédaction par l'IA : le modèle effectif rempli (B02) est conservé en entier avec sa provenance (B05) et transmis à la rédaction."""
    matter = _matter(desk, data, str(data.get('societe') or ''))
    eff = effective(desk, t, data)
    texte = eff['texte']
    if len(texte) > LIMIT_TRAME:   # B05 : refus explicite avant la mise en file, jamais de coupe silencieuse
        raise Qualified('trame_trop_longue', precision='La trame préparée compte %d caractères ; la rédaction IA en accepte %d en entier. '
                                                       'Désactivez des clauses facultatives ou allégez le modèle, ou préparez le document en Word '
                                                       '(téléchargement ou dépôt dans le dossier) puis faites retravailler des sections par l’IA.'
                        % (len(texte), LIMIT_TRAME))
    jeton = uuid.uuid4().hex[:16]
    desk.db.execute('INSERT INTO bibliotheque5639_trames(jeton, modele, titre, langue, texte, created, droit, source, version, empreinte, '
                    'empreinte_source, sections) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (jeton, t['id'], t['titre'], t['langue'], texte, _now(), t['droit'], eff['source'], str(eff['version']), _sha(texte),
                     eff['empreinte_source'], json.dumps(eff['sections'], ensure_ascii=False)))
    desk.db.commit()
    consignes = re.sub(r'\s+', ' ', str(data.get('consignes') or '')).strip()[:3000]
    request = ('Rédige « %s » (%s, document en %s) pour ce dossier en suivant la trame de la bibliothèque AxiorHub [trame:%s] : conserve sa '
               'structure (%d section(s) attendue(s)) et les clauses retenues, complète les mentions à compléter à partir des pièces du dossier, '
               'sans rien inventer.%s') % (
        t['titre'], DROITS.get(t['droit'], t['droit']), LANGUES.get(t['langue'], t['langue']), jeton, len(eff['sections']),
        (' Consignes de l’avocat : ' + consignes) if consignes else '')
    from .docrequest520 import submit
    out = submit(desk, request, matter['id'], 'contrat' if t['categorie'] in ('contrat', 'pacte', 'operation') else 'autre')
    desk.db.execute('INSERT OR IGNORE INTO bibliotheque5642_liens(jeton, demande, created) VALUES(?,?,?)', (jeton, out['request'], _now()))
    desk.db.commit()   # 5.6.42 (F07) : relation explicite demande–instantané, protégée de la purge
    desk.audit('bibliotheque5639_ia', {'modele': t['id'], 'dossier': matter['id'], 'owner': owner, 'source': eff['source']})
    return {'request': out['request'], 'job': out['job_id'],
            'message': 'Rédaction confiée à l’IA avec la trame « %s » : le projet Word sera déposé dans le dossier %s.' % (t['titre'], matter_display(matter))}


def customize(desk, data, owner='cabinet'):
    """Texte personnalisé d'un modèle (ou d'un modèle ajouté) : variables et clauses relevées dans le texte ; structure validée (B04)."""
    t = template(desk, data.get('modele'))
    text = str(data.get('texte') or '').replace('\r\n', '\n').replace('\r', '\n')
    lines = [re.sub(r'[\x00-\x08\x0b-\x1f]', '', x).rstrip() for x in text.split('\n')]
    if not any(x.strip() for x in lines) or len(text) > 200000:
        raise Stop('texte_modele_invalide')
    check_structure(lines)   # refus avec la ligne en cause ; le modèle précédent reste en service
    _save(desk, t, owner, texte='\n'.join(lines))
    tags, keys = _parse_keys(lines)
    desk.audit('bibliotheque5639_personnalise', {'modele': t['id'], 'owner': owner})
    return {'message': 'Modèle « %s » enregistré pour le cabinet : %d variable(s), %d clause(s) optionnelle(s).' % (t['titre'], len(tags), len(keys))}


def _save(desk, t, owner, **fields):
    schema(desk)
    row = desk.db.execute('SELECT * FROM bibliotheque5639 WHERE id=?', (t['id'],)).fetchone()
    now = _now()
    if row is None:
        desk.db.execute('INSERT INTO bibliotheque5639(id, base, titre, langue, droit, categorie, texte, word, owner, created, updated, meta, version) '
                        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (t['id'], t['id'] if t['id'] in BUILTIN else '', t['titre'], t['langue'], t['droit'],
                                                              t['categorie'], fields.get('texte', ''), fields.get('word', ''), owner, now, now,
                                                              fields.get('meta', '{}'), 1))
    else:
        for k, v in fields.items():
            desk.db.execute('UPDATE bibliotheque5639 SET %s=?, updated=? WHERE id=?' % k, (v, now, t['id']))
        if set(fields) & {'texte', 'word'}:
            desk.db.execute('UPDATE bibliotheque5639 SET version=version+1 WHERE id=?', (t['id'],))
    desk.db.commit()


def restore(desk, data, owner='cabinet'):
    t = template(desk, data.get('modele'))
    if t['id'] not in BUILTIN:
        raise Stop('modele_bibliotheque_absent')
    schema(desk)
    desk.db.execute('DELETE FROM bibliotheque5639 WHERE id=?', (t['id'],))
    desk.db.commit()
    desk.audit('bibliotheque5639_retabli', {'modele': t['id'], 'owner': owner})
    return {'message': 'Modèle d’origine rétabli (texte et modèle Word du cabinet retirés).'}


def set_word(desk, data, owner='cabinet'):
    """Modèle Word du cabinet pour cette trame (importé depuis le poste ou choisi dans Nextcloud) ; vide = retour au texte.
    B04 : structure des clauses validée avant enregistrement ; B03 : variables et clauses relevées et proposées dans le formulaire."""
    t = template(desk, data.get('modele'))
    path = str(data.get('path') or '').strip()
    if not path:
        meta = dict(t['meta'])
        meta.pop('variables', None)
        meta.pop('clauses', None)
        _save(desk, t, owner, word='', meta=json.dumps(meta, ensure_ascii=False))
        return {'message': 'Modèle Word retiré : la trame texte est utilisée.'}
    from .corporate5636 import _source, load_template, check_docx
    path = _source(path)
    raw = load_template(desk, path)
    check_docx(raw)
    _, problems = W5.word_structure(raw)
    if problems:   # le modèle précédemment valide reste en service
        raise Qualified('modele_conditions_desequilibrees', precision='Modèle Word refusé — %s' % problems)
    tags, clauses = W5.word_keys(raw)
    standard = {k for k, _, _ in BUILTIN[t['id']]['variables']} if t['id'] in BUILTIN else set()
    standard |= set(t['fixes']) | {k for k, _, _ in t['variables']} | set(prefill_keys())
    previous = set(t['meta'].get('variables') or [])
    gone = sorted(previous - set(tags))
    suspects = {}
    for tag in tags:
        if tag not in standard:
            close = difflib.get_close_matches(tag, sorted(standard), n=1, cutoff=0.8)
            if close:
                suspects[tag] = close[0]
    labels = {k: v for k, v in (t['meta'].get('libelles') or {}).items() if k in tags or k.startswith('clause_')}
    meta = {**t['meta'], 'variables': tags, 'clauses': clauses, 'empreinte': _sha(raw), 'libelles': labels, 'suspectes': suspects,
            'disparues': gone, 'nouvelles': [], 'meta_version': META_VERSION, 'hydratation': {'etat': 'ok', 'date': _now()}}
    _save(desk, t, owner, word=path, meta=json.dumps(meta, ensure_ascii=False))
    desk.audit('bibliotheque5639_word', {'modele': t['id'], 'owner': owner, 'balises': len(tags), 'clauses': len(clauses)})
    notes = ['%d variable(s), %d clause(s) relevée(s)' % (len(tags), len(clauses))]
    if suspects:
        notes.append('à vérifier (faute de frappe ?) : ' + ', '.join('{{%s}} → {{%s}}' % kv for kv in sorted(suspects.items())))
    if gone:
        notes.append('champs disparus par rapport au modèle précédent : ' + ', '.join('{{%s}}' % x for x in gone))
    return {'message': 'Modèle Word enregistré : %s.' % ' ; '.join(notes)}


def refresh(desk, data, owner='cabinet'):
    """5.6.42 (F02, F03) : « Actualiser le modèle » — relit le Word du cabinet, présente les champs nouveaux et disparus ; la version change
    seulement si le contenu a changé."""
    t = template(desk, data.get('modele'))
    if not t.get('word'):
        raise Stop('modele_word_absent')
    from .corporate5636 import load_template, check_docx
    raw = load_template(desk, t['word'])
    check_docx(raw)
    _, problems = W5.word_structure(raw)
    if problems:
        raise Qualified('modele_conditions_desequilibrees', precision='Modèle Word refusé — %s' % problems)
    changed = (t['meta'].get('empreinte') or '') != _sha(raw)
    meta = _word_meta(t, raw)
    desk.db.execute('UPDATE bibliotheque5639 SET meta=?%s, updated=? WHERE id=?' % (', version=version+1' if changed else ''),
                    (json.dumps(meta, ensure_ascii=False), _now(), t['id']))
    desk.db.commit()
    desk.audit('bibliotheque5642_actualise', {'modele': t['id'], 'owner': owner, 'modifie': changed})
    if not changed:
        return {'message': 'Modèle Word inchangé depuis son enregistrement (version %s).' % t.get('version', 1)}
    return {'message': 'Modèle Word actualisé (version %d) : %d nouveau(x) champ(s) ou clause(s), %d disparu(s) — vérifiez les options.' % (
        int(t.get('version', 1)) + 1, len(meta['nouvelles']), len(meta['disparues']))}


def prefill_keys():
    return ('denomination', 'forme_long', 'siege', 'capital', 'nominal', 'nombre_titres', 'objet', 'duree', 'rcs_ville', 'siren', 'associes',
            'parties', 'premier_dirigeant', 'dirigeant', 'ville', 'titres', 'date_acte', 'firma', 'sitz', 'stammkapital', 'gesellschafter',
            'parteien', 'denominacion', 'domicilio', 'socios', 'partes', 'sociedad', 'company_name', 'registered_office', 'shareholders')


def set_labels(desk, data, owner='cabinet'):
    """B03 : libellé et valeur par défaut des champs relevés dans le modèle du cabinet (Word ou texte)."""
    t = template(desk, data.get('modele'))
    known = {k for k, _, _ in t['variables']} | {'clause_' + k for k, _, _, _ in t['clauses']}
    labels = dict(t['meta'].get('libelles') or {})
    for key in known:
        label = re.sub(r'\s+', ' ', str(data.get('libelle_' + key) or '')).strip()[:120]
        default = str(data.get('defaut_' + key) or '').strip()[:500]
        if label or default:
            labels[key] = {'libelle': label, 'defaut': default}
        elif ('libelle_' + key) in data:
            labels.pop(key, None)
    _save(desk, t, owner, meta=json.dumps({**t['meta'], 'libelles': labels}, ensure_ascii=False))
    return {'message': 'Libellés des champs enregistrés.'}


def set_default_law(desk, data, owner='cabinet'):
    """E01 : droit applicable proposé par défaut au cabinet (indépendant de la langue de l'interface)."""
    droit = str(data.get('droit') or '')
    if droit not in DROITS:
        raise Stop('modele_categorie_invalide')
    desk.setting(SETTING_DROIT, droit)
    desk.audit('bibliotheque5640_droit', {'droit': droit, 'owner': owner})
    return {'message': 'Droit applicable par défaut du cabinet : %s.' % DROITS[droit]}


def add(desk, data, owner='cabinet'):
    """« Ajouter mon modèle » : titre, catégorie, droit, et texte (syntaxe de la bibliothèque) et/ou modèle Word."""
    titre = re.sub(r'\s+', ' ', str(data.get('titre') or '')).strip()[:160]
    if not titre:
        raise Stop('titre_modele_requis')
    categorie = str(data.get('categorie') or 'contrat')
    droit = str(data.get('droit') or 'FR')
    if categorie not in CATEGORIES or droit not in DROITS:
        raise Stop('modele_categorie_invalide')
    text = str(data.get('texte') or '').strip()
    path = str(data.get('path') or '').strip()
    if not text and not path:
        raise Stop('texte_ou_modele_word_requis')
    ident = 'cab_' + uuid.uuid4().hex[:12]
    schema(desk)
    now = _now()
    desk.db.execute('INSERT INTO bibliotheque5639(id, base, titre, langue, droit, categorie, texte, word, owner, created, updated, meta, version) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (ident, '', titre, LANG_OF_DROIT[droit], droit, categorie, '', '', owner, now, now, '{}', 1))
    desk.db.commit()
    try:
        if text:
            customize(desk, {'modele': ident, 'texte': text}, owner)
        if path:
            set_word(desk, {'modele': ident, 'path': path}, owner)
    except Stop:
        desk.db.execute('DELETE FROM bibliotheque5639 WHERE id=?', (ident,))
        desk.db.commit()
        raise
    desk.audit('bibliotheque5639_ajout', {'modele': ident, 'owner': owner})
    return {'id': ident, 'goto': '?vue=bibliotheque&modele=' + ident, 'message': 'Modèle « %s » ajouté à la bibliothèque du cabinet.' % titre}


def remove(desk, data, owner='cabinet'):
    ident = str(data.get('modele') or '')
    if ident in BUILTIN or not ident.startswith('cab_'):
        raise Stop('modele_bibliotheque_absent')
    schema(desk)
    desk.db.execute('DELETE FROM bibliotheque5639 WHERE id=?', (ident,))
    desk.db.commit()
    desk.audit('bibliotheque5639_retire', {'modele': ident, 'owner': owner})
    return {'goto': '?vue=bibliotheque', 'message': 'Modèle retiré de la bibliothèque.'}


def word_template(desk, ident):
    t = template(desk, ident)
    if t.get('word'):
        from .corporate5636 import load_template
        raw = load_template(desk, t['word'])
    else:
        raw = template_docx(t)
    name = re.sub(r'[\\/:*?"<>|]', '_', 'Trame - %s.docx' % t['titre'])[:150]
    return {'download': {'filename': name, 'mime': DOCX_MIME, 'base64': base64.b64encode(raw).decode()}}


# ------------------------------------------------------------------------------------------------ IA (Pilote, rédaction de documents)
def _words(text):
    return set(re.findall(r'\w+', fold(str(text or '')).lower()))


def match(desk, request, lang=None):
    """Modèle désigné par une demande de rédaction : expression caractéristique du modèle entièrement présente dans la demande (mots
    entiers) ; en cas d'égalité, le droit applicable par défaut du cabinet (E01), puis le droit français — jamais la seule langue de
    l'interface."""
    words = _words(request)
    try:
        law = default_law(desk)
    except Exception:   # noqa: BLE001
        law = 'FR'
    best = None
    for t in catalog(desk):
        for phrase in t.get('mots') or ():
            need = _words(phrase)
            if not need or not need <= words:
                continue
            if len(need) < 2 and next(iter(need)) not in STRONG:
                continue
            score = (len(need), t['droit'] == law, t['droit'] == 'FR')
            if best is None or score > best[0]:
                best = (score, t)
    return best[1] if best else None


STRONG = {'nda', 'cgv', 'agb', 'satzung', 'gesellschaftsvertrag', 'estatutos', 'gesellschaftervereinbarung', 'anteilskaufvertrag',
          'absichtserklarung', 'geheimhaltungsvereinbarung', 'dienstleistungsvertrag', 'gesellschafterdarlehen', 'darlehensvertrag',
          'beteiligungsvereinbarung', 'abtretungsvertrag', 'vertraulichkeitsvereinbarung', 'unternehmergesellschaft'}


def trame_for_request(desk, request):
    """Trame transmise à la rédaction : l'instantané choisi dans la bibliothèque ([trame:…], immuable, complet), sinon le modèle effectif
    clairement désigné par la demande. B05 : jamais de coupe silencieuse ; une trame trop longue n'est pas tronquée — ses sections
    attendues sont transmises et l'omission du texte est signalée."""
    schema(desk)
    m = re.search(r'\[trame:([0-9a-f]{16})\]', str(request or ''))
    if m:
        row = desk.db.execute('SELECT * FROM bibliotheque5639_trames WHERE jeton=?', (m.group(1),)).fetchone()
        if row is None:   # 5.6.42 (audit 5.6.41, F05) : jamais de repli sur le modèle actuel pour une trame explicitement choisie
            raise Qualified('instantane_modele_introuvable',
                            precision='La trame choisie dans la bibliothèque pour cette demande est introuvable (restauration partielle ou '
                                      'incident de maintenance). Choisissez de nouveau le modèle dans la bibliothèque (Corporate › '
                                      'Bibliothèque de modèles › Rédiger par l’IA) : la rédaction ne se fera pas sur une autre version.')
        stored = row['empreinte'] if 'empreinte' in row.keys() else ''
        if stored and stored != _sha(row['texte']):
            raise Qualified('instantane_modele_altere',
                            precision='La trame enregistrée pour cette demande ne correspond plus à son empreinte : choisissez de nouveau '
                                      'le modèle dans la bibliothèque.')
        if row:
            sections = json.loads(row['sections'] or '[]') if 'sections' in row.keys() else []
            return {'titre': row['titre'], 'langue': row['langue'], 'droit': DROITS.get(row['droit'], row['droit']) if 'droit' in row.keys() else '',
                    'origine': 'choisie par l’avocat dans la bibliothèque', 'source': row['source'] if 'source' in row.keys() else 'texte',
                    'version': row['version'] if 'version' in row.keys() else '', 'empreinte': _sha(row['texte']),
                    'consigne': CONSIGNE, 'sections_attendues': sections, 'texte': row['texte']}
    t = match(desk, request)
    if not t:
        return None
    try:
        eff = effective(desk, t, {})
    except Stop:   # modèle du cabinet illisible ou mal structuré : aucune trame plutôt qu'une trame fausse
        return None
    base = {'titre': t['titre'], 'langue': t['langue'], 'droit': DROITS.get(t['droit'], t['droit']),
            'origine': 'modèle de la bibliothèque correspondant à la demande', 'source': eff['source'], 'version': eff['version'],
            'consigne': CONSIGNE, 'clauses_proposees': [lab for k, lab, _, _ in t['clauses'] if eff['flags'].get(k)],
            'sections_attendues': eff['sections']}
    if len(eff['texte']) > LIMIT_TRAME:
        return {**base, 'texte': '', 'texte_omis': 'trame trop longue pour être transmise en entier (%d caractères) : suivre les sections '
                                                   'attendues et le signaler dans a_completer' % len(eff['texte'])}
    return {**base, 'empreinte': _sha(eff['texte']), 'texte': eff['texte']}


HEADING_STYLES = {'titre', 'intertitre', 'titre1', 'titre2', 'heading', 'heading1', 'heading2'}
MIN_BODY = 40   # caractères utiles sous un intitulé pour qu'une section soit réellement rédigée


def _title_key(text):
    """Intitulé normalisé : sans numérotation (« Article 3 — », « 3. », « II - »), sans mention à compléter, minuscules sans accents."""
    key = fold(str(text or '')).lower()
    key = re.sub(r'\[[^\]]*\]', ' ', key)
    key = re.sub(r'^\s*(article|art\.|section|chapitre|titre|clause|§)\s*[\divxlc]*\s*', ' ', key)
    key = re.sub(r'^\s*[\divxlc]+\s*[.)\-–—:]\s*', ' ', key)
    key = re.sub(r'[^\w\s]', ' ', key)
    return re.sub(r'\s+', ' ', key).strip()


def _same_title(expected, heading):
    if not expected or not heading:
        return False
    if expected == heading or expected in heading or heading in expected:
        return True
    words = [w for w in expected.split() if len(w) > 3]
    if words and all(w in heading.split() for w in words):
        return True
    return difflib.SequenceMatcher(None, expected, heading).ratio() >= 0.85


def coverage_report(desk, request, doc):
    """5.6.42 (audit 5.6.41, F06) : couverture STRUCTURELLE des sections attendues de la trame. Une section n'est présente que si un
    intitulé du projet lui correspond (numérotation et variantes normalisées) ET qu'un corps exploitable le suit ; une section seulement
    citée dans une phrase, ou un intitulé vide, n'est pas une section rédigée. Un contrôle impossible est signalé comme tel, jamais
    comme une absence d'écart. La qualité juridique du contenu reste à relire par l'avocat."""
    try:
        trame = trame_for_request(desk, request)
    except Exception as exc:   # noqa: BLE001
        return {'statut': 'controle_indisponible', 'cause': str(exc)[:80], 'attendues': [], 'presentes': [], 'vides': [],
                'citees_seulement': [], 'absentes': []}
    expected = [str(x) for x in (trame or {}).get('sections_attendues') or []]
    if not expected:
        return {'statut': 'sans_objet', 'attendues': [], 'presentes': [], 'vides': [], 'citees_seulement': [], 'absentes': []}
    paras = [p for p in doc.get('paragraphes') or [] if isinstance(p, dict)]
    headings = []   # (clé, index du paragraphe)
    for i, p in enumerate(paras):
        style = fold(str(p.get('style') or '')).lower().replace(' ', '')
        if style in HEADING_STYLES:
            headings.append((_title_key(p.get('texte')), i))
    bodies = {}
    for n, (key, i) in enumerate(headings):
        end = headings[n + 1][1] if n + 1 < len(headings) else len(paras)
        body = ' '.join(str(p.get('texte') or '') for p in paras[i + 1:end])
        body = re.sub(r'\[[^\]]*\]', ' ', body)
        bodies[i] = len(re.sub(r'\s+', ' ', body).strip())
    texts = ' '.join(_title_key(p.get('texte')) for p in paras)
    report = {'statut': 'complet', 'attendues': [{'id': 's%d' % (k + 1), 'titre': t} for k, t in enumerate(expected)],
              'presentes': [], 'vides': [], 'citees_seulement': [], 'absentes': []}
    used = set()
    for title in expected:
        key = _title_key(title)
        found = next((i for k, i in headings if i not in used and _same_title(key, k)), None)
        if found is not None:
            used.add(found)
            report['presentes' if bodies.get(found, 0) >= MIN_BODY else 'vides'].append(title)
        elif key and key in texts:
            report['citees_seulement'].append(title)
        else:
            report['absentes'].append(title)
    if report['vides'] or report['citees_seulement'] or report['absentes']:
        report['statut'] = 'ecarts'
    return report


def coverage(desk, request, doc):
    """B05 : sections de la trame à reprendre dans le projet rédigé (absentes, seulement citées ou sans corps) — 5.6.42 (F06) : contrôle
    structurel (``coverage_report``)."""
    report = coverage_report(desk, request, doc)
    return report['absentes'] + report['citees_seulement'] + report['vides']


CONSIGNE = ('Utilise cette trame comme base : suis sa structure et ses articles, adapte-la au dossier et à la demande, complète les mentions '
            '« à compléter » à partir des pièces et laisse-les apparentes si l’information manque ; ne supprime pas une clause sans le signaler '
            'dans a_completer ; signale toute référence légale à vérifier. Chaque section de « sections_attendues » doit figurer dans le projet.')


# ------------------------------------------------------------------------------------------------ écran
def _resume(t, lang):
    r = t.get('resume') or {}
    return r.get(lang) or r.get('fr') or ''


def _url(prefix, cid, **q):
    base = prefix + '/corporate?vue=bibliotheque'
    if cid:
        base += '&societe=' + cid
    return base + ''.join('&%s=%s' % (k, v) for k, v in q.items() if v)


def page_html(desk, prefix, args, cid=''):
    lang = _lang()
    law = default_law(desk)
    droit = str(args.get('droit') or law)
    cat = str(args.get('categorie') or '')
    ident = str(args.get('modele') or '')
    if ident:
        return detail_html(desk, prefix, args, ident, cid)
    items = [t for t in catalog(desk) if (droit == 'tous' or t['droit'] == droit) and (not cat or t['categorie'] == cat)]
    company_form = ''
    if cid:
        from . import corporate5636 as co
        company_form = co.company(desk, cid)['forme']
    filters = ('<form class="b5639-filters" method="get" action="%s/corporate"><input type="hidden" name="vue" value="bibliotheque">%s'
               '<label>Droit<select name="droit">%s</select></label><label>Catégorie<select name="categorie"><option value="">Toutes</option>%s</select>'
               '</label><button class="ax-btn ghost" type="submit">Afficher</button></form>') % (
        e(prefix, quote=True), ('<input type="hidden" name="societe" value="%s">' % e(cid, quote=True)) if cid else '',
        ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == droit else '', e(v)) for k, v in list(DROITS.items()) + [('tous', 'Tous les droits')]),
        ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == cat else '', e(v)) for k, v in CATEGORIES.items()))
    law_form = ('<details class="b5640-law"><summary>Droit applicable par défaut du cabinet : <strong>%s</strong></summary><p class="ax-muted">Le droit '
                'proposé ne dépend pas de la langue de l’interface : un cabinet français dont l’interface est en anglais garde le droit français. '
                'Chaque document affiche son droit et sa langue.</p><form class="m5-form m5-inline" data-api="m5636/corporate/bibliotheque/droit" '
                'data-reload="1"><label>Droit par défaut<select name="droit">%s</select></label><button class="ax-btn ghost" type="submit">Enregistrer'
                '</button></form></details>') % (e(DROITS[law]), ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == law else '', e(v))
                                                                             for k, v in DROITS.items()))
    groups = []
    for key, label in CATEGORIES.items():
        rows = [t for t in items if t['categorie'] == key]
        if not rows:
            continue
        rows.sort(key=lambda t: (company_form not in t.get('formes', ()), t['origine'] != 'cabinet', t['titre']))
        groups.append('<h3>%s</h3><ul class="b5639-list">%s</ul>' % (e(label), ''.join(
            '<li><a class="ax-card b5639-item" href="%s"><strong>%s</strong>%s<small>%s</small></a></li>' % (
                e(_url(prefix, cid, modele=t['id']), quote=True), e(t['titre']), _badges(t, company_form),
                e(_resume(t, lang) or DROITS.get(t['droit'], ''))) for t in rows)))
    intro = ('<section class="ax-card b5639"><h2>Bibliothèque de modèles</h2><p class="ax-muted">Statuts, pactes, lettres d’intention, protocoles, '
             'cessions et contrats, en droit français, allemand, espagnol et anglais. Choisissez un modèle, cochez les clauses utiles, complétez '
             'les variables (pré-remplies depuis la société choisie) puis déposez le projet dans le dossier, téléchargez-le en Word ou confiez la '
             'rédaction à l’IA, qui suivra la trame. Vous pouvez personnaliser chaque modèle, le remplacer par votre modèle Word ou ajouter les '
             'vôtres.</p>%s%s</section>') % (filters, law_form)
    add_form = ('<section class="ax-card"><details><summary>＋ Ajouter mon modèle</summary><form class="m5-form" data-api="m5636/corporate/bibliotheque/'
                'ajouter" data-reload="1"><label>Titre<input name="titre" required maxlength="160"></label><label>Catégorie<select name="categorie">%s'
                '</select></label><label>Droit<select name="droit">%s</select></label><label>Texte du modèle (facultatif si vous importez un modèle '
                'Word)<textarea name="texte" rows="10" data-no-dictation placeholder="## Objet&#10;La société {{denomination}}…&#10;[[si '
                'non_concurrence]]&#10;## Non-concurrence&#10;…&#10;[[fin]]"></textarea></label><label>Modèle Word (facultatif)%s</label>%s'
                '<button class="ax-btn" type="submit">Ajouter le modèle</button></form></details></section>') % (
        ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in CATEGORIES.items()),
        ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == law else '', e(v)) for k, v in DROITS.items()),
        _word_input(), _syntax_help())
    return intro + (''.join(groups) or '<p class="ax-muted">Aucun modèle pour ce filtre.</p>') + add_form


def _badges(t, company_form=''):
    out = []
    if company_form and company_form in t.get('formes', ()):
        out.append('<span class="b5639-badge on">%s</span>' % e(company_form))
    if t['droit'] != 'FR':
        out.append('<span class="b5639-badge">%s</span>' % e(t['droit']))
    if t['origine'] == 'cabinet':
        out.append('<span class="b5639-badge cab">Modèle du cabinet</span>')
    elif t['personnalise']:
        out.append('<span class="b5639-badge cab">Personnalisé</span>')
    if t.get('word'):
        out.append('<span class="b5639-badge cab">Word du cabinet</span>')
    return ' ' + ''.join(out) if out else ''


def _word_input(value=''):
    from .corporate5636 import _template_input
    return _template_input(value, '/Modèles/Mon modèle.docx')


def _syntax_help():
    return ('<details class="b5639-help"><summary>Syntaxe des modèles</summary><ul><li><code>{{variable}}</code> : donnée remplacée à la '
            'préparation (dénomination, siège, capital…).</li><li><code>[[si clause]]</code> … <code>[[sinon]]</code> … <code>[[fin]]</code>, sur '
            'des lignes seules : clause optionnelle proposée en case à cocher ; aussi possible dans une ligne.</li><li><code>## Intitulé</code> : '
            'article numéroté automatiquement ; <code># Titre</code> : titre en gras ; <code>- texte</code> : élément de liste.</li><li>Dans un '
            'modèle Word, mêmes balises ; les paragraphes <code>[[si …]]</code> / <code>[[fin]]</code> sont retirés du document préparé.</li>'
            '</ul></details>')


def _ready_html(desk, args, owner=None):
    """B06 : document prêt, avec son expiration ; lien expiré : explication et nouvelle préparation."""
    jeton = str(args.get('fichier') or '')
    if not re.fullmatch(r'[0-9a-f]{32}', jeton):
        return ''
    row = _file_row(desk, jeton, owner)
    expires = (_parse_time(row['expires_at']) or _parse_time(_expiry(row['created']))) if row else None
    if row is None or expires is None or expires <= datetime.now(timezone.utc):
        return ('<p class="b5639-warn" role="status">Ce document n’est plus disponible (lien expiré) : vérifiez les informations ci-dessous puis '
                'cliquez de nouveau « Préparer ».</p>')
    return ('<p class="b5639-ready" role="status">Document prêt jusqu’au %s (UTC) : <button type="button" class="ax-btn m5-export" '
            'data-api="m5636/corporate/bibliotheque/fichier?jeton=%s">⬇ Télécharger le document</button></p>') % (
        e(expires.strftime('%d/%m/%Y %H:%M')), e(jeton, quote=True))


def detail_html(desk, prefix, args, ident, cid=''):
    lang = _lang()
    t = template(desk, ident)
    data = prefill(desk, cid) if cid else {}
    values = values_for(t, data)
    flags = flags_for(t, {})
    warn = ''
    if t['droit'] != 'FR':
        warn = ('<p class="b5639-warn" role="note">Modèle de %s : trame de travail à faire valider par un conseil local avant tout usage ; '
                'AxiorHub ne donne pas d’avis sur un droit étranger.</p>') % e(DROITS[t['droit']].replace('Droit ', 'droit '))
    fields = []
    for key, label, default in t['variables']:
        v = values.get(key) or ''
        if key in MULTILINE or len(v) > 90:
            fields.append('<label>%s<textarea name="%s" rows="3" data-no-dictation>%s</textarea></label>' % (e(label), e(key, quote=True), e(v)))
        else:
            fields.append('<label>%s<input name="%s" value="%s"></label>' % (e(label), e(key, quote=True), e(v, quote=True)))
    clauses = ''.join('<label class="e5636-switch" title="%s"><input type="checkbox" name="clause_%s"%s> %s</label>' % (
        e(help_, quote=True), e(k, quote=True), ' checked' if flags.get(k) else '', e(label)) for k, label, _, help_ in t['clauses'])
    try:
        matters = load_matters(desk.c)
    except Stop:
        matters = []
    own_matter = ''
    if cid:
        from . import corporate5636 as co
        own_matter = co.company(desk, cid)['matter']
    matter_select = '' if own_matter else ('<label>Dossier (dépôt et rédaction IA)<select name="matter"><option value="">— choisir —</option>%s</select>'
                                           '</label>' % ''.join('<option value="%s">%s</option>' % (e(m['id'], quote=True), e(matter_display(m)))
                                                                for m in matters))
    owner = None   # affichage : jeton imprévisible ; le téléchargement lui-même vérifie le propriétaire
    source = ('modèle Word du cabinet' if t.get('word') else 'trame personnalisée par le cabinet' if t['personnalise'] or t['origine'] == 'cabinet'
              else 'trame d’origine AxiorHub')
    identity = ('<p class="b5640-id"><span>Droit applicable : <strong>%s</strong></span> · <span>Langue du document : <strong>%s</strong></span> · '
                '<span>Source : %s, version %s</span></p>') % (e(DROITS.get(t['droit'], t['droit'])), e(LANGUES.get(t['langue'], t['langue'])),
                                                               e(source), e(str(t.get('version', 1))))
    identity += _word_state_html(t) + _progress_html(t, values)
    form = ('<section class="ax-card"><h3>Préparer le document</h3>%s%s<form class="m5-form b5639-form b5640-steps" data-api="m5636/corporate/'
            'bibliotheque/generer" data-reload="1" data-result="#b5640-apercu"><input type="hidden" name="modele" value="%s"><input type="hidden" '
            'name="societe" value="%s"><input type="hidden" name="version_vue" value="%s">%s'
            '<fieldset><legend>1. Destination</legend><label class="e5636-switch"><input type="radio" name="destination" value="telecharger" checked> '
            'Télécharger en Word</label><label class="e5636-switch"><input type="radio" name="destination" value="dossier"> Déposer dans le dossier '
            '(JURIDIQUE)</label><label class="e5636-switch"><input type="radio" name="destination" value="ia"> Faire rédiger par l’IA à partir de '
            'cette trame et des pièces du dossier</label>%s<label>Consignes pour l’IA (facultatif)<textarea name="consignes" rows="2"></textarea>'
            '</label></fieldset>'
            '<fieldset class="b5639-vars"><legend>2. Informations</legend>%s</fieldset>'
            '<fieldset><legend>3. Clauses</legend>%s</fieldset>'
            '<fieldset><legend>4. Vérifier puis préparer</legend><p class="ax-muted">L’aperçu est calculé par le même moteur que le document final '
            '(clauses retenues, variables saisies, modèle Word du cabinet le cas échéant) ; les mentions à compléter sont signalées.</p>'
            '<textarea id="b5640-apercu" class="b5639-preview" rows="14" readonly aria-label="Aperçu du document"></textarea>'
            '<div class="ax-actions"><button class="ax-btn ghost" type="submit" data-extra="destination=apercu">Aperçu du document</button>'
            '<button class="ax-btn" type="submit">Préparer</button></div></fieldset></form></section>') % (
        identity, _ready_html(desk, args, owner), e(t['id'], quote=True), e(cid, quote=True), e(view_token(t), quote=True), warn,
        matter_select, ''.join(fields),
        clauses or '<p class="ax-muted">Aucune clause optionnelle.</p>')
    custom = ('<details class="ax-card b5640-advanced"><summary><strong>Personnalisation avancée du modèle</strong> (texte, champs, modèle Word)'
              '</summary><p class="ax-muted">Le texte ci-dessous est la trame utilisée par le cabinet : modifiez-le, ajoutez vos {{variables}} et vos '
              'clauses [[si …]] ; elles seront proposées à la préparation. Une clause mal fermée est refusée avec la ligne en cause.</p>'
              '<form class="m5-form" data-api="m5636/corporate/bibliotheque/personnaliser" data-reload="1"><input type="hidden" name="modele" '
              'value="%s"><textarea name="texte" rows="18" data-no-dictation class="b5639-src">%s</textarea><button class="ax-btn" type="submit">'
              'Enregistrer le modèle du cabinet</button></form>%s%s%s'
              '<h4>Modèle Word</h4><p class="ax-muted">Téléchargez la trame en Word, adaptez-la (texte, mise en forme, balises), puis importez-la : '
              'elle sera utilisée à la place du texte, pour le téléchargement, le dépôt et la rédaction IA ; ses champs et clauses sont relevés.</p>'
              '<p><button type="button" class="ax-btn ghost m5-export" data-api="m5636/corporate/bibliotheque/word?modele=%s">⬇ Télécharger la trame '
              'en Word</button></p><form class="m5-form" data-api="m5636/corporate/bibliotheque/word" data-reload="1"><input type="hidden" '
              'name="modele" value="%s"><label>Mon modèle Word%s</label><button class="ax-btn ghost" type="submit">Enregistrer le modèle Word</button>'
              '</form>%s</details>') % (
        e(t['id'], quote=True), e('\n'.join(t['texte'])), _syntax_help(), _labels_html(t),
        ('<form class="m5-form m5-inline" data-api="m5636/corporate/bibliotheque/retablir" data-reload="1" data-confirm="Revenir au modèle '
         'd’origine ? Votre texte et votre modèle Word seront retirés."><input type="hidden" name="modele" value="%s"><button class="ax-btn ghost" '
         'type="submit">Revenir au modèle d’origine</button></form>' % e(t['id'], quote=True)) if (t['personnalise'] or t.get('word')) and
        t['origine'] == 'axiorhub' else '',
        e(t['id'], quote=True), e(t['id'], quote=True), _word_input(t.get('word', '')),
        ('<form class="m5-form m5-inline" data-api="m5636/corporate/bibliotheque/retirer" data-reload="1" data-confirm="Retirer ce modèle de la '
         'bibliothèque ?"><input type="hidden" name="modele" value="%s"><button class="ax-btn ghost" type="submit">Retirer ce modèle</button></form>'
         % e(t['id'], quote=True)) if t['origine'] == 'cabinet' else '')
    head = ('<p><a href="%s">← Bibliothèque de modèles</a></p><section class="ax-card"><h2>%s %s</h2><p class="ax-muted"><span>%s</span> — <span>%s</span></p>'
            '<p class="ax-muted">Projet à relire : vérifiez les références légales à la date de l’acte et adaptez les clauses au dossier.</p>'
            '</section>') % (e(_url(prefix, cid, droit=t['droit']), quote=True), e(t['titre']), _badges(t), e(DROITS.get(t['droit'], '')),
                             e(_resume(t, lang)))
    return head + form + custom


def _word_state_html(t):
    """5.6.42 (audit 5.6.41, ergonomie, F02/F03) : version et empreinte du Word, état de synchronisation, champs nouveaux et disparus,
    action « Actualiser le modèle »."""
    if not t.get('word'):
        return ''
    meta = t.get('meta') or {}
    state = meta.get('hydratation') or {}
    if state.get('etat') == 'erreur':
        status = ('<span class="c5636-warn">source illisible (%s) : vérifiez le chemin dans Nextcloud ou réimportez le modèle Word ci-dessous'
                  '</span>' % e(state.get('cause', '')))
    else:
        status = 'synchronisé le %s' % e(str(state.get('date') or '')[:16].replace('T', ' ')) if state.get('date') else 'à actualiser'
    news = meta.get('nouvelles') or []
    gone = meta.get('disparues') or []
    label = lambda k: '[[si %s]]' % k[7:] if k.startswith('clause_') else '{{%s}}' % k   # noqa: E731
    changes = ''
    if news:
        changes += '<p class="c5636-warn">Nouveaux depuis la version précédente : %s — vérifiez-les dans le formulaire.</p>' % e(', '.join(map(label, news)))
    if gone:
        changes += '<p class="c5636-warn">Disparus depuis la version précédente : %s.</p>' % e(', '.join(map(label, gone)))
    return ('<div class="b5642-word"><p><span>Modèle Word : <code>%s</code></span> · <span>empreinte <code>%s</code></span> · <span>%s</span></p>%s'
            '<form class="m5-form m5-inline" data-api="m5636/corporate/bibliotheque/actualiser" data-reload="1"><input type="hidden" name="modele" '
            'value="%s"><button class="ax-btn ghost" type="submit">Actualiser le modèle</button></form></div>') % (
        e(t['word']), e((meta.get('empreinte') or '—')[:12]), status, changes, e(t['id'], quote=True))


def _progress_html(t, values):
    """5.6.42 (ergonomie) : informations déjà renseignées et mentions à compléter, avant les quatre étapes."""
    total = len(t['variables'])
    if not total:
        return ''
    head = MISSING.get(t['langue'] if t['langue'] in LANGS else 'fr', MISSING['fr']).split('%s')[0]
    filled = sum(1 for k, _, _ in t['variables'] if (values.get(k) or '').strip() and not str(values.get(k)).startswith(head))
    return ('<p class="b5642-progress" role="status"><progress max="%d" value="%d"></progress> %d information(s) sur %d déjà renseignée(s)%s</p>'
            % (total, filled, filled, total, ' — complétez les autres à l’étape 2' if filled < total else ''))


def _labels_html(t):
    """B03 : champs ajoutés par le cabinet (texte ou Word) — libellé et valeur par défaut modifiables ; fautes de frappe et champs disparus
    signalés."""
    standard = {k for k, _, _ in BUILTIN[t['id']]['variables']} if t['id'] in BUILTIN else set()
    extra = [(k, lab, d) for k, lab, d in t['variables'] if k not in standard]
    extra_c = [(k, lab) for k, lab, _, _ in t['clauses'] if t['id'] not in BUILTIN or k not in {c[0] for c in BUILTIN[t['id']]['clauses']}]
    if not extra and not extra_c and not t['meta'].get('disparues'):
        return ''
    suspects = t['meta'].get('suspectes') or {}
    rows = ''.join('<tr><td><code>{{%s}}</code>%s</td><td><input name="libelle_%s" value="%s" aria-label="Libellé"></td><td><input name="defaut_%s" '
                   'value="%s" aria-label="Valeur par défaut"></td></tr>' % (
                       e(k), (' <small class="c5636-warn">faute de frappe ? {{%s}}</small>' % e(suspects[k])) if k in suspects else '',
                       e(k, quote=True), e(lab, quote=True), e(k, quote=True), e(d, quote=True)) for k, lab, d in extra)
    rows += ''.join('<tr><td><code>[[si %s]]</code></td><td><input name="libelle_clause_%s" value="%s" aria-label="Libellé"></td><td></td></tr>' % (
        e(k), e(k, quote=True), e(lab, quote=True)) for k, lab in extra_c)
    gone = t['meta'].get('disparues') or []
    return ('<h4>Champs du modèle du cabinet</h4><form class="m5-form" data-api="m5636/corporate/bibliotheque/libelles" data-reload="1">'
            '<input type="hidden" name="modele" value="%s"><div class="o5636-scroll"><table class="o5636-grid"><thead><tr><th>Champ</th>'
            '<th>Libellé dans le formulaire</th><th>Valeur par défaut</th></tr></thead><tbody>%s</tbody></table></div>%s'
            '<button class="ax-btn ghost" type="submit">Enregistrer les libellés</button></form>') % (
        e(t['id'], quote=True), rows, ('<p class="c5636-warn">Champs disparus depuis le modèle précédent : %s</p>'
                                       % e(', '.join('{{%s}}' % x for x in gone))) if gone else '')


# ------------------------------------------------------------------------------------------------ API
def api_post(desk, route, data, owner):
    if route == 'corporate/bibliotheque/generer':
        return generate(desk, data, owner)
    if route == 'corporate/bibliotheque/personnaliser':
        return customize(desk, data, owner)
    if route == 'corporate/bibliotheque/retablir':
        return restore(desk, data, owner)
    if route == 'corporate/bibliotheque/word':
        return set_word(desk, data, owner)
    if route == 'corporate/bibliotheque/libelles':
        return set_labels(desk, data, owner)
    if route == 'corporate/bibliotheque/actualiser':
        return refresh(desk, data, owner)
    if route == 'corporate/bibliotheque/droit':
        return set_default_law(desk, data, owner)
    if route == 'corporate/bibliotheque/ajouter':
        return add(desk, data, owner)
    if route == 'corporate/bibliotheque/retirer':
        return remove(desk, data, owner)
    return None


def api_get(desk, route, args, owner='cabinet'):
    if route == 'corporate/bibliotheque/fichier':
        return download(desk, args.get('jeton'), owner)
    if route == 'corporate/bibliotheque/word':
        return word_template(desk, args.get('modele'))
    if route == 'corporate/bibliotheque':
        lang = _lang()
        return {'modeles': [{'id': t['id'], 'titre': t['titre'], 'droit': t['droit'], 'categorie': t['categorie'], 'resume': _resume(t, lang),
                             'personnalise': t['personnalise'], 'origine': t['origine']} for t in catalog(desk)]}
    return None
