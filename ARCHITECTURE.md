# Architecture

## Vue d'ensemble

AxiorHub Pilote est une application Python (bibliothèque standard, WSGI) organisée autour d'une **file de travail** : la
surveillance de la messagerie et les tâches planifiées déposent des travaux, des workers les exécutent, l'interface web
montre l'état et recueille les décisions de l'avocat.

```
 IMAP (IDLE + relève) ──► surveillance ──┐
 Tâches planifiées (routines, style) ────┼──► file de travail (SQLite) ──► workers ──► brouillons IMAP
 Instructions depuis l'interface ────────┘                                   │           projets Nextcloud
                                                                             │           agenda / tâches CalDAV
                                                                             ▼
                                                                  routeur de modèles IA
                                                     Ollama local │ API externe (pseudonymisation obligatoire)
```

## Composants

| Dossier / module | Rôle |
|---|---|
| `web.py` | point d'entrée : interface (`ui`), `worker`, `watch`, commandes d'administration |
| `standalone.py`, `agent/standalone_auth.py` | distribution autonome : serveur Waitress, comptes, rôles, sessions |
| `agent/setup560.py` | assistant d'installation (cabinet, messagerie, Nextcloud, IA, mode) |
| `agent/desk.py` | file de travail, verrous, reprise |
| `agent/mailbox.py` | IMAP : lecture, dépôt de brouillons, surveillance |
| `agent/dav.py` | WebDAV (dossiers, pièces) et CalDAV (agenda, tâches) |
| `agent/engine.py`, `agent/intelligence.py` | analyse des courriels, rattachement aux dossiers, réponses |
| `agent/document_projects.py`, `agent/docrequest520.py` | projets de documents à partir des modèles (`templates/`) |
| `agent/pieces510.py` | pièces, tampons, bordereaux (pypdf intégré) |
| `agent/learning410.py`, `agent/style550.py` | règles métier et style du cabinet, appris puis validés |
| `agent/model.py`, `agent/hybrid400.py`, `agent/ia540.py` | routage des modèles locaux et externes |
| `agent/pseudo540.py` | pseudonymisation réversible avant tout appel externe |
| `agent/web440.py`, `agent/shell501.py`, `agent/static/` | interface : routes, menu, styles (`app520.css` regroupe les feuilles) |
| `installer.py`, `upgrade.py`, `install.sh` | installation système et mise à jour vérifiée (manifeste SHA-256, retour arrière) |
| `deploy/vps/` | ensemble Docker du VPS, hôtes virtuels Apache, scripts d'installation et de sauvegarde |
| `docker/` | image AxiorHub Pilote et amorçage de la configuration |
| `integrations/` | Open WebUI, Roundcube, pont vocal, llama.cpp (facultatifs) |
| `tests/` | tests unitaires et d'intégration, sans réseau |

## Données

- `config.json` : réglages (sans secret) ; `secrets/` : mots de passe et clés (fichiers 600).
- `state.sqlite3` : courriels traités, dossiers, mémoire, journaux ; `desk.sqlite3` : file de travail, règles, style,
  pilotage.
- `users.sqlite3` (distribution autonome) : comptes, rôles, journal des comptes.

## Principes

1. **Rien ne part sans l'avocat** : brouillons et projets uniquement ; l'envoi, la signature et le dépôt restent humains.
2. **Traçabilité** : chaque production garde ses sources et l'historique des décisions.
3. **Réversibilité** : mises à jour vérifiées par manifeste, retour arrière possible, règles apprises révocables.
4. **Local d'abord** : l'IA externe est une option explicite et pseudonymisée.
