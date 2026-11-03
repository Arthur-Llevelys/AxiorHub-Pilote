# Banc d'essai comparatif

La page **Banc d'essai et état des fonctions** (Paramètres, Cabinet et utilisateurs) compare des missions complètes entre AxiorHub et une référence, comme Claude Desktop ou Cowork.

**Le comparatif réel n'a pas été fait.** Le logiciel est livré sans résultat, sans score et sans gain. Rien n'y lance un modèle : une personne mène les essais, puis saisit ce qu'elle a mesuré. La relecture juridique reste humaine.

## Déroulement

1. **Protocole.** Créez-le, ajoutez les cas, fixez l'effectif minimal et les parcours prioritaires, puis figez-le. Un protocole figé ne se modifie plus : une évolution est une nouvelle version. Le corpus et le protocole reçoivent une empreinte SHA-256, recalculée à chaque essai.
2. **Configurations.** Consignez celle d'AxiorHub (version, modèle et politique sont relevés par le logiciel) et celle de la référence : canal réel, offre, version, modèle, compétences, connecteurs, sources, intervention humaine. Le canal est une liste fermée : un essai par API ne peut pas être présenté comme un essai de Claude Desktop. L'installation et la préparation initiale se mesurent à part de l'usage quotidien.
3. **Essai.** Un essai porte sur une seule partie du corpus : réglage (identifiant `reg-`) ou évaluation finale (`eva-`). Le contenu des cas réservés reste masqué tant qu'aucune évaluation n'a démarré. La page indique l'ordre alterné.
4. **Résultats.** Pour chaque cas et chaque produit : temps de préparation, durée d'exécution, temps de correction, résultat (rejeté, partiel, exploitable), omissions, incidents, livrable avec sa version et son empreinte, et la trace de ce qui s'est passé : sources, actions, destinataires, références, échéances. Une mesure non relevée reste absente. Un résultat enregistré ne se réécrit pas.
5. **Relecture.** Un avocat donne son avis sur des sorties désignées par une lettre. Si la mise à l'aveugle n'a pas été obtenue, il le dit ; la personne qui a saisi un résultat ne peut pas se déclarer à l'aveugle. L'avis le plus sévère est retenu.
6. **Coûts.** Un appel d'AxiorHub se lit au registre du budget par son identifiant. Les autres montants viennent d'une facture ou d'une estimation déclarée. Un appel ou un crédit ne compte qu'une fois ; un appel incertain reste inconnu.
7. **Comparaison et réception.** Différences par cas, puis médiane, quartiles et effectif par famille. Si les données manquent, la page ne conclut pas et dit pourquoi. Une personne reçoit ensuite chaque famille ; une nouvelle version ou une autre configuration appelle une nouvelle qualification.

## Erreurs critiques

Divulgation hors droits, action externe non autorisée, mauvais destinataire, référence fabriquée présentée comme vérifiée, échéance certaine sans données suffisantes.

Elles sont détectées sur la trace de l'essai, ou signalées par le relecteur. Une seule empêche de recevoir la famille concernée. L'absence d'erreur sur les cas essayés ne prouve pas une absence universelle de défaut.

## Étendre le corpus à cinquante situations

Le corpus fourni (`agent/banc_corpus5689.py`) compte douze cas fictifs. Il exerce l'outil et n'autorise aucune réception.

Répartition cible, vingt cas de réglage et trente d'évaluation :

| Famille | Réglage | Évaluation |
|---|---|---|
| Consultation | 4 | 5 |
| Contrat | 4 | 5 |
| Réponse fondée sur le dossier | 3 | 5 |
| Acte contentieux | 3 | 5 |
| Audience | 3 | 5 |
| Suivi Corporate ou cession | 3 | 5 |

Méthode :

1. Partez de vraies situations de travail, puis récrivez-les entièrement avec des faits inventés. Ne recopiez aucune pièce. Sinon, utilisez un dossier dont les droits sont établis pour chaque destination testée : un benchmark n'autorise pas à transmettre un dossier.
2. Pour chaque cas, écrivez la question, les sources autorisées avec leur texte, les livrables, les faits attendus, les réserves, les critères de qualité, les actions permises et les destinataires admis. Listez les références vérifiables. Si une donnée rend une échéance incertaine, indiquez-la dans `echeance_donnees_manquantes`.
3. Répartissez les difficultés dans chaque famille : un cas simple, un cas à source manquante, un cas à contradiction, un cas piégé (note interne à ne pas transmettre, destinataire proche, citation à ne pas inventer, date à ne pas déduire).
4. Tirez au sort réglage et évaluation avant toute lecture des résultats. Une personne qui règle le produit ne lit pas les cas d'évaluation.
5. Ajoutez les cas à un protocole du cabinet, vérifiez la couverture affichée par la page, puis figez. Fixez avant essai l'effectif minimal et les trois parcours prioritaires.
6. Faites relire le corpus par un avocat qui ne participera pas aux essais.

La cible de réduction médiane de trente pour cent du temps total sur trois parcours prioritaires est un objectif de réception à confirmer, ni une performance observée ni une garantie.

## Références

Les pages consultées le 2 novembre 2026 sur les offres de comparaison figurent sur la page du banc. Elles décrivent des offres, pas une qualité : chaque essai consigne l'offre réellement utilisée.
