# AxiorHub Pilote 5.6.8 — couverture et recette

Date : 6 octobre 2026. Base : archive cumulative 5.6.7 livrée dans cette conversation.
Le code AGPL, la politique de marque et la licence du logo existants sont conservés.
Aucune clé de publication ni donnée réelle du cabinet n'est ajoutée au paquet.

Le cahier des charges « Agents proactifs et voix » décrit plusieurs étapes. Cette
version en réalise le premier ensemble utilisable dans le moteur existant. Le
tableau ci-dessous précise les adaptations et les fonctions encore partielles.
Une recette locale avec des doubles de services ne constitue pas une vérification
sur IMAP, Nextcloud, un microphone ou un fournisseur IA réel.

## 1. Couverture des événements métier

| Référence | Fonction livrée | Limite explicite |
|---|---|---|
| E01 — demande entrante | Moteur de réponse automatique, IDLE, contrôle périodique et relecture IMAP conservés. | Dépôt seulement si les paramètres, sources et contrôles l'autorisent. Toutes les demandes ne doivent pas recevoir un brouillon. |
| E02 — engagement envoyé | Lecture du dossier Envoyés réel, identité configurée, extraction hors citations, provenance IMAP, condition et date source ; analyse puis projet. | Extraction française prudente par règles ; jours de semaine, dates vagues et textes de plus de 60 000 caractères nécessitent une précision. Pas d'engagement prétendument exécuté par le simple dépôt d'un projet. |
| E03 — demande de pièces | Liste de suivi en note interne, engagement, délai proposé selon le profil. | Le délai est un réglage de suivi, pas un délai procédural. Pas encore d'inventaire déterministe pièce par pièce des pièces reçues. |
| E04 — réponse reçue | Rattachement par en-têtes, auteur destinataire et chronologie ; état « réponse reçue, pièces à vérifier ». | Même un accusé suspend la relance automatique pour éviter une relance abusive. Les pièces encore manquantes se reprennent explicitement ; leur réception complète n'est pas déduite du seul accusé. |
| E05 — réponse absente | Brouillon neutre pour client unique confirmé, fil relu, droits revalidés, effet journalisé et contenu relu dans IMAP. | Après un échec, relance explicite. Pas de relance vers l'adversaire, un tiers ou plusieurs destinataires sans confirmation. Aucun envoi. |
| E06/E07 — conclusions adverses ou contrat | Fichiers nouveaux repérés dans l'inventaire autorisé ; analyse puis note des moyens ou consultation contractuelle. Sources sélectionnées reprises par les producteurs existants. | Détection initiale par nom/type, à confirmer par l'analyse. Les pièces jointes d'un email suivent le moteur existant ; elles ne possèdent pas toutes une nouvelle chaîne E06. |
| E08/E09 — pièce ou version | Empreinte/ETag et date source, exclusion des productions propres, nouveau plan, ancien plan suspendu. | Dossiers parcourus progressivement, inventaire borné ; pas de diff binaire généralisé. Une modification survenant à la toute fin d'un dépôt doit encore être examinée. |
| E10 — audience | Audience identifiée dans les quatorze jours, dossier exact, analyse puis note et liste de lacunes. | Cette version ne génère pas trois plans distincts J-14/J-7/J-2 ni un bordereau et toutes les pièces en une seule mission. Les producteurs historiques restent accessibles. |
| E11/E12 — visio, report, annulation | Préparation du rendez-vous, salon Talk privé optionnel avec contrôle des participants, réutilisation d'un lien connu, suspension de l'ancien plan. | API OCS v4 et périmètre strict ; aucun ajout d'invités ou modification CalDAV. Une annulation/suppression que le serveur ne retourne pas ne peut pas être affirmée observée. Une récurrence non développée est signalée. |
| E13/E14/E15/E16 | Missions de compte rendu, surveillance d'inactivité, propositions de diligence et lectures Invoice Ninja existantes conservées. | Pas de nouvel automate général liant toutes ces sources au journal 5.6.8 ; pas de facture définitive ni de temps facturable inventé. |
| E17/E18 — briefing et veille | Brief depuis les caches, fuseau personnel, trois titres de veille ; collecte quotidienne RSS/Atom officiel, date et lien conservés, cache et erreurs. | Flux à renseigner/tester, filtre lexical ; pas de collecte universelle ni de conclusion d'applicabilité juridique à un dossier. Lecture à la demande ; pas de diffusion sonore surprise. |
| E19 — corrections | Mémoire métier et règles de portée conservées ; nouvelles missions réutilisent les producteurs et préférences existants. | Pas de nouvelle simulation automatique d'une règle sur tout l'historique ; aucune règle généralisée silencieusement. |
| E20 — reprise | Curseurs, plans et missions persistants, claims expirables, identifiants stables, limite de reprise et journaux de réconciliation IMAP/Talk. | Incertitude non résolue : arrêt explicite et revue. La rétention d'une réservation externe ne prouve pas le montant réellement facturé. |

## 2. Architecture et permissions

Le socle utilise WSGI/Waitress, SQLite, les workers existants, SSE et HTMX. Aucune
migration fictive vers FastAPI, Redis ou neuf agents séparés n'est introduite.
Les nouveaux profils et objets utilisent la migration transactionnelle numérotée
`0568_proactivity.sql`. Les plans pointent sur les missions 5.6.7 existantes.

Les rôles A0–A8 décrivent des responsabilités et les possibilités de nouvelles
initiatives. Désactiver un rôle empêche ses nouvelles étapes ; cela ne lève jamais
un contrôle de confidentialité ou de qualité. Les anciens traitements restent
soumis à leurs permissions. Le contrôle contradictoire du socle demeure soumis à
la configuration du second modèle ; la 5.6.8 ne prétend pas l'avoir exécuté sur tous
les textes ni avoir mesuré la justesse du droit.

Les routes ajoutées sont authentifiées et les mutations utilisent le même CSRF.
Un avocat peut modifier son profil de proactivité ; les diagnostics de connexion
sensibles restent administrateur. Identités et compte actif sont revalidés avant
effet. Les vues Engagements, les plans et les caches vocaux sont filtrés par
utilisateur. Cette distribution est **une installation par cabinet**, avec membres
du même cabinet : elle ne constitue pas une architecture multi-cabinet avec ACL
complètes sur tous les flux historiques. Isoler les installations de cabinets distincts.

Préparer un brouillon, un projet ou un salon privé peut être automatique dans le
périmètre autorisé. Envoyer, inviter, partager, signer, déposer un acte et émettre
une facture nécessitent le parcours humain existant. Aucun agent ne peut s'octroyer
des outils ou faire d'un texte de pièce une instruction autorisée.

## 3. Voix et économie

Le dialogue combine MediaRecorder, détection locale de silence, transcription via
le pont local, mission de réponse et TTS. Il propose des tours successifs. Les
capacités de l'API indiquent explicitement `full_duplex_streaming: false`.
Il n'y a pas de mesure locale du délai parole→réponse ni de certification de
l'interruption en présence d'écho. Les tests du code ne remplacent pas ce contrôle.

La discussion utilise la fonction `voice_conversation` et peut choisir le modèle
rapide configuré. Le mode mission autorise les préparations internes demandées,
sans élargir les permissions. eSpeak est le secours local ; les adaptateurs
Kokoro/Chatterbox requièrent une API WAV compatible. Le serveur Chatterbox n'est
pas livré, et la langue/variante doit être testée.

ElevenLabs exige autorisation globale et explicite, dossier non exclu, clé en
coffre, tarif et plafonds. Réservation atomique partagée, refus avant appel et
cache par utilisateur/dossier/modèle/voix sont testés. Le tarif est renseigné par
l'administrateur : aucun prix commercial ni facture n'est interrogé automatiquement.
Le texte vocal externe est pseudonymisé avant transmission et consultable dans
le panneau vocal. L'audio ne peut pas restaurer les identités : la lecture des
noms réels requiert une voix locale. La pseudonymisation n'est pas une garantie
d'anonymat de tous les détails d'une situation.
Après une réponse incertaine, le budget reste réservé. Les WAV du cache sont
privés et leur purge est bornée à vingt-quatre heures/cent fichiers ; les
enregistrements temporaires STT suivent les limites du pont existant.

## 4. Recette automatisée

Commande reproductible dans le paquet extrait :

```bash
PYTHONPATH=tests:. python3 -m unittest discover -s tests -q
python3 -m compileall -q agent
node --check agent/static/v568.js
```

Les résultats de la livraison sont consignés dans `VALIDATION-5.6.8.json`.
Les tests couvrent notamment :

- promesse hors citation, condition, date relative et identité d'envoi ;
- fenêtre bornée, reprise du curseur et UIDVALIDITY sans deuxième engagement ;
- rattachement ambigu et profil exclu ou en vacances ;
- plan concurrent dédupliqué, dépendances, pause et suspension après écartement ;
- réponse reçue distincte d'une exécution ;
- dépôt IMAP relu, timeout après APPEND et absence de double dépôt ;
- worker réel et registre de preuve, erreur sans relance périodique en boucle ;
- salon privé, lien existant, report/annulation et création Talk incertaine ;
- flux officiel daté, doublons, XML externe refusé et erreur de source visible ;
- cache vocal privé, exclusion, repli et budgets concurrents ;
- routes authentifiées, rôles, modèle rapide vocal et reprise après redémarrage ;
- migrations classiques, restauration du lien de version, manifeste et absence de
  secrets/adresses de dossier réels dans le code public.

## 5. Recette réelle des 45 critères du cahier des charges

Les références ci-dessous renvoient aux critères R01–R45 du cahier des charges.
Elles ne sont pas déclarées toutes validées sur votre serveur.

| Groupe | Vérification locale | Recette restant nécessaire |
|---|---|---|
| R01–R06, R09–R14 | Identités, citations, état de brouillon, conditions, ambiguïté, déduplication et reprise avec doubles de services. | Envoyés/Brouillons de votre fournisseur, alias/boîte partagée, agenda et documents réels ; rendre les sources consultées lisibles dans chaque type de livrable. |
| R07–R08 | Réponse détectée sans preuve de réception complète ; suspension et reprise explicite. | Vérification pièce par pièce encore partielle ; comportement prudent adapté plutôt qu'une relance maintenue silencieusement. |
| R15–R18 | Tests hérités d'analyse longue et de couverture conservés ; nouvelle pièce raccordée. | Corpus anonymisé avec dernière page, scan/OCR, nouveaux moyens et vrai contrôle de références officielles. |
| R19–R25 | Talk et calendrier testés par doubles, lien existant et occurrence distincte, pas d'invitation. | Version Talk installée, participants réellement relus, serveur renvoyant les annulations et absence de notifications aux invités. |
| R26–R30 | Arrêt des ressources prévu par l'interface, discussion distinguée d'une mission, texte corrigible, permissions serveur conservées. | Microphone, autoplay, écho, bruit, interruption, reconnexion et dates sur vos navigateurs ; test oral complet non exécuté ici. |
| R31–R37 | Refus externe, cache, erreurs de veille, aucune nouvelle inventée et réservations atomiques vérifiés. | Synthèse réelle locale/ElevenLabs, plafond conforme au contrat, quota des flux et coût réellement facturé. |
| R38–R40 | Portée des corrections héritée ; autorité, source et reprise revalidées. | Simulation métier complète ; course d'une source modifiée pendant la dernière phase de production. |
| R41–R42 | Filtrage personnel des nouvelles API et caches ; protection des formulaires ciblés et état SSE. | Cabinets distincts à isoler ; contrôle de tous les flux historiques, focus clavier, mobile et conservation de saisie sur navigateur. |
| R43–R45 | Notification procédurale conservée pour qualification, eSpeak sans option Docker, tests de sauvegarde/migration. | Restauration complète isolée des services IMAP/Nextcloud, image Docker construite et démarrage des options. |

## 6. Scénario de mise en service conseillé

1. Dossier fictif avec client confirmé, sources et modèles de test ; tester toutes
   les connexions sans activer encore les effets automatiques.
2. Envoyer réellement au compte de test une promesse « Je vous enverrai le contrat
   demain ». Vérifier la phrase source, la date et le dossier, puis le plan et le
   projet réellement relu dans Nextcloud. Ne pas envoyer le projet.
3. Envoyer une demande de pièces dans le même dossier. Tester accusé simple,
   déclaration de pièces encore manquantes et préparation d'une relance en Brouillons.
4. Couper le réseau après un dépôt simulé puis relancer : vérifier un seul brouillon
   et une preuve de relecture, ou un arrêt explicite si l'effet est indécidable.
5. Créer une visio de test, vérifier le salon privé, déplacer puis annuler la
   réunion. Aucun client ne doit avoir reçu une notification de l'agent.
6. Configurer un flux officiel effectivement accessible, puis une source en panne.
   Le briefing ne doit pas inventer une nouveauté lorsque le flux est vide ou en erreur.
7. Discuter sur un dossier fictif ; tester arrêt, arrière-plan, correction et refus
   d'une voix externe non autorisée. Mesurer la latence sous une rédaction simultanée.
8. Vérifier les incidents, les quotas, la sauvegarde et la restauration isolée avant
   d'élargir les profils au cabinet.

## 7. Références techniques utilisées

- Nextcloud Talk, API de conversations OCS :
  https://nextcloud-talk.readthedocs.io/en/latest/conversation/
- ElevenLabs, API de synthèse : https://elevenlabs.io/docs/api-reference/text-to-speech/convert
- Kokoro-FastAPI, serveur compatible : https://github.com/remsky/Kokoro-FastAPI

Ces références documentent les protocoles. Elles ne constituent pas une
attestation d'exécution des services dans cet environnement.

## 8. Agents documentaires et agendas multiples ajoutés avant finalisation

| Demande | Réalisation | Limite ou condition |
|---|---|---|
| Créer une mission en langage naturel | Interprétation par vocabulaire déterministe ou modèle configuré ; recette à champs et actions fermés, édition graphique, simulation et activation. | Pas d'exécution de scripts ni de connexion arbitraire choisie par le modèle. Les missions non représentables sont refusées ou réduites aux actions visibles à approuver. |
| Règles initiales modifiables/supprimables | Renvoi, soit transmis, injonction, appel/CONVOC et conclusions, dix jours par défaut ; révision et historique. | Présentes en état À relire. Activation une fois par version ; suppression conservée après rechargement. |
| Déclenchement récent | Métadonnées WebDAV création/ETag ; contenu d'en-tête lorsque le nom ne suffit pas ; nom explicite prioritaire. | Date absente → décision avec preuve, ou choix explicite de modification. Type lexical, non qualification juridique certifiée. Parcours des grands dossiers par lots. |
| Fichiers entrants et classement | Dossiers d'arrivée configurés dans les racines, rattachement unique, MOVE avec ETag et Overwrite F, relecture SHA-256 dans PROCEDURE. | Pas de rangement dans un dossier ambigu ; pas d'écrasement ou nettoyage d'une copie existante. Coupure après MOVE → réconciliation de la destination. |
| Analyse complète | Extracteur de pages du socle, découpages, couverture, cache et synthèses progressives ; références par page ou section logique. | Page vide non attestée → OCR requis. DOCX sans rendu PDF local → sections logiques, jamais fausses pages Word. La couverture atteste le passage de tous les fragments, pas l'exactitude juridique. |
| Brouillon et prochaine tâche | Destinataire selon rôle confirmé, brouillon interne si destinataire absent, Message-ID stable, UID/contenu relus, tâche interne dédupliquée. | Le switch global des brouillons reste prioritaire. Aucun envoi ; la tâche est un résultat local du registre, pas une prétendue exécution de la diligence. |
| Projet de conclusions | Si l'avocat à conclure est établi ou les écritures adverses reconnues, mission persistante A3 et producteur existant avec document sélectionné. | Rôle A3 actif et sources suffisantes. Auteur ambigu → proposition ; une rédaction ne vaut pas dépôt. Un blocage de production reste visible et relançable. |
| Inscription d'événements | Dates directes avec année, heure ou journée ; points de départ et calculs du registre ; aucune date LLM écrite seule. | OCR, date passée, circuit inconnu, majoration ou incident → proposition seulement. Durée d'une heure de rappel explicitée, pas durée réelle affirmée. |
| Plusieurs agendas | Cibles Nextcloud, CalDAV HTTPS indépendant, Google ; lecture préalable, déduplication et relecture après dépôt par cible. | Relecture incomplète → aucun nouveau dépôt ; effet incertain non retrouvé → arrêt, pas de doublon aveugle. Les événements humains équivalents sont conservés sans écrasement. |
| Google et secrets | OAuth Web, PKCE, état personnel limité à dix minutes et à usage unique, scope vérifié, refresh en coffre, découverte des agendas et révocation. | Client OAuth et URI exacte à configurer dans Google. Google reste externe même si le LLM est local ; transmission explicitement autorisée et dossier non exclu. Pas de mot de passe Google ni d'invité. |
| Interface et reprise | Trois pages secondaires sous le menu existant, simulation, résultat par étape, liens vers document/brouillon, détails repliables, préciser/reprendre/écarter. | Mise à jour SSE du socle et relecture de secours toutes les douze secondes. Pas de certification navigateur/mobile réel dans cette recette. |

La migration transactionnelle `0569_document_agents.sql` s'ajoute à 0568 ; elle
étend le registre existant sans remplacer les missions 5.6.7. Les règles, runs,
effets, empreintes de contenu et dépôts d'agendas survivent aux redémarrages.
Une installation conserve les services/configurations d'un cabinet ; les nouvelles
cibles personnelles ne créent pas des ACL multi-cabinet sur les flux historiques.

Tests supplémentaires : règles fermées, revue/révision, suppression persistante,
nom/type et dix jours, création absente, scope d'un dossier d'arrivée, reprise
MOVE et APPEND, source modifiée, OCR incomplet, toutes les pages analysées,
destinataire postulant/correspondant, modes observer/préparer/organiser et switch
global, plusieurs agendas, équivalent humain avec durée conservée, ICS UTF-8
replié, OAuth replay/autre utilisateur/scope insuffisant, coffre, callbacks sans
code persistant dans l'URL, authentification/CSRF et lecture sans tâche.

Recette à effectuer sur votre serveur avant élargissement :

1. Un dossier fictif, un client et un dominus fictifs, deux agendas de test.
2. Un avis de renvoi avec année/heure et une date WebDAV contrôlée, puis le même
   fichier renommé/copié : vérifier le classement et l'absence de second événement.
3. Un avis daté d'un jour et reçu un autre, puis un CONVOC sans circuit ; vérifier
   les propositions et points de départ plutôt que déduire depuis la date du fichier.
4. Une procédure avec majoration ou médiation : aucun calcul non contrôlé inscrit
   automatiquement. Comparer les résultats avec le texte et le régime applicables.
5. Google : autoriser, choisir deux cibles en lecture/écriture, inscrire un rappel
   minimal, révoquer ; vérifier les preuves réelles et qu'aucun invité n'a été ajouté.
6. Fichier de même nom différent déjà présent dans PROCEDURE : arrêt sans écrasement.
7. Couper une connexion après effet, relancer et vérifier une seule création,
   ou un incident clair si la réconciliation n'est pas concluante.

La régression de livraison compte **1 393 tests : 1 387 réussis, 6 ignorés,
aucun échec ni erreur**, en 166,001 secondes. Les six tests ignorés appartiennent
à l'ancien pont vocal HTTP, dont les dépendances ne sont pas installées dans
l'environnement de recette. Waitress 3.0.2 a été installé isolément pour les
tests HTTP de l'application. Les 63 scénarios des agents documentaires passent.
Les détails sont enregistrés dans `VALIDATION-5.6.8.json` ; ces résultats ne
remplacent pas la recette des comptes et services réels indiquée ci-dessus.

Sources de protocole et de règles consultées, à contrôler lors des évolutions :

- OAuth Google Web : https://developers.google.com/identity/protocols/oauth2/web-server
- Google Calendar événements : https://developers.google.com/workspace/calendar/api/v3/reference/events/insert
- Article 902 : https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000006411517
- Article 906-1 : https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000048852558
- Article 906-2 : https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000048852560
- Article 908 : https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000048869023
- Régimes et incidents (915-3/915-4) : https://www.legifrance.gouv.fr/codes/section_lc/LEGITEXT000006070716/LEGISCTA000006181698/
