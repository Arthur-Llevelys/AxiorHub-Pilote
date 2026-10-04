# Composants tiers et compatibilité — AxiorHub 5.6.2

Date de l’audit direct : 1er octobre 2026. Identifiant de licence du code
AxiorHub : `AGPL-3.0-or-later`.

Ce relevé couvre les dépendances déclarées dans l’image Docker principale et le
pont vocal optionnel. Les paquets transitifs exacts de l’image de base doivent
être figés et rescannés à chaque publication d’image.

| Composant | Version déclarée | Licence principale | Usage | Conclusion de l’audit direct |
|---|---:|---|---|---|
| Python | image `python:3.12-slim` | PSF-2.0 | environnement | compatible |
| Waitress | 3.0.2 | ZPL-2.1 | serveur WSGI | compatible |
| HTMX | 2.0.8 | 0BSD | formulaires dynamiques, copie locale | texte inclus dans `agent/static/HTMX-LICENSE.txt` |
| pypdf | 4.3.1 | BSD-3-Clause | lecture, tampon et assemblage des PDF de pièces (5.1.0), copie locale sans dépendance | texte inclus dans `agent/_vendor/pypdf/LICENSE` ; imports rendus relatifs, code non modifié par ailleurs |
| Poppler / `poppler-utils` | paquet Debian | GPL-2.0-or-later et licences de fichiers | processus séparé d’extraction PDF | redistribuable avec notices et sources correspondantes selon le paquet |
| Tesseract OCR + données françaises | paquet Debian | Apache-2.0 | processus séparé OCR | compatible |
| LibreOffice Writer | paquet Debian | MPL-2.0 et licences de fichiers | processus séparé DOCX/PDF | compatible ; conserver les notices du paquet |
| curl | paquet Debian | curl | contrôle de santé | compatible |
| tini | paquet Debian | MIT | init du conteneur | compatible |
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

## Dépendance facultative (5.0.0)

- cryptography (Apache-2.0 ou BSD) : chiffrement des signaux de notification web ; absente, les notifications sont simplement indisponibles.
