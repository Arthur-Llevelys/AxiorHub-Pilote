# AxiorHub Pilote 5.6.9 — installation et réglages

Version cumulative du 6 octobre 2026, sur le socle 5.6.8. Une seule archive contient l'application entière ; les comptes, modèles,
corrections, dossiers, index et secrets existants sont conservés par la migration. Le guide [INSTALLATION-5.6.8.md](INSTALLATION-5.6.8.md)
reste la référence pour la première mise en place des fonctions 5.6.7/5.6.8 ; ce guide décrit ce qui change en 5.6.9.

## 1. Contrôler, puis installer

```bash
sha256sum -c axiorhub-mail-agent-5.6.9.tar.gz.sha256
tar -xzf axiorhub-mail-agent-5.6.9.tar.gz
cd axiorhub-mail-agent-5.6.9
sudo apt-get install -y python3-cryptography espeak-ng
sudo bash install.sh
sudo python3 /opt/axiorhub-mail-agent/current/install-interface.py
```

Mise à niveau directe acceptée depuis 3.5.0 à 5.6.8. Retour arrière : `sudo python3 /opt/axiorhub-mail-agent/current/upgrade.py --rollback`.
Docker : conserver `.env` et `data/`, remplacer le code par le paquet complet, `docker compose up -d --build`.

## 2. Vérifier la mise en service

Outils › **Mise en service** → « Lancer les contrôles ». Chaque ligne en échec indique le correctif (page ou commande). La même
vérification existe en ligne de commande :

```bash
sudo python3 /opt/axiorhub-mail-agent/current/manage.py readiness
```

Sous Docker : `docker compose exec axiorhub python3 manage.py readiness`. Le test du micro et du HTTPS se fait depuis le navigateur sur
cette page (aucun son transmis). Rappel : le micro exige HTTPS hors localhost.

## 3. Régime économe

Activé d'office. Outils › IA externe sûre › « Régime économe et consommation » :

- case « Régime économe » et quota journalier des analyses automatiques (20 par défaut) ;
- « Appliquer le profil économe de modèles » route le tri, la lecture des pièces jointes, la conversation vocale et le contrôle vers le
  plus petit modèle de conversation installé dans Ollama (par exemple `qwen3:4b` à côté de `qwen3:27b`). Installez-le d'abord :
  `ollama pull qwen3:4b` (ou tout modèle léger de votre choix) ;
- le tableau « Consommation des 7 derniers jours » commence à se remplir après l'installation.

Pour revenir au régime complet : décocher la case et enregistrer.

## 4. Accueil téléphonique conversationnel

Paramètres › Connexions › Accueil administratif :

1. « Activer Twilio Voice entrant », URL exacte du webhook et Auth Token (inchangés depuis 5.6.7).
2. « Accueil conversationnel » **et** « J'accepte que la voix de l'appelant soit transcrite par Twilio ». Sans les deux cases, l'accueil
   par touches reste actif. La reconnaissance vocale est un service externe facturé par Twilio (≈ 0,02 $ par tranche de 15 s).
3. Dans la console Twilio, le webhook vocal doit pointer vers `https://…/reception567/twilio` en POST ; les étapes suivantes
   (`?step=1`, `?step=2&intent=…`) sont ajoutées par AxiorHub et signées.

## 5. Invoice Ninja en écriture

Paramètres › Connexions › Invoice Ninja : « Autoriser la création de factures en brouillon… ». Puis, dans Cabinet › Temps et honoraires
› un dossier : lier le client Invoice Ninja (section Impayés), renseigner le taux horaire dans les conditions, valider des temps, et :

- « Créer la facture en brouillon » : une facture par lot de temps non encore facturés ; vous la relisez et l'envoyez dans Invoice Ninja ;
- « Transmettre » un temps validé : une tâche Invoice Ninja avec sa durée.

Le jeton API doit avoir le droit d'écriture. Une écriture « à vérifier dans Invoice Ninja » n'est jamais rejouée automatiquement.

## 6. Règles documentaires

Dans Agents et règles, chaque règle a désormais un « Sous-dossier de classement » (PROCEDURE par défaut). Les exécutions qui attendent
une décision apparaissent sur Aujourd'hui, en tête de page, avec les dossiers suggérés.

## 7. Recette rapide après installation

1. Aujourd'hui : plus de rechargement intempestif ; sur téléphone, le menu est replié et s'ouvre par ☰.
2. Mise en service : lancer les contrôles, corriger ce qui est en échec, tester le micro.
3. IA externe sûre : vérifier que le régime économe est coché ; appliquer le profil de modèles si un petit modèle est installé.
4. Agents et règles : relire les règles par défaut, choisir le sous-dossier, activer ; déposer un avis de renvoi dans un dossier
   d'arrivée dont le dossier est ambigu : le bloc « À décider » doit le proposer avec des dossiers suggérés.
5. Voix : démarrer le dialogue ; la réponse est lue phrase par phrase ; parler l'interrompt.
6. Téléphone (si activé) : appeler le numéro, dire « je souhaite un rappel », donner son nom : une tâche « Demande de rappel » apparaît.
7. Invoice Ninja (si activé) : créer une facture en brouillon sur un dossier de test, vérifier dans Invoice Ninja qu'elle est en brouillon
   et qu'aucun courriel n'est parti.
