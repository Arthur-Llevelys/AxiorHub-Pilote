"""5.6.109 (lot H, F09) : discussions organisées et partagées — créer, renommer, épingler, grouper, rattacher, changer de contexte, partager
nominativement (Lecteur / Participant), révoquer, corbeille.

Stockage : celui de ``historique56109`` (associations autour de ``assistant_threads``), sans recopier un message. Contrats de droits repris de
la référence du dossier technique (``reference/discussions109.py``), avec les services réels : rôles et droits fins de ``membres5650``
(« supprimer »), accès aux dossiers et courriels de ``acces5697``, annuaire des comptes actifs (``membres5650.directory``).

| Action | Propriétaire | Lecteur | Participant |
|---|---|---|---|
| Lire, rechercher, exporter (droit « exporter ») | oui | oui | oui |
| Continuer (nouveau message, avec SES droits) | oui | non | oui |
| Renommer, rattacher, changer le contexte, partager, révoquer, supprimer, restaurer | oui | non | non |
| Épingler, grouper, archiver, retirer de ma liste | préférence personnelle | idem | idem |

- L'acteur, le cabinet et le rôle viennent de la requête authentifiée, jamais du formulaire ; un administrateur n'est pas lecteur implicite.
- Politique du cabinet (``discussions109:policy:<cabinet>``, administrateur, révisée) : ``user_delete_enabled``, ``internal_share_enabled``,
  ``corbeille_jours``. Supprimer exige la politique ET le droit individuel « supprimer » ET la propriété ; un travail actif lié refuse (409).
- Partager contrôle, pour chaque référence du fil (dossier, courriel, sources citées, liens utilisés), le droit de l'auteur et celui du
  destinataire ; une provenance illisible bloque. Partager n'ouvre aucune pièce. Révoquer coupe aussitôt liste, lecture, recherche et export.
- Chaque écriture : POST, CSRF, corps borné, clé d'idempotence, révision attendue (409 si périmée), même 404 neutre pour absent et interdit.
"""
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from .common import Stop
from . import historique56109 as H

PREFIXE = 'm56109/discussions/'
ECRITURES = ('creer', 'renommer', 'preferences', 'groupe-creer', 'groupe-modifier', 'groupe-supprimer', 'partager', 'revoquer', 'rattacher',
             'detacher', 'contexte', 'supprimer', 'restaurer', 'politique')
ROLES = ('administrateur', 'avocat', 'assistant')
MAX_POST = 20_000
CLE = re.compile(r'^[A-Za-z0-9_.:-]{8,120}$')
CLE_POLITIQUE = 'discussions109:policy:'
POLITIQUE_DEFAUT = {'user_delete_enabled': True, 'internal_share_enabled': True, 'corbeille_jours': 30, 'revision': 0}
TYPES_LIENS = ('matter', 'mail', 'piece', 'ai_thread')          # « deliverable » et « team_thread » : refusés dans cette livraison
PERSONNEL = frozenset({'epingler', 'grouper', 'archiver', 'masquer'})
PROPRIETAIRE = frozenset({'renommer', 'rattacher', 'partager', 'revoquer', 'supprimer', 'restaurer', 'contexte'})
CONFIRMATION = 'supprimer_pour_tous'

MESSAGES = {
    'introuvable': 'Discussion introuvable, ou vous n’y avez pas accès.',
    'action_interdite': 'Cette action est réservée au propriétaire de la discussion.',
    'suppression_interdite_par_politique': 'La suppression des discussions est désactivée par l’administrateur du cabinet. Vous pouvez l’archiver ou la retirer de votre liste.',
    'suppression_interdite_par_droit': 'Votre compte n’a pas le droit « Supprimer ». Vous pouvez archiver la discussion ou la retirer de votre liste.',
    'partage_interdit_par_politique': 'Le partage interne des discussions est désactivé par l’administrateur du cabinet.',
    'destinataire_non_autorise': 'Ce compte n’existe pas ou n’est pas actif dans le cabinet.',
    'partage_invalide': 'Partage impossible : choisissez un autre compte et le rôle Lecteur ou Participant.',
    'source_non_partageable': 'Partage refusé : une source de cette discussion (dossier, courriel ou pièce) n’est pas accessible à ce compte. Rien n’a été partagé.',
    'conflit_de_revision': 'La discussion a changé entre-temps (sur ce poste ou un autre) : actualisez avant de recommencer. Rien n’a été modifié.',
    'titre_invalide': 'Titre invalide : 1 à 120 caractères, sur une ligne.',
    'nom_invalide': 'Nom de groupe invalide : 1 à 60 caractères, sur une ligne.',
    'groupe_inconnu': 'Groupe introuvable.',
    'travail_en_cours': 'Un travail lié à cette discussion est encore en cours : ouvrez-le et arrêtez-le, ou archivez la discussion.',
    'confirmation_requise': 'Confirmez la suppression pour tous les participants.',
    'corbeille_expiree': 'Le délai de la corbeille est dépassé : cette discussion ne peut plus être restaurée ici.',
    'pas_dans_la_corbeille': 'Cette discussion n’est pas dans la corbeille.',
    'reference_indisponible': 'Élément introuvable, ou vous n’y avez pas accès.',
    'type_non_pris_en_charge': 'Ce type d’élément ne peut pas encore être rattaché.',
    'dossier_inconnu': 'Dossier introuvable, ou vous n’y avez pas accès.',
    'role_insuffisant': 'Réglage réservé à l’administrateur du cabinet.',
    'politique_invalide': 'Réglage invalide.',
    'cle_requise': 'Demande incomplète : rechargez la page.',
    'cle_reutilisee': 'Cette demande a déjà servi pour une autre action : rechargez la page.',
    'operation_en_cours': 'Cette action est déjà en cours : patientez.',
    'requete_trop_longue': 'Demande trop longue.',
    'origine_ou_csrf_refuse': 'Session expirée ou origine refusée : rechargez la page.',
    'controle_acces_indisponible': 'Contrôle des accès indisponible : rien n’a été modifié.',
}
STATUTS = {'introuvable': '404 Not Found', 'groupe_inconnu': '404 Not Found', 'reference_indisponible': '404 Not Found', 'dossier_inconnu': '404 Not Found',
           'action_interdite': '403 Forbidden', 'suppression_interdite_par_politique': '403 Forbidden', 'suppression_interdite_par_droit': '403 Forbidden',
           'partage_interdit_par_politique': '403 Forbidden', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden',
           'conflit_de_revision': '409 Conflict', 'travail_en_cours': '409 Conflict', 'cle_reutilisee': '409 Conflict', 'operation_en_cours': '409 Conflict',
           'corbeille_expiree': '409 Conflict', 'pas_dans_la_corbeille': '409 Conflict', 'requete_trop_longue': '413 Payload Too Large',
           'controle_acces_indisponible': '503 Service Unavailable'}


class Refus(Stop):
    pass


# ------------------------------------------------------------------------------------------------ contrats (référence du dossier, raccordés)
def verifier(action, compte, fil, politique_, role_accorde, peut_lire_reference, peut_supprimer=True):
    """Contrat de droits d'une action. ``role_accorde`` est relu dans les ACL du cabinet (jamais repris du formulaire) ; ``peut_lire_reference``
    contrôle chaque provenance du fil ; aucun privilège de lecture pour un administrateur."""
    if not compte or fil.get('deleted_at'):
        raise Refus('introuvable')
    owner = compte == fil['owner_id']
    if not owner and role_accorde not in H.ROLES_PARTAGE:
        raise Refus('introuvable')
    if any(not peut_lire_reference(ref) for ref in fil.get('references', ())):
        raise Refus('introuvable')
    if action == 'lire' or action in PERSONNEL:
        return True
    if action == 'continuer' and (owner or role_accorde == 'contributor'):
        return True
    if action in PROPRIETAIRE and owner:
        if action == 'supprimer':
            if not politique_['user_delete_enabled']:
                raise Refus('suppression_interdite_par_politique')
            if not peut_supprimer:
                raise Refus('suppression_interdite_par_droit')
        if action == 'partager' and not politique_['internal_share_enabled']:
            raise Refus('partage_interdit_par_politique')
        return True
    raise Refus('action_interdite')


def titre_valide(value, maxi=120, code='titre_invalide'):
    if not isinstance(value, str):
        raise Refus(code)
    value = value.strip()
    if not value or len(value) > maxi or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise Refus(code)
    return value          # texte, jamais du HTML : échappé à l'affichage (``textContent`` côté navigateur)


def revision_attendue(actuelle, attendue):
    if type(attendue) is not int or type(actuelle) is not int or attendue != actuelle:
        raise Refus('conflit_de_revision')


# ------------------------------------------------------------------------------------------------ politique du cabinet
def politique(desk):
    brut = desk.settings(CLE_POLITIQUE + H.cabinet(desk), None)
    out = dict(POLITIQUE_DEFAUT)
    if isinstance(brut, dict):
        for k in ('user_delete_enabled', 'internal_share_enabled'):
            if isinstance(brut.get(k), bool):
                out[k] = brut[k]
        if type(brut.get('corbeille_jours')) is int and 1 <= brut['corbeille_jours'] <= 365:
            out['corbeille_jours'] = brut['corbeille_jours']
        if type(brut.get('revision')) is int:
            out['revision'] = brut['revision']
    return out


def politique_publique(desk):
    return {'politique': politique(desk)}


def politique_html(desk, prefix, role):
    """Paramètres › Cabinet › Discussions IA : réglage de l'administrateur (les autres rôles voient l'état, sans commande)."""
    from html import escape as e
    p = politique(desk)
    admin = role == 'administrateur'
    dis = '' if admin else ' disabled'
    return ('<details class="p5621-section p5660-sec" id="discussions-ia"><summary>Discussions IA</summary><div class="h109-politique" data-h109-politique data-h109-revision="%d">'
            '<p>Les discussions avec l’assistant sont privées par défaut. Ce réglage ne donne à l’administrateur aucun accès à leur contenu.</p>'
            '<label class="h109-case"><input type="checkbox" name="user_delete_enabled"%s%s> Autoriser les utilisateurs à supprimer leurs discussions '
            '(il faut aussi le droit individuel « Supprimer » et être propriétaire de la discussion)</label>'
            '<label class="h109-case"><input type="checkbox" name="internal_share_enabled"%s%s> Autoriser le partage nominatif dans le cabinet (Lecteur ou Participant)</label>'
            '<label>Durée de la corbeille (jours)<input type="number" name="corbeille_jours" min="1" max="365" value="%d"%s></label>'
            '%s<p role="status" data-h109-etat></p></div></details><script defer src="%s"></script>') % (
        p['revision'], ' checked' if p['user_delete_enabled'] else '', dis, ' checked' if p['internal_share_enabled'] else '', dis, p['corbeille_jours'], dis,
        '<button type="button" class="ax-btn" data-h109-politique-enregistrer>Enregistrer</button>' if admin else '<p class="h109-note">Réglage réservé à l’administrateur du cabinet.</p>',
        e(prefix + '/static/v56109-discussions.js', quote=True))


def regler_politique(desk, qui, valeurs, revision, cle):
    """Administrateur du cabinet seulement ; booléens stricts ; révision attendue ; trace au journal."""
    cab, compte, role = H.acteur(desk, qui)
    if role != 'administrateur':
        raise Refus('role_insuffisant')
    if not isinstance(valeurs, dict) or set(valeurs) - {'user_delete_enabled', 'internal_share_enabled', 'corbeille_jours'}:
        raise Refus('politique_invalide')
    for k in ('user_delete_enabled', 'internal_share_enabled'):
        if k in valeurs and not isinstance(valeurs[k], bool):
            raise Refus('politique_invalide')
    if 'corbeille_jours' in valeurs and not (type(valeurs['corbeille_jours']) is int and 1 <= valeurs['corbeille_jours'] <= 365):
        raise Refus('politique_invalide')

    def faire():
        actuelle = politique(desk)
        revision_attendue(actuelle['revision'], revision)
        nouvelle = {**actuelle, **valeurs, 'revision': actuelle['revision'] + 1}
        desk.setting(CLE_POLITIQUE + cab, nouvelle)
        desk.audit('discussions56109_politique', {'par': compte, **{k: nouvelle[k] for k in ('user_delete_enabled', 'internal_share_enabled', 'corbeille_jours')}})
        return {'politique': nouvelle}
    return executer(desk, compte, cle, 'politique', {'v': valeurs, 'r': revision}, faire)


# ------------------------------------------------------------------------------------------------ idempotence
def executer(desk, compte, cle, nature, parametres, fn):
    """Une commande = un effet : même clé et mêmes paramètres → même reçu ; autre usage de la clé → ``cle_reutilisee`` (409)."""
    cle = str(cle or '')
    if not CLE.match(cle):
        raise Refus('cle_requise')
    empreinte = hashlib.sha256(json.dumps({'n': nature, 'p': parametres}, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    row = desk.db.execute('SELECT empreinte, etat, resultat FROM assistant_ops109 WHERE acteur=? AND cle=?', (compte, cle)).fetchone()
    if row:
        if row[0] != empreinte:
            raise Refus('cle_reutilisee')
        if row[1] == 'fait':
            return {**json.loads(row[2]), 'rejoue': True}
        if row[1] == 'echec':
            raise Stop(json.loads(row[2]).get('error') or 'operation_echouee')
        raise Refus('operation_en_cours')
    quand = H.maintenant()
    desk.db.execute('INSERT INTO assistant_ops109(acteur,cle,nature,empreinte,etat,resultat,cree_le,maj_le) VALUES(?,?,?,?,?,?,?,?)',
                    (compte, cle, nature, empreinte, 'en_cours', '{}', quand, quand))
    desk.db.commit()
    try:
        out = fn()
    except BaseException as ex:
        if desk.db.in_transaction:
            desk.db.rollback()
        code = str(ex).split(':')[0] if isinstance(ex, Stop) else 'erreur_interne'
        desk.db.execute("UPDATE assistant_ops109 SET etat='echec', resultat=?, maj_le=? WHERE acteur=? AND cle=?", (json.dumps({'error': code}), H.maintenant(), compte, cle))
        desk.db.commit()
        raise
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute("UPDATE assistant_ops109 SET etat='fait', resultat=?, maj_le=? WHERE acteur=? AND cle=?",
                    (json.dumps(out, ensure_ascii=False, default=str), H.maintenant(), compte, cle))
    desk.db.commit()
    return out


# ------------------------------------------------------------------------------------------------ références et contrôles de sources
def references(desk, fil):
    """Provenances du fil : dossier, courriel, dossiers des sources citées, liens « utilisé pour la réponse » et associations de dossier,
    pièce ou courriel. Une source citée illisible (JSON abîmé) est une provenance inconnue : ``inconnue:...``."""
    refs = set()
    if fil.get('matter'):
        refs.add('matter:' + str(fil['matter']))
    if fil.get('mail_key'):
        refs.add('mail:' + str(fil['mail_key']))
    for m in desk.db.execute('SELECT id, sources FROM assistant_messages WHERE thread_id=?', (fil['id'],)):
        sources = H._sources(dict(m))
        if sources is None:
            refs.add('inconnue:%s' % m['id'])
            continue
        for s in sources:
            if isinstance(s, dict) and s.get('matter'):
                refs.add('matter:' + str(s['matter']))
    for l in desk.db.execute('SELECT source_kind, source_id FROM assistant_thread_links109 WHERE thread_id=?', (fil['id'],)):
        if l['source_kind'] in ('matter', 'mail', 'piece'):
            refs.add('%s:%s' % (l['source_kind'], l['source_id']))
    return refs


def _peut_lire_ref(desk, a, ref):
    """Droit actuel de ``a`` (compte, rôle) sur une référence ; inconnue ou en panne : non."""
    from . import acces5697
    nature, _, ident = str(ref).partition(':')
    try:
        if nature == 'matter':
            return acces5697.peut_lire_dossier(desk, a, ident)
        if nature == 'mail':
            return (not acces5697.masques(desk, a)) or acces5697.peut_lire_message(desk, a, ident)
        if nature == 'piece':
            from .ui_ai5689 import _dossier_du_chemin
            mid = _dossier_du_chemin(desk, ident)
            return bool(mid) and acces5697.peut_lire_dossier(desk, a, mid)
    except Stop:
        return False
    except Exception:   # noqa: BLE001 — registre illisible : refus
        return False
    return False


def _fil(desk, qui, thread_id, avec_supprime=False):
    """Fil (lisible par la personne) + son ACL relue en base ; même 404 neutre sinon."""
    fil = H.charger_fil(desk, qui, thread_id, avec_supprime=avec_supprime)
    _, compte, role = H.acteur(desk, qui)
    fil['references'] = references(desk, fil)
    return fil, compte, role


def _controle(desk, action, qui, thread_id, avec_supprime=False):
    from . import membres5650
    fil, compte, role = _fil(desk, qui, thread_id, avec_supprime)
    droits = membres5650.effective_rights(desk.c['state_dir'], compte, role)
    # la lecture du fil (dossier, courriel, cabinet, ACL) est déjà contrôlée par ``charger_fil`` ; chaque message garde son propre masque.
    # Le contrôle de TOUTES les provenances est celui du partage (``partager``) : une source citée abîmée n'empêche pas d'épingler son fil.
    verifier(action, compte, {**fil, 'references': (), 'deleted_at': None if avec_supprime else fil.get('deleted_at')}, politique(desk),
             fil.get('member_role'), lambda r: _peut_lire_ref(desk, (compte, role), r), peut_supprimer=bool(droits.get('supprimer')))
    return fil, compte, role


def _cas(desk, thread_id, revision):
    """Compare-and-swap de la révision commune (titre, ACL, liens, contexte, suppression)."""
    cur = desk.db.execute('UPDATE assistant_thread_owners109 SET revision=revision+1 WHERE thread_id=? AND revision=?', (thread_id, revision))
    if cur.rowcount != 1:
        raise Refus('conflit_de_revision')
    return revision + 1


def _travail_actif(desk, thread_id):
    row = desk.db.execute("SELECT j.id FROM assistant_messages m JOIN jobs j ON j.id=m.job_id WHERE m.thread_id=? AND j.status IN (%s) LIMIT 1"
                          % ','.join('?' * len(H.JOBS_ACTIFS)), (thread_id, *H.JOBS_ACTIFS)).fetchone()
    if row is None:
        row = desk.db.execute("SELECT job_id AS id FROM assistant_messages WHERE thread_id=? AND role='user' AND status IN ('queued','running') LIMIT 1",
                              (thread_id,)).fetchone()
    return row['id'] if row else None


def _destinataire(desk, auth, user_id):
    from . import membres5650
    annuaire = membres5650.directory(desk, auth)
    m = annuaire.get(str(user_id or ''))
    if not m or m.get('role') not in ROLES:
        raise Refus('destinataire_non_autorise')
    return m['owner'], m['role']


# ------------------------------------------------------------------------------------------------ opérations
def creer(desk, qui, titre, dossier, cle):
    """Nouvelle discussion nommée (sans message, sans relance, sans dossier imposé) ; le dossier proposé est contrôlé. Le premier message
    peut aussi la créer par le circuit ordinaire (``submit_question``)."""
    from . import acces5697
    from .common import load_matters
    from .workspace import chat_scope
    cab, compte, role = H.acteur(desk, qui)
    titre = titre_valide(titre) if titre not in (None, '') else 'Nouvelle discussion'
    dossier = str(dossier or '')[:80]
    if dossier and (not acces5697.peut_lire_dossier(desk, (compte, role), dossier) or not any(str(m['id']) == dossier for m in load_matters(desk.c))):
        raise Refus('dossier_inconnu')

    def faire():
        scope, selected, _ = chat_scope(desk.c, {'matter': dossier, 'key': ''})
        tid = secrets.token_hex(16)
        quand = desk.now()
        desk.db.execute('INSERT INTO assistant_threads(id,scope,matter,mail_key,title,created,updated) VALUES(?,?,?,?,?,?,?)',
                        (tid, scope, selected['id'] if selected else '', '', titre, quand, quand))
        desk.db.execute('INSERT INTO assistant_thread_owners109(thread_id,cabinet_id,owner_id,revision,attribue_le,attribue_par) VALUES(?,?,?,1,?,?)',
                        (tid, cab, compte, H.maintenant(), 'creation'))
        desk.db.commit()
        return {'thread_id': tid, 'revision': 1}
    return executer(desk, compte, cle, 'creer', {'t': titre, 'd': dossier}, faire)


def renommer(desk, qui, thread_id, titre, revision, cle):
    titre = titre_valide(titre)
    fil, compte, _ = _controle(desk, 'renommer', qui, thread_id)

    def faire():
        rev = _cas(desk, fil['id'], revision)
        desk.db.execute('UPDATE assistant_threads SET title=? WHERE id=?', (titre, fil['id']))
        desk.db.commit()
        return {'thread_id': fil['id'], 'title': titre, 'revision': rev}
    return executer(desk, compte, cle, 'renommer', {'id': fil['id'], 't': titre, 'r': revision}, faire)


def preferences(desk, qui, thread_id, valeurs, cle):
    """Épingle, archive, retrait de ma liste, groupe : préférences de la personne connectée, sans effet sur les autres participants."""
    fil, compte, _ = _controle(desk, 'epingler', qui, thread_id)
    cab = H.cabinet(desk)
    if not isinstance(valeurs, dict) or not valeurs or set(valeurs) - {'pinned', 'archived', 'hidden', 'group_id'}:
        raise Refus('parametre_invalide')
    for k in ('pinned', 'archived', 'hidden'):
        if k in valeurs and not isinstance(valeurs[k], bool):
            raise Refus('parametre_invalide')
    groupe = valeurs.get('group_id', '__inchange__')
    if groupe not in ('__inchange__', None, ''):
        if not isinstance(groupe, str) or not desk.db.execute('SELECT 1 FROM assistant_groups109 WHERE id=? AND cabinet_id=? AND owner_id=?', (groupe, cab, compte)).fetchone():
            raise Refus('groupe_inconnu')        # groupe d'une autre personne : refusé, même si elle lit les mêmes fils

    def faire():
        actuel = desk.db.execute('SELECT * FROM assistant_thread_preferences109 WHERE thread_id=? AND cabinet_id=? AND user_id=?', (fil['id'], cab, compte)).fetchone()
        p = dict(actuel) if actuel else {'pinned': 0, 'archived': int(bool(fil.get('owner_archived'))) if fil['owner_id'] == compte else 0, 'hidden': 0, 'group_id': None}
        for k in ('pinned', 'archived', 'hidden'):
            if k in valeurs:
                p[k] = int(valeurs[k])
        if groupe != '__inchange__':
            p['group_id'] = groupe or None
        desk.db.execute('INSERT INTO assistant_thread_preferences109(thread_id,cabinet_id,user_id,pinned,archived,hidden,group_id,revision) VALUES(?,?,?,?,?,?,?,1) '
                        'ON CONFLICT(thread_id,cabinet_id,user_id) DO UPDATE SET pinned=excluded.pinned, archived=excluded.archived, hidden=excluded.hidden, '
                        'group_id=excluded.group_id, revision=revision+1', (fil['id'], cab, compte, p['pinned'], p['archived'], p['hidden'], p['group_id']))
        desk.db.commit()
        return {'thread_id': fil['id'], **{k: (bool(p[k]) if k != 'group_id' else (p[k] or '')) for k in ('pinned', 'archived', 'hidden', 'group_id')}}
    return executer(desk, compte, cle, 'preferences', {'id': fil['id'], 'v': valeurs}, faire)


def groupes(desk, qui):
    cab, compte, _ = H.acteur(desk, qui)
    if not H.schema_present(desk):
        return []
    return [dict(r) for r in desk.db.execute('SELECT id, name, position, revision FROM assistant_groups109 WHERE cabinet_id=? AND owner_id=? ORDER BY position, name',
                                             (cab, compte)).fetchall()]


def groupe_creer(desk, qui, nom, cle):
    cab, compte, _ = H.acteur(desk, qui)
    nom = titre_valide(nom, 60, 'nom_invalide')

    def faire():
        n = desk.db.execute('SELECT COUNT(*) FROM assistant_groups109 WHERE cabinet_id=? AND owner_id=?', (cab, compte)).fetchone()[0]
        if n >= 50:
            raise Refus('nom_invalide')
        gid = secrets.token_hex(8)
        desk.db.execute('INSERT INTO assistant_groups109(id,cabinet_id,owner_id,name,position) VALUES(?,?,?,?,?)', (gid, cab, compte, nom, n))
        desk.db.commit()
        return {'id': gid, 'name': nom, 'position': n, 'revision': 1}
    return executer(desk, compte, cle, 'groupe_creer', {'n': nom}, faire)


def _groupe(desk, cab, compte, gid):
    r = desk.db.execute('SELECT * FROM assistant_groups109 WHERE id=? AND cabinet_id=? AND owner_id=?', (str(gid or ''), cab, compte)).fetchone()
    if r is None:
        raise Refus('groupe_inconnu')
    return dict(r)


def groupe_modifier(desk, qui, gid, nom, position, revision, cle):
    cab, compte, _ = H.acteur(desk, qui)
    g = _groupe(desk, cab, compte, gid)
    nom = titre_valide(nom, 60, 'nom_invalide') if nom is not None else g['name']
    if position is not None and not (type(position) is int and 0 <= position <= 1000):
        raise Refus('parametre_invalide')

    def faire():
        cur = desk.db.execute('UPDATE assistant_groups109 SET name=?, position=?, revision=revision+1 WHERE id=? AND owner_id=? AND revision=?',
                              (nom, g['position'] if position is None else position, g['id'], compte, revision))
        if cur.rowcount != 1:
            raise Refus('conflit_de_revision')
        desk.db.commit()
        return {'id': g['id'], 'name': nom, 'revision': revision + 1}
    return executer(desk, compte, cle, 'groupe_modifier', {'g': g['id'], 'n': nom, 'p': position, 'r': revision}, faire)


def groupe_supprimer(desk, qui, gid, revision, cle):
    """Retire le groupe dans une transaction : les préférences qui le désignent reviennent à « Sans groupe », puis le groupe disparaît. Aucun
    fil n'est touché (jamais de cascade vers ``assistant_threads``)."""
    cab, compte, _ = H.acteur(desk, qui)
    g = _groupe(desk, cab, compte, gid)

    def faire():
        desk.db.execute('BEGIN IMMEDIATE') if not desk.db.in_transaction else None
        if desk.db.execute('SELECT revision FROM assistant_groups109 WHERE id=?', (g['id'],)).fetchone()[0] != revision:
            raise Refus('conflit_de_revision')
        n = desk.db.execute('UPDATE assistant_thread_preferences109 SET group_id=NULL, revision=revision+1 WHERE group_id=? AND cabinet_id=? AND user_id=?',
                            (g['id'], cab, compte)).rowcount
        desk.db.execute('DELETE FROM assistant_groups109 WHERE id=? AND owner_id=?', (g['id'], compte))
        desk.db.commit()
        return {'id': g['id'], 'discussions_sans_groupe': n}
    return executer(desk, compte, cle, 'groupe_supprimer', {'g': g['id'], 'r': revision}, faire)


def membres(desk, qui, thread_id, auth=None):
    """Participants du fil (tout lecteur autorisé) ; comptes proposables (propriétaire seulement : comptes actifs du cabinet, sans adresse)."""
    from . import membres5650
    fil, compte, _ = _fil(desk, qui, thread_id)
    annuaire = membres5650.directory(desk, auth)
    liste = [{'user_id': r['user_id'], 'nom': (annuaire.get(r['user_id']) or {}).get('name') or r['user_id'], 'role': r['role'],
              'role_libelle': H.ROLES_LIBELLES[r['role']], 'actif': r['user_id'] in annuaire}
             for r in desk.db.execute('SELECT user_id, role FROM assistant_thread_members109 WHERE thread_id=? AND cabinet_id=? ORDER BY user_id',
                                      (fil['id'], H.cabinet(desk)))]
    out = {'thread_id': fil['id'], 'proprietaire': (annuaire.get(fil['owner_id']) or {}).get('name') or fil['owner_id'], 'membres': liste,
           'revision': fil['revision'], 'politique': politique(desk)}
    if fil['owner_id'] == compte:
        deja = {m['user_id'] for m in liste} | {compte}
        out['candidats'] = sorted(({'user_id': k, 'nom': v.get('name') or k, 'role': membres5650.ROLES.get(v.get('role'), '')}
                                   for k, v in annuaire.items() if k not in deja and v.get('role') in ROLES), key=lambda x: x['nom'].lower())
    return out


def partager(desk, qui, thread_id, destinataire, role_partage, revision, cle, auth=None):
    """Partage nominatif (Lecteur ou Participant) après contrôle de chaque référence : l'auteur peut la partager, le destinataire peut la
    lire. Une référence interdite ou de provenance inconnue refuse le partage du fil complet, sans en révéler le contenu."""
    fil, compte, role = _controle(desk, 'partager', qui, thread_id)
    if role_partage not in H.ROLES_PARTAGE:
        raise Refus('partage_invalide')
    dest, dest_role = _destinataire(desk, auth, destinataire)
    if dest == fil['owner_id']:
        raise Refus('partage_invalide')
    for ref in fil['references']:
        if not _peut_lire_ref(desk, (compte, role), ref) or not _peut_lire_ref(desk, (dest, dest_role), ref):
            raise Refus('source_non_partageable')
    cab = H.cabinet(desk)

    def faire():
        rev = _cas(desk, fil['id'], revision)
        desk.db.execute('INSERT INTO assistant_thread_members109(thread_id,cabinet_id,user_id,role,granted_by,created_at) VALUES(?,?,?,?,?,?) '
                        'ON CONFLICT(thread_id,cabinet_id,user_id) DO UPDATE SET role=excluded.role, granted_by=excluded.granted_by, revision=revision+1',
                        (fil['id'], cab, dest, role_partage, compte, H.maintenant()))
        desk.db.commit()
        desk.audit('discussions56109_partage', {'fil': H._empreinte(fil['id']), 'par': H._empreinte(compte), 'pour': H._empreinte(dest), 'role': role_partage})
        return {'thread_id': fil['id'], 'user_id': dest, 'role': role_partage, 'revision': rev}
    return executer(desk, compte, cle, 'partager', {'id': fil['id'], 'u': dest, 'r': role_partage, 'v': revision}, faire)


def revoquer(desk, qui, thread_id, user_id, revision, cle):
    """Retire l'accès : la liste, la lecture, la recherche et l'export du destinataire cessent à la requête suivante (aucun cache de droits)."""
    fil, compte, _ = _controle(desk, 'revoquer', qui, thread_id)
    cab = H.cabinet(desk)
    user_id = str(user_id or '')

    def faire():
        rev = _cas(desk, fil['id'], revision)
        n = desk.db.execute('DELETE FROM assistant_thread_members109 WHERE thread_id=? AND cabinet_id=? AND user_id=?', (fil['id'], cab, user_id)).rowcount
        if n != 1:
            raise Refus('introuvable')
        desk.db.commit()
        desk.audit('discussions56109_revocation', {'fil': H._empreinte(fil['id']), 'par': H._empreinte(compte), 'pour': H._empreinte(user_id)})
        return {'thread_id': fil['id'], 'revoque': user_id, 'revision': rev}
    return executer(desk, compte, cle, 'revoquer', {'id': fil['id'], 'u': user_id, 'v': revision}, faire)


def rattacher(desk, qui, thread_id, nature, ident, version, revision, cle):
    """Association de présentation (dossier, courriel, pièce, autre discussion IA) : type fermé, existence et droits de l'auteur vérifiés.
    Elle n'importe rien, ne lance aucune lecture, ne partage rien ; les participants doivent pouvoir lire un nouvel élément de dossier."""
    from .common import load_matters
    fil, compte, role = _controle(desk, 'rattacher', qui, thread_id)
    if nature not in TYPES_LIENS:
        raise Refus('type_non_pris_en_charge')
    ident = str(ident or '').strip()
    version = str(version or '')[:160]
    if not ident or len(ident) > 600:
        raise Refus('reference_indisponible')
    if nature == 'ai_thread':
        if not H.lisible(desk, (compte, role), ident) or ident == fil['id']:
            raise Refus('reference_indisponible')
    elif nature == 'matter' and not any(str(m['id']) == ident for m in load_matters(desk.c)):
        raise Refus('reference_indisponible')
    if nature != 'ai_thread':
        if not _peut_lire_ref(desk, (compte, role), nature + ':' + ident):
            raise Refus('reference_indisponible')
        for m in desk.db.execute('SELECT user_id FROM assistant_thread_members109 WHERE thread_id=? AND cabinet_id=?', (fil['id'], H.cabinet(desk))).fetchall():
            if not _peut_lire_ref(desk, (m['user_id'], _role_de(desk, m['user_id'])), nature + ':' + ident):
                raise Refus('source_non_partageable')

    def faire():
        rev = _cas(desk, fil['id'], revision)
        desk.db.execute('INSERT OR IGNORE INTO assistant_thread_links109(thread_id,cabinet_id,source_kind,source_id,source_version,source_role,created_by,created_at) '
                        'VALUES(?,?,?,?,?,?,?,?)', (fil['id'], H.cabinet(desk), nature, ident, version, 'association', compte, H.maintenant()))
        desk.db.commit()
        return {'thread_id': fil['id'], 'lien': {'type': nature, 'id': ident, 'version': version, 'role': 'association'}, 'revision': rev}
    return executer(desk, compte, cle, 'rattacher', {'id': fil['id'], 'n': nature, 'i': ident, 'v': version, 'r': revision}, faire)


def _role_de(desk, user_id):
    return H._role_annuaire(desk, user_id)


def detacher(desk, qui, thread_id, nature, ident, version, revision, cle):
    """Retire un lien de présentation ; la provenance d'une réponse déjà produite (``used_context``) n'est jamais retirée."""
    fil, compte, _ = _controle(desk, 'rattacher', qui, thread_id)

    def faire():
        rev = _cas(desk, fil['id'], revision)
        desk.db.execute("DELETE FROM assistant_thread_links109 WHERE thread_id=? AND source_kind=? AND source_id=? AND source_version=? AND source_role='association'",
                        (fil['id'], str(nature or ''), str(ident or ''), str(version or '')))
        desk.db.commit()
        return {'thread_id': fil['id'], 'revision': rev}
    return executer(desk, compte, cle, 'detacher', {'id': fil['id'], 'n': nature, 'i': ident, 'v': version, 'r': revision}, faire)


def liens(desk, qui, thread_id):
    fil, _, _ = _fil(desk, qui, thread_id)
    return [dict(r) for r in desk.db.execute('SELECT source_kind, source_id, source_version, source_role, created_at FROM assistant_thread_links109 '
                                             'WHERE thread_id=? ORDER BY created_at', (fil['id'],)).fetchall()]


def changer_contexte(desk, qui, thread_id, dossier, revision, cle):
    """Change le dossier du fil (le même identifiant) : nouveau dossier contrôlé pour le propriétaire ET chaque participant, aucun travail
    actif, révision de contexte enregistrée à partir du prochain message ; les messages antérieurs gardent leur provenance."""
    from . import acces5697
    from .common import load_matters
    from .workspace import chat_scope
    fil, compte, role = _controle(desk, 'contexte', qui, thread_id)
    dossier = str(dossier or '')[:80]
    if dossier and (not any(str(m['id']) == dossier for m in load_matters(desk.c)) or not acces5697.peut_lire_dossier(desk, (compte, role), dossier)):
        raise Refus('dossier_inconnu')
    for m in desk.db.execute('SELECT user_id FROM assistant_thread_members109 WHERE thread_id=? AND cabinet_id=?', (fil['id'], H.cabinet(desk))).fetchall():
        if dossier and not acces5697.peut_lire_dossier(desk, (m['user_id'], _role_de(desk, m['user_id'])), dossier):
            raise Refus('source_non_partageable')
    if _travail_actif(desk, fil['id']) is not None:
        raise Refus('travail_en_cours')

    def faire():
        rev = _cas(desk, fil['id'], revision)
        scope, selected, _ = chat_scope(desk.c, {'matter': dossier, 'key': ''})
        dernier = desk.db.execute('SELECT COALESCE(MAX(id),0) FROM assistant_messages WHERE thread_id=?', (fil['id'],)).fetchone()[0]
        n = desk.db.execute('SELECT COALESCE(MAX(revision),0) FROM assistant_thread_contexts109 WHERE thread_id=?', (fil['id'],)).fetchone()[0]
        if n == 0:   # contexte d'origine, conservé avant le premier changement
            desk.db.execute('INSERT INTO assistant_thread_contexts109(thread_id,revision,matter,scope,from_message_id,changed_by,changed_at) VALUES(?,?,?,?,?,?,?)',
                            (fil['id'], 1, str(fil.get('matter') or ''), str(fil.get('scope') or ''), 0, 'origine', str(fil.get('created') or '')))
            n = 1
        desk.db.execute('INSERT INTO assistant_thread_contexts109(thread_id,revision,matter,scope,from_message_id,changed_by,changed_at) VALUES(?,?,?,?,?,?,?)',
                        (fil['id'], n + 1, selected['id'] if selected else '', scope, dernier + 1, compte, H.maintenant()))
        desk.db.execute("UPDATE assistant_threads SET matter=?, scope=?, mail_key='', updated=? WHERE id=?", (selected['id'] if selected else '', scope, desk.now(), fil['id']))
        desk.db.commit()
        return {'thread_id': fil['id'], 'matter': selected['id'] if selected else '', 'revision': rev}
    return executer(desk, compte, cle, 'contexte', {'id': fil['id'], 'd': dossier, 'r': revision}, faire)


def supprimer(desk, qui, thread_id, revision, confirmation, cle):
    """Suppression logique du fil commun (corbeille), pour tous les participants : politique du cabinet ET droit individuel « supprimer »
    ET propriété ; refusée tant qu'un travail lié est actif. Aucune pièce, aucun courriel, aucun livrable n'est supprimé."""
    fil, compte, _ = _controle(desk, 'supprimer', qui, thread_id)
    if confirmation != CONFIRMATION:
        raise Refus('confirmation_requise')

    def faire():
        if not desk.db.in_transaction:
            desk.db.execute('BEGIN IMMEDIATE')
        if _travail_actif(desk, fil['id']) is not None:      # relu dans la même transaction que l'écriture
            raise Refus('travail_en_cours')
        rev = _cas(desk, fil['id'], revision)
        desk.db.execute('UPDATE assistant_thread_owners109 SET deleted_at=?, deleted_by=? WHERE thread_id=?', (H.maintenant(), compte, fil['id']))
        desk.db.commit()
        desk.audit('discussions56109_corbeille', {'fil': H._empreinte(fil['id']), 'par': H._empreinte(compte)})
        return {'thread_id': fil['id'], 'corbeille': True, 'revision': rev}
    return executer(desk, compte, cle, 'supprimer', {'id': fil['id'], 'r': revision}, faire)


def corbeille(desk, qui):
    cab, compte, _ = H.acteur(desk, qui)
    if not H.schema_present(desk):
        return {'items': [], 'jours': politique(desk)['corbeille_jours']}
    jours = politique(desk)['corbeille_jours']
    rows = desk.db.execute('SELECT t.id, t.title, t.matter, o.deleted_at, o.revision FROM assistant_threads t JOIN assistant_thread_owners109 o ON o.thread_id=t.id '
                           'WHERE o.cabinet_id=? AND o.owner_id=? AND o.deleted_at IS NOT NULL ORDER BY o.deleted_at DESC LIMIT 200', (cab, compte)).fetchall()
    out = []
    for r in rows:
        try:
            fin = datetime.fromisoformat(r['deleted_at']) + timedelta(days=jours)
        except (TypeError, ValueError):
            continue
        out.append({'id': r['id'], 'title': r['title'] or 'Discussion', 'matter': r['matter'] or '', 'deleted_at': r['deleted_at'],
                    'restaurable': fin > datetime.now(timezone.utc), 'revision': r['revision']})
    return {'items': out, 'jours': jours}


def restaurer(desk, qui, thread_id, revision, cle, auth=None):
    """Restauration depuis la corbeille : identité, cabinet, droits actuels du propriétaire revérifiés ; un participant dont le compte n'est
    plus actif ne retrouve pas l'accès."""
    from . import membres5650
    fil, compte, _ = _controle(desk, 'restaurer', qui, thread_id, avec_supprime=True)
    if not fil.get('deleted_at'):
        raise Refus('pas_dans_la_corbeille')
    try:
        if datetime.fromisoformat(fil['deleted_at']) + timedelta(days=politique(desk)['corbeille_jours']) <= datetime.now(timezone.utc):
            raise Refus('corbeille_expiree')
    except ValueError:
        raise Refus('corbeille_expiree') from None
    actifs = membres5650.directory(desk, auth)

    def faire():
        rev = _cas(desk, fil['id'], revision)
        retires = 0
        for m in desk.db.execute('SELECT user_id FROM assistant_thread_members109 WHERE thread_id=?', (fil['id'],)).fetchall():
            if m['user_id'] not in actifs:
                retires += desk.db.execute('DELETE FROM assistant_thread_members109 WHERE thread_id=? AND user_id=?', (fil['id'], m['user_id'])).rowcount
        desk.db.execute("UPDATE assistant_thread_owners109 SET deleted_at=NULL, deleted_by='' WHERE thread_id=?", (fil['id'],))
        desk.db.commit()
        return {'thread_id': fil['id'], 'restaure': True, 'acces_retires': retires, 'revision': rev}
    return executer(desk, compte, cle, 'restaurer', {'id': fil['id'], 'r': revision}, faire)


# ------------------------------------------------------------------------------------------------ routes
def allowed(role, method, api):
    """``standalone_auth.allowed`` : écritures POST pour les trois rôles du cabinet (le service relit propriété, ACL, politique et droits) ;
    « politique » : administrateur seulement."""
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):].strip('/') if reste.startswith(PREFIXE) else ''
    if method != 'POST' or reste not in ECRITURES or role not in ROLES:
        return False
    return reste != 'politique' or role == 'administrateur'


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    qui = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    try:
        if method != 'POST' or reste not in ECRITURES:
            raise Stop('introuvable')
        if not allowed(qui[1], method, name):
            raise Stop('role_insuffisant')
        try:
            longueur = int(env.get('CONTENT_LENGTH') or 0)
        except ValueError:
            longueur = MAX_POST + 1
        if longueur > MAX_POST:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        d = check_post(env, auth)
        tid, cle, rev = str(d.get('id') or ''), d.get('cle'), d.get('revision')
        if reste == 'creer':
            out = creer(desk, qui, d.get('titre'), d.get('dossier'), cle)
        elif reste == 'renommer':
            out = renommer(desk, qui, tid, d.get('titre'), rev, cle)
        elif reste == 'preferences':
            out = preferences(desk, qui, tid, d.get('valeurs'), cle)
        elif reste == 'groupe-creer':
            out = groupe_creer(desk, qui, d.get('nom'), cle)
        elif reste == 'groupe-modifier':
            out = groupe_modifier(desk, qui, d.get('groupe'), d.get('nom'), d.get('position'), rev, cle)
        elif reste == 'groupe-supprimer':
            out = groupe_supprimer(desk, qui, d.get('groupe'), rev, cle)
        elif reste == 'partager':
            out = partager(desk, qui, tid, d.get('compte'), d.get('role'), rev, cle, auth)
        elif reste == 'revoquer':
            out = revoquer(desk, qui, tid, d.get('compte'), rev, cle)
        elif reste == 'rattacher':
            out = rattacher(desk, qui, tid, d.get('type'), d.get('element'), d.get('version'), rev, cle)
        elif reste == 'detacher':
            out = detacher(desk, qui, tid, d.get('type'), d.get('element'), d.get('version'), rev, cle)
        elif reste == 'contexte':
            out = changer_contexte(desk, qui, tid, d.get('dossier'), rev, cle)
        elif reste == 'supprimer':
            out = supprimer(desk, qui, tid, rev, d.get('confirmation'), cle)
        elif reste == 'restaurer':
            out = restaurer(desk, qui, tid, rev, cle, auth)
        else:
            out = regler_politique(desk, qui, d.get('valeurs'), rev, cle)
        return H._json(out)
    except Stop as ex:
        return H.erreur(str(ex).split(':')[0])
