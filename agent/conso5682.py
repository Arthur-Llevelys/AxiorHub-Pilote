"""5.6.82 : registre de consommation (ConsumptionLedger).

Trois canaux distincts : ``abonnement`` (ChatGPT, jetons connus, crédits seulement à titre de repère), ``api`` (dollars estimés), ``local``.
Les jetons sont repris des champs réellement fournis ; ceux de raisonnement sont déjà compris dans la sortie et ne sont pas ajoutés deux
fois. Un usage absent reste inconnu (« Non fournie ») : jamais zéro, jamais illimité.

La réservation de mission garde un budget cumulé pour un travail et ses enfants, y compris après un redémarrage. Elle est une limite locale
d'AxiorHub : ni une garantie de facturation, ni une connaissance du quota réel du fournisseur.
"""
from datetime import datetime, timedelta, timezone
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


def connect(state_dir):
    db = sqlite3.connect(Path(state_dir) / 'desk.sqlite3', timeout=10)
    db.executescript(SCHEMA)
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
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return None
    details_in = usage.get('input_tokens_details') if isinstance(usage.get('input_tokens_details'), dict) else {}
    details_out = usage.get('output_tokens_details') if isinstance(usage.get('output_tokens_details'), dict) else {}
    cached = details_in.get('cached_tokens') if isinstance(details_in.get('cached_tokens'), int) else pick('cache_read_input_tokens')
    return {'entree': pick('input_tokens', 'prompt_tokens'), 'entree_cache': cached, 'sortie': pick('output_tokens', 'completion_tokens'),
            'raisonnement': details_out.get('reasoning_tokens') if isinstance(details_out.get('reasoning_tokens'), int) else None}


def amount(kind_prices, used):
    """Quantité consommée dans l'unité du fournisseur (entrée en cache comptée à son propre tarif) ; None si le tarif ou l'usage manque."""
    price_in, price_cache, price_out, _unit = kind_prices
    if price_in is None or price_out is None or used.get('entree') is None or used.get('sortie') is None:
        return None
    cached = min(used.get('entree_cache') or 0, used['entree'])
    plain = used['entree'] - cached if price_cache is not None else used['entree']
    total = plain * price_in + used['sortie'] * price_out + (cached * price_cache if price_cache is not None else 0)
    return round(total / 1_000_000, 6)


def note(cfg, usage, status):
    """Écrit la ligne de consommation d'un appel terminé ou interrompu ; jamais de texte."""
    state_dir = cfg.get('state_dir')
    if not state_dir or not (Path(state_dir) / 'desk.sqlite3').is_file():
        return None
    used = fields(usage)
    prices = cfg.get('prix5682') or (None, None, None, 'usd')
    quantity = amount(prices, used)
    db = connect(state_dir)
    try:
        db.execute('INSERT INTO conso_5682(at,fournisseur,modele,canal,fonction,mission,entree,entree_cache,sortie,raisonnement,quantite,unite,etat) '
                   'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (datetime.now(timezone.utc).isoformat(), str(cfg.get('provider_id') or ''), str(cfg.get('model') or ''), canal(cfg),
                    str(cfg.get('purpose') or ''), str(cfg.get('mission5682') or ''), used['entree'], used['entree_cache'], used['sortie'],
                    used['raisonnement'], quantity, prices[3], status))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
        db.execute('DELETE FROM conso_5682 WHERE at<?', (cutoff,))
        db.execute('DELETE FROM reservations_5682 WHERE at<?', (cutoff,))
        db.commit()
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


def snapshot(state_dir):
    """Mois en cours par fournisseur et canal : sommes des seules valeurs connues, appels sans usage comptés à part."""
    db = connect(state_dir)
    prefix = datetime.now(timezone.utc).strftime('%Y-%m-') + '%'
    try:
        rows = db.execute("SELECT fournisseur,canal,unite,COUNT(*),COALESCE(SUM(entree),0),COALESCE(SUM(entree_cache),0),COALESCE(SUM(sortie),0),"
                          "COALESCE(SUM(quantite),0),SUM(CASE WHEN quantite IS NULL THEN 1 ELSE 0 END) FROM conso_5682 WHERE at LIKE ? "
                          "GROUP BY fournisseur,canal,unite ORDER BY fournisseur", (prefix,)).fetchall()
    finally:
        db.close()
    return [dict(zip(('fournisseur', 'canal', 'unite', 'appels', 'entree', 'entree_cache', 'sortie', 'quantite', 'sans_usage'), r)) for r in rows]
