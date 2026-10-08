"""5.6.14 (sections 5, 8, 4) : parcours de référence des missions complexes.

Chaque parcours est un graphe de tâches identifiées (code, rôle, type, dépendances explicites, résultat obligatoire). « T07 à T11 selon
section » est remplacé par une liste explicite par section. Les rôles définissent les entrées, le type de résultat et la fonction de
modèle (routage par fonction, M07/C20). Un parcours ne crée pas de conversation isolée : toutes les tâches partagent les
artefacts de la mission.
"""

# rôle → (libellé, fonction de modèle routée, rôle 5.6.8 requis dans les initiatives, type de résultat par défaut)
ROLES = {
    'superviseur': ('Superviseur', 'assistant', 'A0', 'plan'),
    'documentaliste': ('Documentaliste', 'attachment_review', 'A2', 'inventaire'),
    'analyste_faits': ('Analyste des faits', 'legal_analysis', 'A2', 'chronologie'),
    'analyste_procedure': ('Analyste de procédure', 'legal_analysis', 'A3', 'profil_procedural'),
    'chercheur': ('Chercheur juridique', 'legal_analysis', 'A3', 'recherche'),
    'redacteur': ('Rédacteur', 'document_drafting', 'A3', 'section'),
    'controleur': ('Contrôleur', 'control', 'A8', 'rapport_controle'),
    'gestion': ('Assistant de gestion', 'assistant', 'A7', 'diligences'),
}
OUTPUT_TYPES = ('cadrage', 'inventaire', 'extraits', 'echanges', 'agenda', 'fiche_parties', 'profil_procedural', 'recherche', 'chronologie',
                'procedure', 'matrice', 'section', 'bordereau', 'assemblage', 'rapport_controle', 'presentation', 'selection_ecritures',
                'comparaison', 'reponse', 'demandes_maintenues', 'plan', 'diligences', 'reponse_libre')


def _t(code, title, role, task_type, output, depends, instruction, required=True):
    return {'code': code, 'title': title, 'role': role, 'type': task_type, 'output': output, 'depends': list(depends), 'instruction': instruction, 'required': required}


ASSIGNATION = [
    _t('T01', 'Cadrer', 'superviseur', 'cadrage', 'cadrage', [],
       'Cadrer la mission : objectif, prétentions envisagées, délai, juridiction, voie (fond, référé, exécution), représentation, montant, champs manquants essentiels.'),
    _t('T02', 'Inventorier', 'documentaliste', 'inventaire', 'inventaire', ['T01'], 'Inventorier pièces, courriels, agenda et versions ; signaler la couverture à obtenir.'),
    _t('T03', 'Lire les pièces', 'documentaliste', 'lecture_pieces', 'extraits', ['T02'], 'Lire intégralement les pièces déterminantes avec couverture par page ; extraits localisés.'),
    _t('T04', 'Lire les échanges', 'documentaliste', 'lecture_echanges', 'echanges', ['T02'], 'Lire les courriels utiles du dossier ; engagements établis, dates, pièces annoncées.'),
    _t('T05', 'Examiner l’agenda', 'documentaliste', 'agenda', 'agenda', ['T02'], 'Qualifier les événements : dates confirmées par une pièce ou indicatives.'),
    _t('T06', 'Identifier les parties', 'analyste_faits', 'analyse', 'fiche_parties', ['T03', 'T04'],
       'Établir la fiche des parties : rôles, identités, qualité à agir, représentants, coordonnées utiles, avec la source de chaque donnée ; champs manquants listés.'),
    _t('T07', 'Qualifier la procédure', 'analyste_procedure', 'profil', 'profil_procedural', ['T01', 'T03', 'T05', 'T06'],
       'Qualifier juridiction, compétence, saisine, représentation et profil daté d’après le registre ; ambiguïté = décision.'),
    _t('T08', 'Rechercher le droit', 'chercheur', 'recherche', 'recherche', ['T07'],
       'Rechercher textes et décisions applicables via les connecteurs juridiques ; versions, portée, réserves et autorités contraires. Aucune référence non récupérée n’est tenue pour vérifiée.'),
    _t('T09', 'Établir les faits', 'analyste_faits', 'analyse', 'chronologie', ['T03', 'T04', 'T05', 'T06'],
       'Chronologie sourcée : événement, date, source (pièce + page ou courriel), statut (établi, allégué, à discuter) ; contradictions conservées.'),
    _t('T10', 'Établir la procédure', 'analyste_procedure', 'analyse', 'procedure', ['T03', 'T04', 'T05', 'T07'],
       'Actes et décisions antérieurs, notifications, délais ; chaque élément avec sa source.'),
    _t('T11', 'Structurer les demandes', 'analyste_faits', 'analyse', 'matrice', ['T08', 'T09', 'T10'],
       'Matrice prétention → partie concernée, moyen de fait, moyen de droit, preuve, montant et formule de calcul, demande du dispositif.'),
    _t('T12a', 'Rédiger l’en-tête', 'redacteur', 'section', 'section', ['T06', 'T07'],
       'Rédiger l’en-tête et les mentions selon le profil procédural validé ; champs manquants en [À COMPLÉTER : …], jamais inventés (RG, adresse, date de signification).'),
    _t('T12b', 'Rédiger les faits', 'redacteur', 'section', 'section', ['T09'],
       'Rédiger le rappel des faits à partir de la chronologie sourcée ; distinguer positions des parties ; conserver les contradictions utiles.'),
    _t('T12c', 'Rédiger la procédure', 'redacteur', 'section', 'section', ['T10'], 'Rédiger le rappel de la procédure à partir des actes établis.'),
    _t('T12d', 'Rédiger la discussion', 'redacteur', 'section', 'section', ['T08', 'T09', 'T10', 'T11'],
       'Rédiger la discussion par prétention ou argument : faits, texte applicable (références vérifiées seulement), analyse, preuve.'),
    _t('T12e', 'Rédiger le dispositif', 'redacteur', 'section', 'section', ['T11'],
       'Rédiger le dispositif (PAR CES MOTIFS) reprenant exactement les demandes de la matrice, leur ordre principal/subsidiaire et leurs montants.'),
    _t('T13', 'Construire le bordereau', 'documentaliste', 'bordereau', 'bordereau', ['T02', 'T11', 'T12b', 'T12d'],
       'Bordereau des pièces réellement invoquées, numérotation stable, présence vérifiée dans l’inventaire.'),
    _t('T14', 'Assembler et contrôler', 'controleur', 'assemblage', 'assemblage', ['T12a', 'T12b', 'T12c', 'T12d', 'T12e', 'T13'],
       'Assembler le Word selon le modèle du profil et contrôler l’ensemble (assertions, mentions, citations, relecture indépendante par blocs).'),
    _t('T15', 'Corriger et recontrôler', 'redacteur', 'correction', 'assemblage', ['T14'],
       'Correction ciblée des défauts localisés (au plus deux passages automatiques), puis nouveau contrôle.', required=False),
    _t('T16', 'Présenter à l’avocat', 'superviseur', 'presentation', 'presentation', ['T14'],
       'Paquet final : projet Word, bordereau, inventaire, rapport des sources, rapport de contrôle, décisions résiduelles ; validation du contenu demandée.'),
]

CONCLUSIONS = [
    _t('C01', 'Cadrer', 'superviseur', 'cadrage', 'cadrage', [], 'Cadrer : réponse aux dernières conclusions adverses, demandes à maintenir, délai, juridiction, voie, représentation.'),
    _t('C02', 'Inventorier', 'documentaliste', 'inventaire', 'inventaire', ['C01'], 'Inventorier pièces, courriels, agenda ; versions des écritures.'),
    _t('C03', 'Sélectionner les écritures', 'documentaliste', 'selection_ecritures', 'selection_ecritures', ['C02'],
       'Identifier nos dernières conclusions valides et les dernières adverses : auteur, partie, nature, version, date, source de l’information ; un brouillon récent ne remplace pas une version déposée.'),
    _t('C04', 'Lire nos conclusions', 'documentaliste', 'lecture_ecriture', 'extraits', ['C03'], 'Lecture intégrale de nos dernières conclusions (couverture par page).'),
    _t('C05', 'Lire les conclusions adverses', 'documentaliste', 'lecture_ecriture_adverse', 'extraits', ['C03'], 'Lecture intégrale des dernières conclusions adverses (couverture par page).'),
    _t('C06', 'Extraire les moyens adverses', 'analyste_faits', 'analyse', 'matrice', ['C05'],
       'Pour chaque prétention, moyen, pièce et élément nouveau adverse : extrait localisé, position, preuves invoquées ; aucun moyen omis, y compris en dernière page.'),
    _t('C07', 'Comparer aux versions antérieures', 'analyste_faits', 'analyse', 'comparaison', ['C03', 'C05'],
       'Comparer les conclusions adverses avec leur version antérieure si elle existe : demandes modifiées, pièces ajoutées, moyens nouveaux.', required=False),
    _t('C08', 'Qualifier la procédure', 'analyste_procedure', 'profil', 'profil_procedural', ['C01', 'C04'], 'Profil procédural daté applicable aux écritures.'),
    _t('C09', 'Rechercher le droit', 'chercheur', 'recherche', 'recherche', ['C06', 'C08'], 'Textes et décisions utiles à la réponse, autorités contraires comprises.'),
    _t('C10', 'Préparer la réponse point par point', 'analyste_faits', 'analyse', 'reponse', ['C04', 'C06', 'C09'],
       'Pour chaque argument adverse : réponse proposée, preuves favorables et défavorables, droit applicable, choix stratégique ou besoin d’instruction ; argument sans réponse visible.'),
    _t('C11', 'Demandes maintenues', 'analyste_faits', 'analyse', 'demandes_maintenues', ['C04', 'C10'],
       'Prétentions et moyens de nos écritures précédentes à maintenir ; toute prétention antérieure non reprise est signalée avant validation.'),
    _t('C12', 'Rédiger faits et procédure', 'redacteur', 'section', 'section', ['C04', 'C06'], 'Rédiger faits et procédure actualisés.'),
    _t('C13', 'Rédiger la discussion', 'redacteur', 'section', 'section', ['C09', 'C10', 'C11'],
       'Rédiger la discussion : sous-parties par question à trancher, réponse à chaque argument adverse, demandes maintenues ; la dernière demande ne disparaît jamais.'),
    _t('C14', 'Rédiger le dispositif', 'redacteur', 'section', 'section', ['C11', 'C13'], 'Dispositif reprenant toutes les prétentions maintenues et leurs montants.'),
    _t('C15', 'Construire le bordereau', 'documentaliste', 'bordereau', 'bordereau', ['C02', 'C12', 'C13'], 'Bordereau des pièces du cabinet réellement citées ; pièces adverses distinctes.'),
    _t('C16', 'Assembler et contrôler', 'controleur', 'assemblage', 'assemblage', ['C08', 'C12', 'C13', 'C14', 'C15'],
       'Assembler et contrôler : prétentions discussion/dispositif, demandes maintenues vs écritures précédentes, pièces citées vs bordereau, profil applicable.'),
    _t('C17', 'Corriger et recontrôler', 'redacteur', 'correction', 'assemblage', ['C16'], 'Correction ciblée puis nouveau contrôle (deux passages au plus).', required=False),
    _t('C18', 'Présenter à l’avocat', 'superviseur', 'presentation', 'presentation', ['C16'], 'Paquet final et décisions résiduelles.'),
]

PARCOURS = {'assignation': {'label': 'Assignation', 'tasks': ASSIGNATION, 'document_kind': 'assignation'},
            'conclusions': {'label': 'Conclusions en réponse', 'tasks': CONCLUSIONS, 'document_kind': 'conclusions'}}


def template(name):
    from .common import Stop
    if name not in PARCOURS:
        raise Stop('parcours_inconnu')
    return PARCOURS[name]


def detect(text):
    """Parcours suggéré par l'instruction, confirmé par l'avocat avant lancement (jamais lancé sur un seul mot)."""
    import re
    from .common import fold
    f = fold(text or '')
    if re.search(r'\b(prepare|preparer|redige|rediger|redaction|etablis|etablir)\w*\s+(une |l |la |d |de |mon |notre )?(projet d |projet de )?assignation\b', f) or 'assigner ' in f or 'requete introductive' in f:
        return 'assignation'
    # conclusions : le verbe porte directement sur les conclusions (« réponds aux dernières conclusions », « rédige des conclusions en réponse »),
    # pas une autre production qui les cite (« trame de plaidoirie à partir des conclusions »)
    if re.search(r'\b(reponds|repondre|repond|replique|repliquer|prepare|preparer|redige|rediger|redaction)\w*\s+(aux |a |des |les |nos |mes |de |d |nouvelles |dernieres )*(dernieres |nouvelles )?conclusions\b', f) or 'conclusions en reponse' in f:
        return 'conclusions'
    return ''
