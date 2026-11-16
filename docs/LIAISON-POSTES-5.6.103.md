# Liaison directe entre postes (5.6.103)

Les postes de travail AxiorHub Pilote (Windows et Ubuntu, mode poste) peuvent se relier directement entre eux sur le réseau local du
cabinet, **sans serveur**.

## Ce qui passe, ce qui ne passe pas

- Passe : messages et discussions (à deux, groupes, mentions), réponses de Pilote, pièces jointes (25 Mo au plus par fichier,
  empreinte SHA-256 vérifiée à l'arrivée), présence (en ligne, absent, hors ligne avec la dernière vue), réglages partagés
  (fournisseurs d'IA avec leur clé d'API, connecteurs MCP, connexions API du cabinet).
- Ne passe pas : dossiers, agenda, tâches (Nextcloud ou le serveur s'en chargent), identifiants personnels (mots de passe de
  messagerie, jetons OAuth, sessions, abonnement ChatGPT).
- Pilote : c'est toujours le poste de la personne qui écrit « @pilote » qui fait tourner l'IA ; sa réponse part ensuite vers les
  autres postes comme un message.

## Activation

1. Paramètres › Confort et maintenance › **Liaison entre postes du cabinet** : cocher « Activer la liaison entre postes », Enregistrer
   (compte administrateur du poste). La liaison est **désactivée par défaut** : sans activation, aucun port n'est ouvert au réseau.
2. Donner au poste un nom lisible (« Poste de Me DURAND »).
3. Appairer : sur un poste, « Afficher un code » (8 chiffres, valable 10 minutes, 5 essais, usage unique) ; sur l'autre, choisir le
   poste découvert ou saisir son adresse IP à la main, puis taper le code. Chaque poste garde ensuite la clé publique de l'autre.
4. Dans la Messagerie instantanée, les postes appairés apparaissent comme correspondants (« Nouvelle conversation », « Nouveau groupe »).
5. Réglages partagés : sur un poste administrateur, « Diffuser aux postes » pour UN réglage ; chaque poste destinataire choisit
   « Appliquer » ou « Refuser » (décision journalisée). Un connecteur MCP reçu reste à tester et à activer sur le poste qui le reçoit.

Seules les adresses privées sont acceptées : 10.x.x.x, 172.16.x.x à 172.31.x.x, 192.168.x.x, et en IPv6 fc00::/7 et fe80::/10.
Par exemple, une adresse qui commence par 192 sans se poursuivre par 168 n'est pas une adresse privée : elle est refusée avec un
message clair.

## Pare-feu

La liaison écoute sur le port **TCP 47862** (une seule adresse privée du poste, jamais toutes les interfaces) et la découverte
utilise le port **UDP 47863** (diffusion limitée au réseau local).

- **Windows** : au premier démarrage de la liaison, Windows peut demander d'autoriser « AxiorHub Pilote » : cocher **Réseaux privés**
  seulement. À défaut, un administrateur Windows peut créer les règles (PowerShell en administrateur) :

  ```
  New-NetFirewallRule -DisplayName "AxiorHub Pilote - liaison entre postes" -Direction Inbound -Protocol TCP -LocalPort 47862 -Profile Private -RemoteAddress LocalSubnet -Action Allow
  New-NetFirewallRule -DisplayName "AxiorHub Pilote - découverte des postes" -Direction Inbound -Protocol UDP -LocalPort 47863 -Profile Private -RemoteAddress LocalSubnet -Action Allow
  ```

  Le réseau du cabinet doit être classé « Privé » dans Windows. L'installateur ne crée pas ces règles en 5.6.103 : la liaison est
  désactivée par défaut et l'ajout d'une règle de pare-feu demande les droits d'administration de Windows au moment de
  l'installation ; c'est laissé à une version ultérieure.
- **Ubuntu** (si `ufw` est actif) : `sudo ufw allow from 192.168.0.0/16 to any port 47862 proto tcp` et la même règle pour
  `47863 proto udp` (adapter la plage à celle du cabinet).

Si la découverte est bloquée (réseau Wi-Fi isolant les postes, commutateur filtrant la diffusion), l'adresse saisie à la main suffit.

## Sécurité, en bref

- Identité de chaque poste : clés Ed25519 (signature) et X25519 (chiffrement des secrets), clés privées au coffre chiffré du poste.
- Appairage : SPAKE2 (RFC 9382) avec le code ; un attaquant ne peut tester qu'un code par tentative.
- Chaque session : clés éphémères X25519, signatures des deux postes sur la transcription, puis trames ChaCha20-Poly1305 numérotées
  (une trame rejouée, réordonnée ou altérée ferme la session).
- Secrets des réglages partagés : chiffrés de bout en bout pour chaque poste destinataire ; jamais écrits en clair dans la base ni dans
  le journal ; rangés au coffre existant au moment où le destinataire clique sur « Appliquer ».
- Rien n'est envoyé hors du réseau local ; aucune relève, aucun service tiers.

## Limites

- **Poste éteint** : un message pour un poste éteint attend sur le poste qui l'a écrit (« en attente de remise ») et part au retour
  du destinataire, si l'expéditeur est lui aussi allumé à ce moment-là. C'est la seule vraie limite d'une liaison sans serveur.
- Groupes : seul le poste qui a créé un groupe relié en change les membres ; un poste ne remet ses messages qu'aux postes avec
  lesquels il est appairé (chaque paire de postes s'appaire une fois).
- Un message retiré par son auteur n'est retiré que sur son propre poste.
- La découverte automatique est en IPv4 ; en IPv6, saisir l'adresse à la main.
- Si l'adresse IP d'un poste change (DHCP), la liaison de ce poste se remet à l'écoute sur la nouvelle adresse dans la minute, et
  les autres postes l'apprennent par son annonce suivante (ou par l'adresse saisie à la main).
