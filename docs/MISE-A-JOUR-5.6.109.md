# Mise à jour en 5.6.109 : ce que l'administrateur doit faire

Note courte pour l'administrateur et le prestataire. Le détail des changements est dans `CHANGELOG.md` (entrée 5.6.109) et l'état
ticket par ticket dans `VALIDATION-5.6.109.json`.

## Avant la mise à jour (installations encore en 5.6.108)

En 5.6.108, la sauvegarde pouvait faire perdre des écritures de la base du bureau et retirer son journal pendant le service (mécanisme
démontré sous Linux ; une base de production a été trouvée endommagée). Jusqu'à la mise à jour :

- ne lancer **aucune sauvegarde automatique** : décocher « Sauvegarder automatiquement chaque jour » (Paramètres › Poste, section
  « Sauvegarde automatique ») ;
- ne lancer **aucune sauvegarde manuelle** depuis l'interface (même chemin de code) ;
- après une remise en place de la base depuis une archive (procédure « Base illisible » de `docs/REVERSIBILITE-5.6.88.md`) : copier à
  part l'archive utilisée avant de redémarrer, et suspendre la sauvegarde automatique avant de redémarrer les workers. En 5.6.108, la base
  restaurée relance une sauvegarde une minute après le redémarrage, et la rétention retire alors l'archive saine du même jour. La 5.6.109
  ne relance plus rien d'office : aucune sauvegarde automatique avant l'heure planifiée suivante, archive d'origine protégée 72 heures.

## Juste après la mise à jour

1. **Contrôler les unités systemd.** Jusqu'ici, une mise à jour ne réécrivait jamais les unités installées : elles gardaient le
   contenu de la première installation. La 5.6.109 les réécrit depuis la version livrée, garde l'ancien contenu dans `backups/` et
   recharge systemd ; un retour arrière ne remet pas les anciennes unités. Ce chemin a été essayé sous Linux dans Docker, pas sur un
   vrai systemd. Vérifier sur le serveur :

   ```
   systemctl daemon-reload
   systemctl cat axiorhub-mail-ui.service axiorhub-mail-desk-worker.service axiorhub-mail-watch.service | grep -E 'RestartPreventExitStatus|StartLimit|RestartSec|Memory'
   ```

   Attendu : `RestartPreventExitStatus=78`, `StartLimitIntervalSec=600`, `StartLimitBurst=5`, `RestartSec=15` ; interface
   `MemoryHigh=450M`, `MemoryMax=768M`, `MemorySwapMax=256M` ; travailleur `MemoryHigh=1536M`. Ces limites de mémoire sont des valeurs
   de départ : la cause de la limite de 512 Mo atteinte par l'interface n'a pas été trouvée ; les ajuster après mesure
   (`systemctl status axiorhub-mail-ui.service`, ligne « Memory »). Si une unité porte encore l'ancien contenu, la remplacer par celle du
   dossier `deploy/` de la version installée, puis `systemctl daemon-reload` et redémarrer les services.
2. **Voir les nuits manquées** : `python3 -m agent.secours56109 sauvegardes` (depuis le dossier de l'application, par exemple
   `/opt/axiorhub-mail-agent/current`, avec l'utilisateur du service ; l'état est trouvé par `/etc/axiorhub-mail-agent/config.json`,
   sinon ajouter `--config FICHIER` ou `--etat DOSSIER`). La commande liste les sauvegardes faites, en échec ou absentes ; `python3 -m agent.secours56109 diagnostic` donne l'état
   général (bases, archives, alerte de base illisible). Rien n'est modifié par ces deux commandes.
3. **Réactiver la sauvegarde automatique** (Paramètres › Poste), puis lancer une sauvegarde et vérifier qu'elle finit « réussie ».
4. **Historique de l'assistant.** Chaque personne ne voit plus que ses propres fils. Les anciens fils sans auteur sont attribués
   d'office au compte quand une seule identité a pu les écrire (installation autonome à un compte, serveur avec l'administrateur seul,
   authentification du serveur sans registre de comptes, et aucune autre identité jamais vue) ; l'attribution est tracée au journal.
   Dans les autres cas (plusieurs comptes, application Nextcloud), les anciens fils restent invisibles, administrateur compris, jusqu'à
   leur attribution : `/assistant?vue=attribuer`, une preuve écrite par attribution. Un historique vide juste après la mise à jour
   signifie qu'il reste des fils à attribuer.

5. **Clé Mistral (si vous en utilisez une).** Dans Paramètres › Modèles d'IA : réexaminer la clé ; si la carte affiche « Modèle à
   choisir », choisir un modèle listé par votre clé ; lancer « Essayer une requête réelle » (courriel fictif, même forme que la
   rédaction) et lire le résultat : c'est la première vérification réelle de la correction, qui n'a été essayée qu'avec un faux
   transport. Renseigner le tarif du modèle retenu s'il n'est pas connu : sans tarif, aucun envoi n'est fait. Le réglage `top_p` mis à
   1 à la main en 5.6.108 peut rester : la 5.6.109 l'envoie d'office à Mistral quand la température est à 0.
6. **Modèle et contexte par tâche (facultatif).** Même page, tableau « Modèles locaux et routage » : chaque fonction peut recevoir une
   connexion et un modèle précis (« Choisi par vous ») et un niveau de contexte « Réduit » ou « Normal ». Rien ne change tant que vous
   n'y touchez pas. Un modèle fixé est appelé seul : s'il échoue ou si sa connexion est en panne, la tâche s'arrête avec un message qui
   le nomme, sans bascule vers un autre modèle. Le choix n'élargit aucune autorisation (mode local, consentement, dossier exclu). Le
   détail de chaque mission indique la taille réellement transmise et ce que le niveau réduit a écarté.

## Base illisible : ce qui change

Si la base du bureau ne peut plus être lue, AxiorHub affiche « AxiorHub est arrêté par sécurité » au lieu d'erreurs 500 et les
services s'arrêtent sans boucle de redémarrage. Suivre alors la procédure « Base illisible » de `docs/REVERSIBILITE-5.6.88.md`
(outil `python3 -m agent.secours56109 archives | preparer-remise | lever`).

## Nouvelles options : toutes désactivées par défaut

| Option | Où la trouver | Exige |
|---|---|---|
| Gemini (Google) | Paramètres › Modèles d'IA | clé API ; « Tester la connexion » réussi et tarif saisi avant activation |
| Bland, téléphonie par IA | Paramètres › Connexions API, famille « Téléphonie par IA » ; guide `docs/GUIDE-BLAND.md` | compte et clé API Bland ; numéros et crédits chez Bland |
| Rappel de rendez-vous au client, deux jours avant | Aujourd'hui › Mes routines \| Mes agents IA | Bland relié ; accord du client ; mode par défaut « préparer et faire confirmer » |
| Décision locale | Paramètres › IA | un modèle local (Ollama) |
| Éditeurs GridCraft, PdfCraft, DeckCraft | Paramètres › Documents | ressources compilées, non livrées : option non activable pour l'instant |
| 13 méthodes métier (`metier109-*`) | page Compétences | rien ; à relire par l'avocat avant usage |
| Fonds juridique local, fichiers de ce poste | Paramètres › Recherche et sources ; Paramètres › Poste | rien ; mise à jour du fonds à la main |
| Pappers Entreprises (API), registres étrangers | Paramètres › Connexions API | clé ou compte du fournisseur (coûts inscrits dans « Consommation et coûts ») |

Exceptions, actives dès la mise à jour : les sources publiques sans clé (data.gouv.fr, JusticeLibre, législations allemande,
espagnole, britannique, canadienne et québécoise) sont préconfigurées puis activées après une vérification automatique par le serveur
(recherche neutre, aucune donnée du cabinet) ; une source désactivée par l'utilisateur le reste. Les compétences de parcours
(`parcours109-*`) sont proposées actives sur une installation neuve ; une désactivation existante est conservée. Les portails
(e-Barreau, INPI, Infogreffe, CARPA, portails étrangers) ne produisent aucun effet : aucun profil n'est qualifié.

## À savoir

- Facture au temps : la ligne est désormais calculée en heures à 6 décimales (50 min à 200 € l'heure = 166,67 €, au lieu de 166,00 €).
- Les fichiers de base de données placés dans un dossier ou un emplacement du poste ne sont plus jamais ouverts (« fichier de base non lu »).
- Navigateur supervisé : le modèle se choisit dans Paramètres › IA (« Modèle du navigateur ») ; qwen3.5:9b n'a jamais été essayé avec
  cette version : la recette des 30 parcours (`scripts/recette-navigateur-qwen56109.py`) est à faire par le cabinet avant usage.
- Rappel de rendez-vous par Bland (désactivé) : le lieu libre de l'agenda n'entre jamais dans un rappel ; seuls « visioconférence »,
  « téléphone », l'adresse du cabinet réglée dans la carte de la routine, ou rien. Le mode « envoyer automatiquement » exige le droit
  « envoyer », relu à chaque passage ; chaque tentative compte dans le plafond du jour, et le premier résultat incertain suspend l'envoi
  automatique (à réactiver après vérification). Modifier le texte du SMS, la consigne d'appel ou les coordonnées du cabinet demande une
  nouvelle validation. La routine n'a pas de bouton « Lancer ».
- Veille de sécurité : le fichier `account.conf` d'acme.sh est surveillé ; les alias globaux d'Apache (`conf-enabled`) comptent dans
  l'exposition ; une copie d'application non servie est signalée à part.
- Export : aucun pourcentage inventé par un modèle n'en sort (simulation de décision, anciens projets d'avis bloqués).
- Les traductions anglaises, allemandes et espagnoles ajoutées n'ont pas été relues par un humain.
