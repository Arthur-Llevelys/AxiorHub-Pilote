"""5.6.82 : registre de consommation (ConsumptionLedger).

Trois canaux distincts : ``abonnement`` (ChatGPT, jetons connus, crédits seulement à titre de repère), ``api`` (dollars estimés), ``local``.
Les jetons sont repris des champs réellement fournis ; ceux de raisonnement sont déjà compris dans la sortie et ne sont pas ajoutés deux
fois. Un usage absent reste inconnu (« Non fournie ») : jamais zéro, jamais illimité.

La réservation de mission garde un budget cumulé pour un travail et ses enfants, y compris après un redémarrage. Elle est une limite locale
d'AxiorHub : ni une garantie de facturation, ni une connaissance du quota réel du fournisseur.

5.6.109 (C12) : l'observation est normalisée par le format du fournisseur (``consommation56109``) : cache compris dans l'entrée ou ajouté à
l'entrée selon le schéma, catégories disjointes, grille figée à l'appel, montant calculé distinct du registre du budget. Une ligne par appel
et par émission (``call_id``, ``tentative``) : le même événement reçu deux fois, ou un état cumulatif d'un flux, révise la ligne
(``revision``) au lieu d'en ajouter une. Les lignes d'avant la 5.6.109 (``format_usage`` vide) restent telles qu'elles ont été relevées.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

SCHEMA = '''
CREATE TABLE IF NOT EXISTS conso_5682(
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, fournisseur TEXT NOT NULL, modele TEXT NOT NULL, canal TEXT NOT NULL, fonction TEXT NOT NULL,
  mission TEXT NOT NULL DEFAULT '', entree INTEGER, entree_cache INTEGER, sortie INTEGER, raisonnement INTEGER,
  quantite REAL, unite TEXT NOT NULL, etat TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS conso_5682_at ON conso_5682(at, fournisseur);
CREATE TABLE IF NOT EXISTS reservations_5682(
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, mission TEXT NOT NULL, perimetre TEXT NOT NULL, quantite REAL NOT NULL, unite TEXT NOT NULL,
  etat TEXT NOT NULL, decision TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS reservations_5682_m ON reservations_5682(mission, unite, etat);
'''
KEEP_DAYS = 400
_migrees = set()
# colonnes d'une observation que la révision d'un même appel peut compléter : une valeur connue n'est jamais remplacée par une inconnue
NOMBRES = ('entree', 'entree_cache', 'sortie', 'raisonnement', 'hors_cache', 'cache_ecrit', 'cache_ecrit_5m', 'cache_ecrit_1h', 'montant_micro', 'quantite')
QUALITES = ('format_usage', 'mesure', 'etat_montant', 'devise', 'tarif_json', 'unite')


def connect(state_dir):
    """Connexion d'écriture (observation d'un appel). Les colonnes de la 5.6.109 sont ajoutées une fois par processus, comme au démarrage
    du service (``budget_ledger5689.ensure``) : jamais pendant une lecture, qui passe par ``consommation56109.lecture``."""
    db = sqlite3.connect(Path(state_dir) / 'desk.sqlite3', timeout=10)
    db.executescript(SCHEMA)
    if str(state_dir) not in _migrees:
        from .consommation56109 import COLONNES, migrer
        if not {n for n, _ in COLONNES['conso_5682']} <= {r[1] for r in db.execute('PRAGMA table_info(conso_5682)')}:
            db.execute('BEGIN IMMEDIATE')
            try:
                migrer(db, ('conso_5682',))
                db.commit()
            except BaseException:
                db.rollback()
                raise
        _migrees.add(str(state_dir))
    return db


def canal(cfg):
    kind = cfg.get('provider_type') or cfg.get('type') or 'ollama'
    return 'abonnement' if kind == 'chatgpt' else ('local' if kind == 'ollama' else 'api')


def fields(usage):
    """Champs d'usage tels que fournis (Responses, Chat, Messages) ; None lorsqu'ils manquent."""
    usage = usage if isinstance(usage, dict) else {}

    def pick(*names):
        for name in names:
            value = usage.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
        return None
    details_in = usage.get('input_tokens_details') if isinstance(usage.get('input_tokens_details'), dict) else usage.get('prompt_tokens_details')
    details_in = details_in if isinstance(details_in, dict) else {}
    details_out = usage.get('output_tokens_details') if isinstance(usage.get('output_tokens_details'), dict) else usage.get('completion_tokens_details')
    details_out = details_out if isinstance(details_out, dict) else {}
    def valid(v):
        return v if type(v) is int and v >= 0 else None
    cached = valid(details_in.get('cached_tokens'))
    if cached is None:
        cached = pick('cache_read_input_tokens')
    return {'entree': pick('input_tokens', 'prompt_tokens'), 'entree_cache': cached, 'sortie': pick('output_tokens', 'completion_tokens'),
            'raisonnement': valid(details_out.get('reasoning_tokens'))}


def amount(kind_prices, used):
    """Quantité consommée dans l'unité du fournisseur (entrée en cache comptée à son propre tarif) ; None si le tarif ou l'usage manque."""
    price_in, price_cache, price_out, _unit = kind_prices
    if price_in is None or price_out is None or used.get('entree') is None or used.get('sortie') is None:
        return None
    # Un schéma de cache additif (Messages) exige le normaliseur du fournisseur.
    # Ne pas le rabattre silencieusement sur le total d'un schéma inclusif.
    if (used.get('entree_cache') or 0) > used['entree']:
        return None
    cached = used.get('entree_cache') or 0
    plain = used['entree'] - cached if price_cache is not None else used['entree']
    total = plain * price_in + used['sortie'] * price_out + (cached * price_cache if price_cache is not None else 0)
    return round(total / 1_000_000, 6)


def _attribution(db, cfg):
    """Auteur de la demande en cours (contexte de la requête ou de la mission ; vide pour un automatisme) et empreinte du dossier de la
    décision de routage. Sert aux seuls droits de lecture du relevé : aucun nom de dossier n'est écrit ici."""
    auteur = dossier = ''
    try:
        from .membres5650 import current_author
        auteur = str(current_author() or '')[:200]
    except Exception:   # noqa: BLE001 — hors requête et hors mission : pas d'auteur
        pass
    if cfg.get('decision5682'):
        try:
            row = db.execute('SELECT dossier_h FROM decisions_5682 WHERE uid=?', (str(cfg['decision5682']),)).fetchone()
            dossier = str(row[0] or '') if row else ''
        except sqlite3.Error:
            pass
    return auteur, dossier


def note(cfg, usage, status):
    """Écrit ou révise l'observation d'un appel terminé ou interrompu ; jamais de texte. Rend la quantité dans l'unité de l'appel (dollars
    calculés pour une API, repère de crédits pour un abonnement) ou ``None`` si elle n'est pas connue. Un appel interrompu garde sa ligne :
    il peut avoir été facturé."""
    state_dir = cfg.get('state_dir')
    if not state_dir or not (Path(state_dir) / 'desk.sqlite3').is_file():
        return None
    from .consommation56109 import observer
    obs = observer(cfg, usage)
    n = obs['usage']
    prices = cfg.get('prix5682') or (None, None, None, 'usd')
    if obs['canal'] == 'api':
        quantity = None if obs['montant_micro'] is None else round(obs['montant_micro'] / 1_000_000, 6)
    elif prices[3] == 'credits':
        quantity = amount(prices, fields(usage))          # abonnement : repère de crédits, jamais un prix ni un solde de quota
    else:
        quantity = None
    stamp = datetime.now(timezone.utc).isoformat()
    # « entree » : jetons d'entrée connus (total, ou entrée ordinaire déclarée quand les catégories de cache d'un schéma additif manquent) ;
    # « hors_cache », « entree_cache » et « cache_ecrit » gardent les catégories disjointes, NULL quand elles ne sont pas fournies
    valeurs = {'entree': obs['entree_connue'], 'entree_cache': n['cache_lu'], 'sortie': n['sortie_facturable'], 'raisonnement': n['raisonnement'], 'quantite': quantity,
               'unite': prices[3], 'etat': status, 'format_usage': obs['format'], 'mesure': obs['mesure'], 'hors_cache': n['entree_hors_cache'],
               'cache_ecrit': n['cache_ecrit'], 'cache_ecrit_5m': n['cache_ecrit_5m'], 'cache_ecrit_1h': n['cache_ecrit_1h'], 'montant_micro': obs['montant_micro'],
               'etat_montant': obs['etat_montant'], 'devise': obs['devise'],
               'tarif_json': json.dumps(obs['tarif'], ensure_ascii=False, sort_keys=True) if obs['tarif'] else ''}
    call_id = str(cfg.get('_budget_call5689') or cfg.get('decision5682') or '')
    tentative = str(cfg.get('_tentative56109') or '')
    db = connect(state_dir)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN IMMEDIATE')
        ancienne = db.execute('SELECT * FROM conso_5682 WHERE call_id=? AND tentative=?', (call_id, tentative)).fetchone() if call_id or tentative else None
        if ancienne is None:
            auteur, dossier = _attribution(db, cfg)
            ligne = {'at': stamp, 'fournisseur': str(cfg.get('provider_id') or ''), 'modele': str(cfg.get('model') or ''), 'canal': obs['canal'],
                     'fonction': str(cfg.get('purpose') or ''), 'mission': str(cfg.get('mission5682') or ''), 'call_id': call_id, 'tentative': tentative,
                     'auteur': auteur, 'dossier_h': dossier, **valeurs}
            db.execute('INSERT INTO conso_5682(%s) VALUES(%s)' % (','.join(ligne), ','.join('?' for _ in ligne)), list(ligne.values()))
        else:
            # même appel, même émission : état cumulatif d'un flux, répétition de l'événement ou mesure tardive. La ligne est révisée,
            # jamais doublée ; une valeur connue n'est pas remplacée par une inconnue ; un appel terminé ne redevient pas interrompu.
            nouvelle = {k: (valeurs[k] if valeurs[k] is not None else ancienne[k]) for k in NOMBRES}
            if obs['mesure'] not in ('absente', 'incoherente', 'non_qualifiee') or not ancienne['format_usage']:
                nouvelle.update({k: valeurs[k] for k in QUALITES})
            nouvelle['etat'] = ancienne['etat'] if ancienne['etat'] == 'done' and status != 'done' else status
            change = {k: v for k, v in nouvelle.items() if v != ancienne[k]}
            if change:
                change.update(revision=int(ancienne['revision'] or 0) + 1, revise_le=stamp)
                db.execute('UPDATE conso_5682 SET %s WHERE id=?' % ','.join(k + '=?' for k in change), list(change.values()) + [ancienne['id']])
            quantity = nouvelle['quantite']
        cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
        db.execute('DELETE FROM conso_5682 WHERE at<?', (cutoff,))
        db.execute('DELETE FROM reservations_5682 WHERE at<?', (cutoff,))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
    return quantity


def estimate(prices, tokens_in, tokens_out):
    if prices[0] is None or prices[2] is None:
        return None
    return round((tokens_in * prices[0] + tokens_out * prices[2]) / 1_000_000, 6)


def used(state_dir, mission, unit):
    from .budget_ledger5689 import utilise_mission
    return utilise_mission(state_dir, mission, unit)


def reserve(state_dir, mission, quantity, unit, limit, decision='', provider='', model='', purpose=''):
    """Réserve avant l'appel. 5.6.89 : adaptateur du registre commun : un seul compteur, et une ligne par décision même sans mission."""
    if quantity is None or unit == 'aucune':
        return 0
    from .budget_ledger5689 import reserver_mission
    return reserver_mission(state_dir, mission, quantity, unit, limit, decision, provider, model, purpose)


def settle(state_dir, ident, state, actual=None):
    """Rapproche la réservation : consommée, libérée (rien de facturé) ou incertaine (flux coupé : l'usage réel n'est pas connu)."""
    from .budget_ledger5689 import regler_mission
    regler_mission(state_dir, ident, state, actual)


def snapshot(state_dir, debut=None, fin=None):
    """Période par fournisseur et canal : sommes des seules valeurs connues, appels sans usage comptés à part. Sans bornes : le mois en
    cours à l'heure de Paris. 5.6.109 : bornes ``at >= début`` et ``at < fin`` en UTC (plus de préfixe de date UTC, qui rangeait la première
    heure du mois du cabinet dans le mois précédent) ; lecture seule : ni création de table ni écriture."""
    from .consommation56109 import lecture, periode
    if debut is None or fin is None:
        mois = periode(None)
        debut, fin = mois['debut_utc'], mois['fin_utc']
    db = lecture(state_dir)
    if db is None:
        return []
    try:
        rows = db.execute("SELECT fournisseur,canal,unite,COUNT(*),SUM(entree),SUM(entree_cache),SUM(sortie),"
                          "SUM(quantite),SUM(CASE WHEN quantite IS NULL THEN 1 ELSE 0 END),COUNT(entree),COUNT(entree_cache),COUNT(sortie) FROM conso_5682 WHERE at>=? AND at<? "
                          "GROUP BY fournisseur,canal,unite ORDER BY fournisseur", (debut, fin)).fetchall()
    except sqlite3.Error:      # table pas encore créée (aucun appel) ou base occupée : rien à présenter, jamais un zéro inventé
        return []
    finally:
        db.close()
    return [dict(zip(('fournisseur', 'canal', 'unite', 'appels', 'entree', 'entree_cache', 'sortie', 'quantite', 'sans_usage', 'entree_connues', 'cache_connues', 'sortie_connues'), r)) for r in rows]
