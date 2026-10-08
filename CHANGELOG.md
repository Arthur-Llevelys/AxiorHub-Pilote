# Historique des versions

AxiorHub — créé par Timo RAINIO. Les notes détaillées des versions antérieures à la publication ouverte ne sont pas
reprises ici ; seules les grandes étapes le sont.

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
