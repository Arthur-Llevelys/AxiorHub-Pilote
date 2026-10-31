# Recette de la 5.6.83 : état ligne par ligne

Référence : complément du cahier des charges « Avocat conseil, compétences et cessions » et son annexe technique, tous deux du 31 octobre 2026.
La livraison est découpée : la 5.6.83 couvre les compétences, le moteur d'opération commun, les cessions de parts et d'actions et les formulaires
officiels ; la 5.6.84 couvrira les cessions de fonds et de bail, la bibliothèque de modèles et les fonctions de conseil.

États : **livré et testé**, **partiel**, **5.6.84** (prévu dans la version suivante), **non essayé** (code présent, essai réel impossible ici).
Tous les essais utilisent des données fictives.

## Compétences (SK)

| Ligne | État | Preuve ou motif |
|---|---|---|
| SK01 YAML | Livré et testé | `yaml5683` ; blocs, listes, métadonnées imbriquées, clés dupliquées refusées ; neuf archives réelles lues sans erreur |
| SK02 ressources | Livré et testé | `skill_resources5683` ; essai réel : 131 fichiers inventoriés sur les neuf archives |
| SK03 export et réimport | Livré et testé | `export_package`, fichiers restitués à l'identique |
| SK04 compétence longue | Livré et testé | Noyau et sections utiles dans le budget réel ; sections omises signalées |
| SK05 301 entrées | Livré et testé | Refus explicite, aucune troncature |
| SK06 chemin et lien symbolique | Livré et testé | Refus sans écriture |
| SK07 droits | Livré et testé | Restriction non commerciale et droits inconnus affichés ; aucun contenu des archives dans le dépôt |
| SK08 connecteur manquant | Livré et testé | Capacités : Disponible, Connexion requise, Accès refusé, Capacité limitée |
| SK09 outil natif absent | Partiel | Le parcours serveur existant (5.6.52) reste en place ; pas de recette dédiée par fournisseur |
| SK10 sortie invalide | Partiel | Une réparation au plus pour le rapprochement des formulaires (testé) ; ailleurs, schéma existant du modèle |
| SK11 page 200 sans contenu | Livré et testé | `liens5683` : référence non déclarée vérifiée |
| SK12 réseau privé | Livré et testé | Refus, y compris après une redirection |
| SK13 Humaniseur | Non livré | Contrôle faits, montants, dates et citations à écrire : 5.6.84 |
| SK14 ChatGPT exclusif | Livré et testé en 5.6.82 | Les nouveaux appels passent par le même point de contrôle |
| SK15 annulation | Livré en 5.6.82 | Comportement inchangé ; journal des étapes ajouté (`skill_runs5683`) |
| SK16 vingt rôles | Non essayé | Aucun essai rôle par rôle sur un modèle réel |
| SK17 scripts | Livré et testé | Une décision par script ; essai réel : 19 scripts, 1 remplacé (vérification des liens), 18 indisponibles avec motif ; aucun exécuté |
| Paquets PK (27 combinaisons) | Non essayé | Aucun test sur Ollama, Mistral ou ChatGPT réels ; Gouvernance reste suspendue faute d'autorisation |

## Cessions (CT)

| Ligne | État | Preuve |
|---|---|---|
| CT01 parts SARL | Livré et testé | Aucun ordre de mouvement ; acte, notification, attestation de dépôt, agrément si requis |
| CT02 actions SAS | Livré et testé | Ordre, instruction et attestation distincts ; attestation seulement après l'inscription réelle ; statuts et pacte exigés |
| CT03 générer | Livré et testé | Registres et détentions inchangés |
| CT04 réalisation sans preuve | Livré et testé | Refus sans mouvement : pièce du dossier vérifiée ou signature vérifiée |
| CT05 concurrence | Livré et testé | Contrôle dans la transaction |
| CT06 répétition | Livré et testé | Un seul mouvement ; reprise après panne rapprochée sans doublon |
| CT07 dates | Livré et testé | Sept dates distinctes ; transfert à l'inscription pour les actions |
| CT08 2759 | Livré et testé | Applicable sans acte pour des actions seulement |
| CT09 droits | Livré et testé | Assiette, abattement, taux, montant exact et arrondi ; règles à valider par un avocat |
| CT10 forme non couverte | Livré et testé | Périmètre limité affiché, préparation manuelle à confirmer |

L'arrondi repose sur un texte relu dont la base signale une renumérotation : le montant exact reste affiché à côté du montant arrondi, à vérifier par l'avocat.
Aucun minimum de perception ne figure à l'article 726 relu.

## Interface et formalités

| Ligne | État | Preuve |
|---|---|---|
| UX01 prix modifié | Livré et testé | Documents périmés signalés, nouvelle version, acte signé conservé |
| UX02 création de document | Livré | Actualisation après l'action ; essai navigateur |
| UX03 correction d'un champ | Livré et testé | Valeur et source conservées, aucun fait créé |
| UX04 mobile et clavier | Livré | Onglets sélectionnés visibles sans dépendre de la couleur ; essai à 390 px |
| OP01 transmis et accepté | Livré et testé | États distincts, récépissé exigé au retour, rejet géré |
| OP02 reprise | Livré et testé | Aucune double formalité ni double mouvement |

Aucun dépôt automatique : une formalité se prépare, s'autorise, se transmet à la main et son récépissé revient au dossier.

## Formulaires officiels

13 formulaires au catalogue, aucun PDF dans le dépôt. Essai réel du 31 octobre 2026 : les 13 téléchargés depuis impots.gouv.fr et service-public.gouv.fr, empreintes identiques.
Trois ont des champs (2672-SD, 2676-SD, 2759-SD) ; écriture puis relecture réussies sur les trois. Les dix autres sont des PDF plats : fiche de données à recopier, sans imiter le Cerfa.

Limites : le rapprochement par mots-clés ne remplit qu'une partie des champs et ne distingue pas encore cédant et cessionnaire dans le 2759-SD ; relecture obligatoire. Le calage sur PDF plat n'est pas livré.
Non identifiés : 2670, 1674, requête en conciliation.

## Discussion : courriels, agenda et documents (demande du cabinet)

| Point | État | Preuve |
|---|---|---|
| Lecture des courriels sans les transmettre | Livré et testé | Messagerie simulée ; lecture sans marquer lu |
| Agenda, événements et tâches | Livré et testé | Instantané local ; ancienneté signalée |
| Date du jour transmise au modèle | Livré et testé | Toutes les questions |
| Dossier réservé | Livré et testé | Courriel, événement et document masqués, y compris pendant la préparation en file (correction d'une fuite possible) |
| Panne de messagerie | Livré et testé | Dite dans la réponse, jamais prise pour une absence de courriel |
| Modèle occupé | Livré et testé | Liste relevée rendue sans analyse |
| Documents | Livré et testé | Plus récente d'abord à égalité (l'ordre inverse était un défaut), dossier actif et fraîcheur, dossier nommé, aucune recherche sans objet |
| Essai sur une vraie messagerie | Non essayé | À confirmer sur le serveur du cabinet |

## Non livré dans la 5.6.83

- Page Cessions de fonds et de bail, FD01 à FD09 : 5.6.84.
- Bibliothèque des 66 modèles, TemplateVersion et validation par modèle : 5.6.84. Les modèles de cession sont génériques, à faire valider par un avocat.
- Fonctions de conseil prioritaires : 5.6.84.
- Éditeur de classeur GridCraft : reporté ; aperçu Excel en lecture seule livré.
- Liaison automatique des événements de signature : le service existe (`record_verified_signature`), le branchement sur chaque fournisseur reste à faire.
- Comparaison avant et après sur consultations réelles : à mener par le cabinet.
