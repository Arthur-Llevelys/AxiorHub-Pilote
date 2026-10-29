# Historique des versions

AxiorHub — créé par Timo RAINIO. Les notes détaillées des versions antérieures à la publication ouverte ne sont pas
reprises ici ; seules les grandes étapes le sont.

## 5.6.78 — Correctif : texte caché des PDF repéré aussi sur le serveur Linux

La recette GitHub de la 5.6.77 a échoué sur Linux : le contrôle du texte caché des PDF importait une bibliothèque pypdf installée à part,
présente sur le poste de développement mais absente du serveur. L’import échouait en silence et aucun texte caché n’était repéré.

- Le contrôle utilise désormais la copie de pypdf embarquée dans AxiorHub (comme l’analyse des pièces) : il fonctionne sur le serveur,
  le poste Linux et l’application Windows.
- Nouveaux tests dans les conditions du serveur (aucun pypdf installé à part, Poppler présent).
- Installer directement la 5.6.78 : la 5.6.77 n’apporte pas le contrôle des PDF sur un serveur Linux. La pseudonymisation renforcée de la
  5.6.77 n’est pas concernée.

## 5.6.77 — Pseudonymisation plus sûre avant l’IA externe et texte caché des PDF

Demande du cabinet du 28 octobre 2026, après lecture du retour d’expérience d’un confrère sur la conformité de son infrastructure d’IA :
renforcer ce qui part vers une IA externe (dont Claude par le connecteur) et ce qui entre par les pièces adverses.

**Pseudonymisation** (tout envoi à une IA non locale, connecteur Claude compris) :
- **Numéros validés par leur clé**, pour en reconnaître davantage sans masquer n’importe quel nombre :
  - IBAN par la clé modulo 97, y compris coupé sur deux lignes (la fin d’un IBAN partait auparavant en clair) ;
  - NIR avec espaces, points ou tirets, Corse 2A/2B, masqué même si la clé est erronée ; NIR coupé par un retour à la ligne reconnu
    par sa clé ;
  - SIREN reconnu par la formule de Luhn sans la mention « SIREN » ; carte bancaire écrite d’un bloc ;
  - nouveaux : numéro RG, numéro Portalis, RPPS, numéro de sinistre, immatriculation de véhicule.
- **Codes de raisonnement conservés** : IDCC, convention SYNTEC, CSG/CRDS, PMSS, réduction Fillon, AGIRC-ARRCO, DSN, ECLI, rôles (DRH…)
  ne sont plus pris pour des noms de personnes ; articles, dates et montants restent intacts.
- **Personnes et homonymes** :
  - « Paul DURAND » et « DURAND Paul » reçoivent le même marqueur ;
  - un « DURAND » isolé reçoit le marqueur de la seule personne connue de ce nom, même cité avant elle ;
  - s’il existe des homonymes (Sophie DURAND, Paul DURAND), le nom isolé reçoit son propre marqueur : pas d’erreur d’imputation ;
  - « Maître DURAND » n’est jamais rapproché d’un homonyme (avocat, notaire, commissaire de justice).
- **Sociétés** : « ZORVAL SAS » et « la société ZORVAL » sont reconnues comme sociétés.
- **Autotest** visible dans Paramètres › Modèles d’IA (« Aperçu de ce qui part ») : un corpus fictif vérifie ces règles à chaque
  affichage. Les tests de la version cassent volontairement le moteur de huit façons (tests de mutation) et vérifient que l’autotest
  le voit à chaque fois.
- La table conservée du connecteur Claude est reprise : les marqueurs déjà donnés à Claude restent les mêmes.

**Texte caché des PDF** (bouclier 5.6.59, qui couvrait déjà le HTML des courriels et Word) :
- repérés à la lecture : texte blanc sur fond blanc, police d’au plus 2 points, texte hors de la page, texte invisible ou transparent ;
- la couche de texte d’un PDF numérisé puis reconnu (OCR) n’est pas signalée, ni un titre blanc sur un bandeau de couleur ;
- le passage est retiré du texte lu et remplacé par « [AxiorHub : texte masqué retiré du PDF, page N : …] » ; l’alerte est élevée s’il
  s’adresse à une IA, et le travail passe en relecture ;
- contrôle local, sans réseau ni OCR ; une page illisible n’empêche jamais la lecture de la pièce.
- Aide « Sécurité de l’IA » complétée (fr, en, de, es).

## 5.6.76 — Opportunités et prospects : suivi commercial du cabinet (CRM), en complément des fonctions métier

Demande du cabinet du 28 octobre 2026 : une fonction CRM de suivi des opportunités et des prospects, avec détection proposée dans les
courriels et accès par le connecteur Claude.

- **Contacts & CRM › Opportunités** (`/contacts-crm?vue=opportunites`) : tableau par étape (Nouveau contact › Premier échange › Conflit
  vérifié › Proposition envoyée, puis Gagné / Perdu). Chaque opportunité : prospect (fiche contact « Prospect »), objet, matière,
  honoraires estimés, source (site, recommandation d’un client ou d’un confrère, client existant, réseau…) et toujours une prochaine action
  datée ; historique de chaque modification.
- **Conflit d’intérêts obligatoire** avant « Conflit vérifié » et « Proposition envoyée » : recherche lancée depuis l’opportunité (prospect
  et parties adverses connues) ; un rapprochement bloque jusqu’à l’examen du rapport (« Poursuivre »), « Décliner » bloque toujours.
- **Proposition** (devis, convention, lettre de mission) : date d’envoi et relance proposée à 7 jours.
- **Gagné** (avocat ou administrateur) : ouvre le dossier (répertoire Nextcloud sous une racine autorisée, correspondant), relie le contact
  comme client, et prépare la **convention d’honoraires en brouillon** au montant estimé (Facturation et honoraires ; rien n’est émis).
- **Perdu** : motif obligatoire (honoraires, conflit, hors domaine, sans suite, autre cabinet, décliné…).
- **« À décider »** : rubrique « Prospect à suivre » — actions arrivées à échéance (aujourd’hui ou en retard) et propositions à accepter
  ou écarter.
- **Détection proposée** (réglable) : toutes les 15 minutes, le modèle LOCAL lit au plus 4 courriels récents d’expéditeurs inconnus, sans
  dossier, et propose une opportunité quand il y reconnaît une demande de nouveau client. Rien n’est créé sans l’avocat ; aucun courriel
  n’est relu deux fois ; jamais de modèle externe ; le texte du courriel est traité comme une donnée, jamais comme une instruction.
- **Indicateurs** : opportunités et honoraires estimés en cours, taux de transformation et délai moyen sur 12 mois, résultats par source,
  motifs de perte.
- **Connecteur Claude** : outils `opportunites` (lecture, pseudonymisée), `proposer_opportunite` et `proposer_relance` (propositions à
  valider dans « À décider ») ; compétence Claude mise à jour.
- Cloisonnement : une opportunité dont le contact ou le dossier est réservé à d’autres membres n’est jamais montrée.
- Recherche de fonctions : « Opportunités », « Prospects », « Pipeline », « Suivi commercial », « Nouveaux clients ».

## 5.6.75 — Connecteur Claude : AxiorHub dans Claude (claude.ai, Claude Desktop, mobile), pseudonymisé, préparer sans jamais envoyer

Demande du cabinet du 27 octobre 2026 : utiliser AxiorHub depuis Claude avec l’abonnement de l’avocat, option de pseudonymisation,
brouillons et notes préparés par Claude, mémoire et plugins.

- **Connecteur personnalisé de Claude** (voie autorisée par Anthropic : aucun jeton d’abonnement Claude n’est lu par AxiorHub). Adresse
  `https://<serveur>/<préfixe>/claude/mcp` (MCP « Streamable HTTP », réponses JSON). Connexion par OAuth 2.1 avec PKCE (S256) : métadonnées
  RFC 9728 et RFC 8414 (avec et sans préfixe), inscription dynamique RFC 7591 limitée aux adresses de retour de Claude (claude.ai, claude.com)
  et de l’ordinateur (Claude Code, port libre), identité publiée de Claude (document de métadonnées CIMD, lu seulement sur les domaines
  d’Anthropic), jetons d’accès d’une heure et de renouvellement de 30 jours à usage unique, code rejoué = accès fermé, révocation RFC 7009.
  L’avocat se connecte avec SON compte AxiorHub (page de connexion 5.6.48) puis « Autoriser Claude » (compte, ce que Claude reçoit, adresse
  de retour, mise en garde contre un lien reçu d’un tiers). Changer de mot de passe ou désactiver le compte ferme l’accès.
- **Pseudonymisation** (5.4.0) de tout ce que Claude reçoit : activée par défaut, imposée à tous les comptes tant que l’administrateur ne la
  laisse pas au choix de chacun. Marqueurs STABLES pour le compte (table de correspondance conservée sur le serveur, jamais transmise) :
  cohérents d’une conversation à l’autre et dans la mémoire de Claude. Les marqueurs écrits par Claude sont rétablis au dépôt ; un
  marqueur inconnu est laissé tel quel et signalé (objet « À VÉRIFIER » d’un brouillon).
- **Lecture** : dossiers du compte (dossiers réservés à d’autres membres et dossiers exclus de l’IA externe jamais visibles), fiche,
  recherche, lecture d’un document ou courriel indexé, « À décider », échéances et agenda, mémoire du dossier, savoir-faire approuvé. Les
  correspondances confidentielles entre avocats (5.6.64) ne sont jamais transmises.
- **Préparer, jamais envoyer** : brouillon de courriel déposé dans « Brouillons » (relu, vérifié, citations contrôlées, réponse dans le fil
  d’un courriel reçu) ; note interne ou projet d’acte en document Word dans le dossier (sinon « A TRAITER ») ; fait proposé à la mémoire du
  dossier avec un extrait EXACT du document source ; savoir-faire générique proposé à la bibliothèque (marqueurs non rétablis). Tout reste à
  valider dans AxiorHub. Réglage du cabinet « lecture seule » ; droit « Déposer des brouillons » du compte respecté.
- **Mémoire et plugins de Claude** : consignes et savoir-faire en ressources MCP, mémoire et fiche d’un dossier en modèles de ressources,
  invites (« Faire le point du jour », « Préparer une réponse », « Note de synthèse », « Projet d’acte ») ; compétence (skill) Claude et
  plugin Claude (Claude Code, Cowork) téléchargeables, préremplis avec l’adresse du serveur.
- **Paramètres › Modèles d’IA › Claude (connecteur)** : réglages du cabinet (administrateur), adresse et pas à pas, connexions Claude du
  compte (retirer, pseudonymiser), derniers appels. Version serveur seulement, désactivé par défaut.
- **Toutes les fonctions** : « Navigateur supervisé » (5.6.73) y figure désormais ; il n’était trouvé que par la recherche. « À valider
  (téléphone) » est rattaché à « Aujourd’hui ». Un test vérifie qu’aucune fonction n’est plus trouvable seulement par la recherche.
- Modèle Apache du VPS : « Referrer-Policy: same-origin » (avec « no-referrer », les formulaires partaient avec « Origin: null » et étaient
  refusés).

## 5.6.74 — Associer un téléphone par QR code, valider depuis le téléphone (version serveur)

Demande du cabinet du 26 octobre 2026 (inspirée de l’association d’appareils de Row-Bot).

- **Associer un téléphone** (Paramètres › Poste et maintenance › Téléphones associés) : « Associer un téléphone » affiche un QR code ;
  l’appareil photo du téléphone ouvre « Associer ce téléphone » (compte affiché, nom de l’appareil), puis « Associer ». Invitation à usage
  unique, valable 10 minutes, liée au compte connecté ; seule l’empreinte du jeton est conservée ; un aperçu de lien ne la consomme
  jamais ; tentatives limitées par adresse. Session du téléphone : 90 jours, révocable (« Retirer »), fermée aussi par un changement de
  mot de passe ; dix appareils au plus par compte.
- **Valider depuis le téléphone** : page « À valider » (`/mobile`) — la file « À décider / À relire » du compte, en grand, avec les mêmes
  boutons qu’à l’ordinateur (relire, déposer une réponse « à compléter », écarter, autoriser une opération…). Les notifications (4.9.0)
  annoncent aussi, en nombres seulement, les points à décider.
- Version serveur seulement : un poste Windows ou Ubuntu n’est pas joignable depuis un téléphone.
- QR code produit sur le serveur par Segno 1.6.6 (BSD-3-Clause), copie locale `agent/_vendor/segno` ; image servie à la même origine, au
  seul compte qui l’a demandée, gardée en mémoire 10 minutes.
- Contraste : compteur de l’onglet actif de la file « À décider » lisible en thème clair et sombre.

## 5.6.73 — Navigateur supervisé (application Windows et Ubuntu)

Demande du cabinet du 26 octobre 2026 : reprendre de Row-Bot (Apache 2.0) le pilotage d’un navigateur, adapté à un cabinet d’avocats.

- **Navigateur supervisé** (Toutes les fonctions › Navigateur supervisé). L’avocat confie une tâche (« récupère l’extrait
  d’immatriculation », « consulte l’état de la procédure ») ; l’agent travaille pas à pas dans une fenêtre Edge / Chrome visible, avec un
  profil propre à AxiorHub (connexions conservées d’une séance à l’autre). Aucune dépendance ajoutée : le navigateur déjà installé est
  piloté par le protocole DevTools.
- **Ce qui attend l’avocat.** Jamais de saisie de mot de passe, de code à usage unique ni de numéro de carte : l’agent s’arrête et l’avocat
  le fait dans la fenêtre. Payer, réserver avec un montant, envoyer / déposer / signer / transmettre / notifier, supprimer, modifier un
  compte : « Autoriser » ou « Refuser », quoi que propose le modèle. Une vérification anti-robots (CAPTCHA) est toujours faite par
  l’avocat ; l’agent ne tente jamais de la contourner.
- **Sites.** Liste du cabinet préremplie (juridictions, Légifrance, registres des sociétés, professions, administrations), modifiable ; un
  autre site demande l’accord une fois (ou « toujours ») ; jamais ce poste ni le réseau local.
- **Résultats.** Fichiers téléchargés et pages enregistrées (texte et page complète) rangés dans « Pièces récupérées » du dossier choisi,
  sinon gardés sur le poste ; historique des séances sans paramètres d’adresse ni texte saisi.
- **Contrôle.** « Prendre la main » / « Rendre la main », « Arrêter », « Fermer le navigateur » ; une séance à la fois.
- Licences : extraits adaptés de Row-Bot (Copyright 2026 Row-Bot Contributors, Apache 2.0) — `LICENSES/Apache-2.0.txt`, `NOTICE-row-bot`,
  `THIRD_PARTY_NOTICES.md`, SBOM.

## 5.6.72 — Alerte « modèle local d’IA injoignable », nouvelles tentatives après une coupure

Incident du cabinet du 22 octobre 2026 : le moteur local (Ollama, sur un autre poste du réseau) s’est arrêté un soir ; pendant plus de
12 heures, aucun courriel n’a été trié ni rédigé, sans aucun signal sur « Aujourd’hui ».

- **Alerte sur « Aujourd’hui ».** Quand des travaux échouent depuis plus de 10 minutes parce que le modèle local ne répond pas, un
  bandeau le dit : heure de la première panne, dernier appel réussi, adresse du moteur, points à vérifier (machine allumée et sans
  veille, Ollama démarré et accessible depuis le réseau) et bouton « Tester maintenant ». Il disparaît dès qu’un appel ou le test réussit.
- **Nouvelles tentatives.** Une réponse « sans dossier » qui a échoué parce que l’IA était injoignable, en pause ou surchargée est
  retentée quand l’IA répond de nouveau (3 essais au plus) ; aucune tentative n’est lancée pendant la coupure.
- Recette : un test de planification du point du matin ne dépend plus de la date réelle.

## 5.6.71 — Surveillance des dossiers allégée, activité de l’agent lisible, réponses « sans dossier », réponses en attente visibles

Diagnostic du cabinet du 22 octobre 2026 (lecture seule) : environ 1 500 surveillances de dossier par jour, chacune relisant Nextcloud ;
« Ce que fait l’agent » incompréhensible ; très peu de brouillons, car les courriels sans dossier confirmé n’étaient jamais rédigés et
les réponses préparées en attente de décision n’avaient aucun bouton.

- **Surveillance allégée.** Un événement d’agenda nouveau ou modifié ne relance plus le tour complet de tous les dossiers : seule la
  surveillance du dossier qu’il désigne (au plus 5). Quand la veille des documents tourne, le tour périodique ne relit plus Nextcloud
  (elle le fait déjà et signale chaque fichier modifié) et passe à 6 heures au moins.
- **« Ce que fait l’agent » lisible.** Chaque travail dit à quoi il sert ; chaque passage terminé dit ce qu’il a produit
  (« 11 courriels analysés · 2 réponses mises en préparation · 6 en attente de votre décision ») ; les passages sans résultat et les
  travaux techniques (contrôles d’intégrité, index de recherche, nettoyage de la boîte, actualisation interne) ne sont plus listés.
  Les contrôles d’intégrité (sans IA) tournent une fois par nuit.
- **Réponses « sans dossier ».** Un courriel qui appelle une réponse mais dont le dossier n’est pas confirmé (expéditeur inconnu,
  prospect, correspondant à confirmer, rattachement incertain) reçoit une réponse préparée à partir du seul courriel et de ses pièces
  jointes, marquée « [À COMPLÉTER] » et déposée dans Brouillons si le contrôle de sûreté l’autorise. Jamais envoyée ; le dossier
  seulement supposé par l’agent n’est jamais utilisé ; une seule tentative automatique par courriel ; réglage dans Paramètres › Messagerie.
- **Réponses en attente de décision.** Une réponse préparée que le contrôle n’autorise pas à déposer devient une carte « Courriels à
  relire et envoyer » avec le motif ; la page du courriel permet de relire et modifier le texte puis de le déposer dans Brouillons, de
  le déposer tel quel « à compléter » (si le contrôle de sûreté l’autorise) ou de l’écarter. Avant, aucun bouton ne le permettait.
- Recette : le test du contrôle PHP ne dépend plus du PHP réellement installé sur la machine.

## 5.6.70 — Facturation électronique intégrée, messagerie et Dossier enrichis, visioconférence Jitsi, blocage automatique des accès suspects

Demande du cabinet du 22 octobre 2026.

- **Objet des projets « à compléter ».** Un brouillon qui contient encore « [À COMPLÉTER : …] » porte la mention dans son objet,
  visible dès la liste des brouillons.
- **Facturation électronique (Facturation › Facturation électronique).** Avec SUPERPDP ou B2Brouter configuré dans Connexions :
  envoi des factures Factur-X (B2Brouter accepte désormais le Factur-X), réception des factures fournisseurs (liste, téléchargement,
  enregistrement dans le dossier des factures fournisseurs de l’espace de fichiers), suivi du traitement des factures émises
  (statuts de la plateforme), comptes rendus de déclaration (e-reporting) et **export par période pour l’expert-comptable** :
  un ZIP avec les factures émises et reçues (CSV lisible par un tableur, PDF en option) et une notice.
- **Messagerie.** « 📁 Joindre depuis Nextcloud » dans la rédaction d’un courriel : parcourir les dossiers du cabinet ou chercher
  un fichier par son nom, 10 fichiers et 15 Mo au plus ; les dossiers réservés à d’autres membres restent invisibles.
- **Onglet « Courriels » du Dossier** : les courriels rattachés au dossier, avec sélection et traitement par l’IA.
- **Recherche dans les listes de dossiers.** Chaque liste déroulante de choix d’un dossier client reçoit une case de recherche
  (sans tenir compte des accents) et un bouton pour sélectionner le dossier trouvé.
- **« Traiter avec l’IA » plusieurs courriels** (Boîte de réception, page Courriels, onglet Courriels du Dossier) : cocher des
  courriels écartés par l’agent — par exemple la demande d’un prospect — puis demander une réponse pour chacun (25 au plus). Dossier
  établi par l’agent s’il existe, sinon réponse « sans dossier » fondée sur le courriel et ses pièces ; jamais un dossier deviné,
  jamais un dossier réservé. Les réponses arrivent dans « À relire » ; rien n’est envoyé.
- **Visioconférence Jitsi.** Réglage de l’adresse de votre serveur Jitsi (Paramètres › Messagerie), bouton « 🎥 Lien de
  visioconférence » dans la rédaction et case « Visioconférence » à la création d’un rendez-vous (lien unique ajouté au lieu et à la
  description). Script `deploy/jitsi/installer-jitsi.sh` : Jitsi sous Docker sur des ports distincts (10080/10443 en local, 10010/UDP
  pour les flux), accès authentifié pour créer une salle, invités admis ; exemple de mandataire Apache fourni.
- **Sécurité : blocage automatique des accès suspects.** Une adresse qui sonde 5 fois en 10 minutes des chemins d’attaque connus
  (fichiers PHP, .env, .git, wp-admin…) ou qui échoue 10 fois à se connecter en 10 minutes est bloquée 15 minutes, puis 1 heure,
  puis 24 heures en cas de récidive ; le poste de l’avocat (réseau local, adresse de confiance) n’est jamais bloqué. Paramètres › Cabinet › Sécurité des
  accès : blocages en cours, levée, blocage manuel, adresses de confiance. Journal `securite-acces.log` exploitable par fail2ban.
- **Audit de sécurité.** L’adresse du visiteur n’est plus lue dans la première entrée de X-Forwarded-For (falsifiable) mais
  uniquement derrière un mandataire de confiance (local ou AXIORHUB_TRUSTED_PROXIES), entrée la plus à droite non fiable : la limite
  des tentatives de connexion ne pouvait plus être contournée ni s’appliquer à tous les visiteurs à la fois.
- **Correctifs PHP du 21 octobre 2026 (CVE-2026-109618 et CVE-2026-109619).** AxiorHub n’utilise pas PHP, mais le serveur qui
  héberge Nextcloud ou WordPress, oui : Santé et Paramètres › Cabinet signalent une version vulnérable (corrigée à partir de 8.5.12,
  8.4.27, 8.3.36 et 8.2.35). Script `deploy/securite/durcir-serveur.sh` : rapport par défaut ; `--appliquer` met à jour PHP,
  redémarre PHP-FPM/Apache, installe fail2ban (SSH, AxiorHub, Nextcloud) et les mises à jour automatiques ; `--pare-feu` règle ufw.
- **Bandeau « Plafond mensuel de l’IA externe bientôt atteint »** : bouton « Masquer ». Il ne revient que si la situation change
  (nouveau mois, autre fournisseur concerné).
- Boîte de réception : espacement entre les boutons ; cases à cocher étroites devant les courriels. Thème sombre : onglet actif de la
  Facturation et état « Analyse partielle » lisibles.

## 5.6.69 — Analyses payantes répétées supprimées, projets « à compléter » réellement rédigés

Diagnostic du cabinet du 21 octobre 2026 (lecture seule du serveur) : pas de brouillon, analyses de documents en erreur,
préparations bloquées, et des appels payants répétés au fournisseur externe pour les mêmes courriels.

- **Orchestration des courriels.** Un courriel déjà analysé était réanalysé (classement et analyse différentielle) à chaque
  rafraîchissement de la mémoire du dossier, plusieurs fois par jour : c’est ce qui épuisait le plafond mensuel. L’analyse n’est
  refaite que si le courriel, son rattachement ou son tri changent.
- **Projets « à compléter » réellement rédigés.** En préparation automatique, le modèle rendait un texte vide dès qu’une décision de
  l’avocat manquait (« Aucun texte fiable ne peut être proposé »). Il rédige désormais le projet (faits sourcés,
  plan, réponse aux points posés) avec « [À COMPLÉTER : …] » là où votre position manque ; dépôt aux mêmes conditions de sûreté
  qu’en 5.6.67, jamais envoyé.
- **Analyses de documents.** Un passage sans réponse du modèle (page blanche, image) ne fait plus échouer toute l’analyse
  (« analyse de section incomplète ») : il est signalé « à vérifier sur l’original ». Une synthèse vide reprend les relevés du lot.
- **IA externe : la demande tient dans la taille autorisée.** Les rédactions confiées à un fournisseur externe sont dimensionnées
  15 000 caractères sous la taille maximale (place des consignes), au lieu d’être refusées (« contexte trop long »).
- **À décider.** Le regroupement des missions automatiques fonctionne même quand le titre de la carte est coupé ; les préparations
  automatiques arrêtées « Document nouveau ou modifié » d’un même dossier forment une seule carte. « Ne plus proposer pour ce dossier »
  met aussi en pause ces préparations.

## 5.6.68 — Plafond de l’IA compté juste, relais local, file « À décider » allégée

Retours du cabinet du 21 octobre 2026 (captures d’« Aujourd’hui », des erreurs et de « Sécurité de l’IA ») : routines « rédigées sans
IA », aucun brouillon de courriel depuis la veille, 33 décisions pour le dossier « 09 - Scanner », motifs d’erreur illisibles et deux
« instructions suspectes » sans courriel ni pièce.

- **Plafond atteint : le modèle local prend le relais.** Quand le plafond mensuel d’un fournisseur externe (ou son quota) est atteint,
  la demande passe au modèle local Ollama de la même fonction au lieu d’une production « sans IA » (routines, réponses de
  l’assistant, brouillons). Le refus du plafond a lieu avant tout envoi ; si le modèle local est absent, la cause réelle reste affichée.
- **Réservations des appels en échec.** Un appel en échec gardait sa réservation estimée jusqu’à la fin du mois, même sans facturation.
  Un refus (4xx, sauf délai 408), une panne du fournisseur (5xx) ou une connexion impossible libèrent désormais la réservation ; un
  délai dépassé ou une coupure restent « incertains ». La consommation affichée ne compte plus les appels en échec.
- **Paramètres › IA › Plafond du mois** : par fournisseur, plafond, dépensé, réservé incertain, reste et utilisation ; l’administrateur
  peut libérer les réservations incertaines après contrôle de la facture du fournisseur (journalisé).
- **Bandeau sur Aujourd’hui** quand un plafond est atteint ou utilisé à 90 %, avec le lien vers le réglage.
- **IA externe : taille mesurée sur ce qui part.** La politique comptait la donnée et les messages qui la contiennent (deux à trois fois
  la taille réelle) : « contexte trop long » pour des questions ordinaires à l’assistant.
- **Assistant** : une pièce sélectionnée illisible (scan de mauvaise qualité) est signalée et la réponse se fait avec les autres sources.
- **Indexation de tous les dossiers** : un dossier en pause est sauté (le lot s’arrêtait sur « suspension sans pause en vigueur »).
- **À décider allégé.** Un dossier de dépôt (« Scanner », « Numérisation », « A TRAITER », « A classer »…) ne déclenche plus de mission
  automatique par pièce. Les missions automatiques en attente d’une décision sont regroupées par dossier en une carte, avec le motif,
  les pièces concernées, « Écarter » et « Ne plus proposer pour ce dossier » (exclusion de la proactivité, réversible).
- **Motifs en clair** dans « Erreurs des 7 derniers jours », les routines et les travaux interrompus (plafond, scans, conclusions
  multiples, dossier regroupant plusieurs affaires, pause, relances suspendues).
- **Sécurité de l’IA.** Le banc d’essai des modèles, qui contient exprès des instructions piégées fictives, n’est plus ni nettoyé ni
  signalé ; ses anciennes alertes passent en « fausse alerte (banc d’essai) ». Le texte masqué retiré des lettres d’information est une
  « Information — rien à faire », repliée en bas de page et hors du compte « à examiner ».
- **Routines du cabinet** : cartes alignées en haut, de même hauteur, sur toute la largeur.
- Une ancre vers une section repliée (lien du bandeau vers le plafond) ouvre cette section.
- Jeux d’essai des versions 5.6.67 et 5.6.68 : noms de parties et numéros de procédure remplacés par des valeurs entièrement fictives.

## 5.6.67 — Réponses plus pertinentes et documents mieux préparés, même avec un modèle local

Demande du cabinet du 20 octobre 2026 : six améliorations pour que l’IA prépare mieux courriels, documents et notes, y compris avec un
modèle open source (632 courriels examinés en 7 jours, 1 brouillon préparé).

1. **Plus de courriels préparés.** Un courriel juridique ou stratégique d’un dossier connu reçoit désormais un projet de réponse
   « à compléter » dans les Brouillons : faits et pièces du dossier, plan, et « [À COMPLÉTER : …] » là où votre position est à arrêter.
   Objet et première ligne marqués « [À COMPLÉTER] » ; jamais envoyé, et l’envoi depuis AxiorHub le refuse tant qu’un marqueur reste.
   Déposé seulement si le contrôle indépendant confirme qu’il est fondé sur les sources, sans risque pour le destinataire et sans
   instruction cachée suivie ; jamais quand des sources ont dû être omises. Réglage : « Projets de réponse à compléter » (actif).
2. **Rattachement au bon dossier.** Les numéros RG connus d’un dossier (fiche, avis de procédure) et les noms de ses parties qui lui
   sont propres servent au rapprochement des courriels ; un RG qui ne désigne qu’un dossier y rattache le courriel (le rôle du
   correspondant n’est jamais deviné), un indice partagé par plusieurs dossiers n’est pas utilisé. Un expéditeur que vous avez
   confirmé pour un seul dossier compte comme un indice. Confirmer une association apprend le RG cité dans l’objet du courriel.
3. **Les bons passages.** Second tri des passages retrouvés (pertinence fine, part des mots de la demande, nom de la pièce, diversité :
   un passage presque identique à un autre est écarté), les plus pertinents d’abord ; les rédactions reçoivent moins de passages, mais
   meilleurs. Chaque passage est étiqueté (pièce, date, nature) et l’empreinte des nouveaux passages tient compte du nom de la pièce.
4. **Rédiger par étapes et vérifier.** Les documents longs (conclusions, assignation, note, contrat, compte rendu, mise en demeure)
   sont rédigés en deux temps : un plan court tiré du dossier, puis chaque section avec ses seules sources ; si le plan n’est pas
   exploitable, la rédaction d’un seul tenant reprend. Après toute rédaction (courriel, document, projet d’acte), chaque date, montant,
   numéro RG — et dans un courriel chaque nom en capitales — absent de ce que le modèle a reçu est marqué « [À VÉRIFIER : …] ».
5. **Apprendre de vos corrections, mesurablement.** Les phrases que vous ajoutez ou retirez régulièrement (au moins 3 envois au même
   profil de destinataire) deviennent des règles proposées dans Progrès de l’agent (« Adopter » / « Écarter ») ; seules des phrases de
   forme, jamais un fait ni un nom. Les documents produits sont relus dans Nextcloud après deux jours : part du texte gardée, par type.
6. **Le bon modèle pour chaque tâche.** Paramètres › IA propose une répartition (rapide, approfondi, contrôle) selon les modèles
   installés dans Ollama, appliquée en un clic ; un banc d’essai fait rédiger par un modèle local les réponses à vos cas réels
   (jusqu’à 20, anonymisés) et les compare aux vôtres. Les cas ne sortent jamais du cabinet.

## 5.6.66 — « À décider / À relire » par rubriques, notes internes rangées dans le dossier

Retour du cabinet du 20 octobre 2026 : les réponses préparées par l’IA étaient noyées, dans « À décider / À relire », parmi des dizaines
de notes internes (« [À traiter] Avis de renvoi… ») ; certaines n’y apparaissaient pas du tout.

- Aujourd’hui : « À décider / À relire » est séparé en rubriques — « Courriels à relire et envoyer » en premier, puis « À décider »,
  « Documents à examiner » et « Notes internes » ; analyses interrompues et tenue des dossiers restent repliées. Filtres par rubrique.
- Une réponse préparée dont le brouillon a perdu la marque de l’agent (réenregistré par la messagerie, qui retire l’en-tête
  X-AxiorHub) figure désormais dans « Courriels à relire et envoyer » ; auparavant elle n’apparaissait que dans la page Courriels.
- Notes internes déjà déposées en brouillon (adressées au cabinet, « [À traiter] », « [Projet] ») : bouton « Ranger dans le dossier »
  et « Tout ranger dans les dossiers » — chacune devient un document Word de son dossier (sinon « A TRAITER ») et le brouillon va dans
  la corbeille de la messagerie (restaurable ; sans corbeille, il reste en place). Une réponse à un tiers n’est jamais rangée.
- Correction d’un risque introduit par la 5.6.65 : une demande identique à un travail « suspendu » le retrouvait (audit 5.6.64, R13),
  même quand la pause qui l’avait suspendu était levée depuis ; un ancien examen des courriels resté « suspendu » aurait pu absorber
  chaque nouvel examen. Ce n’était pas le cas sur le serveur du cabinet (vérifié en lecture seule), mais seul un travail suspendu par une
  pause encore en vigueur est désormais retrouvé.
- Au démarrage des workers et à chaque reprise, les travaux restés suspendus après la fin de leur pause sont repris (demande de
  l’avocat de moins de 6 heures) ou clos avec un motif (contrôles automatiques, reprogrammés par leur planificateur).
- Notes internes : un avis de procédure (avis de renvoi, de procédure, calendrier) sans destinataire paramétré donne maintenant un
  document Word « AAAA-MM-JJ - Note interne - … » rangé directement dans le dossier concerné ; à défaut (dossier inaccessible ou non
  identifié), dans un dossier neutre « A TRAITER » à la racine des dossiers. Plus de brouillon « [À traiter] » adressé au cabinet.
  Un fichier existant n’est jamais remplacé.
- Les brouillons d’information des avis de procédure (au confrère, au client) indiquent leur dossier sur Aujourd’hui (« Dossier non
  identifié » auparavant).

## 5.6.65 — Corrections de l’audit de la 5.6.64 (confidentialité, savoir-faire, contacts, file de travail)

Audit développeur du 19 octobre 2026 sur la 5.6.64 : 17 anomalies reproduites (R01 à R17), aucune bloquante (P0). Les 26 contre-tests
du dossier technique de l’audit sont intégrés à la suite (`tests/test_audit5664.py`, `tests/test_files5664.py`) et réussissent tous.

**Correspondances confidentielles entre avocats**
- R01 : le statut « confidentiel » est celui de la pièce entière (nom, objet, début du texte), calculé avant le découpage et hérité par
  chaque passage. Un passage éloigné de la mention n’est plus transmis. Les pièces déjà indexées sont requalifiées à la lecture, sans
  réindexation.
- R02 : une base de travail choisie automatiquement (fichier le plus récent) n’est jamais une correspondance confidentielle ; la
  suivante, ou le modèle interne, est retenue. Une base désignée par l’avocat reste possible, avec la règle de non-citation.
- R03 : une information appuyée en partie sur une correspondance confidentielle n’est plus transmise pour un document destiné à un
  tiers ; un message invite à la reprendre à partir des seules pièces communicables. Elle reste, marquée, pour l’usage interne.
- R04 : un texte officiel vérifié par le serveur qui cite l’article 66-5 n’est plus écarté ; « confidentialité » (accord, clause) ne
  désigne plus une correspondance.
- R11 : les faits validés de la mémoire du dossier gardent leur provenance ; ceux tirés d’une correspondance confidentielle ne
  reviennent plus par les rédactions libres. Un fait sans pièce rattachée est signalé « à vérifier avant communication ».

**Savoir-faire du cabinet**
- R07 : tous les champs réutilisés (titre, texte, objectif, limites, domaine, juridiction, type d’acte, mots-clés) sont anonymisés,
  y compris pour une nouvelle version envoyée sans dossier. Les versions existantes sont recontrôlées une fois ; une version approuvée
  modifiée par ce contrôle repasse « à approuver ».
- R12 : un savoir-faire tiré d’un dossier réservé reste dans le périmètre de ce dossier tant qu’il n’est pas approuvé : un compte
  exclu ne le voit ni dans la page, ni dans la file, et ne peut pas le modifier par son identifiant.

**Contacts**
- R05 : un courriel momentanément illisible n’est plus sauté : il est relu au passage suivant ; après trois échecs, il est abandonné
  et signalé.
- R06 : une adresse écartée pour un message de liste de diffusion est réexaminée quand elle écrit un message personnel.

**File de travail et arrêt**
- R08 : au plus 6 analyses de dossier en attente, place du lot entier réservée en une fois, pour tous les producteurs.
- R09 : un travail qui attend le verrou d’un dossier ne bloque plus les autres dossiers.
- R10 : « Arrêter » et la pause ferment réellement une réponse ChatGPT en cours.
- R13 : une demande identique à un travail suspendu par la pause ne crée plus de doublon.
- R14 : un travail reporté à plus tard ne compte plus comme une charge actuelle.
- R15 : les durées maximales sont surveillées toutes les 30 secondes par un superviseur indépendant, même si tous les workers sont pris.
- R16 : le contrôle de santé ne prend plus le verrou de production : une sonde lente ne retarde plus les rédactions.
- R17 : « Annuler les travaux automatiques » respecte l’origine enregistrée : une indexation demandée par l’avocat est conservée.

**Mode sombre** : les étiquettes d’état de la page « Savoir-faire du cabinet » (« En usage », « à approuver ») redeviennent
lisibles (contrôle des contrastes repassé en clair et en sombre sur Aujourd’hui, Paramètres et Savoir-faire).

**Mise à jour depuis Paramètres** : bouton vérifié sur la version 5.6.64 publiée (flux, téléchargement, empreinte, signature, pour
Windows, Ubuntu poste et serveur) et installation silencieuse Windows 5.6.63 → 5.6.64 essayée dans un dossier isolé.

## 5.6.64 — Aujourd’hui centré sur les décisions du jour, plus de salve d’analyses, fichiers parasites ignorés, correspondances confidentielles

Retour du cabinet du 19 octobre 2026 : 133 éléments dans « À décider / À relire » (76 « À décider »), dont beaucoup sans intérêt ;
32 travaux en file quelques minutes après la reprise de l’IA ; 100 fichiers « en erreur » sur 203 dans un dossier (fichiers
temporaires, archives), chacun avec sa lacune « Lecture de … ».

- Aujourd’hui : la file ne montre que les décisions et relectures du jour (décisions attendues par l’agent, projets et brouillons de
  moins de 14 jours, engagements, contradictions, sécurité, facturation). La tenue des fiches de dossiers (identités, classements,
  informations proposées, lacunes, relectures de modifications), les points d’avancement client proposés et les projets anciens sont
  résumés en bas, par dossier, avec un lien : ils se traitent depuis chaque dossier. La page du dossier garde tout.
- Reprise de l’IA : au plus 6 analyses de dossier en attente (indexation, surveillance immédiate, recherche de faits) ; au-delà, ou
  si une de vos demandes attend, un dossier modifié reste « à traiter » et est repris à un tour suivant. Plus de salve.
- Fichiers qui ne sont pas des pièces (temporaires, verrous d’Office ou de LibreOffice, raccourcis, vignettes) : retirés de
  l’inventaire du dossier, sans lacune. Archives et formats non pris en charge : plus de lacune « Lecture de … ». Les lacunes de ce
  type déjà ouvertes sont fermées sans bruit ; les abandons passés sortent des cartes « Changements récents » et « Depuis votre
  dernière lecture ».
- Correspondances confidentielles entre avocats (article 66-5 de la loi du 31 décembre 1971) : reconnues par leur nom ou leur objet
  (« confidentiel », sauf « non confidentiel ») ou par une formule propre à la correspondance entre confrères dans le texte ; une
  clause de confidentialité dans un contrat ne suffit pas. Elles restent lisibles par l’avocat (fiche du dossier, assistant,
  préparation de rendez-vous, routines), marquées « confidentiel — usage interne » à côté de chaque source, mais ne sont jamais
  transmises comme source d’un document rédigé (projets documentaires, rédactions libres, y compris notes et consultations qui
  peuvent être remises au client). Une pièce choisie par l’avocat est gardée, avec un avertissement au modèle.
- Mode sombre : le bouton « Prendre en charge une mission » de la page du dossier redevient lisible (contrôle des contrastes repassé
  en clair et en sombre sur Aujourd’hui et la page du dossier).

## 5.6.63 — Surveillance des documents espacée, analyses d’échéances sans échec inutile

Charge mesurée chez le cabinet le 18 octobre 2026 avec le nouveau diagnostic de la 5.6.62 : workers occupés 22 % du temps, dont
3 h 30 pour la seule surveillance des documents des dossiers (1 693 passages en 24 h).

- Surveillance des documents : les lots d’un tour s’enchaînent toujours, mais un nouveau tour complet de tous les dossiers ne commence
  que 30 min après la fin du précédent (60 min en régime économe ; réglage `live430:documents_sweep_minutes`). Avant : nouveau tour
  5 min après, la surveillance ne s’arrêtait jamais.
- Échéances (recherche par règles, sans modèle d’IA) : un long document est lu en entier (la limite de 100 000 caractères, faite pour
  le contexte d’un modèle, ne s’applique plus) ; une pièce illisible (scan de mauvaise qualité, sans texte) est signalée une fois
  — « vérifiez vous-même si elle fait courir un délai » — au lieu d’échouer à chaque passage.

## 5.6.62 — ChatGPT avec votre abonnement, Aujourd’hui réorganisé, textes lisibles partout

- ChatGPT avec l’abonnement de l’utilisateur (Plus ou Pro), sans clé API, par le programme officiel d’OpenAI « Sign in with
  ChatGPT » (logiciels libres hébergés par l’utilisateur) : Paramètres › Modèles d’IA › ChatGPT, « Continuer avec ChatGPT ».
  Inscription dynamique, PKCE, jeton d’identité vérifié, jetons chiffrés et renouvelés seuls, déconnexion avec révocation ; API
  Responses avec `store: false`. Même pseudonymisation, même bouclier et même autorisation « données externes » que les autres
  fournisseurs. Poste : retour d’OpenAI reçu par AxiorHub ; serveur : adresse de retour recopiée. Mode d’emploi :
  `docs/CHATGPT-ABONNEMENT.md`.
- Aujourd’hui : « À décider / À relire » prend la première colonne ; « Reprendre là où j’en étais » passe en bas de page.
- Point d’avancement client : le client lit la nature du travail (« un projet de courrier »), jamais un nom de fichier interne ;
  l’avocat voit les documents concernés, leur emplacement et un bouton « Ouvrir dans Nextcloud ».
- Tâches « Relire : … » en double dans Ma journée (copie locale et tâche relue depuis Nextcloud) : fusionnées, plus jamais créées en
  double ; un projet existant en .docx, .pdf et .md n’apparaît qu’une fois (Word en priorité).
- Textes illisibles corrigés : sous-menu « Autres pages de réglage et d’outillage » de Paramètres, « Contexte détecté » du Pilote,
  lien « Guide d’utilisation » du panneau « Toutes les fonctions », largeur de la colonne de Paramètres ; mode sombre (menus,
  cartes, états). Contrôle automatique des contrastes passé sur toutes les pages principales (`scripts/audit-contraste.js`).
- « Ce que fait l’agent » : chaque type de travail a un libellé en clair (plus de « search_index490 »).
- `manage.py diagnostic-file` : motifs des échecs répétés, en clair, avec leur nombre.
- Mises à jour : bouton « Vérifier maintenant » (sans attendre la vérification automatique, toutes les 6 heures).
- L’IA ne tourne plus en continu : un travail périodique (surveillance, orchestration, pilotage, production, Deck, suivi des envois…)
  ne revient qu’un intervalle complet APRÈS la fin du précédent (et non depuis sa mise en file) ; quand une demande de l’avocat attend
  ou que la file des travaux automatiques est chargée, les travaux périodiques non essentiels attendent leur tour (les nouveaux
  courriels ne sont jamais différés). Diagnostic : « Charge des workers sur 24 h » avec les travaux qui occupent le plus l’IA.
- Aucun blocage du traitement par l’IA : une réponse de ChatGPT silencieuse ou sans fin échoue avec un message clair (délai de
  lecture, plafond de 30 min) ; messagerie injoignable : la liste des brouillons n’est relue que 10 min plus tard (le worker n’attend
  plus jusqu’à 45 s toutes les 2 min).

## 5.6.61 — Veille qui passe et qui est pertinente, pièce jointe illisible non bloquante, mise à jour signée depuis GitHub

- Un travail qui a besoin du verrou exclusif (veille, indexation…) et l’a attendu trois fois le réserve : aucun nouveau travail à verrou
  partagé (réponses de l’assistant, brouillons) ne démarre avant lui, seuls les travaux sans verrou continuent. Constaté après la
  5.6.60 : une suite continue de réponses de l’assistant repoussait la veille indéfiniment.
- « Actualiser » dans la veille : la collecte déjà en file prend la priorité d’une demande de l’avocat ; les collectes mises en file
  avant la 5.6.60 avec l’ancienne priorité sont relevées.
- Aujourd’hui : les brouillons préparés par l’agent dans la messagerie s’affichent de nouveau dans « À décider / À relire ». Depuis
  la 5.6.57, leur liste n’était relue que par l’ancien cadre « À relire », qui n’est plus affiché ; le worker la relit désormais
  toutes les 2 minutes. Si la file ne peut pas être affichée, un message le dit au lieu de la faire disparaître.
- File « À décider / À relire » claire et exploitable : titres lisibles (l’objet, jamais la consigne interne donnée au modèle), motif
  réel d’un échec en clair, boutons « Relancer » et « Écarter » dans la carte, tentatives d’un même objet regroupées, projets à
  relire avant les décisions, analyses automatiques interrompues jamais « prioritaires » et repliées en fin de file, onglets avec
  compteurs (Tout, À relire, À décider, Interrompus), dossier cliquable.
- « Vos missions » : même présentation (objet en titre, action de l’agent en sous-titre, motif en clair, plus de « Traitement
  démarré » sur une mission interrompue, bouton « Relancer »).
- Lecture des longs documents : un résumé de passage trop long est coupé à la fin d’une phrase et signalé « […] » au lieu de faire
  échouer toute l’analyse (« analyse_section_incomplete ») ; une réponse sans identifiant de source vaut pour le passage analysé.
- Projets documentaires : les pièces transmises au modèle sont limitées à sa capacité réelle (auparavant 84 000 caractères pour une
  limite de 65 000 : « Le contexte dépasse la capacité du modèle » sur tout dossier fourni) ; une référence de pièce inconnue
  recopiée par le modèle est retirée et signalée à l’avocat au lieu de faire échouer le projet (« source document invalide ») ;
  une source juridique inconnue reste bloquante.
- Assistant : une pièce jointe illisible ou introuvable (scan sans texte reconnu, format à convertir, document déplacé…) n’empêche plus
  la réponse ; elle est signalée dans la réponse, et le modèle est prévenu de ne pas en déduire le contenu.
- Veille juridique pertinente : la recherche Internet (Tavily) est faite en mode « actualités » ; un résultat sans date, trop ancien ou
  ayant l’allure d’une page permanente (formation, diplôme, librairie, « guide complet ») n’est plus publié ; un connecteur juridique
  non configuré n’est plus présenté comme une panne (« collecte partielle ») ; les résultats sans date déjà collectés sont masqués une
  fois (toujours consultables).
- Mise à jour depuis la page des versions GitHub, sans ligne de commande, sur les trois plateformes : AxiorHub annonce la nouvelle
  version (Paramètres › Confort et maintenance › Mises à jour, vue d’ensemble) et l’administrateur clique « Mettre à jour ».
  Serveur Linux : un service système, installé et activé automatiquement par `install.sh` et le paquet Debian, télécharge l’archive,
  vérifie son empreinte et sa SIGNATURE, installe par la mise à jour habituelle, contrôle les services et revient à la version
  précédente sinon. Windows 11 : l’installateur signé est vérifié puis lancé en mode silencieux (même dossier, mêmes raccourcis) et
  l’application se rouvre. Ubuntu poste : le paquet signé est vérifié puis installé après le mot de passe demandé par Ubuntu.
  Une version non signée par la clé livrée avec AxiorHub n’est jamais installée. Option « Installer automatiquement les nouvelles
  versions la nuit » (2 h – 5 h, une tentative par version). Mode d’emploi : `docs/MISE-A-JOUR-SIGNEE.md` ; signature des versions :
  `scripts/signer-version.ps1`.

## 5.6.60 — File de travail débloquée, diagnostic des blocages, page Paramètres réorganisée

- Correctif : un seul dossier dont l’IA était mise en pause figeait toute la file de travail. Le balayage de surveillance des
  dossiers échouait sur ce dossier, se mettait en « suspendu pour pause », puis était aussitôt remis en file faute de pause qui le
  concerne, et repris toutes les 3 secondes en tête de file (118 739 essais constatés sur un serveur) : veille, indexation et
  surveillance restaient en attente. Le balayage passe désormais les dossiers en pause, et un travail suspendu sans pause en vigueur
  n’est plus relancé qu’avec un délai, puis arrêté avec un message clair au-delà de trois essais.
- Tête de file : un travail qui attend le verrou général attend un délai croissant (10 s à 5 min) sans compter de tentative ; le
  worker passe aux travaux suivants ; le détenteur du verrou est noté.
- Durée maximale par type de travail : au-delà, l’arrêt est demandé automatiquement (réponse de l’assistant : 20 min, brouillon :
  30 min, veille : 1 h, rédaction longue : 4 h, indexation : 6 h, autres : 3 h). Une question posée il y a plus de 6 h n’est plus
  relancée au redémarrage du service.
- Veille programmée : passe devant les travaux de maintenance ; un passage orphelin d’un créneau dépassé est clos.
- Diagnostic des blocages dans « L’agent en un coup d’œil » et dans Paramètres › Confort et maintenance (pause de l’IA, verrou tenu
  trop longtemps, travaux trop longs, arrêts non confirmés, attente du verrou, workers silencieux, file pleine ou figée, échecs
  répétés, veille orpheline), avec « Arrêter ce travail » et « Réessayer maintenant » ; commande serveur en lecture seule
  `manage.py diagnostic-file`.
- Page Paramètres réorganisée sans retirer de réglage : menu latéral en cinq familles, vue d’ensemble (état des réglages), recherche
  d’une rubrique, sommaire et sections repliables ; « Cabinet et accueil » réparti (accueil téléphonique et envoi avec les
  courriels ; confort et maintenance à part ; comptes avec le cabinet) ; sources juridiques et veille réunies. Les anciennes
  adresses et rubriques restent servies.

## 5.6.59 — Le dossier en bref, savoir-faire du cabinet, protection contre les instructions cachées

- Protection de l’IA contre les instructions cachées dans les courriels, documents et images (injection d’instructions) :
  - avant chaque appel du modèle, local ou externe : caractères invisibles retirés (dont les caractères « tag » Unicode qui cachent
    du texte), faux jetons de conversation neutralisés ;
  - détection en français, anglais, allemand et espagnol (texte adressé à une IA, demande d’ignorer ses consignes, de cacher quelque
    chose à l’avocat, de révéler ses instructions, d’approuver sans contrôle, de transmettre des données, image externe porteuse de
    paramètres) ; le modèle est prévenu par un message du système ;
  - sur une alerte élevée, un contrôle ne peut plus conclure à l’absence de problème : aucun brouillon n’est déposé
    automatiquement, le travail passe en relecture ; les images externes sont retirées des textes produits ;
  - texte masqué d’un courriel HTML (display:none, taille ou opacité nulle, attribut hidden) et d’un document Word (texte masqué,
    taille minuscule) retiré à la lecture et signalé ; le texte lu par OCR dans une image suit le même contrôle ;
  - journal des alertes, page « Sécurité de l’IA », alertes élevées dans la file « À décider / À relire », ligne dans
    « L’agent en un coup d’œil ».
- Le dossier en bref (A02), en tête de la page Dossier : qui représente qui, objectif du client, situation, prochaines dates
  confirmées (les dates proposées sont comptées à part), décisions du cabinet, changements récents, pièces et informations à
  rechercher ; chaque élément renvoie à sa source ; résumé calculé sans IA et versionné par l’empreinte de ses entrées.
- Bilan de pièce (A02) : une pièce nouvellement lue produit un bilan de ses apports (faits, demandes, événements, parties, échéances,
  décisions de justice) dans « Depuis votre dernière lecture » ; une simple mise en forme ou un renommage n’en produit pas.
- Savoir-faire du cabinet (A07) : structures de courrier, listes de vérification, clauses approuvées, méthodes de relecture et
  trames, versionnées, anonymisées si elles viennent d’un dossier, approuvées avant réutilisation (droit « valider »), avec date de
  revue ; l’agent en reçoit au plus deux par travail, rapprochées de la vraie consigne et de la phase ; usage journalisé. Une
  rédaction corrigée se propose depuis l’onglet Travaux du dossier.

## 5.6.58 — Correctif : file unique robuste, aperçu des factures, échéances

- Aujourd’hui : la file « À décider / À relire » disparaissait entièrement dès qu’une proposition de facturation (temps validés non
  facturés, brouillon à reprendre) était en attente. Ces propositions s’affichent désormais comme « Proposition à décider », et un
  élément de type inconnu est présenté « À examiner » au lieu d’effacer la file.
- Présentations de facture sobre, moderne et compacte : la note interne du modèle n’apparaît plus dans l’aperçu textuel ; le titre
  affiché est « Facture d’honoraires ».
- Préparation depuis l’agenda : une échéance (« Échéance : … », date limite, délai) n’est plus présentée comme un rendez-vous ;
  bouton « Préparer cette échéance » et note de préparation.
- Captures d’écran à jour (docs/captures/fr, 10 à 18), réalisées avec des données fictives.

## 5.6.57 — Parcours de bout en bout : file unique, missions jusqu’au Word, préfacture ; préparation, pièces, suivi client, contacts, reprise

- Parcours 1 (courriel reçu → brouillon prêt à relire) :
  - une seule file « À décider / À relire » sur Aujourd’hui, Dossier et Produire, qui reprend aussi les décisions attendues par
    l’agent et les projets à relire ; « Ouvrir » mène à la carte d’action de chaque décision ;
  - états réels des travaux : en file, en cours, projet prêt, une question bloque ce passage, échec avec nouvel essai prévu ;
  - « L’agent en un coup d’œil » en tête de « Pourquoi rien n’est produit ? » : signe de vie du worker, travail en cours, plus
    ancien travail en attente, collectes de veille orphelines, erreurs du modèle.
- Parcours 2 (mission → Word révisable) :
  - autorisation donnée une fois (Paramètres › Documents) de créer les nouvelles versions de brouillon sans code de confirmation,
    dans le sous-dossier de brouillons, sans jamais écraser un fichier ;
  - relecture Word avec le passage d’origine et la proposition côte à côte, le motif et les sources ; « Reformuler » ; acceptation
    groupée des seules modifications de formulation.
- Parcours 3 (diligences → préfacture → facture) :
  - temps proposés chaque jour à partir des événements du dossier, avec leur source et leur traitement au regard de la
    convention, confirmés dans la page du dossier et rappelés dans Facturation › À faire ;
  - aperçu PDF réel produit depuis le même Word que le document ;
  - trois présentations de facture (sobre par défaut, moderne, compacte) aux mêmes variables et contrôles.
- Préparation d’audience ou de rendez-vous depuis l’agenda : fiche immédiate et versionnée (synthèse, chronologie, derniers écrits,
  pièces clés, points ouverts), puis note de préparation en mission.
- Demande de pièces guidée : une demande regroupée au client, déposée en brouillon sur action ; rapprochement des documents reçus
  confirmé par l’avocat.
- Point d’avancement client proposé après une étape vérifiable : jamais une action seulement préparée annoncée comme accomplie ;
  refus définitif ; fréquence réglable par dossier.
- Contacts alimentés par la boîte mail : lecture des seuls en-têtes, progressive et sans relecture ; adresses du cabinet, de service
  et listes exclues ; nouveaux contacts « À classer ».
- « Reprendre là où j’en étais » sur Aujourd’hui (pages de travail revérifiées) et recherche étendue aux documents de facturation et
  aux décisions du cabinet.

## 5.6.56 — Audit 5.6.55 : 27 corrections (facturation, cloisonnement, identités, rédaction, veille)

- Facturation :
  - émission sous verrou sur la ligne relue : un abandon ou une modification concurrente l’emporte ;
  - numérotation annulée avec l’émission en cas de panne ;
  - document émis rendu depuis son paquet d’émission figé, octets originaux conservés ;
  - cumul des avoirs plafonné ;
  - provisions émises imputées au solde même partiellement réglées ;
  - totaux sur tous les documents ;
  - règlements : conflit si une référence est réutilisée avec un autre contenu, dates du calendrier réelles ;
  - IBAN contrôlé (MOD 97) ;
  - brouillons recalculés après modification du client, blocage chiffré après changement du régime du cabinet ;
  - fichiers publiés relus et comparés à leur empreinte (« conflit » sans écrasement, copie de l’original) ;
  - tableau de bord limité aux dossiers visibles ;
  - le formulaire « Nouveau document » et les autres créations ouvrent la fiche créée ;
  - choix d’une entreprise dans l’annuaire ;
  - aperçu nommé « textuel », états calculs, données, format, fichiers et transmission séparés.
- Cloisonnement : un contact relié à un dossier réservé n’est plus lisible, modifiable ni archivable par un compte exclu ; le
  cloisonnement d’une requête est effacé à la fin de la réponse.
- Identités du cabinet : un seul registre (adresses, adresse d’expédition, identifiant, alias) pour la messagerie, la proactivité, les
  propositions et les associations. Les propositions qui visaient le cabinet sont écartées. Le panneau « Vos adresses reconnues par
  l’agent » propose un test de classification.
- Rédaction :
  - l’intention réelle (réponse au fond, demande de précision, relance…) est transmise à la rédaction et au contrôle, qui ne
    requalifie plus toute réponse en demande de précision ;
  - la rédaction de documents reçoit les décisions et consignes du dossier.
- Veille : les anciens profils sont réellement planifiés ; un passage resté en attente sans travail est repris ; nouveaux états
  « en file », « en cours » et « mise en file refusée ».
- Contacts : rôles des correspondants conservés à l’import ; les homonymes aux courriels différents ne sont plus fusionnés ; la
  création et le lien sont atomiques ; un filtre sans résultat l’indique et propose de réinitialiser.
- Aujourd’hui : la veille occupe toute la largeur ; la file « À décider / À relire » est en bas, à la largeur des routines.
- Connexion depuis la racine : retour à Aujourd’hui.
- Les 33 contre-tests de l’audit et son test JavaScript font partie de la suite. Détail : CONFORMITE-5.6.56.md.

## 5.6.55 — Brouillons automatiques rétablis ; page « Facturation et honoraires » refondue

- Brouillons automatiques : depuis la 5.6.52, chaque lot d’indexation et chaque synthèse reconstruisait aussitôt toutes les données du
  dossier concerné (pièces, faits, chronologie). Sur un grand dossier ou un dossier interne volumineux (« 09 - Scanner »), ce travail
  occupait le travailleur unique et la base pendant de longues minutes : les autres tâches, dont la rédaction des brouillons de réponse,
  restaient « en file ». L’actualisation est désormais une tâche distincte de faible priorité (« Actualisation des données du dossier »),
  une seule par dossier, réservée aux dossiers clients d’au plus 1 500 pièces et enregistrée par lots ; une rédaction n’attend jamais
  la reconstruction d’un grand dossier (elle utilise les dernières données lues et le signale). Sur un dossier de plus de 300 pièces,
  le bouton « Actualiser les données » de la fiche lance une tâche prioritaire au lieu de faire attendre la page. Les tâches en file
  reprennent après la mise à jour.
- Facturation : la page ne propose plus de convention d’honoraires pour tous les dossiers. Seuls les dossiers clients sont concernés ;
  les dossiers internes du cabinet reconnus par leur nom (« 01 - Modèles », « 09 - Scanner », bibliothèque, comptabilité…) et les
  dossiers archivés sont écartés, avec un bouton « Pas un dossier client » et une liste « Dossiers écartés » pour réintégrer un dossier.
  La proposition « sans convention » ne vise plus que les dossiers apparus après la mise à jour ; les propositions existantes de ce type
  sont retirées.
- Nouvelle page en cinq onglets : « Tableau de bord » (à facturer, impayés et échus, brouillons, encaissé du mois ; liste « À faire »
  classée par urgence : facture échue, document approuvé à émettre, à envoyer, brouillon, temps à facturer, provision, budget),
  « Documents » (filtres brouillons, à émettre, impayées, émises, devis et conventions ; recherche), « Dossiers » (dossiers clients avec
  facturé, encaissé, solde et bouton « Facturer »), « Honoraires » et « Réglages ». « Nouveau document » choisit le type puis le dossier
  client ; la fiche du cabinet incomplète (SIREN, adresse, régime de TVA, IBAN) est signalée avant toute émission.

## 5.6.54 — Correctif : identifiants des documents de facturation

- Deux brouillons de facturation, avoirs ou estimations créés dans la même seconde (machine rapide, double clic sans clé de demande)
  recevaient le même identifiant technique : la seconde création échouait. Les identifiants sont désormais aléatoires ; aucune donnée
  existante n’est modifiée. Constaté par la recette GitHub de la 5.6.53 (2 erreurs sous Linux) ; test de non-régression ajouté.
- Deux règlements identiques sans référence enregistrés le même jour (par exemple deux chèques du même montant) étaient fusionnés en un
  seul : ils restent désormais distincts ; un règlement avec référence n’est toujours enregistré qu’une fois.

## 5.6.53 — Audit 5.6.52 : opérations externes fiabilisées, facturation et honoraires, dossier FACTURATION

Corrections et livraisons de l’audit « AxiorHub Pilote 5.6.52 » (détail : `CONFORMITE-5.6.53.md`).

- **Opérations externes (F01 à F05, U01)** : un compte de service modifié impose une nouvelle revue ; une panne avant envoi se
  réessaie sans doublon ; une signature n’est « terminée » qu’une fois le signé et ses preuves archivés ; un brouillon distant n’est
  jamais présenté comme envoyé ; une connexion personnelle ne sert qu’à son titulaire ou à un délégué ; fiche d’opération complète
  avant « Valider et transmettre ».
- **Routines et droits (F06 à F08)** : création idempotente, pause effective sur les passages en file, droit « Réglages » exigé pour
  configurer les connexions du cabinet.
- **Facturation et honoraires** : nouvelle page (À préparer, Documents, Règlements, chiffrage Honorium, réglages et modèles) ;
  devis, lettres de mission, conventions, factures et avoirs depuis huit modèles Word ; qualification de TVA explicite ;
  numérotation unique à l’émission ; anti-double facturation ; règlements ; Factur-X (PDF/A-3 + CII), UBL ; profil secret
  professionnel ; transmission par un seul trajet validé.
- **Dossier FACTURATION** dans chaque dossier client : factures, avoirs, devis, conventions et preuves y sont rangés, jamais parmi
  les pièces.

## 5.6.52 — Audit 5.6.51 : corrections F01 à F10, routines personnalisées, connexions API utilisables par l’IA

Corrections et livraisons de l’audit « AxiorHub Pilote 5.6.51 » (détail : `CONFORMITE-5.6.52.md`).

- **Accès (F01)** : une règle, une information, une relecture, une routine ou une opération désignée par son identifiant est contrôlée
  sur son dossier réel ; un dossier réservé est refusé sans lecture ni écriture.
- **Données du dossier (F02 à F04)** : contexte de rédaction actualisé après chaque indexation ; informations d’une version remplacée
  classées « historique » ; rappels des faits signalés périmés dès que leurs informations changent.
- **Relecture Word (F05 à F07, F09)** : source modifiée = acceptation à recontrôler ; ancre par position et voisins ; applications
  cumulatives dans des fichiers numérotés ; règles « ce document » appliquées.
- **Préparations et routines (F08, F10)** : reprise après une pause ; une routine n’est jamais perdue quand la file est pleine.
- **Routines personnalisées** sur Aujourd’hui : créer, modifier, dupliquer, mettre en pause, supprimer, lancer ; jours, heure, mois ou
  date unique ; dossiers choisis ; aperçu et historique des passages ; comptes rendus dans « À relire » ; jamais d’envoi, de signature
  ni de facturation par une routine.
- **Connexions API** dans Paramètres › Connexions : AR24, Youtrust, Légifrance, Judilibre, Pappers Justice, Pennylane, DocuSign,
  SuperPDP, B2Brouter, GoCardless, Chorus Pro, Maileva, Microsoft Graph, Google Workspace, Axonaut. L’IA consulte les connexions
  vérifiées en lecture ; toute écriture (signature, recommandé, courrier, facture, prélèvement) est une opération préparée puis validée.

## 5.6.51 — Audit 5.6.50 : frontières d’accès, page Dossier refondue, huit fonctions d’autonomie

Corrections et livraisons de l’audit « AxiorHub Pilote 5.6.50 » (détail : `CONFORMITE-5.6.51.md`).

- **Accès et validation (B01, B02, B05)** : un objet rattaché à un dossier réservé (lien d’archive, demande, mission, chemin de fichier…)
  est refusé avant toute lecture ; les corrections du dossier demandent le droit « Valider » effectif ; un registre des droits illisible
  ne rouvre jamais les accès (copie de secours, diagnostic et restauration pour l’administrateur).
- **Références (B03)** et **messagerie (B04)** : article comparé par code et numéro ; messages par lots sans trou.
- **Page Dossier en six onglets** : Vue d’ensemble, Faits et arguments, Procédure, Pièces, Correspondances, Travaux ; bandeau avec l’état
  réel de l’analyse et « Prendre en charge une mission ».
- **Parties** sans doublons ni fusion abusive (alias, rapprochements à confirmer, fusion réversible) ; **informations** attribuées à leur
  auteur, qualifiées (allégué, contesté, constaté…), validées par « Enregistrer », « Hors sujet », « À vérifier », « Refuser » ; refus
  conservés après une nouvelle analyse ; **rappel des faits** et **rappel de la procédure** rédigés avec leurs sources.
- **Pièces** : seulement les pièces (nos pièces, pièces de chaque adversaire, à numéroter, à classer) ; actes, jurisprudence, notes et
  documents de gestion à part ; couverture documentaire et relance ciblée des lectures en erreur.
- **Procédure** : procédure de référence, procédures historiques ou citées, actes aux dates distinctes, décisions examinées ou
  seulement mentionnées ; chronologie commune (litige / procédure).
- **Autonomie** : missions lancées depuis le dossier (préparation d’audience, livrable libre ; double clic = même mission) ; décisions
  et consignes du cabinet mémorisées et utilisées par chaque rédaction ; « Depuis votre dernière lecture » ; informations manquantes ;
  relecture Word par propositions appliquées en suivi des modifications dans un nouveau fichier ; file unique « À décider / À relire »
  (Aujourd’hui, Dossier, Produire) ; préparation d’audience aux paliers J-14 / J-7 / J-2 (désactivée par défaut) ; apprentissage des
  corrections à portée choisie (ce document, ce dossier, mon style).

## 5.6.50 — Messagerie interne, droits par compte, dossiers réservés, fichiers sur un autre serveur WebDAV

Demande du cabinet (15 octobre 2026) : une messagerie entre les membres du cabinet qui ne dépende pas d’une session Nextcloud, des
droits et pouvoirs réglables compte par compte, la possibilité de réserver des dossiers, et des fichiers sur un serveur WebDAV autre
que Nextcloud.

- **Messagerie du cabinet** (menu « Messagerie », pastille des non-lus) : conversations à deux, groupes (créés par n’importe quel
  membre ; le créateur retire des membres, chacun peut partir) et **fil de chaque dossier**, ouvert depuis la page du dossier.
  Chaque message porte le nom et le rôle de son auteur ; « @identifiant » prévient un membre ; un message se retire (il reste
  signalé comme retiré). Tout reste sur le serveur du cabinet ; un administrateur ne lit pas les conversations des autres.
- **Avis de l’agent** dans le fil du dossier : document produit (lien vers les travaux de l’agent), réponse à un courriel déposée
  dans les brouillons, fiche du dossier mise à jour. L’agent ne lit jamais les conversations.
- **Droits par compte** (Paramètres › Droits et accès, administrateur) : sur la base du rôle, six droits s’accordent ou se retirent
  compte par compte — envoyer des courriels, déposer des brouillons, valider les décisions et productions, gérer l’agenda,
  facturation et temps, réglages du cabinet. Un administrateur garde tous ses droits ; la gestion des comptes et des droits lui
  reste réservée. Contrôle côté serveur, dans la version serveur, la distribution autonome et l’ExApp.
- **Dossiers réservés** (facultatif, rubrique « Accès au dossier » de la page du dossier) : un dossier réservé n’est visible que
  des comptes choisis et des administrateurs — listes et sélecteurs de dossiers, recherche du cabinet, assistant, agenda,
  messagerie — et toute requête qui le désigne est refusée. La boîte de réception reste commune (boîte unique du cabinet). Les
  travaux de l’agent pour le cabinet ne sont pas cloisonnés.
- **Fichiers sur un autre serveur WebDAV** que Nextcloud (Paramètres › Connexions : « Serveur WebDAV autre que Nextcloud ») :
  mêmes racines autorisées, même lecture, création sans écrasement ; un fichier « supprimé » est déplacé dans « Corbeille AxiorHub »
  du même dossier (jamais effacé) ; l’ouverture en ligne reste propre à Nextcloud ; les agendas viennent des agendas CalDAV de
  chaque utilisateur (Paramètres › Agendas).

## 5.6.49 — Recherche juridique avant chaque réponse

Constat du 15 octobre 2026 : les connecteurs juridiques du cabinet (GoodLegal, OpenLegi, LibreJustice…) n’étaient consultés que par la
page Recherche juridique, la préparation d’audience et les missions. Une question à l’agent, une note, des conclusions, un courrier ou une
réponse de l’assistant étaient rédigés sans eux : le modèle citait le droit de mémoire, avec des erreurs (essai du cabinet sur
l’article 1240 du Code civil : « responsabilité de plein droit » au lieu de responsabilité pour faute).

- **Recherche avant la rédaction** : questions et documents (courrier, note, consultation, conclusions, mise en demeure, assignation…),
  réponses de l’assistant, et réponses aux courriels reçus quand le courriel pose une question de droit. Les connecteurs utilisables
  sont interrogés avant que le modèle écrive ; les textes et décisions trouvés lui sont donnés comme sources (L1, L2…).
- **Ce qui part** : une requête courte, sans noms propres (retirés), puis anonymisée (noms du dossier, adresses, téléphones, IBAN,
  numéros RG). Pour un courriel, seules les phrases qui posent la question partent. Aucune pièce ni aucun courriel entier.
- **Statut des sources** : seule une décision revérifiée sur la source officielle est « vérifiée » ; le reste est une piste, citée
  avec la mention « (à vérifier) ». Le modèle ne doit inventer aucune référence et marque [À VÉRIFIER : …] un point de droit sans source.
- **Traçabilité** : la fiche de contrôle de chaque document (et les limites de chaque réponse) indique les connecteurs consultés, ceux
  qui n’ont pas répondu, ceux qui ne sont pas utilisés et pourquoi. Une référence citée qui ne vient d’aucune source consultée est
  ajoutée aux points à compléter ; dans une réponse à un courriel, elle bloque le dépôt automatique du brouillon.
- **Paramètres › Sources juridiques** : nouvelle rubrique « Recherche avant chaque réponse » : chaque connecteur, utilisé ou non, avec
  la raison (désactivé, envoi non autorisé, test de connexion à refaire, usage « analyse juridique » non coché).
- Une même question n’est pas renvoyée aux connecteurs pendant une heure ; une panne d’un connecteur n’empêche jamais la réponse,
  qui le dit.

## 5.6.48 — Page de connexion, premier compte, déconnexion et mot de passe oublié

Demande du cabinet (14 octobre 2026) : à la première installation, rien n’indiquait comment choisir l’identifiant et le mot de passe,
et l’interface n’avait ni bouton de connexion ni bouton de déconnexion.

- **Page de connexion** (version serveur) : la fenêtre « Basic » du navigateur est remplacée par une page aux couleurs d’AxiorHub, en
  français, anglais, allemand et espagnol, lisible sur téléphone et en mode sombre. « Se souvenir de moi » garde la session 30 jours
  sur l’appareil (12 heures sinon). Après connexion, on revient sur la page demandée. Les tentatives sont limitées (5 par identifiant
  et 20 par adresse en 15 minutes). La session est un cookie `HttpOnly`, `Secure`, `SameSite=Lax` ; le serveur ne garde que son
  empreinte.
- **Compte existant repris tel quel** : l’identifiant et le mot de passe actuels (`ui-auth.json`, ou ceux changés dans Paramètres ›
  Comptes) ouvrent la session administrateur, avec les données du cabinet. Changer ce mot de passe ferme les sessions ouvertes.
- **Premier lancement** : une nouvelle installation ne demande plus de mot de passe dans le terminal. La première visite ouvre la page
  « Premier lancement », qui crée le compte administrateur avec le code privé écrit sur le serveur
  (`sudo cat /var/lib/axiorhub-mail-agent/premier-compte.code`). Les inscriptions sont ensuite fermées.
- **« Se déconnecter »** : bouton en pied du menu, sous le nom et le rôle du compte connecté.
- **Mot de passe oublié** : un lien valable 30 minutes est envoyé à l’adresse de récupération (Paramètres › Comptes), par le serveur
  SMTP de la messagerie. La réponse est la même que le compte existe ou non. Sans adresse ni envoi : `sudo python3
  /opt/axiorhub-mail-agent/current/reinitialiser-acces.py` sur le serveur.
- **Comptes du cabinet** : l’administrateur ajoute des comptes avocat ou assistant(e) dans Paramètres › Comptes, avec un mot de passe
  provisoire à changer à la première connexion. Il peut aussi changer leur rôle, les désactiver (leurs sessions sont fermées) ou leur
  donner un nouveau mot de passe provisoire. Les droits par rôle sont ceux de la distribution autonome ; les en-têtes d’identité
  envoyés par le navigateur ne sont jamais crus.
- Inchangés : l’en-tête « Basic » de l’administrateur reste accepté pour les scripts du cabinet. Les accès d’intégration (jeton Open
  WebUI, application Nextcloud, ExApp, fenêtre du poste, distribution autonome) gardent leur propre connexion.

## 5.6.47 — Correctifs : longues générations locales, lecture vocale fluide, recherche de dossier dans l’agenda

Signalement du 14 octobre 2026 : « Délai dépassé — étape : génération (/api/chat) » en préparant la fiche d’un dossier de 202 pièces.

- AxiorHub demandait à Ollama la réponse entière d’un coup : le délai de 240 s portait sur toute la génération, et une fiche longue
  dépassait ce délai alors que le modèle travaillait encore.
- La génération locale se fait maintenant en flux : le délai porte sur le silence entre deux morceaux de réponse (un modèle qui
  écrit n’est jamais coupé), avec un plafond de 30 minutes pour la génération entière. Un modèle qui ne répond plus reste arrêté au
  délai, et « Pause de l’IA » ou « Arrêter » ferment toujours la connexion.
- **Lecture vocale (Kokoro…)** : 3 à 4 s de silence entre deux paragraphes du résumé oral, le temps de synthétiser le suivant. Il est
  désormais préparé pendant la lecture du précédent ; la pause entre paragraphes est réglable dans Paramètres › Synthèse vocale
  (500 ms par défaut, valeur propre à chaque langue possible : `fr=500`, `en=400`…).
- **Agenda, « Ajouter un événement »** : la liste des dossiers affiche le nom complet du dossier (et non le seul client), dans l’ordre
  alphabétique, sans plafond de 400 ; toute liste de dossiers de plus de 12 choix reçoit une case « Rechercher un dossier ».

## 5.6.46 — Correctif : plus d’écriture de plusieurs minutes dans la base

Relevé « écritures longues » du serveur du cabinet (14 octobre 2026) : deux traitements périodiques tenaient la base en écriture, ce
qui faisait attendre le worker (« base de données occupée… ») et pouvait afficher « Un instant… » dans les pages.

- **Rapprochement des courriels** (`portfolio.reconcile_work_items`, 250 à 330 s toutes les 15 minutes) : la transaction s’ouvrait au
  premier changement d’état d’une carte puis restait ouverte pendant la lecture IMAP des messages suivants (jusqu’à 200). Les
  décisions sont maintenant prises d’abord, puis écrites en une transaction courte ; une carte changée entre-temps (par exemple
  archivée par l’avocat) n’est jamais écrasée.
- **Agenda** (`workplan.calendar_events`, environ 28 s) : le rattachement des événements aux dossiers se calculait dans la
  transaction, avec des motifs recompilés pour chaque événement. Il est calculé avant d’écrire, avec des motifs préparés une fois
  (mêmes résultats) ; la transaction ne contient plus que l’effacement de la période et l’insertion des lignes prêtes. Même
  préparation pour les tâches et le plan de travail.

## 5.6.45 — Correctif : le worker survit à une base occupée

Journal du serveur du 14 octobre 2026 (installation de la 5.6.44, 17:07:52) : le worker s’est arrêté sur « database is locked » au
début d’un tour de sa boucle (`work_once`), hors de la préparation du démarrage protégée en 5.6.44.

- La boucle du worker annule sa transaction, écrit « base de données occupée par un autre processus (worker N…) ; nouvel essai dans
  N s » dans le journal, attend (3 s, puis jusqu’à 60 s) et reprend, au lieu de s’arrêter ; un travail resté « en cours » par cette
  interruption est rendu à la file (ou signalé s’il ne peut pas être rejoué sans risque).
- La reprise des travaux interrompus au démarrage du worker attend elle aussi que la base soit libérée.
- Toute autre erreur de base reste remontée (le service est relancé par systemd et l’erreur reste visible).

## 5.6.44 — Démarrage tolérant au verrou, test de Talk, audit 5.6.43 (F01 à F10), corrections dans la page du dossier

Audit « AxiorHub Pilote 5.6.43 » et demandes de l’avocat du 14 octobre 2026. Détail : `CONFORMITE-5.6.44.md`.

- **Démarrage tolérant au verrou de la base** : si un autre processus tient l’écriture de la base au démarrage, l’interface, le worker
  et la veille attendent et réessaient (1, 2, 4… 15 s, jusqu’à 5 minutes, `AXIORHUB_STARTUP_WAIT`) avec une ligne claire dans le
  journal, au lieu de s’arrêter en boucle. Les écritures faites à chaque démarrage (parcours fournis, cas d’évaluation, aperçus
  expirés) n’ont lieu que si quelque chose a changé : un démarrage sans changement n’écrit rien.
- **« Tester l’affichage » de Talk** : règle des navigateurs — la directive `frame-ancestors` l’emporte sur `X-Frame-Options` ;
  l’intégration n’est jugée possible que si chaque politique reçue autorise l’adresse d’AxiorHub vue par le navigateur.
- **Page du dossier** : chaque partie, la juridiction et le numéro de RG proposés par l’IA se corrigent (« Corriger »), se suppriment
  ou s’ajoutent ; corrections historisées et annulables (« Rétablir », « Annuler ma correction ») ; une proposition supprimée ou
  corrigée est refusée et n’est plus réutilisée dans les rédactions ; doublons évidents de parties fusionnés (même nom suivi d’une
  adresse ou d’une précision entre parenthèses).
- **Audit 5.6.43** :
  - F01 : l’arrêt ferme la connexion exacte de l’appel au modèle, même après réception des en-têtes ; plus de promesse
    « dans la seconde » ;
  - F02 : un courriel sans pièce jointe reste conservé quand les deux règles sont actives ;
  - F03 : les travaux de classement portent leur dossier (pause du dossier) ; travaux existants rattachés une fois ;
  - F04 : autorisation et pause du dossier relues avant chaque dépôt automatique ;
  - F05 : progression persistante de la boîte (au-delà des 30 derniers messages), messages différés repris ;
  - F06 : mandat « Confier ce classement à l’IA » durable, avec sa sélection exacte, repris après la pause ;
  - F07 : un classement annulé peut être relancé ; état affiché aligné sur le travail ;
  - F08 : lecture et manifeste versionnés (`lecture.v2.md`…), aucune version vérifiée remplacée ;
  - F09 : la pause suspend un travail actif (reprenable), sans second clic si la reprise intervient pendant l’interruption ;
  - F10 : « Arrêter » pendant une pause devient une annulation définitive.

## 5.6.43 — Pause et arrêt fiables, courriels du cabinet, classement des courriels, agendas séparés, Talk, navigation

Cahier des charges « IA, courriels, agendas, ergonomie et navigation » (version 9, 14 octobre 2026). Détail : `CONFORMITE-5.6.43.md`.

- **Pause et arrêt (§ 16)** : contrôleur unique `agent/arret5643.py`. « Pause de l’IA » suspend les travaux en attente (état
  `suspended`, repris à la reprise), demande l’arrêt des travaux d’IA en cours et coupe un appel au modèle en cours dans la seconde
  (connexion suivie et fermée) ; les travaux sans IA (dépôts, conservation, agenda) continuent. L’arrêt d’un travail arrête ses
  travaux enfants ; origine, parent et dossier de chaque travail sont enregistrés (migration 0643). Les planificateurs ne créent
  aucun travail automatique pendant la pause.
- **Courriels du cabinet (§ 21)** : garde commune `agent/sens5643.py` (identités du cabinet, sens du message, destinataires) ; aucun
  brouillon adressé au cabinet lui-même ; les messages envoyés par le cabinet ne reçoivent jamais de réponse automatique ; la
  routine du matin passe les courriels urgents à la rédaction avec le dossier au lieu d’un modèle vide ; « À COMPLÉTER » signalé.
- **Compétences (§ 7)** : la vraie consigne de l’avocat est rechargée pour choisir les compétences ; sélection par phase (analyser,
  décider, rédiger, contrôler, courriel) ; budget d’assemblage unique (24 000 caractères) sans coupure au milieu d’une compétence ;
  cinq méthodes fournies dans la rubrique Compétences, désactivées par défaut.
- **Classement des courriels et des pièces (§ 10 à 14)** : dans « Courriels », bloc « Classement dans le dossier client » :
  enregistrer le courriel (original `.eml` octet pour octet et lecture `.md`) dans CORRESPONDANCES et les pièces choisies dans
  NOS PIECES / PJ courriels ; dépôt exclusif, relu et vérifié par empreinte ; état honnête (en attente, vérifié, partiel, échec,
  incertain) ; règle générale du compte et règle par courriel, mandat à l’IA ; onglet « Correspondances » du dossier (lecture,
  original téléchargeable, notes du cabinet séparées). Migration 0644 (tables des courriels conservés).
- **Produire lisible (§ 18)** : cartes avec nom du dossier, état en français, date JJ/MM/AAAA HH:mm ; taille de texte de base
  portée à 100 % de la préférence du navigateur (16 px au lieu de 15 px) ; contrastes des deux thèmes revus.
- **Playbooks (§ 19)** : parcours en erreur repris étape par étape (« Reprendre cette étape »), arrêt du parcours, détail du
  parcours affiché d’abord, nom métier du parcours et états en français ; réservation atomique d’une étape.
- **Navigation (§ 20)** : six accès principaux, Talk sous condition, menu complémentaire de huit entrées ; sous-sections trouvées
  par la recherche ; onglets de « Produire ».
- **Agendas (§ 17)** : « Agenda en ligne » et « Agenda personnel » séparés ; l’Agenda personnel n’est jamais copié vers un agenda en
  ligne ; les anciens événements copiés vers Nextcloud sont présentés pour un choix explicite (garder localement ou garder la copie).
- **Page Talk (§ 22)** : option par utilisateur dans Paramètres › Connexions ; Nextcloud Talk affiché sous « Agenda et tâches » avec
  une politique de sécurité propre à cette page ; « Tester l’affichage ».
- Écrans, messages et guide traduits en anglais, allemand et espagnol pour ces nouveautés.

## 5.6.42 — Audit 5.6.41 (F01 à F07), comptes dans les paramètres, mode ExApp

Audit de la 5.6.41 et cahier des charges « AxiorHub Pilote ExApp ». Détail : `CONFORMITE-5.6.42.md`.

- **F01 (P0) — unité comptable** : méthode de preuve 5. Une mention d'unité portée par une ligne chiffrée ne vaut que pour cette ligne ;
  une déclaration (ligne sans montant) vaut pour son tableau ou sa section, ou pour le document si elle est dans sa zone de titre ; deux
  unités au même niveau : `unite_non_etablie` ; la source de l'unité (ligne, positions) est jointe à la preuve ; les rapports établis avec
  la méthode 4 sont à recontrôler (écarts présentés avant régénération).
- **F02 — anciens modèles Word** : métadonnées (champs, clauses, empreinte) reconstruites à la première ouverture, hors transaction,
  idempotentes, libellés et version conservés ; source illisible signalée sur la fiche avec l'action à mener.
- **F03 — Word modifié** : empreinte comparée avant toute préparation ; nouvelle version, champs nouveaux et disparus présentés, refus
  explicite plutôt que des options périmées ; l'aperçu et « Préparer » portent sur la version affichée ; bouton « Actualiser le modèle ».
- **F04 — trame IA fidèle** : sauts de ligne (`w:br`, `w:cr`) et tabulations conservés dans le texte transmis à l'IA.
- **F05 — instantané exigé** : une demande avec trame choisie ne se rédige jamais sur une autre version (`instantane_modele_introuvable`,
  `instantane_modele_altere`), erreur propagée à l'état du travail.
- **F06 — couverture structurelle** : une section n'est présente que si un intitulé lui correspond et qu'un corps exploitable le suit ;
  sections absentes, seulement citées ou vides signalées dans la relecture ; contrôle impossible affiché comme tel.
- **F07 — purge sûre** : relation explicite demande–instantané, références établies avant toute suppression dans une transaction, erreur
  de lecture = purge différée ; maintenance bornée sur les seuls candidats expirés (index).
- **Paramètres › Comptes** (nouvelle rubrique) : identifiant et mot de passe de l'administrateur de l'interface (version serveur et
  postes), changement avec le mot de passe actuel ; distribution autonome : accès à la page Comptes.
- **Mode ExApp** (`agent/exapp5642.py`, `exapp.py`) : moteur livrable comme application externe Nextcloud (AppAPI, HaRP) — cycle de vie,
  en-têtes AppAPI, rôles par groupes Nextcloud, droits de la distribution autonome, pages affichées dans Nextcloud, services Nextcloud
  par les en-têtes AppAPI ; conditionnement dans le dépôt séparé AxiorHub-Pilote-ExApp (version de développement 0.1.0).
- **Application Nextcloud 0.4.3** : choix guidé de la configuration (serveur, même ordinateur, poste avec Nextcloud hébergé), « Tester
  l'adresse », date du dernier test réussi.

## 5.6.41 — Application Nextcloud : mode découverte sur un poste, prérequis et étapes d’installation

Demandes de l’avocat du 13 octobre 2026. Détail : `CONFORMITE-5.6.41.md`.

- **Version poste (Windows 11, Ubuntu poste) utilisable avec l’application Nextcloud en mode découverte**, pour des tests seulement :
  - *Nextcloud sur ce même ordinateur* : les appels de l’application venus de l’adresse de bouclage sont acceptés (y compris sous le nom
    `host.docker.internal` pour un Nextcloud dans Docker Desktop, sur la seule route d’intégration) ; les réponses `health` et `session`
    portent `mode: poste` et un avertissement, que l’application affiche en bandeau permanent ; cookie de session admis dans le cadre sur
    `127.0.0.1` ;
  - *Nextcloud hébergé* : « Mon AxiorHub local » (application 0.4.2) ouvre AxiorHub dans un onglet du navigateur ;
  - appel relayé depuis un autre ordinateur : refus explicite `moteur_poste_non_pris_en_charge` (HTTP 409), « utilisable seulement en mode
    découverte… installez la version serveur ».
- **Paramètres › Connexions › Application Nextcloud** sur le poste : avertissement, adresses à saisir (même ordinateur, Docker Desktop,
  Nextcloud hébergé), identifiant et mot de passe locaux, case « Autoriser la liaison des comptes depuis Nextcloud (même ordinateur) ».
- **Application Nextcloud 0.4.2** : bandeau du mode découverte, réglage « Adresse de mon AxiorHub local » et entrée « Mon AxiorHub
  local », messages ; description de la boutique (anglais, français) et README réorganisés : prérequis, trois étapes (version serveur sur
  Linux, application depuis la boutique Nextcloud, liaison des comptes), section « Mode découverte » et messages en cas de difficulté.
- Guides du poste (Windows 11, Ubuntu) et README : mode découverte et son avertissement ; version serveur recommandée.
- **README en français, anglais, allemand et espagnol** : section « Installer » réécrite (choix de la version ; version serveur Linux recommandée, serveur existant ou VPS ; poste Windows 11 ; poste Ubuntu 24.04 ; premier réglage ; application Nextcloud et mode découverte) et **captures d’écran actualisées dans chaque langue** (`docs/captures/<langue>/`, données fictives) ; les captures de la 5.6.1 sont retirées.
- **Interface dans Nextcloud** : le service worker n’intercepte plus les pages affichées dans un cadre (le navigateur garde la session du cadre ; constaté sur un banc local, où le cadre restait blanc dès la deuxième ouverture).
- Mode sombre : barre des routines d’« Aujourd’hui » lisible (fond clair et libellés blancs auparavant).

## 5.6.40 — Corrections de l’audit 5.6.39 : preuve des chiffres, bibliothèque de modèles fiable, attente bornée

Audit du code 5.6.39 (7 défauts, 2 optimisations, 3 améliorations). Détail, tests et limites : `CONFORMITE-5.6.40.md`.

- **Chiffres du rapport de gestion (B01, priorité absolue)** : un montant d’une autre ligne n’est plus jamais « justifié ». Le segment d’un
  poste s’arrête en fin de ligne et devant tout nouvel intitulé, même inconnu (« Charges externes ») ; le libellé ET le montant doivent
  figurer dans l’extrait cité ; plusieurs montants sans colonnes de tableau établies restent « à vérifier ». Méthode 4 : les rapports
  antérieurs sont signalés pour recontrôle, les écarts avec l’ancien contrôle sont présentés et le rapport déjà relu reste dans le dossier.
  Les contrôles des chiffres sont désormais conservés avec l’opération (ils n’étaient pas affichés).
- **Bibliothèque de modèles** : un seul « modèle effectif » pour le téléchargement, le dépôt et l’IA — le Word choisi par le cabinet est
  bien celui transmis à la rédaction IA (B02), avec un instantané complet et immuable ; les champs et clauses d’un modèle Word sont
  proposés dans le formulaire, libellés modifiables, fautes de frappe signalées (B03) ; une clause mal fermée est refusée avec la ligne en
  cause, à l’enregistrement comme à la préparation, sans jamais produire un document tronqué (B04) ; plus aucune coupe silencieuse de la
  trame : refus explicite si elle est trop longue, sections manquantes signalées après rédaction (B05) ; expiration réelle des
  téléchargements à 24 heures et purge périodique (B06) ; remplissage Word qui conserve le gras, l’italique et les signets des balises (B07).
- **Plus rapide** : une seule analyse des comptes par document (P01) ; catalogue construit en une lecture, sans création de tables à chaque
  affichage (P02).
- **Interface** : droit applicable par défaut réglé pour le cabinet, indépendant de la langue de l’interface ; chaque document affiche son
  droit et sa langue (E01) ; préparation en quatre étapes avec « Aperçu du document » calculé par le même moteur que le document final,
  personnalisation avancée repliée (E02) ; pages « base occupée » : essais automatiques espacés (3, 5, 8, 13 s) puis page stable avec
  « Réessayer », référence et diagnostic ; après un clic, aucun renvoi automatique du formulaire (E03).

## 5.6.39 — Bibliothèque de modèles (Corporate) et corrections de l’audit 5.6.37

Demande de l’avocat du 12 octobre 2026 et audit du code 5.6.37 (8 fiches). Détail, tests et limites : `CONFORMITE-5.6.39.md`.

- **Bibliothèque de modèles** (Corporate › « 📚 Bibliothèque de modèles », avec ou sans société choisie) : 47 trames prêtes à l’emploi.
  Droit français : statuts de SAS, SASU, SELAS, SARL, EURL, SELARL, SCI, SCM et SCP avec clauses à cocher (agrément, préemption, sortie
  conjointe, sortie forcée, inaliénabilité, exclusion, changement de contrôle, actions de préférence, comité, directeurs généraux…) ;
  pacte d’associés, pacte d’actionnaires, lettre d’intention, protocole sous conditions suspensives, compromis de vente de fonds de
  commerce sous conditions suspensives, actes de cession de titres et de fonds de commerce ; contrats (prestation de services,
  confidentialité, CGV, compte courant d’associé, prestations intragroupe). Droit allemand (GmbH, UG, Gesellschaftervereinbarung,
  Anteilskaufvertrag…), espagnol (S.L., S.A., pacto de socios, compraventa de participaciones…) et anglais (Articles of Association,
  Shareholders’ Agreement, SPA…), chacun avec ses contrats et la mention « à faire valider par un conseil local ».
- **Préparer un document** : variables pré-remplies depuis la société choisie (dénomination, siège, capital, associés, dirigeant…),
  articles numérotés automatiquement, clauses retenues seules ; Word à télécharger, dépôt dans le dossier (JURIDIQUE) ou rédaction par
  l’IA, qui reçoit la trame remplie comme base. Le Pilote reçoit aussi la trame lorsqu’une demande de rédaction la désigne clairement
  (« statuts d’une SAS », « pacte d’actionnaires », « lettre d’intention »…).
- **Modèles du cabinet** : « Personnaliser ce modèle » (texte, {{variables}}, clauses [[si …]] … [[fin]]), « Télécharger la trame en
  Word » puis « Mon modèle Word » (mise en forme du cabinet conservée), « Ajouter mon modèle », « Revenir au modèle d’origine ».
- **Audit 5.6.37** : chiffres du rapport de gestion rattachés à leur poste, leur devise, leur unité et leur exercice dans le même segment
  (méthode 3) ; mise en file Corporate transactionnelle, notification après validation ; réalisation réservée aux actes prêts, parcours
  distinct « actes établis hors d’AxiorHub », date de décision confirmée inscrite au registre ; écarter une initiative annule ses
  rédactions enfants (autorisation vérifiée au démarrage et avant le dépôt) ; relance après paiement partiel : texte corrigé conservé et
  relecture confirmée ; abandon pendant la préparation respecté ; application Nextcloud 0.4.1 (réponses de session obsolètes ignorées).
- **Pages d’erreur lisibles** (400 « Action non effectuée », 401, 404, 405, 500, 503) : logo, explication, boutons « Recharger la page »,
  « Revenir en arrière » (page d’origine du même site) et « Accueil » ; une adresse inconnue donne « Page introuvable » (404). Moins
  d’erreurs affichées : une page en erreur inattendue est réessayée une fois automatiquement, la base occupée l’est jusqu’à
  disponibilité, le nom du site est comparé sans tenir compte des majuscules ni du port par défaut, et sur le poste « localhost » et
  « 127.0.0.1 » sont redirigés l’un vers l’autre, et Apache affiche « AxiorHub redémarre » (rechargement automatique) au lieu de 502/503 pendant un redémarrage
  (`install-interface.py`, modèles `deploy/`).
- Aide « Corporate » mise à jour en quatre langues ; textes nouveaux traduits en anglais, allemand et espagnol.

## 5.6.38 — Corrective : l'interface AxiorHub s'affiche dans Nextcloud même avec l'identifiant AxiorHub mémorisé

Signalement de l'avocat du 12 octobre 2026 (application Nextcloud 0.4.0 : « … n'autorise pas la connexion »). Détail : `CONFORMITE-5.6.38.md`.

- Quand AxiorHub et Nextcloud sont sur le même domaine, le navigateur envoie aussi l'identifiant de connexion d'AxiorHub qu'il a
  mémorisé ; la session Nextcloud n'était alors pas examinée et la page partait avec « X-Frame-Options: DENY ». La session Nextcloud est
  désormais vérifiée en premier ; la redirection de l'adresse à usage unique est elle aussi affichable dans le cadre du seul site
  Nextcloud de la clé. Une connexion par mot de passe sans session Nextcloud reste interdite de cadre.

## 5.6.37 — L’interface AxiorHub dans Nextcloud, instruction au Pilote corrigée, audit 5.6.36

Demandes de l’avocat du 12 octobre 2026 sur l’application Nextcloud et audit du code 5.6.36. Détail, tests et limites : `CONFORMITE-5.6.37.md`.

- **Instruction au Pilote depuis Nextcloud** : l’échec « autorisation_mission_non_verifiable » (installation sur un serveur, compte
  relié = compte du serveur) est corrigé : la mission appartient au cabinet, comme celles lancées depuis l’interface ; en distribution
  Docker, elle reste au compte relié.
- **Application Nextcloud 0.4.0** : la page « AxiorHub Pilote » affiche l’interface AxiorHub elle-même, avec ses rubriques dans le menu
  Nextcloud aux couleurs d’AxiorHub ; « Ouvrir dans un onglet » et « Vue simplifiée » (écran de la 0.3.0).
- **Session de l’interface dans Nextcloud** (contrat d’intégration 1.3) : `POST /integration/v1/session`, signé avec la clé personnelle,
  remet une adresse de connexion à usage unique (60 secondes, empreinte seule conservée) ; `/nextcloud-session` l’échange contre un
  cookie de session de 12 heures (HttpOnly, limité au chemin ; Secure, SameSite=None, Partitioned en HTTPS), révoqué avec la clé.
  L’affichage dans un cadre n’est permis qu’au site Nextcloud déclaré par la clé et seulement pour cette session ; une connexion par
  mot de passe reste interdite de cadre. Dans un cadre sans session, une page sans donnée demande à l’application une nouvelle adresse
  (jamais de fenêtre de mot de passe).
- **Audit du code 5.6.36 (16 fiches)** : liaison Nextcloud à travers la façade Docker ; clé personnelle confrontée au compte actuel
  (désactivation, rôle, mot de passe) ; décisions réservées à l’avocat et à l’administrateur ; pièces du manifeste identifiées et
  versionnées ; chiffres du rapport de gestion contrôlés dans leur extrait (poste, montant, signe, devise, unité, exercice) ;
  Corporate : la saisie prépare un projet, les registres changent à « Enregistrer la réalisation », contrôles complets (annulations
  cumulées, souscriptions), une seule transaction, file pleine sans effet, clé d’idempotence ; initiatives écartées sans effet et
  rédactions suivies jusqu’au document ; relances : niveau fondé sur les envois confirmés, solde revérifié avant dépôt, routine du
  jour récupérable ; journées entières copiées vers Outlook en dates civiles.
- **Application Nextcloud 0.4.0** : menu repliable (choix mémorisé) ; description de la boutique en anglais et en français avec un
  tutoriel d’installation de la version serveur sur Ubuntu.
- **Affichage intégré** : dans Nextcloud, un seul menu (celui de Nextcloud) ; le menu d’AxiorHub est masqué et « Toutes les
  fonctions » devient une page ouverte depuis le menu Nextcloud ; la page prend toute la largeur.

## 5.6.36 — Application Nextcloud par utilisateur, notifications apaisées, initiatives, données des pièces, outils IA, corporate

Demandes de l’avocat du 12 octobre 2026 (correctifs, application Nextcloud, nouvelles fonctions). Détail demande par demande, avec
les tests et les limites : `CONFORMITE-5.6.36.md`.

- **Application Nextcloud 0.3.0** : chaque utilisateur relie son propre compte AxiorHub dans ses paramètres personnels Nextcloud
  (adresse, identifiant, mot de passe échangé contre une clé personnelle révocable) ; un utilisateur non relié n’a aucun accès.
  Interface complète : instruction au Pilote, recherche dans les dossiers, fiche, recherche juridique, travaux récents. « Moteur
  injoignable » corrigé : réponses JSON même quand la base est occupée (nouvel essai automatique), appels signés accessibles sans
  session en mode Docker, diagnostic précis dans l’application. Contrat d’intégration 1.2 (appairage, `instruction`, recherche).
- **Modèle de courrier** : seule la balise `{{corps}}` est obligatoire ; un refus nomme la balise manquante ou inconnue.
- **Notifications** : le bandeau rouge d’« Aujourd’hui » devient un indicateur neutre ; le détail « Points à vérifier » est en bas
  de page, repliable, avec un bouton par point (relancer, écarter les erreurs déjà vues, voir) ; un dossier illisible ne suspend plus
  la surveillance des autres ; la file pleine n’est plus une erreur. Barre d’activité repliable, repliée par défaut sur toutes les
  pages. Les demandes de l’agent et les missions s’annulent ou se retirent de la liste.
- **Pilote** : fenêtre centrée (l’icône reste à droite), utilisable sur téléphone.
- **« Un instant… »** : page graphique qui se recharge seule ; lectures réessayées automatiquement ; les écritures longues sont
  mesurées et affichées dans le diagnostic.
- **Envoi direct** : réglage « Autoriser l’envoi quand je clique sur « Envoyer » » (Paramètres › Messagerie), pour tous les dossiers
  ou certains, avec ou sans confirmation ; l’agent n’envoie jamais rien de lui-même.
- **Agents documentaires** : dossiers choisis en cliquant dans Nextcloud, réglages en cases et boutons.
- **README et confidentialité** : la promesse « jamais » sur les sources est remplacée par les contrôles réels, leurs limites et la
  validation attendue de l’avocat ; signalement d’anomalie anonymisé ; modèles de ticket GitHub ; contrôle de confidentialité étendu
  aux métadonnées des images et aux journaux.
- **Initiatives** : à réception d’une assignation, plan d’actions proposé (dossier « DEMANDEUR - DÉFENDEUR - référence », pièces
  jointes rangées, intervenants, audience à l’agenda, courriel au client, note sur les arguments adverses, projet de constitution,
  courriel au confrère pour les pièces, courrier à la juridiction), corrigé et validé par l’avocat, puis exécuté étape par étape
  (brouillons et projets seulement).
- **Données des pièces (GED)** : type, date, parties, dates clés, montants, textes cités et demandes de chaque pièce, avec leur statut,
  interrogeables dans tout le cabinet et exportables ; pièces jointes des courriels rangées dans « PJ courriels » du dossier.
- **Outils IA** : grilles d’analyse sourcées, playbooks de négociation appliqués à un contrat Word en modifications suivies (copie et
  note de justification), bibliothèque de mémos juridiques validés.
- **Corporate** : boutons « Approbation des comptes » et « Autre diligence en droit des sociétés » ; sociétés, approbation des comptes
  (dix actes, dont rapport de gestion établi à partir des comptes en PDF), transfert de siège, changement de dirigeant, cession de
  titres (une saisie, quatre effets), augmentation ou réduction de capital, dissolution, transformation et autres diligences,
  création de société selon sa forme ; table de capitalisation, registres des mouvements de titres et des décisions ; champs
  personnalisés. Modèles d’actes du cabinet : « Mes actes » (vos propres actes, proposés dans les opérations choisies) ou un modèle
  par acte, choisi dans Nextcloud ou importé depuis le poste ; variables `{{denomination}}` ou `{{ denomination }}` listées sous
  chaque formulaire avec un exemple fictif ; balises inconnues signalées dès l’enregistrement.
- **Contacts & CRM** : clients, parties, confrères, juridictions et experts reliés aux dossiers ; bloc « Intervenants » du dossier.
- **Relances d’impayés** : chaque matin, projets de relance des factures échues d’Invoice Ninja (lecture seule), à valider.
- **Microsoft** : complément Word (playbooks, mémos, reformulation, en modifications suivies) et agenda Microsoft 365 (copie des
  événements AxiorHub, lecture des rendez-vous Outlook).
- **Droits** : les nouvelles actions suivent les rôles (administrateur, avocat, assistant) aussi en mode Docker.
- **Traductions** : écrans, messages et fiches d’aide nouveaux en anglais, allemand et espagnol ; les actes générés restent en
  français.

## 5.6.35 — Corrective : preuves ancrées dans l’extrait, agenda fiable, contrat Nextcloud 1.1, application Nextcloud utilisable

Audit de la 5.6.34 (fiches F01 à F18 et améliorations proposées) et anomalies relevées sur un serveur en production. Détail fiche
par fiche, avec les tests et les limites : `CONFORMITE-5.6.35.md`.

- **Preuves des fiches (F01, F02)** : une date ou un montant n’est « documenté » que s’il figure dans l’extrait cité, jamais
  ailleurs dans le fragment ; montants structurés (valeur exacte, devise, signe) : un prix en USD ne justifie plus un prix en EUR,
  un avoir ne justifie plus un débit, une année ou un numéro de pièce n’est jamais un montant ; séparateurs ambigus signalés ;
  empreinte du passage conservée. La méthode de relevé passe à 5635.1 : les anciens relevés sont refaits, les corrections de l’avocat
  conservées, et une fiche établie avec l’ancienne méthode l’indique.
- **Agenda (F03 à F07, F18)** : chaque occurrence d’une série a sa propre entrée (cache reconstruit à la mise à jour) et sa propre
  identité à l’export ; une modification ne touche que les champs envoyés (durée, journée entière et notes conservés) et refuse
  d’écraser une modification faite entre-temps ; EXDATE et occurrences modifiées appliquées, UNTIL inclusif, rappels repris à
  l’import ; restauration réelle (statuts, rappels, annulations) avec aperçu puis confirmation, après sauvegarde de l’état actuel ;
  la copie vers Nextcloud se fait hors de toute transaction (plus de verrou de la base pendant un appel réseau).
- **Agenda, parcours** : « Modifier » sous chaque événement, « Annuler la série », recherche et filtre par dossier (200 événements
  visibles au lieu de 30), liste des sauvegardes avec « Restaurer… » ; la copie vers Nextcloud est présentée comme une copie à sens
  unique, jamais comme une synchronisation.
- **Contrat Nextcloud 1.1 (F08 à F13)** : l’opération signée doit correspondre à la route appelée ; contexte sans fuseau, mal formé
  ou incomplet refusé proprement (4xx, jamais d’exception) ; une demande refusée ne réserve plus rien et une clé d’idempotence est
  liée au contenu de la demande ; annulation durable même sans travail lancé ; manifeste de pièces vérifié (taille, empreinte,
  appartenance au dossier) et réellement appliqué au travail ; résultat figé à la première lecture (une nouvelle préparation ne le
  modifie plus) ; décisions « Approuver », « Refuser », « Demander une correction » avec commentaire et historique complet.
- **Application Nextcloud 0.2.0** : « Aucun dossier ne vous est accessible » corrigé — les dossiers du moteur sont importés
  automatiquement (groupe par défaut, réglable), un tableau d’administration rattache chaque dossier à un groupe ; secret partagé
  de 8 caractères au moins, comme le moteur ; résultat entièrement lisible avant décision (F16) ; suivi rattaché au travail lancé
  (F17) ; travaux récents et réouverture d’un résultat ancien ; liens du site, des anomalies et du code vers
  `AxiorHub-Pilote-Nextcloud`.
- **Diagnostic Windows (F14)** : propriétaire et liste d’accès réellement lus ; une DACL nulle (accès complet pour tous) ou vide est
  une erreur ; un compte ou un propriétaire non reconnu donne « à vérifier », jamais un voyant vert.
- **Recette automatique du serveur** : le test HTTP voit Waitress installé dans l’environnement de l’interface (ui-venv) au lieu de
  signaler « No module named 'waitress' » ; les contrôles du contrat d’intégration ne dépendent plus de l’umask (F15).
- **Traitements trop anciens** : un passage du relevé des pièces est borné à 20 minutes (réglable) ; une pièce longue reprend au
  passage suivant sans refaire les fragments déjà relevés ; un relevé interrompu par un redémarrage reprend seul ; l’ancienneté
  d’un traitement en cours se mesure depuis son démarrage, pas depuis son attente dans la file.
- **Affichage** : les échéances détectées d’un dossier sont datées en heure locale (« 13/10/2026 21:19 ») au lieu d’un horodatage
  ISO brut.
- **Traductions** : textes nouveaux de l’agenda, messages des opérations d’agenda, avis de méthode de la fiche, fiche d’aide « Agenda
  et tâches » réécrite en quatre langues.
- **Recette GitHub (F15)** : tests sous umask 022 et 077, contrôles 5.6.34 et 5.6.35 exécutés aussi sous Windows, journaux archivés
  pour les trois parcours (Linux, migrations root, Windows).

## 5.6.34 — Fiabilité des fiches, parcours simplifiés, agenda local, état du système par mode d’installation, contrat Nextcloud

Cahier des charges « Audit et préparation de la nouvelle version » (11 octobre 2026) et demandes complémentaires de l’avocat. Détail
fiche par fiche, avec les limites : `CONFORMITE-5.6.34.md`.

- **Relevés de pièces contrôlés (B01–B03, B05, B11, B12)** : validation JSON Schema complète côté serveur (entier ≠ 3,5, champs
  facultatifs, tailles, énumérations), reprise unique puis fragment « invalide » (jamais une fiche vide réputée réussie) ; chaque fait,
  date, montant et demande porte un extrait retrouvé dans le texte, sinon « à vérifier » ; page déduite de l’extrait (position pour un
  document non paginé) ; index complet sans plafond, omissions signalées ; état par pièce et reprises bornées (une pièce en échec ne
  bloque plus les suivantes, pause annoncée si le modèle est indisponible) ; cache dépendant de la méthode et du texte extrait ; bilan
  exact (« 23 fichiers, 20 extraits disponibles, 15 relevés complets contrôlés ; … sources utilisées ») au lieu de « pièces lues ».
- **Fiche du dossier (B04, B09, B10)** : corrections de l’avocat conservées indépendamment des versions de l’IA (migration des
  anciennes corrections, conflit explicite entre deux onglets, proposition de l’agent affichée à côté, historique et restauration) ;
  SAS ALPHA et SARL ALPHA ne sont plus fusionnées (rapprochement à confirmer) ; dates impossibles refusées, dates partielles gardées,
  affirmations sans pièce valide et dates égales à la date du fichier placées dans « À vérifier ».
- **Disponibilité et diagnostic (B07, B08, B15, B16)** : consultation des alertes et de la fiche d’audience sans écriture ni appel
  distant ; suivi de préparation borné, expliqué en cas d’interruption, sans seconde préparation (clé d’idempotence) ; erreurs
  qualifiées (service, étape, corrélation, ressource anonymisée) au lieu d’un « http_404 » générique ; « Détail » ouvre la demande
  précise, « Annuler » est une annulation durable (demandes de document, missions), jamais une pause.
- **Profil Juridique du modèle local (chapitre 5)** : température 0,3, top_p 0,9, top_k 40, repeat_penalty 1,0, contexte automatique
  borné par le modèle ; réglages conservés à la mise à jour (un zéro explicite n’est jamais remonté) avec proposition d’adoption ;
  sous-profil « Extraction factuelle » à 0 ; options réellement transmises à Ollama et mémorisées ; panneau Paramètres › IA › Modèles
  locaux (Tester, Restaurer le profil juridique, réglages avancés expliqués).
- **Parcours (chapitres 6 et 7)** : espace Audiences (Fiche / Préparer, plusieurs adversaires, téléversement d’écritures absentes du dossier) et Produire › Réviser avec sélecteurs de
  dossier et de document (plus de chemin ni d’identifiant à saisir) ; Recherche juridique sans dossier, registre unique des sources
  (B06) et résultats en cartes ; « Alertes du cabinet » (Marquer comme lu, Me le rappeler demain, Marquer comme traité, Rouvrir,
  Annuler la dernière action ; rouverte si son contenu change) ; bouton « Résumé » pleine largeur ; matrice des 64 fonctions.
- **Agenda local par défaut** : rendez-vous, audiences, échéances, rattachement aux dossiers, rappels, sauvegarde quotidienne, import
  .ics (Nextcloud, Google, séries simples) et export ; la synchronisation Nextcloud devient une option.
- **État du système selon le mode d’installation** : serveur Linux (systemd), poste Linux ou Windows (processus réellement lancés,
  activité, réponse de l’interface), fichier d’authentification réellement utilisé, autorisations Windows lues (ACL) ; alertes
  « fonctionnalité / cause vérifiée / action » ; connecteurs « Non configuré » ou « À tester ».
- **Traduction complète en anglais, allemand et espagnol** : les textes écrits par les scripts du navigateur (boutons comme
  « Commencer la dictée », états, compteurs « Suivi des engagements … · 0 suite(s) en cours », confirmations) sont désormais
  traduits avec le dictionnaire des écrans ; modèles à donnée insérée (« Modifier « … » »), phrases à segments « · », fil d’Ariane,
  libellés cités dans les aides ; environ 1 300 phrases ajoutées après un relevé de 300 pages. Les données (noms, objets, dossiers,
  références légales, exemples de commandes vocales prononcées en français) ne sont jamais traduites.
- **Rapports dans la langue du cabinet** : briefing du matin, tri des courriels, bilan de la semaine et résumé oral sont rédigés
  dans la langue choisie (cabinet, ou celle de l’avocat) ; les brouillons de réponse aux correspondants restent en français.
- **Application Nextcloud** : dépôt dédié `AxiorHub-Pilote-Nextcloud` (application à la racine, recette d’installation réelle).
- **Open WebUI** : la version classique n’a pas de section Courriel ; AxiorHub n’y renvoie plus (lien et cadre « Courrier Open WebUI »
  retirés) : la messagerie se consulte dans Courriels, Roundcube restant accessible.
- **Mise à jour (B13)** : le test « depuis 5.6.31 » part bien de 5.6.31 ; cas 5.6.32 et 5.6.33 ; réinstallation sans fausse promesse
  de retour arrière ; reçu contrôlé avant toute modification ; espace disque vérifié avant tout arrêt ; version de sortie centralisée ;
  ces tests root s’exécutent désormais dans la recette GitHub.
- **Guide d’installation (B17)** : étape « Synthèse vocale (Kokoro) » selon le système (Windows, poste Ubuntu, serveur) et voix
  par langue (l’allemand n’est pas proposé par Kokoro : eSpeak NG ou Chatterbox).
- **Application Nextcloud 0.1.0 (chapitre 9, cycle distinct)** : application native PHP + Vue 3 et contrat moteur `/integration/v1`
  (contexte signé, nonce, périmètre, idempotence, annulation, approbation d’une version). Non qualifiée : aucune compatibilité n’est
  annoncée avant installation réelle sur Nextcloud 32 à 35 (workflow manuel fourni).

## 5.6.33 — Correctif de mise à jour depuis la 5.6.31

- **Mise à jour refusée** : l’archive 5.6.32 répondait « Cette mise à jour directe exige une version précédente comprise entre 3.5.0
  et 5.6.32 » sur une installation 5.6.31. Le passage de version avait remplacé « 5.6.31 » par « 5.6.32 » jusque dans la liste des
  versions acceptées par `upgrade.py`. La liste est rétablie (5.6.31 et 5.6.32 acceptées) ; aucune autre différence avec la 5.6.32.
- **Contrôle permanent** (`tests/test_v5633.py`, exécuté sur tous les systèmes) : la version immédiatement précédente et toutes les
  versions 5.6.x publiées sont acceptées, la version installée ne l’est pas, la liste est ordonnée, et l’installateur web accepte la
  même version précédente. Le test de mise à jour sur système de fichiers, lui, ne tourne qu’en root sous Linux : c’est pourquoi
  l’erreur n’avait pas été vue.

## 5.6.32 — Veille planifiée, sources juridiques, compétences, documents de procédure, écrans traduits, interface sans blocage

Cahier des charges additionnel, deuxième livraison. Matrice de conformité : `CONFORMITE-5.6.32.md`. Tests : `tests/test_v5632.py`.

### Corrections
- **Interface qui se bloquait** (« Interface indisponible », retour après rechargement) : l’affichage d’une page réécrivait des données
  par défaut à l’ouverture de la base et attendait la fin des travaux de fond. Le schéma est désormais préparé une fois par processus,
  l’affichage n’écrit plus rien, attend au plus 2 secondes, saute les écritures facultatives si la base est occupée et, au pire,
  affiche « Un instant… » avec rechargement automatique. Une transaction ouverte est validée avant tout appel réseau (IA, Nextcloud,
  messagerie, connecteurs). Les erreurs d’interface sont consignées dans `erreurs-interface.log`.
- **Erreur 412 sur les tâches Nextcloud** : version de la tâche mise à jour après chaque écriture ; une version périmée est relue et
  rapprochée au lieu d’échouer ; une tâche présente plusieurs fois change d’état partout ; plus de tâches « Relire : … » en double.
- **Habitudes de style** : la longueur n’est plus une habitude à valider mais un repère automatique ; plan proposé seulement avec au
  moins trois vraies parties ; nouvelles habitudes utiles (numérotation, présentation des décisions citées, intitulé du dispositif) ;
  chaque proposition dit ce qu’elle changera.
- **Tests métier continus** : une fois par jour, vérification incrémentale de la chaîne d’audit.
- **Lisibilité en thème clair** : élément courant du menu « Toutes les fonctions » ; barre latérale recouverte sur Studio de production.

### Nouveautés
- **Examen du dossier entier** : fiche par pièce (faits datés avec la page, demandes, montants, références) ; l’IA examine toutes les
  pièces au niveau de détail que permet sa mémoire de travail, couverture affichée ; plus de « La génération a atteint sa limite » sur
  un dossier volumineux (garde de contexte et relance d’une réponse coupée).
- **Sources juridiques** (Paramètres › Sources juridiques et guide) : JusticeLibre sans clé, OpenLegi, GoodLegal, LibreJustice, Legal
  Data Hunter ; vérification à niveaux séparés et activation après accord sur les données externes.
- **Contrôle des références** dans « Vérifier » : articles, lois, pourvois, décisions, ECLI et citations contrôlés à la date utile.
- **Veille juridique planifiée** : thèmes, flux officiels, source juridique, Tavily ; cadre dans Aujourd’hui et page de lecture.
- **Compétences de l’IA** : trois intégrées (dont l’assistant juridique de Christophe Quézel-Ambrunaz, AGPL-3.0), création, import,
  copie, versions, choix automatique ou manuel.
- **Documents de procédure → agenda → brouillons** depuis la page du dossier, sans envoi.
- **Dossiers de travail** (NOS PIECES, PIECES ADVERSES - {{partie_adverse01}}…) réglables dans Mon style, complétés avec les parties
  adverses de chaque dossier.
- **Résumé oral du briefing** à lire ou à écouter (synthèse vocale du cabinet).
- **Messagerie** : signature, police, copie cachée automatique, règles de classement vers un sous-dossier.
- **Guide d’installation** : étapes réelles pour les agendas, Invoice Ninja et Twilio (champs et vérification), sources et veille.
- **Modèles Word** : aide aux balises et modèle d’exemple fictif à télécharger.
- **Écrans métier en anglais, allemand et espagnol** (dictionnaire de 2 799 phrases relevées sur les écrans réels ; les données ne sont
  jamais traduites).
- **README** en anglais, allemand et espagnol.

## 5.6.31 — Guide d’installation pas à pas, interface en quatre langues, menu « Toutes les fonctions », guide d’utilisation

Cahier des charges additionnel (guide d’installation et veille), premier lot livré : fiches R00, R01, R02, R17, R18 et R19.
Matrice de conformité exigence par exigence : `CONFORMITE-5.6.31.md`. Tests : `tests/test_v5631.py`.

- **Guide d’installation** (Paramètres › Guide d’installation, et au premier démarrage) : on choisit d’abord les fonctions utilisées
  (documents, messagerie, agenda, recherches, veille, facturation, téléphonie) ; seules leurs étapes s’affichent, une question et trois à
  cinq champs par écran, « Où trouver cette information », ce que l’étape permet, « Plus de détails » pour les réglages techniques,
  progression « Étape 3 sur 7 ». Retour, Continuer, Passer, Enregistrer et reprendre plus tard, Abandonner.
- **Sessions et preuves** : une session de guide par utilisateur (révision contrôlée, reprise après fermeture) ; les secrets saisis
  sont chiffrés aussitôt dans un coffre propre à la session et ne sont jamais renvoyés ; les tests portent sur la configuration
  candidate, sans rien enregistrer pour essayer ; chaque preuve vise une connexion et une capacité (lecture, dossiers, écriture
  de test, service, modèle, génération, identification SMTP) : modifier le SMTP ne périme pas la preuve IMAP, changer le mot de passe
  IMAP la périme. Publication avec contrôle de révision : un réglage modifié entre-temps par un autre écran donne un conflit ciblé.
- **Fonctions non choisies non bloquantes** (R00) : la messagerie peut être désactivée (« Messagerie utilisée par AxiorHub ») ;
  aucune connexion IMAP n’est alors ouverte et la configuration reste valide. Fin d’installation complète ou partielle reconnue par
  le premier démarrage ; « Découvrir puis configurer plus tard » au premier lancement.
- **Langues** : français, English, Deutsch, Español. Choix dans la barre du haut, enregistré par utilisateur ; langue résolue à
  chaque requête (deux avocats peuvent travailler dans deux langues en même temps). Catalogues complets et contrôlés (mêmes
  variables, pluriels) pour le menu, la barre du haut, le guide d’installation et le guide d’utilisation ; les autres écrans restent
  en français dans cette version. La langue de l’interface est distincte de la langue de rédaction et du droit applicable.
- **Menu « Toutes les fonctions »** : remplace « Rubriques » et « Outils » ; 63 fonctions en six groupes d’usage, recherche par nom,
  besoin ou ancien nom, « Configurer » vers l’étape du guide quand un prérequis manque, Échap ferme le panneau. Les adresses
  existantes sont conservées.
- **Guide d’utilisation** : une fiche par fonction en quatre langues (objectif, quand l’utiliser, prérequis, étapes avec les noms
  exacts des boutons, résultat, vérification, erreurs courantes, exemple fictif), parcours par besoin, « Aide sur cette page » sur
  chaque écran. Un test vérifie que chaque bouton cité existe réellement sur l’écran.
- **Correctif de recette** : un test d’agenda échouait le dimanche (événement « demain » hors de la semaine affichée) ; le logiciel
  n’était pas en cause.

## 5.6.30 — Corrections : Paramètres sans rechargement, messagerie STARTTLS, poste Windows

Tests : `tests/test_v5630.py`.

- **Paramètres › Connexions** : après un premier enregistrement, les suivants ne faisaient plus rien (erreur de script sur un réglage
  absent de la page) ; il fallait recharger la page. Corrigé : on peut modifier, enregistrer, tester, puis corriger et enregistrer
  de nouveau sans recharger. Message « Enregistrement… » pendant l’envoi ; « Test non validé » n’est plus répété.
- **Messagerie** : nouveau réglage « Chiffrement IMAP » (automatique d’après le port, TLS direct ou STARTTLS) ; le port 143 se
  connecte en STARTTLS. Chaque échec de test indique sa cause : serveur introuvable, port fermé, port sans TLS direct, délai
  dépassé, certificat refusé, identifiant ou mot de passe refusé. Aide aux réglages dans la rubrique Courriels.
- **Nextcloud** : un dossier de travail local choisi au niveau de la racine elle-même (…\Nextcloud\CABINET pour la racine
  /CABINET) est accepté ; message 404 explicite selon le cas (dossier local ou WebDAV).
- **Poste Windows** :
  - aperçu des modèles Word (« previsualisation_libreoffice_poppler_requise ») : Poppler n’est plus nécessaire, les pages sont
    rendues par PDFium (inclus) ; LibreOffice est lancé par `soffice.com` et la conversion est relancée après l’initialisation d’un
    profil neuf (premier lancement sans fichier produit) — même correction pour la lecture des DOCX et le bordereau de pièces ;
  - la fenêtre AxiorHub a son propre bouton et son icône dans la barre des tâches (identifiant d’application, icône et commande
    de relance), au lieu de l’icône d’Edge ; épingler la fenêtre épingle AxiorHub ;
  - fermer la fenêtre arrête de nouveau les services (Edge confie la fenêtre à un processus enfant : la fermeture est suivie sur
    la fenêtre elle-même) ;
  - fichier de configuration toujours écrit en UTF-8 ;
  - le PDF produit par LibreOffice (aperçu Word, lecture DOCX, bordereau) est relu après une courte attente s’il est encore
    verrouillé, au lieu d’échouer.
- **Audience** : le dispositif « PAR CES MOTIFS » est retrouvé même collé au texte qui précède (extraction sans Poppler).
- **Courriels** : plus de style en ligne bloqué par la politique de sécurité du navigateur.

## 5.6.29 — Maîtrise du travail automatique : pause par dossier, consignes, fin des boucles

Tests : `tests/test_v5629.py`.

- **Mettre l’IA en pause sur un dossier** (page Dossier, et bouton ⏸ sur un travail de « Ce que fait l’agent ») : plus aucun travail
  automatique pour ce dossier (diligences des courriels entrants, orchestrateur, surveillance des fichiers, avis de procédure,
  mémoire, projets et créations automatiques de fichiers, brouillons de réponse aux courriels entrants). Les travaux automatiques
  déjà en file sont annulés, celui en cours reçoit une demande d’arrêt ; vos propres demandes restent possibles. Bandeau sur la page
  du dossier, badge « ⏸ IA en pause » dans la liste des dossiers, « ▶ Reprendre l’IA » pour lever la pause.
- **Consignes pour l’agent** (page Dossier) : consignes permanentes transmises comme prioritaires à chaque travail sur ce dossier
  (projets documentaires, réponses aux courriels, documents demandés, fiche de travail).
- **Coupe-circuit** : un travail automatique qui échoue trois fois de suite sur un dossier n’est plus relancé seul ; la page du
  dossier le signale (« Relances suspendues ») avec « Réautoriser ». Les relances à la main restent possibles.
- **Boucles corrigées** :
  - « Vérification des réponses aux engagements » et l’analyse des envois échouaient toutes les cinq minutes sur un courriel trop
    volumineux (le passage s’arrêtait avant d’avancer) : le message est désormais lu par ses en-têtes et le passage continue ;
  - « Actualisation de la fiche vivante » était remise en file à chaque passage de la mémoire pour les dossiers en échec : arrêt
    après trois échecs, et limite de génération relevée pour cette fiche ;
  - « Création confirmée des fichiers documentaires » en double (« Ce nom de fichier existe déjà ») : une création automatique
    en double est abandonnée proprement, sans erreur répétée.

## 5.6.28 — Messagerie complète dans Courriels, vue Tâches simplifiée

Tests : `tests/test_v5628.py`.

- **Courriels › Boîte de réception — fonctions de messagerie** : barre d’actions sur chaque courriel (Répondre, Répondre à tous,
  Transférer, Archiver, Déplacer vers un dossier, Indésirable, Supprimer, Non lu, Suivi) et bouton « Nouveau message ».
  Rédaction : À, Cc, Cci, objet, texte mis en forme (gras, italique, souligné, barré, listes, citation, lien), citation du message
  d’origine, pièces jointes du message transféré au choix et fichiers ajoutés (15 Mo au total), fil conservé (In-Reply-To).
  Réponse à tous sans l’adresse du cabinet.
- **Garde-fous** : « Supprimer » déplace dans la corbeille (récupérable), jamais de suppression définitive ; « Archiver » crée le
  dossier Archives s’il manque ; seuls les dossiers existants de la messagerie sont acceptés ; texte mis en forme nettoyé côté
  serveur (liste fermée de balises, liens http, https, mailto). Le message est d’abord enregistré dans Brouillons ; l’envoi passe
  par le circuit existant (interrupteur général, aperçu puis confirmation, plafond horaire, copie dans Envoyés), avec la version
  mise en forme ; le rattachement à un dossier n’est pas exigé pour un message rédigé par l’avocat dans la messagerie. Si l’envoi
  est désactivé, le message reste dans Brouillons et un lien mène au réglage.
- **Répondre avec l’IA** (ex-« Demander une réponse à l’agent ») : bouton aussi dans la barre d’actions. Une demande de l’avocat
  passe outre les filtres automatiques (déjà lu, liste de diffusion, message automatique, règles locales…), seul un message
  supprimé reste refusé ; pour un message envoyé par le cabinet, la réponse est adressée à ses destinataires extérieurs.
  Correctif : « Relancer avec mon instruction » produit un nouveau brouillon au lieu d’être refusé après le premier.
- **Agenda et tâches › Tâches** : liste compacte groupée par échéance (en retard, aujourd’hui, cette semaine, plus tard, sans
  échéance), case « terminée » sur chaque tâche, dossier et échéance en étiquettes, actions regroupées (confier à l’IA, terminer,
  supprimer, modifier, programmer dans l’agenda) ; filtres par période et par dossier ; « Ajouter une tâche » à côté de la liste.

## 5.6.27 — Page Dossier refondue

Tests : `tests/test_v5627.py`.

- **Une seule page Dossier** (`/dossier?id=…`), ouverte depuis la liste des dossiers : lien « ← Tous les dossiers », titre,
  « Mettre à jour la fiche », menu « Plus » vers les vues détaillées ; onglets Fiche, Pièces, Courriels, Travaux de l’agent.
  Les pages Fiche par rubriques et Chronologie ont un lien « ← Retour au dossier ».
- **Fiche de travail préparée par l’agent** (modèle « Analyse juridique et stratégie ») : résumé en quelques phrases, rappel des
  faits rédigé comme un récit (pas une liste de pièces), parties au litige seulement (qualité, conseil, client du cabinet —
  magistrats, commissaires de justice, experts, personnes citées et jurisprudence écartés, doublons fusionnés), procédure
  (juridiction, RG, stade, prochaine étape), chronologie des 5 à 15 événements déterminants, enjeux et points à vérifier.
  Les actes (assignation, conclusions, décisions, contrats) sont lus en premier ; factures et frais en dernier ; une décision
  d’une autre affaire est signalée comme « jurisprudence citée ». Chaque paragraphe renvoie à ses pièces ; une référence
  inconnue est retirée. Fiche versionnée, une seule préparation en file par dossier, échec affiché en clair.
- **Corrections de l’avocat** : résumé, rappel des faits et parties se corrigent sur la page (crayon) ; affichées aussitôt et
  transmises à l’agent comme prioritaires à la mise à jour suivante.
- **Parties (repérage automatique)** : un nom n’est proposé que si la pièce le désigne comme partie (« à la requête de »,
  « contre : », demandeur, défendeur, appelant, intimé, « demande », « sollicite »…) ; jamais un avocat, un commissaire de justice,
  un magistrat, un greffier ou un expert ; mots parasites retirés (« Pièce », « Date », « Toque »…). Les anciennes propositions
  non validées sont masquées ; les vues Synthèse et Parties reprennent les parties de la fiche de travail quand elle existe.
- **Travailler avec l’agent dans le dossier** : demande libre ou raccourcis (courrier au client, note de synthèse, projet de
  conclusions, prochaine échéance), type de demande au choix ; les réponses et documents produits s’affichent sur la page.

## 5.6.26 — Coffre sous Windows, messagerie en trois colonnes, réponse sans dossier

Tests : `tests/test_v5625.py` (classes InboxFolders et VaultOnWindows).

- **Poste Windows** : l’enregistrement des paramètres ne bute plus sur « clé coffre permissions incorrectes ». Windows ne
  reflète pas ses droits d’accès dans les bits POSIX ; la protection vient du profil de l’utilisateur. Sous Linux, contrôle
  inchangé (clé et secrets en 0600 / 0640).
- **Courriels › Boîte de réception** : vue de messagerie en trois colonnes placée directement sous les onglets (dossiers,
  liste, lecture) ; le bloc « À relire » n’apparaît plus en dessous. Tous les dossiers de la messagerie sont accessibles :
  Boîte de réception, Brouillons, Envoyés, Boîte d’envoi, Archives, Indésirables, Corbeille et sous-dossiers, avec leurs
  libellés en français. Seuls les dossiers existants de la messagerie sont acceptés.
- **Réponse sans dossier** : « Sans dossier » prépare la réponse à partir du courriel, de ses pièces jointes et de
  l’instruction, sans source de dossier ; elle suit le même contrôle et le même dépôt vérifié dans Brouillons.
- **Créer un nouveau dossier depuis un courriel** : client, affaire, référence AAAAMMJJnnn proposée et emplacement à côté des
  dossiers existants ; le répertoire « Client - Affaire - Référence » est créé puis enregistré, l’expéditeur rattaché comme
  client, et la réponse peut être demandée aussitôt dans ce dossier.

## 5.6.25 — Poste de travail Windows 11, boîte de réception dans Courriels, ajout d’événements à l’agenda

Tests : `tests/test_v5625.py`. Guide : `docs/INSTALLATION-WINDOWS.md`.

- **Poste Windows 11** : application de bureau autonome, sur le modèle du poste Ubuntu. Installateur
  `AxiorHub-Pilote-Setup-<version>.exe` pour l’utilisateur courant (sans droits d’administrateur), Python inclus, raccourcis
  menu Démarrer, Bureau et ouverture de session au choix, désinstallation depuis Paramètres Windows (données conservées sauf
  demande). Fenêtre Microsoft Edge en mode application, ouverte par un jeton local à usage unique (deux minutes) échangé contre
  un cookie de session strict ; interface limitée à `127.0.0.1`. Services surveillés et arrêtés avec leur arbre de processus.
- **Couche portable** (`agent/portable.py`) : verrous de fichiers Windows, droits et propriétaires sans effet sous Windows,
  recherche des outils installés (Tesseract, LibreOffice, Poppler, eSpeak NG), lecture des PDF par `pypdf` quand Poppler manque,
  processus sans fenêtre de console. Sous Linux, comportement et chemins inchangés.
- **Base de travail** : initialisation du schéma sérialisée entre processus (plus d’erreur « duplicate column » au premier
  lancement d’un profil neuf).
- **Courriels › Boîte de réception** : tous les courriels de la boîte, du plus récent au plus ancien, recherche, état connu de
  l’agent, lecture du texte et des pièces jointes. « Demander une réponse à l’agent » pour un courriel choisi, même ignoré ou
  jamais traité : dossier choisi, instruction facultative, texte des pièces jointes ajouté aux sources ; la réponse suit le
  chemin habituel (contrôle, dépôt vérifié dans Brouillons, rien n’est envoyé). Réponses préparées : modifier, supprimer
  (corbeille de la messagerie), relancer avec une instruction, aussi depuis l’atelier « À relire ».
- **Agenda et tâches › Ajouter un événement** : intitulé, jour, heures ou journée entière, lieu, dossier, note ; dépôt dans un
  ou plusieurs agendas configurés (Nextcloud, CalDAV, Google Agenda) avec relecture de chacun et compte rendu agenda par agenda.
  Pour Google, titre neutre sauf si l’agenda autorise la transmission des détails. Ouvert aux rôles avocat, administrateur et
  assistant(e).
- **Intégration continue** : tâche Windows (tests du poste et construction de l’installateur).

## 5.6.24 — Corrections de l’audit des 5.6.22 et 5.6.23, dossier en onglets, icônes d’Aujourd’hui

Matrice détaillée constat par constat : `CONFORMITE-5.6.24.md` ; tests de reproduction : `tests/test_v5624.py`.

- **Dossier** : la fiche s’organise en douze onglets (Synthèse, Parties, Faits et procédure, Arguments des parties, Chronologie,
  Juridiction et échéances, Pièces, Textes et jurisprudence, Écritures et projets, Simulation, Décisions, Actions), chacun avec ses
  sources et sa fraîcheur. Bouton unique « Analyser le dossier » (indexation, faits, chronologie, synthèse) suivi étape par étape,
  reprise des seules étapes en erreur (F25, F34). La recherche des faits couvre toutes les pièces et projette la chronologie dans le
  même travail (F26, F32) ; correction structurée d’un fait (texte, intitulé, date confirmée) avec révision attendue (F27) ; un fait
  refusé n’apparaît plus « à valider » (F28) ; deux courriels distincts restent deux événements, les dates inconnues sont à part (F29) ;
  la synthèse charge les faits validés et refusés de la fiche (F31) ; une preuve modifiée suspend la validation (F33).
- **Missions** : mandat versionné (pause, annulation, révision) contrôlé avant chaque appel, dépôt et publication (F01) ; baux expirés
  repris ou soumis à décision, reprise après redémarrage par rapprochement (F02) ; validation sur le fichier relu, immuable (F03) ;
  empreinte canonique de la demande (F04) ; textes intégraux des pièces transmis à l’analyse (F05) ; périmètre de lecture et sources
  modifiées soumis à décision (F06) ; contrat commun de source juridique (F07) ; sources inventées refusées, relation montant/acteur
  démontrée (F08) ; lots enregistrés et consolidation récursive sans perte (F09).
- **Dossier local** : publication exclusive (F10), versions uniques et rétention réglable (F11), liens symboliques refusés à chaque
  composant (F12), ETag fondé sur le contenu (F13).
- **Invoice Ninja** : réservation de chaque temps avant tout envoi, plus de tâche pour un temps facturé (F14) ; relecture exigeante
  (montant TTC, libellés, statut, échéance, conditions) (F15) ; validité du devis appliquée et identité de devis par demande (F16) ;
  journal des opérations réclamé en transaction exclusive (F17).
- **Droits** : le rôle avocat accède aux missions, décisions, profils et facturation (F18).
- **Poste de bureau** : premier lancement guidé dans Paramètres (F19) ; installation par capacités, dossier local suffisant, messagerie
  facultative (F20) ; services surveillés et relancés de façon bornée, verrou d’instance, identifiants WebKit limités à l’origine
  locale, liens externes dans le navigateur (F24).
- **Courriels et Word** : brouillon sans drapeau Draft ou sans corps non conforme (F21) ; images, champs et objets protégés lors d’une
  révision Word (F22). **MCP** : réponses corrélées par identifiant, flux SSE complets, pagination contrôlée (F23).
- **Navigation** : plus de « job=0 » après une action immédiate (F35), script des missions chargé une fois (F36), formulaires GET non
  détournés (F37).
- **Interface** : bouton « 📁 Choisir… » pour le dossier de travail local dans Paramètres ; icônes dans Aujourd’hui (ignorer ou
  supprimer un document produit par l’agent, masquer un blocage, arrêter un travail, ajouter/modifier/supprimer un événement ou une
  tâche) ; jours de l’agenda en français.

## 5.6.23 — Guide « poste Ubuntu en dix minutes »

- **`docs/GUIDE-POSTE-10-MINUTES.md`** : installation de l’application de bureau sur Ubuntu 24.04, d’Ollama au premier briefing,
  avec cinq captures de l’interface (Aujourd’hui, Pilote, Paramètres, Documents, Dossiers) prises sur l’aperçu de démonstration
  (données fictives), le fonctionnement au quotidien (services permanents, journaux, mise à jour, désinstallation) et un tableau de
  dépannage. Lien depuis le README.

## 5.6.22 — Dossier de travail local

- **Dossier de travail local** (poste et serveur) : dans Paramètres › Connexions › Nextcloud, « Dossier de travail local » désigne un
  répertoire existant (dossier synchronisé par le client Nextcloud de bureau, partage monté). Les racines (`/Dossiers`) et tous les
  chemins du cabinet y sont alors lus et écrits directement, avec la même interface que WebDAV : inventaires bornés, lecture avec
  contrôle de version (ETag calculé sur date et taille), dépôt exclusif (jamais de remplacement silencieux), remplacement contrôlé
  conservant la version précédente dans `.axiorhub-versions`, création de sous-dossiers dans le périmètre du dossier seulement.
  Fichiers cachés, répertoires « secrets » et liens symboliques sont ignorés comme avec Nextcloud ; aucune sortie du dossier n’est possible.
- **Agendas** : toujours lus par CalDAV ; avec un dossier local, l’adresse, l’identifiant et le mot de passe Nextcloud restent utiles
  pour les agendas, sinon ils sont signalés indisponibles (jamais simulés).
- Réglage validé (chemin absolu, répertoire existant, jamais la racine du système ; vide = WebDAV).

## 5.6.21 — Réseau du cabinet et Paramètres regroupés

- **Ollama, Kokoro et passerelle vocale sur le réseau du cabinet** : les adresses privées (10.x, 172.16–31.x, 192.168.x, fc00::/7,
  noms en `.local`) sont admises pour ces services, par exemple `http://192.168.1.115:11434` ; les adresses publiques restent refusées
  (le texte des dossiers ne sort pas du cabinet sans décision explicite). Libellés mis à jour dans Connexions.
- **Paramètres regroupés** : une seule page à six rubriques — Connexions ; Intelligence artificielle ; Voix ; Routines et
  initiatives ; Documents et procédure ; Cabinet et accueil — chaque réglage à un seul endroit. Les formulaires existants sont
  réutilisés tels quels ; les doublons sont retirés (la voix n’est réglée que dans « Voix », le rôle par dossier que dans « Documents
  et procédure », Ollama que dans « Connexions »). Les anciennes adresses (`/parametres/connexions`, `/parametres/assistant`,
  `/parametres/proactivite`, `/ia-externe`, `/confort`, `/atelier/reglages`, `/parametres/agendas`, `/parametres?tab=…`) ouvrent la
  rubrique correspondante ; le menu Outils ne les liste plus séparément.

## 5.6.20 — Mode poste : application de bureau Ubuntu 24.04

- **`poste.py`** : AxiorHub sur un poste de travail, sans root ni Apache. Données, configuration et secrets dans
  `~/.local/share/axiorhub-pilote`, journaux dans `~/.local/state/axiorhub-pilote`. Premier lancement : configuration sans secret
  (même modèle que le conteneur Docker, Ollama et Kokoro sur 127.0.0.1) et compte local « admin » avec mot de passe aléatoire en
  0600 ; l’assistant d’installation de l’interface prend ensuite le relais. Les services sont ceux du serveur, lancés comme
  sous-processus : interface (127.0.0.1 seulement, port réglable), worker, veille IMAP, passage périodique de l’agent.
- **Fenêtre d’application** WebKitGTK (python3-gi, Gtk 3, WebKit2 4.1) avec authentification locale automatique ; à défaut,
  ouverture du navigateur. `--no-window` pour l’unité systemd utilisateur, `--stop`, `--status`, `--print-password`.
- **Paquet Debian « poste »** : `scripts/build-deb.py … --variant poste` produit `axiorhub-pilote-poste_<version>_amd64.deb` :
  arbre complet sous `/usr/lib/axiorhub-pilote/current`, lanceur `/usr/bin/axiorhub-pilote`, entrée de menu, icône, unité
  utilisateur `axiorhub-pilote.service` ; dépendances apt (python3-gi, WebKitGTK, waitress, cryptography, LibreOffice Writer,
  Tesseract, Poppler, eSpeak NG). Pas de script de maintenance : rien n’est écrit hors du paquet, les données restent dans le
  dossier personnel.
- **Interface** : `web.py serve --port` ; `agent.web.serve(config, auth, port, host)`.

## 5.6.19 — Pilote simplifié

- **Un bouton « Envoyer l’instruction »**, placé sous le résultat attendu, au libellé stable (plus de « Démarrer la mission » /
  « Confier une autre mission » / « Démarrer une nouvelle mission » selon l’état). Ctrl+Entrée envoie aussi.
- **Commandes de contexte retirées** (Préparer audience, Répondre aux conclusions, Assignation, Comparer, Réviser, Devis) :
  l’instruction se dit ou s’écrit ; le résultat attendu reste sélectionnable.
- **Une seule dictée** : le bouton « Commencer la dictée » sous l’instruction (mode rapide ou local au choix). Le bouton « Dicter »
  du panneau, qui faisait double emploi, est retiré ; « Dialoguer » (conversation vocale par tours) reste.
- **« Effacer la conversation »** : vide l’affichage du fil ; les missions restent consultables dans Outils › Missions.
- **Fenêtre flottante** : sur « Aujourd’hui », le Pilote n’est plus monté dans la page ; le bouton robot ouvre la fenêtre, large par
  défaut (le bouton ⤢ la réduit, choix mémorisé par navigateur), comme sur les autres pages.
- **« Écouter le briefing »** lit le briefing du matin produit par la routine (titres, puces et heures en clair) au lieu de
  l’instantané « un événement à 9 heures ». Le mode discret (noms masqués) n’est plus activé par défaut : il reste activable dans
  Paramètres › Assistant.

## 5.6.18 — Paquet Debian

- **Paquet `axiorhub-pilote_5.6.18_all.deb`** : construit en local par `scripts/build-deb.py` à partir de l’archive de livraison
  contrôlée, sans dpkg ni Linux (format ar et tar écrits en Python). Installation ou mise à niveau par
  `sudo apt install ./axiorhub-pilote_5.6.18_all.deb` : apt apporte les dépendances système (python3, python3-cryptography,
  poppler, tesseract-ocr-fra, LibreOffice Writer, minisign, eSpeak NG ; Apache recommandé).
- **Même logique qu’install.sh** : le script de post-installation reprend `installer.provision_files` (installation neuve) ou
  `upgrade.upgrade` (mise à niveau cumulative avec sauvegardes et reçu) sur la charge utile `/usr/share/axiorhub-pilote/…tar.gz` ;
  les versions précédentes restent sous `/opt/axiorhub-mail-agent/releases`, le retour arrière (`upgrade.py --rollback`) et le
  nettoyage (`install.sh --prune-old-releases`) sont inchangés. Aucun secret créé ; `install-interface.py` reste l’étape suivante.
- **Retrait** : `apt remove` arrête et désactive les services ; versions, configuration, secrets et données sont conservés et le
  message indique comment les retirer volontairement.
- **Tests** : structure du paquet (membres ar, control, md5sums, droits, reproductibilité, charge utile altérée refusée) et
  installation à blanc sous une racine simulée (neuve puis mise à niveau, retrait) sans dpkg ni root.

## 5.6.17 — Kokoro, bouton robot et routines en tête d’« Aujourd’hui »

- **Kokoro : « audio synthèse invalide »** : Kokoro-FastAPI répond par défaut en flux, avec un en-tête WAV aux tailles fictives que
  la vérification refusait. La demande précise désormais un fichier complet (`stream: false`) et, par sécurité, un en-tête de flux
  est réparé d’après la longueur réelle avant vérification des échantillons.
- **Bouton robot** : sur « Aujourd’hui », où le Pilote est monté dans la page, le bouton flottant restait affiché sans effet (la règle
  globale `button{display}` rendait son masquage inopérant). Il mène désormais au Pilote (défilement et focus sur l’instruction) ;
  ailleurs il ouvre et replie le panneau comme avant.
- **Routines du cabinet en tête** : le cadre complet (briefing du matin, tri des courriels, bilan de la semaine, documents à préparer)
  et « Style du cabinet » sont placés juste sous la barre des routines, avant le Pilote, au lieu du bas de page.

## 5.6.16 — Correctif : installateur d’interface

- **install-interface.py** : son contrôle de version n’admettait que les versions précédentes, jamais la version active ; depuis
  plusieurs versions il s’arrêtait sur « Version active non prise en charge » sans rétablir le lien de configuration, ni mettre à
  jour les unités systemd et la configuration Apache. La version active (celle du script) est désormais toujours admise.
- **Réparation du lien sans l’installateur** : la fonction `agent.runtime_config567.enable` peut être appelée directement
  (commande donnée dans la note de mise à jour) ; elle est idempotente.

## 5.6.15 — Correctif : enregistrement des paramètres et pont de configuration

- **Enregistrement des paramètres** : sur l’installation systemd, lorsque le lien `/etc/axiorhub-mail-agent/config.json` vers la copie
  runtime avait été remplacé par un fichier ordinaire (après `axiorhub-mail mode …` ou `configure`), le service confiné en lecture
  seule sur `/etc` ne pouvait plus écrire et le navigateur affichait « Unexpected token '<' … is not valid JSON ». L’enregistrement
  renvoie désormais un refus lisible (« configuration non inscriptible ») avec la commande de réparation.
- **Pont de configuration préservé** : `axiorhub-mail mode` et toute écriture administrative passent à travers le lien (écriture dans la
  cible, propriétaire et droits conservés) au lieu de le remplacer.
- **Réparation automatique** : `install-interface.py` rétablit le lien quand une copie runtime existe déjà : la plus récente des deux
  configurations est conservée, l’autre est sauvegardée (`configuration567/config-remplace-*.json` et `backups/config-fichier-*.json`).
- **Erreurs internes lisibles** : une erreur interne sur une route `/api440/` renvoie du JSON (message en français) et inscrit la trace
  dans le journal du service (`journalctl -u axiorhub-mail-ui.service`) ; plus de page HTML dans une réponse attendue en JSON.
- **Même vérification sur les autres routes appelées par le navigateur** : un refus (dictée, pièces jointes de l’assistant, modèles
  Word, conclusions d’audience, extensions, flux en direct, API v1) est renvoyé en JSON avec son motif au lieu de la page
  « Action non effectuée » ; l’assistant d’installation (première mise en service) signale lui aussi « configuration non
  inscriptible » au lieu d’une erreur brute.

## 5.6.14 — Missions complexes et préparation juridique vérifiable

Missions et sous-tâches (M01–M09, C18–C20)

- **Mission principale et sous-tâches** : parcours « assignation » (T01–T16, sections explicites par dépendance) et « conclusions en
  réponse » (C01–C18) ; tâches identifiées avec parent, dépendances explicites, contrat de sortie (type de résultat) ; cycles rejetés ;
  bornes configurables (64 tâches, profondeur 4, 3 travaux simultanés, budget d'appels) ; entrées trop volumineuses traitées par lots puis
  consolidées, jamais perdues en silence.
- **Dépendances fondées sur un résultat accepté** : une abstention ou un blocage reste visible et bloque ; une branche facultative peut être
  omise avec motif ; le parent ne se termine pas sans livrable obligatoire. Tous les résultats parents sont transmis par références
  d'artefacts (type, version, hash), plus un seul texte tronqué.
- **Reprise sans double effet** : tâche persistée avant exécution, bail renouvelable, numéro de génération (une tentative ancienne ne
  publie pas) ; dépôt Word par journal d'opérations (préparé, en cours, incertain, confirmé, conflit) rapproché avant toute répétition.
- **Rôles et modèles** : superviseur, documentaliste, analystes des faits et de procédure, chercheur, rédacteur, contrôleur, gestion ;
  fonction de modèle réellement routée et tracée par tâche ; un rôle désactivé bloque avec alternative explicite.
- **Plan révisable** : « Compléter les instructions » ou « Reprends uniquement … » crée une révision du plan et ne relance que les
  tâches visées et leurs dépendantes ; décisions regroupées (champs manquants, procédure ambiguë, écritures à sélectionner, réserves,
  budget) ; pause et annulation propagées, fichiers conservés ; validation attachée au hash de la version.
- **Paquet final** : Word sans note interne, bordereau, inventaire, rapport des sources, fiche de contrôle séparée, décisions résiduelles.
- Migration des plans 5.6.8 en groupes plats (sens inchangé) ; une abstention ne débloque plus une étape suivante (C12).

Profils, sources et contrôle (M11–M15, A05–A07, C04–C06)

- **Registre daté des profils procéduraux** (JEX, référés TJ/TC/TAE, TJ au fond avec ou sans avocat, TC/TAE au fond) : conditions
  d'emploi, références à vérifier, mentions, formalités, modèle Word ; qualification sur juridiction, voie, représentation et montant ;
  ambiguïté = décision ; hors couverture = déclaré ; approbation explicite de l'avocat (Outils › Profils procéduraux).
- **Chaîne de preuve des sources** : instantané figé (identifiant stable, version distante, hash, pages lues, blanches, illisibles,
  exclusions motivées, homonymes distincts, total réel) ; delta des sources.
- **Contrôle par assertion** : montants, dates, pièces, demandes et références extraits et confrontés aux sources ; cohérence
  discussion/dispositif ; relecture indépendante par blocs sur le texte entier avec les sources utiles ; exécution (non exécuté, partiel,
  exécuté) distincte du résultat (réussi, réserves, bloqué, indisponible) ; badges « Contrôle juridique réussi / avec réserves /
  bloquant » ; correction ciblée bornée (deux passages) puis décision.

Corrections de fiabilité (C01–C03, C07–C11, C13–C17)

- Reprise IMAP sans duplication (rapprochement par identifiant de message avant tout nouvel APPEND) ; relecture complète du brouillon
  (sujet, expéditeur, destinataires, corps, drapeau) et état « modifié » si l'avocat l'a changé ; route mail_drafting ; aucune note
  technique dans le corps du courriel.
- Révision Word par blocs : paragraphes inchangés conservés avec leurs styles, tableaux et contrôles de contenu à leur place ; mode de
  réécriture complète explicite ; rapport de révision.
- Comparaison des versions normalisée (€, euros, décimales, espaces insécables, dates françaises et ISO) et ordonnée (ajout, retrait,
  modification, déplacement, doublon supprimé).
- Fiche d'audience : questions probables lues au schéma canonique (likely_questions), erreurs SQL ou Nextcloud visibles, liens encodés,
  « en réponse » n'est plus classé adverse.
- Modèles Word : affectation explicite, champs manquants marqués et listés, « cabinet » n'est plus un repli générique.
- Cache des analyses longues : digest des poids du modèle, routage global retiré de la signature.
- Recette de production : trois niveaux (connexion, transport, mission), vrai producteur, contrôle avec erreur injectée, interruption
  après écriture puis reprise sans second dépôt, modèle comparé ; prérequis non testé = pas de succès global.
- Invoice Ninja : rapprochement complet des factures (client exigé, lignes, taxes, totaux Decimal, remise, devise, échéance), temps
  fidèles (intervalle mesuré ou durée déclarée signalée, identifiant conservé si la relecture échoue), réservation transactionnelle de
  chaque temps.
- MCP : initialisation, notification, identifiant de session conservé, tools/list paginé, arguments construits d'après le schéma réel
  (champs imbriqués), diagnostic en trois niveaux (connexion, recherche, récupération du texte).

Invoice Ninja (N01–N09)

- Adaptateur unique (société, pagination complète, 401/403/422/429 traduits, Decimal, identifiants opaques, liste d'opérations permises :
  aucun envoi, paiement ni conversion) ; journal des opérations avec rapprochement après réponse incertaine ; correspondances locales ↔
  distantes par société.
- Clients et contacts (recherche avant création, homonyme = choix, contacts préservés), projet rattaché au dossier (un par dossier,
  reprise sans doublon), devis en brouillon avec aperçu (HT, taxes, TTC), synchronisation périodique des statuts et paiements (un temps
  facturé depuis Invoice Ninja n'est plus facturable), parcours Pilote « Prépare le devis… » avec données manquantes en une carte.

Interface (U01–U06)

- Routines en tête d'« Aujourd'hui » (dernier lancement, état, accès, lancement sans double clic, pause des automatismes).
- Centre « À décider » compact : résumé replié, panneau latéral, cartes avec contexte, question, recommandation, sources, conséquence et
  actions Valider, Modifier, Compléter les instructions, Reporter, Annuler ; focus et état préservés.
- Agenda en semaine par défaut, préférence explicite conservée. Commandes de contexte du Pilote (Préparer audience, Répondre, Comparer,
  Réviser, Assignation, Devis) ; intentions « mission » et « facturation » ; vue de mission complexe (arbre, filtres blocages / résultats).
- Mise en service : capture, transcription, synthèse et repli eSpeak distingués ; fiche de contrôle séparée du document.

Voir CONFORMITE-5.6.14.md pour la matrice exigence par exigence (livré, partiel, non livré).

## 5.6.13 — Résultat annoncé, rédaction libre convergente, texte et voix unifiés

Corrections prioritaires

- **Rédaction libre convergente** : une demande directe de document reprend le modèle Word approuvé du cabinet (en-têtes, pieds de
  page, styles ; choix par le libellé du modèle ou le réglage `docreq5613:templates`), une révision conserve le document d'origine
  au lieu d'un Word générique reconstruit, et chaque projet passe par le contrôle juridique (mentions obligatoires, citations, relecture
  par un second modèle distinct du rédacteur). Trois états distincts sont affichés : « Dépôt vérifié », « Contrôle juridique effectué »,
  « Validation de l'avocat » (bouton « Valider ce projet »).
- **Une question ne crée plus de document** : l'intention (Réponse / Analyse dans le fil / Word dans ce dossier / Brouillon dans
  Drafts) est annoncée avant de démarrer, modifiable, et n'est plus déduite d'un mot comme « contrat » ou « audience ». « Prépare un
  courriel au confrère » sans courriel sélectionné produit un brouillon dans Drafts (destinataire à renseigner), jamais un Word.
- **Texte et voix unifiés** : même dossier, mêmes pièces jointes (liste commune avec nom, extraction, pages lisibles, retrait), même
  courriel, même mission courante et même fil ; le rafraîchissement du résultat ne remplace plus l'historique des tours.
- **Replier le panneau arrête le dialogue vocal** (micro et lecture) ; indicateur permanent « Micro actif » avec arrêt immédiat.
- **Banc du petit modèle par fonction** : onze épreuves à critères fermés (JSON exact, date unique, tri publicité / procédure, dossier
  ambigu, pièce incomplète, montants contradictoires, instruction malveillante dans une pièce, abstention attendue) ; un modèle admis
  au tri ne reçoit pas la lecture des pièces ni le contrôle ; le résultat est lié à l'empreinte (digest) du modèle et devient périmé
  si les poids changent.
- **Cache des analyses longues** : la signature ne contient plus que les paramètres utilisés (modèle, réglages documents/Ollama/
  routage, consignes d'extension) ; un banc ou un quota enregistré n'invalide plus rien. « Analyse réutilisée » ou le motif précis
  du recalcul est affiché.
- **« À décider » complet** : missions manuelles bloquées (dossier à préciser, erreur), demandes de document sans dossier ou en
  échec (reprise avec le dossier choisi, même identifiant), courriels à rattacher, règles proposées ; chaque blocage a son action.

Interface

- Zone d'instruction unique : le panneau Pilote est monté dans « Aujourd'hui » (même composant, même historique, mêmes limites).
- Sélection du dossier par client, adversaire, référence et alias, nom complet, dossiers récents en premier.
- Mode élargi du panneau pour les analyses longues ; résultat produit avec emplacement, modèle utilisé, contrôles effectués, points à
  compléter ; activité de l'agent regroupée par dossier, tâches techniques dans les détails ; rubrique « Intelligence artificielle »
  réunissant modèles locaux, routage, régime économe, consommation et IA externe sûre.

Fonctionnalités

- Manifeste des sources (conclusions retenues et leur version, pièces, extraits lus, courriels, fichiers non lus) joint à chaque projet.
- Comparaison des versions lors d'une révision : paragraphes ajoutés et retirés, montants, dates, demandes et dispositif modifiés.
- Fiche d'audience par dossier (Outils › Fiche d'audience, `/audience`) : audiences à venir, dernières conclusions, pièces et tâches
  attendues, notes de plaidoirie, questions probables, projets à relire.
- Règle proposée après une correction (réversible : adoption pour le cabinet ou le dossier, ou ignorée).
- Recette de production réelle (Mise en service, `manage.py recette-production`) : Word lisible, respect du modèle, dépôt Nextcloud relu
  dans `_RECETTE_AXIORHUB`, reprise après interruption, brouillon IMAP retrouvé.

## 5.6.12 — Un seul panneau Pilote, banc du modèle économe, cache des fusions

- **Un seul panneau Pilote** : le bouton robot ouvre l'unique panneau ; « 🎙 Dicter » dicte l'instruction, « 💬 Dialoguer » lance la
  conversation vocale par tours dans ce même panneau, avec le même dossier, les mêmes documents et le même fil (chaque tour vocal
  rejoint l'historique des résultats). Plus de bouton micro flottant sur les pages qui ont le panneau.
- **Banc du modèle économe (audit F09)** : avant de recevoir le tri, la lecture des pièces jointes et la conversation vocale, le plus
  petit modèle local doit réussir trois épreuves déterministes (objet JSON exact, date au format demandé, tri d'un courriel
  publicitaire), sans donnée de dossier. Le contrôle ne lui est confié que s'il est distinct du modèle de rédaction. Les contrôles
  de la préparation d'audience et de l'analyse stratégique sont maintenus en régime économe.
- **Cache des fusions (audit F13)** : les synthèses intermédiaires des documents longs sont mises en cache par l'empreinte de leurs
  entrées, comme les fragments ; une relecture ne recalcule rien. Seuls les réglages qui changent le résultat (documents, modèles,
  routage) invalident le cache, plus toute la configuration.

## 5.6.11 — Courriels du cabinet et affaires anciennes

- **Un courriel envoyé par le cabinet n'est jamais une demande** : l'adresse d'expédition et l'identifiant de connexion comptent
  comme adresses du cabinet même s'ils ne figurent pas dans « Autres adresses du cabinet » (un courriel que l'avocat s'envoie à
  lui-même ne donne plus lieu à un projet de réponse).
- **Balayages automatiques limités aux courriels récents** : l'orchestrateur et l'autonomie n'examinaient pas seulement les nouveaux
  courriels mais, par lots, tout l'historique rattaché à un dossier, et proposaient des réponses, projets et actes pour des affaires
  traitées des semaines plus tôt. Désormais : fenêtre de 14 jours (réglable dans Paramètres › Connexions › Courriels) et jamais les
  courriels envoyés par le cabinet. Ce n'est pas le modèle (local ou externe) qui manquait de pertinence : la sélection des courriels
  à examiner ne tenait pas compte de leur âge.
- **Expéditeur inconnu sans dossier** (démarchage, lettre d'information, prospect) : plus de brouillon automatique ; le courriel est
  présenté « à qualifier ».
- **Audit 5.6.10, lot fiabilité** : facturation Invoice Ninja en centimes exacts (une ligne = montant convenu), réservation atomique
  (deux demandes simultanées ne créent plus deux factures), relecture rapprochée (identifiant, client, statut brouillon, lignes,
  montant) avec l'état « déposée, conformité non vérifiée » en cas d'écart ; index documentaire porté à 2 000 fragments avec couverture
  signalée et classement BM25 avant la limite ; interruption vocale qui rend bien la parole ; analyses différées par le quota conservées
  et reprises le lendemain ; diagnostic de mise en service à niveaux (configuré, accessible, authentifié, fonctionnel, non testé) et
  adapté à Docker ; icônes PWA aux vraies tailles (192, 512, maskable) ; annonce téléphonique lue entièrement avant toute saisie,
  touche 0 à chaque étape, conservation appliquée chaque jour jusqu'au titre des tâches ; bloc « À décider » sans rechargement ;
  message de version généré ; volet « document introuvable » explicite au lieu de « http 404 ».

## 5.6.10 — Accueil téléphonique : information de l'appelant

- **Annonce complète** lue avant toute reconnaissance vocale : accueil automatisé utilisant une intelligence artificielle, paroles
  transcrites par un prestataire (Twilio), aucun enregistrement audio conservé par le cabinet, aucun conseil juridique par téléphone,
  touche 0 pour refuser et utiliser le clavier, rappel aux horaires d'ouverture pour parler à une personne.
- **Texte modifiable** dans Paramètres › Connexions › Accueil administratif (« Annonce lue à l'appelant »), sous la responsabilité du
  cabinet en tant que responsable du traitement ; le texte par défaut s'applique tant que le champ est vide.
- **Touche 0** : bascule immédiate vers l'accueil par touches, sans reconnaissance vocale ; la suite de l'appel suit le flux classique.
- **Traçabilité** : la tâche créée mentionne « accueil automatisé avec IA ; transcription Twilio, non vérifiée ».

## 5.6.9 — Économe, réactif, décisions à portée de clic

- **Régime économe** (activé d'office, réglable dans « IA externe sûre ») : contrôles périodiques espacés, quota journalier des
  analyses automatiques (vos demandes ne sont jamais limitées), contrôle par un second modèle réservé aux textes destinés à des tiers,
  comptabilité des jetons locaux et externes par fonction, profil « plus petit modèle local » pour le tri et le contrôle.
- **« À décider » sur Aujourd'hui** : documents au dossier ambigu (avec les dossiers suggérés), dates à prouver, suites bloquées et
  engagements à préciser, résolus sur place. Sous-dossier de classement au choix (PROCEDURE, PIECES, CORRESPONDANCES, EXPERTISES, HONORAIRES).
- **Voix** : lecture phrase par phrase pendant que la suite se prépare, tours plus courts ; conversation par tours, pas de duplex intégral.
- **Accueil téléphonique conversationnel administratif** (Twilio, activation et consentement explicites) : motifs fermés, deux questions
  au plus, tâche enregistrée ; aucun conseil juridique.
- **Invoice Ninja en écriture** après validation : facture en brouillon à partir des temps validés, temps transmis comme tâches ;
  rien n'est envoyé au client.
- **Mise en service** : écran et commande `manage.py readiness` (services réels, dossier Envoyés, transcription, synthèse, intégrations),
  test du micro et du HTTPS dans le navigateur, repères Docker.
- Corrections 5.6.8 : icône /favicon.ico, menu replié par défaut sur téléphone, requête de profil en double, relectures pilotées par
  le flux en direct, lectures de fichiers en UTF-8 (relance automatique), scripts d'installation importables hors Linux.

Détail : [CHANGELOG-5.6.9.md](CHANGELOG-5.6.9.md).

## 5.6.7 — Missions, voix locale et exploitation contrôlée

- Missions persistantes depuis le robot transversal : contexte de dossier et documents,
  demande unique, plan, résultat, suspension et reprise. Préparation interne ou suggestion.
- Dictée par le pont local, lecture des réponses et du briefing par eSpeak-NG,
  préférences personnelles explicites de ton, longueur et initiative.
- Connexions IMAP/Nextcloud/Invoice Ninja/SMTP/Ollama modifiables avec validation,
  révision et coffre chiffré ; inventaire Docker sous forme de plan non exécuté.
- Même politique de confidentialité avant les secours externes, budgets réservés
  atomiquement, permissions fermées par défaut, bootstrap administrateur protégé,
  API Bearer et callbacks OnlyOffice préservés.
- Relecture SHA-256 et reprise du dépôt Word à destination stable, verrous par dossier
  sur les producteurs ciblés, récupération bornée des jobs après redémarrage.
- Sources longues sélectionnées transmises à l’analyse progressive, refus des pages
  non extraites, cache tenant compte des réglages ; révision Word par fragments complets.
- Scores d’évaluation manquants affichés non évalués ; corrections avant/après
  conservées jusqu’à la limite explicite de 120 000 caractères.
- Factures actives et ouvertes filtrées, pagination Invoice Ninja contrôlée ; connecteur
  toujours en lecture seule. Sondes d’interface et de workers distinguées.
- Accueil Twilio par touches et réception officielle WhatsApp Business entrants,
  signés et dédupliqués, créant uniquement des tâches administratives. Désactivés par défaut.
- Archive sur liste publique de fichiers, sauvegarde VPS avec producteurs arrêtés
  et vérification des SQLite extraites ; diagnostic/import Open WebUI remis à jour.

Les limites et la recette sur services réels sont explicitées dans `RECETTE-5.6.7.md`.
L’image Docker, la qualité juridique et les comptes téléphoniques réels ne sont pas
certifiés par les tests locaux.

## 5.6.6 — État du système fiable

- **Page rechargée toutes les deux secondes** : un bouton déjà utilisé une fois (par ex. « Contrôler maintenant ») retrouvait son
  ancienne demande à chaque mise à jour du flux d'activité et rechargeait la page sans fin. Seul l'envoi que l'avocat vient de faire
  recharge désormais la page, une fois.
- **Invoice Ninja désactivé** : son jeton absent n'est plus signalé.
- **Ollama lancé hors de `ollama.service`** (Docker, autre unité) : plus d'alerte « inactive » si l'API Ollama répond ; un Ollama
  réellement arrêté reste signalé.
- **Fichiers Nextcloud** : un brouillon dont le dossier a été supprimé ou déplacé est « absent » (carte à vérifier), et non plus
  « dossier illisible » avec une « Erreur Nextcloud » ; chaque fichier n'apparaît qu'une fois dans le détail.

## 5.6.5 — File fluide et relecture plus claire

- **La file ne bloque plus vos demandes** : les travaux automatiques (surveillance, indexation, recherche de faits…) ont leur propre
  plafond (60 en attente, 8 par type) ; vos demandes entrent toujours. Avant, 100 travaux en attente, quels qu'ils soient, suffisaient à
  refuser toute nouvelle demande (« file attente pleine »).
- **Plus d'inondation par les anciens dossiers** : un dossier lu pour la première fois ou modifié n'est analysé que si un fichier a été
  modifié depuis moins de 30 jours (date inconnue : analysé, par prudence). Une file pleine ne fait plus échouer la surveillance des
  documents (analyse simplement reportée). Les suites d'un travail terminé (lot suivant d'indexation…) et « Indexer tous les dossiers »
  ne sont pas soumises au plafond automatique.
- **Rôles des correspondants** : juridiction / greffe, expert, administration, commissaire de justice, autre partie (assureur, caution,
  mandataire…), contact personnel (hors dossier). Rien du dossier n'est communiqué à ces destinataires sans validation de l'avocat ; aucun
  brouillon n'est préparé pour un contact personnel.
- **Relecture d'un brouillon** : le courriel d'origine (expéditeur, date, extrait) est affiché, avec des liens vers l'original et vers le
  brouillon dans la messagerie, et « Ouvrir dans Courriels à relire » ouvre directement ce brouillon.
- **Document préparé** : emplacement exact (dossier › sous-dossier › fichier) dans le volet et dans le fil ; bouton « Modifier le
  document » (Nextcloud / OnlyOffice) en premier, éditeur AxiorHub en second.

## 5.6.4 — Secours externe

- **Modèle local trop lent** : pour les rédactions et les demandes de l'avocat (assistant, brouillons, documents, analyse, audiences,
  Roundcube), une génération locale qui dépasse le délai (3 minutes par défaut, réglable de 1 à 15 minutes) est abandonnée et confiée au
  premier fournisseur externe prêt, dans l'ordre Mistral, Claude, ChatGPT, OpenRouter ; un travail qui a attendu son tour plus longtemps
  que ce délai part directement chez le fournisseur externe. Le tri des courriels, la lecture des pièces jointes et le contrôle
  indépendant restent locaux.
- Seuls les fournisseurs activés **et autorisés** sont utilisés ; tout envoi est **pseudonymisé** sur le serveur ; si aucun fournisseur
  ne répond, le modèle local termine le travail.
- **Dépôt GitHub complété** : la règle `data/` du `.gitignore` excluait aussi `agent/data` (règles de délais de procédure et de
  prescription) et `tests/data` ; un clone du dépôt 5.6.1 à 5.6.3 n'avait ni échéances ni prescriptions (l'archive livrée n'était pas
  concernée). Règles ancrées à la racine, fichiers ajoutés, contrôle des données personnelles étendu à ces dossiers ; un test empêche
  le retour de l'erreur.
- Réglage dans *IA externe sûre → Secours externe* (désactivé par défaut) ; chaque bascule est visible dans le détail du travail et dans
  le journal des bascules.

## 5.6.3 — Authentification, permissions et flux d'activité

Défauts corrigés, chacun couvert par un test qui échoue sur la 5.6.2 (échanges HTTP réels, serveur lancé dans le test) :

- **Flux d'activité (SSE)** : le mandataire du mode autonome attendait la fin de chaque réponse (événements livrés en bloc toutes les
  25 s) ; Waitress retenait les petits envois (`send_bytes=1`) ; une coupure avant le premier envoi ne rendait jamais la place de flux
  (« Flux actifs trop nombreux » après quelques coupures) ; un `Last-Event-ID` non numérique provoquait une erreur 500 ; le flux de
  l'administrateur était redirigé vers l'assistant d'installation ; Apache (installation système et VPS) ne transmet plus le flux par
  paquets ni compressé.
- **Authentification (mode autonome)** : sessions enregistrées sur le serveur (déconnexion et révocation réelles) ; changement ou
  réinitialisation du mot de passe et désactivation d'un compte déconnectent ses autres sessions ; 5 échecs par adresse (20 par poste)
  en 15 minutes ; 3 liens « mot de passe oublié » par heure ; lien refusé pour un compte désactivé, tous les liens en cours annulés
  après usage ; temps de réponse identique pour une adresse inconnue ; en-têtes `Referrer-Policy` et `X-Frame-Options`.
- **Permissions** : réglages du cabinet réservés à l'administrateur (niveau d'autonomie, services, extensions, profil du cabinet,
  correspondance des dossiers, profil et modèle de bordereau, barème) ; API `/api/v1` en lecture seule pour les avocats et assistants ;
  chemins ambigus (`//`, `..`, barre finale) normalisés ou refusés avant le contrôle des droits ; seuls `/static/<fichier>` et
  « À propos » sont servis sans connexion ; en-têtes `X-AxiorHub-*` venant du navigateur supprimés ; base des comptes en 0600, dossier
  en 0700, secrets en 0700.
- **Poste de pilotage** : bouton « Effacer la discussion » (les travaux, documents et brouillons ne sont pas touchés) ; bouton « Arrêter »
  sous chaque demande en attente ou en cours (annulée si elle n'a pas commencé, arrêt demandé sinon) ; « Annuler ce travail » dans le
  détail d'un travail de « Ce que fait l'agent ».
- **Surveillance des documents** : un nom de fichier ambigu dans Nextcloud est ignoré et signalé au lieu de bloquer la lecture du dossier ;
  un dossier illisible n'arrête plus la surveillance des autres (avant : surveillance de tout le cabinet suspendue après trois échecs).
- **Distribution** : bibliothèque `cryptography` ajoutée à l'image Docker (les notifications mobiles ne pouvaient pas fonctionner) ;
  tests de notifications sautés, et non en échec, sans cette bibliothèque.
- **Intégration continue** : dépendances de l'image installées ; tests exécutés aussi depuis l'archive livrée ; motifs des échecs
  publiés en annotations lisibles sans connexion.

## 5.6.2 — Questions sur les courriels

- **« Résume les mails reçus aujourd'hui »** (ou hier, cette semaine, à une date) : réponse immédiate dans le poste de pilotage, classée
  par suite donnée (à traiter, brouillon prêt, sans réponse nécessaire), sans IA ni file de travail. Auparavant, la question partait en
  rédaction de document dans un dossier deviné à tort.
- **Choix du dossier** : un seul mot courant du nom de dossier (« mails », « pièces », « dossier »…) ne suffit plus à choisir le dossier
  d'office ; il reste proposé.
- **État du système** : les cartes en incident montrent les fichiers en cause (chemin et motif) ; les fichiers facultatifs absents
  (intégration Open WebUI, clé de mises à jour désactivées) ne sont plus des incidents ; les redémarrages du service ne comptent plus comme
  des boucles pour les contrôles automatiques.
- **Boutons d'action immédiate** (« Contrôler maintenant »…) : « Fait ✓ », puis bouton rétabli et page actualisée, au lieu de rester
  « Enregistré ✓ » et désactivés.
- Lecture des fichiers JSON toujours en UTF-8 (accents corrects quel que soit le système) ; titre des anciennes pages « AxiorHub Pilote ».

## 5.6.1 — Interface fiable

- **Survol lisible** : les anciennes règles de style donnaient à tous les boutons un texte blanc au survol, y compris aux éléments à fond clair
  (cartes « À relire », boutons secondaires), qui devenaient illisibles. Elles ne visent plus que les boutons sans style propre ; un contrôle
  automatique du contraste au survol couvre les pages principales, en mode clair et sombre.
- **Volet de relecture** : l'arrière-plan n'est plus un bouton ; il reste un voile semi-transparent au lieu de devenir bleu plein.
- **Liens du poste de pilotage** : le préfixe de l'interface (par exemple `/agent-courriel`) est transmis ; « Ouvrir dans Courriels à relire »
  et les autres liens des fragments mènent à la bonne page.
- **« Ce que fait l'agent »** : chaque travail s'ouvre sur son détail (document demandé, pièce ou courriel concerné, avancement, résultat,
  « Ouvrir dans l'éditeur », « Ouvrir dans Nextcloud », « Relancer ») ; la liste indique déjà le nom de la pièce ou l'objet de la demande.
- **« Envoyer… »** : si le brouillon a été modifié, il est enregistré puis la confirmation d'envoi s'ouvre, au lieu d'un refus.
- **Recette automatique** : motif de chaque échec affiché ; deux tests dépendants de l'environnement corrigés (horloge, LibreOffice présent).
- **Diagnostic** : les minuteries d'analyse et de nettoyage sont réactivées par l'installateur d'interface ; commande de relance indiquée.
- **File de travail** : vieillissement des priorités ; un travail automatique n'attend plus indéfiniment, sans passer devant vos demandes.

## 5.6.0 — Distribution

- **Assistant d'installation** au premier démarrage : identité du cabinet, messagerie, Nextcloud (découverte des
  agendas), IA locale, mixte ou externe, mode observation ou brouillons ; chaque connexion se teste avant enregistrement.
- **Ensemble Docker pour VPS** (`deploy/vps/`) : AxiorHub, Nextcloud (MariaDB, Redis, cron), Roundcube, OnlyOffice,
  Ollama en option ; **hôtes virtuels Apache** et **certificats Let's Encrypt (Certbot)** ; script d'installation,
  réglages Nextcloud (Agenda, Tâches, Deck, OnlyOffice) et sauvegarde.
- **Comptes et rôles du cabinet** : administrateur, avocat, assistant(e) ; contrôle côté serveur ; création de comptes
  avec mot de passe provisoire, désactivation, réinitialisation, journal ; premier compte administrateur, inscriptions
  publiques fermées.
- **Mention d'auteur** : page « À propos », pied du menu, `NOTICE` (condition AGPL article 7 b), `AUTHORS.md`.
- **Identité du cabinet** issue de la configuration (signature, prompts, ressort) au lieu de valeurs écrites en dur.
- **Documentation publique** : README, installation VPS, installation système, comptes et rôles, confidentialité,
  architecture.
- **Contrôle de confidentialité renforcé** (`scripts/privacy-scan.py`) : texte et métadonnées des modèles `.docx`,
  courriels, téléphones, chemins de serveur, adresses IP, clés d'API, liste personnelle non publiée. Les tests et les
  modèles de documents n'utilisent plus que des données fictives.
- Emplacement de Roundcube détecté automatiquement (`AXIORHUB_ROUNDCUBE_ROOT` pour l'imposer).

## 5.5.0 — Style du cabinet

Apprentissage des formulations validées par l'avocat, règles visibles et révocables ; interface alignée sur la maquette
du poste de pilotage ; correction du cache des feuilles de style.

## 5.4.0 — IA externe sûre

Pseudonymisation réversible obligatoire hors IA locale, aperçu de ce qui part, connecteur Anthropic natif, mode mixte
local et API.

## 5.3.0 — Poste de pilotage

Écran unique : ce qui attend une décision, ce qui est prêt, ce qui bloque ; zone de saisie qui devine le dossier ;
Deck et Tâches Nextcloud.

## 5.2.x — Pièces, agenda, interface et fiabilité

Demandes de documents, « Pourquoi peu de brouillons ? », file de travail débloquée.

## 5.1.0 — Pièces et bordereaux

Numérotation, tampon, bordereau de communication de pièces, assemblage PDF.

## 5.0.x — Fonctions métier

Temps et honoraires, rendez-vous, conflits d'intérêts, prescription, pilotage ; agenda et tâches ; interface unique.

## 4.x — Assistant vivant

Routage hybride des modèles, apprentissage métier, installation autonome cumulative, production utile et mesurée,
surveillance IMAP en continu, deux workers, atelier de correction des courriels et documents.

## 3.x — Poste de travail

Interface guidée, documents du cabinet, préparation proactive, assistance métier, documents longs, distribution Docker
autonome sous AGPL.

## 1.x – 2.x — Assistant courriel

Brouillons de réponse dans la messagerie, mémoire des réponses envoyées, rattachement aux dossiers Nextcloud, chronologie,
analyse stratégique, projets d'actes, agenda et tâches, recherche juridique tracée.
