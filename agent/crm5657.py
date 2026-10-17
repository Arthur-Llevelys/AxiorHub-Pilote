"""5.6.57 (audit 5.6.55, § 8.3) : contacts alimentés par la boîte mail, de façon progressive et reprenable.

- Activation explicite dans « Contacts & CRM » : dossiers de messagerie lus (réception et envois par défaut) et période du premier
  passage. Le cabinet autorise l'enregistrement des coordonnées observées sans valider chaque adresse.
- Lecture des SEULS en-têtes (De, À, Cc, Répondre-à, Date, List-Id, Auto-Submitted), par UID croissant et par lots bornés ; un curseur
  durable par dossier (UIDVALIDITY + dernier UID lu) : aucun message n'est relu ; un changement d'UIDVALIDITY relance le dossier depuis
  la période du premier passage, sans doublon (contacts dédupliqués par adresse normalisée).
- Exclus : identités du cabinet (registre unique), adresses de service (no-reply, mailer-daemon, postmaster, bounce…), listes de
  diffusion (List-Id) et messages automatiques. Une présence en copie ne prouve aucun rôle.
- Nouveau contact : catégorie « À classer » (jamais devinée), nom tel qu'il figure dans l'en-tête, provenance « courriel ». Une
  catégorie corrigée par l'avocat n'est jamais remplacée. Lien au dossier seulement si l'adresse y est déjà un correspondant validé.
- Bilan persistant : nouveaux, enrichis, à classer, exclus, erreurs, curseurs et prochaine reprise.
"""
from datetime import date, datetime, timedelta, timezone
from email.utils import getaddresses, parseaddr
from html import escape as e
import json
import re

from .common import Stop, load_matters

SCHEMA = '''
CREATE TABLE IF NOT EXISTS crm57_cursors(folder TEXT PRIMARY KEY, uidvalidity TEXT NOT NULL, last_uid INTEGER NOT NULL DEFAULT 0, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm57_seen(email TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
  messages INTEGER NOT NULL DEFAULT 0, contact TEXT NOT NULL DEFAULT '', excluded TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS crm57_runs(id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT NOT NULL, finished TEXT NOT NULL DEFAULT '', read INTEGER NOT NULL DEFAULT 0,
  created INTEGER NOT NULL DEFAULT 0, enriched INTEGER NOT NULL DEFAULT 0, excluded INTEGER NOT NULL DEFAULT 0, errors INTEGER NOT NULL DEFAULT 0,
  detail TEXT NOT NULL DEFAULT '', more INTEGER NOT NULL DEFAULT 0);
'''
SETTINGS = 'crm5657:collecte'
SERVICE = re.compile(r'^(no[-_.]?reply|donotreply|ne[-_.]?pas[-_.]?repondre|mailer[-_.]?daemon|postmaster|bounces?|notifications?|alertes?|newsletter)'
                     r'([+._-].*)?@', re.I)
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)


def settings(desk):
    raw = desk.settings(SETTINGS, None) or {}
    cfg = desk.c.get('mail', {})
    folders = raw.get('folders') or [cfg.get('inbox') or 'INBOX', cfg.get('sent') or 'Sent']
    return {'enabled': bool(raw.get('enabled')), 'folders': [str(f)[:120] for f in folders][:6], 'days': int(raw.get('days') or 180),
            'batch': max(20, min(int(raw.get('batch') or 200), 500))}


def save_settings(desk, owner, data):
    folders = data.get('folders') or data.get('dossiers') or ''
    if isinstance(folders, str):
        folders = [x.strip() for x in re.split(r'[\n,;]+', folders) if x.strip()]
    try:
        days = int(data.get('days') or data.get('periode') or 180)
    except (TypeError, ValueError):
        raise Stop('periode_invalide') from None
    if not 1 <= days <= 3650:
        raise Stop('periode_invalide')
    value = {'enabled': data.get('enabled', data.get('actif')) in (True, 'true', 'on', '1', 'oui'), 'folders': folders[:6], 'days': days}
    desk.setting(SETTINGS, value)
    desk.audit('crm5657_collecte_reglages', {'actif': value['enabled'], 'dossiers': len(folders), 'par': owner})
    return settings(desk)


def _imap_since(days):
    d = date.today() - timedelta(days=days)
    return '%02d-%s-%d' % (d.day, MONTHS[d.month - 1], d.year)


def _norm(addr):
    return str(addr or '').strip().lower()


def _addresses(msg):
    out = []
    for header in ('From', 'Reply-To', 'To', 'Cc'):
        for name, addr in getaddresses([str(msg.get(header, '') or '')]):
            addr = _norm(addr)
            if '@' in addr:
                out.append((name.strip().strip('"')[:200], addr, header))
    return out


def _excluded(addr, own, msg, header):
    from .crm5636 import EMAIL
    if addr in own:
        return 'adresse_du_cabinet'
    if not EMAIL.match(addr):
        return 'adresse_invalide'
    if SERVICE.match(addr):
        return 'adresse_de_service'
    if header in ('From', 'Reply-To') and (msg.get('List-Id') or msg.get('List-Unsubscribe')):
        return 'liste_de_diffusion'
    if header == 'From' and str(msg.get('Auto-Submitted', 'no')).lower() not in ('', 'no'):
        return 'message_automatique'
    return ''


def _matter_links(desk):
    """Adresse -> [(dossier, rôle)] des correspondants déjà associés (associations validées)."""
    from .crm5636 import import_existing  # noqa: F401 — même table de conversion des rôles
    role_map = {'client': 'client', 'confrere_adverse': 'avocat_adverse', 'juridiction': 'juridiction', 'expert': 'expert',
                'commissaire_justice': 'commissaire'}
    out = {}
    for m in load_matters(desk.c, unfiltered=True):
        for c in m.get('correspondents') or []:
            addr = _norm(c.get('email'))
            if '@' in addr:
                out.setdefault(addr, []).append((str(m['id']), role_map.get(str(c.get('role') or ''), 'autre')))
    return out


def collect(desk, box=None, limit=None):
    """Un passage borné ; renvoie le bilan. Reprend là où le passage précédent s'est arrêté."""
    from . import crm5636 as crm
    from .mailbox import own_addresses
    ensure_schema(desk)
    crm.schema(desk)
    cfg = settings(desk)
    if not cfg['enabled']:
        raise Stop('collecte_contacts_desactivee')
    own = own_addresses(desk.c.get('mail', {}))
    links = _matter_links(desk)
    budget = int(limit or cfg['batch'])
    run = desk.db.execute('INSERT INTO crm57_runs(started) VALUES(?)', (_now(),)).lastrowid
    desk.db.commit()
    stats = {'read': 0, 'created': 0, 'enriched': 0, 'excluded': 0, 'errors': 0, 'more': 0, 'detail': []}
    mailbox = box
    if mailbox is None:
        from .mailbox import Mailbox
        mailbox = Mailbox(desk.c['mail'])
    try:
        for folder in cfg['folders']:
            if budget <= 0:
                stats['more'] = 1
                break
            try:
                validity = mailbox.select(folder)
            except Stop as ex:
                stats['errors'] += 1
                stats['detail'].append('%s : %s' % (folder, str(ex).split(':')[0]))
                continue
            cur = desk.db.execute('SELECT * FROM crm57_cursors WHERE folder=?', (folder,)).fetchone()
            if cur and cur['uidvalidity'] != str(validity):
                stats['detail'].append('%s : UIDVALIDITY modifiée, dossier relu depuis la période du premier passage' % folder)
                cur = None
            last = int(cur['last_uid']) if cur else 0
            uids = mailbox.search(folder, 'UID', '%d:*' % (last + 1)) if last else mailbox.search(folder, 'SINCE', _imap_since(cfg['days']))
            uids = sorted({int(u) for u in uids if str(u).isdigit() and int(u) > last})
            if len(uids) > budget:
                stats['more'] = 1
            for uid in uids[:budget]:
                try:
                    mail = mailbox.fetch(folder, str(uid), headers_only=True)
                except Stop:
                    stats['errors'] += 1
                    last = uid
                    continue
                when = mail.timestamp.astimezone(timezone.utc).isoformat(timespec='seconds') if getattr(mail, 'timestamp', None) else _now()
                for name, addr, header in _addresses(mail.msg):
                    why = _excluded(addr, own, mail.msg, header)
                    seen = desk.db.execute('SELECT * FROM crm57_seen WHERE email=?', (addr,)).fetchone()
                    if why:
                        if not seen:
                            desk.db.execute('INSERT INTO crm57_seen(email,name,first_seen,last_seen,messages,excluded) VALUES(?,?,?,?,1,?)',
                                            (addr, name, when, when, why))
                            stats['excluded'] += 1
                        continue
                    if seen:
                        desk.db.execute('UPDATE crm57_seen SET last_seen=MAX(last_seen,?),messages=messages+1,name=CASE WHEN name=\'\' THEN ? ELSE name END '
                                        'WHERE email=?', (when, name, addr))
                        continue
                    existing = desk.db.execute('SELECT id FROM contacts5636 WHERE email=?', (addr,)).fetchone()
                    if existing:
                        contact = existing['id']
                        stats['enriched'] += 1
                    else:
                        display = name or addr.split('@')[0]
                        contact = crm.save(desk, {'name': display, 'email': addr, 'category': 'a_classer', 'kind': 'personne'}, source='courriel', commit=False)['id']
                        stats['created'] += 1
                    for mid, role in links.get(addr, []):
                        try:
                            crm.link(desk, contact, mid, role, commit=False)
                        except Stop:
                            pass
                    desk.db.execute('INSERT INTO crm57_seen(email,name,first_seen,last_seen,messages,contact) VALUES(?,?,?,?,1,?)',
                                    (addr, name, when, when, contact))
                stats['read'] += 1
                budget -= 1
                last = uid
                if stats['read'] % 50 == 0:                      # curseur durable : une reprise ne relit pas ce lot
                    desk.db.execute('INSERT OR REPLACE INTO crm57_cursors(folder,uidvalidity,last_uid,updated) VALUES(?,?,?,?)',
                                    (folder, str(validity), last, _now()))
                    desk.db.commit()
            desk.db.execute('INSERT OR REPLACE INTO crm57_cursors(folder,uidvalidity,last_uid,updated) VALUES(?,?,?,?)', (folder, str(validity), last, _now()))
            desk.db.commit()
    finally:
        if box is None:
            try:
                mailbox.close()
            except Exception:   # noqa: BLE001
                pass
        desk.db.execute('UPDATE crm57_runs SET finished=?,read=?,created=?,enriched=?,excluded=?,errors=?,detail=?,more=? WHERE id=?',
                        (_now(), stats['read'], stats['created'], stats['enriched'], stats['excluded'], stats['errors'],
                         json.dumps(stats['detail'], ensure_ascii=False)[:2000], stats['more'], run))
        desk.db.commit()
    return stats


def schedule(desk, stamp=None):
    """Cycle de surveillance : un passage au plus toutes les 30 minutes (davantage tant qu'un premier passage n'est pas terminé)."""
    if not settings(desk)['enabled']:
        return []
    stamp = stamp or datetime.now(timezone.utc).timestamp()
    ensure_schema(desk)
    last = desk.db.execute('SELECT more FROM crm57_runs ORDER BY id DESC LIMIT 1').fetchone()
    every = 300 if (last and last['more']) else 1800
    if stamp - float(desk.settings('crm5657:last', 0) or 0) < every:
        return []
    if desk.db.execute("SELECT 1 FROM jobs WHERE kind='crm_collecte5657' AND status IN ('pending','running')").fetchone():
        return []
    desk.setting('crm5657:last', stamp)
    return [desk.enqueue('crm_collecte5657', {}, priority=70)]


def summary(desk):
    ensure_schema(desk)
    last = desk.db.execute('SELECT * FROM crm57_runs ORDER BY id DESC LIMIT 1').fetchone()
    a_classer = desk.db.execute("SELECT COUNT(*) FROM contacts5636 WHERE category='a_classer' AND archived=0").fetchone()[0] if \
        desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='contacts5636'").fetchone() else 0
    return {'settings': settings(desk), 'last': dict(last) if last else None, 'a_classer': a_classer,
            'cursors': [dict(r) for r in desk.db.execute('SELECT * FROM crm57_cursors ORDER BY folder')],
            'excluded': desk.db.execute("SELECT COUNT(*) FROM crm57_seen WHERE excluded<>''").fetchone()[0]}


def panel_html(desk, prefix):
    s = summary(desk)
    cfg, last = s['settings'], s['last']
    bilan = ('Dernier passage le %s : %d message(s) lus, %d contact(s) créé(s), %d enrichi(s), %d adresse(s) exclue(s), %d erreur(s)%s.' % (
        str(last['finished'] or last['started'])[:16].replace('T', ' '), last['read'], last['created'], last['enriched'], last['excluded'], last['errors'],
        ' — suite au prochain passage' if last['more'] else '')) if last else 'Aucun passage pour l’instant.'
    cursors = ''.join('<li>%s : dernier UID lu %s</li>' % (e(c['folder']), e(str(c['last_uid']))) for c in s['cursors'])
    return ('<section class="ax-card" id="crm5657"><h2>Alimenter mes contacts depuis la boîte mail</h2><p class="ax-muted">Lecture des seuls en-têtes, '
            'progressive et sans relecture ; adresses du cabinet, de service et listes exclues ; les nouveaux contacts arrivent « À classer » '
            '(aucun rôle deviné).</p><p>%s</p>%s<p><a href="%s">%d contact(s) à classer</a></p>'
            '<form class="m5-form" data-api="m5657/contacts/reglages" data-reload><label class="r52-day"><input type="checkbox" name="actif"%s> '
            'Alimenter mes contacts depuis cette boîte</label><label>Dossiers de messagerie (un par ligne)<textarea name="dossiers" rows="2" data-no-dictation>%s</textarea></label>'
            '<label>Période du premier passage (jours)<input name="periode" value="%d" inputmode="numeric" size="5"></label>'
            '<button class="ax-btn" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form>%s</section>') % (
        e(bilan), ('<details><summary>Curseurs</summary><ul>%s</ul></details>' % cursors) if cursors else '',
        e(prefix + '/contacts-crm?categorie=a_classer', quote=True), s['a_classer'], ' checked' if cfg['enabled'] else '', e('\n'.join(cfg['folders'])),
        cfg['days'], ('<form class="m5-form m5-inline" data-api="m5657/contacts/collecter" data-reload><button class="ax-btn ghost" type="submit">'
                      'Lancer un passage maintenant</button></form>') if cfg['enabled'] else '')


def post(desk, owner, role, route, data):
    if route == 'contacts/reglages':
        cfg = save_settings(desk, owner, data)
        return {'message': 'Collecte des contacts activée.' if cfg['enabled'] else 'Collecte des contacts désactivée.'}
    if route == 'contacts/collecter':
        if not settings(desk)['enabled']:
            raise Stop('collecte_contacts_desactivee')
        if desk.db.execute("SELECT 1 FROM jobs WHERE kind='crm_collecte5657' AND status IN ('pending','running')").fetchone():
            return {'message': 'Un passage est déjà en cours.'}
        desk.enqueue('crm_collecte5657', {}, priority=20)
        return {'message': 'Passage lancé : le bilan s’affiche à la fin.'}
    raise Stop('route_inconnue')
