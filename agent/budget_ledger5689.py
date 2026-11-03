"""5.6.89 : registre commun du budget (chapitres 43 et 44 du cahier).

Seule autorité sur la dépense : ``budget567.reserve``, ``ai_gateway.estimate_and_check_budget`` et ``conso5682.reserve`` en sont des adaptateurs.
Il n'y a qu'un compteur : les plafonds sont lus ici, sur les allocations du registre, augmentées des seules lignes anciennes qui n'y ont pas
de jumeau (``ai_usage_v391`` sans ``call_id``, ``ai_reservations_v567`` et ``reservations_5682`` d'avant la bascule ou d'un ancien travailleur).

- Plafonds : cabinet (année civile), mois (tout le cabinet), fournisseur (mois), mission. Un périmètre sans plafond n'est pas contrôlé.
- Réservation idempotente par ``call_id`` en transaction courte (``BEGIN IMMEDIATE``), jamais pendant le réseau.
- États : ``reserved`` (rien n'est parti) → ``dispatched`` (marqué avant l'envoi) → ``settled`` / ``uncertain`` ; ``released`` pour un échec certain.
  Une coupure après émission reste incertaine et garde son montant : ni zéro, ni libération, y compris à la migration.
- Montants en entiers (micro-unités) ; montant fournisseur, conversion, date du taux et coût retenu sont conservés séparément.
- Une mesure qui dépasse l'estimation est enregistrée telle quelle, jamais ramenée au plafond ; elle bloque les nouveaux appels concernés.
- Les crédits et remises sont des lignes distinctes : ils ne modifient pas l'usage brut.
- L'estimation par la taille du texte reste une approximation : ce registre tient un plafond AxiorHub, il ne garantit pas la facture du
  fournisseur, et les autres logiciels utilisant le même compte restent hors de ce plafond.
"""
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal, InvalidOperation
import json
from pathlib import Path
import secrets
import sqlite3
import uuid

from .common import Stop

VERSION = 1
PROTOCOLE = 1                   # protocole de budget des travailleurs ; un ancien travailleur n'en a pas
MICRO = Decimal(1_000_000)
CREDITS = 'CRD'                 # repère des abonnements : jamais additionné aux dollars
ETATS = ('reserved', 'dispatched', 'uncertain', 'settled', 'released')
OUVERTS = ('reserved', 'dispatched', 'uncertain')
ANCIENNE_HEURE = 3600           # une ligne « reserved » ou « dispatched » plus ancienne appartient à un appel interrompu sans retour
TOLERANCE = Decimal('1.2')      # une mesure au-delà de 120 % de la réservation bloque ce modèle jusqu'à l'acquittement
ATTENTE_RECONTROLE = 6          # heures
GARDER_JOURS = 800              # journal et réservations closes : deux exercices, pour le rapprochement des factures
SETTING = 'budget5689:plafonds'
CODES = {'fournisseur': 'budget_mensuel_fournisseur_depasse', 'mission': 'budget_mission_atteint', 'mois': 'budget_mois_atteint', 'cabinet': 'budget_cabinet_atteint'}
ATTENTE_CODES = frozenset(CODES.values()) | {'budget_bloque_depassement', 'budget_plafonds_illisibles'}
LIBELLES = {'fournisseur': 'plafond mensuel du fournisseur', 'mission': 'plafond de la mission', 'mois': 'plafond mensuel du cabinet', 'cabinet': 'plafond annuel du cabinet'}
MESSAGES = {'budget_mission_atteint': 'Plafond de la mission atteint : le travail attend une décision, rien n’a été transmis.',
            'budget_mois_atteint': 'Plafond mensuel du cabinet atteint : rien n’a été transmis.',
            'budget_cabinet_atteint': 'Plafond annuel du cabinet atteint : rien n’a été transmis.',
            'budget_bloque_depassement': 'Une mesure réelle a dépassé l’estimation pour ce modèle : nouveaux appels bloqués jusqu’au contrôle de ce dépassement.',
            'budget_plafonds_illisibles': 'Les plafonds du cabinet sont illisibles : aucun nouvel appel payant tant qu’ils ne sont pas corrigés.',
            'service_non_bornable': 'Le coût de ce service ne peut pas être borné : il faut une autorisation expresse de l’administrateur.',
            'budget_travailleur_ancien_refuse': 'Un ancien travailleur tente de réserver un budget : arrêtez-le et mettez-le à jour.'}
BILAN = (('licence', 'Licences'), ('hebergement', 'Hébergement ou amortissement du matériel'), ('energie', 'Énergie'), ('stockage', 'Stockage'), ('sauvegardes', 'Sauvegardes'),
         ('maintenance', 'Maintenance'), ('api', 'API des modèles'), ('sources', 'Sources juridiques'), ('connecteurs', 'Connecteurs'))

SCHEMA = '''
CREATE TABLE IF NOT EXISTS budget_meta5689(cle TEXT PRIMARY KEY, valeur TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS budget_caps5689(
  cabinet_id TEXT NOT NULL, cap_id TEXT NOT NULL, scope TEXT NOT NULL CHECK(scope IN ('cabinet','mois','mission','fournisseur')), cle TEXT NOT NULL,
  currency TEXT NOT NULL CHECK(length(currency)=3), period_start TEXT NOT NULL, period_end TEXT NOT NULL,
  limit_micro INTEGER NOT NULL CHECK(limit_micro>=0), revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
  PRIMARY KEY(cabinet_id,cap_id));
CREATE TABLE IF NOT EXISTS budget_reservations5689(
  cabinet_id TEXT NOT NULL, reservation_id TEXT NOT NULL, mission_id TEXT NOT NULL, call_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, legacy_ref TEXT,
  provider TEXT NOT NULL, model TEXT NOT NULL, purpose TEXT NOT NULL, currency TEXT NOT NULL CHECK(length(currency)=3),
  ceiling_micro INTEGER NOT NULL CHECK(ceiling_micro>=0), actual_micro INTEGER CHECK(actual_micro IS NULL OR actual_micro>=0),
  provider_amount_micro INTEGER, provider_currency TEXT, fx_rate TEXT, fx_date TEXT, retained_micro INTEGER,
  pricing_version TEXT NOT NULL, usage_json TEXT NOT NULL DEFAULT '{}', provider_ref TEXT NOT NULL DEFAULT '', bound TEXT NOT NULL DEFAULT 'estimation',
  origin TEXT NOT NULL DEFAULT 'appel', reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, dispatched_at TEXT, closed_at TEXT,
  state TEXT NOT NULL CHECK(state IN ('reserved','dispatched','uncertain','settled','released')),
  PRIMARY KEY(cabinet_id,reservation_id), UNIQUE(cabinet_id,call_id), UNIQUE(cabinet_id,mission_id,idempotency_key));
CREATE UNIQUE INDEX IF NOT EXISTS budget_res5689_legacy ON budget_reservations5689(legacy_ref) WHERE legacy_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS budget_res5689_state ON budget_reservations5689(state,created_at);
CREATE TABLE IF NOT EXISTS budget_allocations5689(
  cabinet_id TEXT NOT NULL, reservation_id TEXT NOT NULL, cap_id TEXT NOT NULL, reserved_micro INTEGER NOT NULL CHECK(reserved_micro>=0),
  settled_micro INTEGER CHECK(settled_micro IS NULL OR settled_micro>=0), PRIMARY KEY(cabinet_id,reservation_id,cap_id));
CREATE INDEX IF NOT EXISTS budget_alloc5689_cap ON budget_allocations5689(cap_id);
CREATE TABLE IF NOT EXISTS budget_credits5689(
  id TEXT PRIMARY KEY, cabinet_id TEXT NOT NULL, provider TEXT NOT NULL, nature TEXT NOT NULL CHECK(nature IN ('credit','remise')), currency TEXT NOT NULL CHECK(length(currency)=3),
  amount_micro INTEGER NOT NULL CHECK(amount_micro>=0), conditions TEXT NOT NULL DEFAULT '', expires_at TEXT, month TEXT NOT NULL, added_at TEXT NOT NULL, added_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS budget_events5689(
  id TEXT PRIMARY KEY, at TEXT NOT NULL, nature TEXT NOT NULL, call_id TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
  micro INTEGER, etat TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS budget_events5689_open ON budget_events5689(nature,etat);
CREATE TABLE IF NOT EXISTS budget_workers5689(worker TEXT PRIMARY KEY, protocole INTEGER NOT NULL, version TEXT NOT NULL, vu_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS couts_mensuels5689(
  id TEXT PRIMARY KEY, month TEXT NOT NULL, categorie TEXT NOT NULL, amount_micro INTEGER CHECK(amount_micro IS NULL OR amount_micro>=0), currency TEXT NOT NULL CHECK(length(currency)=3),
  statut TEXT NOT NULL CHECK(statut IN ('connu','estime','inconnu')), note TEXT NOT NULL DEFAULT '', saisi_par TEXT NOT NULL, saisi_le TEXT NOT NULL, UNIQUE(month,categorie));
'''
TRIGGERS = '''
CREATE TRIGGER IF NOT EXISTS budget5689_ancien_v567 BEFORE INSERT ON ai_reservations_v567
WHEN NOT EXISTS (SELECT 1 FROM budget_reservations5689 WHERE legacy_ref=NEW.id)
BEGIN SELECT RAISE(ABORT,'budget_travailleur_ancien_refuse'); END;
CREATE TRIGGER IF NOT EXISTS budget5689_ancien_5682 BEFORE INSERT ON reservations_5682
BEGIN SELECT RAISE(ABORT,'budget_travailleur_ancien_refuse'); END;
'''


class AttenteBudget(Stop):
    """``budget_wait`` : rien n'est parti ; une attente de budget n'est pas un échec."""

    def __init__(self, code, perimetre='', plafond=0, engage=0, demande=0):
        super().__init__(code)
        self.perimetre, self.plafond, self.engage, self.demande = perimetre, plafond, engage, demande

    def vue(self):
        return {'etat': 'budget_wait', 'code': str(self), 'perimetre': self.perimetre, 'libelle': LIBELLES.get(self.perimetre, ''), 'plafond_micro': self.plafond,
                'engage_micro': self.engage, 'demande_micro': self.demande, 'message': MESSAGES.get(str(self), 'Plafond atteint : rien n’a été transmis.')}


def _maintenant():
    return datetime.now(timezone.utc)


def _iso(moment=None):
    return (moment or _maintenant()).isoformat()


def micro(value):
    """Montant décimal (nombre ou texte) en micro-unités, arrondi au-dessus : une borne ne se sous-estime pas."""
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise Stop('montant_invalide') from None
    if not amount.is_finite() or amount < 0:
        raise Stop('montant_invalide')
    return int((amount * MICRO).to_integral_value(rounding=ROUND_CEILING))


def unites(value):
    return Decimal(int(value or 0)) / MICRO


def cabinet_ref(config=None):
    return str((config or {}).get('cabinet_id') or 'cabinet')


def connect(state_dir):
    from .ai_gateway import _usage_db
    db = _usage_db(state_dir)
    db.row_factory = sqlite3.Row
    ensure(db)
    return db


def _pret(db):
    try:
        row = db.execute("SELECT valeur FROM budget_meta5689 WHERE cle='schema'").fetchone()
    except sqlite3.Error:
        return False
    return bool(row) and str(row[0]) == str(VERSION)


def ensure(db):
    """Migration idempotente ; sûre si plusieurs travailleurs démarrent ensemble."""
    if _pret(db):
        return
    from .budget567 import SCHEMA as ANCIEN
    from .conso5682 import SCHEMA as CONSO
    db.executescript('BEGIN IMMEDIATE;' + ANCIEN.rstrip().rstrip(';') + ';' + CONSO + SCHEMA + 'COMMIT;')
    db.execute('BEGIN IMMEDIATE')
    try:
        if 'call_id' not in {r[1] for r in db.execute('PRAGMA table_info(ai_usage_v391)')}:
            db.execute("ALTER TABLE ai_usage_v391 ADD COLUMN call_id TEXT NOT NULL DEFAULT ''")
        db.execute("INSERT OR REPLACE INTO budget_meta5689 VALUES('schema',?)", (str(VERSION),))
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _evenement(db, nature, call_id='', provider='', model='', montant=None, etat='', detail=None):
    from .data_policy5689 import journal_sur
    db.execute('INSERT INTO budget_events5689(id,at,nature,call_id,provider,model,micro,etat,detail) VALUES(?,?,?,?,?,?,?,?,?)',
               (uuid.uuid4().hex, _iso(), nature, call_id, provider, model, montant, etat, json.dumps(journal_sur(detail or {}), ensure_ascii=False, sort_keys=True)))


# ------------------------------------------------------------------------------------------------ plafonds
def _mois(moment=None):
    return (moment or _maintenant()).strftime('%Y-%m')


def _fin_mois(mois):
    annee, numero = int(mois[:4]), int(mois[5:7])
    return '%04d-%02d-01' % (annee + (numero == 12), 1 if numero == 12 else numero + 1)


def _plafond(scope, cle, limite, devise, mois):
    if scope == 'mission':
        ident, debut, fin = 'mission:%s:%s' % (cle, devise), '', ''
    elif scope == 'cabinet':
        ident, debut, fin = 'cabinet:%s:%s' % (mois[:4], devise), mois[:4] + '-01-01', '%04d-01-01' % (int(mois[:4]) + 1)
    elif scope == 'mois':
        ident, debut, fin = 'mois:%s:%s' % (mois, devise), mois + '-01', _fin_mois(mois)
    else:
        ident, debut, fin = 'fournisseur:%s:%s:%s' % (cle, mois, devise), mois + '-01', _fin_mois(mois)
    return {'cap_id': ident, 'scope': scope, 'cle': cle, 'limit': limite, 'currency': devise, 'debut': debut, 'fin': fin, 'mois': mois}


def plafonds_cabinet(db, mois=None):
    """Périmètres du cabinet ; une valeur illisible bloque au lieu d'ouvrir. Sans réglage : limite ``None``, mais la dépense y reste allouée :
    un plafond fixé en cours de mois tient compte de ce qui est déjà engagé."""
    mois = mois or _mois()
    annuel = mensuel = None
    try:
        row = db.execute('SELECT value FROM settings WHERE key=?', (SETTING,)).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row:
        try:
            value = json.loads(row[0])
            if not isinstance(value, dict):
                raise ValueError
            annuel = micro(value['cabinet_usd']) if value.get('cabinet_usd') not in (None, '') else None
            mensuel = micro(value['mois_usd']) if value.get('mois_usd') not in (None, '') else None
        except (ValueError, TypeError, Stop):
            raise AttenteBudget('budget_plafonds_illisibles', 'cabinet') from None
    return [_plafond('cabinet', 'cabinet', annuel, 'USD', mois), _plafond('mois', 'cabinet', mensuel, 'USD', mois)]


def _anciens(db, cap):
    """Dépenses anciennes sans jumeau dans le registre : un travailleur ancien, ou une ligne d'avant la bascule, reste comptée."""
    if cap['currency'] == CREDITS:
        scope_sql, values = '', []
    elif cap['scope'] == 'fournisseur':
        scope_sql, values = ' AND provider=?', [cap['cle']]
    else:
        scope_sql, values = '', []
    total = Decimal(0)
    if cap['currency'] == 'USD' and cap['scope'] != 'mission':
        motif = (cap['debut'][:4] if cap['scope'] == 'cabinet' else cap['debut'][:7]) + '%'
        row = db.execute("SELECT COALESCE(SUM(estimated_cost_usd),0) FROM ai_usage_v391 WHERE status='done' AND at LIKE ? AND COALESCE(call_id,'')=''" + scope_sql, [motif] + values).fetchone()
        total += Decimal(str(row[0] or 0))
        row = db.execute("SELECT COALESCE(SUM(amount),0) FROM ai_reservations_v567 r WHERE state IN ('reserved','uncertain') AND at LIKE ?" + scope_sql +
                         ' AND NOT EXISTS(SELECT 1 FROM budget_reservations5689 b WHERE b.legacy_ref=r.id)', [motif] + values).fetchone()
        total += Decimal(str(row[0] or 0))
    if cap['scope'] == 'mission':
        unite = 'credits' if cap['currency'] == CREDITS else 'usd'
        row = db.execute("SELECT COALESCE(SUM(quantite),0) FROM reservations_5682 WHERE mission=? AND unite=? AND etat IN ('reserve','consomme','incertain')", (cap['cle'], unite)).fetchone()
        total += Decimal(str(row[0] or 0))
    return micro(total)


def engage(db, cap):
    """Engagements d'un plafond : coûts retenus, réservations ouvertes et incertaines, lignes anciennes."""
    row = db.execute('SELECT COALESCE(SUM(CASE WHEN settled_micro IS NOT NULL THEN settled_micro ELSE reserved_micro END),0) FROM budget_allocations5689 WHERE cap_id=?',
                     (cap['cap_id'],)).fetchone()
    return int(row[0] or 0) + _anciens(db, cap)


def _inscrire(db, cabinet, cap):
    db.execute('INSERT INTO budget_caps5689(cabinet_id,cap_id,scope,cle,currency,period_start,period_end,limit_micro) VALUES(?,?,?,?,?,?,?,?) '
               'ON CONFLICT(cabinet_id,cap_id) DO UPDATE SET limit_micro=excluded.limit_micro,revision=revision+1 WHERE limit_micro<>excluded.limit_micro',
               (cabinet, cap['cap_id'], cap['scope'], cap['cle'], cap['currency'], cap['debut'], cap['fin'], cap['limit'] or 0))


def _refermer_perimees(db):
    """Appels interrompus sans retour : rien n'est parti → libéré ; déjà parti → incertain, jamais zéro."""
    limite = _iso(_maintenant() - timedelta(seconds=ANCIENNE_HEURE))
    for row in db.execute("SELECT * FROM budget_reservations5689 WHERE state='reserved' AND created_at<? AND origin='appel'", (limite,)).fetchall():
        _clore(db, row, 'released', 'jamais_emise')
    for row in db.execute("SELECT * FROM budget_reservations5689 WHERE state='dispatched' AND dispatched_at<?", (limite,)).fetchall():
        db.execute("UPDATE budget_reservations5689 SET state='uncertain',reason='sans_retour' WHERE cabinet_id=? AND reservation_id=?", (row['cabinet_id'], row['reservation_id']))
        if row['legacy_ref']:
            db.execute("UPDATE ai_reservations_v567 SET state='uncertain' WHERE id=? AND state='reserved'", (row['legacy_ref'],))
    # Migration : une ligne reprise que l'ancien travailleur a close depuis (usage compté à part dans ai_usage_v391) ne compte pas deux fois
    for row in db.execute("SELECT b.* FROM budget_reservations5689 b JOIN ai_reservations_v567 r ON r.id=b.legacy_ref WHERE b.origin='migration' AND b.state='uncertain' AND r.state='closed'").fetchall():
        _clore(db, row, 'released', 'clos_par_ancien_travailleur')


def _reserver(db, cabinet, *, call_id, mission, cle, fournisseur, modele, fonction, montant, devise, plafonds, tarif, borne, legacy=None, miroir=None):
    """Corps transactionnel : tous les plafonds relus, tout ou rien, une seule réservation par ``call_id``."""
    existante = db.execute('SELECT * FROM budget_reservations5689 WHERE cabinet_id=? AND call_id=?', (cabinet, call_id)).fetchone()
    if existante is None:
        existante = db.execute('SELECT * FROM budget_reservations5689 WHERE cabinet_id=? AND mission_id=? AND idempotency_key=?', (cabinet, mission, cle)).fetchone()
    if existante is not None and existante['state'] not in OUVERTS:
        return existante
    deja = {r['cap_id']: r['reserved_micro'] for r in db.execute('SELECT cap_id,reserved_micro FROM budget_allocations5689 WHERE cabinet_id=? AND reservation_id=?',
                                                                 (cabinet, existante['reservation_id']))} if existante is not None else {}
    bloque = db.execute("SELECT 1 FROM budget_events5689 WHERE nature='depassement' AND etat='ouvert' AND provider=? AND model=? LIMIT 1", (fournisseur, modele)).fetchone()
    if bloque and fournisseur:
        raise AttenteBudget('budget_bloque_depassement', 'fournisseur', 0, 0, montant)
    plafond_total = max(montant, existante['ceiling_micro']) if existante is not None else montant
    for cap in plafonds:
        supplement = plafond_total - deja.get(cap['cap_id'], 0)
        if supplement <= 0 or cap['limit'] is None:
            continue
        deja_engage = engage(db, cap)
        if deja_engage + supplement > cap['limit']:
            raise AttenteBudget(CODES[cap['scope']], cap['scope'], cap['limit'], deja_engage, supplement)
    if existante is None:
        reservation = uuid.uuid4().hex
        db.execute('INSERT INTO budget_reservations5689(cabinet_id,reservation_id,mission_id,call_id,idempotency_key,legacy_ref,provider,model,purpose,currency,ceiling_micro,'
                   'pricing_version,usage_json,bound,created_at,state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (cabinet, reservation, mission, call_id, cle, legacy, fournisseur, modele, fonction, devise, plafond_total, tarif, '{}', borne, _iso(), 'reserved'))
    else:
        reservation = existante['reservation_id']
        db.execute('UPDATE budget_reservations5689 SET ceiling_micro=?,provider=CASE WHEN provider=\'\' THEN ? ELSE provider END,model=CASE WHEN model=\'\' THEN ? ELSE model END,'
                   'pricing_version=CASE WHEN pricing_version=\'\' THEN ? ELSE pricing_version END,legacy_ref=COALESCE(legacy_ref,?) WHERE cabinet_id=? AND reservation_id=?',
                   (plafond_total, fournisseur, modele, tarif, legacy, cabinet, reservation))
    for cap in plafonds:
        _inscrire(db, cabinet, cap)
        db.execute('INSERT INTO budget_allocations5689(cabinet_id,reservation_id,cap_id,reserved_micro) VALUES(?,?,?,?) '
                   'ON CONFLICT(cabinet_id,reservation_id,cap_id) DO UPDATE SET reserved_micro=excluded.reserved_micro', (cabinet, reservation, cap['cap_id'], plafond_total))
    if miroir:
        ligne = db.execute('SELECT * FROM budget_reservations5689 WHERE cabinet_id=? AND reservation_id=?', (cabinet, reservation)).fetchone()['legacy_ref']
        if not db.execute('SELECT 1 FROM ai_reservations_v567 WHERE id=?', (ligne,)).fetchone():   # vue conservée pour budget5668 : ce n'est pas un compteur
            db.execute('INSERT INTO ai_reservations_v567 VALUES (?,?,?,?,?)', (ligne, _iso(), fournisseur, float(unites(plafond_total)), 'reserved'))
    return db.execute('SELECT * FROM budget_reservations5689 WHERE cabinet_id=? AND reservation_id=?', (cabinet, reservation)).fetchone()


def _transaction(db, travail):
    db.execute('BEGIN IMMEDIATE')
    try:
        _refermer_perimees(db)
        out = travail()
        db.commit()
        return out
    except BaseException:
        db.rollback()
        raise


def tarif_de(cfg):
    return 'usd_par_million:%s/%s' % (cfg.get('input_usd_per_million') or 0, cfg.get('output_usd_per_million') or 0)


def reserver_appel(cfg, montant, db_factory):
    """Adaptateur de ``budget567.reserve`` : plafonds du fournisseur et du cabinet ; complète la réservation de la décision de routage s'il y en a une.
    Le montant et les plafonds viennent du serveur (configuration du fournisseur), jamais de la demande."""
    db = db_factory(cfg['state_dir'])
    db.row_factory = sqlite3.Row
    try:
        ensure(db)
        cabinet = cabinet_ref(cfg.get('external_policy_config'))
        provider = str(cfg.get('provider_id') or '')
        decision = str(cfg.get('decision5682') or '')
        ancienne = db.execute('SELECT state,legacy_ref,mission_id,idempotency_key FROM budget_reservations5689 WHERE cabinet_id=? AND call_id=?', (cabinet, decision)).fetchone() if decision else None
        if ancienne is not None and ancienne['state'] not in OUVERTS:
            decision, ancienne = '', None                       # la décision a déjà été réglée : c'est un nouvel appel, pas une répétition
        call_id = decision or uuid.uuid4().hex
        legacy = (ancienne['legacy_ref'] if ancienne is not None else None) or secrets.token_hex(16)
        mois = _mois()
        plafonds = [_plafond('fournisseur', provider, micro(cfg['monthly_budget_usd']), 'USD', mois)] + plafonds_cabinet(db, mois)
        ligne = _transaction(db, lambda: _reserver(db, cabinet, call_id=call_id, mission=ancienne['mission_id'] if ancienne is not None else str(cfg.get('mission5682') or ''),
                                                   cle=ancienne['idempotency_key'] if ancienne is not None else call_id, fournisseur=provider, modele=str(cfg.get('model') or ''),
                                                   fonction=str(cfg.get('purpose') or ''), montant=micro(montant), devise='USD', plafonds=plafonds, tarif=tarif_de(cfg),
                                                   borne='estimation', legacy=legacy, miroir=True))
        cfg['_budget_reservation'], cfg['_budget_call5689'] = ligne['legacy_ref'], call_id
        return float(unites(ligne['ceiling_micro']))
    finally:
        db.close()


def reserver_mission(state_dir, mission, quantite, unite, limite, call_id, fournisseur='', modele='', fonction='', config=None):
    """Adaptateur de ``conso5682.reserve`` : réservation de la décision de routage, sur la mission quand il y en a une."""
    devise = CREDITS if unite == 'credits' else 'USD'
    db = connect(state_dir)
    try:
        cabinet = cabinet_ref(config)
        montant = micro(quantite)
        plafonds = [_plafond('mission', mission, micro(limite), devise, _mois())] if mission and limite else []
        call_id = str(call_id or uuid.uuid4().hex)
        borne = 'forfait' if devise == CREDITS else 'estimation'
        if devise == 'USD':
            plafonds += plafonds_cabinet(db)
        ligne = _transaction(db, lambda: _reserver(db, cabinet, call_id=call_id, mission=mission, cle=call_id, fournisseur=fournisseur, modele=modele, fonction=fonction, montant=montant,
                                                   devise=devise, plafonds=plafonds, tarif='', borne=borne))
        return ligne['reservation_id']
    finally:
        db.close()


def reserver_service(desk, data, acteur):
    """Réservation directe (route d'administration) : idempotente, plafonds relus au serveur. Le navigateur ne fournit ni cabinet, ni plafond, ni état."""
    call_id = str(data.get('call_id') or '').strip()
    cle = str(data.get('idempotency_key') or '').strip()
    mission = str(data.get('mission_id') or '').strip()
    if not call_id or not cle or len(call_id) > 120 or len(cle) > 120:
        raise Stop('cle_idempotence_requise')
    if data.get('bornable') is False and data.get('autorisation') is not True:
        raise Stop('service_non_bornable')
    fournisseur = str(data.get('fournisseur') or '')[:80]
    from .ai_gateway import provider_registry
    provider = provider_registry(desk.c).get(fournisseur) if fournisseur else None
    db = connect(desk.c['state_dir'])
    try:
        cabinet = cabinet_ref(desk.c)
        mois = _mois()
        plafonds = list(plafonds_cabinet(db, mois))
        if provider and provider.get('monthly_budget_usd'):
            plafonds.append(_plafond('fournisseur', fournisseur, micro(provider['monthly_budget_usd']), 'USD', mois))
        rule = None
        if mission:
            from .politique5682 import normalize
            rule = normalize(desk.settings('ai:politique', {}) or {})
            plafonds.append(_plafond('mission', mission, micro(rule['plafond_mission_usd']), 'USD', mois))
        montant = micro(data.get('montant_usd'))
        ligne = _transaction(db, lambda: _reserver(db, cabinet, call_id=call_id, mission=mission, cle=cle, fournisseur=fournisseur, modele=str(data.get('modele') or '')[:120],
                                                   fonction=str(data.get('fonction') or '')[:60], montant=montant, devise='USD', plafonds=plafonds, tarif=str(data.get('tarif') or 'declare')[:120],
                                                   borne='autorisation' if data.get('bornable') is False else 'estimation'))
        return vue(ligne)
    finally:
        db.close()


def vue(ligne):
    """Forme du contrat BudgetReservation."""
    return {'reservation_id': ligne['reservation_id'], 'cabinet_id': ligne['cabinet_id'], 'mission_id': ligne['mission_id'], 'call_id': ligne['call_id'],
            'idempotency_key': ligne['idempotency_key'], 'currency': ligne['currency'], 'ceiling_micro': ligne['ceiling_micro'], 'actual_micro': ligne['actual_micro'],
            'pricing_version': ligne['pricing_version'] or 'non_renseigne', 'state': ligne['state']}


# ------------------------------------------------------------------------------------------------ émission
def preparer_emission(cfg):
    """À appeler juste après la réservation. ``arret5643.interruptible`` exécute le marquage au dernier moment, après son contrôle de pause."""
    cfg['_emission5689'] = 'preparee'

    def emettre():
        marquer_emis(cfg)
        cfg['_emission5689'] = 'envoyee'
    cfg['_emettre5689'] = emettre


def noter_forfait(cfg):
    """Abonnement appelé hors du routeur : coût forfaitaire, mais l'appel figure au registre comme les autres."""
    state_dir = cfg.get('state_dir')
    if not state_dir or cfg.get('decision5682') or not (Path(state_dir) / 'desk.sqlite3').is_file():
        return
    call_id = uuid.uuid4().hex
    reserver_mission(state_dir, str(cfg.get('mission5682') or ''), Decimal(0), 'credits', 0, call_id, str(cfg.get('provider_id') or ''), str(cfg.get('model') or ''), str(cfg.get('purpose') or ''))
    cfg['_budget_call5689'] = call_id


def marquer_emis(cfg):
    state_dir = cfg.get('state_dir')
    legacy, call_id = cfg.get('_budget_reservation'), cfg.get('_budget_call5689') or cfg.get('decision5682')
    if not state_dir or not (legacy or call_id) or not (Path(state_dir) / 'desk.sqlite3').is_file():
        return
    db = connect(state_dir)
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE budget_reservations5689 SET state='dispatched',dispatched_at=? WHERE state='reserved' AND (legacy_ref=? OR call_id=?)", (_iso(), legacy or '', call_id or ''))
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------------------------------------ règlement
def _clore(db, ligne, etat, motif, retenu=None):
    """Clôture commune : allocations et vue ancienne suivent, dans la transaction de l'appelant."""
    valeur = 0 if etat == 'released' else retenu
    db.execute('UPDATE budget_reservations5689 SET state=?,reason=?,closed_at=? WHERE cabinet_id=? AND reservation_id=?', (etat, motif, _iso(), ligne['cabinet_id'], ligne['reservation_id']))
    db.execute('UPDATE budget_allocations5689 SET settled_micro=? WHERE cabinet_id=? AND reservation_id=?', (valeur, ligne['cabinet_id'], ligne['reservation_id']))
    if ligne['legacy_ref']:
        db.execute("UPDATE ai_reservations_v567 SET state='closed' WHERE id=? AND state IN ('reserved','uncertain')", (ligne['legacy_ref'],))


def _regler(db, ligne, montant, devise=None, taux=None, date_taux=None, reference='', usage=None):
    """Règlement idempotent : la répétition d'un même ``call_id`` n'est comptée qu'une fois ; une mesure différente est notée, jamais recomptée."""
    if ligne['state'] == 'settled':
        if ligne['retained_micro'] != montant:
            _evenement(db, 'ecart_reglement', ligne['call_id'], ligne['provider'], ligne['model'], montant, 'a_controler', {'retenu': ligne['retained_micro']})
        return False
    taux_texte = str(taux) if taux is not None else '1'
    try:
        retenu = int((Decimal(montant) * Decimal(taux_texte)).to_integral_value(rounding=ROUND_CEILING))
    except InvalidOperation:
        raise Stop('taux_invalide') from None
    db.execute('UPDATE budget_reservations5689 SET actual_micro=?,provider_amount_micro=?,provider_currency=?,fx_rate=?,fx_date=?,retained_micro=?,provider_ref=?,usage_json=? '
               'WHERE cabinet_id=? AND reservation_id=?',
               (retenu, montant, devise or ligne['currency'], taux_texte, date_taux or _iso()[:10], retenu, str(reference)[:120], json.dumps(usage or {}, sort_keys=True),
                ligne['cabinet_id'], ligne['reservation_id']))
    _clore(db, ligne, 'settled', 'mesure' if ligne['state'] != 'released' else 'mesure_apres_liberation', retenu)
    plafond = ligne['ceiling_micro']
    if retenu > plafond:
        bloquant = Decimal(retenu) > Decimal(plafond) * TOLERANCE
        _evenement(db, 'depassement', ligne['call_id'], ligne['provider'], ligne['model'], retenu - plafond, 'ouvert' if bloquant else 'note', {'reserve': plafond, 'retenu': retenu})
    return True


def cout_usage(cfg, usage):
    """Coût mesuré d'après les jetons fournis et le tarif de l'appel ; None si l'usage n'est pas fourni."""
    from .conso5682 import fields
    used = fields(usage)
    if used['entree'] is None and used['sortie'] is None:
        return None
    total = Decimal(used['entree'] or 0) * Decimal(str(cfg.get('input_usd_per_million') or 0)) + Decimal(used['sortie'] or 0) * Decimal(str(cfg.get('output_usd_per_million') or 0))
    return int(total.to_integral_value(rounding=ROUND_CEILING))       # dollars par million de jetons × jetons = micro-dollars


def regler_appel(db, cfg, legacy, status, released, usage):
    """Adaptateur de ``budget567.settle``, dans la transaction de ``record_usage``."""
    if not _pret(db):
        return
    db.row_factory = sqlite3.Row
    call_id = cfg.get('_budget_call5689') or cfg.get('decision5682') or ''
    if not legacy and not call_id:
        return
    ligne = db.execute('SELECT * FROM budget_reservations5689 WHERE (legacy_ref=? AND legacy_ref<>\'\') OR call_id=?', (legacy or '', call_id)).fetchone()
    if ligne is None or ligne['state'] == 'settled':
        return
    if status == 'done':
        montant = cout_usage(cfg, usage) if ligne['currency'] != CREDITS else None
        if montant is not None:
            _regler(db, ligne, montant, usage=_jetons(usage))
        elif ligne['currency'] == CREDITS:
            _clore(db, ligne, 'settled', 'forfait', ligne['ceiling_micro'])
        else:
            db.execute("UPDATE budget_reservations5689 SET state='uncertain',reason='usage_non_fourni' WHERE cabinet_id=? AND reservation_id=?", (ligne['cabinet_id'], ligne['reservation_id']))
    elif released:
        _clore(db, ligne, 'released', 'non_facture')
    else:
        db.execute("UPDATE budget_reservations5689 SET state='uncertain',reason='coupure_apres_emission' WHERE cabinet_id=? AND reservation_id=? AND state IN ('reserved','dispatched')",
                   (ligne['cabinet_id'], ligne['reservation_id']))


def _jetons(usage):
    from .conso5682 import fields
    return {k: v for k, v in fields(usage).items() if v is not None}


def regler_mission(state_dir, ident, etat, reel=None):
    """Adaptateur de ``conso5682.settle``. ``consomme`` sans mesure retient le montant réservé ; ``liberee`` ou ``incertain`` ne libèrent que ce qui n'est jamais parti."""
    if not ident:
        return
    db = connect(state_dir)
    try:
        def travail():
            ligne = db.execute('SELECT * FROM budget_reservations5689 WHERE reservation_id=?', (str(ident),)).fetchone()
            if ligne is None or ligne['state'] in ('settled', 'released'):
                return
            if etat == 'consomme':
                if reel is not None:
                    _regler(db, ligne, micro(reel))
                else:
                    _clore(db, ligne, 'settled', 'estimation_retenue', ligne['ceiling_micro'])
            elif ligne['state'] == 'reserved':
                _clore(db, ligne, 'released', 'jamais_emise')
            elif etat == 'incertain' and ligne['state'] == 'dispatched':
                db.execute("UPDATE budget_reservations5689 SET state='uncertain',reason='coupure_apres_emission' WHERE cabinet_id=? AND reservation_id=?", (ligne['cabinet_id'], ligne['reservation_id']))
        _transaction(db, travail)
    finally:
        db.close()


def rapprocher(state_dir, call_id, montant, devise='USD', taux=None, date_taux=None, reference='', usage=None):
    """Mesure tardive ou facture : soldée une seule fois. Une ligne libérée à tort est reprise, l'événement comptable n'est jamais rejeté."""
    db = connect(state_dir)
    try:
        def travail():
            ligne = db.execute('SELECT * FROM budget_reservations5689 WHERE call_id=?', (str(call_id),)).fetchone()
            if ligne is None:
                raise Stop('reservation_absente')
            compte = _regler(db, ligne, micro(montant), devise, taux, date_taux, reference, usage)
            nouvelle = db.execute('SELECT * FROM budget_reservations5689 WHERE call_id=?', (str(call_id),)).fetchone()
            if compte:
                _evenement(db, 'rapprochement', ligne['call_id'], ligne['provider'], ligne['model'], nouvelle['retained_micro'], 'fait', {'etat_precedent': ligne['state']})
            return {**vue(nouvelle), 'compte': compte}
        return _transaction(db, travail)
    finally:
        db.close()


def liberer_refs(db, references, acteur):
    """Libération par l'administrateur après contrôle de la facture (``budget5668.release``), dans sa transaction. Jamais appelée par une migration."""
    if not _pret(db):
        return
    db.row_factory = sqlite3.Row
    for reference in references:
        ligne = db.execute('SELECT * FROM budget_reservations5689 WHERE legacy_ref=?', (reference,)).fetchone()
        if ligne is not None and ligne['state'] in OUVERTS:
            _clore(db, ligne, 'released', 'facture_controlee')
            _evenement(db, 'liberation_administrateur', ligne['call_id'], ligne['provider'], ligne['model'], ligne['ceiling_micro'], 'fait', {'par_h': _h(acteur)})


def _h(valeur):
    import hashlib
    return hashlib.sha256(str(valeur or '').encode()).hexdigest()[:16]


def acquitter(desk, evenement, acteur):
    """Le cabinet a contrôlé un dépassement : le modèle redevient utilisable. Le montant, lui, reste enregistré."""
    db = connect(desk.c['state_dir'])
    try:
        def travail():
            n = db.execute("UPDATE budget_events5689 SET etat='acquitte' WHERE id=? AND nature='depassement' AND etat='ouvert'", (str(evenement),)).rowcount
            if not n:
                raise Stop('evenement_absent')
        _transaction(db, travail)
    finally:
        db.close()
    desk.audit('budget5689_depassement_acquitte', {'evenement': str(evenement)[:40], 'par': acteur})
    return {'ok': True, 'message': 'Dépassement contrôlé : ce modèle peut de nouveau être appelé. Son coût reste au bilan.'}


# ------------------------------------------------------------------------------------------------ lectures
def _cap_mission(mission, unite):
    return _plafond('mission', str(mission), 0, CREDITS if unite == 'credits' else 'USD', _mois())


def utilise_mission(state_dir, mission, unite):
    db = connect(state_dir)
    try:
        return float(unites(engage(db, _cap_mission(mission, unite))))
    finally:
        db.close()


def engage_fournisseur_usd(state_dir, provider, mois=None):
    """Engagements du mois chez un fournisseur (lecture pour le choix du modèle : la réservation, seule, décide)."""
    db = connect(state_dir)
    try:
        return unites(engage(db, _plafond('fournisseur', str(provider), 0, 'USD', mois or _mois())))
    finally:
        db.close()


def admissible(state_dir, provider, cfg, montant):
    """Filtre du routeur : un fournisseur dont le plafond ne couvre plus l'estimation n'est pas candidat. Lecture seule ; ne réserve rien."""
    limite = cfg.get('monthly_budget_usd')
    if not limite or montant is None or not state_dir:
        return True
    return engage_fournisseur_usd(state_dir, provider) + Decimal(str(montant)) <= Decimal(str(limite))


def mission(desk, ident):
    """Estimation, plafond, coût provisoire ou réel d'une mission. Sans ligne, rien n'est présenté comme nul : ``etat`` le dit."""
    db = connect(desk.c['state_dir'])
    try:
        lignes = db.execute('SELECT state,currency,ceiling_micro,retained_micro,provider,model FROM budget_reservations5689 WHERE mission_id=?', (str(ident),)).fetchall()
        from .politique5682 import normalize
        rule = normalize(desk.settings('ai:politique', {}) or {})
        out = {'mission_id': str(ident), 'plafond_usd': str(rule['plafond_mission_usd']), 'appels': len(lignes), 'reel_micro': 0, 'provisoire_micro': 0, 'incertain_micro': 0, 'credits_micro': 0}
        for r in lignes:
            if r['currency'] == CREDITS:
                out['credits_micro'] += int(r['retained_micro'] or r['ceiling_micro']) if r['state'] != 'released' else 0
            elif r['state'] == 'settled':
                out['reel_micro'] += int(r['retained_micro'] or 0)
            elif r['state'] == 'uncertain':
                out['incertain_micro'] += int(r['ceiling_micro'])
            elif r['state'] in OUVERTS:
                out['provisoire_micro'] += int(r['ceiling_micro'])
        out['etat'] = 'aucun_appel_facturable_enregistre' if not lignes else ('incertain' if out['incertain_micro'] else ('provisoire' if out['provisoire_micro'] else 'reel'))
        out['limite'] = 'Plafond AxiorHub : l’estimation par la taille du texte n’est pas une garantie de facture.'
        return out
    finally:
        db.close()


def etat(desk):
    """Plafonds et dépassements du mois, pour la page et le diagnostic."""
    from .ai_gateway import provider_registry
    db = connect(desk.c['state_dir'])
    try:
        mois = _mois()
        rows = []
        caps = []
        try:
            caps = plafonds_cabinet(db, mois)
        except AttenteBudget as error:
            rows.append({'perimetre': 'cabinet', 'libelle': LIBELLES['cabinet'], 'erreur': str(error)})
        for pid, item in sorted(provider_registry(desk.c).items()):
            if item.get('monthly_budget_usd'):
                caps.append(_plafond('fournisseur', pid, micro(item['monthly_budget_usd']), 'USD', mois))
        for cap in [c for c in caps if c['limit'] is not None]:
            rows.append({'perimetre': cap['scope'], 'cle': cap['cle'], 'libelle': LIBELLES[cap['scope']], 'plafond_micro': cap['limit'], 'engage_micro': engage(db, cap)})
        ouverts = [dict(r) for r in db.execute("SELECT id,at,provider,model,micro FROM budget_events5689 WHERE nature='depassement' AND etat='ouvert' ORDER BY at DESC")]
        incertains = db.execute("SELECT COUNT(*),COALESCE(SUM(ceiling_micro),0) FROM budget_reservations5689 WHERE state='uncertain'").fetchone()
        return {'mois': mois, 'plafonds': rows, 'depassements_ouverts': ouverts, 'incertains': int(incertains[0]), 'incertains_micro': int(incertains[1])}
    finally:
        db.close()


def enregistrer_plafonds(desk, data, acteur, version):
    """Plafonds du cabinet, en dollars comme les connexions. Vide : pas de plafond de ce périmètre (annoncé sur la page). 409 si la page est périmée."""
    actuel = desk.settings(SETTING, {}) or {}
    if not isinstance(actuel, dict):
        actuel = {}
    if int(version if version not in (None, '') else -1) != int(actuel.get('revision') or 0):
        raise Stop('version_en_conflit')
    valeur = {'revision': int(actuel.get('revision') or 0) + 1, 'par': _h(acteur), 'le': _iso()}
    for cle in ('cabinet_usd', 'mois_usd'):
        brut = data.get(cle)
        valeur[cle] = None if brut in (None, '') else str(unites(micro(brut)).normalize())
    desk.setting(SETTING, valeur)
    desk.audit('budget5689_plafonds', {k: valeur[k] for k in ('cabinet_usd', 'mois_usd', 'revision')})
    reprendre_attentes(desk)
    return {'ok': True, 'revision': valeur['revision'], 'message': 'Plafonds enregistrés : ils s’appliquent dès le prochain appel.'}


def ajouter_credit(desk, data, acteur):
    """Crédit ou remise : ligne distincte, jamais retranchée de l'usage brut."""
    nature = data.get('nature')
    if nature not in ('credit', 'remise'):
        raise Stop('nature_credit_invalide')
    mois = str(data.get('mois') or _mois())[:7]
    ident = uuid.uuid4().hex
    db = connect(desk.c['state_dir'])
    try:
        _transaction(db, lambda: db.execute('INSERT INTO budget_credits5689 VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                                            (ident, cabinet_ref(desk.c), str(data.get('fournisseur') or '')[:80], nature, 'USD', micro(data.get('montant_usd')), str(data.get('conditions') or '')[:400],
                                             str(data.get('expire_le') or '')[:20] or None, mois, _iso(), _h(acteur))))
    finally:
        db.close()
    desk.audit('budget5689_credit', {'nature': nature, 'mois': mois})
    return {'ok': True, 'id': ident, 'message': 'Ligne enregistrée. L’usage brut reste inchangé.'}


def saisir_cout(desk, data, acteur):
    """Coût fixe du mois (saisie du cabinet). ``inconnu`` reste inconnu : aucune valeur n'est déduite."""
    categorie = data.get('categorie')
    if categorie not in {c[0] for c in BILAN} - {'api'}:
        raise Stop('categorie_cout_invalide')
    statut = data.get('statut') if data.get('statut') in ('connu', 'estime', 'inconnu') else 'connu'
    brut = data.get('montant')
    montant = None if statut == 'inconnu' or brut in (None, '') else micro(brut)
    if montant is None:
        statut = 'inconnu'
    mois = str(data.get('mois') or _mois())[:7]
    db = connect(desk.c['state_dir'])
    try:
        _transaction(db, lambda: db.execute('INSERT INTO couts_mensuels5689 VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(month,categorie) DO UPDATE SET amount_micro=excluded.amount_micro,'
                                            'statut=excluded.statut,note=excluded.note,saisi_par=excluded.saisi_par,saisi_le=excluded.saisi_le',
                                            (uuid.uuid4().hex, mois, categorie, montant, str(data.get('devise') or 'USD')[:3].upper(), statut, str(data.get('note') or '')[:400], _h(acteur), _iso())))
    finally:
        db.close()
    return {'ok': True, 'message': 'Coût enregistré pour ' + mois + '.'}


def _appels_locaux(desk, mois):
    try:
        row = desk.db.execute('SELECT COUNT(*) FROM decisions_5682 WHERE at LIKE ? AND unite=\'aucune\'', (mois + '%',)).fetchone()
        return int(row[0] or 0)
    except sqlite3.Error:
        return 0


def bilan_mensuel(desk, mois=None):
    """Bilan par catégorie. Une catégorie non renseignée reste « inconnue » : le total n'est alors jamais présenté comme complet.
    Ce bilan ne crée ni temps, ni diligence, ni ligne de facture : l'activité de l'IA n'est pas convertie en temps facturable."""
    mois = str(mois or _mois())[:7]
    db = connect(desk.c['state_dir'])
    try:
        saisis = {r['categorie']: r for r in db.execute('SELECT * FROM couts_mensuels5689 WHERE month=?', (mois,))}
        api = db.execute("SELECT COALESCE(SUM(CASE WHEN state='settled' THEN retained_micro ELSE 0 END),0),COALESCE(SUM(CASE WHEN state='uncertain' THEN ceiling_micro ELSE 0 END),0),"
                         "COALESCE(SUM(CASE WHEN state IN ('reserved','dispatched') THEN ceiling_micro ELSE 0 END),0),COUNT(*) FROM budget_reservations5689 WHERE currency='USD' AND created_at LIKE ?",
                         (mois + '%',)).fetchone()
        anciens = micro(Decimal(str(db.execute("SELECT COALESCE(SUM(estimated_cost_usd),0) FROM ai_usage_v391 WHERE status='done' AND at LIKE ? AND COALESCE(call_id,'')=''", (mois + '%',)).fetchone()[0] or 0)))
        credits_ = [dict(r) for r in db.execute('SELECT nature,provider,amount_micro,currency,conditions,expires_at FROM budget_credits5689 WHERE month=?', (mois,))]
    finally:
        db.close()
    lignes, total, complet = [], 0, True
    for cle, libelle in BILAN:
        if cle == 'api':
            confirme = int(api[0]) + anciens
            ligne = {'categorie': cle, 'libelle': libelle, 'montant_micro': confirme, 'etat': 'provisoire' if api[1] or api[2] else 'connu', 'incertain_micro': int(api[1]), 'en_cours_micro': int(api[2]),
                     'note': 'Usage brut. Montant confirmé ; les appels coupés restent à part jusqu’au rapprochement.' if api[1] else 'Usage brut, hors crédits et remises.'}
            total += confirme
        else:
            r = saisis.get(cle)
            if r is None or r['amount_micro'] is None:
                ligne = {'categorie': cle, 'libelle': libelle, 'montant_micro': None, 'etat': 'inconnu', 'note': 'Non renseigné : ce coût n’est pas nul.'}
                complet = False
            else:
                ligne = {'categorie': cle, 'libelle': libelle, 'montant_micro': int(r['amount_micro']), 'devise': r['currency'], 'etat': r['statut'], 'note': r['note']}
                if r['currency'] == 'USD':
                    total += int(r['amount_micro'])
                else:
                    complet = False       # aucune conversion inventée entre devises
        lignes.append(ligne)
    locaux = _appels_locaux(desk, mois)
    limites = ['Plafond et bilan AxiorHub : les autres logiciels utilisant le même compte fournisseur n’y figurent pas.', 'Aucune conversion automatique en temps facturable.']
    if locaux:
        limites.insert(0, 'Modèle local : %d appel(s) sans coût d’API. Le coût total du local n’est pas nul : matériel, énergie, stockage et maintenance restent à renseigner.' % locaux)
    return {'mois': mois, 'devise': 'USD', 'lignes': lignes, 'total_connu_micro': total, 'total_complet': complet, 'credits': credits_, 'appels_locaux': locaux, 'limites': limites}


# ------------------------------------------------------------------------------------------------ bascule et travailleurs
def prochaine_verification(code):
    """Date de reprise d'un travail en attente de budget : au plus tard au début du mois suivant pour un plafond mensuel."""
    moment = _maintenant() + timedelta(hours=ATTENTE_RECONTROLE)
    if code in ('budget_mensuel_fournisseur_depasse', 'budget_mois_atteint'):
        fin = datetime.fromisoformat(_fin_mois(_mois()) + 'T00:00:05+00:00')
        moment = min(moment, fin)
    return moment.isoformat()


def reprendre_attentes(desk):
    """Plafond relevé : les travaux en attente de budget sont relus sans attendre leur prochain contrôle."""
    marques = ','.join('?' for _ in ATTENTE_CODES)
    desk.db.execute("UPDATE jobs SET not_before=?,blocked_by='' WHERE status='pending' AND blocked_by IN (" + marques + ')', [desk.now()] + sorted(ATTENTE_CODES))
    desk.db.commit()


def _reprendre_anciennes(db):
    """Lignes ouvertes d'avant la bascule : reprises « incertaines », montant conservé, provenance notée. Rien n'est libéré, rien n'est remis à zéro."""
    cabinet = 'cabinet'
    reprises = 0
    for r in db.execute("SELECT * FROM ai_reservations_v567 WHERE state IN ('reserved','uncertain')").fetchall():
        if db.execute('SELECT 1 FROM budget_reservations5689 WHERE legacy_ref=?', (r['id'],)).fetchone():
            continue
        ident = uuid.uuid4().hex
        montant = micro(Decimal(str(r['amount'])))
        mois = str(r['at'])[:7]
        db.execute('INSERT INTO budget_reservations5689(cabinet_id,reservation_id,mission_id,call_id,idempotency_key,legacy_ref,provider,model,purpose,currency,ceiling_micro,pricing_version,'
                   "bound,origin,reason,created_at,dispatched_at,state) VALUES(?,?,'',?,?,?,?,'','','USD',?,'avant_5689','estimation','migration',?,?,?,'uncertain')",
                   (cabinet, ident, 'migration-' + r['id'], 'migration-' + r['id'], r['id'], r['provider'], montant, 'repris_' + r['state'], r['at'], r['at']))
        for cap in (_plafond('fournisseur', r['provider'], 0, 'USD', mois), _plafond('mois', 'cabinet', 0, 'USD', mois), _plafond('cabinet', 'cabinet', 0, 'USD', mois)):
            db.execute('INSERT OR IGNORE INTO budget_caps5689(cabinet_id,cap_id,scope,cle,currency,period_start,period_end,limit_micro) VALUES(?,?,?,?,?,?,?,0)',
                       (cabinet, cap['cap_id'], cap['scope'], cap['cle'], cap['currency'], cap['debut'], cap['fin']))
            db.execute('INSERT OR IGNORE INTO budget_allocations5689(cabinet_id,reservation_id,cap_id,reserved_micro) VALUES(?,?,?,?)', (cabinet, ident, cap['cap_id'], montant))
        reprises += 1
    return reprises


def preparer(config, worker='', version=''):
    """Au démarrage de chaque travailleur ou du service web : schéma, reprise des réservations ouvertes, barrière contre les anciens travailleurs.
    Idempotent et sans effet externe. La barrière est un déclencheur SQLite : un ancien processus ne peut plus insérer de réservation, donc plus envoyer."""
    db = connect(config['state_dir'])
    try:
        ensure(db)
        db.executescript('BEGIN IMMEDIATE;' + TRIGGERS + 'COMMIT;')
        def travail():
            n = _reprendre_anciennes(db)
            if n:
                _evenement(db, 'migration', montant=n, etat='fait', detail={'reprises_incertaines': n})
            if worker:
                db.execute('INSERT OR REPLACE INTO budget_workers5689 VALUES(?,?,?,?)', (str(worker)[:80], PROTOCOLE, str(version)[:40], _iso()))
            db.execute("INSERT OR IGNORE INTO budget_meta5689 VALUES('bascule',?)", (_iso(),))
            cutoff = _iso(_maintenant() - timedelta(days=GARDER_JOURS))
            db.execute("DELETE FROM budget_allocations5689 WHERE reservation_id IN (SELECT reservation_id FROM budget_reservations5689 WHERE state IN ('settled','released') AND created_at<?)", (cutoff,))
            db.execute("DELETE FROM budget_reservations5689 WHERE state IN ('settled','released') AND created_at<?", (cutoff,))
            db.execute('DELETE FROM budget_events5689 WHERE at<?', (cutoff,))
            return n
        return _transaction(db, travail)
    finally:
        db.close()


def bascule(desk):
    """État de la bascule, pour vérifier avant et après une mise à jour. Un ancien travailleur n'apparaît jamais ici : il n'écrit rien ; il est bloqué par le déclencheur."""
    db = connect(desk.c['state_dir'])
    try:
        noms = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'budget5689_%'")}
        faite = db.execute("SELECT valeur FROM budget_meta5689 WHERE cle='bascule'").fetchone()
        return {'protocole': PROTOCOLE, 'barriere_active': len(noms) == 2, 'bascule_le': faite[0] if faite else '', 'travailleurs': [dict(r) for r in db.execute('SELECT * FROM budget_workers5689 ORDER BY worker')]}
    finally:
        db.close()


def retour_arriere(desk, acteur):
    """Avant de réinstaller une version antérieure : retire la barrière, conserve toutes les données. Les réservations ouvertes restent visibles de l'ancien code par leur vue ``ai_reservations_v567``."""
    db = connect(desk.c['state_dir'])
    try:
        def travail():
            db.execute('DROP TRIGGER IF EXISTS budget5689_ancien_v567')
            db.execute('DROP TRIGGER IF EXISTS budget5689_ancien_5682')
            db.execute("DELETE FROM budget_meta5689 WHERE cle='bascule'")
            for r in db.execute("SELECT legacy_ref,ceiling_micro,provider,created_at FROM budget_reservations5689 WHERE state IN ('reserved','dispatched','uncertain') AND legacy_ref IS NOT NULL").fetchall():
                db.execute("INSERT INTO ai_reservations_v567 VALUES (?,?,?,?,'uncertain') ON CONFLICT(id) DO UPDATE SET state='uncertain'", (r['legacy_ref'], r['created_at'], r['provider'], float(unites(r['ceiling_micro']))))
        _transaction(db, travail)
    finally:
        db.close()
    desk.audit('budget5689_retour_arriere', {'par': acteur})
    return {'ok': True, 'message': 'Barrière retirée. Registre et journaux conservés. Arrêtez les travailleurs avant d’installer la version antérieure.'}
