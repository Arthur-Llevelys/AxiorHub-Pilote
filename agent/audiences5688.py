"""5.6.88 : l'audience devient un objet du cabinet, rattaché à son événement d'agenda par un identifiant stable.

- Une audience garde ce qu'un simple événement ne dit pas : juridiction, chambre, type, RG, dossier, avocat qui s'y rend, notes, statut.
- Son horaire, son lieu et son intitulé restent ceux de l'événement (``agenda5634``) : un seul endroit à corriger.
- Migration prudente et répétable : chaque événement de type « audience » reçoit sa fiche, y compris un événement annulé ; rien n'est
  supprimé ni copié ailleurs. La juridiction vient de la fiche du dossier seulement si elle est sûre (saisie par l'avocat ou validée, et
  sans ambiguïté) ; sinon elle reste vide.
- Nextcloud reste l'agenda de référence du cabinet : une audience vue dans un agenda Nextcloud reçoit aussi une fiche, en lecture seule
  pour la date et le lieu. Un faux positif s'écarte d'un clic et ne revient pas.
- Sans avocat choisi, la mention « Sans avocat désigné » apparaît partout où l'audience est citée.
"""
from datetime import date, datetime, timedelta, timezone
import re
import sqlite3
import uuid

from .common import Stop, fold, load_matters

TYPES = {'mise_en_etat': 'Mise en état', 'plaidoiries': 'Plaidoiries', 'refere': 'Référé', 'procedure_orale': 'Procédure orale', 'orientation': 'Orientation',
         'conciliation': 'Conciliation', 'delibere': 'Délibéré', 'autre': 'Autre'}
STATUTS = {'a_venir': 'À venir', 'tenue': 'Tenue', 'renvoyee': 'Renvoyée', 'annulee': 'Annulée', 'ecartee': 'Écartée (pas une audience)'}
SANS_AVOCAT = 'Sans avocat désigné'
SANS = 'sans'   # valeur du filtre « Sans avocat désigné »
AGENT_ACTORS = ('agent', 'ia', 'modele')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS audiences5688(id TEXT PRIMARY KEY, event_id TEXT NOT NULL DEFAULT '', event_uid TEXT NOT NULL DEFAULT '',
  cal_key TEXT NOT NULL DEFAULT '', matter TEXT NOT NULL DEFAULT '', juridiction TEXT NOT NULL DEFAULT '', juridiction_source TEXT NOT NULL DEFAULT '',
  chambre TEXT NOT NULL DEFAULT '', type_audience TEXT NOT NULL DEFAULT 'autre', rg TEXT NOT NULL DEFAULT '', avocat TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '', statut TEXT NOT NULL DEFAULT 'a_venir', origine TEXT NOT NULL DEFAULT 'saisie', propagate INTEGER NOT NULL DEFAULT 0,
  title TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '', starts TEXT NOT NULL DEFAULT '', ends TEXT NOT NULL DEFAULT '',
  all_day INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS audiences5688_event ON audiences5688(event_id) WHERE event_id<>'';
CREATE UNIQUE INDEX IF NOT EXISTS audiences5688_cal ON audiences5688(cal_key) WHERE cal_key<>'';
CREATE INDEX IF NOT EXISTS audiences5688_avocat ON audiences5688(avocat, statut, starts);
'''


def schema(desk):
    from . import agenda5634
    agenda5634.schema(desk)
    desk.db.executescript(SCHEMA)
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ membres et droits
def members(desk, auth=None):
    from .membres5650 import directory
    try:
        return directory(desk, auth)
    except Exception:   # noqa: BLE001 — annuaire indisponible : seuls les comptes déjà connus sont refusés plus loin
        return {}


def member_name(known, owner):
    item = known.get(owner) or {}
    return str(item.get('name') or owner or '')


def role_of(desk, owner, known=None):
    if owner == 'cabinet':
        return 'administrateur'
    return str((known if known is not None else members(desk)).get(owner, {}).get('role') or '')


def can_see(desk, owner, matter, known=None):
    """Hors requête (rappels, liste du matin), l'accès est contrôlé pour le DESTINATAIRE ; registre illisible : refus."""
    if not matter:
        return True
    from .membres5650 import hidden_for
    try:
        return str(matter) not in hidden_for(desk.c['state_dir'], owner, role_of(desk, owner, known))
    except Stop:
        return False


def guard(matter):
    """Contrôle de l'utilisateur de la requête : un objet désigné par son identifiant passe aussi par ici."""
    from .membres5650 import viewer_hidden
    if matter and str(matter) in (viewer_hidden() or frozenset()):
        raise Stop('dossier_reserve')


def check_person(desk, owner, matter, auth=None, code='avocat_inconnu'):
    """Une personne désignée doit être un membre du cabinet et avoir accès au dossier."""
    if not owner:
        return ''
    known = members(desk, auth)
    if owner != 'cabinet' and owner not in known:
        raise Stop(code)
    if not can_see(desk, owner, matter, known):
        raise Stop('personne_sans_acces_au_dossier')
    return owner


# ------------------------------------------------------------------------------------------------ fiche du dossier
def fiche_sure(desk, matter):
    """Juridiction, chambre et RG repris de la fiche seulement s'ils sont sûrs : procédure saisie par l'avocat et sans concurrente, ou
    information validée. Deux procédures actives différentes sans référence : rien n'est deviné."""
    empty = {'juridiction': '', 'chambre': '', 'rg': '', 'source': ''}
    if not matter:
        return empty
    try:
        rows = [dict(r) for r in desk.db.execute("SELECT juridiction,chambre,rg,reference FROM d51_procedures WHERE matter=? AND statut='active' "
                                                 "AND origin='avocat' AND juridiction<>''", (matter,))]
    except sqlite3.OperationalError:
        rows = []
    chosen = [r for r in rows if r['reference']] or rows
    if len({fold(r['juridiction']) for r in chosen}) == 1:
        r = chosen[0]
        return {'juridiction': r['juridiction'], 'chambre': r['chambre'], 'rg': r['rg'], 'source': 'procedure_avocat'}
    if rows:
        return empty
    try:
        from . import dossier5624, dossier5644, fiche460
        best = fiche460._best(dossier5624._records(desk, matter), 'jurisdiction')
        shown = dossier5644.single(desk, matter, 'jurisdiction', dossier5624._fact_view(desk, matter, best) if best else None)
    except Exception:   # noqa: BLE001 — fiche indisponible : la juridiction reste vide, jamais inventée
        return empty
    value = str((shown or {}).get('value') or '').strip()
    if not value or shown.get('status') not in ('validated', 'pinned'):
        return empty
    return {'juridiction': value[:200], 'chambre': '', 'rg': '', 'source': 'information_validee'}


def _type_from(title):
    text = fold(title)
    for word, kind in (('mise en etat', 'mise_en_etat'), ('plaid', 'plaidoiries'), ('refere', 'refere'), ('delibere', 'delibere'), ('conciliation', 'conciliation'),
                       ('orientation', 'orientation')):
        if word in text:
            return kind
    return 'autre'


def _utc(value, tz):
    """Instant UTC d'une date du cache d'agenda (date seule : minuit dans le fuseau du cabinet)."""
    raw = str(value or '')
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw):
            return datetime.combine(date.fromisoformat(raw), datetime.min.time(), tz).astimezone(timezone.utc).isoformat()
        parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)).astimezone(timezone.utc).isoformat()
    except ValueError:
        return ''


# ------------------------------------------------------------------------------------------------ rattachement aux événements
def _insert(desk, values):
    now = desk.now()
    row = {'id': uuid.uuid4().hex, 'event_id': '', 'event_uid': '', 'cal_key': '', 'matter': '', 'juridiction': '', 'juridiction_source': '', 'chambre': '',
           'type_audience': 'autre', 'rg': '', 'avocat': '', 'notes': '', 'statut': 'a_venir', 'origine': 'saisie', 'propagate': 0, 'title': '', 'location': '',
           'starts': '', 'ends': '', 'all_day': 0, 'version': 1, 'created': now, 'updated': now, **values}
    if row['matter']:
        sure = fiche_sure(desk, row['matter'])
        row.update(juridiction=row['juridiction'] or sure['juridiction'], chambre=row['chambre'] or sure['chambre'], rg=row['rg'] or sure['rg'],
                   juridiction_source=row['juridiction_source'] or sure['source'])
    cur = desk.db.execute('INSERT OR IGNORE INTO audiences5688(%s) VALUES(%s)' % (','.join(row), ','.join('?' for _ in row)), tuple(row.values()))
    return row if cur.rowcount else None


def _from_event(desk, event, origine, propagate):
    return _insert(desk, {'event_id': event['id'], 'event_uid': event['uid'], 'matter': event['matter'], 'origine': origine, 'propagate': int(propagate),
                          'type_audience': _type_from(event['title']), 'title': event['title'], 'location': event['location'], 'starts': event['starts'],
                          'ends': event['ends'], 'all_day': int(event['all_day']), 'statut': 'a_venir' if event['status'] == 'actif' else 'annulee'})


def by_event(desk, event_id):
    row = desk.db.execute('SELECT * FROM audiences5688 WHERE event_id=?', (str(event_id),)).fetchone()
    return dict(row) if row else None


def on_event(desk, event, change):
    """Appelé par l'agenda après l'enregistrement local : la fiche suit l'événement, puis la copie distante est planifiée.
    Jamais bloquant : l'événement est déjà enregistré ; un incident se répare au passage suivant de ``migrate``."""
    try:
        schema(desk)
        hearing = by_event(desk, event['id'])
        if event['kind'] == 'audience' and not hearing and change == 'cree':
            hearing = _from_event(desk, event, 'saisie', str(event.get('source') or 'local') == 'local')
        if hearing:
            statut = hearing['statut']
            if event['status'] != 'actif' and statut not in ('annulee', 'ecartee'):
                statut = 'annulee'
            desk.db.execute('UPDATE audiences5688 SET title=?,location=?,starts=?,ends=?,all_day=?,matter=?,statut=?,updated=? WHERE id=?',
                            (event['title'], event['location'], event['starts'], event['ends'], int(event['all_day']), event['matter'], statut, desk.now(), hearing['id']))
        desk.db.commit()
        from . import propagation5688
        propagation5688.follow(desk, event['id'], 'annuler' if event['status'] != 'actif' else 'envoyer')
    except Exception as ex:   # noqa: BLE001
        try:
            desk.audit('audience5688_suivi_differe', {'id': event.get('id'), 'cause': str(ex)[:120]})
        except Exception:   # noqa: BLE001
            pass


def migrate(desk):
    """Une fiche par événement de type audience, sans en perdre : idempotent, aucune copie distante, aucun événement modifié.
    Seule une audience déjà copiée dans Outlook reste suivie (sa copie existe déjà)."""
    schema(desk)
    rows = desk.db.execute("SELECT a.* FROM agenda5634 a LEFT JOIN audiences5688 h ON h.event_id=a.id WHERE a.kind='audience' AND h.id IS NULL").fetchall()
    added = 0
    if rows:
        from . import microsoft5636 as ms
        ms.schema(desk)
        linked = {r[0] for r in desk.db.execute('SELECT local_id FROM ms_links5636')}
        for r in rows:
            added += bool(_from_event(desk, dict(r), 'migration', r['id'] in linked))
    desk.db.commit()
    return added


def sync_nextcloud(desk, now=None):
    """Audiences vues dans les agendas Nextcloud (agenda de référence) : fiche créée ou mise à jour, sans écriture vers Nextcloud."""
    from .agenda5634 import LOCAL_URL, _tz
    from .audience5613 import HEARING_WORDS
    schema(desk)
    now = now or datetime.now(timezone.utc)
    tz = _tz(desk)
    since = (now - timedelta(days=1)).isoformat()
    copied = {r[0] for r in desk.db.execute("SELECT remote_uid FROM agenda5634 WHERE remote_uid<>''")}
    added = 0
    moved = []
    for r in desk.db.execute('SELECT uid,recurrence_id,title,location,starts,ends,matter FROM calendar_cache WHERE calendar_url<>? LIMIT 2000', (LOCAL_URL,)).fetchall():
        if r['uid'] in copied or not any(w in fold(r['title']) for w in HEARING_WORDS):
            continue
        starts, ends = _utc(r['starts'], tz), _utc(r['ends'], tz)
        if not starts or starts < since:
            continue
        key = '%s|%s' % (r['uid'], r['recurrence_id'])
        old = desk.db.execute('SELECT id,matter,propagate FROM audiences5688 WHERE cal_key=?', (key,)).fetchone()
        if old:
            cur = desk.db.execute('UPDATE audiences5688 SET title=?,location=?,starts=?,ends=?,matter=?,updated=? WHERE id=? AND (title<>? OR location<>? OR starts<>? OR ends<>?)',
                                  (r['title'][:200], r['location'][:200], starts, ends, old['matter'] or r['matter'], desk.now(), old['id'], r['title'][:200], r['location'][:200], starts, ends))
            if cur.rowcount and old['propagate']:
                moved.append(old['id'])
            continue
        if r['matter'] and desk.db.execute("SELECT 1 FROM audiences5688 WHERE matter=? AND starts=? AND statut<>'ecartee'", (r['matter'], starts)).fetchone():
            continue   # même audience déjà suivie : pas de doublon
        added += bool(_insert(desk, {'cal_key': key, 'event_uid': r['uid'], 'matter': r['matter'], 'origine': 'nextcloud', 'type_audience': _type_from(r['title']),
                                     'title': r['title'][:200], 'location': r['location'][:200], 'starts': starts, 'ends': ends, 'all_day': int(len(str(r['starts'])) == 10)}))
    desk.db.commit()
    if moved:   # copie déjà demandée par une personne : elle suit l'agenda de référence
        from . import propagation5688
        for ident in moved:
            propagation5688.follow_hearing(desk, ident)
    return added


# ------------------------------------------------------------------------------------------------ lecture
SELECT = ('SELECT h.id,h.event_id,h.event_uid,h.cal_key,h.matter,h.juridiction,h.juridiction_source,h.chambre,h.type_audience,h.rg,h.avocat,h.notes,h.statut,h.origine,h.propagate,'
          'h.version,h.created,h.updated,COALESCE(a.title,h.title) AS title, COALESCE(a.location,h.location) AS location, COALESCE(a.starts,h.starts) AS starts, '
          'COALESCE(a.ends,h.ends) AS ends, COALESCE(a.all_day,h.all_day) AS all_day, a.description AS description, a.reminders AS reminders, a.status AS event_status '
          'FROM audiences5688 h LEFT JOIN agenda5634 a ON a.id=h.event_id')


def view(desk, row, known=None):
    from .agenda5634 import _tz
    item = dict(row)
    known = members(desk) if known is None else known
    item['avocat_nom'] = member_name(known, item['avocat']) if item['avocat'] else SANS_AVOCAT
    item['sans_avocat'] = not item['avocat']
    item['type_label'] = TYPES.get(item['type_audience'], item['type_audience'])
    item['statut_label'] = STATUTS.get(item['statut'], item['statut'])
    item['lecture_seule'] = not item['event_id']
    item['quand'] = when_label(item['starts'], item['all_day'], _tz(desk))
    return item


def when_label(starts, all_day, tz):
    try:
        moment = datetime.fromisoformat(str(starts)).astimezone(tz)
    except ValueError:
        return 'date inconnue'
    return moment.strftime('%d/%m/%Y') + ('' if all_day else moment.strftime(' à %H:%M'))


def get(desk, ident):
    schema(desk)
    row = desk.db.execute(SELECT + ' WHERE h.id=?', (str(ident or ''),)).fetchone()
    if not row:
        raise Stop('audience_absente')
    guard(row['matter'])
    return view(desk, row)


def _day(value, tz, end=False):
    try:
        day = date.fromisoformat(str(value)[:10])
    except ValueError:
        raise Stop('date_invalide') from None
    return datetime.combine(day + timedelta(days=1 if end else 0), datetime.min.time(), tz).astimezone(timezone.utc).isoformat()


def listing(desk, avocat='', start='', end='', statut='', matter='', limit=400, known=None):
    """Audiences de la période, filtrées par avocat (``sans`` : sans avocat désigné) ; les dossiers réservés de l'utilisateur sont retirés."""
    from .agenda5634 import _tz
    from .membres5650 import viewer_hidden
    schema(desk)
    tz = _tz(desk)
    today = datetime.now(tz).date()
    a = _day(start or today.isoformat(), tz)
    b = _day(end or (today + timedelta(days=90)).isoformat(), tz, end=True)
    sql, params = SELECT + ' WHERE COALESCE(a.starts,h.starts)>=? AND COALESCE(a.starts,h.starts)<?', [a, b]
    if statut:
        if statut not in STATUTS:
            raise Stop('statut_audience_invalide')
        sql += ' AND h.statut=?'
        params.append(statut)
    else:
        sql += " AND h.statut<>'ecartee'"
    if avocat:
        sql += ' AND h.avocat=?'
        params.append('' if avocat == SANS else avocat)
    if matter:
        sql += ' AND h.matter=?'
        params.append(matter)
    hidden = viewer_hidden() or frozenset()
    known = members(desk) if known is None else known
    from .acces5697 import Perimetre, acteur_requete, masques_requete   # 5.6.97 (B07) : droits joints avant la limite
    clause, caches = Perimetre(acteur_requete(), masques_requete(desk) | hidden).sql('h.matter')
    rows = desk.db.execute(sql + ' AND ' + clause + ' ORDER BY COALESCE(a.starts,h.starts) LIMIT ?', (*params, *caches, int(limit))).fetchall()
    return [view(desk, r, known) for r in rows if r['matter'] not in hidden]


# ------------------------------------------------------------------------------------------------ écriture
def _text(data, key, maximum):
    return re.sub(r'\s+', ' ', str(data.get(key) or '')).strip()[:maximum]


def _clean(desk, data, current=None, auth=None):
    cur = current or {}
    out = {}
    for key, maximum in (('juridiction', 200), ('chambre', 120), ('rg', 40)):
        out[key] = _text(data, key, maximum) if key in data else cur.get(key, '')
    out['notes'] = str(data['notes']).strip()[:4000] if 'notes' in data else cur.get('notes', '')
    kind = str(data['type'] if 'type' in data else cur.get('type_audience', 'autre'))
    if kind not in TYPES:
        raise Stop('type_audience_invalide')
    out['type_audience'] = kind
    statut = str(data['statut'] if 'statut' in data else cur.get('statut', 'a_venir'))
    if statut not in STATUTS:
        raise Stop('statut_audience_invalide')
    out['statut'] = statut
    matter = str(data['matter'] if 'matter' in data else cur.get('matter', ''))
    if matter and not any(m['id'] == matter for m in load_matters(desk.c)):
        raise Stop('dossier_absent')
    guard(matter)
    out['matter'] = matter
    out['avocat'] = check_person(desk, str(data['avocat'] if 'avocat' in data else cur.get('avocat', '')), matter, auth)
    return out


def _title(fields, data):
    title = _text(data, 'title', 200)
    if title:
        return title
    return ' · '.join(x for x in ('Audience', TYPES.get(fields['type_audience']) if fields['type_audience'] != 'autre' else '', fields['juridiction']) if x)


def _create_event(desk, data, fields, owner):
    from . import agenda5634
    event = {'kind': 'audience', 'title': _title(fields, data), 'matter': fields['matter'], 'location': data.get('location') or '', 'description': '',
             'start': data.get('start'), 'duration_minutes': data.get('duration_minutes') or 60, 'all_day': data.get('all_day'), 'days': data.get('days') or 1}
    if data.get('reminders') is not None:
        event['reminders'] = data['reminders']
    created = agenda5634.create(desk, event, owner)
    hearing = by_event(desk, created['id']) or _from_event(desk, agenda5634._row(desk, created['id']), 'saisie', True)
    return hearing


def _update_event(desk, hearing, data, fields, owner):
    from . import agenda5634
    current = agenda5634._row(desk, hearing['event_id'])
    if current['status'] != 'actif':
        return
    if fields['statut'] == 'annulee':
        agenda5634.cancel(desk, hearing['event_id'])
        return
    event = {k: data[k] for k in ('title', 'start', 'duration_minutes', 'days', 'reminders') if data.get(k) not in (None, '')}
    if 'location' in data:
        event['location'] = data['location']
    if current['matter'] != fields['matter']:
        event['matter'] = fields['matter']
    if event:
        agenda5634.update(desk, {'id': hearing['event_id'], **event}, owner)


def save(desk, data, owner='cabinet', auth=None):
    """Création ou correction par une personne. L'horaire, le lieu et l'intitulé passent par l'agenda ; le reste par la fiche."""
    schema(desk)
    if owner in AGENT_ACTORS:
        raise Stop('audience_reservee_aux_personnes')
    ident = str(data.get('id') or '')
    if ident:
        row = desk.db.execute('SELECT * FROM audiences5688 WHERE id=?', (ident,)).fetchone()
        if not row:
            raise Stop('audience_absente')
        hearing = dict(row)
        guard(hearing['matter'])
        if str(data.get('version') or hearing['version']) != str(hearing['version']):
            raise Stop('audience_modifiee_entre_temps')
        fields = _clean(desk, data, hearing, auth)
        if hearing['event_id']:
            _update_event(desk, hearing, data, fields, owner)
    else:
        fields = _clean(desk, data, auth=auth)
        hearing = _create_event(desk, data, fields, owner)
    source = hearing['juridiction_source'] if fields['juridiction'] == hearing['juridiction'] else 'saisie'
    if not ident and not data.get('juridiction'):   # fiche créée à l'instant : ce qu'elle a repris de la fiche du dossier est conservé
        fields.update(juridiction=hearing['juridiction'], chambre=fields['chambre'] or hearing['chambre'], rg=fields['rg'] or hearing['rg'])
        source = hearing['juridiction_source']
    desk.db.execute('UPDATE audiences5688 SET juridiction=?,juridiction_source=?,chambre=?,type_audience=?,rg=?,avocat=?,notes=?,statut=?,matter=?,propagate=?,version=version+1,updated=? WHERE id=?',
                    (fields['juridiction'], source, fields['chambre'], fields['type_audience'], fields['rg'], fields['avocat'], fields['notes'], fields['statut'],
                     fields['matter'], 1 if not ident else hearing['propagate'], desk.now(), hearing['id']))
    desk.db.commit()
    desk.audit('audience5688_enregistree', {'id': hearing['id'], 'matter': fields['matter'], 'statut': fields['statut'], 'par': owner})
    from . import propagation5688
    propagation5688.follow_hearing(desk, hearing['id'])
    return {'id': hearing['id'], 'message': 'Audience enregistrée.' if fields['avocat'] else 'Audience enregistrée. ' + SANS_AVOCAT + '.'}


def propose_copy(desk, ident, owner='cabinet'):
    """Une audience reprise par la migration ou lue dans Nextcloud n'est copiée que sur demande d'une personne."""
    hearing = get(desk, ident)
    desk.db.execute('UPDATE audiences5688 SET propagate=1,updated=? WHERE id=?', (desk.now(), hearing['id']))
    desk.db.commit()
    desk.audit('audience5688_copie_demandee', {'id': hearing['id'], 'par': owner})
    from . import propagation5688
    return propagation5688.follow_hearing(desk, hearing['id'])


def linked_tasks(desk, ident):
    from . import taches5688
    return taches5688.of_origin(desk, 'audience', ident)


def public(row):
    keep = ('id', 'matter', 'juridiction', 'chambre', 'type_audience', 'type_label', 'rg', 'avocat', 'avocat_nom', 'sans_avocat', 'notes', 'statut', 'statut_label',
            'title', 'location', 'starts', 'ends', 'quand', 'origine', 'version', 'lecture_seule')
    return {k: row.get(k) for k in keep}
