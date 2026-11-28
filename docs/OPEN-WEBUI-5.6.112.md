# Open WebUI dans AxiorHub Pilote (5.6.112) — clé en main

AxiorHub Pilote affiche l'Open WebUI du cabinet dans sa page **Conversations IA** (menu principal) et, pour chaque dossier, dans
l'onglet **Conversation IA** de la fiche, sur la conversation propre à ce dossier. Open WebUI n'est pas embarqué dans AxiorHub : il tourne
à côté (Docker sur le serveur), garde son nom et son logo, et ses mises à jour s'appliquent sans nouvelle version d'AxiorHub.

Une fois AxiorHub installé et Open WebUI installé avec lui, il n'y a **rien à régler à la main** :

- le compte Open WebUI de chaque personne est créé à sa première visite de « Conversations IA » (même adresse que dans AxiorHub), avec sa
  clé d'API ; son mot de passe initial lui est affiché une seule fois ;
- les réglages d'AxiorHub sont reportés dans Open WebUI et **restent identiques** : connexions IA, paramètres de génération de chaque
  modèle (température, top p, top k, min p, pénalité de répétition, contexte, sans « réflexion »), voix (synthèse et dictée), compétences,
  connecteurs MCP, invite système ; quand ils changent dans AxiorHub, ils sont reportés automatiquement (au plus toutes les dix minutes,
  à l'affichage de Conversations IA ou des Paramètres), et à chaque mise à jour d'AxiorHub (avec l'outil AxiorHub).

## 1. À l'installation d'AxiorHub

À la fin de `sudo python3 install-interface.py`, une question propose d'installer Open WebUI. Il faut Docker
(`sudo apt install -y docker.io`) et un nom HTTPS pour Open WebUI (par exemple `ia.votre-domaine`) avec son certificat
(`sudo certbot certonly --apache -d ia.votre-domaine`). La même chose, plus tard :

```
sudo python3 /opt/axiorhub-mail-agent/current/install-openwebui.py --nom ia.votre-domaine
```

Le script :

- télécharge l'image officielle d'Open WebUI **0.13.5**, figée par son empreinte (plusieurs gigaoctets, une seule fois) ;
- crée le conteneur `axiorhub-openwebui` (données dans le volume `axiorhub-openwebui`, écoute sur 127.0.0.1 seulement) ;
- écrit `/etc/axiorhub-mail-agent/openwebui.env` (droits 600) : secret de session, affichage autorisé dans AxiorHub et nulle part
  ailleurs, clés d'API activées, interface en français ;
- crée le site Apache `axiorhub-openwebui` (HTTPS, WebSocket) avec le certificat du nom ; si le certificat manque, il donne la commande
  certbot et n'écrit rien ; un nom déjà servi par un autre site n'est jamais modifié ;
- crée le **compte administrateur d'Open WebUI** (mot de passe initial affiché une fois : notez-le) et sa clé d'API ;
- règle Open WebUI d'après AxiorHub (section 2) et le **relie** à AxiorHub (clé d'administrateur gardée chiffrée par AxiorHub).

Une seule question est posée : transmettre ou non à Open WebUI les clés des fournisseurs d'IA (Mistral, OpenRouter…), des connecteurs MCP
et de la voix ElevenLabs. Sans accord, ces connexions sont ajoutées désactivées.

Mise à jour d'Open WebUI, quand une mise à jour d'AxiorHub livre une nouvelle version :

```
sudo python3 /opt/axiorhub-mail-agent/current/install-openwebui.py --mettre-a-jour
```

Les données sont d'abord sauvegardées dans `/var/backups/axiorhub-openwebui` ; si la nouvelle version ne répond pas, l'ancienne est
remise en service.

## 2. Ce qui est réglé dans Open WebUI (et tenu identique à AxiorHub)

| Élément | Ce qui est fait |
|---|---|
| Comptes | clés d'API permises aux comptes ; compte de chaque personne créé par AxiorHub à sa première visite |
| Outil AxiorHub | installé, relié à l'API d'AxiorHub avec son jeton |
| Connexions IA | Mistral, OpenRouter, OpenAI, Anthropic, Gemini et Ollama réglés dans AxiorHub (clés après accord) |
| Paramètres des modèles | chaque modèle employé par AxiorHub (par exemple `qwen3.8:27b`, `qwen3.5:9b`) reçoit les paramètres d'AxiorHub (profil choisi, Juridique par défaut : température 0,3, top p 0,9, top k 40, min p 0, pénalité de répétition 1,0 sur 64 jetons, contexte 32 768, sans « réflexion ») ; pour Mistral, OpenAI ou Anthropic, seuls ceux qu'AxiorHub leur transmet |
| Voix | Kokoro ou Chatterbox : même serveur, même voix, même vitesse ; ElevenLabs si sa clé est acceptée ; Brindille et eSpeak, propres à AxiorHub : voix française du navigateur. Dictée : Whisper de même taille que la dictée d'AxiorHub (rapide : base, équilibré : small, précis : large-v3-turbo) |
| Connecteurs MCP | connecteurs actifs d'AxiorHub (jetons après accord) ; un connecteur OAuth est à relier dans Open WebUI |
| Compétences | compétences approuvées par un avocat dans AxiorHub (texte seulement) |
| Modèle « AxiorHub Avocat » | invite système AxiorHub, outil et compétences, mêmes paramètres ; choisi par défaut |
| Invites | `/dossier`, `/echeances`, `/courriel`, `/recherche` |

Tout le cabinet y a accès en lecture. Rien de ce que le cabinet a réglé lui-même n'est retiré.

## 3. Instance déjà en place (installée autrement)

```
sudo axiorhub-mail openwebui
```

La commande demande l'adresse d'Open WebUI, puis la clé d'API d'un **administrateur** d'Open WebUI. Sans clé, laissez la réponse vide :
elle demande alors le courriel et le mot de passe de l'administrateur (utilisés pour cette seule connexion, jamais enregistrés), active
les clés d'API d'Open WebUI si elles ne le sont pas, puis reprend la clé existante de l'administrateur (jamais remplacée) ou en crée une.
Elle propose aussi de fermer l'inscription publique d'Open WebUI, puisque AxiorHub crée désormais les comptes du cabinet. Elle règle
ensuite tout (section 2) et relie l'instance à AxiorHub.

Dans une instance réglée à la main : un outil AxiorHub déjà importé est mis à jour (pas de second outil) ; vos modèles et votre modèle
par défaut restent les vôtres (« AxiorHub Avocat » est ajouté à côté) ; vos outils, fonctions et réglages propres ne sont pas touchés.

Affichage dans AxiorHub : sans en-tête qui l'interdit, le cadre fonctionne. Pour le restreindre à la seule adresse d'AxiorHub (conseillé),
ajoutez à la configuration d'Open WebUI :

```
CONTENT_SECURITY_POLICY=frame-ancestors 'self' https://<adresse d'AxiorHub>
```

« Tester l'affichage » (page Conversations IA ou Paramètres › Connexions) dit ce qu'il en est. Un compte qui existait déjà dans cette
instance n'est jamais modifié : la personne enregistre elle-même sa clé d'API dans Conversations IA.

## 4. Au quotidien

- Première ouverture de Conversations IA : le compte de la personne est créé ; elle se connecte une fois dans le cadre (session du
  navigateur) avec le mot de passe initial affiché, puis le change dans Open WebUI.
- L'onglet Conversation IA d'un dossier ouvre la conversation « Dossier … » de cette personne, ou la crée. Les missions AxiorHub du
  dossier restent ouvertes par « Missions AxiorHub du dossier ».
- Paramètres › Connexions › Open WebUI du cabinet : état de la liaison, dernière synchronisation, « Synchroniser maintenant ».
