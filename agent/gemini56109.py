"""5.6.109 (lot E, F15) : connexion Google Gemini par clé API, clarification « API ou abonnement ».

Constat (dossier technique, DIAGNOSTIC § 9) : ``ai_gateway.PROVIDER_TYPES`` et ``Model`` ignoraient « gemini ». Raccordement retenu :

- Transport : la compatibilité OpenAI documentée par Google (``BASE_URL``, ``Authorization: Bearer``, chemin ``/chat/completions``) passe par
  le chemin EXISTANT ``Model._openai_compatible`` : politique des données (``garde_modele``), contrôle d'envoi, pseudonymisation et enveloppe
  figée (lot P, ``transmission56109`` : ``figer``, ``emettre``, ``restituer``), pause et arrêt (``arret5643.interruptible``), réservation du
  budget, marque d'émission (``preparer_emission``), journal des transmissions et usage normalisé. Aucun SDK, aucune dépendance, aucun second
  transport : ce module ne fait que préparer la requête (``adapter_requete``) et lire la réponse (``lire_reponse``), d'après le noyau
  ``reference/gemini_api.py`` du dossier technique.
- Adresse fixe (``BASE_URL``) : une adresse libre est refusée. La clé reste au coffre existant (``ai_gateway.save_secret_version``,
  chiffré) ; elle ne figure jamais dans une adresse, un corps, un journal, une erreur, une page ni un export : les erreurs sont réduites à un
  code public (``code_public``), jamais au texte du fournisseur.
- Tester avant d'activer (même méthode que ``cles5696`` pour Mistral et Claude) : lecture du catalogue (``GET /models``) avec la clé
  candidate, sans effet local ; enregistrement = second test sur les octets relus du coffre, puis publication en une transaction (référence de
  clé, catalogue lu, contrôle daté et révision ; 409 si la révision a changé). Une clé refusée ne remplace jamais la clé en service. Un premier
  enregistrement crée une connexion **désactivée, sans autorisation d'envoi et sans tarif** (coût inconnu, jamais zéro).
- Connexion non testée = jamais utilisée : ``garde_connexion`` (appelée par ``Model.__init__`` puis de nouveau juste avant l'émission) exige un
  contrôle réussi pour la révision en vigueur et un modèle présent dans le catalogue lu pour ce compte. Ni routage, ni mode mixte, ni route
  par fonction (``ai_gateway.save_route`` construit un ``Model``) ne peuvent donc retenir une connexion non testée ; un modèle absent n'est
  jamais remplacé en silence par un autre.
- Aucun repli silencieux : un 429 de Google devient ``gemini_limite_atteinte`` (et non un code de plafond qui ferait démarrer le modèle local) ;
  un blocage de sécurité, une troncature, un appel d'outil ou une réponse vide arrêtent le travail avec leur motif.
- Passerelle Gemini CLI (connexion par compte Google) et Vertex AI : NON livrées dans cette version (isolation du CLI, outils et comptes non
  qualifiés) ; la carte le dit. Les images ne sont pas branchées pour Gemini : refus explicite (``gemini_images_non_branchees``), jamais une
  perte silencieuse.

Documentation officielle relue par l'auditeur le 19 novembre 2026 (aucun compte réel essayé ici) : ai.google.dev/gemini-api/docs/openai
(compatibilité), /docs/models (catalogue), /docs/billing (facturation distincte de l'abonnement de l'application Gemini), /gemini-api/terms.
"""
from datetime import datetime, timedelta, timezone
import hashlib
from html import escape as e
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from . import cles5696
from .common import Stop

PID = 'gemini_api'
BASE_URL = 'https://generativelanguage.googleapis.com/v1beta/openai'
CLES_URL = 'https://aistudio.google.com/api-keys'
FACTURATION_URL = 'https://ai.google.dev/gemini-api/docs/billing'
CONDITIONS_URL = 'https://ai.google.dev/gemini-api/terms'
PREFIXE = 'm56109/gemini/'
LECTURES = ('etat',)
ECRITURES = ('tester', 'enregistrer', 'activer')
CONTROLE_KEY = 'gemini56109:controle:'
CATALOGUE_KEY = 'gemini56109:catalogue:'
RECU_KEY = 'gemini56109:recu:'
RECU_JOURS = 7
MAX_POST = 20000
MODELE_RE = re.compile(r'gemini-[a-z0-9][a-z0-9.\-]{0,100}')
# Le catalogue liste aussi des modèles qui ne conversent pas (empreintes, images, voix, temps réel) : jamais retenus pour la génération.
NON_CONVERSATION = re.compile(r'embedding|imagen|image-generation|-image|tts|native-audio|live|aqa|veo|robotics')
ETATS_TESTES = ('cle_reconnue', 'generation_reussie')
DEFINITIFS = ('gemini_cle_refusee', 'gemini_acces_refuse')       # un réexamen qui les rencontre retire la connexion du routage
GENERATION_TEXTE = 'Réponds uniquement OK'
GENERATION_JETONS = 256        # les modèles à réflexion comptent leur réflexion dans la limite de sortie : borne courte mais suffisante
FINS_BLOQUEES = ('content_filter', 'safety', 'recitation', 'blocklist', 'prohibited_content', 'spii', 'image_safety')
CLE_IDEM = re.compile(r'[A-Za-z0-9_-]{1,80}')

ETATS = {'a_connecter': 'À connecter', 'cle_reconnue': 'Clé reconnue', 'generation_reussie': 'Test de génération réussi',
         'non_testee': 'Connexion non testée', 'gemini_cle_refusee': 'Clé refusée', 'gemini_acces_refuse': 'Accès refusé'}
MESSAGES = {
    'gemini_cle_refusee': 'Clé Gemini refusée par Google (401) : vérifiez-la ou créez-en une dans Google AI Studio. Rien n’a été enregistré ni envoyé.',
    'gemini_acces_refuse': 'Accès refusé par Google (403) : la clé n’a pas les droits nécessaires, ou la région du compte n’est pas prise en charge par l’API.',
    'gemini_limite_atteinte': 'Quota ou limite de débit de l’API Gemini atteint (429) : le travail s’arrête avec ce motif, sans autre fournisseur. Consultez la facturation de votre projet Google.',
    'gemini_modele_indisponible': 'Modèle ou adresse introuvable chez Google (404) : ce modèle n’est pas accessible à ce compte.',
    'gemini_requete_refusee': 'Requête refusée par Google (400) : paramètre, modèle ou région du compte non pris en charge.',
    'gemini_indisponible': 'Google ne répond pas pour le moment : la connexion n’a pas pu être vérifiée. La connexion en service n’a pas changé.',
    'gemini_catalogue_invalide': 'La liste des modèles renvoyée par Google est illisible : rien n’a été enregistré.',
    'gemini_catalogue_vide': 'Aucun modèle de conversation n’est accessible à cette clé : rien n’a été enregistré.',
    'gemini_connexion_non_testee': 'Connexion Gemini non testée (ou modifiée depuis son test) : cliquez « Tester la connexion » dans Paramètres › Modèles d’IA. Rien n’a été envoyé.',
    'gemini_modele_hors_catalogue': 'Ce modèle ne figure pas parmi les modèles lus pour ce compte Google : choisissez un modèle de la liste testée. Aucun autre modèle n’est pris à sa place.',
    'gemini_modele_a_choisir': 'Indiquez le modèle à utiliser, parmi ceux que le test a listés pour votre compte.',
    'gemini_adresse_non_officielle': 'Adresse refusée : la connexion Gemini utilise uniquement l’adresse officielle de l’API Google.',
    'gemini_tarif_requis': 'Renseignez le tarif d’entrée et de sortie (par million de jetons, d’après votre facturation Google) avant d’activer : sans tarif, le coût reste inconnu.',
    'gemini_tarif_invalide': 'Tarif ou plafond invalide (nombre positif attendu, par exemple 0,30).',
    'gemini_images_non_branchees': 'Les images ne sont pas transmises à Gemini dans cette version : le travail s’arrête plutôt que de répondre sans les voir.',
    'gemini_generation_cle_enregistree': 'Le test de génération porte sur la clé enregistrée : enregistrez-la puis autorisez l’envoi avant de le lancer.',
    'reponse_ia_bloquee_securite': 'Google a bloqué la réponse (filtre de sécurité) : aucun texte n’est rendu ni enregistré.',
    'generation_ia_non_terminee': 'La génération ne s’est pas terminée normalement : rien n’est rendu comme résultat.',
    'cle_api_fournisseur_absente': 'Aucune clé Gemini enregistrée.',
    'reglage_modifie_entre_temps': 'Le réglage a changé entre-temps : rechargez la page. Rien n’a été modifié.',
    'autorisation_donnees_externes_requise': 'Cochez l’autorisation d’envoi des données pseudonymisées pour activer la connexion.',
    'secret_fournisseur_invalide': 'Clé mal formée : collez la clé en entier, sans espace ni retour à la ligne. Rien n’a été envoyé ni enregistré.',
    'cle_requise': 'Clé d’idempotence manquante.',
    'cle_reutilisee': 'Cette clé a déjà servi à une autre demande.',
    'role_insuffisant': 'Action non autorisée pour votre rôle.',
    'origine_ou_csrf_refuse': 'Requête refusée (origine ou jeton).',
    'requete_trop_longue': 'Requête trop longue.',
    'introuvable': 'Réglage introuvable.',
}
STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden',
           'reglage_modifie_entre_temps': '409 Conflict', 'cle_reutilisee': '409 Conflict', 'requete_trop_longue': '413 Payload Too Large',
           'ia_en_pause': '409 Conflict'}


def message(code):
    code = str(code or '').split(':')[0]
    if code in MESSAGES:
        return MESSAGES[code]
    from .web440 import human
    return human(code)


def _now():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------------------------------------ requête et réponse (noyau du dossier)
def requete(modele, messages, *, catalogue, donnees_externes_autorisees, local_strict=False, limite_sortie=4096, schema=None, capacites=None):
    """Construction d'une requête (noyau ``reference/gemini_api.py``) : refus hors autorisation, modèle du catalogue seulement, schéma JSON
    transmis seulement s'il a été essayé. Ne contient jamais la clé (elle est dans l'en-tête du transport)."""
    if local_strict or not donnees_externes_autorisees:
        raise Stop('fournisseur_externe_non_autorise')
    if not isinstance(modele, str) or not MODELE_RE.fullmatch(modele):
        raise Stop('gemini_modele_hors_catalogue')
    if modele not in catalogue:
        raise Stop('gemini_modele_hors_catalogue')
    if type(limite_sortie) is not int or not 1 <= limite_sortie <= 1_000_000:
        raise Stop('limite_sortie_invalide')
    if not isinstance(messages, list) or not messages:
        raise Stop('messages_ia_invalides')
    for m in messages:
        if not isinstance(m, dict) or m.get('role') not in ('system', 'user', 'assistant') or not isinstance(m.get('content'), str):
            raise Stop('message_ia_invalide')
    out = {'model': modele, 'messages': messages, 'stream': False, 'max_tokens': limite_sortie}
    if schema is not None:
        if not (capacites or {}).get('schema_json_teste'):
            raise Stop('schema_non_teste')
        out['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'axiorhub_result', 'schema': schema, 'strict': True}}
    return out


def adapter_requete(model, req, json_schema):
    """Appelé par ``Model._openai_compatible`` avant le contrôle de l'enveloppe : seconde garde de la connexion, aucune image, et sortie
    structurée non promise au fournisseur tant qu'elle n'a pas été essayée (consigne JSON dans le message système ; validation locale)."""
    cfg = getattr(model, 'cfg', None) or {}
    garde_connexion({**cfg, 'diagnostic5682': False})
    if getattr(model, '_images56106', None):
        raise Stop('gemini_images_non_branchees')
    req = dict(req)
    req.pop('response_format', None)
    for cle in [k for k in req if k not in ('model', 'messages', 'stream', 'max_tokens', 'temperature', 'top_p')]:
        req.pop(cle)                   # seuls les paramètres documentés par la compatibilité OpenAI de Google
    if json_schema is not None:
        if (cfg.get('capacites56109') or {}).get('schema_json_teste'):
            from .schema5634 import strict_form
            req['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'axiorhub_result', 'strict': True, 'schema': strict_form(json_schema)}}
        else:
            consigne = ('Réponds UNIQUEMENT par un objet JSON valide, sans texte autour ni bloc de code, conforme à ce schéma JSON : '
                        + json.dumps(json_schema, ensure_ascii=False))
            messages = list(req.get('messages') or [])
            if messages and isinstance(messages[0], dict) and messages[0].get('role') == 'system':
                messages[0] = {**messages[0], 'content': str(messages[0].get('content') or '') + '\n\n' + consigne}
            else:
                messages.insert(0, {'role': 'system', 'content': consigne})
            req['messages'] = messages
    return req


def lire_reponse(result, json_mode=False):
    """Seule une fin « stop » avec un texte non vide est une réponse. Blocage, troncature, appel d'outil, fin inconnue : arrêt motivé."""
    try:
        choix = result['choices'][0]
        message_ = choix['message']
        fin = choix.get('finish_reason')
    except (KeyError, IndexError, TypeError, AttributeError):
        raise Stop('reponse_fournisseur_invalide') from None
    if not isinstance(message_, dict):
        raise Stop('reponse_fournisseur_invalide')
    if message_.get('tool_calls'):
        raise Stop('appel_outil_ia_refuse')
    fin = str(fin).lower() if fin is not None else None
    if fin in ('length', 'max_tokens'):
        raise Stop('generation_ia_incomplete')
    if fin in FINS_BLOQUEES:
        raise Stop('reponse_ia_bloquee_securite')
    if fin != 'stop':
        raise Stop('generation_ia_non_terminee')
    texte = message_.get('content')
    if isinstance(texte, list):
        texte = ''.join(str(x.get('text') or '') if isinstance(x, dict) else str(x) for x in texte)
    if not isinstance(texte, str) or not texte.strip():
        raise Stop('reponse_ia_vide')
    texte = texte.strip()
    if json_mode:
        texte = re.sub(r'^```(?:json)?\s*|\s*```$', '', texte).strip()
        if not texte.startswith('{') and '{' in texte and '}' in texte:
            texte = texte[texte.index('{'):texte.rindex('}') + 1]
        if not texte:
            raise Stop('reponse_ia_vide')
    return texte


def traduire_erreur(error):
    """Erreur du transport pendant un travail : un 429 de Google garde un motif propre (jamais un code de plafond, qui ferait démarrer le
    modèle local sans décision de l'avocat). ``None`` : traduction commune de ``Model``."""
    if str(error).split(':')[0] == 'http_429':
        return Stop('gemini_limite_atteinte')
    return None


def code_public(error):
    """Code public d'un échec de test : statut seulement, jamais le texte du fournisseur (il peut recopier la clé ou le contenu)."""
    code = str(error).split(':')[0]
    if code in ('http_401', 'authentification_fournisseur_refusee'):
        return 'gemini_cle_refusee'
    if code in ('http_403', 'acces_fournisseur_refuse'):
        return 'gemini_acces_refuse'
    if code in ('http_429', 'quota_fournisseur_depasse', 'gemini_limite_atteinte'):
        return 'gemini_limite_atteinte'
    if code in ('http_404', 'modele_ou_route_fournisseur_absent'):
        return 'gemini_modele_indisponible'
    if code == 'http_400':
        return 'gemini_requete_refusee'
    if code in ('json_http_invalide', 'reponse_trop_volumineuse', 'gemini_catalogue_invalide'):
        return 'gemini_catalogue_invalide'
    if code in MESSAGES or code in ('ia_en_pause',):
        return code
    return 'gemini_indisponible'          # 5xx, délai, connexion impossible, ou tout autre retour non qualifié


# ------------------------------------------------------------------------------------------------ catalogue
def _http():
    """Transport réel du catalogue : classe ``HTTP`` commune (HTTPS, sans redirection), clé dans l'en-tête. Jamais appelé par les tests."""
    def appel(methode, chemin, cle, corps=None):
        from .common import HTTP
        http = HTTP(BASE_URL, timeout=8)
        http.headers.update({'Authorization': 'Bearer ' + cle, 'Accept': 'application/json'})
        return http.json(methode, chemin, corps)
    return appel


def catalogue_conversation(doc):
    """Modèles de conversation accessibles au compte, dans l'ordre du fournisseur (préfixe « models/ » retiré)."""
    if not isinstance(doc, dict) or not isinstance(doc.get('data'), list):
        raise Stop('gemini_catalogue_invalide')
    vus = []
    for x in doc['data']:
        slug = str((x or {}).get('id') or '') if isinstance(x, dict) else ''
        slug = slug[len('models/'):] if slug.startswith('models/') else slug
        if MODELE_RE.fullmatch(slug) and not NON_CONVERSATION.search(slug) and slug not in vus:
            vus.append(slug)
    return vus


def lire_catalogue(cle, transport=None):
    return catalogue_conversation((transport or _http())('GET', '/models', cle))


# ------------------------------------------------------------------------------------------------ état de la connexion
def connexion(desk):
    """(identifiant, réglage enregistré) de la connexion Gemini ; (PID, None) s'il n'y en a pas encore."""
    from .politique5682 import provider_of
    pid, _ = provider_of(desk.c, 'gemini')
    pid = pid or PID
    return pid, desk.settings('ai:provider:' + pid, None)


def etat_teste(state_dir, pid):
    """Contrôle en vigueur (lecture seule) : testée = contrôle réussi pour la révision actuelle du réglage, catalogue lu conservé."""
    from .ai_gateway import revision_of
    reglage = cles5696.lire(state_dir, 'ai:provider:' + str(pid)) or {}
    controle = cles5696.lire(state_dir, CONTROLE_KEY + str(pid)) or {}
    catalogue = cles5696.lire(state_dir, CATALOGUE_KEY + str(pid)) or {}
    revision = revision_of(reglage)
    teste = (reglage.get('type') == 'gemini' and revision > 0 and controle.get('etat') in ETATS_TESTES and controle.get('revision') == revision)
    modeles = [str(m) for m in catalogue.get('modeles') or [] if isinstance(m, str)]
    return {'teste': bool(teste), 'modeles': modeles, 'controle': controle, 'reglage': reglage, 'revision': revision}


def garde_connexion(cfg):
    """Point de passage de tout ``Model`` de type « gemini » : adresse officielle, connexion testée pour sa révision, modèle du catalogue lu.
    Le contrôle de connexion (``diagnostic5682``, sans contenu) et le test de génération (``test56109``, catalogue lu à l'instant) ne
    demandent pas de test préalable ; l'émission refait la garde complète (``adapter_requete``)."""
    url = str(cfg.get('url') or '').rstrip('/')
    if url != BASE_URL:
        raise Stop('gemini_adresse_non_officielle')
    modele = str(cfg.get('model') or '')
    if cfg.get('test56109'):
        if modele not in (cfg.get('catalogue56109') or ()):
            raise Stop('gemini_modele_hors_catalogue')
        return
    if cfg.get('diagnostic5682'):
        return
    st = etat_teste(cfg.get('state_dir') or '', cfg.get('provider_id') or cfg.get('id') or PID)
    if not st['teste']:
        raise Stop('gemini_connexion_non_testee')
    if modele not in st['modeles']:
        raise Stop('gemini_modele_hors_catalogue')


def etat(desk):
    """État lisible, sans secret ni appel au fournisseur (aucune écriture : affichage)."""
    from .ai_gateway import internal_secret
    pid, reglage = connexion(desk)
    st = etat_teste(desk.c['state_dir'], pid)
    controle = st['controle']
    if not reglage or not reglage.get('secret_file'):
        cle = 'a_connecter'
    elif st['teste']:
        cle = controle['etat']
    elif controle.get('etat') in DEFINITIFS:
        cle = controle['etat']
    else:
        cle = 'non_testee'
    reglage = reglage or {}
    tarif = bool(_positif(reglage.get('input_usd_per_million')) and _positif(reglage.get('output_usd_per_million')))
    return {'provider': pid, 'etat': cle, 'libelle': ETATS[cle], 'teste': st['teste'], 'controle_le': str(controle.get('le') or ''),
            'modeles': st['modeles'], 'modele': str(reglage.get('model') or ''), 'revision': st['revision'],
            'configuree': bool(reglage.get('secret_file')), 'systeme': bool(reglage) and not internal_secret(desk, reglage.get('secret_file')),
            'actif': bool(reglage.get('enabled')), 'envoi': bool(reglage.get('external_data_allowed')), 'tarif': tarif,
            'prix_entree': reglage.get('input_usd_per_million') or '', 'prix_sortie': reglage.get('output_usd_per_million') or '',
            'plafond_mensuel': reglage.get('monthly_budget_usd') or '', 'plafond_requete': reglage.get('per_request_budget_usd') or '',
            'dernier_echec': str((controle.get('dernier_echec') or {}).get('code') or '')}


def _positif(value):
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


# ------------------------------------------------------------------------------------------------ écritures (une transaction chacune)
def _transaction(desk, faire):
    db = cles5696.db_de(desk.c['state_dir'])
    if db is None:
        raise Stop('base_indisponible')
    try:
        db.execute('BEGIN IMMEDIATE')
        out = faire(db)
        db.execute('COMMIT')
        return out
    except BaseException:
        try:
            db.execute('ROLLBACK')
        except sqlite3.Error:
            pass
        raise
    finally:
        db.close()


def _ecrire(db, cle, valeur):
    db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (cle, json.dumps(valeur, ensure_ascii=False)))


def _pause(desk):
    from .arret5643 import reason_to_stop
    code = reason_to_stop(desk.c.get('state_dir'))
    if code:
        raise Stop(code)


def tester_generation(desk, pid, reglage, modele, modeles):
    """Test facultatif, sur demande : texte fictif, quelques jetons du quota. Par ``Model`` comme tout appel : politique des données,
    autorisation d'envoi de la connexion, pseudonymisation, enveloppe contrôlée, pause, budget (tarif exigé) et journal des transmissions."""
    from .model import Model, pseudo_sources
    cfg = {**reglage, 'id': pid, 'provider_id': pid, 'type': 'gemini', 'provider_type': 'gemini', 'model': modele, 'state_dir': desk.c['state_dir'],
           'external_policy_config': desk.c, 'purpose': 'control', 'temperature': 0, 'pseudo': pseudo_sources(desk.c),
           'test56109': True, 'catalogue56109': list(modeles)}
    texte = Model(cfg).complete([{'role': 'user', 'content': GENERATION_TEXTE}], 0, GENERATION_JETONS)
    if not isinstance(texte, str) or not texte.strip():
        raise Stop('reponse_ia_vide')
    return True


def tester(desk, owner, cle='', utiliser_existante=False, generation=False, modele='', transport=None):
    """« Tester la connexion » : lecture du catalogue avec la clé candidate (aucun effet local), ou réexamen de la clé enregistrée (contrôle
    daté, catalogue relu ; une clé refusée retire la connexion du routage). Génération fictive seulement sur demande, clé enregistrée."""
    from .ai_gateway import revision_of
    from .common import read_secret
    pid, reglage = connexion(desk)
    if utiliser_existante:
        if not reglage or not reglage.get('secret_file'):
            raise Stop('cle_api_fournisseur_absente')
        cle = read_secret(reglage['secret_file'])
    elif generation:
        raise Stop('gemini_generation_cle_enregistree')
    cle = cles5696.cle_valide(cle)
    if generation:
        _pause(desk)                   # IA en pause : aucun appel de génération, même fictif
    quand = _now().isoformat()
    origine = 'enregistree' if utiliser_existante else 'candidate'
    try:
        modeles = lire_catalogue(cle, transport)
    except Stop as error:
        code = code_public(error)
        if utiliser_existante:
            def noter(db):
                actuel = cles5696.valeur(db, CONTROLE_KEY + pid) or {}
                if code in DEFINITIFS:
                    actuel = {'etat': code, 'le': quand, 'revision': revision_of(cles5696.valeur(db, 'ai:provider:' + pid))}
                else:
                    actuel = {**actuel, 'dernier_echec': {'code': code, 'le': quand}}
                _ecrire(db, CONTROLE_KEY + pid, actuel)
            _transaction(desk, noter)
        desk.audit('gemini_cle_testee', {'provider': pid, 'cle': origine, 'resultat': code})
        raise Stop(code) from None
    if not modeles:
        desk.audit('gemini_cle_testee', {'provider': pid, 'cle': origine, 'resultat': 'gemini_catalogue_vide'})
        raise Stop('gemini_catalogue_vide')
    etat_ = 'cle_reconnue'
    if generation:
        choisi = modele or str(reglage.get('model') or '')
        if choisi not in modeles:
            raise Stop('gemini_modele_hors_catalogue')
        tester_generation(desk, pid, reglage, choisi, modeles)
        etat_ = 'generation_reussie'
    if utiliser_existante:
        def publier(db):
            rev = revision_of(cles5696.valeur(db, 'ai:provider:' + pid))
            _ecrire(db, CATALOGUE_KEY + pid, {'le': quand, 'modeles': modeles})
            _ecrire(db, CONTROLE_KEY + pid, {'etat': etat_, 'le': quand, 'modeles': len(modeles), 'revision': rev})
        _transaction(desk, publier)
    desk.audit('gemini_cle_testee', {'provider': pid, 'cle': origine, 'resultat': etat_, 'modeles': len(modeles),
                                     'donnees_transmises': 'texte fictif' if generation else 'aucune'})
    texte = '%s : %d modèle(s) de conversation accessibles à ce compte (%s).' % (ETATS[etat_], len(modeles), ', '.join(modeles[:8]))
    return {'ok': True, 'statut': etat_, 'le': quand, 'modeles': modeles,
            'message': texte if utiliser_existante else texte + ' Rien n’est encore enregistré.', 'noreload': not utiliser_existante}


def _modele_publie(reglage, modeles, choisi):
    if choisi:
        if choisi not in modeles:
            raise Stop('gemini_modele_hors_catalogue')
        return choisi
    ancien = str((reglage or {}).get('model') or '')
    if ancien:
        if ancien in modeles:
            return ancien
        raise Stop('modele_en_service_absent')
    raise Stop('gemini_modele_a_choisir')


def enregistrer(desk, owner, cle, revision, modele='', transport=None):
    """Publication d'une clé vérifiée deux fois (clé saisie, puis octets relus du coffre) ; la clé en service reste en cas d'échec."""
    from .ai_gateway import internal_secret, revision_of, save_secret_version
    from .common import read_secret
    from .modeles5681 import DEFAULT_MONTHLY_USD, DEFAULT_REQUEST_USD
    pid, reglage = connexion(desk)
    if reglage and not internal_secret(desk, reglage.get('secret_file')):
        raise Stop('cle_geree_par_le_systeme')
    attendu = cles5696.revision_attendue(revision)
    cle = cles5696.cle_valide(cle)
    try:
        modeles = lire_catalogue(cle, transport)          # aucun effet local avant ce premier test
    except Stop as error:
        raise Stop(code_public(error)) from None
    if not modeles:
        raise Stop('gemini_catalogue_vide')
    choisi = _modele_publie(reglage, modeles, str(modele or '').strip())
    if attendu is not None and attendu != revision_of(reglage):
        raise Stop('reglage_modifie_entre_temps')
    chemin = save_secret_version(desk, pid, cle)
    quand = _now().isoformat()
    try:
        if read_secret(chemin) != cle:
            raise Stop('coffre_relecture_differente')
        try:
            second = lire_catalogue(read_secret(chemin), transport)      # octets réellement enregistrés
        except Stop as error:
            raise Stop(code_public(error)) from None
        if choisi not in second:
            raise Stop('gemini_catalogue_vide')

        def publier(db):
            ancien = cles5696.valeur(db, 'ai:provider:' + pid)
            if ancien and ancien.get('type') not in (None, 'gemini'):
                raise Stop('identifiant_fournisseur_occupe')
            actuelle = revision_of(ancien)
            if attendu is not None and attendu != actuelle:
                raise Stop('reglage_modifie_entre_temps')
            if ancien:
                value = {**ancien, 'url': BASE_URL, 'secret_file': chemin, 'secret_precedent': str(ancien.get('secret_file') or ''), 'model': choisi,
                         'revision': actuelle + 1, 'cle_le': quand}
            else:   # première clé : connexion proposée désactivée, sans envoi ni tarif (coût inconnu)
                value = {'type': 'gemini', 'url': BASE_URL, 'model': choisi, 'enabled': False, 'external_data_allowed': False, 'secret_file': chemin,
                         'timeout_seconds': 240, 'max_context_chars': 120000, 'temperature': 0, 'structured_mode': 'json', 'zdr_required': False,
                         'monthly_budget_usd': DEFAULT_MONTHLY_USD, 'per_request_budget_usd': DEFAULT_REQUEST_USD, 'input_usd_per_million': 0,
                         'output_usd_per_million': 0, 'state_dir': desk.c['state_dir'], 'revision': 1, 'cle_le': quand}
            _ecrire(db, 'ai:provider:' + pid, value)
            _ecrire(db, CATALOGUE_KEY + pid, {'le': quand, 'modeles': modeles})
            _ecrire(db, CONTROLE_KEY + pid, {'etat': 'cle_reconnue', 'le': quand, 'modeles': len(modeles), 'revision': value['revision']})
            db.execute('DELETE FROM settings WHERE key=?', ('ai:models:' + pid,))
            return value
        value = _transaction(desk, publier)
    except BaseException:
        if str(Path(chemin).resolve()) not in cles5696.references(desk, pid):
            try:
                os.unlink(chemin)      # candidat jamais publié : effacé, la clé en service reste
            except OSError:
                pass
        raise
    desk.c.setdefault('ai_providers', {})[pid] = value
    cles5696.nettoyer(desk, pid)
    desk.audit('gemini_cle_enregistree', {'provider': pid, 'type': 'gemini', 'revision': value['revision'], 'modeles': len(modeles), 'modele': choisi,
                                          'secret_replaced': True, 'donnees_transmises': 'aucune'})
    return {'ok': True, 'provider': pid, 'revision': value['revision'], 'modele': choisi, 'modeles': len(modeles), 'statut': 'cle_reconnue',
            'actif': bool(value.get('enabled')),
            'message': 'Clé enregistrée et testée : %d modèle(s) de conversation. La connexion reste %s ; le mode de l’IA et les routes sont inchangés.' % (
                len(modeles), 'activée' if value.get('enabled') else 'désactivée')}


def _montant(value, maximum=100000):
    texte = str(value if value is not None else '').replace(' ', '').replace(' ', '').replace(',', '.')
    if texte == '':
        return None
    try:
        n = float(texte)
    except ValueError:
        raise Stop('gemini_tarif_invalide') from None
    if n != n or n < 0 or n > maximum:
        raise Stop('gemini_tarif_invalide')
    return n


def _vrai(value):
    return value is True or str(value or '').lower() in ('on', 'true', '1', 'oui', 'yes')


def activer(desk, owner, data):
    """Activer ou désactiver la connexion testée : modèle du catalogue, tarif (exigé pour activer : sans lui le coût est inconnu), plafonds et
    autorisation d'envoi. Ne change ni le mode de l'IA, ni les routes, ni la révision de la clé (le test reste valable)."""
    from .ai_gateway import revision_of
    pid, reglage = connexion(desk)
    if not reglage:
        raise Stop('cle_api_fournisseur_absente')
    attendu = cles5696.revision_attendue(data.get('revision'))
    actif, envoi = _vrai(data.get('actif')), _vrai(data.get('envoi'))
    prix_e, prix_s = _montant(data.get('prix_entree')), _montant(data.get('prix_sortie'))
    mensuel, requete_ = _montant(data.get('plafond_mensuel')), _montant(data.get('plafond_requete'))
    modele = str(data.get('modele') or reglage.get('model') or '').strip()
    st = etat_teste(desk.c['state_dir'], pid)
    if actif:
        if not st['teste']:
            raise Stop('gemini_connexion_non_testee')
        if modele not in st['modeles']:
            raise Stop('gemini_modele_hors_catalogue')
        if not envoi:
            raise Stop('autorisation_donnees_externes_requise')
        if not prix_e or not prix_s:
            raise Stop('gemini_tarif_requis')
    elif modele not in st['modeles']:
        modele = str(reglage.get('model') or '')

    def ecrire(db):
        ancien = cles5696.valeur(db, 'ai:provider:' + pid) or {}
        if attendu is not None and attendu != revision_of(ancien):
            raise Stop('reglage_modifie_entre_temps')
        value = {**ancien, 'enabled': actif, 'external_data_allowed': envoi, 'model': modele}
        for champ, n in (('input_usd_per_million', prix_e), ('output_usd_per_million', prix_s), ('monthly_budget_usd', mensuel),
                         ('per_request_budget_usd', requete_)):
            if n:
                value[champ] = n
        _ecrire(db, 'ai:provider:' + pid, value)
        return value
    value = _transaction(desk, ecrire)
    desk.c.setdefault('ai_providers', {})[pid] = value
    desk.audit('gemini_connexion_reglee', {'provider': pid, 'enabled': actif, 'external_data_allowed': envoi, 'modele': modele,
                                           'tarif_renseigne': bool(_positif(value.get('input_usd_per_million')) and _positif(value.get('output_usd_per_million')))})
    return {'ok': True, 'actif': actif, 'envoi': envoi, 'modele': modele,
            'message': ('Connexion Gemini activée (%s). Pour l’utiliser, choisissez-la dans le mode de l’IA ou le routage par fonction.' % modele) if actif
            else 'Connexion Gemini désactivée : elle n’est plus proposée.'}


# ------------------------------------------------------------------------------------------------ routes ``/api440/m56109/gemini/*``
def allowed(role, method, api):
    """Réglage commun du cabinet : administrateur seulement (lecture et écriture)."""
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):].strip('/') if reste.startswith(PREFIXE) else ''
    if method in ('GET', 'HEAD'):
        return reste in LECTURES and role == 'administrateur'
    return method == 'POST' and reste in ECRITURES and role == 'administrateur'


def _json(data, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(data, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def _sha(*parts):
    return hashlib.sha256('\x1f'.join(str(p) for p in parts).encode('utf-8')).hexdigest()


def _recu(desk, acteur, route, data, faire):
    """Clé d'idempotence d'un effet : même clé, même demande = même reçu (aucun second effet) ; même clé, autre demande = 409. Le reçu ne
    garde que le résultat public et une empreinte (la clé API n'y figure qu'en empreinte)."""
    cle = str(data.get('cle') or '')
    if not CLE_IDEM.fullmatch(cle):
        raise Stop('cle_requise')
    empreinte = _sha(route, json.dumps({k: v for k, v in data.items() if k not in ('cle', 'api_key')}, sort_keys=True, default=str),
                     _sha(str(data.get('api_key') or '')))
    nom = RECU_KEY + _sha(acteur, route, cle)[:40]
    trouve = cles5696.lire(desk.c['state_dir'], nom)
    if trouve:
        if trouve.get('empreinte') != empreinte:
            raise Stop('cle_reutilisee')
        return {**(trouve.get('resultat') or {}), 'rejoue': True}
    out = faire()
    limite = (_now() - timedelta(days=RECU_JOURS)).isoformat()

    def garder(db):
        _ecrire(db, nom, {'empreinte': empreinte, 'resultat': out, 'le': _now().isoformat()})
        for k, v in db.execute('SELECT key, value FROM settings WHERE key LIKE ?', (RECU_KEY + '%',)).fetchall():
            try:
                if str(json.loads(v).get('le') or '') < limite:
                    db.execute('DELETE FROM settings WHERE key=?', (k,))
            except (TypeError, ValueError, AttributeError):
                db.execute('DELETE FROM settings WHERE key=?', (k,))
    _transaction(desk, garder)
    return out


def _poster(desk, owner, reste, d):
    if reste == 'tester':
        return tester(desk, owner, d.get('api_key') or '', _vrai(d.get('utiliser_existante')), _vrai(d.get('generation')), str(d.get('modele') or '').strip())
    if reste == 'enregistrer':
        return enregistrer(desk, owner, d.get('api_key') or '', d.get('revision'), str(d.get('modele') or '').strip())
    return activer(desk, owner, d)


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, role = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    try:
        if reste not in LECTURES + ECRITURES:
            raise Stop('introuvable')
        if not allowed(role, method, name):     # contrôle du rôle avant toute lecture du corps
            raise Stop('role_insuffisant')
        if method in ('GET', 'HEAD'):
            st = etat(desk)
            return _json({k: v for k, v in st.items() if k not in ('systeme',)})
        try:
            longueur = int(env.get('CONTENT_LENGTH') or 0)
        except ValueError:
            longueur = MAX_POST + 1
        if longueur > MAX_POST:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        d = check_post(env, auth)
        if reste == 'tester' and not _vrai(d.get('utiliser_existante')):
            return _json(_poster(desk, owner, reste, d))           # clé candidate : aucun effet local, rien à rejouer
        return _json(_recu(desk, owner, reste, d, lambda: _poster(desk, owner, reste, d)))
    except Stop as ex:
        code = str(ex).split(':')[0]
        return _json({'error': code, 'message': message(code)}, STATUTS.get(code, '422 Unprocessable Entity'))


# ------------------------------------------------------------------------------------------------ carte de Paramètres › Modèles d'IA
def _champ(nom, libelle, valeur='', extra=''):
    return '<label class="m5-field">%s<input name="%s" value="%s" maxlength="40" inputmode="decimal" autocomplete="off"%s></label>' % (
        e(libelle), e(nom, quote=True), e(str(valeur), quote=True), extra)


def carte(desk):
    """Carte « Google Gemini · clé API » (même mécanisme que la carte Mistral : tester, enregistrer, réexaminer ; activation à part).
    Affichage seul : aucune écriture, aucun appel au fournisseur. Les clés d'idempotence des formulaires sont tirées à l'affichage."""
    from .cockpit530 import _local
    st = etat(desk)
    quand = ('dernier contrôle : %s' % _local(desk, st['controle_le'], '%d/%m %H:%M')) if st['controle_le'] else 'aucun contrôle enregistré'
    nombre = (' · %d modèle(s) accessibles' % len(st['modeles'])) if st['modeles'] and st['configuree'] else ''
    echec = ('<p class="ax-muted">Dernier réexamen sans réponse : <span>%s</span></p>' % e(message(st['dernier_echec']))) if st['dernier_echec'] else ''
    tarif = ('<p>Tarif renseigné : %s $ en entrée, %s $ en sortie, par million de jetons (d’après votre facturation Google).</p>' % (
        e(str(st['prix_entree'])), e(str(st['prix_sortie'])))) if st['tarif'] else (
        '<p class="ax-alert" role="status">Tarif non renseigné : coût inconnu, la connexion ne peut pas être utilisée.</p>' if st['configuree'] else '')
    tete = ('<p class="m82-state"><strong>État : %s</strong> · %s%s</p>%s'
            '<p>Connexion Gemini : %s · Envoi de données à Google : %s · Quota restant : Non fourni.</p>%s') % (
        e(st['libelle']), e(quand), e(nombre), echec, 'activée' if st['actif'] else 'non activée (proposée désactivée)',
        'autorisé' if st['envoi'] else 'non autorisé', tarif)
    clarte = ('<details class="m82-adv" open><summary>API ou abonnement : quelle différence ?</summary>'
              '<p>Une clé API Google AI Studio est facturée à l’usage par Google, sur la facturation de votre projet, séparément de tout abonnement.</p>'
              '<p>Un abonnement grand public n’est pas une API : Google AI Pro ou Ultra (application Gemini), Mistral Le Chat Pro, ChatGPT Plus ou Pro '
              'ne donnent ni clé ni quota d’API utilisable par AxiorHub.</p>'
              '<p>Exceptions déjà prises en charge : l’abonnement ChatGPT a sa propre connexion (section ChatGPT) ; pour Mistral, l’offre de votre '
              'organisation peut couvrir l’usage de la clé Studio, selon votre compte (carte Mistral).</p>'
              '<p>Connexion Google par Gemini CLI : option non livrée dans cette version (passerelle à qualifier : isolation du programme, outils, '
              'comptes). Vertex AI : non proposé. Utilisez la clé API.</p>'
              '<p>La connexion Gmail ou Google Agenda ne donne pas accès à Gemini : autorisations et secrets restent distincts.</p>'
              '<p>Une clé ou une offre payante ne garantit pas le secret professionnel : relisez les <a href="%s" target="_blank" rel="noopener">conditions de '
              'l’API Gemini</a> et la région de votre compte. Pour des utilisateurs de l’Espace économique européen, ces conditions exigent les services payants.</p>'
              '</details>') % e(CONDITIONS_URL, quote=True)
    if st['systeme']:
        forms = '<p class="ax-muted">Clé gérée par l’administrateur du serveur : elle ne se remplace pas depuis cette page.</p>'
    else:
        forms = ('<ol class="k96-etapes"><li><a href="%s" target="_blank" rel="noopener">Ouvrir Google AI Studio</a> et créer une clé dédiée '
                 '(<a href="%s" target="_blank" rel="noopener">facturation de l’API</a>).</li><li>Coller la clé une seule fois ci-dessous.</li>'
                 '<li>Tester la connexion, puis enregistrer la clé avec un modèle de la liste.</li></ol>'
                 '<form class="m5-form" data-api="m56109/gemini/enregistrer" data-reload="1"><input type="hidden" name="revision" value="%d">'
                 '<input type="hidden" name="cle" value="%s">'
                 '<label class="m5-field">Clé API Gemini<input type="password" name="api_key" maxlength="4096" autocomplete="new-password" required></label>'
                 '<label class="m5-field">Modèle à utiliser (parmi la liste du test)<input name="modele" maxlength="120" autocomplete="off" value="%s"></label>'
                 '<button class="ax-btn ghost" type="submit" data-api="m56109/gemini/tester">Tester la connexion</button> '
                 '<button class="ax-btn" type="submit">Enregistrer la clé</button><p role="status" class="m5-status"></p></form>') % (
            e(CLES_URL, quote=True), e(FACTURATION_URL, quote=True), st['revision'], uuid.uuid4().hex, e(st['modele'], quote=True))
        if st['configuree']:
            forms += ('<form class="m5-form m82-inline" data-api="m56109/gemini/tester" data-reload="1"><input type="hidden" name="utiliser_existante" value="1">'
                      '<input type="hidden" name="cle" value="%s"><input type="hidden" name="modele" value="%s">'
                      '<label class="r52-day"><input type="checkbox" name="generation"> Tester aussi une génération (texte fictif « %s », quelques jetons de votre quota)</label>'
                      '<button class="ax-btn ghost" type="submit">Réexaminer la clé enregistrée</button><p role="status" class="m5-status"></p></form>') % (
                uuid.uuid4().hex, e(st['modele'], quote=True), e(GENERATION_TEXTE))
            options = ''.join('<option value="%s"%s>%s</option>' % (e(m, quote=True), ' selected' if m == st['modele'] else '', e(m)) for m in st['modeles'])
            forms += ('<form class="m5-form" data-api="m56109/gemini/activer" data-reload="1"><fieldset><legend>Activation</legend>'
                      '<input type="hidden" name="revision" value="%d"><input type="hidden" name="cle" value="%s">'
                      '<label class="m5-field">Modèle<select name="modele">%s</select></label>%s%s%s%s'
                      '<label class="r52-day"><input type="checkbox" name="envoi"%s> J’autorise l’envoi à Google des données pseudonymisées nécessaires</label>'
                      '<label class="r52-day"><input type="checkbox" name="actif"%s> Activer la connexion Gemini</label></fieldset>'
                      '<button class="ax-btn" type="submit">Enregistrer l’activation</button><p role="status" class="m5-status"></p></form>') % (
                st['revision'], uuid.uuid4().hex, options,
                _champ('prix_entree', 'Tarif entrée, $ par million de jetons', st['prix_entree']),
                _champ('prix_sortie', 'Tarif sortie, $ par million de jetons', st['prix_sortie']),
                _champ('plafond_mensuel', 'Plafond mensuel, $', st['plafond_mensuel']),
                _champ('plafond_requete', 'Plafond par requête, $', st['plafond_requete']),
                ' checked' if st['envoi'] else '', ' checked' if st['actif'] else '')
            forms += ('<form class="m5-form m82-inline" data-api="m5682/essai-requete"><input type="hidden" name="provider" value="%s">'
                      '<button class="ax-btn ghost" type="submit">Essayer une requête réelle (texte fictif, même forme que la rédaction)</button>'
                      '<p role="status" class="m5-status"></p></form>') % e(st['provider'], quote=True)
    return ('<section class="ax-card m82" id="k109-gemini"><h2>Google Gemini · clé API</h2>%s%s%s'
            '<p class="ax-muted">Une connexion jamais testée, ou modifiée depuis son test, n’est proposée à aucun routage. Tester ou enregistrer la clé ne change '
            'ni le mode de l’IA, ni les routes, ni les automatismes, et ne lit aucun dossier.</p></section>') % (tete, clarte, forms)
