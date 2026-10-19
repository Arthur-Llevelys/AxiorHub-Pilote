"""5.6.59 (audit 5.6.55, A07) : bibliothèque de savoir-faire du cabinet, réellement réutilisable par l'agent.

Une ressource est une méthode ou un texte de référence approuvé pour un usage défini : structure de courrier, liste de vérification,
clause approuvée, méthode de relecture, trame de rédaction. Elle porte : type, domaine, juridiction ou territoire, type d'acte, objectif,
limites, variables obligatoires, mots-clés, auteur, version et date de revue.

- **Versionnée** : chaque modification crée une nouvelle version, à approuver ; la version approuvée précédente reste celle qui sert tant
  que la nouvelle n'est pas approuvée. Le retrait d'une ressource cesse immédiatement son usage ; l'historique est conservé.
- **Anonymisée** si elle vient d'un dossier : noms des parties et de leurs alias, client, adresses électroniques, téléphones, IBAN,
  dates, montants et numéros de rôle sont remplacés avant l'enregistrement ; les références juridiques (articles) sont conservées. Le
  texte d'origine n'est pas conservé.
- **Approuvée avant réutilisation** par un compte qui a le droit « valider » ; un texte qui contient des instructions adressées à une IA
  ne peut pas être approuvé (``bouclier5659``).
- **Sélectionnée pour la tâche** : au plus deux ressources par travail, approuvées, non retirées, dont la date de revue n'est pas
  dépassée, adaptées à la phase (courriel, document, contrôle) et rapprochées de la vraie consigne par leurs mots-clés. Elles sont
  transmises au modèle comme une compétence du cabinet : elles décrivent une méthode, n'accordent aucun droit, n'élargissent pas le
  dossier et ne lèvent aucun contrôle. L'usage (ressource, version, travail, raison) est journalisé.
- Une modification de style (« Mon style »), une règle métier (apprentissage) et un fait propre au dossier restent des catégories
  distinctes : la bibliothèque ne reçoit que des méthodes et des textes de référence.
"""
from datetime import date, datetime, timezone
import json
import re
import secrets

from .common import Stop, fold

SCHEMA = '''
CREATE TABLE IF NOT EXISTS s59_resources(id TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'active', created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS s59_versions(resource TEXT NOT NULL, version INTEGER NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
  domain TEXT NOT NULL DEFAULT '', jurisdiction TEXT NOT NULL DEFAULT '', act_type TEXT NOT NULL DEFAULT '', objective TEXT NOT NULL DEFAULT '',
  limits TEXT NOT NULL DEFAULT '', variables TEXT NOT NULL DEFAULT '[]', keywords TEXT NOT NULL DEFAULT '', body TEXT NOT NULL,
  origin TEXT NOT NULL DEFAULT 'creation', origin_matter TEXT NOT NULL DEFAULT '', anonymized INTEGER NOT NULL DEFAULT 0,
  review_due TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'proposee', author TEXT NOT NULL, approved_by TEXT NOT NULL DEFAULT '',
  approved_at TEXT NOT NULL DEFAULT '', motif TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, PRIMARY KEY(resource, version));
CREATE INDEX IF NOT EXISTS s59_versions_status ON s59_versions(status, resource);
CREATE TABLE IF NOT EXISTS s59_usage(id INTEGER PRIMARY KEY AUTOINCREMENT, resource TEXT NOT NULL, version INTEGER NOT NULL, job_id TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
'''
KINDS = {'structure_courrier': 'Structure de courrier', 'liste_verification': 'Liste de vérification', 'clause': 'Clause approuvée',
         'methode_relecture': 'Méthode de relecture', 'trame': 'Trame de rédaction'}
PHASES = {'structure_courrier': ('redaction_courriel',), 'liste_verification': ('controle', 'redaction_document', 'redaction_courriel'),
          'clause': ('redaction_document',), 'methode_relecture': ('controle', 'redaction_document'), 'trame': ('redaction_document',)}
STATUS = {'proposee': 'À approuver', 'approuvee': 'Approuvée', 'remplacee': 'Remplacée par une version plus récente', 'refusee': 'Refusée'}
ORIGINS = {'creation': 'Créée dans la bibliothèque', 'dossier': 'Tirée d’une rédaction corrigée (anonymisée)'}
MAX_SELECTED = 2
BUDGET = 6000
STOP = {'pour', 'avec', 'dans', 'une', 'des', 'les', 'sur', 'par', 'que', 'qui', 'est', 'aux', 'ces', 'son', 'ses', 'leur', 'cette', 'faire',
        'prepare', 'preparer', 'redige', 'rediger', 'projet', 'document', 'courrier', 'dossier', 'client', 'merci', 'tout', 'plus', 'sans'}


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    _recheck_5665(desk)


# 5.6.65 (audit 5.6.64, R07) : champs de prose réutilisés ailleurs (transmis au modèle avec la compétence) ; tous anonymisés.
PROSE = ('title', 'body', 'domain', 'jurisdiction', 'act_type', 'objective', 'limits', 'keywords')
RECHECK = 'savoir5665:recontrole_anonymisation'


def _recheck_5665(desk):
    """Une fois : les versions tirées d'un dossier sont réanonymisées sur tous leurs champs (avant la 5.6.65, seuls le titre et le texte
    l'étaient, et une nouvelle version sans « matter » n'était pas traitée). Une version approuvée dont le contenu change repasse « à
    approuver » : son usage automatique est suspendu jusqu'à la revue, le texte n'est pas perdu."""
    try:
        if desk.settings(RECHECK, False):
            return
    except Exception:   # noqa: BLE001 — base sans table de réglages (outil isolé)
        return
    rows = desk.db.execute("SELECT * FROM s59_versions WHERE origin_matter<>''").fetchall()
    for v in rows:
        changed = {}
        for f in PROSE:
            value, n = anonymize(desk, v['origin_matter'], v[f])
            if n and value != v[f]:
                changed[f] = value
        if not changed and v['anonymized']:
            continue
        changed['anonymized'] = 1
        if v['status'] == 'approuvee' and len(changed) > 1:
            changed['status'] = 'proposee'
            changed['motif'] = 'Réanonymisée par la 5.6.65 (objectif, limites ou mots-clés nominatifs) : à revoir avant nouvel usage.'
        desk.db.execute('UPDATE s59_versions SET %s WHERE resource=? AND version=?' % ','.join('%s=?' % k for k in changed),
                        (*changed.values(), v['resource'], v['version']))
    desk.setting(RECHECK, True)


def _words(text):
    return {w for w in re.findall(r'[a-z0-9]{4,}', fold(text or '')) if w not in STOP}


# ------------------------------------------------------------------------------------------------ anonymisation
def anonymize(desk, mid, text):
    """Texte d'un dossier rendu réutilisable : parties et alias, client, courriels, téléphones, IBAN, dates, montants, RG. Les numéros
    d'articles et de textes restent (« article 1231-5 du code civil »)."""
    out = str(text or '')
    names = set()
    try:
        from . import modele5651
        for p in modele5651.parties(desk, mid, include_merged=True):
            names.add(p['display_name'])
            names.update(a['alias'] for a in p['aliases'])
            if p.get('counsel'):
                names.add(p['counsel'])
    except Exception:   # noqa: BLE001
        pass
    try:
        from .common import load_matters
        m = next((x for x in load_matters(desk.c) if str(x['id']) == str(mid)), None)
        if m:
            names.update(x for x in (m.get('client_name'), m.get('name'), m.get('title')) if x)
            for c in m.get('correspondents') or []:
                if c.get('name'):
                    names.add(str(c['name']))
    except Exception:   # noqa: BLE001
        pass
    count = 0
    # identifiants structurés d'abord (un nom remplacé à l'intérieur d'une adresse la rendrait méconnaissable), puis les noms
    rules = ((r'[\w.+-]+@[\w-]+\.[\w.-]+', '[COURRIEL]'), (r'\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,4})?\b', '[IBAN]'),
             (r'(?:\+33\s?|\b0)[1-9](?:[\s.-]?\d{2}){4}\b', '[TÉLÉPHONE]'),
             (r'\b(?:RG|R\.G\.|n°\s*RG)\s*:?\s*\d{2}\s*/\s*\d{3,6}\b|\b\d{2}/\d{5}\b', '[RG]'),
             (r'\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}(?:er)?\s+(?:janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|'
              r'septembre|octobre|novembre|d[ée]cembre)\s+\d{4}\b', '[DATE]'),
             (r'\b\d[\d\s.  ]*(?:,\d+)?\s?(?:€|\beuros?\b|\bEUR\b)', '[MONTANT]'),
             (r'\b(?:SIREN|SIRET|RCS)\b[^\n,;]{0,30}\d[\d\s]{6,}\d', '[IMMATRICULATION]'))
    for rx, repl in rules:
        out, k = re.subn(rx, repl, out, flags=re.I)
        count += k
    for n in sorted((x for x in names if x and len(str(x)) >= 3), key=len, reverse=True):
        out, k = re.subn(re.escape(str(n)), '[PARTIE]', out, flags=re.I)
        count += k
        for token in re.findall(r'[A-ZÀ-Ý][\w’\'-]{3,}', str(n)):
            if fold(token) not in ('madame', 'monsieur', 'societe', 'maitre', 'cabinet', 'avocat', 'avocats'):
                out, k = re.subn(r'\b%s\b' % re.escape(token), '[PARTIE]', out, flags=re.I)
                count += k
    return out, count


# ------------------------------------------------------------------------------------------------ registre
def _clean_fields(data):
    kind = str(data.get('kind') or data.get('type') or '')
    if kind not in KINDS:
        raise Stop('type_de_savoir_faire_invalide')
    title = str(data.get('title') or data.get('titre') or '').strip()[:200]
    body = str(data.get('body') or data.get('texte') or '').strip()
    if not title or not body:
        raise Stop('titre_et_texte_obligatoires')
    if len(body) > 20000:
        raise Stop('savoir_faire_trop_long')
    variables = data.get('variables') or ''
    if isinstance(variables, str):
        variables = [v.strip() for v in re.split(r'[,;\n]', variables) if v.strip()]
    variables = [re.sub(r'[^\w-]', '_', str(v))[:60] for v in variables][:30]
    review = str(data.get('review_due') or data.get('revue') or '').strip()[:10]
    if review and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', review):
        raise Stop('date_de_revue_invalide')
    return {'kind': kind, 'title': title, 'body': body, 'variables': variables, 'review_due': review,
            **{k: str(data.get(k) or data.get(alt) or '').strip()[:limit] for k, alt, limit in (
                ('domain', 'domaine', 120), ('jurisdiction', 'juridiction', 120), ('act_type', 'type_acte', 120), ('objective', 'objectif', 1000),
                ('limits', 'limites', 2000), ('keywords', 'mots_cles', 400))}}


def check(fields):
    """Contrôles de cohérence : variables déclarées et utilisées, texte sans instruction adressée à une IA."""
    used = set(re.findall(r'\{\{\s*([\w-]+)\s*\}\}', fields['body']))
    declared = set(fields['variables'])
    from .bouclier5659 import inspect
    verdict = inspect(fields['body'] + '\n' + fields.get('objective', '') + '\n' + fields.get('limits', ''))
    return {'non_declarees': sorted(used - declared), 'non_utilisees': sorted(declared - used),
            'securite': [f['label'] for f in verdict['findings'] if f['level'] != 'info'], 'niveau': verdict['level']}


def propose(desk, author, data, matter=''):
    """Nouvelle ressource (ou nouvelle version si ``data['resource']``), toujours « à approuver »."""
    ensure_schema(desk)
    fields = _clean_fields(data)
    origin, anonymized = 'creation', 0
    rid = str(data.get('resource') or '')
    now = _now()
    if rid:   # 5.6.65 (R07b) : origine de la révision résolue AVANT le traitement du contenu (le formulaire n'envoie pas « matter »)
        row = desk.db.execute('SELECT state FROM s59_resources WHERE id=?', (rid,)).fetchone()
        if not row:
            raise Stop('savoir_faire_absent')
        prev = desk.db.execute('SELECT origin,origin_matter FROM s59_versions WHERE resource=? ORDER BY version DESC LIMIT 1', (rid,)).fetchone()
        if prev and prev['origin_matter']:
            if matter and str(matter) != prev['origin_matter']:
                raise Stop('savoir_faire_autre_dossier')
            matter = prev['origin_matter']
    if matter:   # 5.6.65 (R07) : chaque champ réutilisable est anonymisé, pas seulement le titre et le texte
        for f in PROSE:
            fields[f] = anonymize(desk, matter, fields[f])[0]
        origin, anonymized = 'dossier', 1
    if rid:
        version = desk.db.execute('SELECT COALESCE(MAX(version),0)+1 FROM s59_versions WHERE resource=?', (rid,)).fetchone()[0]
        desk.db.execute('UPDATE s59_resources SET updated=? WHERE id=?', (now, rid))
    else:
        rid, version = 's59-' + secrets.token_hex(6), 1
        desk.db.execute('INSERT INTO s59_resources(id,state,created,updated) VALUES(?,?,?,?)', (rid, 'active', now, now))
    desk.db.execute('INSERT INTO s59_versions(resource,version,kind,title,domain,jurisdiction,act_type,objective,limits,variables,keywords,body,origin,'
                    'origin_matter,anonymized,review_due,status,author,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (rid, version, fields['kind'], fields['title'], fields['domain'], fields['jurisdiction'], fields['act_type'], fields['objective'],
                     fields['limits'], json.dumps(fields['variables']), fields['keywords'], fields['body'], origin, str(matter or ''), anonymized,
                     fields['review_due'], 'proposee', str(author)[:80], now))
    desk.db.commit()
    return {'resource': rid, 'version': version, 'controle': check(fields)}


def decide(desk, author, rid, version, decision, motif=''):
    """Approuver ou refuser une version proposée. L'approbation remplace la version approuvée précédente."""
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM s59_versions WHERE resource=? AND version=?', (rid, int(version))).fetchone()
    if not row:
        raise Stop('savoir_faire_absent')
    if row['status'] != 'proposee':
        raise Stop('version_deja_decidee')
    now = _now()
    if decision == 'approuver':
        fields = dict(row)
        fields['variables'] = json.loads(row['variables'] or '[]')
        verdict = check(fields)
        if verdict['niveau'] == 'eleve':
            raise Stop('savoir_faire_contient_des_instructions_pour_une_ia')
        if row['origin'] == 'dossier' and not row['anonymized']:
            raise Stop('savoir_faire_non_anonymise')
        desk.db.execute("UPDATE s59_versions SET status='remplacee' WHERE resource=? AND status='approuvee'", (rid,))
        desk.db.execute("UPDATE s59_versions SET status='approuvee',approved_by=?,approved_at=?,motif=? WHERE resource=? AND version=?",
                        (str(author)[:80], now, str(motif)[:500], rid, int(version)))
    elif decision == 'refuser':
        desk.db.execute("UPDATE s59_versions SET status='refusee',approved_by=?,approved_at=?,motif=? WHERE resource=? AND version=?",
                        (str(author)[:80], now, str(motif)[:500], rid, int(version)))
    else:
        raise Stop('decision_invalide')
    desk.db.execute('UPDATE s59_resources SET updated=? WHERE id=?', (now, rid))
    desk.db.commit()
    return {'ok': True}


def withdraw(desk, author, rid, active=False):
    ensure_schema(desk)
    if not desk.db.execute('SELECT 1 FROM s59_resources WHERE id=?', (rid,)).fetchone():
        raise Stop('savoir_faire_absent')
    desk.db.execute('UPDATE s59_resources SET state=?,updated=? WHERE id=?', ('active' if active else 'retiree', _now(), rid))
    desk.db.commit()
    return {'ok': True}


def _hidden():
    from .membres5650 import viewer_hidden
    return viewer_hidden() or frozenset()


def visible_version(v, hidden=None):
    """5.6.65 (audit 5.6.64, R12) : une version tirée d'un dossier réservé reste dans le périmètre de ce dossier tant qu'elle n'est pas
    approuvée (publiée au cabinet) ; un compte exclu du dossier ne la lit, ne la compte ni ne la modifie."""
    hidden = _hidden() if hidden is None else hidden
    return not (v['origin_matter'] and v['origin_matter'] in hidden and v['status'] != 'approuvee')


def listing(desk):
    """Ressources avec leur version en usage (approuvée), leur dernière version et leur état de revue."""
    ensure_schema(desk)
    today = date.today().isoformat()
    hidden = _hidden()
    out = []
    for r in desk.db.execute('SELECT * FROM s59_resources ORDER BY updated DESC').fetchall():
        versions = [dict(v) for v in desk.db.execute('SELECT * FROM s59_versions WHERE resource=? ORDER BY version DESC', (r['id'],))
                    if visible_version(v, hidden)]
        if not versions:
            continue
        for v in versions:
            v['variables'] = json.loads(v['variables'] or '[]')
        used = next((v for v in versions if v['status'] == 'approuvee'), None)
        latest = versions[0] if versions else None
        expired = bool(used and used['review_due'] and used['review_due'] < today)
        out.append({'id': r['id'], 'state': r['state'], 'used': used, 'latest': latest, 'versions': versions, 'expired': expired,
                    'pending': [v for v in versions if v['status'] == 'proposee'],
                    'usage': desk.db.execute('SELECT COUNT(*) FROM s59_usage WHERE resource=?', (r['id'],)).fetchone()[0]})
    return out


def usable(desk):
    today = date.today().isoformat()
    ensure_schema(desk)
    rows = desk.db.execute("SELECT v.* FROM s59_versions v JOIN s59_resources r ON r.id=v.resource WHERE r.state='active' AND v.status='approuvee' "
                           "AND (v.review_due='' OR v.review_due>=?)", (today,)).fetchall()
    out = []
    for v in rows:
        d = dict(v)
        d['variables'] = json.loads(d['variables'] or '[]')
        out.append(d)
    return out


def select(desk, text, phase, manual=None):
    """Au plus deux ressources pour un travail : choix explicite (``manual``), sinon phase compatible et mots-clés de la consigne."""
    rows = usable(desk)
    if manual:
        wanted = {str(x) for x in manual}
        return [{'id': v['resource'], 'version': v['version'], 'reason': 'Choisie pour ce travail'} for v in rows if v['resource'] in wanted][:MAX_SELECTED]
    words = _words(text)
    if not words:
        return []
    scored = []
    for v in rows:
        if phase and phase not in PHASES.get(v['kind'], ()):
            continue
        keys = _words(' '.join((v['title'], v['domain'], v['act_type'], v['keywords'], v['jurisdiction'])))
        common = words & keys
        if common:
            scored.append((len(common), v['title'], {'id': v['resource'], 'version': v['version'],
                                                      'reason': 'Rapprochée de la consigne : %s' % ', '.join(sorted(common)[:5])}))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [x[2] for x in scored[:MAX_SELECTED]]


def instructions_for(desk, selection, job=None):
    blocks, used = [], 0
    for sel in selection:
        v = desk.db.execute('SELECT * FROM s59_versions WHERE resource=? AND version=?', (sel['id'], int(sel['version']))).fetchone()
        if not v:
            continue
        variables = json.loads(v['variables'] or '[]')
        head = ['Type : %s' % KINDS.get(v['kind'], v['kind'])]
        for label, key in (('Domaine', 'domain'), ('Juridiction ou territoire', 'jurisdiction'), ('Type d’acte', 'act_type'), ('Objectif', 'objective'),
                           ('Limites d’emploi', 'limits')):
            if v[key]:
                head.append('%s : %s' % (label, v[key]))
        if variables:
            head.append('Variables obligatoires : %s. Renseigne-les à partir des seules sources du dossier ; à défaut, écris « [À COMPLÉTER : nom] ». '
                        'Ne reprends jamais une valeur d’un autre dossier.' % ', '.join(variables))
        block = ('COMPÉTENCE « Savoir-faire du cabinet : %s » version %s (%s)\n%s\n---\n%s' % (v['title'], v['version'], sel['reason'], '\n'.join(head), v['body']))
        if used + len(block) > BUDGET:
            blocks.append('[Savoir-faire « %s » non transmis : il dépasse la place disponible dans le contexte.]' % v['title'])
            continue
        blocks.append(block)
        used += len(block)
        if job is not None:
            try:
                desk.db.execute('INSERT INTO s59_usage(resource,version,job_id,kind,reason,at) VALUES(?,?,?,?,?,?)',
                                (v['resource'], v['version'], str(job.get('id') or ''), str(job.get('kind') or ''), sel['reason'][:300], _now()))
            except Exception:   # noqa: BLE001
                pass
    return '\n\n'.join(blocks)


def for_job_text(desk, row, args, text, phase):
    """Texte de savoir-faire à joindre aux compétences d'un travail (appelé par ``competences5632.for_job``)."""
    manual = args.get('savoir_faire') if isinstance(args, dict) else None
    selection = select(desk, text, phase, manual=manual if isinstance(manual, list) else None)
    if not selection:
        return '', []
    out = instructions_for(desk, selection, {'id': row['id'] if row else '', 'kind': row['kind'] if row else ''})
    desk.db.commit()
    return out, selection


def queue_items(desk, prefix='', actor='cabinet', role='administrateur'):
    """File unique : versions de savoir-faire à approuver (pour les comptes qui peuvent approuver)."""
    if role not in ('administrateur', 'avocat'):
        return []
    ensure_schema(desk)
    out = []
    hidden = _hidden()
    for v in desk.db.execute("SELECT v.resource,v.version,v.title,v.author,v.created,v.origin_matter,v.status FROM s59_versions v JOIN s59_resources r "
                             "ON r.id=v.resource WHERE v.status='proposee' AND r.state='active' ORDER BY v.created LIMIT 40"):
        if not visible_version(v, hidden):
            continue
        out.append({'class': 'savoir', 'object': 'savoir5659:%s:%d' % (v['resource'], v['version']), 'revision': str(v['version']),
                    'title': 'Savoir-faire à approuver : %s' % v['title'][:120],
                    'consequence': 'Proposé par %s le %s. L’agent ne l’utilisera qu’après approbation.' % (v['author'], v['created'][:10]),
                    'href': prefix + '/savoir-faire#' + v['resource'], 'choices': ('ouvrir',)})
    return out[:20]


# ------------------------------------------------------------------------------------------------ page
def page_html(desk, prefix, owner, role, can_approve):
    from html import escape as e
    rows = listing(desk)
    kinds = ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in KINDS.items())

    def form(rid='', v=None, matter=False):
        v = v or {}
        val = lambda k: e(str(v.get(k) or ''), quote=True)
        return ('<form class="m5-form s59-form" data-api="m5659/savoir/proposer" data-reload>%s%s'
                '<label>Type<select name="kind">%s</select></label><label>Titre<input name="title" required maxlength="200" value="%s"></label>'
                '<label>Domaine<input name="domain" maxlength="120" value="%s" placeholder="Ex. baux commerciaux"></label>'
                '<label>Juridiction ou territoire<input name="jurisdiction" maxlength="120" value="%s" placeholder="Ex. France"></label>'
                '<label>Type d’acte<input name="act_type" maxlength="120" value="%s" placeholder="Ex. mise en demeure"></label>'
                '<label>Objectif<textarea name="objective" rows="2" maxlength="1000">%s</textarea></label>'
                '<label>Limites d’emploi<textarea name="limits" rows="2" maxlength="2000">%s</textarea></label>'
                '<label>Variables obligatoires (séparées par des virgules)<input name="variables" maxlength="600" value="%s"></label>'
                '<label>Mots-clés pour la sélection<input name="keywords" maxlength="400" value="%s"></label>'
                '<label>Date de revue<input type="date" name="review_due" value="%s"></label>'
                '<label>Texte de la ressource<textarea name="body" rows="8" required maxlength="20000">%s</textarea></label>'
                '<button class="ax-btn" type="submit">%s</button><p role="status" class="m5-status"></p></form>') % (
            ('<input type="hidden" name="resource" value="%s">' % e(rid, quote=True)) if rid else '',
            ('<label>Dossier d’origine (le texte sera anonymisé)<input name="matter" maxlength="200" required placeholder="Référence du dossier"></label>' if matter else ''),
            kinds.replace('value="%s"' % v.get('kind'), 'value="%s" selected' % v.get('kind')) if v.get('kind') else kinds,
            val('title'), val('domain'), val('jurisdiction'), val('act_type'), e(str(v.get('objective') or '')), e(str(v.get('limits') or '')),
            e(', '.join(v.get('variables') or []), quote=True), val('keywords'), val('review_due'), e(str(v.get('body') or '')),
            'Proposer la nouvelle version' if rid else 'Proposer')

    items = []
    for r in rows:
        shown = r['used'] or r['latest']
        if not shown:
            continue
        badges = []
        if r['state'] == 'retiree':
            badges.append('<span class="d51-badge s59-off">Retirée</span>')
        elif r['used']:
            badges.append('<span class="d51-badge s59-ok">En usage · v%d</span>' % r['used']['version'])
        if r['expired']:
            badges.append('<span class="d51-badge s59-warn">Revue dépassée : non utilisée</span>')
        if r['pending']:
            badges.append('<span class="d51-badge s59-warn">%d version(s) à approuver</span>' % len(r['pending']))
        pend = ''
        for p in r['pending']:
            ctl = check(p)
            notes = []
            if ctl['securite']:
                notes.append('<p class="d51-warn" role="alert">Sécurité : %s. Cette version ne peut pas être approuvée.</p>' % e(' ; '.join(ctl['securite'])))
            if ctl['non_declarees']:
                notes.append('<p class="d51-warn">Variables utilisées mais non déclarées : %s</p>' % e(', '.join(ctl['non_declarees'])))
            if ctl['non_utilisees']:
                notes.append('<p class="ax-muted">Variables déclarées mais absentes du texte : %s</p>' % e(', '.join(ctl['non_utilisees'])))
            actions = ''
            if can_approve:
                actions = ''.join('<form class="m5-form d51-inline" data-api="m5659/savoir/decider" data-reload><input type="hidden" name="resource" value="%s">'
                                  '<input type="hidden" name="version" value="%d"><input type="hidden" name="decision" value="%s">%s'
                                  '<button class="ax-btn%s" type="submit">%s</button><p role="status" class="m5-status"></p></form>' % (
                                      e(r['id'], quote=True), p['version'], d, '<input name="motif" maxlength="500" placeholder="Motif (facultatif)">' if d == 'refuser' else '',
                                      '' if d == 'approuver' else ' ghost', 'Approuver' if d == 'approuver' else 'Refuser') for d in ('approuver', 'refuser'))
            pend += ('<div class="s59-pending"><h5>Version %d à approuver — %s, %s%s</h5>%s<pre class="s59-body">%s</pre>%s</div>' % (
                p['version'], e(p['author']), e(p['created'][:10]), ' · anonymisée' if p['anonymized'] else '', ''.join(notes), e(p['body']), actions))
        meta = ' · '.join(e(x) for x in (KINDS.get(shown['kind'], ''), shown['domain'], shown['jurisdiction'], shown['act_type']) if x)
        used = r['used']
        detail = ''
        if used:
            detail = ('<dl class="d51-dl">%s</dl><pre class="s59-body">%s</pre>' % (''.join('<dt>%s</dt><dd>%s</dd>' % (e(k), e(str(v))) for k, v in (
                ('Objectif', used['objective']), ('Limites d’emploi', used['limits']), ('Variables obligatoires', ', '.join(used['variables'])),
                ('Origine', ORIGINS.get(used['origin'], used['origin'])), ('Approuvée par', '%s le %s' % (used['approved_by'], used['approved_at'][:10])),
                ('Date de revue', used['review_due'] or '—'), ('Utilisations', r['usage'])) if v), e(used['body'])))
        manage = ''
        if can_approve:
            manage = ('<form class="m5-form d51-inline" data-api="m5659/savoir/retirer" data-reload><input type="hidden" name="resource" value="%s">'
                      '<input type="hidden" name="actif" value="%s"><button class="ax-btn ghost" type="submit">%s</button></form>') % (
                e(r['id'], quote=True), '1' if r['state'] == 'retiree' else '0', 'Remettre en usage' if r['state'] == 'retiree' else 'Retirer')
        items.append('<li class="s59-item" id="%s"><div class="s59-head"><strong>%s</strong> %s<div class="ax-muted">%s</div></div>'
                     '<details><summary>Voir et modifier</summary>%s<details><summary>Nouvelle version</summary>%s</details>%s</details>%s</li>' % (
                         e(r['id'], quote=True), e(shown['title']), ''.join(badges), meta, detail, form(r['id'], shown), manage, pend))
    body = ('<ul class="s59-list">%s</ul>' % ''.join(items)) if items else '<p class="ax-muted">Aucune ressource pour l’instant.</p>'
    intro = ('<p>Méthodes et textes de référence du cabinet : structures de courrier, listes de vérification, clauses approuvées, méthodes de '
             'relecture, trames. Une ressource n’est utilisée par l’agent qu’une fois <strong>approuvée</strong>, tant que sa <strong>date de revue</strong> '
             'n’est pas dépassée, et seulement pour les travaux auxquels elle correspond (au plus deux par travail). Elle décrit une méthode : '
             'elle n’accorde aucun droit et ne lève aucun contrôle.</p>')
    add = ('<details class="d51-card"><summary>Ajouter une ressource</summary>%s</details>'
           '<details class="d51-card"><summary>À partir d’une rédaction corrigée dans un dossier</summary><p class="ax-muted">Collez le passage corrigé : '
           'les noms des parties, le client, les adresses, téléphones, IBAN, dates, montants et numéros de rôle sont remplacés avant '
           'l’enregistrement ; le texte d’origine n’est pas conservé.</p>%s</details>') % (form(), form(matter=True))
    return '<div class="s59"><h1>Savoir-faire du cabinet</h1>%s%s%s</div>' % (intro, add, body)
