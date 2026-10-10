# Poste de travail Windows 11

AxiorHub Pilote s'installe aussi comme une application de bureau Windows 11, sur le modèle du poste Ubuntu 24.04 : tout
fonctionne sur l'ordinateur, sans serveur. L'interface n'écoute que sur l'adresse locale `127.0.0.1` ; la fenêtre
s'ouvre avec Microsoft Edge en mode application et se connecte par un jeton à usage unique, valable deux minutes.

## Ce qu'il faut

| Élément | Rôle | Obligatoire |
|---|---|---|
| Windows 11, 64 bits | système | oui |
| Microsoft Edge | fenêtre de l'application (présent d'origine) | oui |
| [Ollama pour Windows](https://ollama.com/download/windows) ou un fournisseur d'IA configuré dans Paramètres | modèle de langue | oui, l'un ou l'autre |
| Tesseract OCR (avec la langue française) | lecture des PDF numérisés | non |
| LibreOffice | conversions de documents Word et PDF | non |
| eSpeak NG | voix de secours | non |
| Poppler | lecture des PDF ; sinon `pypdf`, inclus, prend le relais | non |

Python n'est pas nécessaire : il est inclus dans l'application.

## Installer

1. Téléchargez `AxiorHub-Pilote-Setup-<version>.exe` et son fichier `.sha256` depuis la page des versions du dépôt.
2. Vérifiez l'empreinte dans PowerShell ; elle doit être identique au contenu du fichier `.sha256` :

   ```powershell
   Get-FileHash .\AxiorHub-Pilote-Setup-5.6.25.exe -Algorithm SHA256
   ```

3. Double-cliquez sur l'installateur. L'application n'étant pas signée par un éditeur, Windows SmartScreen peut afficher
   « Windows a protégé votre ordinateur » : cliquez sur **Informations complémentaires**, puis **Exécuter quand même**.
4. Choisissez les raccourcis (menu Démarrer, Bureau, lancement à l'ouverture de session) puis **Installer**.

L'installation se fait pour l'utilisateur courant, sans droits d'administrateur :

| Contenu | Emplacement |
|---|---|
| Application | `%LOCALAPPDATA%\Programs\AxiorHub Pilote` |
| Configuration, mémoire des dossiers, état | `%LOCALAPPDATA%\AxiorHub Pilote\donnees` |
| Journaux | `%LOCALAPPDATA%\AxiorHub Pilote\journaux` |

Installation sans fenêtre, par exemple pour un déploiement sur plusieurs postes :

```powershell
.\AxiorHub-Pilote-Setup-5.6.25.exe --silencieux --demarrage
```

Options : `--sans-bureau`, `--sans-raccourcis`, `--demarrage` (lancement à l'ouverture de session), `--lancer`
(ouvrir l'application à la fin).

## Premier lancement

Ouvrez **AxiorHub Pilote** depuis le menu Démarrer. Les services (interface, travaux, veille de la messagerie, passage
périodique de l'agent) démarrent en arrière-plan et la fenêtre s'ouvre sur **Paramètres** pour le premier réglage :

1. **Dossier de travail** : bouton « 📁 Choisir… », par exemple un dossier synchronisé par le client Nextcloud ;
2. **Modèle d'IA** : Ollama local (`http://127.0.0.1:11434`) ou un fournisseur externe pseudonymisé ;
3. **Messagerie** (facultative) : IMAP, dossier Brouillons ;
4. **Agendas** (facultatifs) : Nextcloud, CalDAV ou Google Agenda.

Fermer la fenêtre arrête l'application et ses services. Un second lancement ouvre une nouvelle fenêtre sur l'instance déjà
active.

## Mettre à jour

Lancez l'installateur de la nouvelle version : l'application est remplacée, la configuration et les données sont conservées.
Un poste en mode serveur se met à jour comme d'habitude (voir [INSTALLATION-SERVEUR.md](INSTALLATION-SERVEUR.md)).

## Désinstaller

**Paramètres Windows › Applications › Applications installées › AxiorHub Pilote › Désinstaller.** Les services sont arrêtés,
puis une question permet de supprimer aussi les données ; par défaut elles restent dans `%LOCALAPPDATA%\AxiorHub Pilote`.

## Construire l'installateur depuis les sources

Sur un poste Windows avec Python 3.12 :

```powershell
python -m pip install -r scripts\windows-requirements.txt
python scripts\build-windows.py dist\windows
```

Le dossier `dist\windows` contient l'installateur, une archive `.zip` de l'application (déploiement sans installateur) et
leurs empreintes `.sha256`. L'intégration continue construit le même installateur à chaque envoi sur le dépôt.
