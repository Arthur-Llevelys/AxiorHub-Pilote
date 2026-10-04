# Sécurité

## Signaler une vulnérabilité

Utilisez le **signalement privé de vulnérabilité** du dépôt GitHub (onglet *Security → Report a vulnerability*).
N'ouvrez pas de ticket public contenant un secret, une donnée de client ou le détail d'une faille exploitable.

## Ce qui ne doit jamais être publié

`.env`, `data/`, `deploy/vps/data/`, les bases SQLite, les fichiers `*.secret`, les archives de dossiers, les journaux
contenant des données de clients, les captures d'écran d'une installation réelle. `scripts/privacy-scan.py` bloque les
cas courants ; il ne dispense pas d'une relecture.

## Principes

- Rien ne part sans validation humaine : AxiorHub Pilote dépose des brouillons et des projets, il n'envoie ni ne dépose rien.
- Une IA non locale ne reçoit que des textes pseudonymisés.
- Les services n'écoutent que sur `127.0.0.1` ; le frontal HTTPS est seul exposé.

AxiorHub Pilote prépare des travaux internes. Une sortie automatisée doit être contrôlée par un professionnel avant envoi,
signature, dépôt ou communication à un tiers.
