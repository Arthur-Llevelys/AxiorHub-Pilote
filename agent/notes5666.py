"""5.6.66 : notes internes rangées comme documents Word dans le dossier concerné, et non plus déposées comme brouillons de courriel
adressés au cabinet (« [À traiter] … » dans « À relire »). Sans dossier identifié, ou si le dossier n'est pas accessible, la note va dans
un dossier neutre « A TRAITER » à la racine des dossiers du cabinet.

Un fichier existant n'est jamais remplacé (nom libre « (2) », « (3) »…) ; rien n'est envoyé.
"""
from datetime import datetime
from pathlib import PurePosixPath

from .common import Stop, clean_path, under

NEUTRAL = 'A TRAITER'
AUTO_SETTING = 'notes5687:rangement_auto'
AUTO_MINUTES = 15
DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


def neutral_folder(desk, matter=None):
    roots = [clean_path(r) for r in (desk.c.get('nextcloud', {}) or {}).get('roots') or [] if str(r).strip()]
    if not roots:
        raise Stop('racine_des_dossiers_inconnue')
    root = next((r for r in roots if matter and matter.get('path') and under(clean_path(matter['path']), r)), roots[0])
    return clean_path(root + '/' + NEUTRAL), root


def deposit(desk, client, matter, title, lines, day=None, label='Note interne'):
    """Dépose la note (titre, lignes de texte) ; renvoie {'chemin', 'emplacement': 'dossier' | 'a_traiter'}. ``label`` : « Note interne »,
    ou « Projet » pour un projet d'acte préparé avec Claude (5.6.75)."""
    from .docrequest520 import _free_path, _safe_name, build_docx
    doc = {'titre': title, 'paragraphes': [{'style': 'texte', 'texte': line} for line in lines if str(line).strip()]}
    data = build_docx(doc, {}, '')
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
    last = 'note_interne_non_deposee'
    for place, folder, boundary in targets:
        try:
            if boundary and hasattr(client, 'ensure_folder'):
                client.ensure_folder(folder, boundary)
            path = _free_path(client, folder, base)
            client.put_file(path, data, DOCX)
            return {'chemin': path, 'emplacement': place, 'nom': PurePosixPath(path).name}
        except Stop as ex:
            last = str(ex)
            continue
    raise Stop(last)


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
    items = waiting(desk)
    return desk.enqueue('ranger_notes5666', {'items': items[:60]}, priority=45) if items else None


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


def file_drafts(desk, args, dav=None, trash=None):
    """Travail « ranger_notes5666 » : chaque note interne désignée (brouillon adressé au cabinet) devient un document Word de son dossier
    (sinon « A TRAITER ») ; le brouillon va dans la corbeille de la messagerie et quitte « À relire »."""
    from . import cockpit530, drafts440
    from .common import load_matters
    wanted = [str(x).strip() for x in (args.get('items') or []) if str(x).strip()][:60]
    if not wanted:
        raise Stop('aucune_note_designee')
    cockpit530.ensure_schema(desk)
    cache = cockpit530._drafts(desk, refresh=True)
    validity = str(cache.get('validity', ''))
    found = {'mail:%s:%s' % (validity, d['uid']): d for d in cache.get('items', [])}
    matters = {str(m['id']): m for m in load_matters(desk.c)}
    if dav is None:
        from .document_projects import _dav
        dav = _dav(desk)
    move = trash or _to_trash
    filed, skipped, kept_in_drafts = [], 0, 0
    for item in wanted:
        d = found.get(item)
        if not d or not cockpit530.is_internal_note(desk, d):
            skipped += 1
            continue
        draft = drafts440.get_draft(desk.c['mail'], d['uid'], validity)
        mid = cockpit530._draft_matter(desk, d)
        note = deposit(desk, dav, matters.get(str(mid)), _strip_prefix(d.get('subject')), str(draft.get('body') or '').split('\n'))
        if not move(desk, d['uid'], validity):
            kept_in_drafts += 1
        desk.db.execute('INSERT OR REPLACE INTO cockpit530_reviewed VALUES(?,?,?,?)',
                        (item, 'range', ('%s — %s' % (_strip_prefix(d.get('subject')), note['chemin']))[:250], desk.now()))
        desk.db.commit()
        filed.append(note)
    desk.audit('notes5666_rangees', {'rangees': len(filed), 'ignorees': skipped, 'gardees_en_brouillon': kept_in_drafts})
    message = '%d note(s) interne(s) rangée(s) : %d dans leur dossier, %d dans « A TRAITER ».' % (
        len(filed), sum(1 for n in filed if n['emplacement'] == 'dossier'), sum(1 for n in filed if n['emplacement'] == 'a_traiter'))
    if kept_in_drafts:
        message += ' %d brouillon(s) laissé(s) dans la messagerie (pas de corbeille trouvée).' % kept_in_drafts
    return {'ok': True, 'rangees': len(filed), 'notes': [n['chemin'] for n in filed], 'message': message}
