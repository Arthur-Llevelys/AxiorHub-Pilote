"""5.6.89 : télécharger et exporter un livrable (audit 5.6.88, § 50).

Deux commandes distinctes. Télécharger rend sur le poste la version choisie, avec un type et un nom décidés par le serveur, sans cache partagé. Exporter écrit dans une destination autorisée : c'est un travail suivi, avec
clé d'idempotence, et « Exporté » n'apparaît qu'après relecture du fichier écrit. Le navigateur ne désigne jamais un chemin : il donne une référence que le serveur résout, et les droits sont recontrôlés à chaque action.

Reprise : le chemin et l'empreinte attendus sont notés avant l'écriture. Après une coupure, on regarde ce qui existe ; si c'est le bon fichier, on ne réécrit rien. Aucun export ne remplace un fichier : un original reçu, signé
ou émis n'est jamais écrasé. L'envoi d'un courriel ou un dépôt officiel restent des actions à part : rien ici n'en déclenche.
"""
from datetime import datetime, timezone
import csv
import hashlib
import io
import json
from pathlib import PurePosixPath
import re
from urllib.parse import quote
import uuid
import zipfile

from .common import Stop, clean_path
from . import livrables5689 as liv, livrables_apercu5689 as apercu

SCHEMA = '''CREATE TABLE IF NOT EXISTS prepared_exports5689(id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, parameters TEXT NOT NULL, item_id TEXT NOT NULL, document_id TEXT NOT NULL, version_id TEXT NOT NULL, format TEXT NOT NULL, destination TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'demande', output_path TEXT NOT NULL DEFAULT '', expected_sha256 TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL DEFAULT '', size INTEGER NOT NULL DEFAULT 0, job_id INTEGER, requested_by TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL, updated TEXT NOT NULL)'''
JOB = 'export_livrable5689'
STATES = {'demande': 'Export demandé', 'en_cours': 'Export en cours', 'ecriture': 'Écriture du fichier', 'exporte': 'Exporté', 'echec': 'Échec de l’export'}
PREVIEW_CSP = {'pdf': "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; object-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'",
               'image': "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"}
SIGNATURES = {'.pdf': b'%PDF-', '.png': b'\x89PNG', '.jpg': b'\xff\xd8\xff', '.jpeg': b'\xff\xd8\xff', '.gif': b'GIF8', '.webp': b'RIFF'}


def schema(desk):
    desk.db.execute(SCHEMA)
    desk.db.commit()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def _nom(row, ext):
    """Nom maîtrisé par le serveur : tiré du titre, débarrassé de ce qui pourrait casser un en-tête ou un chemin."""
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', '_', str(row['title'])).strip(' ._')[:120] or 'document'
    return base + (' v%d' % row['version_number'] if row['version_number'] > 1 else '') + '.' + ext


def _disposition(name, inline=False):
    fallback = re.sub(r'[^A-Za-z0-9._-]+', '_', name)[:120]
    return '%s; filename="%s"; filename*=UTF-8\'\'%s' % ('inline' if inline else 'attachment', fallback, quote(name, safe=''))


def verifier_sortie(fmt, data):
    """Réouverture réelle du fichier produit : un fichier écrit n'est pas encore un fichier valable."""
    if not data:
        return False
    if fmt == 'pdf':
        if not data.startswith(b'%PDF-'):
            return False
        try:
            from ._vendor.pypdf import PdfReader
            return len(PdfReader(io.BytesIO(data)).pages) > 0
        except Exception:   # noqa: BLE001 — PDF illisible, quelle qu'en soit la raison
            return False
    if fmt in ('docx', 'xlsx', 'odt', 'ods', 'zip'):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                return z.testzip() is None and (fmt != 'docx' or 'word/document.xml' in z.namelist())
        except zipfile.BadZipFile:
            return False
    if fmt == 'csv':
        try:
            data.decode('utf-8')
        except UnicodeDecodeError:
            return False
    return True


def _garde_cellule(value):
    """Une cellule qui commencerait comme une formule s'exécuterait à l'ouverture dans un tableur."""
    text = str(value)
    return "'" + text if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text


def convertir(raw, source, cible):
    """Conversions qualifiées seulement. Rend les octets, ou lève une erreur explicite : aucun fichier approximatif."""
    if source == cible:
        return raw
    if source == 'docx' and cible == 'pdf':
        from .pieces510 import docx_to_pdf
        data = docx_to_pdf(raw)
        if not data:
            raise Stop('conversion_indisponible')
        return data
    if source == 'xlsx' and cible == 'csv':
        from .apercu5679 import xlsx_sheets
        sheets = xlsx_sheets(raw, max_rows=100000, max_cols=200)
        if not sheets:
            raise Stop('classeur_illisible')
        out = io.StringIO()
        writer = csv.writer(out, delimiter=';')
        for row in sheets[0][1]:
            writer.writerow([_garde_cellule(c) for c in row])
        return out.getvalue().encode('utf-8')
    raise Stop('format_non_disponible')


def _representation_prete(desk, row, fmt, client):
    """Export déjà fait pour cette version : relu et contrôlé, puis servi tel quel, sans nouvelle conversion."""
    rep = desk.db.execute("SELECT * FROM prepared_representations5689 WHERE item_id=? AND format=? AND verified=1 AND sha256<>'' ORDER BY role='principal' DESC LIMIT 1", (row['id'], fmt)).fetchone()
    if not rep or rep['role'] == 'principal':
        return None
    try:
        info = client.stat(rep['path'])
        data = client.download({**info, 'path': rep['path']})
    except Stop:
        return None
    return data if hashlib.sha256(data).hexdigest() == rep['sha256'] else None


def telecharger(desk, row, fmt='', inline=False, version_demandee='', client=None, caps=None):
    """Réponse brute : la version et le format demandés, ou une erreur. Jamais un autre contenu en remplacement."""
    client = apercu._client(desk, client)
    ext = PurePosixPath(row.get('path') or '').suffix.lower().lstrip('.')
    fmt = str(fmt or ext)
    caps = apercu.capacites(desk) if caps is None else caps
    if row['production_state'] not in ('verified', 'published') or not row.get('path'):
        raise Stop('fichier_pas_encore_cree')
    if fmt not in apercu.formats(row, caps):
        raise Stop('conversion_indisponible' if fmt == 'pdf' else 'format_non_disponible')
    row, raw = apercu.octets(desk, row, client, version_demandee)
    data = raw if fmt == ext else (_representation_prete(desk, row, fmt, client) or convertir(raw, ext, fmt))
    if fmt != ext and not verifier_sortie(fmt, data):
        raise Stop('conversion_non_verifiee')
    mime = liv.MIME.get('.' + fmt, 'application/octet-stream')
    headers = [('X-Content-Type-Options', 'nosniff'), ('Cache-Control', 'private, no-store'), ('Vary', 'Cookie'), ('X-AxiorHub-Version', row['version_id'][:80])]
    if inline:
        family = 'pdf' if fmt == 'pdf' else 'image' if '.' + fmt in apercu.IMAGES else ''
        signature = SIGNATURES.get('.' + fmt)
        if not family or not signature or not data.startswith(signature):   # un fichier qui ne ressemble pas à ce qu'il annonce n'est pas affiché
            raise Stop('apercu_format_non_pris_en_charge')
        return {'status': '200 OK', 'kind': mime, 'body': data, 'frame_self': True, 'csp': PREVIEW_CSP[family], 'headers': headers + [('Content-Disposition', _disposition(_nom(row, fmt), True))]}
    return {'status': '200 OK', 'kind': mime, 'body': data, 'headers': headers + [('Content-Disposition', _disposition(_nom(row, fmt)))]}


def _public(row):
    state = row['state']
    return {'id': row['id'], 'state': state, 'state_label': STATES[state], 'done': state == 'exporte', 'format': row['format'], 'destination': row['destination'], 'job_id': row['job_id'], 'erreur': row['error'], 'cle': row['idempotency_key'],
            'version_id': row['version_id'], 'fichier': PurePosixPath(row['output_path']).name if state == 'exporte' else ''}


def demander(desk, row, fmt, destination, cle, owner, caps=None):
    """Crée le travail d'export. La même clé rend le même travail, même si la réponse précédente s'est perdue ; aucune seconde écriture."""
    schema(desk)
    cle = str(cle or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', cle):
        raise Stop('cle_idempotence_absente')
    parameters = json.dumps([row['document_id'], row['version_id'], str(fmt), str(destination)])
    old = desk.db.execute('SELECT * FROM prepared_exports5689 WHERE idempotency_key=?', (cle,)).fetchone()
    if old:
        if old['parameters'] != parameters:
            raise Stop('cle_idempotence_deja_utilisee')
        return _public(old)
    caps = apercu.capacites(desk) if caps is None else caps
    allowed = apercu.formats(row, caps)
    if str(fmt) not in allowed:
        raise Stop('conversion_indisponible' if str(fmt) == 'pdf' else 'format_non_disponible')
    if str(destination) not in apercu.destinations(desk, row):
        raise Stop('destination_non_autorisee')
    ident, now = uuid.uuid4().hex, _now()
    desk.db.execute('INSERT INTO prepared_exports5689(id,idempotency_key,parameters,item_id,document_id,version_id,format,destination,requested_by,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (ident, cle, parameters, row['id'], row['document_id'], row['version_id'], str(fmt), str(destination), str(owner)[:160], now, now))
    desk.db.commit()
    job = desk.enqueue(JOB, {'export': ident, 'matter': row['matter_id']})
    desk.db.execute('UPDATE prepared_exports5689 SET job_id=? WHERE id=?', (job, ident))
    desk.db.commit()
    desk.audit('livrable5689_export_demande', {'export': ident, 'document': row['document_id'][:40], 'format': str(fmt), 'par': str(owner)[:80]})
    return _public(desk.db.execute('SELECT * FROM prepared_exports5689 WHERE id=?', (ident,)).fetchone())


def etat(desk, ident):
    schema(desk)
    row = desk.db.execute('SELECT * FROM prepared_exports5689 WHERE id=?', (str(ident),)).fetchone()
    if not row:
        raise Stop('export_absent')
    return row


def _chemin_libre(client, folder, name):
    stem, _, ext = name.rpartition('.')
    for n in range(1, 40):
        target = clean_path('%s/%s' % (folder, name if n == 1 else '%s (%d).%s' % (stem, n, ext)))
        try:
            client.stat(target)
        except Stop as ex:
            if str(ex) in ('http_404', 'fichier_nextcloud_introuvable'):
                return target
            raise
    raise Stop('fichier_nom_indisponible')


def _fin(desk, ident, row, item, data_sha, info, size, client):
    now = _now()
    desk.db.execute("UPDATE prepared_exports5689 SET state='exporte', sha256=?, size=?, error='', updated=? WHERE id=?", (data_sha, int(size), now, ident))
    liv._representation(desk, item, row['output_path'], 'export', data_sha, info.get('etag'), size, True)
    desk.db.commit()
    desk.audit('livrable5689_exporte', {'export': ident, 'document': row['document_id'][:40], 'format': row['format'], 'sha256': data_sha})
    return {'ok': True, 'export': ident, 'state': 'exporte', 'message': 'Exporté et relu dans le stockage.'}


def _echec(desk, ident, code):
    desk.db.execute("UPDATE prepared_exports5689 SET state='echec', error=?, updated=? WHERE id=?", (str(code)[:120], _now(), ident))
    desk.db.commit()


def executer(desk, args, client=None):
    """Travail d'export : droits du dossier revérifiés au moment d'agir, fichier converti contrôlé, écriture exclusive, relecture. Reprise sans doublon après coupure."""
    from . import pause5629
    schema(desk)
    ident = str(args.get('export') or '')
    row = etat(desk, ident)
    if row['state'] == 'exporte':
        return {'ok': True, 'export': ident, 'state': 'exporte', 'message': 'Déjà exporté.'}
    client = apercu._client(desk, client)
    item = liv._item(desk, row['item_id'])
    if not item:
        _echec(desk, ident, 'livrable_absent')
        raise Stop('livrable_absent')
    if row['output_path'] and row['expected_sha256']:      # coupure après l'écriture : la preuve se trouve dans le stockage, pas dans notre mémoire
        try:
            info = client.stat(row['output_path'])
            existing = client.download({**info, 'path': row['output_path']})
        except Stop as ex:
            if str(ex) not in ('http_404', 'fichier_nextcloud_introuvable'):
                raise
            existing = None
        if existing is not None:
            if hashlib.sha256(existing).hexdigest() != row['expected_sha256']:
                _echec(desk, ident, 'fichier_different_a_la_destination')
                raise Stop('fichier_different_a_la_destination')
            return _fin(desk, ident, row, item, row['expected_sha256'], info, len(existing), client)
    desk.db.execute("UPDATE prepared_exports5689 SET state='en_cours', updated=? WHERE id=?", (_now(), ident))
    desk.db.commit()
    try:
        pause5629.checkpoint(desk)
        dest = apercu.destinations(desk, item).get(row['destination'])
        if not dest:
            raise Stop('destination_non_autorisee')
        _, raw = apercu.octets(desk, item, client, version_demandee=row['version_id'])
        ext = PurePosixPath(item['path']).suffix.lower().lstrip('.')
        data = convertir(raw, ext, row['format'])
        if not verifier_sortie(row['format'], data):
            raise Stop('conversion_non_verifiee')
        sha = hashlib.sha256(data).hexdigest()
        client.ensure_folder(dest['folder'], dest['boundary'])
        path = row['output_path'] or _chemin_libre(client, dest['folder'], _nom(item, row['format']))
        desk.db.execute("UPDATE prepared_exports5689 SET state='ecriture', output_path=?, expected_sha256=?, updated=? WHERE id=?", (path, sha, _now(), ident))
        desk.db.commit()
        pause5629.checkpoint(desk)
        client.put_file(path, data, liv.MIME.get('.' + row['format'], 'application/octet-stream'))      # exclusif : aucun fichier existant n'est remplacé
        info = client.stat(path)
        back = client.download({**info, 'path': path})
        if hashlib.sha256(back).hexdigest() != sha or not verifier_sortie(row['format'], back):
            raise Stop('export_non_verifie')
    except Stop as ex:
        if str(ex) in ('operation_annulee',):
            raise
        _echec(desk, ident, str(ex))
        raise
    return _fin(desk, ident, etat(desk, ident), item, sha, info, len(back), client)
