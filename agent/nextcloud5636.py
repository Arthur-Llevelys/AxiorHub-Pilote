"""5.6.36 : choisir des dossiers Nextcloud en cliquant (racines, dossiers des clients, dossiers d'arrivée des agents documentaires).

L'avocat n'écrit plus de chemin : il parcourt les dossiers de son Nextcloud (ou du dossier synchronisé local) et clique « Choisir ce
dossier ». Seuls des noms de dossiers sont lus — jamais le contenu d'un fichier.
"""
from .common import Stop, clean_path


def _local_dirs(base, path, ext=''):
    """Dossier de travail local (poste) : sous-dossiers par nom, sans lien symbolique ni dossier caché ; avec ``ext``, la liste
    renvoyée contient aussi les fichiers de cette extension (``'file': True``)."""
    target = base
    for part in [x for x in path.split('/') if x]:
        if part in ('.', '..'):
            raise Stop('chemin_refuse')
        target = target / part
        if target.is_symlink():
            raise Stop('chemin_refuse')
    if not target.is_dir():
        raise Stop('dossier_introuvable')
    out = []
    for child in target.iterdir():
        try:
            if child.name.startswith('.') or child.is_symlink():
                continue
            is_dir = child.is_dir()
            if not is_dir and not (ext and child.is_file() and child.name.lower().endswith(ext)):
                continue
        except OSError:
            continue
        out.append({'name': child.name[:200], 'path': clean_path(path + '/' + child.name), **({} if is_dir else {'file': True})})
    return out


def folders(desk, path, ext=''):
    """Sous-dossiers d'un dossier Nextcloud : {'path', 'parent', 'dirs': [{'name', 'path'}]} ; avec ``ext`` (« .docx »), aussi
    ``'files'`` : les fichiers de cette extension (choix d'un modèle Word)."""
    from .dav import DAV
    raw = str(path or '/').strip() or '/'
    if len(raw) > 600 or any(ord(c) < 32 for c in raw):
        raise Stop('dossier_invalide')
    path = clean_path(raw)
    try:
        client = DAV(desk.c.get('nextcloud') or {})
    except (KeyError, TypeError, OSError):
        raise Stop('nextcloud_non_configure') from None
    local = getattr(client, 'local', None)
    ext = ext if ext in ('.docx',) else ''
    if local is not None:
        items = _local_dirs(local, path, ext)
    else:
        items = [{'name': x['path'].rstrip('/').rsplit('/', 1)[-1][:200], 'path': x['path'], **({} if x.get('directory') else {'file': True})}
                 for x in client.list_folder(path) if x.get('directory') or (ext and x['path'].lower().endswith(ext))]
    dirs = sorted([d for d in items if not d.get('file')], key=lambda d: d['name'].casefold())[:500]
    parent = (path.rsplit('/', 1)[0] or '/') if path != '/' else ''
    out = {'path': path, 'parent': parent, 'dirs': dirs}
    if ext:
        out['files'] = sorted([{'name': d['name'], 'path': d['path']} for d in items if d.get('file')], key=lambda d: d['name'].casefold())[:500]
    return out
