"""5.6.68 : motifs lisibles pour les codes encore affichés tels quels (« budget mensuel fournisseur depasse »…) dans « Erreurs des 7 derniers
jours », les routines et les cartes « Interrompus »."""

MESSAGES = {
    'budget_mensuel_fournisseur_depasse': 'Plafond mensuel du fournisseur d’IA externe atteint : réglez-le dans Paramètres › IA. Depuis 5.6.68, '
                                          'le modèle local prend le relais quand il est installé.',
    'budget_requete_fournisseur_depasse': 'Demande plus coûteuse que le plafond par requête du fournisseur d’IA externe (Paramètres › IA).',
    'budget_fournisseur_non_configure': 'Plafonds du fournisseur d’IA externe non renseignés (Paramètres › IA).',
    'tarif_fournisseur_non_renseigne': 'Tarifs du fournisseur d’IA externe non renseignés (Paramètres › IA).',
    'politique_externe_contexte_trop_long': 'Demande trop volumineuse pour être confiée à l’IA externe (taille maximale réglée dans « IA externe sûre »).',
    'politique_externe_dossier_exclu': 'Dossier réservé au modèle local : l’IA externe n’est pas utilisée pour lui.',
    'politique_externe_contexte_non_identifie': 'Dossier non identifié : l’IA externe n’est pas utilisée tant que le dossier n’est pas établi.',
    'ocr_qualite_insuffisante': 'Scan de qualité insuffisante : la lecture automatique est trop incertaine (rescannez la pièce ou joignez sa version texte).',
    'ocr_sans_texte_reconnu': 'Aucun texte reconnu dans le scan ou l’image.',
    'document_sans_texte_exploitable': 'Document sans texte exploitable.',
    'plusieurs_dernieres_conclusions_partie': 'Plusieurs « dernières conclusions » possibles pour une même partie : désignez celles à retenir.',
    'dossier_parent_contient_plusieurs_affaires': 'Ce dossier regroupe plusieurs affaires : choisissez l’affaire concernée.',
    'suspension_sans_pause_en_vigueur': 'Arrêté : le travail se suspendait pour un dossier en pause rencontré en chemin. Relancez-le après contrôle.',
    'relances_suspendues_apres_trois_echecs': 'Relances suspendues après trois échecs : corrigez la cause puis relancez.',
    'agent_en_pause_sur_ce_dossier': 'L’agent est en pause sur ce dossier.',
    'file_attente_pleine': 'File de travail pleine : la suite reprendra quand elle se videra.',
    'contexte_trop_long': 'Demande trop longue pour le modèle : réduisez le nombre de pièces ou découpez la demande.',
    'contexte_ia_insuffisant': 'Le dossier dépasse la fenêtre du modèle : le travail se poursuit par étapes.',
    'generation_ia_incomplete': 'Réponse du modèle coupée avant la fin : relancez.',
    'reponse_ia_vide': 'Le modèle n’a rien répondu : relancez.',
    'fonction_ia_invalide': 'Fonction d’IA inconnue.',
}


def short(code, limit=160):
    """Motif lisible d'un code (ou de l'exception qui le porte), à défaut le code sans soulignés."""
    from .web440 import human
    raw = str(code or '').split(':')[0].strip()
    text = MESSAGES.get(raw) or human(raw)
    return (text or raw.replace('_', ' '))[:limit]
