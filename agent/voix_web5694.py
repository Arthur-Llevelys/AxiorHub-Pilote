"""5.6.94 (V03, V08) : routes ``/api440/m5689/voix/*`` et section Carbon de Paramètres › Voix.

Routes : ``voix/etat`` (lecture, tous les rôles) ; ``voix/preparer`` (texte développé pour la voix du navigateur, avocat et administrateur,
comme la lecture vocale existante ``m568/speech``) ; ``voix/reglages`` (choix de la voix et de la voix de repli : administrateur, déclaré dans
``standalone_auth.ADMIN_API`` donc droit « réglages »). L'écriture passe par ``config567.save`` : même contrôle de révision (409 si la page
est périmée), même écriture atomique du fichier de configuration. Le compte vient de la requête authentifiée, jamais du corps.

La section est rendue entièrement côté serveur avec les composants communs (``ui_components5689``) ; ``v5694-voix-page.js`` n'ajoute que
l'essai d'écoute. Les réglages personnels (vitesse, Alkove) sont enregistrés par la route existante du profil (``m567/profile``).
"""
from html import escape as e

from .common import Stop
from . import voix5694

LECTURE = ('voix/etat',)
ECRITURE = ('voix/preparer', 'voix/reglages')
NOMS = {'brindille': 'Brindille', 'kokoro': 'Kokoro', 'espeak': 'eSpeak NG', 'chatterbox': 'Chatterbox', 'elevenlabs': 'ElevenLabs'}
LIENS = {'cc_by': 'https://creativecommons.org/licenses/by/4.0/', 'kyutai': 'https://github.com/kyutai-labs/pocket-tts',
         'mfa': 'https://github.com/MontrealCorpusTools/mfa-models', 'alkove': 'https://github.com/alkove-ai/alkove',
         'brindille': 'https://huggingface.co/edbrdi/brindille', 'aurora': 'https://huggingface.co/AuroraAI-Research/AuroraTTS-100K',
         'lex': 'https://huggingface.co/alkove-ai/alkove-lex-1-preview'}


def allowed(role, route):
    route = route.split('?', 1)[0]
    if route in LECTURE:
        return role in ('administrateur', 'avocat', 'assistant')
    if route == 'voix/preparer':
        return role in ('administrateur', 'avocat')
    if route == 'voix/reglages':
        return role == 'administrateur'
    return False


def _erreur(error):
    from .erreurs5634 import explain
    from .web import REASONS
    from .web440 import json_out
    code = str(error).split(':')[0]
    statut = ('403 Forbidden' if code == 'role_insuffisant' else '409 Conflict' if code == 'configuration_modifiee_recharger' else
              '404 Not Found' if code in ('route_inconnue', 'dossier_absent') else '400 Bad Request')
    message = {'configuration_modifiee_recharger': 'La configuration a été modifiée entre-temps : rechargez la page, rien n’a été enregistré.',
               'voix_repli_invalide': 'La voix de repli doit être une voix locale : eSpeak NG, Kokoro ou Chatterbox.',
               'fournisseur_voix_invalide': 'Voix inconnue.'}.get(code) or REASONS.get(code) or explain(code, getattr(error, 'detail', None))
    return json_out({'error': code, 'message': message}, statut)


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _route(env, desk, auth, name, args, method)
    except Stop as error:
        return _erreur(error)


def _route(env, desk, auth, name, args, method):
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m5689/'):].split('?', 1)[0]
    if method == 'GET':
        if route not in LECTURE:
            raise Stop('route_inconnue')
        return json_out(voix5694.etat(desk, owner))
    if method != 'POST':
        raise Stop('methode_refusee')
    if route not in ECRITURE:
        raise Stop('route_inconnue')
    data = check_post(env, auth)
    if not allowed(role, route):
        raise Stop('role_insuffisant')
    if route == 'voix/preparer':
        dossier = str(data.get('matter') or '')
        if dossier:
            from .membres5650 import hidden_for
            if dossier in hidden_for(desk.c['state_dir'], owner, role):
                raise Stop('dossier_absent')
        return json_out(voix5694.preparer(desk, str(data.get('text') or '')[:7000], owner, dossier))
    fournisseur, repli = str(data.get('fournisseur') or ''), str(data.get('repli') or voix5694.REPLI_DEFAUT)
    if fournisseur not in voix5694.FOURNISSEURS:
        raise Stop('fournisseur_voix_invalide')
    if repli not in voix5694.REPLIS:
        raise Stop('voix_repli_invalide')
    try:
        revision = int(data.get('revision'))
    except (TypeError, ValueError):
        raise Stop('configuration_modifiee_recharger') from None
    from . import config567
    out = config567.save(desk, {'revision': revision, 'values': {'speech568.provider': fournisseur, 'speech568.fallback_provider': repli}}, env)
    return json_out({'revision': out['revision'], 'provider': fournisseur, 'fallback': repli,
                     'message': 'Voix enregistrée : %s ; repli du serveur : %s.' % (NOMS[fournisseur], NOMS[repli])})


# ---------------------------------------------------------------- écran
def _lien(cle, texte):
    return '<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>' % (e(LIENS[cle], quote=True), texte)


def _licences(prefix, th):
    local = lambda chemin, texte: '<a href="%s">%s</a>' % (e(prefix + '/static/' + chemin, quote=True), texte)
    resume = th('voix5694.licences_resume', estelle=_cle_html('estelle'), cc=_cle_html('cc'), mfa=_cle_html('mfa'), alkove=_cle_html('alkove'),
                apache=_cle_html('apache'))
    resume = (resume.replace(_cle_html('estelle'), _lien('kyutai', th('voix5694.lic_estelle'))).replace(_cle_html('cc'), _lien('cc_by', 'CC BY 4.0'))
              .replace(_cle_html('mfa'), _lien('mfa', 'Montreal Forced Aligner')).replace(_cle_html('alkove'), _lien('alkove', 'Alkove'))
              .replace(_cle_html('apache'), local('alkove/LICENSE', 'Apache 2.0')))
    details = ('<ul class="v94-credits">'
               '<li>%s</li><li>%s</li><li>%s</li><li>%s</li></ul><p>%s · %s · %s · %s</p>') % (
        th('voix5694.credit_brindille', revision=voix5694.BRINDILLE_REVISION[:12]).replace('Brindille', _lien('brindille', 'Brindille'), 1),
        th('voix5694.credit_lex').replace('Lex', _lien('lex', 'Lex'), 1),
        th('voix5694.credit_aurora').replace('AuroraTTS-100K', _lien('aurora', 'AuroraTTS-100K'), 1),
        th('voix5694.credit_alkove', version=voix5694.ALKOVE_VERSION),
        local('brindille/NOTICE', th('voix5694.notice_brindille')), local('brindille/LICENSE', th('voix5694.licence_brindille')),
        local('alkove/NOTICE', th('voix5694.notice_alkove')), local('alkove/LICENSE', th('voix5694.licence_alkove')))
    return '<div class="v94-licences"><h3>%s</h3><p data-v94-licences>%s</p>%s</div>' % (th('voix5694.licences'), resume, details)


def _cle_html(nom):
    """Repère inséré dans un texte traduit (déjà échappé) puis remplacé par un lien : la traduction place le lien où la langue l'exige."""
    return '[[%s]]' % nom


def section_html(desk, prefix, owner, role, env):
    from .i18n5631 import current, th
    from . import assistant567
    from .ui_components5689 import case, etat_html, etiquette, formulaire, liste
    actuel, secours = voix5694.fournisseur(desk.c), voix5694.repli(desk.c)
    profil = assistant567.profile(desk, owner)
    nom = lambda cle: NOMS.get(cle, cle)
    corps = '<p class="v94-intro">%s</p>' % th('voix5694.intro')
    corps += '<p class="v94-actuelle">%s %s</p>' % (
        th('voix5694.actuelle', voix=nom(actuel)), etiquette(th('voix5694.tag_choisie'), 'succes') if actuel in NOMS else etiquette(th('voix5694.tag_inconnue'), 'erreur'))
    if actuel == 'brindille':
        corps += '<p class="ax-muted">%s</p>' % th('voix5694.repli', voix=nom(secours))
    cartes = ''
    for cle in voix5694.FOURNISSEURS:
        marques = (etiquette(th('voix5694.tag_defaut'), 'info') if cle == voix5694.DEFAUT else '') + (etiquette(th('voix5694.tag_choisie'), 'succes') if cle == actuel else '')
        cartes += '<li class="v94-carte" data-voix="%s"><strong>%s</strong> %s<p>%s</p></li>' % (
            e(cle, quote=True), th('voix5694.p_' + cle), marques, th('voix5694.p_' + cle + '_desc'))
    corps += '<ul class="v94-cartes" aria-label="%s">%s</ul>' % (th('voix5694.choix'), cartes)
    if role == 'administrateur':
        try:
            from .config567 import catalog
            revision = int(catalog(desk, env)['revision'])
        except (Stop, OSError, ValueError, KeyError):
            revision = None
        if revision is None:
            corps += etat_html('erreur', th('voix5694.config_illisible'))
        else:
            options = [(cle, th('voix5694.p_' + cle)) for cle in voix5694.FOURNISSEURS]
            replis = [(cle, nom(cle)) for cle in voix5694.REPLIS]
            # data-config567 : même clé que le formulaire des connexions ; le choix ne se règle qu'ici sur cette page
            champs = liste('fournisseur', th('voix5694.choix'), options, actuel if actuel in voix5694.FOURNISSEURS else voix5694.DEFAUT,
                           aide=th('voix5694.choix_aide')).replace('<select name="fournisseur"', '<select name="fournisseur" data-config567="speech568.provider"', 1)
            champs += liste('repli', th('voix5694.repli_libelle'), replis, secours, aide=th('voix5694.repli_aide')).replace(
                '<select name="repli"', '<select name="repli" data-config567="speech568.fallback_provider"', 1)
            champs += '<input type="hidden" name="revision" value="%d"><p><button type="submit" class="ax-btn">%s</button></p>' % (revision, th('voix5694.enregistrer'))
            corps += formulaire('m5689/voix/reglages', champs, recharge=True, classe='v94-choix')
    else:
        corps += '<p class="ax-muted">%s</p>' % th('voix5694.lecture_seule')
    # essai d'écoute : le texte d'exemple fait entendre les abréviations développées (V04) ; rien n'est lu avant le clic
    vitesse = max(0.7, min(1.5, float(profil.get('speech_rate') or 1.0)))
    corps += ('<div class="v94-essai" role="group" aria-labelledby="v94-essai-titre"><h3 id="v94-essai-titre">%s</h3>'
              '<label class="m5-field ui89-champ"><span>%s</span><textarea id="v94-essai-texte" rows="3" maxlength="2000" data-no-dictation>%s</textarea></label>'
              '<p class="v94-boutons"><button type="button" class="ax-btn" data-v94-ecouter>%s</button> '
              '<button type="button" class="ax-btn ghost" data-v94-arreter>%s</button></p>'
              '<p id="v94-essai-etat" class="m5-status" role="status" aria-live="polite" data-v94-etat></p></div>') % (
        th('voix5694.essai'), th('voix5694.essai_texte'), th('voix5694.essai_exemple'), th('voix5694.ecouter'), th('voix5694.arreter'))
    # réglages personnels : vitesse (déjà dans le profil) et Alkove ; route existante du profil
    reglages = ('<label class="m5-field ui89-champ"><span>%s <output id="v94-vitesse-valeur" for="v94-vitesse">%s</output></span>'
                '<input type="range" id="v94-vitesse" name="speech_rate" min="0.7" max="1.5" step="0.1" value="%.1f" aria-describedby="v94-vitesse-aide">'
                '<span class="ui89-aide" id="v94-vitesse-aide">%s</span></label>') % (
        th('voix5694.vitesse'), ('%.1f' % vitesse).replace('.', ',') if current() == 'fr' else '%.1f' % vitesse, vitesse, th('voix5694.vitesse_aide'))
    reglages += '<h3>%s</h3><p class="ax-muted">%s</p>' % (th('voix5694.dictee'), th('voix5694.dictee_intro'))
    reglages += case('voice_isolation', th('voix5694.isoler'), bool(profil.get('voice_isolation', True)), aide=th('voix5694.isoler_aide'))
    reglages += case('stop_on_silence', th('voix5694.arret'), bool(profil.get('stop_on_silence', True)), aide=th('voix5694.arret_aide'))
    reglages += '<p><button type="submit" class="ax-btn">%s</button></p>' % th('voix5694.enregistrer_prefs')
    corps += formulaire('m567/profile', reglages, classe='v94-reglages')
    try:
        from .kokoro5634 import guide_html
        from .diagnostic5634 import install_mode
        guide = guide_html(current(), install_mode(desk.c))
    except Exception:   # noqa: BLE001 — le guide est une aide : son absence ne bloque pas la page
        guide = ''
    if guide:
        corps += '<details class="v94-guide"><summary>%s</summary>%s</details>' % (th('voix5694.guide_kokoro'), guide)
    corps += _licences(prefix, th)
    return ('<section class="v94-voix" id="v94-voix" aria-labelledby="v94-titre" data-v94-page><h2 id="v94-titre">%s</h2>%s</section>'
            % (th('voix5694.titre'), corps))
