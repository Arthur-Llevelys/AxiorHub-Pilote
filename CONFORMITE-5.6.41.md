# Conformité 5.6.41 — application Nextcloud et version poste (mode découverte)

Version de référence : 5.6.40. Demandes de l’avocat du 13 octobre 2026 :

1. que l’application Nextcloud explique d’elle-même le cas d’un moteur installé sur un poste local, avec un paragraphe dans sa
   description et dans l’aide, et que la description de la boutique et le README montrent les prérequis et les étapes d’installation
   (version serveur Linux, application via la boutique Nextcloud) ;
2. puis : rendre tout de même les versions Windows et Ubuntu poste compatibles avec l’application Nextcloud, limitées à l’adresse locale
   du PC (`http://127.0.0.1:<port>`), avec un avertissement rappelant les limites, recommandant la version serveur pour la sécurité et
   précisant que cette configuration sert aux tests et à la découverte. Choix retenu : « Découverte + lien depuis un Nextcloud hébergé ».
3. enfin : revoir le README du dépôt de l’application Nextcloud (ce que fait l’application, installation de l’application et de la
   version serveur, ou d’une version locale limitée à la découverte, captures actualisées, français et anglais) et le README du moteur
   (captures actualisées, installation et configuration des versions serveur, Windows 11 et Ubuntu, en quatre langues).

| Demande | État 5.6.41 / application 0.4.2 |
|---|---|
| Poste compatible, limité à l’adresse locale du PC | **Réalisé.** Moteur en mode poste (`ui-auth.json` « poste » ou `AXIORHUB_POSTE_HOME`) : routes `/integration/v1/*` acceptées depuis l’adresse de bouclage seulement (`127.0.0.1`, `::1`) ; Nextcloud dans Docker Desktop : nom `host.docker.internal` admis sur le port configuré, sur la seule route d’intégration, depuis l’adresse de bouclage ; tout appel relayé : 409 `moteur_poste_non_pris_en_charge` avec un message explicite. Cookie de session admis dans le cadre sur `127.0.0.1` / `localhost`. Tests `tests/test_v5641.py` (WorkstationEngine). |
| Nextcloud hébergé | **Réalisé.** Le serveur Nextcloud ne peut pas joindre un poste : réglage personnel « Adresse de mon AxiorHub local » (adresse de bouclage seulement, contrôlée) et entrée « Mon AxiorHub local » qui ouvre AxiorHub dans un onglet du navigateur, avec l’avertissement ; aucun échange entre le serveur Nextcloud et le poste. Tests PHP `UserLinkTest`. |
| Avertissement (limites, tests et découverte, version serveur recommandée) | **Réalisé.** Texte transmis par le moteur (`avertissement`) et affiché par l’application en bandeau permanent ; repris dans « Mon AxiorHub local », les paramètres personnels, la page du poste (Paramètres › Connexions › Application Nextcloud), la description de la boutique, le README de l’application et les guides du poste. Traduit en anglais, allemand et espagnol. |
| Message clair dans l’application, paragraphe dans la description et l’aide | **Réalisé.** Messages « AxiorHub installé sur un poste de travail : utilisable seulement en mode découverte… » et « AxiorHub est injoignable… » (orientation vers « Mon AxiorHub local ») ; note à côté de l’adresse dans les paramètres personnels et d’administration ; section « Mode découverte » de la description et du README. |
| Prérequis et étapes visibles (boutique et README du dépôt de l’application) | **Réalisé.** Prérequis (serveur Ubuntu 24.04 LTS ou Debian 12, mémoire, disque, Nextcloud 32 à 35 en HTTPS, PHP, adresse HTTPS, comptes), puis Étape 1 (version serveur sur Linux, commandes), Étape 2 (boutique Nextcloud ou `occ app:install`), Étape 3 (liaison des comptes), installation sans la boutique, messages en cas de difficulté ; README avec une partie en anglais. |
| README du dépôt de l’application Nextcloud (français et anglais) : ce que fait l’application, installation de l’application et de la version serveur, ou d’une version locale limitée à la découverte, captures actualisées | **Réalisé.** `README.md` (français) et `README.en.md` (anglais) : ce que fait l’application, « Quelle configuration choisir ? », prérequis, étapes 1 à 3, mode découverte avec avertissement « jamais pour la production », dépannage ; captures fictives `screenshots/fr|en/` (interface dans Nextcloud, bandeau du mode découverte, « Mon AxiorHub local », paramètres personnels). |
| README du moteur en français, anglais, allemand et espagnol : captures actualisées, installation et configuration des versions serveur, Windows 11 et Ubuntu | **Réalisé.** Section « Installer » commune aux quatre langues (version serveur recommandée, Windows 11, Ubuntu 24.04, premier réglage, application Nextcloud) ; neuf captures par langue (`docs/captures/fr|en|de|es/`). Deux défauts trouvés en préparant les captures et corrigés : service worker et cadre Nextcloud, barre des routines en mode sombre. Tests `EngineReadme5641`, `NextcloudApp042`. |

Limites : en mode découverte avec un Nextcloud hébergé, l’application ne fait qu’ouvrir AxiorHub dans un onglet (ni Pilote, ni
recherche, ni affichage dans Nextcloud) ; un Nextcloud dans Docker sous Linux (Ubuntu) ne joint pas l’adresse de bouclage du poste :
installer Nextcloud directement sur le poste. La publication sur la boutique d’applications Nextcloud (signature, enregistrement) reste à
faire par le responsable du projet ; d’ici là, l’installation manuelle décrite dans le README s’applique.
