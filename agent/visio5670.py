"""5.6.70 : visioconférence libre Jitsi Meet, hébergée par le cabinet.

- Réglage : l'adresse publique du serveur Jitsi du cabinet (Paramètres › Visioconférence, administrateur). Le serveur s'installe avec
  ``deploy/jitsi/installer-jitsi.sh`` sur des ports qui ne gênent pas les autres services (interface web en local sur 10080, flux
  audio et vidéo en UDP 10010), derrière le proxy HTTPS existant (``deploy/jitsi/apache-visio.example.conf``).
- Une salle = un nom long et aléatoire (jamais le nom du client ni du dossier) : le lien ne se devine pas. Avec l'authentification
  du serveur (installée par défaut), seuls les comptes du cabinet ouvrent une salle ; les invités la rejoignent par le lien.
- Le lien s'ajoute à un rendez-vous de l'agenda (« Visioconférence ») ou s'insère dans un courriel (bouton de la messagerie).
"""
import re
import secrets
from urllib.parse import urlsplit

from .common import Stop

SETTING = 'visio5670:jitsi'


def config(desk):
    raw = desk.settings(SETTING, {}) or {}
    return {'url': str(raw.get('url') or '').rstrip('/'), 'enabled': bool(raw.get('enabled')) and bool(raw.get('url'))}


def save(desk, actor, url, enabled=True):
    url = str(url or '').strip().rstrip('/')
    if url:
        parts = urlsplit(url)
        if parts.scheme != 'https' or not parts.hostname or parts.query or parts.fragment or parts.username or parts.password:
            raise Stop('adresse_visio_https_requise')
        if parts.path not in ('', '/'):
            raise Stop('adresse_visio_sans_chemin')
    desk.setting(SETTING, {'url': url, 'enabled': bool(enabled and url)})
    desk.audit('visio5670_reglage', {'url': url, 'actif': bool(enabled and url), 'par': str(actor)[:80]})
    return {'ok': True, 'message': 'Visioconférence %s.' % ('activée : ' + url if enabled and url else 'désactivée')}


def room(desk, title=''):
    """Lien d'une nouvelle salle : préfixe neutre + 20 caractères aléatoires (aucune donnée du dossier dans l'adresse)."""
    cfg = config(desk)
    if not cfg['enabled']:
        raise Stop('visio_non_configuree')
    name = 'Cabinet-' + re.sub(r'[^A-Za-z0-9]', '', secrets.token_urlsafe(20))[:20]
    return {'url': cfg['url'] + '/' + name, 'salle': name}


def with_link(desk, data):
    """Rendez-vous de l'agenda : lien ajouté au lieu (s'il est vide) et aux notes quand « visio » est demandée."""
    if str(data.get('visio') or '') not in ('1', 'true', 'on', 'oui'):
        return data
    link = room(desk)['url']
    out = dict(data)
    out['location'] = (str(out.get('location') or '').strip() + ' · ' if str(out.get('location') or '').strip() else '') + 'Visioconférence : ' + link
    notes = str(out.get('description') if 'description' in out else out.get('notes') or '').strip()
    text = (notes + '\n\n' if notes else '') + 'Visioconférence (Jitsi) : ' + link
    if 'description' in out:
        out['description'] = text
    else:
        out['notes'] = text
    return out


def check(desk):
    """Test de joignabilité de la page d'accueil Jitsi (aucune donnée envoyée)."""
    from .common import HTTP
    cfg = config(desk)
    if not cfg['url']:
        raise Stop('visio_non_configuree')
    try:
        raw = HTTP(cfg['url'], timeout=8).request('GET', cfg['url'] + '/', None, {'Accept': 'text/html'}, 400_000)
    except Stop as ex:
        raise Stop('serveur_visio_injoignable') from ex
    ok = b'jitsi' in raw.lower() or b'JitsiMeetJS' in raw
    return {'ok': ok, 'message': 'Serveur Jitsi joignable.' if ok else 'La page répond, mais ne ressemble pas à Jitsi Meet : vérifiez l’adresse.'}


def settings_html(desk, prefix, role):
    from html import escape as e
    cfg = config(desk)
    if role != 'administrateur':
        return ('<section class="ax-card" id="visio5670"><h2>Visioconférence</h2><p>%s</p></section>' % (
            ('Serveur du cabinet : <strong>%s</strong>.' % e(cfg['url'])) if cfg['enabled'] else 'Non configurée (réglage administrateur).'))
    return ('<section class="ax-card" id="visio5670"><h2>Visioconférence (Jitsi Meet)</h2><p class="ax-muted">Serveur de visioconférence libre hébergé '
            'par le cabinet. Installation : <code>sudo bash deploy/jitsi/installer-jitsi.sh --domaine …</code> (ports 10080 en local et 10010/UDP), puis un sous-domaine HTTPS '
            'servi par votre proxy (<code>deploy/jitsi/apache-visio.example.conf</code>). Les liens générés ne contiennent aucune donnée du dossier.</p>'
            '<form class="m5-form" data-api="m5670/visio/reglage" data-reload><label>Adresse publique (https://…)<input name="url" type="url" value="%s" '
            'placeholder="https://visio.example.test" maxlength="200"></label><label class="m5-check"><input type="checkbox" name="enabled" value="1"%s> '
            'Proposer la visioconférence dans l’agenda et la messagerie</label><button class="ax-btn" type="submit">Enregistrer</button>'
            '<p role="status" class="m5-status"></p></form>%s</section>') % (
        e(cfg['url'], quote=True), ' checked' if cfg['enabled'] or not cfg['url'] else '',
        ('<form class="m5-form m5-inline" data-api="m5670/visio/tester"><button class="ax-btn ghost" type="submit">Tester le serveur</button>'
         '<p role="status" class="m5-status"></p></form>') if cfg['url'] else '')
