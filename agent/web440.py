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

STATIC = {'/static/v5614.js': 'text/javascript', '/static/v5614.css': 'text/css', '/static/v5613.css': 'text/css', '/static/v569.js': 'text/javascript', '/static/rules568.js': 'text/javascript', '/static/v568.js': 'text/javascript', '/static/v568.css': 'text/css', '/static/v567.js': 'text/javascript', '/static/v567.css': 'text/css', '/static/v440.css': 'text/css', '/static/v440.js': 'text/javascript',
          '/static/v440-office.js': 'text/javascript', '/static/v450.css': 'text/css', '/static/v450.js': 'text/javascript',
          '/static/v460.css': 'text/css', '/static/v460.js': 'text/javascript',
          '/static/v470.css': 'text/css', '/static/v470.js': 'text/javascript',
          '/static/v480.css': 'text/css', '/static/v480.js': 'text/javascript',
          '/static/v490.css': 'text/css', '/static/v490.js': 'text/javascript',
          '/static/v500.css': 'text/css', '/static/v500.js': 'text/javascript',
          '/static/v510.css': 'text/css', '/static/v510.js': 'text/javascript',
          '/static/app520.css': 'text/css', '/static/v520.css': 'text/css', '/static/v520.js': 'text/javascript',
          '/static/v530.css': 'text/css', '/static/v530.js': 'text/javascript', '/static/v540.js': 'text/javascript', '/static/v550.js': 'text/javascript', '/static/v550.css': 'text/css', '/static/v560.css': 'text/css', '/static/v561.css': 'text/css'}
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
    content = ('<a class="cf-skip" href="#ax-main">Aller au contenu</a><div class="ax-main ax-in-workspace" id="ax-main" tabindex="-1">' + body + '</div>'
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
        'envoi_desactive': 'L’envoi depuis AxiorHub est désactivé. Utilisez « Ouvrir dans la messagerie ».',
        'envoi_non_active_pour_ce_dossier': 'L’envoi n’est pas autorisé pour le dossier de ce brouillon (page Confort).',
        'envoi_dossier_non_identifie': 'Le dossier de ce brouillon n’est pas identifié : l’envoi est refusé. Utilisez « Ouvrir dans la messagerie ».',
        'confirmation_envoi_requise': 'Cochez la case de confirmation avant d’activer l’envoi.',
        'smtp_non_configure': 'Aucun serveur d’envoi (SMTP) n’est configuré.',
        'smtp_securite_invalide': 'La sécurité SMTP doit être « starttls » ou « ssl ».',
        'smtp_echec': 'Le serveur d’envoi a refusé ou n’a pas répondu : le message n’est PAS parti.',
        'smtp_destinataire_refuse': 'Un destinataire a été refusé par le serveur d’envoi : vérifiez les adresses et la messagerie.',
        'brouillon_a_verifier': 'Ce brouillon est marqué « À VÉRIFIER » : contrôlez-le et retirez la mention avant tout envoi.',
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
    if path in STATIC and method == 'GET':
        name = path.rsplit('/', 1)[1]
        return {'status': '200 OK', 'kind': STATIC[path] + '; charset=utf-8',
                'body': (Path(__file__).parent / 'static' / name).read_text(encoding='utf-8')}
    if not (path in ('/parametres/agents', '/parametres/agendas', '/agents-documents', '/engagements', '/veille', '/parametres/proactivite', '/accueil-administratif', '/missions', '/audience', '/missions-complexes', '/profils', '/parametres/assistant', '/parametres/connexions', '/mise-en-service', '/courriels', '/documents', '/documents/edit', '/atelier/reglages', '/echeances', '/fiche', '/chronologie', '/verification', '/sources', '/modeles', '/progres', '/autonomie', '/tracabilite', '/recherche', '/confort', '/sw.js', '/hors-ligne', '/pieces', '/diagnostic', '/ia-externe', '/mon-style', '/a-propos') or path in _pages500() or path.startswith('/api440/')):
        return None
    from . import shell501
    shell501.set_context(cfg)
    desk = Desk(cfg)
    try:
        if path.startswith('/api440/'):
            return api(env, desk, auth, prefix, path[len('/api440/'):], args, method)
        if method != 'GET':
            return {'status': '405 Method Not Allowed', 'kind': 'text/plain; charset=utf-8', 'body': 'Méthode refusée.'}
        if path in ('/parametres/agents','/parametres/agendas','/agents-documents'):
            from .web_rules568 import page
            return page_out(page(desk,auth,prefix,env,path,args))
        if path in ('/engagements','/veille','/parametres/proactivite','/mise-en-service','/audience','/missions-complexes','/profils'):
            from .web568 import page
            env['axiorhub.args']=args
            return page_out(page(desk,auth,prefix,env,path))
        if path in ('/accueil-administratif','/missions','/parametres/assistant','/parametres/connexions'):
            from . import web567
            if path == '/accueil-administratif':return page_out(web567.reception_page(desk,auth,prefix,env))
            if path == '/missions':return page_out(web567.page(desk,auth,prefix,env))
            if path == '/parametres/connexions':return page_out(web567.connections_page(desk,auth,prefix,env))
            return page_out(web567.preferences_page(desk,auth,prefix,env))
        if path == '/courriels':
            return page_out(drafts_page(desk, auth, prefix, args))
        if path == '/documents':
            return page_out(documents_page(desk, auth, prefix, args))
        if path == '/documents/edit':
            return editor_page(desk, auth, prefix, args)
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


def api(env, desk, auth, prefix, name, args, method):
    from . import drafts440, office440, notices440
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
            if name == 'drafts':
                return json_out(drafts440.list_drafts(mail, int(args.get('limit', 40) or 40)))
            if name == 'draft':
                return json_out(drafts440.get_draft(mail, args.get('uid', ''), args.get('validity', '')))
            if name == 'office/config':
                path = allowed_path(desk, args.get('path', ''))
                mode = 'view' if args.get('mode') == 'view' else 'edit'
                opened = office440.open_document(desk, auth, path, user_name=desk.c['mail'].get('from_name', 'Cabinet'), mode=mode)
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
                return json_out(web490.handle(desk, name, {}, 'GET', args))
            if name.startswith('m500/'):
                from . import web500
                return json_out(web500.handle(desk, name, {}, 'GET', args))
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
        if name == 'draft/save':
            result = drafts440.save_draft(mail, data)
            _learn(desk, data, result)
            desk.audit('brouillon_440_modifie', {'uid': result['uid'], 'agent': bool(result.get('agent_key'))})
            return json_out(result)
        if name == 'draft/discard':
            result = drafts440.discard_draft(mail, data.get('uid', ''), data.get('uidvalidity', ''), data.get('confirm', ''))
            desk.audit('brouillon_440_ecarte', {'uid': str(data.get('uid', ''))[:20]})
            return json_out(result)
        if name == 'draft/assist':
            from .integration import submit_question
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
            result = web500.handle(desk, name, data)
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
            result = web490.handle(desk, name, data)
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
            result = web450.handle(desk, name, data)
            desk.audit('echeance_450_api', {'route': name})
            return json_out(result)
        raise Stop('route_inconnue')
    except Stop as ex:
        return json_out({'error': str(ex), 'message': human(str(ex))}, '400 Bad Request')


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
def drafts_page(desk, auth, prefix, args):
    from . import drafts440
    from .workstation import external_links
    links = external_links(desk)
    error = ''
    listing = {'items': [], 'uidvalidity': '', 'folder': desk.c['mail']['drafts']}
    try:
        listing = drafts440.list_drafts(desk.c['mail'])
    except Stop as ex:
        error = human(str(ex))
    except Exception:
        error = 'La messagerie IMAP ne répond pas. Vérifiez la connexion dans Paramètres → Connexions.'
    from .conflicts500 import banner_html
    notice = banner_html(desk, prefix)
    rows = ''
    for item in listing['items']:
        rows += ('<button type="button" class="ax-row" data-uid="%s" data-search="%s">'
                 '<span class="ax-row-top"><strong>%s</strong>%s</span>'
                 '<span class="ax-row-sub">%s</span><span class="ax-row-date">%s</span></button>') % (
            e(item['uid']), e((item['subject'] + ' ' + item['to']).lower(), quote=True),
            e(item['subject'][:110]), '<em class="ax-badge" title="Brouillon préparé par l’agent">Agent</em>' if item['agent'] else '',
            e(item['to'][:90] or 'Sans destinataire'), e(str(item['date'])[:16].replace('T', ' ')))
    webmail = links.get('roundcube_drafts') or links.get('roundcube') or ''
    body = ('<div class="ax-split" id="ax-drafts" data-validity="%s">'
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
    from .web520 import mail_pipeline_html
    body = mail_pipeline_html(desk, prefix, compact=True) + body
    return shell('Courriels', body, prefix, auth['csrf'], '/courriels')


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
            '<div id="ax-office-wrap"><div id="ax-office"></div></div>'
            '<div id="ax-office-help" class="ax-card" hidden></div>'
            '<script type="application/json" id="ax-office-boot">%s</script>'
            '<script defer src="%s"></script>') % (
        e(prefix + '/documents'), e(name),
        e(prefix + '/verification?' + urlencode({'path': path, 'matter': args.get('matter', '')}), quote=True),
        json.dumps({'path': path, 'server': cfg['server_url'], 'matter': args.get('matter', ''),
                    'mode': 'view' if args.get('mode') == 'view' else 'edit'}).replace('</', '<\\/'),
        e(prefix + '/static/v440-office.js'))
    html = shell(name, body, prefix, auth['csrf'], '/documents')
    origin = cfg['server_url']
    ds = urlsplit(origin)
    host = ds.scheme + '://' + ds.netloc
    csp = ("default-src 'none'; script-src 'self' %s; connect-src 'self' %s; img-src 'self' data: %s; "
           "style-src 'self' 'unsafe-inline'; font-src 'self' data: %s; form-action 'self'; frame-src %s; "
           "frame-ancestors 'none'; base-uri 'none'") % (host, host, host, host, host)
    return page_out(html, csp)


def settings_page(desk, auth, prefix, args):
    from . import office440, notices440
    cfg = office440.settings(desk)
    matters = sorted(load_matters(desk.c), key=lambda m: matter_display(m).casefold())
    base = office440.public_base(desk, auth)
    engines = ''.join('<option value="%s"%s>%s</option>' % (v, ' selected' if cfg['engine'] == v else '', t) for v, t in (
        ('onlyoffice', 'ONLYOFFICE Document Server'), ('euro-office', 'Euro-Office')))
    office = ('<section class="ax-card" id="ax-office-settings"><h2>1 · Modifier les documents dans l’interface</h2>'
              '<p class="ax-muted">Les deux serveurs (ONLYOFFICE, Euro-Office) s’intègrent par le même principe : cette page affiche leur éditeur ; '
              'le serveur relit le fichier ici via une adresse signée et renvoie vos modifications à l’agent, qui les écrit dans Nextcloud.</p>'
              '<label>Moteur<select id="of-engine">%s</select></label>'
              '<label>Adresse du serveur de documents<input id="of-server" placeholder="https://office.example.fr" value="%s"></label>'
              '<label>Secret JWT <small>(le même que dans local.json du serveur de documents : services.CoAuthoring.token.*.secret)</small>'
              '<input id="of-secret" type="password" autocomplete="off" placeholder="%s"></label>'
              '<label>Adresse de cette interface vue par le serveur de documents <small>(laissez vide si c’est la même que dans votre navigateur ; sinon l’adresse interne, par exemple https://agent.example.fr/agent-courriel)</small>'
              '<input id="of-callback" value="%s" placeholder="%s"></label>'
              '<div class="ax-actions"><button type="button" class="ax-btn" id="of-save">Enregistrer</button>'
              '<button type="button" class="ax-btn ghost" id="of-diag">Tester la connexion</button>'
              '<button type="button" class="ax-btn ghost" id="of-clear">Retirer le secret</button></div>'
              '<div id="of-result" class="ax-diag" aria-live="polite"></div></section>') % (
        engines, e(cfg['server_url'], quote=True), 'secret enregistré (laissez vide pour le conserver)' if cfg['jwt'] else 'aucun secret enregistré',
        e(cfg['callback_base'], quote=True), e(base, quote=True))
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
    body = '<h1>Réglages de l’atelier</h1>' + office + notices
    return shell('Réglages de l’atelier', body, prefix, auth['csrf'], '/parametres')
