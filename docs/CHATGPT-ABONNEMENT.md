# ChatGPT avec votre abonnement (sans clé API)

Depuis la 5.6.62, AxiorHub Pilote peut utiliser votre abonnement **ChatGPT Plus ou Pro** pour la rédaction et l'analyse, sans clé API.
Il passe par le programme officiel d'OpenAI « Sign in with ChatGPT » destiné aux logiciels libres hébergés par l'utilisateur
(https://developers.openai.com/siwc). AxiorHub (licence AGPL-3.0, installé chez le cabinet) en relève.

## Connecter

1. **Paramètres › Modèles d'IA › ChatGPT (votre abonnement)**, puis **« Continuer avec ChatGPT »** (administrateur).
2. La page officielle d'OpenAI s'ouvre dans un nouvel onglet. Connectez-vous et acceptez que AxiorHub utilise votre abonnement.
3. Ensuite :
   - **Poste (Windows 11, Ubuntu poste)** : AxiorHub reçoit seul la réponse d'OpenAI. Rechargez la page Paramètres.
   - **Serveur** : votre navigateur affiche une page introuvable dont l'adresse commence par `http://127.0.0.1:1455/auth/callback`.
     C'est normal. Copiez cette adresse complète dans « Après la connexion » et cliquez sur « Terminer la connexion ». Elle ne
     contient qu'un code à usage unique, inutilisable sans le secret resté sur le serveur.
4. Choisissez le modèle, cochez « Utiliser ChatGPT dans AxiorHub » et « J'autorise l'envoi des textes, pseudonymisés, à OpenAI »,
   puis enregistrez.
5. Confiez à ChatGPT les fonctions voulues avec « Mode mixte » ou « Routage » (même page). Le tri des courriels et la lecture des
   pièces jointes peuvent rester sur le modèle local.

## Ce qui est protégé

- **Pseudonymisation** des textes envoyés (noms, adresses, identifiants), **bouclier** contre les instructions cachées, **dossiers
  exclus** toujours traités en local, autorisation « données externes » du cabinet exigée : exactement comme pour un fournisseur à
  clé API.
- AxiorHub demande à OpenAI de ne pas conserver les échanges (`store: false`).
- Les jetons de connexion sont **chiffrés** dans le coffre d'AxiorHub ; ils se renouvellent seuls (accès : 1 heure ; renouvellement :
  30 jours glissants). Le jeton d'identité d'OpenAI est vérifié (signature, émetteur, destinataire, expiration, nonce).
- **Déconnecter** révoque l'accès chez OpenAI puis efface les jetons. Vous pouvez aussi retirer l'accès dans ChatGPT › Paramètres.

## À savoir

- Dans ChatGPT › Paramètres › Gestion des données, **désactivez « Améliorer le modèle pour tous »**.
- Les demandes d'AxiorHub sont **décomptées de votre abonnement** ; avec Plus, la limite sur 5 heures est commune à ChatGPT et aux
  applications connectées. Suivi et limites : https://chatgpt.com/settings/usage (« Gérer l'usage »). Limite atteinte : AxiorHub le
  dit clairement et ne bascule jamais en silence vers un autre fournisseur.
- Un abonnement ChatGPT est personnel : chaque avocat devrait utiliser son propre compte. Dans cette version, une seule connexion
  est enregistrée par installation d'AxiorHub.
- Le programme est en aperçu : OpenAI peut changer les modèles, limites ou conditions. Gardez un modèle local en secours.
