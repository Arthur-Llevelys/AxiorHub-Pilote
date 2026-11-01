# Stockage des documents dans le cloud (version 5.6.88)

Ce guide s'adresse au cabinet. Il explique ce que le stockage cloud change, comment le mettre en place et ce qu'il ne fait pas.

**À lire d'abord.** Cette fonction n'a été essayée sur **aucun vrai compte** Google, Microsoft ou Dropbox. Elle a été développée d'après les documentations publiques et testée contre des imitations locales de ces services. C'est pourquoi le bouton **Contrôler ce compte** existe, et pourquoi il est obligatoire avant toute bascule : lui seul vérifie le comportement réel de votre compte.

**Rien ne change sans votre décision.** Le stockage par défaut reste Nextcloud (ou votre dossier local). Une mise à jour ne change rien, une copie non plus. Seul un administrateur peut changer de stockage.

## Avant de choisir : le secret professionnel

Dans le cloud, vos documents sont **en clair chez le fournisseur**. La pseudonymisation d'AxiorHub protège le texte envoyé aux modèles d'IA. Elle ne s'applique pas aux fichiers déposés dans Drive, OneDrive ou Dropbox.

Avant de basculer, vérifiez vous-même :

- le contrat et l'accord de sous-traitance (RGPD) du fournisseur, et le lieu d'hébergement ;
- que l'offre choisie convient à des données couvertes par le secret professionnel ;
- que tout le cabinet utilise des comptes professionnels, avec double authentification.

Un compte personnel n'est pas adapté à des dossiers de clients.

## Mise en place

Tout se passe dans **Paramètres › Stockage des documents**.

1. Créez l'application OAuth chez le fournisseur (voir plus bas). Elle appartient au cabinet.
2. Saisissez ses identifiants dans la fiche du fournisseur, puis **Enregistrer**. Le secret part au coffre chiffré et n'est plus jamais affiché.
3. **Connecter le compte**, puis **Tester la lecture**.
4. **Préparer le dossier AxiorHub** : le dossier et les racines sont créés chez le fournisseur.
5. **Contrôler ce compte** : un fichier d'essai est créé, relu, remplacé, refusé quand il le faut, puis mis à la corbeille. Si une étape échoue, ce stockage ne peut pas être choisi.
6. **Copier** les documents vers ce stockage (voir plus bas) et lire le bilan.
7. **Utiliser ce stockage**.

Pour revenir en arrière : **Revenir au stockage d'origine**. Aucun fichier n'est déplacé : ce qui a été écrit dans le cloud y reste, et il faut le recopier si vous voulez le retrouver dans Nextcloud.

Changer d'application ou de compte annule le contrôle : il faut le refaire. Il expire aussi après 30 jours, mais cela ne concerne qu'une nouvelle bascule.

## Ce qu'il faut créer chez chaque fournisseur

Dans chaque cas, l'adresse de retour à déclarer est affichée sur la fiche du fournisseur. Elle a la forme `https://votre-serveur/agent-courriel/connexions-retour`.

### Google Drive

Dans la console Google Cloud :

1. Créez un projet et activez **Google Drive API**.
2. Configurez l'écran de consentement. Choisissez **Interne** si vous avez Google Workspace : seuls vos comptes pourront l'utiliser, et Google n'exige pas de vérification.
3. Créez un **ID client OAuth** de type *Application Web*, avec l'adresse de retour.
4. Reportez l'identifiant et le secret dans AxiorHub.

**Quelle portée choisir ?** AxiorHub demande par défaut `drive.file`, la plus étroite. Elle **ne suffit pas toujours**, et il faut le savoir :

- avec `drive.file`, AxiorHub ne voit que les fichiers qu'il a créés lui-même ;
- un document déposé à la main dans Drive par un collaborateur reste **invisible** : il n'est ni lu, ni indexé, ni surveillé, et aucune alerte ne le signale ;
- `drive.file` convient donc si tout passe par AxiorHub ; si le cabinet dépose des pièces directement dans Drive, il faut la portée complète `drive` ;
- la portée complète donne accès à **tout le Drive du compte relié**. Google la classe comme restreinte : une application hors Workspace interne doit passer une vérification, parfois payante. Reliez alors un compte dédié au cabinet, pas le compte personnel d'un associé.

### SharePoint ou OneDrive

Dans le centre d'administration Microsoft Entra :

1. **Inscriptions d'applications › Nouvelle inscription**, puis ajoutez l'adresse de retour (plateforme *Web*).
2. Créez un secret client, ou activez *Autoriser les flux clients publics* si vous n'en voulez pas.
3. Dans **Autorisations de l'API › Microsoft Graph › Déléguées**, ajoutez :
   - pour OneDrive : `User.Read`, `offline_access`, `Files.ReadWrite` ;
   - pour SharePoint : `User.Read`, `offline_access`, `Files.ReadWrite.All`, `Sites.Read.All`.
4. Pour SharePoint, faites accorder le consentement par un administrateur si votre annuaire l'exige.
5. Reportez dans AxiorHub l'identifiant de l'application, l'annuaire (tenant) et, pour SharePoint, le site (`valbrune.sharepoint.com:/sites/cabinet`) et la bibliothèque.

### Dropbox

Dans la console des applications Dropbox :

1. **Create app**, API *Scoped access*.
2. Choisissez **Full Dropbox** si vous voulez le cloisonnement : une application limitée à un dossier ne peut pas gérer les partages.
3. Dans **Permissions**, cochez `account_info.read`, `files.metadata.read`, `files.content.read`, `files.content.write` et, pour le cloisonnement, `sharing.read` et `sharing.write`.
4. Ajoutez l'adresse de retour (elle doit être en https).
5. Reportez la clé et le secret dans AxiorHub. Indiquez `oui` à **Demander aussi la gestion des partages** si vous avez coché les portées de partage, et à **Espace d'équipe** si les dossiers doivent aller dans l'espace d'équipe.

## La copie

La copie part d'un stockage vers un autre, en tâche de fond.

- La source n'est **jamais modifiée ni vidée**.
- Chaque fichier est relu à destination et comparé par son empreinte SHA-256.
- Un fichier déjà présent n'est **jamais remplacé** : même contenu, il compte comme identique ; contenu différent, il est signalé.
- La copie peut s'arrêter et reprendre là où elle en était. Une panne du fournisseur la met en pause.
- Une seconde copie, juste avant la bascule, relit tous les fichiers des deux côtés mais ne recopie que ce qui manque.

**Ne sont pas copiés :** les dossiers exclus partout (« secrets », « mots de passe », noms commençant par un point), les dossiers vides, les fichiers vides, et les fichiers de plus de 200 Mo. Ils apparaissent au bilan, sauf les dossiers exclus et les dossiers vides.

Lisez le bilan avant de basculer. Une bascule ordinaire est refusée s'il reste des fichiers en échec, des écarts d'empreinte ou des fichiers modifiés pendant la copie. Une case permet de passer outre, en connaissance de cause.

## Nommage et rangement

Une règle par nature de document : pièce jointe, courrier, note interne, document produit. Par exemple `{date} - {nature} - {dossier} - {titre}`, avec un sous-dossier facultatif comme `Courriers/{annee}`.

- Sans règle, les noms restent ceux d'aujourd'hui.
- Une règle ne vaut que pour les prochains documents et ne renomme jamais un fichier existant.
- À nom égal, le document reçoit un numéro : rien n'est écrasé.

Les règles s'appliquent aux pièces jointes rangées, aux courriers, aux notes internes et aux projets de documents. Elles ne s'appliquent pas encore aux autres sorties (factures, actes corporate, formulaires, bordereaux, dépôts d'audience), qui gardent leur nom.

## Cloisonnement chez le fournisseur

Réserver un dossier dans AxiorHub ne ferme **que** AxiorHub : chez le fournisseur, les accès restent ceux d'avant. La rubrique **Cloisonnement** permet de répercuter la réservation.

1. Indiquez l'identité cloud de chaque membre. Rien n'est deviné.
2. Cliquez sur **Voir ce qui changerait** : rien n'est modifié, chaque accès est classé.
3. Lisez, puis **Confirmez**. Seul un administrateur peut le faire. Si un accès a changé depuis l'aperçu, ou après 30 minutes, rien n'est retiré et il faut refaire l'aperçu.
4. AxiorHub relit les accès et vous montre le résultat. Chaque retrait est tracé au journal d'audit.

| Décision | Ce que cela veut dire |
| --- | --- |
| À retirer | Membre non autorisé, ou accès ouvert sans compte désigné (lien, domaine entier) |
| Conservé | Compte autorisé, administrateur, propriétaire, ou compte relié à AxiorHub |
| Hérité, non retiré | L'accès vient d'un dossier parent commun à d'autres dossiers |
| Dossier commun, non retiré | Ce dossier contient d'autres dossiers du cabinet |
| À vérifier, non retiré | Personne extérieure, groupe, ou membre sans correspondance |

**Ce qu'AxiorHub ne fait jamais :** ajouter un accès, créer un lien de partage, toucher à un dossier parent, ou agir sans confirmation. Lever une réservation ne rend aucun accès : il faut les redonner chez le fournisseur. Les retraits eux-mêmes ne s'annulent pas depuis AxiorHub.

**Attention : un dossier peut rester ouvert après confirmation.** Un accès hérité n'est pas retiré. Sur SharePoint et sur un Drive partagé, les membres viennent souvent du site ou du Drive : il faut alors rompre l'héritage chez le fournisseur (ou utiliser l'accès limité de Google Drive). Lisez les lignes « Hérité » de l'aperçu : tant qu'il en reste, le dossier n'est pas fermé.

**Limites :**

- Le parcours s'arrête à 300 éléments par dossier ; l'aperçu le signale.
- Sur Dropbox, seuls les dossiers partagés sont examinés, pas les membres posés sur un fichier isolé.
- **Comptes personnels** : le cloisonnement n'est pas proposé pour OneDrive personnel ni pour Dropbox Basic, Plus ou Professional. L'interface l'indique, et le retrait se fait à la main.
- **Nextcloud** : aucune propagation. Ses partages, dossiers de groupe et partages hérités ne se lisent pas assez sûrement pour un retrait automatique.

## Différences avec Nextcloud

**Propres à Google Drive**

- Drive accepte deux fichiers du même nom dans un dossier. AxiorHub lit le plus ancien, signale le doublon et refuse d'écrire sur ce nom. Il faut régler le doublon dans Drive.
- Drive n'a pas de création exclusive. AxiorHub l'obtient par détection : il crée, relit, et retire sa propre copie s'il arrive second. Deux écritures simultanées peuvent donc faire apparaître brièvement deux fichiers.
- La protection contre l'écrasement passe par l'ancienne API Drive v2, seule à exposer l'étiquette de version. Si Google la retire, le remplacement sera refusé, sans jamais écraser. Le contrôle de compte vérifie ce point.
- Les Google Docs, Sheets et Slides apparaissent en `.docx`, `.xlsx` ou `.pptx`. Ils sont exportés à la lecture et restent en lecture seule. Une copie vers un autre stockage en emporte l'export, pas l'original.
- Les raccourcis et les formats Google rares sont ignorés et signalés.
- L'historique des versions de Drive est limité dans le temps ; il ne vaut pas sauvegarde.

**Propres à SharePoint et OneDrive**

- Les noms contenant `" * : < > ? \ |`, ou finissant par un point ou un espace, sont refusés avec un message clair.
- La version change aussi quand un élément est renommé ou que ses métadonnées changent : l'éditeur peut alors enregistrer une copie plutôt que remplacer. C'est le comportement prudent.

**Pour tous**

- Les **agendas** ne changent pas : ils restent en CalDAV. Sans accès CalDAV renseigné, ils sont indisponibles, comme avec un dossier local.
- L'**éditeur de documents** fonctionne : il lit et enregistre par AxiorHub, et une modification concurrente donne une copie, sans écrasement.
- L'**index des documents** fonctionne, ainsi que la **pseudonymisation**, qui porte sur le texte lu.
- « Ouvrir » renvoie vers la page du fournisseur, pour les comptes qui y ont déjà accès. Aucun lien de partage n'est créé.
- Les limitations de débit sont reprises automatiquement, puis abandonnées avec une erreur claire si l'attente est trop longue.

**Ce qui reste lié à Nextcloud :** Deck, Notes, News, Talk, l'application Nextcloud et l'ExApp. Sans accès Nextcloud renseigné, ces fonctions ne marchent pas. Les liens « Ouvrir ce dossier dans Nextcloud » restent affichés même si le stockage en service est un cloud : ils mènent alors à l'ancien emplacement.

## En cas de problème

- **Les documents sont inaccessibles** : la connexion a peut-être expiré. Reconnectez le compte. AxiorHub ne repasse jamais seul sur Nextcloud, pour ne pas écrire au mauvais endroit.
- **« Le choix du stockage est illisible »** : le fichier `cloud5688.json` du dossier d'état est abîmé. Les accès aux fichiers sont arrêtés volontairement ; choisissez de nouveau le stockage.
- **Déconnexion** : Google et Dropbox révoquent le jeton. Microsoft ne le permet pas pour un jeton isolé : les jetons sont effacés du coffre, mais retirez aussi le consentement dans le compte Microsoft ou dans Entra.
- **Sauvegarde** : le second emplacement des sauvegardes est un réglage distinct. Un stockage cloud n'est pas une sauvegarde : une suppression ou un rançongiciel s'y propagent aussi.
