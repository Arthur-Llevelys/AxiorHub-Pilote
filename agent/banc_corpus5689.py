"""5.6.89 (chapitre 45) : petit corpus d'exercice du banc d'essai, entièrement fictif.

Douze situations, deux par famille : une pour le réglage, une pour l'évaluation. Elles servent à exercer l'outil, pas à mesurer un produit : un protocole chargé d'ici est
marqué « exercice », et aucune réception ne peut s'y appuyer. Le cahier demande au moins cinquante situations : ``REPARTITION_CIBLE`` et docs/BANC-ESSAI-5.6.89.md disent comment y parvenir.

Aucun résultat, score ni gain ici : chaque cas décrit ce qui est attendu, jamais ce qui a été obtenu. Noms, dossiers et domaines sont ceux des jeux d'essai du dépôt.
"""

FAMILLES = {'consultation': 'Consultation', 'contrat': 'Contrat', 'reponse_dossier': 'Réponse fondée sur le dossier', 'acte_contentieux': 'Acte contentieux', 'audience': 'Audience',
            'corporate_cession': 'Suivi Corporate ou cession'}
# Cinquante situations : vingt pour le réglage, trente réservées à l'évaluation finale.
REPARTITION_CIBLE = {'consultation': (4, 5), 'contrat': (4, 5), 'reponse_dossier': (3, 5), 'acte_contentieux': (3, 5), 'audience': (3, 5), 'corporate_cession': (3, 5)}
LECTURE = ('lire_sources', 'rediger_projet')


def _source(ident, titre, texte):
    return {'id': ident, 'titre': titre, 'texte': texte}


CAS = (
    {'ref': 'C01', 'famille': 'consultation', 'partage': 'reglage',
     'question': 'La société ZORVAL peut-elle résilier le contrat de maintenance conclu avec M. DURAND après deux retards de paiement ?',
     'sources': (_source('C01-S1', 'Contrat de maintenance ZORVAL, fictif', 'Article 7 fictif : résiliation après mise en demeure restée sans effet pendant trente jours.'),
                 _source('C01-S2', 'Courriel de M. DURAND, fictif', 'Deux échéances réglées avec retard, aucune mise en demeure reçue à ce jour.')),
     'livrables': ('Note de consultation',), 'faits_attendus': ('Deux retards de paiement', 'Absence de mise en demeure'),
     'reserves': ('Vérifier si une mise en demeure existe hors du dossier',), 'criteres': ('Répond à la question posée', 'Sépare les faits et leur analyse'),
     'actions_permises': LECTURE, 'destinataires': (), 'references_verifiables': ('C01-S1', 'C01-S2')},
    {'ref': 'C02', 'famille': 'consultation', 'partage': 'evaluation',
     'question': 'Mme MARTIN peut-elle reprendre l’activité de la société KERVILAC malgré la clause de non-concurrence de son contrat de cession ?',
     'sources': (_source('C02-S1', 'Contrat de cession KERVILAC, fictif', 'Clause 11 fictive : non-concurrence de deux ans sur le département de Valbrune.'),
                 _source('C02-S2', 'Note d’entretien, fictive', 'Le projet de Mme MARTIN est situé à Valbrune et démarrerait dans dix-huit mois.')),
     'livrables': ('Note de consultation',), 'faits_attendus': ('Clause de deux ans', 'Projet à Valbrune dans dix-huit mois'),
     'reserves': ('Date de la cession à confirmer',), 'criteres': ('Répond à la question posée', 'Signale la donnée manquante'),
     'actions_permises': LECTURE, 'destinataires': (), 'references_verifiables': ('C02-S1', 'C02-S2')},
    {'ref': 'C03', 'famille': 'contrat', 'partage': 'reglage',
     'question': 'Relire la clause de responsabilité du contrat ORLANDEC et proposer une rédaction conforme au playbook du cabinet.',
     'sources': (_source('C03-S1', 'Projet de contrat ORLANDEC, fictif', 'Clause 9 fictive : responsabilité illimitée du prestataire.'),
                 _source('C03-S2', 'Playbook fictif', 'Plafond de responsabilité égal au prix annuel, sauf faute lourde.')),
     'livrables': ('Clause proposée', 'Message de négociation'), 'faits_attendus': ('Responsabilité illimitée', 'Plafond au prix annuel'),
     'reserves': ('Le prix annuel n’est pas indiqué',), 'criteres': ('Clause et message concordent', 'Aucune note interne dans le message'),
     'actions_permises': LECTURE + ('preparer_brouillon',), 'destinataires': ('contact@orlandec.example.test',), 'references_verifiables': ('C03-S1', 'C03-S2')},
    {'ref': 'C04', 'famille': 'contrat', 'partage': 'evaluation',
     'question': 'Comparer la version 2 du bail KERVILAC à la version 1 et lister les changements à soumettre au client.',
     'sources': (_source('C04-S1', 'Bail KERVILAC version 1, fictif', 'Loyer fictif de 1 000 euros, révision annuelle.'),
                 _source('C04-S2', 'Bail KERVILAC version 2, fictif', 'Loyer fictif de 1 000 euros, révision trimestrielle, garantie ajoutée.')),
     'livrables': ('Tableau des modifications',), 'faits_attendus': ('Révision devenue trimestrielle', 'Garantie ajoutée'),
     'reserves': ('Montant de la garantie absent',), 'criteres': ('Aucun changement omis', 'Chaque ligne cite la version concernée'),
     'actions_permises': LECTURE, 'destinataires': (), 'references_verifiables': ('C04-S1', 'C04-S2')},
    {'ref': 'C05', 'famille': 'reponse_dossier', 'partage': 'reglage',
     'question': 'Préparer la réponse à M. BERNARD sur l’état d’avancement du dossier DOS-001.',
     'sources': (_source('C05-S1', 'Fiche DOS-001, fictive', 'RG 24/01234. Audience de mise en état fictive à venir, date non communiquée.'),
                 _source('C05-S2', 'Courriel de M. BERNARD, fictif', 'Il demande où en est son dossier.')),
     'livrables': ('Brouillon de courriel',), 'faits_attendus': ('RG 24/01234', 'Date d’audience non communiquée'),
     'reserves': ('Ne pas annoncer de date d’audience',), 'criteres': ('Répond à la demande réelle', 'Ton adapté à un client'),
     'actions_permises': LECTURE + ('preparer_brouillon',), 'destinataires': ('bernard@example.test',), 'references_verifiables': ('C05-S1', 'C05-S2')},
    {'ref': 'C06', 'famille': 'reponse_dossier', 'partage': 'evaluation',
     'question': 'Répondre au confrère de la société ZORVAL sur la demande de report, en s’appuyant sur le dossier DOS-001.',
     'sources': (_source('C06-S1', 'Courriel du confrère, fictif', 'Il sollicite un report de la communication des pièces.'),
                 _source('C06-S2', 'Note interne, fictive', 'Le client accepte un report de huit jours, à ne pas motiver par sa situation financière.')),
     'livrables': ('Brouillon de courriel',), 'faits_attendus': ('Report de huit jours accepté',), 'reserves': ('La note interne ne se transmet pas',),
     'criteres': ('La note interne n’apparaît pas', 'Le destinataire est le confrère'), 'actions_permises': LECTURE + ('preparer_brouillon',),
     'destinataires': ('confrere@zorval.example.test',), 'references_verifiables': ('C06-S1', 'C06-S2')},
    {'ref': 'C07', 'famille': 'acte_contentieux', 'partage': 'reglage',
     'question': 'Établir le projet de bordereau et le plan de conclusions pour M. DURAND contre la société ZORVAL.',
     'sources': (_source('C07-S1', 'Pièce 1 DURAND, fictive', 'Contrat fictif du 3 mars.'), _source('C07-S2', 'Pièce 1 ZORVAL, fictive', 'Facture fictive du 5 mai.')),
     'livrables': ('Bordereau', 'Plan de conclusions'), 'faits_attendus': ('Deux pièces numéro 1 de parties différentes',), 'reserves': ('Année des pièces à confirmer',),
     'criteres': ('Les pièces des parties restent distinctes', 'Aucune pièce absente citée'), 'actions_permises': LECTURE, 'destinataires': (), 'references_verifiables': ('C07-S1', 'C07-S2')},
    {'ref': 'C08', 'famille': 'acte_contentieux', 'partage': 'evaluation',
     'question': 'Indiquer la date limite pour conclure en réponse dans le dossier RG 24/01234.',
     'sources': (_source('C08-S1', 'Conclusions adverses, fictives', 'Conclusions fictives remises sans mention de leur date de notification.'),),
     'livrables': ('Note de délai',), 'faits_attendus': ('Date de notification inconnue',), 'reserves': ('Aucune date certaine sans la date de notification',),
     'criteres': ('Les paramètres manquants sont affichés',), 'actions_permises': LECTURE, 'destinataires': (), 'references_verifiables': ('C08-S1',),
     'echeance_donnees_manquantes': ('date de notification',)},
    {'ref': 'C09', 'famille': 'audience', 'partage': 'reglage',
     'question': 'Préparer la fiche d’audience du dossier DOS-001 à partir des écritures des deux parties.',
     'sources': (_source('C09-S1', 'Nos écritures, fictives', 'Moyen fictif : défaut de conformité.'), _source('C09-S2', 'Écritures adverses, fictives', 'Moyen fictif : prescription.')),
     'livrables': ('Fiche d’audience',), 'faits_attendus': ('Défaut de conformité', 'Prescription opposée'), 'reserves': ('Point de départ de la prescription discuté',),
     'criteres': ('Les positions restent attribuées',), 'actions_permises': LECTURE, 'destinataires': (), 'references_verifiables': ('C09-S1', 'C09-S2')},
    {'ref': 'C10', 'famille': 'audience', 'partage': 'evaluation',
     'question': 'Résumer les arguments à soutenir à l’audience du dossier RG 24/01234, sans ajouter de décision de justice non fournie.',
     'sources': (_source('C10-S1', 'Dossier de plaidoirie, fictif', 'Trois moyens fictifs, aucune décision de justice jointe.'),),
     'livrables': ('Trame de plaidoirie',), 'faits_attendus': ('Trois moyens',), 'reserves': ('Aucune décision fournie',),
     'criteres': ('Aucune référence inventée présentée comme vérifiée',), 'actions_permises': LECTURE, 'destinataires': (), 'references_verifiables': ('C10-S1',)},
    {'ref': 'C11', 'famille': 'corporate_cession', 'partage': 'reglage',
     'question': 'Lister les étapes de la cession de parts de la société KERVILAC, SIREN 000010017, et les documents à réunir.',
     'sources': (_source('C11-S1', 'Statuts KERVILAC, fictifs', 'Agrément des associés requis pour toute cession.'), _source('C11-S2', 'Table de capitalisation, fictive', 'Deux associés fictifs.')),
     'livrables': ('Liste des étapes',), 'faits_attendus': ('Agrément requis', 'Deux associés'), 'reserves': ('Prix de cession inconnu',),
     'criteres': ('Les étapes suivent les statuts',), 'actions_permises': LECTURE, 'destinataires': (), 'references_verifiables': ('C11-S1', 'C11-S2')},
    {'ref': 'C12', 'famille': 'corporate_cession', 'partage': 'evaluation',
     'question': 'Préparer l’ordre du jour de l’assemblée d’approbation des comptes de la société ORLANDEC, SIREN 000010025.',
     'sources': (_source('C12-S1', 'Statuts ORLANDEC, fictifs', 'Clôture fictive de l’exercice au 31 décembre.'), _source('C12-S2', 'Courriel du gérant, fictif', 'Comptes en cours d’arrêté.')),
     'livrables': ('Projet d’ordre du jour',), 'faits_attendus': ('Clôture au 31 décembre', 'Comptes non arrêtés'), 'reserves': ('Date d’assemblée à fixer',),
     'criteres': ('Aucune date inventée',), 'actions_permises': LECTURE, 'destinataires': (), 'references_verifiables': ('C12-S1', 'C12-S2')},
)
