"""5.6.66 : notes internes rangées comme documents Word dans le dossier concerné, et non plus déposées comme brouillons de courriel
adressés au cabinet (« [À traiter] … » dans « À relire »). Sans dossier identifié, ou si le dossier n'est pas accessible, la note va dans
un dossier neutre « A TRAITER » à la racine des dossiers du cabinet.

Un fichier existant n'est jamais remplacé (nom libre « (2) », « (3) »…) ; rien n'est envoyé.

5.6.89 (B06, B07) : le rangement suit un journal indexé sur compte + dossier IMAP + UIDVALIDITY + UID, avec destination et empreinte. Étapes : ``reserve`` (destination choisie),
``publie`` (fichier écrit), ``verifie`` (fichier relu), ``deplace`` (brouillon sorti de la messagerie), ``termine``. Une reprise vérifie ce qui existe et ne refait que l'étape
manquante : un accusé perdu ne recrée jamais le Word. L'annulation et la pause sont relues au début de chaque note, avant la publication et avant le déplacement ; leur motif
remonte tel quel au gestionnaire de travail. Une annulation n'est pas réactivée : seule une nouvelle demande reprend depuis le journal.
"""
from datetime import datetime
import hashlib
import json
from pathlib import PurePosixPath

from . import pause5629
from .arret5643 import STOP_CODES
from .common import Stop, clean_path, under

NEUTRAL = 'A TRAITER'
AUTO_SETTING = 'notes5687:rangement_auto'
AUTO_MINUTES = 15
DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
MISSING = ('fichier_nextcloud_introuvable', 'http_404')
SCHEMA = '''CREATE TABLE IF NOT EXISTS rangement5689(compte TEXT NOT NULL, dossier_imap TEXT NOT NULL, uidvalidity TEXT NOT NULL, uid TEXT NOT NULL, item TEXT NOT NULL,
  matter TEXT NOT NULL DEFAULT '', destination TEXT NOT NULL DEFAULT '', emplacement TEXT NOT NULL DEFAULT '', empreinte TEXT NOT NULL DEFAULT '', etape TEXT NOT NULL,
  brouillon_garde INTEGER NOT NULL DEFAULT 0, erreur TEXT NOT NULL DEFAULT '', job INTEGER, created TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(compte, dossier_imap, uidvalidity, uid));'''


def neutral_folder(desk, matter=None):
    roots = [clean_path(r) for r in (desk.c.get('nextcloud', {}) or {}).get('roots') or [] if str(r).strip()]
    if not roots:
        raise Stop('racine_des_dossiers_inconnue')
    root = next((r for r in roots if matter and matter.get('path') and under(clean_path(matter['path']), r)), roots[0])
    return clean_path(root + '/' + NEUTRAL), root


def _docx(title, lines):
    from .docrequest520 import build_docx
    return build_docx({'titre': title, 'paragraphes': [{'style': 'texte', 'texte': line} for line in lines if str(line).strip()]}, {}, '')


def _targets(desk, matter, title, day, label):
    """Nom de base et emplacements possibles, le dossier d'abord, le dossier neutre ensuite."""
    from .docrequest520 import _safe_name
    base = '%s - %s - %s' % ((day or datetime.now()).strftime('%Y-%m-%d'), label, _safe_name(title))
    targets = []
    if matter and matter.get('path'):
        folder = clean_path(matter['path'])
        if label == 'Note interne':     # 5.6.88 : règle de nommage du cabinet ; sans règle, le nom habituel
            from . import cloud_nommage5688 as nommage
            naming = nommage.context_of(matter, date=(day or datetime.now()).date().isoformat(), titre=_safe_name(title))
            base = nommage.apply(desk, 'note', base + '.docx', naming)[:-len('.docx')]
            folder = nommage.folder_for(desk, 'note', folder, folder, naming)
        targets.append(('dossier', folder, clean_path(matter['path']) if folder != clean_path(matter['path']) else None))
    try:
        folder, root = neutral_folder(desk, matter)
        targets.append(('a_traiter', folder, root))
    except Stop:
        pass
    return base, targets


def deposit(desk, client, matter, title, lines, day=None, label='Note interne', before_put=None):
    """Dépose la note (titre, lignes de texte) ; renvoie {'chemin', 'emplacement': 'dossier' | 'a_traiter'}. ``label`` : « Note interne »,
    ou « Projet » pour un projet d'acte préparé avec Claude (5.6.75). ``before_put(chemin, emplacement, empreinte)`` : réservation au journal, juste avant l'écriture."""
    from .docrequest520 import _free_path
    data = _docx(title, lines)
    base, targets = _targets(desk, matter, title, day, label)
    last = 'note_interne_non_deposee'
    for place, folder, boundary in targets:
        pause5629.checkpoint(desk)      # 5.6.89 (B07) : hors du try : un arrêt n'est pas un dossier inaccessible
        try:
            if boundary and hasattr(client, 'ensure_folder'):
                client.ensure_folder(folder, boundary)
            path = _free_path(client, folder, base)
        except Stop as ex:
            last = str(ex)
            continue
        if before_put:
            before_put(path, place, hashlib.sha256(data).hexdigest())
        pause5629.checkpoint(desk)
        try:
            client.put_file(path, data, DOCX)
            return {'chemin': path, 'emplacement': place, 'nom': PurePosixPath(path).name}
        except Stop as ex:
            last = str(ex)
            continue
    raise Stop(last)


def schema(desk):
    desk.db.executescript(SCHEMA)


def _box(desk):
    mail = desk.c.get('mail') or {}
    return str(mail.get('username') or mail.get('user') or ''), str(mail.get('drafts') or '')


def journal_entry(desk, uid, validity):
    """Ligne du journal de rangement pour ce brouillon ; ``None`` s'il n'a jamais été pris en charge."""
    schema(desk)
    compte, dossier = _box(desk)
    row = desk.db.execute('SELECT * FROM rangement5689 WHERE compte=? AND dossier_imap=? AND uidvalidity=? AND uid=?', (compte, dossier, str(validity), str(uid))).fetchone()
    return dict(row) if row else None


def _journal(desk, uid, validity, item, **fields):
    """Écrit l'étape et la valide aussitôt : le journal précède ou suit chaque effet, jamais une transaction commune avec lui."""
    compte, dossier = _box(desk)
    now = desk.now()
    fields['updated'] = now
    desk.db.execute('INSERT OR IGNORE INTO rangement5689(compte,dossier_imap,uidvalidity,uid,item,etape,job,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                    (compte, dossier, str(validity), str(uid), item, 'reserve', getattr(desk, 'active_job_id', None), now, now))
    desk.db.execute('UPDATE rangement5689 SET %s WHERE compte=? AND dossier_imap=? AND uidvalidity=? AND uid=?' % ','.join(k + '=?' for k in fields),
                    list(fields.values()) + [compte, dossier, str(validity), str(uid)])
    desk.db.commit()


def _proof(client, path, sha256):
    """Le fichier attendu est-il à sa place ? ``True`` : relu, conforme (ou présence seule si le client ne sait pas lire) ; ``False`` : absent ou autre contenu."""
    try:
        info = client.stat(path)
    except Stop as ex:
        if str(ex) in MISSING:
            return False
        raise
    if not hasattr(client, 'download'):
        return True
    return hashlib.sha256(client.download({**info, 'path': path})).hexdigest() == sha256


def waiting(desk):
    """5.6.87 : notes de l'agent encore en brouillon. Un brouillon écrit par l'avocat pour lui-même n'en fait pas partie, et une note déjà rangée n'est pas reprise."""
    from . import cockpit530
    cockpit530.ensure_schema(desk)
    cache = cockpit530._drafts(desk)
    validity = str(cache.get('validity', ''))
    done = {r[0] for r in desk.db.execute('SELECT item FROM cockpit530_reviewed')}
    items = ('mail:%s:%s' % (validity, d['uid']) for d in cache.get('items', []) if d.get('agent') and cockpit530.is_internal_note(desk, d))
    return [item for item in items if item not in done]


def schedule(desk, now_ts):
    """Boucle des automatismes : avant, rien ne se rangeait sans le bouton « Tout ranger dans les dossiers », et les notes restaient dans la messagerie."""
    if not desk.settings(AUTO_SETTING, True) or now_ts - float(desk.settings('auto:notes5687', 0) or 0) < AUTO_MINUTES * 60:
        return None
    desk.setting('auto:notes5687', now_ts)
    if desk.db.execute("SELECT 1 FROM jobs WHERE kind='ranger_notes5666' AND status IN ('pending','running') LIMIT 1").fetchone():
        return None
    refused = _cancelled_items(desk)
    items = [item for item in waiting(desk) if item not in refused]
    return desk.enqueue('ranger_notes5666', {'items': items[:60]}, priority=45) if items else None


def _cancelled_items(desk):
    """5.6.89 (B07) : notes d'un rangement annulé par l'avocat. L'automatisme ne les reprend pas : ce serait réactiver l'annulation ; une nouvelle demande reste possible."""
    out = set()
    for row in desk.db.execute("SELECT args FROM jobs WHERE kind='ranger_notes5666' AND status='cancelled' AND stop_reason IN ('user','parent') ORDER BY id DESC LIMIT 50"):
        try:
            out.update(str(x) for x in (json.loads(row['args'] or '{}').get('items') or []))
        except (TypeError, ValueError, AttributeError):
            continue
    return out


def _strip_prefix(subject):
    from .cockpit530 import NOTE_PREFIXES
    subject = str(subject or '').strip()
    for prefix in NOTE_PREFIXES:
        if subject.startswith(prefix):
            return subject[len(prefix):].strip()
    return subject or 'Note interne'


def _to_trash(desk, uid, validity):
    """Brouillon déplacé dans la corbeille de la messagerie (restaurable) ; sans corbeille, il reste en place (jamais supprimé)."""
    from . import drafts440
    from .mailbox import Mailbox
    box = Mailbox(desk.c['mail'])
    try:
        trash = drafts440.find_trash(box)
    finally:
        box.close()
    if not trash:
        return False
    drafts440.discard_draft(desk.c['mail'], uid, validity, 'yes')
    return True


def _publish(desk, dav, item, uid, validity, d, entry, matters):
    """Étapes jusqu'au fichier relu. Un fichier déjà au bon endroit avec la bonne empreinte n'est pas recréé, même si l'étape « publié » n'a pas été notée."""
    from . import cockpit530, drafts440
    step = (entry or {}).get('etape')
    if step in ('publie', 'verifie') or (step == 'reserve' and entry['destination'] and _proof(dav, entry['destination'], entry['empreinte'])):
        if step == 'verifie' or _proof(dav, entry['destination'], entry['empreinte']):
            _journal(desk, uid, validity, item, etape='verifie', erreur='')
            return
        step = None                    # fichier retiré ou remplacé depuis : la note repart de son brouillon, sous un nom libre
    if d is None:
        raise Stop('brouillon_source_absent')
    pause5629.checkpoint(desk)
    draft = drafts440.get_draft(desk.c['mail'], d['uid'], validity)
    mid = str(cockpit530._draft_matter(desk, d) or '')

    def reserve(path, place, sha256):
        _journal(desk, uid, validity, item, etape='reserve', destination=path, emplacement=place, empreinte=sha256, matter=mid, erreur='')
    note = deposit(desk, dav, matters.get(mid), _strip_prefix(d.get('subject')), str(draft.get('body') or '').split('\n'), before_put=reserve)
    _journal(desk, uid, validity, item, etape='publie', destination=note['chemin'], emplacement=note['emplacement'])
    entry = journal_entry(desk, uid, validity)
    if not _proof(dav, entry['destination'], entry['empreinte']):
        raise Stop('note_non_verifiee_apres_depot')
    _journal(desk, uid, validity, item, etape='verifie')


def _finish(desk, item, uid, validity, subject):
    """Dernière étape : la note quitte « À relire » ; le fichier et son journal restent la preuve du rangement."""
    entry = journal_entry(desk, uid, validity)
    desk.db.execute('INSERT OR REPLACE INTO cockpit530_reviewed VALUES(?,?,?,?)', (item, 'range', ('%s — %s' % (subject, entry['destination']))[:250], desk.now()))
    desk.db.commit()
    _journal(desk, uid, validity, item, etape='termine', erreur='')
    return journal_entry(desk, uid, validity)


def _item_parts(item):
    parts = item.split(':')
    return (parts[1], parts[2]) if len(parts) == 3 and parts[0] == 'mail' and parts[2] else (None, None)


def file_drafts(desk, args, dav=None, trash=None):
    """Travail « ranger_notes5666 » : chaque note interne désignée (brouillon adressé au cabinet) devient un document Word de son dossier
    (sinon « A TRAITER ») ; le brouillon va dans la corbeille de la messagerie et quitte « À relire ».

    5.6.89 : une reprise repart du journal. Un incident sur une note n'arrête pas les suivantes et ne retire aucune preuve ; seul un motif d'arrêt (annulation, pause) interrompt
    le travail et remonte tel quel."""
    from . import cockpit530
    from .common import load_matters
    wanted = [str(x).strip() for x in (args.get('items') or []) if str(x).strip()][:60]
    if not wanted:
        raise Stop('aucune_note_designee')
    cockpit530.ensure_schema(desk)
    schema(desk)
    cache = cockpit530._drafts(desk, refresh=True)
    validity = str(cache.get('validity', ''))
    mail_ok = cache.get('ok') is not False
    found = {'mail:%s:%s' % (validity, d['uid']): d for d in cache.get('items', [])}
    matters = {str(m['id']): m for m in load_matters(desk.c)}
    if dav is None:
        from .document_projects import _dav
        dav = _dav(desk)
    move = trash or _to_trash
    filed, partial, skipped, kept_in_drafts, already, failures = [], [], 0, 0, 0, []
    try:
        for item in wanted:
            pause5629.checkpoint(desk)      # 5.6.89 (B07) : l'annulation et la pause sont relues entre deux notes, jamais converties en erreur
            item_validity, uid = _item_parts(item)
            d = found.get(item)
            entry = journal_entry(desk, uid, item_validity) if uid else None
            if entry and entry['etape'] == 'termine':
                already += 1
                continue
            if not entry and (not d or not cockpit530.is_internal_note(desk, d)):
                skipped += 1
                continue
            if d and not cockpit530.is_internal_note(desk, d):
                skipped += 1
                continue
            subject = _strip_prefix(d.get('subject')) if d else item
            try:
                if not (entry and entry['etape'] == 'deplace'):
                    if d is None and not mail_ok:          # messagerie muette : rien ne prouve le départ du brouillon, la preuve du fichier reste
                        partial.append(entry)
                        continue
                    _publish(desk, dav, item, uid, item_validity, d, entry, matters)
                    if d is not None:
                        pause5629.checkpoint(desk)
                        kept = not move(desk, d['uid'], item_validity)
                        kept_in_drafts += int(kept)
                        _journal(desk, uid, item_validity, item, etape='deplace', brouillon_garde=int(kept))
                    else:                                  # la messagerie répond et le brouillon n'y est plus : le déplacement a abouti, son accusé manquait
                        _journal(desk, uid, item_validity, item, etape='deplace')
                filed.append(_finish(desk, item, uid, item_validity, subject))
            except Stop as ex:
                if str(ex) in STOP_CODES:
                    raise
                failures.append(str(ex))
                current = journal_entry(desk, uid, item_validity)
                if current:
                    _journal(desk, uid, item_validity, item, erreur=str(ex)[:120])
                    if current['etape'] in ('publie', 'verifie'):
                        partial.append(current)
    finally:
        desk.audit('notes5666_rangees', {'rangees': len(filed), 'ignorees': skipped, 'gardees_en_brouillon': kept_in_drafts, 'recuperation_partielle': len(partial), 'deja_rangees': already})
    if failures and not filed and not partial and not already:
        raise Stop(failures[0])
    message = '%d note(s) interne(s) rangée(s) : %d dans leur dossier, %d dans « A TRAITER ».' % (
        len(filed), sum(1 for n in filed if n['emplacement'] == 'dossier'), sum(1 for n in filed if n['emplacement'] == 'a_traiter'))
    if kept_in_drafts:
        message += ' %d brouillon(s) laissé(s) dans la messagerie (pas de corbeille trouvée).' % kept_in_drafts
    if partial:
        message += ' %d note(s) en récupération partielle : fichier conservé, brouillon encore dans la messagerie ; seul le déplacement sera repris.' % len(partial)
    return {'ok': True, 'rangees': len(filed), 'notes': [n['destination'] for n in filed], 'recuperation_partielle': len(partial),
            'fichiers_conserves': [n['destination'] for n in partial], 'message': message}
