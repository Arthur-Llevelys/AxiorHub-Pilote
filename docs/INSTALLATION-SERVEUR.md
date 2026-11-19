# Installation système (sans Docker) et mise à jour

Ce mode installe AxiorHub Pilote comme service système sur un serveur Ubuntu/Debian qui héberge déjà Nextcloud, Roundcube et
Apache. Pour un serveur neuf, préférez le [VPS clé en main](INSTALLATION-VPS.md).

## 1. Construire l'archive vérifiée

Depuis une copie du dépôt :

```bash
python3 scripts/build-release.py /tmp/axiorhub-mail-agent-5.6.6.tar.gz
```

L'archive contient un manifeste `MANIFEST.sha256` ; l'installateur refuse toute archive modifiée.

## 2. Installer ou mettre à jour

```bash
cd /tmp && tar -xzf axiorhub-mail-agent-5.6.6.tar.gz && cd axiorhub-mail-agent-5.6.6
sudo bash install.sh
```

- **Serveur neuf** : le code est copié dans `/opt/axiorhub-mail-agent/releases/5.6.6`, la configuration dans
  `/etc/axiorhub-mail-agent/` (secrets en 600), l'état dans `/var/lib/axiorhub-mail-agent/`, et les services systemd
  `axiorhub-mail-*` sont installés.
- **Mise à jour** (depuis 3.5.0 ou plus récent, sans passer par les versions intermédiaires) : les fichiers sont
  vérifiés, les bases migrées, le lien `current` basculé ; en cas d'échec, l'ancienne version reste active.
  Retour arrière : `sudo python3 upgrade.py --rollback`.

Une recette automatique de la version installée s'exécute ensuite en arrière-plan ; son résultat apparaît dans
*Pourquoi rien n'est produit ?*.

## 3. Configurer

```bash
sudo axiorhub-mail configure     # messagerie, Nextcloud, agendas, IA locale (saisie masquée des mots de passe)
sudo axiorhub-mail doctor        # contrôle des accès IMAP, Nextcloud, CalDAV et du modèle
```

Interface web sur le nom HTTPS existant :

```bash
sudo python3 /opt/axiorhub-mail-agent/current/install-interface.py
```

L'installateur d'interface ajoute une configuration Apache limitée au chemin `/agent-courriel/` et démarre les
services (interface, deux workers, surveillance IMAP). L'emplacement de Roundcube est détecté (`/var/www/html/roundcube*`) ;
`AXIORHUB_ROUNDCUBE_ROOT` permet de l'imposer.

**Fichiers sur un autre serveur WebDAV (5.6.50)** : sans Nextcloud, renseignez dans *Paramètres › Connexions* le champ « Serveur
WebDAV autre que Nextcloud » (adresse HTTPS du dossier racine des fichiers), avec l'identifiant et le mot de passe d'application. Les
racines autorisées s'appliquent de la même façon ; un fichier supprimé depuis AxiorHub est déplacé dans « Corbeille AxiorHub » ; les
agendas s'ajoutent en CalDAV par chaque utilisateur (*Paramètres › Agendas*).

**Premier lancement (5.6.48)** : aucun mot de passe n'est demandé dans le terminal. Ouvrez `https://votre-nom/agent-courriel/` :
la page « Premier lancement » crée le compte administrateur avec le code privé affiché par
`sudo cat /var/lib/axiorhub-mail-agent/premier-compte.code`. Une installation existante garde son identifiant et son mot de passe.

Pour publier l'interface sur un sous-domaine dédié avec HTTPS, utilisez le modèle `deploy/apache-agent.example.conf`
puis `sudo certbot --apache -d agent.votre-cabinet.fr --redirect`.

**Modifier les documents dans AxiorHub (5.6.108)** : installez le serveur de documents EuroOffice ou OnlyOffice avec son secret
JWT en suivant [INSTALLATION-EDITEUR-DOCUMENTS.md](INSTALLATION-EDITEUR-DOCUMENTS.md) (parcours A : Docker, sous-domaine HTTPS,
Apache, Certbot). Sans lui, les documents Word restent consultables en aperçu (lecture seule) et modifiables dans l'éditeur
intégré WordCraft (version d'essai, une nouvelle version à chaque enregistrement).

**Faire signer un PDF par lien (5.6.108, facultatif)** : installez SignaturePDF à côté d'AxiorHub en suivant
[INSTALLATION-SIGNATUREPDF.md](INSTALLATION-SIGNATUREPDF.md), puis réglez-le dans Paramètres › Connexions (cochez « Instance du
cabinet »). Protégez ses adresses `/cron` et `/api/share/get` comme indiqué à l'étape A7.

**Configurer l’IA locale (5.6.108)** : Paramètres › IA › « Configurer l’IA locale » (administrateur) cherche Ollama à l’adresse
réglée puis sur `127.0.0.1:11434` ; sur le serveur, il repère `ollama` (PATH, `/usr/bin`, `/usr/local/bin`) et l’unité
`/etc/systemd/system/ollama.service` sans rien lancer. Absent : installez-le vous-même (`curl -fsSL https://ollama.com/install.sh | sh`) ;
arrêté : `sudo systemctl start ollama`. L’assistant lit `/proc/meminfo` et `nvidia-smi`/`rocm-smi` s’ils existent, liste les modèles,
suggère des modèles adaptés à la machine (catalogue vérifié : licence, lien officiel, date du relevé), les télécharge par Ollama
seulement après confirmation (en mode local strict, refusé tant que `registry.ollama.ai` n’est pas admis au registre des
prestataires), applique la répartition, le modèle d’embeddings (confirmé à part) et le préréglage du conseil, puis fait un essai court ;
tout est réversible. Ollama sur une autre machine du réseau : adresse IP privée ou nom `….local`, jamais une adresse publique.

## 4. Démarrer prudemment

1. Mode **observation** : AxiorHub Pilote analyse sans rien écrire. Contrôlez les rattachements aux dossiers.
2. Mode **brouillons** : `sudo axiorhub-mail mode drafts`. Les brouillons apparaissent dans la messagerie.
3. Activez les automatismes un par un dans *Paramètres → Automatismes*.

## 4 bis. Veille de sécurité du serveur (5.6.101)

Installée et activée par défaut par le paquet Debian et par `install.sh` (rien sur un poste Windows ou Ubuntu) :

- collecteur `/usr/lib/axiorhub-pilote/veille-securite.py` (copie de `deploy/securite/veille-securite.py`, bibliothèque standard de
  Python, lisible : son en-tête dit exactement ce qu'il lit et écrit). Il tourne sous root **en lecture seule** : il n'arrête, ne
  supprime ni ne modifie rien ; il écrit seulement son rapport dans `/var/lib/axiorhub-mail-agent/securite/` (root:axiorhub-mail, 640,
  30 jours d'historique) et n'ouvre de connexion que vers 127.0.0.1 (sonde des sites Apache) ;
- minuteurs `axiorhub-veille-securite-rapide.timer` (toutes les 10 minutes : processus, réseau, tâches planifiées, fichiers de
  démarrage) et `axiorhub-veille-securite-complete.timer` (chaque nuit à 4 h 40 : tous les contrôles S01 à S13), avec les protections
  systemd `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=read-only`, `PrivateTmp`, `IPAddressDeny=any` / `IPAddressAllow=localhost` ;
- résultats dans *Toutes les fonctions › Sécurité › Sécurité du serveur* (administrateurs), bandeau rouge d'Aujourd'hui et notification
  neutre du téléphone associé pour un constat critique. Les commandes de correction sont à copier : AxiorHub n'en exécute aucune.

Désactiver : `sudo systemctl disable --now axiorhub-veille-securite-rapide.timer axiorhub-veille-securite-complete.timer`, ou le
bouton « Désactiver la veille » de la page (une demande appliquée par `axiorhub-veille-securite-demande.path`). Une désactivation est
conservée lors des mises à jour (`sudo python3 /usr/lib/axiorhub-pilote/veille-securite.py --activer` pour revenir en arrière).
Programmes connus ou chemins autorisés propres au cabinet : `/etc/axiorhub-mail-agent/veille-securite.json`, par exemple
`{"connus": ["mon-calcul"], "autorises": ["/srv/outil/*"]}`. Un motif `/tmp/...` est déconseillé : n'importe quel compte de la
machine peut y déposer un programme.

Collabora intégré à Nextcloud (5.6.102) : l'application « Collabora Online – Built-in CODE Server » (`richdocumentscode`, ou
`richdocumentscode_arm64`) est reconnue sans réglage quand elle est lancée depuis `apps/richdocumentscode/collabora/` d'un Nextcloud (ou `custom_apps/`, `apps-extra/`) :
ses programmes décompressés dans `/tmp/appimage_extracted_…` (mode sans FUSE) ne sont pas signalés comme lancés depuis un dossier
temporaire (contrôle S03) s'ils descendent de ce lanceur, ni pour une forte charge du processeur (S01, rendu de documents) ; le
nom seul ne suffit jamais, et le contrôle de minage (S04) leur reste appliqué. La page indique « Collabora intégré à Nextcloud reconnu (N processus) ». Pour vérifier que ces fichiers sont bien ceux
publiés par Nextcloud, depuis le dossier de Nextcloud : `sudo -u www-data php occ integrity:check-app richdocumentscode` (lecture seule).

## 5. Données et sauvegarde

Sauvegardez `/etc/axiorhub-mail-agent/` et `/var/lib/axiorhub-mail-agent/` (chiffrés, hors du serveur). Ils contiennent
des données de clients et des secrets.
