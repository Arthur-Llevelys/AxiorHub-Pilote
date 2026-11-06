"""5.6.94 (lot traces) : étapes des travaux au format OpenTelemetry, écrit à la main (aucune dépendance nouvelle).

Modèle de données (celui d'OpenTelemetry) : un span par travail exécuté (racine de sa trace), un span enfant par appel au modèle et par
lot de requêtes Nextcloud ou IMAP, un événement « refus » pour chaque refus (pause, horaires, politique de fournisseur, budget, dossier
réservé). trace_id (16 octets) et span_id (8 octets) en hexadécimal viennent de ``secrets``, jamais d'un horodatage ; début et fin en
nanosecondes ; statut 0 (non renseigné), 1 (réussi), 2 (erreur) ; attributs et événements.

Branchements, sans second journal : ``desk.work_once`` ouvre le contexte (``debut_travail``) juste après ``trace480.begin`` et donne
l'issue (``issue``) ; ``trace480.end`` le clôt (``clore``) sur tous les chemins de sortie ; ``trace480.record_call`` ajoute l'appel au modèle
(``appel_modele``) ; les compteurs de ``charge5693`` (requêtes au stockage, commandes IMAP, octets) donnent les lots ; ``mailbox`` et
``dav`` notent l'étape en cours (``etape``), qui sert aussi à qualifier une erreur de relève (T06, ``cause_exception``).

Confidentialité (T02) : liste FERMÉE d'attributs (``ATTRIBUTS``), chacun avec un format qui ne laisse passer qu'un code, un nombre, un
booléen ou une empreinte : ni texte, ni consigne, ni nom de document, ni intitulé de dossier, ni adresse électronique, ni nom de personne.
Le dossier n'apparaît que sous une empreinte opaque (HMAC avec un sel propre à l'installation). Un attribut hors liste ou mal formé est
refusé (jamais écrit), et compté (``REFUSES``).

Coût (T03, T07) : aucun travail de fond. Les spans d'un travail restent en mémoire et sont écrits par paquets (``TAMPON`` spans ou
``DELAI_TAMPON`` secondes) dans une seule transaction, avec la purge (rétention de 7 jours par défaut, plafond de lignes) dans la même
transaction. Une base occupée fait perdre des traces, jamais un travail. Revers assumé : un arrêt brutal du processus perd au plus le
dernier paquet (une trentaine de secondes).

Export (T04) : ``ExportTraceServiceRequest`` en OTLP/JSON (identifiants en hexadécimal, entiers 64 bits en chaînes), téléchargeable,
et envoi facultatif, sur décision d'un administrateur, vers un collecteur OTLP/HTTP LOCAL uniquement (``verifier_collecteur``).
"""
import contextvars
import hashlib
import hmac
import imaplib
import ipaddress
import json
import math
import re
import secrets
import socket
import sqlite3
import ssl
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from .common import Stop

REGLAGE = 'traces5694:reglage'
SEL = 'traces5694:sel'
RETENTION_JOURS = 7
PLAFOND_LIGNES = 20000
BORNES_RETENTION = (1, 30)
BORNES_PLAFOND = (1000, 200000)
TAMPON = 100                 # spans gardés en mémoire avant écriture groupée (une validation SQLite coûte quelques ms)
DELAI_TAMPON = 30            # secondes au plus entre deux écritures
TAMPON_MAXIMUM = 2000        # base indisponible : au-delà, les plus anciens spans en mémoire sont abandonnés
CACHE_REGLAGE = 30           # secondes
LOT_ENVOI = 1000             # spans par requête vers le collecteur
CAUSES = ('pause', 'horaires', 'politique', 'budget', 'dossier_reserve')
LIBELLES_CAUSES = {'pause': 'pause de l’IA', 'horaires': 'horaires de l’IA', 'politique': 'fournisseur non autorisé',
                   'budget': 'plafond de dépense', 'dossier_reserve': 'dossier réservé'}
STATUT_NON_RENSEIGNE, STATUT_OK, STATUT_ERREUR = 0, 1, 2

SCHEMA = ('CREATE TABLE IF NOT EXISTS traces5694_spans(span_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, parent_id TEXT NOT NULL DEFAULT \'\', '
          'nom TEXT NOT NULL, genre TEXT NOT NULL, debut_ns INTEGER NOT NULL, fin_ns INTEGER NOT NULL, statut INTEGER NOT NULL DEFAULT 0, '
          'code TEXT NOT NULL DEFAULT \'\', attributs TEXT NOT NULL DEFAULT \'{}\', evenements TEXT NOT NULL DEFAULT \'[]\')')
INDEX = ('CREATE INDEX IF NOT EXISTS traces5694_debut ON traces5694_spans(debut_ns)',
         'CREATE INDEX IF NOT EXISTS traces5694_trace ON traces5694_spans(trace_id)')
CHAMPS = 'span_id,trace_id,parent_id,nom,genre,debut_ns,fin_ns,statut,code,attributs,evenements'   # colonnes nommées : une table migrée garde son ordre
COLONNES = (('parent_id', "TEXT NOT NULL DEFAULT ''"), ('statut', 'INTEGER NOT NULL DEFAULT 0'), ('code', "TEXT NOT NULL DEFAULT ''"),
            ('attributs', "TEXT NOT NULL DEFAULT '{}'"), ('evenements', "TEXT NOT NULL DEFAULT '[]'"))

# ---------------------------------------------------------------- liste fermée des attributs (T02)
_CODE = re.compile(r'[a-z0-9][a-z0-9_.:-]{0,79}')
_CLASSE = re.compile(r'[A-Za-z_][A-Za-z0-9_.]{0,79}')
_MODELE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/+-]{0,119}')
_EMPREINTE = re.compile(r'[0-9a-f]{16}')
_NOM = re.compile(r'[a-z0-9_]{1,40}( [a-z0-9][a-z0-9_.:-]{0,79})?')
_HEX = {16: re.compile(r'[0-9a-f]{16}'), 32: re.compile(r'[0-9a-f]{32}')}
ATTRIBUTS = {
    'axiorhub.travail.type': 'code', 'axiorhub.travail.id': 'entier', 'axiorhub.travail.origine': 'code',
    'axiorhub.travail.essai': 'entier', 'axiorhub.travail.attente_ms': 'entier', 'axiorhub.travail.execution_ms': 'entier',
    'axiorhub.travail.resultat': 'code', 'axiorhub.dossier.empreinte': 'empreinte',
    'error.type': 'classe', 'axiorhub.erreur.code': 'code', 'axiorhub.erreur.etape': 'code', 'axiorhub.erreur.service': 'code',
    'gen_ai.system': 'code', 'axiorhub.fournisseur.id': 'code', 'gen_ai.request.model': 'modele', 'axiorhub.modele.etape': 'code',
    'axiorhub.modele.palier': 'code', 'axiorhub.modele.externe': 'booleen', 'axiorhub.modele.duree_ms': 'entier',
    'gen_ai.usage.input_tokens': 'entier', 'gen_ai.usage.output_tokens': 'entier',
    'axiorhub.modele.nouvel_essai': 'booleen', 'axiorhub.modele.relais': 'booleen',
    'axiorhub.lot.service': 'code', 'axiorhub.lot.requetes': 'entier', 'axiorhub.lot.octets': 'entier',
    'axiorhub.refus.cause': 'code',
}
EVENEMENTS = ('refus',)
REFUSES = {'n': 0}


def _valeur(format_, valeur):
    """Valeur admise pour ce format, ou None (refus). Une chaîne ne passe que si elle a la forme d'un code : jamais une phrase."""
    if format_ == 'entier':
        return max(0, min(int(valeur), 2 ** 62)) if isinstance(valeur, int) and not isinstance(valeur, bool) else None
    if format_ == 'booleen':
        return valeur if isinstance(valeur, bool) else None
    if not isinstance(valeur, str):
        return None
    motif = {'code': _CODE, 'classe': _CLASSE, 'modele': _MODELE, 'empreinte': _EMPREINTE}[format_]
    return valeur if motif.fullmatch(valeur) and '@' not in valeur else None


def filtrer(attributs):
    """Attributs gardés : clés de la liste fermée, valeurs au bon format. Le reste est refusé et compté, jamais écrit."""
    sortie = {}
    for cle, valeur in (attributs or {}).items():
        format_ = ATTRIBUTS.get(cle)
        propre = _valeur(format_, valeur) if format_ else None
        if propre is None:
            if valeur not in (None, ''):
                REFUSES['n'] += 1
            continue
        sortie[cle] = propre
    return sortie


def code_public(texte):
    """Code d'erreur public (partie avant « : ») s'il en a la forme ; sinon vide."""
    code = str(texte or '').split(':', 1)[0].strip()
    return code if _CODE.fullmatch(code) else ''


# ---------------------------------------------------------------- réglage
_cache = {}
_verrou = threading.Lock()
_tampons = {}


def _bornes(valeur, bornes, defaut):
    try:
        valeur = int(valeur)
    except (TypeError, ValueError):
        return defaut
    return max(bornes[0], min(valeur, bornes[1]))


def reglage(desk):
    brut = desk.settings(REGLAGE, None)
    brut = brut if isinstance(brut, dict) else {}
    return {'actif': brut.get('actif', True) is not False, 'retention_jours': _bornes(brut.get('retention_jours'), BORNES_RETENTION, RETENTION_JOURS),
            'plafond': _bornes(brut.get('plafond'), BORNES_PLAFOND, PLAFOND_LIGNES), 'collecteur': str(brut.get('collecteur') or ''),
            'revision': brut['revision'] if isinstance(brut.get('revision'), int) and not isinstance(brut.get('revision'), bool) else 0}


def _cle(desk):
    return str(desk.c.get('state_dir') or id(desk))


def _reglage_cache(desk):
    cle = _cle(desk)
    entree = _cache.get(cle)
    if entree and time.monotonic() - entree[0] < CACHE_REGLAGE:
        return entree[1]
    try:
        valeur = reglage(desk)
    except (sqlite3.Error, ValueError, TypeError):
        valeur = {'actif': True, 'retention_jours': RETENTION_JOURS, 'plafond': PLAFOND_LIGNES, 'collecteur': '', 'revision': 0}
    _cache[cle] = (time.monotonic(), valeur)
    return valeur


def oublier_cache():
    _cache.clear()


def enregistrer_reglage(desk, data, auteur):
    """Réglage des traces (actif, rétention, plafond, collecteur local). Révision périmée : 409. Le collecteur est vérifié avant d'être gardé."""
    actuel = reglage(desk)
    revision = data.get('revision')
    try:
        revision = int(str(revision))
    except (TypeError, ValueError):
        raise Stop('reglage_traces_invalide') from None
    if revision != actuel['revision']:
        raise Stop('revision_perimee')
    retention = data.get('retention_jours', actuel['retention_jours'])
    plafond = data.get('plafond', actuel['plafond'])
    for valeur, bornes in ((retention, BORNES_RETENTION), (plafond, BORNES_PLAFOND)):
        try:
            if not bornes[0] <= int(str(valeur)) <= bornes[1]:
                raise Stop('reglage_traces_invalide')
        except (TypeError, ValueError):
            raise Stop('reglage_traces_invalide') from None
    collecteur = str(data.get('collecteur') or '').strip()
    if collecteur:
        collecteur = verifier_collecteur(collecteur, resoudre=False)
    actif = data.get('actif')
    actif = actif in (True, 'on', 'oui', '1', 1, 'true')
    nouveau = {'actif': actif, 'retention_jours': int(str(retention)), 'plafond': int(str(plafond)), 'collecteur': collecteur,
               'revision': actuel['revision'] + 1}
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        if reglage(desk)['revision'] != actuel['revision']:
            raise Stop('revision_perimee')
        desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (REGLAGE, json.dumps(nouveau)))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    _cache.pop(_cle(desk), None)
    hote = urlsplit(collecteur).hostname or '' if collecteur else ''
    desk.audit('traces5694_reglage', {'actif': actif, 'retention_jours': nouveau['retention_jours'], 'plafond': nouveau['plafond'],
                                      'collecteur': hote, 'revision': nouveau['revision'], 'par': str(auteur)[:120]})
    return reglage(desk)


def _sel(desk):
    cle = 'sel:' + _cle(desk)
    if cle in _cache:
        return _cache[cle]
    valeur = desk.settings(SEL, None)
    if not (isinstance(valeur, str) and len(valeur) == 64):
        if desk.db.in_transaction:      # jamais la validation de la transaction d'un autre : le sel sera créé au prochain travail
            return ''
        valeur = secrets.token_hex(32)
        desk.db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', (SEL, json.dumps(valeur)))
        desk.db.commit()
        valeur = desk.settings(SEL, valeur)
    _cache[cle] = valeur
    return valeur


def empreinte_dossier(desk, matter):
    """Empreinte opaque d'un dossier : HMAC-SHA256 avec le sel de l'installation (un identifiant deviné ne se vérifie pas hors de la base)."""
    matter = str(matter or '').strip()
    sel = _sel(desk) if matter else ''
    if not sel:
        return ''
    return hmac.new(sel.encode(), matter.encode('utf-8', 'replace'), hashlib.sha256).hexdigest()[:16]


# ---------------------------------------------------------------- étape en cours (IMAP, Nextcloud)
_fil = threading.local()


def etape(service, nom):
    """Dernière étape réseau du fil (``imap``/``select``, ``nextcloud``/``propfind``…) : sert à qualifier une erreur, jamais à la tracer seule."""
    _fil.etape = (service, nom)


def derniere_etape():
    return getattr(_fil, 'etape', None) or ('', '')


# erreurs de transport : l'étape notée en est la cause certaine (une erreur de fichier local, elle, n'est pas imputée à IMAP)
_CLASSES_RESEAU = (imaplib.IMAP4.error, ConnectionError, TimeoutError, socket.gaierror, socket.herror, ssl.SSLError, EOFError)


def cause_exception(error):
    """T06 : cause non confidentielle d'une exception inattendue : classe, dernière étape réseau, code (errno, raison TLS, type IMAP).
    Jamais le message de l'exception : une réponse de serveur IMAP peut citer un sujet, une adresse ou un extrait de courriel."""
    classe = type(error).__qualname__ if _CLASSE.fullmatch(type(error).__qualname__ or '') else 'Exception'
    module = type(error).__module__ or ''
    if module not in ('builtins', '') and _CLASSE.fullmatch(module + '.' + classe):
        classe = (module.rsplit('.', 1)[-1] + '.' + classe)[:80]
    service, nom = derniere_etape()
    code = ''
    if isinstance(error, imaplib.IMAP4.abort):
        code = 'imap_connexion_interrompue'
    elif isinstance(error, imaplib.IMAP4.readonly):
        code = 'imap_boite_en_lecture_seule'
    elif isinstance(error, imaplib.IMAP4.error):
        code = 'imap_reponse_refusee'
    elif isinstance(error, (TimeoutError, socket.timeout)):
        code = 'delai_depasse'
    elif isinstance(error, ssl.SSLError):
        raison = str(getattr(error, 'reason', '') or '').lower()
        code = ('tls_' + raison) if _CODE.fullmatch('tls_' + raison) and raison else 'tls_echec'
    elif isinstance(error, OSError) and isinstance(error.errno, int):
        code = 'errno_%d' % error.errno
    elif isinstance(error, sqlite3.Error):
        nom_erreur = str(getattr(error, 'sqlite_errorname', '') or '').lower()
        code = nom_erreur if _CODE.fullmatch(nom_erreur) else 'base_locale'
        service, nom = 'base', ''
    reseau = isinstance(error, _CLASSES_RESEAU) and not isinstance(error, sqlite3.Error)
    detail = {'cause': classe}
    if code:
        detail['code'] = code
    if service:
        detail['service'] = service
        # l'étape n'est la cause certaine que pour une erreur réseau ; sinon c'est la dernière étape connue avant l'erreur
        detail['stage'] = (nom if reseau else 'apres_' + nom) if nom else ''
    if not detail.get('stage'):
        detail.pop('stage', None)
    return detail


# ---------------------------------------------------------------- contexte d'un travail
_CTX = contextvars.ContextVar('axiorhub_traces5694', default=None)


def _ns_iso(texte):
    if not texte:
        return 0
    try:
        from datetime import datetime, timezone
        valeur = datetime.fromisoformat(str(texte).replace('Z', '+00:00'))
        valeur = valeur if valeur.tzinfo else valeur.replace(tzinfo=timezone.utc)
        return int(valeur.timestamp() * 1e9)
    except (TypeError, ValueError, OverflowError):
        return 0


def _champ(row, cle):
    try:
        return row[cle]
    except (KeyError, IndexError, TypeError):
        return None


def debut_travail(desk, row, matter=''):
    """Ouvre la trace d'un travail (appelé par ``desk.work_once``). Ne lève jamais d'exception ; traces désactivées : rien n'est gardé."""
    try:
        _fil.etape = None
        if not _reglage_cache(desk)['actif']:
            _CTX.set(None)
            return
        maintenant = time.time_ns()
        depuis = max(_ns_iso(_champ(row, 'created')), _ns_iso(_champ(row, 'not_before')))
        _CTX.set({'desk': desk, 'trace_id': secrets.token_hex(16), 'span_id': secrets.token_hex(8), 'debut': maintenant,
                  'kind': str(_champ(row, 'kind') or ''), 'job_id': _champ(row, 'id'), 'origine': str(_champ(row, 'origin') or ''),
                  'essai': int(_champ(row, 'attempts') or 0) + 1, 'attente_ms': max(0, (maintenant - depuis) // 1_000_000) if depuis else 0,
                  'dossier': empreinte_dossier(desk, matter), 'appels': [], 'evenements': [], 'resultat': '', 'code': '', 'detail': {},
                  'jetons': None})
    except Exception:   # noqa: BLE001 — le traçage n'empêche jamais un travail
        _CTX.set(None)


def _cause_refus(code):
    if not code:
        return ''
    from .arret5643 import CODES_HORAIRES
    if code in CODES_HORAIRES:
        return 'horaires'
    if code in ('automatismes_en_pause', 'ia_en_pause', 'agent_en_pause_sur_ce_dossier', 'dossier_en_pause', 'controle_pause_indisponible'):
        return 'pause'
    if code == 'dossier_reserve':
        return 'dossier_reserve'
    from .budget5668 import BUDGET_CODES
    from .budget_ledger5689 import ATTENTE_CODES
    from .chatgpt5682 import HELD_CODES
    if code in BUDGET_CODES or code in ATTENTE_CODES or code in HELD_CODES:
        return 'budget'
    if (code.startswith('politique_') or code in ('fournisseur_interdit_par_politique', 'autorisation_donnees_externes_requise', 'perimetre_local_requis',
                                                  'chatgpt_usage_plan_non_autorise', 'fournisseur_ia_inactif', 'politique_illisible')):
        return 'politique'
    return ''


def _evenement(cible, cause, code):
    cible.append({'nom': 'refus', 'temps': time.time_ns(), 'attributs': filtrer({'axiorhub.refus.cause': cause, 'axiorhub.erreur.code': code})})


def refus(code):
    """Refus ou arrêt rencontré par le travail (code public d'un ``Stop``) : événement s'il s'agit d'un refus connu, code provisoire sinon."""
    try:
        ctx = _CTX.get()
        if ctx is None:
            return
        code = code_public(code)
        ctx['code'] = code
        cause = _cause_refus(code)
        if cause and not any(ev['attributs'].get('axiorhub.erreur.code') == code for appel in ctx['appels'] for ev in appel['evenements']):
            _evenement(ctx['evenements'], cause, code)
    except Exception:   # noqa: BLE001
        return


def issue(status, result=None):
    """Issue définitive du travail (terminé, erreur, suspendu, annulé), avec son code et sa cause non confidentielle."""
    try:
        ctx = _CTX.get()
        if ctx is None:
            return
        ctx['resultat'] = str(status or '')
        result = result if isinstance(result, dict) else {}
        if status != 'done':
            ctx['code'] = code_public(result.get('erreur')) or ctx['code']
            detail = result.get('detail')
            ctx['detail'] = detail if isinstance(detail, dict) else {}
    except Exception:   # noqa: BLE001
        return


def jetons(entree=0, sortie=0):
    """Jetons connus de l'appel en cours (fournisseur externe ou Ollama), repris par l'appel suivant tracé."""
    ctx = _CTX.get()
    if ctx is not None:
        ctx['jetons'] = (int(entree or 0), int(sortie or 0))


def appel_modele(cfg, stage, status, error, duration_ms):
    """Appel au modèle du travail en cours (depuis ``trace480.record_call``). Hors travail (page, voie interactive sans contexte) : rien."""
    try:
        ctx = _CTX.get()
        if ctx is None:
            return
        cfg = cfg or {}
        fin = time.time_ns()
        duree = max(0, int(duration_ms or 0))
        fournisseur_type = str(cfg.get('provider_type') or cfg.get('type') or 'ollama')
        fournisseur = str(cfg.get('provider_id') or fournisseur_type)
        palier = next((str(cfg.get(k)) for k in ('tier', 'palier', 'role', 'profile') if cfg.get(k)), '')
        precedent = next((a for a in reversed(ctx['appels']) if a['statut'] == STATUT_ERREUR), None)
        attributs = {'gen_ai.system': fournisseur_type, 'axiorhub.fournisseur.id': fournisseur, 'gen_ai.request.model': str(cfg.get('model') or ''),
                     'axiorhub.modele.etape': str(stage or ''), 'axiorhub.modele.palier': palier, 'axiorhub.modele.externe': fournisseur_type != 'ollama',
                     'axiorhub.modele.duree_ms': duree}
        if precedent is not None:      # après un échec : même fournisseur = nouvel essai ; autre fournisseur = relais
            attributs['axiorhub.modele.nouvel_essai'] = precedent['fournisseur'] == fournisseur
            attributs['axiorhub.modele.relais'] = precedent['fournisseur'] != fournisseur
        if ctx['jetons']:
            attributs['gen_ai.usage.input_tokens'], attributs['gen_ai.usage.output_tokens'] = ctx['jetons']
            ctx['jetons'] = None
        code = code_public(error) if status != 'ok' else ''
        appel = {'span_id': secrets.token_hex(8), 'debut': fin - duree * 1_000_000, 'fin': fin, 'statut': STATUT_OK if status == 'ok' else STATUT_ERREUR,
                 'code': code, 'fournisseur': fournisseur, 'attributs': filtrer(attributs), 'evenements': [], 'etape': str(stage or '')}
        cause = _cause_refus(code)
        if cause:
            _evenement(appel['evenements'], cause, code)
        if len(ctx['appels']) < 200:
            ctx['appels'].append(appel)
    except Exception:   # noqa: BLE001
        return


def _lots(ctx):
    """Lots de requêtes du travail, d'après les compteurs de ``charge5693`` (lus sans les remettre à zéro)."""
    from . import charge5693
    compteurs = getattr(charge5693._fil, 'compteurs', None) or {}
    temps = getattr(charge5693._fil, 'temps', None) or {}
    sortie = []
    for service, categorie in (('nextcloud', 'stockage'), ('imap', 'imap')):
        n = int(compteurs.get(categorie) or 0)
        if not n:
            continue
        debut, fin = temps.get(categorie) or (ctx['debut'], ctx['debut'])
        sortie.append({'span_id': secrets.token_hex(8), 'nom': 'lot_' + service, 'debut': debut, 'fin': max(fin, debut), 'statut': STATUT_NON_RENSEIGNE,
                       'attributs': filtrer({'axiorhub.lot.service': service, 'axiorhub.lot.requetes': n,
                                             'axiorhub.lot.octets': int(compteurs.get(categorie + '_octets') or 0)})})
    return sortie


def clore():
    """Fin du travail (depuis ``trace480.end``) : spans construits et mis au tampon ; écriture groupée si le tampon est plein ou ancien."""
    ctx = _CTX.get()
    _CTX.set(None)
    if ctx is None:
        return
    try:
        fin = time.time_ns()
        resultat = ctx['resultat'] or 'remis_en_attente'
        statut = STATUT_OK if resultat == 'done' else (STATUT_ERREUR if resultat == 'error' else STATUT_NON_RENSEIGNE)
        detail = ctx['detail']
        attributs = {'axiorhub.travail.type': ctx['kind'], 'axiorhub.travail.id': ctx['job_id'], 'axiorhub.travail.origine': ctx['origine'],
                     'axiorhub.travail.essai': ctx['essai'], 'axiorhub.travail.attente_ms': ctx['attente_ms'],
                     'axiorhub.travail.execution_ms': (fin - ctx['debut']) // 1_000_000, 'axiorhub.travail.resultat': resultat,
                     'axiorhub.dossier.empreinte': ctx['dossier']}
        if ctx['code'] and resultat != 'done':
            attributs['axiorhub.erreur.code'] = ctx['code']
        if detail:
            attributs['error.type'] = str(detail.get('cause') or '')
            attributs['axiorhub.erreur.service'] = str(detail.get('service') or '')
            attributs['axiorhub.erreur.etape'] = str(detail.get('stage') or '')
            if detail.get('code'):
                attributs['axiorhub.erreur.code'] = str(detail['code'])
        trace, racine = ctx['trace_id'], ctx['span_id']
        lignes = [(racine, trace, '', _nom(ctx['kind'], 'travail'), 'travail', ctx['debut'], fin, statut, ctx['code'] if statut != STATUT_OK else '',
                   json.dumps(filtrer(attributs), sort_keys=True), json.dumps(ctx['evenements']))]
        for appel in ctx['appels']:
            lignes.append((appel['span_id'], trace, racine, _nom(appel['etape'], 'appel_modele', 'appel_modele '), 'modele', appel['debut'], appel['fin'],
                           appel['statut'], appel['code'], json.dumps(appel['attributs'], sort_keys=True), json.dumps(appel['evenements'])))
        for lot in _lots(ctx):
            lignes.append((lot['span_id'], trace, racine, lot['nom'], 'lot', lot['debut'], lot['fin'], lot['statut'], '',
                           json.dumps(lot['attributs'], sort_keys=True), '[]'))
        _ajouter(ctx['desk'], lignes)
    except Exception:   # noqa: BLE001 — le traçage n'empêche jamais un travail
        return


def _nom(valeur, defaut, prefixe=''):
    valeur = str(valeur or '')
    return (prefixe + valeur) if _CODE.fullmatch(valeur) else defaut


# ---------------------------------------------------------------- écriture groupée et purge (T03)
def _ajouter(desk, lignes):
    cle = _cle(desk)
    with _verrou:
        tampon = _tampons.setdefault(cle, {'lignes': [], 'depuis': time.monotonic()})
        tampon['lignes'].extend(lignes)
        if len(tampon['lignes']) > TAMPON_MAXIMUM:
            del tampon['lignes'][:len(tampon['lignes']) - TAMPON_MAXIMUM]
        pret = len(tampon['lignes']) >= TAMPON or time.monotonic() - tampon['depuis'] >= DELAI_TAMPON
    if pret:
        vider(desk)


def schema(desk):
    """Migration idempotente : table, colonnes manquantes (``PRAGMA table_info`` puis ``ALTER TABLE``), index."""
    if getattr(desk, '_traces5694', False):
        return
    desk.db.execute(SCHEMA)
    presentes = {r[1] for r in desk.db.execute('PRAGMA table_info(traces5694_spans)')}
    for nom, definition in COLONNES:
        if nom not in presentes:
            desk.db.execute('ALTER TABLE traces5694_spans ADD COLUMN %s %s' % (nom, definition))
    for requete in INDEX:
        desk.db.execute(requete)
    desk.db.commit()
    desk._traces5694 = True


def vider(desk):
    """Écrit le tampon de cette base en une transaction, purge comprise. Base occupée ou transaction déjà ouverte : le tampon attend."""
    cle = _cle(desk)
    with _verrou:
        tampon = _tampons.get(cle)
        if not tampon or not tampon['lignes']:
            return 0
        lignes, tampon['lignes'] = tampon['lignes'], []
        tampon['depuis'] = time.monotonic()
    if desk.db.in_transaction:
        _rendre(cle, lignes)
        return 0
    r = _reglage_cache(desk)
    try:
        desk.db.execute('PRAGMA busy_timeout=300')
        schema(desk)
        desk.db.executemany('INSERT OR IGNORE INTO traces5694_spans(' + CHAMPS + ') VALUES (?,?,?,?,?,?,?,?,?,?,?)', lignes)
        _purger(desk, r['retention_jours'], r['plafond'])
        desk.db.commit()
        return len(lignes)
    except sqlite3.Error:
        try:
            desk.db.rollback()
        except sqlite3.Error:
            pass
        _rendre(cle, lignes)
        return 0
    finally:
        try:
            desk.db.execute('PRAGMA busy_timeout=%d' % (int(getattr(desk, '_busy', 10)) * 1000))
        except sqlite3.Error:
            pass


def _rendre(cle, lignes):
    with _verrou:
        tampon = _tampons.setdefault(cle, {'lignes': [], 'depuis': time.monotonic()})
        tampon['lignes'][:0] = lignes
        if len(tampon['lignes']) > TAMPON_MAXIMUM:
            del tampon['lignes'][:len(tampon['lignes']) - TAMPON_MAXIMUM]


def _purger(desk, retention_jours, plafond):
    """Dans la transaction d'écriture : traces plus anciennes que la rétention, puis traces entières les plus anciennes au-delà du plafond."""
    limite = time.time_ns() - int(retention_jours) * 86400 * 1_000_000_000
    desk.db.execute('DELETE FROM traces5694_spans WHERE debut_ns<?', (limite,))
    n = desk.db.execute('SELECT COUNT(*) FROM traces5694_spans').fetchone()[0]
    while n > plafond:
        exces = n - plafond
        retire = desk.db.execute("DELETE FROM traces5694_spans WHERE trace_id IN (SELECT trace_id FROM traces5694_spans WHERE parent_id='' "
                                 "ORDER BY debut_ns LIMIT ?)", (exces,)).rowcount
        if retire <= 0:      # spans orphelins (racine déjà retirée) : les plus anciens
            retire = desk.db.execute('DELETE FROM traces5694_spans WHERE span_id IN (SELECT span_id FROM traces5694_spans ORDER BY debut_ns LIMIT ?)',
                                     (exces,)).rowcount
        if retire <= 0:
            break
        n -= retire


# ---------------------------------------------------------------- lecture
def spans(desk, depuis_ns=0, limite=None):
    """Spans enregistrés depuis cet instant (écrit d'abord le tampon du processus). ``None`` si la base n'a pas pu être lue."""
    vider(desk)
    try:
        schema(desk)
        requete = 'SELECT ' + CHAMPS + ' FROM traces5694_spans WHERE debut_ns>=? ORDER BY debut_ns'
        lignes = desk.db.execute(requete + (' LIMIT %d' % int(limite) if limite else ''), (int(depuis_ns),)).fetchall()
    except sqlite3.Error:
        return None
    sortie = []
    for l in lignes:
        try:
            sortie.append({'span_id': l[0], 'trace_id': l[1], 'parent_id': l[2], 'nom': l[3], 'genre': l[4], 'debut': int(l[5]), 'fin': int(l[6]),
                           'statut': int(l[7]), 'code': l[8], 'attributs': json.loads(l[9] or '{}'), 'evenements': json.loads(l[10] or '[]')})
        except (TypeError, ValueError):
            continue
    return sortie


def _centile(valeurs, p):
    if not valeurs:
        return None
    valeurs = sorted(valeurs)
    rang = max(0, min(len(valeurs) - 1, math.ceil(p / 100 * len(valeurs)) - 1))   # méthode du rang le plus proche
    return valeurs[rang]


def synthese(desk, heures=24, maintenant_ns=None):
    """T05 : par type de travail sur la période, durée médiane et 95e centile, attente avant démarrage, erreurs, nouveaux essais, refus par
    cause ; et les dix travaux les plus lents avec la frise de leurs étapes. ``None`` si la base n'a pas pu être lue (jamais un faux zéro)."""
    maintenant_ns = maintenant_ns or time.time_ns()
    lignes = spans(desk, maintenant_ns - int(heures) * 3600 * 1_000_000_000)
    if lignes is None:
        return None
    racines = [s for s in lignes if s['genre'] == 'travail']
    enfants = {}
    for s in lignes:
        if s['parent_id']:
            enfants.setdefault(s['parent_id'], []).append(s)
    types = {}
    for s in racines:
        a = s['attributs']
        t = types.setdefault(a.get('axiorhub.travail.type') or s['nom'], {'execution': [], 'attente': [], 'erreurs': 0, 'essais': 0, 'refus': {}, 'n': 0})
        t['n'] += 1
        t['execution'].append(int(a.get('axiorhub.travail.execution_ms', (s['fin'] - s['debut']) // 1_000_000)))
        t['attente'].append(int(a.get('axiorhub.travail.attente_ms', 0)))
        t['erreurs'] += 1 if s['statut'] == STATUT_ERREUR else 0
        t['essais'] += 1 if int(a.get('axiorhub.travail.essai', 1)) > 1 else 0
        for ev in s['evenements'] + [ev for c in enfants.get(s['span_id'], []) for ev in c['evenements']]:
            cause = (ev.get('attributs') or {}).get('axiorhub.refus.cause')
            if cause:
                t['refus'][cause] = t['refus'].get(cause, 0) + 1
        t['essais'] += sum(1 for c in enfants.get(s['span_id'], []) if c['attributs'].get('axiorhub.modele.nouvel_essai'))
    par_type = sorted(({'kind': k, 'n': v['n'], 'mediane_ms': _centile(v['execution'], 50), 'p95_ms': _centile(v['execution'], 95),
                        'attente_mediane_ms': _centile(v['attente'], 50), 'erreurs': v['erreurs'], 'essais': v['essais'], 'refus': v['refus']}
                       for k, v in types.items()), key=lambda x: (-(x['p95_ms'] or 0), x['kind']))
    lents = sorted(racines, key=lambda s: -(s['fin'] - s['debut']))[:10]
    frises = []
    for s in lents:
        etapes = sorted(enfants.get(s['span_id'], []), key=lambda c: c['debut'])
        frises.append({'kind': s['attributs'].get('axiorhub.travail.type') or s['nom'], 'debut': s['debut'], 'duree_ms': (s['fin'] - s['debut']) // 1_000_000,
                       'attente_ms': int(s['attributs'].get('axiorhub.travail.attente_ms', 0)), 'resultat': s['attributs'].get('axiorhub.travail.resultat', ''),
                       'code': s['code'], 'etapes': [{'nom': c['nom'], 'genre': c['genre'], 'decalage_ms': max(0, (c['debut'] - s['debut']) // 1_000_000),
                                                      'duree_ms': max(0, (c['fin'] - c['debut']) // 1_000_000), 'statut': c['statut'], 'code': c['code'],
                                                      'attributs': c['attributs']} for c in etapes]})
    return {'heures': heures, 'travaux': len(racines), 'spans': len(lignes), 'par_type': par_type, 'lents': frises}


# ---------------------------------------------------------------- OTLP/JSON (T04)
def _valeur_otlp(valeur):
    if isinstance(valeur, bool):
        return {'boolValue': valeur}
    if isinstance(valeur, int):
        return {'intValue': str(valeur)}
    return {'stringValue': str(valeur)}


def _attributs_otlp(attributs):
    return [{'key': k, 'value': _valeur_otlp(v)} for k, v in sorted(filtrer(attributs).items())]


def otlp(lignes, version=''):
    """``ExportTraceServiceRequest`` (OTLP/JSON) : identifiants en hexadécimal, horodatages et entiers 64 bits en chaînes décimales.
    Les attributs repassent par la liste fermée : une ligne altérée dans la base ne sort pas davantage."""
    if not version:
        from . import __version__ as version
    sortie = []
    for s in lignes:
        if not (_HEX[32].fullmatch(s['trace_id']) and _HEX[16].fullmatch(s['span_id']) and (not s['parent_id'] or _HEX[16].fullmatch(s['parent_id']))):
            continue
        span = {'traceId': s['trace_id'], 'spanId': s['span_id'], 'name': s['nom'] if _NOM.fullmatch(str(s['nom'] or '')) else str(s['genre']),
                'kind': 1 if s['genre'] == 'travail' else 3, 'startTimeUnixNano': str(int(s['debut'])), 'endTimeUnixNano': str(int(s['fin'])),
                'attributes': _attributs_otlp(s['attributs']),
                'events': [{'timeUnixNano': str(int(ev.get('temps') or s['debut'])), 'name': ev.get('nom'), 'attributes': _attributs_otlp(ev.get('attributs') or {})}
                           for ev in s['evenements'] if ev.get('nom') in EVENEMENTS],
                'status': {'code': int(s['statut']) if s['statut'] in (0, 1, 2) else 0}}
        if s['parent_id']:
            span['parentSpanId'] = s['parent_id']
        if span['status']['code'] == STATUT_ERREUR and code_public(s['code']):
            span['status']['message'] = code_public(s['code'])
        sortie.append(span)
    return {'resourceSpans': [{'resource': {'attributes': [{'key': 'service.name', 'value': {'stringValue': 'axiorhub'}},
                                                          {'key': 'service.version', 'value': {'stringValue': str(version)}}]},
                               'scopeSpans': [{'scope': {'name': 'axiorhub.traces5694', 'version': str(version)}, 'spans': sortie}]}]}


def export(desk, heures=24):
    """Téléchargement : (nom du fichier, contenu JSON, nombre de spans). ``Stop`` si la base n'a pas pu être lue."""
    heures = max(1, min(int(heures), 24 * BORNES_RETENTION[1]))
    lignes = spans(desk, time.time_ns() - heures * 3600 * 1_000_000_000)
    if lignes is None:
        raise Stop('traces_illisibles')
    contenu = json.dumps(otlp(lignes), ensure_ascii=False, separators=(',', ':'))
    desk.audit('traces5694_export', {'spans': len(lignes), 'heures': heures, 'sha256': hashlib.sha256(contenu.encode()).hexdigest()})
    return 'traces-axiorhub-%dh.otlp.json' % heures, contenu, len(lignes)


# ---------------------------------------------------------------- collecteur local (T04)
LOCAUX = frozenset(('127.0.0.1', 'localhost', '::1'))
_ETIQUETTE = re.compile(r'[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?')


def verifier_collecteur(url, resoudre=True, resolveur=socket.getaddrinfo):
    """Adresse d'un collecteur OTLP/HTTP admise : http(s), sans identifiant, sans paramètre ni fragment, hôte ``127.0.0.1``, ``localhost``,
    ``::1`` ou nom d'hôte SANS point (réseau local configuré par l'administrateur). Un nom sans point qui se résout vers une adresse publique
    est refusé à l'envoi (``resoudre``). Tout le reste est refusé : aucune adresse publique, aucun service en ligne. Renvoie l'URL normalisée."""
    texte = str(url or '').strip()
    if not texte or len(texte) > 300 or any(c in texte for c in '\r\n\t \\'):
        raise Stop('collecteur_invalide')
    try:
        p = urlsplit(texte)
        hote = (p.hostname or '').lower()
        port = p.port
    except ValueError:
        raise Stop('collecteur_invalide') from None
    if p.scheme not in ('http', 'https') or p.username is not None or p.password is not None or p.query or p.fragment or not hote:
        raise Stop('collecteur_invalide')
    if hote not in LOCAUX:
        if '.' in hote or ':' in hote or not _ETIQUETTE.fullmatch(hote) or hote.isdigit() or re.fullmatch(r'0x[0-9a-f]+', hote):
            raise Stop('collecteur_non_local')
        if resoudre:
            try:
                adresses = {a[4][0] for a in resolveur(hote, port or 4318, 0, socket.SOCK_STREAM)}
            except (OSError, UnicodeError):
                raise Stop('collecteur_introuvable') from None
            for adresse in adresses:
                ip = ipaddress.ip_address(adresse.split('%', 1)[0])
                if ip.is_global or ip.is_multicast or ip.is_unspecified or str(ip) in ('169.254.169.254', 'fd00:ec2::254'):
                    raise Stop('collecteur_non_local')
    chemin = p.path if p.path not in ('', '/') else '/v1/traces'
    netloc = ('[%s]' % hote if ':' in hote else hote) + (':%d' % port if port else '')
    return urlunsplit((p.scheme, netloc, chemin, '', ''))


def _poster(url, corps, delai=5):
    """Envoi réel (sans proxy, sans redirection suivie). Remplacé par un faux dans les essais : aucun réseau dans les tests."""
    import urllib.request

    class _SansRedirection(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    ouvreur = urllib.request.build_opener(urllib.request.ProxyHandler({}), _SansRedirection())
    requete = urllib.request.Request(url, data=corps, method='POST', headers={'Content-Type': 'application/json'})
    with ouvreur.open(requete, timeout=delai) as reponse:
        return int(reponse.status)


ENVOIS = 'traces5694:envois'


def envoyer(desk, cle, auteur, heures=24, poster=None, resolveur=socket.getaddrinfo):
    """Envoi décidé par un administrateur vers le collecteur LOCAL réglé : adresse revérifiée (résolution comprise) juste avant l'envoi,
    clé d'idempotence (une clé déjà employée : 409), lots de ``LOT_ENVOI`` spans. Renvoie le nombre de spans et de requêtes."""
    cle = str(cle or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', cle):
        raise Stop('cle_idempotence_requise')
    r = reglage(desk)
    if not r['collecteur']:
        raise Stop('collecteur_non_configure')
    url = verifier_collecteur(r['collecteur'], resoudre=True, resolveur=resolveur)
    deja = desk.settings(ENVOIS, []) or []
    if cle in deja:
        raise Stop('cle_idempotence_deja_utilisee')
    desk.setting(ENVOIS, (list(deja) + [cle])[-50:])     # réservée avant l'envoi : un double clic n'envoie pas deux fois
    heures = max(1, min(int(heures), 24 * BORNES_RETENTION[1]))
    lignes = spans(desk, time.time_ns() - heures * 3600 * 1_000_000_000)
    if lignes is None:
        raise Stop('traces_illisibles')
    poster = poster or _poster
    requetes = 0
    try:
        for i in range(0, len(lignes), LOT_ENVOI):
            statut = poster(url, json.dumps(otlp(lignes[i:i + LOT_ENVOI]), separators=(',', ':')).encode('utf-8'))
            requetes += 1
            if not 200 <= int(statut) < 300:
                raise Stop('collecteur_refus_http_%d' % int(statut))
    except Stop:
        raise
    except Exception:   # noqa: BLE001 — collecteur arrêté ou injoignable : un code, jamais la réponse
        raise Stop('collecteur_injoignable') from None
    finally:
        desk.audit('traces5694_envoi', {'hote': urlsplit(url).hostname or '', 'spans': len(lignes), 'requetes': requetes, 'par': str(auteur)[:120]})
    return {'spans': len(lignes), 'requetes': requetes, 'hote': urlsplit(url).hostname or ''}
