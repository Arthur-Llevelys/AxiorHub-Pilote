"""5.6.101 (lot S, S-U1 à S-U4) : page « Sécurité du serveur » et routes ``/api440/m56101/securite/…``.

- ``GET /securite-serveur`` : administrateur seulement ; tout autre rôle reçoit la même réponse qu'une page absente (404 neutre).
  Poste Windows ou Ubuntu : la page dit « non disponible sur ce poste ». Lecture seule : la page lit les tables remplies par la
  maintenance (``securite56101.ingerer``) ; aucune analyse, aucune écriture pendant l'affichage (R05).
- ``POST /api440/m56101/securite/connu`` (motif obligatoire, empreinte affichée, clé d'idempotence ; 409 si le constat a changé),
  ``…/fiche`` (brouillon de fiche d'incident, jamais envoyé), ``…/veille`` (demande d'activation ou de désactivation des minuteurs,
  appliquée par le service privilégié ; AxiorHub n'appelle jamais ``systemctl``). ``GET …/etat`` : état général en JSON.
- Réponses privées : ``Cache-Control: private, no-store``.
"""
from html import escape as e
from urllib.parse import quote

from .common import Stop
from . import securite56101 as s

PAGE = '/securite-serveur'
PREFIXE = 'm56101/securite'
ECRITURES = ('connu', 'fiche', 'veille')
LECTURES = ('etat',)
MAX_POST = 8_000
STATUTS = {403: '403 Forbidden', 404: '404 Not Found', 409: '409 Conflict', 422: '422 Unprocessable Entity', 503: '503 Service Unavailable'}
MESSAGES = {'introuvable': 'Élément introuvable.', 'version_perimee': 'Ce constat a changé depuis l’affichage : rechargez la page avant de le marquer comme connu.',
            'motif_requis': 'Indiquez le motif (au moins quelques mots) : il est inscrit au journal d’audit.',
            'cle_requise': 'Demande incomplète : rechargez la page.', 'cle_reutilisee': 'Cette demande a déjà servi pour une autre action.',
            'action_invalide': 'Action inconnue.', 'veille_non_installee': 'La veille de sécurité n’est pas installée sur ce serveur.',
            'origine_ou_csrf_refuse': 'Session expirée ou origine refusée : rechargez la page.', 'requete_trop_longue': 'Demande trop longue.',
            'json_requis': 'Format de demande refusé.', 'json_invalide': 'Format de demande refusé.', 'objet_json_requis': 'Format de demande refusé.'}
AIDE = {'S01': 'Processeur occupé plus de 30 minutes par un programme inconnu.', 'S02': 'Programme lancé puis effacé du disque.',
        'S03': 'Programme lancé depuis un dossier temporaire ou web.', 'S04': 'Mineur connu ou connexion vers un port de minage.',
        'S05': 'Tâche planifiée d’un compte de service ; compte web absent de /etc/cron.deny.',
        'S06': 'Fichiers de démarrage ajoutés ou modifiés (systemd, cron, ld.so.preload, sudoers, clés SSH).',
        'S07': 'Fichier de secrets lisible par le compte web (noms des réglages seulement, jamais leurs valeurs).',
        'S08': 'Racine de site trop large ; sonde locale de /.env, /.git/config et /config/config.php.',
        'S09': 'Application web plus ancienne que la version minimale sûre connue (table embarquée, sans vérification en ligne).',
        'S10': 'Dossier web appartenant au compte web ; script d’un administrateur dans un dossier modifiable par lui.',
        'S11': 'Ports Docker ouverts sur toutes les interfaces (Docker contourne ufw) ; terminaux web et automatisation.',
        'S12': 'Échecs de connexion sur 24 h ; Fail2ban absent ou arrêté.', 'S13': 'Droits des fichiers de configuration, de secrets et de rapports d’AxiorHub.'}
COULEURS = {'vert': ('Aucun constat ouvert', 's101-vert'), 'orange': ('Constats importants à examiner', 's101-orange'),
            'rouge': ('Constat critique : agir sans attendre', 's101-rouge'), 'gris': ('Aucun rapport de la veille', 's101-gris')}


def allowed(role, method, api):
    """``standalone_auth.allowed`` : les routes de la veille sont réservées à l'administrateur (liste fermée)."""
    if role != 'administrateur':
        return False
    reste = str(api or '').split('?', 1)[0][len(PREFIXE):].strip('/')
    return (method == 'POST' and reste in ECRITURES) or (method in ('GET', 'HEAD') and reste in LECTURES)


def _prive(out):
    out['headers'] = [h for h in out.get('headers', []) if h[0].lower() != 'cache-control'] + [('Cache-Control', 'private, no-store')]
    return out


def _json(data, status='200 OK'):
    from .web440 import json_out
    return _prive(json_out(data, status))


# ------------------------------------------------------------------------------------------------------------------- page
def page_route(env, cfg, auth, prefix, path, args, shell):
    from .desk import Desk
    from .web440 import page_out
    from .web567 import actor
    if actor(env)[1] != 'administrateur':
        return None                       # même réponse qu'une page absente
    if not s.disponible(auth):
        corps = ('<section class="s101" aria-labelledby="s101-titre"><h1 id="s101-titre">Sécurité du serveur</h1>'
                 '<p class="s101-note">Non disponible sur ce poste : la veille de sécurité surveille le serveur Linux du cabinet (version serveur '
                 'd’AxiorHub). Rien n’est installé sur un poste de travail Windows ou Ubuntu.</p></section>')
        return _prive(page_out(shell('Sécurité du serveur', corps, prefix, auth['csrf'], PAGE)))
    desk = Desk(cfg)
    try:
        corps = page_html(desk, prefix, args)
    finally:
        desk.db.close()
    head = '<script defer src="%s/static/v56101-securite.js"></script>' % e(prefix, quote=True)
    return _prive(page_out(shell('Sécurité du serveur', corps, prefix, auth['csrf'], PAGE, head=head)))


def _bloc_commandes(commandes, ident):
    if not commandes:
        return ''
    texte = '\n'.join(commandes)
    return ('<div class="s101-commandes"><div class="s101-commandes-tete"><h4 id="cmd-%(i)s">Commandes à copier</h4>'
            '<button type="button" class="ax-btn ghost" data-s101-copier="pre-%(i)s" aria-describedby="cmd-%(i)s">Copier</button></div>'
            '<pre id="pre-%(i)s" tabindex="0" aria-labelledby="cmd-%(i)s"><code>%(t)s</code></pre>'
            '<p class="s101-mini">À lancer par vous ou votre prestataire après lecture : AxiorHub n’exécute aucune commande sur le serveur.</p></div>') % {
        'i': ident, 't': e(texte)}


def _constat_html(desk, prefix, x, connu=False):
    import hashlib
    ident = hashlib.sha256(x['cle'].encode()).hexdigest()[:16]
    vu = ''.join('<li>%s</li>' % e(v) for v in x['vu'])
    tete = ('<article class="s101-constat s101-n-%(n)s" id="c-%(i)s" aria-labelledby="t-%(i)s">'
            '<header><span class="s101-niveau s101-n-%(n)s">%(l)s</span> <span class="s101-id">%(c)s</span>'
            '<h3 id="t-%(i)s">%(t)s</h3><p class="s101-mini">Vu pour la première fois le %(p)s ; dernier passage le %(d)s.</p></header>'
            '<p>%(x)s</p><h4>Ce qui a été vu</h4><ul class="s101-vu">%(v)s</ul>') % {
        'n': e(x['niveau'], quote=True), 'i': ident, 'l': e(x['libelle']), 'c': e(x['controle']), 't': e(x['titre']),
        'p': e(s.heure_locale(desk, x['premier'])), 'd': e(s.heure_locale(desk, x['dernier'])), 'x': e(x['explication']), 'v': vu}
    corps = _bloc_commandes(x['commandes'], ident) + '<p><a href="#aide-%s">Aide sur ce contrôle</a></p>' % e(x['controle'], quote=True)
    if connu:
        c = x.get('connu') or {}
        corps += '<p class="s101-mini">Marqué comme connu par %s le %s. Motif : %s</p>' % (e(c.get('auteur', '')), e(s.heure_locale(desk, c.get('at', ''))), e(c.get('motif', '')))
    else:
        corps += ('<form class="s101-connu" data-s101-connu data-url="%(u)s" data-cle="%(k)s" data-empreinte="%(h)s" novalidate>'
                  '<label for="m-%(i)s">Motif (obligatoire, inscrit au journal d’audit)</label>'
                  '<textarea id="m-%(i)s" name="motif" rows="2" maxlength="500" required minlength="3"></textarea>'
                  '<button type="submit" class="ax-btn ghost">Marquer comme connu</button>'
                  '<p class="s101-statut" role="status" aria-live="polite"></p></form>') % {
            'u': e(prefix + '/api440/' + PREFIXE + '/connu', quote=True), 'k': e(x['cle'], quote=True), 'h': e(x['empreinte'], quote=True), 'i': ident}
    return tete + corps + '</article>'


def page_html(desk, prefix, args=None):
    args = args or {}
    st = s.etat(desk)
    libelle, classe = COULEURS[st['couleur']]
    if st['desactivee']:
        alerte = '<p class="s101-alerte" role="status">Veille désactivée par un administrateur : aucun passage n’a lieu.</p>'
    elif st['arretee']:
        alerte = ('<p class="s101-alerte" role="status">Veille arrêtée ? Le dernier rapport a plus de %d minutes%s. Vérifiez : '
                  '<code>systemctl list-timers \'axiorhub-veille-securite-*\'</code></p>') % (
            s.ARRET_MINUTES, '' if not st['jamais'] else ' (aucun rapport reçu)')
    else:
        alerte = ''
    if st['erreurs']:
        alerte += '<p class="s101-alerte" role="status">Contrôles en erreur au dernier passage : %s. Leurs constats précédents restent affichés.</p>' % e(', '.join(st['erreurs']))
    if st.get('demande'):
        alerte += '<p class="s101-note" role="status">Une demande d’activation ou de désactivation attend le service système.</p>'
    out = ['<section class="s101" aria-labelledby="s101-titre"><h1 id="s101-titre">Sécurité du serveur</h1>',
           '<div class="s101-etat %s"><p class="s101-pastille"><strong>%s</strong> — %d critique%s, %d important%s, %d à surveiller</p>'
           '<p class="s101-mini">Dernier passage rapide (toutes les 10 minutes) : %s. Dernier passage complet (chaque nuit à 4 h 40) : %s.</p>%s</div>' % (
               classe, e(libelle), st['critiques'], '' if st['critiques'] == 1 else 's', st['importants'], '' if st['importants'] == 1 else 's',
               st['surveiller'], e(s.heure_locale(desk, st['rapide'])), e(s.heure_locale(desk, st['complet'])), alerte),
           '<p class="s101-note">AxiorHub observe et explique : il n’arrête, ne supprime ni ne modifie rien sur le serveur. Les commandes proposées '
           'sont à copier et à lancer par vous ou votre prestataire.</p>']
    ouverts = s.constats(desk)
    out.append('<h2 id="s101-ouverts">Constats ouverts</h2>')
    out += [_constat_html(desk, prefix, x) for x in ouverts] or ['<p class="empty">Aucun constat ouvert.</p>']
    connus = s.constats(desk, connus=True)
    if connus:
        out.append('<details class="s101-connus"><summary>Constats marqués comme connus (%d)</summary>%s</details>' % (
            len(connus), ''.join(_constat_html(desk, prefix, x, connu=True) for x in connus)))
    out.append(_fiche_html(desk, prefix, args))
    out.append(_hebdo_html(desk))
    out.append(_historique_html(desk))
    out.append(_veille_html(prefix, st))
    out.append('<section class="s101-aide" aria-labelledby="s101-aide-titre"><h2 id="s101-aide-titre">Aide : les contrôles</h2><dl>%s</dl>'
               '<p class="s101-mini">Bilan et durcissement guidés du serveur : <code>%s</code> (sans option : bilan seul, rien n’est modifié).</p></section>' % (
                   ''.join('<dt id="aide-%s">%s</dt><dd>%s</dd>' % (k, k, e(v)) for k, v in AIDE.items()),
                   'sudo bash /opt/axiorhub-mail-agent/current/deploy/securite/durcir-serveur.sh'))
    out.append('</section>')
    return ''.join(out)


def _fiche_html(desk, prefix, args):
    cases = ''.join('<li><label><input type="checkbox" name="mesures" value="%s"> %s</label></li>' % (e(code, quote=True), e(libelle)) for code, libelle in s.MESURES)
    html = ('<section class="s101-fiche" id="fiche" aria-labelledby="s101-fiche-titre"><h2 id="s101-fiche-titre">Fiche d’incident</h2>'
            '<p>Brouillon pour le registre des violations de données (article 33.5 du RGPD) : chronologie tirée des rapports, constats, mesures '
            'déjà prises. Il est déposé dans « À décider / À relire » et n’est jamais envoyé.</p>'
            '<form data-s101-fiche data-url="%s"><fieldset><legend>Mesures déjà prises</legend><ul>%s</ul></fieldset>'
            '<button type="submit" class="ax-btn">Préparer une fiche d’incident</button><p class="s101-statut" role="status" aria-live="polite"></p></form>') % (
        e(prefix + '/api440/' + PREFIXE + '/fiche', quote=True), cases)
    ident = str(args.get('fiche') or '')
    f = s.fiche(desk, ident) if ident else None
    if f:
        html += ('<h3>Brouillon du %s</h3><label for="s101-fiche-texte">Texte du brouillon (à copier dans le registre)</label>'
                 '<textarea id="s101-fiche-texte" rows="18" readonly>%s</textarea>') % (e(s.heure_locale(desk, f['cree'])), e(f['texte']))
    return html + '</section>'


def _hebdo_html(desk):
    r = s.resume_hebdomadaire(desk)
    charge = ('%s (sur %d cœur%s)' % (str(r['charge']).replace('.', ','), r['coeurs'], 's' if r['coeurs'] > 1 else '')) if r['charge'] is not None else 'inconnue'
    surveiller = ''.join('<li>%s</li>' % e(t) for t in r['surveiller']) or '<li>Aucun.</li>'
    return ('<section class="s101-hebdo" aria-labelledby="s101-hebdo-titre"><h2 id="s101-hebdo-titre">Résumé des sept derniers jours</h2>'
            '<ul><li>Charge moyenne : %s</li><li>Passages : %d</li><li>Constats ouverts : %d</li><li>Nouveaux : %d</li><li>Résolus : %d</li></ul>'
            '<h3>À surveiller</h3><ul>%s</ul></section>') % (e(charge), r['passages'], r['ouverts'], r['nouveaux'], r['resolus'], surveiller)


def _historique_html(desk):
    lignes = s.historique(desk)
    if not lignes:
        return '<section class="s101-historique"><h2>Historique sur 30 jours</h2><p class="empty">Aucun passage enregistré.</p></section>'
    corps = ''.join('<tr><th scope="row">%s</th><td>%d</td><td>%d</td><td>%d</td><td>%d</td><td>%s</td></tr>' % (
        e(l['jour'][8:10] + '/' + l['jour'][5:7] + '/' + l['jour'][:4]), l['passages'], l['critiques'], l['importants'], l['surveiller'],
        e(str(l['charge']).replace('.', ',') if l['charge'] is not None else '—')) for l in lignes)
    return ('<section class="s101-historique" aria-labelledby="s101-hist-titre"><h2 id="s101-hist-titre">Historique sur 30 jours</h2><div class="s101-table">'
            '<table><thead><tr><th scope="col">Jour</th><th scope="col">Passages</th><th scope="col">Critiques</th><th scope="col">Importants</th>'
            '<th scope="col">À surveiller</th><th scope="col">Charge moyenne</th></tr></thead><tbody>%s</tbody></table></div></section>') % corps


def _veille_html(prefix, st):
    action, libelle = ('activer', 'Réactiver la veille') if st['desactivee'] else ('desactiver', 'Désactiver la veille')
    return ('<section class="s101-veille" aria-labelledby="s101-veille-titre"><h2 id="s101-veille-titre">Réglage de la veille</h2>'
            '<p>Active par défaut sur le serveur. La désactivation dépose une demande que le service système applique ; elle est inscrite au '
            'journal d’audit. Équivalent en ligne de commande : <code>sudo systemctl disable --now axiorhub-veille-securite-rapide.timer '
            'axiorhub-veille-securite-complete.timer</code></p>'
            '<button type="button" class="ax-btn ghost" data-s101-veille="%s" data-url="%s" data-confirmer="%s">%s</button>'
            '<p class="s101-statut" role="status" aria-live="polite"></p></section>') % (
        action, e(prefix + '/api440/' + PREFIXE + '/veille', quote=True),
        e('Désactiver la veille de sécurité du serveur ? Aucune intrusion ne sera plus signalée.' if action == 'desactiver' else 'Réactiver la veille ?', quote=True),
        e(libelle))


# ------------------------------------------------------------------------------------------------------------------- routes
def api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post
    from .web567 import actor
    owner, role = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    if not allowed(role, method, name) or not s.disponible(auth):
        return _json({'error': 'introuvable', 'message': MESSAGES['introuvable']}, STATUTS[404])
    try:
        if method in ('GET', 'HEAD'):
            st = s.etat(desk)
            return _json({k: st[k] for k in ('couleur', 'rapide', 'complet', 'arretee', 'desactivee', 'critiques', 'importants', 'surveiller', 'erreurs')})
        if int(env.get('CONTENT_LENGTH') or 0) > MAX_POST:
            raise Stop('requete_trop_longue')
        data = check_post(env, auth)
        if reste == 'connu':
            out = s.marquer_connu(desk, owner, role, data.get('cle'), data.get('empreinte'), data.get('motif'), data.get('idem'))
        elif reste == 'fiche':
            out = s.preparer_fiche(desk, owner, role, data.get('mesures'), data.get('idem'))
            out['href'] = prefix + PAGE + '?fiche=' + quote(out['id']) + '#fiche'
        else:
            out = s.demander_veille(desk, owner, role, data.get('action'), data.get('idem'))
        return _json(out)
    except Stop as ex:
        code = str(ex).split(':')[0]
        status = {'introuvable': 404, 'version_perimee': 409, 'cle_reutilisee': 409, 'origine_ou_csrf_refuse': 403}.get(code, 422)
        return _json({'error': code, 'message': MESSAGES.get(code, 'Demande refusée.')}, STATUTS[status])
