# Conformité 5.6.38 — corrective de l'affichage dans Nextcloud

Version de référence : 5.6.37. Signalement de l'avocat du 12 octobre 2026 : dans l'application Nextcloud 0.4.0, le cadre affiche
« courriel.… n'autorise pas la connexion ».

| Signalement | État 5.6.38 |
|---|---|
| Cadre refusé par AxiorHub dans Nextcloud | **Réalisé.** Cause : AxiorHub et Nextcloud sur le même domaine ; le navigateur joint l'identifiant Basic d'AxiorHub qu'il a mémorisé ; `authenticate` n'examinait la session Nextcloud (cookie `axh_nc`) qu'en l'absence d'en-tête `Authorization` et la page partait avec `X-Frame-Options: DENY` et `frame-ancestors 'none'`. Correctif : session Nextcloud vérifiée en premier dès qu'un cookie est présent ; redirection de l'adresse à usage unique sans `X-Frame-Options`, `frame-ancestors` limité au site Nextcloud de la clé. Une connexion par mot de passe sans session Nextcloud valide garde `DENY`. Test : `tests/test_v5637.py`, `SessionWithRememberedLogin`. |

Aucun réglage Apache ni modification de l'application Nextcloud n'est nécessaire, sauf si le serveur web ajoute lui-même un en-tête
`Content-Security-Policy` contenant `frame-ancestors` aux pages d'AxiorHub (à retirer pour `/agent-courriel/`).
