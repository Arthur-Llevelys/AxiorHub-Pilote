"""5.6.96 (lot mistral, A01 à A03) : connexion Mistral par clé Studio : tester, enregistrer, suivre les limites.

Défaut corrigé (5.6.95) : ``politique5682._prepare_exclusive`` enregistrait la clé AVANT de relire le catalogue ; une clé erronée
écrasait donc la clé en service. Les deux formulaires qui reçoivent une clé (mode de l'IA, profil Mixte) et la carte
« Mistral · clé Studio » passent désormais par le même service :

1. ``tester`` : lecture du catalogue (``GET /v1/models``) avec la clé candidate. Aucun effet local, aucun dossier lu, aucune routine
   lancée, aucune inférence consommée. Un test de génération (« Réponds uniquement OK », texte fictif) n'a lieu que sur demande expresse.
2. ``enregistrer`` : test, écriture d'un NOUVEAU fichier du coffre (un par révision), second test sur les octets réellement enregistrés,
   puis publication en une transaction (référence de clé, catalogue, révision), refusée si la révision lue à l'ouverture a changé (409).
   En cas d'échec, la connexion en service reste intacte et le fichier candidat est effacé. Tester ou enregistrer ne change ni le mode,
   ni les routes, ni les automatismes ; un premier enregistrement crée une connexion inactive, sans autorisation d'envoi.
3. Retours du fournisseur pendant le travail (``noter_erreur``) : 401/403 arrêtent les relances jusqu'à correction ; 429 respecte
   ``Retry-After``, sinon un délai croissant borné, puis une décision est demandée ; un quota n'est « atteint » que sur un signal explicite
   (402 ou code du fournisseur), jamais sur un 429 seul ; réseau et 5xx : « Mistral indisponible », report borné. Dans le mode
   « API Mistral uniquement », le travail garde sa ligne dans la file (``HELD_CODES``) et rien ne bascule vers un autre fournisseur.

Documentation officielle relue le 9 novembre 2026 : clés créées dans Studio (console.mistral.ai, section « API keys ») ; usage et limites
dans l'Admin Panel (admin.mistral.ai › API › Usage) ; un 429 signale un débit dépassé (jetons par minute) ou un volume mensuel dépassé :
seul, il ne prouve pas l'épuisement du quota. Le solde du quota n'est pas exposé par l'API : il s'affiche « Non fourni ».
"""
from datetime import datetime, timedelta, timezone
from html import escape as e
import json
import os
import re
import sqlite3

from . import cles5696
from .cles5696 import RESEAU, classer, db_de as _db, lire as _lire, valeur as _valeur
from .common import Stop

API_URL = 'https://api.mistral.ai/v1'
CLES_URL = 'https://console.mistral.ai/home?profile_dialog=api-keys'
USAGE_URL = 'https://admin.mistral.ai/organization/usage'
QUOTA_LIBELLE = 'Utilise le quota de votre offre Mistral ; au-delà, selon vos réglages Mistral'
CONTROLE_KEY = 'mistral5696:controle:'
INCIDENT_KEY = 'mistral5696:incident:'
DELAIS_MINUTES = (2, 10, 30, 60)          # délai croissant borné ; après le dernier essai, une décision est demandée
RETRY_MIN_SECONDES = 30                    # un Retry-After nul ne relance pas en boucle
ATTENTE_BLOQUANTE_HEURES = 24              # travail retenu sur sa ligne ; libéré dès la correction (``liberer_travaux``)
GENERATION_TEXTE = 'Réponds uniquement OK'
GENERATION_JETONS = 8

ETATS = {'a_connecter': 'À connecter', 'connexion_en_cours': 'Connexion en cours', 'cle_reconnue': 'Clé reconnue',
         'generation_reussie': 'Test de génération réussi', 'limite_temporaire': 'Limite atteinte', 'quota_atteint': 'Limite atteinte',
         'cle_a_verifier': 'À reconnecter', 'acces_refuse': 'À reconnecter', 'indisponible': 'Mistral indisponible',
         'decision_requise': 'Décision requise', 'non_controlee': 'Clé enregistrée, non contrôlée'}
DETAILS = {'cle_a_verifier': 'Clé à vérifier : Mistral la refuse (401). Les relances automatiques sont arrêtées jusqu’à correction.',
           'acces_refuse': 'Accès refusé (403) : la clé n’a pas les droits nécessaires. Les relances automatiques sont arrêtées jusqu’à correction.',
           'limite_temporaire': 'Limite de débit atteinte (429) : le travail garde sa ligne et reprend après le délai indiqué.',
           'quota_atteint': 'Quota atteint : quota de l’offre ou crédits épuisés, confirmé par Mistral. Les travaux attendent un réexamen.',
           'indisponible': 'Mistral ne répond pas ou renvoie une erreur de serveur : nouvel essai borné, sur la même ligne.',
           'decision_requise': 'Derniers essais sans succès : réexaminez la connexion pour reprendre les travaux.'}
CODES = {'cle_a_verifier': 'mistral_cle_a_verifier', 'acces_refuse': 'mistral_acces_refuse', 'limite_temporaire': 'mistral_limite_temporaire',
         'quota_atteint': 'mistral_quota_atteint', 'indisponible': 'mistral_indisponible', 'decision_requise': 'mistral_decision_requise'}
HELD_CODES = frozenset(CODES.values())     # travaux retenus sur leur ligne (``chatgpt5682.HELD_CODES`` les reprend : une seule liste)
BLOQUANTS = frozenset(('cle_a_verifier', 'acces_refuse', 'quota_atteint', 'decision_requise'))
CODES_BLOQUANTS = frozenset(CODES[k] for k in BLOQUANTS)
# Codes publics déjà traduits par ``Model`` qu'un incident Mistral explique (le travail est alors retenu, pas écarté).
TRADUITS = RESEAU | {'authentification_fournisseur_refusee', 'acces_fournisseur_refuse', 'quota_fournisseur_depasse', 'credit_fournisseur_epuise'}


def _now():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------------------------------------ test de la clé (service commun ``cles5696``)
def _code_test(error):
    return cles5696.code_test('mistral', error)


def _cle(value):
    return cles5696.cle_valide(value)


def _lire_catalogue(cle, base, transport):
    return cles5696.lire_catalogue('mistral', cle, base, transport)


def tester_catalogue(cle, base=API_URL, transport=None):
    """Lignes du catalogue de la clé ; aucun effet local. Erreurs : codes publics seulement."""
    return cles5696.tester_catalogue('mistral', cle, base, transport)


def catalogue_conversation(lignes):
    """(modèles de conversation utilisables, modèles à capacité inconnue) : voir ``cles5696.catalogue_conversation``."""
    return cles5696.catalogue_conversation('mistral', lignes)


def tester_generation(desk, pid, provider, chemin, slug, base):
    """Test facultatif, sur demande expresse : texte fictif, quelques jetons. Seul ce test consomme du quota d'inférence.
    Il passe par ``Model`` comme tout appel : politique d'envoi (« local uniquement » le refuse), autorisation d'envoi de la connexion,
    pseudonymisation, budget et journal des transmissions. Il exige donc une connexion Mistral déjà autorisée à l'envoi."""
    from .model import Model
    from .modeles5681 import tariff
    provider = dict(provider or {})
    prices = tariff(slug) or (provider.get('input_usd_per_million') or 0, provider.get('output_usd_per_million') or 0)
    cfg = {**provider, 'id': pid, 'provider_id': pid, 'type': 'mistral', 'provider_type': 'mistral', 'url': base, 'model': slug, 'secret_file': str(chemin),
           'input_usd_per_million': prices[0], 'output_usd_per_million': prices[1], 'external_data_allowed': bool(provider.get('external_data_allowed')),
           'state_dir': desk.c['state_dir'], 'external_policy_config': desk.c, 'purpose': 'control', 'temperature': 0,
           'diagnostic5682': True, 'sans_liste5682': True, 'test5696': True}      # contrôle de connexion : aucun contenu de dossier
    try:
        texte = Model(cfg).complete([{'role': 'user', 'content': GENERATION_TEXTE}], 0, GENERATION_JETONS)
    except Stop as error:
        raise Stop(_code_test(error) if classer(error)[0] else str(error).split(':')[0]) from None
    if not isinstance(texte, str) or not texte.strip():
        raise Stop('mistral_generation_refusee')
    return True


# ------------------------------------------------------------------------------------------------ lecture des réglages
def connexion(config):
    """(identifiant, réglage) de la connexion Mistral enregistrée ; ('mistral', None) s'il n'y en a pas encore."""
    return cles5696.connexion('mistral', config)


def _base(provider):
    return cles5696.base_de('mistral', provider)


# ------------------------------------------------------------------------------------------------ incidents (retours pendant le travail)
def incident(state_dir, provider_id, now=None):
    """Incident en cours (bloquant, ou report non échu), sinon None."""
    found = _lire(state_dir, INCIDENT_KEY + str(provider_id))
    if not found or not found.get('nature'):
        return None
    if found.get('bloquant'):
        return found
    return found if str(found.get('jusqu_a') or '') > (now or _now()).isoformat() else None


def verifier(state_dir, provider_id):
    """Avant toute décision de routage en mode « API Mistral uniquement » : aucun appel pendant un incident en cours."""
    found = incident(state_dir, provider_id)
    if found:
        raise Stop(found.get('code') or CODES[found['nature']])


def noter_erreur(cfg, error, now=None):
    """Retour du fournisseur pendant un travail : incident enregistré pour cette connexion (aucun secret, aucun contenu)."""
    nature, retry = classer(error)
    state_dir = cfg.get('state_dir') or ''
    if not nature or not state_dir or cfg.get('test5696'):      # le test d'une clé candidate ne change pas l'état de la connexion
        return None
    pid = str(cfg.get('provider_id') or cfg.get('id') or 'mistral')
    now = now or _now()
    db = _db(state_dir)
    if db is None:
        return None
    try:
        db.execute('BEGIN IMMEDIATE')
        previous = _valeur(db, INCIDENT_KEY + pid) or {}
        if previous.get('bloquant') and nature not in BLOQUANTS:
            db.execute('ROLLBACK')
            return previous          # une décision ou une correction attendue n'est pas effacée par un incident passager
        same = previous.get('nature') == nature or (previous.get('nature') in ('limite_temporaire', 'indisponible') and nature in ('limite_temporaire', 'indisponible'))
        tries = int(previous.get('essais') or 0) + 1 if same else 1
        value = {'nature': nature, 'essais': tries, 'depuis': previous.get('depuis') if same and previous.get('depuis') else now.isoformat(),
                 'le': now.isoformat(), 'statut': int(dict(getattr(error, 'detail', None) or {}).get('remote_status') or 0), 'bloquant': nature in BLOQUANTS,
                 'jusqu_a': ''}
        if not value['bloquant']:
            if tries > len(DELAIS_MINUTES):
                value.update({'nature': 'decision_requise', 'bloquant': True, 'apres': nature})
            else:
                wait = max(retry, RETRY_MIN_SECONDES) if retry is not None else DELAIS_MINUTES[tries - 1] * 60
                value.update({'jusqu_a': (now + timedelta(seconds=wait)).isoformat(), 'retry_after': retry})
        value['code'] = CODES[value['nature']]
        db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (INCIDENT_KEY + pid, json.dumps(value)))
        db.execute('COMMIT')
        return value
    except sqlite3.Error:
        try:
            db.execute('ROLLBACK')
        except sqlite3.Error:
            pass
        return None
    finally:
        db.close()


def noter_succes(cfg):
    """Réponse obtenue : l'incident de cette connexion est levé (lecture seule tant qu'il n'y en a pas)."""
    state_dir = cfg.get('state_dir') or ''
    pid = str(cfg.get('provider_id') or cfg.get('id') or 'mistral')
    if state_dir and not cfg.get('test5696') and _lire(state_dir, INCIDENT_KEY + pid):
        _effacer_incident(state_dir, pid)


def _effacer_incident(state_dir, pid):
    db = _db(state_dir)
    if db is None:
        return
    try:
        db.execute('DELETE FROM settings WHERE key=?', (INCIDENT_KEY + str(pid),))
    finally:
        db.close()


def code_travail(state_dir, provider_id, code):
    """Code du travail après un échec en mode exclusif : l'incident Mistral qui l'explique (travail retenu sur sa ligne), sinon ''."""
    code = str(code).split(':')[0]
    if code not in TRADUITS and not re.fullmatch(r'http_(5\d\d|429|40[123])', code):
        return ''
    found = incident(state_dir, provider_id)
    return (found.get('code') or CODES[found['nature']]) if found else ''


def incident_du_mode(config):
    """Code d'attente si le mode « API Mistral uniquement » est en vigueur et sa connexion en incident ; '' sinon."""
    from .politique5682 import current
    rule = current(config)
    if not rule or rule['mode'] != 'mistral':
        return ''
    pid, item = connexion(config)
    found = incident(config.get('state_dir'), pid) if item else None
    return (found.get('code') or CODES[found['nature']]) if found else ''


def next_check(state_dir, code=''):
    """Échéance d'un travail retenu : fin du report (Retry-After ou délai croissant), ou 24 h pour un incident bloquant
    (le travail est libéré dès la correction de la clé ou le réexamen)."""
    now = _now()
    later = (now + timedelta(hours=ATTENTE_BLOQUANTE_HEURES)).isoformat()
    if code in CODES_BLOQUANTS:
        return later
    db = _db(state_dir)
    if db is None:
        return (now + timedelta(minutes=DELAIS_MINUTES[0])).isoformat()
    try:
        rows = db.execute('SELECT value FROM settings WHERE key LIKE ?', (INCIDENT_KEY + '%',)).fetchall()
    finally:
        db.close()
    dates = []
    for (raw,) in rows:
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict) and value.get('bloquant'):
            dates.append(later)
        elif isinstance(value, dict) and str(value.get('jusqu_a') or '') > now.isoformat():
            dates.append(value['jusqu_a'])
    return min(dates) if dates else (now + timedelta(minutes=DELAIS_MINUTES[0])).isoformat()


def liberer_travaux(state_dir):
    """Clé corrigée ou connexion réexaminée : les travaux retenus reviennent tout de suite, sur leur même ligne (aucun doublon)."""
    db = _db(state_dir)
    if db is None:
        return 0
    try:
        marks = ','.join('?' * len(HELD_CODES))
        return db.execute("UPDATE jobs SET not_before=? WHERE status='pending' AND blocked_by IN (%s)" % marks,
                          (_now().isoformat(), *sorted(HELD_CODES))).rowcount
    except sqlite3.Error:
        return 0
    finally:
        db.close()


# ------------------------------------------------------------------------------------------------ test d'une clé
def _pause(desk):
    from .arret5643 import reason_to_stop
    code = reason_to_stop(desk.c.get('state_dir'))
    if code:
        raise Stop(code)


def _generation(desk, pid, provider, chemin, base, utiles, inconnus, modele=''):
    """Test de génération sur demande : le modèle choisi, sinon un modèle à confirmer, sinon le plus économe. Pause respectée."""
    from .catalogue5682 import level_of
    _pause(desk)
    ids = [r['id'] for r in utiles + inconnus]
    if modele and modele not in ids:
        raise Stop('modele_non_teste')
    econome = [r['id'] for r in utiles if level_of('mistral', r['id']) == 'econome']
    slug = modele or (inconnus[0]['id'] if inconnus and not utiles else (econome or ids)[0])
    tester_generation(desk, pid, provider, chemin, slug, base)
    confirme = [r for r in inconnus if r['id'] == slug]
    if confirme:
        utiles = sorted(utiles + [{**confirme[0], 'preuve': 'test_generation'}], key=lambda r: r['id'].lower())
    return utiles, slug


def tester(desk, cle='', utiliser_existante=False, generation=False, modele='', owner='', transport=None):
    """``POST m5682/mistral/tester`` : statut, date et nombre de modèles de conversation, rien d'autre.
    Clé candidate : aucun effet local. Clé enregistrée (réexamen) : contrôle daté, incident levé si la clé répond, sinon noté."""
    pid, provider = connexion(desk.c)
    base = _base(provider)
    if utiliser_existante:
        if not provider or not provider.get('secret_file'):
            raise Stop('cle_api_fournisseur_absente')
        from .common import read_secret
        cle = read_secret(provider['secret_file'])
    cle = _cle(cle)
    when = _now().isoformat()
    try:
        utiles, inconnus = catalogue_conversation(_lire_catalogue(cle, base, transport))
    except Stop as error:
        if utiliser_existante:
            noter_erreur({'state_dir': desk.c['state_dir'], 'provider_id': pid}, error)
        desk.audit('mistral_cle_testee', {'provider': pid, 'cle': 'enregistree' if utiliser_existante else 'candidate', 'resultat': _code_test(error)})
        raise Stop(str(error) if str(error) == 'mistral_catalogue_invalide' else _code_test(error)) from None
    etat = 'cle_reconnue'
    if generation:
        if utiliser_existante:
            utiles, _ = _generation(desk, pid, provider, provider['secret_file'], base, utiles, inconnus, modele)
        else:
            from .ai_gateway import save_secret_version
            chemin = save_secret_version(desk, pid, cle)       # fichier temporaire du coffre : le transport de ``Model`` lit la clé là
            try:
                utiles, _ = _generation(desk, pid, provider, chemin, base, utiles, inconnus, modele)
            finally:
                try:
                    os.unlink(chemin)
                except OSError:
                    pass
        etat = 'generation_reussie'
    if not utiles:
        desk.audit('mistral_cle_testee', {'provider': pid, 'cle': 'enregistree' if utiliser_existante else 'candidate', 'resultat': 'catalogue_modeles_vide'})
        raise Stop('catalogue_modeles_vide')
    if utiliser_existante:
        from .ai_gateway import revision_of
        desk.setting(CONTROLE_KEY + pid, {'etat': etat, 'le': when, 'modeles': len(utiles), 'revision': revision_of(desk.settings('ai:provider:' + pid, {}))})
        _effacer_incident(desk.c['state_dir'], pid)
        liberer_travaux(desk.c['state_dir'])
    desk.audit('mistral_cle_testee', {'provider': pid, 'cle': 'enregistree' if utiliser_existante else 'candidate', 'resultat': etat,
                                      'modeles': len(utiles), 'donnees_transmises': 'texte fictif' if generation else 'aucune'})
    message = '%s : %d modèle(s) de conversation.' % (ETATS[etat], len(utiles))
    return {'ok': True, 'statut': etat, 'le': when, 'modeles': len(utiles),
            'message': message if utiliser_existante else message + ' Rien n’est encore enregistré.', 'noreload': not utiliser_existante}


# ------------------------------------------------------------------------------------------------ enregistrement transactionnel
def enregistrer(desk, cle, revision, owner, modele='', generation=False, transport=None, choisir=None, provider_id=''):
    """``POST m5682/mistral/enregistrer`` : publication transactionnelle par le service commun (``cles5696.enregistrer``) ; l'incident de la
    connexion est levé dans la même transaction et les travaux retenus reviennent sur leur ligne."""
    gen = (lambda pid, provider, chemin, base, utiles, inconnus, choisi: _generation(desk, pid, provider, chemin, base, utiles, inconnus, choisi)[0])         if generation else None
    return cles5696.enregistrer(desk, 'mistral', cle, revision, owner, modele, transport, choisir, provider_id, gen,
                                lambda pid: liberer_travaux(desk.c['state_dir']))


def preparer_mode(desk, data, owner, transport=None):
    """Mode « API Mistral uniquement » : une clé saisie passe par le service commun ; activation en dernier."""
    cles5696.preparer_mode(desk, 'mistral', data, owner, lambda cle, revision: enregistrer(desk, cle, revision, owner, transport=transport))


# ------------------------------------------------------------------------------------------------ affichage
def etat(desk):
    """État lisible de la connexion, séparé du fournisseur actif et de la politique d'envoi."""
    from .ai_gateway import internal_secret, revision_of
    pid, provider = connexion(desk.c)
    state_dir = desk.c['state_dir']
    controle = desk.settings(CONTROLE_KEY + pid, {}) or {}
    found = incident(state_dir, pid)
    if not provider or not provider.get('secret_file'):
        key = 'a_connecter'
    elif found:
        key = found['nature']
    elif controle.get('etat') in ETATS:
        key = controle['etat']
    else:
        key = 'non_controlee'
    return {'provider': pid, 'etat': key, 'libelle': ETATS[key], 'detail': DETAILS.get(key, ''), 'controle_le': controle.get('le') or '',
            'modeles': controle.get('modeles'), 'jusqu_a': (found or {}).get('jusqu_a') or '', 'configuree': bool(provider and provider.get('secret_file')),
            'systeme': bool(provider) and not internal_secret(desk, provider.get('secret_file')),
            'revision': revision_of(desk.settings('ai:provider:' + pid, {})),
            'actif': bool(provider and provider.get('enabled')), 'envoi': bool(provider and provider.get('external_data_allowed'))}


def carte_guide(desk, env, prefix, admin=False):
    """5.6.96 (intégration I06) : même carte dans l'étape IA du guide d'installation ; réservée à l'administrateur."""
    return carte(desk) if admin else ''


def carte(desk):
    """Carte « Mistral · clé Studio » de Paramètres › Modèles d'IA (le guide d'installation l'appelle aussi : un seul formulaire)."""
    from . import politique5682
    from .cockpit530 import _local
    st = etat(desk)
    rule = politique5682.normalize(desk.settings(politique5682.KEY, {}) or {})
    when = ('dernier contrôle : %s' % _local(desk, st['controle_le'], '%d/%m %H:%M')) if st['controle_le'] else 'aucun contrôle enregistré'
    count = (' · %d modèle(s) de conversation' % st['modeles']) if isinstance(st['modeles'], int) else ''
    later = (' · prochain essai : %s' % _local(desk, st['jusqu_a'], '%d/%m %H:%M')) if st['jusqu_a'] else ''
    head = ('<p class="m82-state"><strong>État : %s</strong> · %s%s%s</p>%s'
            '<p>Fournisseur actif : <strong>%s</strong> · Connexion Mistral : %s · Envoi de données à Mistral : %s</p>'
            '<p>%s. Quota restant : Non fourni. <a href="%s" target="_blank" rel="noopener">Gérer l’usage Mistral</a></p>') % (
        e(st['libelle']), e(when), e(count), e(later), ('<p class="ax-muted">%s</p>' % e(st['detail'])) if st['detail'] else '',
        e(politique5682.LABELS.get(rule['mode'], rule['mode'])), 'activée' if st['actif'] else 'non activée',
        'autorisé' if st['envoi'] and rule['consentement'] else 'non autorisé', e(QUOTA_LIBELLE), e(USAGE_URL, quote=True))
    if st['systeme']:
        form = '<p class="ax-muted">Clé gérée par l’administrateur du serveur : elle ne se remplace pas depuis cette page.</p>'
    else:
        form = ('<ol class="k96-etapes"><li><a href="%s" target="_blank" rel="noopener">Ouvrir Mistral Studio</a> et créer une clé dédiée dans l’organisation abonnée.</li>'
                '<li>Coller la clé une seule fois ci-dessous.</li><li>Tester la connexion, puis enregistrer la clé.</li></ol>'
                '<form class="m5-form" data-api="m5682/mistral/enregistrer" data-reload="1"><input type="hidden" name="revision" value="%d">'
                '<label class="m5-field">Clé Studio Mistral<input type="password" name="cle" maxlength="4096" autocomplete="new-password" required></label>'
                '<label class="r52-day"><input type="checkbox" name="generation"> Tester aussi une génération (texte fictif « %s », quelques jetons de votre quota)</label>'
                '<label class="m5-field">Modèle à utiliser si le modèle en service n’est plus proposé (facultatif)<input name="modele" maxlength="160" autocomplete="off"></label>'
                '<button class="ax-btn ghost" type="submit" data-api="m5682/mistral/tester">Tester la connexion</button> '
                '<button class="ax-btn" type="submit">Enregistrer la clé</button><p role="status" class="m5-status"></p></form>') % (
            e(CLES_URL, quote=True), st['revision'], e(GENERATION_TEXTE))
        if st['configuree']:
            form += ('<form class="m5-form m82-inline" data-api="m5682/mistral/tester" data-reload="1"><input type="hidden" name="utiliser_existante" value="1">'
                     '<button class="ax-btn ghost" type="submit">Réexaminer la clé enregistrée</button><p role="status" class="m5-status"></p></form>')
    return ('<section class="ax-card m82" id="k96-mistral"><h2>Mistral · clé Studio</h2>%s%s'
            '<p class="ax-muted">Tester ou enregistrer la clé ne change ni le mode de l’IA, ni les routes, ni les automatismes, et ne lit aucun dossier. '
            'Pour l’utiliser, choisissez « %s » dans Mode de l’IA avec l’autorisation d’envoi.</p></section>') % (
        head, form, e(politique5682.LABELS['mistral']))
