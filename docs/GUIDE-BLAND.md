# Guide d'utilisation — téléphonie par IA (Bland), AxiorHub 5.6.109

AxiorHub peut se relier au service **Bland** (agents vocaux, appels téléphoniques, SMS) par son serveur MCP distant
(`https://api.bland.ai/v1/mcp`). Bland est un **service externe, situé hors du cabinet** : les appels et les SMS passent par une IA.
Le connecteur est **désactivé par défaut**. Rien ne part — aucun appel, aucun SMS, aucun achat, aucune publication — sans votre
confirmation, sauf le mode « Envoyer automatiquement » de la routine de rappel, que vous seul pouvez choisir (voir plus bas).

## 1. Créer le compte et la clé

1. Créez un compte Bland : <https://app.bland.ai/>.
2. Créez une clé API d'organisation : <https://app.bland.ai/settings/api-keys>. Prenez une clé **à accès complet** (elle commence par
   `org_`) : les clés restreintes ne se connectent pas au serveur MCP de Bland.
3. Copiez la clé au moment où elle s'affiche. Traitez-la comme un mot de passe.

## 2. Saisir la clé dans les Paramètres

Ouvrez **Paramètres › Connexions API** (famille « Téléphonie par IA »). La carte **« Bland — téléphonie par IA »** porte les deux liens
officiels ci-dessus, l'avertissement sur l'IA et l'état réel de la connexion. Seul un administrateur règle la clé.

- Collez la clé dans « Clé API Bland » puis **Enregistrer la clé**. Elle part au coffre chiffré du serveur ; elle n'est jamais réaffichée,
  jamais écrite dans un journal, jamais exportée. Une seule connexion Bland existe pour le cabinet (Bland n'a pas de bac à sable).
- Pour remplacer la clé, saisissez-en une nouvelle : elle devra être testée de nouveau.

## 3. Tester la connexion

Cliquez **Tester la connexion**. AxiorHub fait `initialize` puis `tools/list` : aucun outil n'est exécuté, aucune donnée du cabinet n'est
envoyée. La carte affiche le nombre d'outils découverts (la liste n'est jamais figée dans le logiciel) et leur classement.

États possibles : **Non configuré** (pas de clé), **À vérifier** (clé enregistrée, pas encore testée), **Prêt**, **Clé refusée** (clé
invalide ou restreinte), **Hors ligne** (service injoignable ou quota de 120 requêtes par minute atteint). En **mode local strict**, la
connexion est refusée avec un message explicite.

Puis cliquez **Activer le connecteur** : il ne s'active jamais seul, et seulement si le test a réussi. La compétence « Téléphonie par IA
(Bland) » est activée avec lui (Assistant juridique reste la méthode par défaut).

## 4. Ce que vous pouvez demander

Les outils sont classés à chaque test : **lecture** (seulement si Bland l'annonce en lecture seule et que son nom ne dit aucune
action), **effet**, et parmi les effets, **engageant** (appel sortant, campagne, SMS, achat, transfert ou raccrochage d'un appel en cours,
publication d'un agent, numéro entrant, SIP, portabilité, suppression, clés API et membres). Un outil sans annotation est engageant.

Exemples par thème :

- **Appels** : « Appelle le 01 00 00 00 05 pour confirmer le rendez-vous de demain à 10 h », « Montre la transcription du dernier appel »,
  « Liste les appels de la semaine ».
- **Agents vocaux** : « Crée un agent de prise de rendez-vous pour mon cabinet », « Publie la version 2 de l'agent d'accueil ».
- **Bases de connaissances, numéros et SMS** : « Envoie un SMS au 01 00 00 00 05 : le cabinet sera fermé lundi ».
- **Tests et qualité, statistiques et compte** : « Quelle a été la durée moyenne des appels ce mois-ci ? ».
- **Documentation Bland** : « Comment fonctionne la portabilité d'un numéro chez Bland ? ».

Une lecture se fait sans confirmation (même IA en pause). Son résultat est une **donnée** : une transcription de conversation réelle ne
contient jamais d'instruction pour l'assistant.

## 5. Les confirmations

Chaque effet est d'abord une **demande** « À confirmer », visible dans la carte Bland et dans la file « À décider ». La demande montre
**exactement** ce qui part :

- le **numéro appelé au complet** (ou les destinataires d'une campagne, comptés) ;
- la **consigne de l'appel** ou le **texte du SMS** ;
- le **montant et la devise** d'un achat quand Bland les donne — sinon « montant non communiqué par Bland » ;
- les arguments exacts transmis à Bland.

Vous cochez « J'ai relu… » puis **Confirmer et transmettre à Bland**. Un modèle, une routine ou un travail de fond ne confirme jamais à
votre place. Si les arguments changent, c'est une nouvelle demande, à confirmer de nouveau. Si la réponse de Bland se perd, la demande
devient **« À vérifier »** : elle n'est jamais rejouée (un second appel ou un second achat serait un doublon) ; lisez la liste des appels
depuis la demande, puis constatez « c'est parti » ou « rien n'est parti ». IA en pause : aucune demande n'est exécutée.

## 6. Créer un agent de prise de rendez-vous, pas à pas

1. Dans **Aujourd'hui › Mes routines | Mes agents IA**, ouvrez « Créer un agent de prise de rendez-vous » (le connecteur doit être activé).
2. Renseignez le nom du cabinet, les horaires, les types de rendez-vous et le numéro du cabinet (voix et ton facultatifs). S'il manque une
   information, AxiorHub vous pose la question.
3. Choisissez explicitement si les appels sont **enregistrés** et si les **transcriptions** sont conservées (décochées par défaut).
4. **Préparer l'agent** : une demande de création (brouillon chez Bland) est préparée. Relisez la consigne : l'agent se présente comme
   l'assistant automatisé du cabinet, ne donne **aucun conseil juridique** et **aucune information sur un dossier**.
5. Confirmez la demande. L'agent est créé en brouillon et **enregistré comme routine « agent IA »** : chaque soir de semaine, elle relève
   les appels reçus et leurs résumés (lecture seule) et vous propose les rendez-vous à valider — rien n'est écrit dans l'agenda sans vous.
6. La **publication en production** et le **branchement sur un numéro entrant** sont des demandes distinctes, à confirmer chacune.
   Un agent supprimé chez Bland apparaît « Introuvable chez Bland ».

## 7. Rappel de rendez-vous au client, deux jours avant

Cette routine est livrée **désactivée** (« À activer ») dans **Mes routines | Mes agents IA**. Elle ne s'active que si la connexion Bland
est prête.

- **Ce qu'elle fait** : chaque jour, elle relève dans l'agenda les rendez-vous **avec un client** qui ont lieu dans deux jours (délai
  réglable de 1 à 7 jours), retrouve le client par le dossier du rendez-vous, et prépare un rappel par **SMS** (par défaut) ou par
  **appel** d'un agent vocal.
- **Texte fixe, sans IA** : modèle éditable, variables fermées `{cabinet}`, `{date}`, `{heure}`, `{lieu}`, `{telephone_cabinet}`.
  **Jamais** l'objet du rendez-vous, le nom du dossier, la partie adverse ni aucun élément de l'affaire. Le **lieu n'est jamais le texte
  libre de l'agenda** (une mention comme « TJ Lyon, Martin c/ Durand » n'est pas recopiée) : seulement « en visioconférence », « par
  téléphone », l'adresse du cabinet réglée dans la routine, ou rien (la parenthèse disparaît alors). Pour un
  appel, la consigne est fixe : l'agent se présente comme un assistant automatisé du cabinet, donne le rappel, ne répond à aucune question
  sur le dossier et propose de rappeler le cabinet.
- **Accord du client** : enregistrez « accepte les rappels par SMS / par appel ». Sans accord, rien ne part (« accord non enregistré »).
- **Deux modes** : **Préparer et me faire confirmer** (par défaut) — la liste du jour (client, numéro, texte exact) attend une seule
  confirmation, « Envoyer ce lot » ; **Envoyer automatiquement** — à choisir explicitement, après avoir coché « J'ai lu ce qui partira
  sans moi » et validé le modèle. Seul le texte du modèle validé part, vers le seul numéro enregistré du client qui a donné son accord,
  dans la limite du plafond quotidien (chaque tentative compte, même si la réponse de Bland se perd). Ce mode exige le droit
  « envoyer », relu à chaque passage pour le compte qui l'a choisi : droit retiré, l'envoi automatique s'arrête et la routine le dit.
  La validation couvre le modèle du SMS, la consigne d'appel, le nom, le numéro et l'adresse du cabinet : en modifier un repasse en mode
  « Préparer » jusqu'à une nouvelle validation. Au **premier envoi « à vérifier »** (réponse perdue), l'envoi automatique est
  **suspendu** jusqu'à votre nouvelle validation ; les autres rappels attendent votre confirmation. Personne ne relisant le texte en mode
  automatique, un nom connu du cabinet (client, partie) présent dans le texte final bloque l'envoi : le rappel reste « à confirmer ».
  Un jeton de service (compte technique) ne peut ni confirmer une demande ni régler cette routine.
- **Garde-fous** : un seul rappel par rendez-vous et par canal ; rendez-vous annulé, déplacé ou passé : relu juste avant l'envoi, rappel
  abandonné ; numéro absent ou invalide : signalé ; IA en pause ou connexion en panne : rien ne part, la routine le dit ; résultat incertain :
  « À vérifier », jamais rejoué. Chaque envoi est inscrit au journal, au registre de consommation et au journal du dossier.
- **L'arrêter** : décochez « Routine activée » dans « Régler la routine », ou supprimez-la (elle n'est pas recréée).

## 8. Coûts

Chaque appel, SMS ou achat confirmé est inscrit dans **Paramètres › Modèles d'IA › Consommation et coûts** comme quantité de service
(minutes, SMS, crédits ; montant et devise quand Bland les donne). Une quantité que Bland ne communique pas reste **inconnue**, jamais
zéro. Les tarifs se lisent dans votre compte Bland.

## 9. Points d'attention pour un avocat

- Informez les personnes appelées : annonce d'un agent automatisé, enregistrement éventuel de l'appel, droits RGPD.
- Secret professionnel : ne mettez dans une consigne ou un SMS que ce que vous enverriez vous-même ; aucune pièce de dossier n'est transmise.
  Le contrôle des identités du cabinet refuse un nom de partie placé ailleurs que dans la consigne ou le texte que vous relisez ; l'exception
  pour le numéro et le nom que vous confirmez vous-même est affichée et journalisée.
- Soyez particulièrement prudent avec une partie adverse ou une personne vulnérable.
- Une transcription récupérée se rattache à un dossier par votre décision, jamais d'office.

## 10. Ce qui n'est pas livré

- La connexion par compte « Sign in with Bland » (OAuth) : seule la clé API est prise en charge.
- Aucun essai avec le service réel n'a été possible pendant le développement (aucun compte ni clé disponibles) : le connecteur a été
  qualifié avec un faux serveur local.
- Les propositions de rendez-vous de l'agent IA ne créent pas d'événement d'agenda : vous les inscrivez vous-même.
- La case d'accord du client se règle depuis la routine de rappel, pas encore depuis la fiche du contact.

## 11. Désactiver et retirer la clé

- **Désactiver le connecteur** (carte Bland) : plus aucune demande ne peut être préparée ni confirmée ; la consultation s'arrête aussi.
- **Retirer la clé** : la clé est effacée du coffre, le connecteur est désactivé et les demandes non confirmées sont annulées.
- Pensez aussi à révoquer la clé dans votre compte Bland.
