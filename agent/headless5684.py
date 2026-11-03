"""5.6.84 : travail sur un document sans éditeur ouvert (cahier 5.6.84, § 16).

Le plugin ne vit que dans un éditeur ouvert ; il n'y a donc jamais de navigateur caché pour simuler une présence. Le serveur prépare une nouvelle version, vérifie sa structure et publie avec contrôle d'ETag.
Si un éditeur s'ouvre pendant ce travail, le résultat devient une proposition de la session active : le binaire n'est jamais remplacé sous une coédition. Si l'original a évolué, une copie de conflit est conservée."""
from pathlib import PurePosixPath
import hashlib
import io
import time
import zipfile

from .common import Stop, xml_bytes, xml_office
from . import office_bridge5684 as bridge, office_gen5684 as gen

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


def _replace_in_xml(xml, expected, replacement):
    """Remplacement dans le texte d'un seul élément, ce qui préserve styles, tableaux et numérotation. Une cible ambiguë ou à cheval sur plusieurs éléments est refusée."""
    root = xml_bytes(xml)
    hits = [node for node in root.iter(W + 't') if node.text and expected in node.text]
    total = sum(node.text.count(expected) for node in hits)
    if total != 1:
        raise Stop('cible_ambigue_ou_absente')
    hits[0].text = hits[0].text.replace(expected, replacement)
    return xml_office(root, xml)


def apply_operations(raw, operations):
    """Nouvelle version en mémoire : les autres parties de l'archive sont recopiées à l'identique, aucune reconstruction depuis le texte."""
    source = zipfile.ZipFile(io.BytesIO(raw))
    if 'word/document.xml' not in source.namelist():
        raise Stop('document_office_non_lisible')
    xml = source.read('word/document.xml')
    for op in operations:
        xml = _replace_in_xml(xml, op['expected'], op['replacement'])
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            target.writestr(item, xml if item.filename == 'word/document.xml' else source.read(item.filename))
    data = out.getvalue()
    zipfile.ZipFile(io.BytesIO(data)).testzip()
    return data


def _proposal(desk, active, operations, reason):
    """Éditeur ouvert : toute mutation passe par son adaptateur, en proposition."""
    bridge.schema(desk)
    gen_id = active['generation_id']
    mirror = bridge._mirror(desk, gen_id)
    ops = []
    for op in operations:
        block = next((b for b in desk.db.execute('SELECT block_id, text FROM office_blocks5684 WHERE generation_id=? AND removed=0', (gen_id,)) if b['text'].count(op['expected']) == 1), None)
        if not block:
            raise Stop('cible_ambigue_ou_absente')
        ops.append({'action': 'replace_text', 'block_id': block['block_id'], 'expected': op['expected'], 'replacement': op['replacement']})
    return bridge.create_command(desk, gen_id, active['document_id'], mirror['revision'], mirror['snapshot_hash'], ops, needs_decision=True, reason=reason, origin='serveur')


def revise(desk, path, operations, owner='ia', client=None, reason='Modification préparée sans éditeur ouvert', pause=None, build=None):
    """Coordination, lecture, nouvelle version, contrôle juste avant publication. Rend {'etat': 'publie' | 'proposition' | 'conflit', ...}.
    ``build(octets lus)`` (5.6.89) : nouvelle version déjà composée par le parcours des propositions, sous le même bail et le même contrôle d'ETag."""
    gen.schema(desk)
    bridge.schema(desk)
    from .office440 import dav_client
    dav = client or dav_client(desk)
    info = dav.stat(path)
    document_id = gen.document_for(desk, path, info)
    desk.db.commit()
    with gen.lease(desk, document_id):
        active = gen.active(desk, document_id)
        if active:
            return {'etat': 'proposition', 'command_id': _proposal(desk, active, operations, reason)}
        raw = dav.download(info)
        new = build(raw) if build else apply_operations(raw, operations)
        if pause:
            pause()     # point d'essai : un éditeur peut s'ouvrir pendant le travail
        active = gen.active(desk, document_id)
        if active:      # SV07 : relu juste avant de publier
            return {'etat': 'proposition', 'command_id': _proposal(desk, active, operations, reason)}
        fresh = dav.stat(path)
        sha = hashlib.sha256(new).hexdigest()
        if fresh['etag'] != info['etag']:
            p = PurePosixPath(path)
            copy = str(p.with_name('%s (proposition %s)%s' % (p.stem, time.strftime('%Y%m%d-%H%M'), p.suffix)))
            dav.put_file(copy, new, DOCX)
            return {'etat': 'conflit', 'path': copy, 'message': 'Le fichier a changé pendant le travail : proposition conservée à côté.'}
        dav.replace_file(path, new, fresh['etag'])
        check = dav.stat(path)
        if hashlib.sha256(dav.download(check)).hexdigest() != sha:
            raise Stop('relecture_nextcloud_non_conforme')
    desk.audit('office5684_serveur', {'path': path, 'owner': owner, 'sha256': sha})
    return {'etat': 'publie', 'path': path, 'sha256': sha, 'etag': check['etag']}
