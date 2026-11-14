"""5.6.89 : routes ``/api440/m5689/dossiers/*``, ``assertions/*``, ``missions/*``, ``playbooks/*`` et ``accords/*`` (chapitres 33 à 36 et 48).

Le compte et le rôle viennent de la requête authentifiée, jamais du corps : ``cabinet_id``, ``allowed_actions`` ou un rôle envoyés par le navigateur ne sont pas lus. Chaque écriture est déclarée ici, inconnue refusée ; le service refait ses contrôles.
Toute écriture exige CSRF et une clé d'idempotence ; une version périmée répond 409. Les actions longues rendent un identifiant et un état : une demande en file n'est jamais présentée comme une action terminée.
"""
import json
import re
import sqlite3

from .common import Stop, clean_path, load_matters, under
from . import accords5689, assertions5689 as A, consultation5689 as conseil, extraction5689, personnes5689, playbook5689

ID = r'[A-Za-z0-9_.-]{1,80}'
# (motif de route, rôle minimal) : tout ce qui n'est pas ici est refusé en écriture
ECRITURES = tuple((re.compile('^' + motif.replace('{}', ID) + '$'), droit) for motif, droit in (
    (r'dossiers/{}/assertions', ''), (r'dossiers/{}/sources/importer', ''), (r'dossiers/{}/sources/{}/qualifier', ''), (r'dossiers/{}/sources/{}/extraire', ''), (r'dossiers/{}/sources/{}/retirer', 'valider'),
    (r'dossiers/{}/rapprochements/proposer', ''), (r'dossiers/{}/rapprochements/{}/decider', 'valider'), (r'dossiers/{}/parties/{}/role', ''),
    (r'assertions/{}/validation', 'valider'), (r'assertions/{}/relation', ''),
    (r'missions', ''), (r'missions/{}/reprendre', ''), (r'missions/{}/annuler', ''), (r'missions/{}/actualiser', ''), (r'missions/{}/reponses', 'valider'), (r'missions/{}/vers-livrable', ''), (r'missions/propositions/{}/resoudre', 'valider'), (r'missions/propositions/{}/decider', 'valider'), (r'missions/{}/reporter', 'valider'),
    (r'playbooks', 'valider_modele'), (r'playbooks/{}/publier', 'valider_modele'), (r'playbooks/{}/version', 'valider_modele'), (r'playbooks/{}/regle', 'valider_modele'), (r'playbooks/{}/retirer-regle', 'valider_modele'), (r'playbooks/{}/proposer-regle', ''), (r'playbooks/propositions/{}/decider', 'valider_modele'),
    (r'accords', 'valider'), (r'accords/{}/extraire', ''), (r'accords/{}/obligations', 'valider'), (r'accords/{}/revalider', ''), (r'accords/obligations/{}/evenement', 'valider'), (r'accords/occurrences/{}/decider', 'valider')))
ROLES_DROITS = {'valider': ('administrateur', 'avocat'), 'valider_modele': ('administrateur', 'avocat')}
STATUS = {'version_perimee': '409 Conflict', 'cle_idempotence_deja_utilisee': '409 Conflict', 'requete_reutilisee_avec_autres_donnees': '409 Conflict', 'element_remplace': '409 Conflict', 'proposition_deja_traitee': '409 Conflict', 'rapprochement_deja_traite': '409 Conflict', 'mission_sans_question': '409 Conflict', 'playbook_deja_publie': '409 Conflict', 'playbook_fige': '409 Conflict', 'evenement_deja_declare': '409 Conflict', 'mission_non_reprenante': '409 Conflict', 'conflit_edition_concurrente': '409 Conflict',
          'dossier_reserve': '403 Forbidden', 'role_insuffisant': '403 Forbidden', 'validation_avocat_requise': '403 Forbidden', 'element_absent': '404 Not Found', 'dossier_absent': '404 Not Found', 'version_source_absente': '404 Not Found', 'mission_absente': '404 Not Found', 'playbook_absent': '404 Not Found', 'accord_absent': '404 Not Found', 'route_inconnue': '404 Not Found', 'obligation_absente': '404 Not Found', 'occurrence_absente': '404 Not Found'}
MESSAGES = {'version_perimee': 'Cet élément a changé depuis votre affichage : rechargez la page ; rien n’a été écrasé.', 'cle_idempotence_absente': 'Requête incomplète : rechargez la page et recommencez.', 'cle_idempotence_deja_utilisee': 'Cette demande a déjà servi pour une autre action : rechargez la page.',
            'dossier_reserve': 'Ce dossier est réservé.', 'validation_avocat_requise': 'Cette décision revient à un avocat.', 'passage_a_relire': 'Relisez le passage dans sa source avant de valider : les mêmes mots ne prouvent pas que la source soutient l’affirmation.', 'pagination_word_refusee': 'Un Word n’a pas de pagination stable : indiquez un paragraphe ou un signet, ou appuyez-vous sur un PDF figé.',
            'source_a_rattacher': 'Cet élément repris n’a pas de version de source : rattachez-la avant de le valider.', 'preuve_signature_requise': 'Indiquez la preuve du prestataire de signature, ou attestez vous-même de la signature. Le nom du fichier ne suffit pas.', 'attestation_explicite_requise': 'L’attestation de l’avocat doit être cochée expressément.',
            'playbook_fige': 'Une version publiée est figée : créez une nouvelle version.', 'playbook_sans_regle': 'Ajoutez au moins une règle avant de publier.', 'playbook_deja_publie': 'Cette version est déjà publiée : rechargez la page.', 'proposition_deja_traitee': 'Cette proposition a déjà été traitée : rechargez la page.',
            'aucune_proposition_acceptee': 'Acceptez au moins une proposition avant de refaire les documents.', 'evenement_deja_declare': 'Un autre événement a déjà été déclaré pour cette obligation.', 'regle_incomplete': 'Indiquez le nom de la règle, son type de clause et la position souhaitée.', 'playbook_incomplet': 'Indiquez le domaine, la juridiction et le côté conseillé.',
            'playbook_non_publie': 'Aucun playbook publié pour ce domaine, cette juridiction et ce côté.', 'contrat_a_designer': 'Désignez la version du contrat à analyser.', 'contrat_word_requis': 'La revue porte sur un contrat au format Word.', 'evenement_non_documente': 'L’événement déclencheur doit être appuyé par une source du dossier.', 'reponses_incompletes': 'Répondez à chaque question.',
            'source_mission_autre_dossier': 'Une source demandée n’appartient pas à ce dossier.', 'mission_sans_question': 'Cette mission n’attend plus de réponse : rechargez la page.', 'occurrence_perimee': 'Cette proposition n’est plus valable : le document ou les droits ont changé.', 'accord_non_actif': 'Cet accord n’est plus en vigueur ou doit être recontrôlé.', 'projet_bloque_par_le_controle': 'Le contrôle a bloqué le projet : voyez le motif dans les limites de la mission.'}


def droit_requis(route):
    """Droit fin d'une écriture, lu par ``membres5650`` ; None pour une route déclarée sans droit fin, 'inconnue' pour le reste."""
    route = route.split('?', 1)[0].strip('/')
    for motif, droit in ECRITURES:
        if motif.match(route):
            return droit
    return 'inconnue'


def allowed(role, route):
    """Lecture ouverte aux rôles du cabinet (les services filtrent par dossier). Écriture : déclarée, et rôle suffisant pour un droit d'avocat."""
    if role not in ('administrateur', 'avocat', 'assistant'):
        return False
    droit = droit_requis(route)
    if droit == 'inconnue':
        return False
    return role in ROLES_DROITS.get(droit, ('administrateur', 'avocat', 'assistant'))


def _out(value, status='200 OK'):
    from .web440 import json_out
    return json_out(value, status)


def _erreur(ex):
    from .budget_ledger5689 import AttenteBudget
    from .erreurs5634 import explain
    if isinstance(ex, AttenteBudget):
        return _out(ex.vue())
    code = str(ex).split(':')[0]
    return _out({'error': code, 'message': MESSAGES.get(code) or explain(code, getattr(ex, 'detail', None))}, STATUS.get(code, '400 Bad Request'))


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        return _erreur(ex)


def _api(env, desk, auth, prefix, name, args, method):
    from .membres5650 import hidden_for
    from .web440 import check_post
    from .web567 import actor
    owner, role = actor(env)
    if role not in ('administrateur', 'avocat', 'assistant'):
        raise Stop('role_insuffisant')
    route = name[len('m5689/'):].split('?', 1)[0].strip('/')
    parts = route.split('/')
    A.ensure_schema(desk)
    hidden = hidden_for(desk.c['state_dir'], owner, role)
    if method == 'GET':
        return _lecture(desk, parts, args, owner, role)
    if method != 'POST':
        raise Stop('methode_refusee')
    if not allowed(role, route):
        raise Stop('route_inconnue' if droit_requis(route) == 'inconnue' else 'role_insuffisant')
    data = check_post(env, auth)
    return _ecriture(desk, parts, data, owner, role, hidden, env)


def _lecture(desk, parts, args, owner, role):
    tete = parts[0]
    if tete == 'dossiers' and len(parts) >= 3:
        mid, quoi = parts[1], parts[2]
        A.acces(desk, mid, owner, role)
        if quoi == 'assertions' and len(parts) == 3:
            statuts = [s for s in str(args.get('statut') or '').split(',') if s in A.STATUTS]
            return _out({'assertions': A.lister(desk, mid, owner, role, args.get('nature') or None, statuts or None), 'avis': 'Valider un élément confirme son enregistrement, pas sa vérité.'})
        if quoi == 'projection':
            return _out(A.projection(desk, mid, owner, role))
        if quoi == 'recherche':
            return _out(A.rechercher(desk, str(args.get('q') or '')[:300], owner, role, mid, {k: args.get(k) for k in ('nature', 'etat', 'type', 'partie')}, str(args.get('mission') or '')))
        if quoi == 'sources':
            return _out({'sources': [{k: v[k] for k in ('version_id', 'source_id', 'sha256', 'categorie', 'etat', 'piece_partie', 'piece_numero', 'piece_titre', 'document_date', 'importe_le')} | {'nom': v['path'].rsplit('/', 1)[-1], 'categorie_label': A.CATEGORIES.get(v['categorie'], '')} for v in A.versions(desk, mid, args.get('categorie') or '', str(args.get('toutes') or '') != '1')]})
        if quoi == 'rapprochements':
            return _out({'rapprochements': personnes5689.lister(desk, mid), 'avis': 'Une similarité de nom ne suffit pas : chaque rapprochement reste une proposition réversible.'})
        if quoi == 'missions':
            return _out({'missions': conseil.lister(desk, mid, owner, role)})
        if quoi == 'accords':
            return _out({'accords': accords5689.lister(desk, mid, owner, role)})
        if quoi == 'extractions' and len(parts) == 4:
            run = extraction5689.suivi(desk, parts[3], owner, role)
            if run['matter'] != mid:
                raise Stop('extraction_absente')
            return _out(run)
    if tete == 'assertions' and len(parts) == 3 and parts[2] == 'source':
        return _out(A.ouvrir_source(desk, parts[1], owner, role))
    if tete == 'assertions' and len(parts) == 2:
        row = A.charger(desk, parts[1], owner, role)
        return _out({**A.vue(row), 'historique': A.historique(desk, parts[1], owner, role)})
    if tete == 'missions' and len(parts) == 2:
        return _out(conseil.vue(desk, parts[1], owner, role))
    if tete == 'playbooks':
        if len(parts) == 1:
            return _out({'playbooks': playbook5689.lister(desk)})
        if len(parts) == 2:
            return _out(playbook5689.vue(desk, parts[1]))
    if tete == 'accords' and len(parts) == 2:
        accords5689._charger(desk, parts[1], owner, role)
        return _out(accords5689.vue(desk, parts[1]))
    raise Stop('route_inconnue')


def _importer(desk, mid, data, owner, role):
    """Relit le fichier dans le dossier : le navigateur désigne un emplacement du dossier, le serveur calcule l'empreinte."""
    from .document_projects import _dav
    A.acces(desk, mid, owner, role)
    matter = next(m for m in load_matters(desk.c) if str(m['id']) == str(mid))
    path = clean_path(str(data.get('path') or ''))
    if not under(path, matter['path']):
        raise Stop('source_mission_autre_dossier')
    client = _dav(desk)
    info = client.stat(path)
    raw = client.download({**info, 'path': path})
    row = A.importer_version(desk, mid, path, raw, etag=info.get('etag') or '', source_id=str(info.get('fileid') or ''), emetteur=str(data.get('emetteur') or ''), document_date=str(data.get('document_date') or ''), acteur=owner, categorie=str(data.get('categorie') or ''))
    return {k: row[k] for k in ('version_id', 'source_id', 'sha256', 'categorie', 'etat')} | {'candidats': json.loads(row['categorie_candidats']), 'analyse': _reception56100(desk, mid, row['version_id'], owner, role)}


def _reception56100(desk, mid, version_id, owner, role):
    """5.6.100 (lot G) : pièce vérifiée classée dans un dossier contentieux → événement de réception (sans modèle). Jamais bloquant pour le classement."""
    try:
        from .delta56100 import reception
        return {'evenement': reception(desk, mid, version_id, (owner, role), 'classement') or ''}
    except (Stop, sqlite3.Error) as ex:
        desk.audit('delta56100_reception_impossible', {'matter': str(mid), 'cause': str(ex)[:80]})
        return {'evenement': '', 'etat': 'non_enregistre'}


def _ecriture(desk, parts, data, owner, role, hidden, env):
    tete = parts[0]
    if tete == 'dossiers':
        mid = parts[1]
        A.acces(desk, mid, owner, role)
        if parts[2] == 'assertions':
            return _out(A.ajouter(desk, mid, data, owner, role))
        if parts[2] == 'parties':
            personnes5689.ajouter_role(desk, mid, parts[3], data.get('role'), owner, data.get('source_ref'))
            return _out({'roles': personnes5689.roles(desk, mid, parts[3])})
        if parts[2] == 'rapprochements':
            if parts[3] == 'proposer':
                return _out({'nouveaux': personnes5689.rapprochements_entre_dossiers(desk, mid), 'message': 'Propositions à décider : rien n’a été fusionné.'})
            return _out(personnes5689.decider(desk, parts[3], str(data.get('decision') or ''), owner, hidden))
        if parts[2] == 'sources':
            if parts[3] == 'importer':
                return _out(_importer(desk, mid, data, owner, role))
            if parts[4] == 'qualifier':
                out = A.qualifier_piece(desk, mid, parts[3], data, owner, role)
                return _out({k: out[k] for k in ('version_id', 'categorie', 'piece_partie', 'piece_numero', 'piece_titre', 'sha256')} | {'analyse': _reception56100(desk, mid, out['version_id'], owner, role)})
            if parts[4] == 'retirer':
                return _out({**A.retirer_source(desk, mid, parts[3], owner, role, data.get('motif') or ''), 'message': 'Source retirée : les éléments et décisions restent, leur obsolescence est affichée.'})
            run = extraction5689.lancer(desk, mid, parts[3], owner, role, data.get('cle'))
            return _out({**run, 'message': 'Lecture demandée (travail n° %s) : les propositions apparaîtront à la fin.' % run['job_id']})
    if tete == 'assertions':
        if parts[2] == 'validation':
            return _out(A.decider(desk, parts[1], data, owner, role))
        row = A.charger(desk, parts[1], owner, role)
        A.relier(desk, row['matter'], parts[1], str(data.get('relation') or ''), str(data.get('cible') or ''), data.get('justification') or '', owner)
        return _out({'relation': data.get('relation'), 'message': 'Relation enregistrée.'})
    if tete == 'missions':
        if len(parts) == 1:
            return _out({**conseil.creer(desk, data, owner, role), 'message': 'Mission enregistrée : son avancement s’affiche ici.'})
        if parts[1] == 'propositions':
            if parts[3] == 'decider':
                return _out(playbook5689.decider_proposition(desk, parts[2], data, owner, role))
            return _out({**playbook5689.resoudre(desk, parts[2], str(data.get('choix') or ''), owner, role, data.get('revision') if data.get('revision') is not None else -1, data.get('texte') or ''), 'message': 'Choix enregistré.'})
        action = parts[2]
        if action == 'reponses':
            return _out({**conseil.repondre(desk, parts[1], data, owner, role), 'message': 'Décisions enregistrées : la mission reprend.'})
        if action == 'reprendre':
            return _out({**conseil.reprendre(desk, parts[1], data, owner, role), 'message': 'Mission relancée : seul ce qui manque sera refait.'})
        if action == 'annuler':
            return _out({**conseil.annuler(desk, parts[1], owner, role), 'message': 'Mission annulée.'})
        if action == 'actualiser':
            out = playbook5689.actualiser(desk, parts[1], owner, role)
            return _out({**out, 'message': ('%d clause(s) en conflit : un choix est demandé.' % len(out['conflits'])) if out['conflits'] else ('Contrat modifié, sans effet sur les propositions.' if out['modifie'] else 'Contrat inchangé depuis l’analyse.')})
        if action == 'reporter':
            return _out(playbook5689.reporter_decisions(desk, parts[1], data, owner, role))
        from .membres5650 import effective_rights
        return _out(playbook5689.vers_livrable(desk, parts[1], data.get('document_id'), data.get('cle'), owner, role, hidden, effective_rights(desk.c['state_dir'], owner, role)))
    if tete == 'playbooks':
        if len(parts) == 1:
            A._cle(data.get('cle'))
            return _out({**playbook5689.creer(desk, data, owner, role), 'message': 'Brouillon créé : ajoutez ses règles, puis publiez-le.'})
        if parts[1] == 'propositions':
            out = playbook5689.decider_regle(desk, parts[2], str(data.get('decision') or ''), owner, role, str(data.get('cle_regle') or ''))
            return _out({**out, 'message': 'Règle ajoutée à une nouvelle version en brouillon, à publier.' if out['brouillon'] else 'Proposition refusée.'})
        if parts[2] == 'publier':
            return _out({**playbook5689.publier(desk, parts[1], owner, role, data.get('version')), 'message': 'Version publiée : elle s’applique aux nouvelles missions.'})
        if parts[2] == 'version':
            A._cle(data.get('cle'))
            return _out({**playbook5689.nouvelle_version(desk, parts[1], data, owner, role), 'message': 'Nouvelle version en brouillon.'})
        if parts[2] == 'regle':
            return _out({**playbook5689.ajouter_regle(desk, parts[1], data, owner, role), 'message': 'Règle enregistrée dans le brouillon.'})
        if parts[2] == 'retirer-regle':
            return _out({**playbook5689.retirer_regle(desk, parts[1], data.get('regle'), owner, role), 'message': 'Règle retirée du brouillon.'})
        return _out({'proposition': playbook5689.proposer_regle(desk, parts[1], data, owner, role), 'message': 'Proposition de règle enregistrée, à approuver avec sa portée.'})
    if tete == 'accords':
        if len(parts) == 1:
            return _out({**accords5689.creer(desk, str(data.get('dossier') or ''), data, owner, role), 'message': 'Accord enregistré sur cette version signée.'})
        if parts[1] == 'obligations':
            return _out({**accords5689.declarer_evenement(desk, parts[2], data, owner, role), 'message': 'Événement enregistré : les échéances sont proposées.'})
        if parts[1] == 'occurrences':
            out = accords5689.decider(desk, parts[2], str(data.get('decision') or ''), owner, role, data.get('version'))
            return _out({'id': out['id'], 'etat': out['etat'], 'message': 'Inscrit à l’agenda et aux tâches du cabinet.' if out['etat'] == 'acceptee' else 'Proposition refusée.'})
        if parts[2] == 'extraire':
            out = accords5689.lancer_extraction(desk, parts[1], owner, role)
            return _out({**out, 'message': 'Relevé des obligations demandé (travail n° %s).' % out['job_id']})
        if parts[2] == 'revalider':
            out = accords5689.revalider(desk, parts[1], owner, role)
            return _out({**out, 'message': ('%d proposition(s) invalidée(s).' % out['invalidees']) if out['invalidees'] else 'Document signé inchangé.'})
        return _out({**accords5689.ajouter_obligation(desk, parts[1], data, owner, role), 'message': 'Obligation ajoutée.'})
    raise Stop('route_inconnue')
