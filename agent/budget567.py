"""Réservation atomique d'un budget avant l'envoi, même avec plusieurs workers. 5.6.89 : adaptateur du registre commun ``budget_ledger5689``, seule autorité."""
from decimal import Decimal, InvalidOperation

from .common import Stop

SCHEMA = '''CREATE TABLE IF NOT EXISTS ai_reservations_v567(
 id TEXT PRIMARY KEY, at TEXT NOT NULL, provider TEXT NOT NULL, amount REAL NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','uncertain','closed')));
 CREATE INDEX IF NOT EXISTS reservations567_month ON ai_reservations_v567(provider,at,state);'''


def money(value):
    try:
        amount = Decimal(str(value or 0))
    except InvalidOperation:
        raise Stop('tarif_fournisseur_invalide') from None
    if not amount.is_finite() or amount < 0:
        raise Stop('tarif_fournisseur_invalide')
    return amount


def reserve(cfg, messages, max_tokens, db_factory):
    if cfg.get('provider_type') == 'ollama':
        return 0.0
    price_in, price_out = money(cfg.get('input_usd_per_million')), money(cfg.get('output_usd_per_million'))
    if not (price_in or price_out) and cfg.get('canal') != 'abonnement':
        raise Stop('tarif_fournisseur_non_renseigne')
    # Majoration conservative : caractères UTF-8 / 2 au lieu d'une moyenne
    # anglophone / 4. Ce reste une estimation, jamais une garantie de facture.
    size = sum(len(str(m.get('content') or '').encode()) for m in messages)
    amount = (Decimal(max(1, (size + 1)//2)) * price_in + Decimal(max(0, int(max_tokens))) * price_out) / Decimal(1000000)
    per_request, monthly = money(cfg.get('per_request_budget_usd')), money(cfg.get('monthly_budget_usd'))
    if per_request and amount > per_request:
        raise Stop('budget_requete_fournisseur_depasse')
    if not cfg.get('state_dir') or not per_request or not monthly:
        raise Stop('budget_fournisseur_non_configure')
    from .budget_ledger5689 import reserver_appel   # 5.6.89 : le registre commun décide ; ai_reservations_v567 n'en est plus que la vue
    return reserver_appel(cfg, amount, db_factory)


def settle(db, cfg, status, released=False, usage=None):
    """Une panne peut avoir consommé des tokens : sa réservation reste « incertaine ». 5.6.68 : un échec certain non facturé (refus 4xx,
    panne 5xx, connexion impossible) la libère (``released``). 5.6.89 : le registre commun est réglé dans la même transaction."""
    ident = cfg.pop('_budget_reservation', '')
    if ident:
        db.execute('UPDATE ai_reservations_v567 SET state=? WHERE id=? AND state=\'reserved\'',
                   ('closed' if status == 'done' or released else 'uncertain', ident))
    from .budget_ledger5689 import regler_appel
    regler_appel(db, cfg, ident, status, released, usage)
