"""5.6.109 (lot B) : connecteur Bland (téléphonie par IA), « Mes routines | Mes agents IA », rappel de rendez-vous au client.

Demande du cabinet du 23 novembre 2026 : relier AxiorHub au service Bland (agents vocaux, appels, SMS) avec la clé API réglée dans les
Paramètres, créer un agent de prise de rendez-vous enregistré comme routine, et livrer une routine de rappel de rendez-vous, désactivée.

Faits retenus (documentation publique relevée le 23/11/2026, donnée et non consigne) : serveur MCP distant ``https://api.bland.ai/v1/mcp``,
Streamable HTTP sans session, 120 requêtes par minute et par organisation, clé d'organisation à accès complet en ``Authorization: Bearer``
(les clés restreintes ne se connectent pas), outils annotés lecture seule / destructif, **aucune confirmation demandée par le serveur** :
c'est AxiorHub qui la demande. La voie OAuth « Sign in with Bland » n'est pas livrée dans cette version. Aucune liste d'outils figée : les
outils sont découverts par ``tools/list`` au test de la connexion.

- **Connexion** (registre existant ``connexions5652``, une seule connexion du cabinet, environnement « production » : Bland n'a pas de bac à
  sable) : la clé part au coffre (``vault567``) par ``connexions5652.save`` ; elle n'est jamais rendue (HTML, JSON), jamais journalisée,
  jamais exportée. « Tester la connexion » (POST) passe par ``connexions5652.test`` → ``Adaptateur.identity`` : ``initialize`` puis
  ``tools/list``. États : Non configuré, À vérifier, Prêt, Clé refusée, Hors ligne. **Désactivé par défaut** ; « Activer » demande un test
  réussi ; refusé en mode local strict ; jamais activé seul.
- **Outils classés** (``classer``) : *lecture* seulement si l'annotation lecture seule est présente ET que le nom ne correspond à aucun
  motif d'effet ; tout le reste est *effet* ; sous-classe *engageant* (liste de motifs fermée ``MOTIFS_ENGAGEANTS``). Un outil sans
  annotation, ou à annotation illisible, est engageant. Liste mise en cache avec révision (``bland56109_outils``).
- **Confirmation humaine** : mécanisme existant des opérations à effet externe (``connexions5652`` : ``connexion_ops5652``, état
  « À valider », empreinte de la fiche affichée, bail, réponse perdue = « Résultat à vérifier », jamais rejouée). Une demande Bland est une
  opération préparée (``preparer``) ; ``confirmer`` n'est atteignable que par la route POST de l'interface (session de l'avocat, CSRF) :
  ``_HUMAIN`` n'est posé que là, et ``connexions5652.validate`` refuse une opération Bland sans cette autorisation (``exiger_confirmation``).
  Une routine ou un travail de fond prépare et attend. Arguments figés par leur empreinte : d'autres arguments = autre demande. IA en pause :
  aucun effet. Seule exception : le mode « Envoyer automatiquement » de la routine de rappel (``_EXCEPTION``), borné au modèle de texte
  validé, au numéro enregistré du client du rendez-vous et à l'outil SMS ou d'appel réglé.
- **Confidentialité** : aperçu exact de ce qui part (numéro au complet, consigne ou texte, destinataires, montant et devise quand l'outil
  les donne, sinon « montant non communiqué par Bland ») ; politique des données (``data_policy5689.exiger``) ; contrôle des identités du
  registre (``pseudo540``) sur les arguments avec une exception explicite et journalisée pour les champs que l'avocat confirme (numéro
  appelé, consigne ou texte) ; aucune pièce de dossier transmise.
- **Routines** : type fermé « agent IA » (référence de l'agent distant, version, état, dernière vérification) dont le passage ne fait que
  lire ; type fermé « rappel de rendez-vous » (``rappel_rdv``) créé une fois à la migration, désactivé (« à activer »).
- **Coûts** : chaque effet confirmé est inscrit au registre (``budget_ledger5689.noter_quantite``) : minutes, SMS, crédits, montant ;
  inconnu reste inconnu.

Aucune écriture pendant une lecture (R05) ; schéma créé au démarrage (``Desk._prepare``) ; absent et interdit : même 404 neutre.
"""
from contextlib import contextmanager
import contextvars
from datetime import datetime, timedelta, timezone
from html import escape as e
import hashlib
import json
import re
import secrets
import uuid

from .common import Stop

PROVIDER = 'bland'
ENDPOINT = 'https://api.bland.ai/v1/mcp'
LIEN_COMPTE = 'https://app.bland.ai/'
LIEN_CLE = 'https://app.bland.ai/settings/api-keys'
LIEN_DOC = 'https://docs.bland.ai/integrations/mcp/overview'
CAP_LECTURE = 'bland.lecture'
CAP_EFFET = 'bland.effet'
PREFIXE = 'm56109/bland/'
MAX_POST = 20_000
MAX_ARGUMENTS = 12_000
CLE = re.compile(r'^[A-Za-z0-9_.:-]{8,120}$')
REGLAGE = 'bland56109:connecteur'
REGLAGE_RAPPEL = 'bland56109:rappel'
REPERE_RAPPEL = 'bland56109:rappel_cree'
ROUTINE_RAPPEL = 'r109-rappel-rdv'
TYPES_ROUTINE = {'agent_ia': 'Agent IA (téléphonie Bland)', 'rappel_rdv': 'Rappel de rendez-vous au client'}

# Fiche du registre des connexions (``connexions5652._spec``) : mêmes champs que les autres fournisseurs.
SPEC = {'label': 'Bland — téléphonie par IA', 'family': 'telephonie',
        'auth': 'Clé API d’organisation à accès complet (en-tête Authorization: Bearer org_…) ; les clés restreintes ne se connectent pas',
        'access': LIEN_CLE, 'docs': LIEN_DOC, 'fields': [{'name': 'api_key', 'label': 'Clé API Bland', 'secret': True, 'required': True, 'help': ''}],
        'sans_bac_a_sable': True, 'territoires': (),
        'caps': {CAP_LECTURE: ('Consulter (appels, agents, numéros, statistiques, documentation)', 'lecture', None),
                 CAP_EFFET: ('Action confirmée par l’avocat (appel, SMS, achat, publication…)', 'ecriture', 'envoyer')},
        'usage': 'Agents vocaux, appels et SMS par le service Bland, hors du cabinet ; chaque action est confirmée par l’avocat.',
        'note': 'La connexion par compte (« Sign in with Bland », OAuth) n’est pas livrée dans cette version : seule la clé API est prise en charge.'}

AVERTISSEMENT = ('Bland est un service externe, situé hors du cabinet : les appels et les SMS passent par une IA. Vérifiez l’information des '
                 'personnes appelées (annonce d’un agent automatisé, enregistrement de l’appel, RGPD, secret professionnel), surtout pour des '
                 'clients ou des parties adverses. Aucune pièce de dossier n’est transmise.')
ETATS = {'non_configure': 'Non configuré', 'a_verifier': 'À vérifier', 'pret': 'Prêt', 'cle_refusee': 'Clé refusée', 'hors_ligne': 'Hors ligne'}

# ------------------------------------------------------------------------------------------------ classement des outils
# Motifs d'EFFET : un outil dont le nom correspond n'est jamais une lecture, même annoncé « lecture seule ».
MOTIFS_EFFET = re.compile(r'(?i)(create|update|set_|edit|patch|delete|remove|send|start|stop|run|trigger|publish|promote|deploy|buy|purchase|'
                          r'cancel|upload|import|add_|assign|attach|connect|configure|transfer|hang|batch|campaign|rollback|revert|invite|post_|'
                          r'put_|call_bland_api|plan)')
# Sous-classe ENGAGEANT (liste fermée, extensible ici, testée) : (code, libellé, motif).
MOTIFS_ENGAGEANTS = (
    ('appel', 'Appel sortant', r'(create|send|place|start|make)_?(phone_?)?call|outbound|dial'),
    ('campagne', 'Campagne ou lot', r'batch|campaign|bulk'),
    ('sms', 'SMS', r'sms|text_?message|send_?text'),
    ('achat', 'Achat', r'buy|purchase|plan|billing|top_?up|recharge'),
    ('appel_en_cours', 'Transfert ou raccrochage d’un appel en cours', r'transfer|hang_?up|stop_?call|end_?call|stop_?active'),
    ('publication', 'Publication d’un agent en production', r'publish|promote|deploy|production|go_?live|rollback|revert'),
    ('numero_entrant', 'Configuration d’un numéro entrant', r'(update|configure|set|assign|attach|connect|edit)_?.*(inbound|number)|inbound'),
    ('sip', 'SIP', r'sip'),
    ('portabilite', 'Portabilité d’un numéro', r'port_?in|porting|portab'),
    ('suppression', 'Suppression', r'delete|remove|destroy|purge|erase'),
    ('cles_membres', 'Clés API et membres', r'api_?key|keys?$|member|invite|role|permission'),
    ('api_generique', 'Appel générique à l’API', r'call_bland_api|bland_api_post|bland_api_patch|bland_api_delete'),
)


def classer(outil):
    """{'classe': lecture | effet, 'engageant': bool, 'motif': code, 'libelle': …}. Lecture seulement si ``readOnlyHint`` est vrai ET que le
    nom ne correspond à aucun motif d'effet ; un outil sans annotation (ou annotation illisible) est engageant ; ``destructiveHint`` aussi."""
    nom = str((outil or {}).get('name') or '')
    ann = (outil or {}).get('annotations')
    lecture_annoncee = isinstance(ann, dict) and ann.get('readOnlyHint') is True
    for code, libelle, motif in MOTIFS_ENGAGEANTS:
        if re.search(motif, nom, re.I):
            return {'classe': 'effet', 'engageant': True, 'motif': code, 'libelle': libelle}
    if not isinstance(ann, dict) or not ann or ('readOnlyHint' not in ann and 'destructiveHint' not in ann):
        return {'classe': 'effet', 'engageant': True, 'motif': 'non_annote', 'libelle': 'Outil sans annotation : traité comme engageant'}
    if lecture_annoncee and not MOTIFS_EFFET.search(nom):
        return {'classe': 'lecture', 'engageant': False, 'motif': '', 'libelle': 'Lecture'}
    if ann.get('destructiveHint') is True or (not lecture_annoncee and ann.get('destructiveHint') is not False):
        return {'classe': 'effet', 'engageant': True, 'motif': 'destructif', 'libelle': 'Effet annoncé destructif'}
    return {'classe': 'effet', 'engageant': False, 'motif': 'effet', 'libelle': 'Effet (modification chez Bland)'}


# ------------------------------------------------------------------------------------------------ schéma (démarrage)
SCHEMA = '''
CREATE TABLE IF NOT EXISTS bland56109_outils(revision INTEGER PRIMARY KEY AUTOINCREMENT, empreinte TEXT NOT NULL, outils_json TEXT NOT NULL,
  nombre INTEGER NOT NULL, releve_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bland56109_demandes(op TEXT PRIMARY KEY, outil TEXT NOT NULL, classe TEXT NOT NULL, engageant INTEGER NOT NULL,
  nature TEXT NOT NULL DEFAULT '', apercu_json TEXT NOT NULL DEFAULT '{}', exceptions_json TEXT NOT NULL DEFAULT '[]', declencheur TEXT NOT NULL,
  origine_json TEXT NOT NULL DEFAULT '{}', revision_outils INTEGER NOT NULL DEFAULT 0, suite TEXT NOT NULL DEFAULT '', cree_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bland56109_agents(agent_ref TEXT PRIMARY KEY, routine TEXT NOT NULL, nom TEXT NOT NULL, version TEXT NOT NULL DEFAULT '',
  etat TEXT NOT NULL, derniere_verif TEXT NOT NULL DEFAULT '', op TEXT NOT NULL DEFAULT '', choix_json TEXT NOT NULL DEFAULT '{}', cree_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bland56109_propositions(id TEXT PRIMARY KEY, routine TEXT NOT NULL, appel TEXT NOT NULL, resume TEXT NOT NULL,
  etat TEXT NOT NULL DEFAULT 'a_valider', cree_le TEXT NOT NULL, traite_le TEXT NOT NULL DEFAULT '', traite_par TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS bland56109_rappels(cle TEXT PRIMARY KEY, rdv TEXT NOT NULL, debut TEXT NOT NULL, canal TEXT NOT NULL,
  contact TEXT NOT NULL DEFAULT '', numero TEXT NOT NULL DEFAULT '', texte_sha TEXT NOT NULL DEFAULT '', jour TEXT NOT NULL, etat TEXT NOT NULL,
  op TEXT NOT NULL DEFAULT '', motif TEXT NOT NULL DEFAULT '', occurrence TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT '',
  envoye_le TEXT NOT NULL DEFAULT '', maj_le TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS bland56109_rappels_jour ON bland56109_rappels(jour, etat);
CREATE TABLE IF NOT EXISTS bland56109_accords(contact TEXT PRIMARY KEY, sms INTEGER NOT NULL DEFAULT 0, appel INTEGER NOT NULL DEFAULT 0,
  par TEXT NOT NULL DEFAULT '', le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bland56109_ops(acteur TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL, resultat TEXT NOT NULL,
  cree_le TEXT NOT NULL, PRIMARY KEY(acteur, cle));
'''


def _maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _sha(*parts):
    return hashlib.sha256('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()


def ensure_schema(desk):
    """``Desk._prepare`` : tables du lot, puis la routine de rappel livrée par défaut (une seule fois, désactivée)."""
    noms = re.findall(r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)', SCHEMA)
    existants = {r[0] for r in desk.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if any(n not in existants for n in noms):
        if desk.db.in_transaction:
            desk.db.commit()
        desk.db.executescript(SCHEMA)
        desk.db.commit()
    creer_rappel_par_defaut(desk)


def _table(desk, nom):
    return bool(desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nom,)).fetchone())


# ------------------------------------------------------------------------------------------------ connexion (registre connexions5652)
def _row(desk):
    """La connexion Bland du cabinet, sans créer de table (lecture possible pendant un GET)."""
    if not _table(desk, 'connexions5652'):
        return None
    return desk.db.execute("SELECT * FROM connexions5652 WHERE provider=? AND owner='cabinet' ORDER BY updated DESC LIMIT 1", (PROVIDER,)).fetchone()


def _local_strict(desk):
    from .selection_sources5697 import local_strict
    return local_strict(desk)


def _pause_manuelle(desk):
    try:
        from .arret5643 import manual_paused
        return manual_paused(desk)
    except Exception:   # noqa: BLE001 — contrôle illisible : on s'arrête (jamais d'effet parce que le contrôle a échoué)
        return True


def etat_connexion(desk, row=None):
    """Code d'état de la carte (aucune écriture)."""
    row = row if row is not None else _row(desk)
    if row is None:
        return 'non_configure'
    secrets_set = json.loads(row['secrets_json'] or '{}')
    if 'api_key' not in secrets_set:
        return 'non_configure'
    erreur = str(row['last_error'] or '')
    if erreur.startswith(('http_401', 'http_403', 'erreur_mcp_distante:-32001')):
        return 'cle_refusee'
    if erreur.startswith(('connexion_http', 'delai_http', 'http_5', 'http_429', 'reponse_mcp')):
        return 'hors_ligne'
    if erreur or not row['last_test']:
        return 'a_verifier'
    return 'pret'


def reglage(desk):
    return dict(desk.settings(REGLAGE, {}) or {})


def actif(desk):
    return bool(reglage(desk).get('actif'))


def utilisable(desk, row=None):
    """Connecteur activé par l'avocat, connexion prête, mode local strict absent."""
    return actif(desk) and etat_connexion(desk, row) == 'pret' and not _local_strict(desk)


def _exiger_utilisable(desk):
    if _local_strict(desk):
        raise Stop('mode_local_strict')
    if not actif(desk):
        raise Stop('connecteur_bland_desactive')
    if etat_connexion(desk) != 'pret':
        raise Stop('connecteur_bland_non_pret')


def enregistrer_cle(desk, acteur, role, cle):
    """Clé au coffre par le service commun des connexions ; une nouvelle clé crée une nouvelle génération (à tester de nouveau)."""
    from . import connexions5652 as cx
    cle = str(cle or '').strip()
    if not 8 <= len(cle) <= 4096 or re.search(r'[\s\x00]', cle):
        raise Stop('cle_bland_invalide')
    data = {'provider': PROVIDER, 'portee': 'cabinet', 'api_key': cle, 'cap_' + CAP_LECTURE.replace('.', '_'): 'on',
            'cap_' + CAP_EFFET.replace('.', '_'): 'on'}
    row = _row(desk)
    if row is not None:
        data.update(id=row['id'], version=row['version'])
    cx.save(desk, acteur, role, data)
    desk.audit('bland56109_cle_enregistree', {'par': acteur})        # jamais la clé
    return vue(desk)


def tester(desk, acteur, role):
    """« Tester la connexion » : ``initialize`` puis ``tools/list`` par le test commun des connexions (aucun outil exécuté)."""
    from . import connexions5652 as cx
    if _local_strict(desk):
        raise Stop('mode_local_strict')
    row = _row(desk)
    if row is None or etat_connexion(desk, row) == 'non_configure':
        raise Stop('cle_bland_absente')
    out = cx.test(desk, acteur, role, row['id'])
    v = vue(desk)
    v['ok'] = bool(out.get('ok'))
    v['erreur'] = out.get('erreur', '')
    return v


def activer(desk, acteur, role, on):
    """Activation explicite par l'avocat : connexion prête et mode local strict absent ; la désactivation est toujours possible. La
    compétence native suit l'activation tant que l'avocat ne l'a pas réglée lui-même."""
    on = bool(on)
    if on:
        if _local_strict(desk):
            raise Stop('mode_local_strict')
        if etat_connexion(desk) != 'pret':
            raise Stop('connecteur_bland_non_pret')
    actuel = reglage(desk)
    desk.setting(REGLAGE, {'actif': on, 'par': acteur, 'le': _maintenant(), 'revision': int(actuel.get('revision') or 0) + 1})
    if _table(desk, 'skill_state5632'):
        desk.db.execute('UPDATE skill_state5632 SET enabled=?, updated=? WHERE id=? AND user_choice=0', (1 if on else 0, _maintenant(), COMPETENCE))
        desk.db.commit()
    desk.audit('bland56109_connecteur_' + ('active' if on else 'desactive'), {'par': acteur})
    return vue(desk)


def retirer(desk, acteur, role):
    """Retirer la clé : secret effacé du coffre, connecteur désactivé, demandes non confirmées annulées par le service commun."""
    from . import connexions5652 as cx
    row = _row(desk)
    if row is not None:
        cx.clear_secret(desk, acteur, role, row['id'], 'api_key')
        desk.db.execute("UPDATE connexion_ops5652 SET state='annulee',error='cle_retiree',updated=? WHERE connection=? AND state IN "
                        "('a_valider','echec_avant_envoi','a_revalider','a_revalider_compte')", (_maintenant(), row['id']))
        desk.db.commit()
    if actif(desk):
        activer(desk, acteur, role, False)
    desk.audit('bland56109_cle_retiree', {'par': acteur})
    return vue(desk)


def vue(desk):
    """Vue publique, jamais la clé : état, activation, nombre d'outils et révision, date du dernier test."""
    row = _row(desk)
    cache = dernier_releve(desk)
    etat = etat_connexion(desk, row)
    return {'etat': etat, 'etat_libelle': ETATS[etat], 'actif': actif(desk), 'local_strict': _local_strict(desk),
            'cle_enregistree': bool(row is not None and 'api_key' in json.loads(row['secrets_json'] or '{}')),
            'cle_enregistree_le': (json.loads(row['secrets_json'] or '{}').get('api_key', '')[:10] if row is not None else ''),
            'dernier_test': row['last_test'] if row is not None else '', 'derniere_erreur': row['last_error'] if row is not None else '',
            'outils': cache['nombre'] if cache else 0, 'revision_outils': cache['revision'] if cache else 0,
            'oauth': 'non livré dans cette version'}


# ------------------------------------------------------------------------------------------------ cache des outils
def _empreinte_outils(outils):
    canon = sorted(([str(t.get('name', '')), t.get('annotations') or {}, t.get('inputSchema') or {}] for t in outils if isinstance(t, dict)),
                   key=lambda x: x[0])
    return hashlib.sha256(json.dumps(canon, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def noter_outils(desk, outils):
    """Nouvelle révision seulement si la liste a changé ; rend le numéro de révision."""
    propres = [{'name': str(t.get('name'))[:120], 'description': str(t.get('description') or '')[:600],
                'annotations': t.get('annotations') if isinstance(t.get('annotations'), dict) else None,
                'inputSchema': t.get('inputSchema') if isinstance(t.get('inputSchema'), dict) else {}}
               for t in outils if isinstance(t, dict) and t.get('name')]
    empreinte = _empreinte_outils(propres)
    dernier = desk.db.execute('SELECT revision, empreinte FROM bland56109_outils ORDER BY revision DESC LIMIT 1').fetchone()
    if dernier and dernier['empreinte'] == empreinte:
        return int(dernier['revision'])
    cur = desk.db.execute('INSERT INTO bland56109_outils(empreinte, outils_json, nombre, releve_le) VALUES(?,?,?,?)',
                          (empreinte, json.dumps(propres, ensure_ascii=False), len(propres), _maintenant()))
    desk.db.commit()
    return int(cur.lastrowid)


def dernier_releve(desk):
    if not _table(desk, 'bland56109_outils'):
        return None
    row = desk.db.execute('SELECT * FROM bland56109_outils ORDER BY revision DESC LIMIT 1').fetchone()
    if not row:
        return None
    outils = json.loads(row['outils_json'] or '[]')
    return {'revision': int(row['revision']), 'nombre': int(row['nombre']), 'releve_le': row['releve_le'],
            'outils': [{**t, **classer(t)} for t in outils]}


def outil(desk, nom):
    cache = dernier_releve(desk)
    for t in (cache or {}).get('outils', ()):
        if t['name'] == nom:
            return t, cache['revision']
    raise Stop('outil_bland_absent_de_la_liste')


def _chercher(desk, motif, classe):
    cache = dernier_releve(desk) or {}
    for t in cache.get('outils', ()):
        if t['classe'] == classe and re.search(motif, t['name'], re.I):
            return t
    return None


# ------------------------------------------------------------------------------------------------ autorisations d'effet
_HUMAIN = contextvars.ContextVar('bland56109_humain', default=None)          # posé seulement par la route POST de l'interface
_AUTORISEES = contextvars.ContextVar('bland56109_autorisees', default=frozenset())
_EXCEPTION = contextvars.ContextVar('bland56109_exception_rappel', default=None)


@contextmanager
def requete_humaine(acteur):
    """Route POST authentifiée (session, CSRF) : seule source d'une confirmation de l'avocat."""
    jeton = _HUMAIN.set(str(acteur))
    try:
        yield
    finally:
        _HUMAIN.reset(jeton)


def _exiger_humain():
    if not _HUMAIN.get():
        raise Stop('confirmation_humaine_requise')


def exiger_humain():
    """5.6.109 (intégration I1) : contrôle PUBLIC, employé aussi par ``formalites56109.autoriser`` : sans la marque posée par la route POST
    authentifiée de l'interface (``requete_humaine``), ``Stop('confirmation_humaine_requise')`` ; un modèle, une routine ou un automatisme ne
    la posent jamais. L'ancien nom privé ``_exiger_humain`` reste la mise en œuvre, relue à chaque appel (les essais de mutation qui le
    remplacent atteignent aussi les appelants du nom public)."""
    return _exiger_humain()


@contextmanager
def _autoriser(op_id):
    jeton = _AUTORISEES.set(_AUTORISEES.get() | {op_id})
    try:
        yield
    finally:
        _AUTORISEES.reset(jeton)


def exiger_confirmation(op):
    """Appelé par ``connexions5652.validate`` avant toute réservation d'une opération Bland : confirmation de l'avocat en cours pour CETTE
    opération, ou exception bornée de la routine de rappel. Sinon refus : rien ne part et l'opération reste à valider."""
    if op['id'] in _AUTORISEES.get():
        return
    exc = _EXCEPTION.get()
    if exc and exc.get('op') == op['id'] and _conforme_exception(op, exc):
        return
    raise Stop('confirmation_bland_requise')


def _conforme_exception(op, exc):
    """Exception « Envoyer automatiquement » : déclencheur de la routine de rappel, outil SMS ou d'appel réglé pour elle, un seul numéro (le
    numéro enregistré du client), texte égal au modèle validé rendu pour ce rendez-vous. Aucun autre outil Bland."""
    cfg = config_rappel_brute(None) if exc.get('cfg') is None else exc['cfg']
    try:
        payload = json.loads(op['payload_json'] or '{}')
    except ValueError:
        return False
    nom = payload.get('outil')
    if str(op['trigger'] or '') != 'rappel_automatique':
        return False
    if nom not in (cfg.get('outil_sms'), cfg.get('outil_appel')) or nom != exc.get('outil'):
        return False
    attendu = exc.get('arguments')
    return isinstance(attendu, dict) and payload.get('arguments') == attendu and _numeros(attendu) == [exc.get('numero')]


def _exiger_execution(desk, params):
    """Défense en profondeur au moment de l'appel distant : autorisation, pause, mode local strict, connecteur activé."""
    ref = str(params.get('reference') or '')
    exc = _EXCEPTION.get()
    if ref not in _AUTORISEES.get() and not (exc and exc.get('op') == ref):
        raise Stop('confirmation_bland_requise')
    if _pause_manuelle(desk):
        raise Stop('automatismes_en_pause')
    from .pause5629 import checkpoint
    checkpoint(desk)
    _exiger_utilisable(desk)


# ------------------------------------------------------------------------------------------------ transport MCP (Streamable HTTP, sans session)
class Adaptateur:
    """Adaptateur du registre ``connexions5652`` (``adapter``) : clé lue au dernier moment, jamais copiée ni journalisée."""
    provider = PROVIDER
    version = '1'

    def __init__(self, ctx, desk):
        self.ctx, self.desk = ctx, desk
        self._id = 0
        self._session = ''

    def _rpc(self, methode, params=None, notification=False):
        from . import mcp5614
        corps = {'jsonrpc': '2.0', 'method': methode}
        if not notification:
            self._id += 1
            corps['id'] = self._id
        if params is not None:
            corps['params'] = params
        entetes = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': mcp5614.PROTOCOL,
                   'Authorization': 'Bearer ' + self.ctx.secret('api_key')}
        if self._session:
            entetes['Mcp-Session-Id'] = self._session
        client = self.ctx.http(ENDPOINT)
        brut = client.request('POST', ENDPOINT, json.dumps(corps).encode('utf-8'), entetes, 4_000_000)
        for k, v in (getattr(client, 'last_headers', None) or {}).items():
            if str(k).lower() == 'mcp-session-id' and not self._session:
                self._session = str(v)[:200]
        if notification:
            return None
        rep = mcp5614.correlate(brut, corps['id'])
        if rep.get('error'):
            err = rep['error'] if isinstance(rep['error'], dict) else {}
            raise Stop('erreur_mcp_distante:' + str(err.get('code', ''))[:12])
        return rep.get('result') or {}

    def _ouvrir(self):
        from . import mcp5614
        init = self._rpc('initialize', {'protocolVersion': mcp5614.PROTOCOL, 'capabilities': {}, 'clientInfo': {'name': 'AxiorHub', 'version': '5.6.109'}})
        if not isinstance(init, dict) or not init.get('protocolVersion'):
            raise Stop('reponse_mcp_invalide')
        try:
            self._rpc('notifications/initialized', notification=True)
        except Stop:
            pass
        return init

    def _lister(self, max_pages=20):
        outils, curseur, vus = [], None, set()
        for _ in range(max_pages):
            res = self._rpc('tools/list', {'cursor': curseur} if curseur else {})
            lignes = res.get('tools') if isinstance(res, dict) else None
            if not isinstance(lignes, list):
                raise Stop('liste_outils_mcp_invalide')
            outils += [t for t in lignes if isinstance(t, dict) and t.get('name')]
            curseur = res.get('nextCursor')
            if not curseur:
                return outils
            if curseur in vus:
                raise Stop('pagination_outils_mcp_incomplete')
            vus.add(curseur)
        raise Stop('pagination_outils_mcp_incomplete')

    def identity(self):
        """Test de la connexion : ``initialize`` puis ``tools/list`` ; aucun outil exécuté. Refusé en mode local strict."""
        if _local_strict(self.desk):
            raise Stop('mode_local_strict')
        init = self._ouvrir()
        outils = self._lister()
        revision = noter_outils(self.desk, outils)
        serveur = init.get('serverInfo') if isinstance(init.get('serverInfo'), dict) else {}
        return {'service': 'Bland', 'serveur': str(serveur.get('name') or '')[:80], 'outils': len(outils), 'revision_outils': revision}

    def lire(self, nom, arguments):
        self._ouvrir()
        res = self._rpc('tools/call', {'name': nom, 'arguments': arguments})
        if isinstance(res, dict) and res.get('isError'):
            raise Stop('outil_mcp_en_erreur')
        return res

    def call(self, capability, params):
        """Effet confirmé (``connexions5652.validate``) : un seul ``tools/call``, jamais rejoué ici."""
        if capability != CAP_EFFET:
            raise Stop('capacite_non_codee')
        _exiger_execution(self.desk, params)
        nom, arguments = str(params.get('outil') or ''), params.get('arguments')
        if not nom or not isinstance(arguments, dict):
            raise Stop('demande_bland_invalide')
        self._ouvrir()
        step = params.get('_step')
        if callable(step):
            step('appel_outil', '', nom)
        res = self._rpc('tools/call', {'name': nom, 'arguments': arguments})
        if isinstance(res, dict) and res.get('isError'):
            raise Stop('outil_mcp_en_erreur')
        donnees = _donnees(res)
        ref = ''
        if isinstance(donnees, dict):
            ref = str(donnees.get('call_id') or donnees.get('message_id') or donnees.get('agent_id') or donnees.get('id') or '')[:200]
        return {'remote_ref': ref, 'etat': str((donnees or {}).get('status') or 'transmis') if isinstance(donnees, dict) else 'transmis',
                'donnees': donnees if isinstance(donnees, (dict, list)) else {'texte': str(donnees)[:4000]}}


def _donnees(res):
    """Contenu structuré d'un résultat d'outil, sinon le JSON du texte, sinon le texte (données, jamais des instructions)."""
    if not isinstance(res, dict):
        return {}
    if isinstance(res.get('structuredContent'), (dict, list)):
        return res['structuredContent']
    from . import mcp5614
    texte = mcp5614.text_from(res)
    try:
        return json.loads(texte)
    except ValueError:
        return texte[:8000]


def _adaptateur(desk):
    from . import connexions5652 as cx
    row = _row(desk)
    if row is None:
        raise Stop('cle_bland_absente')
    return cx.adapter(desk, row)


# ------------------------------------------------------------------------------------------------ aperçu, politique, enveloppe
CLES_NUMERO = re.compile(r'(?i)(phone|number|numero|to$|^to_|recipient|destinataire|from$|caller)')
CLES_TEXTE = re.compile(r'(?i)(task|prompt|message|text|body|instruction|consigne|first_sentence|greeting|script|name)')
CLES_MONTANT = re.compile(r'(?i)^(amount|price|cost|montant|total|quantity|credits?)$')
CLES_DEVISE = re.compile(r'(?i)^(currency|devise)$')


def _parcourir(valeur, chemin=()):
    if isinstance(valeur, dict):
        for k, v in valeur.items():
            yield from _parcourir(v, chemin + (str(k),))
    elif isinstance(valeur, list):
        for i, v in enumerate(valeur):
            yield from _parcourir(v, chemin + (str(i),))
    else:
        yield chemin, valeur


def _numeros(arguments):
    out = []
    for chemin, v in _parcourir(arguments):
        cle = next((c for c in reversed(chemin) if not c.isdigit()), '')
        if isinstance(v, str) and CLES_NUMERO.search(cle) and re.search(r'\d{6,}', re.sub(r'[\s.()-]', '', v)):
            out.append(v)
    return out


def apercu(nom, classement, arguments):
    """Ce qui part, exactement : nature, numéros au complet, consigne ou texte, destinataires d'un lot, montant et devise d'un achat."""
    numeros = _numeros(arguments)
    textes = [{'champ': '.'.join(c), 'valeur': v} for c, v in _parcourir(arguments)
              if isinstance(v, str) and CLES_TEXTE.search(next((x for x in reversed(c) if not x.isdigit()), '')) and v not in numeros]
    listes = [len(v) for v in (arguments or {}).values() if isinstance(v, list)]
    montant = devise = None
    for c, v in _parcourir(arguments):
        cle = c[-1] if c else ''
        if CLES_MONTANT.match(cle) and isinstance(v, (int, float)) and not isinstance(v, bool):
            montant = v
        elif CLES_DEVISE.match(cle) and isinstance(v, str):
            devise = v.upper()[:8]
    out = {'outil': nom, 'nature': classement['libelle'], 'motif': classement['motif'], 'engageant': classement['engageant'],
           'numeros': numeros, 'textes': textes, 'destinataires': max(listes) if listes else (len(numeros) or 0), 'arguments': arguments}
    if classement['motif'] == 'achat':
        out['montant'] = ('%s %s' % (montant, devise or '(devise non communiquée)')) if montant is not None else 'montant non communiqué par Bland'
    return out


def _politique(desk, acteur, role, nom, arguments):
    from .data_policy5689 import exiger
    exiger(desk.c, 'outil_mcp', PROVIDER, finalite='telephonie:' + nom, acteur=acteur, role=role,
           requete=json.dumps(arguments, ensure_ascii=False, default=str)[:2000])


def controle_enveloppe(desk, arguments, apercu_):
    """Identités du registre du cabinet dans les arguments : refus, sauf dans les champs que l'avocat confirme lui-même (numéro appelé,
    consigne ou texte, affichés en entier dans la demande) — exception explicite, rendue pour être affichée et journalisée."""
    try:
        from .pseudo540 import Pseudonymizer
        pseudo = Pseudonymizer(desk.c.get('pseudo') or {'state_dir': desk.c.get('state_dir', '')})
    except Exception:   # noqa: BLE001 — registre illisible : rien ne part (jamais ouvert parce que le contrôle a échoué)
        raise Stop('registre_identites_illisible') from None
    confirmes = set(apercu_['numeros']) | {t['valeur'] for t in apercu_['textes']}
    exceptions = []
    for chemin, v in _parcourir(arguments):
        if not isinstance(v, str) or len(v) < 3:
            continue
        try:
            trouve = pseudo.residuelle(v, technique=False)
        except Exception:   # noqa: BLE001
            raise Stop('registre_identites_illisible') from None
        if not trouve:
            continue
        if v in confirmes:
            exceptions.append('.'.join(chemin))
        else:
            raise Stop('pseudo_identite_residuelle')
    return exceptions


# ------------------------------------------------------------------------------------------------ demandes (opérations préparées)
def preparer(desk, acteur, role, nom, arguments, cle, declencheur='assistant', origine=None, nature=''):
    """Prépare une demande (aucun effet) : outil connu de la dernière liste, jamais une lecture ; aperçu exact ; politique des données et
    contrôle des identités (refus = arrêt expliqué, aucune demande créée) ; opération du registre commun « À valider », idempotente."""
    from . import connexions5652 as cx
    _exiger_utilisable(desk)
    cle = str(cle or '')
    if not CLE.match(cle):
        raise Stop('cle_requise')
    if not isinstance(arguments, dict) or len(json.dumps(arguments, ensure_ascii=False, default=str)) > MAX_ARGUMENTS:
        raise Stop('arguments_bland_invalides')
    t, revision = outil(desk, str(nom or ''))
    classement = classer(t)
    if classement['classe'] == 'lecture':
        raise Stop('outil_de_lecture_sans_demande')
    requis = (t.get('inputSchema') or {}).get('required') or []
    if any(r not in arguments for r in requis if isinstance(r, str)):
        raise Stop('arguments_bland_incomplets')
    ap = apercu(t['name'], classement, arguments)
    _politique(desk, acteur, role, t['name'], arguments)
    exceptions = controle_enveloppe(desk, arguments, ap)
    row = _row(desk)
    cx.ensure_schema(desk)
    payload = {'outil': t['name'], 'arguments': arguments}
    phash = _sha(json.dumps(payload, sort_keys=True, ensure_ascii=False))
    idem = _sha(row['id'], row['generation'], CAP_EFFET, cle)[:40]
    ancien = desk.db.execute('SELECT * FROM connexion_ops5652 WHERE idem_key=?', (idem,)).fetchone()
    if ancien:
        if ancien['payload_hash'] != phash:
            raise Stop('cle_reutilisee')             # même clé, autres arguments : jamais la même demande
        return fiche(desk, acteur, 'administrateur', ancien['id'])
    oid = 'op-' + _sha(idem, uuid.uuid4().hex)[:20]
    maintenant = _maintenant()
    desk.db.execute('INSERT INTO connexion_ops5652(id,connection,provider,capability,matter,owner,state,idem_key,payload_json,payload_hash,document_path,'
                    'document_sha,connection_generation,identity_hash,environment,trigger,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (oid, row['id'], PROVIDER, CAP_EFFET, '', str(acteur), 'a_valider', idem, json.dumps(payload, ensure_ascii=False), phash, '', '',
                     row['generation'], cx.identity_hash(row), row['environment'], str(declencheur)[:80], maintenant, maintenant))
    desk.db.execute('INSERT INTO bland56109_demandes(op,outil,classe,engageant,nature,apercu_json,exceptions_json,declencheur,origine_json,revision_outils,cree_le) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?)', (oid, t['name'], classement['classe'], int(classement['engageant']), nature or classement['motif'],
                                                       json.dumps(ap, ensure_ascii=False), json.dumps(exceptions), str(declencheur)[:80],
                                                       json.dumps(origine or {}, ensure_ascii=False), revision, maintenant))
    desk.db.commit()
    desk.audit('bland56109_demande_preparee', {'operation': oid, 'outil': t['name'], 'classe': classement['classe'], 'engageant': classement['engageant'],
                                              'declencheur': str(declencheur)[:80]})
    if exceptions:
        desk.audit('bland56109_exception_enveloppe', {'operation': oid, 'champs': exceptions})
    return fiche(desk, acteur, 'administrateur', oid)


def _op(desk, acteur, role, op_id):
    """Opération Bland visible par ce compte ; absente, d'un autre fournisseur ou interdite : le même 404 neutre."""
    from . import connexions5652 as cx
    if not _table(desk, 'connexion_ops5652') or not _table(desk, 'bland56109_demandes'):
        raise Stop('introuvable')
    try:
        op, _row_ = cx._op_access(desk, acteur, _role_effectif(desk, role, op_id), op_id)
    except Stop:
        raise Stop('introuvable') from None
    dem = desk.db.execute('SELECT * FROM bland56109_demandes WHERE op=?', (op['id'],)).fetchone()
    if op['provider'] != PROVIDER or dem is None:
        raise Stop('introuvable')
    return op, dem


def _role_effectif(desk, role, op_id):
    """Une demande préparée par une routine du cabinet (propriétaire « cabinet ») est visible des avocats du cabinet comme de
    l'administrateur ; une demande personnelle ne l'est que de son auteur (et de l'administrateur)."""
    row = desk.db.execute('SELECT owner FROM connexion_ops5652 WHERE id=? AND provider=?', (str(op_id or ''), PROVIDER)).fetchone()
    return 'administrateur' if (row and row['owner'] == 'cabinet' and role in ('administrateur', 'avocat')) else role


ETATS_DEMANDE = {'a_valider': 'À confirmer', 'envoi_en_cours': 'Envoi en cours', 'envoyee': 'Transmise', 'terminee': 'Faite (constatée chez Bland)',
                 'resultat_a_verifier': 'À vérifier (réponse perdue : jamais rejouée)', 'refusee': 'Refusée par Bland', 'echec': 'Échec',
                 'echec_avant_envoi': 'Échec avant envoi (rien n’est parti)', 'annulee': 'Annulée', 'a_revalider_compte': 'Clé changée : à revoir'}


def fiche(desk, acteur, role, op_id):
    from . import connexions5652 as cx
    op, dem = _op(desk, acteur, role, op_id)
    empreinte = cx.sheet(desk, acteur, _role_effectif(desk, role, op['id']), op['id'])['empreinte']
    return {'operation': op['id'], 'outil': dem['outil'], 'classe': dem['classe'], 'engageant': bool(dem['engageant']), 'nature': dem['nature'],
            'apercu': json.loads(dem['apercu_json'] or '{}'), 'exceptions': json.loads(dem['exceptions_json'] or '[]'),
            'declencheur': dem['declencheur'], 'etat': op['state'], 'etat_libelle': ETATS_DEMANDE.get(op['state'], op['state']),
            'version': op['version'], 'empreinte': empreinte, 'erreur': op['error'], 'avertissement': AVERTISSEMENT,
            'preparee_le': op['created'], 'preparee_par': op['owner'], 'suite': dem['suite']}


def confirmer(desk, acteur, role, op_id, version, empreinte, relu=False):
    """Confirmation explicite de l'avocat (route POST de l'interface seulement) puis UN appel par le service commun ; réponse perdue =
    « À vérifier », jamais rejouée. Une routine, un travail de fond ou un modèle n'atteint jamais cette fonction avec ``_HUMAIN`` posé."""
    from . import connexions5652 as cx
    _exiger_humain()
    _refuser_jeton_service()
    if role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    from .membres5650 import effective_rights
    if role != 'administrateur' and not effective_rights(desk.c['state_dir'], acteur, role).get('envoyer'):
        raise Stop('role_insuffisant')
    if not relu:
        raise Stop('relecture_requise')
    op, dem = _op(desk, acteur, role, op_id)
    if op['state'] not in cx.START_STATES:
        return fiche(desk, acteur, role, op['id'])      # second clic : même demande, aucun nouvel effet
    if _pause_manuelle(desk):
        raise Stop('automatismes_en_pause')
    _exiger_utilisable(desk)
    payload = json.loads(op['payload_json'])
    _politique(desk, acteur, role, payload['outil'], payload['arguments'])
    controle_enveloppe(desk, payload['arguments'], json.loads(dem['apercu_json'] or '{}'))
    try:
        with _autoriser(op['id']):
            cx.validate(desk, acteur, _role_effectif(desk, role, op['id']), op['id'], version, str(empreinte or ''))
    except Stop:
        apres_effet(desk, op['id'])
        raise
    apres_effet(desk, op['id'])
    desk.audit('bland56109_demande_confirmee', {'operation': op['id'], 'outil': payload['outil'], 'par': acteur})
    return fiche(desk, acteur, role, op['id'])


def annuler(desk, acteur, role, op_id):
    from . import connexions5652 as cx
    op, _dem = _op(desk, acteur, role, op_id)
    cx.cancel(desk, acteur, _role_effectif(desk, role, op['id']), op['id'])
    _rappel_suivre(desk, op['id'], 'annule', 'demande_annulee')
    return fiche(desk, acteur, role, op['id'])


UNITES = {'appel': 'minute_bland', 'campagne': 'minute_bland', 'sms': 'sms_bland', 'achat': 'credit_bland'}


def _noter_cout(desk, op, dem, donnees, connu=True):
    """Registre de consommation existant : minutes, SMS, crédits ; montant et devise quand Bland les donne. Inconnu reste inconnu."""
    from .budget_ledger5689 import noter_quantite
    nature = dem['nature'] if dem['nature'] in UNITES else (json.loads(dem['apercu_json'] or '{}').get('motif') or '')
    unite = UNITES.get(nature)
    if not unite:
        return
    quantite = None
    if connu and isinstance(donnees, dict):
        for cle in ('minutes', 'duration_minutes', 'segments', 'credits', 'quantity'):
            if isinstance(donnees.get(cle), (int, float)) and not isinstance(donnees.get(cle), bool):
                quantite = donnees[cle]
                break
        if quantite is None and nature == 'sms':
            quantite = 1
    try:
        noter_quantite(desk.c['state_dir'], op['id'] + ':' + unite, 'Bland', unite, quantite, etat='declaree' if quantite is not None else 'inconnue',
                       operation='bland:' + dem['outil'], source='bland56109', config=desk.c)
        montant = donnees.get('amount') if connu and isinstance(donnees, dict) else None
        devise = str(donnees.get('currency') or '').lower() if connu and isinstance(donnees, dict) else ''
        if nature == 'achat' and re.fullmatch(r'[a-z]{3}', devise or ''):
            noter_quantite(desk.c['state_dir'], op['id'] + ':montant', 'Bland', 'montant_%s_bland' % devise,
                           montant if isinstance(montant, (int, float)) and not isinstance(montant, bool) else None,
                           etat='declaree' if isinstance(montant, (int, float)) else 'inconnue', operation='bland:' + dem['outil'], source='bland56109', config=desk.c)
    except Stop as ex:   # le registre refuse : l'effet a eu lieu ; le refus est journalisé, jamais un faux zéro
        desk.audit('bland56109_cout_non_inscrit', {'operation': op['id'], 'motif': str(ex).split(':')[0]})


def apres_effet(desk, op_id):
    """Après ``validate`` : état final, coût, suites (agent enregistré comme routine, rappel noté). Aucun nouvel appel distant."""
    op = desk.db.execute('SELECT * FROM connexion_ops5652 WHERE id=?', (op_id,)).fetchone()
    dem = desk.db.execute('SELECT * FROM bland56109_demandes WHERE op=?', (op_id,)).fetchone()
    if op is None or dem is None:
        return
    resultat = json.loads(op['result_json'] or '{}')
    donnees = resultat.get('donnees') if isinstance(resultat, dict) else {}
    if op['state'] == 'envoyee':
        desk.db.execute("UPDATE connexion_ops5652 SET state='terminee',updated=? WHERE id=? AND state='envoyee'", (_maintenant(), op_id))
        desk.db.commit()
        _noter_cout(desk, op, dem, donnees, True)
        if dem['nature'] == 'creation_agent':
            _enregistrer_agent(desk, op, dem, donnees)
        _rappel_suivre(desk, op_id, 'envoye', '')
    elif op['state'] == 'resultat_a_verifier':
        _noter_cout(desk, op, dem, {}, False)
        _rappel_suivre(desk, op_id, 'a_verifier', op['error'])
    elif op['state'] in ('refusee', 'echec', 'echec_avant_envoi'):
        _rappel_suivre(desk, op_id, 'echec', op['error'])


def rapprocher(desk, acteur, role, op_id):
    """Résultat incertain : lecture de la liste des appels chez Bland pour que l'avocat constate ; aucun nouvel envoi."""
    op, dem = _op(desk, acteur, role, op_id)
    if op['state'] != 'resultat_a_verifier':
        raise Stop('demande_non_a_verifier')
    t = _chercher(desk, r'list_?(recent_)?calls?|call_?logs?$', 'lecture')
    if t is None:
        return {'operation': op['id'], 'lignes': [], 'message': 'Aucun outil de lecture des appels dans la liste de Bland : vérifiez dans le tableau de bord Bland.'}
    out = consulter(desk, acteur, role, t['name'], {})
    return {'operation': op['id'], 'outil': t['name'], 'lignes': out['resultat'], 'limites': out['limites']}


def constater(desk, acteur, role, op_id, constat):
    """L'avocat constate ce qui s'est passé chez Bland : « parti » (fait) ou « non parti » (annulée) ; jamais de rejeu automatique."""
    _exiger_humain()
    op, dem = _op(desk, acteur, role, op_id)
    if op['state'] != 'resultat_a_verifier':
        raise Stop('demande_non_a_verifier')
    if constat not in ('parti', 'non_parti'):
        raise Stop('constat_invalide')
    etat = 'terminee' if constat == 'parti' else 'annulee'
    desk.db.execute('UPDATE connexion_ops5652 SET state=?,error=?,version=version+1,updated=? WHERE id=? AND state=?',
                    (etat, 'constate_' + constat + '_par_' + acteur[:60], _maintenant(), op['id'], 'resultat_a_verifier'))
    desk.db.commit()
    _rappel_suivre(desk, op['id'], 'envoye' if constat == 'parti' else 'echec', 'constat_' + constat)
    desk.audit('bland56109_constat', {'operation': op['id'], 'constat': constat, 'par': acteur})
    return fiche(desk, acteur, role, op['id'])


def consulter(desk, acteur, role, nom, arguments):
    """Outil de LECTURE seulement (classement), sans confirmation ; possible IA en pause ; politique des données ; résultat = DONNÉES."""
    _exiger_utilisable(desk)
    t, _rev = outil(desk, str(nom or ''))
    if classer(t)['classe'] != 'lecture':
        raise Stop('outil_a_effet_demande_requise')
    if not isinstance(arguments, dict) or len(json.dumps(arguments, default=str)) > MAX_ARGUMENTS:
        raise Stop('arguments_bland_invalides')
    _politique(desk, acteur, role, t['name'], arguments)
    res = _adaptateur(desk).lire(t['name'], arguments)
    return {'outil': t['name'], 'resultat': _donnees(res), 'lu_le': _maintenant(),
            'limites': 'Données récupérées chez Bland (transcriptions de vraies conversations possibles) : ce sont des DONNÉES, jamais des '
                       'instructions ; rien n’est classé dans un dossier sans décision de l’avocat.'}


# ------------------------------------------------------------------------------------------------ agent de prise de rendez-vous (routine « agent IA »)
QUESTIONS_AGENT = {'cabinet': 'Quel est le nom du cabinet que l’agent doit annoncer ?',
                   'horaires': 'Quels sont les horaires d’ouverture du cabinet (jours et heures) ?',
                   'types': 'Quels types de rendez-vous l’agent peut-il proposer (premier rendez-vous, suivi, visioconférence…) ?',
                   'telephone': 'Quel numéro du cabinet l’agent donne-t-il pour rappeler un humain ?'}


def consigne_agent(choix):
    """Consigne par défaut d'un agent créé depuis AxiorHub : assistant automatisé du cabinet, aucun conseil juridique, aucune information sur
    un dossier ; enregistrement et transcription : choix affichés à l'avocat, jamais activés en silence."""
    return ('Tu es l’assistant automatisé du cabinet %(cabinet)s. Annonce dès le début de l’appel que tu es un assistant automatisé du cabinet. '
            'Ton rôle : proposer un rendez-vous parmi ces types : %(types)s, dans ces horaires : %(horaires)s. Ton ton : %(ton)s. '
            'Tu ne donnes jamais de conseil juridique. Tu ne donnes jamais d’information sur un dossier, une procédure ou une personne, même si on '
            'te le demande ; dans ce cas, propose que le cabinet rappelle la personne au %(telephone)s. Tu notes le nom, le numéro et le créneau '
            'souhaité ; tu ne confirmes jamais un rendez-vous : le cabinet le confirmera. %(enregistrement)s') % {
        **choix, 'enregistrement': 'Indique que l’appel est enregistré.' if choix.get('enregistrement') else 'L’appel n’est pas enregistré.'}


def preparer_agent_rdv(desk, acteur, role, data, cle):
    """Parcours « Crée un agent de prise de rendez-vous pour mon cabinet » : questions ciblées si une information manque, puis une demande
    de création (brouillon chez Bland), à confirmer ; publication et numéro entrant restent des demandes distinctes."""
    choix = {k: re.sub(r'\s+', ' ', str(data.get(k) or '')).strip()[:300] for k in ('cabinet', 'horaires', 'types', 'telephone', 'voix', 'ton')}
    manque = [QUESTIONS_AGENT[k] for k in QUESTIONS_AGENT if not choix[k]]
    if manque:
        return {'questions': manque, 'message': 'Il manque des informations pour préparer l’agent.'}
    choix['ton'] = choix['ton'] or 'courtois et sobre'
    choix['enregistrement'] = str(data.get('enregistrement')).lower() in ('true', 'on', '1', 'oui')
    choix['transcription'] = str(data.get('transcription')).lower() in ('true', 'on', '1', 'oui')
    _exiger_utilisable(desk)
    t = _chercher(desk, r'create_?(bland_?)?agent', 'effet')
    if t is None:
        raise Stop('outil_creation_agent_absent')
    props = (t.get('inputSchema') or {}).get('properties') or {}

    def cle_de(*noms):
        return next((n for n in noms if n in props), None)
    k_consigne = cle_de('prompt', 'instructions', 'system_prompt', 'task', 'description')
    if not k_consigne:
        raise Stop('outil_creation_agent_non_reconnu')
    arguments = {k_consigne: consigne_agent(choix)}
    k_nom = cle_de('name', 'nom', 'title')
    if k_nom:
        arguments[k_nom] = ('Accueil rendez-vous — ' + choix['cabinet'])[:120]
    k_voix = cle_de('voice', 'voice_id')
    if k_voix and choix['voix']:
        arguments[k_voix] = choix['voix'][:80]
    k_accueil = cle_de('first_sentence', 'greeting', 'first_message')
    if k_accueil:
        arguments[k_accueil] = 'Bonjour, vous êtes en relation avec l’assistant automatisé du cabinet %s.' % choix['cabinet']
    k_enr = cle_de('record', 'recording')
    if k_enr:
        arguments[k_enr] = bool(choix['enregistrement'])
    return preparer(desk, acteur, role, t['name'], arguments, cle, declencheur='parcours_agent_rdv', origine={'agent_rdv': choix},
                    nature='creation_agent')


def _enregistrer_agent(desk, op, dem, donnees):
    """Création confirmée : l'agent distant devient une routine « agent IA » (lecture seule), une seule fois par agent."""
    ref = ''
    if isinstance(donnees, dict):
        ref = str(donnees.get('agent_id') or donnees.get('id') or '')[:200]
    if not ref:
        desk.db.execute("UPDATE bland56109_demandes SET suite='agent_sans_reference' WHERE op=?", (op['id'],))
        desk.db.commit()
        return
    if desk.db.execute('SELECT 1 FROM bland56109_agents WHERE agent_ref=?', (ref,)).fetchone():
        return
    choix = (json.loads(dem['origine_json'] or '{}').get('agent_rdv') or {})
    nom = ('Agent IA — accueil rendez-vous (%s)' % choix.get('cabinet', ''))[:120]
    rid = _inserer_routine(desk, 'r109-agent-' + _sha(ref)[:16], nom, 'agent_ia', 'Relever les appels reçus par l’agent et proposer les rendez-vous à valider',
                           {'freq': 'jours', 'days': '12345', 'time': '18:30'}, enabled=1, auteur=op['owner'])
    desk.db.execute('INSERT OR IGNORE INTO bland56109_agents(agent_ref,routine,nom,version,etat,derniere_verif,op,choix_json,cree_le) VALUES(?,?,?,?,?,?,?,?,?)',
                    (ref, rid, nom, str((donnees or {}).get('version') or ''), 'brouillon', _maintenant(), op['id'],
                     json.dumps(choix, ensure_ascii=False), _maintenant()))
    desk.db.execute('UPDATE bland56109_demandes SET suite=? WHERE op=?', ('routine:' + rid, op['id']))
    desk.db.commit()
    desk.audit('bland56109_agent_enregistre', {'agent': ref, 'routine': rid})


def _inserer_routine(desk, rid, titre, type_, objectif, calendrier, enabled=0, auteur='cabinet'):
    """Routine du type fermé dans le registre existant (``routines5652``), créée par le programme, jamais par le modèle ni par le formulaire
    générique ; existante : rendue telle quelle."""
    from . import routines5652 as rt
    rt.ensure_schema(desk)
    if desk.db.execute('SELECT 1 FROM routines5652 WHERE id=?', (rid,)).fetchone():
        return rid
    maintenant = _maintenant()
    desk.db.execute('INSERT INTO routines5652(id,owner,scope,title,objective,instruction,task_type,matters_json,matter_scope,schedule_json,timezone,catchup_hours,'
                    'max_matters,enabled,version,permissions_json,legacy_kind,active_since,created_at,updated_at,updated_by,created_by) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?)',
                    (rid, 'cabinet', 'cabinet', titre, objectif, '', type_, '[]', '', json.dumps(rt.normalize_schedule(calendrier), sort_keys=True),
                     str(rt.cabinet_tz(desk)), 12, 50, int(enabled), json.dumps({'mode': 'lecture' if type_ == 'agent_ia' else 'rappel', 'effets_externes': False}),
                     '', maintenant, maintenant, maintenant, str(auteur)[:120], str(auteur)[:120]))
    rt._log_version(desk, rt.get(desk, rid), str(auteur)[:120], 'creation')
    desk.db.commit()
    return rid


def _executer_agent(desk, oid, snap, result):
    """Passage de la routine « agent IA » : LECTURE seulement (agent, appels reçus, résumés) ; propositions à valider, rien dans l'agenda."""
    from . import routines5652 as rt
    agent = desk.db.execute('SELECT * FROM bland56109_agents WHERE routine=?', (snap['id'],)).fetchone()
    if agent is None:
        result['impossible'].append('agent IA introuvable dans AxiorHub')
        return ''
    if not utilisable(desk):
        result['impossible'].append('connexion Bland non prête ou désactivée : rien n’a été relevé')
        return ''
    t_agent = _chercher(desk, r'^get_?(bland_?)?agent$', 'lecture')
    if t_agent is not None:
        rt.checkpoint(desk, oid)
        try:
            lu = consulter(desk, 'cabinet', 'administrateur', t_agent['name'], {'agent_id': agent['agent_ref']})['resultat']
            version = str((lu or {}).get('version') or agent['version']) if isinstance(lu, dict) else agent['version']
            desk.db.execute('UPDATE bland56109_agents SET etat=?,version=?,derniere_verif=? WHERE agent_ref=?',
                            (str((lu or {}).get('status') or 'brouillon') if isinstance(lu, dict) else agent['etat'], version, _maintenant(), agent['agent_ref']))
        except Stop as ex:
            if str(ex).split(':')[0] in ('outil_mcp_en_erreur', 'http_404', 'erreur_mcp_distante'):
                desk.db.execute("UPDATE bland56109_agents SET etat='introuvable',derniere_verif=? WHERE agent_ref=?", (_maintenant(), agent['agent_ref']))
                desk.db.commit()
                result['impossible'].append('agent supprimé ou introuvable chez Bland')
                return ''
            raise
        desk.db.commit()
    t_appels = _chercher(desk, r'list_?(recent_)?calls?', 'lecture')
    if t_appels is None:
        result['impossible'].append('aucun outil de lecture des appels chez Bland')
        return ''
    rt.checkpoint(desk, oid)
    lignes = consulter(desk, 'cabinet', 'administrateur', t_appels['name'], {})['resultat']
    appels = lignes.get('calls') if isinstance(lignes, dict) else (lignes if isinstance(lignes, list) else [])
    nouveaux = 0
    for a in appels or []:
        if not isinstance(a, dict):
            continue
        if a.get('agent_id') and str(a['agent_id']) != agent['agent_ref']:
            continue
        cid = str(a.get('call_id') or a.get('id') or '')[:200]
        if not cid:
            continue
        cur = desk.db.execute('INSERT OR IGNORE INTO bland56109_propositions(id,routine,appel,resume,cree_le) VALUES(?,?,?,?,?)',
                              ('p-' + _sha(snap['id'], cid)[:20], snap['id'], cid, str(a.get('summary') or a.get('resume') or '(sans résumé)')[:2000], _maintenant()))
        nouveaux += cur.rowcount
    desk.db.commit()
    result['produit'].append('%d appel(s) relevé(s), %d nouvelle(s) proposition(s) à valider' % (len(appels or []), nouveaux))
    if nouveaux:
        result['a_decider'].append({'action': 'Relire les propositions de rendez-vous de l’agent IA (rien n’est inscrit dans l’agenda sans vous)'})
    return snap['id']


# ------------------------------------------------------------------------------------------------ routine de rappel de rendez-vous
VARIABLES = ('cabinet', 'date', 'heure', 'lieu', 'telephone_cabinet')
MODELE_DEFAUT = ('Bonjour, le cabinet {cabinet} vous rappelle votre rendez-vous du {date} à {heure} ({lieu}). Pour le déplacer, appelez le '
                 '{telephone_cabinet}.')
CONSIGNE_APPEL = ('Tu es l’assistant automatisé du cabinet {cabinet}. Annonce d’emblée que tu es un assistant automatisé du cabinet. Rappelle le '
                  'rendez-vous du {date} à {heure} ({lieu}). Ne réponds à aucune question sur un dossier ; propose de rappeler le cabinet au '
                  '{telephone_cabinet}.')
RAPPEL_DEFAUT = {'mode': 'preparer', 'delai': 2, 'canal': 'sms', 'modele': MODELE_DEFAUT, 'modele_valide': '', 'plafond': 20, 'cabinet': '',
                 'telephone_cabinet': '', 'outil_sms': 'send_sms', 'outil_appel': 'create_call', 'canaux_clients': {},
                 # 5.6.109 (relecture D1-D4) : adresse du cabinet (seul lieu écrit permis), compte qui a choisi l'envoi automatique, suspension
                 'adresse_cabinet': '', 'auto_par': '', 'auto_role': '', 'auto_suspendu': ''}


def config_rappel_brute(desk):
    if desk is None:
        return dict(RAPPEL_DEFAUT)
    return {**RAPPEL_DEFAUT, **(desk.settings(REGLAGE_RAPPEL, {}) or {})}


def modele_valide(modele):
    """Variables fermées seulement, aucune autre accolade, 10 à 320 caractères."""
    modele = str(modele or '').strip()
    if not 10 <= len(modele) <= 320:
        raise Stop('modele_rappel_longueur')
    reste = modele
    for v in VARIABLES:
        reste = reste.replace('{%s}' % v, '')
    if '{' in reste or '}' in reste:
        raise Stop('modele_rappel_variable_inconnue')
    return modele


def rendre(modele, valeurs):
    """Remplacement littéral des seules variables fermées (jamais ``format``) ; aucun élément de l'affaire n'est une variable. Lieu vide :
    la parenthèse « ({lieu}) » disparaît avec lui."""
    out = str(modele)
    if not str(valeurs.get('lieu') or ''):
        out = out.replace(' ({lieu})', '').replace('({lieu})', '')
    for v in VARIABLES:
        out = out.replace('{%s}' % v, str(valeurs.get(v, '')))
    return out


def creer_rappel_par_defaut(desk):
    """Migration : la routine « Rappel de rendez-vous au client, deux jours avant » est créée une seule fois, désactivée ; supprimée par
    l'utilisateur, elle n'est jamais recréée (repère durable)."""
    if desk.settings(REPERE_RAPPEL, ''):
        return False
    _inserer_routine(desk, ROUTINE_RAPPEL, 'Rappel de rendez-vous au client, deux jours avant', 'rappel_rdv',
                     'SMS ou appel de rappel aux clients, deux jours avant leur rendez-vous', {'freq': 'quotidien', 'time': '09:00'}, enabled=0,
                     auteur='migration')
    desk.setting(REPERE_RAPPEL, _maintenant())
    desk.audit('bland56109_rappel_cree', {'routine': ROUTINE_RAPPEL, 'active': False})
    return True


def numero_e164(valeur):
    """Numéro français (01 00 00 00 05) ou international (+…) normalisé ; invalide : None."""
    s = re.sub(r'[\s.()\-]', '', str(valeur or ''))
    if s.startswith('00'):
        s = '+' + s[2:]
    if re.fullmatch(r'0[1-9]\d{8}', s):
        return '+33' + s[1:]
    if re.fullmatch(r'\+[1-9]\d{7,14}', s):
        return s
    return None


def _tz(desk):
    from . import routines5652 as rt
    return rt.cabinet_tz(desk)


def _jour_cabinet(desk):
    try:
        from .acces5683 import today_of
        return today_of(desk)[0]
    except Exception:   # noqa: BLE001
        return datetime.now(_tz(desk)).date()


def _rdv(desk, ident):
    if not _table(desk, 'agenda5634'):
        return None
    return desk.db.execute('SELECT * FROM agenda5634 WHERE id=?', (ident,)).fetchone()


def rendez_vous_du_jour(desk, jour):
    """Rendez-vous actifs de l'agenda existant qui commencent ce jour-là (jour du cabinet)."""
    if not _table(desk, 'agenda5634'):
        return []
    tz = _tz(desk)
    debut = datetime.combine(jour, datetime.min.time(), tz)
    fin = debut + timedelta(days=1)
    out = []
    for r in desk.db.execute("SELECT * FROM agenda5634 WHERE kind='rendez_vous' AND status='actif' AND all_day=0 ORDER BY starts").fetchall():
        try:
            d = datetime.fromisoformat(r['starts'])
        except ValueError:
            continue
        d = d if d.tzinfo else d.replace(tzinfo=tz)
        if debut <= d < fin:
            out.append(r)
    return out


def client_du_rdv(desk, ev):
    """Client du rendez-vous par le lien existant rendez-vous → dossier → contact (rôle « client ») ; interne ou sans client : None."""
    if not ev['matter'] or not _table(desk, 'contact_links5636') or not _table(desk, 'contacts5636'):
        return None
    return desk.db.execute("SELECT c.* FROM contact_links5636 l JOIN contacts5636 c ON c.id=l.contact WHERE l.matter=? AND l.role='client' "
                           "AND c.archived=0 ORDER BY CASE WHEN c.phone<>'' THEN 0 ELSE 1 END, c.created LIMIT 1", (ev['matter'],)).fetchone()


def accord(desk, contact_id):
    row = desk.db.execute('SELECT * FROM bland56109_accords WHERE contact=?', (contact_id,)).fetchone() if _table(desk, 'bland56109_accords') else None
    return {'sms': bool(row and row['sms']), 'appel': bool(row and row['appel'])}


def noter_accord(desk, acteur, role, contact_id, sms, appel, canal=''):
    """« Accepte les rappels par SMS / par appel » : accord du client enregistré par l'avocat sur sa fiche (table liée au contact) ; canal
    de rappel propre à ce client, facultatif."""
    if role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    if not _table(desk, 'contacts5636') or not desk.db.execute('SELECT 1 FROM contacts5636 WHERE id=?', (str(contact_id),)).fetchone():
        raise Stop('introuvable')
    desk.db.execute('INSERT OR REPLACE INTO bland56109_accords(contact,sms,appel,par,le) VALUES(?,?,?,?,?)',
                    (str(contact_id), int(bool(sms)), int(bool(appel)), acteur, _maintenant()))
    desk.db.commit()
    if canal in ('sms', 'appel'):
        cfg = config_rappel_brute(desk)
        canaux = dict(cfg.get('canaux_clients') or {})
        canaux[str(contact_id)[:80]] = canal
        desk.setting(REGLAGE_RAPPEL, {**(desk.settings(REGLAGE_RAPPEL, {}) or {}), 'canaux_clients': canaux})
    desk.audit('bland56109_accord_client', {'contact': str(contact_id), 'sms': bool(sms), 'appel': bool(appel), 'par': acteur})
    return accord(desk, contact_id)


def _valeurs(desk, ev, cfg):
    """Variables fermées d'un rappel : nom du cabinet, date, heure, lieu ou « en visioconférence », numéro du cabinet. Jamais l'objet du
    rendez-vous, le nom du dossier, la partie adverse ni aucun élément de l'affaire."""
    d = datetime.fromisoformat(ev['starts'])
    d = (d if d.tzinfo else d.replace(tzinfo=_tz(desk))).astimezone(_tz(desk))
    visio = False
    if _table(desk, 'agenda5697'):
        r = desk.db.execute('SELECT visio FROM agenda5697 WHERE event_id=?', (ev['id'],)).fetchone()
        visio = bool(r and r['visio'] not in ('', 'sans'))
    # 5.6.109 (relecture D2) : le champ libre « lieu » de l'agenda n'est JAMAIS recopié (juridiction, parties…) ; valeurs fermées seulement :
    # « en visioconférence », « par téléphone », l'adresse du cabinet réglée dans la routine, ou rien
    libre = re.sub(r'\s+', ' ', str(ev['location'] or '')).strip().lower()
    if visio:
        lieu = 'en visioconférence'
    elif libre in ('par téléphone', 'téléphone', 'telephone', 'par telephone'):
        lieu = 'par téléphone'
    else:
        lieu = str(cfg.get('adresse_cabinet') or '')[:120]
    return {'cabinet': cfg.get('cabinet') or 'votre avocat', 'date': d.strftime('%d/%m/%Y'), 'heure': d.strftime('%H:%M'), 'lieu': lieu,
            'telephone_cabinet': cfg.get('telephone_cabinet') or ''}


def _arguments_rappel(desk, cfg, canal, numero, texte):
    """Arguments de l'outil SMS ou d'appel réglé, construits d'après son schéma ; schéma non reconnu : None (rien ne part)."""
    nom = cfg['outil_sms'] if canal == 'sms' else cfg['outil_appel']
    try:
        t, _rev = outil(desk, nom)
    except Stop:
        return nom, None
    props = (t.get('inputSchema') or {}).get('properties') or {}
    k_num = next((k for k in ('phone_number', 'to', 'number', 'phone', 'recipient') if k in props), None)
    k_txt = next((k for k in (('message', 'text', 'body') if canal == 'sms' else ('task', 'prompt', 'instructions')) if k in props), None)
    if not k_num or not k_txt:
        return nom, None
    return nom, {k_num: numero, k_txt: texte}


def _rappel_suivre(desk, op_id, etat, motif):
    if not _table(desk, 'bland56109_rappels'):
        return
    row = desk.db.execute('SELECT * FROM bland56109_rappels WHERE op=?', (op_id,)).fetchone()
    if row is None:
        return
    envoye = _maintenant() if etat == 'envoye' else row['envoye_le']
    desk.db.execute('UPDATE bland56109_rappels SET etat=?,motif=?,envoye_le=?,maj_le=? WHERE cle=?', (etat, str(motif or '')[:120], envoye, _maintenant(), row['cle']))
    desk.db.commit()
    if etat == 'envoye':
        ev = _rdv(desk, row['rdv'])
        desk.audit('bland56109_rappel_envoye', {'rdv': row['rdv'], 'canal': row['canal'], 'mode': row['mode']})
        if ev is not None and ev['matter']:
            try:
                from .memoire5651 import record_change
                record_change(desk, ev['matter'], 'evenement', 'rappel_bland', row['cle'], 'envoye',
                              'Rappel du rendez-vous envoyé le %s par %s' % (envoye[:10], 'SMS' if row['canal'] == 'sms' else 'appel'), {})
            except Exception:   # noqa: BLE001 — la mention est au journal du rappel ; l'échec de la note est sans effet sur l'envoi
                pass


def _canal_client(cfg, contact_id):
    return (cfg.get('canaux_clients') or {}).get(str(contact_id)) or cfg.get('canal') or 'sms'


def preparer_rappels(desk, oid='', jour=None):
    """Liste du jour : rendez-vous avec un client dans ``delai`` jours ; rappel préparé (demande « À confirmer ») ou motif de non-envoi.
    Une seule ligne par rendez-vous, canal et date (clé d'idempotence) : jamais de doublon après reprise."""
    cfg = config_rappel_brute(desk)
    jour = jour or (_jour_cabinet(desk) + timedelta(days=max(1, min(int(cfg.get('delai') or 2), 7))))
    lignes = []
    maintenant = datetime.now(timezone.utc)
    for ev in rendez_vous_du_jour(desk, jour):
        client = client_du_rdv(desk, ev)
        if client is None:
            lignes.append({'rdv': ev['id'], 'etat': 'ignore', 'motif': 'rendez-vous sans client identifié ou interne'})
            continue
        canal = _canal_client(cfg, client['id'])
        cle = '%s|%s|%s' % (ev['id'], canal, ev['starts'][:10])
        deja = desk.db.execute('SELECT * FROM bland56109_rappels WHERE cle=?', (cle,)).fetchone()
        if deja is not None and deja['etat'] not in ('non_envoye',):
            lignes.append({'rdv': ev['id'], 'etat': deja['etat'], 'motif': 'déjà traité', 'cle': cle, 'op': deja['op']})
            continue
        motif, numero, op_id, etat = '', numero_e164(client['phone']), '', 'non_envoye'
        debut = datetime.fromisoformat(ev['starts'])
        debut = debut if debut.tzinfo else debut.replace(tzinfo=_tz(desk))
        if not accord(desk, client['id'])[canal]:
            motif = 'accord non enregistré'
        elif not numero:
            motif = 'numéro absent ou invalide'
        elif debut <= maintenant:
            motif = 'rendez-vous déjà passé'
        else:
            gabarit = cfg['modele'] if canal == 'sms' else CONSIGNE_APPEL
            texte = rendre(gabarit, _valeurs(desk, ev, cfg))
            nom, arguments = _arguments_rappel(desk, cfg, canal, numero, texte)
            if arguments is None:
                motif = 'outil %s introuvable ou non reconnu dans la liste de Bland' % nom
            else:
                try:
                    f = preparer(desk, 'cabinet', 'administrateur', nom, arguments, 'rappel-' + _sha(cle)[:40], declencheur='rappel_rdv',
                                 origine={'rappel': cle, 'occurrence': oid}, nature=canal if canal == 'sms' else 'appel')
                    op_id, etat = f['operation'], 'prepare'
                except Stop as ex:
                    motif = 'non préparé : ' + str(ex).split(':')[0]
        desk.db.execute('INSERT OR REPLACE INTO bland56109_rappels(cle,rdv,debut,canal,contact,numero,texte_sha,jour,etat,op,motif,occurrence,mode,maj_le) '
                        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (cle, ev['id'], ev['starts'], canal, client['id'], numero or '', '', jour.isoformat(), etat, op_id, motif, oid, cfg['mode'], _maintenant()))
        desk.db.commit()
        lignes.append({'rdv': ev['id'], 'etat': etat, 'motif': motif, 'cle': cle, 'op': op_id})
    return lignes


def _toujours_valable(desk, rappel):
    """Relu juste avant l'envoi : rendez-vous existant, actif, à la même heure, pas encore passé ; accord et numéro du client inchangés."""
    ev = _rdv(desk, rappel['rdv'])
    if ev is None or ev['status'] != 'actif':
        return 'rendez-vous annulé'
    if ev['starts'] != rappel['debut']:
        return 'rendez-vous déplacé depuis la préparation'
    debut = datetime.fromisoformat(ev['starts'])
    debut = debut if debut.tzinfo else debut.replace(tzinfo=_tz(desk))
    if debut <= datetime.now(timezone.utc):
        return 'rendez-vous déjà passé'
    client = client_du_rdv(desk, ev)
    if client is None or str(client['id']) != rappel['contact']:
        return 'client du rendez-vous changé'
    if not accord(desk, client['id'])[rappel['canal']]:
        return 'accord non enregistré'
    if numero_e164(client['phone']) != rappel['numero']:
        return 'numéro du client changé'
    return ''


def empreinte_lot(desk, jour):
    lignes = desk.db.execute("SELECT r.cle, o.id, o.payload_hash FROM bland56109_rappels r JOIN connexion_ops5652 o ON o.id=r.op "
                             "WHERE r.jour=? AND r.etat='prepare' AND o.state='a_valider' ORDER BY r.cle", (jour,)).fetchall() \
        if _table(desk, 'connexion_ops5652') else []
    return _sha(json.dumps([list(x) for x in lignes])), lignes


def confirmer_lot(desk, acteur, role, jour, empreinte, relu=False):
    """Mode « Préparer et me faire confirmer » : UNE confirmation de l'avocat envoie le lot affiché (empreinte) ; chaque rappel est relu
    juste avant son envoi (``_toujours_valable``) et ``pause5629.checkpoint`` précède chaque effet."""
    from . import connexions5652 as cx
    from .pause5629 import checkpoint
    _exiger_humain()
    _refuser_jeton_service()
    if role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    from .membres5650 import effective_rights
    if role != 'administrateur' and not effective_rights(desk.c['state_dir'], acteur, role).get('envoyer'):
        raise Stop('role_insuffisant')
    if not relu:
        raise Stop('relecture_requise')
    attendu, lignes = empreinte_lot(desk, str(jour))
    if not lignes:
        raise Stop('lot_vide')
    if str(empreinte or '') != attendu:
        raise Stop('fiche_modifiee_depuis_affichage')
    if _pause_manuelle(desk):
        raise Stop('automatismes_en_pause')
    _exiger_utilisable(desk)
    bilan = []
    for cle, op_id, _ph in lignes:
        rappel = desk.db.execute('SELECT * FROM bland56109_rappels WHERE cle=?', (cle,)).fetchone()
        motif = _toujours_valable(desk, rappel)
        if motif:
            cx.cancel(desk, acteur, 'administrateur', op_id)
            desk.db.execute("UPDATE bland56109_rappels SET etat='abandonne',motif=?,maj_le=? WHERE cle=?", (motif, _maintenant(), cle))
            desk.db.commit()
            bilan.append({'cle': cle, 'etat': 'abandonne', 'motif': motif})
            continue
        checkpoint(desk)
        if _pause_manuelle(desk):
            bilan.append({'cle': cle, 'etat': 'prepare', 'motif': 'IA en pause : arrêt du lot'})
            break
        op = desk.db.execute('SELECT * FROM connexion_ops5652 WHERE id=?', (op_id,)).fetchone()
        try:
            with _autoriser(op_id):
                cx.validate(desk, acteur, _role_effectif(desk, role, op_id), op_id, op['version'], '')
        except Stop as ex:
            apres_effet(desk, op_id)
            bilan.append({'cle': cle, 'etat': 'echec', 'motif': str(ex).split(':')[0]})
            continue
        apres_effet(desk, op_id)
        bilan.append({'cle': cle, 'etat': 'envoye'})
    desk.audit('bland56109_lot_confirme', {'jour': str(jour), 'par': acteur, 'nombre': len(bilan)})
    return {'jour': str(jour), 'bilan': bilan}


def _envois_auto_du_jour(desk):
    """Tentatives d'envoi automatique du jour du cabinet : toute tentative engagée compte (horodatée AVANT l'appel, relecture D1), quel qu'en
    soit le résultat — envoyé, à vérifier (réponse perdue : peut-être parti) ou refusé."""
    # 5.6.109 (intégration I1) : ``envoye_le`` est un instant UTC ; le jour du cabinet commence à minuit DANS SON FUSEAU. Comparer la date UTC au
    # jour du cabinet laissait passer, entre minuit et 1 h (2 h en été) à Paris, des envois au-delà du plafond quotidien
    debut = datetime.combine(_jour_cabinet(desk), datetime.min.time(), _tz(desk)).astimezone(timezone.utc).isoformat(timespec='seconds')
    return desk.db.execute("SELECT COUNT(*) FROM bland56109_rappels WHERE mode='auto' AND envoye_le<>'' AND envoye_le>=?",
                           (debut,)).fetchone()[0]


def _envoyer_auto(desk, rappel, cfg):
    """Mode « Envoyer automatiquement » (choisi explicitement par l'avocat) : l'exception bornée, et elle seule."""
    from . import connexions5652 as cx
    from .pause5629 import checkpoint
    op = desk.db.execute('SELECT * FROM connexion_ops5652 WHERE id=?', (rappel['op'],)).fetchone()
    if op is None or op['state'] != 'a_valider':
        return 'non_disponible'
    payload = json.loads(op['payload_json'])
    nom, arguments = payload['outil'], payload['arguments']
    # la demande préparée doit être celle du modèle validé, rendu pour ce rendez-vous, vers le numéro enregistré
    ev = _rdv(desk, rappel['rdv'])
    gabarit = cfg['modele'] if rappel['canal'] == 'sms' else CONSIGNE_APPEL
    _n, attendus = _arguments_rappel(desk, cfg, rappel['canal'], rappel['numero'], rendre(gabarit, _valeurs(desk, ev, cfg)))
    if attendus is None or attendus != arguments:
        return 'demande_differente_du_modele'
    # 5.6.109 (relecture D2) : personne ne confirme en mode automatique : AUCUNE exception d'identité ; une identité connue du registre dans le
    # texte final laisse la demande « à confirmer », avec son motif
    motif = _identite_hors_numero(desk, arguments, rappel['numero'])
    if motif:
        desk.db.execute("UPDATE bland56109_rappels SET motif=?,maj_le=? WHERE cle=?", (motif, _maintenant(), rappel['cle']))
        desk.db.commit()
        return motif
    # 5.6.109 (relecture D1) : la tentative est réservée et horodatée AVANT l'appel : elle compte dans le plafond quel qu'en soit le résultat
    desk.db.execute("UPDATE connexion_ops5652 SET trigger='rappel_automatique',updated=? WHERE id=? AND state='a_valider'", (_maintenant(), op['id']))
    desk.db.execute("UPDATE bland56109_rappels SET mode='auto',etat='tentative',envoye_le=?,maj_le=? WHERE cle=?",
                    (_maintenant(), _maintenant(), rappel['cle']))
    desk.db.commit()
    op = desk.db.execute('SELECT * FROM connexion_ops5652 WHERE id=?', (op['id'],)).fetchone()
    checkpoint(desk)
    jeton = _EXCEPTION.set({'op': op['id'], 'outil': nom, 'numero': rappel['numero'], 'arguments': arguments, 'cfg': cfg})
    try:
        cx.validate(desk, 'rappel_automatique', 'administrateur', op['id'], op['version'], '')
    except Stop as ex:
        apres_effet(desk, op['id'])
        etat = desk.db.execute('SELECT state FROM connexion_ops5652 WHERE id=?', (op['id'],)).fetchone()
        if etat and etat['state'] == 'resultat_a_verifier':
            return 'a_verifier'
        return 'echec:' + str(ex).split(':')[0]
    finally:
        _EXCEPTION.reset(jeton)
    apres_effet(desk, op['id'])
    return 'envoye'


def _identite_hors_numero(desk, arguments, numero):
    """Motif si une identité connue du registre figure dans une valeur transmise autre que le numéro ; registre illisible : motif aussi."""
    try:
        from .pseudo540 import Pseudonymizer
        pseudo = Pseudonymizer(desk.c.get('pseudo') or {'state_dir': desk.c.get('state_dir', '')})
        for _chemin, v in _parcourir(arguments):
            if isinstance(v, str) and v != numero and len(v) >= 3 and pseudo.residuelle(v, technique=False):
                return 'identité connue du cabinet dans le texte : rappel à confirmer par l’avocat'
    except Exception:   # noqa: BLE001 — contrôle impossible : rien ne part sans l'avocat
        return 'registre des identités illisible : rappel à confirmer par l’avocat'
    return ''


def empreinte_auto(cfg):
    """Empreinte validée par l'avocat pour l'envoi automatique (relecture D4) : modèle du SMS, consigne d'appel et toutes les valeurs écrites
    dans le texte ou la consigne (nom, numéro et adresse du cabinet), outils réglés. Une modification : retour au mode « Préparer »."""
    return _sha('auto-v2', cfg.get('modele'), CONSIGNE_APPEL, cfg.get('cabinet'), cfg.get('telephone_cabinet'), cfg.get('adresse_cabinet'),
                cfg.get('outil_sms'), cfg.get('outil_appel'))


def _mode_auto_valable(cfg):
    return (cfg.get('mode') == 'auto' and bool(cfg.get('modele_valide')) and cfg['modele_valide'] == empreinte_auto(cfg)
            and not cfg.get('auto_suspendu'))


def _droit_envoyer(desk, compte, role):
    """Droit « envoyer » du compte (relecture D3), rôle relu dans l'annuaire des membres quand il y figure ; illisible : refusé."""
    try:
        from .membres5650 import effective_rights
        row = desk.db.execute('SELECT role FROM membres5650 WHERE owner=?', (compte,)).fetchone() if _table(desk, 'membres5650') else None
        role = row['role'] if row else role
        if role == 'administrateur':
            return True
        return role in ('avocat', 'assistant') and bool(effective_rights(desk.c['state_dir'], compte, role).get('envoyer'))
    except Exception:   # noqa: BLE001
        return False


def _suspendre_auto(desk, motif):
    desk.setting(REGLAGE_RAPPEL, {**(desk.settings(REGLAGE_RAPPEL, {}) or {}), 'auto_suspendu': motif[:200]})
    desk.audit('bland56109_rappel_auto_suspendu', {'motif': motif[:200]})


def _refuser_jeton_service():
    """Le jeton de service (sans identité) ne confirme rien et ne règle rien."""
    try:
        from .membres5650 import current_author
        auteur = str(current_author() or '')
    except Exception:   # noqa: BLE001 — hors requête : pas d'auteur
        auteur = ''
    if auteur == 'jeton_service':
        raise Stop('jeton_service_refuse')


def _executer_rappel(desk, oid, snap, result):
    """Passage quotidien : relevé de l'agenda, préparation ; en mode automatique validé, envoi borné (plafond) ; sinon le lot attend une
    seule confirmation. IA en pause ou connexion en panne : rien ne part, la routine le dit."""
    from . import routines5652 as rt
    cfg = config_rappel_brute(desk)
    if _pause_manuelle(desk):
        result['impossible'].append('IA en pause : aucun rappel n’est parti')
        return ''
    if not utilisable(desk):
        result['impossible'].append('connexion Bland non prête ou désactivée : aucun rappel n’est parti')
        return ''
    rt.checkpoint(desk, oid)
    lignes = preparer_rappels(desk, oid)
    prets = [x for x in lignes if x['etat'] == 'prepare']
    for x in lignes:
        if x['etat'] in ('non_envoye', 'ignore') and x.get('motif'):
            result['impossible'].append('rendez-vous %s : %s' % (x['rdv'], x['motif']))
    if not prets:
        result['produit'].append('aucun rappel à envoyer aujourd’hui')
        return oid
    if cfg.get('mode') == 'auto' and cfg.get('auto_suspendu'):
        result['impossible'].append('envoi automatique suspendu (%s) : les rappels attendent votre confirmation' % cfg['auto_suspendu'])
    elif cfg.get('mode') == 'auto' and not _mode_auto_valable(cfg):
        result['impossible'].append('modèle de texte modifié depuis sa validation : retour au mode « Préparer et me faire confirmer »')
    if _mode_auto_valable(cfg) and not _droit_envoyer(desk, cfg.get('auto_par') or '', cfg.get('auto_role') or ''):
        _suspendre_auto(desk, 'droit « envoyer » retiré au compte qui a choisi l’envoi automatique')
        result['impossible'].append('droit « envoyer » retiré au compte qui a choisi l’envoi automatique : envoi automatique arrêté, rappels à confirmer')
        cfg = config_rappel_brute(desk)
    if _mode_auto_valable(cfg):
        envoyes = 0
        for x in prets:
            rt.checkpoint(desk, oid)
            if _envois_auto_du_jour(desk) >= int(cfg.get('plafond') or 0):
                result['impossible'].append('plafond quotidien atteint : les rappels restants attendent votre confirmation')
                break
            rappel = desk.db.execute('SELECT * FROM bland56109_rappels WHERE cle=?', (x['cle'],)).fetchone()
            motif = _toujours_valable(desk, rappel)
            if motif:
                from . import connexions5652 as cx
                cx.cancel(desk, 'cabinet', 'administrateur', rappel['op'])
                desk.db.execute("UPDATE bland56109_rappels SET etat='abandonne',motif=?,maj_le=? WHERE cle=?", (motif, _maintenant(), rappel['cle']))
                desk.db.commit()
                result['impossible'].append('rendez-vous %s : %s' % (rappel['rdv'], motif))
                continue
            etat = _envoyer_auto(desk, rappel, cfg)
            if etat == 'envoye':
                envoyes += 1
            elif etat == 'a_verifier':   # 5.6.109 (relecture D1) : premier résultat incertain : l'envoi automatique s'arrête et le dit
                _suspendre_auto(desk, 'réponse de Bland perdue : envoi à vérifier')
                result['impossible'].append('rendez-vous %s : envoi à vérifier (réponse perdue) ; envoi automatique suspendu, les autres rappels '
                                            'attendent votre confirmation' % rappel['rdv'])
                break
            else:
                result['impossible'].append('rendez-vous %s : %s' % (rappel['rdv'], etat))
        result['produit'].append('%d rappel(s) envoyé(s) automatiquement' % envoyes)
    else:
        result['a_decider'].append({'action': 'Confirmer le lot de %d rappel(s) du jour (Mes routines | Mes agents IA)' % len(prets)})
        result['produit'].append('%d rappel(s) préparé(s), à confirmer' % len(prets))
    return oid


def executer(desk, oid, snap, result):
    """Point d'entrée de ``routines5652.execute`` pour les types fermés de ce lot."""
    if snap['task_type'] == 'rappel_rdv':
        return _executer_rappel(desk, oid, snap, result)
    if snap['task_type'] == 'agent_ia':
        return _executer_agent(desk, oid, snap, result)
    raise Stop('type_routine_invalide')


def regler_rappel(desk, acteur, role, data):
    """Activation et réglages de la routine de rappel : délai (1 à 7 jours), canal par défaut et par client, modèle, plafond, mode. Le mode
    automatique exige la case « J'ai lu ce qui partira sans moi » ; toute modification du modèle repasse en mode « Préparer » jusqu'à
    revalidation. Activer exige une connexion Bland prête."""
    from . import routines5652 as rt
    _refuser_jeton_service()
    if role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    # 5.6.109 (relecture D3) : envoi automatique, validation du modèle, activation : même droit « envoyer » que pour confirmer
    if (data.get('mode') == 'auto' or _vrai(data.get('valider_modele')) or _vrai(data.get('lu_envoi_auto')) or _vrai(data.get('activer'))) \
            and not _droit_envoyer(desk, acteur, role):
        raise Stop('role_insuffisant')
    cfg = config_rappel_brute(desk)
    nouveau = dict(cfg)
    if 'delai' in data:
        try:
            nouveau['delai'] = int(data['delai'])
        except (TypeError, ValueError):
            raise Stop('delai_rappel_invalide') from None
        if not 1 <= nouveau['delai'] <= 7:
            raise Stop('delai_rappel_invalide')
    if 'canal' in data:
        if data['canal'] not in ('sms', 'appel'):
            raise Stop('canal_rappel_invalide')
        nouveau['canal'] = data['canal']
    if 'plafond' in data:
        try:
            nouveau['plafond'] = max(0, min(int(data['plafond']), 200))
        except (TypeError, ValueError):
            raise Stop('plafond_rappel_invalide') from None
    for k in ('cabinet', 'telephone_cabinet', 'adresse_cabinet'):
        if k in data:
            nouveau[k] = re.sub(r'\s+', ' ', str(data[k] or '')).strip()[:120]
    if 'modele' in data:
        nouveau['modele'] = modele_valide(data['modele'])
        if nouveau['modele'] != cfg['modele']:
            nouveau['modele_valide'] = ''               # modèle modifié : à revalider ; mode automatique suspendu d'ici là
    if 'mode' in data:
        if data['mode'] not in ('preparer', 'auto'):
            raise Stop('mode_rappel_invalide')
        nouveau['mode'] = data['mode']
    if nouveau['mode'] == 'auto':
        if str(data.get('lu_envoi_auto')).lower() not in ('true', 'on', '1', 'oui') and cfg.get('mode') != 'auto':
            raise Stop('ecran_envoi_auto_non_lu')
        if str(data.get('valider_modele')).lower() in ('true', 'on', '1', 'oui'):
            nouveau.update(modele_valide=empreinte_auto(nouveau), auto_par=acteur, auto_role=role, auto_suspendu='')
    if 'contact' in data and data.get('canal_client') in ('sms', 'appel'):
        canaux = dict(nouveau.get('canaux_clients') or {})
        canaux[str(data['contact'])[:80]] = data['canal_client']
        nouveau['canaux_clients'] = canaux
    desk.setting(REGLAGE_RAPPEL, nouveau)
    if 'activer' in data:
        on = str(data['activer']).lower() in ('true', 'on', '1', 'oui')
        if on and not utilisable(desk):
            raise Stop('connecteur_bland_non_pret')
        row = rt.get(desk, ROUTINE_RAPPEL)
        basculer_routine(desk, acteur, role, ROUTINE_RAPPEL, on, row['version'])
    desk.audit('bland56109_rappel_regle', {'par': acteur, 'mode': nouveau['mode'], 'canal': nouveau['canal'], 'delai': nouveau['delai'],
                                          'modele_valide': _mode_auto_valable(nouveau)})
    return {k: v for k, v in config_rappel_brute(desk).items() if k != 'canaux_clients'}


def basculer_routine(desk, acteur, role, rid, on, version):
    from . import routines5652 as rt
    return rt.basculer_type_ferme(desk, acteur, role, rid, on, version)


# ------------------------------------------------------------------------------------------------ interface
COMPETENCE = 'builtin.telephonie109.bland'


def _date(v):
    return e(str(v or '')[:16].replace('T', ' '))


def _cle_html():
    return '<input type="hidden" name="cle" value="%s">' % uuid.uuid4().hex


def _form(route, champs, bouton, classe='ax-btn', confirm=''):
    return ('<form class="m5-form r52-inline b109-form" data-api="%s%s" data-reload%s>%s%s<button class="%s" type="submit">%s</button>'
            '<p role="status" class="m5-status"></p></form>') % (PREFIXE, route, (' data-confirm="%s"' % e(confirm, quote=True)) if confirm else '',
                                                                champs, _cle_html(), classe, e(bouton))


def aide_html(lang=None):
    """Rubrique d'aide en ligne (``agent/aide/<langue>.json``, clé ``telephonie_bland``) affichée dans la carte."""
    try:
        from .aide5631 import article
        art, _ = article('telephonie_bland', lang)
    except Exception:   # noqa: BLE001
        art = None
    if not art:
        return ''
    return ('<details class="b109-aide"><summary>Aide : téléphonie par IA</summary><p>%s</p><ol>%s</ol><p class="cx52-note">%s</p></details>') % (
        e(art.get('objective', '')), ''.join('<li>%s</li>' % e(s) for s in art.get('steps', [])), e(art.get('verify', '')))


def page_famille(desk, auth, prefix, env):
    """5.6.109 (intégration I2) : onglet « Téléphonie par IA (Bland) » de la famille Accueil téléphonique (``/accueil-administratif?vue=bland``,
    fonction ``telephonie_bland`` du registre, rubrique du guide d'utilisation). Même carte que Paramètres › Connexions API, même état réel,
    mêmes formulaires (droits relus par chaque route) ; lecture seule au rendu."""
    from html import escape
    from .web440 import shell
    from .web567 import actor
    owner, role = actor(env)
    corps = ('<div class="cx52 b109-page"><h1>Téléphonie par IA (Bland)</h1><p class="cx52-note">Les réglages sont aussi dans <a href="%s">Paramètres › '
             'Connexions API</a> ; les agents IA et le rappel de rendez-vous se règlent dans « Mes routines | Mes agents IA ».</p>%s</div>') % (
        escape(prefix + '/parametres?tab=connexions', quote=True), carte_html(desk, prefix, owner, role))
    return shell('Téléphonie par IA (Bland)', corps, prefix, auth['csrf'], '/accueil-administratif')


def carte_html(desk, prefix, owner, role):
    """Carte « Bland — téléphonie par IA » des Paramètres › Connexions API (aucune écriture ; jamais la clé)."""
    v = vue(desk)
    etat = v['etat']
    peut_regler = role == 'administrateur'
    lignes = []
    if v['local_strict']:
        lignes.append('<p class="b109-alerte" role="alert">Mode local strict : la connexion à Bland est refusée (aucun appel externe). '
                      'Changez le mode des données pour l’utiliser.</p>')
    cle = ''
    if peut_regler:
        cle = ('<form class="m5-form b109-form" data-api="%scle" data-reload><label>Clé API Bland<input type="password" name="api_key" '
               'autocomplete="new-password" maxlength="4096" placeholder="%s"></label>%s<button class="ax-btn" type="submit">Enregistrer la clé</button>'
               '<p role="status" class="m5-status"></p></form>') % (PREFIXE, e('enregistrée le %s — saisir une nouvelle clé pour la remplacer' % v['cle_enregistree_le']
                                                                         if v['cle_enregistree'] else 'org_…', quote=True), _cle_html())
        if v['cle_enregistree']:
            cle += _form('tester', '', 'Tester la connexion')
            cle += _form('retirer', '', 'Retirer la clé', 'ax-btn ghost', 'Retirer la clé Bland ? Les demandes non confirmées seront annulées.')
        if etat == 'pret' and not v['local_strict']:
            cle += _form('activer', '<input type="hidden" name="on" value="%s">' % ('false' if v['actif'] else 'true'),
                         'Désactiver le connecteur' if v['actif'] else 'Activer le connecteur', 'ax-btn ghost' if v['actif'] else 'ax-btn')
    else:
        cle = '<p class="cx52-note">Seul un administrateur règle la clé et l’activation.</p>'
    outils = ''
    cache = dernier_releve(desk)
    if cache:
        compte = {'lecture': 0, 'effet': 0, 'engageant': 0}
        for t in cache['outils']:
            compte['engageant' if t['engageant'] else t['classe']] += 1
        lis = ''.join('<li><code>%s</code> — %s</li>' % (e(t['name']), e(t['libelle'])) for t in cache['outils'][:400])
        outils = ('<details class="b109-outils"><summary>%d outil(s) découvert(s) (révision %d, relevé le %s) : %d lecture(s), %d effet(s), %d engageant(s)'
                  '</summary><ul>%s</ul></details>') % (cache['nombre'], cache['revision'], _date(cache['releve_le']), compte['lecture'], compte['effet'],
                                                        compte['engageant'], lis)
    demandes = ''
    if _table(desk, 'connexion_ops5652') and _table(desk, 'bland56109_demandes'):
        rows = desk.db.execute("SELECT o.id,o.state,o.created,d.outil,d.nature FROM connexion_ops5652 o JOIN bland56109_demandes d ON d.op=o.id "
                               "WHERE o.provider=? AND o.state IN ('a_valider','resultat_a_verifier','echec_avant_envoi') ORDER BY o.created DESC LIMIT 30",
                               (PROVIDER,)).fetchall()
        if rows:
            demandes = '<h4>Demandes à confirmer ou à vérifier</h4><ul class="b109-demandes">%s</ul>' % ''.join(
                '<li><a href="%s">%s</a> — %s · %s</li>' % (e(prefix + '/operation?id=' + r['id'], quote=True), e(r['outil']),
                                                           e(ETATS_DEMANDE.get(r['state'], r['state'])), _date(r['created'])) for r in rows)
    return ('<article class="cx52-card b109-carte" id="cx52-bland"><h3>Bland — téléphonie par IA<span class="cx52-state b109-etat-%s">%s</span></h3>'
            '<p class="cx52-note">%s · %s</p><p class="b109-avertissement" role="note">%s</p>%s'
            '<div class="cx52-links"><a href="%s" target="_blank" rel="noopener noreferrer">Créer un compte Bland</a>'
            '<a href="%s" target="_blank" rel="noopener noreferrer">Créer une clé API</a></div>'
            '<p class="cx52-note">Connecteur : <strong>%s</strong>. %s Dernier test : %s.</p>'
            '<p class="cx52-note">Chaque appel, SMS, achat, publication ou branchement de numéro est préparé puis confirmé par vous, un par un. '
            'Coûts inscrits dans « Consommation et coûts ».</p>%s%s%s%s</article>') % (
        e(etat, quote=True), e(ETATS[etat]), e('Téléphonie par IA'), e(SPEC['usage']), e(AVERTISSEMENT), ''.join(lignes),
        e(LIEN_COMPTE, quote=True), e(LIEN_CLE, quote=True), 'activé' if v['actif'] else 'désactivé', e(SPEC['note']),
        _date(v['dernier_test']) or 'aucun', cle, outils, demandes, aide_html())


def fiche_html(desk, prefix, owner, role, op_id):
    """Page /operation d'une demande Bland : exactement ce qui part, avertissement, confirmation explicite (case + bouton)."""
    f = fiche(desk, owner, role, op_id)
    ap = f['apercu']
    numeros = ''.join('<li><strong>%s</strong></li>' % e(n) for n in ap.get('numeros') or []) or '<li>Aucun numéro.</li>'
    textes = ''.join('<li><span>%s</span><pre class="b109-texte">%s</pre></li>' % (e(t['champ']), e(t['valeur'])) for t in ap.get('textes') or []) \
        or '<li>Aucun texte.</li>'
    montant = ('<dt>Montant</dt><dd><strong>%s</strong></dd>' % e(ap['montant'])) if ap.get('montant') else ''
    exceptions = ('<p class="b109-exception" role="note">Exception explicite au contrôle des identités : %s contiennent un nom ou une donnée du cabinet, '
                  'transmis parce que vous les confirmez vous-même. Cette exception est journalisée.</p>' % e(', '.join(f['exceptions']))) if f['exceptions'] else ''
    actions = ''
    if f['etat'] in ('a_valider', 'echec_avant_envoi') and role in ('administrateur', 'avocat'):
        actions = ('<form class="m5-form b109-form" data-api="%sdemande/confirmer" data-reload><input type="hidden" name="id" value="%s">'
                   '<input type="hidden" name="version" value="%d"><input type="hidden" name="empreinte" value="%s">%s'
                   '<label class="r52-day"><input type="checkbox" name="relu" required> J’ai relu le numéro, le texte et le montant ci-dessus</label>'
                   '<button class="ax-btn" type="submit">Confirmer et transmettre à Bland</button><p role="status" class="m5-status"></p></form>') % (
            PREFIXE, e(f['operation'], quote=True), f['version'], e(f['empreinte'], quote=True), _cle_html())
        actions += _form('demande/annuler', '<input type="hidden" name="id" value="%s">' % e(f['operation'], quote=True), 'Annuler la demande', 'ax-btn ghost')
    if f['etat'] == 'resultat_a_verifier' and role in ('administrateur', 'avocat'):
        champ = '<input type="hidden" name="id" value="%s">' % e(f['operation'], quote=True)
        actions = ('<p class="b109-alerte" role="alert">Réponse perdue : l’action a peut-être eu lieu. Elle ne sera jamais rejouée automatiquement '
                   '(un second appel ou un second achat serait un doublon). Consultez la liste des appels, puis constatez.</p>' +
                   _form('demande/rapprocher', champ, 'Lire la liste des appels chez Bland', 'ax-btn ghost') +
                   _form('demande/constater', champ + '<input type="hidden" name="constat" value="parti">', 'Constater : c’est parti') +
                   _form('demande/constater', champ + '<input type="hidden" name="constat" value="non_parti">', 'Constater : rien n’est parti', 'ax-btn ghost'))
    return ('<section class="cx53-sheet b109-fiche"><h2>Demande Bland : %s</h2><p class="cx52-state">%s</p>%s'
            '<p class="b109-avertissement" role="note">%s</p><dl class="cx53-dl"><dt>Outil</dt><dd><code>%s</code> (%s%s)</dd>'
            '<dt>Numéro(s) appelé(s) ou destinataire(s)</dt><dd><ul>%s</ul></dd><dt>Destinataires d’une campagne</dt><dd>%d</dd>%s'
            '<dt>Déclencheur</dt><dd>%s, préparée par %s le %s</dd></dl><h3>Consigne ou texte transmis</h3><ul class="cx53-list">%s</ul>%s'
            '<details><summary>Arguments exacts transmis à Bland</summary><pre class="b109-texte">%s</pre></details>%s</section>') % (
        e(ap.get('nature') or f['outil']), e(f['etat_libelle']), ('<p class="cx52-note">Motif : %s</p>' % e(f['erreur'])) if f['erreur'] else '',
        e(f['avertissement']), e(f['outil']), e(ap.get('nature', '')), ', engageant' if f['engageant'] else '', numeros, int(ap.get('destinataires') or 0),
        montant, e(f['declencheur']), e(f['preparee_par']), _date(f['preparee_le']), textes, exceptions,
        e(json.dumps(ap.get('arguments') or {}, ensure_ascii=False, indent=2)), actions)


def _carte_rappel(desk, prefix, role, r):
    cfg = config_rappel_brute(desk)
    pret = utilisable(desk)
    etat = 'Activée' if r['enabled'] else 'À activer'
    regl = ''
    if role in ('administrateur', 'avocat'):
        canal = ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if cfg['canal'] == k else '', e(v)) for k, v in (('sms', 'SMS'), ('appel', 'Appel d’un agent vocal')))
        regl = ('<details class="b109-regler"><summary class="ax-btn ghost">Régler la routine</summary><form class="m5-form b109-form" data-api="%srappel/regler" data-reload>'
                '<label>Délai avant le rendez-vous (jours)<input name="delai" type="number" min="1" max="7" value="%d"></label>'
                '<label>Canal par défaut<select name="canal">%s</select></label>'
                '<label>Nom du cabinet annoncé<input name="cabinet" maxlength="120" value="%s"></label>'
                '<label>Numéro du cabinet pour déplacer le rendez-vous<input name="telephone_cabinet" maxlength="40" value="%s"></label>'
                '<label>Adresse du cabinet (seul lieu écrit dans un rappel ; vide : aucun lieu)<input name="adresse_cabinet" maxlength="120" value="%s"></label>'
                '<label>Modèle du texte (variables : {cabinet}, {date}, {heure}, {lieu}, {telephone_cabinet})<textarea name="modele" rows="3" maxlength="320">%s</textarea></label>'
                '<p class="cx52-note">Le texte est fixe : aucune IA ne l’écrit. Il ne contient jamais l’objet du rendez-vous, le nom du dossier, la partie adverse ni '
                'aucun élément de l’affaire. Le lieu n’est jamais le texte libre de l’agenda : « en visioconférence », « par téléphone », l’adresse ci-dessus, ou rien.</p>'
                '<p class="cx52-note">Consigne fixe d’un rappel par appel (fait partie de la validation) :</p><pre class="b109-texte">%s</pre>'
                '<fieldset><legend>Mode</legend><label class="r52-day"><input type="radio" name="mode" value="preparer"%s> Préparer et me faire confirmer (par défaut)</label>'
                '<label class="r52-day"><input type="radio" name="mode" value="auto"%s> Envoyer automatiquement</label>'
                '<p class="b109-alerte">En mode « Envoyer automatiquement », partiront sans vous, chaque jour : le texte du modèle validé ci-dessus, rendu pour '
                'chaque rendez-vous, vers le seul numéro enregistré du client qui a donné son accord, dans la limite du plafond. Rien d’autre. Toute modification '
                'du modèle, de la consigne d’appel, du nom, du numéro ou de l’adresse du cabinet repasse en mode « Préparer » jusqu’à une nouvelle validation. '
                'Au premier envoi dont la réponse se perd (« à vérifier »), l’envoi automatique s’arrête jusqu’à votre nouvelle validation.</p>'
                '<label class="r52-day"><input type="checkbox" name="lu_envoi_auto"> J’ai lu ce qui partira sans moi</label>'
                '<label class="r52-day"><input type="checkbox" name="valider_modele"> Je valide ce modèle de texte</label>'
                '<label>Plafond quotidien d’envois automatiques<input name="plafond" type="number" min="0" max="200" value="%d"></label></fieldset>'
                '<label class="r52-day"><input type="checkbox" name="activer"%s%s> Routine activée%s</label>%s'
                '<button class="ax-btn" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form></details>') % (
            PREFIXE, int(cfg['delai']), canal, e(cfg['cabinet'], quote=True), e(cfg['telephone_cabinet'], quote=True), e(cfg['adresse_cabinet'], quote=True),
            e(CONSIGNE_APPEL), e(cfg['modele']),
            ' checked' if cfg['mode'] != 'auto' else '', ' checked' if cfg['mode'] == 'auto' else '', int(cfg['plafond']),
            ' checked' if r['enabled'] else '', '' if (pret or r['enabled']) else ' disabled', '' if pret else ' (connexion Bland prête requise)', _cle_html())
    lot = ''
    jour = (_jour_cabinet(desk) + timedelta(days=int(cfg['delai']))).isoformat()
    emp, lignes = empreinte_lot(desk, jour)
    if lignes and role in ('administrateur', 'avocat'):
        details = ''
        for cle, op_id, _ph in lignes:
            dem = desk.db.execute('SELECT apercu_json FROM bland56109_demandes WHERE op=?', (op_id,)).fetchone()
            ap = json.loads(dem['apercu_json']) if dem else {}
            details += '<li><strong>%s</strong> — %s</li>' % (e(', '.join(ap.get('numeros') or [])), e(' '.join(t['valeur'] for t in ap.get('textes') or [])))
        lot = ('<div class="b109-lot"><h4>Rappels préparés pour le %s</h4><ul>%s</ul><form class="m5-form b109-form" data-api="%srappel/confirmer-lot" data-reload>'
               '<input type="hidden" name="jour" value="%s"><input type="hidden" name="empreinte" value="%s">%s<label class="r52-day"><input type="checkbox" name="relu" required> '
               'J’ai relu les numéros et les textes ci-dessus</label><button class="ax-btn" type="submit">Envoyer ce lot</button><p role="status" class="m5-status"></p></form></div>') % (
            e(jour), details, PREFIXE, e(jour, quote=True), e(emp, quote=True), _cle_html())
    accords = ''
    if role in ('administrateur', 'avocat'):
        vus = {}
        for i in range(8):
            for ev in rendez_vous_du_jour(desk, _jour_cabinet(desk) + timedelta(days=i)):
                c = client_du_rdv(desk, ev)
                if c is not None:
                    vus.setdefault(c['id'], c)
        lignes_accord = ''
        for cid, c in vus.items():
            a = accord(desk, cid)
            canal_c = (cfg.get('canaux_clients') or {}).get(str(cid), '')
            options = ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if canal_c == k else '', e(v))
                              for k, v in (('', 'canal par défaut'), ('sms', 'SMS'), ('appel', 'appel')))
            lignes_accord += ('<li>%s %s — %s<form class="m5-form r52-inline b109-form" data-api="%srappel/accord" data-reload>'
                              '<input type="hidden" name="contact" value="%s"><label class="r52-day"><input type="checkbox" name="sms"%s> accepte les rappels par SMS</label>'
                              '<label class="r52-day"><input type="checkbox" name="appel"%s> accepte les rappels par appel</label>'
                              '<label>Canal de ce client<select name="canal">%s</select></label>%s<button class="ax-btn ghost" type="submit">Enregistrer l’accord</button>'
                              '<p role="status" class="m5-status"></p></form></li>') % (
                e(c['first_name'] or ''), e(c['name']), e(c['phone'] or 'numéro absent'), PREFIXE, e(cid, quote=True), ' checked' if a['sms'] else '',
                ' checked' if a['appel'] else '', options, _cle_html())
        if lignes_accord:
            accords = ('<details class="b109-accords"><summary>Accord des clients des rendez-vous à venir (sans accord : aucun rappel)</summary>'
                       '<ul>%s</ul></details>') % lignes_accord
    refus = ''
    if _table(desk, 'bland56109_rappels'):
        rows = desk.db.execute("SELECT * FROM bland56109_rappels WHERE motif<>'' ORDER BY maj_le DESC LIMIT 10").fetchall()
        if rows:
            refus = '<details><summary>Derniers rappels non envoyés</summary><ul>%s</ul></details>' % ''.join(
                '<li>%s — %s</li>' % (e(x['jour']), e(x['motif'])) for x in rows)
    return ('<div class="c5614-routine r52-card b109-routine%s" id="r52-%s"><div class="r52-head"><strong>%s</strong><span class="r52-badge r52-%s">%s</span></div>'
            '<span class="r52-meta">%s · mode : %s · canal : %s · %d jour(s) avant</span>%s%s%s%s</div>') % (
        '' if r['enabled'] else ' r52-paused', e(r['id'], quote=True), e(r['title']), 'on' if r['enabled'] else 'off', e(etat),
        e(r['objective']), e('envoi automatique' if _mode_auto_valable(cfg) else 'préparer et me faire confirmer'), e('SMS' if cfg['canal'] == 'sms' else 'appel'),
        int(cfg['delai']), lot, regl, accords, refus)


ETATS_AGENT = {'brouillon': 'Brouillon (test)', 'introuvable': 'Introuvable chez Bland', 'published': 'Publié', 'draft': 'Brouillon (test)'}


def _carte_agent(desk, prefix, role, r):
    a = desk.db.execute('SELECT * FROM bland56109_agents WHERE routine=?', (r['id'],)).fetchone()
    props = desk.db.execute("SELECT * FROM bland56109_propositions WHERE routine=? AND etat='a_valider' ORDER BY cree_le DESC LIMIT 20", (r['id'],)).fetchall()
    lis = ''.join('<li>%s %s</li>' % (e(p['resume']), _form('proposition/traiter', '<input type="hidden" name="id" value="%s">' % e(p['id'], quote=True),
                                                             'Relu', 'ax-btn ghost') if role in ('administrateur', 'avocat') else '') for p in props)
    etat = ETATS_AGENT.get(a['etat'], a['etat']) if a else 'Inconnu'
    return ('<div class="c5614-routine r52-card b109-agent" id="r52-%s"><div class="r52-head"><strong>%s</strong><span class="r52-badge r52-%s">%s</span></div>'
            '<span class="r52-meta">Agent %s · version %s · dernière vérification : %s · passage en lecture seule</span>%s</div>') % (
        e(r['id'], quote=True), e(r['title']), 'off' if (a and a['etat'] == 'introuvable') else 'on', e(etat), e(a['agent_ref'] if a else '—'),
        e(a['version'] if a else '—'), _date(a['derniere_verif'] if a else ''),
        ('<h4>Propositions à valider (rien n’est inscrit dans l’agenda sans vous)</h4><ul>%s</ul>' % lis) if lis else '')


# 5.6.109 (intégration I2) : « Crée un agent de prise de rendez-vous pour mon cabinet » dit à l'assistant. Reconnaissance DÉTERMINISTE (aucun
# modèle), courte demande seulement ; la réponse indique où ouvrir le parcours guidé (``preparer_agent_rdv``) : rien n'est créé ni confirmé.
_INTENTION_VERBE = re.compile(r'\b(cree|creer|creez|creons|cree[sz]?-moi|fais|faites|mets en place|mettre en place|mettez en place|configure|configurer|'
                              r'configurez|prepare|preparer|preparez|installe|installer|installez)\b')
_INTENTION_OBJET = re.compile(r'\bagents?\b.{0,60}\b(prise de )?(rendez[- ]vous|rdv)\b')


def intention_agent_rdv(question):
    from .common import fold
    texte = re.sub(r'\s+', ' ', fold(str(question or ''))).strip()
    return 0 < len(texte) <= 300 and bool(_INTENTION_VERBE.search(texte)) and bool(_INTENTION_OBJET.search(texte))


def mention_rappel(desk, rdv_id):
    """5.6.109 (I2) : mention de la fiche du rendez-vous (``agenda/evenement``) : dernier rappel au client constaté envoyé, ou envoi à vérifier.
    Lecture seule, après le contrôle des droits de la fiche ; rien sans preuve (une demande préparée ou en échec n'est pas mentionnée)."""
    if not _table(desk, 'bland56109_rappels'):
        return ''
    row = desk.db.execute("SELECT etat, canal, envoye_le FROM bland56109_rappels WHERE rdv=? AND etat IN ('envoye','a_verifier') "
                          'ORDER BY maj_le DESC LIMIT 1', (str(rdv_id or ''),)).fetchone()
    if row is None:
        return ''
    canal = 'SMS' if row['canal'] == 'sms' else 'appel'
    if row['etat'] == 'envoye':
        try:   # instant UTC du constat, dit dans le fuseau de l'agenda du cabinet
            from .agenda5634 import _tz
            quand = datetime.fromisoformat(str(row['envoye_le'])).astimezone(_tz(desk)).strftime('%d/%m/%Y à %H:%M')
        except (ValueError, TypeError):
            quand = 'jour inconnu'
        return 'Rappel envoyé au client le %s (%s, Bland).' % (quand, canal)
    return 'Rappel au client : envoi à vérifier (%s, Bland).' % canal


def reponse_intention(desk, question):
    """Réponse sans modèle du fil de l'assistant, ou ``None`` si la demande n'est pas celle-là. Lecture seule : aucun appel à Bland,
    aucune demande préparée ; le parcours se fait dans Aujourd'hui, l'avocat confirme ensuite la création chez Bland."""
    if not intention_agent_rdv(question):
        return None
    try:
        pret = utilisable(desk)
    except Exception:   # noqa: BLE001 — état illisible : le chemin des réglages est indiqué
        pret = False
    if pret:
        texte = ('Je ne crée rien moi-même. Le parcours guidé est prêt : ouvrez Aujourd’hui › Mes routines | Mes agents IA, puis « Créer un agent '
                 'de prise de rendez-vous ». Il vous demande le nom du cabinet, les horaires, les types de rendez-vous et le numéro ; l’agent est '
                 'ensuite créé chez Bland en brouillon, seulement après votre confirmation.')
    else:
        texte = ('Je ne crée rien moi-même. Reliez d’abord Bland : Paramètres › Connexions API, carte « Bland — téléphonie par IA » (clé, '
                 '« Tester la connexion », « Activer le connecteur »). Le parcours « Créer un agent de prise de rendez-vous » apparaît ensuite dans '
                 'Aujourd’hui › Mes routines | Mes agents IA ; la création chez Bland reste soumise à votre confirmation.')
    return {'answer': texte, 'source_ids': [], 'limits': [], 'proposed_actions': [], 'sources': [], 'conversation': True, 'connecteur': True,
            'parcours': 'agent_rdv'}


def _form_agent(role):
    if role not in ('administrateur', 'avocat'):
        return ''
    return ('<details class="b109-creer" id="b109-creer"><summary class="ax-btn ghost">Créer un agent de prise de rendez-vous</summary>'
            '<form class="m5-form b109-form" data-api="%sagent/creer" data-reload><p class="cx52-note">L’agent est créé chez Bland en brouillon, après votre '
            'confirmation. Il se présente comme l’assistant automatisé du cabinet, ne donne aucun conseil juridique ni aucune information sur un dossier. '
            'La publication et le branchement sur un numéro entrant sont des confirmations distinctes.</p>'
            '<label>Nom du cabinet<input name="cabinet" maxlength="120"></label><label>Horaires<input name="horaires" maxlength="300"></label>'
            '<label>Types de rendez-vous<input name="types" maxlength="300"></label><label>Numéro du cabinet<input name="telephone" maxlength="40"></label>'
            '<label>Voix (facultatif)<input name="voix" maxlength="80"></label><label>Ton (facultatif)<input name="ton" maxlength="120"></label>'
            '<label class="r52-day"><input type="checkbox" name="enregistrement"> Enregistrer les appels (annoncé à l’appelant)</label>'
            '<label class="r52-day"><input type="checkbox" name="transcription"> Conserver les transcriptions</label>%s'
            '<button class="ax-btn" type="submit">Préparer l’agent</button><p role="status" class="m5-status"></p></form></details>') % (PREFIXE, _cle_html())


def cartes(desk, prefix, owner, role, compact):
    """Cartes des routines de ce lot (rappel de rendez-vous, agents IA) avec leur état réel ; aucune écriture."""
    from . import routines5652 as rt
    if not _table(desk, 'routines5652'):
        return [], []
    rows = desk.db.execute("SELECT * FROM routines5652 WHERE deleted_at IS NULL AND task_type IN ('rappel_rdv','agent_ia') ORDER BY task_type DESC, created_at").fetchall()
    cards, strip = [], []
    for r in rows:
        cards.append(_carte_rappel(desk, prefix, role, r) if r['task_type'] == 'rappel_rdv' else _carte_agent(desk, prefix, role, r))
        etat = ('Activée' if r['enabled'] else 'À activer') if r['task_type'] == 'rappel_rdv' else ('Agent IA' if r['enabled'] else 'En pause')
        strip.append('<li class="r99-routine b109-ligne%s"><span class="r99-etat">%s</span><span class="r99-nom">%s</span><span class="r99-heure">%s</span></li>' % (
            '' if r['enabled'] else ' r99-pause', '' if r['enabled'] else '⏸', e(r['title']), e(etat)))
    if utilisable(desk):
        cards.append('<div class="c5614-routine r52-card b109-nouvel-agent">%s</div>' % _form_agent(role))
    return cards, strip


def monter_cartes(desk, prefix, owner, role, compact, cards, strip):
    """Point de montage du bloc des routines d'Aujourd'hui (``web5652.routines_block``) ; une panne est dite, jamais silencieuse."""
    try:
        c, s = cartes(desk, prefix, owner, role, compact)
    except Exception:   # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception('routines Bland')
        c, s = ['<div class="c5614-routine r52-card"><p class="d51-warn" role="alert">Agents IA et rappels : affichage momentanément indisponible.</p></div>'], []
    cards.extend(c)
    strip.extend(s)
    return cards, strip


# ------------------------------------------------------------------------------------------------ routes m56109/bland/
LECTURES = ('etat', 'outils', 'demande/fiche')
ECRITURES_ADMIN = ('cle', 'tester', 'activer', 'retirer')
ECRITURES_AVOCAT = ('demande/confirmer', 'demande/annuler', 'demande/rapprocher', 'demande/constater', 'consulter', 'agent/creer',
                    'rappel/regler', 'rappel/confirmer-lot', 'rappel/accord', 'proposition/traiter')
ECRITURES_TOUS = ('demande/preparer',)
STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden',
           'cle_reutilisee': '409 Conflict', 'version_en_conflit': '409 Conflict', 'fiche_modifiee_depuis_affichage': '409 Conflict',
           'requete_trop_longue': '413 Payload Too Large', 'registre_identites_illisible': '503 Service Unavailable', 'jeton_service_refuse': '403 Forbidden'}
MESSAGES = {'introuvable': 'Demande introuvable.', 'role_insuffisant': 'Votre rôle ne permet pas cette action.',
            'jeton_service_refuse': 'Un jeton de service (sans identité) ne confirme rien et ne règle rien : seul l’avocat connecté le peut.',
            'mode_local_strict': 'Mode local strict : la connexion à Bland est refusée (aucun appel externe).',
            'connecteur_bland_desactive': 'Le connecteur Bland est désactivé : activez-le dans Paramètres › Connexions API.',
            'connecteur_bland_non_pret': 'La connexion Bland n’est pas prête : testez-la dans Paramètres › Connexions API.',
            'cle_bland_absente': 'Aucune clé Bland enregistrée.', 'cle_bland_invalide': 'Clé invalide : collez la clé d’organisation telle qu’affichée par Bland.',
            'automatismes_en_pause': 'IA en pause : aucune action n’est transmise. La consultation reste possible.',
            'confirmation_humaine_requise': 'Seul l’avocat confirme, depuis l’interface.', 'confirmation_bland_requise': 'Confirmation de l’avocat requise.',
            'relecture_requise': 'Cochez la case de relecture avant de confirmer.', 'cle_requise': 'Demande incomplète : rechargez la page.',
            'cle_reutilisee': 'Cette demande a déjà servi pour d’autres arguments : une nouvelle confirmation est nécessaire.',
            'fiche_modifiee_depuis_affichage': 'La demande a changé depuis son affichage : relisez-la.',
            'version_en_conflit': 'La demande a changé depuis son affichage : rechargez la page.',
            'outil_de_lecture_sans_demande': 'Cet outil ne fait que lire : utilisez « Consulter ».',
            'outil_a_effet_demande_requise': 'Cet outil a un effet : il demande une confirmation.',
            'outil_bland_absent_de_la_liste': 'Outil absent de la liste de Bland : testez de nouveau la connexion.',
            'pseudo_identite_residuelle': 'Contrôle avant l’envoi : une identité connue du cabinet figure dans un champ qui ne s’affiche pas en entier. Rien n’a été préparé.',
            'registre_identites_illisible': 'Le registre des identités du cabinet est illisible : rien n’est transmis.',
            'requete_trop_longue': 'Demande trop longue.', 'origine_ou_csrf_refuse': 'Session expirée ou origine refusée : rechargez la page.',
            'ecran_envoi_auto_non_lu': 'Cochez « J’ai lu ce qui partira sans moi » pour choisir l’envoi automatique.',
            'lot_vide': 'Aucun rappel à envoyer.', 'outil_creation_agent_absent': 'Aucun outil de création d’agent dans la liste de Bland.',
            'outil_creation_agent_non_reconnu': 'L’outil de création d’agent de Bland n’a pas la forme attendue : rien n’a été préparé.',
            'modele_rappel_variable_inconnue': 'Le modèle contient une variable inconnue.', 'modele_rappel_longueur': 'Modèle trop court ou trop long.'}


def allowed(role, method, api):
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):].strip('/') if reste.startswith(PREFIXE) else ''
    if role not in ('administrateur', 'avocat', 'assistant'):
        return False
    if method in ('GET', 'HEAD'):
        return reste in LECTURES
    if method != 'POST':
        return False
    if reste in ECRITURES_ADMIN:
        return role == 'administrateur'
    if reste in ECRITURES_AVOCAT:
        return role in ('administrateur', 'avocat')
    return reste in ECRITURES_TOUS


def _json(data, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(data, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def _recu(desk, acteur, cle, route, data, faire):
    """Clé d'idempotence de chaque action : même clé, même demande = même reçu ; même clé, autre demande = 409."""
    cle = str(cle or '')
    if not CLE.match(cle):
        raise Stop('cle_requise')
    empreinte = _sha(route, json.dumps({k: v for k, v in data.items() if k not in ('cle', 'api_key')}, sort_keys=True, default=str),
                     _sha(str(data.get('api_key') or '')))
    row = desk.db.execute('SELECT empreinte, resultat FROM bland56109_ops WHERE acteur=? AND cle=?', (acteur, cle)).fetchone()
    if row:
        if row['empreinte'] != empreinte:
            raise Stop('cle_reutilisee')
        return {**json.loads(row['resultat']), 'rejoue': True}
    out = faire()
    desk.db.execute('INSERT OR REPLACE INTO bland56109_ops(acteur, cle, empreinte, resultat, cree_le) VALUES(?,?,?,?,?)',
                    (acteur, cle, empreinte, json.dumps(out, ensure_ascii=False, default=str)[:20000], _maintenant()))
    desk.db.commit()
    return out


def _vrai(v):
    return str(v).lower() in ('true', 'on', '1', 'oui')


def _post(desk, owner, role, reste, d):
    if reste == 'cle':
        return enregistrer_cle(desk, owner, role, d.get('api_key'))
    if reste == 'tester':
        out = tester(desk, owner, role)
        return {**out, 'message': ('Connexion établie : %d outil(s) découvert(s).' % out['outils']) if out['ok'] else
                'Échec du test : %s.' % ETATS.get(out['etat'], out['etat'])}
    if reste == 'activer':
        return {**activer(desk, owner, role, _vrai(d.get('on'))), 'message': 'Connecteur activé.' if _vrai(d.get('on')) else 'Connecteur désactivé.'}
    if reste == 'retirer':
        return {**retirer(desk, owner, role), 'message': 'Clé retirée.'}
    if reste == 'demande/preparer':
        out = preparer(desk, owner, role, d.get('outil'), d.get('arguments') if isinstance(d.get('arguments'), dict) else {}, d.get('cle'),
                       declencheur=str(d.get('declencheur') or 'assistant'))
        return {**out, 'message': 'Demande préparée : à confirmer par l’avocat.'}
    if reste == 'demande/confirmer':
        out = confirmer(desk, owner, role, str(d.get('id') or ''), d.get('version') or 0, d.get('empreinte'), _vrai(d.get('relu')))
        return {**out, 'message': out['etat_libelle']}
    if reste == 'demande/annuler':
        return {**annuler(desk, owner, role, str(d.get('id') or '')), 'message': 'Demande annulée.'}
    if reste == 'demande/rapprocher':
        return {**rapprocher(desk, owner, role, str(d.get('id') or '')), 'noreload': True, 'message': 'Liste des appels lue (données).'}
    if reste == 'demande/constater':
        return {**constater(desk, owner, role, str(d.get('id') or ''), str(d.get('constat') or '')), 'message': 'Constat enregistré.'}
    if reste == 'consulter':
        return {**consulter(desk, owner, role, d.get('outil'), d.get('arguments') if isinstance(d.get('arguments'), dict) else {}), 'noreload': True}
    if reste == 'agent/creer':
        out = preparer_agent_rdv(desk, owner, role, d, d.get('cle'))
        if 'questions' in out:
            return {**out, 'noreload': True, 'message': out['message'] + ' ' + ' '.join(out['questions'])}
        return {**out, 'message': 'Agent préparé : confirmez sa création dans « Demandes à confirmer » (Paramètres › Connexions API) ou depuis la file À décider.'}
    if reste == 'rappel/regler':
        return {**regler_rappel(desk, owner, role, d), 'message': 'Réglages enregistrés.'}
    if reste == 'rappel/confirmer-lot':
        return {**confirmer_lot(desk, owner, role, str(d.get('jour') or ''), d.get('empreinte'), _vrai(d.get('relu'))), 'message': 'Lot traité.'}
    if reste == 'rappel/accord':
        return {**noter_accord(desk, owner, role, str(d.get('contact') or ''), _vrai(d.get('sms')), _vrai(d.get('appel')), str(d.get('canal') or '')),
                'message': 'Accord enregistré.'}
    if reste == 'proposition/traiter':
        cur = desk.db.execute("UPDATE bland56109_propositions SET etat='traitee',traite_le=?,traite_par=? WHERE id=? AND etat='a_valider'",
                              (_maintenant(), owner, str(d.get('id') or '')))
        desk.db.commit()
        if not cur.rowcount:
            raise Stop('introuvable')
        return {'message': 'Proposition relue.'}
    raise Stop('introuvable')


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, role = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    try:
        if reste not in LECTURES + ECRITURES_ADMIN + ECRITURES_AVOCAT + ECRITURES_TOUS:
            raise Stop('introuvable')
        if not allowed(role, method, name):
            raise Stop('role_insuffisant' if method == 'POST' else 'introuvable')
        if method in ('GET', 'HEAD'):
            if reste == 'etat':
                return _json(vue(desk))
            if reste == 'outils':
                cache = dernier_releve(desk)
                return _json({'revision': cache['revision'] if cache else 0, 'outils': [
                    {k: t[k] for k in ('name', 'classe', 'engageant', 'motif', 'libelle')} for t in (cache or {}).get('outils', [])]})
            return _json(fiche(desk, owner, role, str(args.get('id') or '')))
        # 5.6.109 (relecture) : le jeton de service (Bearer sans identité, lu comme membres5650.gate) n'écrit rien ici : ni confirmation, ni
        # réglage du rappel, ni aucune autre action
        if str(env.get('HTTP_AUTHORIZATION') or '').startswith('Bearer ') and not env.get('HTTP_X_AXIORHUB_USER') and not env.get('axiorhub.jeton5688'):
            raise Stop('jeton_service_refuse')
        try:
            longueur = int(env.get('CONTENT_LENGTH') or 0)
        except ValueError:
            longueur = MAX_POST + 1
        if longueur > MAX_POST:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        d = check_post(env, auth)
        with requete_humaine(owner):
            return _json(_recu(desk, owner, d.get('cle'), reste, d, lambda: _post(desk, owner, role, reste, d)))
    except Stop as ex:
        code = str(ex).split(':')[0]
        return _json({'error': code, 'message': MESSAGES.get(code, 'Demande refusée : %s.' % code.replace('_', ' '))},
                     STATUTS.get(code, '422 Unprocessable Entity'))
