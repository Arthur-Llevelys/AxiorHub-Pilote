# Tableur, PDF et présentations dans AxiorHub (GridCraft, PdfCraft, DeckCraft)

Notice courte pour l'avocat et l'assistant. Version 5.6.110.

## Ce que c'est

AxiorHub est fourni avec trois éditeurs libres, en plus de WordCraft pour les documents Word :

| Éditeur | Ouvre | Permet | Exports |
|---|---|---|---|
| GridCraft 0.19.1 | classeur Excel (`.xlsx`) | modifier les cellules, formules recalculées | PDF, CSV |
| PdfCraft 0.21.1 | PDF | lire, annoter, tourner, réorganiser les pages | images, texte |
| DeckCraft 0.18.1 | présentation PowerPoint (`.pptx`) | modifier les diapositives et leur texte | PDF, images |

Ce sont des **versions d'essai** (« alpha » selon leurs auteurs). Ils tournent dans le navigateur, servis par AxiorHub : aucune donnée ne
sort, aucun compte n'est demandé, rien n'est téléchargé d'Internet.

## Comment s'en servir

1. Ouvrez le document : aperçu d'un document du dossier, page Fichiers (sélectionnez le fichier) ou Produire.
2. Cliquez sur « Modifier dans GridCraft (version d'essai) » (ou PdfCraft, DeckCraft), puis « Ouvrir dans GridCraft ».
3. Modifiez, puis enregistrez dans l'éditeur (Fichier › Enregistrer, ou Ctrl+S).
4. AxiorHub compare la version reçue à l'original et affiche le résultat (pages, feuilles ou diapositives ; texte modifié ; pertes).
5. « Conserver cette version » crée un **nouveau fichier à côté de l'original**, nommé par exemple
   « Tableau des créances DURAND (modifié dans GridCraft 24-11-2026 14h05).xlsx ». L'original n'est jamais remplacé.
   « Abandonner cette version » ne laisse rien dans le dossier.

Un export (PDF d'un classeur, images d'une présentation) devient aussi un fichier distinct à côté du document, par exemple
« … (export GridCraft 24-11-2026 14h05).pdf ».

## Ce qui ne marche pas, et pourquoi

- La comparaison porte sur la structure et le texte ; la mise en forme, les images et les objets ne sont pas comparés : relisez la version
  gardée avant de l'envoyer.
- Un fichier de plus de 20 Mo n'est pas ouvert ni repris.
- Un fichier avec des macros ou des objets actifs est refusé.
- « Enregistrer sous » dans un autre format que celui du document (par exemple `.deckcraft`) n'est pas repris : AxiorHub le dit et ne
  garde rien.
- Les commandes de l'IA ne sont pas proposées dans ces éditeurs.
- Il faut un navigateur récent (WebGL 2 ou WebGPU). Sinon, l'éditeur le dit et vous gardez les autres moyens (télécharger, Word ou
  LibreOffice sur le poste, serveur de documents).

## Pour l'administrateur

Paramètres › Documents, section « Éditeurs intégrés : tableur, PDF et présentations » : une case par éditeur, cochée par défaut. La
décocher retire les boutons ; les documents restent dans les dossiers, intacts. Licences : `THIRD_PARTY_NOTICES.md`.
