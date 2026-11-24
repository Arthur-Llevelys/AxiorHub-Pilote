"""5.6.109 (lot L, F01) : barre des pièces jointes du lecteur des courriels — identité des pièces, classement d'une pièce ou d'un lot.

Demande du cabinet (cahier 5.6.109, « Barre des pièces jointes dans Courriels » et « Classer et enregistrer les pièces jointes ») :

- **Deux destinations distinctes.** « Tout enregistrer » télécharge un ZIP sur le poste (route ``pieces-zip`` du lecteur, inchangée).
  « Classer » copie les pièces choisies dans le dossier du cabinet, sans déplacer ni marquer le message IMAP, sans rien envoyer, sans
  toucher aux règles de classement automatique (deux choix distincts, panneau 5.6.43 inchangé).
- **Aucune nouvelle copie sans reçu.** Le classement est monté sur ``correspondances5643`` : ``prepare`` (proposition versionnée, aucun
  fichier écrit : c'est l'aperçu avant copie), ``confirm`` (clé de requête : double clic et deux onglets rejoignent le même travail) et le
  travail déterministe ``file_mail_attachments5643`` (``run_file_attachments`` : dépôt exclusif, relecture, empreinte comparée, reçu par
  pièce dans ``mail_archive_assets``). Le mode « pièces » de l'ancien « Enregistrer dans Nextcloud » n'est pas utilisé : il prend tout.
- **Identité MIME.** Les index du lecteur portent sur ``msg.iter_attachments()`` ; les clés de l'archive sur toutes les feuilles de
  ``msg.walk()``. Les deux numérotations ne sont pas interchangeables : ``identites`` fait correspondre chaque pièce du lecteur à sa feuille
  par l'objet MIME lui-même, puis vérifie type, nom, taille et empreinte SHA-256 sur les mêmes octets. La sélection envoyée par l'écran porte
  l'index ET l'empreinte : au classement, le message est relu dans la génération IMAP confirmée (UIDVALIDITY), et une pièce dont l'empreinte
  ne correspond plus est refusée (409) — jamais la pièce N d'un autre message du fil.
- **Intention de lot** (``pieces56109_lots``) : acteur, source exacte (boîte, UIDVALIDITY, UID, clé du message, empreinte des octets),
  dossier, destination, pièces et empreintes, clé d'idempotence. Chaque pièce a un état : attendue, en cours, vérifiée, à vérifier,
  refusée ; l'état du lot se lit « 3 sur 4 classées ; 1 à vérifier » et ouvre la destination. Les succès ne sont jamais recommencés :
  la reprise ne vise que les pièces qui en ont besoin (un dépôt incertain est relu, jamais redéposé à l'aveugle).
- **Droits.** Message et dossier sont contrôlés avant toute lecture (404 neutre : absent et interdit répondent pareil ; 503 si le contrôle
  est en panne) à la préparation, à la confirmation, à la reprise et à la lecture de l'état ; ils sont RELUS au moment de l'effet, dans le
  travail, avant chaque dépôt (``garde_effet``) : refus ou génération changée, plus aucune copie.

Aucun appel à un modèle : tout fonctionne IA en pause (travail déterministe d'origine « utilisateur »). Aucune écriture pendant une
lecture (R05) ; schéma créé au démarrage (``lecteur56106.ensure_schema``). Données du journal : jamais d'objet ni de corps de courriel.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
import sqlite3

from .common import Stop

MAX_PIECES = 60                      # pièces d'un lot (même borne que les parties analysées d'un message)
ID_LOT = re.compile(r'^[0-9a-f]{32}$')
EMPREINTE = re.compile(r'^[0-9a-f]{64}$')
ETATS = ('attendue', 'en_cours', 'verifiee', 'a_verifier', 'refusee')
LIBELLES = {'attendue': 'Attendue', 'en_cours': 'En cours', 'verifiee': 'Classée et vérifiée', 'a_verifier': 'À vérifier', 'refusee': 'Refusée',
            'deja': 'Déjà classée'}
MOTIFS_NON_CLASSABLE = {
    'courriel_joint': 'Courriel joint : il se télécharge, mais ne se classe pas comme une pièce (enregistrez le courriel complet dans Correspondances).',
    'piece_trop_volumineuse': 'Pièce trop volumineuse pour être classée d’ici : téléchargez-la.',
    'partie_introuvable': 'Pièce non retrouvée dans le message : téléchargez-la.',
    'identite_differente': 'Pièce non identifiée avec certitude dans le message : téléchargez-la.',
    'partie_du_corps': 'Cette partie est le corps du message, pas une pièce jointe.'}
MOTIFS_REFUS = {
    'piece_modifiee_ou_absente': 'La pièce n’est plus dans le message d’origine.', 'conflit_fichier_existant': 'Un autre fichier porte déjà ce nom : rien n’a été remplacé.',
    'empreinte_differente': 'Le fichier déposé ne correspond pas à la pièce : à vérifier dans le dossier.', 'destination_hors_dossier': 'Destination hors du dossier.',
    'autorisation_retiree': 'Vos droits sur ce courriel ou ce dossier ont changé : la pièce n’a pas été copiée.',
    'courriel_source_deplace_ou_absent': 'Le courriel a été déplacé ou supprimé avant la copie.', 'operation_annulee': 'Classement arrêté avant la copie.',
    'non_copiee': 'La pièce n’a pas été copiée.'}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS pieces56109_lots(id TEXT PRIMARY KEY, compte TEXT NOT NULL, role TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL,
  boite TEXT NOT NULL, uidvalidity TEXT NOT NULL, uid TEXT NOT NULL, cle_courriel TEXT NOT NULL, raw_sha256 TEXT NOT NULL, matter TEXT NOT NULL,
  destination TEXT NOT NULL, destination_label TEXT NOT NULL DEFAULT '', pieces TEXT NOT NULL, proposal_id TEXT NOT NULL DEFAULT '',
  link_id TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL, reprises INTEGER NOT NULL DEFAULT 0, cree_le TEXT NOT NULL, maj_le TEXT NOT NULL,
  UNIQUE(compte, cle));
CREATE INDEX IF NOT EXISTS pieces56109_lots_lien ON pieces56109_lots(link_id);
CREATE INDEX IF NOT EXISTS pieces56109_lots_source ON pieces56109_lots(compte, boite, uidvalidity, uid);
'''


def _now():
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(desk):
    """Création idempotente au démarrage (appelée par ``lecteur56106.ensure_schema``) ; aucune donnée écrite."""
    desk.db.executescript(SCHEMA)


def _table(desk):
    try:
        return bool(desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pieces56109_lots'").fetchone())
    except sqlite3.Error:
        return False


# ------------------------------------------------------------------------------------------------ identité des pièces
def octets_de(part):
    """Octets décodés d'une pièce jointe ; un courriel joint (``message/rfc822``) rend le message joint complet, pas un fichier vide."""
    try:
        if part.is_multipart():
            if part.get_content_type() == 'message/rfc822':
                return part.get_payload(0).as_bytes()
            return part.as_bytes()
        return part.get_payload(decode=True) or b''
    except Exception:   # noqa: BLE001 — partie illisible : elle reste dans l'original
        return b''


def identites(msg, cfg=None):
    """Pièces du lecteur (ordre de ``msg.iter_attachments()``) avec leur identité d'archive : ``{'index', 'nom', 'type', 'taille',
    'empreinte', 'cle', 'classable', 'motif', 'signature'}``. ``cle`` est la clé de ``correspondances5643.mime_parts`` (feuille de
    ``walk()``), retrouvée par l'objet MIME lui-même puis vérifiée sur les mêmes octets ; ``signature`` : image intégrée ou image de
    signature probable (écartée par défaut d'un classement en lot)."""
    from . import correspondances5643 as cx
    cles, rang = {}, 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        rang += 1
        cles[id(part)] = str(rang)
    par_cle = {p['key']: p for p in cx.mime_parts(msg, cfg)}
    out = []
    for n, part in enumerate(msg.iter_attachments()):
        nom = str(part.get_filename() or '')
        octets = octets_de(part)
        item = {'index': n, 'nom': nom[:200], 'type': part.get_content_type(), 'taille': len(octets), 'empreinte': hashlib.sha256(octets).hexdigest(),
                'cle': '', 'classable': False, 'motif': '', 'signature': False}
        p = par_cle.get(cles.get(id(part), ''))
        if part.is_multipart():
            item['motif'] = 'courriel_joint'
        elif p is None:
            item['motif'] = 'partie_introuvable'
        elif p.get('state') != 'extraite':
            item['motif'] = 'piece_trop_volumineuse'
        elif (p['type'], p['name'] or '', p['size'], p['sha256']) != (item['type'], nom, len(octets), item['empreinte']):
            item['motif'] = 'identite_differente'
        elif not (p.get('attachment') or p.get('inline_image')):
            item['motif'] = 'partie_du_corps'
        else:
            item.update(cle=p['key'], classable=True, signature=bool(p.get('inline_image') or p.get('signature_like')))
        out.append(item)
    return out


def pieces_du_lecteur(msg, cfg=None):
    """Pièces pour la route ``courriel`` du lecteur : mêmes champs que ``boite5625._attachments`` (``index``, ``name``, ``type``, ``size``),
    plus le genre d'aperçu et l'identité (``empreinte``, ``cle_archive``, ``classable``, ``motif``, ``signature``)."""
    from .apercu5679 import kind_of
    out = []
    for x in identites(msg, cfg):
        nom = x['nom'] or 'pièce %d' % (x['index'] + 1)
        genre = kind_of(nom)
        out.append({'index': x['index'], 'name': nom, 'type': x['type'], 'size': x['taille'],
                    'apercu': {'pdf': 'pdf', 'image': 'image', 'docx': 'html'}.get(genre, ''), 'empreinte': x['empreinte'], 'cle_archive': x['cle'],
                    'classable': x['classable'], 'motif': MOTIFS_NON_CLASSABLE.get(x['motif'], ''), 'signature': x['signature'],
                    'texte': not x['motif'] == 'courriel_joint'})
    return out


# ------------------------------------------------------------------------------------------------ droits
def _dossier(desk, acteur, matter):
    """Fiche du dossier s'il est visible de l'acteur et porte un répertoire ; absent ou interdit : ``introuvable`` (même réponse)."""
    from . import lecteur56106 as lx
    from .common import load_matters
    matter = str(matter or '').strip()
    if not matter:
        raise Stop('dossier_a_choisir')
    lx._dossier_visible(desk, acteur, matter)
    fiche = next((m for m in load_matters(desk.c) if str(m['id']) == matter), None)
    if fiche is None:
        raise Stop('introuvable')
    if not fiche.get('path'):
        raise Stop('dossier_sans_repertoire')
    return fiche


def _droits_actuels(desk, compte, role, cle_courriel, matter):
    """L'acteur voit-il encore ce message et ce dossier ? Registres relus maintenant (une révocation vaut aussitôt). Panne du contrôle :
    ``Stop('controle_indisponible')`` remonte (jamais un accès élargi)."""
    from . import lecteur56106 as lx
    from .acces_courriel56100 import exiger_contexte, masques
    if role not in lx.ROLES_COURRIEL:
        return False
    try:
        caches = masques(desk, (compte, role))
        if str(matter) in caches:
            return False
        exiger_contexte(desk, (compte, role), cle_courriel, matter)
    except Stop as ex:
        if str(ex) in ('introuvable', 'role_insuffisant'):
            return False
        raise
    return True


def _role_actuel(desk, compte):
    """5.6.109 (intégration I1) : rôle ACTUEL du compte (annuaire des membres : comptes du serveur ou de la distribution autonome, sinon
    dernier rôle vu), jamais celui enregistré dans le lot. Compte inconnu ou désactivé : '' (refus). Annuaire en panne : ``controle_indisponible``."""
    if compte == 'cabinet':
        return 'administrateur'
    try:
        from .membres5650 import directory
        return str((directory(desk).get(compte) or {}).get('role') or '')
    except Exception:   # noqa: BLE001 — jamais un accès élargi
        raise Stop('controle_indisponible') from None


def garde_effet(desk, link, item):
    """Appelée par ``correspondances5643.run_file_attachments`` avant CHAQUE dépôt : une pièce demandée depuis le lecteur n'est copiée
    que si une personne qui l'a demandée voit encore le message et le dossier, avec son rôle ACTUEL. Aucun lot ne porte la pièce
    (classement 5.6.43, mandat automatique) : leurs propres gardes s'appliquent. 5.6.109 (intégration I1) : un lot qui porte la pièce mais
    n'est pas « confirmé » ne vaut jamais autorisation (« rien à contrôler » n'est pas « autorisé ») : refus. Refus :
    ``Stop('autorisation_retiree')`` — le travail s'arrête, aucune autre pièce n'est copiée."""
    if not _table(desk):
        return
    cle = str(item.get('key') or '')
    lots = [r for r in desk.db.execute('SELECT * FROM pieces56109_lots WHERE link_id=? ORDER BY maj_le DESC', (link['id'],)).fetchall()
            if any(str(p.get('cle')) == cle for p in json.loads(r['pieces'] or '[]'))]
    if not lots:
        return
    for lot in lots:
        if lot['etat'] == 'confirme' and _droits_actuels(desk, lot['compte'], _role_actuel(desk, lot['compte']), lot['cle_courriel'], lot['matter']):
            return
    raise Stop('autorisation_retiree')


# ------------------------------------------------------------------------------------------------ état d'un lot
def _lot(desk, acteur, ident):
    """Lot de la personne ; absent, d'une autre personne, ou dont le message ou le dossier ne lui est plus visible : ``introuvable``."""
    ident = str(ident or '')
    if not ID_LOT.match(ident) or not _table(desk):
        raise Stop('introuvable')
    row = desk.db.execute('SELECT * FROM pieces56109_lots WHERE id=? AND compte=?', (ident, acteur[0])).fetchone()
    if not row or not _droits_actuels(desk, acteur[0], acteur[1], row['cle_courriel'], row['matter']):
        raise Stop('introuvable')
    return row


def _lien(desk, link_id):
    return desk.db.execute('SELECT * FROM mail_archive_links WHERE id=?', (str(link_id or ''),)).fetchone() if link_id else None


def _etats_pieces(desk, lot):
    """État de chaque pièce du lot, lu dans les reçus de ``correspondances5643`` (actifs, opérations) et la file ; sans écriture."""
    from . import correspondances5643 as cx
    pieces = json.loads(lot['pieces'] or '[]')
    link = _lien(desk, lot['link_id'])
    actifs, erreurs, travail = {}, {}, 'not_requested'
    if link is not None:
        actifs = {a['part_key']: a for a in desk.db.execute(
            "SELECT part_key,path,state,sha256,detail_json FROM mail_archive_assets WHERE link_id=? AND kind='attachment' AND version=1", (link['id'],))}
        for o in desk.db.execute("SELECT phase,error_code FROM mail_archive_operations WHERE link_id=? AND phase LIKE 'attachment:%' ORDER BY updated_at", (link['id'],)):
            if o['error_code']:
                erreurs[o['phase'].split(':', 1)[1]] = o['error_code']
        travail = cx._projected(desk, link, 'file_attachments')
    actif_travail = travail in cx.ACTIVE_LINK_STATES
    out = []
    for p in pieces:
        a = actifs.get(str(p['cle']))
        etat, motif = 'attendue', ''
        if a is not None and a['state'] == 'verified' and a['sha256'] == p['sha256']:
            etat = 'verifiee'
        elif lot['etat'] == 'propose':
            etat = 'attendue'
        elif a is not None and a['state'] == 'uncertain':
            etat, motif = 'a_verifier', 'Dépôt incertain : le fichier a peut-être été créé, sa relecture a échoué.'
        elif a is not None and a['state'] == 'error':
            etat, motif = 'refusee', MOTIFS_REFUS.get(erreurs.get(str(p['cle']), '').split(':')[0], MOTIFS_REFUS['non_copiee'])
        elif a is not None and a['state'] == 'writing':
            etat, motif = ('en_cours', '') if actif_travail else ('a_verifier', 'Copie interrompue : le fichier a peut-être été créé.')
        elif actif_travail:
            etat = 'attendue'
        elif travail in ('cancelled',):
            etat, motif = 'refusee', MOTIFS_REFUS['autorisation_retiree' if _refus_de_droits(desk, lot) else 'operation_annulee']
        elif travail in ('not_requested',):
            etat = 'attendue'
        else:                                        # travail terminé (vérifié, partiel, erreur) sans reçu pour cette pièce
            etat, motif = 'refusee', MOTIFS_REFUS['non_copiee'] + ' Vous pouvez reprendre le classement.'
        out.append({'index': p['index'], 'cle': p['cle'], 'nom': p['nom'], 'nom_classement': p.get('nom_classement') or p['nom'], 'type': p['type'],
                    'taille': p['taille'], 'etat': etat, 'libelle': LIBELLES[etat], 'motif': motif, 'chemin': a['path'] if a is not None and etat == 'verifiee' else '',
                    'deja': bool(p.get('deja'))})
    return out, travail


def _refus_de_droits(desk, lot):
    try:
        return not _droits_actuels(desk, lot['compte'], lot['role'], lot['cle_courriel'], lot['matter'])
    except Stop:
        return True


def _phrase(compte):
    """« 3 sur 4 classées ; 1 à vérifier » : jamais un succès sans reçu vérifié."""
    total = sum(compte.values())
    morceaux = ['%d sur %d classée%s' % (compte['verifiee'], total, 's' if compte['verifiee'] > 1 else '')]
    for etat, mot in (('a_verifier', 'à vérifier'), ('refusee', 'refusée'), ('en_cours', 'en cours'), ('attendue', 'en attente')):
        if compte[etat]:
            morceaux.append('%d %s%s' % (compte[etat], mot, 's' if compte[etat] > 1 and etat == 'refusee' else ''))
    return ' ; '.join(morceaux)


def vue_lot(desk, lot):
    """Vue publique d'un lot (lecture seule) : pièces et états, phrase d'état, destination à ouvrir, reprise possible."""
    from .common import load_matters, matter_display
    pieces, travail = _etats_pieces(desk, lot)
    compte = {e: sum(1 for p in pieces if p['etat'] == e) for e in ETATS}
    fiche = next((m for m in load_matters(desk.c) if str(m['id']) == lot['matter']), None)
    ouvrir = ''
    link = _lien(desk, lot['link_id'])
    if fiche is not None and fiche.get('path'):
        racine = str(fiche['path']).rstrip('/')
        dossier_cible = next((p['chemin'].rsplit('/', 1)[0] for p in pieces if p['chemin']), '')
        if not dossier_cible and link is not None:
            dossier_cible = next((x.get('dir') or '' for x in json.loads(link['resolution_json']).get('parts', []) if x.get('dir')), '')
        if dossier_cible.startswith(racine + '/'):
            from urllib.parse import urlencode
            ouvrir = '/dossier?' + urlencode({'id': lot['matter'], 'vue': 'fichiers', 'sous': 'repertoire', 'rep': dossier_cible[len(racine) + 1:]})
    propose = lot['etat'] == 'propose'
    termine = not propose and travail not in ('queued', 'saving', 'verifying', 'cancel_requested') and not (compte['attendue'] or compte['en_cours'])
    a_reprendre = not propose and travail not in ('queued', 'saving', 'verifying', 'cancel_requested') and compte['verifiee'] < len(pieces)
    if propose:
        deja = sum(1 for p in pieces if p['etat'] == 'verifiee')
        phrase = '%d pièce%s à copier' % (len(pieces) - deja, '' if len(pieces) - deja == 1 else 's') + (' ; %d déjà classée%s dans ce dossier' % (deja, '' if deja == 1 else 's') if deja else '')
    else:
        phrase = _phrase(compte)
    return {'id': lot['id'], 'etat': lot['etat'], 'travail': travail, 'matter': lot['matter'], 'dossier': matter_display(fiche) if fiche else lot['matter'],
            'destination': lot['destination'], 'destination_label': lot['destination_label'], 'pieces': pieces, 'compte': compte, 'phrase': phrase,
            'termine': termine, 'a_reprendre': a_reprendre, 'ouvrir': ouvrir, 'reprises': lot['reprises'],
            'source': {'uid': lot['uid'], 'dossier': lot['boite'], 'uidvalidity': lot['uidvalidity']},
            'message': ('Aperçu avant copie : rien n’est encore copié. Le message reste dans la messagerie ; aucune règle automatique n’est modifiée.' if propose
                        else ('Classement en cours : ' + phrase + '.' if not termine else 'Classement terminé : ' + phrase + '.'))}


# ------------------------------------------------------------------------------------------------ lecture : état pour un courriel, destinations
def classement(desk, acteur, args, box=None):
    """Ce que la barre affiche pour ce courriel : pour chaque pièce (clé d'archive), où elle est déjà classée parmi les dossiers visibles ;
    les derniers lots de la personne pour ce message (reprise après interruption). Droits du message d'abord ; aucune lecture du corps."""
    from . import correspondances5643 as cx, lecteur56106 as lx
    from .acces_courriel56100 import masques
    from .common import load_matters, matter_display
    own = box is None
    box = box or lx._boite(desk)
    try:
        ref, folder = lx.garder(desk, acteur, box, args)
        try:   # message absent : même réponse neutre qu'un message interdit (en-têtes seulement, jamais le corps)
            box.fetch(folder, ref.uid, headers_only=True, expected_validity=ref.uidvalidity)
        except Stop as ex:
            if str(ex) in lx.NEUTRES or str(ex) in ('date_imap_absente', 'date_imap_invalide'):
                raise Stop('introuvable') from None
            raise
    finally:
        if own:
            lx._fermer(box)
    caches = masques(desk, acteur)
    noms = {str(m['id']): matter_display(m) for m in load_matters(desk.c)}
    deja = {}
    rows = desk.db.execute('SELECT l.id, l.matter_id FROM mail_archive_links l JOIN mail_archive_sources s ON s.archive_id=l.archive_id '
                           'WHERE s.account_id=? AND s.folder=? AND s.uid=? AND s.uidvalidity=?', (cx.account_id(desk.c), folder, str(ref.uid), str(ref.uidvalidity))).fetchall()
    for r in rows:
        if r['matter_id'] in caches or r['matter_id'] not in noms:
            continue                                 # dossier réservé ou disparu : rien n'en est dit, pas même un compteur
        for a in desk.db.execute("SELECT part_key,path,state,detail_json FROM mail_archive_assets WHERE link_id=? AND kind='attachment' AND version=1", (r['id'],)):
            etat = {'verified': 'verifiee', 'uncertain': 'a_verifier', 'error': 'refusee'}.get(a['state'], 'en_cours')
            morceaux = [x for x in str(a['path']).split('/') if x]
            try:
                libelle = str(json.loads(a['detail_json'] or '{}').get('libelle') or '')
            except ValueError:
                libelle = ''
            deja.setdefault(a['part_key'], []).append({'matter': r['matter_id'], 'dossier': noms[r['matter_id']], 'etat': etat, 'libelle': LIBELLES[etat],
                                                       'emplacement': libelle or (morceaux[-4] if len(morceaux) >= 4 else '')})
    lots = []
    if _table(desk):
        for lot in desk.db.execute('SELECT * FROM pieces56109_lots WHERE compte=? AND boite=? AND uidvalidity=? AND uid=? ORDER BY maj_le DESC LIMIT 5',
                                   (acteur[0], folder, str(ref.uidvalidity), str(ref.uid))).fetchall():
            if lot['matter'] in caches or lot['etat'] != 'confirme':
                continue
            lots.append(vue_lot(desk, lot))
    return {'uid': ref.uid, 'dossier': folder, 'uidvalidity': ref.uidvalidity, 'deja': deja, 'lots': lots}


def destinations(desk, acteur, matter):
    """Emplacements proposés pour ce dossier (identifiant, libellé, nom du répertoire) ; jamais un chemin envoyé par le navigateur."""
    from . import correspondances5643 as cx
    fiche = _dossier(desk, acteur, matter)
    return {'matter': fiche['id'], 'destinations': [{'id': d['id'], 'label': '%s — %s' % (d['label'], d['dossier']) if d['dossier'] else d['label'], 'defaut': d['defaut']}
                                                    for d in cx.destinations_pieces(desk, fiche)]}


# ------------------------------------------------------------------------------------------------ préparer, confirmer, reprendre
def _selection(valeur):
    """``[{index, empreinte, nom?}]`` : 1 à ``MAX_PIECES`` pièces, index entier, empreinte SHA-256, sans doublon."""
    if not isinstance(valeur, list) or not valeur:
        raise Stop('aucune_piece_selectionnee')
    if len(valeur) > MAX_PIECES:
        raise Stop('trop_de_pieces')
    out, vus = [], set()
    for x in valeur:
        x = x if isinstance(x, dict) else {}
        try:
            index = int(x.get('index'))
        except (TypeError, ValueError):
            raise Stop('piece_invalide') from None
        empreinte = str(x.get('empreinte') or '')
        if isinstance(x.get('index'), bool) or index < 0 or index > 999 or not EMPREINTE.match(empreinte):
            raise Stop('piece_invalide')
        if index in vus:
            continue
        vus.add(index)
        out.append({'index': index, 'empreinte': empreinte, 'nom': re.sub(r'\s+', ' ', str(x.get('nom') or '')).strip()[:120]})
    return out


def _meme_octets(prop, meta, parties):
    """La proposition de ``correspondances5643`` porte-t-elle sur le message contrôlé (empreinte des octets complets) et, pour chaque
    pièce choisie, sur les octets que l'écran a désignés (empreinte de la pièce) ?"""
    par_cle = {x['key']: x for x in prop['parts']}
    return prop['raw_sha256'] == meta['raw_sha256'] and all(par_cle.get(p['cle'], {}).get('sha256') == p['empreinte'] for _, p in parties)


def _source_de(ref, folder):
    return {'folder': folder, 'uid': str(ref.uid), 'uidvalidity': str(ref.uidvalidity)}


def preparer(desk, acteur, data, box=None):
    """Aperçu avant copie : message et dossier contrôlés, pièces résolues sur les mêmes octets (index ET empreinte), proposition
    ``correspondances5643.prepare`` (aucun fichier écrit), intention de lot enregistrée. Idempotente par clé."""
    from . import correspondances5643 as cx, lecteur56106 as lx, pause5629
    compte, role = acteur
    cle = lx._cle(data.get('cle'))
    choix = _selection(data.get('pieces'))
    # le dossier choisi est envoyé sous le nom « affaire » : le service répond lui-même, par le même 404 neutre, qu'il soit absent ou réservé
    matter, destination = str(data.get('affaire') or '').strip(), str(data.get('destination') or 'nos_pieces')[:80]
    demande = lx._empreinte('classer', str(data.get('uid') or ''), str(data.get('dossier') or ''), matter, destination, choix)
    own = box is None
    box = box or lx._boite(desk)
    try:
        ref, folder = lx.garder(desk, acteur, box, data)            # droits du message avant tout reçu, ancien ou nouveau, et avant tout octet
        fiche = _dossier(desk, acteur, matter)
        ancien = desk.db.execute('SELECT * FROM pieces56109_lots WHERE compte=? AND cle=?', (compte, cle)).fetchone()
        if ancien:
            if ancien['empreinte'] != demande:
                raise Stop('cle_idempotence_deja_utilisee')
            return dict(vue_lot(desk, ancien), rejeu=True)
        cible = cx.destination_pieces(desk, fiche, destination)
        source = _source_de(ref, folder)
        try:
            raw, msg, meta = cx.capture(desk, source, box)          # octets complets, lecture seule, génération IMAP confirmée par le garde
        except Stop as ex:
            if str(ex) in lx.NEUTRES:
                raise Stop('introuvable') from None
            raise
        connues = identites(msg, desk.c)
        parties = []
        for c in choix:
            piece = connues[c['index']] if c['index'] < len(connues) else None
            if piece is None or piece['empreinte'] != c['empreinte']:
                raise Stop('piece_modifiee')                        # l'index ne désigne plus ces octets : jamais « la pièce N » d'un autre message
            if not piece['classable']:
                raise Stop('piece_non_classable')
            parties.append((c, piece))
        pause5629.checkpoint(desk)
        prop = cx.prepare(desk, compte, {'source': source, 'matter_id': fiche['id'], 'actions': ['file_attachments'], 'pieces_destination': cible['id'],
                                         'attachments_selection': {'mode': 'selected', 'parts': [
                                             {'part_key': p['cle'], 'display_name': c['nom'] or p['nom']} for c, p in parties]}}, box)
    finally:
        if own:
            lx._fermer(box)
    par_cle = {x['key']: x for x in prop['parts']}
    if not _meme_octets(prop, meta, parties):
        raise Stop('piece_modifiee')                                # la proposition ne porte pas sur les octets contrôlés : elle n'est jamais confirmée
    link_id = cx._sha(('link|%s|%s' % (prop['archive_id'], fiche['id'])).encode())
    deja = {a['part_key'] for a in desk.db.execute("SELECT part_key FROM mail_archive_assets WHERE link_id=? AND kind='attachment' AND state='verified'", (link_id,))}
    pieces = [{'index': p['index'], 'cle': p['cle'], 'nom': p['nom'] or 'pièce %d' % (p['index'] + 1), 'nom_classement': par_cle[p['cle']]['stored_name'],
               'type': p['type'], 'taille': p['taille'], 'sha256': p['empreinte'], 'deja': p['cle'] in deja} for _, p in parties]
    ident, quand = secrets.token_hex(16), _now()
    try:
        desk.db.execute('INSERT INTO pieces56109_lots(id,compte,role,cle,empreinte,boite,uidvalidity,uid,cle_courriel,raw_sha256,matter,destination,destination_label,'
                        "pieces,proposal_id,link_id,etat,cree_le,maj_le) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'propose',?,?)",
                        (ident, compte, role, cle, demande, folder, str(ref.uidvalidity), str(ref.uid), ref.cle, meta['raw_sha256'], fiche['id'], cible['id'],
                         '%s — %s' % (cible['label'], cible['dossier']), json.dumps(pieces, ensure_ascii=False), prop['proposal_id'], link_id, quand, quand))
        desk.db.commit()
    except sqlite3.IntegrityError:                                  # même clé envoyée deux fois en même temps : le premier reçu fait foi
        desk.db.rollback()
        ancien = desk.db.execute('SELECT * FROM pieces56109_lots WHERE compte=? AND cle=?', (compte, cle)).fetchone()
        if ancien['empreinte'] != demande:
            raise Stop('cle_idempotence_deja_utilisee') from None
        return dict(vue_lot(desk, ancien), rejeu=True)
    desk.audit('pieces56109_propose', {'lot': ident, 'matter': fiche['id'], 'destination': cible['id'], 'pieces': len(pieces), 'par': str(compte)[:80]})
    lot = desk.db.execute('SELECT * FROM pieces56109_lots WHERE id=?', (ident,)).fetchone()
    return dict(vue_lot(desk, lot), rejeu=False)


def _regarder(desk, acteur, lot, box):
    """Droits relus au moment de l'effet : le message, dans la génération IMAP du lot (UIDVALIDITY changée : ``introuvable``), et le dossier."""
    from . import lecteur56106 as lx
    ref, folder = lx.garder(desk, acteur, box, {'uid': lot['uid'], 'dossier': lot['boite'], 'uidvalidity': lot['uidvalidity'], 'cle_courriel': lot['cle_courriel']})
    _dossier(desk, acteur, lot['matter'])
    return ref, folder


def confirmer(desk, acteur, data, box=None):
    """« Classer » : la proposition du lot est confirmée (``correspondances5643.confirm``), le travail de dépôt part en file. Idempotente :
    un second clic rejoint le même travail. Message, génération IMAP et dossier relus d'abord."""
    from . import correspondances5643 as cx, lecteur56106 as lx, pause5629
    lot = _lot(desk, acteur, data.get('id'))
    own = box is None
    box = box or lx._boite(desk)
    try:
        _regarder(desk, acteur, lot, box)
    finally:
        if own:
            lx._fermer(box)
    if lot['etat'] == 'confirme':
        return dict(vue_lot(desk, lot), rejeu=True)
    pause5629.checkpoint(desk)
    # 5.6.109 (intégration I1) : l'état « confirmé » du lot et la mise en file du dépôt sont validés ENSEMBLE : ``cx.confirm`` rejoint la
    # transaction ouverte ici et la valide (ou l'annule). Le worker ne peut jamais prendre le travail d'un lot resté « proposé ».
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        desk.db.execute("UPDATE pieces56109_lots SET etat='confirme', maj_le=? WHERE id=? AND etat='propose'", (_now(), lot['id']))
        out = cx.confirm(desk, acteur[0], {'proposal_id': lot['proposal_id'], 'expected_version': 1, 'request_key': 'p109-' + lot['id']})
        desk.db.execute('UPDATE pieces56109_lots SET link_id=? WHERE id=?', (out['link_id'], lot['id']))
        desk.db.commit()
    except Stop as ex:
        if desk.db.in_transaction:
            desk.db.rollback()
        if str(ex) in ('proposition_absente', 'proposition_autre_utilisateur', 'dossier_absent'):
            raise Stop('introuvable') from None
        raise
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    desk.audit('pieces56109_classement', {'lot': lot['id'], 'matter': lot['matter'], 'destination': lot['destination'], 'pieces': len(json.loads(lot['pieces'])),
                                          'par': str(acteur[0])[:80]})
    lot = desk.db.execute('SELECT * FROM pieces56109_lots WHERE id=?', (lot['id'],)).fetchone()
    return dict(vue_lot(desk, lot), rejeu=False)


def reprendre(desk, acteur, data, box=None):
    """Reprise d'un lot interrompu : seules les pièces sans reçu vérifié sont visées ; un travail encore actif est rejoint, jamais doublé ;
    une pièce déjà vérifiée n'est pas recopiée (``_deposit`` la rejoint), un dépôt incertain est relu avant toute nouvelle écriture."""
    from . import correspondances5643 as cx, lecteur56106 as lx, pause5629
    compte = acteur[0]
    cle = lx._cle(data.get('cle'))
    lot = _lot(desk, acteur, data.get('id'))
    demande = lx._empreinte('reprendre', lot['id'])
    own = box is None
    box = box or lx._boite(desk)
    try:
        ref, folder = _regarder(desk, acteur, lot, box)
        ancien = lx._recu(desk, compte, 'classer/reprendre', cle, demande)
        if ancien is not None:
            return dict(vue_lot(desk, lot), rejeu=True)
        if lot['etat'] != 'confirme':
            raise Stop('lot_non_confirme')
        vue = vue_lot(desk, lot)
        restantes = [p for p in vue['pieces'] if p['etat'] != 'verifiee']
        if not restantes or not vue['a_reprendre']:
            return dict(vue, rejeu=False)                           # rien à reprendre, ou travail encore actif : il est rejoint
        fiche = _dossier(desk, acteur, lot['matter'])
        pause5629.checkpoint(desk)
        prop = cx.prepare(desk, compte, {'source': _source_de(ref, folder), 'matter_id': fiche['id'], 'actions': ['file_attachments'],
                                         'pieces_destination': lot['destination'],
                                         'attachments_selection': {'mode': 'selected', 'parts': [{'part_key': p['cle'], 'display_name': ''} for p in restantes]}}, box)
    finally:
        if own:
            lx._fermer(box)
    if prop['raw_sha256'] != lot['raw_sha256']:
        raise Stop('piece_modifiee')
    out = cx.confirm(desk, compte, {'proposal_id': prop['proposal_id'], 'expected_version': 1, 'request_key': 'p109-%s-r%d' % (lot['id'], lot['reprises'] + 1)})
    desk.db.execute('UPDATE pieces56109_lots SET reprises=reprises+1, link_id=?, maj_le=? WHERE id=?', (out['link_id'], _now(), lot['id']))
    lx._garder_recu(desk, compte, 'classer/reprendre', cle, demande, {'lot': lot['id']})
    desk.db.commit()
    desk.audit('pieces56109_reprise', {'lot': lot['id'], 'matter': lot['matter'], 'pieces': len(restantes), 'par': str(compte)[:80]})
    lot = desk.db.execute('SELECT * FROM pieces56109_lots WHERE id=?', (lot['id'],)).fetchone()
    return dict(vue_lot(desk, lot), rejeu=False)


def lire_lot(desk, acteur, ident):
    """État d'un lot de la personne (droits relus : message et dossier) ; lecture seule."""
    return vue_lot(desk, _lot(desk, acteur, ident))
