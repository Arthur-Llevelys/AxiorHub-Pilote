"""5.6.89 : dossier d'audience figé et paquet destiné à la communication (audit 5.6.88, chapitres 37 et 38).

Dossier d'audience : photographie à sa préparation, jamais réécrite. Une projection recalculée dit seulement ce qui a changé depuis, partie par partie. Les notes internes y sont identifiées, et une version communicable les retire.

SubmissionPackage : un manifeste liste dossier, destinataires, canal, fichiers, versions, empreintes, bordereau et contrôles. L'autorisation porte sur l'empreinte de ce manifeste et sur rien d'autre.
Tout changement pertinent (fichier, version, destinataire, canal, bordereau, contrôles) l'invalide ; le service d'effet relit les fichiers et refait le contrôle, quel que soit l'état affiché.

Aucun dépôt n'existe ici : AxiorHub n'a aucune interface officielle de dépôt qualifiée, et rien n'en annonce une. Une archive est une copie de travail, pas une preuve de dépôt. Un reçu ne pourrait venir que d'un canal
réellement intégré (``CANAUX_INTEGRES``, vide) et serait archivé à part.
"""
import hashlib
import io
import json
from pathlib import PurePosixPath
import re
import uuid
import zipfile

from .common import Stop
from . import contentieux5689 as base, contentieux_actes5689 as actes, contentieux_pieces5689 as pieces

CANAUX = {'communication_confrere': 'Communication au confrère', 'depot_par_l_avocat': 'Dépôt fait par l’avocat, hors AxiorHub', 'courrier': 'Envoi postal', 'autre': 'Autre'}
CANAUX_INTEGRES = frozenset()      # aucun canal de dépôt n'est intégré : aucun reçu ne peut être enregistré
ACTIONS = {'envoi': 'Envoi', 'depot': 'Dépôt'}
ETATS = {'brouillon': 'À autoriser', 'autorise': 'Autorisé pour ce manifeste', 'invalide': 'Autorisation périmée : à vérifier et réautoriser'}
MENTION = 'Une archive n’est pas une preuve de dépôt ou de communication.'
MAX_ARCHIVE = 200 * 1024 * 1024


# ------------------------------------------------------------------------------------------------ dossier d'audience
def _projection(desk, mid):
    """Contenu comparable du dossier d'audience : aucun horodatage ni compteur, sinon tout semblerait changer."""
    from . import assertions5689, memoire5651, modele5651
    matrice = base.matrice(desk, mid)
    assertions5689.ensure_schema(desk)
    faits = [{'date': r['date_evenement'], 'evenement': r['statement'], 'nature': assertions5689.KINDS[r['kind']]} for r in desk.db.execute("SELECT date_evenement,statement,kind FROM case_assertions5689 WHERE matter=? AND kind='procedural_event' AND status NOT IN ('rejected','superseded')", (mid,))]
    chronologie = sorted(faits + [{'date': next((v for v in (e.get('dates') or {}).values() if v), ''), 'evenement': e.get('title') or '', 'nature': e.get('nature') or ''} for e in modele5651.events(desk, mid)], key=lambda x: (x['date'] == '', x['date'], x['evenement']))
    demandes, points, preuves, objections, cites = [], [], [], [], {}
    for c in matrice:
        demandes.append({'cote': c['cote'], 'titre': c['titre'], 'demande': c['demande'], 'montant': c['montant'], 'devise': c['devise'], 'statut': c['statut']})
        for x in c['elements']['fait']:
            points.append({'pretention': c['titre'], 'fait': x['texte'], 'etat': x['etat'], 'statut': x['statut'], 'affirme_par': x['affirme_par']})
        for k in ('fait', 'piece'):
            for x in c['elements'][k]:
                if x['exhibit_id']:
                    preuves.append({'pretention': c['titre'], 'element': x['texte'], 'piece': x['citation'] or actes.ABSENTE})
                    cites[x['exhibit_id']] = True
        reponses = {x['parent_id']: x for x in c['elements']['reponse']}
        for x in c['elements']['objection']:
            objections.append({'pretention': c['titre'], 'objection': x['texte'], 'reponse': (reponses.get(x['id']) or {}).get('texte', ''), 'statut': x['statut']})
    registre = pieces.liste(desk, mid)['pieces']
    a_ouvrir = [{'citation': p['citation'], 'titre': p['titre'], 'path': p['path'], 'sha256': p['sha256'], 'etat_fichier': p['etat_fichier']} for p in registre if p['id'] in cites]
    decisions = [{'demande': d['titre'], 'attendu': d['demande']} for d in demandes if d['cote'] == 'cabinet' and d['statut'] == 'valide']
    notes = [{'interne': True, 'nature': memoire5651.DECISION_KINDS.get(d['kind'], d['kind']), 'objet': d['subject'], 'texte': d['text']} for d in memoire5651.decisions(desk, mid)]
    return {'chronologie': chronologie, 'demandes': demandes, 'points_discutes': points, 'preuves': preuves, 'objections': objections, 'decisions_attendues': decisions, 'pieces_a_ouvrir': a_ouvrir, 'notes_internes': notes}


LIBELLES_PARTIES = {'chronologie': 'Chronologie', 'demandes': 'Demandes', 'points_discutes': 'Points discutés', 'preuves': 'Preuves', 'objections': 'Objections', 'decisions_attendues': 'Décisions attendues', 'pieces_a_ouvrir': 'Pièces à ouvrir', 'notes_internes': 'Notes internes'}


def preparer_audience(desk, mid, data, owner, role):
    """Fige une nouvelle version. La précédente reste, inchangée."""
    base.schema(desk)
    base.garde(desk, mid)
    cle = base.cle_de(data.get('cle'))
    audience = str(data.get('audience_id') or '')[:80]
    params = base.empreinte([mid, audience])
    old = base.deja_fait(desk, 'contentieux_audiences5689', cle, params)
    if old:
        return vue_audience(desk, old['id'])
    if audience:
        from . import audiences5688
        audiences5688.schema(desk)
        fiche = desk.db.execute('SELECT matter FROM audiences5688 WHERE id=?', (audience,)).fetchone()
        if not fiche or fiche['matter'] != mid:
            raise Stop('audience_absente')
    contenu = _projection(desk, mid)
    version = int(desk.db.execute('SELECT COALESCE(MAX(version),0) FROM contentieux_audiences5689 WHERE matter=?', (mid,)).fetchone()[0]) + 1
    ident, now = uuid.uuid4().hex, base.maintenant()
    with base.transaction(desk):
        desk.db.execute('INSERT INTO contentieux_audiences5689(id,matter,cle,parametres,audience_id,version,contenu,empreinte,empreintes_parties,prepare_par,prepare_le) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (ident, mid, cle, params, audience, version, json.dumps(contenu, ensure_ascii=False), base.empreinte(contenu), json.dumps({k: base.empreinte(v) for k, v in contenu.items()}), str(owner)[:160], now))
        base.journal(desk, mid, 'audience', ident, 'preparation', owner, '', {}, {'version': version})
    return vue_audience(desk, ident)


def vue_audience(desk, ident):
    """Le contenu lu est celui du jour de préparation ; l'indicateur compare à l'état actuel sans rien réécrire."""
    base.schema(desk)
    row = desk.db.execute('SELECT * FROM contentieux_audiences5689 WHERE id=?', (str(ident or ''),)).fetchone()
    if not row:
        raise Stop('audience_absente')
    base.garde(desk, row['matter'])
    actuel = _projection(desk, row['matter'])
    figees = json.loads(row['empreintes_parties'])
    changees = [k for k, v in actuel.items() if base.empreinte(v) != figees.get(k)]
    out = base._ligne(row)
    out.pop('empreintes_parties', None)
    return {**out, 'contenu': json.loads(row['contenu']), 'modifie_depuis': bool(changees), 'parties_modifiees': [LIBELLES_PARTIES.get(k, k) for k in changees]}


def audiences(desk, mid):
    base.schema(desk)
    base.garde(desk, mid)
    return [{'id': r['id'], 'version': r['version'], 'prepare_le': r['prepare_le'], 'prepare_par': r['prepare_par']} for r in desk.db.execute('SELECT id,version,prepare_le,prepare_par FROM contentieux_audiences5689 WHERE matter=? ORDER BY version DESC LIMIT 20', (mid,))]


def audience_communicable(contenu):
    """Ce qui pourrait sortir : ni notes internes, ni réponses envisagées, ni points de travail."""
    return {k: contenu[k] for k in ('chronologie', 'demandes')} | {'pieces_a_ouvrir': [{'citation': p['citation'], 'titre': p['titre']} for p in contenu['pieces_a_ouvrir'] if p['citation'].startswith('pièce n°')]}


# ------------------------------------------------------------------------------------------------ paquet
def _paquet(desk, ident):
    row = desk.db.execute('SELECT * FROM contentieux_paquets5689 WHERE id=?', (str(ident or ''),)).fetchone()
    if not row:
        raise Stop('paquet_absent')
    return row


def _lire(client, path):
    info = client.stat(path)
    raw = client.download(info)
    return info, raw


def _destinataires(valeur):
    if not isinstance(valeur, list) or not valeur:
        raise Stop('destinataire_requis')
    out = []
    for d in valeur[:10]:
        if not isinstance(d, dict) or len(str(d.get('nom') or '').strip()) < 2:
            raise Stop('destinataire_requis')
        out.append({'nom': base._texte(d.get('nom'), 200), 'qualite': base._texte(d.get('qualite'), 100), 'adresse': base._texte(d.get('adresse'), 300)})
    return out


def _bordereau(desk, mid, acte):
    """Le bordereau vient du même registre que l'acte : les seules pièces du cabinet qu'il cite."""
    if not acte:
        return []
    section = next((s for s in actes._sections(desk, acte['id']) if s['kind'] == 'bordereau'), None)
    ids = pieces.JETON.findall(section['texte']) if section else []
    lignes = {r['id']: r for r in desk.db.execute("SELECT * FROM contentieux_exhibits5689 WHERE matter=? AND statut='actif' AND cote='cabinet'", (mid,))}
    return [{'piece_id': x, 'numero': lignes[x]['numero'], 'titre': lignes[x]['titre']} for x in ids if x in lignes]


def _contenu(desk, mid, acte, annexes, client):
    """Fichiers réellement lus, empreintes calculées à l'instant. Une note, une correspondance confidentielle ou un projet n'y entre pas : ils sont listés comme exclus."""
    from . import pause5629
    fichiers, exclus, problemes = [], [], []
    bordereau = _bordereau(desk, mid, acte)
    if acte:
        pause5629.checkpoint(desk)
        info, raw = _lire(client, acte['path'])
        fichiers.append({'role': 'acte', 'titre': acte['titre'], 'path': acte['path'], 'version_id': str(info.get('etag') or ''), 'sha256': hashlib.sha256(raw).hexdigest(), 'taille': len(raw)})
    for ligne in bordereau:
        piece = pieces._piece(desk, mid, ligne['piece_id'])
        if pieces.est_interne(desk, mid, piece['path']):
            exclus.append({'titre': piece['titre'], 'path': piece['path'], 'motif': 'Document interne ou confidentiel'})
            problemes.append('%s est interne : à retirer du bordereau ou à reclasser.' % pieces.citation(piece))
            continue
        if not piece['path']:
            problemes.append('%s n’a pas de fichier.' % pieces.citation(piece))
            continue
        pause5629.checkpoint(desk)
        try:
            info, raw = _lire(client, piece['path'])
        except Stop:
            problemes.append('%s : fichier illisible.' % pieces.citation(piece))
            continue
        sha = hashlib.sha256(raw).hexdigest()
        if piece['sha256'] and sha != piece['sha256']:
            problemes.append('%s : le fichier a changé depuis son enregistrement.' % pieces.citation(piece))
        fichiers.append({'role': 'piece', 'titre': piece['titre'], 'numero': piece['numero'], 'piece_id': piece['id'], 'path': piece['path'], 'version_id': str(info.get('etag') or ''), 'sha256': sha, 'taille': len(raw)})
    for path in annexes:
        path = str(path or '').strip()
        if not path:
            continue
        if pieces.est_interne(desk, mid, path):
            exclus.append({'titre': PurePosixPath(path).name, 'path': path, 'motif': 'Document interne ou confidentiel'})
            continue
        pause5629.checkpoint(desk)
        info, raw = _lire(client, path)
        fichiers.append({'role': 'annexe', 'titre': PurePosixPath(path).name, 'path': path, 'version_id': str(info.get('etag') or ''), 'sha256': hashlib.sha256(raw).hexdigest(), 'taille': len(raw)})
    return fichiers, bordereau, exclus, problemes


def _controles(desk, acte, problemes):
    """Contrôles du paquet : ceux de l'acte, s'ils sont à jour, et ceux des fichiers. Un contrôle ancien ne vaut pas : il compte comme bloquant."""
    lignes = []
    if acte:
        a_jour = bool(acte['controle_empreinte']) and acte['controle_empreinte'] == actes.empreinte_texte(desk, acte['id'])
        lignes.append({'id': 'acte', 'libelle': 'Contrôles avant acte', 'etat': 'bloquant' if acte['etat'] == 'bloque' or not a_jour else ('reserve' if acte['etat'] == 'reserves' else 'ok'), 'detail': actes.ETATS.get(acte['etat'], acte['etat']) if a_jour else 'Contrôles à refaire : l’acte ou le registre a changé.'})
    lignes.append({'id': 'fichiers', 'libelle': 'Fichiers du paquet', 'etat': 'bloquant' if problemes else 'ok', 'detail': ' '.join(problemes)})
    return {'etat': 'bloquant' if any(x['etat'] == 'bloquant' for x in lignes) else ('reserve' if any(x['etat'] == 'reserve' for x in lignes) else 'ok'), 'lignes': lignes}


def _manifeste(mid, version, acte_id, destinataires, canal, fichiers, bordereau, controles, exclus):
    return {'dossier': mid, 'version': version, 'acte': acte_id, 'destinataires': destinataires, 'canal': canal, 'fichiers': fichiers, 'bordereau': bordereau, 'controles': controles, 'exclus': exclus, 'depot': {'effectue': False, 'preuve': None}}


def _enregistrer(desk, ident, version, destinataires, canal, fichiers, bordereau, controles, exclus, manifeste, etat):
    sha = base.empreinte(manifeste)
    desk.db.execute('UPDATE contentieux_paquets5689 SET version=?,destinataires=?,canal=?,fichiers=?,bordereau=?,controles=?,exclus=?,manifeste=?,manifeste_sha=?,etat=?,updated=? WHERE id=?',
                    (version, json.dumps(destinataires, ensure_ascii=False), canal, json.dumps(fichiers, ensure_ascii=False), json.dumps(bordereau, ensure_ascii=False), json.dumps(controles, ensure_ascii=False), json.dumps(exclus, ensure_ascii=False), json.dumps(manifeste, ensure_ascii=False), sha, etat, base.maintenant(), ident))
    return sha


def creer(desk, mid, data, owner, role, client=None):
    from .office440 import dav_client
    base.schema(desk)
    base.garde(desk, mid)
    cle = base.cle_de(data.get('cle'))
    canal = str(data.get('canal') or '')
    if canal not in CANAUX:
        raise Stop('canal_invalide')
    destinataires = _destinataires(data.get('destinataires'))
    acte_id, annexes = str(data.get('acte_id') or ''), data.get('annexes') if isinstance(data.get('annexes'), list) else []
    params = base.empreinte([mid, acte_id, destinataires, canal, annexes])
    old = base.deja_fait(desk, 'contentieux_paquets5689', cle, params)
    if old:
        return vue(desk, old['id'])
    acte = None
    if acte_id:
        acte = actes._acte(desk, acte_id)
        if acte['matter'] != mid:
            raise Stop('acte_absent')
        if not acte['path']:
            raise Stop('acte_non_publie')
    if not acte and not annexes:
        raise Stop('paquet_vide')
    client = client or dav_client(desk)
    fichiers, bordereau, exclus, problemes = _contenu(desk, mid, acte, annexes, client)
    controles = _controles(desk, acte, problemes)
    ident, now = uuid.uuid4().hex, base.maintenant()
    with base.transaction(desk):
        desk.db.execute('INSERT INTO contentieux_paquets5689(id,matter,cle,parametres,acte_id,canal,etat,cree_par,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)', (ident, mid, cle, params, acte_id, canal, 'brouillon', str(owner)[:160], now, now))
        sha = _enregistrer(desk, ident, 1, destinataires, canal, fichiers, bordereau, controles, exclus, _manifeste(mid, 1, acte_id, destinataires, canal, fichiers, bordereau, controles, exclus), 'brouillon')
        base.journal(desk, mid, 'paquet', ident, 'creation', owner, '', {}, {'manifeste_sha': sha, 'fichiers': len(fichiers), 'exclus': len(exclus)})
    return vue(desk, ident)


def vue(desk, ident):
    """Lecture sans réseau : ``ecarts_locaux`` compare au registre, et « Revérifier » relit les fichiers."""
    row = _paquet(desk, ident)
    base.garde(desk, row['matter'])
    out = base._ligne(row)
    for k in ('destinataires', 'fichiers', 'bordereau', 'controles', 'exclus', 'manifeste'):
        out[k] = json.loads(row[k] or '{}')
    autorisation = desk.db.execute("SELECT * FROM contentieux_autorisations5689 WHERE paquet_id=? ORDER BY at DESC LIMIT 1", (row['id'],)).fetchone()
    out['autorisation'] = dict(autorisation) if autorisation else None
    out['etat_libelle'], out['canal_libelle'], out['mention'] = ETATS.get(row['etat'], row['etat']), CANAUX.get(row['canal'], row['canal']), MENTION
    out['ecarts_locaux'] = _ecarts_locaux(desk, row)
    out['recus'] = [dict(r) for r in desk.db.execute('SELECT * FROM contentieux_recus5689 WHERE paquet_id=?', (row['id'],))]
    return out


def _ecarts_locaux(desk, row):
    ecarts = []
    for f in json.loads(row['fichiers'] or '[]'):
        if f.get('piece_id'):
            piece = desk.db.execute('SELECT numero,statut,sha256 FROM contentieux_exhibits5689 WHERE id=?', (f['piece_id'],)).fetchone()
            if not piece or piece['statut'] != 'actif' or (piece['sha256'] and piece['sha256'] != f['sha256']) or piece['numero'] != f.get('numero'):
                ecarts.append('La pièce « %s » a changé au registre.' % f['titre'])
    if row['acte_id']:
        acte = desk.db.execute('SELECT sha256 FROM contentieux_actes5689 WHERE id=?', (row['acte_id'],)).fetchone()
        depose = next((f for f in json.loads(row['fichiers'] or '[]') if f['role'] == 'acte'), None)
        if acte and depose and acte['sha256'] != depose['sha256']:
            ecarts.append('L’acte a été republié depuis ce manifeste.')
    return ecarts


def liste(desk, mid):
    base.schema(desk)
    base.garde(desk, mid)
    return [{'id': r['id'], 'version': r['version'], 'etat': r['etat'], 'etat_libelle': ETATS.get(r['etat'], r['etat']), 'canal': CANAUX.get(r['canal'], r['canal']), 'updated': r['updated']} for r in desk.db.execute('SELECT id,version,etat,canal,updated FROM contentieux_paquets5689 WHERE matter=? ORDER BY created DESC LIMIT 30', (mid,))]


def _invalider(desk, row, cause):
    """Toute autorisation valide tombe, sans ménagement : l'ancien accord ne couvre plus ce paquet. L'historique reste."""
    now = base.maintenant()
    n = desk.db.execute("UPDATE contentieux_autorisations5689 SET etat='invalidee',invalidee_le=?,cause=? WHERE paquet_id=? AND etat='valide'", (now, str(cause)[:300], row['id'])).rowcount
    if n:
        base.journal(desk, row['matter'], 'paquet', row['id'], 'autorisation_invalidee', 'serveur', str(cause)[:300])
    return n


def modifier(desk, ident, data, owner, role, client=None):
    """Nouvelle version du manifeste, relue. Une destination, un canal ou un fichier changé : l'autorisation tombe dans la même transaction."""
    from .office440 import dav_client
    row = _paquet(desk, ident)
    base.garde(desk, row['matter'])
    base.version_attendue(row, data.get('version'), 'version')
    destinataires = _destinataires(data['destinataires']) if 'destinataires' in data else json.loads(row['destinataires'])
    canal = str(data.get('canal') or row['canal'])
    if canal not in CANAUX:
        raise Stop('canal_invalide')
    annexes = data['annexes'] if isinstance(data.get('annexes'), list) else [f['path'] for f in json.loads(row['fichiers']) if f['role'] == 'annexe']
    acte = actes._acte(desk, row['acte_id']) if row['acte_id'] else None
    client = client or dav_client(desk)
    fichiers, bordereau, exclus, problemes = _contenu(desk, row['matter'], acte, annexes, client)
    controles = _controles(desk, acte, problemes)
    version = row['version'] + 1
    with base.transaction(desk):
        _invalider(desk, row, 'Paquet modifié')
        sha = _enregistrer(desk, row['id'], version, destinataires, canal, fichiers, bordereau, controles, exclus, _manifeste(row['matter'], version, row['acte_id'], destinataires, canal, fichiers, bordereau, controles, exclus), 'brouillon')
        base.journal(desk, row['matter'], 'paquet', row['id'], 'modification', owner, '', {'manifeste_sha': row['manifeste_sha']}, {'manifeste_sha': sha})
    return vue(desk, ident)


def verifier(desk, ident, client=None):
    """Relit tout ce que dit le manifeste. Le moindre écart invalide l'autorisation. Rend la liste des écarts."""
    from .office440 import dav_client
    row = _paquet(desk, ident)
    base.garde(desk, row['matter'])
    client = client or dav_client(desk)
    acte = actes._acte(desk, row['acte_id']) if row['acte_id'] else None
    annexes = [f['path'] for f in json.loads(row['fichiers']) if f['role'] == 'annexe']
    try:
        fichiers, bordereau, exclus, problemes = _contenu(desk, row['matter'], acte, annexes, client)
    except Stop as ex:
        fichiers, bordereau, exclus, problemes = [], [], [], ['Lecture impossible : ' + str(ex).split(':')[0]]
    controles = _controles(desk, acte, problemes)
    ecarts = []
    if base.empreinte(_manifeste(row['matter'], row['version'], row['acte_id'], json.loads(row['destinataires']), row['canal'], fichiers, bordereau, controles, exclus)) != row['manifeste_sha']:
        anciens = {f['path']: f for f in json.loads(row['fichiers'])}
        nouveaux = {f['path']: f for f in fichiers}
        ecarts += ['Fichier modifié : %s.' % anciens[p]['titre'] for p in anciens if p in nouveaux and (nouveaux[p]['sha256'] != anciens[p]['sha256'] or nouveaux[p]['version_id'] != anciens[p]['version_id'])]
        ecarts += ['Fichier disparu : %s.' % anciens[p]['titre'] for p in anciens if p not in nouveaux]
        ecarts += ['Fichier ajouté : %s.' % nouveaux[p]['titre'] for p in nouveaux if p not in anciens]
        ecarts = ecarts or ['Le manifeste ne correspond plus : bordereau ou contrôles ont changé.']
    if ecarts:
        with base.transaction(desk):
            _invalider(desk, row, ' '.join(ecarts))
            if row['etat'] == 'autorise':
                desk.db.execute("UPDATE contentieux_paquets5689 SET etat='invalide',updated=? WHERE id=?", (base.maintenant(), row['id']))
    return {'conforme': not ecarts, 'ecarts': ecarts}


def autoriser(desk, ident, data, owner, role, client=None):
    """Décision humaine sur une empreinte précise, après relecture des fichiers. Rien de ce qui suit n'en élargit la portée."""
    base.garde_role(role)
    row = _paquet(desk, ident)
    base.garde(desk, row['matter'])
    action = str(data.get('action') or '')
    if action not in ACTIONS:
        raise Stop('action_inconnue')
    if str(data.get('manifeste_sha') or '') != row['manifeste_sha']:
        raise Stop('version_en_conflit')
    if json.loads(row['controles'] or '{}').get('etat') == 'bloquant':
        raise Stop('controles_bloquants')
    constat = verifier(desk, ident, client)
    if not constat['conforme']:
        raise Stop('manifeste_modifie')
    with base.transaction(desk):
        _invalider(desk, row, 'Nouvelle autorisation')
        desk.db.execute('INSERT INTO contentieux_autorisations5689(id,paquet_id,manifeste_sha,action,acteur,at) VALUES(?,?,?,?,?,?)', (uuid.uuid4().hex, row['id'], row['manifeste_sha'], action, str(owner)[:160], base.maintenant()))
        desk.db.execute("UPDATE contentieux_paquets5689 SET etat='autorise',updated=? WHERE id=?", (base.maintenant(), row['id']))
        base.journal(desk, row['matter'], 'paquet', row['id'], 'autorisation', owner, '', {}, {'manifeste_sha': row['manifeste_sha'], 'action': action})
    return vue(desk, ident)


def exiger_autorisation(desk, ident, client=None):
    """Contrôle de tout service d'effet : autorisation valide sur l'empreinte courante, puis relecture réelle. Ni l'état affiché ni l'ancien accord ne suffisent."""
    row = _paquet(desk, ident)
    base.garde(desk, row['matter'])
    accord = desk.db.execute("SELECT * FROM contentieux_autorisations5689 WHERE paquet_id=? AND etat='valide' AND manifeste_sha=? ORDER BY at DESC LIMIT 1", (row['id'], row['manifeste_sha'])).fetchone()
    if not accord:
        raise Stop('autorisation_absente_ou_perimee')
    if not verifier(desk, ident, client)['conforme']:
        raise Stop('autorisation_absente_ou_perimee')
    return dict(accord)


def archive(desk, ident, owner, client=None):
    """Copie de travail du contenu autorisé, renvoyée à la personne et non conservée. Le manifeste y dit qu'aucun dépôt n'a eu lieu."""
    from . import pause5629
    from .office440 import dav_client
    client = client or dav_client(desk)
    exiger_autorisation(desk, ident, client)
    row = _paquet(desk, ident)
    tampon = io.BytesIO()
    total = 0
    with zipfile.ZipFile(tampon, 'w', zipfile.ZIP_DEFLATED) as zip_:
        zip_.writestr('manifeste.json', json.dumps({**json.loads(row['manifeste']), 'manifeste_sha': row['manifeste_sha'], 'mention': MENTION}, ensure_ascii=False, indent=2))
        for rang, f in enumerate(json.loads(row['fichiers']), 1):
            pause5629.checkpoint(desk)
            _, raw = _lire(client, f['path'])
            if hashlib.sha256(raw).hexdigest() != f['sha256']:
                raise Stop('autorisation_absente_ou_perimee')
            total += len(raw)
            if total > MAX_ARCHIVE:
                raise Stop('archive_trop_volumineuse')
            zip_.writestr('%02d-%s' % (rang, re.sub(r'[\\/:*?"<>|]', '_', PurePosixPath(f['path']).name)), raw)
    with base.transaction(desk):
        base.journal(desk, row['matter'], 'paquet', row['id'], 'archive', owner, MENTION, {}, {'manifeste_sha': row['manifeste_sha']})
    desk.audit('contentieux5689_archive', {'matter': row['matter'], 'paquet': row['id'], 'owner': owner})
    return tampon.getvalue(), 'paquet-%s-v%d.zip' % (re.sub(r'[^A-Za-z0-9_-]', '_', row['matter'])[:40], row['version'])


def enregistrer_recu(desk, ident, canal, reference, sha256='', details=None):
    """Prévu pour un canal réellement intégré, archivé à part. Aujourd'hui, aucun : l'appel est refusé, et aucun état « déposé » n'existe."""
    if canal not in CANAUX_INTEGRES:
        raise Stop('canal_non_integre')
    row = _paquet(desk, ident)
    desk.db.execute('INSERT INTO contentieux_recus5689(id,paquet_id,canal,reference,sha256,recu_le,details) VALUES(?,?,?,?,?,?,?)', (uuid.uuid4().hex, row['id'], canal, str(reference)[:300], str(sha256)[:64], base.maintenant(), json.dumps(details or {}, ensure_ascii=False)))
    desk.db.commit()
