"""Réservation atomique d'un budget avant l'envoi, même avec plusieurs workers."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import secrets

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
    if not (price_in or price_out) and not cfg.get('pricing_confirmed_free', False):
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
    db = db_factory(cfg['state_dir'])
    try:
        db.executescript(SCHEMA)
        db.execute('BEGIN IMMEDIATE')
        stamp = datetime.now(timezone.utc).isoformat()
        prefix = stamp[:7] + '%'
        provider = cfg.get('provider_id', '')
        used = db.execute("SELECT COALESCE(SUM(estimated_cost_usd),0) FROM ai_usage_v391 WHERE provider=? AND at LIKE ? AND status='done'", (provider, prefix)).fetchone()[0]
        pending = db.execute("SELECT COALESCE(SUM(amount),0) FROM ai_reservations_v567 WHERE provider=? AND at LIKE ? AND state IN ('reserved','uncertain')", (provider, prefix)).fetchone()[0]
        if money(used) + money(pending) + amount > monthly:
            raise Stop('budget_mensuel_fournisseur_depasse')
        ident = secrets.token_hex(16)
        db.execute('INSERT INTO ai_reservations_v567 VALUES (?,?,?,?,?)', (ident, stamp, provider, float(amount), 'reserved'))
        db.commit()
        cfg['_budget_reservation'] = ident
        return float(amount)
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def settle(db, cfg, status):
    """Une panne peut avoir consommé des tokens : ne pas libérer sa réservation automatiquement."""
    ident = cfg.pop('_budget_reservation', '')
    if ident:
        db.execute('UPDATE ai_reservations_v567 SET state=? WHERE id=? AND state=\'reserved\'',
                   ('closed' if status == 'done' else 'uncertain', ident))
