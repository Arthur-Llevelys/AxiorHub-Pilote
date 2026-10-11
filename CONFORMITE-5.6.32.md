# Conformité 5.6.32 — cahier des charges additionnel « Guide d’installation et veille » (deuxième livraison)

Version révisée du cahier des charges : 10 octobre 2026. La 5.6.31 a livré le socle (R00, R02, R17 mécanisme, R18, R19 ; matrice
`CONFORMITE-5.6.31.md`). La 5.6.32 livre la deuxième partie annoncée : agendas, connecteurs juridiques, modèles, éditeur, facturation et
téléphonie dans le guide, veille planifiée avec Tavily, contrôle des sources dans les écrits, traduction des écrans métier, documents de
procédure → agenda → brouillons, compétences ajoutées par l’utilisateur, README en anglais, allemand et espagnol. Elle intègre aussi les
corrections et demandes de l’avocat reçues pendant la réalisation (fin de document).

Légende : **Réalisé** — livré et testé ; **Partiel** — livré avec la limite indiquée ; **Lot suivant** — non commencé.

## Fiches de réalisation

| Fiche | Objet | État 5.6.32 |
|---|---|---|
| R00 | Prérequis et premier démarrage | Réalisé (5.6.31). |
| R01 | Écrans du parcours guidé | Réalisé : les étapes agendas, facturation et téléphonie ont désormais leurs champs et leur vérification ; sources juridiques et veille affichent les cartes de réglage réelles ; l’étape modèles renvoie à l’écran des modèles Word, qui porte l’aide aux balises. |
| R02 | Réglages, secrets et preuves | Réalisé (5.6.31) ; nouvelles capacités prouvées : `calendar.read`, `invoice.read`, `phone.signature`. |
| R03 | Stockage local et WebDAV | Partiel (inchangé) : écriture de test WebDAV générique : lot suivant. |
| R04 | Choisir et tester les IA | Partiel (inchangé) ; garde de contexte Ollama ajoutée (voir « Examen du dossier entier »). |
| R05 | Réception, brouillons, envoi | Partiel (inchangé) ; réglages de messagerie ajoutés (signature, police, règles). |
| R06 | Google Agenda et CalDAV | Réalisé pour le guide : agendas CalDAV autorisés (une adresse par ligne), listes de tâches, client OAuth Google (avancé) ; la vérification liste les agendas du compte et refuse un agenda indiqué introuvable, sans créer d’événement. L’autorisation Google se donne depuis Agenda et tâches (existant). |
| R07 | Quatre connecteurs juridiques | Réalisé : JusticeLibre (accès public sans clé, MCP Streamable HTTP, vérifié réellement : connexion, recherche, lecture d’un texte), OpenLegi, GoodLegal, LibreJustice et Legal Data Hunter (jeton Bearer) ; une carte par fournisseur (compte, clé, vérification à niveaux séparés, activation après accord explicite sur les données externes). Les quatre services à jeton n’ont pas été testés avec un compte réel (aucun compte du cabinet) : la vérification affichera leur état réel lors de la configuration. |
| R08 | Modèles Word | Réalisé : aide aux balises (rôle, obligatoire, exemple fictif, règles du contrôle d’import) et modèle d’exemple fictif à télécharger, construit à la volée et validé par le même contrôle que l’import. Schémas par type d’acte : écran « Modèles d’actes » existant (champs requis par acte). |
| R09 | ONLYOFFICE / Euro-Office | Partiel : diagnostic et réglage existants (Documents du cabinet) ; étape dédiée du guide : lot suivant. |
| R10 | Invoice Ninja | Réalisé pour le guide : activation, adresse, jeton, société ; vérification en lecture seule (aucune facture, aucun paiement, aucune relance). |
| R11 | Téléphonie | Réalisé pour le guide : adresse de rappel Twilio et Auth Token ; vérification locale (format de l’adresse, jeton lisible, calcul de signature identique au webhook) sans appel. |
| R12 | Veille planifiée et cadre Aujourd’hui | Réalisé : profil par utilisateur (thèmes, exclusions, flux officiels CNIL, Justice, EUR-Lex, flux HTTPS personnalisés, source juridique, Tavily, fréquence quotidienne / certains jours / hebdomadaire / à la demande, heure, fuseau) ; un seul travail par utilisateur et par créneau (rattrapage unique après un arrêt), bail de 15 minutes et trois tentatives ; collecte partielle signalée par source ; dédoublonnage par adresse canonique et contenu ; cadre « Veille juridique » dans les deux vues d’Aujourd’hui, page de lecture (Lu, Conserver, Masquer). |
| R13 | Tavily | Réalisé : clé dans le coffre (administrateur), budget mensuel et par passage réservé avant l’appel, résultat incertain compté, erreurs explicites. |
| R14 | Sources des écrits | Réalisé : « Vérifier » relève articles et codes, lois, pourvois, décisions du Conseil d’État, ECLI et citations entre guillemets, puis les contrôle auprès de la source activée à la date utile ; états piste / identifiée / contrôlée / à vérifier / anomalie, avec le paragraphe et la source. Sans source activée, toute référence reste une piste. |
| R15 | Document → agenda → brouillons | Réalisé : panneau de la page Dossier (vue Pièces) ; lecture de tous les documents choisis, dates sourcées (citation, page), avertissements (OCR, délai calculé) ; décision par date (inscrire, ignorer, à préciser) et choix des agendas autorisés ; inscription cible par cible sans doublon ; brouillons d’information client et confrère déposés dans Brouillons et relus (sinon conservés localement). Rien n’est envoyé. |
| R16 | Compétences | Réalisé : page « Compétences de l’IA » ; trois compétences intégrées (assistant juridique de Christophe Quézel-Ambrunaz, AGPL-3.0, provenance et adaptation documentées ; approbation des comptes ; politique de confidentialité RGPD, créations AxiorHub) ; création par formulaire, import SKILL.md ou .zip (textes seulement, scripts et exécutables refusés), copie pour adapter, versions, activation, export ; choix automatique (deux au plus) ou manuel ; prérequis manquants affichés. |
| R17 | Traduction | Réalisé pour les écrans métier, avec une limite : les pages rendues par le serveur sont traduites à la sortie depuis un dictionnaire de 2 799 phrases relevées sur 79 écrans réels (anglais, allemand, espagnol ; nombres variables ; textes, attributs visibles, notifications). Les données (noms, objets de courriels, contenu des dossiers) ne sont jamais traduites ; une phrase absente du dictionnaire reste en français (jamais de traduction approximative) ; les textes produits par les scripts du navigateur restent en partie en français. |
| R18 | Menu unique | Réalisé (5.6.31) ; fonction « Compétences de l’IA » ajoutée au registre avec sa fiche en quatre langues. |
| R19 | Guide d’utilisation | Réalisé ; fiches « Veille juridique » (refaite) et « Compétences de l’IA » (nouvelle) en quatre langues. |
| R20 | Migrer, vérifier, livrer | Réalisé : tables créées de façon réexécutable, réglages existants conservés, installable depuis 5.6.6, suite complète. |

## README

`README.md` (français, par défaut) mis à jour ; `README.en.md`, `README.de.md`, `README.es.md` ajoutés et reliés depuis chaque version.
Les guides détaillés de `docs/` restent en français (indiqué dans les versions traduites).

## Corrections et demandes de l’avocat intégrées

| Demande | Réalisation |
|---|---|
| L’interface se bloque (Paramètres → Aujourd’hui, « Interface indisponible », retour après rechargement) | Cause : à chaque affichage, l’ouverture de la base réécrivait des données par défaut sous le verrou du schéma ; une écriture concurrente (travail de fond) faisait attendre puis échouer la page. Correctifs : schéma préparé une fois par processus, aucune écriture à l’ouverture, délai d’attente court pour l’affichage, écritures facultatives sautées si la base est occupée, réponse 503 « Un instant… » avec rechargement automatique au lieu de « Interface indisponible », journal `erreurs-interface.log`, transaction validée avant tout appel réseau (correctif complet en 5.6.32 : la surveillance des connexions ne fonctionnait pas). |
| « La génération a atteint sa limite… » ; examiner un dossier entier | Fiche par pièce (faits datés, demandes, montants, références, avec la page) préparée en fond ; l’analyse du dossier utilise toutes les pièces au niveau de détail que permet la mémoire de l’IA (complet, résumé, une ligne), couverture affichée ; garde de contexte Ollama avant l’appel ; une réponse coupée par la longueur demandée est relancée une fois plus longue. |
| Tests métier continus trop fréquents | Une fois par jour au lieu de chaque heure ; vérification de la chaîne d’audit incrémentale (relecture complète une fois par semaine). Ils n’appellent pas l’IA. |
| Erreur 412 en supprimant une tâche présente trois fois | Version (ETag) mise à jour après chaque écriture ; en cas de 412, la tâche est relue et l’opération n’est refaite que si nécessaire ; toutes les copies locales changent d’état ; tâches de relecture créées avec un identifiant déterminé par le document (plus de doublon). |
| Habitudes à valider peu utiles | La longueur devient un repère automatique (non proposé) ; un plan n’est proposé qu’avec au moins trois vraies parties (POUR, CONTRE… ignorés) ; nouvelles habitudes : numérotation des parties, présentation des décisions citées, intitulé du dispositif ; chaque proposition dit ce qu’elle changera. |
| Dossiers de travail (NOS PIECES, PIECES ADVERSES - {{partie_adverse01}}…) | Réglables dans Mon style › Réglages (ajout, retrait, usage) ; variables complétées avec les parties adverses de chaque dossier ; reconnaissance d’un dossier nommé autrement après le tiret ; provenance donnée à l’IA ; jamais utilisés pour apprendre le style ; création des dossiers absents depuis la page du dossier (clic) ; destination possible des agents documentaires. |
| Briefing du matin en version orale, à écouter | « Résumé oral » sous le briefing : rédaction par l’IA contrôlée (chaque nombre doit figurer dans le briefing, sinon version par règles), « Écouter » par la synthèse vocale du cabinet (Kokoro, Chatterbox, espeak), paragraphe par paragraphe. |
| Messagerie : signature, police, règles | Paramètres › Messagerie (bouton « Signature et règles » dans la boîte de réception) : signature mise en forme (nouveaux, réponses, transferts), police et taille, copie cachée automatique (aussi sur les brouillons de l’agent, visible et modifiable dans l’éditeur), règles de classement par objet, expéditeur ou destinataire vers un sous-dossier, appliquées après lecture par l’agent (ou 15 minutes), journalisées, sans suppression. |
| Boutons illisibles en thème clair (Toutes les fonctions, Studio de production) | Élément courant du menu lisible ; un `<aside>` du contenu ne prend plus le style de la barre latérale (il la recouvrait). |
