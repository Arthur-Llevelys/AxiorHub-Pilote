"""5.6.32 : réglages de la messagerie (Courriels › Boîte de réception) — signature, police d'écriture et règles.

- Signature mise en forme ajoutée aux nouveaux messages, aux réponses et aux transferts (choix par cas). Elle est insérée dans
  l'éditeur à l'ouverture : l'avocat la voit et peut la modifier avant l'envoi. Par défaut, la signature texte des réglages de
  messagerie (celle des brouillons de l'agent) est reprise.
- Police et taille : appliquées à l'éditeur et au message envoyé (style posé par le serveur, liste fermée de polices).
- Règle « Cci » : adresses mises en copie cachée de chaque message (nouveaux, réponses, transferts, brouillons préparés par l'agent).
  Dans l'éditeur, le champ Cci est prérempli et visible : l'avocat peut retirer une adresse pour un message donné.
- Règle « Déplacer » : si l'objet, l'expéditeur ou un destinataire contient tel texte, le message de la boîte de réception est déplacé
  dans tel sous-dossier. Seuls les messages déjà lus par l'agent (ou reçus depuis plus de 15 minutes) sont déplacés : aucun courriel
  n'échappe à l'analyse. Chaque déplacement est journalisé ; rien n'est supprimé.
"""
import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .common import Stop, private_json

FONTS = {'arial': ('Arial', 'Arial, Helvetica, sans-serif'), 'helvetica': ('Helvetica', 'Helvetica, Arial, sans-serif'),
         'times': ('Times New Roman', '"Times New Roman", Times, serif'), 'georgia': ('Georgia', 'Georgia, serif'),
         'garamond': ('Garamond', 'Garamond, "EB Garamond", Georgia, serif'), 'calibri': ('Calibri', 'Calibri, Carlito, Arial, sans-serif'),
         'cambria': ('Cambria', 'Cambria, Caladea, Georgia, serif'), 'verdana': ('Verdana', 'Verdana, Geneva, sans-serif'),
         'tahoma': ('Tahoma', 'Tahoma, Verdana, sans-serif'), 'trebuchet': ('Trebuchet MS', '"Trebuchet MS", Arial, sans-serif'),
         'courier': ('Courier New', '"Courier New", Courier, monospace')}
SIZES = (9, 10, 11, 12, 13, 14, 16)
MODES = {'nouveau': 'Nouveaux messages', 'repondre': 'Réponses', 'transferer': 'Transferts', 'agent': 'Brouillons préparés par l’agent'}
FIELDS = {'subject': 'l’objet', 'from': 'l’expéditeur', 'to': 'un destinataire (À ou Cc)', 'any': 'l’expéditeur ou un destinataire'}
MAX_RULES = 50
MIRROR = 'courrier5632-cci.json'
EMAIL = re.compile(r'[^\s@,;<>"]+@[^\s@,;<>"]+\.[^\s@,;<>"]+')
SETTLE_MINUTES = 15


def schema(desk):
    desk.db.execute('''CREATE TABLE IF NOT EXISTS mailrules5632(id TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL, data TEXT NOT NULL,
                       enabled INTEGER NOT NULL DEFAULT 1, position INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL, updated TEXT NOT NULL)''')
    desk.db.execute('''CREATE TABLE IF NOT EXISTS mailrule_log5632(at TEXT NOT NULL, rule TEXT NOT NULL, uid TEXT NOT NULL, validity TEXT NOT NULL,
                       subject TEXT NOT NULL, target TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY(validity, uid, rule))''')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _fold(text):
    text = unicodedata.normalize('NFKD', str(text or '')).encode('ascii', 'ignore').decode().casefold()
    return re.sub(r'\s+', ' ', text).strip()


# ------------------------------------------------------------------------------------------- signature et police
def _default_signature(desk):
    from .webmail5628 import text_to_html
    text = str(desk.c.get('mail', {}).get('signature') or '').strip()
    return text_to_html(text) if text else ''


def prefs(desk):
    raw = desk.settings('courrier5632:prefs', None) or {}
    sig = raw.get('signature_html')
    return {'signature_html': _default_signature(desk) if sig is None else sig,
            'sign': {m: bool(raw.get('sign', {}).get(m, True)) for m in ('nouveau', 'repondre', 'transferer')},
            'font': raw.get('font') if raw.get('font') in FONTS else 'arial', 'size': raw.get('size') if raw.get('size') in SIZES else 11}


def save_prefs(desk, data):
    from .webmail5628 import clean_html
    sig = clean_html(str(data.get('signature_html') or '')[:20000])
    if len(sig) > 10000:
        raise Stop('signature_trop_longue')
    font = str(data.get('font') or 'arial')
    if font not in FONTS:
        raise Stop('police_inconnue')
    try:
        size = int(data.get('size') or 11)
    except (TypeError, ValueError):
        raise Stop('taille_police_invalide') from None
    if size not in SIZES:
        raise Stop('taille_police_invalide')
    value = {'signature_html': sig, 'font': font, 'size': size,
             'sign': {m: data.get('sign_' + m) in (True, 'true', '1', 'on') for m in ('nouveau', 'repondre', 'transferer')}}
    desk.setting('courrier5632:prefs', value)
    desk.audit('courrier5632_reglages', {'font': font, 'size': size, 'signature': bool(sig), 'sign': value['sign']})
    return {**prefs(desk), 'message': 'Réglages de la messagerie enregistrés.'}


def font_css(desk):
    p = prefs(desk)
    return 'font-family:%s;font-size:%dpt' % (FONTS[p['font']][1], p['size'])


def signature_for(desk, mode):
    p = prefs(desk)
    key = 'repondre' if mode in ('repondre', 'repondre_tous') else mode
    return p['signature_html'] if p['sign'].get(key) and p['signature_html'] else ''


def wrap(desk, html):
    """Message envoyé dans la police choisie (le style est posé ici, jamais repris du navigateur)."""
    return '<div style="%s">%s</div>' % (font_css(desk).replace('"', '&quot;'), html)


# ------------------------------------------------------------------------------------------- règles
def _addresses(value):
    items = value if isinstance(value, list) else re.split(r'[\s,;]+', str(value or ''))
    out = []
    for a in items:
        a = str(a).strip().strip('<>')
        if not a:
            continue
        if not EMAIL.fullmatch(a) or len(a) > 254:
            raise Stop('adresse_cci_invalide')
        if a.lower() not in [x.lower() for x in out]:
            out.append(a)
    return out[:20]


def rules(desk, kind=None):
    schema(desk)
    rows = desk.db.execute('SELECT * FROM mailrules5632 %s ORDER BY position, created' % ('WHERE kind=?' if kind else ''), (kind,) if kind else ()).fetchall()
    return [{'id': r['id'], 'kind': r['kind'], 'label': r['label'], 'enabled': bool(r['enabled']), **json.loads(r['data'])} for r in rows]


def describe(rule):
    if rule['kind'] == 'cci':
        return 'Mettre en copie cachée %s — %s' % (', '.join(rule['addresses']), ', '.join(MODES[m].lower() for m in rule['scope']))
    joint = ' et ' if rule['match'] == 'all' else ' ou '
    conds = joint.join('%s contient « %s »' % (FIELDS[c['field']], c['value']) for c in rule['conditions'])
    return 'Si %s : déplacer dans « %s »' % (conds, rule['folder'])


def save_rule(desk, data, folders=None):
    """Crée ou modifie une règle ; ``folders`` (noms IMAP existants) sert à refuser un dossier inconnu."""
    schema(desk)
    kind = str(data.get('kind') or '')
    rid = str(data.get('id') or '')
    if kind == 'cci':
        scope = [m for m in MODES if data.get('scope_' + m) in (True, 'true', '1', 'on')] or list(MODES)
        body = {'addresses': _addresses(data.get('addresses')), 'scope': scope}
        if not body['addresses']:
            raise Stop('adresse_cci_requise')
    elif kind == 'deplacer':
        conds = []
        for i in range(5):
            value = re.sub(r'\s+', ' ', str(data.get('cond_value_%d' % i) or '')).strip()[:200]
            field = str(data.get('cond_field_%d' % i) or 'subject')
            if not value:
                continue
            if field not in FIELDS:
                raise Stop('condition_regle_invalide')
            conds.append({'field': field, 'value': value})
        if not conds:
            raise Stop('condition_regle_requise')
        folder = str(data.get('folder') or '').strip()
        if not folder or len(folder) > 200 or any(ch in folder for ch in '\r\n\x00*%'):
            raise Stop('dossier_messagerie_invalide')
        if folders is not None and folder not in folders:
            raise Stop('dossier_messagerie_introuvable')
        if folder.upper() == 'INBOX':
            raise Stop('dossier_messagerie_invalide')
        body = {'conditions': conds, 'match': 'any' if data.get('match') == 'any' else 'all', 'folder': folder}
    else:
        raise Stop('type_de_regle_inconnu')
    now = _now()
    if rid:
        if not desk.db.execute('SELECT 1 FROM mailrules5632 WHERE id=?', (rid,)).fetchone():
            raise Stop('regle_absente')
        desk.db.execute('UPDATE mailrules5632 SET kind=?, data=?, updated=? WHERE id=?', (kind, json.dumps(body, ensure_ascii=False), now, rid))
    else:
        if desk.db.execute('SELECT count(*) FROM mailrules5632').fetchone()[0] >= MAX_RULES:
            raise Stop('trop_de_regles')
        rid = 'r' + os.urandom(6).hex()
        pos = desk.db.execute('SELECT COALESCE(MAX(position),0)+1 FROM mailrules5632').fetchone()[0]
        desk.db.execute('INSERT INTO mailrules5632 VALUES(?,?,?,?,1,?,?,?)', (rid, kind, '', json.dumps(body, ensure_ascii=False), pos, now, now))
    rule = {'id': rid, 'kind': kind, 'enabled': True, **body}
    desk.db.execute('UPDATE mailrules5632 SET label=? WHERE id=?', (describe(rule)[:400], rid))
    desk.db.commit()
    _mirror(desk)
    desk.audit('courrier5632_regle', {'id': rid, 'kind': kind})
    return {'rule': rules_by_id(desk)[rid], 'message': 'Règle enregistrée.'}


def rules_by_id(desk):
    return {r['id']: r for r in rules(desk)}


def set_enabled(desk, rid, on):
    schema(desk)
    if not desk.db.execute('UPDATE mailrules5632 SET enabled=?, updated=? WHERE id=?', (1 if on else 0, _now(), rid)).rowcount:
        raise Stop('regle_absente')
    desk.db.commit()
    _mirror(desk)
    return {'id': rid, 'enabled': bool(on), 'message': 'Règle activée.' if on else 'Règle suspendue.'}


def delete_rule(desk, rid):
    schema(desk)
    if not desk.db.execute('DELETE FROM mailrules5632 WHERE id=?', (rid,)).rowcount:
        raise Stop('regle_absente')
    desk.db.commit()
    _mirror(desk)
    desk.audit('courrier5632_regle_supprimee', {'id': rid})
    return {'id': rid, 'message': 'Règle supprimée.'}


def auto_bcc(desk, mode):
    key = 'repondre' if mode in ('repondre', 'repondre_tous') else mode
    out = []
    for r in rules(desk, 'cci'):
        if r['enabled'] and key in r['scope']:
            out += [a for a in r['addresses'] if a.lower() not in [x.lower() for x in out]]
    return out


def _mirror(desk):
    """Copie des adresses « Cci » des brouillons de l'agent, lue par le moteur de courriels (processus séparé, sans base)."""
    try:
        private_json(Path(desk.c['state_dir']) / MIRROR, {'agent': auto_bcc(desk, 'agent'), 'updated': _now()})
    except (OSError, KeyError):
        pass


def add_agent_bcc(state_dir, message):
    """Ajoute au brouillon préparé par l'agent les adresses de la règle « Cci » (sans doublon avec les destinataires)."""
    try:
        data = json.loads((Path(state_dir) / MIRROR).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return message
    from email.utils import getaddresses
    present = {a.lower() for _, a in getaddresses([str(message.get(h, '')) for h in ('To', 'Cc', 'Bcc')])}
    extra = [a for a in data.get('agent', []) if EMAIL.fullmatch(str(a)) and a.lower() not in present]
    if extra:
        current = str(message.get('Bcc', '')).strip()
        del message['Bcc']
        message['Bcc'] = ', '.join(([current] if current else []) + extra)
    return message


# ------------------------------------------------------------------------------------------- déplacement des courriels reçus
def matches(rule, msg):
    from email.utils import getaddresses
    sender = ' '.join(a + ' ' + n for n, a in getaddresses([str(msg.get('From', ''))]))
    dest = ' '.join(a + ' ' + n for n, a in getaddresses([str(msg.get('To', '')), str(msg.get('Cc', ''))]))
    values = {'subject': str(msg.get('Subject', '')), 'from': sender, 'to': dest, 'any': sender + ' ' + dest}
    hits = [_fold(c['value']) in _fold(values[c['field']]) for c in rule['conditions']]
    return all(hits) if rule['match'] == 'all' else any(hits)


def apply_moves(desk, box=None, limit=200, include_old=False, now=None):
    """Applique les règles « Déplacer » aux messages de la boîte de réception déjà lus par l'agent (ou reçus depuis 15 minutes)."""
    schema(desk)
    active = [r for r in rules(desk, 'deplacer') if r['enabled']]
    if not active:
        return {'moved': 0, 'checked': 0, 'message': 'Aucune règle de déplacement active.'}
    from . import mailbox as mbx
    from .state import State
    from .webmail5628 import _move, _open
    cfg = desk.c['mail']
    account = cfg['username'] + '@' + cfg['host']
    state = State(desk.c['state_dir'])
    own = box is None
    box = box or _open(desk)
    moved = checked = 0
    errors = []
    now = now or datetime.now(timezone.utc)
    try:
        inbox = cfg.get('inbox') or 'INBOX'
        criteria = ['UNDELETED'] if include_old else ['UNDELETED', 'SINCE', (now - timedelta(days=7)).strftime('%d-%b-%Y')]
        uids = list(reversed(box.search(inbox, *criteria)))[:limit]
        for uid in uids:
            try:
                mail = box.fetch(inbox, uid, headers_only=True)
            except Stop:
                continue
            checked += 1
            settled = state.get(mail.key(account)) is not None or mail.timestamp <= now - timedelta(minutes=SETTLE_MINUTES)
            if not settled:
                continue   # l'agent n'a pas encore lu ce courriel : il sera déplacé au passage suivant
            rule = next((r for r in active if matches(r, mail.msg)), None)
            if not rule:
                continue
            if desk.db.execute('SELECT 1 FROM mailrule_log5632 WHERE validity=? AND uid=? AND rule=? AND state=?',
                               (mail.uidvalidity, uid, rule['id'], 'deplace')).fetchone():
                continue
            try:
                _move(box, cfg, inbox, uid, rule['folder'])
                state_txt = 'deplace'
                moved += 1
            except Stop as ex:
                state_txt = 'echec:' + str(ex)[:60]
                errors.append(str(ex))
            desk.db.execute('INSERT OR REPLACE INTO mailrule_log5632 VALUES(?,?,?,?,?,?,?)',
                            (_now(), rule['id'], uid, mail.uidvalidity, str(mail.subject)[:200], rule['folder'], state_txt))
            desk.db.commit()
    finally:
        if own:
            box.close()
        state.db.close()
    if moved:
        desk.audit('courrier5632_deplacements', {'moved': moved, 'checked': checked, 'errors': len(errors)})
    return {'moved': moved, 'checked': checked, 'errors': errors[:5],
            'message': '%d courriel(s) déplacé(s) sur %d examiné(s).' % (moved, checked) + (' Échecs : %s.' % ', '.join(sorted(set(errors))[:3]) if errors else '')}


def schedule(desk, stamp):
    """Toutes les 5 minutes, si une règle de déplacement est active (cycle de surveillance)."""
    schema(desk)
    if not desk.db.execute("SELECT 1 FROM mailrules5632 WHERE kind='deplacer' AND enabled=1 LIMIT 1").fetchone():
        return []
    if stamp - float(desk.settings('courrier5632:last', 0) or 0) < 300:
        return []
    desk.setting('courrier5632:last', stamp)
    if desk.db.execute("SELECT 1 FROM jobs WHERE kind='mailrules5632' AND status IN ('pending','running') LIMIT 1").fetchone():
        return []
    return [desk.enqueue('mailrules5632', {}, priority=60)]


def log(desk, limit=20):
    schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT * FROM mailrule_log5632 ORDER BY at DESC LIMIT ?', (limit,))]
