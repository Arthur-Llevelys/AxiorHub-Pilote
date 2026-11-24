"""5.6.109 (lot R) : base illisible : arrêt propre et lisible, diagnostic et restauration guidée.

Incident sur un serveur de production en 5.6.108 : ``desk.sqlite3`` endommagée (« database disk image is malformed ») dix minutes après la
sauvegarde de nuit. Ensuite, chaque ouverture de ``Desk`` levait une erreur ; l'interface répondait 500 sur toutes les pages, le worker redémarrait
en boucle (plus de dix mille fois en une journée), la veille aussi, et rien de lisible n'était dit à l'avocat.

Ce module, sans dépendance :

- reconnaît une base ILLISIBLE (``SQLITE_CORRUPT``, ``SQLITE_NOTADB`` : « malformed », « not a database ») et la distingue d'une base OCCUPÉE ;
- écrit une alerte hors base, dans le dossier d'état (``base-illisible56109.json``) : le diagnostic la montre même quand la base ne s'ouvre plus.
  Tant que le fichier de la base est le même (inode, taille, date), les services ne le rouvrent plus (aucune écriture) ; dès qu'il a été remplacé
  par une copie saine, l'ouverture reprend et l'alerte est marquée levée (jamais supprimée) ;
- rend la page d'arrêt (503) de l'interface et l'arrêt des workers, de la veille et de ``manage.py`` : un message unique, un code de sortie 78
  (``EX_CONFIG``) et un délai croissant avant la sortie quand l'arrêt se répète (15 s, 30 s… 15 min) : plus de boucle de redémarrage serrée ;
- ne tente JAMAIS de réparation automatique et ne supprime JAMAIS un fichier ;
- outil en ligne de commande (lecture seule par défaut) : ``python3 -m agent.secours56109 [--config FICHIER] diagnostic | sauvegardes | archives
  | preparer-remise BASE DOSSIER_EXTRAIT [--services-arretes --executer] | lever``. Procédure : docs/REVERSIBILITE-5.6.88.md, « Base illisible ».

Règle du lot R (voir ``tests/test_v56109_secours.py``, garde statique) : un fichier SQLite vivant n'est jamais ouvert par ``open()`` dans un processus
qui peut l'avoir ouvert par SQLite ; ce module ne lit les bases que par ``sqlite3`` en lecture seule (``mode=ro``). La seule copie brute est celle
d'une base EXTRAITE d'une archive (fichier hors service) ; la base endommagée est mise de côté par renommage, jamais ouverte ni écrasée.
"""
from datetime import datetime, timezone
from html import escape as e
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time
import uuid

ALERTE = 'base-illisible56109.json'
CODE_SORTIE = 78                     # EX_CONFIG : à déclarer dans RestartPreventExitStatus= des unités (voir le compte rendu du lot R)
ATTENTE_BASE, ATTENTE_MAX = 15, 900
SUFFIXES = ('.sqlite3', '.sqlite', '.db')
MARQUES = ('malformed', 'not a database', 'file is encrypted', 'database corrupt', 'database disk image')
CODES = ('SQLITE_CORRUPT', 'SQLITE_NOTADB')
NOM_SAUVEGARDE = re.compile(r'^axiorhub-(\d{8})-(\d{4})-[0-9a-f]{12}\.axbk$')
DOC = 'docs/REVERSIBILITE-5.6.88.md'

MESSAGE = ('AxiorHub est arrêté par sécurité : la base de travail du bureau (desk.sqlite3) ne peut plus être lue correctement. Aucune écriture '
           'n’est faite et aucune réparation automatique n’est tentée ; les documents et les courriels ne sont pas touchés. Marche à suivre : '
           'docs/REVERSIBILITE-5.6.88.md, section « Base illisible » ; diagnostic en lecture seule : python3 -m agent.secours56109.')


class BaseIllisible(sqlite3.DatabaseError):
    """Arrêt typé : la base ``base`` du dossier d'état est endommagée. Sous-classe de ``sqlite3.DatabaseError`` : le code existant qui traite les
    erreurs de base la voit comme telle ; ce n'est pas un ``Stop`` (un ``Stop`` deviendrait un refus « Action non effectuée » sur une page)."""

    def __init__(self, base='desk.sqlite3', motif='base_illisible'):
        super().__init__('base_illisible')
        self.base, self.motif = base, motif


# ------------------------------------------------------------------------------------------------ reconnaissance
def illisible(error):
    """Vrai pour une base endommagée (corruption, fichier qui n'est pas une base), en suivant la cause ; faux pour une base occupée ou absente."""
    vus = 0
    while error is not None and vus < 6:
        if isinstance(error, BaseIllisible):
            return True
        if isinstance(error, sqlite3.DatabaseError):
            nom = str(getattr(error, 'sqlite_errorname', '') or '')
            texte = str(error).lower()
            if nom in CODES or (not isinstance(error, sqlite3.OperationalError) and any(m in texte for m in MARQUES)):
                return True
            if isinstance(error, sqlite3.OperationalError) and any(m in texte for m in ('malformed', 'not a database')):
                return True
        error, vus = error.__cause__ or error.__context__, vus + 1
    return False


def _maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def empreinte(chemin):
    """Identité du fichier (inode, taille, date de modification) : ``os.stat`` seulement, le fichier n'est pas ouvert."""
    try:
        st = os.stat(chemin)
    except OSError:
        return None
    return {'ino': int(st.st_ino), 'taille': int(st.st_size), 'mtime_ns': int(getattr(st, 'st_mtime_ns', int(st.st_mtime * 1e9)))}


def _lire(etat):
    try:
        data = json.loads((Path(etat) / ALERTE).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _ecrire(etat, data):
    """Écriture atomique de l'alerte (fichier temporaire propre puis remplacement) ; seul ce fichier d'alerte est remplacé."""
    cible = Path(etat) / ALERTE
    temp = cible.with_name('.%s.%s.tmp' % (ALERTE, uuid.uuid4().hex[:12]))
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    os.replace(temp, cible)


def alerte(etat, base='desk.sqlite3'):
    """Alerte active pour CE fichier de base (même identité), sinon ``None``. Un fichier remplacé depuis n'est plus visé."""
    data = _lire(etat)
    if not data or data.get('etat') != 'active' or data.get('base') != base:
        return None
    return data if data.get('empreinte') == empreinte(Path(etat) / base) else None


def noter(etat, base, motif, service):
    """Alerte écrite (ou complétée) hors base ; rend le nombre d'arrêts de ``service`` pour ce fichier (délai croissant)."""
    data = alerte(etat, base) or {'etat': 'active', 'base': base, 'depuis': _maintenant(), 'motif': motif, 'services': {},
                                   'empreinte': empreinte(Path(etat) / base), 'message': MESSAGE}
    data['derniere'] = _maintenant()
    data['services'][service] = int(data['services'].get(service, 0)) + 1
    try:
        _ecrire(etat, data)
    except OSError:   # dossier en lecture seule : l'arrêt a lieu quand même, le message suffit
        pass
    return data['services'][service]


def lever(etat, base='desk.sqlite3', par='ouverture_reussie'):
    """Après le remplacement de la base par une copie saine : l'alerte est marquée levée et conservée (trace), jamais supprimée."""
    data = _lire(etat)
    if not data or data.get('etat') != 'active' or data.get('base') != base:
        return False
    if data.get('empreinte') == empreinte(Path(etat) / base):
        return False          # même fichier : toujours endommagé
    data.update(etat='levee', levee_le=_maintenant(), levee_par=par, empreinte_remplacante=empreinte(Path(etat) / base))
    try:
        _ecrire(etat, data)
    except OSError:
        return False
    return True


# ------------------------------------------------------------------------------------------------ points de montage dans le produit
def garde_desk(c):
    """``Desk._open`` : base déjà reconnue illisible (même fichier) : arrêt immédiat, sans rouvrir SQLite (aucune écriture, aucun point de contrôle)."""
    etat = (c or {}).get('state_dir') if isinstance(c, dict) else None
    if etat and os.path.exists(os.path.join(str(etat), ALERTE)) and alerte(etat):
        raise BaseIllisible('desk.sqlite3', 'alerte_active')


def declarer(c, error, service=None):
    """``Desk._prepare_waiting`` : convertit une erreur de base endommagée en arrêt typé et écrit l'alerte. ``None`` si l'erreur est d'une autre nature."""
    if not illisible(error):
        return None
    if isinstance(error, BaseIllisible) and error.motif == 'alerte_active':
        return error
    etat = (c or {}).get('state_dir') if isinstance(c, dict) else None
    if etat:
        noter(etat, 'desk.sqlite3', _motif(error), service or _service())
    return error if isinstance(error, BaseIllisible) else BaseIllisible('desk.sqlite3', _motif(error))


def lever_si_active(c):
    etat = (c or {}).get('state_dir') if isinstance(c, dict) else None
    if etat and os.path.exists(os.path.join(str(etat), ALERTE)):
        lever(etat)


def _motif(error):
    while error is not None:
        if isinstance(error, BaseIllisible):
            return error.motif
        texte = str(error).lower()
        if 'not a database' in texte or str(getattr(error, 'sqlite_errorname', '')) == 'SQLITE_NOTADB':
            return 'fichier_non_reconnu'
        if 'malformed' in texte or str(getattr(error, 'sqlite_errorname', '')) == 'SQLITE_CORRUPT':
            return 'base_endommagee'
        error = error.__cause__ or error.__context__
    return 'base_illisible'


def _service():
    argv = [a for a in sys.argv[1:] if not a.startswith('-')]
    return (argv[0] if argv else Path(sys.argv[0] or 'axiorhub').name)[:40]


def delai(n, maximum=None):
    """Attente avant la sortie du n-ième arrêt successif d'un service : aucune au premier, puis 15 s, 30 s, 60 s… jusqu'à 15 min."""
    if maximum is None:
        try:
            maximum = float(os.environ.get('AXIORHUB_SECOURS_ATTENTE_MAX', ATTENTE_MAX))
        except ValueError:
            maximum = ATTENTE_MAX
    return 0.0 if n <= 1 else float(min(maximum, ATTENTE_BASE * 2 ** min(n - 2, 10)))


def _etat_de_config(config):
    if isinstance(config, dict):
        return config.get('state_dir')
    try:
        return json.loads(Path(config).read_text(encoding='utf-8')).get('state_dir')
    except (OSError, ValueError, TypeError):
        return None


def arret_service(config, service, error, sortir=None, attendre=time.sleep, flux=None):
    """Worker, veille, liaison, ``manage.py`` : base illisible : alerte, message unique, délai croissant, code 78. Rend la main sans rien faire
    si l'erreur est d'une autre nature (l'appelant la relève alors comme avant)."""
    if not illisible(error):
        return None
    etat = _etat_de_config(config)
    n = noter(etat, 'desk.sqlite3', _motif(error), service) if etat else 1
    flux = flux or sys.stderr or sys.__stderr__
    pause = delai(n)
    try:
        if n == 1:
            print('ARRÊT (base illisible) : ' + MESSAGE, file=flux, flush=True)
        else:
            print('ARRÊT (base illisible) : toujours illisible (arrêt n° %d de « %s ») ; sortie dans %d s, sans nouvelle tentative d’ici là.'
                  % (n, service, pause), file=flux, flush=True)
    except (OSError, ValueError, AttributeError):
        pass
    if pause:
        attendre(pause)
    if sortir is None:
        try:
            sys.stdout.flush()
        except (OSError, ValueError, AttributeError):
            pass
        os._exit(CODE_SORTIE)     # fils de service non démons (voies rapides) : la sortie ne les attend pas ; SQLite supporte l'arrêt brutal
    return sortir(CODE_SORTIE)


def garde_ligne_de_commande(c):
    """``manage.py`` : avant toute commande qui ouvre la base : alerte active, ou sonde en lecture seule qui voit une base endommagée."""
    etat = (c or {}).get('state_dir')
    if not etat:
        return
    if alerte(etat):
        raise BaseIllisible('desk.sqlite3', 'alerte_active')
    verdict = sonde(Path(etat) / 'desk.sqlite3')
    if verdict['etat'] == 'illisible':
        noter(etat, 'desk.sqlite3', verdict['motif'], 'manage')
        raise BaseIllisible('desk.sqlite3', verdict['motif'])


# ------------------------------------------------------------------------------------------------ page d'arrêt de l'interface (503)
TITRE = 'AxiorHub est arrêté par sécurité'
TEXTE = ('La base de travail du bureau ne peut plus être lue correctement. Pour ne rien abîmer, AxiorHub a arrêté ses traitements : aucune '
         'écriture n’est faite et aucune réparation automatique n’est tentée.')
INTACTS = ('Vos documents et vos dossiers, dans leur stockage habituel.', 'Vos courriels, dans votre messagerie.',
           'Les archives de sauvegarde déjà faites.')
ETAPES = ('Prévenez la personne qui administre le serveur du cabinet.',
          'Elle suit la procédure « Base illisible » du guide de réversibilité (docs/REVERSIBILITE-5.6.88.md) : arrêter les services, mettre de côté '
          'une copie du dossier d’état, contrôler la dernière archive, remettre en place la seule base endommagée, redémarrer.',
          'Cette page revient d’elle-même à la normale dès que la base a été remise en place.')


def page_arret(env, prefix=None):
    from .erreurs_pages5639 import guess_prefix, incident_reference, page
    prefix = guess_prefix(env or {}) if prefix is None else prefix
    extra = ('<h2>Ce qui n’est pas touché</h2><ul>%s</ul><h2>Que faire</h2><ol>%s</ol>'
             '<p class="axe-hint">Diagnostic en lecture seule, sur le serveur :</p><p><code>python3 -m agent.secours56109</code></p>') % (
        ''.join('<li>%s</li>' % e(t) for t in INTACTS), ''.join('<li>%s</li>' % e(t) for t in ETAPES))
    return page(env or {}, '503 Service Unavailable', TITRE, TEXTE, prefix, tone='error', extra_html=extra, reference=incident_reference(),
                reload_label='Recharger la page')


def reponse_web(env, kind, config_path='', prefix=None, error=None):
    """``web.internal_error_response`` : (statut, type, corps) de la page d'arrêt si l'erreur vient d'une base illisible, sinon ``None``."""
    error = error if error is not None else sys.exc_info()[1]
    if not illisible(error):
        return None
    etat = _etat_de_config(config_path) if config_path else None
    if etat and not alerte(etat):
        noter(etat, 'desk.sqlite3', _motif(error), 'interface')
    try:
        print('AxiorHub : base illisible : page d’arrêt servie (%s).' % str(env.get('PATH_INFO', ''))[:120], file=sys.stderr or sys.__stderr__, flush=True)
    except (OSError, ValueError, AttributeError):
        pass
    from .web import wants_json
    if wants_json(env, kind):
        return ('503 Service Unavailable', 'application/json; charset=utf-8',
                json.dumps({'error': 'base_illisible', 'message': TITRE + ' : ' + TEXTE}, ensure_ascii=False))
    return '503 Service Unavailable', 'text/html; charset=utf-8', page_arret(env, prefix)


# ------------------------------------------------------------------------------------------------ contrôles en lecture seule
def _ro(chemin, timeout=5):
    return sqlite3.connect(Path(chemin).resolve().as_uri() + '?mode=ro', uri=True, timeout=timeout)


def sonde(chemin):
    """Ouverture en lecture seule et lecture du schéma : 'ok', 'illisible' (endommagée), 'absente' ou 'indisponible' (occupée, droits)."""
    chemin = Path(chemin)
    if not chemin.is_file():
        return {'etat': 'absente', 'motif': ''}
    try:
        db = _ro(chemin)
        try:
            db.execute('PRAGMA schema_version').fetchone()
            db.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()
        finally:
            db.close()
    except sqlite3.Error as error:
        return {'etat': 'illisible' if illisible(error) else 'indisponible', 'motif': _motif(error) if illisible(error) else str(error)[:120]}
    return {'etat': 'ok', 'motif': ''}


def controle(chemin, complet=False):
    """``quick_check`` (ou ``integrity_check`` avec ``complet``) en lecture seule. Rien n'est écrit, rien n'est réparé."""
    verdict = sonde(chemin)
    if verdict['etat'] != 'ok':
        return {**verdict, 'integre': False}
    try:
        db = _ro(chemin, 30)
        try:
            lignes = [r[0] for r in db.execute('PRAGMA %s(5)' % ('integrity_check' if complet else 'quick_check'))]
        finally:
            db.close()
    except sqlite3.Error as error:
        return {'etat': 'illisible' if illisible(error) else 'indisponible', 'motif': _motif(error), 'integre': False}
    return {'etat': 'ok' if lignes == ['ok'] else 'illisible', 'motif': '' if lignes == ['ok'] else 'controle_non_satisfait', 'integre': lignes == ['ok'],
            'details': [] if lignes == ['ok'] else lignes[:5]}


def bases(etat):
    """Bases du dossier d'état (premier niveau et ``auth``), reconnues par leur nom."""
    etat = Path(etat)
    trouvees = [p for p in sorted(etat.iterdir()) if p.is_file() and p.name.lower().endswith(SUFFIXES)] if etat.is_dir() else []
    auth = etat / 'auth'
    if auth.is_dir():
        trouvees += [p for p in sorted(auth.iterdir()) if p.is_file() and p.name.lower().endswith(SUFFIXES)]
    return trouvees


def diagnostic(etat, complet=False):
    etat = Path(etat)
    out = {'dossier_etat': str(etat), 'alerte': _lire(etat), 'bases': []}
    for chemin in bases(etat):
        verdict = controle(chemin, complet)
        out['bases'].append({'base': chemin.relative_to(etat).as_posix(), 'taille': chemin.stat().st_size,
                             'journal_wal': (Path(str(chemin) + '-wal').stat().st_size if Path(str(chemin) + '-wal').exists() else None), **verdict})
    return out


def _reglage(etat, cle, defaut=None):
    try:
        db = _ro(Path(etat) / 'desk.sqlite3')
        try:
            row = db.execute('SELECT value FROM settings WHERE key=?', (cle,)).fetchone()
        finally:
            db.close()
        return json.loads(row[0]) if row else defaut
    except (sqlite3.Error, ValueError, TypeError):
        return defaut


def archives(etat, dossier=None):
    """Archives de sauvegarde présentes (nom exact d'AxiorHub), de la plus récente à la plus ancienne. Aucune n'est ouverte."""
    if dossier is None:
        cfg = _reglage(etat, 'sauvegarde5688:config', {}) or {}
        dossier = cfg.get('dossier') or str(Path(etat) / 'sauvegardes5688')
    dossier = Path(dossier)
    if not dossier.is_dir():
        return {'dossier': str(dossier), 'archives': [], 'lisible': False}
    rows = []
    for p in dossier.iterdir():
        m = NOM_SAUVEGARDE.match(p.name)
        if m and p.is_file() and not p.is_symlink():
            rows.append({'nom': p.name, 'taille': p.stat().st_size, 'date_utc': '%s-%s-%s %s:%s' % (m[1][:4], m[1][4:6], m[1][6:], m[2][:2], m[2][2:])})
    return {'dossier': str(dossier), 'archives': sorted(rows, key=lambda r: r['nom'], reverse=True), 'lisible': True}


def sauvegardes(etat, limite=40):
    """Historique des sauvegardes lu dans la base (lecture seule) : pourquoi une nuit n'a pas abouti (statut, motif, avertissements)."""
    out = {'reussite': _reglage(etat, 'sauvegarde5688:derniere_reussite', ''), 'dernier_essai': _reglage(etat, 'auto:sauvegarde5688', 0),
           'planification': _reglage(etat, 'sauvegarde5688:planification', None), 'config': _reglage(etat, 'sauvegarde5688:config', {}),
           'historique': [], 'travaux': []}
    try:
        db = _ro(Path(etat) / 'desk.sqlite3')
        db.row_factory = sqlite3.Row
        try:
            out['historique'] = [dict(r) for r in db.execute('SELECT debut,fin,declencheur,statut,nom,taille,fichiers,erreur,avertissements FROM sauvegardes5688 '
                                                             'ORDER BY debut DESC LIMIT ?', (int(limite),))]
            out['travaux'] = [dict(r) for r in db.execute("SELECT id,status,created,started,finished,attempts,result FROM jobs WHERE kind='sauvegarde5688' "
                                                          'ORDER BY id DESC LIMIT ?', (int(limite),))]
        finally:
            db.close()
    except sqlite3.Error as error:
        out['erreur'] = 'base_illisible' if illisible(error) else str(error)[:120]
    return out


# ------------------------------------------------------------------------------------------------ remise en place guidée
VERROUS_SERVICES = ('desk-worker.lock', 'desk-worker-2.lock', 'desk-worker-3.lock', 'desk-worker-4.lock', 'watch430.lock')


def services_actifs(etat):
    """Verrous tenus par un worker ou la veille (``flock`` exclusif) ; une liste vide ne prouve pas que l'interface est arrêtée."""
    from .portable import fcntl
    tenus = []
    for nom in VERROUS_SERVICES:
        chemin = Path(etat) / nom
        if not chemin.exists():
            continue
        try:
            with open(chemin, 'a') as verrou:      # fichier de verrou, pas une base
                try:
                    fcntl.flock(verrou, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(verrou, fcntl.LOCK_UN)
                except BlockingIOError:
                    tenus.append(nom)
        except OSError:
            continue
    return tenus


def preparer_remise(etat, base, extrait, services_arretes=False, executer=False):
    """Prépare (et, sur demande explicite, fait) la remise en place d'UNE base depuis une archive EXTRAITE (``python3 -m agent.sauvegarde5688
    extraire``). Jamais d'écrasement : la base actuelle et ses fichiers ``-wal``/``-shm`` sont MIS DE CÔTÉ par renommage dans un dossier daté
    (rien n'est supprimé), puis la copie contrôlée prend leur place. Services arrêtés exigés."""
    etat, extrait = Path(etat), Path(extrait)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+\.(sqlite3|sqlite|db)', str(base or '')):
        raise SystemExit('Nom de base invalide : ' + str(base))
    source = extrait / 'etat' / base
    if not source.is_file():
        source = extrait / base
    actuelle = etat / base
    plan = {'base': base, 'source': str(source), 'actuelle': str(actuelle), 'execute': False, 'etapes': []}
    if not source.is_file():
        plan['refus'] = 'base absente du dossier extrait (attendu : DOSSIER/etat/%s)' % base
        return plan
    verdict = controle(source, complet=True)
    plan['controle_source'] = verdict
    if not verdict.get('integre'):
        plan['refus'] = 'la copie extraite ne passe pas le contrôle d’intégrité : choisir une autre archive'
        return plan
    tenus = services_actifs(etat)
    plan['verrous_tenus'] = tenus
    cote = etat / ('secours56109-%s' % datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    deplaces = [p for p in (actuelle, Path(str(actuelle) + '-wal'), Path(str(actuelle) + '-shm'), Path(str(actuelle) + '-journal')) if p.exists()]
    plan['etapes'] = (['mettre de côté (renommage, sans ouverture) : %s → %s/' % (p.name, cote.name) for p in deplaces] +
                      ['copier %s → %s (nom provisoire, puis renommage)' % (source, actuelle), 'contrôle d’intégrité de la base remise en place'])
    if not executer:
        plan['a_faire'] = 'relancer avec --services-arretes --executer après avoir arrêté tous les services AxiorHub'
        return plan
    if not services_arretes or tenus:
        plan['refus'] = 'services encore actifs (verrous : %s) ou arrêt non confirmé (--services-arretes)' % (', '.join(tenus) or 'aucun')
        return plan
    if shutil.disk_usage(etat).free < source.stat().st_size + 256 * 1024 * 1024:
        plan['refus'] = 'place insuffisante dans le dossier d’état'
        return plan
    cote.mkdir(mode=0o700)
    for p in deplaces:
        os.rename(p, cote / p.name)               # même disque : renommage atomique, le fichier n'est ni ouvert ni recopié ni supprimé
    provisoire = etat / ('.%s.secours56109-%s' % (base, uuid.uuid4().hex[:8]))
    shutil.copyfile(source, provisoire)           # lot R : hors service — source extraite d'une archive, aucun service ne l'a ouverte
    os.chmod(provisoire, 0o600)
    if base == 'desk.sqlite3':
        plan['neutralisation'] = neutraliser_sauvegarde(provisoire)   # sur la COPIE, avant sa mise en place : aucun service ne l'a ouverte
    if actuelle.exists():                         # quelqu'un a recréé la base entre-temps : on n'écrase rien
        plan['refus'] = 'une base %s est réapparue pendant l’opération : rien n’a été remplacé (copie laissée en %s)' % (base, provisoire.name)
        return plan
    os.rename(provisoire, actuelle)
    plan['controle_final'] = controle(actuelle, complet=True)
    plan['mis_de_cote'] = str(cote)
    plan['execute'] = True
    if base == 'desk.sqlite3':
        lever(etat, base, 'secours56109_remise')
    return plan


def neutraliser_sauvegarde(chemin, now=None):
    """Base de bureau extraite d'une archive, AVANT sa mise en place (fichier qu'aucun service n'a ouvert) : la base d'une archive est copiée
    pendant le travail de sauvegarde et le porte « en cours ». On le clôt (« interrompue », motif ``base_restauree``), on annule tout travail de
    sauvegarde resté en file, on pose le repère de restauration, la garde (pas de sauvegarde automatique avant l'heure planifiée suivante) et la
    protection de l'archive d'origine. Écriture par sqlite3, jamais par open()."""
    from . import sauvegarde5688 as sv
    now = now or datetime.now(timezone.utc)
    db = sqlite3.connect(str(chemin), timeout=10)
    try:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'settings' not in tables:
            return {'fait': False, 'motif': 'table settings absente'}
        ids = [r[0] for r in db.execute("SELECT id FROM sauvegardes5688 WHERE statut='en_cours'")] if 'sauvegardes5688' in tables else []
        db.execute('BEGIN IMMEDIATE')
        if ids:
            db.executemany("UPDATE sauvegardes5688 SET statut='interrompue',fin=?,erreur='base_restauree' WHERE id=?", [(now.isoformat(), i) for i in ids])
        travaux = 0
        if 'jobs' in tables:
            travaux = db.execute("UPDATE jobs SET status='cancelled',finished=?,result=? WHERE kind='sauvegarde5688' "
                                 "AND status IN ('pending','running','cancel_requested','suspended')",
                                 (now.isoformat(), json.dumps({'message': 'Base remise en place depuis une archive : sauvegarde non relancée.'}, ensure_ascii=False))).rowcount
        row = db.execute('SELECT value FROM settings WHERE key=?', (sv.CONFIG,)).fetchone()
        cfg = {**sv.DEFAUTS, **(json.loads(row[0]) if row else {})}
        fuseau = db.execute("SELECT value FROM settings WHERE key='calendar:timezone'").fetchone()

        class _Lieu:     # heure planifiée suivante dans le fuseau du cabinet (configuration non disponible ici : Europe/Paris par défaut)
            c = {'calendar': {'timezone': json.loads(fuseau[0]) if fuseau else 'Europe/Paris'}}
        jusqu_a = sv.heure_suivante(_Lieu, now, cfg)
        db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (sv.RESTAURATION, json.dumps({'le': now.isoformat(timespec='seconds'), 'par': 'secours56109',
                                                                                                'archive': ids[-1] if ids else ''})))
        garde = sv.poser_garde(db, 'base_restauree', now, jusqu_a, ids[-1] if ids else '')
        db.commit()
        return {'fait': True, 'sauvegardes_closes': ids, 'travaux_annules': travaux, 'garde_jusqu_a': garde['jusqu_a']}
    except (sqlite3.Error, ValueError) as error:
        db.rollback()
        return {'fait': False, 'motif': str(error)[:120]}
    finally:
        db.close()


# ------------------------------------------------------------------------------------------------ ligne de commande
USAGE = ('Usage : python3 -m agent.secours56109 [--config FICHIER] [--etat DOSSIER] [diagnostic [--complet] | sauvegardes | archives [DOSSIER] | '
         'preparer-remise BASE DOSSIER_EXTRAIT [--services-arretes --executer] | lever]\nLecture seule sauf « preparer-remise … --executer » et « lever ».')


def _options(args):
    etat, config, reste = None, '/etc/axiorhub-mail-agent/config.json', []
    i = 0
    while i < len(args):
        if args[i] in ('--config', '--etat') and i + 1 < len(args):
            if args[i] == '--config':
                config = args[i + 1]
            else:
                etat = args[i + 1]
            i += 2
            continue
        reste.append(args[i])
        i += 1
    return etat or _etat_de_config(config), reste


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if any(a in ('-h', '--help') for a in args):
        print(USAGE)
        return 0
    etat, reste = _options(args)
    if not etat or not Path(etat).is_dir():
        print('Dossier d’état introuvable : indiquez --config ou --etat.\n' + USAGE, file=sys.stderr)
        return 2
    commande = reste[0] if reste else 'diagnostic'
    if commande == 'diagnostic':
        out = diagnostic(etat, '--complet' in reste)
        out['archives'] = archives(etat)
        out['services_actifs'] = services_actifs(etat)
        out['procedure'] = DOC + ', section « Base illisible »'
        code = 1 if any(b['etat'] == 'illisible' for b in out['bases']) or alerte(etat) else 0
    elif commande == 'sauvegardes':
        out, code = sauvegardes(etat), 0
    elif commande == 'archives':
        out, code = archives(etat, reste[1] if len(reste) > 1 else None), 0
    elif commande == 'preparer-remise' and len(reste) >= 3:
        out = preparer_remise(etat, reste[1], reste[2], '--services-arretes' in reste, '--executer' in reste)
        code = 1 if out.get('refus') else 0
    elif commande == 'lever':
        verdict = controle(Path(etat) / 'desk.sqlite3', complet=True)
        if not verdict.get('integre'):
            out, code = {'leve': False, 'motif': 'la base ne passe pas le contrôle d’intégrité', 'controle': verdict}, 1
        else:
            data = _lire(etat)
            if data and data.get('etat') == 'active':
                data.update(etat='levee', levee_le=_maintenant(), levee_par='secours56109_lever')
                _ecrire(etat, data)
            out, code = {'leve': True, 'controle': verdict}, 0
    else:
        print(USAGE, file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
