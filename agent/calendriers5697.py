"""5.6.97 (lot agenda, § 15) : un événement, une destination, un résultat vérifiable — adaptateurs local, CalDAV et Google Agenda.

- Agenda personnel : SQLite (``agenda5634``), hors synchronisation ; aucune écriture locale ne crée quoi que ce soit dans Nextcloud ou Google.
- CalDAV (Nextcloud ou autre serveur) : calendrier EXPLICITEMENT choisi parmi les cibles autorisées (``calendar568``), capacités découvertes
  (PROPFIND : VEVENT, VTODO, droit d'écriture) ; UID stable mémorisé AVANT l'appel ; ``If-None-Match: *`` à la création, ``If-Match`` + ETag à
  l'édition ; 412 → relecture et conflit montré, jamais un écrasement.
- Google Agenda : par ``google5697`` (lot gmail), portée agenda distincte, DÉSACTIVÉ par défaut (réglage du cabinet). Identifiant d'événement
  conforme (base32hex) et stable, ``sendUpdates=none``, aucun invité ; ETag + ``If-Match`` à l'édition ; lecture vérifiée dans la même plage.
- Commande distante (« sortie ») inscrite dans la transaction de création, exécutée après le commit ; erreur ambiguë → « Confirmation
  inconnue » et relecture par UID avant tout nouvel essai ; essais bornés avec attente croissante ; ``pause5629.checkpoint`` avant chaque effet.
- Lecture : une liste unifiée de sources (comptes autorisés), chacune avec son état réel (à jour, périmée, en erreur, non connectée,
  désactivée) et sa dernière synchronisation ; une source en panne garde son cache marqué « périmé », jamais un faux zéro ; l'Agenda
  personnel reste utilisable. Un événement distant n'est jamais copié comme événement local. Les séries se lisent ; leur édition est bloquée.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import uuid
from urllib.parse import quote, urlencode
from xml.etree import ElementTree

from .common import Stop

GOOGLE = 'agenda5697:google'          # réglage du cabinet : {'actif': bool} — désactivé par défaut
PORTEE_GOOGLE = 'https://www.googleapis.com/auth/calendar.events'
GOOGLE_API = 'https://www.googleapis.com/calendar/v3'
ESSAIS = 5                              # essais bornés d'une écriture distante
AMBIGUS = ('delai_http_depasse', 'connexion_http_indisponible', 'http_500', 'http_502', 'http_503', 'http_504')
ETATS = {'a_envoyer': 'Envoi à faire', 'envoi_en_cours': 'Envoi à vérifier', 'confirmation_inconnue': 'Confirmation inconnue : relecture avant nouvel essai',
         'confirme': 'Enregistré et relu dans le calendrier en ligne', 'conflit': 'Conflit : le calendrier en ligne a changé', 'echec': 'Refusé par le calendrier en ligne',
         'abandon': 'Abandonné après plusieurs essais : action requise'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS agenda_sorties5697(id TEXT PRIMARY KEY, proprietaire TEXT NOT NULL, cible TEXT NOT NULL, fournisseur TEXT NOT NULL,
  operation TEXT NOT NULL, cle TEXT NOT NULL, uid TEXT NOT NULL, dossier TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL, etag TEXT NOT NULL DEFAULT '',
  tentatives INTEGER NOT NULL DEFAULT 0, prochain TEXT NOT NULL DEFAULT '', erreur TEXT NOT NULL DEFAULT '', charge TEXT NOT NULL, recu TEXT NOT NULL DEFAULT '{}',
  created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS agenda_sorties5697_etat ON agenda_sorties5697(etat, prochain);
CREATE TABLE IF NOT EXISTS agenda_sources5697(source TEXT PRIMARY KEY, proprietaire TEXT NOT NULL, derniere_tentative TEXT NOT NULL DEFAULT '',
  derniere_reussite TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL DEFAULT '', erreur TEXT NOT NULL DEFAULT '');
'''


def _now():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------------------------------------ iCalendar (RFC 5545)
def _esc(text):
    return str(text or '').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\r', '').replace('\n', '\\n')


def ics(uid, ev):
    """VEVENT sans participant (aucune invitation), sans lien de visio privé ; journée entière en dates (fin exclusive)."""
    from .agenda5634 import _fold
    a, b = datetime.fromisoformat(ev['debut']), datetime.fromisoformat(ev['fin'])
    if ev.get('journee'):
        when = ['DTSTART;VALUE=DATE:' + ev['jour_debut'].replace('-', ''), 'DTEND;VALUE=DATE:' + ev['jour_fin'].replace('-', '')]
    else:
        when = ['DTSTART:' + a.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ'), 'DTEND:' + b.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')]
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//AxiorHub Pilote//Agenda 5.6.97//FR', 'BEGIN:VEVENT', 'UID:' + uid,
             'DTSTAMP:' + _now().strftime('%Y%m%dT%H%M%SZ'), *when, 'SUMMARY:' + _esc(ev['titre'])]
    if ev.get('lieu'):
        lines.append('LOCATION:' + _esc(ev['lieu']))
    if ev.get('notes'):
        lines.append('DESCRIPTION:' + _esc(ev['notes']))
    lines += ['CLASS:' + ('PRIVATE' if ev.get('visibilite') == 'privee' else 'PUBLIC'), 'END:VEVENT', 'END:VCALENDAR']
    return ('\r\n'.join(_fold(x) for x in lines) + '\r\n').encode('utf-8')


def _forme(ev):
    """Ce qui doit être relu à l'identique : début, fin, titre."""
    def canon(v):
        v = str(v or '')
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', v):
            return v
        try:
            return datetime.fromisoformat(v.replace('Z', '+00:00')).astimezone(timezone.utc).isoformat()
        except ValueError:
            return v
    return canon(ev.get('debut')), canon(ev.get('fin')), re.sub(r'\s+', ' ', str(ev.get('titre') or '')).strip()


def _attendu(charge):
    if charge.get('journee'):
        return {'debut': charge['jour_debut'], 'fin': charge['jour_fin'], 'titre': charge['titre']}
    return charge


# ------------------------------------------------------------------------------------------------ adaptateurs
class ConflitVersion(Stop):
    """412 : la ressource distante a changé ; ``distant`` porte la version relue."""

    def __init__(self, distant):
        super().__init__('conflit_version_distante')
        self.distant = distant


class CalDAV:
    """Collection CalDAV choisie : PROPFIND (capacités), REPORT (lecture), GET (relecture), PUT conditionnel (écriture)."""

    D, C = '{DAV:}', '{urn:ietf:params:xml:ns:caldav}'

    def __init__(self, http, url, tz='Europe/Paris'):
        self.http, self.url, self.tz = http, url.rstrip('/') + '/', tz

    def href(self, uid):
        return self.url + quote(uid, safe='') + '.ics'

    def capacites(self):
        body = (b'<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><c:supported-calendar-component-set/>'
                b'<d:current-user-privilege-set/></d:prop></d:propfind>')
        root = ElementTree.fromstring(self.http.request('PROPFIND', self.url, body, {'Depth': '0', 'Content-Type': 'application/xml; charset=utf-8'}, 400_000))
        comps = {x.get('name', '').upper() for x in root.iter(self.C + 'comp')}
        privs = {p.tag for priv in root.iter(self.D + 'privilege') for p in priv}
        ecrire = bool(privs & {self.D + 'write', self.D + 'write-content', self.D + 'all'})
        return {'vevent': 'VEVENT' in comps or not comps, 'vtodo': 'VTODO' in comps, 'ecriture': ecrire, 'lecture': True}

    def lire(self, debut, fin):
        from .calendar568 import _dav_events
        out = []
        for ev in _dav_events(self.http, self.url, debut, fin, self.tz):
            out.append({'uid': ev.get('uid', ''), 'titre': ev.get('summary', ''), 'debut': ev['start'], 'fin': ev['end'], 'lieu': ev.get('location', ''),
                        'journee': bool(ev.get('all_day')), 'recurrence': ev.get('recurrence_id', ''), 'notes': ev.get('description', '')})
        return out

    def relire(self, uid):
        from .dav import parse_events
        try:
            raw = self.http.request('GET', self.href(uid), None, {'Accept': 'text/calendar'}, 400_000)
        except Stop as ex:
            if str(ex) in ('http_404', 'http_410'):
                return None
            raise
        etag = str((self.http.last_headers or {}).get('ETag') or (self.http.last_headers or {}).get('Etag') or '')
        found = [x for x in parse_events(raw.decode('utf-8'), self.tz) if x.get('uid') == uid]
        if len(found) != 1:
            raise Stop('relecture_calendrier_non_conforme')
        x = found[0]
        if x.get('all_day'):
            debut, fin = x['start'][:10], x['end'][:10]
        else:
            debut, fin = x['start'], x['end']
        return {'uid': uid, 'titre': x.get('summary', ''), 'debut': debut, 'fin': fin, 'etag': etag, 'participants': bool(re.search(rb'(?mi)^ATTENDEE', raw))}

    def _put(self, uid, charge, condition):
        try:
            self.http.request('PUT', self.href(uid), ics(uid, charge), {'Content-Type': 'text/calendar; charset=utf-8', **condition}, 400_000)
        except Stop as ex:
            if str(ex) == 'http_412':
                raise ConflitVersion(self.relire(uid)) from None
            raise
        return str((self.http.last_headers or {}).get('ETag') or '')

    def creer(self, uid, charge):
        return self._put(uid, charge, {'If-None-Match': '*'})

    def modifier(self, uid, charge, etag):
        if not etag:
            raise Stop('version_distante_inconnue')
        return self._put(uid, charge, {'If-Match': etag})


class Google:
    """Agenda Google choisi (``calendarId``) : insertion avec identifiant fourni, relecture, mise à jour conditionnelle, liste paginée."""

    def __init__(self, http, calendrier, tz='Europe/Paris'):
        self.http, self.calendrier, self.tz = http, calendrier, tz

    def _chemin(self, suffixe=''):
        return GOOGLE_API + '/calendars/' + quote(self.calendrier, safe='') + '/events' + suffixe

    def _json(self, method, url, body=None, headers=None):
        raw = self.http.request(method, url, None if body is None else json.dumps(body).encode(), {'Content-Type': 'application/json', **(headers or {})}, 2_000_000)
        try:
            return json.loads(raw or b'{}')
        except ValueError:
            raise Stop('json_http_invalide') from None

    @staticmethod
    def corps(uid, charge):
        key = 'date' if charge.get('journee') else 'dateTime'
        a, b = (charge['jour_debut'], charge['jour_fin']) if charge.get('journee') else (charge['debut'], charge['fin'])
        out = {'id': uid, 'summary': charge['titre'], 'start': {key: a}, 'end': {key: b}, 'reminders': {'useDefault': False},
               'visibility': 'private' if charge.get('visibilite') == 'privee' else 'default'}
        if charge.get('lieu'):
            out['location'] = charge['lieu']
        if charge.get('notes'):
            out['description'] = charge['notes']
        return out

    @staticmethod
    def _lu(item):
        s, f = item.get('start') or {}, item.get('end') or {}
        return {'uid': item.get('id', ''), 'titre': item.get('summary', ''), 'debut': s.get('dateTime') or s.get('date') or '', 'fin': f.get('dateTime') or f.get('date') or '',
                'journee': bool(s.get('date')), 'lieu': item.get('location', ''), 'recurrence': item.get('recurringEventId', ''), 'etag': item.get('etag', ''),
                'participants': bool(item.get('attendees')), 'notes': item.get('description', '')}

    def capacites(self):
        info = self._json('GET', GOOGLE_API + '/users/me/calendarList/' + quote(self.calendrier, safe=''))
        role = info.get('accessRole', '')
        return {'vevent': True, 'vtodo': False, 'lecture': role in ('reader', 'writer', 'owner'), 'ecriture': role in ('writer', 'owner')}

    def lire(self, debut, fin):
        out, page = [], ''
        for _ in range(10):
            r = self._json('GET', self._chemin('?' + urlencode({'timeMin': debut.astimezone(timezone.utc).isoformat(), 'timeMax': fin.astimezone(timezone.utc).isoformat(),
                                                                'singleEvents': 'true', 'showDeleted': 'false', 'maxResults': 250, **({'pageToken': page} if page else {})})))
            out += [self._lu(x) for x in r.get('items', []) if x.get('status') != 'cancelled']
            page = r.get('nextPageToken', '')
            if not page:
                return out
        raise Stop('google_evenements_lecture_incomplete')

    def relire(self, uid):
        try:
            item = self._json('GET', self._chemin('/' + quote(uid, safe='')))
        except Stop as ex:
            if str(ex) in ('http_404', 'http_410'):
                return None
            raise
        return None if item.get('status') == 'cancelled' else self._lu(item)

    def creer(self, uid, charge):
        try:
            return self._json('POST', self._chemin('?sendUpdates=none'), self.corps(uid, charge)).get('etag', '')
        except Stop as ex:
            if str(ex) == 'http_409':   # identifiant déjà pris : l'appelant relit et compare
                raise ConflitVersion(self.relire(uid)) from None
            raise

    def modifier(self, uid, charge, etag):
        if not etag:
            raise Stop('version_distante_inconnue')
        try:
            return self._json('PUT', self._chemin('/' + quote(uid, safe='') + '?sendUpdates=none'), self.corps(uid, charge), {'If-Match': etag}).get('etag', '')
        except Stop as ex:
            if str(ex) == 'http_412':
                raise ConflitVersion(self.relire(uid)) from None
            raise


# ------------------------------------------------------------------------------------------------ réglage Google et cibles
def google_reglage(desk):
    return {'actif': bool((desk.settings(GOOGLE, {}) or {}).get('actif'))}


def regler_google(desk, acteur, actif):
    if not isinstance(actif, bool):
        raise Stop('valeur_invalide')
    desk.setting(GOOGLE, {'actif': actif})
    desk.audit('agenda5697_google_reglage', {'actif': actif, 'par': str(acteur)[:80]})
    return google_reglage(desk)


class PontGoogle:
    """Transport des adaptateurs vers ``google5697.appeler`` (connexion « agenda » du compte, jetons chiffrés, renouvellement sérialisé) :
    lecture avec la capacité ``agenda.lecture``, écriture avec ``agenda.ecriture`` — la lecture n'est jamais déduite de l'écriture.
    Les erreurs Google reprennent les codes publics ``http_<statut>`` attendus par les adaptateurs (404, 409, 412…)."""

    def __init__(self, desk, conn_id):
        self.desk, self.conn_id, self.last_headers = desk, conn_id, {}

    def request(self, method, url, data=None, headers=None, limit=0):
        from . import google5697
        from .erreurs5634 import http_error
        capacite = 'agenda.lecture' if method == 'GET' else 'agenda.ecriture'
        conditions = {k: v for k, v in (headers or {}).items() if k.lower() == 'if-match'}
        try:
            out = google5697.appeler(self.desk, self.conn_id, method, url, json.loads(data) if data else None, capacite, conditions)
        except google5697.ErreurGoogle as ex:
            if ex.statut:
                raise http_error(ex.statut, method, url) from None
            raise
        return json.dumps(out).encode('utf-8')


def _google_connecte(desk, owner):
    try:
        from . import google5697
        row = google5697.connexion(desk, owner, 'agenda')
    except Exception:   # noqa: BLE001 — module absent ou base illisible : non connecté, dit tel quel
        return False
    return bool(row and row.get('etat') == 'active')


def _google_http(desk, owner):
    """Connexion Google « agenda » du compte (``google5697``, lot gmail, portée distincte de Gmail). Désactivé ou non connecté : refus explicite."""
    if not google_reglage(desk)['actif']:
        raise Stop('google_agenda_desactive')
    try:
        from . import google5697
    except ImportError:
        raise Stop('google_agenda_non_disponible') from None
    row = google5697.connexion(desk, owner, 'agenda')
    if not row or row.get('etat') != 'active':
        raise Stop('google_agenda_non_connecte')
    return PontGoogle(desk, row['id'])


def cibles(desk, owner):
    """Cibles en ligne autorisées pour ce compte (sans secret), avec le fournisseur et la possibilité d'écrire."""
    from . import calendar568
    try:
        rows = calendar568.effective_targets(desk, owner)
    except Stop:
        rows = []
    out = []
    for t in rows:
        google = t['provider'] == 'google'
        out.append({'id': t['id'], 'libelle': t['label'], 'fournisseur': t['provider'], 'config': t['config'],
                    'ecriture': (google_reglage(desk)['actif'] and _google_connecte(desk, owner)) if google else True})
    return out


def cible_ecriture(desk, owner, cible_id):
    cible = next((c for c in cibles(desk, owner) if c['id'] == cible_id), None)
    if cible is None:
        raise Stop('calendrier_absent')
    if not cible['ecriture']:
        if cible['fournisseur'] == 'google':
            raise Stop('google_agenda_desactive' if not google_reglage(desk)['actif'] else 'google_agenda_non_connecte')
        raise Stop('calendrier_en_lecture_seule')
    return cible


def adaptateur(desk, owner, cible):
    from .agenda5634 import _tz
    tz = _tz(desk).key
    if cible['fournisseur'] == 'google':
        return Google(_google_http(desk, owner), cible['config'].get('calendar_id') or 'primary', tz)
    from . import calendar568
    target = {'provider': cible['fournisseur'], 'config': cible['config'], 'id': cible['id']}
    return CalDAV(calendar568._http(desk, target), cible['config']['url'], tz)


# ------------------------------------------------------------------------------------------------ commandes distantes (sorties)
def uid_stable(owner, cible_id, cle):
    """UID / identifiant Google conforme (base32hex : 0-9 a-v) et stable pour la même édition (même clé)."""
    return 'ag97' + hashlib.sha256(('%s|%s|%s' % (owner, cible_id, cle)).encode()).hexdigest()[:40]


def inscrire(desk, owner, cible_id, cle, contenu):
    """Inscrit la commande dans la transaction de l'appelant ; l'UID est mémorisé AVANT tout appel. Rend l'identifiant de la sortie."""
    cible = cible_ecriture(desk, owner, cible_id)
    ev = contenu['evenement']
    charge = {'titre': contenu['titre'], 'lieu': contenu['lieu'], 'notes': contenu['notes'], 'debut': ev['debut'], 'fin': ev['fin'], 'journee': ev['journee'],
              'visibilite': ev['visibilite']}
    if ev['journee']:
        from .agenda5697 import zone_de
        z = zone_de(desk, ev['zone'])
        charge.update(jour_debut=datetime.fromisoformat(ev['debut']).astimezone(z).date().isoformat(),
                      jour_fin=datetime.fromisoformat(ev['fin']).astimezone(z).date().isoformat())
    ident = uuid.uuid4().hex
    stamp = desk.now()
    desk.db.execute('INSERT INTO agenda_sorties5697(id,proprietaire,cible,fournisseur,operation,cle,uid,dossier,etat,charge,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (ident, owner, cible_id, cible['fournisseur'], 'creer', cle, uid_stable(owner, cible_id, cle), contenu['dossier'], 'a_envoyer',
                     json.dumps(charge, ensure_ascii=False), stamp, stamp))
    return ident


def _sortie(desk, ident):
    row = desk.db.execute('SELECT * FROM agenda_sorties5697 WHERE id=?', (str(ident),)).fetchone()
    if not row:
        raise Stop('introuvable')
    return dict(row)


def recu(desk, ident):
    """Reçu consultable d'une écriture distante (202) : état honnête, essais, prochain essai, dernière erreur."""
    r = _sortie(desk, ident)
    return {'id': r['id'], 'etat': r['etat'], 'libelle': ETATS.get(r['etat'], r['etat']), 'uid': r['uid'], 'tentatives': r['tentatives'],
            'prochain_essai': r['prochain'], 'erreur': r['erreur'], 'cible': r['cible'], 'fournisseur': r['fournisseur'], 'dossier': r['dossier'],
            'distant': json.loads(r['recu'] or '{}').get('distant')}


def _maj(desk, ident, **champs):
    champs['updated'] = desk.now()
    desk.db.execute('UPDATE agenda_sorties5697 SET %s WHERE id=?' % ', '.join('%s=?' % k for k in champs), (*champs.values(), ident))
    desk.db.commit()


def _conforme(lu, charge):
    return bool(lu) and _forme(lu) == _forme(_attendu(charge)) and not lu.get('participants')


def executer(desk, ident, client=None):
    """Exécute UNE commande distante : relecture d'abord si le résultat précédent est inconnu, puis écriture conditionnelle, puis relecture.
    Jamais de faux succès : seul un événement relu conforme passe à « confirmé »."""
    from .pause5629 import checkpoint
    r = _sortie(desk, ident)
    if r['etat'] in ('confirme', 'conflit', 'echec', 'abandon'):
        return recu(desk, ident)
    charge = json.loads(r['charge'])
    try:
        cible = next((c for c in cibles(desk, r['proprietaire']) if c['id'] == r['cible']), None)
        if cible is None:
            raise Stop('calendrier_absent')
        a = client or adaptateur(desk, r['proprietaire'], cible)
        checkpoint(desk)
        if r['tentatives'] >= ESSAIS:   # essais bornés (relectures comprises) : l'avocat reprend la main
            _maj(desk, ident, etat='abandon', erreur='Essais épuisés : vérifiez la connexion du calendrier puis relancez.')
            return recu(desk, ident)
        incertain = r['etat'] in ('envoi_en_cours', 'confirmation_inconnue')
        _maj(desk, ident, etat='confirmation_inconnue' if incertain else 'envoi_en_cours', tentatives=r['tentatives'] + 1)   # trace AVANT l'appel
        if incertain:   # résultat inconnu : relire avant de retenter
            lu = a.relire(r['uid'])
            if lu is not None:
                return _clore(desk, ident, lu, charge)
            _maj(desk, ident, etat='envoi_en_cours')
        try:
            if r['operation'] == 'creer':
                a.creer(r['uid'], charge)
            else:
                a.modifier(r['uid'], charge, r['etag'])
        except ConflitVersion as ex:
            if r['operation'] == 'creer' and _conforme(ex.distant, charge):
                return _clore(desk, ident, ex.distant, charge)   # même UID déjà présent et identique : rien à refaire
            _maj(desk, ident, etat='conflit', recu=json.dumps({'distant': ex.distant}), erreur='Le calendrier en ligne a été modifié entre-temps.')
            return recu(desk, ident)
        lu = a.relire(r['uid'])
        if lu is None:
            raise Stop('relecture_calendrier_absente')
        return _clore(desk, ident, lu, charge)
    except Stop as ex:
        code = str(ex)
        if code in AMBIGUS or code == 'relecture_calendrier_absente':
            attente = timedelta(minutes=2 ** min(r['tentatives'] + 1, 6))
            _maj(desk, ident, etat='confirmation_inconnue', prochain=(_now() + attente).isoformat(), erreur='Réponse perdue ou calendrier indisponible (%s).' % code)
        else:
            _maj(desk, ident, etat='echec', erreur='Refus du calendrier en ligne (%s).' % code)
        return recu(desk, ident)


def _clore(desk, ident, lu, charge):
    if not _conforme(lu, charge):
        _maj(desk, ident, etat='conflit', recu=json.dumps({'distant': lu}), erreur='L’événement relu ne correspond pas à la saisie.')
    else:
        _maj(desk, ident, etat='confirme', etag=lu.get('etag', ''), recu=json.dumps({'distant': lu}), erreur='')
    desk.audit('agenda5697_sortie', {'id': ident, 'etat': _sortie(desk, ident)['etat']})
    return recu(desk, ident)


def modifier_distant(desk, acteur, cible_id, uid, etag, charge, cle):
    """Édition d'un événement distant connu (UID, ETag lus) : commande conditionnelle (If-Match) ; 412 → conflit montré."""
    from .acces5697 import acteur_de
    a = acteur_de(acteur)
    cible_ecriture(desk, a.compte, cible_id)
    if not etag:
        raise Stop('version_distante_inconnue')
    ident = uuid.uuid4().hex
    stamp = desk.now()
    desk.db.execute('INSERT INTO agenda_sorties5697(id,proprietaire,cible,fournisseur,operation,cle,uid,dossier,etat,etag,charge,created,updated) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (ident, a.compte, cible_id, next(c['fournisseur'] for c in cibles(desk, a.compte) if c['id'] == cible_id),
                                                         'modifier', cle, uid, '', 'a_envoyer', etag, json.dumps(charge, ensure_ascii=False), stamp, stamp))
    desk.db.commit()
    return ident


def tick(desk, limite=10, clients=None):
    """Reprise bornée des commandes en attente (cycle de surveillance) ; chaque effet passe par ``pause5629.checkpoint``."""
    from .agenda5697 import schema
    schema(desk)
    now = _now().isoformat()
    rows = desk.db.execute("SELECT id FROM agenda_sorties5697 WHERE etat IN ('a_envoyer','confirmation_inconnue','envoi_en_cours') AND (prochain='' OR prochain<=?) "
                           "ORDER BY created LIMIT ?", (now, int(limite))).fetchall()
    out = []
    for r in rows:
        try:
            out.append(executer(desk, r['id'], (clients or {}).get(r['id']))['etat'])
        except Stop as ex:   # pause ou arrêt : rien d'autre n'est tenté dans ce cycle
            out.append(str(ex))
            break
    return out


def en_attente(desk, acteur, debut, fin):
    """Créations distantes pas encore confirmées, affichées « en attente » (droits appliqués avant tout)."""
    from .acces5697 import dossiers_autorises
    from .agenda5697 import schema
    schema(desk)
    a_ = dossiers_autorises(desk, acteur)
    clause, args = a_.sql('dossier')
    out = []
    for r in desk.db.execute("SELECT * FROM agenda_sorties5697 WHERE operation='creer' AND etat NOT IN ('confirme') AND proprietaire=? AND " + clause +
                             " ORDER BY created LIMIT 200", (a_.acteur.compte, *args)):
        c = json.loads(r['charge'])
        if c['debut'] < fin.astimezone(timezone.utc).isoformat() and c['fin'] > debut.astimezone(timezone.utc).isoformat():
            out.append({'id': 'sortie:' + r['id'], 'title': c['titre'], 'starts': c['debut'], 'ends': c['fin'], 'all_day': bool(c.get('journee')), 'matter': r['dossier'],
                        'source': r['cible'], 'source_label': 'Calendrier en ligne', 'etat_synchro': r['etat'], 'etat_label': ETATS.get(r['etat'], r['etat']),
                        'modifiable': False, 'nature': 'rendez_vous', 'nature_label': 'Rendez-vous'})
    return out


# ------------------------------------------------------------------------------------------------ sources et lecture unifiée
def _source_etat(desk, source):
    row = desk.db.execute('SELECT * FROM agenda_sources5697 WHERE source=?', (source,)).fetchone()
    return dict(row) if row else {}


def _noter(desk, source, owner, ok, erreur=''):
    from .agenda5697 import schema
    schema(desk)   # tables créées quel que soit l'ordre d'appel (première lecture d'un agenda en ligne)
    now = desk.now()
    prev = _source_etat(desk, source)
    desk.db.execute('INSERT OR REPLACE INTO agenda_sources5697 VALUES(?,?,?,?,?,?)',
                    (source, owner, now, now if ok else prev.get('derniere_reussite', ''), 'a_jour' if ok else ('perime' if prev.get('derniere_reussite') else 'erreur'), erreur))
    desk.db.commit()


def sources(desk, owner):
    """Liste unifiée des sources lisibles par ce compte, avec leur état réel (jamais déduit de la seule capacité d'écriture)."""
    from .agenda5697 import schema
    from .workplan import _calendar_urls
    schema(desk)
    out = [{'id': 'local', 'libelle': 'Agenda personnel', 'fournisseur': 'local', 'lecture': True, 'ecriture': True, 'etat': 'a_jour',
            'derniere_synchro': '', 'message': 'Local, non synchronisé'}]
    if _calendar_urls(desk):
        st = _source_etat(desk, 'en_ligne')
        fresh = desk.db.execute("SELECT MAX(fetched) FROM calendar_cache WHERE calendar_url NOT IN ('local:agenda') AND calendar_url NOT LIKE 'agenda5697:%'").fetchone()[0] or ''
        out.append({'id': 'en_ligne', 'libelle': 'Agenda en ligne (Nextcloud)', 'fournisseur': 'nextcloud', 'lecture': True, 'ecriture': False,
                    'etat': st.get('etat') or ('a_jour' if fresh else 'jamais_lu'), 'derniere_synchro': st.get('derniere_reussite') or fresh, 'message': st.get('erreur', '')})
    for c in cibles(desk, owner):
        st = _source_etat(desk, c['id'])
        if c['fournisseur'] == 'nextcloud':   # lu avec « Agenda en ligne » (même collection) : destination d'écriture seulement ici
            etat, message, lecture = 'via_en_ligne', 'Lu avec « Agenda en ligne ».', False
        elif c['fournisseur'] == 'google' and not google_reglage(desk)['actif']:
            etat, message, lecture = 'desactive', 'Google Agenda est désactivé (réglage du cabinet).', False
        elif c['fournisseur'] == 'google' and not _google_connecte(desk, owner):
            etat, message, lecture = 'non_connecte', 'Compte Google non connecté pour l’agenda (Paramètres › Connexions).', False
        else:
            etat, message, lecture = st.get('etat') or 'jamais_lu', st.get('erreur', ''), True
        out.append({'id': c['id'], 'libelle': c['libelle'], 'fournisseur': c['fournisseur'], 'lecture': lecture,
                    'ecriture': c['ecriture'], 'etat': etat, 'derniere_synchro': st.get('derniere_reussite', ''), 'message': message})
    return out


def lire_cible(desk, acteur, cible_id, debut, fin, rafraichir=True, client=None):
    """Lecture d'une cible CalDAV ou Google : réussite → cache « agenda5697:<id> » remplacé sur la période ; échec → cache marqué périmé.
    Droits appliqués dans la requête, AVANT la limite."""
    from .acces5697 import acteur_de, dossiers_autorises
    perimetre = dossiers_autorises(desk, acteur)
    owner = acteur_de(acteur).compte
    url = 'agenda5697:' + cible_id
    cible = next((c for c in cibles(desk, owner) if c['id'] == cible_id), None)
    if cible is None:
        raise Stop('calendrier_absent')
    if rafraichir:
        try:
            if cible['fournisseur'] == 'google' and not google_reglage(desk)['actif']:
                raise Stop('google_agenda_desactive')
            lus = (client or adaptateur(desk, owner, cible)).lire(debut, fin)
            stamp = desk.now()
            if desk.db.in_transaction:
                desk.db.commit()
            desk.db.execute('DELETE FROM calendar_cache WHERE calendar_url=? AND starts<? AND ends>?', (url, fin.astimezone(timezone.utc).isoformat(), debut.astimezone(timezone.utc).isoformat()))
            for ev in lus:
                a = ev['debut'] if len(ev['debut']) > 10 else ev['debut'] + 'T00:00:00+00:00'
                b = ev['fin'] if len(ev['fin']) > 10 else ev['fin'] + 'T00:00:00+00:00'
                a, b = datetime.fromisoformat(a.replace('Z', '+00:00')).astimezone(timezone.utc).isoformat(), datetime.fromisoformat(b.replace('Z', '+00:00')).astimezone(timezone.utc).isoformat()
                ident = hashlib.sha256(('%s|%s|%s|%s' % (url, ev['uid'], ev.get('recurrence', ''), a)).encode()).hexdigest()
                desk.db.execute('INSERT OR REPLACE INTO calendar_cache VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                (ident, ev['uid'], ev.get('recurrence', ''), url, '', ev.get('etag', ''), ev['titre'], ev.get('notes', ''), ev.get('lieu', ''), a, b, 1, '', '[]', stamp))
            desk.db.commit()
            _noter(desk, cible_id, owner, True)
        except Stop as ex:
            _noter(desk, cible_id, owner, False, 'Lecture impossible (%s) : données en cache, potentiellement incomplètes.' % str(ex))
    clause, args = perimetre.sql('matter')
    rows = desk.db.execute('SELECT * FROM calendar_cache WHERE calendar_url=? AND starts<? AND ends>? AND ' + clause + ' ORDER BY starts LIMIT 500',
                           (url, fin.astimezone(timezone.utc).isoformat(), debut.astimezone(timezone.utc).isoformat(), *args)).fetchall()
    return [dict(r) for r in rows], _source_etat(desk, cible_id)
