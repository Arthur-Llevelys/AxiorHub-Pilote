"""5.6.89 (chapitre 19, AI08) : situations de pertinence pour le banc d'essai, et lecture de ce qui a été mesuré.

Six situations fictives : synthèse de courriels, réponse à soi-même, document demandé sans information complète, sources contradictoires, correction immédiate après l'IA, réouverture d'une nouvelle version. Elles entrent dans le
banc existant (``banc5689``), avec ses familles, ses règles de partage et ses contrôles sur trace. Chargées d'ici, elles forment un protocole « exercice » : il rode l'outil et ne permet aucune réception. Le cabinet peut les recopier
dans son propre protocole (``banc5689.ajouter_cas``).

Rien ici ne note une réponse. ``bilan`` relit ce qu'une personne a saisi (fidélité, omissions, bonnes sources, bon dossier, destinataire, temps de correction, délai utile) et laisse ``None`` ce qui n'a pas été mesuré. Il n'y a pas de
score : un essai sur fournisseur simulé exerce le circuit et ne mesure pas la qualité d'une réponse juridique. Un avis métier est requis.
"""
import json

from . import banc5689 as banc

AVERTISSEMENT = 'Les essais sur fournisseur simulé exercent le circuit : ils ne mesurent pas la qualité d’une réponse juridique. Un avis métier est requis.'
LECTURE = ('lire_sources', 'rediger_projet')
BROUILLON = LECTURE + ('preparer_brouillon',)
MESURES = {'fidelite': 'Fidélité aux sources', 'omissions': 'Omissions', 'bonnes_sources': 'Bonnes sources', 'bon_dossier': 'Bon dossier', 'destinataire': 'Bon destinataire', 'correction_s': 'Temps de correction (secondes)', 'delai_utile_s': 'Délai utile (secondes)'}


def _source(ident, titre, texte):
    return {'id': ident, 'titre': titre, 'texte': texte}


CAS = (
    {'ref': 'P01', 'situation': 'synthese_courriels', 'famille': 'reponse_dossier', 'partage': 'reglage',
     'question': 'Synthétiser les trois derniers courriels du dossier DOS-001 et dire ce qui attend une réponse.',
     'sources': (_source('P01-S1', 'Courriel de M. BERNARD, fictif', 'Demande de la date de la prochaine audience.'), _source('P01-S2', 'Courriel du greffe, fictif', 'Calendrier de procédure : conclusions attendues pour le 5 février.'),
                 _source('P01-S3', 'Courriel de M. DURAND, fictif', 'Transmission d’une facture de ZORVAL, sans demande.')),
     'livrables': ('Synthèse',), 'faits_attendus': ('Audience demandée par M. BERNARD', 'Conclusions attendues pour le 5 février'), 'reserves': ('La facture transmise n’appelle pas de réponse',),
     'criteres': ('Chaque point cite son courriel', 'Ce qui attend une réponse est distingué'), 'actions_permises': LECTURE, 'destinataires': (), 'references_verifiables': ('P01-S1', 'P01-S2', 'P01-S3')},
    {'ref': 'P02', 'situation': 'reponse_a_soi_meme', 'famille': 'reponse_dossier', 'partage': 'evaluation',
     'question': 'Préparer la réponse au dernier courriel du fil du dossier DOS-001, écrit par le cabinet lui-même.',
     'sources': (_source('P02-S1', 'Courriel du cabinet, fictif', 'Message du cabinet à M. BERNARD : relance sur les pièces.'), _source('P02-S2', 'Courriel de M. BERNARD, fictif', 'Message antérieur : il promet les pièces sous huit jours.')),
     'livrables': ('Brouillon de courriel',), 'faits_attendus': ('Dernier message écrit par le cabinet',), 'reserves': ('Ne pas répondre au cabinet : le destinataire est M. BERNARD, ou aucune réponse n’est due',),
     'criteres': ('Le cabinet ne se répond pas à lui-même',), 'actions_permises': BROUILLON, 'destinataires': ('bernard@example.test',), 'references_verifiables': ('P02-S1', 'P02-S2')},
    {'ref': 'P03', 'situation': 'document_incomplet', 'famille': 'contrat', 'partage': 'reglage',
     'question': 'Préparer la convention d’honoraires de la société KERVILAC, SIREN 000010033.',
     'sources': (_source('P03-S1', 'Note d’entretien, fictive', 'Mission de conseil pour KERVILAC. Le taux horaire n’a pas été discuté.'),),
     'livrables': ('Convention partielle',), 'faits_attendus': ('Mission de conseil',), 'reserves': ('Taux horaire absent',),
     'criteres': ('Brouillon partiel marqué à compléter', 'Aucun montant inventé'), 'actions_permises': LECTURE, 'destinataires': (), 'references_verifiables': ('P03-S1',)},
    {'ref': 'P04', 'situation': 'sources_contradictoires', 'famille': 'consultation', 'partage': 'evaluation',
     'question': 'Quelle est la date de résiliation du bail ORLANDEC ?',
     'sources': (_source('P04-S1', 'Lettre de ZORVAL, fictive', 'Résiliation notifiée le 3 mars, selon ZORVAL.'), _source('P04-S2', 'Accusé de réception, fictif', 'Pli présenté le 9 mars et non retiré.')),
     'livrables': ('Note',), 'faits_attendus': ('Deux dates différentes selon la source',), 'reserves': ('Ne pas trancher sans la décision de l’avocat',),
     'criteres': ('La contradiction est signalée', 'Chaque date reste attribuée à sa source'), 'actions_permises': LECTURE, 'destinataires': (), 'references_verifiables': ('P04-S1', 'P04-S2')},
    {'ref': 'P05', 'situation': 'correction_apres_ia', 'famille': 'acte_contentieux', 'partage': 'reglage',
     'question': 'Reprendre le projet de bordereau du dossier RG 24/01234 après la correction de l’avocat sur la pièce 2.',
     'sources': (_source('P05-S1', 'Projet de l’IA, fictif', 'Pièce 2 : facture du 5 mai.'), _source('P05-S2', 'Correction de l’avocat, fictive', 'Pièce 2 : avenant du 5 mai, pas une facture.')),
     'livrables': ('Bordereau',), 'faits_attendus': ('Pièce 2 : avenant du 5 mai',), 'reserves': ('La correction de l’avocat prime',),
     'criteres': ('La correction est conservée', 'Le temps de correction est relevé'), 'actions_permises': LECTURE, 'destinataires': (), 'references_verifiables': ('P05-S1', 'P05-S2')},
    {'ref': 'P06', 'situation': 'nouvelle_version', 'famille': 'contrat', 'partage': 'evaluation',
     'question': 'Rouvrir le contrat ZORVAL à sa version 3 et dire ce qui change par rapport à la relecture de la version 2.',
     'sources': (_source('P06-S1', 'Contrat ZORVAL version 2, fictif', 'Durée de deux ans.'), _source('P06-S2', 'Contrat ZORVAL version 3, fictif', 'Durée de trois ans, reconduction tacite ajoutée.')),
     'livrables': ('Tableau des modifications',), 'faits_attendus': ('Durée de trois ans', 'Reconduction tacite ajoutée'), 'reserves': ('La relecture de la version 2 ne vaut pas pour la version 3',),
     'criteres': ('Chaque ligne cite sa version',), 'actions_permises': LECTURE, 'destinataires': (), 'references_verifiables': ('P06-S1', 'P06-S2')},
)


def _protocole_cas(cas):
    return {k: v for k, v in cas.items() if k != 'situation'}


def charger(desk, owner, cle):
    """Protocole d'exercice, figé d'emblée, comme le corpus d'exercice : une même clé rend le même protocole. Il ne mène à aucune réception."""
    banc.ensure_schema(desk)
    cle = banc._cle(cle)
    existe = banc._reprise(desk, 'banc_protocoles5689', cle, None)
    if existe:
        return banc.lire_protocole(desk, existe)
    protocole = banc.creer_protocole(desk, {'cle': cle, 'nom': 'Situations de pertinence, exercice fictif', '_exercice': True}, owner)
    for brut in CAS:
        banc._inserer_cas(desk, protocole['id'], banc.valider_cas({**_protocole_cas(brut), 'origine': 'fictif'}))
    desk.db.commit()
    return banc.figer(desk, protocole['id'], owner, protocole['revision'])


def _controle(desk, essai, nature):
    ligne = desk.db.execute('SELECT resultat FROM banc_controles5689 WHERE essai=? AND nature=?', (essai, nature)).fetchone()
    if ligne is None or ligne['resultat'] == 'non_renseigne':
        return None
    return ligne['resultat'] == 'conforme'


def bilan(desk, run_id):
    """Ce qui a été relevé, situation par situation. ``None`` : non mesuré. Une source hors du dossier du cas fait échouer « bon dossier » : c'est le contrôle sur trace du banc. Aucun total, aucune note."""
    run = banc._run(desk, run_id)
    situations = {c['ref']: c['situation'] for c in CAS}
    lignes = []
    for cas in desk.db.execute('SELECT * FROM banc_cas5689 WHERE protocole=? AND partage=? ORDER BY ref', (run['protocole'], run['phase'])).fetchall():
        essais = desk.db.execute('SELECT * FROM banc_essais5689 WHERE run_id=? AND cas=?', (run_id, cas['id'])).fetchall()
        for e in essais or (None,):
            mesures = dict.fromkeys(MESURES)
            if e is not None:
                sources = _controle(desk, e['id'], 'sources')
                mesures.update(fidelite=e['fidelite'] or None, omissions=e['omissions'], bonnes_sources=sources, bon_dossier=sources, destinataire=None if e['destinataires_exacts'] is None else bool(e['destinataires_exacts']), correction_s=e['correction_s'],
                               delai_utile_s=None if e['preparation_s'] is None and e['execution_s'] is None else (e['preparation_s'] or 0) + (e['execution_s'] or 0))
            avis = desk.db.execute('SELECT COUNT(*) FROM banc_avis5689 WHERE essai=?', (e['id'],)).fetchone()[0] if e is not None else 0
            lignes.append({'cas': cas['ref'], 'situation': situations.get(cas['ref'], ''), 'configuration': e['configuration'] if e is not None else None, 'statut': e['statut'] if e is not None else None, 'mesures': mesures, 'avis_metier': avis})
    return {'essai': run_id, 'lignes': lignes, 'libelles': MESURES, 'avis_metier_requis': True, 'qualite_juridique': None, 'avertissement': AVERTISSEMENT, 'contenu': json.dumps(sorted(situations.values()), ensure_ascii=False)}
