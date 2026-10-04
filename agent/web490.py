"""Interface 4.9.0 : confort d'usage — dictée, envoi facultatif, application mobile, recherche unique, raccourcis."""
from .common import matter_display
from html import escape as e
import json

from . import mobile490, search490, send490, voice490
from .common import Stop, load_matters

SHORTCUTS = (
    ('/', 'Aller à la recherche', 'Hors d’un champ de saisie'),
    ('g puis a', 'Aujourd’hui', ''), ('g puis c', 'Courriels à relire', ''), ('g puis d', 'Documents', ''),
    ('g puis e', 'Échéances', ''), ('g puis r', 'Recherche', ''), ('g puis p', 'Progrès', ''),
    ('Ctrl + Entrée', 'Enregistrer le brouillon ouvert', 'Dans l’éditeur de brouillon'),
    ('Alt + D', 'Démarrer / arrêter la dictée', 'Dans l’éditeur de brouillon'),
    ('?', 'Afficher cette aide', ''),
)
VOICE_COMMANDS = (
    ('Ajoute que je suis disponible le 12', 'Ajoute la phrase « Je suis disponible le 12. » avant la formule de fin. Une date sans mois est signalée, jamais complétée.'),
    ('Remplace rendez-vous par entretien', 'Remplace le mot ou l’expression, à condition qu’elle n’apparaisse qu’une fois.'),
    ('Supprime la dernière phrase', 'Retire la dernière phrase du corps du message (jamais la formule d’appel ou de fin).'),
    ('Nouveau paragraphe / À la ligne', 'Insère un saut au curseur.'),
    ('Reformule : plus courtois', 'Prépare une reformulation par l’IA ; vous validez avant tout remplacement.'),
    ('Annule', 'Annule la dernière modification dictée.'),
    ('Enregistre', 'Enregistre le brouillon dans la messagerie. Rien n’est envoyé.'),
    ('Envoie…', 'REFUSÉ : l’envoi ne se commande jamais à la voix.'),
)


def _matter_options(desk, selected=''):
    return '<option value="">Tous les dossiers</option>' + ''.join(
        '<option value="%s"%s>%s</option>' % (e(m['id'], quote=True), ' selected' if m['id'] == selected else '',
                                               e(matter_display(m))) for m in load_matters(desk.c))


# ----------------------------------------------------------------------------------------------- recherche
def recherche_page(desk, auth, prefix, args, shell):
    st = search490.status(desk)
    kinds = ''.join('<label class="cf-check"><input type="checkbox" name="kind" value="%s" checked> %s</label>' % (k, e(v)) for k, v in search490.KINDS.items())
    body = ('<h1>Recherche</h1>'
            '<p class="ax-muted">Une seule barre pour les courriels, les fichiers, l’agenda et les notes. La recherche est entièrement locale : '
            'rien n’est envoyé à un modèle ni à un service externe, et la requête n’est pas conservée.</p>'
            '<form id="cf-search" class="cf-search" role="search" autocomplete="off">'
            '<label class="cf-label" for="cf-q">Votre recherche</label>'
            '<div class="cf-row"><input type="search" id="cf-q" name="q" maxlength="200" placeholder="ex. mise en demeure Dupont, audience 12 novembre, « clause pénale »" value="%s" autofocus>'
            '<button class="ax-btn" type="submit">Rechercher</button></div>'
            '<div class="cf-filters"><label class="cf-label" for="cf-matter">Dossier</label><select id="cf-matter" name="matter">%s</select>'
            '<label class="cf-label" for="cf-from">Du</label><input type="date" id="cf-from" name="from">'
            '<label class="cf-label" for="cf-to">Au</label><input type="date" id="cf-to" name="to"></div>'
            '<fieldset class="cf-kinds"><legend>Sources</legend>%s</fieldset></form>'
            '<div id="cf-status" class="vf-note" role="status" aria-live="polite"></div>'
            '<div id="cf-results" class="cf-results" aria-live="polite"></div>'
            '<details class="cf-index"><summary>État de l’index</summary>%s</details>') % (
        e(args.get('q', ''), quote=True), _matter_options(desk, args.get('matter', '')), kinds, index_html(st))
    return shell('Recherche', body, prefix, auth['csrf'], '/recherche')


def index_html(st):
    t = st['timing']
    rows = ''.join('<tr><th scope="row">%s</th><td>%d</td></tr>' % (e(search490.KINDS[k]), st['counts'].get(k, 0)) for k in search490.KINDS)
    speed = ('Durée médiane %s ms · 95e centile %s ms · maximum %s ms (sur %d recherches). Objectif : moins de %d ms.' % (
        t['median_ms'], t['p95_ms'], t['max_ms'], t['samples'], st['target_ms'])) if t['samples'] else 'Aucune recherche mesurée pour l’instant.'
    return ('<table class="vf-table"><thead><tr><th>Source</th><th>Éléments indexés</th></tr></thead><tbody>%s</tbody></table>'
            '<p class="vf-note">%s</p><p class="vf-note">Courriels lus dans la messagerie : %d%s. %s</p>') % (
        rows, e(speed), st['mail_indexed'], (' (le plus ancien : %s)' % e(st['mail_oldest'])) if st['mail_oldest'] else '',
        'L’indexation des anciens courriels est terminée.' if st['backfill_done'] else 'L’indexation des anciens courriels se poursuit par passes régulières, des plus récents aux plus anciens.')


# ------------------------------------------------------------------------------------------------ confort
def confort_page(desk, auth, prefix, args, shell):
    snd = send490.status(desk)
    mob = mobile490.status(desk)
    srch = search490.status(desk)
    voice_rows = ''.join('<tr><th scope="row">« %s »</th><td>%s</td></tr>' % (e(c), e(d)) for c, d in VOICE_COMMANDS)
    audio = bool(desk.c.get('audio', {}).get('enabled'))
    matters = ''.join(
        '<tr><th scope="row">%s</th><td><label><input type="checkbox" class="cf-matter-send" data-matter="%s"%s%s> Autoriser l’envoi pour ce dossier</label></td></tr>' % (
            e(m['label']), e(m['id'], quote=True), ' checked' if m['enabled'] else '', '' if snd['enabled'] else ' disabled') for m in snd['matters'])
    log = ''.join('<tr><td>%s</td><td>%s</td><td>%d</td><td>%s</td></tr>' % (e(r['at'][:19]), e(r['matter']), r['recipients'], e(r['status'] + (' — ' + r['detail'] if r['detail'] else ''))) for r in snd['recent'])
    body = ('<h1>Confort d’usage</h1><nav class="cf-toc" aria-label="Sections"><a href="#voix">Dictée</a> <a href="#envoi">Envoi</a> <a href="#mobile">Mobile</a> <a href="#recherche">Recherche</a> <a href="#clavier">Clavier</a></nav>'
            '<section id="voix"><h2>Dictée vocale</h2><p>%s</p>'
            '<p>Dans l’éditeur de brouillon, le bouton « Dicter » enregistre votre voix, la transcrit <strong>sur le serveur du cabinet</strong> (passerelle Vocal locale) puis interprète des commandes simples. '
            'Chaque modification est annonçable à l’écran, annulable, et n’est enregistrée que sur « Enregistre » ou sur le bouton.</p>'
            '<table class="vf-table"><thead><tr><th>Vous dites</th><th>Effet</th></tr></thead><tbody>%s</tbody></table></section>'
            '<section id="envoi"><h2>Envoi depuis l’interface <span class="vf-badge %s">%s</span></h2>'
            '<p>Fonction <strong>facultative, désactivée par défaut</strong>. Sans elle, rien n’est jamais envoyé depuis AxiorHub : le bouton « Ouvrir dans la messagerie » ouvre le brouillon exact dans Roundcube. '
            'Activée, elle ne part <strong>jamais</strong> sans un second clic affichant destinataires et objet, uniquement pour les dossiers que vous autorisez un par un ; l’agent, lui, n’envoie jamais.</p>'
            '%s</section>'
            '<section id="mobile"><h2>Application mobile</h2>%s</section>'
            '<section id="recherche"><h2>Recherche</h2>%s'
            '<div class="ax-actions"><label><input type="checkbox" id="cf-mailindex"%s> Indexer le texte des courriels (lecture seule de la messagerie)</label>'
            '<button type="button" class="ax-btn ghost" id="cf-index-now">Lancer une passe maintenant</button>'
            '<button type="button" class="ax-btn danger" id="cf-purge">Vider l’index de recherche</button></div></section>'
            '<section id="clavier"><h2>Raccourcis clavier</h2><table class="vf-table"><thead><tr><th>Touches</th><th>Action</th><th>Où</th></tr></thead><tbody>%s</tbody></table>'
            '<p class="vf-note">Tous les boutons restent accessibles au clavier (Tab / Maj+Tab) ; les annonces d’état sont lues par les lecteurs d’écran ; le mode sombre suit le réglage de votre appareil.</p></section>') % (
        ('La passerelle Vocal locale est activée.' if audio else 'La passerelle Vocal locale n’est pas activée : la dictée vocale est indisponible tant qu’elle ne l’est pas (voir le guide d’installation).'),
        voice_rows, 'ok' if snd['enabled'] else 'muted', 'activé' if snd['enabled'] else 'désactivé', send_html(snd, matters, log),
        mobile_html(mob), index_html(srch), ' checked' if srch['mail_indexing'] else '',
        ''.join('<tr><th scope="row"><kbd>%s</kbd></th><td>%s</td><td>%s</td></tr>' % (e(k), e(a), e(w)) for k, a, w in SHORTCUTS))
    return shell('Confort d’usage', body, prefix, auth['csrf'], '/confort')


def send_html(snd, matters, log):
    if not snd['smtp_configured']:
        head = ('<p class="ax-alert" role="status">Aucun serveur d’envoi (SMTP) n’est configuré : la fonction ne peut pas être activée. '
                'Ajoutez la section <code>mail.smtp</code> (hôte, port, sécurité, identifiant, fichier du mot de passe) puis rechargez. Le bouton « Ouvrir dans la messagerie » fonctionne sans cela.</p>')
    else:
        head = ('<div class="ax-actions"><label><input type="checkbox" id="cf-send-confirm"> Je confirme vouloir permettre l’envoi depuis AxiorHub, après double confirmation à chaque message.</label>'
                '<button type="button" class="ax-btn" id="cf-send-on"%s>Activer l’envoi</button>'
                '<button type="button" class="ax-btn ghost" id="cf-send-off"%s>Désactiver</button></div>') % (
            ' disabled' if snd['enabled'] else '', '' if snd['enabled'] else ' disabled')
    table = ('<table class="vf-table"><thead><tr><th>Dossier</th><th>Autorisation</th></tr></thead><tbody>%s</tbody></table>' % matters) if matters else ''
    journal = ('<h3>Envois récents (30 jours)</h3><table class="vf-table"><thead><tr><th>Date (UTC)</th><th>Dossier</th><th>Destinataires</th><th>Issue</th></tr></thead><tbody>%s</tbody></table>' % log) if log else ''
    return head + table + journal + ('<p class="vf-note">Garde-fous : activation générale + par dossier, double confirmation avec destinataires et objet, contrôle que le brouillon n’a pas changé depuis l’aperçu, '
                                     'refus des brouillons « À VÉRIFIER », jamais deux fois le même contenu, plafond de %d envois par heure, copie dans les éléments envoyés. Le journal ne contient ni texte ni objet.</p>' % snd['max_per_hour'])


def mobile_html(mob):
    note = '' if mob['available'] else '<p class="ax-alert">Les notifications exigent le paquet Python « cryptography » (non installé) : l’application s’installe et fonctionne, sans notifications poussées.</p>'
    s = mob['summary']
    return ('<p>Ouvrez AxiorHub dans le navigateur de votre téléphone puis choisissez « Ajouter à l’écran d’accueil » (ou « Installer l’application »). '
            'Hors connexion, l’application n’affiche <strong>aucun dossier</strong> et rien du cabinet n’est conservé sur le téléphone.</p>'
            '%s<div class="ax-actions"><button type="button" class="ax-btn" id="cf-push-on" data-key="%s"%s>Activer les notifications sur cet appareil</button>'
            '<button type="button" class="ax-btn ghost" id="cf-push-off">Désactiver (tous les appareils)</button>'
            '<button type="button" class="ax-btn ghost" id="cf-push-test"%s>Envoyer une notification d’essai</button></div>'
            '<p class="vf-note" id="cf-push-state" role="status">Appareils enregistrés : %d.</p>'
            '<p class="vf-note"><strong>Ce qu’une notification dit :</strong> uniquement des nombres, par exemple « %s ». Jamais un nom de client, un objet, un extrait ni un nom de dossier. '
            'Le service de notification de votre navigateur (Google, Mozilla ou Apple) ne reçoit qu’un signal vide.</p>') % (
        note, e(mob['public_key'], quote=True), '' if mob['available'] else ' disabled', '' if mob['available'] and mob['subscriptions'] else ' disabled',
        mob['subscriptions'], e(s['text'] or '2 brouillons à valider · 1 échéance proche'))


# --------------------------------------------------------------------------------------------------- API
def _lst(value):
    if isinstance(value, str):
        value = value.split(',')
    return [str(x).strip() for x in (value or []) if str(x).strip()][:6]


def handle(desk, name, data, method='POST', args=None):
    args = args or {}
    if method == 'GET':
        if name == 'search/query':
            return search490.search(desk, args.get('q', ''), args.get('matter', ''), args.get('from', ''), args.get('to', ''),
                                    _lst(args.get('kinds')) or None, int(args.get('limit', 30) or 30))
        if name == 'search/status':
            return search490.status(desk)
        if name == 'send/status':
            return send490.status(desk)
        if name == 'send/link':
            return send490.webmail_link(desk, args.get('uid', ''), args.get('validity', ''))
        if name == 'mobile/summary':
            return mobile490.summary(desk)
        if name == 'mobile/status':
            return mobile490.status(desk)
        raise Stop('route_inconnue')
    if name == 'voice/apply':
        return voice490.interpret(str(data.get('transcript', '')), str(data.get('body', '')))
    if name == 'send/settings':
        return send490.set_enabled(desk, bool(data.get('enabled')), str(data.get('confirm', '')))
    if name == 'send/matter':
        return send490.set_matter(desk, str(data.get('matter', '')), bool(data.get('enabled')), str(data.get('confirm', '')))
    if name == 'send/prepare':
        return send490.prepare(desk, str(data.get('uid', '')), str(data.get('uidvalidity', '')))
    if name == 'send/confirm':
        return send490.confirm(desk, str(data.get('token', '')))
    if name == 'search/index':
        desk.enqueue('search_index490', priority=0)
        return {'queued': True}
    if name == 'search/mail':
        return search490.set_mail_indexing(desk, bool(data.get('enabled')))
    if name == 'search/purge':
        return search490.purge(desk, str(data.get('confirm', '')))
    if name == 'mobile/subscribe':
        return mobile490.subscribe(desk, str(data.get('endpoint', '')), str(data.get('label', '')), str(data.get('confirm', '')))
    if name == 'mobile/unsubscribe':
        return mobile490.unsubscribe(desk, str(data.get('endpoint', '')), bool(data.get('all')))
    if name == 'mobile/test':
        return mobile490.dispatch(desk, force=True)
    raise Stop('route_inconnue')
