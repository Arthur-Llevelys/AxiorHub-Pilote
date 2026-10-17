"""5.6.55 : page « Facturation et honoraires » refondue — périmètre des dossiers clients, tableau de bord et liste « À faire ».

Constat de l'avocat sur la 5.6.54 : la page proposait une convention d'honoraires pour CHAQUE dossier du registre, y compris les dossiers
internes du cabinet (« 00 - Bibliothèque Cabinet », « 03 - Modèles », « 09 - Scanner »…), et répétait la même carte des dizaines de fois.

- Périmètre : seuls les dossiers CLIENTS entrent dans la facturation. Un dossier interne est reconnu par son nom (numéro d'ordre en tête,
  « 03 - Modèles », ou mot-clé : bibliothèque, modèles, comptabilité, administratif, scanner…) ; un dossier archivé est écarté ; l'avocat
  exclut ou réintègre tout dossier d'un clic (décision conservée, prioritaire sur la détection).
- « Mission nouvelle sans convention » ne vise que les dossiers apparus APRÈS la mise en service de la facturation (les dossiers déjà
  présents ne déclenchent rien) ; les propositions de cette nature déjà affichées pour des dossiers existants sont retirées.
- Tableau de bord : temps validés à facturer, factures impayées (dont échues), brouillons en cours, encaissé ce mois ; une seule liste
  « À faire » priorisée (échéances dépassées, factures prêtes à émettre, documents à envoyer, brouillons, propositions), une action par
  ligne ; vue « Dossiers » : la facturation de chaque dossier client sur une ligne.
"""
from datetime import date, datetime, timedelta, timezone
import json
import re

from .common import Stop, load_matters, matter_display
from . import facturation5653 as fact
from . import honoraires5653 as fees

SCOPE = 'facturation5655:perimetre'
INTERNAL_NUMBER = re.compile(r'^\s*\d{1,3}\s*[-–.]\s+\S')
INTERNAL_WORDS = re.compile(r'biblioth[eè]que|mod[eè]les?\b|comptabilit|administratif|scanner|collaboration|\bstage\b|notes? et recherches?|'
                            r'formation|veille|interne|gestion du cabinet|archives? du cabinet|^\s*(?:\d+\s*[-–.]\s*)?ia\s*$|cabinet\s*$', re.I)
SCHEMA = '''CREATE TABLE IF NOT EXISTS b55_matter_seen(matter TEXT PRIMARY KEY, first_seen TEXT NOT NULL, baseline INTEGER NOT NULL);'''
NEW_MATTER_DAYS = 120


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_schema(desk):
    fact.ensure_schema(desk)
    desk.db.executescript(SCHEMA)


def _scope(desk):
    raw = desk.settings(SCOPE, None) or {}
    return {'exclus': dict(raw.get('exclus') or {}), 'inclus': dict(raw.get('inclus') or {})}


def _portfolio(desk):
    try:
        return {r[0]: r[1] for r in desk.db.execute('SELECT matter,state FROM matter_portfolio')}
    except Exception:   # noqa: BLE001 — portefeuille pas encore calculé
        return {}


def classify(desk, m, scope=None, states=None):
    """('client' | 'interne' | 'exclu' | 'archive', motif). La décision de l'avocat prime sur la détection."""
    scope = scope if scope is not None else _scope(desk)
    mid = str(m['id'])
    if mid in scope['inclus']:
        return 'client', 'réintégré par l’avocat'
    if mid in scope['exclus']:
        return 'exclu', 'exclu par l’avocat'
    name = matter_display(m)
    roles = {str(c.get('role') or '') for c in m.get('correspondents') or []}
    short = len(name.split()) <= 5 and not re.search(r'\d{5,}', name)          # un nom de dossier client porte souvent un numéro
    if 'client' not in roles and (INTERNAL_NUMBER.match(name) or (short and INTERNAL_WORDS.search(name))):
        return 'interne', 'dossier interne du cabinet (nom « %s »)' % name
    state = (states if states is not None else _portfolio(desk)).get(mid, '')
    if state == 'archived':
        return 'archive', 'dossier archivé'
    return 'client', ''


def client_matters(desk):
    scope, states = _scope(desk), _portfolio(desk)
    out = []
    for m in load_matters(desk.c):
        kind, why = classify(desk, m, scope, states)
        if kind == 'client':
            out.append(m)
    return out


def set_scope(desk, actor, role, mid, decision):
    """Exclure un dossier de la facturation (« Pas un dossier client ») ou le réintégrer ; décision conservée et réversible."""
    fact._require(desk, actor, role, 'facturation')
    fact._matter(desk, mid)
    if decision not in ('exclure', 'inclure', 'auto'):
        raise Stop('decision_file_invalide')
    scope = _scope(desk)
    scope['exclus'].pop(str(mid), None)
    scope['inclus'].pop(str(mid), None)
    if decision != 'auto':
        scope['exclus' if decision == 'exclure' else 'inclus'][str(mid)] = {'par': actor, 'le': _now()}
    desk.setting(SCOPE, scope)
    if decision == 'exclure':
        desk.db.execute("UPDATE b53_suggestions SET state='retiree',updated=? WHERE matter=? AND state IN ('proposee','reportee')", (_now(), str(mid)))
        desk.db.commit()
    return scope


def excluded(desk):
    """Dossiers écartés (exclus par l'avocat, internes détectés, archivés), avec leur motif — pour pouvoir les réintégrer."""
    scope, states = _scope(desk), _portfolio(desk)
    out = []
    for m in load_matters(desk.c):
        kind, why = classify(desk, m, scope, states)
        if kind != 'client':
            out.append({'id': str(m['id']), 'nom': matter_display(m), 'type': kind, 'motif': why})
    return sorted(out, key=lambda x: x['nom'].lower())


def note_seen(desk):
    """Mémorise la première apparition de chaque dossier. Au premier passage, tous les dossiers présents forment la référence (aucun
    n'est « nouveau ») ; les propositions « sans convention » déjà affichées pour eux sont retirées."""
    ensure_schema(desk)
    first_run = desk.db.execute('SELECT COUNT(*) FROM b55_matter_seen').fetchone()[0] == 0
    now = _now()
    for m in load_matters(desk.c, unfiltered=True):
        desk.db.execute('INSERT OR IGNORE INTO b55_matter_seen(matter,first_seen,baseline) VALUES(?,?,?)', (str(m['id']), now, 1 if first_run else 0))
    if first_run:
        desk.db.execute("UPDATE b53_suggestions SET state='retiree',updated=? WHERE kind='sans_convention' AND state IN ('proposee','reportee')", (now,))
    desk.db.commit()


def is_new(desk, mid):
    row = desk.db.execute('SELECT first_seen,baseline FROM b55_matter_seen WHERE matter=?', (str(mid),)).fetchone()
    if not row or row['baseline']:
        return False
    return row['first_seen'] >= (datetime.now(timezone.utc) - timedelta(days=NEW_MATTER_DAYS)).isoformat()


# ------------------------------------------------------------------------------------------------ tableau de bord
def _due(d):
    snap = d.get('snapshot') or {}
    return str((snap.get('dates') or {}).get('echeance') or '')


def dashboard(desk):
    """Chiffres clés sur les dossiers clients (lecture : date de calcul jointe ; une source illisible est signalée, pas comptée à zéro)."""
    ensure_schema(desk)
    clients = {str(m['id']) for m in client_matters(desk)}
    today = fact._today(desk).isoformat()
    month = today[:7]
    to_bill, partial = 0, []
    for mid in clients:
        try:
            to_bill += sum(int(e['amount_cents']) for e in fact.billable_time(desk, mid))
        except Exception:   # noqa: BLE001
            partial.append(mid)
    unpaid = unpaid_n = overdue = overdue_n = drafts = 0
    # 5.6.56 (audit 5.6.55, N26) : même périmètre que les listes (dossiers visibles par le lecteur) pour TOUS les agrégats.
    visible = {str(m['id']) for m in load_matters(desk.c)}
    for r in desk.db.execute("SELECT * FROM b53_documents WHERE state<>'abandonne'"):
        if r['matter'] not in visible:
            continue
        if r['kind'] == 'facture' and r['number'] and r['payment_state'] in ('impayee', 'partielle'):
            snap = json.loads(r['snapshot_json'])
            paid = desk.db.execute('SELECT COALESCE(SUM(amount_cents),0) FROM b53_payments WHERE document=? AND verified=1', (r['id'],)).fetchone()[0]
            rest = snap['totaux']['net_a_payer_cents'] - paid
            unpaid += rest
            unpaid_n += 1
            if (snap.get('dates') or {}).get('echeance') and snap['dates']['echeance'] < today:
                overdue += rest
                overdue_n += 1
        if r['state'] in ('brouillon', 'a_controler') or (r['state'] == 'approuve' and not r['number'] and r['kind'] in ('facture', 'avoir')):
            drafts += 1
    collected = sum(int(r['amount_cents']) for r in desk.db.execute(
        'SELECT p.amount_cents,d.matter FROM b53_payments p JOIN b53_documents d ON d.id=p.document WHERE p.verified=1 AND p.day LIKE ?', (month + '%',))
        if r['matter'] in visible)
    return {'a_facturer_cents': to_bill, 'impayes_cents': unpaid, 'impayes_nombre': unpaid_n, 'echus_cents': overdue, 'echus_nombre': overdue_n,
            'brouillons': drafts, 'encaisse_mois_cents': collected, 'calcule_le': _now(), 'lecture_partielle': partial}


PRIORITY = {'echue': 0, 'a_emettre': 1, 'a_envoyer': 2, 'brouillon': 3, 'temps_a_facturer': 4, 'provision_prevue': 4, 'diligences': 4,
            'budget_proche': 5, 'sans_convention': 6}


def todo(desk, refresh=True):
    """Liste « À faire » unique et priorisée : une ligne = un dossier, un motif, un montant, une action."""
    ensure_schema(desk)
    note_seen(desk)
    names = {str(m['id']): (matter_display(m), str(m.get('client_name') or '')) for m in load_matters(desk.c)}
    clients = {str(m['id']) for m in client_matters(desk)}
    today = fact._today(desk)
    items = []
    for r in desk.db.execute("SELECT * FROM b53_documents WHERE state NOT IN ('abandonne','remplace','resilie','refuse','expire','rectifiee')"):
        if r['matter'] not in names:
            continue
        snap = json.loads(r['snapshot_json'])
        base = {'matter': r['matter'], 'matter_name': names[r['matter']][0], 'client': names[r['matter']][1], 'document': r['id'],
                'montant_cents': snap['totaux']['ttc_cents'], 'kind_label': fact.KINDS.get(r['kind'], r['kind'])}
        if r['kind'] == 'facture' and r['number'] and r['payment_state'] in ('impayee', 'partielle'):
            due = (snap.get('dates') or {}).get('echeance') or ''
            if due and due < today.isoformat():
                late = (today - date.fromisoformat(due)).days
                items.append({**base, 'type': 'echue', 'titre': 'Facture %s échue depuis %d jour%s' % (r['number'], late, 's' if late > 1 else ''),
                              'action': 'Enregistrer un règlement', 'detail': 'Échéance du %s ; solde à vérifier avant toute relance.' % due})
        elif r['kind'] in ('facture', 'avoir') and r['state'] == 'approuve' and not r['number']:
            items.append({**base, 'type': 'a_emettre', 'titre': '%s approuvé%s, prêt%s à émettre' % (base['kind_label'], 'e' if r['kind'] == 'facture' else '',
                                                                                                     'e' if r['kind'] == 'facture' else ''),
                          'action': 'Émettre', 'detail': 'Le numéro définitif est attribué à l’émission.'})
        elif r['kind'] in ('devis', 'convention', 'lettre_mission') and r['state'] == 'approuve':
            items.append({**base, 'type': 'a_envoyer', 'titre': '%s %s approuvé%s, à envoyer au client' % (base['kind_label'], r['number'] or '',
                                                                                                           'e' if r['kind'] == 'convention' else ''),
                          'action': 'Ouvrir', 'detail': 'Déposez-la dans FACTURATION puis notez l’envoi ou la signature.'})
        elif r['state'] in ('brouillon', 'a_controler'):
            items.append({**base, 'type': 'brouillon', 'titre': '%s en brouillon' % base['kind_label'], 'action': 'Reprendre le brouillon',
                          'detail': 'Dernière modification le %s.' % r['updated'][:10]})
    for s in fact.suggestions(desk, matters=clients, refresh=refresh):
        if s['matter'] not in clients:
            continue
        if s['kind'] == 'sans_convention' and not is_new(desk, s['matter']):
            continue
        items.append({'matter': s['matter'], 'matter_name': s['matter_name'], 'client': s['client'], 'type': s['kind'], 'titre': s['titre'],
                      'action': s['action'], 'detail': s['declencheur'].get('raison', ''), 'montant_cents': s['montant_cents'], 'proposition': s['key']})
    try:   # 5.6.57 (A05) : diligences proposées par l'agent, à confirmer avant toute préfacture
        for r in desk.db.execute("SELECT matter,COUNT(*) n,SUM(minutes_est) mins FROM time500_proposals WHERE status='a_valider' GROUP BY matter"):
            if r['matter'] not in clients:
                continue
            items.append({'matter': r['matter'], 'matter_name': names[r['matter']][0], 'client': names[r['matter']][1], 'type': 'diligences',
                          'titre': '%d diligence(s) proposée(s) à confirmer (%d min estimées)' % (r['n'], r['mins'] or 0),
                          'action': 'Confirmer les diligences', 'detail': 'Estimations tirées des événements du dossier : rien n’est facturé sans confirmation.',
                          'montant_cents': None, 'lien': '/dossier?id=%s&vue=travaux#p57-diligences' % r['matter']})
    except Exception:   # noqa: BLE001 — module temps non initialisé
        pass
    items.sort(key=lambda x: (PRIORITY.get(x['type'], 9), -(x.get('montant_cents') or 0), x['matter_name'].lower()))
    return items


def overview(desk, q=''):
    """Vue « Dossiers » : pour chaque dossier client, convention, temps à facturer, facturé et solde ; lecture partielle signalée."""
    ensure_schema(desk)
    q = str(q or '').strip().lower()
    rows = []
    for m in client_matters(desk):
        name, client = matter_display(m), str(m.get('client_name') or '')
        if q and q not in (name + ' ' + client + ' ' + str(m['id'])).lower():
            continue
        try:
            s = fact.matter_summary(desk, m['id'])
            to_bill = sum(int(e['amount_cents']) for e in fact.billable_time(desk, m['id']))
        except Stop:
            continue
        rows.append({'id': str(m['id']), 'nom': name, 'client': client, 'convention': s['convention'], 'a_facturer_cents': to_bill,
                     'facture_ht_cents': s['deja_facture_ht_cents'], 'solde_cents': s['solde_connu_cents'], 'documents': len(s['documents']),
                     'partiel': bool(s['lecture_partielle'])})
    rows.sort(key=lambda r: (-(r['a_facturer_cents'] or 0), -(r['solde_cents'] or 0), -r['documents'], r['nom'].lower()))
    return rows


def setup_missing(desk):
    """Ce qui manque à la fiche du cabinet pour émettre (affiché en tête de page, avant toute émission)."""
    ent = fact.entity(desk)
    ident, fis = ent['identite'], ent['fiscal']
    missing = []
    if not ident.get('nom'):
        missing.append('nom du cabinet')
    if not ident.get('siren'):
        missing.append('SIREN')
    if not (ident.get('adresse_ligne1') and ident.get('ville')):
        missing.append('adresse')
    if fis.get('regime_cabinet') not in ('normal', 'franchise'):
        missing.append('régime de TVA')
    if not ident.get('iban'):
        missing.append('IBAN')
    return missing
