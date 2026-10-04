"""Fonctions métier 5.0.0 — socle commun : tables additives, montants, journal d'accès.

Principes communs aux cinq fonctions (temps et honoraires, rendez-vous, conflits, prescription, pilotage) :
  - traitement LOCAL uniquement : aucun de ces modules n'appelle un modèle de langage, une passerelle d'IA ni un
    service externe (un test vérifie qu'ils n'importent aucun module réseau ni aucun module de modèle) ;
  - toute lecture de données sensibles (temps, honoraires, conflits) est inscrite dans le journal d'accès (zone, action,
    identifiant de dossier — jamais de contenu) ;
  - les tables sont créées par ``CREATE TABLE IF NOT EXISTS`` : migration additive, sans modification des tables
    existantes ; un retour arrière laisse ces tables en place, sans effet sur la version précédente ;
  - les montants sont des entiers en centimes d'euro.
"""
from .common import matter_display
from datetime import datetime, timezone
import re

from .common import Stop

EXTERNAL_TRANSMISSION = False   # constante documentaire : ces fonctions ne transmettent rien hors du cabinet

SCHEMA = '''
CREATE TABLE IF NOT EXISTS time500_terms(
  matter TEXT PRIMARY KEY, mode TEXT NOT NULL, rate_cents INTEGER NOT NULL DEFAULT 0, budget_cents INTEGER NOT NULL DEFAULT 0,
  alert_pct INTEGER NOT NULL DEFAULT 80, note TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS time500_proposals(
  id TEXT PRIMARY KEY, matter TEXT NOT NULL, day TEXT NOT NULL, seq INTEGER NOT NULL, minutes_est INTEGER NOT NULL,
  explanation TEXT NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL, decided TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS time500_prop_matter ON time500_proposals(matter, status, day);
CREATE TABLE IF NOT EXISTS time500_items(
  ref TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, kind TEXT NOT NULL, minutes INTEGER NOT NULL, label TEXT NOT NULL,
  day TEXT NOT NULL, matter TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS time500_items_prop ON time500_items(proposal_id);
CREATE TABLE IF NOT EXISTS time500_entries(
  id TEXT PRIMARY KEY, matter TEXT NOT NULL, day TEXT NOT NULL, minutes INTEGER NOT NULL, rate_cents INTEGER NOT NULL,
  amount_cents INTEGER NOT NULL, label TEXT NOT NULL, origin TEXT NOT NULL, proposal_id TEXT NOT NULL DEFAULT '',
  validated_at TEXT NOT NULL, created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS time500_entries_matter ON time500_entries(matter, day);
CREATE TABLE IF NOT EXISTS fees500_invoices(
  id TEXT PRIMARY KEY, matter TEXT NOT NULL, number TEXT NOT NULL, day TEXT NOT NULL, amount_cents INTEGER NOT NULL,
  origin TEXT NOT NULL, external_id TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', created TEXT NOT NULL,
  UNIQUE(matter, number));
CREATE TABLE IF NOT EXISTS fees500_alerts(
  matter TEXT NOT NULL, level TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(matter, level));
CREATE TABLE IF NOT EXISTS access500(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, area TEXT NOT NULL, action TEXT NOT NULL,
  matter TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS meeting500_fiches(
  event_id TEXT PRIMARY KEY, matter TEXT NOT NULL, generated TEXT NOT NULL, signature TEXT NOT NULL, content TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meeting500_reports(
  event_id TEXT PRIMARY KEY, matter TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL,
  created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conflicts500_checks(
  id TEXT PRIMARY KEY, matter TEXT NOT NULL DEFAULT '', trigger_kind TEXT NOT NULL, parties TEXT NOT NULL,
  result TEXT NOT NULL, status TEXT NOT NULL, decision TEXT NOT NULL DEFAULT '', decision_note TEXT NOT NULL DEFAULT '',
  decided TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS conflicts500_checks_matter ON conflicts500_checks(matter, created);
CREATE TABLE IF NOT EXISTS conflicts500_matters(
  matter TEXT PRIMARY KEY, first_seen TEXT NOT NULL, baseline INTEGER NOT NULL, check_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conflicts500_parties(
  id TEXT PRIMARY KEY, matter TEXT NOT NULL, name TEXT NOT NULL, norm TEXT NOT NULL, role TEXT NOT NULL,
  email TEXT NOT NULL DEFAULT '', source TEXT NOT NULL, created TEXT NOT NULL, UNIQUE(matter, norm, role));
CREATE INDEX IF NOT EXISTS conflicts500_parties_norm ON conflicts500_parties(norm);
CREATE TABLE IF NOT EXISTS limitation500(
  id TEXT PRIMARY KEY, matter TEXT NOT NULL, rule_id TEXT NOT NULL, start_event TEXT NOT NULL, start_date TEXT NOT NULL,
  start_note TEXT NOT NULL DEFAULT '', due TEXT NOT NULL DEFAULT '', calc TEXT NOT NULL DEFAULT '{}',
  error TEXT NOT NULL DEFAULT '', events TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL, origin TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '', calendar_uid TEXT NOT NULL DEFAULT '', calendar_state TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS limitation500_matter ON limitation500(matter, status);
CREATE INDEX IF NOT EXISTS limitation500_due ON limitation500(due);
CREATE TABLE IF NOT EXISTS limitation500_journal(
  id INTEGER PRIMARY KEY AUTOINCREMENT, limitation_id TEXT NOT NULL, at TEXT NOT NULL, action TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '', before TEXT NOT NULL DEFAULT '{}', after TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS limitation500_reminders(
  limitation_id TEXT NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(limitation_id, kind));
CREATE TABLE IF NOT EXISTS report500_snapshots(
  period TEXT PRIMARY KEY, generated TEXT NOT NULL, data TEXT NOT NULL);
'''


def ensure_schema(desk):
    # Contrôle léger : ``executescript`` valide toute transaction en cours, il ne doit donc pas être rejoué à chaque appel
    # (journal d'accès, passage de conflits dans la file de travail…).
    try:
        if desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='report500_snapshots'").fetchone():
            return
    except Exception:
        pass
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------------------------------- montants
def cents(value, field='montant'):
    """« 1 234,50 », « 1234.5 », 120 → 123450 / 120 € ; refuse le négatif et l'absurde."""
    if isinstance(value, bool):
        raise Stop('montant_invalide')
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = re.sub(r'[\s  €]|EUR|eur', '', str(value or ''))
        if not text:
            return 0
        if text.count(',') + text.count('.') > 1:
            # séparateur de milliers : le dernier séparateur est la décimale
            last = max(text.rfind(','), text.rfind('.'))
            text = text[:last].replace(',', '').replace('.', '') + '.' + text[last + 1:]
        text = text.replace(',', '.')
        if not re.fullmatch(r'\d{1,9}(?:\.\d{1,2})?', text):
            raise Stop('montant_invalide')
        number = float(text)
    if number < 0 or number > 10_000_000:
        raise Stop('montant_invalide')
    return int(round(number * 100))


def euros(amount_cents):
    """Affichage français : 1 234,50 €."""
    sign = '-' if amount_cents < 0 else ''
    whole, frac = divmod(abs(int(amount_cents)), 100)
    return '%s%s,%02d €' % (sign, '{:,}'.format(whole).replace(',', ' '), frac)


def hours(minutes):
    minutes = int(minutes)
    h, m = divmod(abs(minutes), 60)
    return ('%d h %02d' % (h, m)) if h else ('%d min' % m)


# ------------------------------------------------------------------------------------------- journal d'accès
def log_access(desk, area, action, matter=''):
    """Inscrit « qui regarde quoi » (zone, action, dossier) sans contenu ; une même lecture répétée est regroupée (30 s)."""
    ensure_schema(desk)
    stamp = now()
    last = desk.db.execute('SELECT at FROM access500 WHERE area=? AND action=? AND matter=? ORDER BY id DESC LIMIT 1',
                           (area, action, matter)).fetchone()
    if last:
        try:
            if (datetime.fromisoformat(stamp) - datetime.fromisoformat(last[0])).total_seconds() < 30:
                return False
        except ValueError:
            pass
    desk.db.execute('INSERT INTO access500(at,area,action,matter) VALUES(?,?,?,?)', (stamp, area, action, matter))
    desk.db.commit()
    desk.audit('acces_500', {'area': area, 'action': action, 'matter': matter})
    return True


def access_log(desk, limit=100, area=''):
    ensure_schema(desk)
    sql = 'SELECT at,area,action,matter FROM access500'
    params = []
    if area:
        sql += ' WHERE area=?'
        params.append(area)
    sql += ' ORDER BY id DESC LIMIT ?'
    params.append(max(1, min(int(limit), 500)))
    return [dict(r) for r in desk.db.execute(sql, params)]


def need_reason(reason, minimum=5):
    reason = re.sub(r'\s+', ' ', str(reason or '')).strip()
    if len(reason) < minimum:
        raise Stop('motif_obligatoire')
    return reason[:400]


def matter_index(desk):
    from .common import load_matters
    return {m['id']: m for m in load_matters(desk.c)}


def matter_label(desk, matter_id):
    m = matter_index(desk).get(matter_id)
    if not m:
        return matter_id
    return matter_display(m)


def require_matter(desk, matter_id):
    m = matter_index(desk).get(str(matter_id or ''))
    if not m:
        raise Stop('dossier_absent')
    return m
