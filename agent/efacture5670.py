"""5.6.70 : facturation électronique intégrée — SuperPDP et B2Brouter.

Demande du cabinet du 22 octobre 2026 : envoi et réception des factures par les plateformes agréées, suivi de leur traitement,
consultation des comptes rendus de déclaration (e-reporting) et export des factures par période pour le comptable.

- Envoi : inchangé dans son principe (5.6.53) — une facture émise (Factur-X) part par le trajet choisi dans la fiche du cabinet,
  après validation de l'opération ; B2Brouter accepte désormais le fichier Factur-X tel quel (import puis envoi).
- Réception : les factures fournisseurs sont lues sur les connexions vérifiées (« Recevoir les factures ») et conservées ici (référence,
  émetteur, montants, état) ; le fichier se télécharge ou s'enregistre dans Nextcloud à la demande. Rien n'est accepté ni refusé
  automatiquement chez la plateforme.
- Suivi : les événements de cycle de vie (statuts « fr:2xx » de la réforme, états B2Brouter) des factures transmises sont relus et
  affichés en clair.
- Comptes rendus : les e-reportings transmis à l'administration (PPF) par la plateforme sont listés avec leur période et leur état.
- Export : un fichier ZIP par période — journal des factures émises et reçues (CSV, séparateur « ; », montants en euros) et, au
  choix, les PDF des factures émises.
Aucun appel n'est fait par le modèle : seules les connexions configurées dans Paramètres › Connexions sont utilisées.
"""
from datetime import date, datetime, timedelta, timezone
from html import escape as e
import base64
import csv
import io
import json
from pathlib import PurePosixPath
import re
import zipfile

from .common import Stop, load_matters, matter_display

PROVIDERS = ('superpdp', 'b2brouter')
SETTING_FOLDER = 'efacture5670:dossier_fournisseurs'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS ef5670_recues(id TEXT PRIMARY KEY, provider TEXT NOT NULL, remote_id TEXT NOT NULL, numero TEXT NOT NULL DEFAULT '',
  date TEXT NOT NULL DEFAULT '', emetteur TEXT NOT NULL DEFAULT '', total_ttc TEXT NOT NULL DEFAULT '', total_ht TEXT NOT NULL DEFAULT '',
  tva TEXT NOT NULL DEFAULT '', devise TEXT NOT NULL DEFAULT 'EUR', etat TEXT NOT NULL DEFAULT '', chemin TEXT NOT NULL DEFAULT '',
  recu_le TEXT NOT NULL, maj TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ef5670_suivi(provider TEXT NOT NULL, remote_ref TEXT NOT NULL, document TEXT NOT NULL DEFAULT '', code TEXT NOT NULL,
  libelle TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(provider, remote_ref, code, at));
CREATE TABLE IF NOT EXISTS ef5670_rapports(id TEXT PRIMARY KEY, provider TEXT NOT NULL, remote_id TEXT NOT NULL, nature TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT '', debut TEXT NOT NULL DEFAULT '', fin TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL DEFAULT '',
  erreurs TEXT NOT NULL DEFAULT '', maj TEXT NOT NULL);
'''
# Statuts du cycle de vie de la réforme (norme XP Z12-012) et de l'API SuperPDP, en clair
STATUS = {'fr:200': 'Déposée', 'fr:201': 'Émise par la plateforme', 'fr:202': 'Reçue par la plateforme du destinataire', 'fr:203': 'Mise à disposition',
          'fr:204': 'Prise en charge', 'fr:205': 'Approuvée', 'fr:206': 'Approuvée partiellement', 'fr:207': 'En litige', 'fr:208': 'Suspendue',
          'fr:209': 'Complétée', 'fr:210': 'Refusée', 'fr:211': 'Paiement transmis', 'fr:212': 'Encaissée', 'fr:213': 'Rejetée', 'fr:501': 'Irrecevable',
          'api:uploaded': 'Déposée', 'api:invalid': 'Invalide', 'api:validated': 'Validée', 'api:sent': 'Transmise', 'api:rejected': 'Rejetée',
          # états B2Brouter (factures et comptes rendus fiscaux)
          'new': 'Nouvelle', 'sending': 'En cours d’envoi', 'sent': 'Envoyée', 'read': 'Lue', 'downloaded': 'Téléchargée', 'annulled': 'Annulée',
          'billed': 'Comptabilisée', 'expired': 'Expirée', 'issued': 'Émise', 'accepted': 'Acceptée', 'registered': 'Enregistrée', 'refused': 'Refusée',
          'received': 'Reçue', 'closed': 'Close', 'paid': 'Payée', 'allegedly_paid': 'Paiement annoncé', 'performing_ocr': 'Lecture en cours',
          'ocr_failed': 'Lecture impossible', 'annotated': 'Annotée', 'payment_processed': 'Paiement traité', 'error': 'Erreur', 'invalid': 'Invalide',
          'processing': 'En traitement', 'signed': 'Signée', 'acknowledged': 'Accusée réception', 'deposited': 'Déposée', 'clearing': 'En contrôle',
          'registered_with_errors': 'Enregistrée avec erreurs', 'annullating': 'Annulation en cours'}
BAD = ('fr:207', 'fr:210', 'fr:213', 'fr:501', 'api:invalid', 'api:rejected', 'refused', 'error', 'invalid', 'registered_with_errors')
ROLES = {'SE': 'Vendeur', 'BY': 'Acheteur'}
NATURES = {'transaction': 'Transactions', 'payment': 'Paiements'}


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def label(code):
    code = str(code or '')
    return STATUS.get(code, code.replace('_', ' ') or '—')


def _pick(obj, *paths):
    """Première valeur présente parmi des chemins (« seller.name ») ; tolère les variantes de schéma des plateformes."""
    for path in paths:
        cur = obj
        for part in path.split('.'):
            cur = cur.get(part) if isinstance(cur, dict) else None
            if cur is None:
                break
        if cur not in (None, '', {}, []):
            return cur.get('value') if isinstance(cur, dict) and 'value' in cur else cur
    return ''


def _amount(value):
    try:
        return '%.2f' % float(str(value).replace(',', '.'))
    except (TypeError, ValueError):
        return ''


# ------------------------------------------------------------------------------------------------ connexions
def connections(desk, owner='cabinet'):
    """Connexions SuperPDP / B2Brouter vérifiées dont la lecture des factures est activée."""
    from . import connexions5652 as cx
    rows = [r for r in cx._connections_for(desk, 'einvoice.receive', owner) if r['provider'] in PROVIDERS]
    seen, out = set(), []
    for r in rows:   # une connexion par plateforme (production d'abord)
        if r['provider'] not in seen:
            seen.add(r['provider'])
            out.append(r)
    return out


def _adapter(desk, row):
    from . import connexions5652 as cx
    return cx.adapter(desk, row)


def normalize_received(provider, item):
    """Facture reçue normalisée (SuperPDP : facture EN 16931 ; B2Brouter : facture réduite)."""
    if provider == 'superpdp':
        en = item.get('en_invoice') or {}
        last = (item.get('events') or [{}])[-1] if isinstance(item.get('events'), list) and item.get('events') else {}
        return {'remote_id': str(item.get('id') or ''), 'numero': str(_pick(en, 'number') or ''), 'date': str(_pick(en, 'issue_date') or str(item.get('created_at') or '')[:10]),
                'emetteur': str(_pick(en, 'seller.name', 'seller.trading_name', 'seller.legal_registration_identifier.value') or ''),
                'total_ttc': _amount(_pick(en, 'totals.total_with_vat', 'totals.amount_due_for_payment')),
                'total_ht': _amount(_pick(en, 'totals.total_without_vat')), 'tva': _amount(_pick(en, 'totals.total_vat_amount')),
                'devise': str(_pick(en, 'currency_code') or 'EUR'), 'etat': str(last.get('status_code') or '')}
    contact = item.get('contact') or {}
    return {'remote_id': str(item.get('id') or ''), 'numero': str(item.get('number') or item.get('numero') or ''),
            'date': str(item.get('date') or '')[:10], 'emetteur': str(contact.get('name') or item.get('emetteur') or ''),
            'total_ttc': _amount(item.get('total')), 'total_ht': _amount(item.get('taxable_base')), 'tva': _amount(item.get('tax_amount')),
            'devise': str(item.get('currency') or 'EUR'), 'etat': str(item.get('state') or item.get('etat') or '')}


def normalize_report(provider, item):
    if provider == 'superpdp':
        events = item.get('events') if isinstance(item.get('events'), list) else []
        last = events[-1] if events else {}
        return {'remote_id': str(item.get('id') or ''), 'nature': NATURES.get(item.get('kind'), str(item.get('kind') or '')),
                'role': ROLES.get(item.get('role_code'), str(item.get('role_code') or '')), 'debut': str(item.get('start_period') or ''),
                'fin': str(item.get('end_period') or ''), 'etat': str(last.get('status_code') or last.get('status') or ('transmis' if events else 'en préparation')),
                'erreurs': ''}
    errors = item.get('errors') if isinstance(item.get('errors'), list) else []
    return {'remote_id': str(item.get('id') or ''), 'nature': str(item.get('label') or item.get('type') or 'Compte rendu fiscal'),
            'role': 'Vendeur', 'debut': str(item.get('tax_point_date') or item.get('sent_at') or '')[:10], 'fin': str(item.get('sent_at') or '')[:10],
            'etat': str(item.get('state') or ''), 'erreurs': '; '.join(str(x.get('message') if isinstance(x, dict) else x) for x in errors)[:300]}


def sync(desk, owner='cabinet'):
    """Relit, sur chaque plateforme connectée : factures reçues, suivi des factures transmises, comptes rendus de déclaration."""
    ensure_schema(desk)
    rows = connections(desk, owner)
    if not rows:
        raise Stop('aucune_plateforme_de_facturation_verifiee')
    report = {'recues': 0, 'suivis': 0, 'rapports': 0, 'erreurs': []}
    for row in rows:
        provider = row['provider']
        try:
            ad = _adapter(desk, row)
        except Stop as ex:
            report['erreurs'].append('%s : %s' % (provider, str(ex).split(':')[0]))
            continue
        try:
            for item in (ad.call('einvoice.receive_detail', {'limit': 100}).get('factures') or [])[:200]:
                n = normalize_received(provider, item)
                if not n['remote_id']:
                    continue
                ident = provider + ':' + n['remote_id']
                old = desk.db.execute('SELECT chemin FROM ef5670_recues WHERE id=?', (ident,)).fetchone()
                desk.db.execute('INSERT OR REPLACE INTO ef5670_recues(id,provider,remote_id,numero,date,emetteur,total_ttc,total_ht,tva,devise,etat,chemin,recu_le,maj) '
                                'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,COALESCE((SELECT recu_le FROM ef5670_recues WHERE id=?),?),?)',
                                (ident, provider, n['remote_id'], n['numero'][:120], n['date'][:10], n['emetteur'][:200], n['total_ttc'], n['total_ht'], n['tva'],
                                 n['devise'][:3], n['etat'][:40], old['chemin'] if old else '', ident, _now(), _now()))
                report['recues'] += 1
        except Stop as ex:
            report['erreurs'].append('%s (réception) : %s' % (provider, str(ex).split(':')[0]))
        try:
            for item in (ad.call('ereporting.list', {'limit': 100}).get('rapports') or [])[:200]:
                n = normalize_report(provider, item)
                if not n['remote_id']:
                    continue
                desk.db.execute('INSERT OR REPLACE INTO ef5670_rapports VALUES(?,?,?,?,?,?,?,?,?,?)',
                                (provider + ':' + n['remote_id'], provider, n['remote_id'], n['nature'][:80], n['role'][:20], n['debut'], n['fin'],
                                 n['etat'][:60], n['erreurs'], _now()))
                report['rapports'] += 1
        except Stop as ex:
            report['erreurs'].append('%s (comptes rendus) : %s' % (provider, str(ex).split(':')[0]))
        desk.db.commit()
        report['suivis'] += _track(desk, ad, provider, report)
    desk.db.commit()
    desk.audit('efacture5670_actualisation', {k: v for k, v in report.items() if k != 'erreurs'} | {'erreurs': len(report['erreurs'])})
    return report


def _track(desk, ad, provider, report):
    """Événements des factures transmises par cette plateforme (opérations de transmission validées)."""
    count = 0
    try:
        rows = desk.db.execute("SELECT d.id AS doc, o.remote_ref FROM b53_documents d JOIN connexion_ops5652 o ON o.id=d.delivery_op "
                               "JOIN connexions5652 c ON c.id=o.connection WHERE c.provider=? AND o.remote_ref<>'' ORDER BY d.updated DESC LIMIT 60",
                               (provider,)).fetchall()
    except Exception:   # noqa: BLE001 — tables de facturation absentes : rien à suivre
        return 0
    for r in rows:
        try:
            out = ad.call('einvoice.status', {'remote_ref': r['remote_ref']})
        except Stop as ex:
            report['erreurs'].append('%s (suivi %s) : %s' % (provider, r['remote_ref'], str(ex).split(':')[0]))
            continue
        events = out.get('evenements') if isinstance(out.get('evenements'), list) else []
        if not events and out.get('etat'):
            events = [{'status_code': out['etat'], 'created_at': _now()}]
        for ev in events[-30:]:
            code = str(ev.get('status_code') or ev.get('status') or '')
            if not code:
                continue
            desk.db.execute('INSERT OR IGNORE INTO ef5670_suivi VALUES(?,?,?,?,?,?)',
                            (provider, str(r['remote_ref']), r['doc'], code[:40], label(code)[:120], str(ev.get('created_at') or _now())[:25]))
            count += 1
    desk.db.commit()
    return count


# ------------------------------------------------------------------------------------------------ lectures
def received(desk, limit=200):
    ensure_schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT * FROM ef5670_recues ORDER BY date DESC, recu_le DESC LIMIT ?', (int(limit),))]


def reports(desk, limit=100):
    ensure_schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT * FROM ef5670_rapports ORDER BY fin DESC, debut DESC LIMIT ?', (int(limit),))]


def last_status(desk, doc_id):
    ensure_schema(desk)
    r = desk.db.execute('SELECT code,libelle,at FROM ef5670_suivi WHERE document=? ORDER BY at DESC LIMIT 1', (doc_id,)).fetchone()
    return dict(r) if r else None


def issued(desk, du='', au='', limit=200):
    """Factures et avoirs émis (numérotés), avec dernier état de traitement connu."""
    from . import facturation5653 as fact
    out = []
    for d in fact.documents(desk, kinds=('facture', 'avoir'), limit=None):
        if not d['number']:
            continue
        if (du and d['issue_date'] < du) or (au and d['issue_date'] > au):
            continue
        st = last_status(desk, d['id'])
        d['suivi'] = st['libelle'] if st else ''
        d['suivi_mauvais'] = bool(st and st['code'] in BAD)
        out.append(d)
    out.sort(key=lambda x: (x['issue_date'], x['number']), reverse=True)
    return out[:limit] if limit else out


# ------------------------------------------------------------------------------------------------ fichiers
def _row_for(desk, ident):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM ef5670_recues WHERE id=?', (str(ident),)).fetchone()
    if not row:
        raise Stop('facture_recue_absente')
    return dict(row)


def _fetch_file(desk, owner, rec):
    from . import connexions5652 as cx
    rows = [r for r in cx._connections_for(desk, 'einvoice.download', owner) if r['provider'] == rec['provider']] or \
           [r for r in connections(desk, owner) if r['provider'] == rec['provider']]
    if not rows:
        raise Stop('aucune_plateforme_de_facturation_verifiee')
    files = _adapter(desk, rows[0]).call('einvoice.download', {'remote_ref': rec['remote_id']}).get('fichiers') or {}
    name, raw = next(iter(files.items()), ('', b''))
    if not raw:
        raise Stop('fichier_facture_indisponible')
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    kind = 'application/pdf' if raw[:4] == b'%PDF' else ('application/xml' if raw.lstrip()[:1] == b'<' else 'application/octet-stream')
    ext = {'application/pdf': '.pdf', 'application/xml': '.xml'}.get(kind, '')
    safe = re.sub(r'[^\w .-]+', '_', '%s %s %s' % (rec['date'], rec['emetteur'] or 'fournisseur', rec['numero'] or rec['remote_id'])).strip()[:120]
    return safe + ext, kind, raw


def download(desk, owner, role, ident):
    from .facturation5653 import _require
    _require(desk, owner, role, 'facturation')
    name, kind, raw = _fetch_file(desk, owner, _row_for(desk, ident))
    return {'download': {'filename': name, 'mime': kind, 'base64': base64.b64encode(raw).decode('ascii')}}


def supplier_folder(desk):
    value = str(desk.settings(SETTING_FOLDER, '') or '')
    if value:
        return value
    roots = (desk.c.get('nextcloud') or {}).get('roots') or ['/']
    return roots[0].rstrip('/') + '/COMPTABILITE/FACTURES FOURNISSEURS'


def save_folder(desk, owner, role, path):
    from .common import clean_path, under
    from .facturation5653 import _require
    _require(desk, owner, role, 'facturation')
    path = clean_path(str(path or '').strip())
    roots = (desk.c.get('nextcloud') or {}).get('roots') or []
    if not any(under(path, r) for r in roots):
        raise Stop('dossier_hors_racines_nextcloud')
    desk.setting(SETTING_FOLDER, path)
    return {'ok': True, 'message': 'Dossier des factures fournisseurs : %s' % path}


def store(desk, owner, role, ident):
    """Enregistre le fichier d'une facture reçue dans Nextcloud (dossier des factures fournisseurs, sous-dossier de l'année)."""
    from .facturation5653 import _require
    from .fichiers5670 import client as _dav
    _require(desk, owner, role, 'facturation')
    rec = _row_for(desk, ident)
    if rec['chemin']:
        return {'ok': True, 'chemin': rec['chemin'], 'message': 'Déjà enregistrée : %s' % rec['chemin']}
    name, kind, raw = _fetch_file(desk, owner, rec)
    base = supplier_folder(desk)
    roots = (desk.c.get('nextcloud') or {}).get('roots') or [base]
    folder = base.rstrip('/') + '/' + (rec['date'][:4] or str(date.today().year))
    dav = _dav(desk)
    boundary = next((r for r in roots if folder.startswith(r.rstrip('/') + '/') or folder == r), roots[0])
    dav.ensure_folder(folder, boundary)
    path = folder + '/' + name
    dav.put_file(path, raw, kind)
    desk.db.execute('UPDATE ef5670_recues SET chemin=?,maj=? WHERE id=?', (path, _now(), rec['id']))
    desk.db.commit()
    desk.audit('efacture5670_facture_enregistree', {'facture': rec['id'], 'chemin': path, 'par': str(owner)[:80]})
    return {'ok': True, 'chemin': path, 'message': 'Facture enregistrée dans Nextcloud : %s' % path}


# ------------------------------------------------------------------------------------------------ export comptable
def period(du='', au=''):
    """Période demandée (AAAA-MM-JJ) ; par défaut, le mois précédent."""
    today = date.today()
    first = today.replace(day=1)
    default_au = first - timedelta(days=1)
    default_du = default_au.replace(day=1)
    try:
        d1 = date.fromisoformat(str(du)) if du else default_du
        d2 = date.fromisoformat(str(au)) if au else default_au
    except ValueError:
        raise Stop('periode_invalide') from None
    if d2 < d1 or (d2 - d1).days > 400:
        raise Stop('periode_invalide')
    return d1.isoformat(), d2.isoformat()


def _csv(rows, header):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';', lineterminator='\r\n')
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return ('﻿' + buf.getvalue()).encode('utf-8')   # BOM : ouverture directe dans un tableur


def export(desk, owner, role, du='', au='', with_files=True, dav=None):
    """ZIP pour le comptable : journal des factures émises et reçues de la période et, au choix, les PDF émis."""
    from . import honoraires5653 as fees
    from .facturation5653 import _require
    _require(desk, owner, role, 'facturation')
    du, au = period(du, au)
    out_rows = issued(desk, du, au, limit=None)
    in_rows = [r for r in received(desk, 5000) if du <= (r['date'] or '') <= au]
    euros = lambda cents: ('%.2f' % (int(cents or 0) / 100)).replace('.', ',')
    em = _csv([[d['number'], d['issue_date'], d['kind_label'], d['client'], d['matter_name'], euros(d['ht_cents']), euros(int(d['ttc_cents']) - int(d['ht_cents'])),
                euros(d['ttc_cents']), d['state_label'], d['suivi'], d['payment_label']] for d in out_rows],
              ['Numéro', 'Date', 'Type', 'Client', 'Dossier', 'Montant HT', 'TVA', 'Montant TTC', 'État', 'Traitement plateforme', 'Règlement'])
    rec = _csv([[r['numero'], r['date'], r['emetteur'], r['total_ht'].replace('.', ','), r['tva'].replace('.', ','), r['total_ttc'].replace('.', ','),
                 r['devise'], label(r['etat']), r['provider'], r['chemin']] for r in in_rows],
               ['Numéro', 'Date', 'Fournisseur', 'Montant HT', 'TVA', 'Montant TTC', 'Devise', 'État', 'Plateforme', 'Fichier Nextcloud'])
    notes = ['Export AxiorHub du %s au %s.' % (du, au), '%d facture(s) ou avoir(s) émis, %d facture(s) reçue(s).' % (len(out_rows), len(in_rows)),
             'Montants en euros, séparateur « ; », encodage UTF-8.']
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('factures-emises-%s-%s.csv' % (du, au), em)
        z.writestr('factures-recues-%s-%s.csv' % (du, au), rec)
        missing, total = 0, 0
        if with_files and out_rows:
            if dav is None:
                try:
                    from .fichiers5670 import client
                    dav = client(desk)
                except Stop:
                    dav = None
            from . import facturation5653 as fact
            for d in out_rows:
                pdf = ((fact.get(desk, d['id']).get('files') or {}).get('pdf') or {})
                path = pdf.get('chemin')
                if not path or dav is None:
                    missing += 1
                    continue
                try:
                    raw = dav.download(dav.stat(path))
                except Stop:
                    missing += 1
                    continue
                total += len(raw)
                if total > 60_000_000:
                    missing += 1
                    continue
                z.writestr('pdf/' + PurePosixPath(path).name, raw)
            if missing:
                notes.append('%d PDF non joint(s) (fichier absent, Nextcloud injoignable ou export limité à 60 Mo) : voir la colonne « État ».' % missing)
        z.writestr('LISEZMOI.txt', '\r\n'.join(notes) + '\r\n')
    desk.audit('efacture5670_export', {'du': du, 'au': au, 'emises': len(out_rows), 'recues': len(in_rows), 'par': str(owner)[:80]})
    return {'download': {'filename': 'factures-%s-%s.zip' % (du, au), 'mime': 'application/zip', 'base64': base64.b64encode(buf.getvalue()).decode('ascii')},
            'message': '%d facture(s) émise(s) et %d reçue(s) exportées.' % (len(out_rows), len(in_rows))}


# ------------------------------------------------------------------------------------------------ page
def html(desk, prefix, owner, role):
    from . import facturation5653 as fact
    from . import honoraires5653 as fees
    ensure_schema(desk)
    rows = connections(desk, owner)
    ent = fact.entity(desk)
    route = fact.TRANSPORTS.get(ent['transport'], ent['transport'])
    plat = ''.join('<li><strong>%s</strong> — connexion vérifiée (%s)</li>' % (e({'superpdp': 'SuperPDP', 'b2brouter': 'B2Brouter'}[r['provider']]),
                                                                             e(r['environment'])) for r in rows)
    intro = ('<section class="ax-card ef70"><h2>Plateformes de facturation électronique</h2><ul>%s</ul><p>Envoi des factures émises : <strong>%s</strong> '
             '(fiche du cabinet, onglet « Réglages et modèles »). %s</p>%s</section>') % (
        plat or '<li>Aucune plateforme vérifiée : configurez SuperPDP ou B2Brouter dans <a href="%s">Paramètres › Connexions</a> et activez '
                '« Recevoir les factures ».</li>' % e(prefix + '/parametres/connexions', quote=True),
        e(route), 'Chaque envoi reste une opération à valider ; rien ne part sans vous.',
        ('<form class="m5-form m5-inline" data-api="m5670/efacture/actualiser" data-reload><button class="ax-btn" type="submit">Actualiser depuis les '
         'plateformes</button><p role="status" class="m5-status"></p></form>') if rows else '')
    em = issued(desk, limit=40)
    em_rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td class="num">%s</td><td>%s</td><td%s>%s</td><td>%s</td></tr>' % (
        e(d['number']), e(d['issue_date']), e(d['client'] or d['matter_name']), e(fees.fmt(d['ttc_cents'])), e(d['state_label']),
        ' class="ef70-ko"' if d['suivi_mauvais'] else '', e(d['suivi'] or '—'), e(d['payment_label'] or '—')) for d in em)
    sent = ('<section class="ax-card ef70"><h2>Factures émises — suivi du traitement</h2>%s</section>') % (
        ('<div class="table"><table><thead><tr><th>Numéro</th><th>Date</th><th>Client</th><th>TTC</th><th>État</th><th>Traitement plateforme</th>'
         '<th>Règlement</th></tr></thead><tbody>%s</tbody></table></div>' % em_rows) if em_rows else '<p class="ax-muted">Aucune facture émise.</p>')
    rec = received(desk, 100)
    rec_rows = ''.join(('<tr><td>%s</td><td>%s</td><td>%s</td><td class="num">%s</td><td>%s</td><td class="ef70-actions">'
                        '<button type="button" class="ax-btn ghost m5-export" data-api="m5670/efacture/fichier?id=%s">Télécharger</button>%s</td></tr>') % (
        e(r['date']), e(r['emetteur'] or '—'), e(r['numero'] or r['remote_id']), e((r['total_ttc'] or '—').replace('.', ',')), e(label(r['etat'])),
        e(r['id'], quote=True),
        ('<span class="ax-muted">%s</span>' % e(r['chemin'])) if r['chemin'] else
        ('<form class="m5-form m5-inline m5-mini" data-api="m5670/efacture/enregistrer" data-reload><input type="hidden" name="id" value="%s">'
         '<button class="ax-btn ghost" type="submit">Enregistrer dans Nextcloud</button></form>' % e(r['id'], quote=True))) for r in rec)
    inbox = ('<section class="ax-card ef70"><h2>Factures reçues</h2><p class="ax-muted">Factures fournisseurs reçues par les plateformes. Dossier '
             'd’enregistrement : <strong>%s</strong>.</p>%s%s</section>') % (
        e(supplier_folder(desk)),
        ('<div class="table"><table><thead><tr><th>Date</th><th>Fournisseur</th><th>Numéro</th><th>TTC</th><th>État</th><th>Fichier</th></tr></thead>'
         '<tbody>%s</tbody></table></div>' % rec_rows) if rec_rows else '<p class="ax-muted">Aucune facture reçue pour l’instant (« Actualiser depuis les plateformes »).</p>',
        ('<details><summary>Changer le dossier d’enregistrement</summary><form class="m5-form" data-api="m5670/efacture/dossier" data-reload>'
         '<label>Dossier Nextcloud<input name="chemin" value="%s" maxlength="400"></label><button class="ax-btn" type="submit">Enregistrer</button>'
         '<p role="status" class="m5-status"></p></form></details>' % e(supplier_folder(desk), quote=True)) if role in ('administrateur', 'avocat') else '')
    rep = reports(desk)
    rep_rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s → %s</td><td%s>%s</td><td>%s</td></tr>' % (
        e({'superpdp': 'SuperPDP', 'b2brouter': 'B2Brouter'}.get(r['provider'], r['provider'])), e(r['nature']), e(r['role']), e(r['debut'] or '—'),
        e(r['fin'] or '—'), ' class="ef70-ko"' if r['etat'] in BAD or r['erreurs'] else '', e(label(r['etat'])), e(r['erreurs'] or '')) for r in rep)
    decl = ('<section class="ax-card ef70"><h2>Comptes rendus de déclaration (e-reporting)</h2><p class="ax-muted">Données de transactions et de '
            'paiements transmises à l’administration fiscale par la plateforme, avec leur état.</p>%s</section>') % (
        ('<div class="table"><table><thead><tr><th>Plateforme</th><th>Nature</th><th>Rôle</th><th>Période</th><th>État</th><th>Erreurs</th></tr></thead>'
         '<tbody>%s</tbody></table></div>' % rep_rows) if rep_rows else '<p class="ax-muted">Aucun compte rendu relu pour l’instant.</p>')
    du, au = period()
    exp = ('<section class="ax-card ef70" id="ef70-export"><h2>Export pour le comptable</h2><p class="ax-muted">Un fichier ZIP : journal des factures '
           'émises et reçues de la période (CSV) et, au choix, les PDF des factures émises.</p><div class="ef70-period" data-ef70-export>'
           '<label>Du<input type="date" name="du" value="%s"></label><label>Au<input type="date" name="au" value="%s"></label>'
           '<label class="m5-check"><input type="checkbox" name="pdf" value="1" checked> Joindre les PDF émis</label>'
           '<button type="button" class="ax-btn m5-export" data-api="m5670/efacture/export?du=%s&amp;au=%s&amp;pdf=1">Exporter</button></div></section>') % (
        e(du), e(au), e(du), e(au))
    return '<div class="ef70-page">' + intro + sent + inbox + decl + exp + '</div>'
