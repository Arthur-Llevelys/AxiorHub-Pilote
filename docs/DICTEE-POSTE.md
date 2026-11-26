# La dictée sur votre poste (5.6.110)

Cette notice explique comment installer et utiliser la dictée d’AxiorHub Pilote sur un poste Windows 11 ou Ubuntu 24.04.

## Ce que fait la dictée

Vous parlez, AxiorHub écrit. Le bouton du micro apparaît à côté des zones de texte : instruction au Pilote, réponse à un courriel,
correction d’un projet.

La transcription se fait **sur votre poste**. Aucun service extérieur n’écoute ni ne reçoit votre voix.

À côté du micro, une liste propose deux modes. Choisissez toujours **« Confidentiel — Vocal local »** : c’est la dictée sur votre
poste. L’autre mode, « Rapide », passe par le navigateur, qui peut envoyer le son à son fournisseur : ne l’utilisez jamais pour une
donnée d’un client.

## Ce qui reste sur le poste

- L’enregistrement de votre voix n’est jamais conservé. Il est effacé dès que le texte est prêt, même en cas d’erreur.
- Le texte dicté n’est écrit dans aucun journal.
- Seul le **modèle de reconnaissance** est téléchargé, une seule fois, quand l’administrateur le demande. Ce téléchargement
  n’envoie aucune donnée du cabinet.

## Avant de commencer

- Il faut le compte **administrateur** d’AxiorHub sur ce poste.
- Il faut une connexion à Internet le temps du téléchargement du modèle (60 à 574 Mo selon le modèle). Sans réseau, voir
  [Poste sans réseau](#poste-sans-réseau--déposer-le-modèle-à-la-main).
- Le moteur de dictée est fourni avec l’application Windows et avec le paquet Ubuntu du poste. Il n’y a rien d’autre à installer.

## Choisir le modèle

| Modèle | Taille | Pour qui | Durée pour une phrase de 14 secondes (poste récent) |
|---|---|---|---|
| Rapide | 60 Mo | poste modeste | environ 2,4 s |
| Équilibré | 190 Mo | conseillé | environ 5,4 s |
| Précis | 574 Mo | poste puissant ; lent sinon | environ 19 s |

Ces durées sont des ordres de grandeur, mesurés sur un poste récent. Sur un poste plus ancien, comptez davantage. En cas de doute,
choisissez **Équilibré**.

## Installer sous Windows 11

1. Ouvrez **AxiorHub Pilote**, puis **Paramètres › Voix**.
2. Repérez la carte **Dictée sur ce poste**, en haut de la page.
3. Choisissez un modèle (par exemple **Équilibré**) et la langue de la dictée.
4. Cliquez sur **Installer la dictée sur ce poste**. La progression du téléchargement s’affiche.
5. À la fin, la carte indique « Dictée sur ce poste : active ».
6. Cliquez sur **Vérifier le moteur**. Le message indique que le moteur répond, et en combien de temps.

Si la carte parle du **composant Microsoft Visual C++**, voir [Pannes courantes](#pannes-courantes).

## Installer sous Ubuntu 24.04

1. Installez ou mettez à jour le paquet du poste comme d’habitude :
   `sudo apt install ./axiorhub-pilote-poste_<version>_amd64.deb`.
   `apt` installe aussi la bibliothèque `libgomp1`, nécessaire au moteur.
2. Ouvrez **AxiorHub Pilote**, puis **Paramètres › Voix**.
3. Dans la carte **Dictée sur ce poste**, choisissez un modèle et la langue.
4. Cliquez sur **Installer la dictée sur ce poste** et attendez la fin du téléchargement.
5. Cliquez sur **Vérifier le moteur**.

## Essayer

1. Dans la carte **Dictée sur ce poste**, descendez jusqu’à la **zone d’essai**.
2. Choisissez « Confidentiel — Vocal local », cliquez sur le micro et parlez quelques phrases.
3. Cliquez de nouveau sur le micro (« Arrêter la dictée »), puis lisez le texte obtenu.

Le texte de la zone d’essai n’est ni enregistré ni envoyé ailleurs. Si le résultat est trop lent, prenez un modèle plus rapide. S’il
est trop approximatif, prenez un modèle plus précis.

## Changer de modèle, retirer, désactiver

- **Changer de modèle** : choisissez l’autre modèle, puis **Installer la dictée sur ce poste**. L’ancien modèle reste sur le disque
  tant que vous ne le retirez pas.
- **Retirer le modèle** : libère la place sur le disque. Si c’est le modèle choisi, la dictée sur ce poste est désactivée.
- **Désactiver** : arrête la dictée sur ce poste sans rien effacer. Pour la réactiver, cliquez de nouveau sur **Installer la dictée
  sur ce poste** : rien n’est retéléchargé si le modèle est encore là.

## Place sur le disque

| | Moteur (fourni) | Modèles téléchargés |
|---|---|---|
| Windows 11 | environ 12 Mo, dans le dossier `dictee` de l’application (`%LOCALAPPDATA%\Programs\AxiorHub Pilote\dictee`) | `%LOCALAPPDATA%\AxiorHub Pilote\donnees\state\dictee56110\modeles` |
| Ubuntu 24.04 | environ 21 Mo, dans `/usr/lib/axiorhub-pilote/dictee` | `~/.local/share/axiorhub-pilote/state/dictee56110/modeles` |

Pendant une dictée, l’enregistrement passe quelques secondes dans le dossier voisin `travail`, puis il est effacé.

## Poste sans réseau : déposer le modèle à la main

Si le poste n’a pas accès à Internet, vous pouvez apporter le fichier du modèle depuis un autre ordinateur.

1. Sur un ordinateur relié à Internet, téléchargez **un** de ces fichiers (adresse complète, version figée) :

   | Modèle | Fichier | Taille exacte (octets) | Empreinte SHA-256 |
   |---|---|---|---|
   | Rapide | `ggml-base-q5_1.bin` | 59707625 | `422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898` |
   | Équilibré | `ggml-small-q5_1.bin` | 190085487 | `ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb` |
   | Précis | `ggml-large-v3-turbo-q5_0.bin` | 574041195 | `394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2` |

   Adresse : `https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/<fichier>`
   (remplacez `<fichier>` par le nom du tableau).
2. Vérifiez l’empreinte. Elle doit être identique à celle du tableau :
   - Windows (PowerShell) : `Get-FileHash .\ggml-small-q5_1.bin -Algorithm SHA256`
   - Ubuntu : `sha256sum ggml-small-q5_1.bin`
3. Copiez le fichier, **sans le renommer**, dans le dossier des modèles du poste (voir [Place sur le disque](#place-sur-le-disque)).
   Créez les dossiers `dictee56110` et `modeles` s’ils n’existent pas.
4. Dans **Paramètres › Voix**, choisissez ce modèle et cliquez sur **Installer la dictée sur ce poste**. AxiorHub trouve le fichier,
   contrôle sa taille et son empreinte, puis active la dictée sans rien télécharger.
5. Cliquez sur **Vérifier le moteur**.

Un fichier dont l’empreinte ne correspond pas est refusé : la carte indique « empreinte fausse : fichier non utilisé ». Retirez-le
(**Retirer le modèle**) et recommencez avec un fichier intact.

## Pannes courantes

| Ce que vous voyez | Pourquoi | Que faire |
|---|---|---|
| « Composant Microsoft Visual C++ absent de ce poste » (Windows) | Le moteur a besoin d’un composant gratuit de Microsoft, souvent déjà présent. Il n’est pas fourni avec AxiorHub. | Installez-le depuis le site de Microsoft : <https://aka.ms/vs/17/release/vc_redist.x64.exe>, puis cliquez sur **Vérifier le moteur**. Il faut les droits d’administrateur de Windows. |
| « Bibliothèque du système absente pour la dictée (libgomp1) » (Ubuntu) | La bibliothèque `libgomp1` manque. | `sudo apt-get install -y libgomp1`, puis **Vérifier le moteur**. |
| « empreinte fausse : fichier non utilisé » | Le fichier du modèle, déposé à la main ou modifié, n’est pas celui attendu. | **Retirer le modèle**, puis **Installer la dictée sur ce poste**. |
| « Encore 30 secondes : l’enregistrement s’arrêtera à 10 minutes. » | Une dictée dure dix minutes au plus. | Terminez votre phrase, arrêtez, puis reprenez une nouvelle dictée. |
| « Moteur de dictée : absent » | Serveur ou image Docker (la dictée s’installe sur les postes), application lancée hors paquet, ou version construite sans le moteur. | La carte précise la raison. Sur un poste, installez une version de l’application qui contient le moteur. |
| « Téléchargement impossible » ou « Fichier reçu différent du fichier attendu » | Réseau coupé, pare-feu, ou fichier abîmé en route. Rien n’a été gardé. | Réessayez plus tard, ou déposez le modèle à la main. |
| « Place insuffisante sur le disque » | Le disque est trop plein pour ce modèle. | Libérez de la place ou choisissez un modèle plus petit. |
| « Fichier du modèle illisible » | Le fichier du modèle est abîmé. | **Retirer le modèle**, puis **Installer la dictée sur ce poste**. |
| « Une transcription est déjà en cours » | Une autre dictée est en train d’être transcrite sur ce poste. | Réessayez dans quelques secondes. |
| « La transcription a dépassé le délai prévu » | Dictée longue sur un modèle lent. | Choisissez un modèle plus rapide, ou dictez par parties. |
| « Le navigateur n’a pas converti l’enregistrement en WAV » | Page ancienne gardée en mémoire, ou navigateur trop ancien. | Rechargez la page. Sous Windows, la fenêtre d’AxiorHub utilise Microsoft Edge, à jour. |
| Aucun texte après la dictée | Rien n’a été entendu (micro coupé ou trop loin). | Vérifiez le micro dans les réglages du système, puis réessayez dans la zone d’essai. |

## L’ancienne passerelle de dictée

Les anciens réglages « Activer la dictée locale » et « Passerelle Whisper / Vocal » concernent une passerelle installée sur un
serveur. Elle est facultative et devient inutile quand la dictée sur ce poste est active.

## Pour la personne qui construit les paquets

- Copie réduite du moteur (whisper.cpp `b5907`, licence MIT) : `python scripts/preparer-dictee.py ARCHIVES SORTIE`, à partir des deux
  archives officielles de la publication (empreintes vérifiées ; rien n’est téléchargé). Résultat : `SORTIE\windows` et `SORTIE/linux`.
- Application Windows : `AXIORHUB_WIN_DICTEE=<SORTIE>\windows` avant `python scripts\build-windows.py`. Sans cette variable,
  l’application est construite **sans** moteur de dictée (avertissement affiché).
- Paquet Ubuntu du poste : `python3 scripts/build-deb.py … --variant poste --dictee <SORTIE>/linux` (ajoute `libgomp1` aux dépendances).
  Sans `--dictee`, le paquet est identique à celui des versions précédentes.
- Licences : `THIRD_PARTY_NOTICES.md` et `THIRD_PARTY_NOTICES-DICTEE.txt` (copié à côté du moteur).
