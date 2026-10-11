---
name: approbation-comptes
description: "Prépare le dossier de décisions d’approbation des comptes annuels d’une société (SARL, EURL, SAS, SASU) à partir des documents de la société : questions progressives, calculs traçables, projets de convocation, d’ordre du jour, de résolutions, de procès-verbal ou de décision de l’associé unique, liste des pièces et formalités. Création originale AxiorHub (AGPL-3.0)."
---

# Approbation des comptes annuels

## Objet
Préparer, pour l’avocat, les **projets** nécessaires à l’approbation des comptes d’un exercice. Un projet n’est ni une décision adoptée,
ni un document signé, ni une formalité accomplie : ne jamais présenter l’un comme l’autre.

## Étape 1 — Identifier la société et le cadre
1. Reprendre les informations connues du dossier AxiorHub (dénomination, forme, siège, RCS) ; ne rien supposer.
2. Déterminer la forme sociale. Profils pris en charge : **SARL**, **EURL**, **SAS**, **SASU**. Toute autre forme : indiquer qu’un profil
   qualifié est nécessaire et s’arrêter après l’analyse des documents.
3. Demander, s’ils manquent, et dans cet ordre : statuts à jour, date de clôture de l’exercice, associés et droits de vote, dirigeants,
   comptes annuels (bilan, compte de résultat, annexe), rapports disponibles (gestion, commissaire aux comptes le cas échéant),
   décisions antérieures pertinentes, affectation envisagée du résultat.

## Étape 2 — Règles applicables
- Les délais, modalités de consultation, documents à communiquer, réserves légales ou statutaires et règles de distribution dépendent de
  la forme sociale, des statuts et de la date utile. Rechercher leurs fondements à la date utile au moyen des connecteurs juridiques
  configurés et les soumettre au contrôle des références d’AxiorHub. Ne jamais appliquer une « règle universelle » de mémoire.
- Une stipulation statutaire prévaut dans les limites de la loi : la citer avec l’article des statuts concerné.

## Étape 3 — Contrôles et calculs
- Contrôler la cohérence : identité de la société, exercice, dates, montants repris d’un document à l’autre.
- Les calculs (bénéfice distribuable, dotation à la réserve, dividendes, report à nouveau) sont faits par un calcul déterministe en
  montants décimaux, avec devise, règle d’arrondi et trace de chaque ligne ; l’explication accompagne le calcul sans le remplacer.
- Une valeur lue par OCR incertaine, une réserve inconnue ou des comptes contradictoires : poser une question, ne pas compléter.

## Étape 4 — Projets
Selon le cas confirmé : convocation(s), ordre du jour, texte des résolutions, procès-verbal d’assemblée ou décision de l’associé unique,
affectation du résultat, liste des pièces à joindre et des formalités à préparer (dépôt des comptes, publicité éventuelle). Les champs
inconnus restent « À compléter ». Ne jamais inventer la présence d’un associé, un vote, une majorité ou l’adoption d’une résolution.

## Étape 5 — Suites
Proposer les échéances (assemblée, dépôt) dans le circuit d’agenda d’AxiorHub et les brouillons d’accompagnement dans la messagerie, pour
validation par l’avocat. Aucune signature, aucun dépôt, aucun envoi n’est déclenché par cette compétence.

## Résultat attendu (structure)
Voir `schemas/accounts-output.json` : identification, profil retenu, informations manquantes, contrôles, calculs tracés, projets produits,
références juridiques (avec leur état de contrôle), échéances proposées, réserves.
