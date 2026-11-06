# Composants tiers et compatibilité — AxiorHub 5.6.94

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
| pypdfium2 (PDFium) | 4.30.0 | Apache-2.0 OR BSD-3-Clause (PDFium : BSD-3-Clause et Apache-2.0) | poste Windows : rendu des pages des aperçus Word sans Poppler (5.6.30) | compatible ; notices de PDFium incluses dans le paquet installé |
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
| Segno | 1.6.6 | BSD-3-Clause | QR code d’association d’un téléphone (5.6.74), copie locale sans dépendance | texte inclus dans `agent/_vendor/segno/LICENSE` ; modules repris sans modification (`__init__`, `consts`, `encoder`, `utils`, `writers`) |
| Row-Bot (extraits adaptés) | commit fca083b (25/10/2026) | Apache-2.0 | navigateur supervisé du poste : plan de page, contrôle des conséquences, classement des adresses (`agent/nav5673.py`) | compatible (Apache-2.0 vers AGPL-3.0) ; texte dans `LICENSES/Apache-2.0.txt`, mention dans `NOTICE-row-bot` |

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

## Interface (5.6.82)

Fichiers construits avec esbuild à partir des paquets npm officiels, hébergés localement dans `agent/static/`, sans ressource externe ni télémétrie.

- `carbon5682.js` : Carbon Web Components 2.66.0 (`@carbon/web-components`, IBM), Apache-2.0, et ses dépendances (Lit, BSD-3-Clause).
- `pdfjs5682.js`, `pdf.worker5682.js` : PDF.js 6.4.299 (`pdfjs-dist`, Mozilla), Apache-2.0.

## Interface Carbon (5.6.89)

`carbon5682.js`, `ui_icones5689.py`, `ui_jetons_carbon5689.py` et la police sont reconstruits par `python scripts/build-carbon.py` depuis `scripts/carbon5689/package-lock.json` (`npm ci --ignore-scripts`). Node et npm servent à construire, jamais à l'exécution ; aucun CDN n'est employé. Empreintes et versions : `scripts/carbon5689/manifeste.json`. Le paquet `carbon-components`, dont le support est terminé, n'est pas utilisé.

- `@carbon/web-components` 2.66.0, `@carbon/icons` 11.91.0, `@carbon/icon-helpers` 10.47.0, `@carbon/themes` 11.84.0 (IBM) : Apache-2.0 (`LICENSES/Apache-2.0.txt`).
- `lit` 3.3.3, `lit-html` 3.3.3, `lit-element` 4.2.2, `@lit/reactive-element` 2.1.2 : BSD-3-Clause (`LICENSES/BSD-3-Clause-Lit.txt`).
- `@floating-ui/dom` 1.8.0, `@floating-ui/core` 1.8.0, `@floating-ui/utils` 0.2.12 : MIT (`LICENSES/MIT-floating-ui.txt`).
- `plex-sans-400.woff2`, `plex-sans-600.woff2` : IBM Plex Sans 1.1.0 (`@ibm/plex-sans`), sous-ensemble latin, SIL Open Font License 1.1 (`LICENSES/OFL-1.1-IBM-Plex.txt`).

Télémétrie : `@carbon/web-components` dépend de `@ibm/telemetry-js`, qui ne s'exécute qu'au script d'installation npm. La construction passe `--ignore-scripts` et pose `CARBON_TELEMETRY_DISABLED=1` et `IBM_TELEMETRY_DISABLED=true` : aucune mesure n'est émise, et ce paquet n'entre pas dans les fichiers servis.

## Parcours navigateur (5.6.94, outil de développement)

`scripts/parcours-navigateur.py` pilote un navigateur avec Playwright pour Python 1.63.0 (Microsoft, Apache-2.0) : Chromium dans la recette GitHub, Microsoft Edge déjà installé sur un poste Windows. Playwright et ses navigateurs s'installent à part, sur le poste de développement ou dans la recette ; ils ne sont ni copiés dans l'arbre, ni livrés dans un paquet (`.deb`, Windows, image), ni installés sur le serveur du cabinet, et n'appartiennent pas au SBOM d'exécution. Voir `docs/PARCOURS-NAVIGATEUR.md`.

## Voix dans le navigateur (5.6.94)

Fichiers copiés sans modification, servis par AxiorHub depuis `agent/static/` ; aucun CDN ni Hugging Face n'est contacté à l'usage. Empreintes sha256 : `agent/voix5694.py` (`FICHIERS`), vérifiées par `tests/test_v5694_voix.py`. Les fichiers `LICENSE` et `NOTICE` d'origine sont conservés à côté des fichiers (`agent/static/brindille/`, `agent/static/alkove/`).

- **Brindille** (Eddy Bordi), modèle `edbrdi/brindille` de Hugging Face, révision `d393e3d5161937f2064bcfef33213749af4979f0` : `tts.js`, `tts.wasm`, `model/tts.alk`, `model/tts.params.bin`, `model/tts.json`, Apache-2.0 (`agent/static/brindille/LICENSE`, `NOTICE`). Synthèse vocale française produite dans le navigateur (Web Worker) ; une seule voix, aucun clonage.
  - **Voix Estelle** de Pocket TTS (Kyutai, https://github.com/kyutai-labs/pocket-tts) : Creative Commons Attribution 4.0 International (CC BY 4.0, https://creativecommons.org/licenses/by/4.0/). L'enregistrement d'Estelle (https://huggingface.co/kyutai/tts-voices) est publié par Kyutai sous CC0. Attribution affichée dans Paramètres › Voix.
  - **Dictionnaire de prononciation** `model/fr.dict.bin`, dérivé du dictionnaire français du **Montreal Forced Aligner** (https://github.com/MontrealCorpusTools/mfa-models) : CC BY 4.0. Modifications par l'auteur de Brindille : prononciations converties vers les phonèmes du modèle, réduites à 19 662 mots.
  - **Lex** (`model/lex.alk`, https://huggingface.co/alkove-ai/alkove-lex-1-preview, révision `9365b7c6aa1555a561548e5198d2becf7f7ffe00`) : Apache-2.0.
  - Réseau et synthétiseur harmonique + bruit d'après **AuroraTTS-100K** (AuroraAI-Research, https://huggingface.co/AuroraAI-Research/AuroraTTS-100K) : Apache-2.0.
  - Moteur d'inférence Alkove compilé dans `tts.wasm` : Apache-2.0 ; ses tables de texte dérivent de l'Unicode Character Database 16.0 sous **Unicode License v3** (texte complet dans `agent/static/brindille/NOTICE`).
- **Alkove** 0.4.2 (paquet npm `alkove`, Eddy Bordi, https://github.com/alkove-ai/alkove) : `dist/index.js`, `api-63K47HT3.js`, `chunk-FC4ZSMUX.js`, `chunk-IMUNEOSF.js`, `chunk-MZOMHWYI.js`, `alkove-worker.js`, `alkove-worklet.js`, `alkove-engine.wasm`, Apache-2.0 (`agent/static/alkove/LICENSE`, `NOTICE`). Version 0.x figée : l'API employée est consignée dans `agent/voix5694.py` (`ALKOVE_API`). Traitement du micro dans le navigateur (dictée, conversation vocale) ; aucune empreinte vocale n'est apprise ni conservée.
  - Modèle **Focus** `alkove-focus-1-preview.alk` (https://huggingface.co/alkove-ai/alkove-focus-1-preview, révision `8e91ef608366cf503fbd8aaa44bc2000dce899ed`) : Apache-2.0 ; dérivé de **GTCRN** (Rong Xiaobin, https://github.com/Xiaobin-Rong/gtcrn), licence MIT (texte dans `agent/static/alkove/NOTICE`).
  - Modèle **Cue** `alkove-cue-1-preview.alk` (https://huggingface.co/alkove-ai/alkove-cue-1-preview, révision `0e1f30380bdfb3201b5d4fd7d9e4197f71f11a60`) : Apache-2.0.
  - Les tables de texte d'alkove-core dérivent de l'Unicode Character Database 16.0 : **Unicode License v3** (texte dans `agent/static/alkove/NOTICE`).
  - Les deux modèles ont été téléchargés une fois, aux révisions et empreintes figées par le manifeste du paquet, pour être copiés dans l'arbre ; Alkove vérifie de nouveau leur sha256 dans le navigateur.

Compatibilité : Apache-2.0, MIT et Unicode License v3 sont compatibles avec l'AGPL-3.0-or-later pour une distribution conjointe ; CC BY 4.0 (voix, dictionnaire) impose l'attribution, l'indication des modifications et le lien vers la licence, assurés ici et sur la page Voix. La marque Alkove n'est pas utilisée pour désigner AxiorHub.
