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

# 5.6.51 (audit 5.6.50, A01 et A07) : préparation d'audience et livrable libre, avec les mêmes types de tâches (aucun moteur parallèle).
AUDIENCE = [
    _t('H01', 'Cadrer', 'superviseur', 'cadrage', 'cadrage', [], 'Cadrer la préparation : audience visée, juridiction, enjeux, ce que l’avocat doit pouvoir dire.'),
    _t('H02', 'Inventorier', 'documentaliste', 'inventaire', 'inventaire', ['H01'], 'Inventorier écritures, pièces, courriels et agenda ; versions des écritures.'),
    _t('H03', 'Lire les pièces', 'documentaliste', 'lecture_pieces', 'extraits', ['H02'], 'Lire les écritures et pièces déterminantes ; extraits localisés.'),
    _t('H04', 'Lire les échanges', 'documentaliste', 'lecture_echanges', 'echanges', ['H02'], 'Courriels utiles : engagements, pièces annoncées, demandes du juge.'),
    _t('H05', 'Examiner l’agenda', 'documentaliste', 'agenda', 'agenda', ['H02'], 'Dates confirmées ou indicatives ; audience et calendrier de procédure.'),
    _t('H06', 'Chronologie utile', 'analyste_faits', 'analyse', 'chronologie', ['H03', 'H04', 'H05'],
       'Chronologie utile à l’audience : événement, date et précision, auteur, source ; faits sans date à part.'),
    _t('H07', 'Prétentions en présence', 'analyste_faits', 'analyse', 'matrice', ['H03'],
       'Prétentions de chaque partie, moyens, montants et pièces invoquées ; points contestés.'),
    _t('H08', 'Pièces clés et pièces manquantes', 'gestion', 'analyse', 'diligences', ['H02', 'H07'],
       'Pièces clés à avoir à l’audience ; pièces attendues non reçues avec un brouillon de demande (rien n’est envoyé).'),
    _t('H09', 'Rechercher le droit', 'chercheur', 'recherche', 'recherche', ['H07'], 'Textes et décisions utiles, autorités contraires comprises ; références vérifiées seulement.'),
    _t('H10', 'Rédiger la synthèse', 'redacteur', 'section', 'section', ['H06', 'H07'], 'Synthèse actualisée du dossier pour l’audience.'),
    _t('H11', 'Questions et note de plaidoirie', 'redacteur', 'section', 'section', ['H07', 'H08', 'H09'],
       'Questions à anticiper et projet de note de plaidoirie, attribuant chaque affirmation à son auteur.'),
    _t('H12', 'Assembler et contrôler', 'controleur', 'assemblage', 'assemblage', ['H10', 'H11'], 'Assembler le dossier d’audience (Word) et le contrôler.'),
    _t('H13', 'Présenter à l’avocat', 'superviseur', 'presentation', 'presentation', ['H12'], 'Paquet final et décisions résiduelles.'),
]

LIVRABLE = [
    _t('L01', 'Cadrer', 'superviseur', 'cadrage', 'cadrage', [], 'Cadrer le résultat attendu, son destinataire et ses limites.'),
    _t('L02', 'Inventorier', 'documentaliste', 'inventaire', 'inventaire', ['L01'], 'Inventorier les sources utiles du dossier.'),
    _t('L03', 'Lire les pièces', 'documentaliste', 'lecture_pieces', 'extraits', ['L02'], 'Lire les pièces déterminantes ; extraits localisés.'),
    _t('L04', 'Lire les échanges', 'documentaliste', 'lecture_echanges', 'echanges', ['L02'], 'Lire les courriels utiles.'),
    _t('L05', 'Établir les faits et les changements', 'analyste_faits', 'analyse', 'chronologie', ['L03', 'L04'],
       'Faits et prétentions utiles, avec auteur et source ; ce qui a changé récemment.'),
    _t('L06', 'Rechercher le droit', 'chercheur', 'recherche', 'recherche', ['L05'], 'Droit utile ; références vérifiées seulement.', required=False),
    _t('L07', 'Rédiger', 'redacteur', 'section', 'section', ['L05', 'L06'], 'Rédiger le livrable demandé.'),
    _t('L08', 'Assembler et contrôler', 'controleur', 'assemblage', 'assemblage', ['L07'], 'Assembler le Word et le contrôler.'),
    _t('L09', 'Présenter à l’avocat', 'superviseur', 'presentation', 'presentation', ['L08'], 'Paquet final et décisions résiduelles.'),
]

PARCOURS = {'assignation': {'label': 'Assignation', 'tasks': ASSIGNATION, 'document_kind': 'assignation'},
            'conclusions': {'label': 'Conclusions en réponse', 'tasks': CONCLUSIONS, 'document_kind': 'conclusions'},
            'audience': {'label': 'Préparation d’audience', 'tasks': AUDIENCE, 'document_kind': 'note_audience'},
            'livrable': {'label': 'Livrable', 'tasks': LIVRABLE, 'document_kind': 'autre'}}


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
