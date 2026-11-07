"""5.6.95 (lot routage, G01 à G05) : routage mesuré. Extension du service de routage ``routage5682`` ; ce n'est pas un second service.

G01 — Mesures d'usage réelles, sans texte. Par (fournisseur, modèle, type de tâche) et par jour : appels, réussites, sorties invalides ou
incomplètes (schéma, ``generation_ia_incomplete``…), escalades, relais, corrections demandées après le contrôle de rédaction des brouillons
(lot rédaction), délais (histogramme borné : médiane et 95e centile à ±22 % près) et coût réglé au registre de budget. Sources : la table
``decisions_5682`` (une ligne par appel, déjà tenue, 180 jours) et ``budget_reservations5689`` (rapprochée par ``call_id`` = uid de la
décision). Les spans ``traces5694_spans`` ne sont pas relus : ils ne sont gardés que 7 jours (30 au plus) et ne portent pas l'identifiant
de la décision ; ``decisions_5682`` donne les mêmes faits (fournisseur, modèle, étape, issue, code, début et fin, escalade, relais) sur
toute la fenêtre. Aucune donnée nouvelle n'est collectée sur le contenu : la lecture ne porte que sur des codes, des nombres et des
identifiants de modèles (jamais ``raison_courte``, ``dossier_h`` ni ``proprietaire``).

Calcul incrémental et borné : un filigrane (dernier identifiant de décision lu) ; au plus ``LOT`` décisions par mise à jour ; au plus une
mise à jour toutes les ``RAFRAICHISSEMENT`` secondes (15 min), déclenchée par une décision de routage ou par la page, jamais par un travail
de fond. Une décision encore ouverte attend sa clôture (une heure au plus, puis elle compte « sans issue »). Une base occupée fait perdre
une mise à jour, jamais une décision. Rétention : celle de la source (180 jours).

G02 — Qualité observée. ``score`` combine le banc du cabinet (``routage5682._mesures``, trois essais au moins) et l'usage réel quand
l'échantillon atteint ``SEUIL_ECHANTILLON`` (20 appels sur 30 jours). Pourquoi 20 : l'écart type d'une proportion voisine de 90 % y est
d'environ 7 points, assez pour séparer 96 % de 61 %, pas des écarts fins ; le poids de l'usage croît ensuite avec l'échantillon
(``POIDS_USAGE`` : 0,5 à 20 appels, 0,8 à partir de 100, linéaire entre les deux ; le banc garde le reste). Sans banc, l'usage seul ; sans
usage suffisant, le banc seul (comportement actuel). ``evaluer`` ne classe QUE les candidats que ``routage5682.decide`` a déjà retenus après
toute la politique (mode, destination admise, dossier réservé, pause, horaires, budget) : il ne peut rendre qu'un élément de cette liste.
Règle de choix : le classement actuel (niveau, canal, coût estimé) est conservé ; un candidat mesuré sous le seuil de qualité du cabinet
(``data_policy5689.qualite_minimale``) ou à plus de ``ECART_TOLERE`` points du meilleur candidat mesuré de même niveau n'est plus
éligible ; un candidat non mesuré ne passe pas devant un candidat mesuré (règle de la 5.6.89). Aucun candidat éligible : choix habituel,
marqué provisoire.

G03 — Réglage « Routage mesuré » (``REGLAGE``) : ``conseil`` par défaut (la décision reste celle d'aujourd'hui ; le choix mesuré et sa
raison sont enregistrés à côté, colonnes ``mesure_*`` de ``decisions_5682``), ``actif`` sur décision de l'administrateur.

G04 — Section Paramètres › Modèles d'IA › Routage mesuré (``section_html``) et export CSV (``export_csv``) ; textes dans les 4 langues.

G05 — Mesure avant publication : le rapport JSON de ``scripts/evaluation-redaction.py`` (lot évaluation), copié par l'administrateur dans
``<état>/evaluation-redaction/rapport.json``, sert de banc pour la rédaction des courriels du modèle local évalué, seulement dans le score
mesuré (le banc de la 5.6.82, qui filtre les décisions, n'est pas modifié). Le script n'est ni modifié ni lancé par le serveur.

Aucun appel au modèle n'est fait pour mesurer.
"""
from datetime import datetime, timedelta, timezone
import csv
from html import escape as e
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import time

from .common import Stop

REGLAGE = 'routage5695:mode'
MODES = ('conseil', 'actif')
RAFRAICHISSEMENT = 900          # secondes au plus entre deux mises à jour des agrégats
LOT = 20000                     # décisions lues au plus par mise à jour (mesuré : voir le compte rendu du lot)
FENETRE = 30                    # jours glissants retenus pour la décision
PERIODES = (7, 30, 90)          # filtres de la page
RETENTION = 180                 # jours : celle de ``decisions_5682`` (``routage5682.KEEP_DAYS``)
OUVERTE = 3600                  # secondes : une décision non close au-delà compte « sans issue »
SEUIL_ECHANTILLON = 20
POIDS_USAGE = (0.5, 0.8, 100)   # poids de l'usage à SEUIL_ECHANTILLON appels, poids maximal, appels où il est atteint
ECART_TOLERE = 15               # points
CACHE_MESURES = 60              # secondes
CACHE_REGLAGE = 30              # secondes
BORNES_MS = tuple(int(250 * 1.5 ** i) for i in range(20))   # 250 ms à 554 s ; au-delà, dernière case
MARQUE_CORRECTION = 'correction_redaction'                   # raison posée par ``routage5682.decide`` (code, aucun texte)
EVALUATION = ('evaluation-redaction', 'rapport.json')
_MODELE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/+-]{0,119}')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS routage5695_agregats(
  jour TEXT NOT NULL, fournisseur TEXT NOT NULL, modele TEXT NOT NULL, type_tache TEXT NOT NULL,
  appels INTEGER NOT NULL DEFAULT 0, terminees INTEGER NOT NULL DEFAULT 0, invalides INTEGER NOT NULL DEFAULT 0, escalades INTEGER NOT NULL DEFAULT 0,
  relais INTEGER NOT NULL DEFAULT 0, sans_issue INTEGER NOT NULL DEFAULT 0, arrets INTEGER NOT NULL DEFAULT 0, brouillons INTEGER NOT NULL DEFAULT 0,
  corrections INTEGER NOT NULL DEFAULT 0, cout_usd_micro INTEGER NOT NULL DEFAULT 0, cout_credits_micro INTEGER NOT NULL DEFAULT 0,
  cout_connus INTEGER NOT NULL DEFAULT 0, locaux INTEGER NOT NULL DEFAULT 0, durees TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY(jour, fournisseur, modele, type_tache));
CREATE TABLE IF NOT EXISTS routage5695_choix(
  jour TEXT NOT NULL, type_tache TEXT NOT NULL, actuel TEXT NOT NULL, mesure TEXT NOT NULL, mode TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(jour, type_tache, actuel, mesure, mode));
CREATE TABLE IF NOT EXISTS routage5695_meta(cle TEXT PRIMARY KEY, valeur TEXT NOT NULL);
'''
COMPTEURS = ('appels', 'terminees', 'invalides', 'escalades', 'relais', 'sans_issue', 'arrets', 'brouillons', 'corrections',
             'cout_usd_micro', 'cout_credits_micro', 'cout_connus', 'locaux')
LIBELLES_FOURNISSEURS = {'ollama': 'Le modèle local', 'chatgpt': 'ChatGPT', 'mistral': 'Mistral', 'anthropic': 'Claude', 'openrouter': 'OpenRouter', 'openai': 'OpenAI'}

_verrou = threading.Lock()
_derniers = {}          # base → instant (monotone) de la dernière mise à jour tentée par ce processus
_cache_mesures = {}     # (base, jours) → (échéance, mesures)
_cache_reglage = {}     # base → (échéance, mode)
_cache_evaluation = {}  # base → (mtime, résultat)


def _maintenant():
    return datetime.now(timezone.utc)


def _base(state_dir):
    return Path(state_dir) / 'desk.sqlite3'


def _connect(state_dir, attente=2):
    """Connexion à la base du cabinet ; tables de ce lot et colonnes de ``decisions_5682`` assurées (migration idempotente)."""
    from .routage5682 import _connect as connect5682
    connect5682(state_dir).close()          # table des décisions et colonnes ``mesure_*`` (migration de routage5682)
    db = sqlite3.connect(_base(state_dir), timeout=attente)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def _ms(debut, fin):
    try:
        a = datetime.fromisoformat(str(debut))
        b = datetime.fromisoformat(str(fin))
    except (TypeError, ValueError):
        return None
    return max(0, int((b - a).total_seconds() * 1000))


def _case(ms):
    for i, borne in enumerate(BORNES_MS):
        if ms <= borne:
            return i
    return len(BORNES_MS)


def _centile(histogramme, p):
    """Valeur représentative (moyenne géométrique des bornes de la case) du centile ``p`` ; None sans durée."""
    n = sum(histogramme)
    if not n:
        return None
    rang = max(1, math.ceil(p / 100 * n))
    cumul = 0
    for i, c in enumerate(histogramme):
        cumul += c
        if cumul >= rang:
            if i == 0:
                return int(BORNES_MS[0] / math.sqrt(1.5))
            if i >= len(BORNES_MS):
                return int(BORNES_MS[-1] * math.sqrt(1.5))
            return int(math.sqrt(BORNES_MS[i - 1] * BORNES_MS[i]))
    return None


def _arrets():
    """Codes qui ne disent rien du modèle (arrêt demandé, pause, horaires, plafond de budget) : comptés à part, hors des taux."""
    from .arret5643 import STOP_CODES
    from .budget_ledger5689 import ATTENTE_CODES
    from .sobriete5682 import CODE
    return frozenset(STOP_CODES) | frozenset(ATTENTE_CODES) | {CODE}


def _table_existe(db, nom):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nom,)).fetchone() is not None


def _meta(db, cle, defaut=None):
    row = db.execute('SELECT valeur FROM routage5695_meta WHERE cle=?', (cle,)).fetchone()
    return row[0] if row else defaut


# ---------------------------------------------------------------------------------------------------- G01 : agrégats
def rafraichir(state_dir, force=False, maintenant=None, cabinet=''):
    """Met à jour les agrégats depuis le filigrane. Rend le nombre de décisions lues, ou None si rien n'a été tenté (moins de 15 min depuis
    la dernière mise à jour, base occupée ou illisible). Jamais d'appel au modèle ; jamais de texte lu."""
    if not state_dir or not _base(state_dir).is_file():
        return None
    cle = str(_base(state_dir))
    instant = time.monotonic()
    with _verrou:
        dernier = _derniers.get(cle)
        if not force and dernier is not None and instant - dernier < RAFRAICHISSEMENT:
            return None
        _derniers[cle] = instant
    maintenant = maintenant or _maintenant()
    try:
        db = _connect(state_dir)
    except sqlite3.Error:
        return None
    try:
        db.execute('BEGIN IMMEDIATE')
        try:
            rafraichi = _meta(db, 'rafraichi')
            if not force and rafraichi and rafraichi > (maintenant - timedelta(seconds=RAFRAICHISSEMENT)).isoformat():
                db.rollback()            # un autre processus vient de le faire
                return None
            lus, plein = _lire(db, maintenant, cabinet)
            db.execute('INSERT OR REPLACE INTO routage5695_meta VALUES(?,?)', ('rafraichi', (maintenant - timedelta(seconds=RAFRAICHISSEMENT + 1)).isoformat()
                                                                                    if plein else maintenant.isoformat()))
            limite = (maintenant - timedelta(days=RETENTION)).date().isoformat()
            db.execute('DELETE FROM routage5695_agregats WHERE jour<?', (limite,))
            db.execute('DELETE FROM routage5695_choix WHERE jour<?', (limite,))
            db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
    except sqlite3.Error:
        return None
    finally:
        db.close()
    with _verrou:
        for k in [k for k in _cache_mesures if k[0] == cle]:
            del _cache_mesures[k]
        if plein:
            _derniers.pop(cle, None)       # retard à rattraper : la mise à jour suivante n'attend pas 15 min
    return lus


def _filigrane(db, maintenant):
    valeur = _meta(db, 'filigrane')
    if valeur is not None:
        return int(valeur)
    depuis = (maintenant - timedelta(days=max(PERIODES))).isoformat()
    row = db.execute('SELECT MIN(id) FROM decisions_5682 WHERE at>=?', (depuis,)).fetchone()     # une seule fois, à la première mise à jour
    if row and row[0] is not None:
        return int(row[0]) - 1
    row = db.execute('SELECT MAX(id) FROM decisions_5682').fetchone()
    return int(row[0] or 0)


def _lire(db, maintenant, cabinet):
    """Corps de la mise à jour, dans la transaction : lit au plus ``LOT`` décisions après le filigrane, agrège, avance le filigrane."""
    filigrane = _filigrane(db, maintenant)
    budget = _table_existe(db, 'budget_reservations5689')
    champs = ('d.id,d.uid,d.at,d.fini,d.mission,d.etape,d.type_tache,d.fournisseur,d.modele,d.etat,d.code,d.escalade_de,d.mode,d.raisons,d.unite,'
              'd.mesure_mode,d.mesure_choix')
    if budget:
        lignes = db.execute('SELECT ' + champs + ',b.state AS b_etat,b.currency AS b_devise,b.retained_micro AS b_retenu,b.actual_micro AS b_reel '
                            'FROM decisions_5682 d LEFT JOIN budget_reservations5689 b ON b.cabinet_id IN (?,?) AND b.call_id=d.uid '
                            'WHERE d.id>? ORDER BY d.id LIMIT ?', ('cabinet', cabinet or 'cabinet', filigrane, LOT)).fetchall()
    else:
        lignes = db.execute('SELECT ' + champs + ' FROM decisions_5682 d WHERE d.id>? ORDER BY d.id LIMIT ?', (filigrane, LOT)).fetchall()
    from .routage5682 import ESCALATE_ON, FALLBACK_ON
    arrets = _arrets()
    ouverte = (maintenant - timedelta(seconds=OUVERTE)).isoformat()
    sommes, choix, vus = {}, {}, set()

    def compteur(jour, f, m, t):
        return sommes.setdefault((jour, f, m, t), {**{c: 0 for c in COMPTEURS}, 'durees': [0] * (len(BORNES_MS) + 1)})
    fin = filigrane
    for r in lignes:
        if r['id'] in vus:
            continue
        if r['etat'] == 'decidee' and str(r['at']) > ouverte:
            break                        # appel en cours : il sera compté à sa clôture
        vus.add(r['id'])
        fin = r['id']
        jour = str(r['at'])[:10]
        c = compteur(jour, r['fournisseur'], r['modele'], r['type_tache'])
        code = str(r['code'] or '').split(':')[0]
        try:
            raisons = json.loads(r['raisons'] or '[]')
        except ValueError:
            raisons = []
        correction = MARQUE_CORRECTION in raisons
        if r['etat'] == 'erreur' and code in arrets:
            c['arrets'] += 1
        else:
            c['appels'] += 1
            if r['etat'] == 'terminee':
                c['terminees'] += 1
                if r['type_tache'] == 'redaction_mail' and not correction:
                    c['brouillons'] += 1
            elif r['etat'] == 'erreur':
                if code in ESCALATE_ON:
                    c['invalides'] += 1
                elif code in FALLBACK_ON and r['mode'] == 'auto':
                    c['relais'] += 1
            else:
                c['sans_issue'] += 1
            duree = _ms(r['at'], r['fini']) if r['fini'] else None
            if duree is not None:
                c['durees'][_case(duree)] += 1
            if r['unite'] == 'aucune':
                c['locaux'] += 1
            elif budget and r['b_etat'] == 'settled':
                montant = r['b_retenu'] if r['b_retenu'] is not None else r['b_reel']
                if montant is not None:
                    c['cout_credits_micro' if r['b_devise'] == 'CRD' else 'cout_usd_micro'] += int(montant)
                    c['cout_connus'] += 1
        if r['escalade_de']:             # escalade : comptée au modèle qui a dû être remplacé, le jour de son appel
            a = db.execute('SELECT at,fournisseur,modele,type_tache FROM decisions_5682 WHERE uid=?', (r['escalade_de'],)).fetchone()
            if a:
                compteur(str(a['at'])[:10], a['fournisseur'], a['modele'], a['type_tache'])['escalades'] += 1
        if correction and r['mission']:  # correction après le contrôle de rédaction : comptée au brouillon d'origine (même travail, même étape)
            for a in db.execute('SELECT at,fournisseur,modele,type_tache,etat,raisons FROM decisions_5682 WHERE mission=? AND etape=? AND id<? '
                                'ORDER BY id DESC LIMIT 5', (r['mission'], r['etape'], r['id'])):
                if MARQUE_CORRECTION in str(a['raisons'] or ''):
                    continue
                if a['etat'] == 'terminee':
                    compteur(str(a['at'])[:10], a['fournisseur'], a['modele'], a['type_tache'])['corrections'] += 1
                break
        actuel = '%s|%s' % (r['fournisseur'], r['modele'])
        k = (jour, r['type_tache'], actuel, str(r['mesure_choix'] or ''), str(r['mesure_mode'] or ''))
        choix[k] = choix.get(k, 0) + 1
    for (jour, f, m, t), c in sommes.items():
        ancien = db.execute('SELECT durees FROM routage5695_agregats WHERE jour=? AND fournisseur=? AND modele=? AND type_tache=?', (jour, f, m, t)).fetchone()
        try:
            durees = json.loads(ancien[0]) if ancien else []
        except ValueError:
            durees = []
        durees = [int(x) for x in durees][:len(BORNES_MS) + 1] + [0] * max(0, len(BORNES_MS) + 1 - len(durees))
        durees = [a + b for a, b in zip(durees, c['durees'])]
        db.execute('INSERT INTO routage5695_agregats(jour,fournisseur,modele,type_tache,%s,durees) VALUES(?,?,?,?,%s,?) '
                   'ON CONFLICT(jour,fournisseur,modele,type_tache) DO UPDATE SET %s,durees=excluded.durees' % (
                       ','.join(COMPTEURS), ','.join('?' for _ in COMPTEURS), ','.join('%s=%s+excluded.%s' % (n, n, n) for n in COMPTEURS)),
                   (jour, f, m, t, *[c[n] for n in COMPTEURS], json.dumps(durees)))
    for (jour, t, actuel, mesure, mode_), n in choix.items():
        db.execute('INSERT INTO routage5695_choix(jour,type_tache,actuel,mesure,mode,n) VALUES(?,?,?,?,?,?) '
                   'ON CONFLICT(jour,type_tache,actuel,mesure,mode) DO UPDATE SET n=n+excluded.n', (jour, t, actuel, mesure, mode_, n))
    db.execute('INSERT OR REPLACE INTO routage5695_meta VALUES(?,?)', ('filigrane', str(fin)))
    return len(vus), len(lignes) >= LOT


def mesures(state_dir, jours=FENETRE, maintenant=None):
    """{(fournisseur, modèle, type de tâche): mesures} sur ``jours`` jours glissants ; None si la base n'a pas pu être lue (jamais un faux zéro)."""
    if not state_dir or not _base(state_dir).is_file():
        return None
    cle = (str(_base(state_dir)), int(jours))
    with _verrou:
        trouve = _cache_mesures.get(cle)
        if trouve and maintenant is None and trouve[0] > time.monotonic():
            return trouve[1]
    depuis = ((maintenant or _maintenant()) - timedelta(days=int(jours) - 1)).date().isoformat()
    try:
        db = _connect(state_dir)
        try:
            lignes = db.execute('SELECT * FROM routage5695_agregats WHERE jour>=?', (depuis,)).fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return None
    brut = {}
    for r in lignes:
        k = (r['fournisseur'], r['modele'], r['type_tache'])
        s = brut.setdefault(k, {**{c: 0 for c in COMPTEURS}, 'durees': [0] * (len(BORNES_MS) + 1)})
        for c in COMPTEURS:
            s[c] += int(r[c] or 0)
        try:
            for i, n in enumerate(json.loads(r['durees'] or '[]')[:len(BORNES_MS) + 1]):
                s['durees'][i] += int(n)
        except (ValueError, TypeError):
            pass
    sortie = {k: _taux(s) for k, s in brut.items()}
    if maintenant is None:
        with _verrou:
            _cache_mesures[cle] = (time.monotonic() + CACHE_MESURES, sortie)
    return sortie


def _taux(s):
    """Taux et délais d'une somme d'agrégats. Une réussite = appel terminé sans correction demandée ensuite."""
    appels = s['appels']
    valides = max(0, s['terminees'] - s['corrections'])
    part = (lambda n, d: (n / d) if d else None)
    cout_usd = (s['cout_usd_micro'] / 1e6 / s['cout_connus']) if s['cout_connus'] and s['cout_usd_micro'] else None
    cout_credits = (s['cout_credits_micro'] / 1e6 / s['cout_connus']) if s['cout_connus'] and s['cout_credits_micro'] else None
    return {**{c: s[c] for c in COMPTEURS}, 'valides': valides, 'reussite': part(valides, appels), 'taux_invalides': part(s['invalides'], appels),
            'taux_escalade': part(s['escalades'], appels), 'taux_relais': part(s['relais'], appels), 'taux_controle': part(s['corrections'], s['brouillons']),
            'mediane_ms': _centile(s['durees'], 50), 'p95_ms': _centile(s['durees'], 95), 'cout_usd': cout_usd, 'cout_credits': cout_credits,
            'local': s['locaux'] > 0 and s['locaux'] >= appels, 'suffisant': appels >= SEUIL_ECHANTILLON}


# ---------------------------------------------------------------------------------------------------- G05 : rapport d'évaluation
def evaluation(state_dir):
    """Résultats déterministes du rapport de ``scripts/evaluation-redaction.py`` copié dans l'état du serveur ; None s'il manque ou s'il est
    illisible. Seuls des nombres et l'identifiant du modèle sont repris ; le reste du fichier est ignoré."""
    if not state_dir:
        return None
    chemin = Path(state_dir).joinpath(*EVALUATION)
    try:
        mtime = chemin.stat().st_mtime
    except OSError:
        return None
    cle = str(chemin)
    with _verrou:
        trouve = _cache_evaluation.get(cle)
        if trouve and trouve[0] == mtime:
            return trouve[1]
    resultat = None
    try:
        if chemin.stat().st_size <= 5_000_000:
            data = json.loads(chemin.read_text(encoding='utf-8'))
            modele = str(data.get('modele') or '')
            versions = data.get('versions') if isinstance(data.get('versions'), dict) else {}
            if _MODELE.fullmatch(modele) and versions:
                etiquette = list(versions)[-1]           # la dernière mesure du rapport (« après »)
                s = (versions[etiquette] or {}).get('synthese') or {}
                part = s.get('regle_respectee', s.get('schema_respecte'))
                cas = s.get('cas')
                if isinstance(part, (int, float)) and not isinstance(part, bool) and 0 <= part <= 1 and isinstance(cas, int) and cas > 0:
                    delai = s.get('duree_redaction_mediane_s')
                    resultat = {'fournisseur': 'ollama', 'modele': modele, 'score': round(100 * float(part), 1), 'samples': cas,
                                'delai': int(float(delai) * 1000) if isinstance(delai, (int, float)) and not isinstance(delai, bool) else None,
                                'etiquette': str(etiquette)[:40], 'date': str(data.get('date') or '')[:32], 'source': 'evaluation'}
    except (OSError, ValueError, TypeError, AttributeError):
        resultat = None
    with _verrou:
        _cache_evaluation[cle] = (mtime, resultat)
    return resultat


# ---------------------------------------------------------------------------------------------------- G02 : qualité dans la décision
def poids_usage(appels):
    bas, haut, plein = POIDS_USAGE
    if appels < SEUIL_ECHANTILLON:
        return 0.0
    if appels >= plein:
        return haut
    return bas + (haut - bas) * (appels - SEUIL_ECHANTILLON) / (plein - SEUIL_ECHANTILLON)


def score(banc, usage):
    """(score sur 100 ou None, source) : banc seul, usage seul ou combinaison pondérée (``poids_usage``)."""
    b = float(banc['score']) if banc and banc.get('score') is not None else None
    u = 100.0 * usage['reussite'] if usage and usage.get('suffisant') and usage.get('reussite') is not None else None
    if u is not None and b is not None:
        w = poids_usage(usage['appels'])
        return round(w * u + (1 - w) * b, 1), 'banc+usage'
    if u is not None:
        return round(u, 1), 'usage'
    if b is not None:
        return round(b, 1), 'banc'
    return None, ''


def mode(state_dir):
    """``conseil`` (défaut) ou ``actif`` ; lu dans les réglages du cabinet, gardé 30 s. Base illisible : ``conseil``."""
    if not state_dir or not _base(state_dir).is_file():
        return 'conseil'
    cle = str(_base(state_dir))
    with _verrou:
        trouve = _cache_reglage.get(cle)
        if trouve and trouve[0] > time.monotonic():
            return trouve[1]
    valeur = 'conseil'
    try:
        db = sqlite3.connect(_base(state_dir), timeout=2)
        try:
            row = db.execute('SELECT value FROM settings WHERE key=?', (REGLAGE,)).fetchone()
        finally:
            db.close()
        brut = json.loads(row[0]) if row else {}
        valeur = brut.get('valeur') if isinstance(brut, dict) and brut.get('valeur') in MODES else 'conseil'
    except (sqlite3.Error, ValueError, TypeError):
        valeur = 'conseil'
    with _verrou:
        _cache_reglage[cle] = (time.monotonic() + CACHE_REGLAGE, valeur)
    return valeur


def _nom(provider_id, slug, homonymes):
    nom = LIBELLES_FOURNISSEURS.get(provider_id, provider_id)
    return '%s (%s)' % (nom, slug) if homonymes else nom


def _pourcent(x):
    return '%d %%' % round(100 * x) if x is not None else '?'


def _secondes(ms):
    if ms is None:
        return ''
    return ('%d s' % round(ms / 1000)) if ms >= 1000 else ('%d ms' % ms)


def evaluer(config, found, type_tache, seuil, cle_cout, actuel, maintenant=None):
    """Choix mesuré parmi ``found`` (candidats déjà filtrés par toute la politique) : dictionnaire avec ``choix`` (un élément de ``found``,
    jamais un autre), ``mode``, ``raison``, ``suffisant``, ``identique``. None en cas d'imprévu : la décision reste celle d'aujourd'hui."""
    try:
        return _evaluer(config, found, type_tache, seuil, cle_cout, actuel, maintenant)
    except Exception:   # noqa: BLE001 — le routage mesuré ne fait jamais échouer une décision
        return None


def _evaluer(config, found, type_tache, seuil, cle_cout, actuel, maintenant):
    state_dir = config.get('state_dir')
    reglage = mode(state_dir)
    rafraichir(state_dir, maintenant=maintenant, cabinet=str(config.get('cabinet_id') or ''))
    usage = mesures(state_dir, FENETRE, maintenant) or {}
    eval_ = evaluation(state_dir) if type_tache == 'redaction_mail' else None
    notes = []
    for item in found:
        pid, slug = item[0], item[2]['slug']
        banc = item[5]
        if not banc and eval_ and eval_['fournisseur'] == pid and eval_['modele'] == slug and eval_['samples'] >= 3:
            banc = eval_
        u = usage.get((pid, slug, type_tache))
        valeur, source = score(banc, u)
        notes.append({'item': item, 'score': valeur, 'source': source, 'usage': u, 'banc': banc})
    mesures_ = [n for n in notes if n['score'] is not None]
    suffisant = any(n['usage'] and n['usage']['suffisant'] for n in notes)
    eligibles = []
    if suffisant:                        # sans usage suffisant : comportement actuel inchangé (le banc filtre déjà dans ``decide``)
        for n in mesures_:
            meilleur = max(m['score'] for m in mesures_ if m['item'][3] == n['item'][3])
            if n['score'] < float(seuil) or n['score'] < meilleur - ECART_TOLERE:
                continue
            eligibles.append(n)
    sous_seuil = suffisant and not eligibles

    def rang(n):
        base = cle_cout(n['item'])
        u = n['usage'] if n['usage'] and n['usage']['suffisant'] else None
        delai = (u or {}).get('mediane_ms') or (n['banc'] or {}).get('delai') or base[3]
        return base[:3] + (-int((n['score'] or 0) // 5), float(delai if delai is not None else float('inf')), base[4])
    if eligibles:
        retenu = min(eligibles, key=rang)
        choix = retenu['item']
    else:
        retenu, choix = next(n for n in notes if n['item'] is actuel), actuel
    if not any(choix is item for item in found):         # garde-fou : jamais un candidat hors de la liste filtrée par la politique
        return None
    # Comparaison citée : le meilleur modèle mesuré de chaque AUTRE fournisseur (deux au plus) ; le modèle n'est nommé qu'en cas d'ambiguïté
    autres_notes, vus = [], {retenu['item'][0]}
    for n in sorted((n for n in notes if n['score'] is not None), key=lambda n: -(n['score'] or 0)):
        if n['item'][0] not in vus and len(autres_notes) < 2:
            vus.add(n['item'][0])
            autres_notes.append(n)
    if not autres_notes:                 # un seul fournisseur permis : ses autres modèles
        autres_notes = [n for n in sorted((n for n in notes if n is not retenu and n['score'] is not None), key=lambda n: -(n['score'] or 0))][:2]
    cites = [retenu] + autres_notes
    homonymes = lambda pid: sum(1 for n in cites if n['item'][0] == pid) > 1
    label = lambda n: _nom(n['item'][0], n['item'][2]['slug'], homonymes(n['item'][0]))
    from .tache5682 import TASK_LABELS
    tache = TASK_LABELS.get(type_tache, 'travail')
    if sous_seuil:
        raison = 'Aucun modèle permis n’atteint le seuil de qualité sur mesures réelles (%s) : choix habituel, provisoire.' % tache
    elif not suffisant:
        raison = 'Mesures insuffisantes (moins de %d appels sur %d jours, %s) : choix habituel, provisoire.' % (SEUIL_ECHANTILLON, FENETRE, tache)
    else:
        u = retenu['usage'] if retenu['usage'] and retenu['usage']['suffisant'] else None
        if u:
            tete = '%s : %s de réussite sur %d appels (%s)' % (label(retenu), _pourcent(u['reussite']), u['appels'], tache)
            if u.get('mediane_ms') is not None:
                tete += ', %s médian' % _secondes(u['mediane_ms'])
        else:
            tete = '%s : score %d au banc (%s)' % (label(retenu), round(retenu['score'] or 0), tache)
        autres = []
        for n in autres_notes:
            uu = n['usage'] if n['usage'] and n['usage']['suffisant'] else None
            autres.append('%s : %s' % (label(n), _pourcent(uu['reussite']) if uu else 'score %d au banc' % round(n['score'])))
        raison = tete + ((' ; ' + ' ; '.join(autres)) if autres else '') + '.'
    return {'mode': reglage, 'choix': choix, 'fournisseur': choix[0], 'modele': choix[2]['slug'], 'raison': raison[:300],
            'suffisant': bool(suffisant and not sous_seuil), 'provisoire': bool(sous_seuil or not suffisant),
            'identique': choix is actuel, 'score': retenu.get('score')}


# ---------------------------------------------------------------------------------------------------- G03 : réglage
def reglage(desk):
    brut = desk.settings(REGLAGE, None)
    brut = brut if isinstance(brut, dict) else {}
    valeur = brut.get('valeur') if brut.get('valeur') in MODES else 'conseil'
    revision = brut.get('revision') if isinstance(brut.get('revision'), int) and not isinstance(brut.get('revision'), bool) else 0
    return {'valeur': valeur, 'revision': revision}


def enregistrer_mode(desk, valeur, revision, auteur):
    """Administrateur seulement (contrôlé par la route ET ici). La révision lue doit être l'actuelle (409 sinon) ; même valeur : rien n'est
    réécrit (une répétition de la même demande est sans effet)."""
    if valeur not in MODES:
        raise Stop('routage_mode_invalide')
    try:
        revision = int(str(revision))
    except (TypeError, ValueError):
        raise Stop('routage_mode_invalide') from None
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        actuel = reglage(desk)
        if valeur == actuel['valeur']:
            desk.db.rollback()
            return {**actuel, 'message': MESSAGES_MODE[valeur]}
        if revision != actuel['revision']:
            raise Stop('revision_perimee')
        desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (REGLAGE, json.dumps({'valeur': valeur, 'revision': revision + 1})))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    with _verrou:
        _cache_reglage.pop(str(_base(desk.c['state_dir'])), None)
    desk.audit('routage5695_mode', {'valeur': valeur, 'revision': revision + 1, 'par': str(auteur)[:120]})
    return {'valeur': valeur, 'revision': revision + 1, 'message': MESSAGES_MODE[valeur]}


MESSAGES_MODE = {'conseil': 'Routage mesuré en mode conseil : les décisions restent celles d’aujourd’hui.',
                 'actif': 'Routage mesuré actif : les mesures réelles entrent dans les décisions, toujours après la politique du cabinet.'}


# ---------------------------------------------------------------------------------------------------- G04 : tableau, page, export
def _periode(valeur):
    try:
        valeur = int(str(valeur))
    except (TypeError, ValueError):
        return FENETRE
    return valeur if valeur in PERIODES else FENETRE


def tableau(desk, jours=FENETRE):
    """Lignes de la page et de l'export : par type de tâche, les modèles mesurés et les modèles permis par le mode actuel, avec leurs
    mesures, l'échantillon, le choix actuel, le choix mesuré et l'écart. None si la base n'a pas pu être lue."""
    state_dir = desk.c['state_dir']
    rafraichir(state_dir, cabinet=str(desk.c.get('cabinet_id') or ''))
    data = mesures(state_dir, jours)
    if data is None:
        return None
    from . import politique5682
    try:
        rule = politique5682.read_fresh(state_dir) or politique5682.read(desk.c)
        permis = set(politique5682.candidates(desk.c, rule)) if rule else set()
    except Exception:   # noqa: BLE001 — politique illisible : rien n'est dit permis
        rule, permis = None, set()
    if rule and rule.get('mode') == 'local':
        permis = {'ollama'}
    depuis = (_maintenant() - timedelta(days=jours - 1)).date().isoformat()
    choix = {}
    try:
        db = _connect(state_dir)
        try:
            for r in db.execute('SELECT type_tache,actuel,mesure,mode,SUM(n) n FROM routage5695_choix WHERE jour>=? GROUP BY type_tache,actuel,mesure,mode', (depuis,)):
                choix.setdefault(r['type_tache'], []).append(dict(r))
            rafraichi = _meta(db, 'rafraichi', '')
        finally:
            db.close()
    except sqlite3.Error:
        return None
    eval_ = evaluation(state_dir)
    from .routage5682 import _mesures, _models_for
    permis_modeles = []                  # modèles des connexions permises, montrés même sans mesure (échantillon nul, dit tel quel)
    try:
        from .ai_gateway import provider_registry
        registre = provider_registry(desk.c)
        for pid in sorted(permis):
            if pid in registre and rule:
                permis_modeles += [(pid, r['slug']) for r in _models_for(desk.c, rule, pid, registre[pid]) if r.get('statut', 'actif') == 'actif']
    except Exception:   # noqa: BLE001 — liste des modèles indisponible : seuls les modèles mesurés sont montrés
        permis_modeles = []
    types = sorted({k[2] for k in data} | set(choix))
    sortie = []
    for t in types:
        bancs = {}
        purpose = {'redaction_mail': 'mail_drafting', 'analyse_juridique': 'legal_analysis', 'redaction_acte': 'document_drafting',
                   'conversation': 'assistant', 'controle': 'control'}.get(t)
        if purpose:
            try:
                bancs = _mesures(desk.c, purpose)
            except Exception:   # noqa: BLE001
                bancs = {}
        modeles = []
        for (f, m, tt), s in sorted(data.items()):
            if tt != t:
                continue
            banc = bancs.get((f, m)) or (eval_ if t == 'redaction_mail' and eval_ and eval_['fournisseur'] == f and eval_['modele'] == m else None)
            valeur, source = score(banc, s)
            modeles.append({'fournisseur': f, 'modele': m, 'permis': f in permis, 'banc': banc, 'score': valeur, 'source': source, **s})
        vides = _taux({**{c: 0 for c in COMPTEURS}, 'durees': [0] * (len(BORNES_MS) + 1)})
        for f, m in permis_modeles:
            if not any(x['fournisseur'] == f and x['modele'] == m for x in modeles):
                banc = bancs.get((f, m))
                valeur, source = score(banc, None)
                modeles.append({'fournisseur': f, 'modele': m, 'permis': True, 'banc': banc, 'score': valeur, 'source': source, **vides, 'local': f == 'ollama'})
        lignes = choix.get(t, [])
        total = sum(x['n'] for x in lignes)
        compare = [x for x in lignes if x['mesure']]
        ecarts = sum(x['n'] for x in compare if x['mesure'] != x['actuel'])
        frequent = lambda cle: max(({x[cle] for x in lignes if x[cle]}), key=lambda v: sum(y['n'] for y in lignes if y[cle] == v), default='')
        sortie.append({'type_tache': t, 'modeles': modeles, 'decisions': total, 'comparees': sum(x['n'] for x in compare), 'ecarts': ecarts,
                       'choix_actuel': frequent('actuel'), 'choix_mesure': frequent('mesure'),
                       'actives': sum(x['n'] for x in lignes if x['mode'] == 'actif')})
    return {'jours': jours, 'types': sortie, 'rafraichi': rafraichi, 'mode': reglage(desk), 'evaluation': eval_, 'permis': sorted(permis)}


def _envois(desk, jours):
    """Brouillons envoyés sur la période (``outcomes480``) : tous modèles confondus, l'envoi n'étant pas rattaché au modèle. None si inconnu."""
    depuis = (_maintenant() - timedelta(days=jours)).isoformat()
    try:
        if not _table_existe(desk.db, 'outcomes480'):
            return None
        rows = desk.db.execute('SELECT outcome,COUNT(*) FROM outcomes480 WHERE sent_at>=? GROUP BY outcome', (depuis,)).fetchall()
    except sqlite3.Error:
        return None
    compte = {r[0]: int(r[1]) for r in rows}
    total = sum(compte.values())
    return {'total': total, **compte} if total else None


def _fr(x, chiffres=1):
    return ('%.*f' % (chiffres, x)).replace('.', ',')


def _cout(m):
    from .i18n5631 import t
    if m.get('local'):
        return t('routage5695.cout_local')
    if m.get('cout_usd') is not None:
        return t('routage5695.cout_usd', montant=_fr(m['cout_usd'], 4))
    if m.get('cout_credits') is not None:
        return t('routage5695.cout_credits', montant=_fr(m['cout_credits'], 2))
    return t('routage5695.non_fourni')


def _fournisseur(f):
    from .i18n5631 import t
    return t('routage5695.local') if f == 'ollama' else LIBELLES_FOURNISSEURS.get(f, f)


def _tache(type_tache, labels):
    """Libellé traduit du type de tâche (HTML échappé) ; un type inconnu garde son libellé français ou son code."""
    from .i18n5631 import catalog, th
    return th('routage5695.tache.' + type_tache) if ('routage5695.tache.' + type_tache) in catalog('fr') else e(labels.get(type_tache, type_tache))


def _choix_lisible(valeur):
    if not valeur:
        return '—'
    f, _, m = valeur.partition('|')
    return '%s · %s' % (_fournisseur(f), m)


def section_html(desk, prefix, env):
    """Section Carbon de Paramètres › Modèles d'IA. Aucun texte ni nom de dossier : seulement des modèles, des nombres et des codes."""
    from urllib.parse import parse_qs
    from .i18n5631 import th
    from .tache5682 import TASK_LABELS
    from .web567 import actor
    _owner, role = actor(env)
    jours = _periode((parse_qs(str(env.get('QUERY_STRING') or '')).get('periode') or [''])[0])
    admin = role == 'administrateur'
    data = tableau(desk, jours)
    r = reglage(desk)
    off = '' if admin else ' disabled'
    modes = ''.join('<label class="m50-check"><input type="radio" name="valeur" value="%s"%s%s> <strong>%s</strong> — %s</label>' % (
        v, ' checked' if r['valeur'] == v else '', off, th('routage5695.mode_' + v), th('routage5695.mode_%s_aide' % v)) for v in MODES)
    formulaire = ('<form class="m5-form" data-api="m5689/routage/mode" data-reload="1"><fieldset><legend>%s</legend>%s'
                  '<input type="hidden" name="revision" value="%d"></fieldset>%s<p role="status" class="m5-status"></p></form>') % (
        th('routage5695.mode'), modes, r['revision'], '<button class="ax-btn" type="submit">%s</button>' % th('routage5695.enregistrer') if admin
        else '<p class="ax-note">%s</p>' % th('routage5695.lecture_seule'))
    filtre = ('<form method="get" action="%s#routage5695" class="m82-inline"><input type="hidden" name="rubrique" value="ia">'
              '<label class="m5-field">%s<select name="periode">%s</select></label><button class="ax-btn ghost" type="submit">%s</button>'
              ' <a class="ax-btn ghost" href="%s">%s</a></form>') % (
        e(prefix + '/parametres', quote=True), th('routage5695.periode'),
        ''.join('<option value="%d"%s>%s</option>' % (p, ' selected' if p == jours else '', th('routage5695.jours', n=p)) for p in PERIODES),
        th('routage5695.afficher'), e('%s/api440/m5689/routage/export?periode=%d' % (prefix, jours), quote=True), th('routage5695.export'))
    if data is None:
        corps = '<p class="ax-alert" role="status">%s</p>' % th('routage5695.illisible')
    elif not data['types']:
        corps = '<p class="ax-muted">%s</p>' % th('routage5695.vide', n=jours)
    else:
        corps = ''.join(_bloc(t, TASK_LABELS) for t in data['types'])
    envois = _envois(desk, jours)
    envois_html = ('<p class="ax-muted">%s</p>' % th('routage5695.envois', total=envois['total'], tel_quel=envois.get('tel_quel', 0),
                                                     leger=envois.get('leger', 0), reecrit=envois.get('reecrit', 0))) if envois else ''
    ev = (data or {}).get('evaluation')
    eval_html = ('<p class="ax-muted">%s</p>' % th('routage5695.evaluation', modele=ev['modele'], score=_fr(ev['score']), cas=ev['samples'],
                                                    etiquette=ev['etiquette'])) if ev else '<p class="ax-muted">%s</p>' % th('routage5695.evaluation_absente')
    rafraichi = (data or {}).get('rafraichi') or ''
    return ('<section class="ax-card" id="routage5695" aria-labelledby="routage5695-titre"><h2 id="routage5695-titre">%s</h2><p>%s</p>'
            '<p class="ax-muted">%s</p>%s%s%s%s%s<p class="ax-muted">%s</p></section>') % (
        th('routage5695.titre'), th('routage5695.intro'), th('routage5695.regle', seuil=SEUIL_ECHANTILLON, ecart=ECART_TOLERE), formulaire, filtre,
        corps, envois_html, eval_html, th('routage5695.rafraichi', heure=rafraichi[:16].replace('T', ' ') or '—'))


def _bloc(t, labels):
    from .i18n5631 import th
    lignes = ''
    for m in sorted(t['modeles'], key=lambda m: (not m['permis'], -(m['appels'] or 0))):
        lignes += ('<tr><th scope="row">%s</th><td>%s</td><td>%d%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>'
                   '<td>%s</td><td>%s</td></tr>') % (
            e('%s · %s' % (_fournisseur(m['fournisseur']), m['modele'])),
            th('routage5695.oui') if m['permis'] else th('routage5695.non'), m['appels'],
            '' if m['suffisant'] else ' (%s)' % th('routage5695.insuffisant'), e(_pourcent(m['reussite'])), e(_pourcent(m['taux_invalides'])),
            e(_pourcent(m['taux_escalade'])), e(_pourcent(m['taux_relais'])), e(_pourcent(m['taux_controle']) if m['brouillons'] else '—'),
            e(_secondes(m['mediane_ms']) or '—'), e(_secondes(m['p95_ms']) or '—'), e(_cout(m)),
            e(_fr(m['score']) if m['score'] is not None else '—'))
    if not lignes:
        lignes = '<tr><td colspan="12">%s</td></tr>' % th('routage5695.aucune_mesure')
    ecart = (_pourcent(t['ecarts'] / t['comparees']) if t['comparees'] else '—')
    return ('<h3>%s</h3><div class="m82-table"><table class="vf-table"><caption class="sr-only">%s</caption><thead><tr>'
            '<th scope="col">%s</th><th scope="col">%s</th><th scope="col">%s</th><th scope="col">%s</th><th scope="col">%s</th><th scope="col">%s</th>'
            '<th scope="col">%s</th><th scope="col">%s</th><th scope="col">%s</th><th scope="col">%s</th><th scope="col">%s</th><th scope="col">%s</th>'
            '</tr></thead><tbody>%s</tbody></table></div><p>%s</p>') % (
        _tache(t['type_tache'], labels), _tache(t['type_tache'], labels),
        th('routage5695.col_modele'), th('routage5695.col_permis'), th('routage5695.col_echantillon'), th('routage5695.col_reussite'),
        th('routage5695.col_invalides'), th('routage5695.col_escalades'), th('routage5695.col_relais'), th('routage5695.col_controle'),
        th('routage5695.col_mediane'), th('routage5695.col_p95'), th('routage5695.col_cout'), th('routage5695.col_score'), lignes,
        th('routage5695.choix', actuel=_choix_lisible(t['choix_actuel']), mesure=_choix_lisible(t['choix_mesure']), ecart=ecart,
           n=t['decisions']))


def export_csv(desk, jours):
    """CSV (séparateur « ; », UTF-8 avec BOM) : les mêmes lignes que la page. Stop si la base n'a pas pu être lue."""
    data = tableau(desk, _periode(jours))
    if data is None:
        raise Stop('routage_mesures_illisibles')
    from .tache5682 import TASK_LABELS
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';')
    w.writerow(['periode_jours', 'type_tache', 'libelle', 'fournisseur', 'modele', 'permis', 'appels', 'echantillon_suffisant', 'reussite', 'sorties_invalides',
                'escalades', 'relais', 'echec_controle_brouillons', 'mediane_ms', 'p95_ms', 'cout_moyen_usd', 'cout_moyen_credits', 'local', 'score_banc',
                'score_mesure', 'source_score', 'choix_actuel', 'choix_mesure', 'decisions', 'comparees', 'ecarts'])
    taux = lambda x: '' if x is None else '%.4f' % x
    for t in data['types']:
        for m in t['modeles'] or [None]:
            m = m or {}
            w.writerow([data['jours'], t['type_tache'], TASK_LABELS.get(t['type_tache'], t['type_tache']), m.get('fournisseur', ''), m.get('modele', ''),
                        ('oui' if m.get('permis') else 'non') if m else '', m.get('appels', ''), ('oui' if m.get('suffisant') else 'non') if m else '',
                        taux(m.get('reussite')), taux(m.get('taux_invalides')), taux(m.get('taux_escalade')), taux(m.get('taux_relais')),
                        taux(m.get('taux_controle')), m.get('mediane_ms') or '', m.get('p95_ms') or '', '' if m.get('cout_usd') is None else '%.6f' % m['cout_usd'],
                        '' if m.get('cout_credits') is None else '%.4f' % m['cout_credits'], ('oui' if m.get('local') else 'non') if m else '',
                        '' if not m.get('banc') else m['banc'].get('score', ''), '' if m.get('score') is None else m['score'], m.get('source', ''),
                        t['choix_actuel'], t['choix_mesure'], t['decisions'], t['comparees'], t['ecarts']])
    texte = '﻿' + buf.getvalue()
    desk.audit('routage5695_export', {'jours': data['jours'], 'types': len(data['types'])})
    return 'routage-mesure-%dj.csv' % data['jours'], texte
