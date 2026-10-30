"""5.6.61 : avis de nouvelle version et mise à jour signée depuis la page des versions GitHub, sans ligne de commande.

- ``check`` lit, au plus toutes les 6 heures, le flux public des versions (pas l'API GitHub) et retient la dernière version publiée.
- Serveur Linux : ``request`` dépose la demande lue par le service système ``axiorhub-mail-agent-maj`` (installé et activé par
  ``install.sh`` et le paquet Debian) ; ce service revérifie tout lui-même (version, empreinte, signature) et installe.
- Windows 11 et Ubuntu poste : le travail ``maj_local5661`` télécharge l'installateur de la version, vérifie son empreinte et sa
  signature (``signature5661``), puis l'installe : Windows, installateur en mode silencieux dans le même dossier, avec les mêmes
  raccourcis, et relance ; Ubuntu poste, fenêtre de mot de passe du système (``pkexec``), à défaut l'installateur graphique d'Ubuntu.
- Par défaut, l'administrateur clique « Mettre à jour » ; il peut choisir « Installer automatiquement la nuit » (2 h – 5 h).
- Une version non signée par la clé livrée avec AxiorHub n'est jamais installée.
"""
from datetime import datetime, timedelta, timezone
from html import escape as e
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

from .common import Stop

FEED = 'https://github.com/Arthur-Llevelys/AxiorHub-Pilote/releases.atom'
RELEASES = 'https://github.com/Arthur-Llevelys/AxiorHub-Pilote/releases'
DOWNLOAD = 'https://github.com/Arthur-Llevelys/AxiorHub-Pilote/releases/download/v{v}/{name}'
ASSETS = {'windows': 'AxiorHub-Pilote-Setup-{v}.exe', 'poste': 'axiorhub-pilote-poste_{v}_amd64.deb'}
SCRIPT = Path('/opt/axiorhub-mail-agent/current/miseajour.py')
UNIT = Path('/etc/systemd/system/axiorhub-mail-agent-maj.path')
INTERVAL_HOURS = 6
NIGHT = (2, 5)
KEY = 'maj5661:etat'
AUTO = 'maj5661:automatique'
TRIED = 'maj5661:tentative_automatique'
UPDATE_PAUSE = 'maj5681:pause'
UPDATE_WAIT_SECONDS = 20
UPDATE_PAUSE_SECONDS = 45 * 60
VERSION_RX = re.compile(r'^\d+\.\d+\.\d+$')
MAX_BYTES = 400 * 1024 * 1024


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _tuple(v):
    try:
        return tuple(int(x) for x in str(v).split('.'))
    except ValueError:
        return ()


def installed_version():
    from . import __version__
    return __version__


def latest_from(feed_text):
    tags = re.findall(r'<title>v(\d+\.\d+\.\d+)</title>', feed_text or '')
    return max(tags, key=_tuple) if tags else ''


def platform():
    """Type d'installation : « serveur » (Linux, /opt), « windows » (application empaquetée), « poste » (Ubuntu), « autre »."""
    if os.name == 'nt' and getattr(sys, 'frozen', False):
        return 'windows'
    if Path(__file__).resolve().as_posix().startswith('/usr/lib/axiorhub-pilote/'):
        return 'poste'
    if os.name != 'nt' and SCRIPT.is_file():
        return 'serveur'
    return 'autre'


def check(desk, fetch=None, force=False):
    """Dernière version publiée (au plus une lecture du flux toutes les 6 h). Ne lève pas : une absence de réseau est notée."""
    state = desk.settings(KEY, {}) or {}
    last = state.get('checked') or ''
    if not force and last:
        try:
            if _now() - datetime.fromisoformat(last) < timedelta(hours=INTERVAL_HOURS):
                return state
        except ValueError:
            pass
    try:
        if fetch is None:
            from .common import HTTP
            raw = HTTP('https://github.com', timeout=15).request('GET', FEED, limit=2_000_000)
        else:
            raw = fetch(FEED)
        text = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else str(raw)
        state = {'latest': latest_from(text), 'checked': _now().isoformat(), 'error': ''}
    except Exception as ex:   # noqa: BLE001 — poste ou serveur sans accès Internet : avis simplement absent
        state = {**state, 'checked': _now().isoformat(), 'error': str(ex).split(':')[0][:80]}
    desk.setting(KEY, state)
    return state


def available(desk):
    state = desk.settings(KEY, {}) or {}
    latest = state.get('latest') or ''
    return latest if latest and _tuple(latest) > _tuple(installed_version()) else ''


def server_install():
    return platform() == 'serveur'


def activated():
    return UNIT.is_file()


def signer_ready():
    from .signature5661 import ready
    return ready()


def request_dir(desk):
    return Path(desk.c['state_dir']) / 'maj'


def last_result(desk):
    for name in ('resultat.json', 'resultat-local.json'):
        try:
            return json.loads((request_dir(desk) / name).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
    return None


def _write_local(desk, data):
    folder = request_dir(desk)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / ('.resultat-local-%d.json' % os.getpid())
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, folder / 'resultat-local.json')


def request(desk, owner, version):
    """Demande la mise à jour vers ``version`` (la dernière publiée) : demande au service système, ou travail local (poste)."""
    version = str(version or '')
    if not VERSION_RX.match(version) or version != available(desk):
        raise Stop('version_non_disponible')
    if not signer_ready():
        raise Stop('cle_de_signature_absente')
    kind = platform()
    if kind in ('windows', 'poste'):
        job = desk.enqueue('maj_local5661', {'version': version, 'par': str(owner)[:80]}, priority=5)
        desk.audit('maj5661_demandee', {'version': version, 'par': str(owner)[:80], 'plateforme': kind})
        pause_before_update(desk, version, keep=job)
        return {'ok': True, 'job': job, 'message': 'Mise à jour vers %s lancée : IA en pause, téléchargement et vérification, puis installation. L’IA reprendra seule.' % version}
    if kind != 'serveur' or not activated():
        raise Stop('mise_a_jour_automatique_non_activee')
    folder = request_dir(desk)
    if (folder / 'demande.json').exists():
        raise Stop('mise_a_jour_deja_demandee')
    pause_before_update(desk, version)
    tmp = folder / ('.demande-%s.json' % os.getpid())
    tmp.write_text(json.dumps({'version': version, 'par': str(owner)[:80], 'le': _now().isoformat()}), encoding='utf-8')
    os.replace(tmp, folder / 'demande.json')
    desk.audit('maj5661_demandee', {'version': version, 'par': str(owner)[:80], 'plateforme': kind})
    return {'ok': True, 'message': 'Mise à jour vers %s demandée : l’IA est mise en pause, puis AxiorHub sera indisponible une à deux minutes. '
                                   'L’IA reprendra seule.' % version}


def pause_before_update(desk, version, keep=None, sleep=None):
    """5.6.81 : met l'IA en pause avant d'installer : les travaux en cours s'arrêtent proprement et repartiront à la reprise, au lieu d'être coupés par
    l'arrêt des services. Attente bornée. Une pause déjà décidée par l'avocat est conservée : la mise à jour ne la lèvera pas."""
    import time
    from .arret5643 import DETERMINISTIC, global_paused, pause_all
    if global_paused(desk):
        return False
    pause_all(desk, True, 'mise-a-jour')
    desk.setting(UPDATE_PAUSE, {'version': str(version), 'le': _now().isoformat()})
    marks = ','.join('?' * len(DETERMINISTIC))
    for _ in range(UPDATE_WAIT_SECONDS):
        active = desk.db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('running','cancel_requested') AND id<>? AND kind NOT IN (%s)" % marks,
                                 (int(keep or 0), *DETERMINISTIC)).fetchone()[0]
        desk.db.commit()
        if not active:
            break
        (sleep or time.sleep)(1)
    return True


def resume_after_update(desk, now=None):
    """5.6.81 : lève la pause posée par une mise à jour une fois la version installée ; si elle n'aboutit pas, au bout de 45 minutes : l'IA ne reste
    jamais en pause par oubli. Une pause levée à la main efface simplement le repère. Retourne True si l'IA a repris."""
    from .arret5643 import global_paused, pause_all
    mark = desk.settings(UPDATE_PAUSE, {})
    if not isinstance(mark, dict) or not mark.get('version'):
        return False
    if not global_paused(desk):
        desk.setting(UPDATE_PAUSE, {})
        return False
    try:
        age = ((now or _now()) - datetime.fromisoformat(str(mark.get('le')))).total_seconds()
    except (TypeError, ValueError):
        age = UPDATE_PAUSE_SECONDS
    done = _tuple(installed_version()) >= _tuple(mark['version'])
    if not done and age < UPDATE_PAUSE_SECONDS:
        return False
    desk.setting(UPDATE_PAUSE, {})
    pause_all(desk, False, 'mise-a-jour')
    desk.audit('maj5681_ia_reprise', {'version': mark['version'], 'installee': done})
    return True


def set_automatic(desk, on):
    desk.setting(AUTO, bool(on))
    return {'ok': True, 'message': 'Mises à jour installées automatiquement la nuit.' if on else 'Mises à jour installées sur votre clic.'}


def auto_tick(desk, now=None):
    """Mode automatique : la nuit (2 h – 5 h, heure du poste ou du serveur), une seule tentative par version."""
    if not desk.settings(AUTO, False):
        return None
    version = available(desk)
    local = (now or datetime.now()).hour
    if not version or not NIGHT[0] <= local < NIGHT[1] or desk.settings(TRIED, '') == version:
        return None
    desk.setting(TRIED, version)
    try:
        return request(desk, 'automatique', version)
    except Stop as ex:
        desk.audit('maj5661_automatique_impossible', {'version': version, 'motif': str(ex)})
        return None


# ------------------------------------------------------------------------------------------------ mise à jour locale (poste)
def _download(url, dest, limit=MAX_BYTES):
    if not url.startswith('https://github.com/'):
        raise Stop('adresse_refusee')
    req = urllib.request.Request(url, headers={'User-Agent': 'AxiorHub-Pilote-maj'})
    size = 0
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, 'wb') as out:
        if not resp.geturl().startswith('https://'):
            raise Stop('redirection_non_https')
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise Stop('fichier_trop_volumineux')
            out.write(chunk)
    return dest


def _windows_command(setup):
    """Installateur silencieux dans le même dossier, mêmes raccourcis (Bureau, ouverture de session), puis relance."""
    exe = Path(sys.executable).resolve()
    root = exe.parent.parent if exe.parent.name.startswith('app-') else None
    cmd = [str(setup), '--silencieux', '--lancer']
    if root:
        cmd += ['--racine', str(root)]
    try:
        from windows import deploiement
        dirs = deploiement.shortcut_dirs()
        if not (dirs['bureau'] / deploiement.SHORTCUTS['bureau']).exists():
            cmd.append('--sans-bureau')
        if (dirs['demarrage'] / deploiement.SHORTCUTS['demarrage']).exists():
            cmd.append('--demarrage')
    except Exception:   # noqa: BLE001 — module d'installation absent : réglages par défaut de l'installateur
        pass
    return cmd


def local_update(desk, args, fetch=None, runner=None):
    """Travail ``maj_local5661`` : télécharge, vérifie l'empreinte et la signature, puis installe selon la plateforme."""
    from .signature5661 import SignatureError, verify_file
    version = str(args.get('version') or '')
    kind = str(args.get('plateforme') or platform())
    report = {'depuis': installed_version(), 'vers': version, 'debut': _now().isoformat(), 'etat': 'echec', 'etapes': [], 'plateforme': kind}

    def step(text):
        report['etapes'].append('%s %s' % (_now().isoformat()[11:19], text))
        _write_local(desk, {**report, 'etat': 'en_cours'})
    folder = request_dir(desk) / 'telechargement'
    try:
        if kind not in ASSETS:
            raise Stop('plateforme_sans_mise_a_jour_locale')
        if not VERSION_RX.match(version) or version != available(desk):
            raise Stop('version_non_disponible')
        folder.mkdir(parents=True, exist_ok=True)
        name = ASSETS[kind].format(v=version)
        target = folder / name
        get = fetch or _download
        step('Téléchargement de l’installateur, de son empreinte et de sa signature')
        get(DOWNLOAD.format(v=version, name=name), target)
        get(DOWNLOAD.format(v=version, name=name + '.sha256'), folder / (name + '.sha256'))
        get(DOWNLOAD.format(v=version, name=name + '.sig'), folder / (name + '.sig'))
        expected = (folder / (name + '.sha256')).read_text(encoding='utf-8').split()[0].lower()
        h = hashlib.sha256()
        with open(target, 'rb') as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b''):
                h.update(chunk)
        if h.hexdigest() != expected:
            raise Stop('empreinte_differente')
        step('Empreinte conforme ; vérification de la signature')
        try:
            verify_file(target, (folder / (name + '.sig')).read_bytes())
        except SignatureError as ex:
            raise Stop(str(ex)) from None
        step('Signature vérifiée ; installation')
        run = runner or subprocess
        if kind == 'windows':
            # L'installateur arrête AxiorHub (taskkill /T, arbre de processus) : lancé par « start », il ne dépend plus de ce worker.
            args_ = _windows_command(target)
            if any('"' in a or '%' in a for a in args_):
                raise Stop('chemin_refuse')
            line = 'cmd /d /c start "AxiorHub" ' + ' '.join('"%s"' % a for a in args_)
            run.Popen(line, close_fds=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            report.update({'etat': 'lancee', 'fin': _now().isoformat(),
                           'message': 'Installation lancée : AxiorHub se ferme puis se rouvre dans la nouvelle version.'})
        else:
            done = run.run(['pkexec', 'apt-get', 'install', '-y', str(target)], capture_output=True, text=True, timeout=900)
            if done.returncode == 0:
                report.update({'etat': 'reussie', 'fin': _now().isoformat(),
                               'message': 'Version installée : fermez puis rouvrez AxiorHub pour l’utiliser.'})
            else:
                run.Popen(['xdg-open', str(target)], close_fds=True)
                report.update({'etat': 'a_confirmer', 'fin': _now().isoformat(),
                               'message': 'L’installateur d’Ubuntu s’est ouvert : confirmez l’installation (mot de passe), puis rouvrez AxiorHub.'})
        _write_local(desk, report)
        return {'ok': True, 'etat': report['etat']}
    except Stop as ex:
        report.update({'etat': 'echec', 'fin': _now().isoformat(), 'motif': str(ex)[:200]})
        _write_local(desk, report)
        raise
    except Exception as ex:   # noqa: BLE001
        report.update({'etat': 'echec', 'fin': _now().isoformat(), 'motif': type(ex).__name__})
        _write_local(desk, report)
        raise Stop('mise_a_jour_locale_echouee') from None


# ------------------------------------------------------------------------------------------------ affichage
STATES = {'en_cours': 'En cours', 'reussie': 'Réussie', 'echec': 'Échec', 'lancee': 'Installation lancée', 'a_confirmer': 'À confirmer'}


def html(desk, prefix, role):
    current = installed_version()
    state = desk.settings(KEY, {}) or {}
    new = available(desk)
    kind = platform()
    admin = role == 'administrateur'
    body = '<p>Version installée : <strong>%s</strong>.' % e(current)
    if new:
        body += ' <strong>Nouvelle version disponible : %s</strong> — <a href="%s">voir les nouveautés</a>.</p>' % (
            e(new), e(RELEASES + '/tag/v' + new, quote=True))
    elif state.get('latest'):
        body += ' C’est la dernière version publiée.</p>'
    else:
        body += ' <span>%s</span></p>' % ('Dernière version publiée : inconnue (pas d’accès à GitHub).' if state.get('error')
                                          else 'Dernière version publiée : inconnue.')
    if state.get('checked'):
        body += '<p class="ax-muted">Dernière vérification : %s.</p>' % e(state['checked'][:16].replace('T', ' '))
    if role == 'administrateur':   # 5.6.62 : sans attendre la vérification automatique (toutes les 6 h)
        body += ('<form class="m5-form d51-inline" data-api="m5661/maj/verifier" data-reload><button class="ax-btn ghost" type="submit">'
                 'Vérifier maintenant</button><p role="status" class="m5-status"></p></form>')
    ready = signer_ready()
    if kind == 'autre':
        if new:
            body += '<p><a class="ax-btn" href="%s">Télécharger la version %s</a></p>' % (e(RELEASES + '/tag/v' + new, quote=True), e(new))
    elif not ready:
        body += '<p class="ax-muted">La mise à jour depuis cette page sera possible dès qu’une version signée sera installée.</p>'
    elif kind == 'serveur' and not activated():
        body += ('<p class="ax-muted">Le service de mise à jour sera installé lors de la prochaine mise à jour faite par install.sh ou le '
                 'paquet Debian.</p>')
    elif new and admin:
        body += ('<form class="m5-form" data-api="m5661/maj/demander" data-reload><input type="hidden" name="version" value="%s">'
                 '<button class="ax-btn" type="submit">Mettre à jour vers %s</button><p role="status" class="m5-status"></p></form>'
                 '<p class="ax-muted">AxiorHub met l’IA en pause, télécharge la version, vérifie son empreinte et sa signature, puis l’installe ; '
                 'l’IA reprend seule ensuite, et vos données et réglages sont conservés.</p>') % (e(new, quote=True), e(new))
    elif new:
        body += '<p class="ax-muted">La mise à jour est lancée par un administrateur.</p>'
    if admin and ready and kind != 'autre':
        on = bool(desk.settings(AUTO, False))
        body += ('<form class="m5-form" data-api="m5661/maj/automatique" data-reload><label class="r52-day"><input type="checkbox" name="actif"%s> '
                 'Installer automatiquement les nouvelles versions la nuit (entre 2 h et 5 h)</label>'
                 '<button class="ax-btn ghost" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form>') % (' checked' if on else '')
    res = last_result(desk)
    if res:
        body += '<details><summary><span>Dernière mise à jour (%s → %s) :</span> <strong>%s</strong></summary>%s<ul>%s</ul>%s</details>' % (
            e(str(res.get('depuis', ''))), e(str(res.get('vers', ''))), e(STATES.get(res.get('etat'), res.get('etat', ''))),
            ('<p>%s</p>' % e(res['message'])) if res.get('message') else '',
            ''.join('<li>%s</li>' % e(s) for s in (res.get('etapes') or [])[-12:]),
            ('<p class="d51-warn">Motif : %s</p>' % e(res['motif'])) if res.get('motif') else '')
    return '<section class="ax-card" id="maj5661"><h2>Mises à jour</h2>%s</section>' % body
