"""IA externe sûre (AxiorHub 5.4.0) : mode d'utilisation, aperçu de ce qui part, journal des envois.

Trois modes :
- « Tout en local » (par défaut) : Ollama sur le serveur pour toutes les fonctions ; rien ne sort.
- « Mixte » : le tri des courriels, la lecture des pièces jointes et le contrôle indépendant restent locaux (rapides, volumineux,
  confidentiels) ; la rédaction, l'assistant, l'analyse juridique, les audiences, les documents et Roundcube utilisent le fournisseur
  choisi (Anthropic, OpenAI, Mistral, OpenRouter).
- « Hybride » (4.0) : local par défaut, bascule vers OpenRouter au-delà d'un seuil de complexité (page « Routage hybride »).

Dans tous les cas, ce qui part vers un fournisseur non local est pseudonymisé de façon réversible (pseudo540) : c'est imposé au point
d'envoi (model.Model._leaf) et ne peut pas être désactivé.
"""
from datetime import datetime, timezone
from html import escape as e
import json
import re

from .common import Stop, load_matters, matter_display

LOCAL_PURPOSES = ('mail_triage', 'attachment_review', 'control')


def _routes(desk):
    return desk.c.get('model_routing', {}) or {}


def current(desk):
    from .ai_gateway import PURPOSES
    from .hybrid400 import policy
    mode = policy(desk.c)['mode']
    if mode == 'hybrid':
        return {'mode': 'hybride', 'label': 'Hybride : local, avec bascule vers OpenRouter au-delà d’un seuil'}
    if mode != 'manual':
        return {'mode': 'local', 'label': 'Tout en local : rien ne sort du serveur'}
    routes = _routes(desk)
    external = sorted({(r.get('provider'), r.get('model')) for p, r in routes.items() if p in PURPOSES and isinstance(r, dict) and r.get('provider') not in (None, '', 'ollama')})
    if not external:
        return {'mode': 'local', 'label': 'Tout en local : rien ne sort du serveur'}
    names = ', '.join('%s (%s)' % (p, m) for p, m in external)
    return {'mode': 'mixte', 'label': 'Mixte : tri, pièces jointes et contrôle en local ; rédaction et analyse via ' + names, 'external': external}


def _write_routes(desk, routes):
    for purpose, value in routes.items():
        desk.setting('ai:route:' + purpose, value)
        desk.c.setdefault('model_routing', {})[purpose] = value


def _set_mode(desk, mode):
    from .hybrid400 import normalize
    value = normalize({**(desk.c.get('hybrid_routing') or {}), 'mode': mode})
    value['anonymize_external'] = True
    desk.setting('ai:hybrid', value)
    desk.c['hybrid_routing'] = value


def set_local(desk):
    _set_mode(desk, 'local')
    desk.audit('ia540_mode', {'mode': 'local'})
    return {'message': 'Toutes les fonctions utilisent désormais le modèle local. Rien ne sort du serveur.'}


def apply_mixed(desk, provider_id, model, local_model='', test=True):
    from .ai_gateway import PURPOSES, provider_registry
    provider_id = str(provider_id or '').strip()
    provider = provider_registry(desk.c).get(provider_id)
    if not provider or provider_id == 'ollama' or provider.get('type') == 'ollama':
        raise Stop('fournisseur_externe_requis')
    if not provider.get('enabled'):
        raise Stop('fournisseur_ia_inactif')
    if not provider.get('external_data_allowed'):
        raise Stop('autorisation_donnees_externes_requise')
    model = str(model or provider.get('model') or '').strip()
    local_model = str(local_model or (desk.c.get('ollama') or {}).get('model') or '').strip()
    for value in (model, local_model):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}', value):
            raise Stop('modele_fournisseur_invalide')
    if test:
        from .model import Model
        Model({**provider, 'model': model})                # contrôle de la clé et de l'adresse ; aucun contenu n'est envoyé
    routes = {p: ({'provider': 'ollama', 'model': local_model} if p in LOCAL_PURPOSES else {'provider': provider_id, 'model': model}) for p in PURPOSES}
    _write_routes(desk, routes)
    _set_mode(desk, 'manual')
    desk.audit('ia540_mode', {'mode': 'mixte', 'provider': provider_id, 'model': model, 'local_model': local_model})
    return {'message': 'Mode mixte activé : tri, pièces jointes et contrôle en local (%s) ; rédaction et analyse via %s (%s), avec pseudonymisation.' % (
        local_model, provider_id, model)}


# ======================================================================================== page
def page(desk, prefix, shell, csrf):
    from .ai_gateway import ANTHROPIC_MODELS, provider_registry
    from . import pseudo540
    cur = current(desk)
    providers = [(k, v) for k, v in sorted(provider_registry(desk.c).items()) if k != 'ollama' and v.get('type') != 'ollama']
    usable = [(k, v) for k, v in providers if v.get('enabled') and v.get('external_data_allowed')]
    options = ''.join('<option value="%s">%s · %s</option>' % (e(k, quote=True), e(k), e(v.get('type', ''))) for k, v in usable)
    models = ''.join('<option value="%s">%s</option>' % (e(k, quote=True), e(v)) for k, v in ANTHROPIC_MODELS)
    models += ''.join('<option value="%s"></option>' % e(v.get('model', ''), quote=True) for _, v in usable if v.get('model'))
    local_model = (desk.c.get('ollama') or {}).get('model', '')
    mixed = ('<form class="m5-form" data-api="m540/mode" data-reload="1"><input type="hidden" name="mode" value="mixte">'
             '<label class="m5-field">Fournisseur pour la rédaction et l’analyse<select name="provider" required>%s</select></label>'
             '<label class="m5-field">Modèle<input name="model" list="ia540-models" maxlength="160" required placeholder="claude-sonnet-5-5"></label><datalist id="ia540-models">%s</datalist>'
             '<label class="m5-field">Modèle local pour le tri, les pièces jointes et le contrôle<input name="local_model" maxlength="160" value="%s"></label>'
             '<button class="ax-btn" type="submit">Activer le mode mixte</button></form>') % (options, models, e(local_model, quote=True)) if usable else (
        '<p class="m5-warn">Aucun fournisseur externe n’est encore activé et autorisé. Renseignez-en un (clé API, « Activer », « J’autorise l’envoi… ») dans '
        '<a href="%s">Paramètres › IA</a>.</p>' % e(prefix + '/parametres?tab=ia'))
    log = pseudo540.recent(desk.c['state_dir'], 30)
    rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%d</td><td><button type="button" class="ax-btn ghost m5-mini" data-p540-sample="%d">Voir</button></td></tr>' % (
        e(_local(x['at'])), e(x['provider']), e(x['model']), e(x['purpose']),
        e(', '.join('%s : %d' % (pseudo540.LABELS.get(k, k), n) for k, n in x['counts'].items()) or 'aucun remplacement'), x['characters'], x['id']) for x in log)
    labels = sorted(((m['id'], matter_display(m)) for m in _matters(desk)), key=lambda x: x[1].lower())
    matter_opts = ''.join('<option value="%s">%s</option>' % (e(k, quote=True), e(v)) for k, v in labels)
    body = ('<div class="ia540"><h1>IA externe sûre</h1>'
            '<p class="ax-muted">Ce que l’agent envoie hors du serveur, quand et sous quelle forme. Tout envoi à un fournisseur non local est '
            '<strong>pseudonymisé sur le serveur</strong>, puis la réponse est rétablie localement.</p>'
            '<section class="ax-card"><h2>Mode d’utilisation</h2><p><strong>Actuellement :</strong> %s</p>'
            '<div class="ia540-modes"><article><h3>Tout en local</h3><p>Modèle Ollama du serveur pour tout. Confidentialité maximale ; vitesse limitée par le serveur.</p>'
            '<form class="m5-form m5-inline" data-api="m540/mode" data-reload="1"><input type="hidden" name="mode" value="local"><button class="ax-btn ghost" type="submit">Tout en local</button></form></article>'
            '<article><h3>Mixte (recommandé hors local)</h3><p>Tri des courriels, lecture des pièces jointes et contrôle indépendant en local ; rédaction, assistant, analyse, '
            'audiences, documents et Roundcube via l’API choisie.</p>%s</article>'
            '<article><h3>Hybride</h3><p>Local par défaut, bascule vers OpenRouter pour les seules tâches complexes, avec plafonds de coût.</p>'
            '<a class="ax-btn ghost" href="%s">Régler le routage hybride</a></article></div></section>'
            '<section class="ax-card"><h2>Aperçu de ce qui part</h2><p class="ax-muted">Collez un texte (courriel, extrait de pièce) : vous voyez exactement ce que '
            'le fournisseur recevrait et la table de correspondance, qui reste sur le serveur. Rien n’est envoyé.</p>'
            '<form class="m5-form" id="p540-form" data-api="m540/preview"><label class="m5-field">Texte<textarea name="text" rows="7" maxlength="60000" required></textarea></label>'
            '<label class="m5-field">Dossier (facultatif)<select name="matter"><option value="">—</option>%s</select></label>'
            '<button class="ax-btn" type="submit">Voir ce qui partirait</button></form>'
            '<div id="p540-result" hidden><h3>Texte transmis</h3><pre class="ia540-sent" id="p540-sent"></pre>'
            '<h3>Remplacements (gardés sur le serveur)</h3><table class="vf-table"><thead><tr><th>Marqueur</th><th>Donnée d’origine</th><th>Catégorie</th></tr></thead>'
            '<tbody id="p540-table"></tbody></table><p class="vf-note" id="p540-check"></p></div></section>'
            '<section class="ax-card"><h2>Journal des envois (7 jours)</h2>%s<pre class="ia540-sent" id="p540-sample" hidden></pre>'
            '<p class="vf-note">Le journal garde, sur le serveur, le texte tel qu’il est parti (déjà pseudonymisé), 7 jours. La table de correspondance n’est jamais conservée.</p></section>'
            '<section class="ax-card"><h2>Limites</h2><ul><li>La pseudonymisation n’est pas une anonymisation : un détail factuel (une date, un lieu, un montant '
            'singulier) peut encore permettre de reconnaître une affaire. Les données restent des données personnelles au sens du RGPD.</li>'
            '<li>Les noms écrits en minuscules ou les prénoms rares sans civilité peuvent échapper à la détection : vérifiez l’aperçu sur vos textes types.</li>'
            '<li>Préférez un fournisseur avec engagement de non-conservation ou hébergement européen, et un contrat de traitement des données.</li></ul></section>'
            '</div><script defer src="%s"></script>') % (
        e(cur['label']), mixed, e(prefix + '/routage-hybride'), matter_opts,
        ('<table class="vf-table"><thead><tr><th>Date</th><th>Fournisseur</th><th>Modèle</th><th>Fonction</th><th>Remplacements</th><th>Caractères</th><th></th></tr></thead>'
         '<tbody>%s</tbody></table>' % rows) if rows else '<p class="ok">Aucun envoi externe sur les 7 derniers jours.</p>',
        e(prefix + '/static/v540.js'))
    return shell('IA externe sûre', body, prefix, csrf, '/ia-externe')


def _matters(desk):
    try:
        return load_matters(desk.c)
    except Stop:
        return []


def _local(stamp):
    try:
        return datetime.fromisoformat(stamp).astimezone().strftime('%d/%m %H:%M')
    except (TypeError, ValueError):
        return str(stamp)[:16]


def handle(desk, name, data, method='POST', args=None):
    from . import pseudo540
    from .model import pseudo_sources
    n = name[len('m540/'):]
    args = args or {}
    if n == 'preview' and method == 'POST':
        out = pseudo540.preview(pseudo_sources(desk.c), data.get('text', ''), data.get('matter', ''))
        desk.audit('ia540_apercu', {'counts': out['counts']})
        return {**out, 'message': 'Aperçu prêt : %d remplacement(s). Rien n’a été envoyé.' % len(out['replacements'])}
    if n == 'sample' and method == 'GET':
        try:
            ident = int(args.get('id', ''))
        except ValueError:
            raise Stop('envoi_inconnu') from None
        return {'text': pseudo540.sample(desk.c['state_dir'], ident)}
    if n == 'mode' and method == 'POST':
        mode = str(data.get('mode') or '')
        if mode == 'local':
            return set_local(desk)
        if mode == 'mixte':
            return apply_mixed(desk, data.get('provider', ''), data.get('model', ''), data.get('local_model', ''))
        raise Stop('mode_ia_invalide')
    raise Stop('route_inconnue')


def apply_external_all(desk, provider_id, model):
    """5.6.0 (VPS sans carte graphique) : toutes les fonctions via le fournisseur choisi, toujours avec pseudonymisation."""
    from .ai_gateway import PURPOSES, provider_registry
    provider = provider_registry(desk.c).get(str(provider_id or ''))
    if not provider or provider.get('type') == 'ollama':
        raise Stop('fournisseur_externe_requis')
    if not provider.get('enabled') or not provider.get('external_data_allowed'):
        raise Stop('autorisation_donnees_externes_requise')
    model = str(model or provider.get('model') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}', model):
        raise Stop('modele_fournisseur_invalide')
    _write_routes(desk, {p: {'provider': provider_id, 'model': model} for p in PURPOSES})
    _set_mode(desk, 'manual')
    desk.audit('ia540_mode', {'mode': 'externe', 'provider': provider_id, 'model': model})
    return {'message': 'Toutes les fonctions utilisent %s (%s), avec pseudonymisation.' % (provider_id, model)}
