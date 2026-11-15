"""5.6.102 (lot J) : place occupée par le journal d'audit et nettoyage (purge par durée, vidage complet), chaîne d'empreintes gardée vérifiable.

Demande du cabinet : « vider le journal d'audit pour éviter qu'il prenne de la place inutile ». Décision : les deux.

- **Purge par durée** : les entrées de plus de N mois (12 par défaut, de 1 à 120) partent, à la demande (bouton) et, si l'administrateur
  l'active, une fois par jour dans la maintenance périodique (``activity431.maintenance``), jamais pendant l'affichage d'une page (R05).
- **Vidage complet** : administrateur seulement, double confirmation (case cochée et mot « VIDER » tapé), sur les entrées affichées.
- Chaque opération reste inscrite dans le journal (« journal purgé / vidé par X, N entrées, période ») : la ligne est ajoutée *après* la
  suppression, elle ne peut donc pas être effacée par la même opération ; une purge ultérieure peut l'effacer si elle est assez ancienne,
  mais en inscrit alors une nouvelle. L'historique des opérations (table ``audit_purges56102``) n'est jamais purgé.

**Ancrage de la chaîne.** Chaque événement de ``audit_chain_v290`` porte l'empreinte du précédent. Une purge supprime toujours un *préfixe*
de la chaîne (numéros 1…K, jamais un trou au milieu : une entrée récente arrête le préfixe même si une plus ancienne suit) et enregistre
dans ``audit_purges56102`` le numéro K et l'empreinte du dernier événement supprimé. ``cabinet_pilot.audit_status`` et ``audit_verify``
repartent de cet ancrage : la première entrée conservée doit citer exactement cette empreinte. Retirer l'ancrage, le déplacer ou laisser une
entrée antérieure à l'ancrage fait déclarer la chaîne rompue (jamais un faux « intacte »). Une chaîne déjà rompue n'est jamais purgée :
on ne fait pas disparaître la trace d'une altération.

**Place rendue.** SQLite ne rend la place au disque qu'avec ``VACUUM``, qui verrouille l'écriture le temps de recopier toute la base : il
n'a jamais lieu pendant une requête. Une purge le demande ; la maintenance le fait la nuit (1 h à 6 h, heure du cabinet), s'il y a au moins
1 Mo à rendre et assez d'espace disque (deux fois la base).

L'ancien journal ``audit`` (copie non chaînée de chaque événement) est nettoyé avec les mêmes bornes, sauf les actions qui servent encore
de preuve à une fonction (``CONSERVEES``).
"""
from calendar import monthrange
from datetime import date, datetime, time as dtime, timezone
import hashlib
from html import escape as e
import json
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import time
from urllib.parse import urlencode

from .common import Stop

MOIS_DEFAUT = 12
MOIS_MIN, MOIS_MAX = 1, 120
MOT_VIDER = 'VIDER'
REGLAGE = 'journal56102:reglage'
DERNIERE_AUTO = 'journal56102:derniere_auto'
COMPACTAGE = 'journal56102:compactage'
NUIT = (1, 6)                       # heures du cabinet où la maintenance peut compacter la base
SEUIL_COMPACTAGE = 1024 * 1024      # en dessous d'1 Mo libre, pas de compactage (rien d'utile à rendre)
# Actions de l'ancien journal ``audit`` lues comme preuve par une fonction (dépôt vérifié des conclusions, décisions de l'avocat) : gardées.
CONSERVEES = ('conclusions_televersees', 'etat_metier_modifie')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS audit_purges56102(
  id INTEGER PRIMARY KEY AUTOINCREMENT, seq INTEGER NOT NULL, event_hash TEXT NOT NULL, mode TEXT NOT NULL, par TEXT NOT NULL,
  fait_le TEXT NOT NULL, supprimees INTEGER NOT NULL, du TEXT NOT NULL, au TEXT NOT NULL, mois INTEGER NOT NULL DEFAULT 0,
  ancien_journal INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS audit_purges56102_seq ON audit_purges56102(seq);
CREATE TABLE IF NOT EXISTS journal56102_cles(cle TEXT PRIMARY KEY, action TEXT NOT NULL, resultat TEXT NOT NULL, at TEXT NOT NULL);
'''

MESSAGES = {'version_perimee': 'Le journal a changé depuis l’affichage de la page : rechargez-la et vérifiez les chiffres avant de confirmer.',
            'cle_reutilisee': 'Cette demande a déjà servi pour une autre action : rechargez la page.',
            'cle_requise': 'Demande incomplète : rechargez la page.',
            'mois_invalide': 'Durée de conservation invalide : de 1 à 120 mois.',
            'confirmation_requise': 'Cochez la case et tapez VIDER pour confirmer : rien n’a été supprimé.',
            'chaine_rompue': 'La chaîne d’empreintes est rompue : rien n’a été supprimé. Conservez une copie de la base et cherchez la cause d’abord.',
            'rien_a_vider': 'Le journal ne contient aucune entrée à supprimer.',
            'route_inconnue': 'Adresse inconnue.'}
STATUTS = {'version_perimee': '409 Conflict', 'cle_reutilisee': '409 Conflict', 'route_inconnue': '404 Not Found'}


def ensure_schema(desk):
    """Tables additives ; appelée par ``Desk`` à la préparation de la base (jamais pendant une requête de lecture)."""
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.executescript(SCHEMA)
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ lectures (aucune écriture)
def ancre(desk):
    """Dernier ancrage enregistré (numéro et empreinte du dernier événement supprimé), ou None. Table illisible : None, ce qui fait
    déclarer la chaîne rompue si des entrées ont été purgées (fausse alerte possible, jamais un faux « intacte »)."""
    try:
        row = desk.db.execute('SELECT * FROM audit_purges56102 ORDER BY seq DESC, id DESC LIMIT 1').fetchone()
    except sqlite3.Error:
        return None
    return dict(row) if row else None


def historique(desk, limite=5):
    try:
        return [dict(r) for r in desk.db.execute('SELECT * FROM audit_purges56102 ORDER BY id DESC LIMIT ?', (int(limite),))]
    except sqlite3.Error:
        return []


def reglage(desk):
    raw = desk.settings(REGLAGE, {}) or {}
    try:
        mois = int(raw.get('mois', MOIS_DEFAUT))
    except (TypeError, ValueError, AttributeError):
        mois = MOIS_DEFAUT
    return {'mois': max(MOIS_MIN, min(MOIS_MAX, mois)), 'auto': bool(isinstance(raw, dict) and raw.get('auto'))}


def _empreinte(*parts):
    return hashlib.sha256('|'.join(str(p) for p in parts).encode()).hexdigest()[:20]


def version_reglage(desk):
    r = reglage(desk)
    return _empreinte('reglage', r['mois'], int(r['auto']))


def _aujourdhui(desk):
    """Jour et fuseau du cabinet (la recette tourne en UTC : jamais ``date.today()``)."""
    from .acces5683 import today_of
    return today_of(desk)


def _mois_avant(jour, n):
    total = jour.year * 12 + jour.month - 1 - int(n)
    an, mois = divmod(total, 12)
    mois += 1
    return date(an, mois, min(jour.day, monthrange(an, mois)[1]))


def limite(desk, mois):
    """Instant (ISO, UTC) avant lequel une entrée a « plus de N mois » : minuit, heure du cabinet, N mois avant aujourd'hui."""
    jour, fuseau = _aujourdhui(desk)
    coupe = _mois_avant(jour, mois)
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(fuseau)
    except Exception:   # noqa: BLE001 — fuseau illisible : UTC
        tz = timezone.utc
    return datetime.combine(coupe, dtime(0), tz).astimezone(timezone.utc).isoformat(), coupe


def _bornes(desk, jusqua):
    row = desk.db.execute('SELECT COUNT(*), MIN(at), MAX(at), MIN(seq) FROM audit_chain_v290 WHERE seq<=?', (int(jusqua),)).fetchone()
    return {'n': row[0] or 0, 'du': row[1] or '', 'au': row[2] or '', 'premier': row[3] or 0}


def apercu_purge(desk, mois):
    """Préfixe de la chaîne à supprimer : toutes les entrées jusqu'à la première de moins de N mois (exclue)."""
    coupe_iso, coupe = limite(desk, mois)
    recent = desk.db.execute('SELECT MIN(seq) FROM audit_chain_v290 WHERE at>=?', (coupe_iso,)).fetchone()[0]
    jusqua = (recent - 1) if recent is not None else (desk.db.execute('SELECT COALESCE(MAX(seq),0) FROM audit_chain_v290').fetchone()[0])
    b = _bornes(desk, jusqua)
    a = ancre(desk)
    return {'mois': int(mois), 'limite': coupe_iso, 'limite_jour': coupe.isoformat(), 'jusqua': int(jusqua), **b,
            'version': _empreinte('purge', mois, coupe_iso, jusqua, b['n'], (a or {}).get('seq', 0))}


def apercu_vidage(desk, jusqua=None):
    if jusqua is None:
        jusqua = desk.db.execute('SELECT COALESCE(MAX(seq),0) FROM audit_chain_v290').fetchone()[0]
    b = _bornes(desk, jusqua)
    a = ancre(desk)
    return {'jusqua': int(jusqua), **b, 'version': _empreinte('vider', jusqua, b['n'], b['premier'], (a or {}).get('seq', 0))}


def taille(desk):
    """Nombre d'entrées, période couverte, octets approximatifs (chaîne + ancien journal) et place libre en attente de compactage."""
    row = desk.db.execute('SELECT COUNT(*), MIN(at), MAX(at), COALESCE(SUM(LENGTH(payload)+LENGTH(action)+LENGTH(at)+200),0) '
                          'FROM audit_chain_v290').fetchone()
    ancien = desk.db.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(COALESCE(data,''))+LENGTH(COALESCE(action,''))+60),0) FROM audit").fetchone()
    page = desk.db.execute('PRAGMA page_size').fetchone()[0]
    libres = desk.db.execute('PRAGMA freelist_count').fetchone()[0]
    return {'entrees': row[0] or 0, 'du': row[1] or '', 'au': row[2] or '', 'octets': int(row[3] or 0) + int(ancien[1] or 0),
            'ancien': ancien[0] or 0, 'libre': int(page) * int(libres)}


def etat_compactage(desk):
    raw = desk.settings(COMPACTAGE, {}) or {}
    return raw if isinstance(raw, dict) else {}


# ------------------------------------------------------------------------------------------------ écritures (POST, maintenance)
def _iso_now():
    return datetime.now(timezone.utc).isoformat()


def _cle_idem(desk, cle, action):
    """Même clé, même action → même résultat (aucun second effet) ; même clé pour une autre action → 409."""
    cle = str(cle or '')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,80}', cle):
        raise Stop('cle_requise')
    row = desk.db.execute('SELECT action, resultat FROM journal56102_cles WHERE cle=?', (cle,)).fetchone()
    if row is None:
        return cle, None
    if row[0] != action:
        raise Stop('cle_reutilisee')
    return cle, json.loads(row[1])


def _retenir(desk, cle, action, resultat):
    if cle:
        desk.db.execute('INSERT OR REPLACE INTO journal56102_cles VALUES(?,?,?,?)', (cle, action, json.dumps(resultat, ensure_ascii=False), _iso_now()))


def _mois(value):
    try:
        mois = int(str(value).strip())
    except (TypeError, ValueError):
        raise Stop('mois_invalide') from None
    if not MOIS_MIN <= mois <= MOIS_MAX:
        raise Stop('mois_invalide')
    return mois


def _debut(desk):
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')


def _jour(iso):
    iso = str(iso or '')
    try:
        return date.fromisoformat(iso[:10]).strftime('%d/%m/%Y')
    except ValueError:
        return '—'


def _supprimer(desk, jusqua, mode, par, mois, coupe_iso=None):
    """Dans la transaction ouverte : supprime le préfixe 1…jusqua, enregistre l'ancrage, nettoie l'ancien journal, inscrit l'opération."""
    from .cabinet_pilot import chain_check
    statut = chain_check(desk, 1)   # sans ``ensure_schema`` (qui validerait la transaction ouverte)
    if not statut['valid']:
        raise Stop('chaine_rompue')
    dernier = desk.db.execute('SELECT seq, at, event_hash FROM audit_chain_v290 WHERE seq<=? ORDER BY seq DESC LIMIT 1', (int(jusqua),)).fetchone()
    if dernier is None:
        return None
    b = _bornes(desk, jusqua)
    n = desk.db.execute('DELETE FROM audit_chain_v290 WHERE seq<=?', (int(jusqua),)).rowcount
    garde = ','.join('?' * len(CONSERVEES))
    if coupe_iso:   # purge : même limite de date pour l'ancien journal
        ancien = desk.db.execute("DELETE FROM audit WHERE at<? AND COALESCE(action,'') NOT IN (%s)" % garde, (coupe_iso,) + CONSERVEES).rowcount
    else:           # vidage : tout ce qui n'est pas plus récent que le dernier événement supprimé
        ancien = desk.db.execute("DELETE FROM audit WHERE at<=? AND COALESCE(action,'') NOT IN (%s)" % garde, (dernier['at'],) + CONSERVEES).rowcount
    fait = _iso_now()
    desk.db.execute('INSERT INTO audit_purges56102(seq,event_hash,mode,par,fait_le,supprimees,du,au,mois,ancien_journal) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (int(dernier['seq']), dernier['event_hash'], mode, str(par)[:200], fait, n, b['du'], b['au'], int(mois or 0), ancien))
    compact = etat_compactage(desk)
    desk.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (COMPACTAGE, json.dumps({**compact, 'demande': True, 'depuis': fait})))
    resume = {'mode': mode, 'par': str(par)[:200], 'auteur': str(par)[:200], 'supprimees': n, 'du': b['du'], 'au': b['au'],
              'jusqu_au_numero': int(dernier['seq']), 'ancre': dernier['event_hash'][:16], 'ancien_journal': ancien}
    if mois:
        resume['mois'] = int(mois)
    # La ligne de l'opération est ajoutée après la suppression : elle ne peut pas être effacée par la même opération. ``desk.audit`` valide
    # toute la transaction (suppression, ancrage, demande de compactage et ligne inscrite ensemble, ou rien).
    desk.audit('journal_audit_vide' if mode == 'vidage' else 'journal_audit_purge', resume)
    reste = desk.db.execute('SELECT COUNT(*) FROM audit_chain_v290').fetchone()[0]
    return {**resume, 'reste': reste}


def purger(desk, par, mois, version=None, idem=None, auto=False):
    """Purge par durée. Manuelle : clé d'idempotence et version de l'aperçu affiché (409 si le journal a changé). Automatique : ni l'une ni
    l'autre (la maintenance recalcule l'aperçu dans la transaction)."""
    mois = _mois(mois)
    cle = None
    if not auto:
        cle, deja = _cle_idem(desk, idem, 'purger')
        if deja is not None:
            return deja
    _debut(desk)
    try:
        ap = apercu_purge(desk, mois)
        if not auto and str(version or '') != ap['version']:
            raise Stop('version_perimee')
        if ap['n'] == 0:
            out = {'supprimees': 0, 'message': 'Aucune entrée de plus de %d mois : rien à purger.' % mois}
            _retenir(desk, cle, 'purger', out)
            desk.db.commit()
            return out
        fait = _supprimer(desk, ap['jusqua'], 'auto' if auto else 'purge', par, mois, ap['limite'])
        out = {**fait, 'message': ('%d entrée(s) de plus de %d mois supprimée(s) (du %s au %s). Il en reste %d. La place sera rendue au disque '
                                   'lors de la prochaine maintenance de nuit.') % (fait['supprimees'], mois, _jour(fait['du']), _jour(fait['au']), fait['reste'])}
        _retenir(desk, cle, 'purger', out)
        desk.db.commit()
        return out
    except BaseException:
        desk.db.rollback()
        raise


def vider(desk, par, jusqua, version, compris, mot, idem):
    """Vidage complet des entrées affichées (jusqu'au numéro ``jusqua``) : case cochée ET mot « VIDER » tapé, sinon rien n'est supprimé."""
    cle, deja = _cle_idem(desk, idem, 'vider')
    if deja is not None:
        return deja
    if compris is not True or str(mot or '').strip() != MOT_VIDER:
        raise Stop('confirmation_requise')
    try:
        jusqua = int(jusqua)
    except (TypeError, ValueError):
        raise Stop('version_perimee') from None
    _debut(desk)
    try:
        ap = apercu_vidage(desk, jusqua)
        if str(version or '') != ap['version']:
            raise Stop('version_perimee')
        if ap['n'] == 0:
            raise Stop('rien_a_vider')
        fait = _supprimer(desk, jusqua, 'vidage', par, 0)
        nouvelles = fait['reste'] - 1
        out = {**fait, 'message': ('Journal vidé : %d entrée(s) supprimée(s) (du %s au %s). Seule la ligne « journal vidé » reste%s. La place sera '
                                   'rendue au disque lors de la prochaine maintenance de nuit.') % (
            fait['supprimees'], _jour(fait['du']), _jour(fait['au']),
            (', avec %d entrée(s) arrivée(s) depuis l’affichage' % nouvelles) if nouvelles > 0 else '')}
        _retenir(desk, cle, 'vider', out)
        desk.db.commit()
        return out
    except BaseException:
        desk.db.rollback()
        raise


def regler(desk, par, mois, auto, version, idem):
    cle, deja = _cle_idem(desk, idem, 'reglage')
    if deja is not None:
        return deja
    mois = _mois(mois)
    _debut(desk)
    try:
        if str(version or '') != version_reglage(desk):
            raise Stop('version_perimee')
        valeur = {'mois': mois, 'auto': auto is True}
        desk.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (REGLAGE, json.dumps(valeur)))
        out = {**valeur, 'message': 'Réglage enregistré : conserver %d mois%s.' % (
            mois, ', purge automatique chaque jour' if valeur['auto'] else ', purge seulement à la demande')}
        _retenir(desk, cle, 'reglage', out)
        desk.audit('journal_audit_reglage', {'par': str(par)[:200], 'mois': mois, 'auto': valeur['auto']})   # valide la transaction
        return out
    except BaseException:
        desk.db.rollback()
        raise


def maintenance(desk, stamp=None):
    """Appelée par ``activity431.maintenance`` (jamais pendant une requête) : purge automatique une fois par jour si l'administrateur l'a
    activée, puis compactage de nuit s'il a été demandé. Renvoie ce qui a été fait (pour les tests et le suivi)."""
    stamp = time.time() if stamp is None else float(stamp)
    out = {}
    r = reglage(desk)
    if r['auto'] and stamp - float(desk.settings(DERNIERE_AUTO, 0) or 0) >= 86400:
        desk.setting(DERNIERE_AUTO, stamp)
        try:
            out['purge'] = purger(desk, 'maintenance', r['mois'], auto=True)
        except Stop as ex:   # chaîne rompue : rien supprimé, l'écran du journal le montre déjà
            out['purge'] = {'erreur': str(ex)}
    out['compactage'] = compacter(desk)
    return out


def _heure_cabinet(desk):
    _, fuseau = _aujourdhui(desk)
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(fuseau)).hour
    except Exception:   # noqa: BLE001
        return datetime.now(timezone.utc).hour


def compacter(desk, forcer=False):
    """Rend au disque la place libérée par une purge (``VACUUM``), la nuit seulement, si elle a été demandée et si le disque le permet."""
    etat = etat_compactage(desk)
    if not etat.get('demande'):
        return {'fait': False, 'motif': 'non_demande'}
    if not forcer and not NUIT[0] <= _heure_cabinet(desk) < NUIT[1]:
        return {'fait': False, 'motif': 'hors_nuit'}
    t = taille(desk)
    base = Path(desk.c['state_dir']) / 'desk.sqlite3'
    try:
        avant = base.stat().st_size
        libre_disque = shutil.disk_usage(str(base.parent)).free
    except OSError:
        return {'fait': False, 'motif': 'base_illisible'}
    if t['libre'] < SEUIL_COMPACTAGE:
        desk.setting(COMPACTAGE, {**etat, 'demande': False, 'dernier': {'le': _iso_now(), 'rendu': 0, 'motif': 'rien_a_rendre'}})
        return {'fait': False, 'motif': 'rien_a_rendre'}
    if libre_disque < 2 * avant:
        desk.setting(COMPACTAGE, {**etat, 'echec': {'le': _iso_now(), 'motif': 'espace_disque_insuffisant'}})
        return {'fait': False, 'motif': 'espace_disque_insuffisant'}
    if desk.db.in_transaction:
        desk.db.commit()
    try:
        desk.db.execute('VACUUM')
        desk.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    except sqlite3.Error as ex:   # base occupée : nouvel essai la nuit suivante, demande conservée
        desk.setting(COMPACTAGE, {**etat, 'echec': {'le': _iso_now(), 'motif': str(ex)[:120]}})
        return {'fait': False, 'motif': 'base_occupee'}
    try:
        apres = base.stat().st_size
    except OSError:
        apres = avant
    desk.setting(COMPACTAGE, {'demande': False, 'dernier': {'le': _iso_now(), 'rendu': max(0, avant - apres), 'avant': avant, 'apres': apres}})
    return {'fait': True, 'rendu': max(0, avant - apres)}


# ------------------------------------------------------------------------------------------------ route (POST, administrateur)
def _json(value, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(value, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def api(desk, route, data, me):
    """``POST /api440/m5688s/journal/{purger,vider,reglage}`` : rôle administrateur et jeton CSRF déjà contrôlés par ``securite5688.api``."""
    try:
        if route == 'journal/purger':
            out = purger(desk, me, data.get('mois'), data.get('version'), data.get('idem'))
        elif route == 'journal/vider':
            out = vider(desk, me, data.get('jusqua'), data.get('version'), data.get('compris'), data.get('mot'), data.get('idem'))
        elif route == 'journal/reglage':
            out = regler(desk, me, data.get('mois'), data.get('auto'), data.get('version'), data.get('idem'))
        else:
            raise Stop('route_inconnue')
    except Stop as ex:
        code = str(ex).split(':')[0]
        return _json({'error': code, 'message': MESSAGES.get(code, 'Demande refusée : rien n’a été supprimé.')}, STATUTS.get(code, '422 Unprocessable Entity'))
    return _json(out)


# ------------------------------------------------------------------------------------------------ écran (lecture seule)
def _mo(octets):
    octets = int(octets or 0)
    if octets < 1024 * 1024:
        return '%d Ko' % max(1, round(octets / 1024)) if octets else '0 Ko'
    return ('%.1f Mo' % (octets / 1048576)).replace('.', ',')


def _mode(m):
    return {'purge': 'purge', 'auto': 'purge automatique', 'vidage': 'vidage complet'}.get(m, m)


def encadre_html(desk, prefix, kept, export_limit):
    """Encadré « Place occupée et nettoyage » et section « Vider tout le journal » de la page du journal d'audit (aucune écriture)."""
    t, r = taille(desk), reglage(desk)
    ap, av = apercu_purge(desk, r['mois']), apercu_vidage(desk)
    export = '%s/api440/m5688s/journal/csv?%s' % (prefix, urlencode(kept))
    lien = '<a class="ax-btn ghost" href="%s">Exporter d’abord (CSV)</a>' % e(export, quote=True)
    limite_txt = ('<p class="notice" role="note">L’export est limité aux %s entrées les plus récentes : pour tout garder, exportez par période '
                  '(filtres Du et Au) avant de supprimer.</p>' % format(export_limit, ',').replace(',', ' ')) if t['entrees'] > export_limit else ''
    periode = ('du %s au %s' % (_jour(t['du']), _jour(t['au']))) if t['entrees'] else 'aucune entrée'
    lignes = ['<p><strong>%d entrée(s)</strong>, %s ; environ %s dans la base (ancien journal compris).</p>' % (t['entrees'], periode, _mo(t['octets']))]
    c = etat_compactage(desk)
    if c.get('demande') or t['libre'] >= SEUIL_COMPACTAGE:
        lignes.append('<p class="ax-muted">Place libérée, rendue au disque lors de la prochaine maintenance de nuit : environ %s.</p>' % _mo(t['libre']))
    if c.get('echec'):
        lignes.append('<p class="ax-muted">Dernier essai de compactage le %s : non fait (%s) ; nouvel essai la nuit suivante.</p>' % (
            _jour(c['echec'].get('le')), e(str(c['echec'].get('motif', '')).replace('_', ' '))))
    elif (c.get('dernier') or {}).get('rendu'):
        lignes.append('<p class="ax-muted">Dernier compactage le %s : %s rendus au disque.</p>' % (_jour(c['dernier'].get('le')), _mo(c['dernier']['rendu'])))
    hist = historique(desk, 1)
    if hist:
        h = hist[0]
        lignes.append('<p class="ax-muted">Dernier nettoyage : %s le %s par %s, %d entrée(s) (du %s au %s).</p>' % (
            _mode(h['mode']), _jour(h['fait_le']), e(h['par']), h['supprimees'], _jour(h['du']), _jour(h['au'])))
    def idem():   # clé neuve par formulaire ; rien n'est écrit à l'affichage
        return secrets.token_urlsafe(18)
    reglage_form = ('<form class="m5-form" data-api="m5688s/journal/reglage" data-reload autocomplete="off">'
                    '<label>Conserver<input type="number" name="mois" min="%d" max="%d" value="%d" required> mois</label>'
                    '<label class="m50-check"><input type="checkbox" name="auto"%s> Purger automatiquement chaque jour (sans export préalable)</label>'
                    '<input type="hidden" name="version" value="%s"><input type="hidden" name="idem" value="%s">'
                    '<button class="ax-btn ghost" type="submit">Enregistrer</button></form>') % (
        MOIS_MIN, MOIS_MAX, r['mois'], ' checked' if r['auto'] else '', e(version_reglage(desk), quote=True), idem())
    if ap['n']:
        purge_txt = '<p>%d entrée(s) ont plus de %d mois (du %s au %s).</p>' % (ap['n'], r['mois'], _jour(ap['du']), _jour(ap['au']))
    else:
        purge_txt = '<p>Aucune entrée n’a plus de %d mois.</p>' % r['mois']
    purge_form = ('<form class="m5-form" data-api="m5688s/journal/purger" data-reload data-confirm="%s">'
                  '<input type="hidden" name="mois" value="%d"><input type="hidden" name="version" value="%s"><input type="hidden" name="idem" value="%s">'
                  '<button class="ax-btn" type="submit"%s>Purger maintenant les entrées de plus de %d mois</button> %s</form>') % (
        e('Supprimer définitivement les %d entrée(s) de plus de %d mois ? Exportez-les d’abord si vous devez les garder.' % (ap['n'], r['mois']), quote=True),
        r['mois'], e(ap['version'], quote=True), idem(), '' if ap['n'] else ' disabled', r['mois'], lien)
    nettoyage = ('<section class="ax-card" id="j102-nettoyage"><h2>Place occupée et nettoyage</h2>%s%s<h3>Purger les entrées anciennes</h3>%s%s%s'
                 '<p class="ax-muted">Une ligne « journal purgé » reste inscrite (qui, quand, combien, quelle période) et la chaîne reste vérifiable à partir de '
                 'la première entrée conservée. Sans purge automatique, rien n’est supprimé sans votre clic.</p></section>') % (
        ''.join(lignes), limite_txt, reglage_form, purge_txt, purge_form)
    vider_form = ('<section class="ax-card" id="j102-vider"><h2>Vider tout le journal</h2>'
                  '<p>Supprime définitivement les %d entrée(s) affichées (%s). Il ne restera qu’une ligne « journal vidé » indiquant qui l’a vidé, quand, '
                  'combien d’entrées et sur quelle période.</p>'
                  '<form class="m5-form" data-api="m5688s/journal/vider" data-reload autocomplete="off" data-confirm="%s">'
                  '<label class="m50-check"><input type="checkbox" name="compris"> J’ai exporté le journal ou je n’en ai pas besoin, et je comprends que '
                  'cette suppression est définitive.</label><label>Pour confirmer, tapez VIDER<input name="mot" maxlength="10" autocomplete="off" required></label>'
                  '<input type="hidden" name="jusqua" value="%d"><input type="hidden" name="version" value="%s"><input type="hidden" name="idem" value="%s">'
                  '<button class="ax-btn danger" type="submit"%s>Vider le journal</button> %s</form></section>') % (
        av['n'], periode, e('Vider tout le journal d’audit ? Cette suppression est définitive.', quote=True), av['jusqua'], e(av['version'], quote=True), idem(),
        '' if av['n'] else ' disabled', lien)
    return nettoyage + vider_form


def verdict_ancre(state):
    """Complément honnête du verdict quand des entrées ont été purgées : vérifiée depuis quel numéro, purgée quand et par qui."""
    a = state.get('ancre')
    if not a:
        return ''
    return (' Chaîne vérifiée depuis l’entrée n° %d (entrées antérieures supprimées le %s par %s, %s).' % (
        int(a['seq']) + 1, _jour(a['fait_le']), e(str(a['par'])), _mode(a['mode'])))
