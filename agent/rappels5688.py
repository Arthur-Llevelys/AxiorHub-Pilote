"""5.6.88 : les rappels d'audience et de tâche partent seuls, vers la personne concernée.

- Échéances réglables, J-7 et J-1 par défaut : réglage du cabinet, que chacun peut remplacer pour lui-même ; une tâche peut avoir les siennes.
  Les rappels déjà choisis sur un événement (agenda local) restent honorés.
- Destinataire : l'avocat désigné ou le responsable de la tâche. Une audience sans avocat va au compte du cabinet, avec la mention
  « Sans avocat désigné » ; une tâche sans responsable n'envoie rien (elle reste visible dans « Mes tâches »).
- Canaux existants : notification de l'application (fil d'activité et liste de la personne), signal du téléphone jumelé (sans contenu, 4.9.0),
  courriel à la personne si elle l'active. L'adresse est celle de son compte, jamais une adresse saisie : ce rappel interne n'est pas un
  envoi à un tiers. Pour l'adresse du cabinet, il est déposé par IMAP comme les rappels 5.2.0 ; pour une autre adresse de compte, il passe
  par le serveur SMTP déjà réglé pour les mots de passe.
- Sans doublon : un rappel par personne, objet, échéance et date. Si plusieurs échéances sont dépassées, seule la plus proche part.
  Une audience déplacée redonne droit à ses rappels. Un destinataire qui n'a pas accès au dossier réservé ne reçoit rien.
- Dans le fil commun, un dossier réservé n'est jamais nommé.
"""
from datetime import date, datetime, time as dtime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
import json
import uuid

from .common import Stop

DEFAULT_DAYS = [7, 1]
ALLOWED_DAYS = (0, 1, 2, 3, 7, 14, 30)
CABINET = 'rappels5688:echeances'
MORNING_HOUR = 8
MAX_EMAIL_TRIES = 3
KEPT_DAYS = 120
SCHEMA = '''
CREATE TABLE IF NOT EXISTS rappels5688(id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL, offset_min INTEGER NOT NULL, due TEXT NOT NULL,
  message TEXT NOT NULL, matter TEXT NOT NULL DEFAULT '', link TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, read_at TEXT NOT NULL DEFAULT '',
  shown INTEGER NOT NULL DEFAULT 1, push_state TEXT NOT NULL DEFAULT '', email_state TEXT NOT NULL DEFAULT '', email_tries INTEGER NOT NULL DEFAULT 0,
  UNIQUE(owner, kind, ref, offset_min, due));
CREATE INDEX IF NOT EXISTS rappels5688_owner ON rappels5688(owner, read_at, created);
CREATE TABLE IF NOT EXISTS rappels_pref5688(owner TEXT PRIMARY KEY, push INTEGER NOT NULL DEFAULT 1, email INTEGER NOT NULL DEFAULT 0, jours_audience TEXT NOT NULL DEFAULT '',
  jours_tache TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL);
'''


def schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ réglages
def parse_days(value):
    """« 7, 1 » ou [7, 1] ; seules les échéances proposées sont admises."""
    if isinstance(value, str):
        value = [x for x in value.replace(';', ',').split(',') if x.strip()]
    try:
        days = sorted({int(x) for x in (value or [])}, reverse=True)
    except (TypeError, ValueError):
        raise Stop('echeance_rappel_invalide') from None
    if any(d not in ALLOWED_DAYS for d in days) or len(days) > 4:
        raise Stop('echeance_rappel_invalide')
    return days


def cabinet_days(desk):
    raw = desk.settings(CABINET, None) or {}
    return {'audience': list(raw.get('audience', DEFAULT_DAYS)), 'tache': list(raw.get('tache', DEFAULT_DAYS))}


def save_cabinet(desk, data, owner):
    value = {'audience': parse_days(data.get('audience')), 'tache': parse_days(data.get('tache'))}
    desk.setting(CABINET, value)
    desk.audit('rappels5688_echeances', {**value, 'par': owner})
    return {'message': 'Échéances du cabinet enregistrées.'}


def preferences(desk, owner):
    schema(desk)
    row = desk.db.execute('SELECT * FROM rappels_pref5688 WHERE owner=?', (owner,)).fetchone()
    base = cabinet_days(desk)
    if not row:
        return {'push': True, 'email': False, 'audience': base['audience'], 'tache': base['tache'], 'propres': False}
    return {'push': bool(row['push']), 'email': bool(row['email']), 'audience': json.loads(row['jours_audience']) if row['jours_audience'] else base['audience'],
            'tache': json.loads(row['jours_tache']) if row['jours_tache'] else base['tache'], 'propres': bool(row['jours_audience'] or row['jours_tache'])}


def save_preferences(desk, owner, data, auth=None):
    """Chacun règle ses propres rappels ; le courriel exige une adresse connue sur son compte."""
    schema(desk)
    email = data.get('email') in (True, 'true', 'on', '1')
    if email and not address_of(desk, owner, auth):
        raise Stop('adresse_du_compte_absente')
    days = {}
    for key in ('audience', 'tache'):
        raw = data.get(key)
        days[key] = json.dumps(parse_days(raw)) if raw not in (None, '') else ''
    desk.db.execute('INSERT OR REPLACE INTO rappels_pref5688(owner,push,email,jours_audience,jours_tache,updated) VALUES(?,?,?,?,?,?)',
                    (owner, int(data.get('push') in (True, 'true', 'on', '1')), int(email), days['audience'], days['tache'], desk.now()))
    desk.db.commit()
    desk.audit('rappels5688_preferences', {'owner': owner, 'email': email})
    return {'message': 'Vos rappels sont enregistrés.'}


def address_of(desk, owner, auth=None):
    """Adresse du compte de la personne, lue côté serveur ; jamais une valeur transmise par le navigateur."""
    from .reminders520 import own_address
    if owner == 'cabinet':
        return own_address(desk)
    try:
        from . import connexion5648
        db = connexion5648.connect(desk.c['state_dir'])
        try:
            row = db.execute('SELECT ident,email FROM users WHERE ident=? AND active=1', (owner,)).fetchone()
        finally:
            db.close()
        if row:
            return str(row['email'] or (row['ident'] if '@' in row['ident'] else ''))
    except Exception:   # noqa: BLE001 — comptes de la version serveur absents : autre distribution
        pass
    return owner if '@' in str(owner) else ''   # distribution autonome, ExApp : l'identifiant du compte est son adresse


# ------------------------------------------------------------------------------------------------ canaux
def _push(desk, owner):
    """Signal sans contenu vers les téléphones de la personne (un ancien abonnement sans compte ne sert qu'au compte du cabinet)."""
    from . import mobile490
    from .reminders520 import own_address
    mobile490.ensure_schema(desk)
    if not mobile490.vapid_available():
        return 0
    rows = desk.db.execute("SELECT id,endpoint FROM push_subs490 WHERE owner=? OR (owner='' AND ?='cabinet')", (owner, owner)).fetchall()
    subject = 'mailto:' + (own_address(desk) or 'contact@invalid.example')
    sent = 0
    for row in rows:
        try:
            headers = {'Authorization': mobile490.vapid_header(desk, row['endpoint'], subject), 'TTL': '3600', 'Urgency': 'high'}
        except Stop:
            continue
        code = (mobile490.http_post or mobile490._post)(row['endpoint'], headers)
        sent += code in (200, 201, 202)
    return sent


def _email(desk, owner, items, sender=None):
    """Un seul message par passage. Vrai si le message a été remis au serveur ; faux si aucun moyen d'envoi n'est réglé."""
    from .reminders520 import own_address
    to = address_of(desk, owner)
    if not to:
        return False
    subject = 'AxiorHub — %d rappel(s) d’agenda' % len(items)
    body = ['Rappels de votre agenda AxiorHub :', ''] + ['• ' + it['message'] for it in items] + ['', 'Réglage : Mes tâches › Mes rappels.']
    text = '\n'.join(body)
    if sender:
        return bool(sender(to, subject, text))
    cabinet = own_address(desk)
    if to.lower() == cabinet.lower():
        msg = EmailMessage()
        msg['From'], msg['To'], msg['Subject'] = to, to, subject
        msg['Date'] = format_datetime(datetime.now(timezone.utc))
        msg['Message-ID'] = make_msgid(domain=to.rsplit('@', 1)[-1] or 'localhost')
        msg.set_content(text)
        from .mailbox import Mailbox
        box = Mailbox(desk.c['mail'])
        try:
            box.append_reminder(msg)
        finally:
            box.close()
        return True
    from .connexion5648 import send_mail
    return bool(send_mail(desk.c, to, subject, text))


def _feed_text(desk, kind, message, matter):
    """Le fil d'activité est commun : un dossier réservé n'y est jamais nommé."""
    from .membres5650 import load
    try:
        reserved = bool(matter) and matter in load(desk.c['state_dir'])['reserves']
    except Stop:
        reserved = True
    return ('Rappel : %s à venir (dossier réservé).' % ('audience' if kind == 'audience' else 'tâche')) if reserved else message


def _deliver(desk, created, sender=None):
    from .live430 import emit
    pushed = emails = 0
    owners = {}
    for item in created:
        owners.setdefault(item['owner'], []).append(item)
        if item['shown']:
            emit(desk, 'agenda', _feed_text(desk, item['kind'], item['message'], item['matter']), matter=item['matter'], dedupe='rappel5688-' + item['id'])
    for owner, items in owners.items():
        pref = preferences(desk, owner)
        if pref['push']:
            try:
                count = _push(desk, owner)
            except Exception:   # noqa: BLE001 — le signal ne doit jamais bloquer les autres canaux
                count = 0
            pushed += count
            desk.db.executemany('UPDATE rappels5688 SET push_state=? WHERE id=?', [('envoye' if count else 'aucun_appareil', i['id']) for i in items])
        if pref['email']:
            desk.db.executemany("UPDATE rappels5688 SET email_state='a_envoyer' WHERE id=?", [(i['id'],) for i in items])
        desk.db.commit()
    emails = _flush_email(desk, sender)
    return {'push': pushed, 'email': emails}


def _flush_email(desk, sender=None):
    """Courriels en attente, regroupés par personne ; un échec est retenté au passage suivant, trois fois au plus."""
    rows = [dict(r) for r in desk.db.execute("SELECT * FROM rappels5688 WHERE email_state='a_envoyer' AND email_tries<? ORDER BY created", (MAX_EMAIL_TRIES,))]
    by_owner = {}
    for r in rows:
        by_owner.setdefault(r['owner'], []).append(r)
    sent = 0
    for owner, items in by_owner.items():
        try:
            ok = _email(desk, owner, items, sender)
        except Exception as ex:   # noqa: BLE001
            ok = False
            desk.audit('rappels5688_courriel_non_envoye', {'cause': str(ex)[:120]})
        ids = [(i['id'],) for i in items]
        if ok:
            desk.db.executemany("UPDATE rappels5688 SET email_state='envoye' WHERE id=?", ids)
            sent += 1
        else:
            desk.db.executemany("UPDATE rappels5688 SET email_tries=email_tries+1,email_state=CASE WHEN email_tries+1>=? THEN 'echec' ELSE 'a_envoyer' END WHERE id=?",
                                [(MAX_EMAIL_TRIES, i['id']) for i in items])
        desk.db.commit()
    return sent


# ------------------------------------------------------------------------------------------------ calcul
def _pending_offset(desk, due, offsets, owner, kind, ref, now, earlier=()):
    """Plus proche échéance atteinte et pas encore envoyée ; les plus lointaines dépassées sont closes avec elle (aucune rafale)."""
    reached = sorted(m for m in offsets if due - timedelta(minutes=m) <= now)
    sent = {r[0] for r in desk.db.execute('SELECT offset_min FROM rappels5688 WHERE owner=? AND kind=? AND ref=? AND due=?', (owner, kind, ref, due.isoformat()))}
    waiting = [m for m in reached if m not in sent]
    if not waiting:
        return None, []
    nearest = reached[0]
    if nearest in sent or nearest in earlier:   # déjà signalé (ici ou par l'agenda d'avant 5.6.88) : le reste est clos en silence
        return None, waiting
    return nearest, [m for m in waiting if m != nearest]


def _add(desk, owner, kind, ref, offset, due, message, matter, link, shown=1):
    row = {'id': uuid.uuid4().hex, 'owner': owner, 'kind': kind, 'ref': ref, 'offset_min': offset, 'due': due.isoformat(), 'message': message[:600], 'matter': matter,
           'link': link, 'created': desk.now(), 'shown': shown}
    cur = desk.db.execute('INSERT OR IGNORE INTO rappels5688(%s) VALUES(%s)' % (','.join(row), ','.join('?' for _ in row)), tuple(row.values()))
    return row if cur.rowcount else None


def _label(minutes):
    if minutes >= 1440:
        days = minutes // 1440
        return 'dans %d jour%s' % (days, 's' if days > 1 else '')
    return 'dans %d h' % (minutes // 60) if minutes >= 60 else 'dans %d min' % minutes


def _hearings(desk, now, known):
    from . import audiences5688 as au
    from .agenda5634 import _tz
    tz = _tz(desk)
    horizon = (now + timedelta(days=max(ALLOWED_DAYS) + 1)).isoformat()
    created = []
    rows = desk.db.execute(au.SELECT + " WHERE h.statut='a_venir' AND COALESCE(a.starts,h.starts)>? AND COALESCE(a.starts,h.starts)<?", (now.isoformat(), horizon)).fetchall()
    for r in rows:
        item = dict(r)
        owner = item['avocat'] or 'cabinet'
        if not au.can_see(desk, owner, item['matter'], known):
            continue
        begins = datetime.fromisoformat(item['starts'])
        if item['all_day']:   # journée entière : le rappel part le matin, pas à minuit
            begins = datetime.combine(begins.astimezone(tz).date(), dtime(MORNING_HOUR), tz).astimezone(timezone.utc)
        morning = datetime.combine(begins.astimezone(tz).date(), dtime(MORNING_HOUR), tz).astimezone(timezone.utc)
        offsets = {d * 1440 if d else max(0, int((begins - morning).total_seconds() // 60)) for d in preferences(desk, owner)['audience']} - {0}   # J : le matin même
        chosen = json.loads(item['reminders'] or '[]') if item.get('reminders') else []
        offsets |= {int(x) for x in chosen}
        older = {x[0] for x in desk.db.execute('SELECT minutes FROM agenda_rappels5634 WHERE event=?', (item['event_id'],))} if item['event_id'] else set()
        offset, closed = _pending_offset(desk, begins, offsets, owner, 'audience', item['id'], now, older)
        for minutes in closed:
            _add(desk, owner, 'audience', item['id'], minutes, begins, 'Rappel clos sans envoi.', item['matter'], '', shown=0)
        if offset is None:
            continue
        place = ' · '.join(x for x in (item['juridiction'], item['chambre'], ('RG ' + item['rg']) if item['rg'] else '') if x)
        message = 'Audience %s : %s, le %s%s%s' % (_label(offset), item['title'], au.when_label(item['starts'], item['all_day'], tz),
                                                  (' — ' + place) if place else '', '' if item['avocat'] else ' — ' + au.SANS_AVOCAT)
        row = _add(desk, owner, 'audience', item['id'], offset, begins, message, item['matter'], '/audiences?onglet=suivi&id=' + item['id'])
        if row:
            created.append(row)
    return created


def _tasks(desk, now, known):
    from . import audiences5688 as au
    from . import taches5688
    from .agenda5634 import _tz
    tz = _tz(desk)
    created = []
    for t in taches5688.open_assigned(desk):
        try:
            day = date.fromisoformat(str(t['due'])[:10])
        except ValueError:
            continue
        owner = t['responsable']
        if day < now.astimezone(tz).date() or not au.can_see(desk, owner, t['matter'], known):
            continue
        due = datetime.combine(day, dtime(MORNING_HOUR), tz).astimezone(timezone.utc)
        days = taches5688.reminder_days(t) or preferences(desk, owner)['tache']
        offset, closed = _pending_offset(desk, due, {d * 1440 for d in days}, owner, 'tache', t['id'], now)
        for minutes in closed:
            _add(desk, owner, 'tache', t['id'], minutes, due, 'Rappel clos sans envoi.', t['matter'], '', shown=0)
        if offset is None:
            continue
        message = 'Tâche à faire pour le %s : %s' % (day.strftime('%d/%m/%Y'), t['title'][:300])
        row = _add(desk, owner, 'tache', t['id'], offset, due, message, t['matter'], '/mes-taches#tache-' + t['id'])
        if row:
            created.append(row)
    return created


def tick(desk, stamp=None, sender=None):
    """Surveillance : calcule les rappels dus, puis les remet à leurs canaux hors transaction."""
    from . import audiences5688 as au
    from . import taches5688
    schema(desk)
    au.schema(desk)
    taches5688.schema(desk)
    now = datetime.fromtimestamp(stamp, timezone.utc) if stamp else datetime.now(timezone.utc)
    known = au.members(desk)
    created = _hearings(desk, now, known) + _tasks(desk, now, known)
    desk.db.execute('DELETE FROM rappels5688 WHERE created<?', ((now - timedelta(days=KEPT_DAYS)).isoformat(),))   # rétention : la date due est passée depuis longtemps
    desk.db.commit()
    out = {'rappels': len(created), 'push': 0, 'email': 0}
    if created:
        out.update(_deliver(desk, created, sender))
    else:
        out['email'] = _flush_email(desk, sender)
    return out


def alert(desk, owner, message, matter='', link='', key=''):
    """Avis à une personne (agenda distant à arbitrer) : même liste que les rappels, sans courriel ni signal."""
    schema(desk)
    now = datetime.now(timezone.utc)
    row = _add(desk, owner, 'avis', key or uuid.uuid4().hex, 0, now.replace(microsecond=0, second=0, minute=0), message, matter, link)
    if row:
        from .live430 import emit
        desk.db.commit()
        emit(desk, 'agenda', _feed_text(desk, 'audience', message, matter), matter=matter, dedupe='rappel5688-' + row['id'])
    return row


def unread(desk, owner, hidden=frozenset(), limit=30):
    schema(desk)
    rows = desk.db.execute("SELECT * FROM rappels5688 WHERE owner=? AND read_at='' AND shown=1 ORDER BY created DESC LIMIT ?", (owner, int(limit))).fetchall()
    return [dict(r) for r in rows if r['matter'] not in hidden]


def unread_count(desk, owner):
    try:
        return desk.db.execute("SELECT COUNT(*) FROM rappels5688 WHERE owner=? AND read_at='' AND shown=1", (owner,)).fetchone()[0]
    except Exception:   # noqa: BLE001 — table absente avant le premier passage
        return 0


def mark_read(desk, owner, ident=''):
    schema(desk)
    sql, args = "UPDATE rappels5688 SET read_at=? WHERE owner=? AND read_at=''", [desk.now(), owner]
    if ident:
        sql += ' AND id=?'
        args.append(str(ident))
    count = desk.db.execute(sql, args).rowcount
    desk.db.commit()
    return {'marked': count, 'message': 'Rappel lu.' if ident else 'Rappels marqués comme lus.'}
