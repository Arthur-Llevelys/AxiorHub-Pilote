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
    # 5.6.75
    'connecteur_claude_reserve_au_serveur': 'Le connecteur Claude fonctionne avec la version serveur d’AxiorHub publiée en HTTPS.',
    'connexion_claude_introuvable': 'Connexion Claude introuvable (déjà retirée ?).',
    'pseudonymisation_imposee_par_le_cabinet': 'La pseudonymisation est imposée par le cabinet pour toutes les connexions Claude.',
    'preparation_claude_desactivee': 'Le cabinet a réglé le connecteur Claude en lecture seule : ni brouillon ni note.',
    'dossier_introuvable_ou_non_accessible': 'Dossier introuvable, ou non accessible à ce compte.',
    'document_introuvable_ou_non_accessible': 'Document introuvable, non indexé, ou non accessible à ce compte.',
    'document_confidentiel_non_transmis': 'Correspondance confidentielle entre avocats : son contenu n’est jamais transmis à Claude.',
    'extrait_absent_du_document': 'L’extrait cité ne figure pas mot pour mot dans le document : recopiez-le du texte lu.',
    'extrait_exact_requis_20_a_1200_caracteres': 'Extrait exact du document requis (20 à 1 200 caractères).',
    'source_hors_dossier': 'Le document source n’appartient pas à ce dossier.',
    'courriel_source_introuvable': 'Courriel d’origine introuvable dans les courriels indexés du cabinet.',
    'titre_et_texte_requis': 'Titre et texte requis.',
    'dossier_requis': 'Dossier requis.',
    # 5.6.74
    'association_reservee_au_serveur': 'L’association d’un téléphone se fait sur la version serveur d’AxiorHub (accessible en HTTPS).',
    'compte_requis': 'Connectez-vous avec votre compte pour associer un téléphone.',
    'dix_appareils_maximum': 'Dix appareils au plus par compte : retirez-en un avant d’en associer un autre.',
    'appareil_introuvable': 'Appareil introuvable (déjà retiré ?).',
    'adresse_publique_inconnue': 'Adresse publique du serveur inconnue : ouvrez AxiorHub par son adresse https habituelle, puis réessayez.',
    # 5.6.73
    'navigateur_reserve_au_poste': 'Le navigateur supervisé fonctionne dans l’application AxiorHub Pilote pour Windows ou Ubuntu (poste de l’avocat).',
    'navigateur_absent_du_poste': 'Aucun navigateur compatible sur ce poste : installez Microsoft Edge, Google Chrome ou Chromium.',
    'navigateur_demarrage_impossible': 'Le navigateur n’a pas pu démarrer : fermez les fenêtres du navigateur d’AxiorHub restées ouvertes, puis réessayez.',
    'navigateur_ferme': 'La fenêtre du navigateur a été fermée : relancez la tâche.',
    'navigateur_delai_depasse': 'Le navigateur ne répond plus : arrêtez la séance puis relancez-la.',
    'une_seance_est_deja_en_cours': 'Une séance du navigateur est déjà en cours : arrêtez-la ou attendez sa fin.',
    'aucune_decision_attendue': 'Aucune décision n’est attendue pour cette séance.',
    'aucune_seance': 'Aucune séance du navigateur en cours.',
    'tache_trop_courte': 'Décrivez la tâche en quelques mots.',
    'tache_trop_longue': 'Tâche trop longue (2 000 caractères au plus).',
    'site_invalide': 'Site invalide : indiquez un nom de domaine public, par exemple infogreffe.fr.',
    'repere_absent_de_la_page': 'Le contrôle visé n’est plus sur la page.',
    'option_introuvable': 'Option introuvable dans la liste.',
    'arrete_par_l_avocat': 'Arrêté par l’avocat.',
    'delai_de_decision_depasse': 'Aucune décision en 30 minutes : séance arrêtée.',
    'reponse_ia_invalide': 'Réponse du modèle inexploitable deux fois de suite : séance arrêtée.',
    # 5.6.72
    'moteur_ollama_non_configure': 'Aucun modèle local (Ollama) n’est configuré : voir Paramètres › IA.',
    # 5.6.71
    'projet_prudent_absent': 'Cette réponse préparée n’existe plus (déjà déposée ou écartée) : rechargez la page.',
    'controle_de_surete_non_satisfait': 'Le contrôle de sûreté n’autorise pas le dépôt tel quel : relisez et modifiez le texte, puis déposez-le.',
    'courriel_modifie_depuis_projet': 'Le courriel a changé depuis la préparation de la réponse : demandez une nouvelle réponse.',
    'dossier_modifie_depuis_projet': 'Le répertoire du dossier a changé depuis la préparation de la réponse : demandez une nouvelle réponse.',
    'depot_bloque_decision_requise': 'Votre décision est nécessaire : relisez le texte puis « Déposer ce texte dans Brouillons ».',
    'brouillon_deja_depose_ou_incertain': 'Un brouillon a déjà été déposé pour ce courriel (ou son dépôt est en cours de vérification).',
    'texte_vide': 'Le texte de la réponse est vide.',
    'texte_trop_long': 'Texte trop long (20 000 caractères au plus).',
    'decision_requise': 'Choisissez : déposer le texte relu, ou tel quel « à compléter ».',
    # 5.6.70
    'courriel_introuvable': 'Courriel introuvable dans la messagerie (déplacé ou supprimé ?) : ouvrez-le depuis la Boîte de réception.',
    'aucun_courriel_selectionne': 'Cochez au moins un courriel.',
    'vingt_cinq_courriels_maximum': '25 courriels au plus par lot.',
    'aucune_plateforme_de_facturation_verifiee': 'Aucune plateforme de facturation électronique vérifiée : configurez SuperPDP ou B2Brouter dans '
                                                 'Paramètres › Connexions et activez « Recevoir les factures ».',
    'facture_recue_absente': 'Facture reçue introuvable : actualisez la liste.',
    'fichier_facture_indisponible': 'La plateforme n’a pas fourni le fichier de cette facture.',
    'periode_invalide': 'Période invalide : la date de fin doit suivre la date de début (400 jours au plus).',
    'dossier_hors_racines_nextcloud': 'Ce dossier est hors de l’espace de fichiers du cabinet.',
    'chemin_hors_espace_du_cabinet': 'Ce document est hors de l’espace de fichiers du cabinet.',
    'recherche_trop_courte': 'Tapez au moins deux caractères.',
    'adresse_visio_https_requise': 'Indiquez l’adresse HTTPS du serveur Jitsi (https://…).',
    'adresse_visio_sans_chemin': 'Indiquez seulement l’adresse du serveur Jitsi, sans chemin.',
    'visio_non_configuree': 'Visioconférence non configurée : Paramètres › Courriels et accueil › Visioconférence.',
    'serveur_visio_injoignable': 'Le serveur Jitsi ne répond pas : vérifiez son adresse et son installation.',
    'adresse_ip_invalide': 'Adresse IP invalide.',
    'dix_pieces_jointes_maximum': '10 pièces jointes au plus.',
    'pieces_jointes_trop_lourdes_15_mo_maximum': 'Pièces jointes trop lourdes : 15 Mo au plus au total.',
}


def short(code, limit=160):
    """Motif lisible d'un code (ou de l'exception qui le porte), à défaut le code sans soulignés."""
    from .web440 import human
    raw = str(code or '').split(':')[0].strip()
    text = MESSAGES.get(raw) or human(raw)
    return (text or raw.replace('_', ' '))[:limit]
