# Mode de l'IA, connexion et restauration (5.6.82)

Réglage : Paramètres › Modèles d'IA, bloc « Mode de l'IA ». Une mise à jour ne change rien : les choix par fonction restent en place tant qu'aucun mode n'est retenu.

## Choisir un mode

| Mode | Appels à l'IA | Tâches simples | Rédaction courante | Travaux complexes | En cas de limite ou de panne |
|---|---|---|---|---|---|
| Abonnement ChatGPT uniquement | Votre abonnement, sans clé API | GPT-6 Luna | GPT-6.1 Terra | GPT-6.1 Sol | Travail reporté, aucun autre fournisseur |
| API Mistral uniquement | Votre clé Mistral | Mistral Small | Mistral Large | Mistral Large | Travail à vérifier, aucun autre fournisseur |
| API Claude uniquement | Votre clé Anthropic | Claude Haiku | Claude Sonnet | Claude Opus | Travail à vérifier, aucun autre fournisseur |
| IA locale uniquement | Modèles locaux | selon vos réglages | selon vos réglages | selon vos réglages | Attente ou incident, rien ne sort |
| Automatique parmi mes connexions | Connexions cochées | le local d'abord, puis l'abonnement, puis l'API | | | Repli dans cette liste seulement |

Les modèles viennent de la liste du fournisseur pour votre compte : un nom absent de cette liste n'est jamais appelé. GPT-6 Astra et Claude Fable restent une option du volet avancé.

Dans tous les modes externes, les textes sont pseudonymisés avant l'envoi et les dossiers exclus ne partent pas.

## Consommation automatique

Sans courriel, fichier ou événement nouveau, AxiorHub n'appelle aucun modèle. Les contrôles de santé et les tests ne génèrent aucun texte.

Chaque nouveauté peut entraîner plusieurs appels : tri, réponse, contrôle, et parfois un projet. Le volet avancé plafonne donc les appels automatiques (300 par jour, dont 30 sur modèles avancés, par défaut). Plafond atteint, ces travaux attendent le lendemain. Vos propres demandes ne sont ni comptées ni bloquées. Le journal « Choix de l'IA » montre chaque appel.

Pour réduire encore : désactivez la préparation automatique des projets d'actes (Paramètres, automatismes), qui concentre les appels les plus coûteux.

## Mode strict ChatGPT

1. Section ChatGPT : « Continuer avec ChatGPT », puis acceptez l'usage de votre forfait.
2. Bloc « Mode de l'IA » : retenez l'abonnement, cochez l'autorisation d'envoi, enregistrez.
3. Ollama n'est plus nécessaire. La recherche dans les pièces devient textuelle, sauf option « Recherche par le sens ».

La consommation passe par votre forfait ChatGPT et ses limites, partagées avec vos autres usages. Quand AxiorHub reçoit un refus pour limite, il reporte les travaux (15 minutes, puis jusqu'à 4 heures). Il ne connaît pas votre solde : ce qui n'est pas fourni s'affiche « Non fournie ».

Quota et crédits ne se distinguent pas toujours à l'usage. Pour rester dans votre forfait, réglez les limites de l'application dans ChatGPT › Paramètres › Utilisation. AxiorHub n'achète jamais de crédits.

## Connecter, puis activer (5.6.97)

Se connecter et activer sont deux gestes séparés, dans Paramètres › Modèles d'IA comme dans le guide d'installation (étape IA, « Connecter l'IA ») :

1. **Connecter** : Mistral Studio (clé) ou ChatGPT (abonnement). L'écran affiche « Clé reconnue », « Abonnement autorisé », « Compte connecté, abonnement non autorisé », « Limite atteinte » ou « À reconnecter », avec la date du dernier contrôle. Se connecter ou tester ne change ni le mode, ni les routes, ni les automatismes, et ne lit aucun dossier.
2. **Activer** : bloc « Mode de l'IA », choix du mode et autorisation d'envoi, enregistrés à part.

Mistral : ouvrez Mistral Studio (https://console.mistral.ai, section « API keys »), créez une clé dédiée dans l'organisation abonnée, collez-la une fois, « Tester la connexion », puis « Enregistrer la clé ».

## ChatGPT sur un poste (Windows, Ubuntu)

« Continuer avec ChatGPT » ouvre la page d'OpenAI dans un nouvel onglet. AxiorHub reçoit la réponse sur le poste lui-même et l'écran se met à jour sans recharger la page : « Connexion en cours », puis « Abonnement autorisé » (ou le motif d'un échec). « Annuler » arrête la tentative. Si le navigateur bloque l'onglet, un lien « Ouvrir la page d'OpenAI » s'affiche.

## ChatGPT sur un serveur : liaison avec AxiorHub Poste

Le retour de connexion d'OpenAI atteint la machine où tourne le navigateur, pas le serveur. Quand l'option de déploiement `"liaison_ia": {"active": true}` est présente dans la configuration du serveur, un poste où AxiorHub Poste est installé fait la connexion pour lui. Aucun port n'est ouvert sur Internet : le poste appelle le serveur en HTTPS, jamais l'inverse.

Une fois pour toutes, sur le poste : Paramètres › Modèles d'IA › « Serveur du cabinet », saisissez l'adresse du serveur avec son préfixe (exemple fictif : `https://pilote.example.test/agent-courriel`) et confirmez. Seuls les serveurs confirmés ici sont contactés, avec un certificat valide.

Parcours (comptes fictifs : administratrice `admin@example.test`, cabinet Valbrune) :

1. Sur le serveur, « Continuer avec ChatGPT », puis « Ouvrir AxiorHub Poste » : le navigateur demande d'ouvrir AxiorHub Poste.
2. Sur le poste, « Associer ce poste » : un code court s'affiche (par exemple `482 913`). Le même code apparaît sur l'écran du serveur.
3. Sur le serveur, si les deux codes sont identiques : « Les codes sont identiques : autoriser ». Cochez « Remplacer le compte ChatGPT connecté » seulement pour accepter un autre compte que celui déjà connecté.
4. Le poste ouvre la page d'OpenAI : connectez-vous et acceptez l'utilisation de l'abonnement.
5. Le serveur vérifie la session reçue (signature, compte, droit d'utiliser le forfait, court test « Réponds uniquement OK »), puis l'écran du serveur affiche « Abonnement autorisé ». Rien n'est à recopier.

Le code court n'est ni un mot de passe ni le jeton du poste : il sert seulement à vérifier que le poste qui répond est bien le vôtre. La liaison expire après dix minutes sans approbation ; se déconnecter du serveur ou « Annuler » la ferme. En cas d'échec, d'annulation ou si le poste disparaît, la connexion déjà en service sur le serveur reste en place. Ensuite, le serveur gère seul les renouvellements ; le poste efface sa copie temporaire après l'accusé de réception et garde sa propre connexion.

Si AxiorHub Poste n'est pas installé ou ne connaît pas le serveur, l'écran du serveur le signale après trente secondes et renvoie aux autres méthodes.

## Autres méthodes (inchangées)

Sous « Autres méthodes », dans la section ChatGPT :

- **Copier l'adresse de retour** dans la section ChatGPT du serveur (« Ouvrir la page d'OpenAI (procédure manuelle) », puis « Terminer la connexion »).
- **Transférer une connexion faite sur un poste** (procédure officielle) : sur le poste, « Transférer cette connexion vers le serveur du cabinet », avec une phrase de passe ; collez le paquet chiffré sur le serveur, « Recevoir la connexion ». Le poste cesse d'utiliser cette connexion et le serveur garde son propre identifiant.

Ne placez jamais le paquet dans une adresse, un courriel non chiffré ou une capture.

## Revenir à la version précédente

Avant la mise à jour, l'installateur sauvegarde la base et la configuration. Un retour à la 5.6.81 conserve dossiers, brouillons, documents et comptes : les nouvelles tables sont ignorées par l'ancienne version. Le réglage `ai:politique` est alors sans effet : les choix par fonction reprennent la main.

Pour quitter un mode sans changer de version : choisissez un autre mode, ou activez le mode mixte. La politique cède alors la place aux choix par fonction.
