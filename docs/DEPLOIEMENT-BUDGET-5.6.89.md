# Mise à jour 5.6.89 : registre du budget et politique des données

Note de déploiement pour l'administrateur. À lire avant d'installer la version.

## Ce qui change

- `budget_ledger5689` devient la seule autorité sur la dépense : plafonds cabinet, mois, fournisseur et mission. `budget567.reserve`, `ai_gateway.estimate_and_check_budget` et `conso5682.reserve` en sont des adaptateurs.
- `ai_reservations_v567` n'est plus qu'une vue, tenue pour les anciennes pages. `reservations_5682` n'est plus alimentée.
- `data_policy5689` décide avant chaque accès ou transfert. Tant que le cabinet n'a pas choisi de mode, les réglages existants continuent de s'appliquer.

## Pourquoi un ancien travailleur ne peut pas ignorer les plafonds

Une option d'interface ne protège pas contre un ancien processus : il ne la lit pas. La protection est dans la base.

Au démarrage, chaque nouveau travailleur et le service web appellent `budget_ledger5689.preparer`. Cette étape, idempotente :

1. crée les tables du registre ;
2. reprend les réservations ouvertes (`reserved`, `uncertain`) comme **incertaines**, montant conservé, provenance `migration`, tarif `avant_5689`. Rien n'est libéré, rien n'est remis à zéro ;
3. pose deux déclencheurs SQLite sur `ai_reservations_v567` et `reservations_5682`.

Dès lors, un ancien travailleur qui tente de réserver reçoit `budget_travailleur_ancien_refuse`. Son travail échoue **avant** l'envoi au fournisseur : il ne dépense rien. Les lignes qu'il a déjà écrites (`ai_usage_v391` sans `call_id`, réservations sans jumeau) restent comptées dans les plafonds.

Limite : un ancien travailleur peut encore appeler le modèle local, sans coût d'API. Un ancien travailleur en abonnement ChatGPT sans mission n'écrivait aucune réservation : il n'est pas arrêté par la base, mais il n'engage pas de dépense à l'appel. Il faut donc quand même l'arrêter.

## Ordre conseillé

1. Mettre l'IA en pause (Paramètres), ou laisser la mise à jour le faire.
2. Arrêter ou laisser se terminer les travailleurs : ne pas lancer l'installation pendant une longue génération.
3. Installer, redémarrer **tous** les travailleurs et le service web.
4. Vérifier `GET /api440/m5689/budget/bascule` : `barriere_active` vaut `true`, et chaque travailleur attendu apparaît avec la version 5.6.89. Un ancien travailleur n'y apparaît jamais : il ne s'enregistre pas. S'il en reste un, ses travaux échouent avec le code ci-dessus, visible dans le journal.
5. Reprendre l'IA.

## Après la bascule

- Paramètres › Données et budget de l'IA : les appels repris apparaissent comme incertains. Rapprochez-les avec la facture (`POST /api440/m5689/budget/rapprochement`), ou libérez-les depuis Paramètres › IA **après contrôle de la facture**.
- Fixer un plafond du cabinet prend en compte ce qui est déjà engagé sur le mois.
- Choisir un mode (local strict, cloud, hybride) active les règles nouvelles sur les recherches, le connecteur Claude et la voix. En cloud ou hybride, seuls les prestataires admis au registre restent utilisables : qualifiez-les **avant** de choisir le mode. Une valeur non renseignée reste inconnue.
- Mode local strict choisi expressément : les services privés du cabinet hors Nextcloud (facturation, autres serveurs) doivent être déclarés au registre, nature « Service privé du cabinet », sinon ils sont refusés par la passerelle.
- Changement de comportement : en mode automatique, une panne du modèle local ne bascule plus vers un fournisseur externe coché, sauf si la politique l'autorise expressément. Dès qu'une politique est choisie, le secours externe et l'escalade hybride obéissent à la même case. Sans politique choisie, ces deux options historiques restent celles que le cabinet a réglées.

## Retour arrière

Avant de réinstaller une version antérieure : `POST /api440/m5689/budget/bascule/retour` (administrateur). La barrière est retirée, les réservations ouvertes redeviennent visibles de l'ancien code comme incertaines. Le registre, les journaux et les coûts saisis sont conservés. Arrêtez ensuite les travailleurs, puis installez.

Sans cet appel, l'ancienne version ne peut plus réserver de budget externe : elle refuse d'envoyer, elle ne dépense pas.

## Ce que ce dispositif ne garantit pas

- L'estimation par la taille du texte est une approximation, pas une garantie de facture.
- Les autres logiciels utilisant le même compte fournisseur restent hors du plafond AxiorHub.
- La voix externe et la recherche Tavily gardent leurs compteurs propres (`voice_usage_v568`, `veille5632.usage`) : la politique des données s'y applique, pas encore les plafonds du cabinet.
- Aucun cloisonnement de processus n'existe : aucun code d'extension n'est exécuté, c'est la seule garantie.
