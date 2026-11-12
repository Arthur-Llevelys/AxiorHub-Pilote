"""5.6.97 (lot documents-mission, § 13) : « Nouveau document » dans un dossier, à la main ou avec l'IA, et édition versionnée.

Quatre départs, un seul registre : document vierge, modèle du cabinet, fichier importé, préparation avec l'IA. Les trois premiers sont des
commandes de l'avocat : aucun modèle n'est appelé, elles restent disponibles IA en pause. Le quatrième inscrit une demande dans la file existante
(``docrequest520``) : elle sera traitée par le travailleur quand l'IA le permet, jamais ici.

- **Registre commun** : chaque document créé ou version enregistrée est une ligne de ``livrables5689`` (``prepared_items5689`` : document_id et
  dossier immuables, numéro de version, type, chemin, empreinte, auteur, modèle et sa version, état, sources). Pas de second registre : ce module
  ne garde que ses commandes (clé d'idempotence, ``documents5697_ops``) et le texte des versions qu'il a écrites (comparaison, ``documents5697_textes``).
- **Un seul effet par commande** : la commande et la sortie sont réservées (``BEGIN IMMEDIATE`` puis ``livrables5689.reserve``) avant le dépôt ;
  le dépôt est exclusif (``put_file`` : un fichier existant n'est jamais remplacé) ; il est relu et comparé (``constater``). Rejouée après une réponse
  perdue, la commande relit le stockage avant de redéposer.
- **Édition** : une version s'enregistre sur la version lue (numéro attendu) et l'ETag connu du registre (``replace_file`` avec If-Match). Si un autre
  éditeur est passé avant : ``Conflit`` (409) lisible — qui, quand, quelle version — et rien n'est écrasé ; le texte de la personne lui est rendu.
  L'éditeur simple ne remplace que les paragraphes modifiés : styles, numérotations, tableaux, en-têtes et pieds de page restent ceux du fichier.
- Accès : ``acces5697`` (404 neutre) avant toute lecture, liste, téléchargement ou écriture.
"""
import base64
from datetime import datetime, timezone
import difflib
import hashlib
from html import unescape
import json
from pathlib import PurePosixPath
import re
from xml.sax.saxutils import escape as xml_escape

from . import acces5697
from .common import Stop, clean_path, load_matters

SERVICE = 'documents5697'
MODES = {'vierge': 'Document vierge', 'modele': 'Modèle du cabinet', 'import': 'Importer un fichier', 'ia': 'Préparer avec l’IA'}
# type proposé : libellé, nature du registre, dossier de rangement (docrequest520.FOLDER_GROUPS), nature d'une demande à l'IA, champs, rappel
TYPES = {
    'courrier': {'libelle': 'Courrier d’avocat', 'nature': 'courrier', 'rangement': 'courrier', 'ia': 'courrier',
                 'champs': (('destinataire', 'Destinataire'), ('objet', 'Objet'), ('reference', 'Référence'), ('coordonnees', 'Coordonnées'),
                            ('signature', 'Signature'), ('pieces_jointes', 'Pièces jointes')),
                 'rappel': 'Ouvrez l’aperçu pour relire : rien n’est envoyé.'},
    'conclusions': {'libelle': 'Conclusions', 'nature': 'conclusions', 'rangement': 'conclusions', 'ia': 'conclusions',
                    'champs': (('parties', 'Parties et qualités'), ('procedure', 'Procédure'), ('faits', 'Faits et sources'), ('pretentions', 'Prétentions'),
                               ('moyens', 'Moyens'), ('demandes', 'Demandes'), ('pieces', 'Pièces')),
                    'rappel': 'Les champs non établis restent visibles « à compléter ». Aucun dépôt RPVA.'},
    'assignation': {'libelle': 'Assignation', 'nature': 'assignation', 'rangement': 'assignation', 'ia': 'assignation',
                    'champs': (('parties', 'Parties et qualités'), ('procedure', 'Juridiction et procédure'), ('faits', 'Faits et sources'),
                               ('pretentions', 'Prétentions'), ('moyens', 'Moyens'), ('demandes', 'Demandes'), ('pieces', 'Pièces')),
                    'rappel': 'Les champs non établis restent visibles « à compléter ». Aucun dépôt RPVA ni signification.'},
    'bordereau': {'libelle': 'Bordereau de pièces', 'nature': 'autre_document', 'rangement': 'conclusions', 'ia': '',
                  'champs': (), 'rappel': 'La liste des pièces utilisée (numéro, intitulé, version, date) est gelée avec le bordereau.'},
    'cession': {'libelle': 'Cession de fonds ou de bail', 'nature': 'contrat', 'rangement': 'contrat', 'ia': 'contrat',
                'champs': (), 'rappel': 'Choisissez le modèle de cession de la bibliothèque. Aucun acte signé, aucune formalité envoyée.'},
    'conditions_generales': {'libelle': 'Conditions générales', 'nature': 'conditions_generales', 'rangement': 'contrat', 'ia': 'contrat',
                             'champs': (('activite', 'Activité'), ('clients', 'Type de clients'), ('parametres', 'Paramètres et variantes')),
                             'rappel': 'Modèle versionné du cabinet, sinon un document vierge à compléter.'},
    'libre': {'libelle': 'Document libre', 'nature': 'autre_document', 'rangement': '', 'ia': 'autre', 'champs': (), 'rappel': ''},
}
IMPORTS = {'.docx': b'PK', '.odt': b'PK', '.xlsx': b'PK', '.ods': b'PK', '.pdf': b'%PDF', '.txt': None, '.md': None, '.csv': None,
           '.png': b'\x89PNG', '.jpg': b'\xff\xd8', '.jpeg': b'\xff\xd8'}
MAX_IMPORT = 15_000_000
A_COMPLETER = '[À COMPLÉTER : %s]'
TEXTES_GARDES = 20            # versions dont le texte est gardé pour la comparaison, par document (rétention)
SCHEMA = '''
CREATE TABLE IF NOT EXISTS documents5697_ops(acteur TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL, mode TEXT NOT NULL, dossier_id TEXT NOT NULL DEFAULT '',
  document_id TEXT NOT NULL DEFAULT '', item_id TEXT NOT NULL DEFAULT '', chemin TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL,
  resultat TEXT NOT NULL DEFAULT '{}', cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, PRIMARY KEY(acteur, cle));
CREATE TABLE IF NOT EXISTS documents5697_textes(document_id TEXT NOT NULL, version_number INTEGER NOT NULL, item_id TEXT NOT NULL, texte TEXT NOT NULL, auteur TEXT NOT NULL DEFAULT '',
  le TEXT NOT NULL, PRIMARY KEY(document_id, version_number));
'''
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


class Conflit(Stop):
    """Version périmée : 409, avec ce qu'il faut pour le dire en clair (version courante, auteur, date) ; rien n'a été écrit."""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.detail = detail or {}


def schema(desk):
    from . import livrables5689
    livrables5689.ensure_schema(desk)
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def _texte(value, limite):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limite]


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _empreinte(corps):
    return hashlib.sha256(json.dumps(corps, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')).hexdigest()


def _cle(value):
    value = str(value or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,120}', value):
        raise Stop('cle_idempotence_requise')
    return value


def _client(desk):
    from .document_projects import _dav
    return _dav(desk)


def dossier(desk, a, dossier_id):
    """Dossier visible de l'acteur, avec son emplacement ; 404 neutre sinon. Registre unique d'abord, registre historique ensuite."""
    acces5697.exiger(desk, a, dossier_id)
    m = next((x for x in load_matters(desk.c, unfiltered=True) if x['id'] == dossier_id), None)
    if m and m.get('path'):
        return {'id': dossier_id, 'path': clean_path(m['path']), 'libelle': PurePosixPath(m['path']).name or dossier_id}
    try:
        from . import dossiers5697
        d = dossiers5697.lire(desk, a, dossier_id)
        if d.get('chemin'):
            return {'id': d['id'], 'path': clean_path(d['chemin']), 'libelle': '%s · %s' % (d.get('reference', ''), d.get('titre', ''))}
    except (ImportError, Stop):
        pass
    raise Stop('introuvable')


# ------------------------------------------------------------------------------------------------ contenu (sans modèle)
def _docx(paragraphes):
    from . import bibliotheque5639
    return bibliotheque5639.build_docx(paragraphes, 'fr', notice=False)


def _squelette(type_, titre, valeurs):
    """Document vierge structuré : titre, puis chaque donnée attendue du type ; une donnée absente reste visible « à compléter »."""
    t = TYPES[type_]
    paras = [(titre, 'titre')]
    for cle, libelle in t['champs']:
        valeur = _texte((valeurs or {}).get(cle), 2000)
        paras.append(('%s : %s' % (libelle, valeur or A_COMPLETER % libelle.lower()), 'texte'))
    if len(paras) == 1:
        paras.append(('', 'texte'))
    return paras


def _pieces_bordereau(desk, dossier_id, ids):
    """Pièces choisies, lues sur le serveur (jamais reprises du navigateur) ; la liste est gelée avec ses versions et empreintes."""
    from . import contentieux5689
    contentieux5689.schema(desk)
    ids = [str(x) for x in (ids or [])][:300]
    if not ids:
        raise Stop('pieces_a_choisir')
    rows = {r['id']: dict(r) for r in desk.db.execute("SELECT id,cote,partie,numero,titre,version,version_id,sha256,date_piece FROM contentieux_exhibits5689 "
                                                      "WHERE matter=? AND statut='actif'", (dossier_id,))}
    manquantes = [x for x in ids if x not in rows]
    if manquantes:
        raise Stop('piece_inconnue')
    from .contentieux_pieces5689 import ordre_numero
    pieces = sorted((rows[x] for x in dict.fromkeys(ids)), key=lambda p: (p['cote'], ordre_numero(p['numero'])))
    return [{'id': p['id'], 'cote': p['cote'], 'numero': p['numero'], 'titre': p['titre'], 'version': p['version'], 'version_id': p['version_id'],
             'sha256': p['sha256'], 'date': p['date_piece']} for p in pieces]


def _contenu(desk, a, dossier_id, mode, type_, titre, data):
    """(octets, extension, provenance, texte) du document à déposer. Déterministe pour une même demande (rejeu)."""
    if mode == 'vierge':
        provenance = {'mode': 'vierge', 'type': type_}
        if type_ == 'bordereau':
            pieces = _pieces_bordereau(desk, dossier_id, data.get('pieces'))
            paras = [(titre, 'titre')] + [('Pièce n° %s — %s — %s — version %s%s' % (p['numero'], p['titre'] or 'sans intitulé', _date_fr(p['date']) or 'date non renseignée',
                                                                                     p['version'], (' (empreinte %s)' % p['sha256'][:12]) if p['sha256'] else ''), 'liste')
                                          for p in pieces]
            provenance['pieces'] = pieces
        else:
            paras = _squelette(type_, titre, data.get('valeurs'))
        raw = _docx(paras)
        return raw, '.docx', provenance, '\n'.join(p for p, _ in paras)
    if mode == 'modele':
        from . import bibliotheque5639, modeles5684
        ident = _texte(data.get('modele'), 120)
        if not ident:
            raise Stop('modele_a_choisir')
        version = modeles5684.selectionner(desk, ident)          # retiré : refusé ; non validé : utilisable à la main, signalé
        t = bibliotheque5639.template(desk, ident)
        valeurs = {k: str(v) for k, v in (data.get('valeurs') or {}).items() if isinstance(k, str)}
        out = bibliotheque5639.effective(desk, t, valeurs)
        provenance = {'mode': 'modele', 'type': type_, 'modele': ident, 'modele_version': int(version.get('version') or out.get('version') or 1),
                      'modele_etat': version.get('state', ''), 'modele_empreinte': out.get('empreinte_source', '')}
        return out['docx'], '.docx', provenance, out.get('texte', '')
    if mode == 'import':
        nom = _texte(data.get('nom_fichier'), 160)
        ext = PurePosixPath(nom).suffix.lower()
        if ext not in IMPORTS:
            raise Stop('format_non_accepte')
        raw = data.get('octets')
        if raw is None:
            try:
                raw = base64.b64decode(str(data.get('contenu') or ''), validate=True)
            except ValueError:
                raise Stop('contenu_fichier_invalide') from None
        if not raw or len(raw) > MAX_IMPORT:
            raise Stop('fichier_vide_ou_trop_volumineux')
        signature = IMPORTS[ext]
        if signature and not raw.startswith(signature):
            raise Stop('contenu_ne_correspond_pas_au_format')
        return raw, ext, {'mode': 'import', 'type': type_, 'nom_origine': nom}, texte_de(raw, ext)
    raise Stop('mode_inconnu')


def _date_fr(value):
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', str(value or ''))
    return '%s/%s/%s' % (m.group(3), m.group(2), m.group(1)) if m else ''


def texte_de(raw, ext='.docx'):
    if ext == '.docx':
        try:
            return '\n'.join(t for t in paragraphes_de(raw))
        except Stop:
            return ''
    if ext in ('.txt', '.md', '.csv'):
        return raw.decode('utf-8', errors='replace')[:200_000]
    return ''


# ------------------------------------------------------------------------------------------------ création
def _op(desk, a, cle):
    row = desk.db.execute('SELECT * FROM documents5697_ops WHERE acteur=? AND cle=?', (a.compte, cle)).fetchone()
    return dict(row) if row else None


def _maj_op(desk, a, cle, **champs):
    champs['maj_le'] = _now()
    desk.db.execute('UPDATE documents5697_ops SET %s WHERE acteur=? AND cle=?' % ', '.join(k + '=?' for k in champs), tuple(champs.values()) + (a.compte, cle))
    desk.db.commit()


def _reserver_op(desk, a, cle, empreinte, mode, dossier_id, chemin='', sha=''):
    """Commande réservée avant tout effet. Même clé et même corps : la commande existante ; corps différent : 409."""
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        row = desk.db.execute('SELECT * FROM documents5697_ops WHERE acteur=? AND cle=?', (a.compte, cle)).fetchone()
        if row:
            desk.db.rollback()
            if row['empreinte'] != empreinte:
                raise Stop('cle_idempotence_deja_utilisee')
            return dict(row), False
        now = _now()
        desk.db.execute('INSERT INTO documents5697_ops(acteur,cle,empreinte,mode,dossier_id,chemin,sha256,etat,cree_le,maj_le) VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (a.compte, cle, empreinte, mode, dossier_id, chemin, sha, 'reserve', now, now))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    return _op(desk, a, cle), True


def _chemin_libre(desk, client, dossier_, rangement, titre, ext):
    from .docrequest520 import _safe_name, target_folder
    dossier_cible = target_folder(desk, client, {'path': dossier_['path']}, rangement) if rangement else dossier_['path']
    base = _safe_name(titre)
    for n in range(1, 50):
        chemin = clean_path('%s/%s%s%s' % (dossier_cible, base, '' if n == 1 else ' (%d)' % n, ext))
        try:
            client.stat(chemin)
        except Stop as ex:
            if str(ex) in ('fichier_nextcloud_introuvable', 'http_404'):
                return chemin
            raise
    raise Stop('nom_fichier_indisponible')


def creer(desk, acteur, data, cle):
    """« Nouveau document ». ``data`` : dossier, mode (vierge, modele, import, ia), type, titre, puis selon le mode valeurs, modele, pieces,
    nom_fichier + contenu (base64) ou octets, consigne. Rend l'état réel : document vérifié, à vérifier, ou demande en file pour l'IA."""
    schema(desk)
    a = acces5697.acteur_de(acteur)
    cle = _cle(cle)
    dossier_id = _texte(data.get('dossier'), 80)
    d = dossier(desk, a, dossier_id)
    mode, type_ = str(data.get('mode') or ''), str(data.get('type') or 'libre')
    if mode not in MODES:
        raise Stop('mode_inconnu')
    if type_ not in TYPES:
        raise Stop('type_document_invalide')
    titre = _texte(data.get('titre'), 160) or ('%s — %s' % (TYPES[type_]['libelle'], d['libelle']))[:160]
    if mode == 'ia':
        return _demande_ia(desk, a, d, type_, titre, data, cle)
    corps = {'dossier': dossier_id, 'mode': mode, 'type': type_, 'titre': titre, 'valeurs': data.get('valeurs') or {}, 'modele': data.get('modele') or '',
             'pieces': list(data.get('pieces') or []), 'fichier': _sha(data['octets']) if data.get('octets') is not None else _sha(str(data.get('contenu') or '').encode()),
             'nom_fichier': data.get('nom_fichier') or ''}
    empreinte = _empreinte(corps)
    deja = _op(desk, a, cle)
    if deja:
        if deja['empreinte'] != empreinte:
            raise Stop('cle_idempotence_deja_utilisee')
        if deja['etat'] == 'fait':
            return dict(json.loads(deja['resultat']), rejoue=True)
    raw, ext, provenance, texte = _contenu(desk, a, dossier_id, mode, type_, titre, data)
    sha = _sha(raw)
    client = _client(desk)
    chemin = deja['chemin'] if deja and deja['chemin'] else _chemin_libre(desk, client, d, TYPES[type_]['rangement'], titre, ext)
    op, _ = _reserver_op(desk, a, cle, empreinte, mode, dossier_id, chemin, sha)
    return _deposer(desk, a, cle, op, client, d, type_, titre, raw, provenance, texte)


def _deposer(desk, a, cle, op, client, d, type_, titre, raw, provenance, texte):
    from . import livrables5689, pause5629
    from .livrables5689 import MIME
    chemin = op['chemin']
    ext = PurePosixPath(chemin).suffix.lower()
    provenance = dict(provenance, auteur=a.compte, commande=cle)
    item = livrables5689.reserve(desk, SERVICE, a.compte + ':' + cle, '1', matter=d['id'], kind=TYPES[type_]['nature'], title=titre, path=chemin,
                                 mime=MIME.get(ext, 'application/octet-stream'), sha256=_sha(raw), provenance=provenance, owner=a.compte, label=TYPES[type_]['libelle'])
    _maj_op(desk, a, cle, item_id=item['id'], document_id=item['document_id'])
    if not livrables5689.deja_publiee(desk, item, client):
        pause5629.checkpoint(desk)                       # dernier contrôle avant l'effet (pause, arrêt demandé)
        try:
            client.put_file(chemin, raw, MIME.get(ext, 'application/octet-stream'))
        except Stop as ex:
            if str(ex) not in ('http_412', 'http_409'):   # 412 : un fichier existe déjà à cet endroit (dépôt précédent dont la réponse s'est perdue)
                _maj_op(desk, a, cle, etat='echec', resultat=json.dumps({'erreur': str(ex)}))
                raise
    item = livrables5689.constater(desk, item, client, chemin, raw)
    etat = 'fait' if item['production_state'] == 'verified' else 'a_verifier'
    resultat = {'document_id': item['document_id'], 'version': item['version_number'], 'chemin': item['path'], 'etat': item['production_state'],
                'message': ('Document créé et relu dans le dossier : %s.' % PurePosixPath(item['path']).name) if etat == 'fait' else
                           'Dépôt à vérifier : le fichier n’a pas pu être relu à l’identique (%s).' % (item['state_reason'] or 'relecture en attente')}
    if etat == 'fait':
        _garder_texte(desk, item, texte, a.compte)
    _maj_op(desk, a, cle, etat=etat, resultat=json.dumps(resultat, ensure_ascii=False))
    desk.audit('documents5697_cree', {'dossier': d['id'], 'document': item['document_id'][:40], 'mode': provenance.get('mode'), 'etat': etat})
    return resultat


def _demande_ia(desk, a, d, type_, titre, data, cle):
    """« Préparer avec l'IA » : une demande dans la file de rédaction existante, une seule fois par clé ; aucun modèle appelé ici."""
    from . import docrequest520
    from .arret5643 import global_paused
    nature = TYPES[type_]['ia']
    if not nature:
        raise Stop('type_sans_preparation_ia')
    consigne = _texte(data.get('consigne'), 4000)
    if len(consigne) < 8:
        raise Stop('consigne_trop_courte')
    corps = {'dossier': d['id'], 'mode': 'ia', 'type': type_, 'titre': titre, 'consigne': consigne}
    empreinte = _empreinte(corps)
    op, nouveau = _reserver_op(desk, a, cle, empreinte, 'ia', d['id'])
    if op['etat'] == 'en_file':
        return dict(json.loads(op['resultat']), rejoue=True)
    out = docrequest520.submit(desk, '%s — %s\n%s' % (TYPES[type_]['libelle'], titre, consigne), d['id'], nature, mission_id='documents5697-' + _sha((a.compte + '|' + cle).encode())[:24])
    pause = bool(global_paused(desk))
    resultat = {'demande': out['request'], 'travail': out['job_id'], 'etat': 'en_file', 'ia_en_pause': pause,
                'message': ('Demande enregistrée. L’IA est en pause : la préparation commencera à la reprise ; vous pouvez créer un document vierge en attendant.' if pause else
                            'Demande enregistrée : le projet apparaîtra dans « À relire » une fois préparé. Rien ne sera envoyé.')}
    _maj_op(desk, a, cle, etat='en_file', resultat=json.dumps(resultat, ensure_ascii=False))
    return resultat


# ------------------------------------------------------------------------------------------------ lecture
def _courant(desk, document_id):
    row = desk.db.execute("SELECT * FROM prepared_items5689 WHERE document_id=? AND lifecycle='current' ORDER BY version_number DESC LIMIT 1", (str(document_id or ''),)).fetchone()
    if not row:
        raise Stop('introuvable')
    return dict(row)


def _auteur(row):
    try:
        prov = json.loads(row.get('provenance') or '{}')
    except ValueError:
        prov = {}
    return str(prov.get('auteur') or row.get('owner') or '')


def ouvrir(desk, acteur, document_id):
    """Version courante, paragraphes modifiables (Word), historique des versions et choix d'édition disponibles."""
    schema(desk)
    a = acces5697.acteur_de(acteur)
    row = _courant(desk, document_id)
    acces5697.exiger(desk, a, row['matter_id'] or None)
    from . import office440
    ext = PurePosixPath(row['path']).suffix.lower()
    paragraphes, lisible = [], ''
    try:
        raw = _telecharger(desk, _client(desk), row)
        if ext == '.docx':
            paragraphes = paragraphes_de(raw)
        lisible = 'ok'
    except Stop as ex:
        lisible = str(ex)
    versions = [{'version': r['version_number'], 'le': r['updated'], 'auteur': _auteur(dict(r)), 'etat': r['production_state'], 'courante': r['lifecycle'] == 'current',
                 'texte_garde': bool(desk.db.execute('SELECT 1 FROM documents5697_textes WHERE document_id=? AND version_number=?', (row['document_id'], r['version_number'])).fetchone())}
                for r in desk.db.execute("SELECT * FROM prepared_items5689 WHERE document_id=? AND lifecycle IN ('current','replaced') ORDER BY version_number DESC", (row['document_id'],))]
    editeur = office440.settings(desk)
    return {'document_id': row['document_id'], 'titre': row['title'], 'dossier_id': row['matter_id'], 'chemin': row['path'], 'version': row['version_number'],
            'type': row['label'] or row['kind'], 'auteur': _auteur(row), 'le': row['updated'], 'etat': row['production_state'], 'empreinte': row['sha256'],
            'editable': ext == '.docx' and lisible == 'ok', 'paragraphes': paragraphes, 'lecture': lisible, 'versions': versions,
            'editeur_cabinet': bool(editeur.get('enabled')) and ext in ('.docx', '.odt', '.xlsx', '.ods'), 'format': ext.lstrip('.')}


def _telecharger(desk, client, row):
    """Octets de la version courante, vérifiés contre le registre : un fichier modifié hors AxiorHub est signalé, jamais pris pour la version connue."""
    info = client.stat(row['path'])
    data = client.download({**info, 'path': row['path']})
    if row['sha256'] and _sha(data) != row['sha256']:
        raise Conflit('modifie_dans_le_stockage', {'message': 'Le fichier a été modifié dans le stockage depuis la version %d. Rechargez pour le relire.' % row['version_number']})
    return data


def fichier(desk, acteur, document_id):
    """Téléchargement de la version courante (nom, type, octets)."""
    schema(desk)
    a = acces5697.acteur_de(acteur)
    row = _courant(desk, document_id)
    acces5697.exiger(desk, a, row['matter_id'] or None)
    data = _telecharger(desk, _client(desk), row)
    return PurePosixPath(row['path']).name, row['mime_type'] or 'application/octet-stream', data


# ------------------------------------------------------------------------------------------------ éditeur simple (Word)
def _elements(corps):
    """Éléments de premier niveau du corps : (nature, xml). Les paragraphes et tableaux imbriqués (zones de texte) restent dans leur parent."""
    out, i, n = [], 0, len(corps)
    ouvrant = re.compile(r'<(w:[A-Za-z]+)(?=[\s>/])')
    while i < n:
        if corps[i].isspace():
            j = i
            while j < n and corps[j].isspace():
                j += 1
            out.append(('blanc', corps[i:j]))
            i = j
            continue
        m = ouvrant.match(corps, i)
        if not m:
            raise Stop('modele_docx_invalide')
        balise = m.group(1)
        fin_tete = corps.index('>', i)
        if corps[fin_tete - 1] == '/':
            out.append(('p' if balise == 'w:p' else 'autre', corps[i:fin_tete + 1]))
            i = fin_tete + 1
            continue
        profondeur, j = 1, fin_tete + 1
        motif = re.compile(r'<(/?)%s(?=[\s>/])[^>]*?(/?)>' % re.escape(balise))
        while profondeur:
            t = motif.search(corps, j)
            if not t:
                raise Stop('modele_docx_invalide')
            if t.group(1):
                profondeur -= 1
            elif not t.group(2):
                profondeur += 1
            j = t.end()
        out.append(('p' if balise == 'w:p' else 'autre', corps[i:j]))
        i = j
    return out


def _parties_docx(raw):
    import io
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            xml = z.read('word/document.xml').decode('utf-8')
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError):
        raise Stop('modele_docx_invalide') from None
    debut = re.search(r'<w:body>', xml)
    fin = xml.rfind('<w:sectPr')
    if fin < 0 or (debut and fin < debut.end()):
        fin = xml.rfind('</w:body>')
    if not debut or fin < 0:
        raise Stop('modele_docx_invalide')
    return xml, debut.end(), fin


def _texte_paragraphe(xml):
    from .bibliotheque_word5640 import render_text
    return render_text(xml).replace('\n', ' ').strip()


def paragraphes_de(raw):
    """Texte des paragraphes de premier niveau, dans l'ordre (un par ligne de l'éditeur)."""
    xml, debut, fin = _parties_docx(raw)
    return [_texte_paragraphe(x) for k, x in _elements(xml[debut:fin]) if k == 'p']


def _paragraphe(modele, texte):
    """Nouveau paragraphe qui garde la mise en forme (``w:pPr``) et la police du premier passage (``w:rPr``) du paragraphe d'origine."""
    ppr = re.search(r'<w:pPr>.*?</w:pPr>|<w:pPr/>', modele or '', re.S)
    rpr = re.search(r'<w:r(?:\s[^>]*)?>\s*(<w:rPr>.*?</w:rPr>)', modele or '', re.S)
    run = '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr.group(1) if rpr else '', xml_escape(texte)) if texte else ''
    return '<w:p>%s%s</w:p>' % (ppr.group(0) if ppr else '', run)


def remplacer_paragraphes(raw, lignes):
    """Nouveau DOCX : seuls les paragraphes modifiés sont réécrits ; les autres éléments (tableaux, champs, sauts de section) et toutes les autres
    parties du fichier (styles, numérotation, en-têtes, pieds de page, images) sont recopiés à l'octet près."""
    import io
    import zipfile
    xml, debut, fin = _parties_docx(raw)
    elements = _elements(xml[debut:fin])
    paras = [i for i, (k, _) in enumerate(elements) if k == 'p']
    anciens = [_texte_paragraphe(elements[i][1]) for i in paras]
    nouveaux = [unescape(str(x)).replace('\r', '').replace('\n', ' ') for x in lignes]
    sortie = {i: [] for i in paras}           # xml émis à la place de chaque paragraphe d'origine
    fin_ajouts = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=anciens, b=nouveaux, autojunk=False).get_opcodes():
        if op == 'equal':
            for k in range(i1, i2):
                sortie[paras[k]].append(elements[paras[k]][1])
            continue
        modeles = [elements[paras[k]][1] for k in range(i1, i2)] or [elements[paras[min(i1, len(paras) - 1)]][1]] if paras else ['']
        textes = nouveaux[j1:j2]
        xmls = [_paragraphe(modeles[min(n, len(modeles) - 1)], t) for n, t in enumerate(textes)]
        if i1 < len(paras):
            sortie[paras[i1]].extend(xmls)
        else:
            fin_ajouts.extend(xmls)
    morceaux = []
    for i, (k, x) in enumerate(elements):
        morceaux.append(''.join(sortie[i]) if k == 'p' else x)
    corps = ''.join(morceaux) + ''.join(fin_ajouts)
    document = (xml[:debut] + corps + xml[fin:]).encode('utf-8')
    src, out = io.BytesIO(raw), io.BytesIO()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zout.writestr(info, document if info.filename == 'word/document.xml' else zin.read(info.filename))
    return out.getvalue()


# ------------------------------------------------------------------------------------------------ nouvelle version
def _conflit(desk, row, attendue):
    message = ('Ce document a changé depuis votre ouverture : la version %d a été enregistrée%s le %s. Votre texte n’a pas été enregistré et '
               'reste dans l’éditeur : comparez, puis rechargez la version %d ou enregistrez votre texte comme copie.') % (
        row['version_number'], (' par ' + _auteur(row)) if _auteur(row) else '', _date_heure(desk, row['updated']), row['version_number'])
    return Conflit('version_en_conflit', {'version_courante': row['version_number'], 'version_attendue': attendue, 'auteur': _auteur(row), 'le': row['updated'],
                                          'message': message})


def _date_heure(desk, value):
    try:
        from .cockpit530 import tz
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return moment.astimezone(tz(desk)).strftime('%d/%m/%Y à %H h %M')
    except (ValueError, TypeError):
        return str(value or '')


def enregistrer(desk, acteur, document_id, version_attendue, cle, lignes=None, octets=None):
    """Nouvelle version : paragraphes de l'éditeur simple (``lignes``) ou fichier réimporté (``octets``, même format). Écrite seulement sur la
    version lue et l'ETag connu ; sinon ``Conflit``. Relue après dépôt, puis inscrite au registre (version suivante, version précédente gardée)."""
    from . import pause5629
    schema(desk)
    a = acces5697.acteur_de(acteur)
    cle = _cle(cle)
    row = _courant(desk, document_id)
    acces5697.exiger(desk, a, row['matter_id'] or None)
    try:
        attendue = int(version_attendue)
    except (TypeError, ValueError):
        raise Stop('version_attendue_requise') from None
    corps = {'document': row['document_id'], 'version': attendue, 'lignes': list(lignes) if lignes is not None else None,
             'octets': _sha(octets) if octets is not None else None}
    empreinte = _empreinte(corps)
    deja = _op(desk, a, cle)
    if deja:
        if deja['empreinte'] != empreinte:
            raise Stop('cle_idempotence_deja_utilisee')
        if deja['etat'] == 'fait':
            return dict(json.loads(deja['resultat']), rejoue=True)
    client = _client(desk)
    if deja and deja['etat'] == 'reserve' and deja['sha256']:      # réponse perdue : le stockage dit si la version a été écrite
        repris = _reprendre_version(desk, a, cle, deja, row, client)
        if repris:
            return repris
    if row['version_number'] != attendue:
        raise _conflit(desk, row, attendue)
    ext = PurePosixPath(row['path']).suffix.lower()
    base = _telecharger(desk, client, row)
    if lignes is not None:
        if ext != '.docx':
            raise Stop('edition_simple_word_seulement')
        nouveau = remplacer_paragraphes(base, [str(x)[:20000] for x in list(lignes)[:5000]])
        texte = '\n'.join(paragraphes_de(nouveau))
    elif octets is not None:
        signature = IMPORTS.get(ext)
        if not octets or len(octets) > MAX_IMPORT or (signature and not octets.startswith(signature)):
            raise Stop('contenu_ne_correspond_pas_au_format')
        nouveau, texte = octets, texte_de(octets, ext)
    else:
        raise Stop('contenu_absent')
    if _sha(nouveau) == row['sha256']:
        return {'document_id': row['document_id'], 'version': row['version_number'], 'etat': 'inchange', 'message': 'Aucune modification à enregistrer.'}
    etag = row['etag'] or client.stat(row['path']).get('etag', '')
    op, _ = _reserver_op(desk, a, cle, empreinte, 'version', row['matter_id'], row['path'], _sha(nouveau))
    pause5629.checkpoint(desk)
    try:
        client.replace_file(row['path'], nouveau, etag)
    except Stop as ex:
        if str(ex) in ('version_nextcloud_modifiee', 'http_412'):
            _maj_op(desk, a, cle, etat='conflit')
            raise Conflit('version_en_conflit', {'version_courante': row['version_number'], 'version_attendue': attendue, 'auteur': '', 'le': '',
                                                 'message': 'Le fichier a été modifié dans le stockage (autre éditeur) pendant votre travail. Votre texte n’a pas été '
                                                            'enregistré et reste dans l’éditeur : rechargez pour voir l’autre version, ou enregistrez une copie.'}) from None
        raise
    return _constater_version(desk, a, cle, row, client, nouveau, texte)


def _constater_version(desk, a, cle, row, client, nouveau, texte):
    from . import livrables5689
    info = client.stat(row['path'])
    relu = client.download({**info, 'path': row['path']})
    if _sha(relu) != _sha(nouveau):
        _maj_op(desk, a, cle, etat='a_verifier')
        raise Stop('relecture_differente_a_verifier')
    item = livrables5689.nouvelle_version_constatee(desk, row, row['path'], relu, info, service=SERVICE,
                                                    provenance={'producteur': SERVICE, 'auteur': a.compte, 'version_precedente': row['version_id'], 'commande': cle})
    _garder_texte(desk, item, texte, a.compte)
    resultat = {'document_id': item['document_id'], 'version': item['version_number'], 'etat': item['production_state'], 'chemin': item['path'],
                'message': 'Version %d enregistrée et relue. La version %d reste dans l’historique.' % (item['version_number'], row['version_number'])}
    _maj_op(desk, a, cle, etat='fait', item_id=item['id'], document_id=item['document_id'], resultat=json.dumps(resultat, ensure_ascii=False))
    desk.audit('documents5697_version', {'document': item['document_id'][:40], 'version': item['version_number']})
    return resultat


def _reprendre_version(desk, a, cle, op, row, client):
    """Commande déjà réservée dont la réponse s'est perdue : si le fichier porte déjà le contenu attendu, la version est constatée sans réécriture."""
    try:
        info = client.stat(row['path'])
        data = client.download({**info, 'path': row['path']})
    except Stop:
        return None
    if _sha(data) != op['sha256']:
        return None
    known = desk.db.execute('SELECT * FROM prepared_items5689 WHERE document_id=? AND sha256=?', (row['document_id'], op['sha256'])).fetchone()
    base = row if not known else dict(known)
    if known:
        resultat = {'document_id': known['document_id'], 'version': known['version_number'], 'etat': known['production_state'], 'chemin': known['path'],
                    'message': 'Version %d déjà enregistrée.' % known['version_number']}
        _maj_op(desk, a, cle, etat='fait', resultat=json.dumps(resultat, ensure_ascii=False))
        return resultat
    return _constater_version(desk, a, cle, base, client, data, texte_de(data, PurePosixPath(row['path']).suffix.lower()))


def copie(desk, acteur, document_id, lignes, cle):
    """« Enregistrer comme copie » après un conflit : un nouveau document du même dossier, à partir de la version courante, avec le texte de la personne."""
    a = acces5697.acteur_de(acteur)
    row = _courant(desk, document_id)
    acces5697.exiger(desk, a, row['matter_id'] or None)
    if not row['matter_id']:
        raise Stop('dossier_requis')
    base = _telecharger(desk, _client(desk), row)
    nouveau = remplacer_paragraphes(base, [str(x)[:20000] for x in list(lignes or [])[:5000]])
    return creer(desk, a, {'dossier': row['matter_id'], 'mode': 'import', 'type': 'libre', 'titre': '%s (copie)' % row['title'][:150],
                           'nom_fichier': '%s (copie).docx' % row['title'][:120], 'octets': nouveau}, cle)


def _garder_texte(desk, item, texte, auteur):
    """Texte de la version, pour comparer ; seules les ``TEXTES_GARDES`` dernières versions d'un document sont gardées (rétention)."""
    desk.db.execute('INSERT OR REPLACE INTO documents5697_textes(document_id,version_number,item_id,texte,auteur,le) VALUES(?,?,?,?,?,?)',
                    (item['document_id'], item['version_number'], item['id'], str(texte or '')[:400_000], auteur, _now()))
    desk.db.execute('DELETE FROM documents5697_textes WHERE document_id=? AND version_number NOT IN (SELECT version_number FROM documents5697_textes WHERE document_id=? '
                    'ORDER BY version_number DESC LIMIT ?)', (item['document_id'], item['document_id'], TEXTES_GARDES))
    desk.db.commit()


def comparer(desk, acteur, document_id, de, a_version=None, lignes=None):
    """Différences par paragraphe entre la version ``de`` et la version ``a_version`` (ou le texte en cours d'édition ``lignes``)."""
    schema(desk)
    a = acces5697.acteur_de(acteur)
    row = _courant(desk, document_id)
    acces5697.exiger(desk, a, row['matter_id'] or None)

    def texte(version):
        r = desk.db.execute('SELECT texte FROM documents5697_textes WHERE document_id=? AND version_number=?', (row['document_id'], int(version))).fetchone()
        if r:
            return r['texte'].split('\n')
        if int(version) == row['version_number']:
            raw = _telecharger(desk, _client(desk), row)
            return paragraphes_de(raw) if row['path'].lower().endswith('.docx') else texte_de(raw, PurePosixPath(row['path']).suffix.lower()).split('\n')
        raise Stop('texte_de_version_non_conserve')
    gauche = texte(de)
    droite = [str(x) for x in lignes] if lignes is not None else texte(a_version if a_version is not None else row['version_number'])
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=gauche, b=droite, autojunk=False).get_opcodes():
        if op == 'equal':
            out += [{'op': 'egal', 'texte': x} for x in gauche[i1:i2]]
        else:
            out += [{'op': 'retrait', 'texte': x} for x in gauche[i1:i2]] + [{'op': 'ajout', 'texte': x} for x in droite[j1:j2]]
    return {'de': int(de), 'a': 'edition' if lignes is not None else int(a_version if a_version is not None else row['version_number']), 'lignes': out,
            'changements': sum(1 for x in out if x['op'] != 'egal')}


# ------------------------------------------------------------------------------------------------ panneau
def options(desk, acteur, dossier_id):
    """Ce que propose le panneau pour ce dossier : types, modèles de la bibliothèque, pièces actives (bordereau), état de l'IA."""
    from . import bibliotheque5639, contentieux5689
    from .arret5643 import global_paused
    a = acces5697.acteur_de(acteur)
    d = dossier(desk, a, dossier_id)
    modeles = []
    for t in bibliotheque5639.catalog(desk):
        if t.get('langue', 'fr') != 'fr':
            continue
        modeles.append({'id': t['id'], 'titre': t['titre'], 'categorie': t.get('categorie', ''), 'variables': [{'cle': k, 'libelle': lab} for k, lab, _ in t['variables']][:40]})
    contentieux5689.schema(desk)
    pieces = [dict(r) for r in desk.db.execute("SELECT id,cote,numero,titre,version,date_piece FROM contentieux_exhibits5689 WHERE matter=? AND statut='actif' ORDER BY cote,numero_cle",
                                               (d['id'],))]
    return {'dossier': d, 'types': [{'id': k, 'libelle': v['libelle'], 'champs': [{'cle': c, 'libelle': l} for c, l in v['champs']], 'rappel': v['rappel'], 'ia': bool(v['ia'])}
                                    for k, v in TYPES.items()],
            'modes': MODES, 'modeles': modeles, 'pieces': pieces, 'ia_en_pause': bool(global_paused(desk))}


def documents_du_dossier(desk, acteur, dossier_id, limite=50):
    """Documents du registre commun pour ce dossier (version courante), après le contrôle d'accès."""
    schema(desk)
    a = acces5697.acteur_de(acteur)
    acces5697.exiger(desk, a, dossier_id)
    return [{'document_id': r['document_id'], 'titre': r['title'], 'version': r['version_number'], 'type': r['label'] or r['kind'], 'le': r['updated'],
             'etat': r['production_state'], 'chemin': r['path']}
            for r in desk.db.execute("SELECT * FROM prepared_items5689 WHERE matter_id=? AND lifecycle='current' ORDER BY updated DESC LIMIT ?", (dossier_id, int(limite)))]
