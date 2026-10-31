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

## Connexion sur un serveur

Le retour de connexion d'OpenAI atteint la machine où tourne le navigateur, pas le serveur. Deux chemins :

- **Copier l'adresse de retour** dans la section ChatGPT du serveur (chemin déjà en place).
- **Transférer une connexion faite sur un poste** (procédure officielle) : sur le poste, « Transférer cette connexion vers le serveur du cabinet », avec une phrase de passe ; collez le paquet chiffré sur le serveur, « Recevoir la connexion ». Le poste cesse d'utiliser cette connexion et le serveur garde son propre identifiant.

Ne placez jamais le paquet dans une adresse, un courriel non chiffré ou une capture.

## Revenir à la version précédente

Avant la mise à jour, l'installateur sauvegarde la base et la configuration. Un retour à la 5.6.81 conserve dossiers, brouillons, documents et comptes : les nouvelles tables sont ignorées par l'ancienne version. Le réglage `ai:politique` est alors sans effet : les choix par fonction reprennent la main.

Pour quitter un mode sans changer de version : choisissez un autre mode, ou activez le mode mixte. La politique cède alors la place aux choix par fonction.
