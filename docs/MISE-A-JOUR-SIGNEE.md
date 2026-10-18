# Mise à jour signée depuis la page des versions, sans ligne de commande

Depuis la 5.6.61, AxiorHub Pilote consulte la page des versions GitHub au plus toutes les 6 heures. Quand une nouvelle version est
publiée, il l'annonce dans **Paramètres › Confort et maintenance › Mises à jour** et dans la vue d'ensemble des Paramètres.
L'administrateur clique sur **« Mettre à jour vers X »**. C'est tout : aucune commande à taper, sur aucune des trois plateformes.

Une version n'est installée que si sa **signature** est vérifiée avec la clé publique livrée avec AxiorHub. Un compte GitHub piraté
ou un fichier remplacé en chemin ne suffit donc pas à installer du code chez vous.

## Ce qui se passe après le clic

**Serveur Linux.** Le clic dépose une demande. Le service système `axiorhub-mail-agent-maj`, installé et activé automatiquement par
`install.sh` ou par le paquet Debian, la prend en charge en root :

1. il revérifie que la version demandée est bien la dernière publiée ;
2. il télécharge l'archive, son fichier `.sha256` et sa signature `.sig` ;
3. il vérifie l'empreinte SHA-256, puis la signature ;
4. il extrait l'archive en refusant tout chemin sortant et tout lien ;
5. il installe avec la mise à jour habituelle (`install.sh`), qui arrête puis relance les services et conserve les données ;
6. il contrôle que les services repartent ; sinon il revient à la version précédente (`upgrade.py --rollback`).

AxiorHub est indisponible une à deux minutes.

**Windows 11.** AxiorHub télécharge l'installateur (`AxiorHub-Pilote-Setup-X.exe`), son empreinte et sa signature, et vérifie les
deux. Il lance ensuite l'installateur en mode silencieux, dans le même dossier et avec les mêmes raccourcis. L'application se ferme,
puis se rouvre dans la nouvelle version.

**Ubuntu poste.** AxiorHub télécharge le paquet `axiorhub-pilote-poste_X_amd64.deb`, son empreinte et sa signature, et vérifie les
deux. Ubuntu demande ensuite votre mot de passe dans sa fenêtre habituelle. Si cette fenêtre ne peut pas s'ouvrir, l'installateur
graphique d'Ubuntu s'ouvre sur le paquet. Fermez puis rouvrez AxiorHub une fois l'installation terminée.

Le déroulé et le résultat s'affichent sous « Dernière mise à jour ». Les données et réglages sont toujours conservés.

## Automatique la nuit (facultatif)

Par défaut, rien ne s'installe sans clic. L'administrateur peut cocher **« Installer automatiquement les nouvelles versions la nuit »**.
AxiorHub installe alors la dernière version entre 2 h et 5 h, à l'heure du serveur ou du poste. Il fait une seule tentative par version :
en cas d'échec, il attend votre clic.

## Pour l'auteur des versions : signer chaque version

La clé de signature est créée une seule fois, sur le poste de l'auteur, et protégée par une phrase de passe. La clé publique
(`axiorhub_release.pub`) est livrée avec AxiorHub dans `agent/data/maj-signataires`. La clé privée ne quitte jamais ce poste.

Pour chaque version, le script ci-dessous signe l'archive serveur, l'installateur Windows et les paquets Debian. Il vérifie aussitôt
chaque signature :

```powershell
powershell -ExecutionPolicy Bypass -File scripts\signer-version.ps1 -Version X.Y.Z -Dossier C:\livraison
```

Déposez ensuite les fichiers `.sig` sur la page de la version GitHub, à côté des fichiers signés et de leurs `.sha256`. Une version
publiée sans `.sig` reste annoncée, mais elle ne s'installe pas depuis AxiorHub.

## Administrateurs système (facultatif)

Sur un serveur, `/etc/axiorhub-mail-agent/maj-signataires` contient la clé de confiance. Il est mis à jour avec la clé livrée tant
qu'il porte la mention « géré par AxiorHub ». Une clé posée par l'administrateur (`miseajour.py --activer --cle "ssh-ed25519 …"`)
n'est jamais remplacée. `--desactiver` arrête le service, et `--verifier` affiche la dernière version publiée sans rien installer.
