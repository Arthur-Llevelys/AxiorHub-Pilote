"""Routes and pages of the 4.4.0 workshop: draft editor, document editor, settings.

``route`` is called by the WSGI application after authentication. ``public`` is
called before authentication and only serves the two token-protected routes
used by the Document Server. Every state-changing JSON call requires the exact
Origin and the CSRF token of the session.
"""
from .common import matter_display
import hmac
import json
from html import escape as e
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlencode, urlsplit

from .common import Stop, load_matters, clean_path, under
from .desk import Desk

STATIC = {'/static/v5697-gestion.css': 'text/css', '/static/v5697-sources.css': 'text/css', '/static/v5697-mission.css': 'text/css', '/static/v5697-mission.js': 'text/javascript', '/static/v5697-dossiers.css': 'text/css', '/static/v5697-dossiers.js': 'text/javascript', '/static/v5697-agenda.css': 'text/css', '/static/v5697-agenda.js': 'text/javascript', '/static/v5697-chatgpt.css': 'text/css', '/static/v5697-fiabilite.css': 'text/css', '/static/v5697-gmail.css': 'text/css', '/static/v5696-courriels.js': 'text/javascript', '/static/v5696-courriels.css': 'text/css', '/static/v5696-accueil.js': 'text/javascript', '/static/v5696-accueil.css': 'text/css', '/static/v5696-affichage.css': 'text/css', '/static/v5696-affichage.js': 'text/javascript', '/static/v5695-aujourdhui.css': 'text/css', '/static/v5695-aujourdhui.js': 'text/javascript', '/static/v5695-redaction.css': 'text/css', '/static/v5694-traces.css': 'text/css','/static/v5689-ia.js': 'text/javascript', '/static/v5689-ia.css': 'text/css', '/static/v5689-dossier.js': 'text/javascript', '/static/v5689-dossier.css': 'text/css', '/static/v5689-formulaires.js': 'text/javascript', '/static/v5689-navigation.js': 'text/javascript', '/static/v5689-carbon.css': 'text/css', '/static/v5689-carbon-jetons.css': 'text/css', '/static/v5689-livrables.js': 'text/javascript', '/static/v5689-livrables.css': 'text/css', '/static/v5689-cabinet.css': 'text/css', '/static/v5689-horaires.js': 'text/javascript','/static/v5688-crm.js': 'text/javascript', '/static/carbon5682.js': 'text/javascript', '/static/pdfjs5682.js': 'text/javascript', '/static/pdf.worker5682.js': 'text/javascript',
          '/static/v5688-reversibilite.js': 'text/javascript', '/static/v5688-reversibilite.css': 'text/css', '/static/v5682.js': 'text/javascript', '/static/v5682.css': 'text/css', '/static/v5683.css': 'text/css', '/static/v5684.css': 'text/css', '/static/v5684-office.js': 'text/javascript', '/static/v5681.js': 'text/javascript', '/static/v5680.js': 'text/javascript', '/static/v5679.js': 'text/javascript', '/static/v5674.js': 'text/javascript', '/static/v5673.js': 'text/javascript', '/static/v5671.js': 'text/javascript', '/static/v5670.js': 'text/javascript', '/static/v5660.js': 'text/javascript', '/static/v5653.js': 'text/javascript', '/static/v5653.css': 'text/css', '/static/v5652.js': 'text/javascript', '/static/v5652.css': 'text/css', '/static/v5651.js': 'text/javascript', '/static/v5651.css': 'text/css', '/static/v5650.js': 'text/javascript', '/static/v5650.css': 'text/css', '/static/v5647.js': 'text/javascript', '/static/v5643.js': 'text/javascript', '/static/v5637.js': 'text/javascript', '/static/v5637.css': 'text/css', '/static/office5636.js': 'text/javascript', '/static/office5636.css': 'text/css', '/static/v5636.js': 'text/javascript', '/static/v5636.css': 'text/css', '/static/v5634t.js': 'text/javascript', '/static/v5634.js': 'text/javascript', '/static/v5634.css': 'text/css', '/static/v5632.js': 'text/javascript', '/static/v5632.css': 'text/css', '/static/v5631.js': 'text/javascript', '/static/v5631.css': 'text/css', '/static/v5628.js': 'text/javascript', '/static/v5628.css': 'text/css', '/static/v5627.js': 'text/javascript', '/static/v5627.css': 'text/css', '/static/v5625.js': 'text/javascript', '/static/v5625.css': 'text/css', '/static/v5614.js': 'text/javascript', '/static/v5614.css': 'text/css', '/static/v5613.css': 'text/css', '/static/v569.js': 'text/javascript', '/static/rules568.js': 'text/javascript', '/static/v568.js': 'text/javascript', '/static/v568.css': 'text/css', '/static/v567.js': 'text/javascript', '/static/v567.css': 'text/css', '/static/v440.css': 'text/css', '/static/v440.js': 'text/javascript',
          '/static/v440-office.js': 'text/javascript', '/static/v450.css': 'text/css', '/static/v450.js': 'text/javascript',
          '/static/v460.css': 'text/css', '/static/v460.js': 'text/javascript',
          '/static/v470.css': 'text/css', '/static/v470.js': 'text/javascript',
          '/static/v480.css': 'text/css', '/static/v480.js': 'text/javascript',
          '/static/v490.css': 'text/css', '/static/v490.js': 'text/javascript',
          '/static/v500.css': 'text/css', '/static/v500.js': 'text/javascript',
          '/static/v510.css': 'text/css', '/static/v510.js': 'text/javascript',
          '/static/app520.css': 'text/css', '/static/v520.css': 'text/css', '/static/v520.js': 'text/javascript',
          '/static/v530.css': 'text/css', '/static/v530.js': 'text/javascript', '/static/v540.js': 'text/javascript', '/static/v550.js': 'text/javascript', '/static/v550.css': 'text/css', '/static/v560.css': 'text/css', '/static/v561.css': 'text/css'}
from .voix5694 import statiques as _statiques5694   # noqa: E402 — 5.6.94 (V01) : Brindille, Alkove et scripts de la voix, empreintes dans voix5694
STATIC.update(_statiques5694())
STATIC.update({'/static/v5698-fiche.js': 'text/javascript'})   # 5.6.98 : fiche du dossier (sous-onglets Fichiers, confirmation d'un retrait)
STATIC.update({'/static/v56100-accueil.css': 'text/css', '/static/v56100-produire.css': 'text/css'})   # 5.6.100 (lot E) : Aujourd'hui (raison, suite, Ma journée) et Produire (documents prêts)
STATIC.update({'/static/v56100-integrite.js': 'text/javascript'})   # 5.6.100 (lot C) : volet « Portefeuille détaillé » chargé au dépli
STATIC.update({'/static/v56100-dossiers.css': 'text/css'})   # 5.6.100 (lot D) : récents de l'IA, pagination, reprise de la fiche
STATIC.update({'/static/v56100-lecteur.js': 'text/javascript'})   # 5.6.100 (lot B) : liste « À relire » et lecteur commun (Courriels, Aujourd'hui)
STATIC.update({'/static/v56101-chrono.js': 'text/javascript'})   # 5.6.101 (lot M) : mesure facultative du temps (servi seulement à qui l'a activée)
STATIC.update({'/static/v56101-familles.css': 'text/css', '/static/v56101-familles.js': 'text/javascript'})   # 5.6.101 (lot F) : barres de famille, compteur d'usage, « Fonctions peu utilisées »
STATIC.update({'/static/v56101-aujourdhui.js': 'text/javascript', '/static/v56101-aujourdhui.css': 'text/css'})   # 5.6.101 (lot P) : blocs différés d'Aujourd'hui (dépli, après affichage, retour sans rechargement)
STATIC.update({'/static/v56101-securite.css': 'text/css', '/static/v56101-securite.js': 'text/javascript'})   # 5.6.101 (lot S) : page « Sécurité du serveur », bandeau d'Aujourd'hui
STATIC.update({'/static/v56101-volet.js': 'text/javascript', '/static/v56101-volet.css': 'text/css'})   # 5.6.101 (lot V) : volet de relecture à droite d'Aujourd'hui
STATIC.update({'/static/v56102-pilote.js': 'text/javascript', '/static/v56102-pilote.css': 'text/css'})   # 5.6.102 (lot M) : Pilote dans la messagerie instantanée
STATIC.update({'/static/v56102-navigation.css': 'text/css'})   # 5.6.102 (lot N) : libellés longs du menu principal (retour à la ligne)
STATIC.update({'/static/v56103-m.js': 'text/javascript'})   # 5.6.103 (lot M) : section « Compte Microsoft » (PKCE sur le poste, code d'appareil sur le serveur)
STATIC.update({'/static/v56103-liaison.js': 'text/javascript', '/static/v56103-liaison.css': 'text/css'})   # 5.6.103 (lot L) : liaison entre postes (messagerie, Paramètres)
STATIC.update({'/static/v56108-sync.js': 'text/javascript', '/static/v56108-sync.css': 'text/css'})   # 5.6.108 (lot S) : synchronisation des postes (indicateur)
STATIC.update({'/static/v56103-fichiers.js': 'text/javascript', '/static/v56103-fichiers.css': 'text/css'})   # 5.6.103 (lot F) : page « Fichiers » (deux volets, onglets, versions)
from .tableau56103 import statiques as _statiques56103t   # noqa: E402 — 5.6.103 (lot T) : volet « Tableau blanc », Excalidraw assemblé et ses polices (liste générée)
STATIC.update(_statiques56103t())
STATIC.update({'/static/v56106-redaction.js': 'text/javascript', '/static/v56106-redaction.css': 'text/css'})   # 5.6.106 (lot R) : rédaction d'un courriel
STATIC.update({'/static/v56106-envoi.js': 'text/javascript', '/static/v56106-envoi.css': 'text/css'})   # 5.6.106 (lot E) : options d'envoi, bandeau « Annuler », envois programmés
STATIC.update({'/static/v56106-liste.js': 'text/javascript', '/static/v56106-liste.css': 'text/css'})   # 5.6.106 (lot L) : liste, actions groupées, dossiers, raccourcis, densité
STATIC.update({'/static/v56106-densite.js': 'text/javascript', '/static/v56106-densite.css': 'text/css'})   # 5.6.106 (lot D) : densité de toutes les pages, titre et onglets en bande, logo du cabinet
STATIC.update({'/static/v56106-vision.js': 'text/javascript', '/static/v56106-vision.css': 'text/css'})   # 5.6.106 (lot I) : joindre une image ou une page à l'assistant (aperçu masqué)
STATIC.update({'/static/v56106-routine.js': 'text/javascript', '/static/v56106-routine.css': 'text/css'})   # 5.6.106 (lot O) : « Créer une routine à partir du message », routines sur message d'Aujourd'hui
STATIC.update({'/static/v56106-lecteur.js': 'text/javascript', '/static/v56106-lecteur.css': 'text/css'})   # 5.6.106 (lot V) : lecteur, HTML isolé, résumé par l'IA, courriel → tâche ou agenda
STATIC.update({'/static/v56106-fraude.js': 'text/javascript', '/static/v56106-fraude.css': 'text/css'})   # 5.6.106 (lot F) : pastille et bandeau « courriel potentiellement frauduleux »
STATIC.update({'/static/v56107-demo.js': 'text/javascript', '/static/v56107-demo.css': 'text/css'})   # 5.6.107 (lot M) : bandeau du mode Démo, visite guidée, « Essayer la démo »
STATIC.update({'/static/v56108-assistance.js': 'text/javascript', '/static/v56108-assistance.css': 'text/css'})
STATIC.update({'/static/v56108-accueil.js': 'text/javascript', '/static/v56108-accueil.css': 'text/css'})   # 5.6.108 (lot A) : Aujourd'hui, À relire, volet et reçu de l'assistant   # 5.6.108 (lot T) : Assistance métier en cinq parcours (saisie conservée, état de la demande)
STATIC.update({'/static/v56108-agenda.js': 'text/javascript', '/static/v56108-agenda.css': 'text/css'})   # 5.6.108 (lot G) : agenda, Produire, Dossiers
STATIC.update({'/static/v56108-questionnaire.js': 'text/javascript', '/static/v56108-questionnaire.css': 'text/css'})
STATIC.update({'/static/v56108-dictee.js': 'text/javascript'})   # 5.6.108 (lot Q) : « Dicter la réponse » sur chaque question (dictée commune de v56106-redaction.js)
STATIC.update({'/static/v56108-signaturepdf.css': 'text/css', '/static/v56108-signaturepdf.js': 'text/javascript'})   # 5.6.108 (lot Z) : signature par lien (SignaturePDF)
STATIC.update({'/static/v56108-conseil.css': 'text/css', '/static/v56108-conseil.js': 'text/javascript'})   # 5.6.108 (lot K) : conseil de modèles (réglages, annonce, trace dépliable)   # 5.6.108 (lot Q) : questionnaire de l'acte, aperçu à côté
from .wordcraft56108 import statiques as _statiques56108w   # noqa: E402 — 5.6.108 (lot W) : WordCraft 0.18.1 (fichiers de l'archive, empreintes dans wordcraft56108) et script du lot
STATIC.update(_statiques56108w())
STATIC.update({'/static/v56108-fichiers.js': 'text/javascript', '/static/v56108-fichiers.css': 'text/css'})   # 5.6.108 (lot F) : onglet Fichiers du dossier, remplir un PDF, outils PDF
STATIC.update({'/static/v56108-ia-locale.js': 'text/javascript', '/static/v56108-ia-locale.css': 'text/css'})   # 5.6.108 (lot L) : assistant « Configurer l'IA locale »
STATIC.update({'/static/v56109-repertoire.js': 'text/javascript', '/static/v56109-repertoire.css': 'text/css', '/static/v56109-pieces.js': 'text/javascript', '/static/v56109-pieces.css': 'text/css'})   # 5.6.109 (lot L) : répertoire du dossier trié (F02), barre des pièces jointes et classement du lecteur (F01)
STATIC.update({'/static/v56109-fiche.css': 'text/css', '/static/v56109-honoraires.css': 'text/css'})   # 5.6.109 (lot D) : fiche Conseil / Contentieux, prestations et honoraires du cabinet
STATIC.update({'/static/v56109-conso.js': 'text/javascript', '/static/v56109-conso.css': 'text/css'})   # 5.6.109 (lot M) : section « Consommation et coûts » (filtres, affichage prix ou tokens, détail paginé)
STATIC.update({'/static/v56109-reanalyse.js': 'text/javascript', '/static/v56109-reanalyse.css': 'text/css'})   # 5.6.109 (lot A, F03) : bloc « Analyse du dossier » de la fiche (suivi, couverture, comparaison avant remplacement)
STATIC.update({'/static/v56109-sources.css': 'text/css'})   # 5.6.109 (lot S) : sources publiques, services étrangers, registre étranger et documents publics
STATIC.update({'/static/v56109-navigateur.css': 'text/css'})   # 5.6.109 (lot O) : modèle du navigateur, prise de main acquittée, accès à ce poste
STATIC.update({'/static/v56109-produire.js': 'text/javascript', '/static/v56109-produire.css': 'text/css', '/static/v56109-craft-host.js': 'text/javascript'})   # 5.6.109 (lot V) : Produire en trois vues, livrables structurés, modèles Word (F08) ; pont hôte des éditeurs Craft facultatifs (F16)
STATIC.update({'/static/v56109-historique.js': 'text/javascript', '/static/v56109-historique.css': 'text/css', '/static/v56109-discussions.js': 'text/javascript',
               '/static/v56109-apparence.js': 'text/javascript', '/static/v56109-apparence.css': 'text/css'})   # 5.6.109 (lot H) : historique et discussions de l'assistant (F09), apparence du menu (F10)
STATIC.update({'/static/v56109-fonds.js': 'text/javascript', '/static/v56109-fonds.css': 'text/css'})   # 5.6.109 (lot Q) : « Fonds juridique local » (G23) et « Fichiers de ce poste » (F13) dans les Paramètres
STATIC.update({'/static/v56109-bland.css': 'text/css'})   # 5.6.109 (lot B) : carte Bland (Paramètres › Connexions API), demande à confirmer, routines « Mes agents IA »
STATIC.update({'/static/v56109-relations.js': 'text/javascript', '/static/v56109-relations.css': 'text/css'})   # 5.6.109 (lot G2) : partage client, appels, remplacements, kit d'audience (téléchargement d'un paquet rendu une fois ; formulaires communs)
STATIC.update({'/static/v56109-operations.css': 'text/css'})   # 5.6.109 (lot G3) : « À finaliser », Suites et exécution, formalités supervisées, RIB CARPA (aucun script propre : formulaires communs m5-form)
STATIC.update({'/static/v56109-gestion.css': 'text/css'})   # 5.6.109 (lot G1) : Préfacturation, Assistance régulière, Fonds, frais et débours (aucun script propre : formulaires communs d97)
STATIC.update({'/static/v56109-parcours.css': 'text/css'})   # 5.6.109 (lot G4) : cartes de collecte de portail, d'offre et PDF joint, de documents Pappers (aucun script propre : formulaires communs m5-form)
STATIC.update({'/static/v56109-simulation.css': 'text/css'})   # 5.6.109 (lot N, F14) : onglet « Simulation de décision » du dossier (aucun script propre : formulaires communs, tiroir Sources natif)
MAX_JSON = 260_000
NAV = (('/aujourdhui', 'Aujourd’hui', 'Ce qui demande votre attention'),
       ('/courriels', 'Courriels', 'Relire et corriger les brouillons'),
       ('/documents', 'Documents', 'Ouvrir et modifier les fichiers Nextcloud'),
       ('/dossiers', 'Dossiers', 'Vos dossiers et leur état'),
       ('/echeances', 'Échéances', 'Délais de procédure calculés et suivis'),
       ('/planning', 'Agenda', 'Audiences, rendez-vous, tâches'),
       ('/production', 'Produire', 'Demander un acte, une note, un courrier'),
       ('/verification', 'Vérifier', 'Citations juridiques, mentions obligatoires, relecture contradictoire'),
       ('/progres', 'Progrès', 'Apprentissage, tons par destinataire, règles, autonomie et traçabilité'),
       ('/recherche', 'Recherche', 'Courriels, fichiers, agenda et notes en une seule recherche'),
       ('/cabinet', 'Cabinet', 'Temps et honoraires, rendez-vous, conflits, prescription, pilotage mensuel'),
       ('/confort', 'Confort', 'Dictée, envoi facultatif, application mobile, raccourcis'),
       ('/parametres', 'Paramètres', 'Réglages et connexions'))


# ------------------------------------------------------------------ helpers
def json_out(value, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8',
            'body': json.dumps(value, ensure_ascii=False, default=str)}


def page_out(html, csp_extra=None):
    out = {'status': '200 OK', 'kind': 'text/html; charset=utf-8', 'body': html}
    if csp_extra:
        out['csp'] = csp_extra
    return out


def shell(title, body, prefix, csrf, active='', head=''):
    """Page à gabarit unique (menu latéral, barre du haut, assistant), identique à celui des autres pages d'AxiorHub."""
    from . import shell501
    from .i18n5631 import th
    content = ('<a class="cf-skip" href="#ax-main">' + th('shell.skip') + '</a><div class="ax-main ax-in-workspace" id="ax-main" tabindex="-1">' + body + '</div>'
               '<div id="ax-toast" role="status" aria-live="polite"></div>')
    scripts = ''.join('<script defer src="' + e(prefix) + '/static/' + name + '"></script>' for name in shell501.AX_JS)
    return shell501.render(title, content, prefix, csrf, active or '/courriels', scripts=scripts, head=head)


def _pages500():
    from . import web500
    return web500.PAGES


def err_text(ex):
    return str(ex)


def human(code):
    """Readable French messages for the codes users meet in the workshop."""
    table = {
        'imap_serveur_introuvable': 'Serveur de messagerie introuvable : vérifiez l’adresse IMAP (par exemple imap.votre-hebergeur.fr) et la connexion Internet.',
        'imap_port_ferme': 'Le serveur de messagerie refuse la connexion sur ce port : utilisez 993 (TLS) ou 143 (STARTTLS).',
        'imap_delai_depasse': 'Le serveur de messagerie ne répond pas : vérifiez l’adresse, le port, et qu’un pare-feu ou un antivirus ne bloque pas AxiorHub.',
        'imap_port_sans_tls_direct': 'Ce port n’accepte pas le TLS direct : utilisez le port 993, ou 143 avec STARTTLS.',
        'imap_certificat_refuse': 'Le certificat du serveur de messagerie n’est pas reconnu : vérifiez l’adresse exacte du serveur IMAP indiquée par votre hébergeur.',
        'imap_tls_echoue': 'La connexion chiffrée au serveur de messagerie a échoué : vérifiez le port (993 en TLS, 143 en STARTTLS).',
        'imap_starttls_indisponible': 'Ce serveur ne propose pas STARTTLS sur ce port : utilisez le port 993.',
        'imap_identifiants_refuses': 'Le serveur de messagerie refuse l’identifiant ou le mot de passe : ressaisissez-les (adresse complète en identifiant, mot de passe d’application si votre hébergeur l’exige).',
        'imap_port_invalide': 'Port IMAP invalide : indiquez 993 (TLS) ou 143 (STARTTLS).',
        'connexion_imap_echouee': 'Connexion à la messagerie impossible : vérifiez le serveur, le port, l’identifiant et le mot de passe (Paramètres › Connexions).',
        'http_401': 'Identifiant ou mot de passe refusé (erreur 401). Pour Nextcloud, créez un mot de passe d’application (Paramètres personnels › Sécurité).',
        'http_403': 'Accès refusé (erreur 403) : ce compte n’a pas le droit de lire ce dossier.',
        'http_404': 'Adresse ou dossier introuvable (erreur 404) : vérifiez l’adresse du serveur et la racine des dossiers.',
        'dossier_local_invalide': 'Le dossier de travail local doit être un chemin absolu vers un répertoire existant (par exemple /mnt/cabinet ou le dossier synchronisé du client Nextcloud), jamais la racine du système. Laissez vide pour lire les fichiers par WebDAV.',
        'dossier_local_introuvable': 'Le dossier de travail local configuré est introuvable : vérifiez le chemin dans Paramètres › Connexions ou videz-le pour revenir à WebDAV.',
        'agenda_webdav_non_configure': 'Les agendas restent lus par CalDAV : renseignez l’adresse, l’identifiant et le mot de passe Nextcloud dans Connexions, même avec un dossier de travail local.',
        'configuration_non_inscriptible': 'Le service ne peut pas écrire le fichier de configuration : le lien /etc/axiorhub-mail-agent/config.json a probablement été remplacé par un fichier ordinaire (commande « axiorhub-mail mode » ou « configure » d’une version précédente). Lancez « sudo python3 /opt/axiorhub-mail-agent/current/install-interface.py », qui rétablit le pont de configuration, puis réessayez.',
        'duree_invalide': 'La durée indiquée est invalide (en minutes, entre 1 et 1 440).',
        'secours_sans_fournisseur': 'Aucun fournisseur externe n’est prêt : activez-en un, autorisez l’envoi et indiquez un modèle dans Paramètres › IA.',
        'fichier_hors_dossier': 'Ce fichier n’appartient à aucun dossier du cabinet : il ne peut pas être ouvert d’ici.',
        'travail_invalide': 'Ce travail n’existe plus dans la file (il a pu être nettoyé). Actualisez la page.',
        'mode_honoraires_invalide': 'Choisissez un mode d’honoraires : horaire, forfait ou mixte.',
        'taux_horaire_requis': 'Indiquez le taux horaire convenu pour ce dossier.',
        'forfait_requis': 'Indiquez le montant du forfait ou du budget convenu.',
        'seuil_alerte_invalide': 'Le seuil d’alerte doit être un pourcentage entre 10 et 100.',
        'facture_deja_enregistree': 'Cette facture est déjà enregistrée pour ce dossier.',
        'numero_facture_requis': 'Indiquez le numéro de la facture.',
        'montant_invalide': 'Le montant est invalide (exemple : 1 250,00).',
        'aucune_partie': 'Indiquez au moins une partie à rechercher.',
        'role_partie_invalide': 'Le rôle de la partie est invalide.',
        'nom_partie_invalide': 'Le nom d’une partie est vide ou trop long.',
        'trop_de_parties': 'Douze parties au plus par recherche.',
        'decision_conflit_invalide': 'La décision doit être : accepter, décliner ou à étudier.',
        'motif_decision_conflit_requis': 'Des correspondances existent : indiquez le motif de votre décision (quelques mots au moins).',
        'recherche_conflits_absente': 'Aucune recherche de conflits n’existe pour ce dossier. Lancez-la d’abord.',
        'conflit_a_examiner': 'Ce dossier n’a pas encore passé la recherche de conflits d’intérêts : examinez le résultat et décidez avant toute production.',
        'dossier_decline_conflit': 'Ce dossier a été décliné pour conflit d’intérêts : aucune production n’est lancée.',
        'partie_absente': 'Cette partie n’existe plus. Actualisez la page.',
        'prescription_absente': 'Cette prescription n’existe plus. Actualisez la page.',
        'prescription_a_completer': 'Cette prescription doit d’abord être complétée (règle ou date de départ).',
        'prescription_close': 'Cette prescription est close : elle n’est plus modifiable.',
        'evenement_invalide': 'Cet événement est invalide (type ou date).',
        'evenement_inconnu': 'Ce type d’événement n’existe pas.',
        'evenement_absent': 'Cet événement n’existe plus ou ne figure pas à l’agenda.',
        'issue_invalide': 'L’issue choisie est invalide.',
        'regle_inconnue': 'Cette règle de prescription n’existe pas.',
        'regle_non_applicable': 'Cette règle ne s’applique pas à cette date de départ (texte non encore en vigueur) : choisissez une autre règle ou vérifiez la date.',
        'depart_non_prevu': 'Ce point de départ n’est pas prévu pour cette règle.',
        'date_invalide': 'La date est invalide (format AAAA-MM-JJ).',
        'notes_vides': 'Saisissez ou dictez vos notes avant d’établir le compte rendu.',
        'temps_absent': 'Cette écriture de temps n’existe plus.',
        'facture_absente': 'Cette facture n’existe plus.',
        'bareme_invalide': 'Une valeur du barème est invalide.',
        'etat_compte_rendu_invalide': 'L’état du compte rendu est invalide.',
        'fichier_vide': 'Le fichier est vide.',
        'fichier_invalide': 'Le fichier n’a pas pu être lu (CSV attendu, colonnes dossier, numéro, date, montant).',
        'brouillon_modifie_depuis_ouverture': 'Ce brouillon a été modifié ailleurs (messagerie ou agent) depuis son ouverture. Rouvrez-le pour voir la version actuelle ; votre texte est conservé dans la zone de saisie jusque-là.',
        'uidvalidity_modifiee': 'Le dossier Brouillons a été réorganisé par le serveur. Actualisez la liste.',
        'version_nextcloud_modifiee': 'Le fichier a changé dans Nextcloud pendant votre édition ; vos modifications ont été enregistrées dans une copie.',
        'editeur_documents_non_configure': 'L’éditeur de documents n’est pas encore configuré. Ouvrez les réglages de l’atelier.',
        'format_non_modifiable': 'Ce format ne peut pas être modifié ici.',
        'origine_ou_csrf_refuse': 'Session expirée : rechargez la page (Ctrl+F5).',
        'message_imap_disparu': 'Ce brouillon n’existe plus à cet emplacement (modifié, déplacé ou envoyé depuis la messagerie). Actualisez la liste.',
        'adresse_invalide': 'Une adresse de destinataire est invalide.',
        'dossier_absent': 'Ce dossier n’existe pas ou n’est plus configuré.',
        'mentions_obligatoires_manquantes': 'Des mentions obligatoires manquent : le projet n’est pas prêt à relire. Complétez-le (voir la liste dans le projet).',
        'references_a_verifier_avant_validation': 'Des références juridiques ne sont pas vérifiées. Contrôlez-les puis cochez la case de confirmation avant de valider.',
        'constat_sans_piece': 'La relecture a écarté un constat qui ne citait aucune pièce.',
        'champ_reference_refuse': 'Une référence juridique contient des caractères inattendus : elle n’a pas été transmise au service de sources.',
        'date_des_faits_invalide': 'La date des faits doit être au format AAAA-MM-JJ.',
        'modele_inconnu': 'Ce modèle d’acte n’existe pas.',
        'motif_mention_invalide': 'Un motif de mention est invalide ou trop complexe.',
        'mention_personnalisee_invalide': 'Une mention personnalisée est incomplète ou existe déjà.',
        'identifiant_piste_invalide': 'L’identifiant PISTE est invalide.',
        'secret_piste_invalide': 'Le secret PISTE est invalide.',
        'texte_vide': 'Le texte à contrôler est vide.',
        'base_locale_illisible': 'Le fichier de références du cabinet est illisible.',
        'information_memoire_absente': 'Ce fait n’existe plus. Actualisez la page.',
        'motif_obligatoire': 'Indiquez le motif (au moins quelques mots).',
        'confirmation_regle_requise': 'Une règle n’est créée qu’après votre confirmation explicite : cochez « Je confirme ».',
        'confirmation_autonomie_requise': 'Passer une tâche au niveau « agir » exige de cocher la confirmation.',
        'action_toujours_soumise_a_validation': 'Cette action sort du cabinet : elle reste toujours soumise à votre validation.',
        'tache_autonomie_inconnue': 'Tâche d’autonomie inconnue.',
        'niveau_autonomie_invalide': 'Ce niveau n’est pas proposé pour cette tâche.',
        'regle_invalide': 'La règle est invalide (portée, type ou valeur).',
        'regle_absente': 'Cette règle n’existe plus. Actualisez la page.',
        'etat_regle_invalide': 'État de règle invalide.',
        'portee_regle_invalide': 'La portée de la règle est invalide.',
        'trop_de_regles': 'Nombre maximal de règles atteint : supprimez-en avant d’en créer.',
        'type_courriel_invalide': 'Type de courriel inconnu.',
        'profil_de_ton_inconnu': 'Profil de ton inconnu.',
        'profil_de_ton_invalide': 'Les réglages du profil de ton sont invalides.',
        'correction_ton_invalide': 'La correction de détection est invalide.',
        'filtre_invalide': 'Filtre invalide.',
        'periode_invalide': 'La période doit être au format AAAA-MM-JJ.',
        'format_export_invalide': 'Format d’export inconnu (json ou csv).',
        'proposition_absente_ou_traitee': 'Cette proposition n’existe plus ou a déjà été traitée.',
        'action_invalide': 'Action invalide.',
        'agenda_non_configure': 'L’agenda n’est pas configuré.',
        'transcription_vide': 'Rien n’a été entendu. Réessayez en vous rapprochant du micro.',
        'transcription_trop_longue': 'La dictée est trop longue : dictez par phrases.',
        'texte_a_remplacer_introuvable': 'Ce texte n’apparaît pas dans le message : rien n’a été remplacé.',
        'texte_a_remplacer_ambigu': 'Ce texte apparaît plusieurs fois : rien n’a été remplacé. Précisez un passage plus long.',
        'texte_a_remplacer_vide': 'Dites quel texte remplacer.',
        'texte_a_ajouter_vide': 'Dites ce qu’il faut ajouter.',
        'aucune_phrase_a_supprimer': 'Il n’y a plus de phrase à supprimer dans le corps du message.',
        'dictee_locale_non_configuree': 'La passerelle Vocal locale n’est pas activée : la dictée vocale est indisponible.',
        'envoi_desactive': 'L’envoi depuis AxiorHub est désactivé : activez « Autoriser l’envoi quand je clique sur Envoyer » dans Paramètres › '
                           'Messagerie, ou utilisez « Ouvrir dans la messagerie ».',
        'envoi_non_active_pour_ce_dossier': 'L’envoi n’est pas autorisé pour le dossier de ce brouillon : dans Paramètres › Messagerie, choisissez '
                                            '« Tous les dossiers » ou cochez ce dossier.',
        'envoi_dossier_non_identifie': 'Le dossier de ce brouillon n’est pas identifié : l’envoi est refusé. Utilisez « Ouvrir dans la messagerie ».',
        'confirmation_envoi_requise': 'Cochez la case de confirmation avant d’activer l’envoi.',
        'smtp_non_configure': 'Aucun serveur d’envoi (SMTP) n’est configuré.',
        'smtp_securite_invalide': 'La sécurité SMTP doit être « starttls » ou « ssl ».',
        'smtp_echec': 'Le serveur d’envoi a refusé ou n’a pas répondu : le message n’est PAS parti.',
        'smtp_destinataire_refuse': 'Un destinataire a été refusé par le serveur d’envoi : vérifiez les adresses et la messagerie.',
        'brouillon_a_verifier': 'Ce brouillon est marqué « À VÉRIFIER » : contrôlez-le et retirez la mention avant tout envoi.',
        'brouillon_a_completer': 'Ce brouillon contient encore « [À COMPLÉTER : …] » (objet ou texte) : complétez le fait manquant avant tout envoi.',   # 5.6.95
        'brouillon_variante_a_choisir': 'Ce brouillon contient encore des variantes (« ▸ Variante A… ») : gardez-en une par choix et supprimez les autres avant tout envoi.',
        'brouillon_a_relire': 'Ce brouillon porte encore la mention « [À RELIRE] » (objet ou première ligne) : relisez-le et retirez la mention avant tout envoi.',
        'trop_de_destinataires': 'Trop de destinataires pour un envoi depuis AxiorHub.',
        'piece_jointe_non_envoyable_ici': 'Une pièce jointe ne peut pas être envoyée d’ici : utilisez la messagerie.',
        'apercu_envoi_absent': 'Cet aperçu d’envoi n’existe plus ou a déjà été utilisé. Recommencez.',
        'apercu_envoi_expire': 'L’aperçu d’envoi a expiré (5 minutes). Recommencez.',
        'confirmation_trop_rapide': 'Confirmation trop rapide : relisez les destinataires et l’objet puis confirmez.',
        'brouillon_modifie_depuis_apercu': 'Le brouillon a changé depuis l’aperçu : rien n’a été envoyé. Recommencez.',
        'deja_envoye': 'Ce message a déjà été envoyé : il n’est pas renvoyé.',
        'envoi_incertain_verifiez_les_envoyes': 'Un envoi identique est peut-être parti il y a moins d’une heure : vérifiez les éléments envoyés avant de recommencer.',
        'plafond_horaire_envois': 'Plafond d’envois par heure atteint.',
        'recherche_vide': 'Tapez un mot à rechercher.',
        'recherche_trop_longue': 'La recherche est trop longue (200 caractères au plus).',
        'recherche_sans_terme': 'Aucun mot exploitable dans la recherche.',
        'type_recherche_invalide': 'Choisissez au moins une source.',
        'notifications_indisponibles': 'Les notifications ne sont pas disponibles sur ce serveur.',
        'confirmation_notifications_requise': 'Confirmez l’activation des notifications.',
        'point_de_terminaison_refuse': 'Ce service de notification n’est pas accepté.',
        'trop_d_appareils': 'Nombre maximal d’appareils atteint : désactivez-en un.',
    }
    from .web510 import MESSAGES as messages510
    table.update(messages510)
    if code not in table:   # 5.6.34 : messages des nouveaux contrôles (corrections, annulations, agenda, profil, intégration)
        from .erreurs5634 import MESSAGES
        if code in MESSAGES:
            return MESSAGES[code]
        from .messages5668 import MESSAGES as messages5668   # 5.6.68 : plafond, scans, pauses… en clair
        if code in messages5668:
            return messages5668[code]
        from .gemini56109 import MESSAGES as messages56109e   # 5.6.109 (lot E, F15) : connexion Gemini non testée, blocage de sécurité, limite…
        if code in messages56109e:
            return messages56109e[code]
        from .modeles_catalogue56109 import MESSAGES as messages56109m   # 5.6.109 (lot E) : modèle à choisir, refus lisible du fournisseur
        if code in messages56109m:
            return messages56109m[code]
    return table.get(code, code.replace('_', ' '))


def check_post(env, auth):
    if env.get('HTTP_ORIGIN') != auth['origin'] or not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN', ''), auth['csrf']):
        raise Stop('origine_ou_csrf_refuse')
    length = int(env.get('CONTENT_LENGTH', '0') or 0)
    if length > MAX_JSON:
        raise Stop('requete_trop_longue')
    if env.get('CONTENT_TYPE', '').split(';')[0] != 'application/json':
        raise Stop('json_requis')
    try:
        data = json.loads(env['wsgi.input'].read(length).decode('utf-8') or '{}')
    except (ValueError, UnicodeError):
        raise Stop('json_invalide') from None
    if not isinstance(data, dict):
        raise Stop('objet_json_requis')
    return data


def allowed_path(desk, path):
    path = clean_path(path)
    roots = desk.c.get('nextcloud', {}).get('roots', [])
    if roots and not any(under(path, r) for r in roots):
        raise Stop('dossier_hors_racines')
    return path


# --------------------------------------------------------- unauthenticated
def public(env, cfg, path, method):
    """Document Server routes, protected by short-lived signed tokens."""
    if re.fullmatch(r'/office/(plugin|bridge)/[A-Za-z0-9._/-]{1,120}', path):   # 5.6.84 : fichiers du plugin et pont à jeton court
        from . import web5684
        desk = Desk(cfg)
        try:
            return web5684.public(env, desk, path, method, str(env.get('HTTP_X_AXIORHUB_PREFIX') or '/agent-courriel'))
        except Stop as ex:
            return {'status': '403 Forbidden', 'kind': 'text/plain; charset=utf-8', 'body': 'Accès refusé : ' + str(ex)}
        finally:
            desk.db.close()
    match = re.fullmatch(r'/office/(file|callback)/([A-Za-z0-9_\-.]{20,2000})', path)
    if not match:
        return None
    from . import office440
    desk = Desk(cfg)
    try:
        if match[1] == 'file' and method in ('GET', 'HEAD'):
            data, name = office440.serve_file(desk, match[2])
            return {'status': '200 OK', 'kind': 'application/octet-stream', 'body': data if method == 'GET' else b'',
                    'headers': [('Content-Disposition', 'attachment; filename*=UTF-8\'\'' + __import__('urllib.parse').parse.quote(name))],
                    'csp': "default-src 'none'; sandbox"}
        if match[1] == 'callback' and method == 'POST':
            length = int(env.get('CONTENT_LENGTH', '0') or 0)
            if not 0 < length <= 2_000_000:
                raise Stop('callback_taille_invalide')
            body = env['wsgi.input'].read(length)
            try:
                result = office440.handle_callback(desk, match[2], env.get('HTTP_AUTHORIZATION', ''), body)
            except Stop as ex:
                # The Document Server retries on error 1; report the reason in the audit log only.
                try:
                    desk.audit('editeur_documents_440_callback_refuse', {'reason': str(ex)[:120]})
                except Exception:
                    pass
                return json_out({'error': 1, 'message': str(ex)[:120]}, '403 Forbidden')
            return json_out(result)
        return {'status': '405 Method Not Allowed', 'kind': 'text/plain; charset=utf-8', 'body': 'Méthode refusée.'}
    except Stop as ex:
        return {'status': '403 Forbidden', 'kind': 'text/plain; charset=utf-8', 'body': 'Accès refusé : ' + str(ex)}
    finally:
        desk.db.close()


# ------------------------------------------------------------------ authed
def route(env, cfg, auth, prefix, path, args, method):
    if method == 'GET' and (path == '/audience' or (path == '/audiences-word' and not any(args.get(k) for k in ('hearing', 'word', 'job')))):
        # 5.6.34 (U01) : anciens liens → espace Audiences, au bon dossier
        from urllib.parse import urlencode as _ue
        target = prefix + '/audiences?' + _ue({'onglet': 'fiche' if path == '/audience' else 'preparer', **({'matter': args['matter']} if args.get('matter') else {})})
        return {'status': '302 Found', 'kind': 'text/plain; charset=utf-8', 'body': '', 'headers': [('Location', target)]}
    if method == 'GET' and path in ('/static/ecrans-en.json', '/static/ecrans-de.json', '/static/ecrans-es.json'):
        # 5.6.34 : dictionnaire des écrans pour les textes écrits par les scripts (v5634t.js) ; mis en cache dans la session du navigateur
        from .ecrans5632 import dictionary
        import json as _json
        return {'status': '200 OK', 'kind': 'application/json; charset=utf-8',
                'body': _json.dumps(dictionary(path[-7:-5]), ensure_ascii=False, separators=(',', ':'))}
    if method == 'GET' and (path == '/livrables' or path.startswith('/livrables/')):   # 5.6.89 : documents préparés et visionneuse commune
        from . import livrables_web5689
        from .shell501 import set_context
        set_context(cfg)
        return livrables_web5689.page_route(env, cfg, auth, prefix, path, args, shell)
    if method == 'GET' and path == '/administration/fonctions-peu-utilisees':   # 5.6.101 (lot F, F05) : lecture seule ; autre rôle : même réponse qu'une page absente
        from . import usage56101
        from .shell501 import set_context
        set_context(cfg)
        desk101 = Desk(cfg)
        try:
            html101 = usage56101.page(desk101, auth, prefix, env, shell)
        finally:
            desk101.db.close()
        return {**page_out(html101), 'headers': [('Cache-Control', 'private, no-store')]} if html101 else None
    if method == 'GET' and path == '/securite-serveur':   # 5.6.101 (lot S) : veille de sécurité du serveur ; autre rôle : même réponse qu'une page absente
        from . import securite_web56101
        from .shell501 import set_context
        set_context(cfg)
        return securite_web56101.page_route(env, cfg, auth, prefix, path, args, shell)
    if method == 'GET' and path == '/signature-par-lien':   # 5.6.108 (lot Z) : « Faire signer par lien » sur un PDF d'un dossier (lecture seule ; 404 neutre)
        from . import signaturepdf56108
        from .shell501 import set_context
        set_context(cfg)
        return signaturepdf56108.page_route(env, cfg, auth, prefix, path, args, shell)
    if method == 'GET' and (path == '/editeur/craft' or path.startswith('/editeur/craft/')):   # 5.6.109 (lot V, F16) : éditeurs Craft facultatifs (page, cadre, ressources du manifeste ; désactivés par défaut)
        from . import craft56109
        from .shell501 import set_context
        set_context(cfg)
        return craft56109.page_route(env, cfg, auth, prefix, path, args, shell)
    if method == 'GET' and path in ('/editeur/wordcraft', '/editeur/wordcraft/cadre', '/editeur/traitement'):   # 5.6.108 (lot W) : éditeur intégré WordCraft, cadre, Word / LibreOffice du poste
        from . import wordcraft56108
        from .shell501 import set_context
        set_context(cfg)
        return wordcraft56108.page_route(env, cfg, auth, prefix, path, args, shell)
    if method == 'GET' and path == '/fichiers':   # 5.6.103 (lot F) : explorateur Nextcloud et disque local du poste (lecture seule à l'affichage)
        from . import fichiers_web56103
        return fichiers_web56103.page_route(env, cfg, auth, prefix, path, args, shell)
    if method == 'GET' and path == '/contentieux':   # 5.6.89 : matrice, pièces, actes, délais, audience, paquets d'un dossier
        from . import contentieux_web5689
        from .shell501 import set_context
        set_context(cfg)
        return contentieux_web5689.page_route(env, cfg, auth, prefix, path, args, shell)
    if method == 'GET' and path in ('/nouveau-document', '/document', '/demandes'):   # 5.6.97 (lot documents-mission, § 13 et § 16) : Nouveau document, versions, demandes
        from . import mission_web5697
        from .shell501 import set_context
        set_context(cfg)
        return mission_web5697.page_route(env, cfg, auth, prefix, path, args, shell)
    if method == 'GET' and path in ('/dossiers/nouveau', '/dossiers/import'):   # 5.6.97 (lot dossiers, § 10) : Nouveau dossier, import des dossiers existants
        from . import dossiers_web5697
        from .shell501 import set_context
        set_context(cfg)
        return dossiers_web5697.page_route(env, cfg, auth, prefix, path, args, shell)
    if method == 'GET' and path in ('/dossier/remplir-pdf', '/dossier/outils-pdf'):   # 5.6.108 (lot F) : remplir un PDF à champs, boîte à outils PDF (lecture seule ; 404 neutre)
        from .shell501 import set_context
        set_context(cfg)
        if path == '/dossier/remplir-pdf':
            from . import pdf_formulaire56108
            return pdf_formulaire56108.page_route(env, cfg, auth, prefix, path, args, shell)
        from . import outils_pdf56108
        return outils_pdf56108.page_route(env, cfg, auth, prefix, path, args, shell)
    if method == 'GET' and path in ('/dossier/apercu', '/dossier/fichier'):   # 5.6.79 : aperçu des documents de la page Dossier
        from . import web5679
        return web5679.route(cfg, auth, prefix, path, args)
    if method == 'GET' and path == '/strategy' and args.get('matter'):   # 5.6.79 : la question stratégique se pose dans le dossier
        target = prefix + '/dossier?' + urlencode({'id': str(args['matter']), 'vue': 'travail'}) + '#d79-strategie'
        return {'status': '303 See Other', 'kind': 'text/html; charset=utf-8', 'body': '', 'headers': [('Location', target)]}
    if path in STATIC and method == 'GET':
        name = path[len('/static/'):]   # 5.6.94 : sous-dossiers (brindille/model, alkove/models) ; seules les adresses de STATIC sont servies
        from .voix5694 import BINAIRES
        if STATIC[path] in BINAIRES or STATIC[path] == 'font/woff2':   # 5.6.94 (V01) : WebAssembly et modèles servis en octets, sans jeu de caractères ; 5.6.103 (lot T) : polices du tableau blanc
            return {'status': '200 OK', 'kind': STATIC[path], 'body': (Path(__file__).parent / 'static' / name).read_bytes()}
        return {'status': '200 OK', 'kind': STATIC[path] + '; charset=utf-8',
                'body': (Path(__file__).parent / 'static' / name).read_text(encoding='utf-8')}
    if not (path in ('/parametres', '/parametres/agents', '/parametres/agendas', '/agents-documents', '/engagements', '/veille', '/parametres/proactivite', '/accueil-administratif', '/missions', '/audience', '/missions-complexes', '/profils', '/parametres/assistant', '/parametres/connexions', '/mise-en-service', '/courriels', '/a-relire', '/documents', '/documents/edit', '/atelier/reglages', '/echeances', '/fiche', '/dossier', '/chronologie', '/verification', '/sources', '/modeles', '/progres', '/autonomie', '/tracabilite', '/recherche', '/confort', '/sw.js', '/hors-ligne', '/pieces', '/diagnostic', '/ia-externe', '/mon-style', '/a-propos', '/guide-installation', '/guide-utilisation', '/competences', '/decision', '/audiences', '/reviser', '/recherche-juridique', '/surveillance') or path in _pages500() or path.startswith('/api440/') or path in _pages5636()):
        return None
    from . import shell501
    shell501.set_context(cfg)
    desk = Desk(cfg)
    try:
        if path.startswith('/api440/'):
            return api(env, desk, auth, prefix, path[len('/api440/'):], args, method)
        if method == 'GET' and path in ('/honoraires', '/contacts-crm', '/dossier'):   # 5.6.109 (lot G1) : mêmes pages, mêmes modules ; sections Préfacturation, Assistance régulière (fiche client), Fonds, frais et débours (dossier) insérées (lecture seule)
            from .gestion56109 import habiller
            habille = habiller(desk, env, prefix, path, args, shell)
            if path == '/dossier':   # 5.6.109 (lot G3, G16) : RIB CARPA documentaire inséré après « Fonds, frais et débours » (lecture seule)
                from .formalites56109 import habiller as habiller_g3
                habille = habiller_g3(desk, env, prefix, path, args, habille)
            if path == '/honoraires':
                from . import web500
                from .web567 import actor
                return page_out(web500.honoraires_page(desk, auth, prefix, args, habille, actor(env)))
            if path == '/contacts-crm':
                from . import pages5636
                out5636 = pages5636.page(desk, auth, prefix, env, args, path, habille)
                return out5636 if isinstance(out5636, dict) else page_out(out5636)
            from . import dossier5627
            try:
                return page_out(dossier5627.page(desk, auth, prefix, args, habille))
            except Stop as ex:
                return page_out(shell('Dossier introuvable', '<h1>Dossier introuvable</h1><p>%s</p>' % e(human(str(ex))), prefix, auth['csrf'], '/dossiers'))
        if method != 'GET':
            return {'status': '405 Method Not Allowed', 'kind': 'text/plain; charset=utf-8', 'body': 'Méthode refusée.'}
        if path in _pages5636():   # 5.6.36 : contacts, initiatives, pièces, outils IA, corporate, relances, complément Word
            from . import pages5636
            out5636 = pages5636.page(desk, auth, prefix, env, args, path, shell)
            return out5636 if isinstance(out5636, dict) else page_out(out5636)
        if path in ('/decision', '/audiences', '/reviser', '/recherche-juridique', '/surveillance'):   # 5.6.34 : parcours simplifiés
            from . import web5634
            if path == '/decision':
                from . import decisions5634
                return page_out(decisions5634.page(desk, auth, prefix, env, args, shell))
            if path == '/audiences':
                return page_out(web5634.audiences_page(desk, auth, prefix, env, args, shell))
            if path == '/reviser':
                return page_out(web5634.reviser_page(desk, auth, prefix, env, args, shell))
            if path == '/recherche-juridique':
                return page_out(web5634.research_page(desk, auth, prefix, env, args, shell))
            state = str(args.get('state') or 'open')
            state = state if state in ('open', 'acknowledged', 'snoozed', 'resolved', 'all') else 'open'
            return page_out(shell('Alertes du cabinet', web5634.alerts_html(desk, prefix, state, auth['csrf']), prefix, auth['csrf'], '/surveillance'))
        from . import parametres5621   # 5.6.21 : concentrateur des réglages (une rubrique par sujet, chaque réglage à un seul endroit)
        if path == '/parametres' or path in parametres5621.ALIASES:
            return page_out(parametres5621.page(desk, auth, prefix, env, args, path))
        if path in ('/parametres/agents','/parametres/agendas','/agents-documents'):
            from .web_rules568 import page
            return page_out(page(desk,auth,prefix,env,path,args))
        if path == '/veille':   # 5.6.32 (R12) : veille planifiée, sourcée ; l'ancienne veille officielle est reprise dans les sources
            from . import web5632
            return page_out(web5632.veille_page(desk, auth, prefix, env, shell))
        if path in ('/engagements','/veille','/parametres/proactivite','/mise-en-service','/audience','/missions-complexes','/profils'):
            from .web568 import page
            env['axiorhub.args']=args
            return page_out(page(desk,auth,prefix,env,path))
        if path in ('/accueil-administratif','/missions','/parametres/assistant','/parametres/connexions'):
            from . import web567
            if path == '/accueil-administratif' and str(args.get('vue') or '') == 'bland':   # 5.6.109 (I2) : onglet « Téléphonie par IA (Bland) » de la famille
                from .bland56109 import page_famille
                return page_out(page_famille(desk,auth,prefix,env))
            if path == '/accueil-administratif':return page_out(web567.reception_page(desk,auth,prefix,env))
            if path == '/missions':return page_out(web567.page(desk,auth,prefix,env))
            if path == '/parametres/connexions':return page_out(web567.connections_page(desk,auth,prefix,env))
            return page_out(web567.preferences_page(desk,auth,prefix,env))
        if path == '/courriels':
            cible = vers_a_relire(desk, env, prefix, args)   # 5.6.102 (lot N) : anciennes adresses d'un élément à relire → /a-relire
            if cible:
                return {'status': '302 Found', 'kind': 'text/plain; charset=utf-8', 'body': '', 'headers': [('Location', cible)]}
            # 5.6.96 : le compte choisit la vue « À traiter avec le Pilote » ; 5.6.106 (lot R) : images collées dans la rédaction (data:, blob:),
            # jamais une source externe (img-src reste sans adresse distante)
            return dict(page_out(drafts_page(desk, auth, prefix, args, env)), csp_img='data: blob:')
        if path == '/a-relire':
            return page_out(relire_page(desk, auth, prefix, args, env))   # 5.6.102 (lot N) : liste unique de relecture, accès principal
        if path == '/documents':
            return page_out(documents_page(desk, auth, prefix, args))
        if path == '/documents/edit':
            if args.get('volet'):     # 5.6.101 (lot V) : éditeur seul, dans le cadre du volet d'Aujourd'hui (droits du document, frame-ancestors 'self')
                from .volet56101 import cadre_page
                return cadre_page(desk, auth, prefix, env, args)
            return editor_page(desk, auth, prefix, args)
        if path == '/guide-installation':   # 5.6.31 (R01) : guide d'installation pas à pas, réservé à l'administrateur
            from . import guide5631
            from .web567 import actor
            if actor(env)[1] != 'administrateur':
                return page_out(shell('Guide d’installation', '<h1>Guide d’installation</h1><p>%s</p>' % e(human('role_insuffisant')), prefix, auth['csrf'], '/parametres'))
            return page_out(guide5631.page(desk, auth, prefix, env, args, shell))
        if path == '/competences':   # 5.6.32 (R16) : compétences de l'IA
            from . import web5632
            return page_out(web5632.skills_page(desk, auth, prefix, env, shell))
        if path == '/guide-utilisation':   # 5.6.31 (R19) : guide d'utilisation de toutes les fonctions
            from . import aide5631
            return page_out(aide5631.page(desk, auth, prefix, env, args, shell))
        if path == '/dossier':   # 5.6.27 : page Dossier refondue
            from . import dossier5627
            try:
                return page_out(dossier5627.page(desk, auth, prefix, args, shell))
            except Stop as ex:
                return page_out(shell('Dossier introuvable', '<h1>Dossier introuvable</h1><p>%s</p>' % e(human(str(ex))), prefix, auth['csrf'], '/dossiers'))
        if path == '/fiche' or path == '/chronologie':
            from . import web460
            try:
                if path == '/fiche':
                    return page_out(web460.fiche_page(desk, auth, prefix, args, shell))
                return page_out(web460.chronologie_page(desk, auth, prefix, args, shell))
            except Stop as ex:
                return page_out(shell('Dossier introuvable', '<h1>Dossier introuvable</h1><p>%s</p>' % e(human(str(ex))), prefix, auth['csrf'], '/dossiers'))
        if path == '/diagnostic':
            from . import web520
            return page_out(web520.diagnostic_page(desk, auth, prefix, args, shell))
        if path == '/ia-externe':
            from . import ia540
            return page_out(ia540.page(desk, prefix, shell, auth['csrf']))
        if path == '/a-propos':
            from . import about560
            return page_out(about560.page(prefix, shell, auth['csrf']))
        if path == '/mon-style':
            from . import style550_ui
            return page_out(style550_ui.page(desk, prefix, shell, auth['csrf']))
        if path == '/pieces':
            from . import web510
            return page_out(web510.page(desk, auth, prefix, args, shell))
        if path in _pages500():
            from . import web500
            if path == '/honoraires':   # 5.6.88 : lignes de temps visibles selon le droit « facturation » du compte
                from .web567 import actor
                return page_out(web500.honoraires_page(desk, auth, prefix, args, shell, actor(env)))
            return page_out(web500.PAGES[path](desk, auth, prefix, args, shell))
        if path == '/sw.js':
            from . import mobile490
            return {'status': '200 OK', 'kind': 'text/javascript; charset=utf-8', 'body': mobile490.service_worker(prefix)}
        if path == '/hors-ligne':
            from . import mobile490
            return page_out(mobile490.offline_page(prefix))
        if path in ('/recherche', '/confort'):
            from . import web490
            page = {'/recherche': web490.recherche_page, '/confort': web490.confort_page}[path]
            return page_out(page(desk, auth, prefix, args, shell))
        if path in ('/progres', '/autonomie', '/tracabilite'):
            from . import web480
            page = {'/progres': web480.progres_page, '/autonomie': web480.autonomie_page, '/tracabilite': web480.tracabilite_page}[path]
            return page_out(page(desk, auth, prefix, args, shell))
        if path in ('/verification', '/sources', '/modeles'):
            from . import web470
            if path == '/verification':
                return page_out(web470.verification_page(desk, auth, prefix, args, shell, allowed_path))
            if path == '/sources':
                return page_out(web470.sources_page(desk, auth, prefix, args, shell))
            return page_out(web470.templates_page(desk, auth, prefix, args, shell))
        if path == '/echeances':
            from . import web450
            return page_out(web450.page(desk, auth, prefix, args, shell))
        return page_out(settings_page(desk, auth, prefix, args))
    finally:
        try:
            desk.db.close()
        except Exception:
            pass


def _pages5636():
    from .pages5636 import PAGES
    return PAGES


def api(env, desk, auth, prefix, name, args, method):
    from . import drafts440, office440, notices440
    if name.startswith('m5636/'):   # 5.6.36 : points à vérifier, demandes écartées, contacts, initiatives, GED, outils, corporate…
        from . import web5636
        try:
            return web5636.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            precision = str((getattr(error, 'detail', None) or {}).get('precision', ''))[:400]
            message = REASONS.get(code) or explain(code, getattr(error, 'detail', None))
            return json_out({'error': code, 'message': message + (' ' + precision if precision else '')},
                            '403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve') else '400 Bad Request')
    if name.startswith('m5652/'):   # 5.6.52 : routines personnalisées, connexions API utilisables par l'IA
        from . import web5652
        try:
            return web5652.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve') else
                            ('409 Conflict' if code == 'version_en_conflit' else
                             ('404 Not Found' if code in ('routine_absente', 'connexion_absente', 'operation_absente', 'element_absent') else '400 Bad Request')))
    if name.startswith('m56102/accord/'):   # 5.6.102 (lot K) : accord du client → documents → projet de courriel (404 neutre, 409, no-store ; jamais d'envoi)
        from . import accord56102
        return accord56102.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56102/pilote/'):   # 5.6.102 (lot M) : Pilote dans la messagerie (présence, Résumer, Annuler, projet du volet, réglage ; 404 neutre, 409, no-store)
        from . import pilote_messagerie56102
        return pilote_messagerie56102.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56103/tableau/'):   # 5.6.103 (lot T) : tableau blanc de la messagerie (scène, versions, images, propositions de Pilote ; 404 neutre, 409, no-store, clé d'idempotence)
        from . import tableau56103
        return tableau56103.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56108/fichiers-dossier/'):   # 5.6.108 (lot F) : ajouter un fichier, remplir un PDF, outils PDF (404 neutre, 409, no-store, clé d'idempotence ; originaux jamais réécrits)
        from . import pdf_formulaire56108
        return pdf_formulaire56108.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56108/editeur/'):   # 5.6.108 (lot W) : séances d'édition (404 neutre, 409, 503, no-store, clé d'idempotence ; jamais d'écrasement)
        from . import wordcraft56108
        return wordcraft56108.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56103/fichiers/'):   # 5.6.103 (lot F) : page « Fichiers » (404 neutre, 409, no-store, clé d'idempotence ; aucune lecture par l'IA)
        from . import fichiers_web56103
        return fichiers_web56103.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56108/sync/'):   # 5.6.108 (lot S) : synchronisation des postes (poste seulement ; 404 neutre, 403, 409, no-store, clé d'idempotence)
        from . import sync56108_web
        return sync56108_web.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56103/liaison/'):   # 5.6.103 (lot L) : liaison entre postes (poste seulement ; 404 neutre, 409, no-store, clé d'idempotence)
        from . import liaison_web56103
        return liaison_web56103.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56103/assistant/'):   # 5.6.103 (lot G) : étapes facultatives Google / Microsoft (état réel, passer, reprendre ; 409, no-store)
        from . import assistant56103
        return assistant56103.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56103/gmail/'):   # 5.6.103 (lot G) : Gmail simple (tester, enregistrer, application interne ; administrateur, 409, no-store)
        from . import gmail_simple56103
        return gmail_simple56103.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56103/microsoft/'):   # 5.6.103 (lot M) : compte Microsoft (application intégrée), brouillons Outlook (404 neutre, 409, no-store ; jamais d'envoi)
        from . import microsoft_web56103
        return microsoft_web56103.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m5676/'):   # 5.6.76 : opportunités et prospects
        from . import crm5676
        try:
            return crm5676.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve') else '400 Bad Request')
    if name == 'm56100/relecture' or name.startswith(('m56100/relecture/', 'm56100/relecture?')):   # 5.6.100 (lot B) : file commune « À relire », lecteur et décisions (404 neutre, 409, 503, no-store)
        from . import relecture56100
        return relecture56100.repondre(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56100/ia/'):   # 5.6.100 (lot G) : note et proposition sourcées après de nouvelles écritures (404 neutre, 409, no-store)
        from . import delta56100
        return delta56100.api(env, desk, auth, prefix, name, args, method)
    if name == 'm56101/securite' or name.startswith('m56101/securite/'):   # 5.6.101 (lot S) : veille de sécurité (administrateur ; 404 neutre, 409, no-store)
        from . import securite_web56101
        return securite_web56101.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56101/aujourdhui/'):   # 5.6.101 (lot P) : fragments d'Aujourd'hui en lecture seule (GET ; 404 neutre, 403 de rôle, no-store)
        from . import aujourdhui56101
        return aujourdhui56101.repondre(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56101/chrono/'):   # 5.6.101 (lot M) : mesure du temps, chaque compte pour lui seul (409, no-store ; jeton CSRF dans le corps de la balise)
        from . import chrono56101
        return chrono56101.repondre(env, desk, auth, prefix, name, args, method)
    if name == 'm56101/usage' or name.startswith('m56101/usage/'):   # 5.6.101 (lot F, F05) : compteur d'usage (POST seulement, 409, no-store ; jeton CSRF dans le corps de la balise)
        from . import usage56101
        return usage56101.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56100f/'):   # 5.6.100 (lot F) : présentation de l'interface (cabinet : administrateur ; compte : chacun pour lui)
        from . import reactivite56100
        try:
            return reactivite56100.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            message = {'version_en_conflit': 'Le réglage a été modifié entre-temps : rechargez la page pour voir le réglage actuel.',
                       'choix_interface_invalide': 'Choix de présentation inconnu.'}.get(code) or REASONS.get(code) or explain(code, getattr(error, 'detail', None))
            return json_out({'error': code, 'message': message},
                            '403 Forbidden' if code == 'role_insuffisant' else ('409 Conflict' if code == 'version_en_conflit' else
                                                                                ('404 Not Found' if code == 'route_inconnue' else '400 Bad Request')))
    if name.startswith('m5688s/'):   # 5.6.88 : double authentification, jetons d'API, journal d'audit, ce que l'IA peut voir
        from . import securite5688
        try:
            return securite5688.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code == 'role_insuffisant' else ('429 Too Many Requests' if code == 'trop_de_tentatives' else '400 Bad Request'))
    if name.startswith('m5675/'):   # 5.6.75 : connecteur Claude (réglages, connexions du compte, compétence et plugin)
        from . import claude5675
        try:
            return claude5675.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code == 'role_insuffisant' else '400 Bad Request')
    if name.startswith('m56108/assistance/'):   # 5.6.108 (lot T) : export texte d'un projet d'Assistance métier (GET ; 404 neutre, 503, no-store ; droit « exporter »)
        from . import assistance56108
        try:
            return assistance56108.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            return {**json_out({'error': code, 'message': 'Contrôle des accès indisponible : rien n’a été exporté.' if code == 'controle_acces_indisponible' else 'Élément introuvable.'},
                               '503 Service Unavailable' if code == 'controle_acces_indisponible' else '404 Not Found'), 'headers': [('Cache-Control', 'private, no-store')]}
    if name.startswith('m56108/questionnaire/'):   # 5.6.108 (lot Q) : questionnaire de l'acte (404 neutre, 409, 503, no-store, clé d'idempotence)
        from . import questionnaires56108
        return questionnaires56108.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56108/signaturepdf/'):   # 5.6.108 (lot Z) : SignaturePDF (réglages, test : administrateur ; dépôt, récupération, clôture : avocat ; 404 neutre, 409, no-store, clé d'idempotence)
        from . import signaturepdf56108
        return signaturepdf56108.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56108/conseil/'):   # 5.6.108 (lot K) : conseil de modèles (annonce, trace, réglages : administrateur ; lancer : avocat ; 404 neutre, 409, no-store, clé d'idempotence)
        from . import conseil56108
        return conseil56108.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56108/ia-locale/'):   # 5.6.108 (lot L) : « Configurer l'IA locale » (administrateur ; détection en GET sans écriture ; 403, 404 neutre, 409, no-store, clé d'idempotence)
        from . import ia_locale56108
        return ia_locale56108.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/fiche/'):   # 5.6.109 (lot D, F04) : fiche Conseil / Contentieux — projection, candidats, qualités par instance, champs, propositions, présentation (404 neutre, 409, 422, 503, no-store, clé d'idempotence)
        from . import projection56109
        return projection56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/honoraires/'):   # 5.6.109 (lot D, F05) : catalogue de prestations — brouillons, publication, import, simulation, proposition (droits relus par le service ; 404 neutre, 409, 422, 503, no-store, clé d'idempotence)
        from . import catalogue56109
        return catalogue56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/reanalyse/'):   # 5.6.109 (lot A, F03) : « Analyser le dossier » depuis la fiche — état, couverture, propositions (GET) ; lancer, arrêter, décider (POST : avocat ; 404 neutre, 409, 503, no-store, clé d'idempotence)
        from . import reanalyse56109
        return reanalyse56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/conso/'):   # 5.6.109 (lot M, F18) : relevé « Consommation et coûts » — lecture seule (JSON, fragment, export CSV filtré) ; chacun ses propres demandes, administrateur : tout le cabinet (404 neutre, 503, no-store)
        from . import gouvernance_web5689
        return gouvernance_web5689.api_consommation(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/sources/'):   # 5.6.109 (lot S, F11) : état des sources publiques (GET) ; « Revérifier » (POST : administrateur ; clé d'idempotence, aucun réseau dans la requête, no-store)
        from . import sources_publiques56109
        return sources_publiques56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/international/'):   # 5.6.109 (lot S, G18-G19) : registre étranger d'une société, documents publics classés (GET) ; identifier, collecter (POST : avocat ; 404 neutre, 409, no-store, clé d'idempotence)
        from . import international56109
        return international56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/historique/'):   # 5.6.109 (lot H, F09) : historique de l'assistant — fils, messages, recherche, export, corbeille (GET sans écriture) ; brouillon, attribution (POST ; 404 neutre, 409, no-store)
        from . import historique56109
        return historique56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/discussions/'):   # 5.6.109 (lot H, F09) : renommer, épingler, grouper, rattacher, partager, révoquer, supprimer, restaurer, politique (POST ; 404 neutre, 409, no-store, clé d'idempotence)
        from . import discussions56109
        return discussions56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/apparence/'):   # 5.6.109 (lot H, F10) : préférence d'apparence du compte (GET ; POST : enregistrer, rétablir ; 409, no-store, clé d'idempotence)
        from . import apparence56109
        return apparence56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/choix-modele/'):   # 5.6.109 (lot T) : modèle fixé et contexte transmis par fonction (administrateur ; 403, 404 neutre, 409, no-store)
        from . import choix_modele56109
        return choix_modele56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/navigateur/'):   # 5.6.109 (lot O, G13 et G24) : modèle du navigateur (administrateur), prise et reprise de main acquittées (poste ; 403, 404 neutre, 409, no-store)
        from . import navigateur56109
        return navigateur56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/livrables/'):   # 5.6.109 (lot V, F08) : livrables structurés du registre commun — trames, contenu, export de données, réconciliation (GET) ; créer, enregistrer, plan (POST ; 404 neutre, 409, no-store, clé d'idempotence)
        from . import artefacts56109
        return artefacts56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/modeles/'):   # 5.6.109 (lot V, F08) : modèles Word du cabinet — liste, aperçu (GET) ; importer, valider, produire et contrôler (POST ; 404 neutre, 409, no-store, clé d'idempotence)
        from . import modele_docx56109
        return modele_docx56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/editeurs/'):   # 5.6.109 (lot V, F16) : éditeurs Craft facultatifs — état, séances, candidats, exports distincts, commandes IA validées, décision (404 neutre, 409, no-store, clé d'idempotence)
        from . import craft56109
        return craft56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/poste/'):   # 5.6.109 (lot O, F12 et G13) : autorisations de mission sur le poste, pause, révocation, appairage du compagnon (404 neutre, 409, no-store, clé d'idempotence)
        from . import poste56109
        return poste56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/bland/'):   # 5.6.109 (lot B) : téléphonie par IA (Bland) — état, outils, fiche (GET sans écriture) ; clé, test, activation, demandes, confirmation de l'avocat, rappels, agents (POST ; 403, 404 neutre, 409, no-store, clé d'idempotence)
        from . import bland56109
        return bland56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/decision/'):   # 5.6.109 (lot K, F07) : organisation locale des demandes — état et questions (GET sans écriture) ; réglage (administrateur), réponse à une question ciblée (403, 404 neutre, 409, no-store, clé d'idempotence)
        from . import decision56109
        return decision56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/simulation/'):   # 5.6.109 (lot N, F14) : simulation de décision du dossier — contexte, traitement (GET sans écriture ni modèle) ; cadrage, simuler (202, file commune), annuler, corriger, conserver (POST : avocat ; 403, 404 neutre, 409, 503, no-store, clé d'idempotence)
        from . import simulation56109
        return simulation56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/fonds/'):   # 5.6.109 (lot Q, G23) : fonds juridique local — état, recherche sans réseau, texte d'une version (GET, trois rôles) ; importer un lot local, avancer, suspendre, reprendre, supprimer ou reconstruire l'index (POST : administrateur ; 409, no-store, clé d'idempotence)
        from . import fonds_juridique56109
        return fonds_juridique56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/fichiers-locaux/'):   # 5.6.109 (lot Q, F13) : fichiers de ce poste — état, recherche, passage cité (GET) ; ajouter un emplacement (poste), indexer, révoquer, interroger (POST ; 404 neutre « absence d'autorisation », 409, no-store, clé d'idempotence)
        from . import contexte56109
        return contexte56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/gemini/'):   # 5.6.109 (lot E, F15) : connexion Google Gemini par clé API — état (GET) ; tester, enregistrer, activer (POST : administrateur ; 403, 409, no-store, clé d'idempotence ; la clé n'est jamais rendue)
        from . import gemini56109
        return gemini56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/gestion/'):   # 5.6.109 (lot G1) : préfacturation, assistance régulière, fonds/frais/débours — vues (GET sans écriture) ; collecte, traces, lot atomique, contrat, périodes, mouvements, coordonnées bancaires (POST ; droits relus par le service ; 404 neutre, 409, 422, no-store, clé d'idempotence)
        from . import gestion56109
        return gestion56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/operations/'):   # 5.6.109 (lot G3) : « À finaliser » (G04), suites et exécution (G05), formalités supervisées INPI, Infogreffe, RIB CARPA, étranger (G14-G16, G20) — vues (GET sans écriture) ; préparer, décider, autoriser un effet précis (avocat), constater (POST ; droits relus par le service ; 403, 404 neutre, 409, 422, no-store, clé d'idempotence ; aucun secret reçu)
        from . import closing56109
        return closing56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith(('m56109/partage/', 'm56109/appels/', 'm56109/remplacements/', 'm56109/kit/')):   # 5.6.109 (lot G2) : partage client (G03), appels (G06), remplacements (G07), kit d'audience hors ligne (G08) — vues (GET sans écriture) ; préparer, confirmer, révoquer, importer un retour, classer, consigner, décider, instruire, transitions, geler un kit, notes, reconnexion (POST ; droits relus par le service ; 403, 404 neutre, 409, no-store, clé d'idempotence)
        from . import appels56109, kit_audience56109, partage_client56109, remplacements56109
        module = {'partage': partage_client56109, 'appels': appels56109, 'remplacements': remplacements56109, 'kit': kit_audience56109}[name.split('/')[1]]
        return module.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56109/parcours/'):   # 5.6.109 (lot G4) : offre et PDF joint au brouillon (G10), collecte supervisée d'un portail et routine (G11-G12), documents publics Pappers (G17) — vues (GET sans écriture) ; préparer, profils, lancer, déposer, routine, politique, collecter (POST ; droits relus par le service ; 403, 404 neutre, 409, no-store, clé d'idempotence)
        from . import parcours56109
        return parcours56109.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m5674/'):   # 5.6.74 : téléphones associés par QR code (version serveur)
        from . import appareils5674
        try:
            return appareils5674.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))}, '400 Bad Request')
    if name.startswith('m5673/'):   # 5.6.73 : navigateur supervisé (poste)
        from . import web5673
        try:
            return web5673.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve') else '400 Bad Request')
    if name.startswith('m5672/'):   # 5.6.72 : test du modèle local d'IA (bandeau « injoignable »)
        from . import ia5672
        try:
            return ia5672.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))}, '400 Bad Request')
    if name.startswith('m5671/'):   # 5.6.71 : réponses préparées en attente (déposer, écarter), réponses « sans dossier »
        from . import web5671
        try:
            return web5671.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve') else
                            ('404 Not Found' if code == 'introuvable' else
                             ('503 Service Unavailable' if code == 'controle_indisponible' else   # 5.6.100 (R04)
                              ('409 Conflict' if code == 'version_perimee' else '400 Bad Request'))))
    if name.startswith('m5670/'):   # 5.6.70 : facturation électronique, courriels traités par lot, fichiers Nextcloud, visio, sécurité
        from . import web5670
        try:
            return web5670.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve') else '400 Bad Request')
    if name.startswith('m5668/'):   # 5.6.68 : plafond de l'IA externe, dossiers exclus des missions automatiques
        from . import web5668
        try:
            return web5668.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code == 'role_insuffisant' else '400 Bad Request')
    if name.startswith('m5667/'):   # 5.6.67 : corrections récurrentes, répartition des modèles, banc d'essai
        from . import web5667
        try:
            return web5667.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code == 'role_insuffisant' else '400 Bad Request')
    if name.startswith('m5662/'):   # 5.6.62 : connexion ChatGPT (abonnement)
        from . import web5662
        try:
            return web5662.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code == 'role_insuffisant' else '400 Bad Request')
    if name.startswith('m5661/'):   # 5.6.61 : mise à jour signée depuis la page des versions
        from . import web5661
        try:
            return web5661.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code == 'role_insuffisant' else ('409 Conflict' if code == 'mise_a_jour_deja_demandee' else '400 Bad Request'))
    if name.startswith('m5660/'):   # 5.6.60 : actions du diagnostic de la file
        from . import web5660
        try:
            return web5660.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code == 'role_insuffisant' else ('404 Not Found' if code == 'operation_absente' else '400 Bad Request'))
    if name.startswith('m5659/'):   # 5.6.59 : savoir-faire du cabinet et sécurité de l'IA
        from . import web5659
        try:
            return web5659.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve') else
                            ('409 Conflict' if code == 'version_deja_decidee' else ('404 Not Found' if code.endswith('_absent') or code.endswith('_absente')
                                                                                   else '400 Bad Request')))
    if name.startswith('m5657/'):   # 5.6.57 : parcours de la 5.6.57
        from . import web5657
        try:
            return web5657.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve') else
                            ('409 Conflict' if code == 'version_en_conflit' else ('404 Not Found' if code.endswith('_absent') or code.endswith('_absente')
                                                                                 else '400 Bad Request')))
    if name.startswith('m5688cloud/'):   # 5.6.88 : stockages cloud, copie, nommage, cloisonnement (administrateurs)
        from . import web_cloud5688
        return web_cloud5688.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m5653/'):   # 5.6.53 : facturation et honoraires (registre unique, chiffrage, factures structurées)
        from . import web5653
        try:
            return web5653.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve') else
                            ('409 Conflict' if code in ('version_en_conflit', 'cle_idempotence_deja_utilisee', 'numerotation_concurrente', 'diligence_deja_reservee')
                             else ('404 Not Found' if code in ('document_financier_absent', 'estimation_absente', 'modele_absent', 'element_absent') else '400 Bad Request')))
    if name.startswith('m57/dossiers'):   # 5.6.97 (lot dossiers) : registre unique des affaires (201, 422, 409, 404 neutre, PATCH avec version)
        from . import dossiers_web5697
        return dossiers_web5697.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m5689/'):   # 5.6.89 : routes par domaine
        from . import web5689
        return web5689.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m5688/'):   # 5.6.88 : palette, registres publics, veille des sociétés, page personnelle, note commune, mémoire
        from . import web5688
        return web5688.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('rev5688/'):   # 5.6.88 : sauvegarde, export intégral, vérification (administrateur)
        from . import reversibilite5688
        return reversibilite5688.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m5684/'):   # 5.6.84 : cessions de fonds et de bail, modèles, panneau de l'éditeur
        from . import web5684
        return web5684.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m5683/'):   # 5.6.83 : cessions de parts et actions, compétences, formulaires, règles datées
        from . import web5683
        return web5683.api(env, desk, auth, prefix, name, args, method)
    if name.startswith(('m5682/', 'q5682/')):   # 5.6.82 : mode de l'IA, catalogue, liaison poste-serveur, recherche rapide
        from . import web5682
        try:
            return web5682.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code == 'role_insuffisant' else ('409 Conflict' if code == 'reglage_modifie_entre_temps' else '400 Bad Request'))   # 5.6.96 : clé Mistral, révision périmée
    if name.startswith('m5681/'):   # 5.6.81 : liste des modèles d'un fournisseur, profil Mixte
        from . import web5681
        try:
            return web5681.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .ai_gateway import ERROR_MESSAGES
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': ERROR_MESSAGES.get(code) or REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve') else ('409 Conflict' if code == 'reglage_modifie_entre_temps' else '400 Bad Request'))
    if name.startswith('m5680/'):   # 5.6.80 : passages trouvés sans modèle, affichés pendant que l'IA répond
        from . import web5680
        try:
            return web5680.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve') else '400 Bad Request')
    if name.startswith('m5679/'):   # 5.6.79 : question stratégique, nouveau courrier, correspondant repéré (page Dossier)
        from . import web5679
        try:
            return web5679.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve', 'adresse_du_cabinet') else
                            ('409 Conflict' if code == 'analyse_strategique_deja_en_cours' else
                             ('404 Not Found' if code.endswith('_absent') or code.endswith('_absente') or code == 'operation_absente' else '400 Bad Request')))
    if name.startswith('m5651/'):   # 5.6.51 : page Dossier refondue et fonctions d'autonomie
        from . import web5651
        try:
            return web5651.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve') else
                            ('409 Conflict' if code == 'version_en_conflit' else
                             ('404 Not Found' if code in ('element_absent', 'regle_absente', 'information_absente', 'partie_absente',
                                                          'proposition_absente', 'relecture_absente', 'lacune_absente', 'decision_absente',
                                                          'procedure_absente', 'document_absent') else '400 Bad Request')))
    if name.startswith('m5650/'):   # 5.6.50 : messagerie interne, droits par compte, dossiers réservés
        from . import web5650
        try:
            return web5650.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve') else '400 Bad Request')
    if name.startswith('m5644/'):   # 5.6.44 : corrections de l'avocat dans la page du dossier
        from . import web5644
        try:
            return web5644.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve') else '400 Bad Request')
    if name.startswith('m5643/'):   # 5.6.43 (cahier v9, § 10-14) : courriels et pièces conservés dans le dossier client
        from . import web5643
        try:
            return web5643.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            from .web import REASONS
            return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))},
                            '403 Forbidden' if code == 'role_insuffisant' else ('409 Conflict' if code in ('proposition_obsolete', 'reglage_modifie_entre_temps',
                                                                                                         'classement_modifie_entre_temps') else '400 Bad Request'))
    if name.startswith('m5634/'):   # 5.6.34 : sélecteurs, décisions, alertes, audiences, révision, recherche, agenda local, profil du modèle
        from . import web5634
        try:
            return web5634.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .erreurs5634 import explain
            return json_out({'error': code, 'message': explain(code, getattr(error, 'detail', None))}, '403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve') else '400 Bad Request')
    if name.startswith('m5632/'):   # 5.6.32 : sources, contrôle des références, veille, compétences, documents de procédure
        from . import web5632
        try:
            return web5632.api(env, desk, auth, prefix, name, args, method)
        except Stop as error:
            code = str(error).split(':')[0]
            from .guide5631 import message
            return json_out({'error': code, 'message': message(code)}, '403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve') else
                            ('409 Conflict' if code == 'revision_source_perimee' else '400 Bad Request'))   # 5.6.97 (J06) : fiche modifiée entre-temps
    if name.startswith('m5631/'):   # 5.6.31 : guide d'installation (sessions, étapes, preuves) et choix de la langue
        from . import guide5631
        try:
            if name == 'm5631/langue':
                return guide5631.lang_api(env, desk, auth, name, method)
            if name.startswith('m5631/guide/'):
                return guide5631.api(env, desk, auth, prefix, name, args, method)
            raise Stop('route_inconnue')
        except Stop as error:
            code = str(error).split(':')[0]
            return json_out({'error': code, 'message': guide5631.message(code)}, '403 Forbidden' if code in ('role_insuffisant', 'dossier_reserve') else '400 Bad Request')
    if name.startswith('m56106/redaction/'):   # 5.6.106 (lot R) : modèles de réponse du cabinet (404 neutre, 409, no-store, clé d'idempotence)
        from . import redaction56106
        return redaction56106.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56106/envoi/'):   # 5.6.106 (lot E) : file d'envoi (confirmer, annuler, envoyer maintenant, modifier ; 404 neutre, 409, no-store, clé d'idempotence)
        from . import envoi56106
        return envoi56106.api(env, desk, auth, prefix, name, args, method)
    if name.startswith(('m56106/identite/', 'm56106/densite')):   # 5.6.106 (lot D) : logo du cabinet (administrateur), densité d'affichage par personne (404 neutre, 409, no-store, clé d'idempotence)
        from . import identite56106
        return identite56106.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56106/boite/'):   # 5.6.106 (lot L) : actions groupées, dossiers IMAP, sous-dossier du dossier client (404 neutre, 409, 503, no-store, clé d'idempotence)
        from . import boite56106
        return boite56106.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56106/ocr/'):   # 5.6.106 (lot W) : langues de lecture des scans (ajouter, retirer, choisir ; administrateur, 409, no-store, clé d'idempotence)
        from . import outils_ocr56106
        return outils_ocr56106.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56106/vision/'):   # 5.6.106 (lot I) : fichiers, pages, vignette, aperçu masqué, lecture d'une page, réglages (404 neutre, 409, no-store, clé d'idempotence)
        from . import vision56106
        return vision56106.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56106/lecteur/'):   # 5.6.106 (lot V) : lecteur (HTML isolé, impression, .eml, pièces, fil), résumé par l'IA, courriel → tâche ou agenda (404 neutre, 409, 503, no-store, clé d'idempotence)
        from . import lecteur56106
        return lecteur56106.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56106/routine/'):   # 5.6.106 (lot O) : routines sur message (assistant, aperçu, propositions ; 404 neutre, 409, no-store, clé d'idempotence)
        from . import routine_message56106
        return routine_message56106.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56106/avocatfr/'):   # 5.6.106 (lot P) : adresse @avocat.fr (tester sans envoi, enregistrer ; administrateur, 409, no-store, clé d'idempotence)
        from . import avocatfr56106
        return avocatfr56106.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56107/demo/'):   # 5.6.107 (lot M) : mode Démo — état, recommencer, quitter, lancer la démo depuis le poste (404 neutre hors démo, no-store, clé d'idempotence)
        from . import demo56107
        return demo56107.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m56106/fraude/'):   # 5.6.106 (lot F) : alerte anti-fraude — indésirable, « Je connais cet expéditeur », « Ce n'est pas une fraude », réglages (404 neutre, 409, no-store, clé d'idempotence)
        from . import fraude56106
        return fraude56106.api(env, desk, auth, prefix, name, args, method)
    if name.startswith('m5628/'):   # 5.6.28 : messagerie (répondre, transférer, archiver, déplacer, supprimer, envoyer)
        from .webmail5628 import api as api5628
        try:return api5628(env,desk,auth,prefix,name,args,method)
        except Stop as error:   # 5.6.100 (R04) : courriel absent ou interdit → 404 neutre ; contrôle en panne → 503
            code = str(error).split(':')[0]
            return json_out({'error':code,'message':'Courriel introuvable.' if code == 'introuvable' else human(code)},STATUTS_GARDE100.get(code,'400 Bad Request'))
    if name.startswith('m5627/'):   # 5.6.27 : page Dossier (fiche de travail, corrections, instructions à l'agent)
        from .dossier5627 import api as api5627
        try:return api5627(env,desk,auth,prefix,name,args,method)
        except Stop as error:return json_out({'error':str(error).split(':')[0],'message':human(str(error).split(':')[0])},'400 Bad Request')
    if name.startswith('m5625/'):   # 5.6.25 : boîte de réception (Courriels) et ajout d'événements (Agenda)
        from .web5625 import api as api5625
        try:return api5625(env,desk,auth,prefix,name,args,method)
        except Stop as error:
            code = str(error).split(':')[0]
            if code == 'introuvable':
                return json_out({'error': code, 'message': 'Courriel introuvable.'}, '404 Not Found')
            if code == 'controle_indisponible':   # 5.6.100 (R04) : panne du contrôle : 503, rien n'est montré
                return json_out({'error': code, 'message': human(code)}, '503 Service Unavailable')
            return json_out({'error':code,'message':human(code)+((' — '+str(error).split(':',1)[1].strip()) if ':' in str(error) else '')},'400 Bad Request')
    if name.startswith('m568/'):
        from .web568 import api as api568, human as human568
        try:return api568(env,desk,auth,prefix,name,args,method)
        except Stop as error:return json_out({'error':str(error),'message':human568(str(error))},'400 Bad Request')
    if name.startswith('m567/'):
        from .web567 import api as api567
        try:
            return api567(env,desk,auth,prefix,name,args,method)
        except Stop as error:
            return json_out({'error':str(error),'message':human(str(error))},'400 Bad Request')
    mail = desk.c['mail']
    try:
        if method == 'GET':
            if name == 'drafts':   # 5.6.100 (R04) : chaque brouillon résolu séparément (origine sur en-têtes) avant la réponse
                from . import acces_courriel56100 as ac100
                from .web567 import actor
                return json_out(ac100.filtrer_brouillons(desk, actor(env), mail, drafts440.list_drafts(mail, int(args.get('limit', 40) or 40))))
            if name == 'draft':
                from .reponses5671 import mail_of_draft      # 5.6.87 : lien « Relancer avec une autre IA »
                from . import acces_courriel56100 as ac100
                from .web567 import actor
                draft5687 = drafts440.get_draft(mail, args.get('uid', ''), args.get('validity', ''), garde=ac100.garde_brouillon(desk, actor(env), mail))
                return json_out({**draft5687, 'mail_key': mail_of_draft(desk, draft5687.get('agent_key'))})
            if name == 'office/config':
                path = allowed_path(desk, args.get('path', ''))
                mode = 'view' if args.get('mode') == 'view' else 'edit'
                from .web567 import actor
                owner5684, role5684 = actor(env)   # 5.6.84 : la personne connectée, plus l'identité fixe « cabinet »
                opened = office440.open_document(desk, auth, path, user_name=owner5684 if owner5684 != 'cabinet' else desk.c['mail'].get('from_name', 'Cabinet'), mode=mode, user_id=owner5684, role=role5684)
                return json_out(opened)
            if name == 'office/diagnostic':
                return json_out({'checks': office440.diagnostic(desk, auth)})
            if name == 'assist':
                from .integration import thread_messages
                messages = thread_messages(desk, args.get('thread', ''))
                answers = [m for m in messages if m['role'] == 'assistant']
                last = answers[-1] if answers else None
                return json_out({'status': last['status'] if last else 'queued',
                                 'text': last['content'] if last and last['status'] == 'done' else '',
                                 'error': last['content'] if last and last['status'] == 'error' else ''})
            if name in ('sources/status', 'sources/log', 'templates'):
                from . import web470
                return json_out(web470.handle(desk, name, {}, 'GET', args))
            if name in ('search/query', 'search/status', 'send/status', 'send/link', 'mobile/summary', 'mobile/status'):
                from . import web490
                from .web567 import actor
                return json_out(web490.handle(desk, name, {}, 'GET', {**args, '_owner': actor(env)[0]}))   # 5.6.88 : rappels de la personne
            if name.startswith('m500/'):
                from . import web500
                from .web567 import actor
                return json_out(web500.handle(desk, name, {}, 'GET', args, actor(env)))
            if name.startswith('m510/'):
                from . import web510
                return json_out(web510.handle(desk, name.split('?', 1)[0], {}, 'GET', args))
            if name.startswith('m520/'):
                from . import web520
                return json_out(web520.handle(desk, name, {}, 'GET', args))
            if name.startswith('m540/'):
                from . import ia540
                return json_out(ia540.handle(desk, name.split('?', 1)[0], {}, 'GET', args))
            if name.startswith('m550/'):
                from . import style550_ui
                return json_out(style550_ui.handle(desk, name.split('?', 1)[0], {}, 'GET', args))
            if name.startswith('m530/'):
                from . import cockpit530
                return json_out(cockpit530.handle(desk, name.split('?', 1)[0], {}, 'GET', {**args, 'prefix': prefix}))
            if name in ('learning/dashboard', 'trace/view', 'trace/export', 'autonomy/status'):
                from . import web480
                return json_out(web480.handle(desk, name, {}, 'GET', args))
            if name in ('deadlines', 'deadline/journal'):
                from . import web450
                return json_out(web450.handle(desk, name, {}, 'GET', args))
            raise Stop('route_inconnue')
        if method != 'POST':
            raise Stop('methode_refusee')
        data = check_post(env, auth)
        if name in ('draft/save', 'draft/discard', 'draft/assist'):   # 5.6.100 (R04) : droits recontrôlés à chaque écriture, avant le corps
            from . import acces_courriel56100 as ac100
            from .web567 import actor
            garde100 = ac100.garde_brouillon(desk, actor(env), mail)
        if name == 'draft/save':
            result = drafts440.save_draft(mail, data, garde=garde100)
            _learn(desk, data, result)
            desk.audit('brouillon_440_modifie', {'uid': result['uid'], 'agent': bool(result.get('agent_key'))})
            return json_out(result)
        if name == 'draft/discard':
            result = drafts440.discard_draft(mail, data.get('uid', ''), data.get('uidvalidity', ''), data.get('confirm', ''), garde=garde100)
            desk.audit('brouillon_440_ecarte', {'uid': str(data.get('uid', ''))[:20]})
            return json_out(result)
        if name == 'draft/assist':
            from .integration import submit_question
            ac100.exiger_contexte(desk, actor(env), str(data.get('source_key', '')), str(data.get('matter', '')))   # le dossier ou le courriel de l'écran ne donne aucun droit
            instruction = str(data.get('instruction', '')).strip()
            body = str(data.get('body', ''))
            if not instruction or not body.strip():
                raise Stop('instruction_et_texte_requis')
            question = ('Réécris ce projet de courriel d’avocat selon cette consigne : ' + instruction +
                        '\nNe renvoie que le texte complet du courriel corrigé, en français, sans commentaire, '
                        'sans inventer de fait ni de date absents du projet ou du dossier.\n\nProjet actuel :\n' + body)
            attachments=[]
            if len(question)>12000:
                from .improvements36 import upload_attachment
                attached=upload_attachment(desk,body.encode('utf-8'),'brouillon-a-reviser.txt',
                  matter=str(data.get('matter','')),key=str(data.get('source_key','')))
                attachments=[attached['attachment_id']]
                question='Réécris le projet joint selon cette consigne, sans changer les faits ni les dates : '+instruction
            submitted = submit_question(desk,question,str(data.get('matter','')),str(data.get('source_key','')),attachment_ids=attachments)
            return json_out({'job_id': submitted['job_id'], 'thread': submitted['thread_id']})
        if name == 'settings/office/test':   # 5.6.108 (lot O) : « Tester la connexion » étape par étape (administrateur, CSRF ; aucune donnée de dossier)
            return json_out({'checks': office440.diagnostic(desk, auth, reach=True)})
        if name == 'settings/office':
            result = office440.save_settings(desk, data.get('server_url', ''), data.get('secret', ''),
                                             data.get('callback_base', ''), data.get('engine', 'onlyoffice'))
            if data.get('clear_secret') is True and not data.get('secret'):
                office440.clear_secret(desk)
            return json_out(result)
        if name == 'settings/notices':
            desk.setting('automation:notices440_enabled', bool(data.get('enabled')))
            desk.setting('automation:notice_calendar440', bool(data.get('calendar')))
            desk.audit('avis_procedure_440_reglages', {'enabled': bool(data.get('enabled')), 'calendar': bool(data.get('calendar'))})
            return json_out({'saved': True})
        if name == 'settings/matter':
            return json_out(notices440.save_matter_profile(desk, str(data.get('matter', '')), str(data.get('role', '')),
                                                           data.get('partner_email', ''), data.get('partner_name', '')))
        if name == 'notice/analyze':
            path = allowed_path(desk, data.get('path', ''))
            matter = str(data.get('matter', ''))
            if not notices440.looks_like_notice(path):
                raise Stop('document_non_reconnu_comme_avis')
            info = office440.dav_client(desk).stat(path)
            job = desk.enqueue('analyze_notice440', {'matter': matter, 'path': path,
                                                     'etag': info['etag'] + '#manuel-' + desk.now()}, priority=0)
            return json_out({'job_id': job})
        if name.startswith('fact/') or name.startswith('fiche/'):
            from . import web460
            result = web460.handle(desk, name, data)
            desk.audit('fiche_460_api', {'route': name})
            return json_out(result)
        if name.startswith('m500/'):
            from . import web500
            from .web567 import actor
            result = web500.handle(desk, name, data, viewer=actor(env))
            desk.audit('metier_500_api', {'route': name})
            return json_out(result)
        if name.startswith('m520/'):
            from . import web520
            return json_out(web520.handle(desk, name, data))
        if name.startswith('m530/'):
            from . import cockpit530
            return json_out(cockpit530.handle(desk, name, data, 'POST', {'prefix': prefix}))
        if name.startswith('m540/'):
            from . import ia540
            return json_out(ia540.handle(desk, name, data, 'POST'))
        if name.startswith('m550/'):
            from . import style550_ui
            return json_out(style550_ui.handle(desk, name, data, 'POST'))
        if name.startswith('m510/'):
            from . import web510
            result = web510.handle(desk, name, data)
            desk.audit('pieces_510_api', {'route': name})
            return json_out(result)
        if name.startswith(('voice/', 'send/', 'search/', 'mobile/')):
            from . import web490
            from .web567 import actor
            if name in ('send/prepare', 'send/confirm'):   # 5.6.100 (R04) : brouillon et origine recontrôlés à l'aperçu et à l'effet
                from .acces_courriel56100 import garde_envoi
                garde_envoi(desk, actor(env), mail, web490.brouillon_du_jeton(desk, data.get('token')) if name == 'send/confirm' else data)
            result = web490.handle(desk, name, {**data, '_owner': actor(env)[0]})   # 5.6.88 : abonnement rattaché au compte
            desk.audit('confort_490_api', {'route': name})
            return json_out(result)
        if name.startswith(('learning/', 'tone/', 'rules/', 'autonomy/')):
            from . import web480
            result = web480.handle(desk, name, data)
            desk.audit('apprentissage_480_api', {'route': name})
            return json_out(result)
        if name.startswith(('citations/', 'review/', 'templates/', 'sources/')):
            from . import web470
            if name in ('citations/check', 'review/run') and data.get('opponent_paths'):
                data['opponent_paths'] = [allowed_path(desk, p) for p in data['opponent_paths'][:10]]
            result = web470.handle(desk, name, data)
            desk.audit('verification_470_api', {'route': name})
            return json_out(result)
        if name.startswith('deadline/') or name == 'settings/deadlines':
            from . import web450
            from .web567 import actor
            try:   # 5.6.96 (D03, D05) : l'auteur vient de la session, jamais du corps ; page périmée → 409
                result = web450.handle(desk, name, {**data, '_owner': actor(env)[0]})
            except Stop as ex:
                if str(ex) != 'version_en_conflit':
                    raise
                return json_out({'error': str(ex), 'message': human(str(ex))}, '409 Conflict')
            desk.audit('echeance_450_api', {'route': name})
            return json_out(result)
        raise Stop('route_inconnue')
    except Stop as ex:
        # 5.6.100 (R04) : absent ou interdit → même 404 neutre ; contrôle des droits en panne → 503
        return json_out({'error': str(ex), 'message': human(str(ex))}, STATUTS_GARDE100.get(str(ex), '400 Bad Request'))


STATUTS_GARDE100 = {'introuvable': '404 Not Found', 'controle_indisponible': '503 Service Unavailable',
                    # 5.6.100 (lot B, R09) : brouillon IMAP modifié depuis son ouverture = même conflit de version que le lecteur commun
                    'brouillon_modifie_depuis_ouverture': '409 Conflict', 'version_perimee': '409 Conflict'}


def _learn(desk, data, result):
    """Remember how the lawyer corrected an agent draft (style learning)."""
    try:
        if not result.get('agent_key'):
            return
        from .learning410 import record_review
        original = str(result.get('original_body', ''))
        corrected = str(data.get('body', ''))
        same = ' '.join(original.split()) == ' '.join(corrected.split())
        record_review(desk, result.get('agent_key', '')[:64], '', 'mail_drafting',
                      'accepted' if same else 'modified', original, corrected)
    except Exception as ex:  # learning must never block saving
        try:
            desk.audit('apprentissage_440_non_enregistre', {'reason': str(ex)[:100]})
        except Exception:
            pass


# ------------------------------------------------------------------- pages
def drafts_page(desk, auth, prefix, args, env=None):
    """Courriels : « Boîte de réception » (défaut, ``?vue=boite`` conservé) et, si l'option est active, « À traiter avec le Pilote »
    (``?vue=assistante``, 5.6.96). 5.6.102 (lot N) : avec la nouvelle interface, « À relire » est un accès principal distinct
    (``/a-relire``, ``relire_page``) ; en présentation historique (interrupteur ``interface56100``), Courriels garde son onglet « À relire »
    par défaut, comme avant. 5.6.96 (I03) : la vue est choisie **avant** toute lecture ; seule « À relire » lit les brouillons dans la
    messagerie (``drafts440.list_drafts``) : la boîte de réception ne consulte plus les brouillons pour un panneau caché, et un échec de cette
    lecture ne ralentit ni ne bloque les autres vues. Onglets, adresses, bannières et éditeur restent ceux de leur vue d'origine."""
    from .web567 import actor
    owner, role = actor(env or {})
    vue = str(args.get('vue') or '')
    from . import courriels5696, interface56100
    assistee_active = courriels5696.active(desk, owner)
    separe = interface56100.active(desk, owner)
    vue = vue if vue in ('boite', 'assistante') else ''
    if vue == 'assistante' and not assistee_active:
        vue = ''                       # option désactivée : l'usage courant, sans message d'erreur
    if separe and not vue:
        vue = 'boite'                  # 5.6.102 : la boîte de réception est la page Courriels
    tabs = _courriels_onglets(prefix, vue, assistee_active, separe)
    head = ''
    if vue == 'boite' and (desk.c.get('mail') or {}).get('enabled', True) is False:
        # 5.6.102 (lot N) : messagerie non choisie dans le guide : état réel affiché, aucune lecture IMAP tentée par le script de la boîte
        body = tabs + ('<section class="ax-empty" id="bx5625-desactivee"><h1>Boîte de réception</h1><p>La messagerie n’est pas activée pour AxiorHub : '
                       'aucun courriel n’est lu.</p><p><a class="ax-btn ghost" href="%s">Configurer la messagerie</a> '
                       '<a class="ax-btn ghost" href="%s">Ouvrir « À relire »</a></p></section>') % (
            e(prefix + '/guide-installation?etape=messagerie', quote=True), e(prefix + '/a-relire', quote=True))
    elif vue == 'boite':
        body = tabs + _courriels_boite()
        if separe:   # 5.6.106 (lot L) : liste dense, actions groupées, dossiers, raccourcis (présentation historique inchangée)
            from .boite56106 import monter as _monter56106
            body, head = _monter56106(body, head, prefix, desk, owner)
        if separe:   # 5.6.106 (lot V) : lecteur mis en forme isolé, impression, .eml, fil ; résumé par l'IA ; courriel → tâche ou agenda
            from .lecteur56106 import tete_html as _tete56106v; head += _tete56106v(prefix)
        if separe:   # 5.6.106 (lot O) : bouton « Créer une routine à partir du message » (barre du lecteur) et son assistant
            from .routine_message56106 import tete_html as _tete56106o; head += _tete56106o(prefix)
        from .fraude56106 import tete_html as _tete56106f; head += _tete56106f(prefix)   # 5.6.106 (lot F) : alerte « courriel potentiellement frauduleux » (les deux présentations)
    elif vue == 'assistante':
        from . import courriels_web5696
        body = courriels_web5696.page(desk, prefix, args, owner, role, tabs)
        head = '<script defer src="' + e(prefix) + '/static/v5696-courriels.js"></script>'
    else:                              # présentation historique : « À relire » reste la vue par défaut de Courriels
        from .web520 import mail_pipeline_html
        body = mail_pipeline_html(desk, prefix, compact=True) + tabs + _courriels_a_relire(desk, prefix)
    return shell('Courriels', body, prefix, auth['csrf'], '/courriels', head=head)


def relire_page(desk, auth, prefix, args, env=None):
    """5.6.102 (lot N) : page « À relire » (``/a-relire``), sortie de Courriels. Nouvelle interface : liste unique et lecteur commun de la
    5.6.100 (``relecture56100.page_html``, mêmes droits : un élément interdit ou absent ne s'affiche pas, la route de détail répond le même
    404 neutre). Présentation historique : l'ancienne vue « À relire » de Courriels, avec ses onglets (aucune entrée de menu distincte)."""
    from .web567 import actor
    owner, role = actor(env or {})
    from .web520 import mail_pipeline_html
    from . import interface56100
    if interface56100.active(desk, owner):
        from . import relecture56100
        body = mail_pipeline_html(desk, prefix, compact=True) + relecture56100.page_html(desk, prefix, (owner, role), args)
        return shell('À relire', body, prefix, auth['csrf'], '/a-relire', head=relecture56100.tete_html(prefix))
    from . import courriels5696
    tabs = _courriels_onglets(prefix, '', courriels5696.active(desk, owner), False)
    return shell('Courriels', mail_pipeline_html(desk, prefix, compact=True) + tabs + _courriels_a_relire(desk, prefix), prefix, auth['csrf'], '/courriels')


# 5.6.102 (lot N) : paramètres d'un élément à relire repris par la redirection des anciennes adresses de Courriels (rien d'autre n'est recopié).
RELIRE_PARAMS = ('item', 'source', 'q', 'uid', 'validity', 'return_to')


def vers_a_relire(desk, env, prefix, args):
    """Adresse ``/a-relire`` d'une ancienne adresse de Courriels qui visait « À relire » (marque-pages, notifications, liens d'Aujourd'hui,
    du volet et des fiches) : ``?vue=relire``, ou ``?item=``, ``?source=``, ``?uid=`` hors des vues Boîte et Pilote. '' sinon, ou en
    présentation historique (Courriels y garde « À relire »). La cible ne redirige jamais vers Courriels : aucune boucle possible."""
    vue = str(args.get('vue') or '')
    if vue != 'relire' and (vue in ('boite', 'assistante') or not any(args.get(k) for k in ('item', 'source', 'uid'))):
        return ''
    from .web567 import actor
    from . import interface56100
    if not interface56100.active(desk, actor(env or {})[0]):
        return ''
    garde = [(k, str(args[k])[:600]) for k in RELIRE_PARAMS if args.get(k)]
    return prefix + '/a-relire' + ('?' + urlencode(garde) if garde else '')


def _courriels_onglets(prefix, vue, assistee_active, separe=False):
    """Onglets de Courriels : mêmes adresses qu'avant ; le troisième n'existe que si l'option est active. 5.6.102 (lot N) : avec « À relire »
    en accès distinct (``separe``), la boîte de réception est le premier onglet (``/courriels``) et la barre n'apparaît qu'avec l'option
    « À traiter avec le Pilote » ; le passage de Courriels à « À relire » se fait par la barre de la famille Courriels."""
    if separe:
        if not assistee_active:
            return ''
        onglets = [('boite', prefix + '/courriels', 'Boîte de réception')]
    else:
        onglets = [('', prefix + '/courriels', 'À relire'), ('boite', prefix + '/courriels?vue=boite', 'Boîte de réception')]
    if assistee_active:
        onglets.append(('assistante', prefix + '/courriels?vue=assistante', 'À traiter avec le Pilote'))
    return '<nav class="bx5625-tabs" aria-label="Courriels">%s</nav>' % ''.join(
        '<a href="%s"%s>%s</a>' % (e(url, quote=True), ' aria-current="page"' if cle == vue else '', e(texte)) for cle, url, texte in onglets)


def _courriels_boite():
    """Vue « Boîte de réception » (5.6.25) : remplie par ``v5625.js`` depuis ses routes ; aucune lecture IMAP au rendu de la page."""
    return ('<section class="bx5625" id="bx5625" aria-label="Messagerie">'
            '<nav class="bx5625-folders" aria-label="Dossiers de la messagerie"><h2>Dossiers</h2>'
            '<div id="bx5625-flist"><p class="ax-muted">Lecture des dossiers…</p></div></nav>'
            '<div class="bx5625-list"><div class="bx5625-head"><h1 id="bx5625-title">Boîte de réception</h1>'
            '<p class="ax-muted">Du plus récent au plus ancien ; rien n’est marqué comme lu.</p>'
            '<form class="bx5625-search" role="search"><input type="search" name="q" placeholder="Rechercher par objet ou expéditeur" aria-label="Rechercher dans ce dossier">'
            '<button class="ax-btn ghost" type="submit">Rechercher</button></form></div>'
            '<div class="bx5625-rows" id="bx5625-rows"><p class="ax-muted ax-pad">Lecture de la boîte…</p></div>'
            '<div class="bx5625-pages" id="bx5625-pages"></div></div>'
            '<div class="bx5625-panel" id="bx5625-panel" aria-live="polite"><div class="ax-empty"><h2>Choisissez un courriel</h2>'
            '<p>Vous verrez son texte et ses pièces jointes, les réponses déjà préparées (à modifier, supprimer ou relancer) et pourrez demander '
            'une réponse à l’agent, avec votre instruction. <strong>Rien n’est envoyé</strong>.</p></div></div></section>')


def _courriels_a_relire(desk, prefix):
    """Vue « À relire » : brouillons lus dans la messagerie (seul endroit où ``list_drafts`` est appelé), bannières, filtre, éditeur."""
    from . import drafts440
    from .workstation import external_links
    links = external_links(desk)
    error = ''
    listing = {'items': [], 'uidvalidity': '', 'folder': desk.c['mail']['drafts']}
    try:
        listing = drafts440.list_drafts(desk.c['mail'])
        from .acces_courriel56100 import filtrer_brouillons   # 5.6.100 (R04) : chaque brouillon contrôlé sur son origine, interface active ou non
        listing = filtrer_brouillons(desk, None, desk.c['mail'], listing)
    except Stop as ex:
        error = human(str(ex))
        listing = {'items': [], 'uidvalidity': '', 'folder': desk.c['mail']['drafts']}   # contrôle en panne : rien n'est listé
    except Exception:
        error = 'La messagerie IMAP ne répond pas. Vérifiez la connexion dans Paramètres → Connexions.'
    from .conflicts500 import banner_html
    from .reponses5671 import section_html
    notice = banner_html(desk, prefix) + section_html(desk, prefix)
    rows = ''
    for item in listing['items']:
        rows += ('<button type="button" class="ax-row" data-uid="%s" data-search="%s">'
                 '<span class="ax-row-top"><strong>%s</strong>%s</span>'
                 '<span class="ax-row-sub">%s</span><span class="ax-row-date">%s</span></button>') % (
            e(item['uid']), e((item['subject'] + ' ' + item['to']).lower(), quote=True),
            e(item['subject'][:110]), '<em class="ax-badge" title="Brouillon préparé par l’agent">Agent</em>' if item['agent'] else '',
            e(item['to'][:90] or 'Sans destinataire'), e(str(item['date'])[:16].replace('T', ' ')))
    webmail = links.get('roundcube_drafts') or links.get('roundcube') or ''
    return ('<div class="ax-split" id="ax-drafts" data-validity="%s">'
            '<div class="ax-list" role="region" aria-label="Brouillons">'
            '<div class="ax-list-head"><h1>Courriels à relire</h1>'
            '<p class="ax-muted">Les brouillons du dossier « %s » de votre messagerie. Cliquez sur l’un d’eux pour le corriger ici même.</p>'
            '<input type="search" id="ax-filter" placeholder="Filtrer par objet ou destinataire" aria-label="Filtrer les brouillons">'
            '<div class="ax-actions"><button type="button" id="ax-refresh" class="ax-btn ghost" title="Relire la liste depuis la messagerie">Actualiser</button>%s</div></div>'
            '%s%s<div class="ax-rows" id="ax-rows">%s</div>%s</div>'
            '<section class="ax-editor" id="ax-editor" aria-live="polite">'
            '<div class="ax-empty"><h2>Choisissez un brouillon</h2><p>Vous pourrez le relire, le modifier, demander à l’IA de le reformuler puis l’enregistrer. '
            '<strong>Rien n’est envoyé</strong> : l’envoi reste une action de votre messagerie.</p></div></section></div>') % (
        e(listing['uidvalidity'], quote=True), e(listing['folder']),
        ('<a class="ax-btn ghost" target="_blank" rel="noopener" href="%s" title="Ouvrir la messagerie dans un nouvel onglet">Ouvrir la messagerie</a>' % e(webmail, quote=True)) if webmail else '',
        notice, ('<p class="ax-alert" role="alert">%s</p>' % e(error)) if error else '', rows,
        '' if rows or error else '<p class="ax-muted ax-pad">Aucun brouillon pour le moment. Dès qu’un courriel ou un avis de procédure arrive, l’agent en prépare un ici.</p>')


def documents_page(desk, auth, prefix, args):
    from . import office440
    from .dav import DAV
    cfg = office440.settings(desk)
    matters = sorted(load_matters(desk.c), key=lambda m: matter_display(m).casefold())
    by_id = {m['id']: m for m in matters}
    mid = args.get('matter', '')
    current = by_id.get(mid)
    banner = ''
    if not cfg['enabled']:
        banner = ('<p class="ax-alert">L’éditeur de documents n’est pas encore relié. '
                  '<a href="%s">Le configurer en deux minutes</a> — en attendant, les fichiers restent consultables.</p>') % e(prefix + '/atelier/reglages')
    options = '<option value="">Choisir un dossier…</option>' + ''.join(
        '<option value="%s"%s>%s</option>' % (e(m['id']), ' selected' if m['id'] == mid else '',
                                              e(matter_display(m))) for m in matters)
    listing = ''
    if current:
        folder = args.get('dir') or current['path']
        try:
            folder = allowed_path(desk, folder)
            if not under(folder, current['path']):
                folder = current['path']
            items = DAV(desk.c['nextcloud']).list_folder(folder)
        except Stop as ex:
            items, folder = [], current['path']
            listing += '<p class="ax-alert">%s</p>' % e(human(str(ex)))
        except Exception:
            items = []
            listing += '<p class="ax-alert">Nextcloud ne répond pas pour le moment.</p>'
        crumbs = '<p class="ax-muted ax-crumbs">%s</p>' % e(folder)
        up = ''
        if folder != clean_path(current['path']):
            up = '<a class="ax-file" href="%s">↑ Dossier parent</a>' % e(prefix + '/documents?' + urlencode({'matter': mid, 'dir': str(PurePosixPath(folder).parent)}))
        rows = ''
        for item in sorted(items, key=lambda x: (not x['directory'], x['path'].lower())):
            name = PurePosixPath(item['path']).name
            if item['directory']:
                rows += '<a class="ax-file dir" href="%s"><span>📁 %s</span></a>' % (
                    e(prefix + '/documents?' + urlencode({'matter': mid, 'dir': item['path']})), e(name))
            elif office440.can_open(item['path']):
                edit = office440.extension(item['path']) in office440.EDITABLE
                action = '<a class="ax-btn" href="%s">%s</a>' % (
                    e(prefix + '/documents/edit?' + urlencode({'path': item['path'], 'matter': mid})), 'Modifier' if edit else 'Lire')
                notice = ''
                from .notices440 import looks_like_notice
                if looks_like_notice(item['path']):
                    notice = '<button type="button" class="ax-btn ghost" data-analyze="%s" data-matter="%s" title="Chercher les dates, les inscrire à l’agenda et préparer le courriel d’information">Analyser l’avis</button>' % (
                        e(item['path'], quote=True), e(mid, quote=True))
                rows += '<div class="ax-file"><span>📄 %s<small>%s</small></span><span class="ax-file-actions">%s%s</span></div>' % (
                    e(name), e(_size(item['size'])), notice, action)
            else:
                rows += '<div class="ax-file muted"><span>%s<small>%s · non modifiable ici</small></span></div>' % (e(name), e(_size(item['size'])))
        listing += crumbs + '<div class="ax-files">' + up + (rows or '<p class="ax-muted ax-pad">Dossier vide.</p>') + '</div>'
    else:
        listing = '<p class="ax-muted ax-pad">Choisissez un dossier pour afficher ses fichiers. Les documents Word, Excel, PowerPoint et OpenDocument s’ouvrent dans l’éditeur ; chaque enregistrement est écrit directement dans Nextcloud.</p>'
    recent = ''
    try:
        rows = desk.db.execute('SELECT path,outcome,at FROM office_saves440 ORDER BY id DESC LIMIT 6').fetchall()
        recent = ''.join('<li><a href="%s">%s</a><small>%s · %s</small></li>' % (
            e(prefix + '/documents/edit?' + urlencode({'path': r['path']})), e(PurePosixPath(r['path']).name),
            'copie de sécurité' if r['outcome'] == 'copy' else 'enregistré', e(str(r['at'])[:16].replace('T', ' '))) for r in rows)
    except Exception:
        recent = ''
    from .web520 import docrequest_html
    banner += docrequest_html(desk, prefix, mid)
    body = ('<h1>Documents</h1>%s<form method="get" class="ax-bar" action="%s"><label>Dossier'
            '<select name="matter" id="ax-matter">%s</select></label><button class="ax-btn">Afficher</button></form>'
            '<div class="ax-cols"><section class="ax-card">%s</section>'
            '<div class="ax-card"><h2>Modifiés récemment ici</h2>%s</div></div>') % (
        banner, e(prefix + '/documents'), options, listing,
        ('<ul class="ax-recent">' + recent + '</ul>') if recent else '<p class="ax-muted">Rien pour l’instant.</p>')
    return shell('Documents', body, prefix, auth['csrf'], '/documents')


def _size(n):
    n = int(n or 0)
    return '%d o' % n if n < 1024 else ('%d Ko' % (n // 1024) if n < 1048576 else '%.1f Mo' % (n / 1048576))


def editor_page(desk, auth, prefix, args):
    from . import office440
    cfg = office440.settings(desk)
    path = args.get('path', '')
    try:
        path = allowed_path(desk, path)
        if not office440.can_open(path):
            raise Stop('format_non_modifiable')
    except Stop as ex:
        return page_out(shell('Document', '<p class="ax-alert">%s</p><p><a class="ax-btn" href="%s">Retour aux documents</a></p>' % (
            e(human(str(ex))), e(prefix + '/documents')), prefix, auth['csrf'], '/documents'))
    if not cfg['enabled']:
        return page_out(shell('Document', '<p class="ax-alert">%s <a href="%s">Configurer l’éditeur</a></p>' % (
            e(human('editeur_documents_non_configure')), e(prefix + '/atelier/reglages')), prefix, auth['csrf'], '/documents'))
    name = PurePosixPath(path).name
    body = ('<div class="ax-docbar"><a class="ax-btn ghost" href="%s">← Documents</a><h1>%s</h1>'
            '<a class="ax-btn ghost" href="%s">Vérifier les citations</a>'
            '<span id="ax-docstate" class="ax-muted">Chargement de l’éditeur…</span>'
            '<button type="button" id="ax-diag" class="ax-btn ghost">Un problème ?</button></div>'
            '<div class="o84-editor"><div id="ax-office-wrap"><div id="ax-office"></div></div><div id="ax-office-panel" class="o84-panel" role="complementary" aria-label="Suivi du document" hidden></div></div>'
            '<div id="ax-office-help" class="ax-card" hidden></div>'
            '<script type="application/json" id="ax-office-boot">%s</script>'
            '<script defer src="%s"></script><script defer src="%s"></script>') % (
        e(prefix + '/documents'), e(name),
        e(prefix + '/verification?' + urlencode({'path': path, 'matter': args.get('matter', '')}), quote=True),
        json.dumps({'path': path, 'server': cfg['server_url'], 'matter': args.get('matter', ''),
                    'mode': 'view' if args.get('mode') == 'view' else 'edit'}).replace('</', '<\\/'),
        e(prefix + '/static/v440-office.js'), e(prefix + '/static/v5684-office.js'))
    html = shell(name, body, prefix, auth['csrf'], '/documents')
    origin = cfg['server_url']
    ds = urlsplit(origin)
    host = ds.scheme + '://' + ds.netloc
    csp = ("default-src 'none'; script-src 'self' %s; connect-src 'self' %s; img-src 'self' data: %s; "
           "style-src 'self' 'unsafe-inline'; font-src 'self' data: %s; form-action 'self'; frame-src %s; "
           "frame-ancestors 'none'; base-uri 'none'") % (host, host, host, host, host)
    return page_out(html, csp)


def settings_page(desk, auth, prefix, args, render=None):
    from . import office440, notices440
    cfg = office440.settings(desk)
    matters = sorted(load_matters(desk.c), key=lambda m: matter_display(m).casefold())
    base = office440.public_base(desk, auth)
    engines = ''.join('<option value="%s"%s>%s</option>' % (v, ' selected' if cfg['engine'] == v else '', t) for v, t in (
        ('onlyoffice', 'ONLYOFFICE Document Server'), ('euro-office', 'Euro-Office')))
    from . import office_bridge5684
    flags5684 = office_bridge5684.flags(desk)
    observation = ('<section class="ax-card" id="ax-office-5684"><h2>Observation pendant l’édition</h2><p class="ax-muted">Le plugin AxiorHub est servi par cette application : rien à installer sur le serveur de documents. L’analyse consomme le modèle choisi, '
                   'au plus une fois toutes les %d secondes par document et seulement si la modification peut avoir un effet.</p><form class="m5-form" data-api="m5684/office/reglage" data-reload="1">'
                   '<label class="e5636-switch"><input type="checkbox" name="observation" value="1"%s> Activer l’observation et l’analyse</label><label class="e5636-switch"><input type="checkbox" name="auto_apply" value="1"%s> Autoriser l’écriture automatique en mode Rédiger en autonomie</label>'
                   '<button class="ax-btn ghost" type="submit">Enregistrer</button></form></section>') % (flags5684['min_seconds'], ' checked' if flags5684['observation'] else '', ' checked' if flags5684['auto_apply'] else '')
    # 5.6.108 (lot O) : sans serveur de documents, aperçu en lecture seule ; guide pas à pas ; valeurs d'un poste (Docker sur ce même ordinateur)
    poste = bool((auth or {}).get('poste'))
    port108 = urlsplit(str((auth or {}).get('origin') or 'http://127.0.0.1:8769')).port or 8769
    poste_callback = 'http://host.docker.internal:%d%s' % (port108, (auth or {}).get('prefix', '/agent-courriel'))
    without = ('<div class="ax-note" id="of-sans-serveur"><p><strong>Sans serveur de documents : aperçu en lecture seule.</strong> Les documents Word s’affichent '
               'en aperçu dans AxiorHub et s’ouvrent dans Word sur votre ordinateur. Le serveur de documents (EuroOffice ou OnlyOffice) sert à les '
               '<em>modifier ici</em>, à plusieurs, avec l’enregistrement contrôlé.</p><p><a href="%s" target="_blank" rel="noopener noreferrer">'
               'Guide pas à pas : installer EuroOffice ou OnlyOffice avec son secret JWT (serveur, poste Windows 11, poste Ubuntu 24.04)</a></p></div>') % e(office440.guide_url(), quote=True)
    if poste:
        without += ('<p class="ax-muted" id="of-poste">Sur ce poste, avec le serveur de documents dans Docker : adresse du serveur '
                    '<code>http://127.0.0.1:8080</code>, adresse de cette interface vue par le serveur de documents <code>%s</code>.</p>') % e(poste_callback)
    office = (observation + '<section class="ax-card" id="ax-office-settings"><h2>1 · Modifier les documents dans l’interface</h2>' + without +
              '<p class="ax-muted">Les deux serveurs (ONLYOFFICE, Euro-Office) s’intègrent par le même principe : cette page affiche leur éditeur ; '
              'le serveur relit le fichier ici via une adresse signée et renvoie vos modifications à l’agent, qui les écrit dans le stockage des documents.</p>'
              '<label>Moteur<select id="of-engine">%s</select></label>'
              '<label>Adresse du serveur de documents<input id="of-server" placeholder="%s" value="%s"></label>'
              '<label>Secret JWT <small>(le même que JWT_SECRET du conteneur du serveur de documents ; jamais affiché ici une fois enregistré)</small>'
              '<input id="of-secret" type="password" autocomplete="off" placeholder="%s"></label>'
              '<label>Adresse de cette interface vue par le serveur de documents <small>(%s)</small>'
              '<input id="of-callback" value="%s" placeholder="%s"></label>'
              '<div class="ax-actions"><button type="button" class="ax-btn" id="of-save">Enregistrer</button>'
              '<button type="button" class="ax-btn ghost" id="of-diag">Tester la connexion</button>'
              '<button type="button" class="ax-btn ghost" id="of-clear">Retirer le secret</button></div>'
              '<p class="ax-muted">Le test vérifie, étape par étape : le serveur répond, api.js est joignable, le secret est accepté, et le serveur de documents '
              'joint AxiorHub (fichier d’essai fixe, aucune donnée de dossier).</p>'
              '<div id="of-result" class="ax-diag" aria-live="polite"></div></section>') % (
        engines, 'http://127.0.0.1:8080' if poste else 'https://documents.example.test', e(cfg['server_url'], quote=True),
        'secret enregistré (laissez vide pour le conserver)' if cfg['jwt'] else 'aucun secret enregistré',
        ('sur ce poste, avec Docker : ' + e(poste_callback)) if poste else
        'laissez vide si c’est la même que dans votre navigateur ; sinon l’adresse interne, par exemple https://agent.example.test/agent-courriel',
        e(cfg['callback_base'], quote=True), e(poste_callback if poste else base, quote=True))
    enabled = bool(desk.settings('automation:notices440_enabled', True))
    calendar = bool(desk.settings('automation:notice_calendar440', True))
    rows = ''
    for m in matters[:300]:
        p = notices440.matter_profile(desk, m['id'])
        opts = '<option value="">Non précisé</option>' + ''.join('<option value="%s"%s>%s</option>' % (
            r, ' selected' if p['role'] == r else '', e(notices440.ROLE_LABELS[r])) for r in notices440.CASE_ROLES)
        rows += ('<tr data-matter="%s"><td>%s</td><td><select class="mp-role">%s</select></td>'
                 '<td><input class="mp-name" placeholder="Me …" value="%s"></td><td><input class="mp-mail" type="email" placeholder="confrere@…" value="%s"></td>'
                 '<td><button type="button" class="ax-btn ghost mp-save">Enregistrer</button></td></tr>') % (
            e(m['id'], quote=True), e(matter_display(m)), opts,
            e(p['partner_name'], quote=True), e(p['partner_email'], quote=True))
    notices = ('<section class="ax-card"><h2>2 · Avis de renvoi, convocations, calendriers de procédure</h2>'
               '<p class="ax-muted">Quand un document nommé par exemple « XXX - YYY - 20261002 - Avis de renvoi » arrive dans un dossier, l’agent le lit, '
               'retient seulement les dates qui figurent réellement dans le texte, les inscrit à l’agenda et prépare un brouillon d’information (jamais envoyé).</p>'
               '<label class="ax-check"><input type="checkbox" id="nt-enabled"%s> Analyser automatiquement les nouveaux avis</label>'
               '<label class="ax-check"><input type="checkbox" id="nt-cal"%s> Inscrire les dates à l’agenda</label>'
               '<div class="ax-actions"><button type="button" class="ax-btn" id="nt-save">Enregistrer</button></div>'
               '<h3>Votre rôle par dossier</h3><p class="ax-muted">Postulant : le brouillon est adressé au confrère plaidant (ou dominus litis). Défendeur : l’agent prépare aussi un projet d’acte de constitution à l’arrivée d’une convocation. Sans rôle, le brouillon est une note interne.</p>'
               '<div class="ax-table"><table><thead><tr><th>Dossier</th><th>Votre rôle</th><th>Confrère</th><th>Son courriel</th><th></th></tr></thead><tbody>%s</tbody></table></div></section>') % (
        ' checked' if enabled else '', ' checked' if calendar else '', rows)
    from . import wordcraft56108   # 5.6.108 (lot W) : éditeur intégré WordCraft proposé ou non (réglage administrateur)
    from . import craft56109   # 5.6.109 (lot V, F16) : éditeurs Craft facultatifs (installés et activés par l'administrateur ; désactivés par défaut)
    body = '<h1>Réglages de l’atelier</h1>' + office + wordcraft56108.section_reglages(desk) + craft56109.section_reglages(desk, prefix) + notices
    return (render or shell)('Réglages de l’atelier', body, prefix, auth['csrf'], '/parametres')
