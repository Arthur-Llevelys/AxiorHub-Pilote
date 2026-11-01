"""5.6.88 : copie des audiences vers Outlook et Google Agenda : création, mise à jour, annulation, sans jamais écraser une modification distante.

- La décision reste humaine : Outlook suit le réglage « Copier… » de Microsoft 365, Google la capacité « Copier les audiences » de la
  connexion Google Workspace (désactivée par défaut). Une audience n'est copiée que si une personne l'a saisie ou l'a demandé : une audience
  reprise par la migration ou lue dans Nextcloud attend « Copier vers les agendas ». Le modèle n'a aucun accès à ce module.
- Dossier exclu de la transmission externe ou dossier réservé : rien ne part. Par défaut l'intitulé est neutre (« Audience AxiorHub · dossier ») ;
  juridiction, RG et lieu ne partent que si le cabinet l'a choisi (« Transmettre le détail »). Les notes ne partent jamais.
- L'identifiant distant est conservé (``agenda_distant5688``). Une création reprise après une erreur ne double pas l'événement : Graph reçoit
  le même ``transactionId``, Google le même identifiant.
- Avant toute écriture, l'événement distant est relu : s'il a changé depuis la dernière copie, ou s'il a disparu, rien n'est écrit ;
  la ligne passe à « à arbitrer » et la personne choisit. Un contrôle périodique détecte aussi les changements sans modification locale.
- Aucun appel réseau pendant une écriture SQLite ; l'état local est écrit après l'appel, et une modification survenue pendant l'appel
  est reprise au passage suivant.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json

from .common import Stop

SCHEMA = '''
CREATE TABLE IF NOT EXISTS agenda_distant5688(ref TEXT NOT NULL, provider TEXT NOT NULL, account TEXT NOT NULL DEFAULT '', remote_id TEXT NOT NULL DEFAULT '',
  ical_uid TEXT NOT NULL DEFAULT '', etag TEXT NOT NULL DEFAULT '', signature TEXT NOT NULL DEFAULT '', generation INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 1, force_write INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
  next_try TEXT NOT NULL DEFAULT '', checked TEXT NOT NULL DEFAULT '', distant_json TEXT NOT NULL DEFAULT '{}', updated TEXT NOT NULL,
  PRIMARY KEY(ref, provider));
CREATE INDEX IF NOT EXISTS agenda_distant5688_state ON agenda_distant5688(state, next_try);
'''
PROVIDERS = {'outlook': 'Outlook', 'google': 'Google Agenda'}
STATES = {'a_envoyer': 'copie à faire', 'a_annuler': 'annulation à transmettre', 'envoye': 'copié', 'annule': 'annulé dans l’agenda distant',
          'erreur': 'erreur, à reprendre', 'conflit': 'modifié dans l’agenda distant : à arbitrer', 'supprime_distant': 'supprimé dans l’agenda distant : à arbitrer',
          'exclu': 'non transmis (dossier exclu ou réservé)', 'detache': 'suivi arrêté'}
PENDING = ('a_envoyer', 'a_annuler')
DETAILS = 'agenda5688:details'
MAX_ATTEMPTS = 8
VERIFY_EVERY = 900


def schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def _now():
    return datetime.now(timezone.utc)


def details_allowed(desk):
    return bool(desk.settings(DETAILS, False))


def set_details(desk, value, owner):
    desk.setting(DETAILS, bool(value))
    desk.audit('propagation5688_detail', {'transmis': bool(value), 'par': owner})
    return {'message': 'Le détail de l’audience sera transmis aux agendas.' if value else 'Seul un intitulé neutre sera transmis aux agendas.'}


# ------------------------------------------------------------------------------------------------ événements à copier
def _times(desk, item):
    from .microsoft5636 import civil_days
    if item['all_day']:
        first, after = civil_days(desk, item)
        return first.isoformat(), after.isoformat()
    return item['starts'], item['ends']


def _instant(value):
    text = str(value or '')
    if len(text) == 10:
        return text
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:   # forme inattendue d'un service distant : comparée telle quelle, jamais bloquante
        return text
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def signature(start, end, title, location):
    return hashlib.sha256(json.dumps([_instant(start), _instant(end), str(title or '').strip(), str(location or '').strip()], ensure_ascii=False).encode()).hexdigest()


def source(desk, ref):
    """Ce qui doit être copié, lu dans l'agenda et la fiche d'audience ; None si l'événement n'existe plus."""
    from . import audiences5688 as au
    au.schema(desk)
    if ref.startswith('aud:'):
        row = desk.db.execute(au.SELECT + ' WHERE h.id=?', (ref[4:],)).fetchone()
        hearing = dict(row) if row else None
        event = None
    else:
        row = desk.db.execute('SELECT * FROM agenda5634 WHERE id=?', (ref,)).fetchone()
        event = dict(row) if row else None
        found = desk.db.execute(au.SELECT + ' WHERE h.event_id=?', (ref,)).fetchone()
        hearing = dict(found) if found else None
    base = event or hearing
    if not base or not base['starts'] or not base['ends']:
        return None
    active = (event['status'] == 'actif') if event else True
    if hearing and hearing['statut'] in ('annulee', 'ecartee'):
        active = False
    return {'ref': ref, 'id': (event or {}).get('id') or ref, 'hearing': hearing, 'title': base['title'], 'description': (event or {}).get('description') or '',
            'location': base['location'], 'starts': base['starts'], 'ends': base['ends'], 'all_day': bool(base['all_day']), 'matter': base['matter'], 'active': active}


def blocked(desk, item):
    """Dossier exclu de la transmission externe ou réservé : aucune donnée ne quitte le cabinet."""
    matter = item['matter']
    if not matter:
        return False
    from .hybrid400 import policy
    from .membres5650 import load
    try:
        return matter in policy(desk.c)['excluded_matters'] or matter in load(desk.c['state_dir'])['reserves']
    except Stop:
        return True


def content(desk, item):
    """Intitulé, lieu et texte transmis. Une audience reste neutre sans décision du cabinet ; les notes ne sont jamais transmises."""
    hearing = item['hearing']
    if not hearing:   # rendez-vous déjà copié dans Outlook : il garde le contenu de sa copie (5.6.36)
        return {'title': item['title'][:250], 'location': item['location'], 'text': item['description'][:1900]}
    if details_allowed(desk):
        from .audiences5688 import TYPES
        lines = [hearing['juridiction'], hearing['chambre'], ('RG ' + hearing['rg']) if hearing['rg'] else '', TYPES.get(hearing['type_audience'], '')]
        return {'title': item['title'][:250], 'location': item['location'], 'text': ' · '.join(x for x in lines if x)[:1900]}
    return {'title': 'Audience AxiorHub' + (' · ' + item['matter'] if item['matter'] else ''), 'location': '', 'text': 'Détails dans AxiorHub.'}


# ------------------------------------------------------------------------------------------------ services
class Outlook:
    name = 'outlook'

    def __init__(self, desk, http=None):
        self.desk, self.http = desk, http

    def available(self, owner=''):
        from . import microsoft5636 as ms
        return ms.connected(self.desk) and bool(ms.config(self.desk).get('push'))

    def ready(self, account):
        return self.available()

    def account(self, owner=''):
        from . import microsoft5636 as ms
        return str(ms.config(self.desk).get('account') or '')

    def known(self, ref):
        """Identifiant d'une copie déjà faite par 5.6.36 : adoptée, jamais recréée."""
        from . import microsoft5636 as ms
        ms.schema(self.desk)
        row = self.desk.db.execute('SELECT graph_id,ical_uid FROM ms_links5636 WHERE local_id=?', (ref,)).fetchone()
        return row['graph_id'] if row and row['graph_id'] else ''

    def _body(self, item, text):
        from . import microsoft5636 as ms
        body = {'subject': text['title'], 'categories': ['AxiorHub'], 'isAllDay': item['all_day'], 'sensitivity': 'private',
                'body': {'contentType': 'text', 'content': text['text'] + '\n\n(Événement créé par AxiorHub Pilote.)'}, 'location': {'displayName': text['location']}}
        body.update(ms.graph_times(self.desk, item))
        return body

    @staticmethod
    def normal(raw):
        if not raw:
            return None
        return {'id': str(raw.get('id') or ''), 'etag': str(raw.get('@odata.etag') or ''), 'ical_uid': str(raw.get('iCalUId') or ''), 'cancelled': bool(raw.get('isCancelled')),
                'start': (raw.get('start') or {}).get('dateTime', ''), 'end': (raw.get('end') or {}).get('dateTime', ''), 'title': raw.get('subject') or '',
                'location': (raw.get('location') or {}).get('displayName', '')}

    def read(self, remote_id, account=''):
        from . import microsoft5636 as ms
        return self.normal(ms.get_remote(self.desk, remote_id, self.http))

    def create(self, item, text, generation, account=''):
        from . import microsoft5636 as ms
        out = ms.create_remote(self.desk, {**self._body(item, text), 'transactionId': ms.transaction_id(item['id'], generation)}, self.http)
        view = self.normal(out)
        if view['id']:   # la lecture d'Outlook (5.6.36) reconnaît ainsi sa propre copie et ne la réimporte pas
            self.desk.db.execute('INSERT OR REPLACE INTO ms_links5636 VALUES(?,?,?,?)', (item['id'], view['id'], view['ical_uid'], _now().isoformat()))
            self.desk.db.commit()
        return view

    def update(self, remote_id, item, text, etag, account=''):
        from . import microsoft5636 as ms
        return self.normal(ms.update_remote(self.desk, remote_id, self._body(item, text), etag, self.http))

    def delete(self, remote_id, etag, account=''):
        from . import microsoft5636 as ms
        ms.delete_remote(self.desk, remote_id, etag, self.http)


class Google:
    name = 'google'

    def __init__(self, desk):
        self.desk = desk

    def _connection(self, owner):
        from . import connexions5652 as cx
        rows = cx._connections_for(self.desk, 'calendar.write', owner or 'cabinet')
        return next((r for r in rows if r['owner'] == owner), rows[0] if rows else None)

    def _stored(self, account):
        """La connexion de la copie, tant qu'elle autorise encore l'écriture : un changement d'avocat ne déplace pas l'événement."""
        from . import connexions5652 as cx
        try:
            row = cx._row(self.desk, account)
        except Stop:
            return None
        enabled = json.loads(row['caps_json'] or '{}').get('calendar.write', {}).get('enabled')
        return row if enabled and not row['disconnected'] else None

    def _call(self, account, **params):
        from . import connexions5652 as cx
        row = self._stored(account)
        if row is None:
            raise Stop('agenda_distant_non_relie')
        return cx.adapter(self.desk, row).call('calendar.write', params)['evenement']

    def available(self, owner=''):
        return self._connection(owner) is not None

    def ready(self, account):
        return self._stored(account) is not None

    def account(self, owner=''):
        row = self._connection(owner)
        return str(row['id']) if row is not None else ''

    def known(self, ref):
        return ''

    def _body(self, desk, item, text):
        first, after = _times(desk, item)
        key = 'date' if item['all_day'] else 'dateTime'
        return {'summary': text['title'], 'description': text['text'], 'location': text['location'], 'start': {key: first}, 'end': {key: after}, 'visibility': 'private',
                'reminders': {'useDefault': False}, 'extendedProperties': {'private': {'axiorhubId': item['id']}}}

    @staticmethod
    def normal(raw):
        if not raw:
            return None
        start, end = raw.get('start') or {}, raw.get('end') or {}
        return {'id': str(raw.get('id') or ''), 'etag': str(raw.get('etag') or ''), 'ical_uid': str(raw.get('iCalUID') or ''), 'cancelled': raw.get('status') == 'cancelled',
                'start': start.get('dateTime') or start.get('date') or '', 'end': end.get('dateTime') or end.get('date') or '', 'title': raw.get('summary') or '',
                'location': raw.get('location') or ''}

    def read(self, remote_id, account=''):
        return self.normal(self._call(account, action='lire', id=remote_id))

    def create(self, item, text, generation, account=''):
        ident = 'a5688' + hashlib.sha256(('%s|%d' % (item['id'], generation)).encode()).hexdigest()[:40]   # base32hex : seuls a-v et 0-9 sont admis
        return self.normal(self._call(account, action='creer', id=ident, corps=self._body(self.desk, item, text)))

    def update(self, remote_id, item, text, etag, account=''):
        return self.normal(self._call(account, action='modifier', id=remote_id, etag=etag, corps=self._body(self.desk, item, text)))

    def delete(self, remote_id, etag, account=''):
        self._call(account, action='supprimer', id=remote_id, etag=etag)


def services(desk, http=None):
    return {'outlook': Outlook(desk, http), 'google': Google(desk)}


def _lawyer(item):
    return str((item.get('hearing') or {}).get('avocat') or 'cabinet') if item else 'cabinet'


# ------------------------------------------------------------------------------------------------ planification (après décision humaine)
def _rows(desk, ref):
    schema(desk)
    return {r['provider']: dict(r) for r in desk.db.execute('SELECT * FROM agenda_distant5688 WHERE ref=?', (ref,))}


def _set(desk, ref, provider, **fields):
    if not fields:
        return
    fields['updated'] = desk.now()
    desk.db.execute('UPDATE agenda_distant5688 SET %s WHERE ref=? AND provider=?' % ','.join(k + '=?' for k in fields), (*fields.values(), ref, provider))
    desk.db.commit()


def plan(desk, ref, action='envoyer', svc=None, provider_only=None):
    """Prépare la copie ; le réseau n'est appelé que par ``run``. Une ligne à arbitrer le reste : une modification locale ne tranche pas le conflit."""
    item = source(desk, ref)
    if item is None:
        return 0
    svc = svc or services(desk)
    known = _rows(desk, ref)
    owner = _lawyer(item)
    if not item['active']:
        action = 'annuler'
    count = 0
    for name, service in svc.items():
        if provider_only and name != provider_only:
            continue
        row = known.get(name)
        if row is None and (action == 'annuler' or not service.available(owner)):
            continue
        state = 'a_annuler' if action == 'annuler' else 'a_envoyer'
        if blocked(desk, item) and action != 'annuler':
            state = 'exclu'
        if row is None:
            desk.db.execute('INSERT OR IGNORE INTO agenda_distant5688(ref,provider,account,state,updated) VALUES(?,?,?,?,?)',
                            (ref, name, service.account(owner), state, desk.now()))
            desk.db.commit()
        elif row['state'] in ('conflit', 'supprime_distant', 'detache'):
            _set(desk, ref, name, seq=row['seq'] + 1)
            continue
        elif row['state'] == 'annule' and action == 'annuler':
            continue
        else:
            _set(desk, ref, name, state=state, seq=row['seq'] + 1, attempts=0, next_try='', error='')
        count += state in PENDING
    return count


def _kick(desk, priority=20):
    """Travail mis en file ; en cas de pause, la copie reste planifiée et repart ensuite."""
    try:
        desk.enqueue('propagation5688', {}, priority=priority)
    except Stop:
        pass


def follow(desk, event_id, action='envoyer'):
    """Après un enregistrement dans l'agenda : seulement une audience demandée par une personne, ou un rendez-vous déjà copié dans Outlook."""
    from . import audiences5688 as au
    from . import microsoft5636 as ms
    schema(desk)
    hearing = au.by_event(desk, event_id)
    if hearing:
        if not hearing['propagate']:
            return 0
        count = plan(desk, event_id, action)
    else:
        ms.schema(desk)
        linked = desk.db.execute('SELECT 1 FROM ms_links5636 WHERE local_id=?', (event_id,)).fetchone()
        if not linked or not Outlook(desk).available():
            return 0
        count = plan(desk, event_id, action, provider_only='outlook')
    if count:
        _kick(desk)
    return count


def follow_hearing(desk, ident):
    row = desk.db.execute('SELECT id,event_id,propagate FROM audiences5688 WHERE id=?', (ident,)).fetchone()
    if not row or not row['propagate']:
        return {'message': 'Copie non demandée pour cette audience.'}
    count = plan(desk, ref_of(dict(row)))
    if count:
        _kick(desk)
    return {'message': 'Copie vers les agendas programmée.' if count else 'Aucun agenda distant à mettre à jour.'}


def ref_of(hearing):
    return hearing['event_id'] or 'aud:' + hearing['id']


# ------------------------------------------------------------------------------------------------ exécution (travail de fond)
def _conflict(desk, row, remote, state='conflit'):
    item = source(desk, row['ref'])
    _set(desk, row['ref'], row['provider'], state=state, error='', attempts=0, distant_json=json.dumps(remote or {}, ensure_ascii=False))
    desk.audit('propagation5688_a_arbitrer', {'ref': row['ref'], 'provider': row['provider'], 'state': state})
    from . import rappels5688
    hearing = (item or {}).get('hearing') or {}
    rappels5688.alert(desk, hearing.get('avocat') or 'cabinet', 'Agenda %s : un événement a changé ou disparu. Rien n’a été écrasé : à arbitrer.' % PROVIDERS[row['provider']],
                      (item or {}).get('matter', ''), '/audiences?onglet=suivi&id=' + str(hearing.get('id') or ''), 'distant-%s-%s-%s' % (row['ref'], row['provider'], row['seq']))


def _changed(row, remote):
    """Vrai si l'événement distant diffère de la dernière version connue ; un simple changement d'étiquette (rappel, couleur) ne compte pas."""
    if not row['signature']:
        return False
    return row['etag'] != remote['etag'] and row['signature'] != signature(remote['start'], remote['end'], remote['title'], remote['location'])


def _record(desk, row, remote, state=None):
    """Écriture après l'appel : si l'audience a encore changé pendant l'appel, la ligne reste à copier."""
    current = desk.db.execute('SELECT seq,state FROM agenda_distant5688 WHERE ref=? AND provider=?', (row['ref'], row['provider'])).fetchone()
    again = bool(current) and current['seq'] != row['seq']
    fields = {'remote_id': remote['id'], 'ical_uid': remote['ical_uid'], 'etag': remote['etag'], 'force_write': 0, 'error': '', 'attempts': 0, 'next_try': '',
              'checked': desk.now(), 'distant_json': '{}', 'signature': signature(remote['start'], remote['end'], remote['title'], remote['location'])}
    fields['state'] = current['state'] if again else (state or 'envoye')
    _set(desk, row['ref'], row['provider'], **fields)


def _process(desk, row, service):
    ref, name, account = row['ref'], row['provider'], row['account']
    if not service.ready(account):   # réglage retiré entre-temps : la décision humaine ne vaut plus, rien ne part
        raise Stop('agenda_distant_non_relie')
    item = source(desk, ref)
    cancel = row['state'] == 'a_annuler' or item is None or not item['active']
    remote_id = row['remote_id'] or service.known(ref)
    if cancel:
        if not remote_id:
            _set(desk, ref, name, state='annule', error='')
            return 'annule'
        remote = service.read(remote_id, account)
        if remote is None or remote['cancelled']:
            _set(desk, ref, name, state='annule', error='')
            return 'annule'
        if not row['force_write'] and _changed(row, remote):
            _conflict(desk, row, remote)
            return 'conflit'
        service.delete(remote_id, remote['etag'], account)
        _set(desk, ref, name, state='annule', error='', force_write=0)
        return 'annule'
    if blocked(desk, item):
        _set(desk, ref, name, state='exclu')
        return 'exclu'
    text = content(desk, item)
    if not remote_id:
        _record(desk, row, service.create(item, text, row['generation'], account))
        return 'envoye'
    remote = service.read(remote_id, account)
    if remote is None or remote['cancelled']:
        _conflict(desk, row, remote, 'supprime_distant')
        return 'supprime_distant'
    if not row['signature']:   # copie 5.6.36 adoptée : l'état distant lu devient la référence, le contenu d'AxiorHub est ensuite appliqué
        row = {**row, 'signature': signature(remote['start'], remote['end'], remote['title'], remote['location']), 'etag': remote['etag']}
    if not row['force_write'] and _changed(row, remote):
        _conflict(desk, row, remote)
        return 'conflit'
    _record(desk, row, service.update(remote_id, item, text, remote['etag'], account))
    return 'envoye'


def _failed(desk, row, code):
    attempts = row['attempts'] + 1
    wait = min(3600, 60 * 2 ** min(attempts, 6))
    state = 'erreur' if attempts >= MAX_ATTEMPTS else row['state']
    _set(desk, row['ref'], row['provider'], state=state, attempts=attempts, error=code[:120], next_try=(_now() + timedelta(seconds=wait)).isoformat())
    return 'reessai'


def run(desk, svc=None, limit=20, now=None):
    """Copies en attente, une par une, hors transaction ; une erreur n'arrête pas les autres et sera reprise."""
    schema(desk)
    svc = svc or services(desk)
    stamp = (now or _now()).isoformat()
    if desk.db.in_transaction:
        desk.db.commit()
    rows = [dict(r) for r in desk.db.execute("SELECT * FROM agenda_distant5688 WHERE state IN ('a_envoyer','a_annuler') AND next_try<=? ORDER BY updated LIMIT ?", (stamp, int(limit)))]
    out = {}
    for row in rows:
        try:
            result = _process(desk, row, svc[row['provider']])
        except Stop as ex:
            code = str(ex).split(':')[0]
            if code == 'http_412':   # changé entre la lecture et l'écriture : rien n'a été écrit
                _conflict(desk, row, {})
                result = 'conflit'
            else:
                result = _failed(desk, row, code)
        out[result] = out.get(result, 0) + 1
    return out


def verify(desk, svc=None, limit=20, now=None):
    """Contrôle périodique des copies à venir : une modification distante apparaît même si rien n'a changé dans AxiorHub."""
    schema(desk)
    svc = svc or services(desk)
    moment = (now or _now()).isoformat()
    rows = [dict(r) for r in desk.db.execute("SELECT * FROM agenda_distant5688 WHERE state='envoye' AND remote_id<>'' ORDER BY checked LIMIT ?", (int(limit) * 3,))]
    found = 0
    done = 0
    for row in rows:
        item = source(desk, row['ref'])
        if item is None or item['starts'] < moment or done >= limit:
            continue
        done += 1
        try:
            remote = svc[row['provider']].read(row['remote_id'], row['account'])
        except Stop:
            continue
        if remote is None or remote['cancelled']:
            _conflict(desk, row, remote, 'supprime_distant')
            found += 1
        elif _changed(row, remote):
            _conflict(desk, row, remote)
            found += 1
        else:
            _set(desk, row['ref'], row['provider'], checked=desk.now(), etag=remote['etag'])
    return {'verifiees': done, 'a_arbitrer': found}


def job(desk, args):
    out = run(desk)
    stamp = _now().timestamp()
    if stamp - float(desk.settings('propagation5688:verifie', 0) or 0) >= VERIFY_EVERY:
        desk.setting('propagation5688:verifie', stamp)
        out['controle'] = verify(desk)
    return out


def schedule(desk, stamp=None):
    """Surveillance : reprise des copies en attente et contrôle périodique, sans travail si rien n'est suivi."""
    schema(desk)
    stamp = stamp or _now().timestamp()
    if stamp - float(desk.settings('propagation5688:last', 0) or 0) < 300:
        return []
    moment = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
    due = desk.db.execute("SELECT 1 FROM agenda_distant5688 WHERE state IN ('a_envoyer','a_annuler') AND next_try<=? LIMIT 1", (moment,)).fetchone()
    follows = desk.db.execute("SELECT 1 FROM agenda_distant5688 WHERE state='envoye' LIMIT 1").fetchone()
    if not due and not (follows and stamp - float(desk.settings('propagation5688:verifie', 0) or 0) >= VERIFY_EVERY):
        return []
    desk.setting('propagation5688:last', stamp)
    try:
        return [desk.enqueue('propagation5688', {}, priority=60)]
    except Stop:
        return []


# ------------------------------------------------------------------------------------------------ arbitrage
def status(desk, ref):
    out = []
    for name, row in sorted(_rows(desk, ref).items()):
        try:
            remote = json.loads(row['distant_json'] or '{}')
        except ValueError:
            remote = {}
        out.append({'provider': name, 'label': PROVIDERS[name], 'state': row['state'], 'state_label': STATES.get(row['state'], row['state']), 'error': row['error'],
                    'attempts': row['attempts'], 'distant': remote or {}})
    return out


def resolve(desk, ident, provider, choice, owner='cabinet'):
    """Décision d'une personne sur une ligne à arbitrer ou en erreur."""
    from . import audiences5688 as au
    hearing = au.get(desk, ident)
    ref = ref_of(hearing)
    row = _rows(desk, ref).get(str(provider))
    if not row:
        raise Stop('copie_distante_absente')
    if choice == 'garder_axiorhub':
        if row['state'] not in ('conflit',):
            raise Stop('arbitrage_invalide')
        _set(desk, ref, row['provider'], state='a_annuler' if hearing['statut'] == 'annulee' else 'a_envoyer', force_write=1, next_try='', attempts=0, error='')
        message = 'La version d’AxiorHub remplacera celle de l’agenda distant.'
    elif choice == 'reprendre_distant':
        if row['state'] != 'conflit' or hearing['lecture_seule']:
            raise Stop('arbitrage_invalide')
        _take_remote(desk, hearing, row)
        message = 'La version de l’agenda distant a été reprise dans AxiorHub.'
    elif choice == 'recreer':
        if row['state'] != 'supprime_distant':
            raise Stop('arbitrage_invalide')
        _set(desk, ref, row['provider'], state='a_envoyer', remote_id='', ical_uid='', etag='', signature='', generation=row['generation'] + 1, next_try='', attempts=0, error='')
        if row['provider'] == 'outlook':
            desk.db.execute('DELETE FROM ms_links5636 WHERE local_id=?', (ref,))
            desk.db.commit()
        message = 'L’événement sera recréé dans l’agenda distant.'
    elif choice == 'reprendre':
        if row['state'] != 'erreur':
            raise Stop('arbitrage_invalide')
        _set(desk, ref, row['provider'], state='a_annuler' if hearing['statut'] == 'annulee' else 'a_envoyer', next_try='', attempts=0, error='')
        message = 'Copie relancée.'
    elif choice == 'arreter':
        _set(desk, ref, row['provider'], state='detache', error='')
        message = 'Cette audience n’est plus suivie dans cet agenda ; l’événement distant n’a pas été touché.'
    else:
        raise Stop('arbitrage_invalide')
    desk.audit('propagation5688_arbitrage', {'ref': ref, 'provider': row['provider'], 'choix': choice, 'par': owner})
    _kick(desk)
    return {'message': message}


def _take_remote(desk, hearing, row):
    from . import agenda5634
    remote = json.loads(row['distant_json'] or '{}')
    if not remote.get('start') or not remote.get('end'):
        raise Stop('version_distante_inconnue')
    begins, ends = datetime.fromisoformat(_instant(remote['start']).replace('Z', '+00:00')), datetime.fromisoformat(_instant(remote['end']).replace('Z', '+00:00'))
    change = {'id': hearing['event_id'], 'start': begins.isoformat(), 'duration_minutes': max(5, min(1440, int((ends - begins).total_seconds() // 60)))}
    if details_allowed(desk) and remote.get('location'):
        change['location'] = remote['location']
    agenda5634.update(desk, change)
    _set(desk, row['ref'], row['provider'], state='envoye', etag=str(remote.get('etag') or ''), distant_json='{}', error='',
         signature=signature(remote['start'], remote['end'], remote.get('title'), remote.get('location')))


def overview(desk):
    """État des deux agendas pour la page des audiences (aucun secret)."""
    from . import connexions5652 as cx
    from . import microsoft5636 as ms
    schema(desk)
    counts = {}
    for r in desk.db.execute('SELECT provider,state,COUNT(*) AS n FROM agenda_distant5688 GROUP BY provider,state'):
        counts.setdefault(r['provider'], {})[r['state']] = r['n']
    try:
        google = bool(cx._connections_for(desk, 'calendar.write', 'cabinet'))
    except Exception:   # noqa: BLE001
        google = False
    return {'outlook': {'label': 'Outlook', 'actif': ms.connected(desk) and bool(ms.config(desk).get('push')), 'compte': str(ms.config(desk).get('account') or ''), 'etats': counts.get('outlook', {})},
            'google': {'label': 'Google Agenda', 'actif': google, 'compte': '', 'etats': counts.get('google', {})}, 'detail': details_allowed(desk)}
