# Sécurité

## Signaler une vulnérabilité

Utilisez le **signalement privé de vulnérabilité** du dépôt GitHub (onglet *Security → Report a vulnerability*).
N'ouvrez pas de ticket public contenant un secret, une donnée de client ou le détail d'une faille exploitable.

## Signaler une anomalie sans exposer le cabinet

Le code est public ; les données du cabinet ne le sont jamais. Pour une anomalie ordinaire, utilisez le modèle de ticket
« Signaler une anomalie » et collez-y le texte de « Pourquoi rien n'est produit ? › Préparer un signalement anonymisé » :
version, état des services, codes d'erreur et origines techniques, sans nom de dossier, de partie, de courriel ni de chemin.
N'ajoutez ni capture d'écran d'une installation réelle, ni pièce, ni journal brut.

## Ce qui ne doit jamais être publié

`.env`, `data/`, `deploy/vps/data/`, les bases SQLite, les fichiers `*.secret`, les archives de dossiers, les journaux
contenant des données de clients, les captures d'écran d'une installation réelle. `scripts/privacy-scan.py` bloque les
cas courants ; il ne dispense pas d'une relecture.

## Principes

- Rien ne part sans validation humaine : AxiorHub Pilote dépose des brouillons et des projets, il n'envoie ni ne dépose rien.
- Une IA non locale ne reçoit que des textes pseudonymisés.
- Les services n'écoutent que sur `127.0.0.1` ; le frontal HTTPS est seul exposé.

## Double authentification et accès

Codes à six chiffres (RFC 6238), facultatifs par défaut, avec une option du cabinet pour les exiger. Secret chiffré par le coffre, dix codes
de secours à usage unique gardés hachés, essais limités, code déjà consommé refusé. Comportement de chaque accès pour un compte protégé :

| Accès | Comportement |
|---|---|
| Page de connexion | Le mot de passe seul n'ouvre aucune session ; le code est demandé à chaque connexion. |
| « Se souvenir de moi » | Prolonge la session ouverte après le code (30 jours). Aucun appareil n'est dispensé du code ; une session antérieure à l'activation est fermée. |
| Téléphone associé | L'invitation par QR code ne suffit pas : le téléphone saisit un code. Ceux associés avant l'activation sont déconnectés. |
| Connecteur Claude | L'autorisation passe par la page de connexion, donc par le code. Les autorisations antérieures à l'activation ou à une réinitialisation sont retirées. |
| Jetons d'API personnels | Créés depuis une session qui a passé le code ; ceux d'avant l'activation cessent de fonctionner. Ils ne redemandent pas le code à chaque appel : à garder comme un mot de passe. |
| En-tête « Basic » de l'administrateur | Refusé dès que la protection de ce compte est active ou exigée : les scripts passent à un jeton d'API. |
| Application Nextcloud | Relier un compte demande le code (champ `code` de `/integration/v1/pair`). Les clés déjà remises sont révoquées à l'activation. Une fois relié, c'est la connexion Nextcloud qui s'applique, avec sa propre double authentification. |
| Jeton de service | Accès de machine sans identité, hors de la double authentification. Il se protège sur le serveur. |
| Poste et ExApp | Sans comptes propres : pas de double authentification (fenêtre locale ; connexion Nextcloud). |

Téléphone et codes de secours perdus : un administrateur réinitialise la protection d'un autre compte (trace dans le journal d'audit) ; sur le
serveur, `sudo python3 reinitialiser-acces.py --2fa identifiant`. Le dossier `secrets5688/` et sa clé `.master.key`, voisins de la base des
comptes, font partie de la sauvegarde : sans eux, les codes ne se vérifient plus et chaque compte protégé doit être réinitialisé.

AxiorHub Pilote prépare des travaux internes. Une sortie automatisée doit être contrôlée par un professionnel avant envoi,
signature, dépôt ou communication à un tiers.
