# AxiorHub Pilote 5.6.8 — installation et réglages

Version cumulative du 6 octobre 2026, sur le socle livré 5.6.7. Une seule archive
contient l'application entière ; il n'est pas nécessaire d'installer successivement
les anciennes versions. Les comptes, modèles, corrections, dossiers, index et
secrets existants sont conservés par la migration. Consulter `RECETTE-5.6.8.md`
pour distinguer tests locaux et recette sur les services réels.

## 1. Contrôler le téléchargement

Mettre l'archive et son fichier `.sha256` dans le même répertoire :

```bash
sha256sum -c axiorhub-mail-agent-5.6.8.tar.gz.sha256
tar -xzf axiorhub-mail-agent-5.6.8.tar.gz
cd axiorhub-mail-agent-5.6.8
```

Le résultat doit être `OK`. L'installateur contrôle aussi le manifeste interne.
SHA-256 vérifie l'intégrité des octets ; ce fichier ne remplace pas une signature
d'éditeur. Aucune signature Minisign officielle n'est jointe à cette livraison.
L'installation manuelle reste possible. Ne pas désactiver la vérification des
signatures du mécanisme de mise à jour distante.

## 2. Installation classique existante sous /opt

Sauvegarder la configuration, l'état, le coffre avec ses clés et les livrables
distants. Laisser terminer les productions ou les suspendre. Depuis le répertoire
extrait, après contrôle du SHA-256 :

```bash
sudo apt-get update
sudo apt-get install -y python3-cryptography espeak-ng
sudo bash install.sh
sudo python3 /opt/axiorhub-mail-agent/current/install-interface.py
```

L'interface reste à son URL configurée, par exemple `/agent-courriel/`. Une mise
à niveau directe depuis 5.6.7 est acceptée. Les versions précédentes explicitement
admises par l'installeur sont également prises en charge. Un processus en cours
fait refuser la bascule : attendre sa fin ou arrêter proprement les unités actives
après suspension. Ne pas supprimer le verrou ou les bases pour contourner ce contrôle.

Les réglages deviennent modifiables via la configuration privée de l'application,
sans rendre tout `/etc` inscriptible. Une mise à jour ne réinitialise pas vos secrets.
Pour le retour arrière d'une installation classique, utiliser le mécanisme conservé :

```bash
sudo python3 /opt/axiorhub-mail-agent/current/upgrade.py --rollback
```

Les nouveaux livrables déjà déposés sont conservés. Un retour de code ne remet pas
automatiquement Nextcloud ou IMAP dans un état ancien. Tester votre restauration
isolée avant de supprimer une sauvegarde.

## 3. Nouvelle installation Docker — port 8626

Depuis le même paquet :

```bash
cp .env.example .env
```

Renseigner l'URL publique, le jeton interne aléatoire, les comptes IMAP/Nextcloud
et les URL réelles. Garder le port lié à `127.0.0.1` derrière votre proxy HTTPS.
Le fichier `.env` est privé. Ensuite :

```bash
docker compose up -d --build
docker compose ps
docker compose exec axiorhub cat /data/auth/bootstrap-token
```

Le dernier code est le code privé de première inscription sur `/signup`, pas un
mot de passe à publier. Les autres comptes se créent depuis l'administration.
Les fichiers Apache/Nginx et instructions HTTPS du paquet restent utilisables.
Le microphone exige HTTPS hors localhost. Les secrets et bases vivent dans `data/`,
qui doit être sauvegardé et ne doit jamais entrer dans le dépôt public.

Pour remplacer une installation Docker : conserver son `.env` et son `data/`,
sauvegarder avec services arrêtés, remplacer le code par le paquet complet puis
relancer `docker compose up -d --build`. Éviter `down -v` et toute copie du
répertoire de données dans une archive publique. Les variables d'amorçage `.env`
ne remplacent pas les connexions déjà enregistrées ; modifier celles-ci dans l'interface.

L'image AxiorHub et l'image Kokoro optionnelle n'ont pas été construites dans
l'environnement de livraison. Leurs fichiers sont fournis ; leur démarrage,
latence, consommation et restauration sont à vérifier sur votre machine.

Si vous utilisez l'outil Open WebUI, régénérer le fichier puis remplacer l'outil
existant dans son espace de travail :

```bash
sudo python3 /opt/axiorhub-mail-agent/current/install-openwebui-tool.py
sudo python3 /opt/axiorhub-mail-agent/current/diagnostic-openwebui.py
```

Le diagnostic se lance avec `sudo` : le jeton reste protégé en lecture root. Il
n'affiche pas le secret. Ne pas élargir ses droits pour contourner une permission.

## 4. Activer une proactivité utile

1. Dans **Paramètres → Connexions**, tester IMAP, Nextcloud et l'agenda. Choisir les
   dossiers Envoyés et Brouillons réels ; Envoyés doit être distinct de l'INBOX et
   des Brouillons. Déclarer les adresses et alias réellement autorisés du compte.
2. Dans **Paramètres → Proactivité et engagements**, choisir votre identité, votre
   fuseau, les rôles et les exceptions par dossier. Un avocat ou administrateur
   peut modifier son profil ; une même identité ne peut être suivie par deux profils actifs.
3. Activer le suivi. **Observer** propose des plans ; **Préparer** lance les travaux
   internes autorisés. **Organiser** conserve ces préparations et les capacités
   internes déjà autorisées ; il n'autorise aucun envoi. Régler également les
   automatismes de dépôt du cabinet si vous voulez déposer les brouillons.
4. Ouvrir **Engagements** depuis Aujourd'hui/Missions, puis « Vérifier les envois ».
   Le watcher reprend ensuite Envoyés et les réponses toutes les cinq minutes.
   La coordination des étapes est contrôlée toutes les trente secondes lorsqu'un
   changement de traitement le justifie, sans nouvelle inférence permanente.

Les engagements sont des candidats sourcés : une condition de paiement ou de
réception bloque la préparation automatique. Un dossier ambigu appelle une précision.
Les dates vagues demandent une précision ; aucune échéance procédurale n'est déduite
silencieusement. La fenêtre initiale est de quatorze jours et chaque lot est borné
à cinquante messages, avec reprise du curseur. Les événements déjà historiques
hors de cette fenêtre ne sont pas prétendus analysés.

Une réponse au fil suspend la relance, sans déclarer que toutes les pièces sont
reçues. « Reprendre : pièces encore manquantes » réactive ce suivi après votre
vérification. « Confirmer l'exécution » demande une preuve ou confirmation explicite.
Un projet préparé ne prouve pas l'envoi promis. Une relance n'est automatique que
pour un client unique confirmé, sans conflit, après relecture du fil ; elle reste
dans les Brouillons et son contenu est relu depuis IMAP.

Les suites enchaînent l'analyse puis un projet dans le dossier. Les choix de modèle,
les plafonds, les corrections et les analyses longues existants restent applicables.
Suspendre arrête les nouvelles étapes ; une étape en cours s'arrête au point sûr
prévu par son producteur. Les résultats déjà déposés ne sont pas supprimés.

## 5. Rendez-vous et Talk

Activer Talk dans le profil après avoir testé la connexion Nextcloud. L'adaptateur
vise l'API Talk OCS v4. Une visio explicitement reconnue, liée à un dossier connu
et comprise dans l'horizon choisi peut recevoir un salon privé. Il est relu sur
le serveur : type privé, non listable et seul compte organisateur vérifié.

Aucun invité n'est ajouté et le calendrier n'est pas modifié. Un salon préexistant
partagé ne satisfait pas ce contrôle strict : il demande une vérification, sans
création d'une seconde salle. Un timeout conserve l'incertitude et provoque une
relecture ; il n'entraîne pas un second POST aveugle. Le lien disponible apparaît
dans l'événement. Une réunion déplacée conserve son UID et le salon connu ; une
annulation reçue suspend le plan et conserve les échanges.

## 6. Veille du matin

Dans les mêmes paramètres, activer la veille, saisir les domaines/mots-clés et les
URL **réelles de flux RSS/Atom officiels**. Tester chaque source. Le logiciel
n'invente pas une URL de flux disponible pour chaque institution. Les domaines
autorisés couvrent les institutions françaises et européennes listées dans le code.

Régler les jours et l'heure locale. La page Veille affiche lien, date et extrait
du flux ; les erreurs de source sont visibles. La lecture du briefing peut reprendre
trois titres. Le filtrage utilise des mots-clés ; l'applicabilité à un dossier et
la portée juridique d'une décision nécessitent encore une recherche ou analyse
distincte via les outils existants. Aucun fait client n'est envoyé pour ce classement.
La rétention choisie concerne les nouveautés de veille ; elle n'efface pas les
engagements ni les productions du cabinet.

## 7. Voix et conversation

Le bouton **microphone** à droite ouvre le dialogue. Le bouton robot textuel reste
disponible. Choisir le dossier, puis démarrer : le dossier est fixé pour cette
session. La transcription locale reste configurée dans Connexions ; Ollama n'est
pas un moteur de reconnaissance vocale. Les tours sont limités à 45 secondes et
8 Mo ; le texte est visible et corrigible. La fonction IA `voice_conversation`
permet de choisir un modèle rapide dans les réglages IA existants.

Le mode **discussion** analyse sans produire un document par surprise. Le mode
**mission** autorise les préparations internes demandées. Aucune parole n'autorise
un envoi externe. Fermer, arrêter, changer de page ou mettre la page en arrière-plan
coupe le microphone. Il s'agit de tours successifs, pas de duplex intégral WebRTC.
Tester le silence, l'écho, l'interruption et la transcription des dates sur votre navigateur.

| Fournisseur | Réglage et comportement |
|---|---|
| eSpeak NG | Local, fourni comme dépendance dans Docker ; disponible sans service TTS optionnel. |
| Kokoro | API locale compatible `/v1/audio/speech`, voix française `ff_siwis`, modèle `kokoro`. |
| Chatterbox | Adaptateur pour une API locale compatible ; serveur non fourni. Choisir une variante française adaptée. |
| ElevenLabs | Clé dans le coffre, Voice ID, modèle adapté, consentement externe et tarifs/plafonds obligatoires. |

Pour l'option Kokoro CPU :

```bash
docker compose -f docker-compose.yml -f docker-compose.voice568.yml up -d --build
```

Régler ensuite Connexions → Synthèse vocale sur `kokoro`, URL `http://kokoro:8880`,
voix `ff_siwis`, modèle `kokoro`. Vérifier/pinner l'image `KOKORO_IMAGE` avant
publication. Aucun port TTS supplémentaire n'est exposé publiquement.

Pour ElevenLabs, renseigner le tarif plafond applicable à votre contrat par mille
caractères, le plafond par lecture et le budget mensuel partagé. Autoriser aussi la
politique externe globale et la fonction utilisée. Un dossier exclu reste bloqué.
Une réservation incertaine reste comptée après timeout ; le fournisseur peut avoir
facturé. Le coût affiché est une estimation, pas une facture réconciliée. Le cache
est privé et séparé par utilisateur/dossier ; les lectures identiques évitent un
nouvel appel. Un repli eSpeak peut être configuré, avec le motif visible dans l'état.
Le test de connexion lit les capacités, sans transmettre de texte de dossier.

La synthèse externe applique la pseudonymisation du socle avant transmission.
L'audio distant lit donc les pseudonymes : il ne peut pas réinsérer vos noms réels.
Le panneau montre le texte transmis et l'estimation dans « Données de lecture
vocale ». Pour entendre les noms réels dans un briefing, choisir une voix locale.
La pseudonymisation réduit les identifiants détectés ; elle n'assure pas à elle
seule l'anonymat de toutes les situations décrites.

## 8. Vérification après installation

Faire les scénarios de `RECETTE-5.6.8.md` sur un dossier et des courriels fictifs :
envoi avec promesse, brouillon effectivement retrouvé, double clic, redémarrage,
source modifiée, réunion reportée, voix refusée par budget, source de veille en panne.
Un voyant de service actif n'atteste pas un livrable : consulter les preuves d'UID
ou de contenu distant. Le jeu de tests local emploie des doubles de services ; il
ne certifie ni vos identifiants, ni l'exactitude juridique, ni la latence réelle.

## 9. Créer vos agents documentaires

Les pages sont accessibles depuis Paramètres et Outils, sous les mêmes six
rubriques principales : **Agents et règles**, **Agendas et procédure**,
**Résultats des agents**. Pour une installation classique, les chemins sont
préfixés par l'adresse configurée, par exemple `/agent-courriel/parametres/agents`.
Dans Docker autonome, utiliser `/parametres/agents`.

1. Dans **Connexions**, renseigner URL Nextcloud, identifiant, mot de passe
   d'application, racines autorisées, URL(s) complète(s) des agendas CalDAV et
   dossiers d'arrivée à surveiller. Il n'est pas nécessaire de modifier du code.
   Un dossier d'arrivée doit rester à l'intérieur d'une racine autorisée.
2. Dans **Initiatives et voix**, activer votre profil et choisir Préparer ou
   Organiser. Organiser autorise également le classement et les inscriptions.
   Le mode d'une règle ne peut pas dépasser le plafond de ce profil ; les
   exclusions, pauses et vacances restent prioritaires. Activer aussi le dépôt
   automatique des brouillons dans les automatismes du cabinet.
3. Dans **Agents et règles**, décrire la mission en français, cliquer Interpréter,
   relire les mots/types, l'ancienneté, la portée, les actions et l'autonomie,
   puis Enregistrer et activer. Cette revue a lieu une fois par version de règle,
   pas à chaque source. Aucun script ou outil arbitraire n'est autorisé.
4. Les cinq règles par défaut sont présentes en état À relire ; les modifier,
   activer, suspendre ou supprimer selon votre organisation. Les pièces citant
   un avis ne sont pas classées sur la seule occurrence dans un long texte :
   l'intitulé en tête est prioritaire. La reconnaissance reste lexicale,
   expliquée, et ne certifie pas une qualification juridique.
5. La surveillance existante parcourt progressivement trois dossiers par lot ;
   les contrôles de secours sont périodiques. Les dossiers d'arrivée ont un
   contrôle de cinq minutes. Un très grand cabinet nécessite plusieurs lots ;
   cette cadence n'est pas une garantie de cinq minutes pour chaque fichier.
6. Consulter Résultats des agents : source/ETag/hash, couverture des pages,
   classement, agenda et brouillon relus, tâches et suites. Relancer recherche
   les dépôts existants. Écarter ou suspendre interrompt également la préparation
   des conclusions déjà liée. Les résultats déposés sont conservés.

Exemple de mission (le périmètre des outils reste limité aux actions affichées) :

> Si un fichier créé depuis moins de 10 jours contient AVIS_DE_RENVOI dans son
> nom ou correspond à un avis de renvoi, analyse-le, classe-le dans PROCEDURE du
> bon dossier, inscris les dates prouvées dans mes agendas sans doublon, prépare
> un brouillon d'information selon mon rôle et la prochaine tâche. Prépare un
> projet de conclusions si le dépôt de nos conclusions est demandé. Aucun envoi.

La propriété WebDAV `creationdate` doit être fournie avec un fuseau. Si elle est
absente, l'exécution demande une date avec une preuve, ou vous pouvez choisir
explicitement Modification dans la règle. La modification n'est pas présentée
comme une création. Un scan OCR ne déclenche pas seul une inscription de date.

Un fichier est déplacé par MOVE conditionnel dans le sous-dossier PROCEDURE
du dossier identifié, puis son contenu est relu et comparé. Un nom déjà occupé
par un autre fichier bloque le classement. Une copie identique déjà présente
sans historique garde la source : aucun nettoyage automatique n'est effectué.

Le brouillon est adressé au dominus litis confirmé pour un postulant/correspondant,
ou au client unique confirmé pour les rôles de représentation directe. Sans
destinataire suffisamment établi, il devient une note adressée à vous-même.
Aucun courriel n'est envoyé. La rédaction de conclusions utilise les missions
et producteurs existants ; les sources manquantes restent signalées.

### Délais et profil d'appel

Dans **Agendas et procédure**, sélectionner le dossier et renseigner les données
de rôle et de procédure. Le nom CONVOC ne choisit jamais le circuit. Les calculs
pris en charge se limitent au cadre configuré de l'appel civil ordinaire avec
représentation obligatoire, selon la date de régime et les points de départ
littéralement présents : déclaration d'appel, réception de l'avis de fixation
ou du greffe, notification des conclusions de l'appelant. L'extraction distingue
la date de rédaction d'un avis de sa date explicite de réception.

Le moteur de délais conserve ses versions de règles. Le régime récent utilise
notamment les articles 902, 906-1, 906-2 et 908 du CPC. Les conditions des articles
915-3/915-4, les majorations territoriales, délais réduits par le juge,
interruptions, suspensions et régimes particuliers doivent être contrôlés.
Cette livraison ne prétend pas automatiser tous ces cas : données absentes ou
incident connu → proposition avec motif, sans inscription automatique du calcul.
Les dates passées restent des sources ou propositions, pas de nouveaux rappels
automatiques. Une heure absente produit un rappel sur la journée. Une heure
explicite donne une durée de rappel d'une heure, présentée comme un réglage,
pas comme la durée d'audience attestée. Un événement humain équivalent est
conservé avec sa durée, sans écrasement.

### Ajouter Google Calendar et d'autres CalDAV

1. Dans Google Cloud Console, activer **Google Calendar API**, configurer l'écran
   de consentement et créer un **client OAuth Application Web**. Pour un projet
   en test, ajouter l'utilisateur autorisé selon les règles Google du compte.
2. Déclarer une URI de redirection HTTPS exacte :
   `https://agent.exemple.com/api440/m568/google/callback`, ou
   `https://agent.exemple.com/agent-courriel/api440/m568/google/callback` si
   votre installation utilise ce préfixe. Utiliser votre domaine réel partout.
3. Dans **Connexions → Google Calendar**, enregistrer Client ID, secret du client
   dans le coffre et la même URI. Aucun mot de passe Google n'est saisi.
4. Dans **Agendas et procédure**, cliquer Autoriser Google Calendar. Accepter les
   droits d'événements et de liste des calendriers, puis Lister mes agendas Google.
   Choisir un agenda où votre compte possède le droit writer ou owner ; donner
   un nom, cocher l'autorisation de transmission et enregistrer la cible.
5. Conserver les agendas Nextcloud et ajouter autant de cibles distinctes que
   nécessaire. Les événements sont dédupliqués et vérifiés séparément dans
   chacune. Tester contrôle la lecture sans créer d'événement de démonstration.
6. Révoquer Google déconnecte le compte et désactive les cibles Google. Les
   événements déjà créés ne sont pas supprimés.

Les agendas Nextcloud configurés dans Connexions servent de cibles par défaut.
Pour en désactiver un, ajouter la même URL ici et décocher Activer : il ne
réapparaîtra pas automatiquement. Un autre serveur CalDAV possède sa propre
URL HTTPS, son identifiant et son mot de passe d'application, conservé au coffre.

Par défaut Google reçoit référence du dossier, catégorie et date, aucun texte
intégral ni document. Inclure les détails est une option explicite. Une référence
peut encore identifier un dossier : ce n'est pas une anonymisation garantie.
Les exclusions de transmission du dossier restent appliquées. Google est une
destination externe autorisée séparément du fournisseur du LLM ; le LLM peut
rester local. Aucun invité ni invitation n'est ajouté par ce connecteur.
Les autorisations expirées/révoquées et agendas non inscriptibles restent des
incidents lisibles à reprendre après correction.

Le client OAuth doit être paramétré sur votre compte Google et l'intégration
testée réellement après installation. Aucun compte Google, Nextcloud ou IMAP
réel n'a été contacté lors de la recette locale de livraison.
