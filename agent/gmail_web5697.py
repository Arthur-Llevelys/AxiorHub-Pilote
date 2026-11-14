"""5.6.97 (lot gmail) : routes ``/api440/m5689/gmail/*``, carte Paramètres › Connexions › Gmail, guide de l'administrateur et bloc
« projet Gmail » de la vue « À traiter avec le Pilote ».

- ``gmail/etat`` (GET) : connexion de la personne (sans secret), relève, option du cabinet.
- ``gmail/reglage`` (POST, administrateur) : fournisseur Gmail activé ou non (désactivé par défaut).
- ``gmail/connecter`` (POST) : départ du consentement (lecture ; ``brouillons`` : consentement additionnel). Rend ``oauth_url``.
- ``gmail/deconnecter``, ``gmail/synchroniser`` (POST) : déconnexion avec révocation ; relève mise en file.
- ``gmail/projet/modifier``, ``gmail/projet/abandonner``, ``gmail/projet/resoudre`` (POST) : projet local, version (409).
- ``gmail/deposer`` (POST, droit « déposer ») : brouillon dans le fil Gmail. ``gmail/envoyer`` (POST, droit « envoyer ») : « Envoyer
  maintenant » après relecture exacte. ``gmail/verifier`` (POST) : rapprochement d'un résultat incertain. ``gmail/comparer`` (GET).

Toutes les écritures : CSRF, compte et rôle de la requête authentifiée, clé d'idempotence pour les effets. Aucun appel Google au rendu d'une page.
"""
from html import escape as e
import uuid

from .common import Stop
from . import google5697 as gg

ROLES = ('administrateur', 'avocat')
LECTURE = ('gmail/etat', 'gmail/comparer')
ECRITURE = ('gmail/reglage', 'gmail/connecter', 'gmail/deconnecter', 'gmail/synchroniser', 'gmail/projet/modifier', 'gmail/projet/abandonner',
            'gmail/projet/resoudre', 'gmail/deposer', 'gmail/envoyer', 'gmail/verifier', 'gmail/preparer_ia')
DROITS = {'gmail/deposer': 'deposer', 'gmail/envoyer': 'envoyer', 'gmail/reglage': 'reglages', 'gmail/preparer_ia': 'deposer'}   # lu par membres5650 avant la route
CONFLITS = ('version_perimee', 'brouillon_modifie_a_distance', 'envoi_deja_demande', 'depot_a_verifier')
MESSAGES = {
    'gmail_desactive': 'Le fournisseur Gmail est désactivé pour le cabinet.', 'gmail_non_connecte': 'Aucune boîte Gmail n’est connectée à votre compte.',
    'google_non_configure': 'Le client Google du serveur n’est pas configuré : voir le guide de l’administrateur.',
    'google_uri_retour_invalide': 'L’URI de retour configurée doit être en HTTPS et se terminer par /oauth/google/callback.',
    'google_identifiant_client_invalide': 'Identifiant client Google illisible dans la configuration du serveur.',
    'google_reconnexion_requise': 'Google a retiré l’autorisation : reconnectez Gmail. Aucune relance n’est faite d’ici là.',
    'google_capacite_non_accordee': 'Cette autorisation n’a pas été accordée chez Google.',
    'consentement_brouillons_requis': 'Autorisez d’abord les brouillons Gmail (consentement supplémentaire chez Google).',
    'message_absent': 'Ce message n’est plus dans la copie locale de Gmail.', 'message_sans_reponse': 'Ce message ne demande pas de réponse (envoyé, copie, notification).',
    'projet_absent': 'Projet introuvable.', 'adresse_invalide': 'Adresse de destinataire illisible.', 'destinataire_requis': 'Indiquez au moins un destinataire.',
    'trop_de_destinataires': 'Vingt destinataires au plus.', 'brouillon_a_deposer': 'Déposez d’abord le brouillon dans Gmail, puis relisez-le.',
    'relecture_requise': 'Cochez « J’ai relu ce message tel qu’il partira ».', 'envoi_deja_demande': 'Cet envoi a déjà été demandé : rien n’a été renvoyé.',
    'brouillon_modifie_a_distance': 'Le brouillon a été modifié dans Gmail depuis votre relecture : comparez les deux versions avant de continuer.',
    'brouillon_distant_absent': 'Le brouillon n’existe plus dans Gmail : le projet est redevenu local.', 'depot_a_verifier': 'Un dépôt précédent est à vérifier avant tout nouveau dépôt.',
    'compte_modifie': 'Le compte Gmail a changé depuis la préparation : relisez le projet.', 'geste_reserve_a_la_personne': 'Ce geste est réservé à une personne.',
    'rien_a_verifier': 'Rien à vérifier.', 'pieces_ajoutees_dans_gmail': 'Des pièces ont été ajoutées dans Gmail : envoyez ce brouillon depuis Gmail, ou remplacez-le.',
    'etat_incompatible': 'Cette action ne s’applique pas à l’état actuel.', 'version_perimee': 'Le projet a changé depuis l’affichage : rechargez la page et relisez-le.',
    'google_etat_invalide': 'Autorisation refusée : lien expiré, déjà utilisé ou ouvert dans une autre session.', 'google_etat_expire': 'Autorisation expirée : recommencez.',
    'google_autorisation_refusee': 'Autorisation refusée chez Google : rien n’a été connecté.', 'google_compte_different': 'Un autre compte Google a été choisi : déconnectez d’abord la boîte actuelle.',
    'google_aucune_capacite_accordee': 'Aucune autorisation utile n’a été accordée chez Google.', 'google_configuration_modifiee': 'La configuration Google du serveur a changé : recommencez.',
    'google_connexion_modifiee': 'La connexion a été modifiée pendant l’autorisation : recommencez.', 'google_jeton_renouvellement_absent': 'Google n’a pas fourni d’accès durable : recommencez.',
    'dossier_a_confirmer': 'Confirmez le dossier : l’IA ne prépare jamais une réponse sans dossier confirmé.',
    'projet_perime': 'Un nouveau message est arrivé pendant la préparation : relisez et corrigez le projet avant de le déposer.',
    'modele_local_requis': 'Le modèle local autorisé est indisponible : le contenu Gmail n’est jamais envoyé à un service distant par défaut.',
    'fil_absent': 'Ce fil n’est pas (ou plus) dans votre liste.', 'dossier_reserve': 'Ce fil n’est pas (ou plus) dans votre liste.',
    'categorie_fermee': 'Les courriels ne sont pas ouverts à l’IA dans la politique du cabinet.', 'mode_local_strict': 'Refusé par la politique des données du cabinet.',
    'role_insuffisant': 'Action réservée.', 'route_inconnue': 'Route inconnue.', 'valeur_invalide': 'Valeur invalide.', 'idempotence_requise': 'Page incomplète : rechargez-la.'}


def allowed(role, route):
    return role in ROLES and route.split('?', 1)[0] in LECTURE + ECRITURE


def _garde_projet(desk, owner, role, projet_id):
    """5.6.97 (B01) : un projet dont le message source ou le dossier est réservé à d'autres est « absent » pour cette personne (404 neutre)."""
    # 5.6.100 (R04) : garde commune — projet de la personne, connexion Gmail autorisée de la personne, identifiant Gmail lu sur le serveur,
    # rattachements du message et dossier du projet ; rattachement illisible refusé ; panne du contrôle : 503
    from .acces_courriel56100 import exiger
    try:
        exiger(desk, (owner, role), {'genre': 'gmail', 'projet': str(projet_id or '')})
    except Stop as ex:
        if str(ex) == 'introuvable':
            raise Stop('projet_absent') from None
        raise


def droit_requis(route):
    return DROITS.get(route.split('?', 1)[0])


def _message(code):
    from .web440 import human
    texte = MESSAGES.get(code) or human(code)
    return texte if texte != code else 'Demande refusée.'


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    try:
        return _route(env, desk, auth, prefix, name, args, method)
    except Stop as error:
        code = str(error).split(':')[0]
        statut = ('409 Conflict' if code in CONFLITS else '403 Forbidden' if code in ('role_insuffisant', 'origine_ou_csrf_refuse', 'geste_reserve_a_la_personne')
                  else '404 Not Found' if code in ('route_inconnue', 'projet_absent', 'fil_absent', 'dossier_reserve', 'introuvable')
                  else '503 Service Unavailable' if code == 'controle_indisponible' else '400 Bad Request')   # 5.6.100 (R04)
        return json_out({'error': code, 'message': _message(code)}, statut)


def _route(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post, json_out
    from .web567 import actor
    from . import gmail5697 as gm
    owner, role = actor(env)
    route = name[len('m5689/'):].split('?', 1)[0]
    if not allowed(role, route):
        raise Stop('role_insuffisant' if route in LECTURE + ECRITURE else 'route_inconnue')
    gm.ensure_schema(desk)
    if method == 'GET':
        if route == 'gmail/etat':
            return json_out({'actif': gm.actif(desk), 'connexion': gg.vue(gm.connexion_de(desk, owner)), 'synchro': gm.etat_synchro(desk, owner)})
        if route == 'gmail/comparer':
            _garde_projet(desk, owner, role, str(args.get('projet') or ''))
            return json_out(gm.comparer(desk, owner, str(args.get('projet') or '')))
        raise Stop('route_inconnue')
    if method != 'POST' or route not in ECRITURE:
        raise Stop('route_inconnue')
    data = check_post(env, auth)
    if route == 'gmail/reglage':
        return json_out(gm.regler(desk, role, data.get('actif') is True))
    if route == 'gmail/connecter':
        if not gm.actif(desk):
            raise Stop('gmail_desactive')
        voulues = ['gmail.lecture', 'gmail.brouillons'] if data.get('brouillons') in (True, '1', 'oui') else ['gmail.lecture']
        out = gg.demarrer(desk, owner, 'gmail', gg.session_de(env, auth), voulues)
        return json_out({'oauth_url': out['url'], 'expire_secondes': out['expire_secondes']})
    if route == 'gmail/deconnecter':
        out = gm.deconnecter(desk, owner)
        return json_out({**out, 'message': 'Gmail déconnecté. Accès %s ; jetons et copie locale des messages effacés ; projets et suivis conservés.' % {
            'revoquee': 'révoqué chez Google', 'echec': 'NON révoqué chez Google (révoquez-le depuis votre compte Google)', 'aucun_jeton': 'déjà absent'}.get(out['revocation'], out['revocation'])})
    if route == 'gmail/synchroniser':
        return json_out(gm.demander_synchro(desk, owner))
    if route == 'gmail/preparer_ia':   # C04 : circuit de rédaction commun, modèle local seul, dossier confirmé ; la ligne est relue sous le garde commun
        from . import courriels5696 as cx
        from .acces5697 import masques
        hidden = masques(desk, (owner, role))
        r = cx.ligne(desk, str(data.get('fil') or ''), owner, hidden)
        if r.get('fournisseur') != 'gmail':
            raise Stop('route_inconnue')
        return json_out(gm.demander_ia(desk, owner, role, r['mail_key'], str(data.get('dossier') or ''), str(data.get('instruction') or ''), hidden))
    projet = str(data.get('projet') or '')
    if projet:
        _garde_projet(desk, owner, role, projet)
    if route == 'gmail/projet/modifier':
        return json_out({**gm.modifier(desk, owner, projet, data.get('version'), data), 'message': 'Projet enregistré. Rien n’a été déposé ni envoyé.'})
    if route == 'gmail/projet/abandonner':
        return json_out(gm.abandonner(desk, owner, projet, data.get('version')))
    if route == 'gmail/projet/resoudre':
        return json_out({**gm.resoudre(desk, owner, projet, data.get('version'), str(data.get('choix') or '')), 'message': 'Décision enregistrée.'})
    if route == 'gmail/deposer':
        out = gm.deposer(desk, owner, role, projet, data.get('version'), str(data.get('idempotence') or ''))
        return json_out({**out, 'message': {'depose': 'Brouillon déposé dans le fil Gmail et relu. Rien n’a été envoyé.',
                                           'a_verifier': 'Réponse de Google perdue : dépôt à vérifier avant tout nouveau dépôt. Rien n’a été envoyé.'}.get(out['resultat'], 'Dépôt déjà demandé.')})
    if route == 'gmail/envoyer':
        out = gm.envoyer(desk, owner, role, projet, data.get('version'), str(data.get('empreinte') or ''), str(data.get('idempotence') or ''), data.get('relu') is True)
        return json_out({**out, 'message': {'envoye': 'Message envoyé depuis Gmail.', 'a_verifier': 'Envoi à vérifier : Google n’a pas confirmé. Rien ne sera renvoyé automatiquement.',
                                           'refuse': 'Envoi refusé par Google : rien n’est parti.'}.get(out['resultat'], 'Envoi déjà demandé : rien n’a été renvoyé.')})
    if route == 'gmail/verifier':
        return json_out(gm.verifier(desk, owner, projet))
    raise Stop('route_inconnue')


# ------------------------------------------------------------------------------------------------ Paramètres › Connexions › Gmail
def _bouton_oauth(libelle, brouillons=False, classe='ax-btn'):
    """Formulaire relayé par ``v5652.js`` (``data-cx-oauth``) : la réponse ``oauth_url`` ouvre la page de consentement de Google."""
    return ('<form class="m5-form gm97-inline" data-api="m5689/gmail/connecter" data-cx-oauth>%s<button class="%s" type="submit">%s</button>'
            '<p class="m5-status" role="status"></p></form>') % ('<input type="hidden" name="brouillons" value="1">' if brouillons else '', classe, e(libelle))


def _simple(api, libelle, cachees='', classe='ax-btn ghost'):
    from .ui_components5689 import formulaire
    return formulaire(api, cachees + '<button class="%s" type="submit">%s</button>' % (classe, e(libelle)), recharge=True, classe='gm97-inline')


def guide_html(desk):
    """Guide de l'administrateur : créer le client OAuth « Application Web » dans Google Cloud et déclarer l'URI de retour. Aucun secret affiché."""
    cfg = gg.etat_configuration(desk, 'gmail')
    uri = cfg.get('uri_retour') or 'https://<adresse publique du Pilote>/<préfixe>/api440/m5689/oauth/google/callback'
    etat = ('Client configuré (%s), URI de retour : %s.' % (cfg['client'], cfg['uri_retour'])) if cfg['configure'] else \
        'Client non configuré (%s)' % _message(cfg.get('code', 'google_non_configure'))
    etapes = (
        'Dans Google Cloud, créez (ou choisissez) un projet dédié à Gmail pour le cabinet ; un projet distinct pour la recette.',
        'Bibliothèque d’API : activez « Gmail API » (et « Google Calendar API » seulement si l’agenda Google est aussi utilisé).',
        'Écran de consentement (« Google Auth Platform ») : marque (nom, logo, adresse d’assistance), domaines autorisés, liens vers la politique de '
        'confidentialité et les conditions, audience (« Interne » pour un Workspace du cabinet ; sinon « Externe », et en essai seulement les comptes ajoutés).',
        'Accès aux données : déclarez seulement gmail.readonly et gmail.compose. Ne déclarez ni gmail.modify, ni mail.google.com, ni gmail.send. '
        'Ces portées sont « restreintes » : une ouverture à des comptes externes demande la validation de Google et, le cas échéant, une évaluation de sécurité.',
        'Clients : « Créer un client » de type « Application Web ». Dans « URI de redirection autorisés », ajoutez exactement : %s' % uri,
        'Copiez l’identifiant client dans la configuration du serveur (google5697.client_id). Enregistrez le secret client dans un fichier du coffre du '
        'serveur, lisible par le seul service (par exemple /run/secrets/axiorhub_google_oauth), et indiquez ce chemin (google5697.client_secret_file). '
        'Indiquez la même URI de retour (google5697.redirect_uri). Le secret n’est jamais saisi dans une page ni copié dans un paquet.',
        'Pour séparer les projets Google, une section propre à un usage prime : google5697_gmail ou google5697_agenda (mêmes clés).',
        'Au proxy : ne journalisez pas la chaîne de requête de /oauth/google/callback (elle porte un code d’autorisation à usage unique).',
        'Enfin, activez ci-dessus le fournisseur Gmail : chaque avocat clique alors « Connecter Gmail » et consent avec son propre compte.')
    return ('<details class="gm97-guide" id="gmail5697-guide"><summary>Guide de l’administrateur : client OAuth Google</summary><p>%s</p><ol>%s</ol>'
            '<p class="ax-muted">Le client identifie AxiorHub ; les jetons sont propres à chaque personne qui consent. Ce n’est pas une clé partagée. '
            'Un « mode test » de Google n’est pas une mise en production.</p></details>') % (e(etat), ''.join('<li>%s</li>' % e(x) for x in etapes))


def _quand(desk, owner, valeur):
    """Date et heure locales du compte (« 11/11/2026 17:54 ») ; illisible : rien plutôt qu'une date fausse."""
    if not valeur:
        return ''
    from . import courriels5696 as cx
    from .courriels_web5696 import _date
    return _date(valeur, cx._fuseau(desk, owner))


def section_html(desk, prefix, owner, role, args=None):
    """Carte « Gmail » de Paramètres › Connexions : option du cabinet (administrateur), connexion de la personne, relève, guide.
    ``args['google']`` : issue du retour OAuth (« connecte » ou code du refus), dite en clair."""
    from . import gmail5697 as gm
    from .ui_components5689 import case, formulaire
    gm.ensure_schema(desk)
    actif = gm.actif(desk)
    issue = str((args or {}).get('google') or '')
    annonce = ''
    if issue:
        annonce = '<p class="%s" role="status">%s</p>' % ('ax-ok' if issue == 'connecte' else 'ax-alert', e(
            'Compte Google autorisé : les autorisations accordées sont listées ci-dessous.' if issue == 'connecte' else _message(issue)))
    corps = annonce + ('<p class="ax-muted">Lecture des courriels Gmail et brouillons dans Gmail ; l’envoi se fait seulement sur votre clic « Envoyer maintenant », '
             'après relecture. Désactivé par défaut ; la messagerie IMAP du cabinet reste inchangée.</p>')
    if role == 'administrateur':
        corps += formulaire('m5689/gmail/reglage', case('actif', 'Proposer Gmail aux avocats du cabinet', actif, aide='Désactiver n’efface ni connexion, ni projet, ni suivi.')
                            + '<button class="ax-btn" type="submit">Enregistrer</button>', recharge=True)
    v = gg.vue(gm.connexion_de(desk, owner))
    if not actif:
        corps += '<p>Fournisseur Gmail : <strong>désactivé</strong>.</p>'
    elif not gg.etat_configuration(desk, 'gmail')['configure']:
        corps += '<p class="ax-alert" role="status">Le client Google du serveur n’est pas encore configuré : l’administrateur suit le guide de cette carte.</p>'
    elif v['etat'] in ('absente', 'deconnectee', 'en_attente'):
        corps += '<p>Aucune boîte Gmail connectée à votre compte.</p>' + _bouton_oauth('Connecter Gmail')
    else:
        s = gm.etat_synchro(desk, owner) or {}
        caps = ''.join('<li>%s</li>' % e(gg.LIBELLES[c]) for c in v['capacites']) or '<li>Aucune</li>'
        corps += ('<dl class="gm97-infos"><dt>Boîte</dt><dd>%s</dd><dt>État</dt><dd><strong>%s</strong></dd><dt>Autorisations accordées</dt><dd><ul>%s</ul></dd>'
                  '<dt>Dernière relève</dt><dd>%s%s%s</dd></dl>') % (
            e(v['adresse']), e(v['libelle']), caps, e(_quand(desk, owner, s.get('derniere')) or 'pas encore'), (' — couverture partielle' if s.get('couverture') == 'partielle' else ''),
            (' — %s' % e(s['fenetre_incomplete'])) if s.get('fenetre_incomplete') else '')
        if s.get('erreur') and s.get('etat') in ('espace', 'arrete', 'erreur'):
            corps += '<p class="ax-alert" role="status">Relève : %s</p>' % e(MESSAGES.get(s['erreur'], 'Google ne répond pas pour l’instant : nouvel essai espacé.'))
        if v['etat'] == 'a_reconnecter':
            corps += _bouton_oauth('Reconnecter Gmail')
        else:
            if 'gmail.brouillons' not in v['capacites']:
                corps += _bouton_oauth('Autoriser les brouillons Gmail', brouillons=True, classe='ax-btn ghost')
            corps += _simple('m5689/gmail/synchroniser', 'Synchroniser maintenant')
        corps += _simple('m5689/gmail/deconnecter', 'Déconnecter Gmail')
    if role == 'administrateur':
        corps += guide_html(desk)
    return '<section class="ax-card" id="gmail5697" aria-labelledby="gmail5697-t"><h2 id="gmail5697-t">Gmail</h2>%s</section>' % corps


# ------------------------------------------------------------------------------------------------ bloc « projet Gmail » de la vue assistée
def _champ(nom, libelle, valeur, type_='text', requis=False, aide=''):
    ident = 'gm97-%s-%s' % (nom, uuid.uuid4().hex[:8])
    if type_ == 'textarea':
        entree = '<textarea id="%s" name="%s" rows="8" maxlength="50000">%s</textarea>' % (ident, e(nom, quote=True), e(valeur))
    else:
        entree = '<input id="%s" name="%s" type="%s" value="%s"%s>' % (ident, e(nom, quote=True), e(type_, quote=True), e(valeur, quote=True), ' required' if requis else '')
    return '<div class="ui89-champ"><label for="%s">%s%s</label>%s%s</div>' % (ident, e(libelle), ' (obligatoire)' if requis else '', entree,
                                                                               ('<span class="ui89-aide">%s</span>' % e(aide)) if aide else '')


def _cache(nom, valeur):
    return '<input type="hidden" name="%s" value="%s">' % (e(nom, quote=True), e(str(valeur), quote=True))


def releve_html(r):
    """Relecture exacte : ce qui partira, relu dans le message composé lui-même."""
    pieces = ''.join('<li>%s (%d octets)</li>' % (e(x['nom']), x['taille']) for x in r['pieces']) or '<li>Aucune pièce jointe</li>'
    return ('<dl class="gm97-releve"><dt>Compte d’expédition</dt><dd>%s</dd><dt>À</dt><dd>%s</dd><dt>Cc</dt><dd>%s</dd><dt>Objet</dt><dd>%s</dd>'
            '<dt>Pièces</dt><dd><ul>%s</ul></dd><dt>Texte</dt><dd><pre class="gm97-texte">%s</pre></dd></dl>') % (
        e(r['compte']), e(', '.join(r['a']) or '—'), e(', '.join(r['cc']) or '—'), e(r['objet']), pieces, e(r['texte']))


def _ia_html(desk, r):
    """« Préparer avec l'IA » : dossier à confirmer (obligatoire), instruction facultative ; modèle local du cabinet, projet local à relire."""
    from .common import load_matters, matter_display
    from .ui_components5689 import formulaire
    dossiers = [(m['id'], matter_display(m)) for m in load_matters(desk.c)][:400]
    choix = '<option value="">Choisir le dossier</option>' + ''.join('<option value="%s"%s>%s</option>' % (
        e(v, quote=True), ' selected' if v == r.get('matter') else '', e(t)) for v, t in dossiers)
    ident = 'gm97-ia-' + uuid.uuid4().hex[:8]
    return formulaire('m5689/gmail/preparer_ia', _cache('fil', r['fil']) +
                      '<div class="ui89-champ"><label for="%s-d">Dossier (confirmé, obligatoire)</label><select id="%s-d" name="dossier" required>%s</select></div>'
                      '<div class="ui89-champ"><label for="%s">Votre instruction (facultatif)</label><textarea id="%s" name="instruction" rows="2" maxlength="4000"></textarea></div>'
                      '<div class="cx96-boutons"><button class="ax-btn ghost" type="submit">Préparer avec l’IA</button></div>'
                      '<p class="ax-muted">Modèle local du cabinet, sans envoi à un service distant. Le projet reste dans AxiorHub, à relire ; vous seul le déposez.</p>'
                      % (ident, ident, choix, ident, ident), recharge=True, classe='gm97-ia')


def principal_html(desk, prefix, r, cachees_preparer):
    """Bloc principal d'une ligne Gmail : préparer (projet local), corriger, déposer, relire et envoyer, vérifier. Lecture locale seulement."""
    from . import gmail5697 as gm
    from .common import load_matters, matter_display
    from .ui_components5689 import formulaire
    conn = gg.lire(desk, r['compte'].split(':', 1)[1])
    if not r.get('projet'):
        if r['statut'] not in ('a_traiter', 'a_completer'):
            return ''
        dossiers = [('', 'Comme proposé (%s)' % (r['dossier'] or 'aucun dossier'))] + [(m['id'], matter_display(m)) for m in load_matters(desk.c)][:400]
        choix = ''.join('<option value="%s">%s</option>' % (e(v, quote=True), e(t)) for v, t in dossiers)
        ident = 'gm97-p-' + uuid.uuid4().hex[:8]
        return formulaire('m5689/courriels/preparer', cachees_preparer +
                          '<div class="ui89-champ"><label for="%s-d">Dossier</label><select id="%s-d" name="dossier">%s</select></div>'
                          '<div class="ui89-champ"><label for="%s">Votre réponse (facultatif, modifiable ensuite)</label><textarea id="%s" name="texte" rows="4" maxlength="50000"></textarea></div>'
                          '<div class="cx96-boutons"><button class="ax-btn" type="submit">Préparer une réponse</button></div>'
                          '<p class="ax-muted">Projet local dans AxiorHub : rien n’est déposé dans Gmail ni envoyé.</p>' % (ident, ident, choix, ident, ident),
                          recharge=True, classe='cx96-preparer') + _ia_html(desk, r)
    try:
        p = gm.vue_projet(desk, conn['owner'], r['projet'])
    except Stop:
        return ''
    caps = gg.capacites(conn) if conn['etat'] == 'active' else []
    out = '<div class="gm97-projet"><p class="cx96-projet" role="status">Projet de réponse : <strong>%s</strong>%s</p>' % (
        e(p['libelle']), (' — %s' % e(MESSAGES[p['erreur']])) if p['erreur'] in MESSAGES else '')
    base = _cache('projet', p['id']) + _cache('version', p['version'])
    if p['ia_etat']:
        out += '<p class="cx96-note" role="status">%s</p>' % e(p['ia_libelle'])
        if p['ia_limites']:
            out += '<ul class="gm97-limites">%s</ul>' % ''.join('<li>%s</li>' % e(x) for x in p['ia_limites'])
        if p['ia_etat'] == 'non_applique' and p['texte_ia']:
            out += '<details class="gm97-edition"><summary>Voir la proposition de l’IA (non appliquée)</summary><pre class="gm97-texte">%s</pre></details>' % e(p['texte_ia'])
    if p['perime']:
        out += '<p class="ax-alert" role="alert">Un nouveau message est arrivé pendant la préparation : relisez le fil, corrigez le projet, puis déposez-le.</p>'
    if p['etat'] in ('local', 'depose'):
        out += '<details class="gm97-edition"%s><summary>Corriger le projet</summary>%s</details>' % (' open' if p['etat'] == 'local' else '', formulaire(
            'm5689/gmail/projet/modifier', base + _champ('a', 'À', p['a'], requis=True, aide='Adresses séparées par des virgules. Réponse à tous désactivée par défaut.')
            + _champ('cc', 'Cc', p['cc']) + _champ('objet', 'Objet', p['objet'], requis=True) + _champ('texte', 'Texte', p['texte'], 'textarea')
            + '<button class="ax-btn" type="submit">Enregistrer le projet</button>', recharge=True))
    if p['etat'] == 'local':
        if 'gmail.brouillons' in caps:
            out += _simple('m5689/gmail/deposer', 'Déposer dans Gmail (brouillon)', base + _cache('idempotence', uuid.uuid4().hex), 'ax-btn')
            out += '<p class="ax-muted">Le brouillon est rangé dans le fil Gmail et relu après dépôt. Il n’est pas envoyé.</p>'
        else:
            out += _bouton_oauth('Autoriser les brouillons Gmail', brouillons=True)
    elif p['etat'] == 'depose':
        ident = e(p['id'][:8])
        out += '<section class="gm97-envoi" aria-labelledby="gm97-envoi-%s"><h3 id="gm97-envoi-%s">Relecture avant envoi</h3>%s%s</section>' % (
            ident, ident, releve_html(p['releve']), formulaire('m5689/gmail/envoyer', base + _cache('empreinte', p['empreinte_depot'])
            + _cache('idempotence', uuid.uuid4().hex) + '<div class="ui89-champ"><label class="m5-check"><input type="checkbox" name="relu" required> '
            '<span>J’ai relu ce message tel qu’il partira (compte, destinataires, objet, texte, pièces).</span></label></div>'
            '<button class="ax-btn" type="submit">Envoyer maintenant</button><p class="ax-muted">Un seul envoi. Si Google ne confirme pas, l’état '
            '« Envoi à vérifier » s’affiche et rien n’est renvoyé automatiquement.</p>', recharge=True, classe='gm97-envoyer'))
    elif p['etat'] == 'modifie_a_distance':
        out += ('<p class="ax-alert">Le brouillon a été modifié dans Gmail. Comparez avec la version d’AxiorHub ci-dessous, puis choisissez la version à garder : '
                'rien n’est écrasé sans votre décision.</p>' + releve_html(p['releve']) + '%s%s') % (
            _simple('m5689/gmail/projet/resoudre', 'Garder la version de Gmail', base + _cache('choix', 'garder_gmail')),
            _simple('m5689/gmail/projet/resoudre', 'Remplacer par la version d’AxiorHub au prochain dépôt', base + _cache('choix', 'remplacer')))
    elif p['etat'] in ('envoi_a_verifier', 'depot_a_verifier'):
        out += '<p class="ax-alert" role="status">%s</p>%s' % (
            e('Envoi à vérifier : Google n’a pas confirmé l’envoi. Rien n’a été renvoyé.' if p['etat'] == 'envoi_a_verifier' else 'Dépôt à vérifier : Google n’a pas confirmé le dépôt.'),
            _simple('m5689/gmail/verifier', 'Vérifier dans Gmail', _cache('projet', p['id']), 'ax-btn'))
    elif p['etat'] == 'envoye':
        out += '<p>Réponse envoyée depuis votre boîte Gmail (%s).</p>' % e(p['compte'])
    if p['etat'] in ('local', 'depose', 'modifie_a_distance'):
        out += _simple('m5689/gmail/projet/abandonner', 'Abandonner ce projet', base)
    return out + '</div>'
