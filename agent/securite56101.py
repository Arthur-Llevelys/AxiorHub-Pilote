"""5.6.101 (lot S, cahier § 5) : veille de sécurité du serveur, côté AxiorHub (lecture des rapports, constats, accusés, alertes).

Partage des rôles (S-A1, S-A2) :
- le collecteur ``veille-securite.py`` (root, lecture seule, minuteurs systemd) écrit un rapport JSON dans
  ``/var/lib/axiorhub-mail-agent/securite/`` (root:axiorhub-mail, 640) ; aucune route web ne le déclenche ;
- ce module (compte ``axiorhub-mail``, sans privilège) LIT ces rapports. L'analyse (``ingerer``) n'a lieu que dans la maintenance
  périodique (``activity431.maintenance``), jamais pendant l'affichage d'une page (R05) : la page, le bandeau d'Aujourd'hui et le compteur
  du téléphone ne lisent que les tables déjà remplies ;
- AxiorHub n'exécute jamais de commande de correction ni ``systemctl`` : les commandes du rapport sont affichées pour être copiées par
  l'administrateur ou son prestataire. La désactivation (S-A4) dépose une demande, appliquée par le service privilégié
  ``axiorhub-veille-securite-demande`` (sur le modèle d'``axiorhub-mail-agent-maj.path``).

Constats : identifiant de contrôle (S01…S13), clé stable, niveau (critique, important, surveiller), explication, éléments vus, commandes,
empreinte. « Marquer comme connu » (motif obligatoire, journal d'audit) masque un constat tant que son empreinte ne change pas.

Alertes (S-U3, décisions D1 et D2) : un constat critique ouvert et non connu compte dans ``nombre_alerte`` (clé ``securite`` du résumé
neutre du téléphone, ``mobile490``) et dans le bandeau rouge d'Aujourd'hui (administrateurs) ; un constat important apparaît dans la file
« À décider » (``queue_items``) ; « À surveiller » seulement sur la page et dans le résumé hebdomadaire (S14). Aucune alerte n'est
répétée plus d'une fois par heure pour le même constat : un constat critique résolu reste compté une heure après sa dernière alerte, de
sorte que sa réapparition dans l'heure n'augmente pas le nombre et ne déclenche pas de nouveau signal.

Poste Windows ou Ubuntu : la veille n'existe pas (rien n'y est installé) ; la page le dit, le bandeau et la file restent vides.
"""
from datetime import datetime, timedelta, timezone
import hashlib
from html import escape as e
import json
import os
from pathlib import Path
import re

from .common import Stop

FORMAT = 1
IDS = ('S01', 'S02', 'S03', 'S04', 'S05', 'S06', 'S07', 'S08', 'S09', 'S10', 'S11', 'S12', 'S13')
NIVEAUX = {'critique': ('Critique', 0), 'important': ('Important', 1), 'surveiller': ('À surveiller', 2)}
HISTORIQUE_JOURS = 30
ARRET_MINUTES = 30            # S-U2 : « Veille arrêtée ? » si le dernier rapport a plus de 30 minutes
ANTI_REBOND = 3600            # S-U3 : une alerte au plus par heure et par constat
MAX_RAPPORT = 5 * 1024 * 1024
MAX_VU, MAX_TEXTE, MAX_COMMANDES = 40, 400, 12
MOTIF_MAX = 500
# 5.6.102 (lot C) : éléments que le collecteur reconnaît comme normaux (ligne d'information, jamais une alerte)
RECONNUS = {'collabora': 'Collabora intégré à Nextcloud reconnu'}
CLE_RECONNUS = 'securite56101:reconnus'
MESURES = (('processus', 'Programme suspect arrêté (copie conservée pour l’enquête)'),
           ('tache', 'Tâche planifiée suspecte supprimée (copie conservée)'),
           ('mots_de_passe', 'Mots de passe et clés des applications changés'),
           ('mises_a_jour', 'Applications et système mis à jour'),
           ('acces_web', 'Accès web restreints (racines des sites, fichiers de secrets)'),
           ('sauvegarde', 'Sauvegarde saine vérifiée'),
           ('prestataire', 'Prestataire ou hébergeur prévenu'))

SCHEMA = '''
CREATE TABLE IF NOT EXISTS secu56101_rapports(nom TEXT PRIMARY KEY, passage TEXT NOT NULL, debut TEXT NOT NULL, fin TEXT NOT NULL,
  charge REAL, coeurs INTEGER NOT NULL DEFAULT 0, critiques INTEGER NOT NULL DEFAULT 0, importants INTEGER NOT NULL DEFAULT 0,
  surveiller INTEGER NOT NULL DEFAULT 0, erreurs TEXT NOT NULL DEFAULT '[]', ingere TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS secu56101_rapports_fin ON secu56101_rapports(fin);
CREATE TABLE IF NOT EXISTS secu56101_constats(cle TEXT PRIMARY KEY, controle TEXT NOT NULL, niveau TEXT NOT NULL, titre TEXT NOT NULL,
  explication TEXT NOT NULL DEFAULT '', vu TEXT NOT NULL DEFAULT '[]', commandes TEXT NOT NULL DEFAULT '[]', aide TEXT NOT NULL DEFAULT '',
  empreinte TEXT NOT NULL, premier TEXT NOT NULL, dernier TEXT NOT NULL, etat TEXT NOT NULL DEFAULT 'ouvert', resolu TEXT NOT NULL DEFAULT '',
  derniere_alerte TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS secu56101_constats_etat ON secu56101_constats(etat, niveau);
CREATE TABLE IF NOT EXISTS secu56101_accuses(cle TEXT NOT NULL, empreinte TEXT NOT NULL, motif TEXT NOT NULL, auteur TEXT NOT NULL,
  at TEXT NOT NULL, PRIMARY KEY(cle, empreinte));
CREATE TABLE IF NOT EXISTS secu56101_fiches(id TEXT PRIMARY KEY, cree TEXT NOT NULL, auteur TEXT NOT NULL, texte TEXT NOT NULL,
  mesures TEXT NOT NULL DEFAULT '[]', etat TEXT NOT NULL DEFAULT 'brouillon');
CREATE TABLE IF NOT EXISTS secu56101_cles(cle TEXT PRIMARY KEY, action TEXT NOT NULL, cible TEXT NOT NULL, resultat TEXT NOT NULL, at TEXT NOT NULL);
'''


# ------------------------------------------------------------------------------------------------------------ plateforme
def plateforme(auth=None):
    """« serveur » (Linux, version serveur), « poste » (Ubuntu poste ou moteur local) ou « windows ». Remplaçable dans les tests."""
    if os.name == 'nt':
        return 'windows'
    if (isinstance(auth, dict) and auth.get('poste')) or os.environ.get('AXIORHUB_POSTE_HOME') or \
            Path(__file__).resolve().as_posix().startswith('/usr/lib/axiorhub-pilote/'):
        return 'poste'
    return 'serveur'


def disponible(auth=None):
    try:
        return plateforme(auth) == 'serveur'
    except Exception:   # noqa: BLE001 — en cas de doute, la veille est dite absente (jamais un faux « tout va bien »)
        return False


def dossier_rapports(desk):
    return Path(str(desk.c.get('state_dir') or '/var/lib/axiorhub-mail-agent')) / 'securite'


def dossier_demandes(desk):
    return Path(str(desk.c.get('state_dir') or '/var/lib/axiorhub-mail-agent')) / 'securite-demande'


def ensure_schema(desk):
    """Création idempotente, au démarrage (``Desk``) ou dans la maintenance ; jamais pendant un GET."""
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def _table(desk):
    return bool(desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='secu56101_constats'").fetchone())


def _now(now=None):
    if isinstance(now, datetime):
        return now.astimezone(timezone.utc)
    return datetime.fromtimestamp(now, timezone.utc) if now is not None else datetime.now(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _instant(text):
    try:
        dt = datetime.fromisoformat(str(text).replace('Z', '+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _texte(value, limite=MAX_TEXTE):
    text = ' '.join(str(value if value is not None else '').split())
    return text if len(text) <= limite else text[:limite - 1] + '…'


def _liste(value, n, limite=MAX_TEXTE):
    if not isinstance(value, list):
        return []
    return [_texte(x, limite) for x in value[:n] if isinstance(x, (str, int, float))]


# ------------------------------------------------------------------------------------------------------------ analyse
def _lire_rapport(path):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_RAPPORT:
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(data, dict) or data.get('format') != FORMAT or not isinstance(data.get('constats'), list):
        return None
    if data.get('passage') not in ('rapide', 'complet') or not _instant(data.get('fin')):
        return None
    return data


def _constat_propre(c):
    """Constat du rapport, borné et nettoyé (le rapport est une donnée : jamais du HTML, jamais une instruction)."""
    if not isinstance(c, dict) or c.get('id') not in IDS or c.get('niveau') not in NIVEAUX:
        return None
    cle = _texte(c.get('cle'), 300)
    if not cle or not str(cle).startswith(c['id'] + ':'):
        return None
    empreinte = str(c.get('empreinte') or '')
    if not re.fullmatch(r'[0-9a-f]{16,64}', empreinte):
        empreinte = hashlib.sha256(json.dumps(c.get('vu'), sort_keys=True, default=str).encode()).hexdigest()
    return {'cle': cle, 'controle': c['id'], 'niveau': c['niveau'], 'titre': _texte(c.get('titre') or c['id'], 200),
            'explication': _texte(c.get('explication'), 1200), 'vu': _liste(c.get('vu'), MAX_VU),
            'commandes': _liste(c.get('commandes'), MAX_COMMANDES, 600), 'aide': re.sub(r'[^A-Za-z0-9_-]', '', str(c.get('aide') or c['id']))[:40],
            'empreinte': empreinte}


def _appliquer(desk, nom, rapport, maintenant):
    fin = _instant(rapport['fin']) or maintenant
    fin_iso = _iso(fin)
    controles = rapport.get('controles') if isinstance(rapport.get('controles'), dict) else {}
    faits = [k for k, v in controles.items() if k in IDS and v in ('ok', 'constat')]   # un contrôle en erreur ne résout rien
    trouves = {}
    for c in rapport['constats']:
        propre = _constat_propre(c)
        if propre and propre['controle'] in faits:
            trouves[propre['cle']] = propre
    if faits:
        marques = ','.join('?' * len(faits))
        for row in desk.db.execute("SELECT cle FROM secu56101_constats WHERE etat='ouvert' AND controle IN (%s)" % marques, faits).fetchall():
            if row[0] not in trouves:
                desk.db.execute("UPDATE secu56101_constats SET etat='resolu', resolu=? WHERE cle=?", (fin_iso, row[0]))
    for cle, c in trouves.items():
        old = desk.db.execute('SELECT * FROM secu56101_constats WHERE cle=?', (cle,)).fetchone()
        alerte = old['derniere_alerte'] if old else ''
        nouveau = old is None or old['etat'] != 'ouvert' or old['empreinte'] != c['empreinte']
        connu = desk.db.execute('SELECT 1 FROM secu56101_accuses WHERE cle=? AND empreinte=?', (cle, c['empreinte'])).fetchone()
        if c['niveau'] == 'critique' and nouveau and not connu:
            precedente = _instant(alerte)
            if precedente is None or (fin - precedente).total_seconds() >= ANTI_REBOND:
                alerte = fin_iso
        premier = old['premier'] if (old and old['etat'] == 'ouvert') else fin_iso
        desk.db.execute('INSERT OR REPLACE INTO secu56101_constats(cle,controle,niveau,titre,explication,vu,commandes,aide,empreinte,premier,dernier,'
                        "etat,resolu,derniere_alerte) VALUES(?,?,?,?,?,?,?,?,?,?,?,'ouvert','',?)",
                        (cle, c['controle'], c['niveau'], c['titre'], c['explication'], json.dumps(c['vu'], ensure_ascii=False),
                         json.dumps(c['commandes'], ensure_ascii=False), c['aide'], c['empreinte'], premier, fin_iso, alerte))
    niveaux = [c['niveau'] for c in trouves.values()]
    hote = rapport.get('hote') if isinstance(rapport.get('hote'), dict) else {}
    charge = hote.get('charge')
    try:
        charge = float(charge[0]) if isinstance(charge, list) and charge else None
    except (TypeError, ValueError):
        charge = None
    try:
        coeurs = int(hote.get('coeurs') or 0)
    except (TypeError, ValueError):
        coeurs = 0
    erreurs = [k for k, v in controles.items() if k in IDS and str(v).startswith('erreur')]
    desk.db.execute('INSERT OR REPLACE INTO secu56101_rapports VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (nom, rapport['passage'], _texte(rapport.get('debut') or fin_iso, 40), fin_iso, charge, coeurs, niveaux.count('critique'),
                     niveaux.count('important'), niveaux.count('surveiller'), json.dumps(erreurs), _iso(maintenant)))


def _reconnus_propres(rapport):
    """5.6.102 (lot C) : éléments reconnus comme normaux par le collecteur (liste fermée d'identifiants, nombre de processus borné).
    Le texte affiché est celui d'AxiorHub, jamais celui du rapport."""
    out = []
    for x in rapport.get('reconnus') if isinstance(rapport.get('reconnus'), list) else []:
        if not isinstance(x, dict) or x.get('id') not in RECONNUS:
            continue
        n = x.get('processus')
        if isinstance(n, int) and not isinstance(n, bool) and 0 < n <= 100_000 and x['id'] not in {y['id'] for y in out}:
            out.append({'id': x['id'], 'processus': n})
    return out


def reconnus(desk):
    """Lignes d'information du dernier rapport analysé (lecture seule) : [{'id', 'processus', 'texte', 'fin'}]."""
    try:
        data = desk.settings(CLE_RECONNUS, None)
    except Exception:   # noqa: BLE001 — une ligne d'information absente vaut mieux qu'une page en erreur
        return []
    if not isinstance(data, dict) or not isinstance(data.get('elements'), list):
        return []
    out = []
    for x in _reconnus_propres({'reconnus': data['elements']}):
        out.append({'id': x['id'], 'processus': x['processus'], 'fin': str(data.get('fin') or '')[:40],
                    'texte': '%s (%d processus)' % (RECONNUS[x['id']], x['processus'])})
    return out


def ingerer(desk, now=None, dossier=None, limite=60):
    """Analyse les rapports nouveaux (maintenance périodique seulement). Rapide quand rien n'a changé : seules les dates des deux
    derniers rapports sont relues. Renvoie le nombre de rapports analysés."""
    maintenant = _now(now)
    d = Path(dossier) if dossier else dossier_rapports(desk)
    if not d.is_dir():
        return {'nouveaux': 0, 'etat': 'absent'}
    ensure_schema(desk)
    try:
        marque = [int(os.stat(str(d / n)).st_mtime_ns) if (d / n).exists() else 0 for n in ('dernier-rapide.json', 'dernier-complet.json')]
    except OSError:
        marque = [0, 0]
    if marque != [0, 0] and desk.settings('securite56101:marque', None) == marque:
        return {'nouveaux': 0, 'etat': 'inchange'}
    connus = {r[0] for r in desk.db.execute('SELECT nom FROM secu56101_rapports')}
    limite_age = maintenant - timedelta(days=HISTORIQUE_JOURS)
    noms = sorted(p.name for p in d.glob('rapport-*.json') if p.name not in connus)
    traites = 0
    reconnus = None
    for nom in noms[-int(limite):]:
        rapport = _lire_rapport(d / nom)
        if rapport is None:
            continue
        if (_instant(rapport['fin']) or maintenant) < limite_age:
            continue
        _appliquer(desk, nom, rapport, maintenant)
        reconnus = {'fin': _iso(_instant(rapport['fin']) or maintenant), 'elements': _reconnus_propres(rapport)}
        traites += 1
    borne = _iso(limite_age)
    desk.db.execute('DELETE FROM secu56101_rapports WHERE fin<?', (borne,))
    desk.db.execute("DELETE FROM secu56101_constats WHERE etat='resolu' AND resolu<?", (borne,))
    desk.db.commit()
    if reconnus is not None:
        desk.setting(CLE_RECONNUS, reconnus)      # 5.6.102 : éléments reconnus du dernier rapport (ligne d'information de la page)
    desk.setting('securite56101:marque', marque)
    return {'nouveaux': traites, 'etat': 'ok'}


# ------------------------------------------------------------------------------------------------------------ lecture
def _connu_sql():
    return 'EXISTS(SELECT 1 FROM secu56101_accuses a WHERE a.cle=c.cle AND a.empreinte=c.empreinte)'


def nombre_alerte(desk, now=None):
    """Constats critiques ouverts et non connus (et résolus depuis moins d'une heure après leur dernière alerte : anti-rebond S-U3).
    Lecture seule ; 0 si la veille n'existe pas sur cette plateforme ou si les tables manquent."""
    try:
        if not disponible() or not _table(desk):
            return 0
        borne = _iso(_now(now) - timedelta(seconds=ANTI_REBOND))
        sql = ("SELECT COUNT(*) FROM secu56101_constats c WHERE c.niveau='critique' AND NOT %s AND "
               "(c.etat='ouvert' OR (c.etat='resolu' AND c.derniere_alerte<>'' AND c.derniere_alerte>=?))") % _connu_sql()
        return int(desk.db.execute(sql, (borne,)).fetchone()[0])
    except Exception:   # noqa: BLE001 — le résumé du téléphone ne doit jamais échouer pour la veille
        return 0


def _ligne(row, connu=None):
    out = {k: row[k] for k in ('cle', 'controle', 'niveau', 'titre', 'explication', 'aide', 'empreinte', 'premier', 'dernier', 'etat', 'resolu')}
    out['vu'] = json.loads(row['vu'] or '[]')
    out['commandes'] = json.loads(row['commandes'] or '[]')
    out['libelle'] = NIVEAUX.get(row['niveau'], ('', 9))[0]
    if connu is not None:
        out['connu'] = connu
    return out


def constats(desk, connus=False):
    """Constats ouverts (non connus par défaut ; ``connus=True`` : ceux marqués comme connus), triés par niveau puis par date."""
    if not _table(desk):
        return []
    sql = "SELECT c.* FROM secu56101_constats c WHERE c.etat='ouvert' AND %s %s" % ('' if connus else 'NOT', _connu_sql())
    rows = [_ligne(r) for r in desk.db.execute(sql)]
    if connus:
        for x in rows:
            a = desk.db.execute('SELECT motif, auteur, at FROM secu56101_accuses WHERE cle=? AND empreinte=?', (x['cle'], x['empreinte'])).fetchone()
            x['connu'] = {'motif': a['motif'], 'auteur': a['auteur'], 'at': a['at']} if a else {}
    rows.sort(key=lambda x: x['premier'], reverse=True)
    rows.sort(key=lambda x: NIVEAUX.get(x['niveau'], ('', 9))[1])
    return rows


def constat(desk, cle):
    if not _table(desk):
        return None
    row = desk.db.execute('SELECT * FROM secu56101_constats WHERE cle=?', (str(cle)[:300],)).fetchone()
    return _ligne(row) if row else None


def etat(desk, now=None):
    """État général (S-U2) : couleur, derniers passages de chaque minuteur, veille arrêtée ou désactivée, contrôles en erreur."""
    maintenant = _now(now)
    out = {'disponible': disponible(), 'couleur': 'gris', 'rapide': '', 'complet': '', 'arretee': False, 'desactivee': False,
           'demande': '', 'jamais': True, 'erreurs': [], 'critiques': 0, 'importants': 0, 'surveiller': 0}
    try:
        d = dossier_rapports(desk)
        out['desactivee'] = (d / 'desactivee').is_file()
        out['installee'] = d.is_dir()
        if (dossier_demandes(desk) / 'demande.json').is_file():
            out['demande'] = 'en_attente'
    except OSError:
        out['installee'] = False
    if not _table(desk):
        return out
    for passage in ('rapide', 'complet'):
        row = desk.db.execute('SELECT fin, erreurs FROM secu56101_rapports WHERE passage=? ORDER BY fin DESC LIMIT 1', (passage,)).fetchone()
        if row:
            out[passage] = row['fin']
            out['erreurs'] += [x for x in json.loads(row['erreurs'] or '[]') if x not in out['erreurs']]
    dernier = max([_instant(out[p]) for p in ('rapide', 'complet') if out[p]] or [None], key=lambda x: x or datetime.min.replace(tzinfo=timezone.utc))
    out['jamais'] = dernier is None
    out['arretee'] = dernier is None or (maintenant - dernier).total_seconds() > ARRET_MINUTES * 60
    for x in constats(desk):
        out[{'critique': 'critiques', 'important': 'importants', 'surveiller': 'surveiller'}[x['niveau']]] += 1
    if not out['jamais']:
        out['couleur'] = 'rouge' if out['critiques'] else ('orange' if out['importants'] else 'vert')
    return out


def historique(desk, now=None, jours=HISTORIQUE_JOURS, fuseau=None):
    """Passages des 30 derniers jours, regroupés par jour du cabinet (le plus récent d'abord)."""
    if not _table(desk):
        return []
    tz = fuseau or _fuseau(desk)
    borne = _iso(_now(now) - timedelta(days=jours))
    jours_vus = {}
    for r in desk.db.execute('SELECT * FROM secu56101_rapports WHERE fin>=? ORDER BY fin', (borne,)):
        fin = _instant(r['fin'])
        cle = fin.astimezone(tz).date().isoformat()
        j = jours_vus.setdefault(cle, {'jour': cle, 'passages': 0, 'critiques': 0, 'importants': 0, 'surveiller': 0, 'charges': [], 'erreurs': 0})
        j['passages'] += 1
        j['critiques'] = max(j['critiques'], r['critiques'])
        j['importants'] = max(j['importants'], r['importants'])
        j['surveiller'] = max(j['surveiller'], r['surveiller'])
        j['erreurs'] += 1 if json.loads(r['erreurs'] or '[]') else 0
        if r['charge'] is not None:
            j['charges'].append(float(r['charge']))
    out = []
    for j in sorted(jours_vus.values(), key=lambda x: x['jour'], reverse=True):
        charges = j.pop('charges')
        j['charge'] = round(sum(charges) / len(charges), 2) if charges else None
        out.append(j)
    return out


def resume_hebdomadaire(desk, now=None):
    """S14 : charge moyenne, constats ouverts, nouveaux et résolus sur les sept derniers jours, constats « À surveiller »."""
    out = {'charge': None, 'coeurs': 0, 'ouverts': 0, 'nouveaux': 0, 'resolus': 0, 'surveiller': [], 'passages': 0}
    if not _table(desk):
        return out
    borne = _iso(_now(now) - timedelta(days=7))
    row = desk.db.execute('SELECT AVG(charge), MAX(coeurs), COUNT(*) FROM secu56101_rapports WHERE fin>=?', (borne,)).fetchone()
    out['charge'] = round(row[0], 2) if row[0] is not None else None
    out['coeurs'], out['passages'] = int(row[1] or 0), int(row[2] or 0)
    out['ouverts'] = desk.db.execute("SELECT COUNT(*) FROM secu56101_constats WHERE etat='ouvert'").fetchone()[0]
    out['nouveaux'] = desk.db.execute('SELECT COUNT(*) FROM secu56101_constats WHERE premier>=?', (borne,)).fetchone()[0]
    out['resolus'] = desk.db.execute("SELECT COUNT(*) FROM secu56101_constats WHERE etat='resolu' AND resolu>=?", (borne,)).fetchone()[0]
    out['surveiller'] = [x['titre'] for x in constats(desk) if x['niveau'] == 'surveiller'][:20]
    return out


def _fuseau(desk):
    try:
        from .cockpit530 import tz
        return tz(desk)
    except Exception:   # noqa: BLE001
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo('Europe/Paris')
        except Exception:   # noqa: BLE001
            return timezone(timedelta(hours=1))


def heure_locale(desk, iso):
    dt = _instant(iso)
    if dt is None:
        return 'jamais'
    return dt.astimezone(_fuseau(desk)).strftime('%d/%m/%Y à %H h %M')


# ------------------------------------------------------------------------------------------------------------ écritures (POST)
def _admin(role):
    return role == 'administrateur'


def _cle_idem(desk, cle, action, cible):
    """Clé d'idempotence : même clé, même action → même résultat ; même clé pour une autre action → refus (409)."""
    cle = str(cle or '')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,80}', cle):
        raise Stop('cle_requise')
    row = desk.db.execute('SELECT action, cible, resultat FROM secu56101_cles WHERE cle=?', (cle,)).fetchone()
    if row is None:
        return cle, None
    if row['action'] != action or row['cible'] != cible:
        raise Stop('cle_reutilisee')
    return cle, json.loads(row['resultat'])


def _retenir(desk, cle, action, cible, resultat):
    desk.db.execute('INSERT OR REPLACE INTO secu56101_cles VALUES(?,?,?,?,?)', (cle, action, cible, json.dumps(resultat, ensure_ascii=False), _iso(_now())))


def marquer_connu(desk, owner, role, cle, empreinte, motif, idem):
    """« Marquer comme connu » : motif obligatoire, sur l'empreinte affichée (409 si le constat a changé), inscrit au journal d'audit.
    Le constat est masqué tant que son empreinte ne change pas ; il revient (et peut de nouveau alerter) si elle change."""
    if not _admin(role):
        raise Stop('introuvable')
    motif = _texte(motif, MOTIF_MAX)
    courant = constat(desk, cle)
    if courant is None or courant['etat'] != 'ouvert':
        raise Stop('introuvable')
    cle_idem, deja = _cle_idem(desk, idem, 'connu', courant['cle'])
    if deja is not None:
        return deja
    if len(motif) < 3:
        raise Stop('motif_requis')
    if str(empreinte or '') != courant['empreinte']:
        raise Stop('version_perimee')
    desk.db.execute('INSERT OR REPLACE INTO secu56101_accuses VALUES(?,?,?,?,?)', (courant['cle'], courant['empreinte'], motif, str(owner)[:200], _iso(_now())))
    resultat = {'cle': courant['cle'], 'etat': 'connu', 'message': 'Constat marqué comme connu. Il reviendra si ce qui a été vu change.'}
    _retenir(desk, cle_idem, 'connu', courant['cle'], resultat)
    desk.db.commit()
    desk.audit('securite_serveur_connu', {'cle': courant['cle'], 'controle': courant['controle'], 'niveau': courant['niveau'],
                                          'empreinte': courant['empreinte'][:16], 'motif': motif, 'by': str(owner)[:200]})
    return resultat


def _texte_fiche(desk, mesures, owner, maintenant):
    tz = _fuseau(desk)
    ouverts = constats(desk) + constats(desk, connus=True)
    lignes = ['FICHE D’INCIDENT — BROUILLON (registre des violations de données, article 33.5 du RGPD)',
              'Préparée le %s par AxiorHub à la demande de %s. Document interne : jamais envoyé ; à relire, compléter et conserver.' % (
                  maintenant.astimezone(tz).strftime('%d/%m/%Y à %H h %M'), owner), '',
              '1. Chronologie (tirée des rapports de la veille de sécurité)']
    evenements = sorted(ouverts, key=lambda x: x['premier'])
    for x in evenements[:40]:
        lignes.append('- %s : %s (%s, contrôle %s)' % (heure_locale(desk, x['premier']), x['titre'], x['libelle'], x['controle']))
    if not evenements:
        lignes.append('- Aucun constat ouvert au moment de la préparation.')
    resolus = desk.db.execute("SELECT * FROM secu56101_constats WHERE etat='resolu' ORDER BY resolu DESC LIMIT 20").fetchall()
    for r in resolus:
        lignes.append('- %s : %s — n’est plus observé (résolu)' % (heure_locale(desk, r['resolu']), r['titre']))
    lignes += ['', '2. Constats']
    for x in evenements[:40]:
        lignes.append('- [%s] %s' % (x['libelle'], x['titre']))
        for v in x['vu'][:8]:
            lignes.append('    · %s' % v)
    lignes += ['', '3. Mesures déjà prises (cochées par l’administrateur)']
    choisies = [libelle for code, libelle in MESURES if code in mesures]
    lignes += ['- ' + m for m in choisies] or ['- Aucune mesure cochée.']
    lignes += ['', '4. À compléter par le cabinet',
               '- Nature des données et catégories de personnes concernées :',
               '- Nombre approximatif de personnes et d’enregistrements :',
               '- Conséquences probables :',
               '- Notification à la CNIL (dans les 72 heures si un risque existe) : oui / non, motif :',
               '- Information des personnes concernées : oui / non, motif :',
               '', 'Brouillon préparé par AxiorHub ; aucun envoi n’a été fait.']
    return '\n'.join(lignes)


def preparer_fiche(desk, owner, role, mesures, idem, now=None):
    """S-U4 : brouillon de fiche d'incident, déposé dans la file « À décider / À relire » des administrateurs. Jamais envoyé."""
    if not _admin(role):
        raise Stop('introuvable')
    mesures = sorted({str(m) for m in (mesures if isinstance(mesures, list) else []) if str(m) in dict(MESURES)})
    cle_idem, deja = _cle_idem(desk, idem, 'fiche', ','.join(mesures))
    if deja is not None:
        return deja
    maintenant = _now(now)
    texte = _texte_fiche(desk, mesures, str(owner)[:200], maintenant)
    ident = hashlib.sha256((cle_idem + texte).encode()).hexdigest()[:20]
    desk.db.execute("INSERT OR REPLACE INTO secu56101_fiches VALUES(?,?,?,?,?,'brouillon')",
                    (ident, _iso(maintenant), str(owner)[:200], texte, json.dumps(mesures)))
    resultat = {'id': ident, 'etat': 'brouillon', 'message': 'Brouillon de fiche d’incident déposé dans « À décider / À relire ». Rien n’est envoyé.'}
    _retenir(desk, cle_idem, 'fiche', ','.join(mesures), resultat)
    desk.db.commit()
    desk.audit('securite_serveur_fiche', {'fiche': ident, 'mesures': mesures, 'by': str(owner)[:200]})
    return resultat


def fiche(desk, ident):
    if not _table(desk):
        return None
    row = desk.db.execute('SELECT * FROM secu56101_fiches WHERE id=?', (str(ident)[:40],)).fetchone()
    return dict(row) if row else None


def demander_veille(desk, owner, role, action, idem):
    """S-A4 : dépose une demande (activer / désactiver les minuteurs) lue par le service privilégié ``axiorhub-veille-securite-demande``.
    AxiorHub n'appelle jamais ``systemctl`` lui-même."""
    if not _admin(role) or not disponible():
        raise Stop('introuvable')
    if action not in ('desactiver', 'activer'):
        raise Stop('action_invalide')
    cle_idem, deja = _cle_idem(desk, idem, 'veille', action)
    if deja is not None:
        return deja
    d = dossier_demandes(desk)
    if not d.is_dir():
        raise Stop('veille_non_installee')
    data = {'action': action, 'id': cle_idem, 'par': str(owner)[:120], 'at': _iso(_now())}
    tmp = d / ('.demande-%s.json' % cle_idem.replace(':', '_'))
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o640)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(json.dumps(data, ensure_ascii=False))
    os.replace(str(tmp), str(d / 'demande.json'))
    resultat = {'action': action, 'etat': 'demande_deposee',
                'message': 'Demande déposée : le service système l’applique dans quelques secondes.' if action == 'desactiver' else
                'Demande de réactivation déposée : le service système relance les minuteurs dans quelques secondes.'}
    _retenir(desk, cle_idem, 'veille', action, resultat)
    desk.db.commit()
    desk.audit('securite_serveur_veille', {'action': action, 'by': str(owner)[:200]})
    return resultat


# ------------------------------------------------------------------------------------------------------------ Aujourd'hui
def queue_items(desk, prefix='', actor='cabinet', role='administrateur'):
    """File « À décider / À relire » (``file5651._global_items``) : constats importants non connus, brouillons de fiche d'incident.
    Administrateurs seulement, version serveur seulement."""
    if not _admin(role) or not disponible() or not _table(desk):
        return []
    out = []
    for x in constats(desk):
        if x['niveau'] != 'important':
            continue
        ident = hashlib.sha256(x['cle'].encode()).hexdigest()[:16]
        out.append({'class': 'securite_serveur', 'object': 'securite-serveur:' + ident, 'revision': x['empreinte'][:16], 'matter': '',
                    'matter_name': 'Serveur du cabinet', 'title': x['titre'], 'when': x['premier'],
                    'consequence': _texte(x['explication'], 240) + ' Commandes de correction sur la page « Sécurité du serveur ».',
                    'href': prefix + '/securite-serveur#c-' + ident, 'choices': ('ouvrir', 'reporter'), 'priority': 'normale', 'priority_reason': ''})
    for r in desk.db.execute("SELECT id, cree FROM secu56101_fiches WHERE etat='brouillon' ORDER BY cree DESC LIMIT 5"):
        out.append({'class': 'fiche_incident', 'object': 'fiche-incident:' + r['id'], 'revision': r['id'][:16], 'matter': '',
                    'matter_name': 'Serveur du cabinet', 'title': 'Fiche d’incident (brouillon du %s)' % heure_locale(desk, r['cree']), 'when': r['cree'],
                    'consequence': 'Brouillon pour le registre des violations (art. 33.5 RGPD), à relire et compléter. Jamais envoyé.',
                    'href': prefix + '/securite-serveur?fiche=' + r['id'] + '#fiche', 'choices': ('ouvrir', 'relu', 'reporter'),
                    'priority': 'normale', 'priority_reason': ''})
    return out


def bandeau_html(desk, prefix, owner, role):
    """Contrat « Sécurité → Aujourd'hui » : bandeau rouge sous l'en-tête d'Aujourd'hui (administrateurs), chaîne vide sinon.
    Lecture seule et rapide : une requête sur la table déjà remplie par la maintenance ; aucune analyse au rendu."""
    try:
        if not _admin(role) or not disponible() or not _table(desk):
            return ''
        n = desk.db.execute("SELECT COUNT(*) FROM secu56101_constats c WHERE c.etat='ouvert' AND c.niveau='critique' AND NOT %s" % _connu_sql()).fetchone()[0]
    except Exception:   # noqa: BLE001 — Aujourd'hui ne doit jamais tomber à cause du bandeau
        return ''
    if not n:
        return ''
    return ('<div class="s101-bandeau" role="alert" data-s101-bandeau><strong>Alerte de sécurité du serveur</strong> '
            '<span>%d constat%s critique%s à examiner sans attendre. Rien n’a été modifié sur le serveur : les commandes de correction sont à copier.</span> '
            '<a class="ax-btn" href="%s/securite-serveur">Voir la sécurité du serveur</a></div>') % (
        n, 's' if n > 1 else '', 's' if n > 1 else '', e(prefix, quote=True))
