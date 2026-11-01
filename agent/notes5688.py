"""5.6.88 : note commune de dossier et mentions @personne.

La note est un texte unique par dossier, modifiable par tous ceux qui voient le dossier. Chaque enregistrement crée une version, avec son auteur. Une
écriture part de la version lue : si une autre personne a écrit entre-temps, elle est refusée (``NoteConflit``), le texte de chacun est conservé et
présenté, rien n'est écrasé. Une version ancienne reprise devient une nouvelle version ; ``MAX_VERSIONS`` versions sont gardées par note.

Mentions : « @identifiant » dans une note, un message ou une tâche. La notification passe par le fil du dossier (messagerie interne, pastille « non
lus ») ; une personne qui ne voit pas le dossier n'est jamais notifiée, et le nom d'un dossier réservé ne lui parvient pas. Seules les mentions
nouvelles d'une version notifient. Les tâches viennent d'Aujourd'hui, de Nextcloud ou de l'agent : elles sont relues par la boucle des automatismes
(``scanner_taches``), une notification par tâche et par personne. Aucune donnée n'est donnée au modèle.
"""
from datetime import datetime, timezone
from html import escape as e
import re

from . import messagerie5650 as mx
from .common import Stop, load_matters
from .membres5650 import directory, hidden_for

SCHEMA = '''
CREATE TABLE IF NOT EXISTS note_commune5688(matter TEXT PRIMARY KEY, version INTEGER NOT NULL, texte TEXT NOT NULL, auteur TEXT NOT NULL, auteur_nom TEXT NOT NULL, modifie TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS note_versions5688(matter TEXT NOT NULL, version INTEGER NOT NULL, texte TEXT NOT NULL, auteur TEXT NOT NULL, auteur_nom TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY(matter, version));
CREATE TABLE IF NOT EXISTS mentions_taches5688(tache TEXT NOT NULL, owner TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(tache, owner));
'''
MAX_TEXTE = 20000
MAX_VERSIONS = 200
SCAN_SECONDES = 300
MAX_PROPOSES = 8
AGENT_NOM = 'Agent AxiorHub'


class NoteConflit(Stop):
    """Une autre personne a écrit depuis la version lue : ``actuelle`` porte le texte en place."""

    def __init__(self, actuelle):
        super().__init__('version_en_conflit')
        self.actuelle = actuelle


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_schema(desk):
    if not desk.db.in_transaction:
        desk.db.executescript(SCHEMA)
    mx.ensure_schema(desk)


def _dossier(desk, matter, hidden):
    if str(matter) in hidden:
        raise Stop('dossier_reserve')
    if not any(m['id'] == str(matter) for m in load_matters(desk.c)):
        raise Stop('dossier_absent')
    return str(matter)


def lire(desk, matter, hidden=frozenset()):
    ensure_schema(desk)
    mid = _dossier(desk, matter, hidden)
    row = desk.db.execute('SELECT * FROM note_commune5688 WHERE matter=?', (mid,)).fetchone()
    return dict(row) if row else {'matter': mid, 'version': 0, 'texte': '', 'auteur': '', 'auteur_nom': '', 'modifie': ''}


def versions(desk, matter, hidden=frozenset(), limit=20):
    ensure_schema(desk)
    mid = _dossier(desk, matter, hidden)
    return [dict(r) for r in desk.db.execute('SELECT version,auteur,auteur_nom,at FROM note_versions5688 WHERE matter=? ORDER BY version DESC LIMIT ?', (mid, int(limit)))]


def version(desk, matter, numero, hidden=frozenset()):
    ensure_schema(desk)
    mid = _dossier(desk, matter, hidden)
    row = desk.db.execute('SELECT * FROM note_versions5688 WHERE matter=? AND version=?', (mid, int(numero))).fetchone()
    if not row:
        raise Stop('version_absente')
    return dict(row)


def enregistrer(desk, me, nom, matter, texte, base, members, hidden=frozenset()):
    """Écriture conditionnelle : la mise à jour ne passe que si la version en place est encore ``base`` (atomique, sans fenêtre entre lecture et écriture)."""
    ensure_schema(desk)
    mid = _dossier(desk, matter, hidden)
    texte = str(texte or '').replace('\r\n', '\n').rstrip()
    if len(texte) > MAX_TEXTE:
        raise Stop('note_trop_longue')
    try:
        base = int(base)
    except (TypeError, ValueError):
        raise Stop('version_invalide') from None
    avant = lire(desk, mid, hidden)
    if base == avant['version'] and texte == avant['texte']:
        return {'version': base, 'mentions': [], 'sans_acces': [], 'message': 'Aucun changement.'}
    now, nom = _now(), str(nom or me)[:120]
    if base == 0:
        done = desk.db.execute('INSERT OR IGNORE INTO note_commune5688 VALUES(?,1,?,?,?,?)', (mid, texte, me, nom, now)).rowcount
    else:
        done = desk.db.execute('UPDATE note_commune5688 SET version=version+1,texte=?,auteur=?,auteur_nom=?,modifie=? WHERE matter=? AND version=?',
                               (texte, me, nom, now, mid, base)).rowcount
    if not done:
        desk.db.rollback()
        actuelle = lire(desk, mid, hidden)
        raise NoteConflit({k: actuelle[k] for k in ('version', 'texte', 'auteur_nom', 'modifie')})
    numero = base + 1
    desk.db.execute('INSERT INTO note_versions5688 VALUES(?,?,?,?,?,?)', (mid, numero, texte, me, nom, now))
    desk.db.execute('DELETE FROM note_versions5688 WHERE matter=? AND version<=?', (mid, numero - MAX_VERSIONS))
    desk.db.commit()
    desk.audit('note5688_version', {'matter': mid, 'version': numero})
    nouvelles = [o for o in mx._mentions(texte, members) if o not in mx._mentions(avant['texte'], members) and o != me]
    joints, refuses = notifier(desk, mid, me, nom, nouvelles, members, '%s vous mentionne dans la note commune du dossier.' % nom, '/dossier?id=%s#n88-note' % mid, 'Ouvrir la note')
    return {'version': numero, 'mentions': joints, 'sans_acces': refuses, 'message': 'Note enregistrée (version %d).' % numero +
            (' Sans accès à ce dossier, donc non notifié(s) : %s.' % ', '.join((members.get(o) or {}).get('name') or o for o in refuses) if refuses else '')}


def notifier(desk, matter, auteur, nom, owners, members, texte, lien='', libelle=''):
    """Message dans le fil du dossier, porté par les seules personnes qui voient ce dossier ; renvoie (notifiées, sans accès)."""
    joints, refuses = [], []
    for owner in owners:
        role = (members.get(owner) or {}).get('role', 'avocat')
        (refuses if matter in hidden_for(desk.c['state_dir'], owner, role) else joints).append(owner)
    if not joints:
        return joints, refuses
    tid = mx.matter_thread(desk, matter, '', frozenset(), follow=False)
    _message(desk, tid, auteur, nom, texte, lien, libelle, joints)
    return joints, refuses


def _message(desk, tid, auteur, nom, texte, lien, libelle, joints):
    now = _now()
    cur = desk.db.execute("INSERT INTO messages5650(thread,author,author_name,author_role,kind,text,link,link_label,mentions,at) VALUES(?,?,?,'membre','mention',?,?,?,?,?)",
                          (tid, auteur, str(nom)[:120], str(texte)[:2000], lien[:300] if mx.LINK.fullmatch(lien or '') else '', libelle[:120], mx.json.dumps(joints), now))
    for owner in joints:
        mx._join(desk, tid, owner)
    desk.db.execute('UPDATE fils5650 SET updated=?,last_message=? WHERE id=?', (now, cur.lastrowid, tid))
    desk.db.commit()


def candidats(desk, members, me, matter='', q='', hidden=frozenset()):
    """Personnes proposées à l'autocomplétion : celles qui verraient la mention, jamais les autres. ``jeton`` : ce qui suit l'arobase."""
    if matter:
        _dossier(desk, matter, hidden)
    needle = str(q or '').lstrip('@').lower()[:60]
    local = {}
    for owner in members:
        local.setdefault(owner.split('@')[0].lower(), []).append(owner)
    out = []
    for owner, m in sorted(members.items(), key=lambda kv: str(kv[1].get('name') or kv[0]).lower()):
        if owner == me or (matter and matter in hidden_for(desk.c['state_dir'], owner, m.get('role', 'avocat'))):
            continue
        name = str(m.get('name') or owner)
        if needle and needle not in name.lower() and needle not in owner.lower():
            continue
        out.append({'nom': name, 'jeton': owner.split('@')[0] if len(local[owner.split('@')[0].lower()]) == 1 else owner})
        if len(out) >= MAX_PROPOSES:
            break
    return out


# ------------------------------------------------------------------------------------------------ tâches
def _taches(desk):
    for sql, prefix in (("SELECT id,title,description,matter FROM work_tasks_v211 WHERE status NOT IN ('completed','cancelled') AND (title LIKE '%@%' OR description LIKE '%@%') LIMIT 200", 'w:'),
                        ("SELECT id,title,'' AS description,matter FROM tasks WHERE COALESCE(status,'') NOT IN ('done','completed','cancelled') AND title LIKE '%@%' LIMIT 200", 'l:')):
        try:
            for r in desk.db.execute(sql).fetchall():
                yield prefix + r['id'], r['title'], r['description'], r['matter'] or ''
        except Exception:   # noqa: BLE001 — table absente tant que les tâches n'ont jamais été ouvertes
            continue


def scanner_taches(desk, now_ts, members=None):
    """Boucle des automatismes : toutes les 5 minutes au plus, sans modèle. Une tâche sans dossier notifie par une conversation directe avec l'agent."""
    ensure_schema(desk)
    if now_ts - float(desk.settings('auto:mentions5688', 0) or 0) < SCAN_SECONDES:
        return 0
    desk.setting('auto:mentions5688', now_ts)
    members = directory(desk) if members is None else members
    labels = None
    sent = 0
    for ident, title, description, matter in _taches(desk):
        vues = {r[0] for r in desk.db.execute('SELECT owner FROM mentions_taches5688 WHERE tache=?', (ident,))}
        owners = [o for o in mx._mentions(title + ' ' + description, members) if o not in vues]
        if not owners:
            continue
        if labels is None:
            labels = {m['id'] for m in load_matters(desk.c)}
        now = _now()
        texte = 'Vous êtes mentionné(e) dans une tâche : %s' % title[:300]
        if matter and matter in labels:
            joints, refuses = notifier(desk, matter, mx.AGENT, AGENT_NOM, owners, members, texte, '/planning?vue=taches', 'Ouvrir les tâches')
        else:
            joints, refuses = owners, []
            for owner in owners:
                tid = mx.open_direct(desk, mx.AGENT, owner, members)
                _message(desk, tid, mx.AGENT, AGENT_NOM, texte, '/planning?vue=taches', 'Ouvrir les tâches', [owner])
        for owner in joints + refuses:
            desk.db.execute('INSERT OR IGNORE INTO mentions_taches5688 VALUES(?,?,?)', (ident, owner, now))
        desk.db.commit()
        sent += len(joints)
    return sent


# ------------------------------------------------------------------------------------------------ page du dossier
def card_html(desk, mid, hidden=frozenset()):
    note = lire(desk, mid, hidden)
    rows = ''.join('<li>Version %d · %s · %s <button type="button" class="ax-btn ghost" data-n88-version="%d">Reprendre ce texte</button></li>' % (
        v['version'], e(v['auteur_nom']), e(v['at'][:16].replace('T', ' ')), v['version']) for v in versions(desk, mid, hidden))
    who = ('Version %d, par %s, le %s.' % (note['version'], e(note['auteur_nom']), e(note['modifie'][:16].replace('T', ' ')))) if note['version'] else 'Note encore vide.'
    return ('<section class="ax-card n88-note" id="n88-note" data-matter="%s" data-version="%d"><h2>Note commune</h2><p class="ax-muted" id="n88-who">%s</p>'
            '<label class="m50-sr" for="n88-text">Texte de la note commune</label><textarea id="n88-text" rows="7" maxlength="%d" data-mention data-no-dictation '
            'placeholder="Texte partagé avec les personnes qui voient ce dossier. @identifiant pour mentionner quelqu’un.">%s</textarea>'
            '<div class="n88-conflit" id="n88-conflit" hidden><h3>Une autre personne a écrit pendant ce temps</h3><p>Votre texte n’a pas été enregistré ; il reste ci-dessus. Voici le texte en place :</p>'
            '<pre id="n88-autre"></pre><button type="button" class="ax-btn ghost" id="n88-reprendre">Remplacer mon texte par celui-ci</button><p class="ax-muted">Sinon, reportez ses changements dans votre texte, puis enregistrez.</p></div>'
            '<p><button type="button" class="ax-btn" id="n88-save">Enregistrer la note</button></p><p role="status" id="n88-status"></p>'
            '%s</section>') % (
        e(mid, quote=True), note['version'], who, MAX_TEXTE, e(note['texte']),
        ('<details><summary>Versions précédentes</summary><ol class="n88-versions">%s</ol></details>' % rows) if rows else '')
