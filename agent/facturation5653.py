"""5.6.53 (audit 5.6.52, § 4, § 5, § 8, § 9) : registre unique de facturation et d'honoraires.

Un seul registre pour les devis, lettres de mission, conventions, factures et avoirs ; il réutilise les temps validés
(``time500_entries``), le relevé des factures (``fees500_invoices``), les connexions (opérations validées sur fiche) et les modèles.

Chaîne : Préparer → Relire (contrôles) → Approuver une version précise (empreinte) → Émettre (numéro définitif, fichiers) →
Transmettre (une seule route) → Rapprocher (statuts distants) → Encaisser (règlements vérifiés).

Règles :
- Le serveur calcule tous les montants (centimes, ``Decimal``) ; une modification d'un brouillon recalcule tout et invalide
  l'approbation ; aucun numéro fiscal n'est attribué à un aperçu.
- Numérotation unique, chronologique et continue par série (entité, préfixe, année), attribuée sous verrou dans la même transaction
  que l'émission ; un numéro émis n'est jamais réutilisé, même si la publication des fichiers échoue (publication reprise ensuite).
- Réservation anti-double facturation : un temps validé ou une provision ne peut figurer que dans un seul brouillon ou une seule facture.
- Qualification fiscale explicite (``fiscal5653``) : sans régime qualifié, l'émission est bloquée avec la question à trancher.
- Profil « Avocat — secret professionnel » : libellés génériques dans les données structurées ; détail réservé au client.
- Une facture émise n'est jamais écrasée : la correction passe par un avoir lié.
- Dossier FACTURATION : les documents émis, signés et les preuves sont rangés dans le sous-dossier FACTURATION du dossier client.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import PurePosixPath
import re
import secrets

from .common import Stop, clean_path, load_matters, matter_display
from . import fiscal5653 as fiscal
from . import honoraires5653 as fees
from . import modeles5653 as models

KINDS = {'devis': 'Devis', 'lettre_mission': 'Lettre de mission', 'convention': 'Convention d’honoraires', 'facture': 'Facture', 'avoir': 'Avoir'}
INVOICE_TYPES = {'libre': 'Facture d’honoraires', 'provision': 'Facture de provision', 'temps': 'Facture au temps passé', 'solde': 'Facture de solde'}
STATES = {'brouillon': 'Brouillon', 'a_controler': 'À contrôler', 'approuve': 'Approuvé', 'envoye': 'Envoyé', 'accepte': 'Accepté', 'refuse': 'Refusé',
          'expire': 'Expiré', 'en_signature': 'En signature', 'signe': 'Signé', 'remplace': 'Remplacé', 'resilie': 'Résilié', 'emise': 'Émise',
          'transmission_en_cours': 'Transmission en cours', 'deposee': 'Déposée sur la plateforme', 'rejetee': 'Rejetée ou refusée par la plateforme',
          'recue': 'Reçue par le client', 'rectifiee': 'Rectifiée par avoir', 'abandonne': 'Brouillon abandonné'}
PAYMENT_STATES = {'': '—', 'impayee': 'Non réglée', 'partielle': 'Partiellement réglée', 'reglee': 'Réglée'}
EDITABLE = ('brouillon', 'a_controler', 'approuve')
NATURES = {'honoraires': 'Honoraires', 'frais': 'Frais refacturés (soumis à TVA)', 'debours': 'Débours (à qualifier, article 267 du CGI)'}
UNITS = {'forfait': 'forfait', 'heure': 'heure', 'unite': 'unité'}
SERIES = {'facture': 'F', 'avoir': 'AV', 'devis': 'D', 'convention': 'C', 'lettre_mission': 'LM'}
TEMPLATE_KIND = {'devis': 'devis', 'lettre_mission': 'lettre_mission', 'facture': 'facture', 'avoir': 'avoir'}
AUTHORITIES = {'interne': 'AxiorHub (numérotation interne)', 'invoice_ninja': 'Invoice Ninja', 'pennylane': 'Pennylane'}
TRANSPORTS = {'aucun': 'Aucun (envoi du PDF au client)', 'superpdp': 'SuperPDP (Factur-X)', 'b2brouter': 'B2Brouter', 'chorus_pro': 'Chorus Pro'}
QUALIFIED_TRANSPORTS = ('aucun', 'superpdp')                   # B2Brouter et Chorus Pro : charges utiles à qualifier avant activation
FOLDER = 'FACTURATION'

SCHEMA = '''
CREATE TABLE IF NOT EXISTS b53_entities(id TEXT PRIMARY KEY, identity_json TEXT NOT NULL DEFAULT '{}', fiscal_json TEXT NOT NULL DEFAULT '{}',
  terms_json TEXT NOT NULL DEFAULT '{}', authority TEXT NOT NULL DEFAULT 'interne', transport TEXT NOT NULL DEFAULT 'aucun', version INTEGER NOT NULL DEFAULT 1,
  updated_by TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS b53_clients(matter TEXT PRIMARY KEY, data_json TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1,
  updated_by TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS b53_documents(id TEXT PRIMARY KEY, kind TEXT NOT NULL, subtype TEXT NOT NULL DEFAULT '', matter TEXT NOT NULL, entity TEXT NOT NULL DEFAULT 'cabinet',
  state TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, number TEXT, series TEXT NOT NULL DEFAULT '', issue_date TEXT NOT NULL DEFAULT '',
  template_id TEXT NOT NULL DEFAULT '', engagement TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL DEFAULT '', snapshot_json TEXT NOT NULL,
  approved_hash TEXT NOT NULL DEFAULT '', approved_by TEXT NOT NULL DEFAULT '', approved_at TEXT NOT NULL DEFAULT '', issue_key TEXT,
  files_json TEXT NOT NULL DEFAULT '{}', files_state TEXT NOT NULL DEFAULT '', checks_json TEXT NOT NULL DEFAULT '{}', delivery_op TEXT NOT NULL DEFAULT '',
  remote_status TEXT NOT NULL DEFAULT '', payment_state TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
  request_key TEXT, request_hash TEXT NOT NULL DEFAULT '');
CREATE UNIQUE INDEX IF NOT EXISTS b53_documents_number ON b53_documents(entity, series, number);
CREATE UNIQUE INDEX IF NOT EXISTS b53_documents_issue ON b53_documents(issue_key);
CREATE UNIQUE INDEX IF NOT EXISTS b53_documents_request ON b53_documents(created_by, request_key);
CREATE INDEX IF NOT EXISTS b53_documents_matter ON b53_documents(matter, kind, state);
CREATE TABLE IF NOT EXISTS b53_versions(document TEXT NOT NULL, version INTEGER NOT NULL, snapshot_json TEXT NOT NULL, author TEXT NOT NULL, action TEXT NOT NULL,
  at TEXT NOT NULL, PRIMARY KEY(document, version, action));
CREATE TABLE IF NOT EXISTS b53_allocations(source_kind TEXT NOT NULL, source_id TEXT NOT NULL, document TEXT NOT NULL, amount_cents INTEGER NOT NULL,
  state TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(source_kind, source_id));
CREATE TABLE IF NOT EXISTS b53_sequences(entity TEXT NOT NULL, series TEXT NOT NULL, last_number INTEGER NOT NULL, last_date TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(entity, series));
CREATE TABLE IF NOT EXISTS b53_events(id INTEGER PRIMARY KEY AUTOINCREMENT, document TEXT NOT NULL, kind TEXT NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}',
  actor TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS b53_payments(id TEXT PRIMARY KEY, document TEXT NOT NULL, amount_cents INTEGER NOT NULL, day TEXT NOT NULL, method TEXT NOT NULL,
  source TEXT NOT NULL, reference TEXT NOT NULL DEFAULT '', verified INTEGER NOT NULL DEFAULT 1, actor TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS b53_suggestions(key TEXT PRIMARY KEY, matter TEXT NOT NULL, kind TEXT NOT NULL, trigger_json TEXT NOT NULL, amount_cents INTEGER,
  state TEXT NOT NULL, until TEXT NOT NULL DEFAULT '', document TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL);
'''


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _today(desk):
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(desk.c.get('calendar', {}).get('timezone', 'Europe/Paris'))).date()
    except Exception:   # noqa: BLE001
        return date.today()


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')).hexdigest()


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    fees.ensure_schema(desk)
    models.ensure_schema(desk)


def _event(desk, doc_id, kind, detail=None, actor=''):
    desk.db.execute('INSERT INTO b53_events(document,kind,detail_json,actor,at) VALUES(?,?,?,?,?)',
                    (doc_id, kind, json.dumps(detail or {}, ensure_ascii=False, default=str)[:4000], actor, _now()))


# ------------------------------------------------------------------------------------------------ droits
def _require(desk, actor, role, right):
    if role == 'administrateur':
        return
    from .membres5650 import effective_rights
    try:
        rights = effective_rights(desk.c['state_dir'], actor, role)
    except Stop:
        raise Stop('controle_acces_indisponible') from None
    if not rights.get(right):
        raise Stop('role_insuffisant')


def _matter(desk, mid):
    from .membres5650 import viewer_hidden
    if str(mid) in (viewer_hidden() or frozenset()):
        raise Stop('dossier_reserve')
    m = next((x for x in load_matters(desk.c) if str(x['id']) == str(mid)), None)
    if not m:
        raise Stop('dossier_absent')
    return m


# ------------------------------------------------------------------------------------------------ cabinet émetteur
def entity(desk, eid='cabinet'):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM b53_entities WHERE id=?', (eid,)).fetchone()
    if not row:
        return {'id': eid, 'identite': {}, 'fiscal': {}, 'conditions': {'delai_paiement_jours': 30, 'modalites': 'Virement bancaire',
                                                                       'escompte': 'Pas d’escompte pour paiement anticipé.'},
                'autorite': 'interne', 'transport': 'aucun', 'version': 0}
    return {'id': row['id'], 'identite': json.loads(row['identity_json']), 'fiscal': json.loads(row['fiscal_json']), 'conditions': json.loads(row['terms_json']),
            'autorite': row['authority'], 'transport': row['transport'], 'version': row['version']}


IDENTITY_FIELDS = ('nom', 'barreau', 'adresse_ligne1', 'code_postal', 'ville', 'pays', 'siren', 'siret', 'tva', 'email', 'telephone', 'iban', 'beneficiaire')


def save_entity(desk, actor, role, data):
    """Fiche du cabinet émetteur (identité, régime fiscal QUALIFIÉ, conditions, outil de référence, trajet) : droit « réglages »."""
    _require(desk, actor, role, 'reglages')
    cur = entity(desk)
    ident = dict(cur['identite'])
    for k in IDENTITY_FIELDS:
        if k in data:
            ident[k] = re.sub(r'\s+', ' ', str(data[k] or '')).strip()[:200]
    if ident.get('siren') and not re.fullmatch(r'\d{9}', ident['siren'].replace(' ', '')):
        raise Stop('siren_invalide')
    if ident.get('siret') and not re.fullmatch(r'\d{14}', ident['siret'].replace(' ', '')):
        raise Stop('siret_invalide')
    if ident.get('iban') and not re.fullmatch(r'[A-Z]{2}\d{2}[A-Z0-9 ]{10,40}', ident['iban'].upper()):
        raise Stop('iban_invalide')
    fis = dict(cur['fiscal'])
    if 'regime_cabinet' in data:
        if data['regime_cabinet'] not in ('', 'normal', 'franchise'):
            raise Stop('regime_tva_invalide')
        fis['regime_cabinet'] = data['regime_cabinet']
        fis['qualifie_par'], fis['qualifie_le'] = actor, _now()
    for k in ('justification_franchise', 'taux_defaut', 'categorie_entreprise'):
        if k in data:
            fis[k] = str(data[k] or '').strip()[:400]
    if 'tva_sur_debits' in data:
        fis['tva_sur_debits'] = data['tva_sur_debits'] in (True, 'true', 'on', '1')
    terms = dict(cur['conditions'])
    if 'delai_paiement_jours' in data:
        days = int(fees.dec(data['delai_paiement_jours'], 'delai'))
        if not 0 <= days <= 60:
            raise Stop('delai_paiement_invalide')              # délai légal maximal entre professionnels (L441-10)
        terms['delai_paiement_jours'] = days
    for k in ('modalites', 'escompte'):
        if k in data:
            terms[k] = str(data[k] or '').strip()[:300]
    authority = data.get('autorite', cur['autorite'])
    transport = data.get('transport', cur['transport'])
    if authority not in AUTHORITIES:
        raise Stop('autorite_facturation_invalide')
    if transport not in TRANSPORTS:
        raise Stop('trajet_facturation_invalide')
    now = _now()
    if cur['version']:
        c = desk.db.execute('UPDATE b53_entities SET identity_json=?,fiscal_json=?,terms_json=?,authority=?,transport=?,version=version+1,updated_by=?,updated=? '
                            'WHERE id=? AND version=?', (json.dumps(ident, ensure_ascii=False), json.dumps(fis, ensure_ascii=False), json.dumps(terms, ensure_ascii=False),
                                                       authority, transport, actor, now, cur['id'], int(data.get('version') or cur['version'])))
        if not c.rowcount:
            raise Stop('version_en_conflit')
    else:
        desk.db.execute('INSERT INTO b53_entities(id,identity_json,fiscal_json,terms_json,authority,transport,updated_by,updated) VALUES(?,?,?,?,?,?,?,?)',
                        (cur['id'], json.dumps(ident, ensure_ascii=False), json.dumps(fis, ensure_ascii=False), json.dumps(terms, ensure_ascii=False), authority,
                         transport, actor, now))
    desk.db.commit()
    return entity(desk)


def emission_obligation(ent):
    """Calendrier d'émission selon la catégorie RÉELLE du cabinet (DGFiP) ; la réception concerne tous les assujettis depuis le 1er
    septembre 2026."""
    cat = (ent.get('fiscal') or {}).get('categorie_entreprise') or ''
    if cat in ('ge', 'eti'):
        return 'Émission électronique obligatoire depuis le 1er septembre 2026 (grande entreprise ou ETI).'
    if cat in ('pme', 'micro'):
        return 'Émission électronique obligatoire à partir du 1er septembre 2027 (PME ou micro-entreprise) ; réception depuis le 1er septembre 2026.'
    return 'Catégorie du cabinet à indiquer (micro, PME, ETI, grande entreprise) : elle fixe la date d’obligation d’émission.'


# ------------------------------------------------------------------------------------------------ client cocontractant
def client(desk, mid):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM b53_clients WHERE matter=?', (str(mid),)).fetchone()
    if row:
        return {**json.loads(row['data_json']), 'source': row['source'], 'version': row['version']}
    m = _matter(desk, mid)
    return {'nom': str(m.get('client_name') or ''), 'kind': '', 'country': 'FR', 'source': 'nom du dossier (à compléter)', 'version': 0}


CLIENT_FIELDS = ('nom', 'kind', 'adresse_ligne1', 'code_postal', 'ville', 'country', 'siren', 'siret', 'vat_number', 'email', 'representant', 'qualite_representant',
                 'payeur')


def save_client(desk, actor, role, mid, data):
    """Client cocontractant du dossier (qualité, adresse, identifiants VÉRIFIÉS) ; un particulier n'a jamais de SIREN fabriqué."""
    _require(desk, actor, role, 'facturation')
    _matter(desk, mid)
    cur = client(desk, mid)
    out = {k: cur.get(k, '') for k in CLIENT_FIELDS}
    for k in CLIENT_FIELDS:
        if k in data:
            out[k] = re.sub(r'\s+', ' ', str(data[k] or '')).strip()[:200]
    if out['kind'] and out['kind'] not in fiscal.CLIENT_KINDS:
        raise Stop('qualite_client_invalide')
    if out['kind'] == 'consommateur' and (out.get('siren') or out.get('siret')):
        raise Stop('siren_particulier_refuse')
    if out.get('siren') and not re.fullmatch(r'\d{9}', out['siren'].replace(' ', '')):
        raise Stop('siren_invalide')
    out['country'] = (out.get('country') or 'FR').upper()[:2]
    source = str(data.get('source') or cur.get('source') or 'saisie de l’avocat')[:200]
    now = _now()
    if cur.get('version'):
        c = desk.db.execute('UPDATE b53_clients SET data_json=?,source=?,version=version+1,updated_by=?,updated=? WHERE matter=? AND version=?',
                            (json.dumps(out, ensure_ascii=False), source, actor, now, str(mid), int(data.get('version') or cur['version'])))
        if not c.rowcount:
            raise Stop('version_en_conflit')
    else:
        desk.db.execute('INSERT INTO b53_clients(matter,data_json,source,updated_by,updated) VALUES(?,?,?,?,?)', (str(mid), json.dumps(out, ensure_ascii=False), source,
                                                                                                              actor, now))
    desk.db.commit()
    return client(desk, mid)


def lookup_siren(desk, actor, role, mid, query):
    """Rapprochement public (API Recherche d'entreprises) : résultats PROPOSÉS ; un homonyme ou un écart reste une décision de l'avocat."""
    _require(desk, actor, role, 'facturation')
    _matter(desk, mid)
    from . import connexions5652 as cx
    q = str(query or '').strip()[:120]
    params = {'siren': q} if re.fullmatch(r'\d{9}', q.replace(' ', '')) else {'query': q}
    out = cx.call_tool(desk, actor, role, 'entreprise.rechercher', params, mid)
    rows = (out.get('resultat') or {}).get('resultats') or []
    return {'source': out['source'], 'date': out['date_lecture'], 'resultats': rows[:5],
            'note': 'Données d’annuaire : à confirmer ; elles ne prouvent ni le pouvoir de signer ni la qualité de payeur.'}


# ------------------------------------------------------------------------------------------------ calcul d'un instantané
def _line(raw, n):
    nature = raw.get('nature') or 'honoraires'
    if nature not in NATURES:
        raise Stop('nature_ligne_invalide')
    unit = raw.get('unite') or 'forfait'
    if unit not in UNITS:
        raise Stop('unite_ligne_invalide')
    qty = fees.dec(raw.get('quantite') if raw.get('quantite') not in (None, '') else '1', 'quantite')
    if qty <= 0 or qty.as_tuple().exponent < -6:
        raise Stop('quantite_invalide')
    if raw.get('prix_unitaire_ht_cents') is not None:
        unit_cents = int(raw['prix_unitaire_ht_cents'])
    else:
        unit_cents = fees.cents(fees.dec(raw.get('prix_unitaire_ht'), 'prix_unitaire'))
    if unit_cents < 0:
        raise Stop('prix_unitaire_invalide')
    amount = int((Decimal(unit_cents) * qty).quantize(Decimal('1'), rounding='ROUND_HALF_UP'))
    label = re.sub(r'\s+', ' ', str(raw.get('libelle') or '')).strip()[:300]
    if not label:
        raise Stop('libelle_ligne_requis')
    out = {'id': str(raw.get('id') or 'L%d' % n), 'nature': nature, 'libelle': label, 'quantite': format(qty.normalize(), 'f'), 'unite': unit,
           'prix_unitaire_ht_cents': unit_cents, 'montant_ht_cents': amount, 'periode': str(raw.get('periode') or '')[:60],
           'sources': raw.get('sources') if isinstance(raw.get('sources'), list) else []}
    if raw.get('taux_tva') not in (None, ''):
        out['taux_tva'] = str(fees.dec(raw['taux_tva'], 'taux_tva'))
    return out


def compute(snap, ent, cli):
    """Recalcul intégral : lignes, qualification fiscale, ventilation de TVA, déjà réglé et net à payer (aucun total saisi à la main)."""
    lines = [_line(l, i + 1) for i, l in enumerate(snap.get('lignes') or [])]
    fis = fiscal.qualify(ent, cli, snap.get('choix_fiscal') or {})
    breakdown = fiscal.vat_breakdown(lines, fis) if lines else []
    ht = sum(l['montant_ht_cents'] for l in lines)
    tva = sum(v['tva_cents'] for v in breakdown)
    prepaid = sum(int(i.get('montant_ttc_cents') or 0) for i in snap.get('imputations') or [])
    snap = {**snap, 'lignes': lines, 'fiscal': fis}
    snap['totaux'] = {'ht_cents': ht, 'tva_cents': tva, 'ttc_cents': ht + tva, 'ventilation': breakdown, 'deja_regle_cents': prepaid,
                      'net_a_payer_cents': ht + tva - prepaid}
    return snap


# ------------------------------------------------------------------------------------------------ documents
def _row(desk, doc_id):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM b53_documents WHERE id=?', (str(doc_id or ''),)).fetchone()
    if not row:
        raise Stop('document_financier_absent')
    _matter(desk, row['matter'])                               # dossier RÉEL de l'objet contrôlé (réservé = refus)
    return row


def get(desk, doc_id):
    row = _row(desk, doc_id)
    d = dict(row)
    d['snapshot'] = json.loads(d.pop('snapshot_json'))
    d['files'] = json.loads(d.pop('files_json') or '{}')
    d['checks'] = json.loads(d.pop('checks_json') or '{}')
    d['state_label'] = STATES.get(d['state'], d['state'])
    d['payment_label'] = PAYMENT_STATES.get(d['payment_state'], d['payment_state'])
    d['kind_label'] = KINDS.get(d['kind'], d['kind'])
    d['payments'] = [dict(r) for r in desk.db.execute('SELECT * FROM b53_payments WHERE document=? ORDER BY day', (d['id'],))]
    d['events'] = [dict(r) for r in desk.db.execute('SELECT * FROM b53_events WHERE document=? ORDER BY id DESC LIMIT 30', (d['id'],))]
    return d


def _confidential_terms(desk, m):
    """Termes à ne jamais voir dans les données structurées (profil secret professionnel) : dossier, adversaires, nom long du dossier."""
    terms = {str(m.get('id') or ''), str(matter_display(m))}
    try:
        from .dossiers5632 import adverse_parties
        terms.update(adverse_parties(desk, m['id']))
    except Exception:   # noqa: BLE001
        pass
    client_name = str(m.get('client_name') or '').lower()
    return sorted({t for t in terms if t and len(t) > 3 and t.lower() != client_name})


def prepare(desk, actor, role, kind, mid, data, request_key=''):
    """Nouveau brouillon (aucun numéro fiscal) à partir du dossier, du calcul retenu, des temps validés ou de la convention."""
    _require(desk, actor, role, 'facturation')
    ensure_schema(desk)
    if kind not in KINDS:
        raise Stop('type_document_financier_invalide')
    m = _matter(desk, mid)
    key = str(request_key or '')
    if key and not re.fullmatch(r'[A-Za-z0-9-]{8,80}', key):
        raise Stop('identifiant_requete_invalide')
    content_hash = _sha({'k': kind, 'm': mid, 'd': data})
    if key:
        old = desk.db.execute('SELECT * FROM b53_documents WHERE created_by=? AND request_key=?', (actor, key)).fetchone()
        if old:
            if old['request_hash'] != content_hash:
                raise Stop('cle_idempotence_deja_utilisee')
            return get(desk, old['id'])
    ent, cli = entity(desk), client(desk, mid)
    subtype = str(data.get('sous_type') or ('libre' if kind == 'facture' else ''))
    if kind == 'facture' and subtype not in INVOICE_TYPES:
        raise Stop('type_facture_invalide')
    if kind == 'convention':
        subtype = 'temps' if str(data.get('mode') or '') == 'temps' else 'forfait'
    snap = {'kind': kind, 'sous_type': subtype, 'lignes': [], 'textes': {}, 'imputations': [], 'choix_fiscal': {}, 'profil_secret': True,
            'reference_convention': None, 'dates': {'prestation': str(data.get('date_prestation') or '')[:10], 'echeance': ''}}
    for k in ('mission', 'perimetre_inclus', 'perimetre_exclu', 'hypotheses', 'livrables', 'phases', 'echeancier', 'objectif_et_hypotheses', 'calendrier',
              'detail_client_autorise', 'budget_et_plafond', 'frais_debours_previsibles'):
        if data.get(k):
            snap['textes'][k] = str(data[k])[:4000]
    sources = []
    est = None
    if data.get('estimation'):
        est = fees.get_estimate(desk, str(data['estimation']))
        if est['matter'] != str(mid):
            raise Stop('estimation_absente')
        if est['state'] not in ('retenue', 'signee') and kind in ('convention', 'lettre_mission'):
            raise Stop('estimation_a_retenir')
        sources.append({'type': 'estimation', 'id': est['id'], 'version': est['version']})
    conv = _active_convention(desk, mid)
    if conv:
        snap['reference_convention'] = {'id': conv['id'], 'numero': conv['number'] or '', 'version': conv['version']}
    if kind in ('devis', 'convention', 'lettre_mission') and est:
        amount = est['retained_cents'] if est['retained_cents'] is not None else est['proposed_cents']
        snap['lignes'] = [{'nature': 'honoraires', 'libelle': 'Honoraires — %s' % (snap['textes'].get('mission') or 'mission convenue'), 'quantite': '1',
                           'unite': 'forfait', 'prix_unitaire_ht_cents': amount, 'sources': [sources[-1]]}]
        snap['textes'].setdefault('perimetre_inclus', '\n'.join('— ' + l['libelle_client'] for l in est['result']['lignes'] if l['statut'] == 'inclus'))
        opts = [l for l in est['result']['lignes'] if l['statut'] == 'option']
        if opts:
            snap['textes'].setdefault('options_chiffrees', '\n'.join('— %s : %s HT' % (l['libelle_client'], fees.fmt(l['montant_ht_cents'])) for l in opts))
        snap['textes'].setdefault('provision', '%s %% (%s HT)' % (est['result']['provision_pct'], fees.fmt(fees.provision(amount, est['result']['provision_pct']))))
        snap['estimation'] = {'id': est['id'], 'propose_cents': est['proposed_cents'], 'retenu_cents': est['retained_cents'], 'moteur': est['engine_version']}
    if kind == 'facture' and subtype == 'temps':
        entries = billable_time(desk, mid)
        chosen = set(data.get('temps') or [e['id'] for e in entries])
        for e in entries:
            if e['id'] in chosen:
                snap['lignes'].append({'nature': 'honoraires', 'libelle': e['label'] or 'Diligences', 'quantite': str((Decimal(e['minutes']) / 60).quantize(Decimal('0.01'))),
                                       'unite': 'heure', 'prix_unitaire_ht_cents': e['rate_cents'], 'periode': e['day'],
                                       'sources': [{'type': 'temps', 'id': e['id']}]})
        if not snap['lignes']:
            raise Stop('aucun_temps_a_facturer')
    if kind == 'facture' and subtype == 'provision':
        if not conv:
            raise Stop('convention_requise_pour_provision')
        pct = str(data.get('provision_pct') or (conv['snapshot'].get('provision_pct') if conv else '') or fees.settings(desk)['provision_pct'])
        base = conv['snapshot']['totaux']['ht_cents'] if conv else 0
        snap['lignes'] = [{'nature': 'honoraires', 'libelle': 'Provision sur honoraires (%s %% de la convention)' % pct, 'quantite': '1', 'unite': 'forfait',
                           'prix_unitaire_ht_cents': fees.provision(base, pct), 'sources': [{'type': 'provision', 'id': conv['id'] + ':' + pct}]}]
        snap['provision_pct'] = pct
    if kind == 'facture' and subtype == 'solde':
        if not conv:
            raise Stop('convention_requise_pour_solde')
        snap['lignes'] = [{'nature': 'honoraires', 'libelle': 'Honoraires convenus — solde', 'quantite': '1', 'unite': 'forfait',
                           'prix_unitaire_ht_cents': conv['snapshot']['totaux']['ht_cents'], 'sources': [{'type': 'solde', 'id': conv['id']}]}]
        snap['imputations'] = [{'facture_id': p['id'], 'numero': p['number'], 'montant_ttc_cents': p['snapshot']['totaux']['ttc_cents']}
                               for p in _paid_provisions(desk, mid)]
    for l in data.get('lignes') or []:
        snap['lignes'].append(l)
    if kind == 'convention' and est:
        snap['provision_pct'] = est['result']['provision_pct']
    snap['termes_confidentiels'] = _confidential_terms(desk, m)
    snap = compute(snap, ent, cli)
    template = models.default_for(desk, TEMPLATE_KIND.get(kind) or ('convention_temps' if subtype == 'temps' else 'convention_forfait'))
    doc_id = 'fin-' + secrets.token_hex(9)                    # 5.6.54 : identifiant technique unique (pas d'horodatage à la seconde)
    now = _now()
    desk.db.execute('INSERT INTO b53_documents(id,kind,subtype,matter,state,snapshot_json,template_id,engagement,created_by,created,updated,request_key,request_hash) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (doc_id, kind, subtype, str(mid), 'brouillon', json.dumps(snap, ensure_ascii=False), template['id'],
                                                         (conv or {}).get('id', ''), actor, now, now, key or None, content_hash if key else ''))
    try:
        _reserve(desk, doc_id, snap)
    except Stop:
        desk.db.rollback()
        raise
    desk.db.execute('INSERT INTO b53_versions(document,version,snapshot_json,author,action,at) VALUES(?,?,?,?,?,?)',
                    (doc_id, 1, json.dumps(snap, ensure_ascii=False), actor, 'creation', now))
    _event(desk, doc_id, 'preparation', {'kind': kind, 'sous_type': subtype, 'sources': sources}, actor)
    desk.db.commit()
    return get(desk, doc_id)


def _reserve(desk, doc_id, snap):
    """Réservation atomique des sources facturées (temps, provision, solde) : une source ne peut figurer que dans un seul document."""
    if snap['kind'] not in ('facture',):
        return
    desk.db.execute("DELETE FROM b53_allocations WHERE document=? AND state='reserve'", (doc_id,))
    for l in snap['lignes']:
        for s in l.get('sources') or []:
            if s.get('type') not in ('temps', 'provision', 'solde'):
                continue
            held = desk.db.execute('SELECT document,state FROM b53_allocations WHERE source_kind=? AND source_id=?', (s['type'], s['id'])).fetchone()
            if held and held['document'] != doc_id:
                raise Stop('diligence_deja_reservee')
            if s['type'] == 'temps':
                from .facturation569 import _billed_entries
                try:
                    m = desk.db.execute('SELECT matter FROM time500_entries WHERE id=?', (s['id'],)).fetchone()
                    if m and s['id'] in _billed_entries(desk, m['matter']):
                        raise Stop('diligence_deja_facturee_invoice_ninja')
                except Stop:
                    raise
                except Exception:   # noqa: BLE001 — registre Invoice Ninja absent
                    pass
            desk.db.execute('INSERT OR REPLACE INTO b53_allocations(source_kind,source_id,document,amount_cents,state,created) VALUES(?,?,?,?,?,?)',
                            (s['type'], s['id'], doc_id, l['montant_ht_cents'], 'reserve', _now()))


def billable_time(desk, mid):
    """Temps VALIDÉS du dossier non encore réservés ni facturés (ici ou dans Invoice Ninja)."""
    ensure_schema(desk)
    try:
        rows = [dict(r) for r in desk.db.execute('SELECT * FROM time500_entries WHERE matter=? ORDER BY day', (str(mid),))]
    except Exception:   # noqa: BLE001 — module temps pas encore initialisé
        return []
    held = {r[0] for r in desk.db.execute("SELECT source_id FROM b53_allocations WHERE source_kind='temps'")}
    try:
        from .facturation569 import _billed_entries
        held |= set(_billed_entries(desk, mid))
    except Exception:   # noqa: BLE001
        pass
    return [r for r in rows if r['id'] not in held]


def _active_convention(desk, mid):
    for r in desk.db.execute("SELECT id FROM b53_documents WHERE matter=? AND kind IN ('convention','lettre_mission') AND state IN ('signe','approuve','envoye',"
                             "'en_signature') ORDER BY CASE state WHEN 'signe' THEN 0 ELSE 1 END, updated DESC LIMIT 1", (str(mid),)):
        return get(desk, r['id'])
    return None


def _paid_provisions(desk, mid):
    out = []
    for r in desk.db.execute("SELECT id FROM b53_documents WHERE matter=? AND kind='facture' AND subtype='provision' AND number IS NOT NULL", (str(mid),)):
        d = get(desk, r['id'])
        if d['payment_state'] == 'reglee':
            out.append(d)
    return out


def update(desk, actor, role, doc_id, changes, version):
    """Édition d'un brouillon sur sa version : recalcul intégral, approbation invalidée, contrôles relancés."""
    _require(desk, actor, role, 'facturation')
    row = _row(desk, doc_id)
    if row['state'] not in EDITABLE:
        raise Stop('document_emis_non_modifiable')
    snap = json.loads(row['snapshot_json'])
    if 'lignes' in changes:
        if not isinstance(changes['lignes'], list) or len(changes['lignes']) > 200:
            raise Stop('lignes_invalides')
        snap['lignes'] = changes['lignes']
    for k, v in (changes.get('textes') or {}).items():
        snap['textes'][str(k)[:60]] = str(v)[:4000]
    if 'choix_fiscal' in changes and isinstance(changes['choix_fiscal'], dict):
        snap['choix_fiscal'] = {k: v for k, v in changes['choix_fiscal'].items() if k in ('regime', 'taux', 'confirme', 'fondement', 'mention', 'categorie')}
    if 'dates' in changes and isinstance(changes['dates'], dict):
        for k in ('prestation', 'echeance', 'validite'):
            if k in changes['dates']:
                v = str(changes['dates'][k] or '')[:10]
                if v and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', v):
                    raise Stop('date_invalide')
                snap['dates'][k] = v
    if 'profil_secret' in changes:
        snap['profil_secret'] = changes['profil_secret'] not in (False, 'false', '0', 'off')
    if 'template_id' in changes:
        t = models.get(desk, str(changes['template_id']))
        if t['kind'] not in (TEMPLATE_KIND.get(row['kind']), 'convention_forfait', 'convention_temps') or (row['kind'] != 'convention' and t['kind'] != TEMPLATE_KIND.get(row['kind'])):
            raise Stop('type_modele_invalide')
        desk.db.execute('UPDATE b53_documents SET template_id=? WHERE id=?', (t['id'], doc_id))
    snap = compute(snap, entity(desk), client(desk, row['matter']))
    now = _now()
    cur = desk.db.execute("UPDATE b53_documents SET snapshot_json=?,state='brouillon',approved_hash='',approved_by='',approved_at='',checks_json='{}',"
                          "version=version+1,updated=? WHERE id=? AND version=?", (json.dumps(snap, ensure_ascii=False), now, doc_id, int(version)))
    if not cur.rowcount:
        desk.db.rollback()
        raise Stop('version_en_conflit')
    try:
        _reserve(desk, doc_id, snap)
    except Stop:
        desk.db.rollback()
        raise
    desk.db.execute('INSERT INTO b53_versions(document,version,snapshot_json,author,action,at) VALUES(?,?,?,?,?,?)',
                    (doc_id, int(version) + 1, json.dumps(snap, ensure_ascii=False), actor, 'modification', now))
    _event(desk, doc_id, 'modification', {'approbation': 'invalidee' if row['approved_hash'] else ''}, actor)
    desk.db.commit()
    return get(desk, doc_id)


def abandon(desk, actor, role, doc_id, version):
    """Brouillon abandonné (jamais supprimé) ; ses réservations sont libérées. Un document émis ne s'abandonne pas : avoir."""
    _require(desk, actor, role, 'facturation')
    row = _row(desk, doc_id)
    if row['number'] and row['kind'] in ('facture', 'avoir'):
        raise Stop('document_emis_non_modifiable')
    cur = desk.db.execute("UPDATE b53_documents SET state='abandonne',version=version+1,updated=? WHERE id=? AND version=?", (_now(), doc_id, int(version)))
    if not cur.rowcount:
        raise Stop('version_en_conflit')
    desk.db.execute("DELETE FROM b53_allocations WHERE document=? AND state='reserve'", (doc_id,))
    _event(desk, doc_id, 'abandon', {}, actor)
    desk.db.commit()
    return get(desk, doc_id)


# ------------------------------------------------------------------------------------------------ rendu et contrôles
def _values(desk, d, ent, cli, number='', issue_date=''):
    """Variables des modèles résolues par le SERVEUR (fiche cabinet, client, calcul) ; une valeur absente reste à compléter."""
    snap, ident = d['snapshot'], ent['identite']
    t, fis = snap['totaux'], snap['fiscal']
    m = _matter(desk, d['matter'])
    addr = ' '.join(x for x in (ident.get('adresse_ligne1'), ident.get('code_postal'), ident.get('ville')) if x)
    caddr = ' '.join(x for x in (cli.get('adresse_ligne1'), cli.get('code_postal'), cli.get('ville')) if x)
    terms = ent['conditions']
    issue = issue_date or ''
    due = snap['dates'].get('echeance') or ''
    if not due and issue:
        due = (date.fromisoformat(issue) + timedelta(days=int(terms.get('delai_paiement_jours') or 30))).isoformat()
    vent = '; '.join('%s : base %s, TVA %s' % ('taux %s %%' % v['taux'] if v['categorie'] == 'S' else 'catégorie %s' % v['categorie'], fees.fmt(v['base_ht_cents']),
                                                 fees.fmt(v['tva_cents'])) for v in t['ventilation'])
    conv = snap.get('reference_convention') or {}
    v = {'cabinet_identite': ident.get('nom'), 'barreau': ident.get('barreau'), 'cabinet_adresse': addr, 'cabinet_siren': ident.get('siren'),
         'cabinet_siret': ident.get('siret'), 'cabinet_tva': ident.get('tva') or ('non assujetti (franchise)' if fis['regime'] == 'franchise' else None),
         'cabinet_contact': ' · '.join(x for x in (ident.get('email'), ident.get('telephone')) if x), 'logo': '',
         'client_identite': cli.get('nom'), 'client_adresse': caddr, 'representant_client': cli.get('representant') or '',
         'client_siren_si_applicable': cli.get('siren') or ('sans objet (particulier)' if cli.get('kind') == 'consommateur' else None),
         'client_tva_si_applicable': cli.get('vat_number') or ('sans objet' if fis['perimetre'] in ('b2c', 'b2b_fr', 'public') else None),
         'reference_dossier': matter_display(m), 'reference_client_visible': str(m.get('id')), 'reference_convention': conv.get('numero') or ('sans convention' if d['kind'] == 'devis' else 'non enregistrée'
                                                                                         if d['kind'] in ('facture', 'avoir') else None),
         'categorie_operation': fiscal.operation_category(snap['lignes']), 'adresse_livraison_si_applicable': '',
         'numero_facture': number or None, 'numero_devis': number or None, 'numero_avoir': number or None,
         'date_emission': issue or None, 'date_devis': issue or _today(desk).isoformat(), 'date_document': _today(desk).isoformat(),
         'date_prestation': snap['dates'].get('prestation') or issue or None, 'date_echeance': due or None,
         'date_validite': snap['dates'].get('validite') or (_today(desk) + timedelta(days=30)).isoformat(),
         'total_ht': fees.fmt(t['ht_cents']), 'total_ttc': fees.fmt(t['ttc_cents']), 'ventilation_tva': vent or None, 'net_a_payer': fees.fmt(t['net_a_payer_cents']),
         'devise': 'EUR', 'mentions_tva': fis['mention'] or ('TVA au taux de %s %%.' % fis['taux'] if fis['regime'] == 'normal' else None),
         'remises_et_majorations': '', 'debours_qualifies': 'aucun' if not any(l['nature'] == 'debours' for l in snap['lignes']) else None,
         'imputation_acomptes_factures': '; '.join('Provision %s déduite : %s TTC' % (i['numero'], fees.fmt(i['montant_ttc_cents'])) for i in snap['imputations']) or '',
         'references_factures_anterieures': '', 'detail_client_autorise': snap['textes'].get('detail_client_autorise', ''),
         'modalites_paiement': terms.get('modalites'), 'beneficiaire': ident.get('beneficiaire') or ident.get('nom'), 'iban': ident.get('iban'),
         'conditions_escompte': terms.get('escompte'), 'clause_retard_adaptee_client': fiscal.late_payment_clause(fis['perimetre']),
         'delai_paiement': '%s jours' % terms.get('delai_paiement_jours', 30),
         'mission': snap['textes'].get('mission'), 'perimetre_inclus': snap['textes'].get('perimetre_inclus'), 'perimetre_exclu': snap['textes'].get('perimetre_exclu') or 'néant',
         'hypotheses': snap['textes'].get('hypotheses') or snap['textes'].get('objectif_et_hypotheses'), 'objectif_et_hypotheses': snap['textes'].get('objectif_et_hypotheses') or snap['textes'].get('hypotheses'),
         'livrables': snap['textes'].get('livrables'), 'phases': snap['textes'].get('phases') or 'phase unique', 'echeancier': snap['textes'].get('echeancier'),
         'calendrier': snap['textes'].get('calendrier'), 'budget_et_plafond': snap['textes'].get('budget_et_plafond'),
         'frais_debours_previsibles': snap['textes'].get('frais_debours_previsibles') or 'frais de tiers refacturés sur justificatif',
         'options_chiffrees': snap['textes'].get('options_chiffrees') or 'aucune', 'provision': snap['textes'].get('provision'),
         'forfait_ht': fees.fmt(t['ht_cents']), 'forfait_ttc': fees.fmt(t['ttc_cents']), 'mode_honoraires': {'forfait': 'Forfait', 'temps': 'Au temps passé'}.get(d['subtype'], 'Forfait'),
         'clause_resultat_si_convenue': 'Aucun honoraire de résultat n’est convenu.', 'version_convention': str(d['version']),
         'information_mediation_consommateur': '' if fis['perimetre'] != 'b2c' else None,
         'information_consommateur_si_applicable': '' if fis['perimetre'] != 'b2c' else None,
         'information_retractation_si_applicable': '' if fis['perimetre'] != 'b2c' else None,
         'signataire_avocat': ident.get('nom'), 'signature_avocat': '', 'signataire_client': cli.get('representant') or cli.get('nom'),
         'qualite_signataire': cli.get('qualite_representant') or ('client' if cli.get('kind') == 'consommateur' else None),
         'date_acceptation': '', 'signature_client': '', 'pied_de_page': '%s %s' % (KINDS.get(d['kind'], ''), number or 'brouillon')}
    for k in ('elements_attendus', 'seuil_depenses', 'modalites_fin_mission', 'demarches_amiables', 'version_cgh', 'annexes_identifiees', 'clause_outils_ia_validee',
              'prix_par_phase', 'taux_horaire_ht', 'facture_origine', 'motif_avoir', 'imputation_avoir', 'contexte_valide', 'objectif_client'):
        v.setdefault(k, snap['textes'].get(k))
    for k, val in (snap.get('textes') or {}).items():
        if v.get(k) in (None, '') and val not in (None, ''):
            v[k] = val
    for k in ('date_emission', 'date_devis', 'date_document', 'date_prestation', 'date_echeance', 'date_validite', 'date_acceptation'):
        if isinstance(v.get(k), str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', v[k]):
            v[k] = '%s/%s/%s' % (v[k][8:10], v[k][5:7], v[k][:4])     # dates lisibles dans le document ; le XML garde le format normalisé
    v['_vides_permis'] = ('logo', 'remises_et_majorations', 'adresse_livraison_si_applicable', 'imputation_acomptes_factures', 'references_factures_anterieures',
                          'detail_client_autorise', 'signature_avocat', 'signature_client', 'date_acceptation', 'information_mediation_consommateur',
                          'information_consommateur_si_applicable', 'information_retractation_si_applicable', 'representant_client')
    return v


def _line_values(d):
    out = []
    for l in d['snapshot']['lignes']:
        rate = l.get('taux_tva') or d['snapshot']['fiscal'].get('taux') or '0'
        out.append({'libelle': l['libelle'] + (' (%s)' % l['periode'] if l.get('periode') else ''), 'qte': ('%s h' % l['quantite']) if l['unite'] == 'heure' else l['quantite'],
                    'prix_ht': fees.fmt(l['prix_unitaire_ht_cents']), 'tva': ('%s %%' % rate) if d['snapshot']['fiscal']['categorie_tva'] == 'S' else d['snapshot']['fiscal']['categorie_tva'],
                    'montant_ht': fees.fmt(l['montant_ht_cents'])})
    return out


def preview(desk, actor, role, doc_id):
    """Aperçu NON FISCAL (aucun numéro attribué) : texte rendu, variables manquantes et contrôles."""
    d = get(desk, doc_id)
    ent, cli = entity(desk), client(desk, d['matter'])
    template = models.get(desk, d['template_id'])
    text, missing = models.render_text(template, _values(desk, d, ent, cli, number=d['number'] or '', issue_date=d['issue_date']), _line_values(d))
    return {'texte': text, 'variables_manquantes': missing, 'controles': check(desk, actor, role, doc_id, persist=False)}


REQUIRED_FOR_ISSUE = ('cabinet_identite', 'cabinet_adresse', 'cabinet_siren', 'client_identite', 'client_adresse', 'date_prestation', 'mentions_tva', 'total_ht',
                      'ventilation_tva', 'total_ttc', 'modalites_paiement', 'clause_retard_adaptee_client')


def check(desk, actor, role, doc_id, persist=True):
    """Rapport de contrôle en langage clair : bloquants (empêchent l'approbation ou l'émission) et avertissements. Aucun envoi."""
    d = get(desk, doc_id)
    ent, cli = entity(desk), client(desk, d['matter'])
    snap = d['snapshot']
    blockers, warnings = list(snap['fiscal']['bloquants']), []
    if not snap['lignes']:
        blockers.append('Aucune ligne : ajoutez au moins une prestation.')
    if d['kind'] in ('facture', 'avoir'):
        if not cli.get('kind'):
            blockers.append('La qualité du client manque (professionnel, particulier ou organisme public).')
        if snap['fiscal']['perimetre'] == 'b2b_fr' and not cli.get('siren'):
            blockers.append('Le SIREN du client professionnel manque (mention obligatoire des factures entre professionnels).')
        if any(l['nature'] == 'debours' for l in snap['lignes']):
            blockers.append('Une ligne de débours doit être qualifiée (article 267 du CGI) : utilisez « frais refacturés » ou retirez-la.')
        if snap['totaux']['net_a_payer_cents'] < 0:
            blockers.append('Le net à payer est négatif : vérifiez les provisions imputées.')
        if ent['autorite'] != 'interne':
            warnings.append('La numérotation relève de %s : la facture y sera préparée, AxiorHub ne délivre pas de second numéro.' % AUTHORITIES[ent['autorite']])
    if d['kind'] == 'facture' and d['subtype'] == 'solde' and not snap.get('reference_convention'):
        blockers.append('Une facture de solde se rattache à une convention.')
    if d['kind'] in ('convention', 'lettre_mission') and snap['totaux']['ht_cents'] == 0 and not (snap.get('estimation') or {}).get('retenu_cents') == 0:
        blockers.append('Le prix convenu est absent : retenez une estimation ou saisissez le forfait.')
    if d['kind'] in ('convention', 'lettre_mission', 'devis') and snap['fiscal']['perimetre'] == 'b2c':
        warnings.append('Client consommateur : joignez l’annexe d’information (médiation, rétractation si le contrat est conclu à distance ou hors établissement).')
    template = models.get(desk, d['template_id'])
    _, missing = models.render_text(template, _values(desk, d, ent, cli, number=d['number'] or 'NUMERO', issue_date=d['issue_date'] or _today(desk).isoformat()),
                                    _line_values(d))
    hard = [x for x in missing if x in REQUIRED_FOR_ISSUE or d['kind'] not in ('facture', 'avoir')]
    if hard:
        blockers.append('Informations à compléter : %s.' % ', '.join(x.replace('_', ' ') for x in hard[:12]))
    soft = [x for x in missing if x not in hard]
    if soft:
        warnings.append('Mentions facultatives non renseignées : %s.' % ', '.join(x.replace('_', ' ') for x in soft[:12]))
    if d['kind'] == 'facture' and snap.get('reference_convention') and d['subtype'] == 'temps':
        conv = get(desk, snap['reference_convention']['id']) if snap['reference_convention'].get('id') else None
        if conv and conv['subtype'] == 'forfait':
            warnings.append('La convention prévoit un forfait : ces temps ne doivent pas être facturés en plus des diligences incluses au forfait.')
    report = {'bloquants': blockers, 'avertissements': warnings, 'variables_manquantes': missing, 'controle_le': _now(),
              'conforme': not blockers, 'portee': 'Contrôles AxiorHub (mentions, fiscalité, calculs) ; validation XSD/Schematron et plateforme non incluses.'}
    if persist:
        desk.db.execute('UPDATE b53_documents SET checks_json=?,state=CASE WHEN state=\'brouillon\' THEN \'a_controler\' ELSE state END WHERE id=?',
                        (json.dumps(report, ensure_ascii=False), doc_id))
        desk.db.commit()
    return report


def approval_hash(desk, d):
    ent = entity(desk)
    return _sha({'snapshot': d['snapshot'], 'template': d['template_id'], 'kind': d['kind'], 'matter': d['matter'], 'entity_version': ent['version'],
                 'client': client(desk, d['matter']), 'route': ent['transport'] if d['kind'] in ('facture', 'avoir') else 'remise'})


def approve(desk, actor, role, doc_id, version, shown_hash=''):
    """Approbation d'une VERSION précise (empreinte du contenu, du client, du cabinet et du trajet) par un compte habilité."""
    _require(desk, actor, role, 'facturation')
    d = get(desk, doc_id)
    if d['state'] not in ('brouillon', 'a_controler', 'approuve'):
        raise Stop('document_emis_non_modifiable')
    if int(version) != d['version']:
        raise Stop('version_en_conflit')
    report = check(desk, actor, role, doc_id)
    if report['bloquants']:
        raise Stop('document_a_completer')
    h = approval_hash(desk, get(desk, doc_id))
    if shown_hash and shown_hash != h:
        raise Stop('fiche_modifiee_depuis_affichage')
    now = _now()
    series, number = d['series'], d['number']
    if d['kind'] in ('devis', 'convention', 'lettre_mission') and not d['number']:
        series, number = _next_number(desk, d['kind'])          # référence commerciale (non fiscale) attribuée à l'approbation
    cur = desk.db.execute("UPDATE b53_documents SET state='approuve',approved_hash=?,approved_by=?,approved_at=?,number=?,series=?,version=version+1,updated=? "
                          "WHERE id=? AND version=?", (h, actor, now, number, series or '', now, doc_id, d['version']))
    if not cur.rowcount:
        desk.db.rollback()
        raise Stop('version_en_conflit')
    _event(desk, doc_id, 'approbation', {'empreinte': h[:16]}, actor)
    desk.db.commit()
    return get(desk, doc_id)


def _next_number(desk, kind, year=None):
    """Numéro suivant de la série (préfixe + année) sous verrou de la transaction appelante ; jamais réutilisé."""
    year = year or _today(desk).year
    series = '%s-%d' % (SERIES[kind], year)
    row = desk.db.execute('SELECT last_number,version FROM b53_sequences WHERE entity=? AND series=?', ('cabinet', series)).fetchone()
    if row:
        n = row['last_number'] + 1
        cur = desk.db.execute('UPDATE b53_sequences SET last_number=?,last_date=?,version=version+1 WHERE entity=? AND series=? AND version=?',
                              (n, _today(desk).isoformat(), 'cabinet', series, row['version']))
        if not cur.rowcount:
            raise Stop('numerotation_concurrente')
    else:
        n = 1
        desk.db.execute('INSERT INTO b53_sequences(entity,series,last_number,last_date) VALUES(?,?,?,?)', ('cabinet', series, n, _today(desk).isoformat()))
    return series, '%s-%04d' % (series, n)


# ------------------------------------------------------------------------------------------------ émission
def issue(desk, actor, role, doc_id, idem_key):
    """Émission d'une facture ou d'un avoir APPROUVÉ : numéro définitif et date attribués atomiquement (une seule fois par clé),
    instantané figé, fichiers produits depuis cet instantané puis publiés dans FACTURATION. Une panne de publication laisse la
    facture émise avec « fichiers à publier » : le numéro n'est jamais réattribué."""
    _require(desk, actor, role, 'facturation')
    key = str(idem_key or '')
    if not re.fullmatch(r'[A-Za-z0-9-]{8,80}', key):
        raise Stop('identifiant_requete_invalide')
    d = get(desk, doc_id)
    if d['kind'] not in ('facture', 'avoir'):
        raise Stop('emission_reservee_aux_factures')
    if d['issue_key'] == key or d['number']:
        if d['files_state'] == 'a_publier':
            publish(desk, doc_id)
        return get(desk, doc_id)                                # même clic ou déjà émise : aucune seconde émission
    ent = entity(desk)
    if ent['autorite'] != 'interne':
        raise Stop('numerotation_par_outil_de_reference')
    if d['state'] != 'approuve' or approval_hash(desk, d) != d['approved_hash']:
        raise Stop('approbation_requise_ou_perimee')
    if check(desk, actor, role, doc_id, persist=False)['bloquants']:
        raise Stop('document_a_completer')
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        fresh = desk.db.execute('SELECT number,version,issue_key FROM b53_documents WHERE id=?', (doc_id,)).fetchone()
        if fresh['number']:
            desk.db.rollback()
            return get(desk, doc_id)
        series, number = _next_number(desk, d['kind'])
        today = _today(desk).isoformat()
        snap = d['snapshot']
        if not snap['dates'].get('echeance'):
            snap['dates']['echeance'] = (date.fromisoformat(today) + timedelta(days=int(ent['conditions'].get('delai_paiement_jours') or 30))).isoformat()
        snap['emis'] = {'numero': number, 'date': today, 'emetteur': ent['identite'], 'client': client(desk, d['matter']), 'fiscal': snap['fiscal']}
        desk.db.execute("UPDATE b53_documents SET state='emise',number=?,series=?,issue_date=?,issue_key=?,snapshot_json=?,files_state='a_publier',"
                        "payment_state='impayee',version=version+1,updated=? WHERE id=? AND number IS NULL", (number, series, today, key,
                                                                                                             json.dumps(snap, ensure_ascii=False), _now(), doc_id))
        desk.db.execute("UPDATE b53_allocations SET state='facture' WHERE document=?", (doc_id,))
        desk.db.execute('INSERT INTO b53_versions(document,version,snapshot_json,author,action,at) VALUES(?,?,?,?,?,?)',
                        (doc_id, d['version'] + 1, json.dumps(snap, ensure_ascii=False), actor, 'emission', _now()))
        _event(desk, doc_id, 'emission', {'numero': number}, actor)
        if d['kind'] == 'avoir' and d['origin']:
            desk.db.execute("UPDATE b53_documents SET state='rectifiee',updated=? WHERE id=?", (_now(), d['origin']))
            _event(desk, d['origin'], 'rectification', {'avoir': number}, actor)
        desk.db.commit()
    except BaseException:
        desk.db.rollback()
        raise
    try:                                                        # relevé commun des factures (Cabinet › Temps et honoraires)
        from . import time500
        sign = -1 if d['kind'] == 'avoir' else 1
        time500.add_invoice(desk, d['matter'], number, today, str(fees.euros(sign * d['snapshot']['totaux']['ttc_cents'])),
                            note=KINDS[d['kind']], origin='facturation5653', external_id=doc_id)
    except Exception:   # noqa: BLE001 — relevé indicatif ; le registre 5.6.53 fait foi
        pass
    publish(desk, doc_id)
    return get(desk, doc_id)


def snapshot_for_xml(desk, d):
    snap = d['snapshot']
    em = snap.get('emis') or {}
    ident, cli = em.get('emetteur') or entity(desk)['identite'], em.get('client') or client(desk, d['matter'])
    origin = None
    if d['origin']:
        o = get(desk, d['origin'])
        origin = {'numero': o['number'], 'date': o['issue_date']}
    return {'numero': d['number'] or '', 'date_emission': d['issue_date'] or _today(desk).isoformat(), 'date_echeance': snap['dates'].get('echeance'),
            'date_prestation': snap['dates'].get('prestation') or d['issue_date'], 'type_fiscal': 'avoir' if d['kind'] == 'avoir' else (
                'facture_acompte' if d['subtype'] == 'provision' else 'facture'), 'lignes': snap['lignes'], 'totaux': snap['totaux'], 'fiscal': snap['fiscal'],
            'profil_secret': snap.get('profil_secret', True), 'reference_convention': (snap.get('reference_convention') or {}).get('numero'),
            'facture_origine': origin, 'clause_retard': fiscal.late_payment_clause(snap['fiscal']['perimetre']),
            'emetteur': {'nom': ident.get('nom'), 'siren': (ident.get('siren') or '').replace(' ', ''), 'tva': ident.get('tva'), 'email': ident.get('email'),
                         'iban': ident.get('iban'), 'adresse': {'ligne1': ident.get('adresse_ligne1'), 'code_postal': ident.get('code_postal'),
                                                               'ville': ident.get('ville'), 'pays': ident.get('pays') or 'FR'}},
            'client': {'nom': cli.get('nom'), 'siren': (cli.get('siren') or '').replace(' ', ''), 'tva': cli.get('vat_number'), 'email': cli.get('email'),
                       'adresse': {'ligne1': cli.get('adresse_ligne1'), 'code_postal': cli.get('code_postal'), 'ville': cli.get('ville'), 'pays': cli.get('country') or 'FR'}},
            'termes_confidentiels': snap.get('termes_confidentiels') or []}


def billing_folder(desk, m):
    """Sous-dossier FACTURATION du dossier client (nom réglable comme les autres sous-dossiers du cabinet)."""
    from .docrequest520 import folders
    name = folders(desk).get('facturation') or FOLDER
    return clean_path(m['path'] + '/' + name)


def _safe_name(text):
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]+', ' ', str(text or '')).strip()[:80]


def build_files(desk, d):
    """Fichiers depuis l'instantané (facture et avoir : Word, PDF/A-3 avec XML CII si LibreOffice est présent, XML CII et UBL)."""
    from . import facturx5653 as fx
    ent, cli = entity(desk), client(desk, d['matter'])
    template = models.get(desk, d['template_id'])
    values = _values(desk, d, ent, (d['snapshot'].get('emis') or {}).get('client') or cli, number=d['number'] or '', issue_date=d['issue_date'])
    docx, missing = models.render_docx(template, values, _line_values(d), models.logo(desk), draft=not d['number'] and d['kind'] in ('facture', 'avoir'))
    files = {'docx': docx}
    report = {}
    if d['kind'] in ('facture', 'avoir') and d['number']:
        xsnap = snapshot_for_xml(desk, d)
        xml = fx.cii(xsnap)
        files['cii'] = xml.encode('utf-8')
        files['ubl'] = fx.ubl(xsnap).encode('utf-8')
        visible, _ = models.render_text(template, values, _line_values(d))
        report = fx.check_cii(xml, xsnap, visible)
        try:
            pdf = fx.office_pdf(docx)
        except Stop as ex:
            pdf, report['pdf'] = None, str(ex).split(':')[0]
        if pdf:
            try:
                files['pdf'] = fx.embed_xml(pdf, files['cii'])
                report['facturx'] = 'PDF/A-3 avec factur-x.xml (relation Alternative) — validation PDF/A non exécutée ici'
                if fx.embedded_xml(files['pdf']) != files['cii']:
                    raise Stop('xml_joint_illisible')
            except Stop as ex:
                files['pdf'] = pdf
                report['facturx'] = 'PDF produit sans XML joint : %s' % str(ex).split(':')[0]
        else:
            report.setdefault('facturx', 'LibreOffice absent : PDF/A-3 et Factur-X non produits (XML CII et UBL disponibles)')
    else:
        try:
            files['pdf'] = fx.office_pdf(docx)
        except Stop:
            files['pdf'] = None
    return files, report, missing


def publish(desk, doc_id):
    """Publication atomique dans FACTURATION : écriture exclusive, relecture et empreinte ; aucun écrasement. Reprise idempotente."""
    d = get(desk, doc_id)
    m = _matter(desk, d['matter'])
    from .document_projects import _dav
    client_dav = _dav(desk)
    folder = billing_folder(desk, m)
    try:
        client_dav.ensure_folder(folder, clean_path(m['path']))
    except Stop:
        pass
    files, report, missing = build_files(desk, d)
    who = _safe_name(((d['snapshot'].get('emis') or {}).get('client') or client(desk, d['matter'])).get('nom') or 'client')
    stem = '%s - %s - %s' % (d['number'] or ('brouillon ' + d['id'][-6:]), KINDS[d['kind']], who)
    published = dict(d['files'])
    ok = True
    for key, ext, ctype in (('docx', '.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'), ('pdf', '.pdf', 'application/pdf'),
                            ('cii', ' - factur-x.xml', 'application/xml'), ('ubl', ' - UBL.xml', 'application/xml')):
        data = files.get(key)
        if not data or key in published:
            continue
        path = folder + '/' + stem + ext
        sha = hashlib.sha256(data).hexdigest()
        try:
            client_dav.put_file(path, data, ctype)
        except Stop:
            try:
                st = client_dav.stat(path)
                existing = client_dav.download({'path': path, 'etag': st['etag'], 'size': st.get('size', 0)})
                if hashlib.sha256(existing).hexdigest() != sha:
                    ok = False
                    report.setdefault('conflits', []).append(path)
                    continue
            except Stop:
                ok = False
                continue
        published[key] = {'chemin': path, 'sha256': sha}
    state = 'publies' if ok else 'a_publier'
    desk.db.execute('UPDATE b53_documents SET files_json=?,files_state=?,checks_json=?,updated=? WHERE id=?',
                    (json.dumps(published, ensure_ascii=False), state, json.dumps({**d['checks'], 'structure': report}, ensure_ascii=False, default=str), _now(), doc_id))
    _event(desk, doc_id, 'publication', {'fichiers': [v['chemin'] for v in published.values()], 'etat': state})
    desk.db.commit()
    return get(desk, doc_id)


def export_draft(desk, actor, role, doc_id):
    """Brouillon ou document non fiscal (devis, convention, lettre) déposé dans FACTURATION en Word (et PDF si possible)."""
    _require(desk, actor, role, 'facturation')
    d = get(desk, doc_id)
    if d['kind'] in ('facture', 'avoir') and not d['number']:
        raise Stop('apercu_seulement_avant_emission')
    if d['kind'] in ('devis', 'convention', 'lettre_mission') and d['state'] not in ('approuve', 'envoye', 'en_signature', 'signe', 'accepte'):
        raise Stop('approbation_requise_ou_perimee')
    if d['kind'] in ('devis', 'convention', 'lettre_mission') and approval_hash(desk, d) != d['approved_hash']:
        raise Stop('approbation_requise_ou_perimee')
    return publish(desk, doc_id)


# ------------------------------------------------------------------------------------------------ après émission
def credit_note(desk, actor, role, doc_id, reason, lines=None):
    """Avoir lié à une facture émise (l'original n'est jamais modifié) ; brouillon à relire, approuver puis émettre."""
    _require(desk, actor, role, 'facturation')
    src = get(desk, doc_id)
    if src['kind'] != 'facture' or not src['number']:
        raise Stop('avoir_sur_facture_emise_seulement')
    if not str(reason or '').strip():
        raise Stop('motif_avoir_requis')
    snap = json.loads(json.dumps(src['snapshot']))
    snap.pop('emis', None)
    snap['kind'] = 'avoir'
    snap['imputations'] = []
    snap['lignes'] = lines if lines else [{k: v for k, v in l.items()} for l in snap['lignes']]
    snap['textes']['motif_avoir'] = str(reason)[:500]
    snap['textes']['facture_origine'] = '%s du %s' % (src['number'], src['issue_date'])
    snap = compute(snap, entity(desk), client(desk, src['matter']))
    if snap['totaux']['ttc_cents'] > src['snapshot']['totaux']['ttc_cents']:
        raise Stop('avoir_superieur_a_la_facture')
    template = models.default_for(desk, 'avoir')
    did = 'fin-' + secrets.token_hex(9)                       # 5.6.54 : identifiant technique unique
    now = _now()
    desk.db.execute('INSERT INTO b53_documents(id,kind,subtype,matter,state,snapshot_json,template_id,origin,engagement,created_by,created,updated) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (did, 'avoir', '', src['matter'], 'brouillon', json.dumps(snap, ensure_ascii=False), template['id'], doc_id,
                                                       src['engagement'], actor, now, now))
    _event(desk, did, 'preparation', {'avoir_de': src['number'], 'motif': str(reason)[:200]}, actor)
    desk.db.commit()
    return get(desk, did)


def record_payment(desk, actor, role, doc_id, amount, day, method, reference=''):
    """Règlement constaté (montant, date, moyen) ; l'état « réglée » vient des règlements enregistrés, jamais d'un simple marquage."""
    _require(desk, actor, role, 'facturation')
    d = get(desk, doc_id)
    if d['kind'] != 'facture' or not d['number']:
        raise Stop('reglement_sur_facture_emise_seulement')
    value = fees.cents(fees.dec(amount, 'montant'))
    if value <= 0:
        raise Stop('montant_invalide')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(day or '')):
        raise Stop('date_invalide')
    # 5.6.54 : un règlement portant une référence (virement, chèque) n'est enregistré qu'une fois ; sans référence, deux règlements
    # identiques le même jour sont deux règlements distincts (aucune fusion silencieuse).
    ref = str(reference or '').strip()
    pid = ('pay-' + hashlib.sha256((doc_id + '|' + ref).encode()).hexdigest()[:16]) if ref else 'pay-' + secrets.token_hex(8)
    desk.db.execute('INSERT OR IGNORE INTO b53_payments(id,document,amount_cents,day,method,source,reference,actor,created) VALUES(?,?,?,?,?,?,?,?,?)',
                    (pid, doc_id, value, day, str(method or 'virement')[:40], 'saisie', str(reference)[:120], actor, _now()))
    _refresh_payment_state(desk, doc_id)
    _event(desk, doc_id, 'reglement', {'montant': value, 'date': day}, actor)
    desk.db.commit()
    return get(desk, doc_id)


def _refresh_payment_state(desk, doc_id):
    d = desk.db.execute('SELECT snapshot_json FROM b53_documents WHERE id=?', (doc_id,)).fetchone()
    due = json.loads(d['snapshot_json'])['totaux']['net_a_payer_cents']
    paid = desk.db.execute('SELECT COALESCE(SUM(amount_cents),0) FROM b53_payments WHERE document=? AND verified=1', (doc_id,)).fetchone()[0]
    state = 'reglee' if paid >= due else ('partielle' if paid > 0 else 'impayee')
    desk.db.execute('UPDATE b53_documents SET payment_state=?,updated=? WHERE id=?', (state, _now(), doc_id))


def submit(desk, actor, role, doc_id):
    """Transmission par le SEUL trajet configuré (une opération par facture) ; l'envoi est validé sur la fiche d'opération."""
    _require(desk, actor, role, 'facturation')
    d = get(desk, doc_id)
    if d['kind'] not in ('facture', 'avoir') or not d['number']:
        raise Stop('transmission_apres_emission')
    if d['delivery_op']:
        return {'operation': d['delivery_op'], 'deja': True}
    ent = entity(desk)
    route = ent['transport']
    if route == 'aucun':
        raise Stop('aucun_trajet_configure')
    if route not in QUALIFIED_TRANSPORTS:
        raise Stop('trajet_non_qualifie')
    pdf = (d['files'] or {}).get('pdf')
    if not pdf or 'Alternative' not in str(d['checks'].get('structure', {}).get('facturx', '')) and 'factur-x' not in str(d['checks'].get('structure', {}).get('facturx', '')):
        raise Stop('facturx_requis_pour_la_transmission')
    if not d['checks'].get('structure', {}).get('ok'):
        raise Stop('controles_structure_en_echec')
    from . import connexions5652 as cx
    t = d['snapshot']['totaux']
    op = cx.prepare(desk, actor, role, 'facturation.transmettre', {
        'matter': d['matter'], 'document': pdf['chemin'], 'title': '%s %s' % (KINDS[d['kind']], d['number']), 'content_type': 'application/pdf',
        'numero': d['number'], 'date_echeance': d['snapshot']['dates'].get('echeance'), 'total_ht': fees.fmt(t['ht_cents']), 'total_tva': fees.fmt(t['tva_cents']),
        'total_ttc': fees.fmt(t['ttc_cents']), 'provisions_imputees': fees.fmt(t['deja_regle_cents']), 'canal': TRANSPORTS[route], 'declencheur': 'facturation'})
    desk.db.execute("UPDATE b53_documents SET delivery_op=?,state='transmission_en_cours',updated=? WHERE id=? AND delivery_op=''", (op['id'], _now(), doc_id))
    _event(desk, doc_id, 'transmission_preparee', {'operation': op['id'], 'trajet': route}, actor)
    desk.db.commit()
    return {'operation': op['id'], 'deja': False}


def sync_deliveries(desk):
    """Projection des statuts de transmission (opérations) sur les factures ; un dépôt n'est pas un règlement."""
    ensure_schema(desk)
    for r in desk.db.execute("SELECT id,delivery_op,state FROM b53_documents WHERE delivery_op<>'' AND state IN ('transmission_en_cours','deposee')").fetchall():
        op = desk.db.execute('SELECT state,remote_status,error FROM connexion_ops5652 WHERE id=?', (r['delivery_op'],)).fetchone()
        if not op:
            continue
        new = {'envoyee': 'deposee', 'terminee': 'deposee', 'refusee': 'rejetee', 'refusee_distante': 'rejetee', 'annulee': 'emise'}.get(op['state'])
        if new and new != r['state']:
            desk.db.execute('UPDATE b53_documents SET state=?,remote_status=?,delivery_op=CASE WHEN ?=\'emise\' THEN \'\' ELSE delivery_op END,updated=? WHERE id=?',
                            (new, op['remote_status'] or op['state'], new, _now(), r['id']))
            _event(desk, r['id'], 'statut_transmission', {'etat': new, 'distant': op['remote_status'], 'erreur': op['error']})
    desk.db.commit()


def mark_engagement(desk, actor, role, doc_id, state, version, proof=''):
    """Devis ou convention : envoyé, accepté, refusé, signé (preuve requise : document signé ou courriel d'accord confirmé par l'avocat)."""
    _require(desk, actor, role, 'facturation')
    d = get(desk, doc_id)
    allowed = {'devis': ('envoye', 'accepte', 'refuse', 'expire'), 'convention': ('envoye', 'en_signature', 'signe', 'resilie', 'remplace'),
               'lettre_mission': ('envoye', 'en_signature', 'signe', 'resilie', 'remplace')}.get(d['kind'], ())
    if state not in allowed:
        raise Stop('etat_document_invalide')
    if d['state'] not in ('approuve', 'envoye', 'en_signature', 'accepte', 'signe'):
        raise Stop('approbation_requise_ou_perimee')
    if state in ('signe', 'accepte') and not str(proof or '').strip():
        raise Stop('preuve_accord_requise')
    cur = desk.db.execute('UPDATE b53_documents SET state=?,version=version+1,updated=? WHERE id=? AND version=?', (state, _now(), doc_id, int(version)))
    if not cur.rowcount:
        raise Stop('version_en_conflit')
    if state == 'signe' and d['snapshot'].get('estimation'):
        fees.mark_signed(desk, d['snapshot']['estimation']['id'], d['snapshot']['totaux']['ht_cents'], '%s %s (%s)' % (KINDS[d['kind']], d['number'], str(proof)[:120]))
    if state == 'signe':
        desk.db.execute("UPDATE b53_documents SET state='remplace',updated=? WHERE matter=? AND kind=? AND id<>? AND state='signe'",
                        (_now(), d['matter'], d['kind'], doc_id))
    _event(desk, doc_id, 'etat_' + state, {'preuve': str(proof)[:300]}, actor)
    desk.db.commit()
    return get(desk, doc_id)


# ------------------------------------------------------------------------------------------------ vues
def documents(desk, matter='', kinds=None, limit=200):
    ensure_schema(desk)
    from .membres5650 import viewer_hidden
    hidden = viewer_hidden() or frozenset()
    sql = 'SELECT * FROM b53_documents WHERE state<>\'abandonne\'' + (' AND matter=?' if matter else '') + ' ORDER BY updated DESC LIMIT ?'
    out = []
    names = {str(m['id']): (matter_display(m), str(m.get('client_name') or '')) for m in load_matters(desk.c)}
    for r in desk.db.execute(sql, ((str(matter), int(limit)) if matter else (int(limit),))):
        if r['matter'] in hidden or (kinds and r['kind'] not in kinds):
            continue
        snap = json.loads(r['snapshot_json'])
        out.append({'id': r['id'], 'kind': r['kind'], 'kind_label': KINDS.get(r['kind'], r['kind']), 'subtype': r['subtype'], 'matter': r['matter'],
                    'matter_name': names.get(r['matter'], (r['matter'], ''))[0], 'client': names.get(r['matter'], ('', ''))[1], 'state': r['state'],
                    'state_label': STATES.get(r['state'], r['state']), 'number': r['number'] or '', 'issue_date': r['issue_date'],
                    'ht_cents': snap['totaux']['ht_cents'], 'ttc_cents': snap['totaux']['ttc_cents'], 'net_cents': snap['totaux']['net_a_payer_cents'],
                    'payment_state': r['payment_state'], 'payment_label': PAYMENT_STATES.get(r['payment_state'], ''), 'version': r['version'], 'updated': r['updated']})
    return out


def matter_summary(desk, mid):
    """Bloc « Honoraires et facturation » du dossier : convention applicable, budget, temps validés, déjà facturé, provisions encaissées,
    reste à facturer, solde connu et date de fraîcheur. Une lecture partielle est dite partielle, jamais « aucun impayé »."""
    ensure_schema(desk)
    _matter(desk, mid)
    partial = []
    conv = _active_convention(desk, mid)
    docs = documents(desk, mid)
    issued = [x for x in docs if x['kind'] == 'facture' and x['number']]
    credits = [x for x in docs if x['kind'] == 'avoir' and x['number']]
    billed_ht = sum(x['ht_cents'] for x in issued) - sum(x['ht_cents'] for x in credits)
    paid = desk.db.execute("SELECT COALESCE(SUM(p.amount_cents),0) FROM b53_payments p JOIN b53_documents d ON d.id=p.document WHERE d.matter=? AND p.verified=1",
                           (str(mid),)).fetchone()[0]
    provisions_paid = sum(x['ttc_cents'] for x in issued if x['subtype'] == 'provision' and x['payment_state'] == 'reglee')
    due = sum(x['net_cents'] for x in issued) - sum(x['net_cents'] for x in credits)
    try:
        from . import time500
        t = time500.summary(desk, mid)
        validated, budget, level = t['amount_cents'], t['budget_cents'], t['level']
    except Exception:   # noqa: BLE001
        validated, budget, level = None, None, ''
        partial.append('temps passés')
    to_bill = None
    if conv:
        to_bill = max(0, conv['snapshot']['totaux']['ht_cents'] - billed_ht) if conv['subtype'] != 'temps' else (
            max(0, (validated or 0) - billed_ht) if validated is not None else None)
    return {'convention': {'id': conv['id'], 'numero': conv['number'], 'etat': conv['state_label'], 'version': conv['version'],
                           'montant_ht_cents': conv['snapshot']['totaux']['ht_cents'], 'mode': conv['subtype']} if conv else None,
            'budget_cents': budget, 'niveau_budget': level, 'temps_valides_cents': validated, 'deja_facture_ht_cents': billed_ht,
            'provisions_encaissees_cents': provisions_paid, 'reste_a_facturer_ht_cents': to_bill, 'solde_connu_cents': due - paid,
            'synchronise_le': _now(), 'lecture_partielle': partial, 'documents': docs[:12]}


# ------------------------------------------------------------------------------------------------ propositions de l'agent (« À préparer »)
SUGGESTIONS = {'sans_convention': ('Mission nouvelle sans convention', 'Relire la convention'),
               'temps_a_facturer': ('Temps validés non facturés', 'Préparer la facture'),
               'provision_prevue': ('Provision prévue par la convention', 'Préparer la facture de provision'),
               'budget_proche': ('Budget presque atteint', 'Préparer l’information du client')}


def suggestions(desk, matters=None, refresh=True):
    """Propositions de préparation, avec leur déclencheur, leur source et le choix attendu. Clé unique (dossier, événement, version de
    convention, phase, type) : une proposition refusée ou reportée ne réapparaît pas à chaque passage."""
    ensure_schema(desk)
    from .membres5650 import viewer_hidden
    hidden = viewer_hidden() or frozenset()
    all_matters = [m for m in load_matters(desk.c) if str(m['id']) not in hidden and (matters is None or str(m['id']) in matters)]
    if refresh:
        now = _now()
        for m in all_matters[:300]:
            mid = str(m['id'])
            conv = _active_convention(desk, mid)
            found = []
            if not conv and not desk.db.execute("SELECT 1 FROM b53_documents WHERE matter=? AND kind IN ('convention','lettre_mission') AND state NOT IN ('abandonne')",
                                                (mid,)).fetchone() and str(m.get('status') or 'active') not in ('archive', 'clos', 'archived'):
                found.append(('sans_convention', '', None, {'raison': 'Aucune convention ni lettre de mission enregistrée pour ce dossier.'}))
            entries = billable_time(desk, mid)
            if entries:
                amount = sum(int(e['amount_cents']) for e in entries)
                found.append(('temps_a_facturer', 'temps:%d' % len(entries), amount, {'raison': '%d temps validé(s) non facturé(s) : %s HT.' % (len(entries), fees.fmt(amount)),
                                                                                    'source': 'Cabinet › Temps et honoraires'}))
            if conv and conv['state'] == 'signe' and conv['snapshot'].get('provision_pct') and not desk.db.execute(
                    "SELECT 1 FROM b53_documents WHERE matter=? AND kind='facture' AND subtype='provision' AND state<>'abandonne'", (mid,)).fetchone():
                amount = fees.provision(conv['snapshot']['totaux']['ht_cents'], conv['snapshot']['provision_pct'])
                found.append(('provision_prevue', 'convention:%s:v%s' % (conv['id'], conv['version']), amount,
                              {'raison': 'La convention %s prévoit une provision de %s %%.' % (conv['number'], conv['snapshot']['provision_pct']), 'source': conv['number']}))
            try:
                from . import time500
                t = time500.summary(desk, mid)
                if t['level'] in ('proche', 'depasse'):
                    found.append(('budget_proche', 'budget:%s' % t['level'], t['amount_cents'], {'raison': '%s %% du budget consommé.' % t['ratio_pct'],
                                                                                               'source': 'Cabinet › Temps et honoraires'}))
            except Exception:   # noqa: BLE001
                pass
            for kind, event, amount, trig in found:
                key = hashlib.sha256('|'.join((mid, kind, event)).encode()).hexdigest()[:32]
                desk.db.execute('INSERT INTO b53_suggestions(key,matter,kind,trigger_json,amount_cents,state,created,updated) VALUES(?,?,?,?,?,?,?,?) '
                                'ON CONFLICT(key) DO UPDATE SET amount_cents=excluded.amount_cents,trigger_json=excluded.trigger_json,updated=excluded.updated',
                                (key, mid, kind, json.dumps(trig, ensure_ascii=False), amount, 'proposee', now, now))
        desk.db.commit()
    names = {str(m['id']): (matter_display(m), str(m.get('client_name') or '')) for m in all_matters}
    today = _today(desk).isoformat()
    out = []
    for r in desk.db.execute("SELECT * FROM b53_suggestions WHERE state='proposee' OR (state='reportee' AND until<=?) ORDER BY updated DESC LIMIT 100", (today,)):
        if r['matter'] not in names:
            continue
        title, action = SUGGESTIONS[r['kind']]
        out.append({'key': r['key'], 'matter': r['matter'], 'matter_name': names[r['matter']][0], 'client': names[r['matter']][1], 'kind': r['kind'],
                    'titre': title, 'action': action, 'declencheur': json.loads(r['trigger_json']), 'montant_cents': r['amount_cents']})
    return out


def decide_suggestion(desk, actor, role, key, decision, until=''):
    _require(desk, actor, role, 'facturation')
    row = desk.db.execute('SELECT * FROM b53_suggestions WHERE key=?', (str(key),)).fetchone()
    if not row:
        raise Stop('element_absent')
    _matter(desk, row['matter'])
    if decision not in ('refusee', 'reportee', 'traitee'):
        raise Stop('decision_file_invalide')
    if decision == 'reportee':
        until = str(until or (_today(desk) + timedelta(days=7)).isoformat())[:10]
    desk.db.execute('UPDATE b53_suggestions SET state=?,until=?,updated=? WHERE key=?', (decision, until or '', _now(), key))
    desk.db.commit()
    return {'key': key, 'state': decision}


def queue_items(desk, prefix='', actor='cabinet', role='administrateur'):
    """File unique « À décider / À relire » : documents à approuver ou émettre et propositions de l'agent (sans copie d'état)."""
    try:
        ensure_schema(desk)
        items = []
        for d in documents(desk, limit=60):
            if d['state'] in ('a_controler', 'approuve') and not d['number'] or (d['kind'] in ('facture', 'avoir') and d['state'] == 'approuve'):
                items.append({'class': 'engagement', 'object': 'facturation:' + d['id'], 'revision': d['updated'], 'matter': d['matter'], 'matter_name': d['matter_name'],
                              'title': '%s %s — %s (%s)' % (d['kind_label'], d['number'] or 'brouillon', fees.fmt(d['ttc_cents']), d['state_label']),
                              'consequence': 'Document financier : relire, approuver ou émettre (droit « facturation »).',
                              'href': prefix + '/facturation?document=' + d['id'], 'choices': ('ouvrir',)})
        for s in suggestions(desk, refresh=False)[:20]:
            items.append({'class': 'decision', 'object': 'proposition:' + s['key'], 'revision': s['key'], 'matter': s['matter'], 'matter_name': s['matter_name'],
                          'title': '%s — %s' % (s['titre'], s['declencheur'].get('raison', '')), 'consequence': s['action'],
                          'href': prefix + '/facturation?vue=a-preparer#prop-' + s['key'], 'choices': ('ouvrir',)})
        return items
    except Stop:
        return []
