"""5.6.109 (lot S, F11) : sources publiques sans clé préconfigurées, activées dès la première ouverture puis vérifiées automatiquement.

Raccordement des contrats de ``reference/sources_publiques109.py`` (dossier technique) aux services existants, sans second catalogue ni
second registre : les fiches restent celles de ``sources5632.PROVIDERS``, les connexions les entrées ``lawve:item:<id>`` du registre
``extensions364`` (même coffre, même session MCP), l'activation effective le seul ``sources5632.enable``.

- **Préconfiguration** (``ensure_schema``, appelée par ``Desk._prepare``, donc au démarrage, jamais pendant un GET) : les seules entrées
  MANQUANTES des sources publiques qualifiées sans compte ni clé (``PRECONFIGUREES``) sont créées, sans réseau, avec la demande
  d'activation (« Activé ») ; l'activation effective attend la preuve. Une entrée existante (même désactivée, même avec une adresse ou une
  clé propres) n'est jamais touchée ; une entrée déjà préconfigurée une fois n'est jamais recréée (repère durable par fournisseur) :
  une migration rejouée reste sans effet, une source supprimée ou désactivée par l'utilisateur le reste.
- **Qualification automatique** (``planifier``, appelée par la surveillance ``watch430`` dans le processus de service) : au plus
  ``MAX_PAR_PASSAGE`` sources par passage, réservation durable (bail) pour qu'une seconde fenêtre ou un second travailleur ne double pas
  la sonde, délai progressif (10 min, 1 h, 6 h, 24 h) — jamais de contrôle permanent. Pour chaque source : autorisation relue (demande
  toujours présente et de même révision, IA en pause, mode local strict, accord d'usage, politique des données), sonde réelle
  (``sources5632.verify`` en profondeur « complet » : recherche neutre et bornée, aucune donnée du cabinet), autorisation relue de
  nouveau, puis ``sources5632.enable`` avec la révision de la demande qualifiée. Une connexion seule n'active rien ; une panne garde la
  demande et dit « Hors ligne » ; un index étranger absent dit « Corpus absent » (le connecteur installé n'est pas le fonds présent).
  Hors processus de service (outils, essais), aucune sonde réseau ne part : ``common.EGRESS_GUARD`` n'est posé que par le service.
- **data.gouv.fr** : opérations revues (``operations_donnees`` de la fiche), normaliseur propre (``normaliser_datagouv``) qui garde le texte
  et les références ; une réponse non reconnue est « Indéterminée », jamais « zéro résultat » ; ``consulter`` passe par la politique des
  données et rend la provenance (producteur, jeu, ressource, adresse, dates, licence). Ce n'est jamais une autorité juridique.
- **États** (``etat_source``) : Préconfiguré, Activé — vérification en cours, Prêt, Connexion vérifiée, Outils à valider, Hors ligne,
  Indisponible, Quota atteint, Corpus absent, Bloquée par le mode local strict, Restreint par la politique, IA en pause, Désactivé,
  Compte requis, Non configuré, À vérifier. Calculés sans aucune écriture (R05).
- **Revérifier** (``m56109/sources/reverifier``, administrateur, POST protégé, clé d'idempotence) : remet seulement la source en tête de
  la file de qualification ; la sonde part du processus de service, jamais du fil de la page.
"""
from copy import deepcopy
from datetime import datetime, timezone
from html import escape as e
import hashlib
import json
import re
import time
import uuid

from .common import Stop

PRECONFIGUREES = ('datagouv', 'justicelibre', 'de_bund', 'es_boe', 'gb_legislation', 'ca_federal', 'ca_quebec')
ORIGINE = 'catalogue109'
REPERE = 'sources_publiques56109:preconfigurees'
DELAIS = (600, 3600, 6 * 3600, 86400)      # délai progressif entre deux sondes d'une même source qui n'est pas prête
MAX_PAR_PASSAGE = 2
BAIL = 300                                  # secondes : réservation d'une sonde (une seule fenêtre, un seul travailleur à la fois)
PREFIXE = 'm56109/sources/'
LECTURES = ('etat',)
ECRITURES = ('reverifier',)
MAX_POST = 4000
CLE = re.compile(r'^[A-Za-z0-9_.:-]{8,120}$')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS sources_publiques56109_sondes(pid TEXT PRIMARY KEY, revision_demande INTEGER NOT NULL DEFAULT 0,
  essais INTEGER NOT NULL DEFAULT 0, prochain_essai REAL NOT NULL DEFAULT 0, bail_jusqua REAL NOT NULL DEFAULT 0, titulaire TEXT NOT NULL DEFAULT '',
  dernier_etat TEXT NOT NULL DEFAULT '', derniere_sonde TEXT NOT NULL DEFAULT '', maj_le TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS sources_publiques56109_ops(acteur TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL, resultat TEXT NOT NULL,
  cree_le TEXT NOT NULL, PRIMARY KEY(acteur, cle));
'''
ETATS = {
    'preconfiguree': 'Préconfiguré : vérification à venir',
    'verification_en_cours': 'Activé : vérification en cours',
    'pret': 'Prêt : recherche vérifiée',
    'connexion_verifiee': 'Connexion vérifiée : recherche à vérifier',
    'outils_a_valider': 'Connexion vérifiée : outils à valider',
    'hors_ligne': 'Hors ligne : nouvel essai prévu',
    'indisponible': 'Indisponible temporairement',
    'refuse': 'Accès refusé par le service',
    'quota': 'Quota atteint : nouvel essai plus tard',
    'corpus_absent': 'Corpus absent : textes à charger',
    'bloque_local_strict': 'Bloquée par le mode local strict',
    'restreint_politique': 'Restreint par la politique du cabinet',
    'usage_non_autorise': 'Usage non autorisé',
    'suspendu': 'IA en pause : vérification suspendue',
    'desactive': 'Désactivé',
    'compte_requis': 'Compte requis',
    'non_configure': 'Non configuré',
    'a_verifier': 'À vérifier (fiche conditionnelle)',
}
HORS_LIGNE = ('connexion_http_indisponible', 'delai_http_depasse', 'hote_mcp_introuvable', 'reponse_mcp_illisible')
REFUS = ('http_401', 'http_403', 'erreur_mcp_distante')
STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden',
           'cle_reutilisee': '409 Conflict', 'requete_trop_longue': '413 Payload Too Large'}
MESSAGES = {'introuvable': 'Source introuvable.', 'role_insuffisant': 'Seul un administrateur règle les sources du cabinet.',
            'cle_reutilisee': 'Cette demande a déjà servi pour une autre action : rechargez la page.',
            'cle_requise': 'Demande incomplète : rechargez la page.', 'requete_trop_longue': 'Demande trop longue.',
            'demande_activation_absente': 'Cette source n’est pas activée : cochez son activation avant de la revérifier.',
            'origine_ou_csrf_refuse': 'Session expirée ou origine refusée : rechargez la page.'}


def _maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


# ================================================================================ contrats (reference/sources_publiques109.py)
def preconfigurer(catalogue, existantes, ids):
    """Fiches manquantes, qualifiées et sans authentification : demande d'activation, ``enabled`` faux jusqu'à la preuve. Une entrée
    existante, même désactivée, n'est jamais remplacée ; rien n'est modifié en place (copie)."""
    resultat = deepcopy(existantes)
    for pid in dict.fromkeys(ids):
        if pid in resultat:
            continue
        fiche = catalogue.get(pid)
        if not fiche or not fiche.get('qualified'):
            continue
        if fiche.get('default_auth') != 'none' or 'none' not in fiche.get('auth', ()):
            continue
        resultat[pid] = {'provider': pid, 'endpoint': fiche.get('endpoint', ''), 'transport': fiche['transport'], 'auth_type': 'none',
                         'secret_file': '', 'activation_demandee': True, 'enabled': False, 'status': 'verification_en_cours', 'origine': ORIGINE}
    return resultat


def activer_par_defaut(plan, *, autoriser, qualifier, activer):
    """Enchaînement sans clic : autorisation, sonde réelle, autorisation relue, activation. Une demande retirée, une source déjà active
    ou un état bloquant n'appellent rien ; aucun rejeu ici (le travailleur relit le registre avant tout nouvel essai)."""
    resultat = deepcopy(plan)
    for pid, item in resultat.items():
        if not item.get('activation_demandee') or item.get('enabled'):
            continue
        etat = autoriser(pid)
        if etat != 'autorise':
            item['status'] = etat
            continue
        etat = qualifier(pid)
        if etat != 'operationnel':
            item['status'] = etat
            continue
        etat = autoriser(pid)
        if etat != 'autorise':
            item['status'] = etat
            continue
        activer(pid)
        item.update(enabled=True, status='operationnel')
    return resultat


def etat_utilisation(fiche, item, *, pause, local_strict, usage_autorise, preuve_valide, capacite, index_present=False):
    """État explicatif pour un appel (pas la carte d'accueil) : pause, fiche, entrée, mode local strict, usage, corpus, preuve, capacité."""
    if pause:
        return 'suspendu'
    if not fiche.get('qualified'):
        return 'a_qualifier'
    if not item:
        return 'non_configure'
    if not item.get('enabled'):
        return 'desactive'
    local = fiche.get('transport') == 'integre' and item.get('transport') == 'integre'
    if local_strict and not local:
        return 'bloque_local_strict'
    if not usage_autorise:
        return 'usage_non_autorise'
    if local and not index_present:
        return 'corpus_absent'
    test = item.get('last_test') or {}
    if not preuve_valide or test.get('status') != 'ok':
        return 'a_tester'
    if (test.get('capacites') or {}).get(capacite) not in ('ok', 'vide'):
        return 'outils_a_valider'
    return 'operationnel'


def permissions(item, local_strict):
    """Correspondance testée avec le champ historique ``external_data_allowed`` (accord d'usage de la fiche) : pour un adaptateur
    intégré, il autorise l'usage de l'index LOCAL ; pour une source distante, il autorise l'envoi d'une requête, sauf en local strict."""
    item = item or {}
    integre = item.get('transport') == 'integre'
    accord = bool(item.get('external_data_allowed'))
    return {'index_local': integre and accord, 'requete_externe': (not integre) and accord and not local_strict,
            'synchronisation_internet': integre and accord and not local_strict}


# ================================================================================ schéma et préconfiguration (démarrage)
def eligible(pid):
    from .sources5632 import PROVIDERS
    p = PROVIDERS.get(pid) or {}
    return pid in PRECONFIGUREES and bool(p.get('qualified')) and p.get('default_auth') == 'none' and 'none' in p.get('auth', ())


def ensure_schema(desk):
    """``Desk._prepare`` : tables du lot, puis préconfiguration des seules entrées manquantes (aucun réseau, aucune activation effective)."""
    noms = re.findall(r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)', SCHEMA)
    existants = {r[0] for r in desk.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if any(n not in existants for n in noms):
        if desk.db.in_transaction:
            desk.db.commit()
        desk.db.executescript(SCHEMA)
        desk.db.commit()
    preconfigurer_installation(desk)


def preconfigurer_installation(desk):
    """Crée les entrées manquantes une seule fois par fournisseur (repère durable) ; rend les identifiants créés."""
    from . import sources5632 as S
    deja = set(desk.settings(REPERE, []) or [])
    a_faire = [pid for pid in PRECONFIGUREES if pid not in deja and eligible(pid)]
    if not a_faire:
        return []
    existantes = {pid: S._item(desk, pid) for pid in a_faire if S._item(desk, pid)}
    plan = preconfigurer(S.PROVIDERS, existantes, a_faire)
    crees = []
    for pid in a_faire:
        if pid not in existantes and pid in plan:
            S.connect(desk, pid, allow_external=True, origine_activation=ORIGINE)
            crees.append(pid)
        deja.add(pid)   # entrée existante ou créée : plus jamais recréée ni modifiée par une préconfiguration
    desk.setting(REPERE, sorted(deja))
    if crees:
        desk.audit('sources_publiques56109_preconfigurees', {'sources': crees})
    return crees


# ================================================================================ qualification automatique (processus de service)
def _local_strict(desk):
    from .selection_sources5697 import local_strict
    return local_strict(desk)


def _pause(desk):
    if desk.settings('missions567:pause', False):
        return True
    try:
        from .arret5643 import global_paused
        return bool(global_paused(desk))
    except Exception:   # noqa: BLE001 — contrôle illisible : la sonde attend (jamais de sonde parce que le contrôle a échoué)
        return True


def autoriser(desk, pid, revision_demande):
    """Relu avant la sonde et avant l'activation ; aucun cache."""
    from . import sources5632 as S
    item = S._item(desk, pid)
    if not item or not item.get('activation_demandee') or int(item.get('activation_revision') or 0) != int(revision_demande):
        return 'demande_retiree'
    if _pause(desk):
        return 'suspendu'
    integre = item.get('transport') == 'integre'
    strict = _local_strict(desk)
    if strict and not integre:
        return 'bloque_local_strict'
    droits = permissions(item, strict)
    if not (droits['index_local'] if integre else droits['requete_externe']):
        return 'usage_non_autorise'
    if not integre:
        try:
            from . import data_policy5689
            decision = data_policy5689.evaluer(desk.c, 'outil_mcp', pid, finalite='qualification', acteur='service', role='administrateur', journal=False)
            if not decision['allowed']:
                return 'restreint_politique'
        except Exception:   # noqa: BLE001 — politique illisible : bloqué, jamais ouvert
            return 'restreint_politique'
    return 'autorise'


def qualifier(desk, pid, appel=None):
    """Sonde réelle par le test existant (profondeur « complet ») ; état de capacité, jamais une simple poignée de main."""
    from . import sources5632 as S
    try:
        out = S.verify(desk, pid, 'complet', appel)
    except Stop as ex:
        code = str(ex).split(':')[0]
        return 'demande_retiree' if code == 'revision_source_perimee' else 'indisponible'
    if out.get('status') != 'ok':
        code = str(out.get('error') or '').split(':')[0]
        if code in HORS_LIGNE:
            return 'hors_ligne'
        if code in REFUS:
            return 'refuse'
        if code == 'http_429':
            return 'quota'
        return 'indisponible'
    caps = out.get('capacites') or {}
    if any(v == 'corpus_absent' for v in caps.values()):
        return 'corpus_absent'
    if caps and all(v in ('ok', 'vide') for v in caps.values()):
        return 'operationnel'
    return 'outils_a_valider'


def _reserver(desk, pid, revision, maintenant, titulaire):
    """Bail durable : une seule sonde à la fois par source, quel que soit le processus ; rend vrai si la sonde est due et réservée."""
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('INSERT OR IGNORE INTO sources_publiques56109_sondes(pid, revision_demande, maj_le) VALUES(?,?,?)', (pid, revision, _maintenant()))
    row = desk.db.execute('SELECT * FROM sources_publiques56109_sondes WHERE pid=?', (pid,)).fetchone()
    if int(row['revision_demande']) != int(revision):   # nouvelle demande (réactivation, Revérifier) : délai progressif remis à zéro
        desk.db.execute('UPDATE sources_publiques56109_sondes SET revision_demande=?, essais=0, prochain_essai=0 WHERE pid=?', (revision, pid))
    cur = desk.db.execute('UPDATE sources_publiques56109_sondes SET bail_jusqua=?, titulaire=?, maj_le=? WHERE pid=? AND prochain_essai<=? AND bail_jusqua<=?',
                          (maintenant + BAIL, titulaire, _maintenant(), pid, maintenant, maintenant))
    desk.db.commit()
    return bool(cur.rowcount)


def _liberer(desk, pid, etat, maintenant):
    row = desk.db.execute('SELECT essais FROM sources_publiques56109_sondes WHERE pid=?', (pid,)).fetchone()
    essais = int(row['essais'] if row else 0) + 1
    prochain = 0 if etat == 'operationnel' else maintenant + DELAIS[min(essais - 1, len(DELAIS) - 1)]
    desk.db.execute('UPDATE sources_publiques56109_sondes SET essais=?, prochain_essai=?, bail_jusqua=0, titulaire=\'\', dernier_etat=?, derniere_sonde=?, '
                    'maj_le=? WHERE pid=?', (essais, prochain, etat, _maintenant(), _maintenant(), pid))
    desk.db.commit()
    return prochain


def _noter(desk, pid, revision, etat, prochain):
    """État de qualification conservé sur l'entrée, seulement si la demande est toujours la même (une désactivation entre-temps gagne)."""
    from . import sources5632 as S
    item = S._item(desk, pid)
    if not item or int(item.get('activation_revision') or 0) != int(revision):
        return
    item['qualification56109'] = {'etat': etat, 'at': _maintenant(), 'prochain_essai': datetime.fromtimestamp(prochain, timezone.utc).isoformat(timespec='seconds')
                                  if prochain else ''}
    S._save(desk, item)


def qualifier_et_activer(desk, pid, appel=None, maintenant=None, titulaire='service'):
    """Une source : réservation, autorisation, sonde, autorisation relue, activation par ``sources5632.enable``. Rend l'état final."""
    from . import sources5632 as S
    maintenant = time.time() if maintenant is None else maintenant
    item = S._item(desk, pid)
    if not item or not item.get('activation_demandee') or item.get('enabled'):
        return 'rien_a_faire'
    revision = int(item.get('activation_revision') or 0)
    if not _reserver(desk, pid, revision, maintenant, titulaire):
        return 'deja_reservee'
    etat = 'indisponible'
    try:
        def activer(_pid):
            S.enable(desk, pid, True, revision_demande=revision)
        plan = activer_par_defaut({pid: item}, autoriser=lambda _p: autoriser(desk, pid, revision),
                                  qualifier=lambda _p: qualifier(desk, pid, appel), activer=activer)
        etat = plan[pid].get('status') or 'indisponible'
    except Stop as ex:
        etat = 'demande_retiree' if str(ex).split(':')[0] == 'demande_activation_retiree' else 'indisponible'
    finally:
        prochain = _liberer(desk, pid, etat, maintenant)
    if etat != 'demande_retiree':
        _noter(desk, pid, revision, etat, prochain)
    desk.audit('sources_publiques56109_qualification', {'source': pid, 'etat': etat})
    return etat


def planifier(desk, stamp=None, appel=None):
    """Surveillance (``watch430``) : sources dont l'activation est demandée et pas encore effective, au plus ``MAX_PAR_PASSAGE`` par passage.
    Rien hors processus de service (aucun garde de sortie posé) sauf transport fourni explicitement (essais)."""
    from . import common
    from . import sources5632 as S
    if appel is None and common.EGRESS_GUARD is None:
        return []
    stamp = time.time() if stamp is None else stamp
    faits = []
    for pid in S.PROVIDERS:
        if len(faits) >= MAX_PAR_PASSAGE:
            break
        item = S._item(desk, pid)
        if not item or not item.get('activation_demandee') or item.get('enabled'):
            continue
        row = desk.db.execute('SELECT prochain_essai, bail_jusqua, revision_demande FROM sources_publiques56109_sondes WHERE pid=?', (pid,)).fetchone()
        if row and int(row['revision_demande']) == int(item.get('activation_revision') or 0) and (row['prochain_essai'] > stamp or row['bail_jusqua'] > stamp):
            continue
        faits.append((pid, qualifier_et_activer(desk, pid, appel, stamp)))
    return faits


# ================================================================================ data.gouv.fr : normaliseur et consultation
_EN_TETE = re.compile(r'(?im)^\s*found\s+(\d+)\s+dataset')
_BLOC = re.compile(r'(?m)^\s*(\d+)\.\s+(.+)$')
_CHAMP = re.compile(r'(?im)^\s*(id|url|organization|organisation|license|licence|last[_ ]update|updated|created)\s*:\s*(.+?)\s*$')


def _jeu(d):
    if not isinstance(d, dict):
        return None
    org = d.get('organization') or d.get('organisation') or {}
    jeu = {'id': str(d.get('id') or '')[:200], 'titre': str(d.get('title') or d.get('titre') or '')[:500],
           'producteur': str(org.get('name') if isinstance(org, dict) else org or '')[:300], 'url': str(d.get('page') or d.get('url') or '')[:500],
           'mise_a_jour': str(d.get('last_update') or d.get('last_modified') or '')[:40], 'licence': str(d.get('license') or d.get('licence') or '')[:200]}
    return jeu if jeu['id'] or jeu['titre'] else None


def normaliser_datagouv(result):
    """{'etat': ok | partiel | vide | indetermine | erreur, 'jeux': [...], 'texte': texte d'origine borné}. Formes acceptées : contenu
    structuré, JSON dans les blocs texte, texte documentaire du serveur officiel (« Found N dataset(s) … ID: … URL: … »)."""
    from . import mcp5614
    if not isinstance(result, dict):
        return {'etat': 'indetermine', 'jeux': [], 'texte': ''}
    texte = mcp5614.text_from(result)[:20000]
    if result.get('isError'):
        return {'etat': 'erreur', 'jeux': [], 'texte': texte}
    valeur = result.get('structuredContent')
    if valeur is None:
        try:
            valeur = json.loads(texte) if texte.strip()[:1] in ('{', '[') else None
        except ValueError:
            valeur = None
    if isinstance(valeur, (dict, list)):
        liste = valeur if isinstance(valeur, list) else next((valeur[k] for k in ('datasets', 'data', 'results', 'items') if isinstance(valeur.get(k), list)), None)
        if liste is None:
            return {'etat': 'indetermine', 'jeux': [], 'texte': texte}
        jeux = [j for j in (_jeu(x) for x in liste) if j]
        if not liste:
            return {'etat': 'vide', 'jeux': [], 'texte': texte}
        return {'etat': 'ok' if jeux and all(j['id'] and j['url'] for j in jeux) else ('partiel' if jeux else 'indetermine'), 'jeux': jeux, 'texte': texte}
    m = _EN_TETE.search(texte)
    blocs = list(_BLOC.finditer(texte))
    if m and int(m.group(1)) == 0 and not blocs:
        return {'etat': 'vide', 'jeux': [], 'texte': texte}
    jeux = []
    for i, b in enumerate(blocs):
        corps = texte[b.end():blocs[i + 1].start() if i + 1 < len(blocs) else len(texte)]
        champs = {k.lower().replace(' ', '_'): v for k, v in _CHAMP.findall(corps)}
        jeux.append({'id': champs.get('id', '')[:200], 'titre': b.group(2).strip()[:500], 'producteur': (champs.get('organization') or champs.get('organisation') or '')[:300],
                     'url': champs.get('url', '')[:500], 'mise_a_jour': (champs.get('last_update') or champs.get('updated') or '')[:40],
                     'licence': (champs.get('license') or champs.get('licence') or '')[:200]})
    if not m and not jeux:
        return {'etat': 'indetermine', 'jeux': [], 'texte': texte}
    if not jeux:
        return {'etat': 'indetermine', 'jeux': [], 'texte': texte}
    complet = all(j['id'] and j['url'] for j in jeux)
    return {'etat': 'ok' if complet else 'partiel', 'jeux': jeux, 'texte': texte}


def consulter(desk, operation, arguments, appel=None, acteur='cabinet', role='avocat'):
    """Opération revue de data.gouv.fr (jamais ``resources.query`` avant sa qualification), après les contrôles : source activée, pause,
    mode local strict, politique des données. Résultat normalisé avec provenance et date de consultation ; jamais une citation juridique."""
    from . import extensions364 as ext, mcp5614
    from . import sources5632 as S
    p = S.PROVIDERS['datagouv']
    outil = (p.get('operations_donnees') or {}).get(str(operation or ''))
    if not outil:
        raise Stop('operation_donnees_non_revue')
    item = S._item(desk, 'datagouv')
    if not item or not item.get('enabled'):
        raise Stop('source_desactivee')
    if _pause(desk):
        raise Stop('automatismes_en_pause')
    if _local_strict(desk):
        raise Stop('mode_local_strict')
    from .data_policy5689 import exiger
    exiger(desk.c, 'outil_mcp', 'datagouv', finalite='donnees_publiques', acteur=acteur, role=role,
           requete=json.dumps(arguments, ensure_ascii=False, default=str)[:2000])
    session = mcp5614.CallSession(item, appel or ext._mcp_call)
    session.initialize()
    specs = {t['name']: t for t in session.list_tools()}
    if outil not in specs:
        raise Stop('outil_datagouv_absent')
    if not isinstance(arguments, dict):
        raise Stop('arguments_invalides')
    if operation == 'datasets.search':
        args = mcp5614.search_arguments(specs[outil], str(arguments.get('query') or '')[:200], max(1, min(int(arguments.get('limit') or 5), 20)))
    else:
        args = mcp5614.text_arguments(specs[outil], str(arguments.get('id') or '')[:200])
    reponse = session.call_tool(outil, args)
    n = normaliser_datagouv(reponse)
    consulte = _maintenant()
    return {'operation': operation, 'outil': outil, 'etat': n['etat'], 'texte': n['texte'],
            'jeux': [{**j, 'consulte_le': consulte, 'source': 'data.gouv.fr', 'nature': 'donnee_publique'} for j in n['jeux']],
            'limite': 'Donnée publique avec sa provenance : ni texte de loi, ni preuve du fait litigieux par elle-même ; licence propre à chaque jeu.'}


# ================================================================================ affichage (aucune écriture)
def etat_source(desk, pid, p, item, row=None):
    """Code d'état de la carte (Paramètres et guide lisent le même registre)."""
    from .sources5632 import mode
    if not p.get('qualified'):
        return 'a_verifier'
    integre = p.get('transport') == 'integre'
    index = (row or {}).get('index') or {}
    if not item:
        if eligible(pid):
            return 'preconfiguree'
        return 'compte_requis' if mode(p) in ('cle', 'oauth') else 'non_configure'
    if item.get('enabled'):
        if integre and index and not index.get('present'):
            return 'corpus_absent'
        if not integre and _local_strict(desk):
            return 'bloque_local_strict'
        caps = (item.get('last_test') or {}).get('capacites') or {}
        return 'pret' if any(v in ('ok', 'vide') for v in caps.values()) else 'connexion_verifiee'
    if not item.get('activation_demandee'):
        return 'desactive'
    if item.get('auth_type') in ('bearer', 'oauth2') and not item.get('secret_file'):
        return 'compte_requis'
    if not integre and _local_strict(desk):
        return 'bloque_local_strict'
    if integre and index and not index.get('present'):
        return 'corpus_absent'
    etat = str((item.get('qualification56109') or {}).get('etat') or '')
    return etat if etat in ETATS and etat not in ('preconfiguree',) else 'verification_en_cours'


def _date(v):
    return e(str(v or '')[:16].replace('T', ' '))


def etat_html(p):
    """Ligne d'état de la carte commune (``web5632.carte_source``) ; bouton « Revérifier » (réparation facultative) pour une source dont
    l'activation est demandée et pas encore effective."""
    etat = p.get('etat_source') or 'non_configure'
    q = p.get('qualification') or {}
    detail = ''
    if q.get('at'):
        detail += ' <span class="s56109-detail">Dernière vérification : %s.</span>' % _date(q['at'])
    if q.get('prochain_essai') and not p.get('enabled'):
        detail += ' <span class="s56109-detail">Prochain essai : %s.</span>' % _date(q['prochain_essai'])
    if p.get('activation_demandee') and not p.get('enabled'):
        detail += ' <span class="s56109-detail">Activation demandée : la source sera utilisée dès que la vérification aura réussi.</span>'
    out = '<p class="s56109-etat" data-etat="%s" role="status">État : <strong>%s</strong>%s</p>' % (e(etat, quote=True), e(ETATS.get(etat, etat)), detail)
    if p.get('activation_demandee') and not p.get('enabled') and etat not in ('desactive', 'a_verifier'):
        out += ('<form class="m5-form s56109-reverifier" data-api="%sreverifier" data-reload><input type="hidden" name="provider" value="%s">'
                '<input type="hidden" name="cle" value="%s"><button class="ax-btn ghost" type="submit">Revérifier</button>'
                '<p role="status" class="m5-status"></p></form>') % (PREFIXE, e(p['id'], quote=True), uuid.uuid4().hex)
    return out


def installation_html(desk, csrf=''):
    """Guide d'installation : sources publiques déjà préparées (état réel) et accès étrangers facultatifs : mêmes fiches, mêmes champs et
    même service d'enregistrement que Paramètres › Connexions API (``connexions5652.save``) ; aucun champ de clé pour un service sans clé."""
    from .sources5632 import catalogue
    from . import connexions5652 as cx
    lignes = ''.join('<li><span>%s</span> — <strong>%s</strong></li>' % (e(r['name']), e(ETATS.get(r['etat_source'], r['etat_source'])))
                     for r in catalogue(desk) if r['id'] in PRECONFIGUREES)
    enregistres = {r['provider'] for r in desk.db.execute("SELECT provider FROM connexions5652 WHERE owner='cabinet'")} if desk.db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='connexions5652'").fetchone() else set()
    acces = ''
    for pid, s in cx.PROVIDERS_INTERNATIONAUX.items():
        if s.get('family') != 'international':
            continue
        champs = ''.join('<label>%s<input name="%s" %s maxlength="300"></label>' % (
            e(f['label']), e(f['name'], quote=True), 'type="password" autocomplete="new-password" placeholder="%s"' % ('enregistrée : laisser vide pour la conserver'
                                                                                                              if pid in enregistres else 'facultatif')
            if f['secret'] else 'autocomplete="off"') for f in s['fields'])
        acces += ('<li><strong>%s</strong> · <a href="%s" target="_blank" rel="noopener noreferrer">Obtenir mes accès</a> · <span class="muted">%s</span>'
                  '<form method="post" action="/installation" class="s56109-acces"><input type="hidden" name="csrf" value="%s">%s'
                  '<button name="op" value="acces56109_%s" class="ghost">Enregistrer cet accès</button></form></li>') % (
            e(s['label']), e(s['access'], quote=True), e(s['auth']), e(csrf, quote=True), champs, e(pid, quote=True))
    return ('<section class="notice s56109-installation"><h2>Sources publiques et accès étrangers</h2>'
            '<p>Les sources publiques sans clé sont déjà préparées et activées : le logiciel les vérifie seul, sans aucun clic. Rien n’est '
            'téléchargé à l’ouverture.</p><ul>%s</ul><h3>Accès étrangers (facultatif)</h3><p class="muted">Ignorez cette étape pour l’instant si '
            'vous le souhaitez : chaque accès se configure aussi plus tard dans Paramètres › Connexions API, une seule fois par service. '
            'Un accès enregistré n’est pas un accès vérifié : le test se fait ensuite, capacité par capacité.</p><ul>%s</ul></section>') % (lignes, acces)


def installation_action(desk, owner, form):
    """« Enregistrer cet accès » du guide : le service commun des connexions (coffre, génération, champ vide = secret conservé)."""
    from . import connexions5652 as cx
    pid = str(form.get('op', ''))[len('acces56109_'):]
    spec = cx.PROVIDERS_INTERNATIONAUX.get(pid) or {}
    if spec.get('family') != 'international':
        raise Stop('fournisseur_inconnu')
    data = {'provider': pid, 'portee': 'cabinet'}
    for f in spec['fields']:
        if str(form.get(f['name']) or '').strip():
            data[f['name']] = str(form[f['name']]).strip()
    existant = desk.db.execute("SELECT id, version FROM connexions5652 WHERE provider=? AND owner='cabinet'", (pid,)).fetchone()
    if existant:
        data.update(id=existant['id'], version=existant['version'])
    vue = cx.save(desk, owner, 'administrateur', data)
    return '%s : %s (non vérifié tant que le test n’a pas réussi).' % (spec['label'], cx.STATES.get(vue['etat'], vue['etat']))


# ================================================================================ routes m56109/sources/
def allowed(role, method, api):
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):].strip('/') if reste.startswith(PREFIXE) else ''
    if method in ('GET', 'HEAD'):
        return reste in LECTURES
    return method == 'POST' and reste in ECRITURES and role == 'administrateur'


def _json(data, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(data, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def reverifier(desk, acteur, pid, cle):
    """Remet la source en tête de la file de qualification (aucun réseau ici) ; même clé et même source : même reçu."""
    from . import sources5632 as S
    cle = str(cle or '')
    if not CLE.match(cle):
        raise Stop('cle_requise')
    if pid not in S.PROVIDERS:
        raise Stop('introuvable')
    empreinte = hashlib.sha256(('reverifier|' + pid).encode()).hexdigest()
    row = desk.db.execute('SELECT empreinte, resultat FROM sources_publiques56109_ops WHERE acteur=? AND cle=?', (acteur, cle)).fetchone()
    if row:
        if row['empreinte'] != empreinte:
            raise Stop('cle_reutilisee')
        return {**json.loads(row['resultat']), 'rejoue': True}
    item = S._item(desk, pid)
    if not item or not item.get('activation_demandee'):
        raise Stop('demande_activation_absente')
    desk.db.execute('INSERT OR IGNORE INTO sources_publiques56109_sondes(pid, revision_demande, maj_le) VALUES(?,?,?)',
                    (pid, int(item.get('activation_revision') or 0), _maintenant()))
    desk.db.execute('UPDATE sources_publiques56109_sondes SET prochain_essai=0, essais=0, maj_le=? WHERE pid=?', (_maintenant(), pid))
    out = {'provider': pid, 'message': 'Vérification demandée : elle part en arrière-plan, sans bloquer le logiciel.'}
    desk.db.execute('INSERT INTO sources_publiques56109_ops(acteur, cle, empreinte, resultat, cree_le) VALUES(?,?,?,?,?)',
                    (acteur, cle, empreinte, json.dumps(out, ensure_ascii=False), _maintenant()))
    desk.db.commit()
    desk.audit('sources_publiques56109_reverifier', {'source': pid, 'par': acteur})
    return out


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, role = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    try:
        if reste not in LECTURES + ECRITURES:
            raise Stop('introuvable')
        if not allowed(role, method, name):
            raise Stop('role_insuffisant' if method == 'POST' and reste in ECRITURES else 'introuvable')
        if method in ('GET', 'HEAD'):
            from .sources5632 import catalogue
            return _json({'sources': [{k: r[k] for k in ('id', 'name', 'etat_source', 'activation_demandee', 'enabled', 'qualification')}
                                      for r in catalogue(desk)]})
        try:
            longueur = int(env.get('CONTENT_LENGTH') or 0)
        except ValueError:
            longueur = MAX_POST + 1
        if longueur > MAX_POST:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        d = check_post(env, auth)
        return _json(reverifier(desk, owner, str(d.get('provider') or ''), d.get('cle')))
    except Stop as ex:
        code = str(ex).split(':')[0]
        return _json({'error': code, 'message': MESSAGES.get(code, 'Demande refusée.')}, STATUTS.get(code, '422 Unprocessable Entity'))
