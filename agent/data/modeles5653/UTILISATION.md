# Utiliser les modèles du cabinet

Les huit fichiers Word sont modifiables. Leurs versions Markdown sont des sources textuelles destinées à l'intégration. La version commune est le 16 octobre 2026 ; les clauses et profils fiscaux doivent être vérifiés à la date de leur utilisation.

Ces modèles originaux contiennent des variables entre doubles accolades. Les variantes ne doivent pas être confiées au modèle de langage comme un choix libre de régime fiscal. Le serveur résout les clauses autorisées avant rédaction ; la relecture de l'avocat porte sur l'ensemble du document et ses annexes.

## Variables et sources

| Groupe | Variables principales | Source et contrôle |
|---|---|---|
| Cabinet | cabinet_identite, barreau, cabinet_adresse, cabinet_siren, cabinet_siret, cabinet_tva, cabinet_contact, logo | Fiche cabinet validée ; identifiants vérifiés ; image contrôlée |
| Client | client_identite, client_adresse, client_siren_si_applicable, representant_client, qualite_signataire | Annuaire et pièce ou source d'identification ; distinguer client, représentant et payeur |
| Dossier | reference_dossier, mission, contexte_valide, objectif_client, perimetre_inclus, perimetre_exclu, livrables | Mémoire sourcée et décisions du cabinet ; exclusion des faits contestés présentés comme établis |
| Tarification | forfait_ht, forfait_ttc, taux_horaire_ht, prix_par_phase, budget_et_plafond, provision, echeancier | Calcul serveur retenu et convention applicable ; null ne signifie pas zéro |
| Facture | numero_facture, date_emission, date_prestation, total_ht, ventilation_tva, total_ttc, net_a_payer | Registre d'émission et moteur financier ; ne pas demander au LLM de les inventer |
| Fiscalité | mentions_tva, mentions_tva_et_ttc, categorie_operation | Profil qualifié et daté ; zéro ne prouve pas une franchise ; clause d'exonération ou autoliquidation uniquement si applicable |
| Signature | signataire_client, signataire_avocat, date_acceptation, signature_client | Personnes confirmées et preuve de signature ; un placeholder n'est pas une signature |
| Annexes | version_cgh, annexes_identifiees, preuve_remise_cgh | Identifiants, versions et preuve de remise ; le hash de l'approbation inclut les annexes |
| Suivi | imputation_acomptes_factures, references_factures_anterieures, imputation_avoir | Registre et rapprochements contrôlés ; pas de double imputation |

La liste exhaustive des variables de chaque modèle figure dans `catalogue-modeles.json`. Une variable sans valeur doit être affichée comme donnée manquante et bloquer l'envoi lorsqu'elle est obligatoire. Une variable sans objet est retirée par une règle identifiée ; elle n'est pas remplie avec une donnée inventée.

## Clauses conditionnelles

- `clause_retard_adaptee_client` : branche professionnelle ou consommateur. Les pénalités professionnelles et l'indemnité de 40 euros ne sont pas ajoutées aux consommateurs. Le taux applicable et sa date viennent d'une source validée.
- `clause_resultat_si_convenue` : par défaut, « Aucun honoraire de résultat n'est convenu. » Si l'avocat retient un complément, il précise par écrit assiette, taux ou montant, événement déclencheur, exigibilité et cas particuliers ; il conserve une rémunération des prestations. Ne pas activer une clause de résultat à partir d'une simple suggestion IA.
- `budget_et_plafond` : préciser si le budget est indicatif, un plafond ferme ou une facturation par phase. Ne pas laisser ces trois qualifications simultanément.
- `information_mediation_consommateur` : activée dans son champ, avec identité et coordonnées actuelles du médiateur de la profession, saisine après réclamation préalable et lien vers la procédure. Vérifier les informations officielles avant remplissage ; elles ne sont pas déduites du nom d'un ancien médiateur.
- `information_retractation_si_applicable` : insérer le renvoi à l'annexe 07 lorsqu'elle s'applique ; qualifier le mode de conclusion du contrat. La demande d'exécution anticipée et la reconnaissance de perte du droit après pleine exécution restent deux choix exprès distincts.
- `clause_outils_ia_validee` : exemple de base à adapter à la pratique réelle : « Le cabinet peut recourir à des outils d'assistance pour préparer certains travaux. L'avocat contrôle les sources et les livrables et conserve la responsabilité du travail remis. Leur usage respecte le secret professionnel et la protection des données ; les éventuelles transmissions à un service externe suivent le périmètre autorisé et les garanties retenues. » Cette clause ne dispense pas du choix et du contrôle des outils ; elle ne constitue pas un consentement général au partage des pièces.
- `detail_client_autorise` : référence ou détail uniquement dans le circuit technique qualifié pour le secret professionnel ; exclure toute reprise automatique du résumé du dossier dans les notes XML ou l'objet de facture.

## Lignes et présentation

`[[LIGNES]]` indique une répétition de lignes dans l'intégration. Chaque ligne provient du modèle canonique, avec une quantité décimale et des montants calculés. Les gabarits Word comportent une ligne de démonstration à dupliquer ou à remplacer par le moteur ; elle ne contient aucun tarif réel.

La facture et l'avoir Word sont des modèles de rendu. Ils ne constituent pas un UBL ou un Factur-X. Leur export structuré nécessite le moteur, les profils, les validations et l'acceptation du prestataire décrits dans l'audit.

## Validation et conservation

Avant usage client : résoudre toutes les variables, retirer les variantes inapplicables, vérifier client, périmètre, fiscalité et prix, approuver le document exact et ses annexes. Après signature ou émission, conserver la version finale, les preuves et les liens vers le dossier. Une modification du modèle ne modifie pas les documents déjà signés ou émis.

Les cas d'aide juridictionnelle, rémunérations réglementées, opérations fiscales complexes ou conventions particulières nécessitent des modèles dédiés qualifiés ; ils ne sont pas couverts silencieusement par ces bases génériques.
