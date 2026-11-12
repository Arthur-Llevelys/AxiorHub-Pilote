"""5.6.97 (lot gestion, § 19) : honoraires et encaissement, du travail validé au règlement rapproché.

Le registre de facturation reste ``facturation5653`` (brouillon, approbation, émission numérotée, avoirs, règlements) et les relances
``relances5636``. Ce module ajoute, sans second registre :

Quatre indicateurs, chacun avec la définition montrée à l'utilisateur (``DEFINITIONS``) ; montants en ``Decimal`` (calcul en centimes) :
- **Travail à facturer** : temps validés, frais validés et jalons forfaitaires facturables encore non imputés. Un temps déjà réservé par un
  brouillon ou déjà facturé n'est pas recompté (réservations ``b53_allocations``, qui couvrent aussi les frais et les jalons).
- **Facturé non réglé** : solde des factures émises, net des avoirs émis et des règlements affectés (TTC), avec le facturé HT.
- **Impayés échus** : part de ce solde dont l'échéance est dépassée. Sous-total : jamais additionné au total non réglé.
- **Encaissé** : règlements affectés sur la période ; remboursements identifiés ; paiements non rapprochés montrés à part.

Source de référence par entité de facturation (``b53_entities.authority``) : AxiorHub (``interne``) ou l'outil configuré (Invoice Ninja).
Avec un outil externe, la synchronisation projette factures, avoirs et règlements dans ``fin_externes5697``, rapprochés par
(source, entité, objet, identifiant externe) : rejouer la même synchronisation ne crée aucun doublon et donne les mêmes montants.
Invoice Ninja reste **désactivé par défaut** ; l'activation exige un essai réussi des droits (lecture) sur l'instance. La seule émission
externe passe par une demande inscrite (clé stable) ; sur réponse inconnue, la facture est relue chez l'outil avant tout rejeu. Émission,
envoi et relance restent des actions distinctes ; les relances sont suspendues si le solde ne peut pas être vérifié (synchronisation
en échec, périmée ou en anomalie).

Forfaits : heures saisies et validées, budget interne, reste contractuel à facturer et prochain jalon sont montrés séparément ; le temps
dépassé ne devient jamais un supplément. Saisie manuelle de temps et de frais, validation et brouillon : aucun modèle d'IA.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

from .common import Stop
from . import gestion5697 as g

DEFINITIONS = {
    'travail_a_facturer': 'Diligences et frais validés, ou jalons forfaitaires facturables, encore non imputés. Les brouillons et montants déjà facturés ne sont pas recomptés.',
    'facture_non_regle': 'Solde des factures émises, net des avoirs et des règlements affectés.',
    'impayes_echus': 'Part du solde dont l’échéance est dépassée. Ce sous-total n’est pas additionné au total non réglé.',
    'encaisse': 'Règlements affectés sur la période, avec les remboursements identifiés. Les paiements non rapprochés sont montrés à part.'}
LIBELLES = {'travail_a_facturer': 'Travail à facturer', 'facture_non_regle': 'Facturé non réglé', 'impayes_echus': 'Impayés échus', 'encaisse': 'Encaissé'}
NATURES_FRAIS = {'frais': 'Frais refacturés', 'debours': 'Débours'}
SOURCES = {'interne': 'AxiorHub', 'invoice_ninja': 'Invoice Ninja'}
REGLAGE = 'honoraires5697:sources'           # {'invoice_ninja': {'actif': False, 'essai': {...}}} ; désactivé par défaut
PERIME_HEURES = 24
CENT = Decimal('0.01')


def euros(cents):
    return (Decimal(int(cents)) / 100).quantize(CENT)


def _montant(cents, devise='EUR'):
    return {'cents': int(cents), 'montant': str(euros(cents)), 'devise': devise}


def _aujourdhui(desk):
    from .facturation5653 import _today
    return _today(desk)


def _voir(desk, acteur):
    """Droit fin « voir les honoraires » ; un compte qui ne l'a pas ne voit ni montant, ni temps, ni convention."""
    g.droit(desk, acteur, 'voir_honoraires')


def _schemas(desk):
    from . import facturation5653, metier500
    g.schema(desk)
    facturation5653.ensure_schema(desk)
    metier500.ensure_schema(desk)


# ------------------------------------------------------------------------------------------------ réglages des sources
def reglages(desk):
    valeur = desk.settings(REGLAGE, {}) or {}
    ninja = valeur.get('invoice_ninja') if isinstance(valeur.get('invoice_ninja'), dict) else {}
    return {'invoice_ninja': {'actif': ninja.get('actif') is True, 'essai': ninja.get('essai') or None}}


def source_de(desk, entite='cabinet'):
    from . import facturation5653
    return facturation5653.entity(desk, entite)['autorite']


def essayer_source(desk, acteur, transport, source='invoice_ninja'):
    """Essai des droits et des effets avant activation : lecture seulement (aucune écriture d'essai chez le fournisseur)."""
    g.droit(desk, acteur, 'reglages')
    try:
        droits = transport.essai()
    except Stop as ex:
        droits = {'ok': False, 'erreur': str(ex).split(':')[0]}
    valeur = desk.settings(REGLAGE, {}) or {}
    valeur[source] = {**(valeur.get(source) or {}), 'essai': {'ok': bool(droits.get('ok')), 'lecture': bool(droits.get('lecture')), 'ecriture': bool(droits.get('ecriture')),
                                                              'erreur': str(droits.get('erreur') or ''), 'le': g.maintenant(), 'par': g.compte(acteur)}}
    desk.setting(REGLAGE, valeur)
    return reglages(desk)[source]


def activer_source(desk, acteur, source, actif):
    """Activer exige un essai réussi ; désactiver est toujours possible. Aucun lien ni solde n'est modifié par ce réglage."""
    g.droit(desk, acteur, 'reglages')
    if source not in ('invoice_ninja',):
        raise Stop('source_inconnue')
    actuel = reglages(desk)[source]
    if actif and not (actuel['essai'] or {}).get('ok'):
        raise Stop('essai_source_requis')
    valeur = desk.settings(REGLAGE, {}) or {}
    valeur[source] = {**(valeur.get(source) or {}), 'actif': bool(actif)}
    desk.setting(REGLAGE, valeur)
    return reglages(desk)[source]


# ------------------------------------------------------------------------------------------------ frais et jalons
def saisir_frais(desk, acteur, mid, data):
    from . import honoraires5653 as fees
    g.dossier(desk, acteur, mid)
    g.droit(desk, acteur, 'facturation')
    _schemas(desk)
    cle = g.cle_de(data.get('cle'))
    nature = str(data.get('nature') or 'frais')
    if nature not in NATURES_FRAIS:
        raise Stop('nature_frais_invalide')
    montant = fees.cents(fees.dec(data.get('montant_ht'), 'montant'))
    if montant <= 0:
        raise Stop('montant_invalide')
    libelle = g.texte(data.get('libelle'), 200)
    if not libelle:
        raise Stop('libelle_requis')
    jour = g.jour(data.get('jour'))
    params = g.empreinte([str(mid), nature, montant, libelle, jour])
    old = g.deja_fait(desk, 'frais5697', cle, params)
    if old:
        return dict(old)
    ident = g.nouvel_id('frs')
    # Saisi par l'avocat : validé d'emblée (comme un temps saisi à la main) ; un assistant saisit, un valideur valide.
    etat = 'valide' if g.acteur_de(acteur).role in g.VALIDEURS else 'saisi'
    with g.transaction(desk):
        desk.db.execute('INSERT INTO frais5697(id,parametres,matter,cle,jour,libelle,nature,montant_ht_cents,etat,saisi_par,valide_par,valide_le,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (ident, params, str(mid), cle, jour, libelle, nature, montant, etat, g.compte(acteur), g.compte(acteur) if etat == 'valide' else '',
                         g.maintenant() if etat == 'valide' else '', g.maintenant()))
        g.journal(desk, mid, 'frais', ident, 'saisie', acteur, '', {}, {'montant_ht_cents': montant, 'etat': etat})
    return dict(desk.db.execute('SELECT * FROM frais5697 WHERE id=?', (ident,)).fetchone())


def valider_frais(desk, acteur, ident):
    g.schema(desk)
    row = desk.db.execute('SELECT * FROM frais5697 WHERE id=?', (str(ident),)).fetchone()
    if not row:
        raise Stop('introuvable')
    g.dossier(desk, acteur, row['matter'])
    g.droit(desk, acteur, 'facturation')
    g.valideur(acteur)
    with g.transaction(desk):
        desk.db.execute("UPDATE frais5697 SET etat='valide',valide_par=?,valide_le=? WHERE id=? AND etat='saisi'", (g.compte(acteur), g.maintenant(), row['id']))
        g.journal(desk, row['matter'], 'frais', row['id'], 'validation', acteur)
    return dict(desk.db.execute('SELECT * FROM frais5697 WHERE id=?', (row['id'],)).fetchone())


def ajouter_jalon(desk, acteur, mid, data):
    from . import honoraires5653 as fees
    g.dossier(desk, acteur, mid)
    g.droit(desk, acteur, 'facturation')
    _schemas(desk)
    cle = g.cle_de(data.get('cle'))
    montant = fees.cents(fees.dec(data.get('montant_ht'), 'montant'))
    libelle = g.texte(data.get('libelle'), 200)
    if montant <= 0 or not libelle:
        raise Stop('jalon_invalide')
    prevue = g.jour(data.get('date_prevue'), False)
    params = g.empreinte([str(mid), montant, libelle, prevue])
    old = g.deja_fait(desk, 'jalons5697', cle, params)
    if old:
        return dict(old)
    ident = g.nouvel_id('jal')
    with g.transaction(desk):
        desk.db.execute('INSERT INTO jalons5697(id,parametres,matter,cle,libelle,montant_ht_cents,date_prevue,etat,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (ident, params, str(mid), cle, libelle, montant, prevue, 'prevu', g.maintenant(), g.maintenant()))
        g.journal(desk, mid, 'jalon', ident, 'creation', acteur, '', {}, {'montant_ht_cents': montant})
    return dict(desk.db.execute('SELECT * FROM jalons5697 WHERE id=?', (ident,)).fetchone())


def jalon_atteint(desk, acteur, ident, version):
    """Le jalon devient facturable sur décision d'un valideur (jamais par l'écoulement d'une date ni par un dépassement de temps)."""
    g.schema(desk)
    row = desk.db.execute('SELECT * FROM jalons5697 WHERE id=?', (str(ident),)).fetchone()
    if not row:
        raise Stop('introuvable')
    g.dossier(desk, acteur, row['matter'])
    g.droit(desk, acteur, 'facturation')
    g.valideur(acteur)
    g.version_attendue(row, version)
    with g.transaction(desk):
        desk.db.execute("UPDATE jalons5697 SET etat='facturable',revision=revision+1,updated=? WHERE id=? AND etat='prevu'", (g.maintenant(), row['id']))
        g.journal(desk, row['matter'], 'jalon', row['id'], 'facturable', acteur)
    return dict(desk.db.execute('SELECT * FROM jalons5697 WHERE id=?', (row['id'],)).fetchone())


def _imputes(desk, kind):
    return {r[0] for r in desk.db.execute('SELECT source_id FROM b53_allocations WHERE source_kind=?', (kind,))}


def a_facturer(desk, mid):
    """Éléments non imputés du dossier (aucun contrôle ici : appelé après la garde)."""
    from . import facturation5653
    temps = facturation5653.billable_time(desk, mid)
    pris = _imputes(desk, 'frais')
    frais = [dict(r) for r in desk.db.execute("SELECT * FROM frais5697 WHERE matter=? AND etat='valide' ORDER BY jour", (str(mid),)) if r['id'] not in pris]
    pris = _imputes(desk, 'jalon')
    jalons = [dict(r) for r in desk.db.execute("SELECT * FROM jalons5697 WHERE matter=? AND etat='facturable' ORDER BY date_prevue", (str(mid),)) if r['id'] not in pris]
    return {'temps': temps, 'frais': frais, 'jalons': jalons}


def preparer_brouillon(desk, acteur, mid, data):
    """Brouillon de facture depuis les éléments validés non imputés (sans modèle d'IA). Chaque ligne garde sa source ; la réservation
    empêche toute double facturation. Rien n'est émis ni envoyé."""
    from . import facturation5653
    g.dossier(desk, acteur, mid)
    _schemas(desk)
    a = g.acteur_de(acteur)
    elements = a_facturer(desk, mid)
    choisis = set(data.get('elements') or [])
    lignes = []
    for e in elements['temps']:
        if choisis and e['id'] not in choisis:
            continue
        lignes.append({'nature': 'honoraires', 'libelle': e['label'] or 'Diligences', 'quantite': str((Decimal(e['minutes']) / 60).quantize(CENT)), 'unite': 'heure',
                       'prix_unitaire_ht_cents': e['rate_cents'], 'periode': e['day'], 'sources': [{'type': 'temps', 'id': e['id']}]})
    for f in elements['frais']:
        if choisis and f['id'] not in choisis:
            continue
        lignes.append({'nature': 'frais' if f['nature'] == 'frais' else 'debours', 'libelle': f['libelle'], 'quantite': '1', 'unite': 'forfait',
                       'prix_unitaire_ht_cents': f['montant_ht_cents'], 'periode': f['jour'], 'sources': [{'type': 'frais', 'id': f['id']}]})
    for j in elements['jalons']:
        if choisis and j['id'] not in choisis:
            continue
        lignes.append({'nature': 'honoraires', 'libelle': j['libelle'], 'quantite': '1', 'unite': 'forfait', 'prix_unitaire_ht_cents': j['montant_ht_cents'],
                       'sources': [{'type': 'jalon', 'id': j['id']}]})
    if not lignes:
        raise Stop('rien_a_facturer')
    return facturation5653.prepare(desk, a.compte, a.role, 'facture', str(mid), {'sous_type': 'libre', 'lignes': lignes,
                                                                                  'date_prestation': str(data.get('date_prestation') or '')[:10]},
                                   str(data.get('cle') or ''))


# ------------------------------------------------------------------------------------------------ indicateurs
def _factures(desk, mids):
    """Factures et avoirs émis des dossiers visibles (registre interne), avec avoirs et règlements affectés."""
    if not mids:
        return []
    marques = ','.join('?' * len(mids))
    out = []
    for r in desk.db.execute("SELECT * FROM b53_documents WHERE kind='facture' AND number IS NOT NULL AND matter IN (%s)" % marques, tuple(mids)):
        snap = json.loads(r['snapshot_json'])
        t = snap['totaux']
        avoirs_ht = avoirs_ttc = 0
        for a in desk.db.execute("SELECT snapshot_json FROM b53_documents WHERE kind='avoir' AND origin=? AND number IS NOT NULL", (r['id'],)):
            ta = json.loads(a['snapshot_json'])['totaux']
            avoirs_ht, avoirs_ttc = avoirs_ht + int(ta['ht_cents']), avoirs_ttc + int(ta['ttc_cents'])
        regle = int(desk.db.execute('SELECT COALESCE(SUM(amount_cents),0) FROM b53_payments WHERE document=? AND verified=1', (r['id'],)).fetchone()[0])
        out.append({'source': 'interne', 'id': r['id'], 'matter': r['matter'], 'numero': r['number'], 'emise_le': r['issue_date'],
                    'echeance': str((snap.get('dates') or {}).get('echeance') or r['issue_date'] or ''), 'ht_cents': int(t['ht_cents']) - avoirs_ht,
                    'ttc_cents': int(t['net_a_payer_cents']) - avoirs_ttc, 'regle_cents': regle, 'devise': 'EUR',
                    'solde_cents': int(t['net_a_payer_cents']) - avoirs_ttc - regle})
    return out


def _factures_externes(desk, mids):
    if not mids:
        return []
    marques = ','.join('?' * len(mids))
    return [{'source': r['source'], 'id': r['id_externe'], 'matter': r['matter'], 'numero': r['numero'], 'emise_le': r['jour'], 'echeance': r['echeance'],
             'ht_cents': None, 'ttc_cents': r['montant_cents'], 'regle_cents': r['montant_cents'] - r['solde_cents'], 'devise': r['devise'], 'solde_cents': r['solde_cents']}
            for r in desk.db.execute("SELECT * FROM fin_externes5697 WHERE objet='facture' AND document='' AND matter IN (%s)" % marques, tuple(mids))]


def _visibles(desk, acteur, mid=None):
    """Dossiers retenus : garde commune AVANT tout total. Un dossier désigné et non visible : refus neutre."""
    from .common import load_matters
    if mid:
        g.dossier(desk, acteur, mid)
        return [str(mid)]
    perimetre = g.perimetre(desk, acteur)
    return [str(m['id']) for m in load_matters(desk.c, unfiltered=True) if str(m['id']) in perimetre]


def indicateurs(desk, acteur, mid=None, debut='', fin='', aujourdhui=None):
    """Les quatre indicateurs, leur définition, la date de calcul et l'état de la source. Une source en panne ne donne pas un faux zéro."""
    _voir(desk, acteur)
    _schemas(desk)
    mids = _visibles(desk, acteur, mid)
    today = aujourdhui or _aujourdhui(desk)
    debut = g.jour(debut, False) or today.replace(day=1).isoformat()
    fin = g.jour(fin, False) or today.isoformat()
    # Travail à facturer (HT : aucune TVA n'est calculée avant la facture)
    travail = {'temps_cents': 0, 'frais_cents': 0, 'jalons_cents': 0, 'elements': 0}
    for m in mids:
        e = a_facturer(desk, m)
        travail['temps_cents'] += sum(int(x['amount_cents']) for x in e['temps'])
        travail['frais_cents'] += sum(int(x['montant_ht_cents']) for x in e['frais'])
        travail['jalons_cents'] += sum(int(x['montant_ht_cents']) for x in e['jalons'])
        travail['elements'] += len(e['temps']) + len(e['frais']) + len(e['jalons'])
    total_travail = travail['temps_cents'] + travail['frais_cents'] + travail['jalons_cents']
    factures = _factures(desk, mids) + _factures_externes(desk, mids)
    ouvertes = [f for f in factures if f['solde_cents'] > 0]
    echues = [f for f in ouvertes if f['echeance'] and f['echeance'] < today.isoformat()]
    trop_percu = [f for f in factures if f['solde_cents'] < 0]
    # Encaissé : règlements affectés de la période ; remboursements ; paiements externes non rapprochés à part
    encaisse, remboursements, non_rapproches = 0, [], []
    if mids:
        marques = ','.join('?' * len(mids))
        encaisse = int(desk.db.execute('SELECT COALESCE(SUM(p.amount_cents),0) FROM b53_payments p JOIN b53_documents d ON d.id=p.document WHERE p.verified=1 AND p.day>=? AND p.day<=? '
                                       'AND d.matter IN (%s)' % marques, (debut, fin, *mids)).fetchone()[0])
        for r in desk.db.execute("SELECT * FROM fin_externes5697 WHERE objet='reglement' AND jour>=? AND jour<=? AND matter IN (%s)" % marques, (debut, fin, *mids)):
            if r['montant_cents'] < 0:
                remboursements.append({'source': r['source'], 'id': r['id_externe'], 'montant': str(euros(r['montant_cents'])), 'jour': r['jour']})
            elif json.loads(r['liens'] or '[]'):
                encaisse += r['montant_cents']
            else:
                non_rapproches.append({'source': r['source'], 'id': r['id_externe'], 'montant': str(euros(r['montant_cents'])), 'jour': r['jour']})
        for r in desk.db.execute("SELECT * FROM fin_externes5697 WHERE objet='reglement' AND matter='' AND jour>=? AND jour<=?", (debut, fin)):
            non_rapproches.append({'source': r['source'], 'id': r['id_externe'], 'montant': str(euros(r['montant_cents'])), 'jour': r['jour'], 'dossier': 'non identifié'})
    total_remb = sum(int(Decimal(x['montant']) * 100) for x in remboursements)
    etat = etat_sources(desk)
    return {
        'calcule_le': g.maintenant(), 'periode': {'debut': debut, 'fin': fin}, 'devise': 'EUR', 'sources': etat,
        'travail_a_facturer': {'libelle': LIBELLES['travail_a_facturer'], 'definition': DEFINITIONS['travail_a_facturer'], 'ht': _montant(total_travail),
                               'detail': {k: (str(euros(v)) if k.endswith('_cents') else v) for k, v in travail.items()}},
        'facture_non_regle': {'libelle': LIBELLES['facture_non_regle'], 'definition': DEFINITIONS['facture_non_regle'],
                              'ttc': _montant(sum(f['solde_cents'] for f in ouvertes)),
                              'factures': [{k: f[k] for k in ('source', 'id', 'matter', 'numero', 'echeance')} | {'solde': str(euros(f['solde_cents']))} for f in ouvertes]},
        'impayes_echus': {'libelle': LIBELLES['impayes_echus'], 'definition': DEFINITIONS['impayes_echus'], 'ttc': _montant(sum(f['solde_cents'] for f in echues)),
                          'sous_total_de': 'facture_non_regle', 'factures': [f['numero'] for f in echues]},
        'encaisse': {'libelle': LIBELLES['encaisse'], 'definition': DEFINITIONS['encaisse'], 'ttc': _montant(encaisse), 'remboursements': remboursements,
                     'remboursements_total': _montant(total_remb), 'non_rapproches': non_rapproches},
        'anomalies': ['Facture %s : réglée au-delà du montant dû (%s).' % (f['numero'], str(euros(-f['solde_cents']))) for f in trop_percu] + etat['anomalies']}


def solde_facture(desk, acteur, doc_id):
    """Reste dû d'une facture interne : net à payer moins avoirs émis moins règlements affectés."""
    from . import facturation5653
    _voir(desk, acteur)
    d = facturation5653.get(desk, doc_id)
    g.dossier(desk, acteur, d['matter'])
    f = next(x for x in _factures(desk, [d['matter']]) if x['id'] == d['id'])
    return {'facture': d['id'], 'numero': d['number'], 'solde': _montant(f['solde_cents']), 'regle': _montant(f['regle_cents']), 'echeance': f['echeance']}


def forfait(desk, acteur, mid):
    """Forfait : heures saisies et validées, budget interne, reste contractuel à facturer, prochain jalon. Rien n'est déduit du dépassement."""
    from . import facturation5653, time500
    _voir(desk, acteur)
    g.dossier(desk, acteur, mid)
    _schemas(desk)
    t = time500.summary(desk, str(mid))
    conv = facturation5653._active_convention(desk, str(mid))   # noqa: SLF001 — convention applicable du registre
    facture_ht = sum(f['ht_cents'] for f in _factures(desk, [str(mid)]))
    contractuel = conv['snapshot']['totaux']['ht_cents'] if conv and conv['subtype'] != 'temps' else None
    prochain = desk.db.execute("SELECT id,libelle,montant_ht_cents,date_prevue,etat FROM jalons5697 WHERE matter=? AND etat IN ('prevu','facturable') "
                               "ORDER BY CASE WHEN date_prevue='' THEN 1 ELSE 0 END, date_prevue LIMIT 1", (str(mid),)).fetchone()
    budget = t['budget_cents']
    return {'heures_validees': str((Decimal(t['minutes']) / 60).quantize(CENT)), 'valeur_temps_validee': _montant(t['amount_cents']),
            'heures_en_attente': str((Decimal(t['pending_minutes']) / 60).quantize(CENT)), 'budget_interne': _montant(budget) if budget else None,
            'depassement_budget': _montant(max(0, t['amount_cents'] - budget)) if budget else None,
            'reste_contractuel_ht': _montant(max(0, contractuel - facture_ht)) if contractuel is not None else None,
            'prochain_jalon': dict(prochain) if prochain else None,
            'mention': 'Le temps passé au-delà du forfait n’est pas un supplément : seul un avenant ou une décision du cabinet peut le facturer.'}


# ------------------------------------------------------------------------------------------------ synchronisation (source externe)
def etat_sources(desk, entite='cabinet'):
    """Dernière synchronisation, fraîcheur et anomalies de la source de référence. AxiorHub : toujours à jour (registre local)."""
    g.schema(desk)
    source = source_de(desk, entite)
    if source == 'interne':
        return {'source': 'interne', 'libelle': SOURCES['interne'], 'verifiable': True, 'derniere_synchro': None, 'perime': False, 'anomalies': []}
    row = desk.db.execute('SELECT * FROM fin_synchros5697 WHERE source=? AND entite=? ORDER BY debut DESC LIMIT 1', (source, entite)).fetchone()
    reussie = desk.db.execute("SELECT * FROM fin_synchros5697 WHERE source=? AND entite=? AND etat='reussie' ORDER BY debut DESC LIMIT 1", (source, entite)).fetchone()
    perime = not reussie or datetime.fromisoformat(reussie['fin']) < datetime.now(timezone.utc) - timedelta(hours=PERIME_HEURES)
    anomalies = json.loads(reussie['anomalies']) if reussie else []
    if row and row['etat'] != 'reussie':
        anomalies = anomalies + ['Dernière synchronisation en échec : %s.' % (row['erreur'] or 'erreur inconnue')]
    if perime:
        anomalies = anomalies + ['Solde périmé : aucune synchronisation réussie depuis %d heures.' % PERIME_HEURES]
    actif = reglages(desk).get(source, {}).get('actif', False)
    if not actif:
        anomalies = anomalies + ['Source %s désactivée : les soldes ne sont pas vérifiés.' % SOURCES.get(source, source)]
    return {'source': source, 'libelle': SOURCES.get(source, source), 'verifiable': bool(actif and not perime and row and row['etat'] == 'reussie' and not anomalies),
            'derniere_synchro': reussie['fin'] if reussie else None, 'perime': perime, 'anomalies': anomalies}


def motif_suspension_relance(desk, matter=''):
    """Motif de suspendre les relances (solde non vérifiable) ou ''. Appelé par ``relances5636`` avant de préparer et de déposer."""
    etat = etat_sources(desk)
    if etat['source'] == 'interne' or not reglages(desk).get(etat['source'], {}).get('actif'):
        return ''          # registre local, ou projection non activée : les relances gardent leur propre relecture avant dépôt
    if etat['verifiable']:
        return ''
    return 'solde non vérifiable (%s) : relances suspendues' % '; '.join(etat['anomalies'])[:200]


def _client_vers_dossier(desk):
    try:
        return {r['client_id']: r['matter'] for r in desk.db.execute('SELECT matter,client_id FROM matter_invoice_links_v310')}
    except Exception:   # noqa: BLE001 — liaison des clients jamais initialisée
        return {}


def synchroniser(desk, acteur, transport, entite='cabinet'):
    """Projection des factures, avoirs et règlements de l'outil de référence. Clé (source, entité, objet, identifiant externe) : un
    rejeu met la même ligne à jour, sans doublon. Une panne laisse la projection précédente, marquée périmée et en échec."""
    from . import honoraires5653 as fees
    g.droit(desk, acteur, 'facturation')
    _schemas(desk)
    source = source_de(desk, entite)
    if source == 'interne':
        raise Stop('source_interne_sans_synchronisation')
    if not reglages(desk).get(source, {}).get('actif'):
        raise Stop('source_desactivee')
    sid, debut = g.nouvel_id('syn'), g.maintenant()
    desk.db.execute("INSERT INTO fin_synchros5697(id,source,entite,debut,etat) VALUES(?,?,?,?,'en_cours')", (sid, source, entite, debut))
    desk.db.commit()
    try:
        factures, reglements = transport.factures(), transport.reglements()
    except Stop as ex:
        desk.db.execute("UPDATE fin_synchros5697 SET etat='echec',fin=?,erreur=? WHERE id=?", (g.maintenant(), str(ex).split(':')[0][:120], sid))
        desk.db.commit()
        return etat_sources(desk, entite)
    dossiers = _client_vers_dossier(desk)
    anomalies, compte = [], 0
    with g.transaction(desk):
        for f in factures:
            objet = 'avoir' if f.get('type') == 'avoir' else 'facture'
            montant, solde = fees.cents(fees.dec(f.get('amount', 0), 'montant', minimum=None)), fees.cents(fees.dec(f.get('balance', 0), 'solde', minimum=None))
            ligne = {'matter': dossiers.get(str(f.get('client_id') or ''), ''), 'numero': str(f.get('number') or '')[:60], 'montant_cents': montant, 'solde_cents': solde,
                     'devise': str(f.get('currency') or 'EUR')[:3], 'jour': str(f.get('date') or '')[:10], 'echeance': str(f.get('due_date') or '')[:10],
                     'etat': str(f.get('status') or '')[:40], 'document': str(f.get('axiorhub_id') or '')[:80]}
            if not ligne['matter']:
                anomalies.append('Facture %s : client non relié à un dossier.' % ligne['numero'])
            compte += _projeter(desk, source, entite, objet, str(f['id']), ligne, [])
        for p in reglements:
            liens = [{'facture': str(x.get('invoice_id')), 'montant': str(x.get('amount'))} for x in p.get('invoices') or []]
            montant = fees.cents(fees.dec(p.get('amount', 0), 'montant', minimum=None))
            if p.get('refunded'):
                montant = -fees.cents(fees.dec(p.get('refunded'), 'montant'))
            matter = ''
            for x in liens:
                r = desk.db.execute("SELECT matter FROM fin_externes5697 WHERE source=? AND entite=? AND objet='facture' AND id_externe=?", (source, entite, x['facture'])).fetchone()
                matter = matter or (r['matter'] if r else '')
            ligne = {'matter': matter, 'numero': str(p.get('number') or '')[:60], 'montant_cents': montant, 'solde_cents': 0, 'devise': str(p.get('currency') or 'EUR')[:3],
                     'jour': str(p.get('date') or '')[:10], 'echeance': '', 'etat': 'rembourse' if p.get('refunded') else 'recu', 'document': ''}
            compte += _projeter(desk, source, entite, 'reglement', str(p['id']), ligne, liens)
        desk.db.execute("UPDATE fin_synchros5697 SET etat='reussie',fin=?,compte=?,anomalies=? WHERE id=?", (g.maintenant(), compte, json.dumps(anomalies, ensure_ascii=False), sid))
    return etat_sources(desk, entite) | {'lignes_modifiees': compte}


def _projeter(desk, source, entite, objet, ident, ligne, liens):
    empreinte = g.empreinte([ligne, liens])
    old = desk.db.execute('SELECT empreinte FROM fin_externes5697 WHERE source=? AND entite=? AND objet=? AND id_externe=?', (source, entite, objet, ident)).fetchone()
    if old and old['empreinte'] == empreinte:
        desk.db.execute('UPDATE fin_externes5697 SET vu_le=? WHERE source=? AND entite=? AND objet=? AND id_externe=?', (g.maintenant(), source, entite, objet, ident))
        return 0
    desk.db.execute('INSERT INTO fin_externes5697(source,entite,objet,id_externe,matter,document,numero,montant_cents,solde_cents,devise,jour,echeance,etat,liens,empreinte,vu_le) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,entite,objet,id_externe) DO UPDATE SET matter=excluded.matter,document=excluded.document,'
                    'numero=excluded.numero,montant_cents=excluded.montant_cents,solde_cents=excluded.solde_cents,devise=excluded.devise,jour=excluded.jour,echeance=excluded.echeance,'
                    'etat=excluded.etat,liens=excluded.liens,empreinte=excluded.empreinte,vu_le=excluded.vu_le',
                    (source, entite, objet, ident, ligne['matter'], ligne['document'], ligne['numero'], ligne['montant_cents'], ligne['solde_cents'], ligne['devise'],
                     ligne['jour'], ligne['echeance'], ligne['etat'], json.dumps(liens, ensure_ascii=False), empreinte, g.maintenant()))
    return 1


# ------------------------------------------------------------------------------------------------ émission externe (outbox)
def emettre_externe(desk, acteur, doc_id, cle, transport, verifie=False):
    """Demande d'émission chez l'outil de référence : inscrite (clé stable) avant l'appel, effet après validation, constat ensuite.
    Réponse perdue : la demande est « inconnue » ; le rejeu relit d'abord la facture chez l'outil (par la clé) et ne recrée rien si elle existe.
    L'émission n'envoie rien au client : l'envoi et la relance sont des actions distinctes."""
    from . import facturation5653, pause5629
    d = facturation5653.get(desk, doc_id)
    g.dossier(desk, acteur, d['matter'])
    g.droit(desk, acteur, 'facturation')
    g.valideur(acteur)
    _schemas(desk)
    cle = g.cle_de(cle)
    source = source_de(desk, d['entity'])
    if source == 'interne':
        raise Stop('emission_par_axiorhub')                 # la source interne émet par facturation5653.issue
    if not reglages(desk).get(source, {}).get('actif'):
        raise Stop('source_desactivee')
    if d['state'] != 'approuve':
        raise Stop('approbation_requise_ou_perimee')
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        row = desk.db.execute('SELECT * FROM fin_demandes5697 WHERE cle=?', (cle,)).fetchone()
        if row and row['document'] != d['id']:
            raise Stop('cle_idempotence_deja_utilisee')
        if not row:
            ouverte = desk.db.execute("SELECT cle FROM fin_demandes5697 WHERE document=? AND nature='emission' AND etat IN ('reservee','inconnue','confirmee')", (d['id'],)).fetchone()
            if ouverte:
                raise Stop('emission_deja_demandee')
            desk.db.execute("INSERT INTO fin_demandes5697(cle,nature,document,matter,source,entite,etat,acteur,created,updated) VALUES(?,?,?,?,?,?,'reservee',?,?,?)",
                            (cle, 'emission', d['id'], d['matter'], source, d['entity'], g.compte(acteur), g.maintenant(), g.maintenant()))
            g.journal(desk, d['matter'], 'facture', d['id'], 'emission_demandee', acteur, '', {}, {'cle': cle, 'source': source})
        desk.db.commit()
    except BaseException:
        desk.db.rollback()
        raise
    row = desk.db.execute('SELECT * FROM fin_demandes5697 WHERE cle=?', (cle,)).fetchone()
    if row['etat'] == 'confirmee':
        return dict(row)
    if row['etat'] == 'inconnue' or row['tentatives']:
        try:
            trouvee = transport.chercher_facture(cle)       # relire avant tout rejeu
        except Stop:
            return dict(row) | {'message': 'L’outil ne répond pas : la facture n’a pas pu être relue ; rien n’est renvoyé.'}
        if trouvee:
            return _constater(desk, row, trouvee, acteur)
        if not verifie:
            raise Stop('verification_requise_avant_rejeu')
    pause5629.checkpoint(desk)
    desk.db.execute('UPDATE fin_demandes5697 SET tentatives=tentatives+1,updated=? WHERE cle=?', (g.maintenant(), cle))
    desk.db.commit()
    t = d['snapshot']['totaux']
    try:
        resultat = transport.creer_facture({'cle': cle, 'document': d['id'], 'matter': d['matter'], 'lignes': d['snapshot']['lignes'], 'total_ttc': str(euros(t['ttc_cents']))})
    except Stop as ex:
        etat = 'inconnue' if str(ex).split(':')[0] in ('delai_http_depasse', 'connexion_http_indisponible', 'reponse_inconnue') else 'echec'
        desk.db.execute('UPDATE fin_demandes5697 SET etat=?,reponse=?,updated=? WHERE cle=?', (etat, str(ex).split(':')[0][:200], g.maintenant(), cle))
        desk.db.commit()
        return dict(desk.db.execute('SELECT * FROM fin_demandes5697 WHERE cle=?', (cle,)).fetchone())
    return _constater(desk, row, resultat, acteur)


def _constater(desk, row, resultat, acteur):
    with g.transaction(desk):
        desk.db.execute("UPDATE fin_demandes5697 SET etat='confirmee',id_externe=?,reponse=?,updated=? WHERE cle=?",
                        (str(resultat.get('id') or '')[:80], json.dumps({'numero': resultat.get('number', '')}, ensure_ascii=False), g.maintenant(), row['cle']))
        g.journal(desk, row['matter'], 'facture', row['document'], 'emission_constatee', acteur, '', {}, {'id_externe': resultat.get('id'), 'numero': resultat.get('number')})
    return dict(desk.db.execute('SELECT * FROM fin_demandes5697 WHERE cle=?', (row['cle'],)).fetchone())


class TransportInvoiceNinja:
    """Lecture seule d'Invoice Ninja par le client existant (``facturation569``) : factures, règlements, essai des droits. L'émission externe
    n'est pas qualifiée ici (réservée aux écritures validées de ``facturation569``) : elle est refusée, jamais simulée."""

    def __init__(self, desk, http=None):
        from . import facturation569
        self.client = facturation569._client(desk, http)   # noqa: SLF001 — adresse et jeton lus dans la configuration et le coffre

    def _liste(self, chemin):
        out = self.client.json('GET', chemin)
        return out.get('data', []) if isinstance(out, dict) else []

    def essai(self):
        self._liste('/api/v1/invoices?per_page=1')
        return {'ok': True, 'lecture': True, 'ecriture': False}

    def factures(self):
        return [{**f, 'type': 'avoir' if str(f.get('status_id')) == '-1' else 'facture'} for f in self._liste('/api/v1/invoices?per_page=500&is_deleted=false')]

    def reglements(self):
        return [{'id': p['id'], 'amount': p.get('amount', 0), 'refunded': p.get('refunded') or 0, 'date': p.get('date', ''), 'number': p.get('number', ''),
                 'invoices': [{'invoice_id': x.get('invoice_id') or x.get('paymentable_id'), 'amount': x.get('amount')} for x in p.get('paymentables') or p.get('invoices') or []]}
                for p in self._liste('/api/v1/payments?per_page=500')]

    def chercher_facture(self, cle):
        raise Stop('emission_externe_non_qualifiee')

    def creer_facture(self, donnees):
        raise Stop('emission_externe_non_qualifiee')

