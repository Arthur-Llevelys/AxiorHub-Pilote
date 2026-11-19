# WordCraft 0.18.1 dans AxiorHub — audit, mesure de fidélité et décision (5.6.108, lot W)

Mesure du 18/11/2026 sur le poste de développement Windows 11, Chromium de Playwright sans tête (rendu WebGL 2 logiciel SwiftShader),
script `scripts/fidelite_wordcraft56108.py` (rejouable ; `--reprendre <dossier>` refait seulement la comparaison). Données : trames et actes
produits par le moteur existant, modèles Word livrés avec AxiorHub, fichiers fictifs. Aucune donnée réelle, aucun réseau.

## 1. Archive

`wordcraft-web-0.18.1.zip` (22 536 015 octets, sha256 `3daa7c2d1f209a7eeb2527fcefa3fe31e875848fb1970d68f54edae55fe0bc9f`, conforme au
`SHA256SUMS.txt` signé de la publication), extraite dans un dossier neuf, copiée sans modification dans `agent/static/wordcraft-0.18.1/`
(26 fichiers). Licence du code : MIT ou Apache-2.0 ; polices OFL-1.1 ; dictionnaires et motifs de césure sous licences permissives
(détail dans `THIRD_PARTY_NOTICES.md`). Seuls trois fichiers sont servis : la colle `wordcraft-web-bc9cdbc0b42827ee.js` (182 456 octets),
l'extrait `snippets/eframe-d31a8450ea12dfc1/inline0.js` (280 octets) et le module `wordcraft-web-bc9cdbc0b42827ee_bg.wasm` (60 994 261 octets).
`index.html` d'origine (script en ligne, refusé par notre CSP) n'est pas servi : AxiorHub fournit sa propre page de cadre.

## 2. Audit réseau (avant intégration)

| Point examiné | Constat |
|---|---|
| Colle JavaScript : fonctions offertes au WebAssembly | aucune liaison `fetch`, `XMLHttpRequest`, `WebSocket`, `EventSource`, `sendBeacon`, `importScripts`, `RTCPeerConnection` ; le seul `fetch` est celui d'`init`, à l'adresse que nous passons (fichier .wasm d'AxiorHub) |
| Sorties possibles | `window.open` (liens cliqués : aide, site de l'éditeur, Discord, liens du document) et liens de téléchargement (`a.href` + `blob:`) en mode sans hôte ; en mode hôte, les enregistrements arrivent par message, sans téléchargement |
| Chaînes du module WebAssembly | adresses de documentation et de licences seulement (github.com, getartcraft.com, discord.gg, schémas XML) ; « Update check is not supported in the web browser » : pas de vérification de mises à jour en version web ; aucune télémétrie trouvée |
| Polices, scripts, CDN | aucun : polices et dictionnaires sont dans le .wasm |
| `HOSTING.md`, `_headers`, `.htaccess` | en-têtes de cache et types MIME seulement ; aucun service distant |
| Mesure par un vrai navigateur (§ 4) | **0 requête hors du serveur local** sur 257 ouvertures et 514 enregistrements ; **0 violation de CSP** ; **0 appel à `window.open`** |

Mesures prises : `window.open` est remplacé dans le cadre avant le démarrage (aucun lien externe ne s'ouvre depuis l'éditeur) ; le cadre
(`/editeur/wordcraft/cadre?host=parent`, même origine) a sa propre CSP `default-src 'none'; script-src 'self' 'wasm-unsafe-eval';
connect-src 'self'; … frame-ancestors 'self'`. **La CSP d'AxiorHub est inchangée** : WordCraft fonctionne sous `connect-src 'self'` et
`script-src 'self' 'wasm-unsafe-eval'` sans autre exception (mesuré). Aucun appel externe non désactivable : l'intégration a été poursuivie.

## 3. Poids et temps

- Poids servi : 61 176 997 octets (61,2 Mo) non compressés — AxiorHub ne compresse pas ses réponses ; le module est servi avec
  `?v=0.18.1`, donc gardé par le navigateur (`private, max-age=31536000, immutable`) après la première ouverture. Sur le réseau du cabinet
  (version serveur), compter la durée de téléchargement de 61 Mo la première fois.
- Démarrage (poste, boucle locale, cache vide) : application prête en 0,19 s après le chargement de la page (`wordcraft:ready`).
- Ouverture d'un document : 1,0 s en moyenne, attente de stabilité de l'outil comprise (256 fichiers) ; un fichier a atteint la limite
  d'attente de 90 s de l'outil (`trame-pacte_associes`, état identique au document précédent), enregistrement correct.
- Mesure complète (257 fichiers, deux enregistrements chacun) : 8 min 28 s.
- Navigateurs : seul Chromium a été mesuré (WebGL 2 logiciel). Firefox, Safari et Edge n'ont pas été essayés ; un navigateur sans WebGL 2
  ni WebGPU reçoit un message et la proposition de Word ou du serveur de documents.

## 4. Mesure de fidélité (aller-retour)

Jeu : **257 fichiers** — 111 trames de la bibliothèque (`template_docx`, 87 existantes et 24 nouvelles du lot B), 111 actes produits
par le moteur à partir de ces trames (`build_docx`, mentions « à compléter »), 15 modèles Word livrés (honoraires, factures, conclusions,
fixture), 20 fichiers fictifs (une caractéristique chacun, plus un acte « complet » et un acte simple). Pour chacun : ouverture puis
enregistrement sans modification (rt0), puis « [essai] » tapé au début et nouvel enregistrement (rt1) ; comparaison par
`wordcraft56108.analyser` / `controler` (le contrôle même qui est montré à l'avocat), puis comparaison fine des propriétés de chaque
paragraphe et de chaque caractère (ordre des propriétés ignoré) et des définitions de styles.

Résultat global : rt0 — texte identique sur 254 fichiers sur 257 ; rt1 — exactement un paragraphe modifié sur 253 fichiers sur 257 (les
écarts sont expliqués ci-dessous) ; propriétés de paragraphe et de caractère identiques sur 254 fichiers (les 3 autres ont un texte modifié,
voir ci-dessous) ; **aucune perte sur les 111 trames, les 111 actes et les 15 modèles**.

| Caractéristique | Fichiers qui l'ont | Perte en rt0 | Perte en rt1 | État retenu pour la garde |
|---|---:|---:|---:|---|
| Texte, paragraphes, sections, mise en page, alignements | 257 | 0 | 0 | conservé |
| Gras / italique / souligné (caractères) | 234 / 113 / 3 | 0 | 0 | conservé |
| Styles utilisés | 257 | 0 | 0 | conservé |
| Numérotation (paragraphes et format des niveaux employés) | 2 | 0 | 0 | conservé (WordCraft ajoute les niveaux non définis : pas une perte) |
| Tableaux / cellules | 9 / 9 | 0 | 0 | conservé |
| En-têtes et pieds (texte) ; complexes (champ PAGE…) | 17 ; 16 | 0 | 0 | conservé |
| Notes de bas de page | 2 | 0 | 0 | conservé |
| Modifications suivies (insertion et suppression) | 2 | 0 | 0 | conservé |
| Commentaires | 2 | 0 | 0 | conservé |
| Champs (REF, PAGE) | 2 | 0 | 0 | conservé |
| **Champs de date (DATE, TIME…)** | 2 | texte changé | texte changé | **abîmé** : recalculés à l'ouverture avec des mois en anglais (« 18 November 2026 » au lieu de « 18 novembre 2026 ») |
| Contrôles de contenu | 2 | 0 | 0 | conservé |
| Images, zones de texte | 3 / 2 | 0 | 0 | conservé |
| Signets, liens hypertexte, sauts de page | 3 / 3 / 8 | 0 | 0 | conservé |
| Protection du document | 1 | 0 | — | conservée ; le document protégé ne se modifie pas (WordCraft refuse la saisie) |
| **Objets incorporés (fragment importé `altChunk`)** | 1 | perte | perte | **abîmé** : contenu converti en texte du document, objet d'origine retiré |
| **Macros (`vbaProject.bin`)** | 1 | perte | perte | **perdu** |
| Polices liées au thème (modèles faits avec Word) | 15 | 15 | 15 | modifié sans effet visible : la référence au thème est remplacée par le nom de la même police (Calibri, Cambria) ; rendu inchangé, lien au thème perdu |
| Parties inconnues de l'archive (customXml, stylesWithEffects, webSettings…) | 15 | 0 | 0 | conservées |

WordCraft ajoute à l'enregistrement des parties absentes (thème, réglages, propriétés du document, commentaires étendus) : ce n'est pas une
perte. Ordre des propriétés réécrit selon le schéma : sans effet.

## 5. Garde et contrôle qui en découlent

- **Garde avant ouverture** (`wordcraft56108.FIDELITE`, `garde`) : « risque » (avertissement, bouton « Ouvrir » désactivé jusqu'à une case
  « J'ai compris », Word et le serveur de documents proposés en premier) pour les **champs de date**, les **objets incorporés** et les
  **macros** ; « information » pour les autres caractéristiques présentes (suivi, commentaires, champs, notes, numérotation, sections,
  tableaux, contrôles de contenu, images, zones de texte, protection, polices du thème). Aucune ouverture silencieuse d'un document à risque.
- **Contrôle après enregistrement** (`controler`) : chaque baisse de compte (paragraphes, tableaux, notes, suivi, commentaires, champs,
  numérotation, styles, sections, objets, images, en-têtes, mise en forme, macros…), toute partie disparue, tout changement de mise en page,
  d'alignement ou de format de numérotation, et le texte paragraphe par paragraphe sont montrés **avant** « Garder cette version ». La
  perte « polices du thème » est signalée comme modification sans effet visible.

## 6. Décision

La fidélité est bonne sur les actes courants (trames, actes produits, modèles livrés : aucune perte de structure ni de texte ; seule la
liaison des polices au thème, sans effet visible, change dans les modèles faits avec Word). Conformément à la feuille de lot, WordCraft est
**activé par défaut**, présenté partout comme « version d'essai », avec la garde, le contrôle et la création d'une nouvelle version à côté
de l'original (jamais d'écrasement) ; l'administrateur peut le désactiver (Réglages de l'atelier › « Éditeur intégré sans serveur de
documents »). Limites assumées et dites à l'écran : champs de date recalculés en anglais, objets incorporés et macros, mesure faite dans
Chromium seulement, logiciel « alpha » selon ses auteurs (une édition réelle et longue par un avocat n'a pas été mesurée).
