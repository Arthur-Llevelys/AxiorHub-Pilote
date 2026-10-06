"""Dépôt Nextcloud reprenable : même chemin, mêmes octets, relecture SHA-256."""
import hashlib
import json
import os
import re
from pathlib import Path
import tempfile

from .common import Stop


def staged_path(desk, rid):
    if not re.fullmatch(r'[a-f0-9]{32}', str(rid)):
        raise Stop('identifiant_depot_invalide')
    root = Path(desk.c['state_dir']) / 'outbox567'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root / (rid + '.docx')


def stage(desk, rid, path, data, result):
    from .docrequest520 import _set
    target = staged_path(desk, rid)
    fd, name = tempfile.mkstemp(dir=target.parent, prefix='.deposit-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    result = {**result, 'sha256': hashlib.sha256(data).hexdigest()}
    _set(desk, rid, 'depot_en_cours', path, result)


def finish(desk, row, client, args):
    from .docrequest520 import _set
    from .missions567 import check_authority
    result = json.loads(row['result'] or '{}')
    expected = result.get('sha256', '')
    if not expected or not row['path']:
        raise Stop('preuve_depot_absente')
    from .common import load_matters, under
    matter = next((m for m in load_matters(desk.c) if m['id'] == row['matter']), None)
    if not matter or not under(row['path'], matter['path']):
        raise Stop('depot_hors_dossier_autorise')
    check_authority(desk, args)
    try:
        meta = client.stat(row['path'])
    except Stop as error:
        if str(error) not in ('http_404', 'fichier_nextcloud_introuvable'):
            raise Stop('depot_incertain_verification_requise') from None
        staged = staged_path(desk, row['id'])
        if not staged.is_file():
            raise Stop('contenu_depot_a_reprendre_absent')
        data = staged.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise Stop('contenu_depot_local_altere')
        check_authority(desk, args)
        client.put_file(row['path'], data, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        meta = client.stat(row['path'])
    raw = client.download(meta)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise Stop('contenu_document_non_verifie')
    result.update(readback_sha256=expected, verified_at=desk.now())
    try:
        result['url'] = client.file_web_url(row['path'])
    except Stop:
        result['url'] = ''
    _set(desk, row['id'], 'cree', row['path'], result)
    staged_path(desk, row['id']).unlink(missing_ok=True)
    return {'request': row['id'], 'message': 'Projet Word déposé et relu dans Nextcloud.',
            'created_files': [{'path': row['path'], 'edit_url': result.get('url', '')}],
            'verification': {'sha256': expected, 'readback_sha256': expected, 'at': result['verified_at']}}
