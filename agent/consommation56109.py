"""5.6.109 (lot M, C12 et F18) : usage normalisé par fournisseur, coût calculé en micro-unités, relevé « Consommation et coûts ».

C12. Les fournisseurs ne comptent pas le cache de la même façon : compris dans l'entrée (Responses, Chat, Mistral, Ollama, Gemini) ou
ajouté à l'entrée (Messages). Le format est choisi par l'adaptateur de confiance (type du fournisseur réglé), jamais par le contenu de la
réponse. ``normaliser`` rend des catégories disjointes ; ``cout_tokens`` calcule en ``Decimal`` puis en micro-unités de la devise du tarif.
Une mesure absente reste inconnue (``None``, jamais zéro) ; un vrai zéro compte pour zéro ; un tarif manquant n'est pas un tarif nul ; le
raisonnement, déjà compris dans la sortie, n'est jamais ajouté une seconde fois. Aucun tarif réel n'est écrit ici : la grille est celle de
l'appel (décision de routage ou réglage du fournisseur), figée avec l'observation. L'historique n'est ni recalculé ni complété.

F18. ``releve`` projette en lecture seule (connexion SQLite ``mode=ro`` : aucune écriture, aucun appel de modèle, aucune requête à un
fournisseur) les observations (``conso_5682``), le registre du budget (``budget_reservations5689``, seule autorité sur les montants), les
générations locales (``ai_usage_v391``) et les quantités de service (``budget_quantites56109``). API, abonnement, IA locale et services
externes restent séparés par unité ; aucune case ne mélange dollars, crédits et jetons. Un collaborateur ne lit que ses propres demandes ;
un dossier réservé ou inconnu donne le même refus neutre.
"""
from datetime import datetime, time, timedelta, timezone
from decimal import ROUND_CEILING, Decimal, InvalidOperation
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3

from .common import Stop

# ------------------------------------------------------------------------------------------------ normalisation (C12)
CLES = ('entree', 'cache_lu', 'cache_ecrit', 'cache_ecrit_5m', 'cache_ecrit_1h', 'entree_hors_cache', 'sortie_facturable', 'raisonnement')
CATEGORIES = ('entree_hors_cache', 'cache_lu', 'cache_ecrit_5m', 'cache_ecrit_1h', 'sortie_facturable')   # disjointes : les seules additionnées
# Format d'usage déclaré par l'adaptateur pour chaque type de fournisseur réglé. Un type absent d'ici n'est pas qualifié : rien n'est calculé.
FORMATS = {'anthropic': 'anthropic_messages', 'chatgpt': 'openai_responses', 'mistral': 'mistral', 'openai': 'openai_chat', 'openrouter': 'openai_chat',
           'ollama': 'ollama'}
FORMATS['gemini'] = 'gemini_openai'   # 5.6.109 (lot E, F15) : connexion Gemini par la compatibilité OpenAI de Google (voir ``normaliser``)
NOMS_CHAT = (('prompt_tokens', 'input_tokens'), ('completion_tokens', 'output_tokens'), ('prompt_tokens_details', 'input_tokens_details'),
             ('completion_tokens_details', 'output_tokens_details'))
MESURES = {'complete': 'Complète', 'sans_detail_cache': 'Détail du cache non fourni', 'cache_non_declare': 'Cache non déclaré', 'partielle': 'Partielle', 'absente': 'Non fournie',
           'incoherente': 'Incohérente', 'non_qualifiee': 'Format non qualifié', '': 'Historique, avant la 5.6.109'}
ORIGINES = {'calcule_depuis_usage': 'Calculé depuis l’usage déclaré', 'majorant_cache_non_detaille': 'Majorant : détail du cache non fourni',
            'majorant_tarif_cache_absent': 'Majorant : tarif du cache non renseigné', 'forfait_separe': 'Forfait : pas de prix par appel',
            'cout_poste_non_mesure': 'Local : coût du poste non mesuré', 'usage_absent': 'Usage non fourni', 'usage_partiel': 'Usage partiel',
            'tarif_manquant': 'Tarif non renseigné', 'usage_incoherent': 'Usage incohérent', 'format_non_qualifie': 'Format non qualifié', '': 'Non enregistré'}
SANS_MESURE = ('absente', 'incoherente', 'non_qualifiee')


def _n(obj, cle):
    """Compteur tel que fourni : entier positif ou nul. Booléen, négatif, décimal ou texte : inconnu."""
    v = obj.get(cle) if isinstance(obj, dict) else None
    return v if type(v) is int and v >= 0 else None


def _detail(obj, nom, cle):
    return _n(obj.get(nom), cle) if isinstance(obj, dict) else None


def normaliser(format_usage, u, *, cache_ecriture_non_facturee=False):
    """Catégories disjointes de l'usage, selon le format déclaré par l'adaptateur. ``None`` : non fourni. ``ValueError`` : relevé incohérent
    ou format non qualifié. ``cache_ecriture_non_facturee`` n'est posé que par l'adaptateur, d'après un contrat (forfait, grille qualifiée)."""
    u = u if isinstance(u, dict) else {}
    r = dict.fromkeys(CLES)
    if format_usage in ('openai_responses', 'openai_chat', 'mistral'):
        responses = format_usage == 'openai_responses'
        total = _n(u, 'input_tokens' if responses else 'prompt_tokens')
        cache = _detail(u, 'input_tokens_details' if responses else 'prompt_tokens_details', 'cached_tokens')
        if format_usage == 'mistral' and total is not None and 'prompt_tokens_details' not in u:
            cache = 0                                  # absence = 0 : documenté pour le cache Mistral, pas inféré pour les autres schémas
        if total is not None and cache is not None and cache > total:
            raise ValueError('cache_superieur_entree')
        ecrit = _detail(u, 'input_tokens_details', 'cache_write_tokens') if responses else 0
        if responses and ecrit is None and cache_ecriture_non_facturee is True:
            ecrit = 0
        # le cache lu est un sous-ensemble de l'entrée : il en est retranché. Une écriture de cache à tarif propre doit être détaillée.
        r.update(entree=total, cache_lu=cache, cache_ecrit=ecrit,
                 entree_hors_cache=total - cache if total is not None and cache is not None and ecrit == 0 else None,
                 cache_ecrit_5m=0 if ecrit == 0 else None, cache_ecrit_1h=0 if ecrit == 0 else None,
                 sortie_facturable=_n(u, 'output_tokens' if responses else 'completion_tokens'),
                 raisonnement=_detail(u, 'output_tokens_details' if responses else 'completion_tokens_details', 'reasoning_tokens'))
    elif format_usage == 'anthropic_messages':
        normal, lu, ecrit = _n(u, 'input_tokens'), _n(u, 'cache_read_input_tokens'), _n(u, 'cache_creation_input_tokens')
        e5, e1 = _detail(u, 'cache_creation', 'ephemeral_5m_input_tokens'), _detail(u, 'cache_creation', 'ephemeral_1h_input_tokens')
        if ecrit == 0:
            e5 = e1 = 0
        if ecrit is None and e5 is not None and e1 is not None:
            ecrit = e5 + e1                            # total non répété, détail fourni : rien d'inventé
        if ecrit is not None and e5 is not None and e1 is not None and e5 + e1 != ecrit:
            raise ValueError('cache_ecrit_incoherent')
        # entrée ordinaire, cache lu et cache écrit sont trois catégories qui s'additionnent : aucune n'est retranchée d'une autre
        r.update(entree=normal + lu + ecrit if None not in (normal, lu, ecrit) else None, entree_hors_cache=normal, cache_lu=lu, cache_ecrit=ecrit,
                 cache_ecrit_5m=e5, cache_ecrit_1h=e1, sortie_facturable=_n(u, 'output_tokens'))
    elif format_usage == 'ollama':
        total, cache = _n(u, 'prompt_eval_count'), _n(u, 'prompt_eval_cached_count')
        if total is not None and cache is not None and cache > total:
            raise ValueError('cache_superieur_entree')
        r.update(entree=total, cache_lu=cache, entree_hors_cache=total - cache if total is not None and cache is not None else None,
                 sortie_facturable=_n(u, 'eval_count'))
    elif format_usage == 'gemini':
        total, cache = _n(u, 'promptTokenCount'), _n(u, 'cachedContentTokenCount')
        sortie, reflexion = _n(u, 'candidatesTokenCount'), _n(u, 'thoughtsTokenCount')
        if total is not None and cache is not None and cache > total:
            raise ValueError('cache_superieur_entree')
        r.update(entree=total, cache_lu=cache, entree_hors_cache=total - cache if total is not None and cache is not None else None,
                 cache_ecrit=0, cache_ecrit_5m=0, cache_ecrit_1h=0,
                 sortie_facturable=sortie + reflexion if sortie is not None and reflexion is not None else None, raisonnement=reflexion)
    elif format_usage == 'gemini_openai':
        # 5.6.109 (lot E, F15) : Gemini appelé par sa compatibilité OpenAI (``/chat/completions``), compteurs au format chat. Sources (documentation
        # Google relue le 19/11/2026 par l'auditeur ; aucun compte réel essayé ici) : page « generate-content », section UsageMetadata, de la référence d'API du site ai.google.dev —
        # « promptTokenCount » comprend le contenu en cache (cache COMPRIS dans l'entrée) ; « totalTokenCount » = entrée + candidats + entrée des
        # outils + réflexion (« thoughtsTokenCount », facturée en sortie et hors candidats). La page de compatibilité
        # (ai.google.dev/gemini-api/docs/openai) ne documente ni le détail du cache ni la place de la réflexion dans « completion_tokens » :
        # cache non déclaré (inconnu) sauf détail « cached_tokens » fourni ; sortie facturable = total − entrée (aucun outil n'est transmis),
        # inconnue sans total (jamais déduite de « completion_tokens » seul, qui peut omettre la réflexion).
        total, cache = _n(u, 'prompt_tokens'), _detail(u, 'prompt_tokens_details', 'cached_tokens')
        visible, somme = _n(u, 'completion_tokens'), _n(u, 'total_tokens')
        if total is not None and cache is not None and cache > total:
            raise ValueError('cache_superieur_entree')
        sortie = somme - total if somme is not None and total is not None else None
        if sortie is not None and (sortie < 0 or (visible is not None and visible > sortie)):
            raise ValueError('total_incoherent')
        r.update(entree=total, cache_lu=cache, entree_hors_cache=total - cache if total is not None and cache is not None else None,
                 cache_ecrit=0, cache_ecrit_5m=0, cache_ecrit_1h=0, sortie_facturable=sortie,
                 raisonnement=sortie - visible if sortie is not None and visible is not None else None)
    else:
        raise ValueError('format_usage_non_qualifie')
    if r['raisonnement'] is not None and r['sortie_facturable'] is not None and r['raisonnement'] > r['sortie_facturable']:
        raise ValueError('raisonnement_superieur_sortie')
    return r


def cout_tokens(usage, tarifs):
    """Micro-unités de la devise du tarif (prix par million × jetons) ; ``None`` si une catégorie nécessaire ou son tarif est inconnu.
    Les catégories sont disjointes : ni le raisonnement ni l'entrée totale ne s'y ajoutent. Taxes, outils, audio et forfaits : à part."""
    total = Decimal(0)
    for k in CATEGORIES:
        n = usage.get(k)
        if n is None:
            return None
        if type(n) is not int or n < 0:
            raise ValueError('quantite_invalide')
        if n == 0:
            continue                                   # rien de consommé dans cette catégorie : son tarif n'est pas nécessaire
        if tarifs.get(k) is None:
            return None                                # tarif manquant : jamais un tarif nul
        if isinstance(tarifs[k], bool):
            raise ValueError('tarif_invalide')
        try:
            t = Decimal(str(tarifs[k]))
        except InvalidOperation:
            raise ValueError('tarif_invalide') from None
        if not t.is_finite() or t < 0:
            raise ValueError('tarif_invalide')
        total += n * t
    return int(total.to_integral_value(rounding=ROUND_CEILING))


def montant_affichable(canal, usage, tarifs):
    """Un forfait et un appel local ne reçoivent jamais un prix d'API."""
    if canal == 'abonnement':
        return {'montant_micro': None, 'etat': 'forfait_separe'}
    if canal == 'local':
        return {'montant_micro': None, 'etat': 'cout_poste_non_mesure'}
    if canal != 'api':
        raise ValueError('canal_invalide')
    montant = cout_tokens(usage, tarifs)
    return {'montant_micro': montant, 'etat': 'calcule_depuis_usage' if montant is not None else 'incomplet'}


# ------------------------------------------------------------------------------------------------ observation d'un appel
def type_fournisseur(cfg):
    return str((cfg or {}).get('provider_type') or (cfg or {}).get('type') or 'ollama')


def _noms_chat(u):
    """Famille OpenAI (cache compris dans l'entrée) : une passerelle compatible peut nommer les mêmes compteurs comme l'API Responses.
    Les noms sont alignés ; la famille, donc le sens du cache, reste celle que l'adaptateur a déclarée."""
    if not isinstance(u, dict) or 'prompt_tokens' in u or 'completion_tokens' in u:
        return u
    out = dict(u)
    for chat, responses in NOMS_CHAT:
        if responses in out:
            out[chat] = out[responses]
    return out


def _tarif(value):
    """Tarif par million de jetons, ou ``None`` s'il n'est pas renseigné. Les réglages enregistrent 0 pour un champ laissé vide et la
    réservation refuse déjà un fournisseur sans tarif : zéro vaut donc « non renseigné », jamais « gratuit »."""
    if value is None or isinstance(value, bool) or value == '':
        return None
    try:
        t = Decimal(str(value))
    except InvalidOperation:
        return None
    return t if t.is_finite() and t > 0 else None


def tarifs_de(cfg):
    """(grille, fiche) de l'appel, telle qu'elle est au moment de l'appel : prix de la décision de routage (``prix5682``) ou tarif saisi du
    fournisseur. Aucun tarif d'écriture de cache n'existe dans les réglages : il reste inconnu. La fiche (texte, origine, date du relevé de
    la référence, empreinte) est conservée avec l'observation : un tarif modifié plus tard ne change pas un appel passé."""
    prix = cfg.get('prix5682')
    if isinstance(prix, (tuple, list)) and len(prix) >= 4 and prix[3] == 'usd':
        entree, cache, sortie, origine = prix[0], prix[1], prix[2], 'decision_de_routage'
    else:
        entree, cache, sortie, origine = cfg.get('input_usd_per_million'), None, cfg.get('output_usd_per_million'), 'reglage_du_fournisseur'
    grille = {'entree_hors_cache': _tarif(entree), 'cache_lu': _tarif(cache), 'cache_ecrit_5m': None, 'cache_ecrit_1h': None, 'sortie_facturable': _tarif(sortie)}
    releve = ''
    try:
        from .catalogue5682 import reference
        ref = reference(type_fournisseur(cfg), cfg.get('model'))
    except Exception:   # noqa: BLE001 — la référence datée n'est qu'une provenance : son absence n'empêche pas le calcul
        ref = None
    if ref and ref.get('unite') == 'usd' and (_tarif(ref.get('entree')), _tarif(ref.get('sortie'))) == (grille['entree_hors_cache'], grille['sortie_facturable']):
        origine, releve = 'reference_datee', str(ref.get('releve') or '')
    prix_texte = {k: (None if v is None else str(v)) for k, v in grille.items()}
    empreinte = hashlib.sha256(json.dumps({'devise': 'USD', 'origine': origine, 'releve': releve, 'prix': prix_texte}, sort_keys=True).encode()).hexdigest()[:12]
    return grille, {'devise': 'USD', 'unite': 'par_million_de_jetons', 'origine': origine, 'releve': releve, 'version': 'g' + empreinte, 'prix': prix_texte}


def _mesure(n):
    if all(n[k] is None for k in ('entree', 'entree_hors_cache', 'cache_lu', 'sortie_facturable')):
        return 'absente'
    if all(n[k] is not None for k in CATEGORIES):
        return 'complete'
    if n['entree'] is not None and n['sortie_facturable'] is not None:
        return 'sans_detail_cache'
    if n['entree_hors_cache'] is not None and n['sortie_facturable'] is not None and n['cache_lu'] is None and n['cache_ecrit'] is None:
        return 'cache_non_declare'                     # schéma à cache ajouté : entrée ordinaire et sortie connues, catégories de cache absentes
    return 'partielle'


def _majorant(n, grille):
    """Borne supérieure étiquetée, pour le seul cas où le partage de l'entrée entre cache lu et hors cache manque (compteur ou tarif) : toute
    l'entrée est comptée au tarif d'entrée. Jamais une sous-estimation : refusée dès qu'une écriture de cache est possible ou connue (tarif
    propre, plus élevé), ou si le tarif du cache lu dépasse celui de l'entrée."""
    if n['entree'] is None or n['sortie_facturable'] is None or n['cache_ecrit_5m'] != 0 or n['cache_ecrit_1h'] != 0:
        return None, ''
    if grille['cache_lu'] is not None and grille['entree_hors_cache'] is not None and grille['cache_lu'] > grille['entree_hors_cache']:
        return None, ''
    borne = cout_tokens({'entree_hors_cache': n['entree'], 'cache_lu': 0, 'cache_ecrit_5m': 0, 'cache_ecrit_1h': 0, 'sortie_facturable': n['sortie_facturable']}, grille)
    if borne is None:
        return None, ''
    return borne, 'majorant_cache_non_detaille' if n['cache_lu'] is None else 'majorant_tarif_cache_absent'


def observer(cfg, usage):
    """Observation d'un appel : format du fournisseur, catégories disjointes, état de la mesure, grille figée, montant calculé.
    Le montant est calculé, jamais relevé : il n'est ni la facture ni le relevé du fournisseur. ``None`` : inconnu, avec son motif."""
    cfg = cfg if isinstance(cfg, dict) else {}
    kind = type_fournisseur(cfg)
    canal = 'abonnement' if kind == 'chatgpt' else ('local' if kind == 'ollama' else 'api')
    fmt = FORMATS.get(kind)
    vide = dict.fromkeys(CLES)
    if fmt is None:
        n, mesure = vide, 'non_qualifiee'
    else:
        try:
            # un forfait ne facture rien au jeton : l'écriture de cache n'y a pas de tarif propre (contrat du canal, pas inférence)
            n = normaliser(fmt, _noms_chat(usage) if fmt == 'openai_chat' else usage, cache_ecriture_non_facturee=canal == 'abonnement')
            mesure = _mesure(n)
            if mesure == 'absente':
                n = vide                               # rien n'a été relevé : un zéro posé par le seul schéma n'est pas une mesure
        except ValueError:
            n, mesure = vide, 'incoherente'
    # Entrée connue : le total quand le schéma le donne ; à défaut l'entrée ordinaire déclarée. Dans un schéma à cache ajouté (Messages), des
    # champs de cache absents ne rendent pas ``input_tokens`` inconnu : il est gardé tel quel. Le cache, lui, reste inconnu (ni zéro ni
    # inventé) : le total n'est pas reconstitué et le montant n'est pas calculé, faute de pouvoir le borner.
    sortie = {'format': fmt or '', 'canal': canal, 'usage': n, 'mesure': mesure, 'montant_micro': None, 'devise': '', 'tarif': None,
              'entree_connue': n['entree'] if n['entree'] is not None else n['entree_hors_cache']}
    if canal != 'api':
        sortie['etat_montant'] = 'forfait_separe' if canal == 'abonnement' else 'cout_poste_non_mesure'
        return sortie
    grille, fiche = tarifs_de(cfg)
    sortie.update(devise='USD', tarif=fiche)
    if mesure in SANS_MESURE:
        sortie['etat_montant'] = {'absente': 'usage_absent', 'incoherente': 'usage_incoherent', 'non_qualifiee': 'format_non_qualifie'}[mesure]
        return sortie
    if fmt == 'openai_chat' and isinstance(usage, dict):
        # passerelle compatible qui relaie un modèle à écriture de cache payante (détail « cache_write_tokens » ou « cache_creation_input_tokens ») :
        # le schéma chat n'a pas de tarif pour cette catégorie ; elle est notée, et le montant reste inconnu plutôt que sous-estimé
        details = usage.get('prompt_tokens_details') if isinstance(usage.get('prompt_tokens_details'), dict) else usage.get('input_tokens_details')
        ecrit = max(_n(details, 'cache_write_tokens') or 0, _n(usage, 'cache_creation_input_tokens') or 0)
        if ecrit:
            n = {**n, 'cache_ecrit': ecrit, 'cache_ecrit_5m': None, 'cache_ecrit_1h': None, 'entree_hors_cache': None}
            sortie.update(usage=n, mesure=_mesure(n))
            mesure = sortie['mesure']
    montant, etat = cout_tokens(n, grille), 'calcule_depuis_usage'
    if montant is None:
        montant, etat = _majorant(n, grille)
    if montant is None:
        connus = n['entree'] is not None and n['sortie_facturable'] is not None
        manque_tarif = connus and ((n['entree'] and grille['entree_hors_cache'] is None) or (n['sortie_facturable'] and grille['sortie_facturable'] is None))
        ecrit = (n['cache_ecrit_5m'] or 0) + (n['cache_ecrit_1h'] or 0) or (n['cache_ecrit'] or 0)
        etat = 'tarif_manquant' if manque_tarif or (connus and mesure == 'complete') or (connus and ecrit) else 'usage_partiel'
    sortie.update(montant_micro=montant, etat_montant=etat)
    return sortie


def fiche_observation(obs):
    """Ce que le registre garde de l'observation : nombres et grille, jamais de texte d'un échange."""
    return {'format': obs['format'], 'mesure': obs['mesure'], 'usage': {k: v for k, v in obs['usage'].items() if v is not None},
            'etat_montant': obs['etat_montant'], 'tarif': obs['tarif']}


# ------------------------------------------------------------------------------------------------ schéma (ajouts seulement)
COLONNES = {
    'conso_5682': (('call_id', "TEXT NOT NULL DEFAULT ''"), ('tentative', "TEXT NOT NULL DEFAULT ''"), ('revision', 'INTEGER NOT NULL DEFAULT 0'), ('revise_le', 'TEXT'),
                   ('format_usage', "TEXT NOT NULL DEFAULT ''"), ('mesure', "TEXT NOT NULL DEFAULT ''"), ('hors_cache', 'INTEGER'), ('cache_ecrit', 'INTEGER'),
                   ('cache_ecrit_5m', 'INTEGER'), ('cache_ecrit_1h', 'INTEGER'), ('montant_micro', 'INTEGER'), ('etat_montant', "TEXT NOT NULL DEFAULT ''"),
                   ('devise', "TEXT NOT NULL DEFAULT ''"), ('tarif_json', "TEXT NOT NULL DEFAULT ''"), ('auteur', "TEXT NOT NULL DEFAULT ''"),
                   ('dossier_h', "TEXT NOT NULL DEFAULT ''")),
    'budget_reservations5689': (('computed_micro', 'INTEGER'), ('amount_source', "TEXT NOT NULL DEFAULT ''")),
    'ai_usage_v391': (('mesure', "TEXT NOT NULL DEFAULT ''"),),
}
INDEX_APPEL = "CREATE UNIQUE INDEX IF NOT EXISTS conso_5682_appel ON conso_5682(call_id, tentative) WHERE call_id<>'' OR tentative<>''"
# Quantités de service typées (crédits Pappers, recherches…) : même registre, même transaction, même identifiant d'appel. Jamais une fausse
# devise à trois lettres ; une quantité inconnue reste NULL. Les millièmes évitent tout nombre à virgule flottante.
SCHEMA_QUANTITES = (
    '''CREATE TABLE IF NOT EXISTS budget_quantites56109(
  cabinet_id TEXT NOT NULL, call_id TEXT NOT NULL, provider TEXT NOT NULL, unit_code TEXT NOT NULL CHECK(length(unit_code) BETWEEN 4 AND 40),
  quantite_milli INTEGER CHECK(quantite_milli IS NULL OR quantite_milli>=0), etat TEXT NOT NULL CHECK(etat IN ('estimee','declaree','rapprochee','inconnue')),
  operation TEXT NOT NULL DEFAULT '', mission_id TEXT NOT NULL DEFAULT '', auteur TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0), at TEXT NOT NULL, revise_le TEXT,
  PRIMARY KEY(cabinet_id,call_id,provider,unit_code))''',
    'CREATE INDEX IF NOT EXISTS budget_quantites56109_at ON budget_quantites56109(at, provider)')
ETATS_QUANTITE = ('estimee', 'declaree', 'rapprochee', 'inconnue')


def migrer(db, tables=None):
    """Colonnes et table de la 5.6.109 : ajout seulement, idempotent, dans la transaction de l'appelant (démarrage du service ou d'un
    travailleur, ou première écriture d'une observation). Jamais appelée par une lecture. Une table absente est laissée à son module."""
    for table, colonnes in COLONNES.items():
        if tables is not None and table not in tables:
            continue
        presentes = {r[1] for r in db.execute('PRAGMA table_info(%s)' % table)}
        if not presentes:
            continue
        for nom, definition in colonnes:
            if nom not in presentes:
                db.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, nom, definition))
        if table == 'conso_5682':
            db.execute(INDEX_APPEL)
    if tables is None:
        for instruction in SCHEMA_QUANTITES:
            db.execute(instruction)


def lecture(state_dir):
    """Connexion en lecture seule sur la base du cabinet, ou ``None`` : rien n'est créé, rien n'est migré, rien n'est écrit."""
    chemin = Path(str(state_dir or '')) / 'desk.sqlite3'
    if not state_dir or not chemin.is_file():
        return None
    try:
        db = sqlite3.connect(chemin.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    except sqlite3.Error:
        return None
    db.row_factory = sqlite3.Row
    return db


def _colonnes(db, table):
    try:
        return {r[1] for r in db.execute('PRAGMA table_info(%s)' % table)}
    except sqlite3.Error:
        return set()


def pret(db):
    """Le schéma de la 5.6.109 est-il en place ? Sinon le relevé se dit indisponible : il n'est pas rempli de zéros, et rien n'est migré ici."""
    return ({n for n, _ in COLONNES['conso_5682']} <= _colonnes(db, 'conso_5682')
            and {n for n, _ in COLONNES['budget_reservations5689']} <= _colonnes(db, 'budget_reservations5689')
            and bool(_colonnes(db, 'budget_quantites56109')))


# ------------------------------------------------------------------------------------------------ périodes du cabinet
PERIODES = {'jour': 'Aujourd’hui', '7j': 'Sept derniers jours', 'mois': 'Mois en cours', 'perso': 'Période personnalisée'}
CANAUX = {'api': 'API', 'abonnement': 'Abonnement', 'local': 'IA locale', 'service': 'Services externes'}
MAX_JOURS = 400                 # durée de conservation des observations (conso5682.KEEP_DAYS)
PAR_PAGE, MAX_PAR_PAGE, MAX_EXPORT = 25, 100, 20000
FILTRES = ('fournisseur', 'modele', 'canal', 'fonction', 'mission', 'dossier', 'utilisateur')


def fuseau(desk):
    """Fuseau du cabinet ; sans réglage lisible : Europe/Paris, jamais l'heure UTC du serveur."""
    from zoneinfo import ZoneInfo
    try:
        from .settings568 import profile
        return ZoneInfo(str(profile(desk, 'cabinet')['timezone']))
    except Exception:   # noqa: BLE001 — bureau partiel (essai, diagnostic) ou fuseau illisible
        return ZoneInfo('Europe/Paris')


def _date(texte):
    try:
        return datetime.strptime(str(texte or '')[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def periode(desk, args=None, maintenant=None):
    """Bornes de la période dans le fuseau du cabinet, converties en UTC pour ``at >= début AND at < fin`` (paramètres SQL). Un mois du
    cabinet n'est jamais filtré par un préfixe de la date UTC : à Paris, la première heure du mois appartient encore au mois précédent en UTC."""
    args = args or {}
    zone = fuseau(desk)
    jour = (maintenant or datetime.now(timezone.utc)).astimezone(zone).date()
    code = str(args.get('periode') or 'mois')
    if code not in PERIODES:
        raise Stop('periode_invalide')
    if code == 'jour':
        debut = fin = jour
    elif code == '7j':
        debut, fin = jour - timedelta(days=6), jour
    elif code == 'mois':
        debut = _date(str(args.get('mois') or '')[:7] + '-01') if args.get('mois') else jour.replace(day=1)
        if debut is None:
            raise Stop('periode_invalide')
        fin = (debut.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    else:
        debut, fin = _date(args.get('debut')), _date(args.get('fin'))
        if debut is None or fin is None or fin < debut or (fin - debut).days > MAX_JOURS:
            raise Stop('periode_invalide')
    def borne(jour_local):                             # minuit du cabinet, exprimé en UTC (heure d'été comprise)
        return datetime.combine(jour_local, time.min, tzinfo=zone).astimezone(timezone.utc).isoformat()
    return {'code': code, 'libelle': PERIODES[code], 'debut': debut.isoformat(), 'fin': fin.isoformat(), 'fuseau': str(zone),
            'debut_utc': borne(debut), 'fin_utc': borne(fin + timedelta(days=1))}


def empreinte_dossier(matter):
    """Même empreinte que la décision de routage (``decisions_5682.dossier_h``) : le nom du dossier ne figure pas dans les observations."""
    return hashlib.sha256(str(matter).encode()).hexdigest()[:16] if matter else ''


def filtres(desk, owner, role, args=None):
    """Filtres contrôlés. Le rôle et le compte viennent de la requête authentifiée, jamais des paramètres. Un collaborateur est ramené à
    ses propres demandes ; un dossier inconnu ou réservé donne le même refus neutre ; un contrôle des droits en panne ferme l'accès."""
    args = args or {}
    f = {k: str(args.get(k) or '').strip()[:200] for k in FILTRES}
    if f['canal'] and f['canal'] not in CANAUX:
        raise Stop('filtre_invalide')
    admin = role == 'administrateur'
    try:
        from .membres5650 import hidden_for
        masques = frozenset(hidden_for(desk.c['state_dir'], owner, role) or ())
    except Exception:   # noqa: BLE001 — registre des droits illisible : jamais un accès élargi
        raise Stop('controle_indisponible') from None
    if f['dossier']:
        try:
            from .common import load_matters
            connus = {str(m.get('id')) for m in load_matters(desk.c, unfiltered=True)}
        except Exception:   # noqa: BLE001
            raise Stop('controle_indisponible') from None
        if f['dossier'] not in connus or f['dossier'] in masques:
            raise Stop('introuvable')
    if not admin:
        f['utilisateur'] = str(owner or '')            # jamais la consommation d'un autre compte
    try:
        page = max(1, int(args.get('page') or 1))
        par_page = max(1, min(MAX_PAR_PAGE, int(args.get('par_page') or PAR_PAGE)))
    except (TypeError, ValueError):
        raise Stop('filtre_invalide') from None
    return {**f, 'periode': periode(desk, args), 'admin': admin, 'masques': sorted(empreinte_dossier(m) for m in masques), 'page': page, 'par_page': par_page,
            'detail': str(args.get('detail') or '') in ('1', 'oui', 'true') or bool(f['mission'])}


def _portee(f, p='', dossier=True):
    """Garde-fou des droits, commun à toutes les lectures : (clauses, paramètres) sur les colonnes ``auteur`` et ``dossier_h`` d'une table
    d'observations (préfixe ``p`` : alias de la table). Hors administrateur : ses propres demandes seulement (un compte vide ne voit
    rien), dossiers réservés retirés. ``dossier`` faux : la table ne porte pas de dossier (quantités de service) ; un filtre de dossier
    ou un dossier réservé au lecteur n'y laisse alors rien passer, faute de pouvoir le vérifier."""
    clauses, params = [], []
    if f['utilisateur'] or not f['admin']:
        clauses.append("%sauteur=? AND %sauteur<>''" % (p, p))
        params.append(f['utilisateur'])
    if not dossier:
        return (clauses + ['1=0'], params) if f['dossier'] or (f['masques'] and not f['admin']) else (clauses, params)
    if f['dossier']:
        clauses.append(p + 'dossier_h=?')
        params.append(empreinte_dossier(f['dossier']))
    if f['masques'] and not f['admin']:
        clauses.append('%sdossier_h NOT IN (%s)' % (p, ','.join('?' for _ in f['masques'])))
        params += f['masques']
    return clauses, params


def _attribue(f):
    """Le filtre demande-t-il une attribution (compte, dossier) que seules les observations portent ?"""
    return bool(f['utilisateur'] or f['dossier'] or not f['admin'])


def _simples(f, colonnes, p=''):
    """Filtres d'égalité d'une table. ``colonnes`` : nom du filtre → colonne de cette table (un filtre sans colonne ne s'y applique pas)."""
    clauses, params = [], []
    for nom in ('fournisseur', 'modele', 'canal', 'fonction', 'mission'):
        if f[nom] and colonnes.get(nom):
            clauses.append(p + colonnes[nom] + '=?')
            params.append(f[nom])
    return clauses, params


def _ou(f, colonnes, p=''):
    """Clause WHERE d'une table : fenêtre en UTC (``>=`` début, ``<`` fin), filtres simples, portée du lecteur."""
    clauses, params = [p + colonnes['at'] + '>=?', p + colonnes['at'] + '<?'], [f['periode']['debut_utc'], f['periode']['fin_utc']]
    simples, valeurs = _simples(f, colonnes, p)
    portee, restreints = _portee(f, p, colonnes['portee'] == 'dossier') if colonnes.get('portee') else ([], [])
    return ' AND '.join(clauses + simples + portee), params + valeurs + restreints


# colonnes de chaque table pour les filtres ; « portee » : la table porte l'auteur et le dossier (« dossier »), ou l'auteur seul (« auteur »)
CONSO = {'at': 'at', 'fournisseur': 'fournisseur', 'modele': 'modele', 'canal': 'canal', 'fonction': 'fonction', 'mission': 'mission', 'portee': 'dossier'}
REGISTRE = {'at': 'created_at', 'fournisseur': 'provider', 'modele': 'model', 'fonction': 'purpose', 'mission': 'mission_id'}
LOCAL = {'at': 'at', 'modele': 'model', 'fonction': 'purpose'}
QUANTITES = {'at': 'at', 'fournisseur': 'provider', 'mission': 'mission_id', 'portee': 'auteur'}


def _plus(a, b):
    """Somme de valeurs connues : ``None`` tant qu'aucune n'est connue (jamais zéro par défaut)."""
    return b if a is None else (a if b is None else a + b)


# ------------------------------------------------------------------------------------------------ relevé (F18)
def _lignes_conso(db, f):
    ou, params = _ou(f, CONSO)
    rows = db.execute(
        "SELECT canal,fournisseur,modele,COUNT(*) appels,SUM(entree) entree,COUNT(entree) entree_n,SUM(entree_cache) cache_lu,COUNT(entree_cache) cache_lu_n,"
        "SUM(cache_ecrit) cache_ecrit,COUNT(cache_ecrit) cache_ecrit_n,SUM(sortie) sortie,COUNT(sortie) sortie_n,SUM(raisonnement) raisonnement,"
        "SUM(CASE WHEN mesure IN ('absente','incoherente','non_qualifiee') OR (format_usage='' AND entree IS NULL AND sortie IS NULL) THEN 1 ELSE 0 END) sans_mesure,"
        "SUM(CASE WHEN format_usage='' THEN 1 ELSE 0 END) historiques,SUM(CASE WHEN etat<>'done' THEN 1 ELSE 0 END) interrompus,"
        # entrée réduite à sa part ordinaire, faute de catégories de cache (schéma additif) : la somme est alors dite partielle
        "SUM(CASE WHEN entree IS NOT NULL AND entree=hors_cache AND (entree_cache IS NULL OR cache_ecrit IS NULL) THEN 1 ELSE 0 END) entree_min,"
        "SUM(CASE WHEN etat_montant LIKE 'majorant%' THEN 1 ELSE 0 END) majorants,SUM(CASE WHEN canal='api' AND montant_micro IS NULL THEN 1 ELSE 0 END) sans_montant,"
        "SUM(CASE WHEN unite='credits' THEN quantite END) repere_credits,COUNT(CASE WHEN unite='credits' THEN quantite END) repere_n "
        'FROM conso_5682 WHERE ' + ou + ' GROUP BY canal,fournisseur,modele ORDER BY canal,fournisseur,modele', params).fetchall()
    return [dict(r) for r in rows]


def _exists_conso(f):
    """Restriction d'une ligne du registre ou d'une quantité à la portée du lecteur, par son observation (même identifiant d'appel)."""
    portee, valeurs = _portee(f, 'c.')
    if not portee:
        return '', []
    return " AND EXISTS(SELECT 1 FROM conso_5682 c WHERE c.call_id=r.call_id AND c.call_id<>'' AND " + ' AND '.join(portee) + ')', valeurs


def _registre(db, f):
    """Montants du registre du budget, par fournisseur et modèle. Seule source des montants du relevé : le bilan du mois lit la même projection."""
    from .budget_ledger5689 import projection
    clauses, params = _simples(f, REGISTRE, 'r.')
    if f['canal'] == 'api':
        clauses.append("r.currency<>'CRD'")
    elif f['canal'] == 'abonnement':
        clauses.append("r.currency='CRD'")
    elif f['canal']:
        return {}
    exists, valeurs = _exists_conso(f)
    return projection(db, f['periode']['debut_utc'], f['periode']['fin_utc'], ''.join(' AND ' + c for c in clauses) + exists, params + valeurs)


def _local(db, f):
    """Générations du modèle local (``ai_usage_v391``, fournisseur ``ollama``). Elles ne portent ni compte ni dossier : hors de toute vue
    filtrée par compte ou dossier, et réservées à l'administrateur. Un zéro y tient lieu d'absence : il est lu comme « non fourni »."""
    if _attribue(f) or f['mission'] or f['canal'] not in ('', 'local') or f['fournisseur'] not in ('', 'ollama'):
        return None
    colonnes = _colonnes(db, 'ai_usage_v391')
    if not colonnes:
        return {'appels': 0, 'modeles': []}
    ou, params = _ou(f, LOCAL)
    duree = 'SUM(duration_ms)' if 'duration_ms' in colonnes else 'NULL'
    rows = db.execute("SELECT model modele,COUNT(*) appels,SUM(CASE WHEN input_tokens>0 THEN input_tokens END) entree,SUM(CASE WHEN input_tokens>0 THEN 1 ELSE 0 END) entree_n,"
                      "SUM(CASE WHEN output_tokens>0 THEN output_tokens END) sortie,SUM(CASE WHEN output_tokens>0 THEN 1 ELSE 0 END) sortie_n," + duree + " duree_ms "
                      "FROM ai_usage_v391 WHERE provider='ollama' AND " + ou + ' GROUP BY model ORDER BY model', params).fetchall()
    modeles = [dict(r) for r in rows]
    return {'appels': sum(m['appels'] for m in modeles), 'modeles': modeles, 'duree_mesuree': 'duration_ms' in colonnes}


def _services(db, f):
    if f['canal'] not in ('', 'service') or f['modele'] or f['fonction']:
        return []
    ou, params = _ou(f, QUANTITES)
    rows = db.execute('SELECT provider fournisseur,unit_code unite,etat,COUNT(*) releves,SUM(quantite_milli) quantite_milli,COUNT(quantite_milli) connus,MAX(COALESCE(revise_le,at)) dernier '
                      'FROM budget_quantites56109 WHERE ' + ou + ' GROUP BY provider,unit_code,etat ORDER BY provider,unit_code,etat', params).fetchall()
    return [dict(r) for r in rows]


def _anciens(db, f):
    """Estimations d'avant le registre commun (5.6.89), sans identifiant d'appel : comptées comme dans le bilan, jamais attribuées."""
    if _attribue(f) or f['mission'] or f['canal'] not in ('', 'api') or 'call_id' not in _colonnes(db, 'ai_usage_v391'):
        return None
    ou, params = _ou(f, {'at': 'at', 'fournisseur': 'provider', 'modele': 'model', 'fonction': 'purpose'})
    row = db.execute("SELECT COUNT(*),SUM(estimated_cost_usd) FROM ai_usage_v391 WHERE status='done' AND provider<>'ollama' AND COALESCE(call_id,'')='' AND " + ou, params).fetchone()
    if not row[0]:
        return None
    from .budget_ledger5689 import micro
    return {'appels': int(row[0]), 'micro': micro(Decimal(str(row[1] or 0)))}


def _detail_appels(db, f, limite, decalage):
    ou, params = _ou(f, CONSO, 'c.')
    etape = ',d.etape etape' if _colonnes(db, 'decisions_5682') else ",'' etape"
    jointure = ' LEFT JOIN decisions_5682 d ON d.uid=c.call_id' if _colonnes(db, 'decisions_5682') else ''
    total = db.execute('SELECT COUNT(*) FROM conso_5682 c WHERE ' + ou, params).fetchone()[0]
    rows = db.execute(
        'SELECT c.id,c.at,c.fournisseur,c.modele,c.canal,c.fonction,c.mission,c.etat,c.format_usage,c.mesure,c.entree,c.hors_cache,c.entree_cache cache_lu,c.cache_ecrit,'
        'c.sortie,c.raisonnement,c.quantite,c.unite,c.montant_micro,c.etat_montant,c.devise,c.tarif_json,c.revision,c.revise_le,c.call_id,'
        'r.state registre_etat,r.retained_micro retenu_micro,r.ceiling_micro plafond_micro,r.amount_source origine_registre,r.provider_amount_micro releve_micro,'
        'r.provider_currency releve_devise,r.closed_at regle_le' + etape +
        " FROM conso_5682 c LEFT JOIN budget_reservations5689 r ON r.call_id=c.call_id AND c.call_id<>''" + jointure +
        ' WHERE ' + ou + ' ORDER BY c.at DESC,c.id DESC LIMIT ? OFFSET ?', params + [limite, decalage]).fetchall()
    out = []
    for r in rows:
        ligne = dict(r)
        try:
            ligne['tarif'] = json.loads(ligne.pop('tarif_json') or 'null')
        except ValueError:
            ligne['tarif'] = None
        out.append(ligne)
    return int(total), out


def _canal_vide():
    return {'appels': 0, 'entree': None, 'cache_lu': None, 'cache_ecrit': None, 'sortie': None, 'partiel': False, 'sans_mesure': 0, 'historiques': 0, 'lignes': []}


def releve(desk, owner, role, args=None):
    """Relevé « Consommation et coûts » de la période, pour le lecteur de la requête. Lecture seule ; les contrôles de droits passent avant
    toute donnée. ``disponible`` faux : le schéma n'est pas encore en place (service à redémarrer) ; aucun total n'est alors inventé."""
    f = filtres(desk, owner, role, args)
    base = {'disponible': False, 'periode': f['periode'], 'portee': 'cabinet' if f['admin'] and not f['utilisateur'] else 'personnelle',
            'filtres': {k: f[k] for k in FILTRES}, 'page': f['page'], 'par_page': f['par_page']}
    db = lecture(desk.c['state_dir'])
    if db is None:
        return base
    try:
        if not pret(db):
            return base
        api, abonnement = _canal_vide(), _canal_vide()
        for ligne in _lignes_conso(db, f):
            cible = api if ligne['canal'] == 'api' else (abonnement if ligne['canal'] == 'abonnement' else None)
            if cible is None:
                continue
            partiel = (any(0 < ligne[k + '_n'] < ligne['appels'] for k in ('entree', 'sortie')) or (ligne['entree_n'] and ligne['cache_lu_n'] < ligne['entree_n'])
                       or ligne['entree_min'])
            ligne['partiel'] = bool(partiel)
            cible['lignes'].append(ligne)
            cible['appels'] += ligne['appels']
            for k in ('entree', 'cache_lu', 'cache_ecrit', 'sortie'):
                cible[k] = _plus(cible[k], ligne[k])
            cible['partiel'] = cible['partiel'] or ligne['partiel'] or bool(ligne['sans_mesure'])
            cible['sans_mesure'] += ligne['sans_mesure']
            cible['historiques'] += ligne['historiques']
        abonnement['repere_credits'] = None
        for ligne in abonnement['lignes']:
            abonnement['repere_credits'] = _plus(abonnement['repere_credits'], ligne['repere_credits'])
        montants = _registre(db, f)
        api['registre'] = montants.get('total', {}).get('USD')
        api['par_modele'] = montants.get('lignes', [])
        api['forfaits'] = montants.get('forfaits', 0)
        api['anciens'] = _anciens(db, f)
        sans_montant = sum(x['sans_montant'] for x in api['lignes'])
        api['appels_sans_montant'] = sans_montant
        r = api['registre'] or {}
        api['incomplet'] = bool(sans_montant or r.get('incertain_n') or r.get('reserve_n') or r.get('estime_n') or api['partiel'])
        local = _local(db, f)
        services = _services(db, f)
        sortie = {**base, 'disponible': True, 'api': api, 'abonnement': abonnement, 'local': local, 'services': services,
                  'appels_sans_mesure': api['sans_mesure'] + abonnement['sans_mesure'], 'dernier_rapprochement': r.get('dernier_rapprochement')}
        if f['admin'] and not _attribue(f):
            from . import budget_ledger5689 as bl
            sortie['plafonds'] = bl.plafonds_lus(db, desk)
            sortie['couts_fixes'] = bl.couts_fixes_lus(db, f['periode']['debut'][:7])
        if f['mission']:
            from . import budget_ledger5689 as bl
            sortie['mission'] = bl.mission_lue(db, desk, f['mission'], _exists_conso(f))
        if f['detail']:
            total, appels = _detail_appels(db, f, f['par_page'], (f['page'] - 1) * f['par_page'])
            sortie['detail'] = {'total': total, 'appels': appels, 'pages': max(1, -(-total // f['par_page']))}
        return sortie
    finally:
        db.close()


# ------------------------------------------------------------------------------------------------ export CSV filtré
ENTETES = ('nature', 'date_utc', 'date_cabinet', 'fournisseur', 'modele', 'canal', 'fonction', 'mission', 'etape', 'etat_appel', 'format_usage', 'unite_quantites',
           'entree', 'entree_hors_cache', 'cache_lu', 'cache_ecrit', 'sortie', 'dont_raisonnement', 'etat_mesure', 'montant_calcule', 'devise', 'origine_montant',
           'tarif_entree_par_million', 'tarif_cache_lu_par_million', 'tarif_sortie_par_million', 'tarif_origine', 'tarif_releve', 'tarif_version', 'etat_registre',
           'montant_retenu', 'origine_registre', 'releve_fournisseur', 'devise_releve', 'quantite_service', 'etat_quantite', 'revision', 'identifiant_appel')


def cellule(valeur):
    """Texte d'une case : une valeur inconnue reste vide (jamais 0) ; un tableur n'interprète jamais une valeur importée comme une formule."""
    texte = '' if valeur is None else str(valeur)
    return "'" + texte if texte[:1] in ('=', '+', '-', '@', '\t', '\r') else texte


def _decimal(micro_valeur):
    """Montant en unités, virgule décimale (séparateur de colonnes « ; ») ; vide si inconnu."""
    return '' if micro_valeur is None else ('%.6f' % (Decimal(int(micro_valeur)) / Decimal(1_000_000))).replace('.', ',')


def _local_cabinet(stamp, zone):
    try:
        return datetime.fromisoformat(str(stamp)).astimezone(zone).strftime('%Y-%m-%d %H:%M')
    except (TypeError, ValueError):
        return ''


def export_csv(desk, owner, role, args=None):
    """CSV du relevé filtré (séparateur « ; », UTF-8 avec BOM) : une ligne par appel observé, par génération locale et par quantité de
    service, avec unité, devise, provenance et état de mesure. Mêmes filtres et mêmes droits que l'écran ; ni texte d'un échange, ni clé,
    ni réservation réactivable. Rend (nom, texte, nombre de lignes) ; ``Stop`` si le relevé n'est pas lisible : jamais un fichier de zéros."""
    f = filtres(desk, owner, role, args)
    db = lecture(desk.c['state_dir'])
    if db is None:
        raise Stop('releve_indisponible')
    zone = fuseau(desk)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';', lineterminator='\r\n')
    w.writerow(ENTETES)
    n = 0
    try:
        if not pret(db):
            raise Stop('releve_indisponible')
        def ecrire(**valeurs):
            ligne = dict.fromkeys(ENTETES)
            ligne.update(valeurs)
            w.writerow([cellule(ligne[k]) for k in ENTETES])

        if f['canal'] in ('', 'api', 'abonnement'):
            _total, appels = _detail_appels(db, f, MAX_EXPORT, 0)
            for a in appels:
                tarif = a.get('tarif') or {}
                prix = tarif.get('prix') or {}
                ecrire(nature='appel_ia', date_utc=a['at'], date_cabinet=_local_cabinet(a['at'], zone), fournisseur=a['fournisseur'], modele=a['modele'], canal=a['canal'],
                       fonction=a['fonction'], mission=a['mission'], etape=a.get('etape'), etat_appel=a['etat'], format_usage=a['format_usage'] or 'historique',
                       unite_quantites='jetons', entree=a['entree'], entree_hors_cache=a['hors_cache'], cache_lu=a['cache_lu'], cache_ecrit=a['cache_ecrit'], sortie=a['sortie'],
                       dont_raisonnement=a['raisonnement'], etat_mesure=a['mesure'] or 'historique', montant_calcule=_decimal(a['montant_micro']), devise=a['devise'],
                       origine_montant=a['etat_montant'], tarif_entree_par_million=prix.get('entree_hors_cache'), tarif_cache_lu_par_million=prix.get('cache_lu'),
                       tarif_sortie_par_million=prix.get('sortie_facturable'), tarif_origine=tarif.get('origine'), tarif_releve=tarif.get('releve'),
                       tarif_version=tarif.get('version'), etat_registre=a['registre_etat'], montant_retenu=_decimal(a['retenu_micro']), origine_registre=a['origine_registre'],
                       releve_fournisseur=_decimal(a['releve_micro']), devise_releve=a['releve_devise'], revision=a['revision'], identifiant_appel=a['call_id'])
                n += 1
        if _local(db, f) is not None and _colonnes(db, 'ai_usage_v391'):
            ou, params = _ou(f, LOCAL)
            for r in db.execute("SELECT at,model,purpose,input_tokens,output_tokens FROM ai_usage_v391 WHERE provider='ollama' AND " + ou + ' ORDER BY at DESC LIMIT ?',
                                params + [MAX_EXPORT]):
                # un zéro y tient lieu d'absence (compteur non renvoyé par Ollama) : la case reste vide
                ecrire(nature='ia_locale', date_utc=r['at'], date_cabinet=_local_cabinet(r['at'], zone), fournisseur='ollama', modele=r['model'], canal='local',
                       fonction=r['purpose'], etat_appel='done', format_usage='ollama', unite_quantites='jetons', entree=r['input_tokens'] or None,
                       sortie=r['output_tokens'] or None, origine_montant='cout_poste_non_mesure')
                n += 1
        if f['canal'] in ('', 'service') and not f['modele'] and not f['fonction']:
            ou, params = _ou(f, QUANTITES)
            for r in db.execute('SELECT * FROM budget_quantites56109 WHERE ' + ou + ' ORDER BY at DESC LIMIT ?', params + [MAX_EXPORT]):
                ecrire(nature='service', date_utc=r['at'], date_cabinet=_local_cabinet(r['at'], zone), fournisseur=r['provider'], canal='service', fonction=r['operation'],
                       mission=r['mission_id'], unite_quantites=r['unit_code'], etat_quantite=r['etat'], revision=r['revision'], identifiant_appel=r['call_id'],
                       quantite_service=None if r['quantite_milli'] is None else ('%.3f' % (Decimal(int(r['quantite_milli'])) / 1000)).replace('.', ','))
                n += 1
    finally:
        db.close()
    p = f['periode']
    return 'consommation-%s-%s.csv' % (p['debut'], p['fin']), '﻿' + buf.getvalue(), n


# ------------------------------------------------------------------------------------------------ anciens compteurs (5.6.109, I2)
def renvoi_html(desk, ancien_html, lien_html):
    """Ancien compteur d'une page (régime économe, poste de travail, routage hybride) : avec la nouvelle interface, il renvoie à la section
    unique « Consommation et coûts » (un seul calcul, jamais deux chiffres concurrents) ; interrupteur éteint, l'ancienne présentation reste."""
    try:
        from . import interface56100, membres5650
        if not interface56100.active(desk, membres5650.current_author() or 'cabinet'):
            return ancien_html
    except Exception:   # noqa: BLE001 — réglage illisible : présentation par défaut (nouvelle interface)
        pass
    return ('<p class="ax-muted m109-renvoi">Ce compteur est regroupé dans la section unique « Consommation et coûts » : mêmes mesures, un seul calcul. %s</p>'
            % lien_html)


def lien_section(prefix):
    """Lien vers la section unique, depuis une page qui connaît le préfixe de l'application."""
    from html import escape
    return '<a href="%s">Voir Consommation et coûts</a>' % escape((prefix or '') + '/parametres?tab=ia#consommation-couts', quote=True)
