# Composants tiers et compatibilité — AxiorHub 5.6.12

Date de l’audit direct : 6 octobre 2026. Identifiant de licence du code
AxiorHub : `AGPL-3.0-or-later`.

Ce relevé couvre les dépendances déclarées dans l’image Docker principale et le
pont vocal optionnel. Les paquets transitifs exacts de l’image de base doivent
être figés et rescannés à chaque publication d’image.

| Composant | Version déclarée | Licence principale | Usage | Conclusion de l’audit direct |
|---|---:|---|---|---|
| Python | image `python:3.12-slim` | PSF-2.0 | environnement | compatible |
| Waitress | 3.0.2 | ZPL-2.1 | serveur WSGI | compatible |
| cryptography | 44.0.0 | Apache-2.0 OR BSD-3-Clause | coffre chiffré des connexions et notifications mobiles (VAPID), image Docker | compatible |
| HTMX | 2.0.8 | 0BSD | formulaires dynamiques, copie locale | texte inclus dans `agent/static/HTMX-LICENSE.txt` |
| pypdf | 4.3.1 | BSD-3-Clause | lecture, tampon et assemblage des PDF de pièces (5.1.0), copie locale sans dépendance | texte inclus dans `agent/_vendor/pypdf/LICENSE` ; imports rendus relatifs, code non modifié par ailleurs |
| Poppler / `poppler-utils` | paquet Debian | GPL-2.0-or-later et licences de fichiers | processus séparé d’extraction PDF | redistribuable avec notices et sources correspondantes selon le paquet |
| Tesseract OCR + données françaises | paquet Debian | Apache-2.0 | processus séparé OCR | compatible |
| LibreOffice Writer | paquet Debian | MPL-2.0 et licences de fichiers | processus séparé DOCX/PDF | compatible ; conserver les notices du paquet |
| curl | paquet Debian | curl | contrôle de santé | compatible |
| tini | paquet Debian | MIT | init du conteneur | compatible |
| eSpeak NG | paquet Debian | GPL-3.0-or-later et notices des données vocales | synthèse française locale, processus distinct | conserver licences et sources du paquet ; versions exactes à résoudre au build |
| Minisign | paquet Debian | ISC | vérification des archives de mise à jour | compatible |
| FastAPI | 0.116.1 | MIT | pont vocal optionnel | compatible |
| gradio_client | 1.13.3 | Apache-2.0 | pont vocal optionnel | compatible |
| python-multipart | 0.0.20 | Apache-2.0 | pont vocal optionnel | compatible |
| Uvicorn | 0.35.0 | BSD-3-Clause | pont vocal optionnel | compatible |

Les modèles IA, services MCP, plugins Lawve, services Nextcloud, Roundcube,
Open WebUI et sites AxiorHub connectés ne sont pas incorporés au code par le
seul fait d’être configurés. Leurs licences, conditions d’utilisation et droits
sur les données doivent être contrôlés séparément avant distribution ou usage.

## Contrôles avant publication

1. résoudre l’image de base Docker en digest immuable ;
2. construire l’image publique depuis une copie nettoyée ;
3. générer un SBOM complet de l’image construite avec Syft ou un outil équivalent ;
4. scanner licences et vulnérabilités de toutes les dépendances transitives ;
5. joindre les textes et mentions exigés par les paquets effectivement embarqués ;
6. comparer ce résultat à `SBOM.cdx.json` et bloquer la publication en cas d’écart.

Ce fichier est un inventaire technique de conformité, pas un avis juridique.

## Dépendance cryptographique (5.6.7)

- cryptography (Apache-2.0 ou BSD) : coffre local Fernet et notifications web. Requise pour créer ou lire un secret chiffré ; aucun repli en clair en cas d’absence.

## Services vocaux optionnels (5.6.8)

Le code de Kokoro-FastAPI, les poids Kokoro, les voix Chatterbox et ElevenLabs ne
sont pas incorporés dans cette archive. L'extension `docker-compose.voice568.yml`
référence séparément l'image CPU Kokoro-FastAPI `v0.1.4` du projet
https://github.com/remsky/Kokoro-FastAPI. Avant sa distribution, résoudre l'image
en digest, vérifier les licences du serveur, des poids, des voix et des dépendances
transitives, puis produire son SBOM. Cette image n'a pas été construite ou exécutée
dans la recette de livraison. Le serveur Chatterbox n'est pas fourni : l'adaptateur
requiert une API locale compatible `/v1/audio/speech`. ElevenLabs est un service
externe soumis au compte, à la tarification et aux conditions du fournisseur.

Le connecteur Google Calendar utilise HTTPS/OAuth et la bibliothèque standard
Python ; aucun SDK Google ni code de service Google n'est incorporé au paquet.
L'utilisation de l'API reste soumise aux autorisations du compte et aux conditions
du fournisseur. Nextcloud et les autres serveurs CalDAV restent des services
séparés. Les migrations 0568/0569 n'ajoutent aucune dépendance Python tierce.
