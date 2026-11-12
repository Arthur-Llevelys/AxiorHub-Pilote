"""5.6.96 (C01-C04 sur IMAP, I03) : page « À traiter avec le Pilote » (``/courriels?vue=assistante``) et routes ``/api440/m5689/courriels/*``.

- ``courriels/liste`` (GET) : lignes de la projection locale (aucun appel IMAP ni modèle).
- ``courriels/message``, ``courriels/fil``, ``courriels/piece`` (GET) : lecture à la demande par le fournisseur IMAP, en lecture seule.
- ``courriels/decision`` (POST, CSRF) : reporter, mettre en attente, terminer, écarter (motif requis), rouvrir, annuler ; version (409) et idempotence.
- ``courriels/preparer`` (POST, CSRF, droit « déposer ») : demande de projet par le circuit existant ; rien n'est envoyé.
- ``courriels/reglage`` (POST, CSRF) : option de la vue pour son compte ; la part « cabinet » est réservée à l'administrateur.

Rôles : administrateur et avocat (comme la boîte de réception). Le compte, le rôle et les dossiers réservés viennent de la requête authentifiée.
"""
from html import escape as e
from urllib.parse import urlencode
import uuid

from .common import Stop, load_matters, matter_display
from . import courriels5696 as cx

LECTURE = ('courriels/liste', 'courriels/message', 'courriels/fil', 'courriels/piece')
ECRITURE = ('courriels/decision', 'courriels/preparer', 'courriels/reglage')
ROLES = ('administrateur', 'avocat')
CONFLITS = ('version_perimee',)
NEUTRES = ('dossier_reserve', 'fil_absent', 'introuvable')
ERREURS = {'version_perimee': 'Ce suivi a changé entre-temps (autre onglet ou autre personne) : votre saisie n’a pas été enregistrée. Rechargez la page pour comparer, puis recommencez.',
           'motif_requis': 'Indiquez le motif (3 caractères au moins).', 'de_qui_requis': 'Indiquez de qui vous attendez une réponse.',
           'date_invalide': 'Date illisible.', 'date_passee': 'Choisissez une date à venir.', 'dossier_reserve': 'Ce dossier est réservé à d’autres membres du cabinet.',
           'fil_absent': 'Ce fil n’est plus dans la liste.', 'fil_invalide': 'Fil inconnu.', 'etat_incompatible': 'Cette action ne s’applique pas à l’état actuel.',
           'rien_a_annuler': 'Aucune décision à annuler.', 'preparation_deja_faite': 'La préparation est terminée : le projet ne peut plus être annulé d’ici.',
           'preparation_deja_demandee': 'Une préparation est déjà en cours.', 'reponse_deja_prete': 'Une réponse est déjà prête : relisez-la.',
           'dossier_a_choisir': 'Choisissez un dossier existant ou laissez le champ vide.', 'idempotence_requise': 'Page incomplète : rechargez-la.',
           'version_requise': 'Page incomplète : rechargez-la.', 'message_deplace_ou_boite_reindexee': 'Le message a été déplacé ou la boîte réindexée : il n’est plus à cette place.',
           'messagerie_non_configuree': 'Messagerie non configurée.', 'vue_desactivee': 'La vue « À traiter avec le Pilote » est désactivée.',
           'role_insuffisant': 'Action réservée.', 'introuvable': 'Ce fil n’est pas (ou plus) dans votre liste.', 'route_inconnue': 'Route inconnue.', 'valeur_invalide': 'Valeur invalide.'}
CHAMPS = {'motif_requis': 'motif', 'de_qui_requis': 'de_qui', 'date_invalide': 'jusqua', 'date_passee': 'jusqua', 'dossier_a_choisir': 'dossier'}
from .gmail_web5697 import MESSAGES as _GMAIL5697   # 5.6.97 (lot gmail) : refus propres aux lignes Gmail, en clair
ERREURS = {**_GMAIL5697, **ERREURS}


def allowed(role, route):
    route = route.split('?', 1)[0]
    return role in ROLES and route in LECTURE + ECRITURE


def _hidden(desk, owner, role):
    """5.6.97 : dossiers masqués par le garde commun (``acces5697``), relus à chaque requête."""
    from .acces5697 import masques
    return masques(desk, (owner, role))


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out, human
    try:
        return _route(env, desk, auth, prefix, name, args, method)
    except Stop as error:
        code = str(error).split(':')[0]
        if code in NEUTRES:              # 5.6.97 (B01) : réservé ou absent, même réponse : l'existence d'un message réservé n'est pas divulguée
            return json_out({'error': 'introuvable', 'message': ERREURS['introuvable']}, '404 Not Found')
        statut = ('409 Conflict' if code in CONFLITS else '403 Forbidden' if code in ('role_insuffisant', 'origine_ou_csrf_refuse', 'vue_desactivee')
                  else '404 Not Found' if code in ('route_inconnue',) else '400 Bad Request')
        message = ERREURS.get(code) or human(code)
        return json_out({'error': code, 'message': message if message != code else 'Demande refusée.', **({'champ': CHAMPS[code]} if code in CHAMPS else {})}, statut)


def _route(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m5689/'):].split('?', 1)[0]
    if not allowed(role, route):
        raise Stop('role_insuffisant' if route in LECTURE + ECRITURE else 'route_inconnue')
    hidden = _hidden(desk, owner, role)
    if method == 'GET':
        if route not in LECTURE:
            raise Stop('route_inconnue')
        if not cx.active(desk, owner):
            raise Stop('vue_desactivee')
        if route == 'courriels/liste':
            return json_out({'lignes': [cx.json_ligne(r) for r in cx.lignes(desk, owner, hidden, str(args.get('filtre') or 'actifs'), str(args.get('dossier') or ''))]})
        r = cx.ligne(desk, str(args.get('fil') or ''), owner, hidden)
        if r.get('fournisseur') == 'gmail':   # 5.6.97 (lot gmail) : même contrat de lecture ; projection locale, pièce lue à la demande
            from .gmail5697 import FournisseurGmail
            fournisseur = FournisseurGmail(desk, owner, hidden)
        else:
            fournisseur = cx.FournisseurIMAP(desk, hidden)
        try:
            # 5.6.97 (B01) : la référence garde le rattachement lu sur le serveur ; celui de la ligne affichée ne le remplace plus
            ref = fournisseur.reference(r['mail_key'])
            if route == 'courriels/message':
                return json_out(fournisseur.lire_message(ref))
            if route == 'courriels/fil':
                return json_out(fournisseur.lire_fil(ref))
            return json_out(fournisseur.lire_piece(ref, str(args.get('index') or '0')))
        except Stop as error:
            code = str(error).split(':')[0]
            if code in NEUTRES:
                raise
            # messagerie absente ou injoignable, message déplacé : un état réel de la source, dit en clair, jamais un fil vide présenté comme lu
            from .web440 import human
            return json_out({'indisponible': True, 'code': code, 'message': ERREURS.get(code) or human(code)})
        finally:
            fournisseur.fermer()
    if method != 'POST' or route not in ECRITURE:
        raise Stop('route_inconnue')
    data = check_post(env, auth)
    if route == 'courriels/reglage':
        donnees = {}
        if 'compte' in data:
            donnees['compte'] = data['compte']
        if role == 'administrateur' and 'cabinet' in data:
            donnees['cabinet'] = data['cabinet'] is True
        return json_out(cx.regler(desk, owner, role, donnees))
    if not cx.active(desk, owner):
        raise Stop('vue_desactivee')
    if route == 'courriels/preparer':   # 5.6.97 (lot gmail) : sur une ligne Gmail, projet local par l'avocat, sans IA ; rien n'est déposé ni envoyé
        r = cx.ligne(desk, str(data.get('fil') or ''), owner, hidden)
        if r.get('fournisseur') == 'gmail':
            from . import gmail5697
            dossier = str(data.get('dossier') or '')
            if dossier and (dossier in hidden or dossier not in {m['id'] for m in load_matters(desk.c)}):
                raise Stop('dossier_a_choisir')
            return json_out(gmail5697.preparer(desk, owner, r['mail_key'], dossier or r['matter'], str(data.get('texte') or ''), hidden))
    action = 'preparer' if route == 'courriels/preparer' else str(data.get('action') or '')
    if route == 'courriels/decision' and action == 'preparer':
        raise Stop('action_inconnue')          # « Préparer » a sa route : elle porte le droit « déposer »
    return json_out(cx.decider(desk, owner, str(data.get('fil') or ''), action, data.get('version'), str(data.get('idempotence') or ''), data, hidden, role))


# ------------------------------------------------------------------------------------------------ page
def _url(prefix, **params):
    return prefix + '/courriels?' + urlencode({'vue': 'assistante', **{k: v for k, v in params.items() if v}})


def _date(valeur, zone):
    """Date et heure locales du compte ; illisible : rien plutôt qu'une date fausse."""
    from zoneinfo import ZoneInfo
    quand = cx._instant_de(valeur)
    if quand.year < 1900:
        return ''
    try:
        return quand.astimezone(ZoneInfo(zone)).strftime('%d/%m/%Y %H:%M')
    except (OverflowError, ValueError, KeyError):
        return ''


def _cachees(fil, action, version):
    return ('<input type="hidden" name="fil" value="%s"><input type="hidden" name="action" value="%s"><input type="hidden" name="version" value="%d">'
            '<input type="hidden" name="idempotence" value="%s">') % (e(fil, quote=True), e(action, quote=True), int(version), uuid.uuid4().hex)


def _champ(nom, libelle, type_='text', requis=False, aide='', attributs=''):
    ident = 'cx96-%s-%s' % (nom, uuid.uuid4().hex[:8])
    return ('<div class="ui89-champ cx96-champ"><label for="%s">%s%s</label><input id="%s" name="%s" type="%s"%s%s%s>%s</div>') % (
        ident, e(libelle), ' (obligatoire)' if requis else '', ident, e(nom, quote=True), e(type_, quote=True), ' required' if requis else '',
        (' aria-describedby="%s-aide"' % ident) if aide else '', (' ' + attributs) if attributs else '', ('<span class="ui89-aide" id="%s-aide">%s</span>' % (ident, e(aide))) if aide else '')


def _geste(titre, api, contenu, bouton, classe=''):
    from .ui_components5689 import formulaire
    return ('<details class="cx96-geste%s"><summary>%s</summary>%s</details>' % (' ' + classe if classe else '', e(titre),
            formulaire(api, contenu + '<div class="cx96-boutons"><button class="ax-btn" type="submit">%s</button></div>' % e(bouton), recharge=True)))


def _detail(desk, prefix, r, zone, filtre, dossier):
    from .ui_components5689 import formulaire
    fil, version, statut = r['fil'], r['version'], r['statut']
    liste = _url(prefix, filtre=filtre if filtre != 'actifs' else '', dossier=dossier)
    tete = ('<p class="cx96-dossier-grand">%s</p><h2 id="cx96-detail-titre">%s</h2>'
            '<dl class="cx96-infos"><dt>De</dt><dd>%s</dd><dt>Reçu</dt><dd>%s</dd><dt>Boîte</dt><dd>%s · %s</dd><dt>État</dt><dd><span class="cx96-statut" data-statut="%s">%s</span>%s</dd></dl>') % (
        e(r['dossier'] or 'Dossier à préciser'), e(r['objet']), e(r['de']), e(_date(r['recu'], zone)), e(r['adresse']), e(r['boite']), e(statut, quote=True), e(r['libelle']),
        (' <span class="cx96-precision">%s</span>' % e(r['precision'])) if r['precision'] else '')
    suivi = r['suivi'] or {}
    if suivi.get('statut') == 'en_attente' and suivi.get('attente_de'):
        tete += '<p class="cx96-note">En attente de %s jusqu’au %s.</p>' % (e(suivi['attente_de']), e(_date(suivi.get('echeance'), zone)))
    elif suivi.get('statut') == 'reporte' and suivi.get('echeance'):
        tete += '<p class="cx96-note">Reporté au %s (heure de %s).</p>' % (e(_date(suivi['echeance'], zone)), e(suivi.get('fuseau') or zone))
    if suivi.get('motif'):
        tete += '<p class="cx96-note">Motif : %s</p>' % e(suivi['motif'])
    pourquoi = ('<details class="cx96-pourquoi"><summary>Pourquoi ?</summary><p>%s</p>'
                '<button class="ax-btn ghost" type="button" data-cx96-lire="message" data-fil="%s">Afficher le message source</button>'
                '<div class="cx96-texte" data-cx96-zone="message" aria-live="polite"></div></details>') % (e(cx.raison_lisible(r['raison'])), e(fil, quote=True))
    if r.get('fournisseur') == 'gmail':   # 5.6.97 (lot gmail) : projet local, dépôt dans Gmail et envoi seulement après relecture exacte
        from .gmail_web5697 import principal_html
        principal = principal_html(desk, prefix, r, _cachees(fil, 'preparer', version))
    elif r['projet']:
        principal = ('<p class="cx96-projet">Un projet de réponse existe (non envoyé). <a class="ax-btn" href="%s">Relire la réponse</a></p>'
                     % e(prefix + '/livrables/' + r['projet'] + '?' + urlencode({'retour': _url(prefix, fil=fil, filtre=filtre if filtre != 'actifs' else '', dossier=dossier)}), quote=True))
    elif statut in ('a_traiter', 'a_completer', 'interrompu'):   # 5.6.97 (B04) : une préparation interrompue se reprend ici, sans renvoi silencieux
        dossiers = [('', 'Comme proposé (%s)' % (r['dossier'] or 'aucun dossier : réponse sans dossier'))] + [(m['id'], matter_display(m)) for m in load_matters(desk.c)][:400]
        choix = ''.join('<option value="%s">%s</option>' % (e(v, quote=True), e(t)) for v, t in dossiers)
        ident = 'cx96-instr-' + uuid.uuid4().hex[:8]
        principal = formulaire('m5689/courriels/preparer', _cachees(fil, 'preparer', version) +
                               '<div class="ui89-champ cx96-champ"><label for="%s-d">Dossier</label><select id="%s-d" name="dossier">%s</select></div>'
                               '<div class="ui89-champ cx96-champ"><label for="%s">Votre instruction (facultatif)</label><textarea id="%s" name="instruction" rows="2" maxlength="4000"></textarea></div>'
                               '<div class="cx96-boutons"><button class="ax-btn" type="submit">%s</button></div>'
                               '<p class="ax-muted">Le projet est rédigé par le circuit habituel et rangé dans les brouillons à relire. Rien n’est envoyé.</p>'
                               % (ident, ident, choix, ident, ident, 'Reprendre la préparation' if statut == 'interrompu' else 'Préparer une réponse'), recharge=True, classe='cx96-preparer')
    elif statut == 'en_preparation':
        principal = '<p class="cx96-projet" role="status">Préparation en cours : le projet apparaîtra ici dès qu’il sera prêt. Rien n’est envoyé.</p>'
    else:
        principal = ''
    lire = ('<div class="cx96-fil"><button class="ax-btn ghost" type="button" data-cx96-lire="fil" data-fil="%s">Lire le fil</button>'
            '<div class="cx96-texte" data-cx96-zone="fil" aria-live="polite"></div></div>') % e(fil, quote=True)
    gestes = ''
    if statut not in ('termine', 'ecarte'):
        gestes += _geste('Reporter', 'm5689/courriels/decision', _cachees(fil, 'reporter', version) +
                         _champ('jusqua', 'Reporter au', 'datetime-local', True, aide='Heure de %s.' % zone) + _champ('motif', 'Note', aide='Facultatif.'), 'Reporter')
        gestes += _geste('Mettre en attente', 'm5689/courriels/decision', _cachees(fil, 'attendre', version) +
                         _champ('de_qui', 'En attente de', requis=True, aide='Client, confrère, greffe…') + _champ('jusqua', 'Jusqu’au', 'date', True), 'Mettre en attente')
        gestes += _geste('Terminer', 'm5689/courriels/decision', _cachees(fil, 'terminer', version) + _champ('motif', 'Note', aide='Facultatif.'), 'Terminer')
    gestes += _geste('Écarter', 'm5689/courriels/decision', _cachees(fil, 'ecarter', version) +
                     _champ('motif', 'Motif', requis=True, attributs='minlength="3" maxlength="500"', aide='Par exemple : déjà traité par téléphone, sans objet.'), 'Écarter')
    if statut in ('termine', 'ecarte', 'reporte', 'en_attente'):
        gestes += formulaire('m5689/courriels/decision', _cachees(fil, 'rouvrir', version) + '<button class="ax-btn ghost" type="submit">Rouvrir</button>', recharge=True, classe='cx96-simple')
    if r['suivi']:
        gestes += formulaire('m5689/courriels/decision', _cachees(fil, 'annuler', version) + '<button class="ax-btn ghost" type="submit">Annuler la dernière décision</button>',
                             recharge=True, classe='cx96-simple')
    menu = ('<details class="cx96-menu"><summary>Suivi : reporter, terminer, écarter…</summary><p class="ax-muted">Ces gestes agissent sur le suivi dans AxiorHub ; '
            'le message reste à sa place dans la messagerie.</p>%s</details>') % gestes
    histoire = ''
    sid = cx.suivi_id(desk, fil, r.get('compte', ''), r.get('boite', ''))
    if sid:
        evts = cx.evenements(desk, sid)
        if evts:
            histoire = '<details class="cx96-historique"><summary>Historique (%d)</summary><ul>%s</ul></details>' % (len(evts), ''.join(
                '<li>%s — %s : %s → %s%s%s</li>' % (e(_date(x['at'], zone)), e(x['auteur']), e(cx.LIBELLES.get(x['ancien'], x['ancien'])), e(cx.LIBELLES.get(x['nouveau'], x['nouveau'])),
                                                     (' (%s)' % e(x['motif'])) if x['motif'] else '', ' — annulée' if x['annule'] else '') for x in evts))
    return ('<article class="cx96-detail" aria-labelledby="cx96-detail-titre"><a class="cx96-retour" href="%s">Retour à la liste</a>%s%s%s%s%s%s</article>'
            % (e(liste, quote=True), tete, pourquoi, principal, lire, menu, histoire))


def page(desk, prefix, args, owner, role, tabs):
    """Corps de la vue : liste à gauche, fil choisi à droite ; tout vient de la base locale."""
    hidden = _hidden(desk, owner, role)
    zone = cx._fuseau(desk, owner)
    filtre = str(args.get('filtre') or 'actifs')
    filtre = filtre if filtre in [k for k, _, _ in cx.FILTRES] else 'actifs'
    dossier, boite, fil = str(args.get('dossier') or ''), str(args.get('boite') or ''), str(args.get('fil') or '')
    rows = cx.lignes(desk, owner, hidden, filtre, dossier, boite)
    detail, choisi = '', None
    if fil or args.get('cle'):
        try:
            choisi = cx.ligne(desk, fil, owner, hidden, cle=str(args.get('cle') or ''))
            detail = _detail(desk, prefix, choisi, zone, filtre, dossier)
        except Stop as error:
            detail = '<article class="cx96-detail"><p class="ax-alert" role="alert">%s</p></article>' % e(ERREURS.get(str(error), 'Ce fil n’est pas disponible.'))
    cfg = desk.c.get('mail') or {}
    labels = {m['id']: matter_display(m) for m in load_matters(desk.c) if m['id'] not in hidden}
    options = lambda pairs, courant: ''.join('<option value="%s"%s>%s</option>' % (e(v, quote=True), ' selected' if v == courant else '', e(t)) for v, t in pairs)
    filtres = ('<form class="cx96-filtres" method="get" action="%s" role="search" aria-label="Filtrer les fils"><input type="hidden" name="vue" value="assistante">'
               '<label>Afficher <select name="filtre" data-filter>%s</select></label><label>Dossier <select name="dossier" data-filter>%s</select></label>'
               '<label>Boîte <select name="boite" data-filter>%s</select></label><button class="ax-btn ghost" type="submit">Filtrer</button></form>') % (
        e(prefix + '/courriels', quote=True), options([(k, t) for k, t, _ in cx.FILTRES], filtre), options([('', 'Tous')] + sorted(labels.items(), key=lambda x: x[1]), dossier),
        options([('', 'Toutes'), (str(cfg.get('inbox') or 'INBOX'), '%s · %s' % (cx._adresse(cfg) or 'Messagerie du cabinet', 'Boîte de réception'))], boite))
    items = ''.join(('<li><a class="cx96-ligne%s" href="%s"%s><span class="cx96-dossier">%s</span><strong class="cx96-objet">%s</strong><span class="cx96-de">%s</span>'
                     '<span class="cx96-meta"><time datetime="%s">%s</time> · %s</span><span class="cx96-statut" data-statut="%s">%s</span></a></li>') % (
        ' on' if choisi and choisi['fil'] == r['fil'] else '', e(_url(prefix, fil=r['fil'], filtre=filtre if filtre != 'actifs' else '', dossier=dossier, boite=boite), quote=True),
        ' aria-current="true"' if choisi and choisi['fil'] == r['fil'] else '', e(r['dossier'] or 'Dossier à préciser'), e(r['objet'][:140]), e(r['de'][:120]),
        e(r['recu'], quote=True), e(_date(r['recu'], zone)), e(r['boite']), e(r['statut'], quote=True), e(r['libelle'] + (' · ' + r['precision'] if r['precision'] else ''))) for r in rows)
    vide = '' if rows else '<p class="ax-muted ax-pad">Rien dans cette catégorie. Les courriels arrivent ici après leur relève par l’agent ; aucune lecture de la messagerie n’est faite à l’ouverture.</p>'
    corps = ('<section class="cx96" id="cx96" aria-label="À traiter avec le Pilote" data-fil-ouvert="%s"><div class="cx96-liste">'
             '<div class="cx96-tete"><h1>À traiter avec le Pilote</h1><p class="ax-muted">Les échanges qui appellent une action et les réponses déjà préparées. '
             'Rien n’est envoyé ni déplacé dans la messagerie.</p>%s</div><ul class="cx96-lignes" aria-label="Fils">%s</ul>%s</div>%s</section>') % (
        '1' if detail else '0', filtres, items, vide, detail or '<div class="cx96-detail cx96-vide"><div class="ax-empty"><h2>Choisissez un fil</h2>'
        '<p>Vous verrez son dossier, la raison de l’action, le fil à la demande et le projet de réponse.</p></div></div>')
    return tabs + corps


def reglage_html(desk, owner, role):
    """Carte de réglage (Paramètres › Courriels et accueil) : cabinet (administrateur) et compte. Désactivée par défaut."""
    from .ui_components5689 import case, formulaire
    cabinet, compte, effective = cx.option(desk, owner)
    valeur = 'cabinet' if compte is None else ('oui' if compte else 'non')
    choix = ''.join('<option value="%s"%s>%s</option>' % (v, ' selected' if v == valeur else '', e(t)) for v, t in (
        ('cabinet', 'Comme le cabinet'), ('oui', 'Activée pour mon compte'), ('non', 'Désactivée pour mon compte')))
    contenu = (case('cabinet', 'Proposer la vue à tout le cabinet', cabinet, aide='Réservé à l’administrateur.', desactive=role != 'administrateur') if role == 'administrateur' else '')
    contenu += '<div class="ui89-champ"><label for="cx96-compte">Pour mon compte</label><select id="cx96-compte" name="compte">%s</select></div>' % choix
    contenu += '<button class="ax-btn" type="submit">Enregistrer</button>'
    return ('<section class="ax-card" id="courriels5696" aria-labelledby="courriels5696-t"><h2 id="courriels5696-t">Vue « À traiter avec le Pilote »</h2>'
            '<p class="ax-muted">Troisième onglet de Courriels : les fils qui appellent une action, la raison et le projet de réponse. Désactivée par défaut ; '
            '« À relire » et « Boîte de réception » restent inchangées. La désactiver n’efface aucun suivi ni aucun projet. État pour vous : <strong>%s</strong>.</p>%s</section>') % (
        'activée' if effective else 'désactivée', formulaire('m5689/courriels/reglage', contenu, recharge=True))


def carte_guide(desk, env, prefix, admin=False, owner=''):
    """5.6.96 (intégration I06) : réglage de la vue « À traiter avec le Pilote » dans le guide ; même carte que Paramètres."""
    return reglage_html(desk, owner or 'cabinet', 'administrateur' if admin else 'avocat')


carte_guide.avec_compte = True
