"""5.6.70 : joindre à un courriel des documents de l'espace de fichiers Nextcloud (messagerie intégrée).

- Parcourir : racines configurées du cabinet, puis dossier par dossier (lecture WebDAV, un niveau à la fois) ;
- Rechercher : par nom de fichier dans l'index des pièces (aucun parcours complet du stockage) ;
- Joindre : au dépôt du brouillon ou à l'envoi, le serveur relit chaque fichier choisi (ETag, taille) et l'ajoute en pièce jointe.
Garde-fous : seuls les chemins sous les racines du cabinet sont acceptés ; les dossiers réservés à d'autres membres sont exclus ;
10 fichiers et 15 Mo au plus (même limite que les pièces téléversées).
"""
from pathlib import PurePosixPath

from .common import Stop, clean_path, load_matters, under

MAX_FILES = 10
MAX_BYTES = 15_000_000


def client(desk):
    """Accès aux fichiers du cabinet (Nextcloud ou dossier local) ; message clair s'il n'est pas configuré."""
    from .dav import DAV
    cfg = desk.c.get('nextcloud') or {}
    if not (cfg.get('url') or cfg.get('local_path')):
        raise Stop('nextcloud_ou_dossier_local_a_configurer')
    return DAV(cfg)


def _roots(desk):
    return [clean_path(r) for r in ((desk.c.get('nextcloud') or {}).get('roots') or [])]


def _hidden_paths(desk):
    from .membres5650 import viewer_hidden
    hidden = viewer_hidden() or frozenset()
    if not hidden:
        return []
    # liste complète : load_matters masque déjà au lecteur les dossiers qui lui sont réservés (leurs chemins doivent rester interdits)
    return [clean_path(m['path']) for m in load_matters(desk.c, unfiltered=True) if str(m['id']) in hidden and m.get('path')]


def allowed(desk, path):
    path = clean_path(str(path or ''))
    if not any(under(path, r) for r in _roots(desk)):
        raise Stop('chemin_hors_espace_du_cabinet')
    if any(under(path, h) for h in _hidden_paths(desk)):
        raise Stop('dossier_reserve')
    return path


def browse(desk, path='', dav=None):
    """Contenu d'un dossier (ou racines du cabinet si ``path`` est vide)."""
    if not path:
        return {'chemin': '', 'parent': '', 'entrees': [{'chemin': r, 'nom': PurePosixPath(r).name or r, 'dossier': True, 'taille': 0} for r in _roots(desk)]}
    path = allowed(desk, path)
    if dav is None:
        dav = client(desk)
    hidden = _hidden_paths(desk)
    entries = []
    for x in dav.list_folder(path):
        if any(under(x['path'], h) for h in hidden):
            continue
        entries.append({'chemin': x['path'], 'nom': PurePosixPath(x['path']).name, 'dossier': bool(x['directory']), 'taille': int(x.get('size') or 0)})
    entries.sort(key=lambda x: (not x['dossier'], x['nom'].lower()))
    parent = str(PurePosixPath(path).parent)
    return {'chemin': path, 'parent': parent if any(under(parent, r) for r in _roots(desk)) else '', 'entrees': entries[:500]}


def search(desk, query, limit=30):
    """Fichiers indexés dont le nom contient la recherche (insensible à la casse)."""
    query = str(query or '').strip()
    if len(query) < 2:
        raise Stop('recherche_trop_courte')
    from pathlib import Path
    import sqlite3
    db = sqlite3.connect(Path(desk.c['state_dir']) / 'documents.sqlite3', timeout=10)
    try:
        rows = db.execute('SELECT DISTINCT path FROM docs WHERE path LIKE ? ESCAPE \'\\\' ORDER BY path LIMIT 400',
                          ('%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%',)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        db.close()
    out = []
    for (p,) in rows:
        try:
            p = allowed(desk, p)
        except Stop:
            continue
        if query.lower() not in PurePosixPath(p).name.lower():
            continue
        out.append({'chemin': p, 'nom': PurePosixPath(p).name, 'dossier': False, 'taille': 0})
        if len(out) >= limit:
            break
    return {'resultats': out}


def attachments(desk, paths, dav=None):
    """[(nom, octets, type MIME)] des fichiers Nextcloud choisis, relus au moment du dépôt."""
    import mimetypes
    paths = [str(p) for p in (paths or []) if str(p).strip()]
    if not paths:
        return []
    if len(paths) > MAX_FILES:
        raise Stop('dix_pieces_jointes_maximum')
    if dav is None:
        dav = client(desk)
    out, total = [], 0
    for p in paths:
        p = allowed(desk, p)
        meta = dav.stat(p)
        if int(meta.get('size') or 0) > MAX_BYTES:
            raise Stop('pieces_jointes_trop_lourdes_15_mo_maximum')
        raw = dav.download(meta)
        total += len(raw)
        if total > MAX_BYTES:
            raise Stop('pieces_jointes_trop_lourdes_15_mo_maximum')
        kind = mimetypes.guess_type(PurePosixPath(p).name)[0] or 'application/octet-stream'
        out.append((PurePosixPath(p).name, raw, kind))
    return out
