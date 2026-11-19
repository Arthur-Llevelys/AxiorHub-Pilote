"""5.6.108 (lot O) : essai réel du serveur de documents EuroOffice avec une instance AxiorHub « poste » de test, dans un vrai navigateur.

Ce que fait l'essai (données fictives, profil temporaire, aucun téléchargement) :
1. lance le conteneur ``ghcr.io/euro-office/documentserver`` déjà présent sur la machine (``--pull never``, épinglé par empreinte),
   publié seulement sur 127.0.0.1, avec ``JWT_ENABLED=true`` et un secret JWT d'essai généré en mémoire (jamais écrit sur le disque) ;
2. démarre AxiorHub comme sur un poste (origine http://127.0.0.1:<port>, ``poste: true``) avec un dossier de travail local fictif ;
3. ouvre Paramètres › Documents et procédure dans Chromium (Playwright), saisit l'adresse du serveur, le secret et l'adresse de rappel
   ``http://host.docker.internal:<port>/agent-courriel``, enregistre, puis « Tester la connexion » : toutes les étapes doivent réussir ;
4. ouvre un .docx fictif dans l'éditeur, tape une phrase, enregistre (Ctrl+S) : le fichier local doit changer, contenir la phrase, et le
   rappel du serveur de documents doit avoir été accepté (aucun refus au journal d'audit) ;
5. enregistre un mauvais secret : le test de connexion doit le signaler, l'éditeur doit refuser d'ouvrir le document et un rappel signé
   avec le secret du serveur doit être refusé par AxiorHub ;
6. arrête AxiorHub, arrête et supprime le conteneur (toujours, même en cas d'échec).

Captures (sans secret : le champ est vidé après l'enregistrement) dans ``--sortie``.
Lancement : ``python scripts/essai_editeur56108o.py --sortie DOSSIER`` (Docker Desktop démarré, image présente).
``--fichier-env CHEMIN`` : conteneur lancé exactement comme au parcours B du guide (``--env-file`` préparé par ses commandes
PowerShell, ``--restart unless-stopped``) ; le secret est lu dans ce fichier, jamais affiché.
Non inscrit dans ``PARCOURS`` (le banc bloque toute adresse autre que la sienne ; l'éditeur se charge depuis le serveur de documents).
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PREFIX = '/agent-courriel'
IMAGE = 'ghcr.io/euro-office/documentserver@sha256:2fadc1b3dc99250d6576fc0c0f34349db6725b752b08e91db9831fbb803fc5c2'
DOSSIER = '/Dossiers/DOS-001 - DURAND c. ZORVAL'
DOCUMENT = DOSSIER + '/Projet de courrier DURAND.docx'
PHRASE = 'Ajout de l essai du lot O 5.6.108'


class Echec(Exception):
    pass


def verifier(condition, message):
    if not condition:
        raise Echec(message)


def journal(texte):
    print('[essai 56108o] ' + texte, flush=True)


def docx_fictif(*paragraphes):
    w = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    r = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    corps = ''.join('<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % p for p in paragraphes)
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/>'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="%s/officeDocument" Target="word/document.xml"/></Relationships>' % r)
        z.writestr('word/document.xml', '<w:document xmlns:w="%s"><w:body>%s<w:sectPr/></w:body></w:document>' % (w, corps))
    return tampon.getvalue()


def texte_docx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.read('word/document.xml').decode('utf-8', 'replace')


# ----------------------------------------------------------------------------------------- conteneur
class Conteneur:
    def __init__(self, nom, port, secret, fichier_env=''):
        self.nom, self.port, self.secret, self.fichier_env = nom, port, secret, fichier_env
        self.url = 'http://127.0.0.1:%d' % port

    def lancer(self):
        subprocess.run(['docker', 'rm', '-f', self.nom], capture_output=True)
        if self.fichier_env:   # commande du guide : secret lu par Docker dans le fichier d'environnement (JWT_ENABLED, JWT_SECRET)
            out = subprocess.run(['docker', 'run', '-d', '--pull', 'never', '--name', self.nom, '--restart', 'unless-stopped',
                                  '-p', '127.0.0.1:%d:80' % self.port, '--env-file', self.fichier_env, IMAGE], capture_output=True, text=True)
        else:
            env = {**os.environ, 'JWT_SECRET': self.secret}   # « -e JWT_SECRET » sans valeur : repris de l'environnement, jamais dans la ligne de commande
            out = subprocess.run(['docker', 'run', '-d', '--pull', 'never', '--name', self.nom, '-p', '127.0.0.1:%d:80' % self.port,
                                  '-e', 'JWT_ENABLED=true', '-e', 'JWT_SECRET', IMAGE], capture_output=True, text=True, env=env)
        verifier(out.returncode == 0, 'docker run a échoué : ' + out.stderr[-400:])

    def mesurer(self):
        """Mémoire du conteneur et taille de l'image (pour le guide)."""
        stats = subprocess.run(['docker', 'stats', '--no-stream', '--format', '{{.MemUsage}}', self.nom], capture_output=True, text=True).stdout.strip()
        taille = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Size}}', IMAGE], capture_output=True, text=True).stdout.strip()
        return {'memoire': stats, 'image_octets': taille}

    def attendre(self, limite=240):
        fin = time.time() + limite
        while time.time() < fin:
            try:
                with urllib.request.urlopen(self.url + '/healthcheck', timeout=5) as r:
                    if r.read().strip() == b'true':
                        return
            except OSError:
                pass
            time.sleep(3)
        raise Echec('le serveur de documents ne répond pas après %d s' % limite)

    def supprimer(self):
        subprocess.run(['docker', 'stop', self.nom], capture_output=True)
        subprocess.run(['docker', 'rm', self.nom], capture_output=True)


# ----------------------------------------------------------------------------------------- instance AxiorHub (poste)
class Poste:
    def __init__(self, base, port):
        self.base, self.port = Path(base), port
        self.origine = 'http://127.0.0.1:%d' % port
        self.url = self.origine + PREFIX

    def preparer(self):
        from agent.common import private_json
        data = self.base / 'donnees'
        state, local = data / 'state', data / 'stockage'
        state.mkdir(parents=True)
        os.environ['AXIORHUB_DATA_DIR'] = str(data)
        os.environ['AXIORHUB_AUTH_STATE'] = str(data / 'auth')
        (local / DOSSIER.lstrip('/')).mkdir(parents=True)
        self.fichier = local / DOCUMENT.lstrip('/')
        self.fichier.write_bytes(docx_fictif('Projet de courrier fictif pour le dossier DOS-001.', 'Valbrune, essai sans donnée réelle.'))
        private_json(data / 'matters.json', [{'id': 'DOS-001', 'client_name': 'DURAND', 'path': DOSSIER, 'references': ['DOS-001', 'RG 24/01234'],
                                               'aliases': ['DURAND'], 'correspondents': [{'email': 'durand@example.test', 'role': 'client'}]}])
        self.config = {
            'mode': 'drafts', 'state_dir': str(state), 'matters_file': str(data / 'matters.json'), 'max_messages_per_run': 2,
            'installation': {'done': True, 'at': 'essai', 'by': 'essai'},
            'mail': {'enabled': False, 'inbox': 'INBOX', 'drafts': 'Drafts', 'sent': 'Sent', 'host': 'mail.example.test', 'username': '',
                     'own_addresses': ['cabinet@example.test'], 'from_address': 'cabinet@example.test', 'from_name': 'Cabinet Valbrune'},
            'nextcloud': {'local_path': str(local), 'roots': ['/Dossiers'], 'matter_roots': ['/Dossiers']},
            'documents': {}, 'calendar': {'urls': [], 'timezone': 'Europe/Paris', 'notice_hours': 0},
            'ollama': {'url': 'http://127.0.0.1:9', 'model': 'essai-absent'}, 'automation': {'health_enabled': False, 'sync_enabled': False},
            'autonomy': {'enabled': False}, 'orchestrator': {'enabled': False}, 'production': {'enabled': False},
            'updates': {'channel': 'stable', 'metadata_url': ''}, 'ai_providers': {}, 'workstation': {}, 'audio': {'enabled': False}}
        self.config_path = data / 'config.json'
        private_json(self.config_path, self.config)
        salt = secrets.token_hex(16)
        self.auth_path = data / 'ui-auth.json'
        private_json(self.auth_path, {'username': 'admin', 'salt': salt, 'poste': True, 'csrf': secrets.token_urlsafe(32),
                                      'hash': hashlib.scrypt(secrets.token_urlsafe(24).encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex(),
                                      'origin': self.origine, 'prefix': PREFIX})

    def demarrer(self):
        from waitress import create_server
        from agent import __version__, budget_ledger5689, passerelle5689
        from agent.web import App
        budget_ledger5689.preparer(self.config, 'web', __version__)
        passerelle5689.installer(self.config)
        app = App(str(self.config_path), str(self.auth_path))
        # Comme ``agent.web.serve`` : écoute sur 127.0.0.1 seulement, mandataire de confiance 127.0.0.1.
        self.serveur = create_server(app, host='127.0.0.1', port=self.port, threads=8, send_bytes=1, max_request_body_size=20_100_000,
                                     trusted_proxy='127.0.0.1', trusted_proxy_headers={'x-forwarded-proto', 'x-forwarded-for'},
                                     clear_untrusted_proxy_headers=True)
        threading.Thread(target=self.serveur.run, daemon=True, name='essai-waitress').start()

    def arreter(self):
        self.serveur.close()

    def desk(self):
        from agent.common import load_config
        from agent.desk import Desk
        return Desk(load_config(str(self.config_path)))

    def lien_session(self, suite):
        from agent.poste_session import new_launch_token
        from urllib.parse import quote
        return self.url + '/poste-session?jeton=' + new_launch_token(self.auth_path) + '&suite=' + quote(suite, safe='')


# ----------------------------------------------------------------------------------------- navigateur
def regler(page, poste, conteneur, secret):
    page.goto(poste.lien_session('/parametres?rubrique=documents'), wait_until='load')
    page.wait_for_selector('#of-server')
    page.select_option('#of-engine', 'euro-office')
    page.fill('#of-server', conteneur.url)
    page.fill('#of-secret', secret)
    page.fill('#of-callback', 'http://host.docker.internal:%d%s' % (poste.port, PREFIX))
    with page.expect_response(lambda r: r.url.endswith('/api440/settings/office')) as rep:
        page.click('#of-save')
    verifier(rep.value.status == 200, 'enregistrement des réglages refusé : HTTP %s' % rep.value.status)
    page.wait_for_function("() => document.querySelector('#of-secret').value === ''")


def tester(page, captures, nom):
    with page.expect_response(lambda r: r.url.endswith('/api440/settings/office/test'), timeout=90000) as rep:
        page.click('#of-diag')
    resultat = rep.value.json()
    page.wait_for_selector('#of-result .ax-check-row')
    page.wait_for_function("() => !document.querySelector('#ax-toast .ax-toast')", timeout=15000)   # notification disparue avant la capture
    page.locator('#ax-office-settings').scroll_into_view_if_needed()
    page.locator('#ax-office-settings').screenshot(path=str(captures / nom))
    return resultat['checks']


def ouvrir(page, poste):
    from urllib.parse import quote
    page.goto(poste.url + '/documents/edit?path=' + quote(DOCUMENT, safe=''), wait_until='load')


def essai(args):
    from playwright.sync_api import sync_playwright
    captures = Path(args.sortie)
    captures.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_hex(32)
    if args.fichier_env:   # secret du fichier d'environnement préparé par les commandes du guide (jamais affiché)
        lignes = Path(args.fichier_env).read_text(encoding='utf-8').splitlines()
        verifier('JWT_ENABLED=true' in lignes, 'JWT_ENABLED=true absent du fichier d’environnement')
        secret = next((l.split('=', 1)[1] for l in lignes if l.startswith('JWT_SECRET=')), '')
        verifier(len(secret) == 64, 'JWT_SECRET absent ou de longueur inattendue dans le fichier d’environnement')
    conteneur = Conteneur(args.conteneur, args.port_editeur, secret, args.fichier_env)
    base = tempfile.mkdtemp(prefix='axh-essai-56108o-')
    poste = Poste(base, args.port)
    resume = {'etapes': []}
    try:
        journal('lancement du conteneur %s (image présente, aucun téléchargement)' % conteneur.nom)
        conteneur.lancer()
        poste.preparer()
        poste.demarrer()
        conteneur.attendre()
        journal('serveur de documents prêt')
        avant = poste.fichier.read_bytes()
        with sync_playwright() as pw:
            navigateur = pw.chromium.launch()
            contexte = navigateur.new_context(viewport={'width': 1366, 'height': 900}, locale='fr-FR')
            page = contexte.new_page()
            erreurs = []
            page.on('pageerror', lambda exc: erreurs.append(str(exc)[:300]))

            # 1. réglages et test de connexion (bon secret)
            regler(page, poste, conteneur, secret)
            checks = tester(page, captures, '01-tester-la-connexion-ok.png')
            journal('test de connexion : ' + ' | '.join(('OK ' if c['ok'] else 'KO ') + c['name'] for c in checks))
            verifier(all(c['ok'] for c in checks), 'une étape du test a échoué : %s' % [c for c in checks if not c['ok']])
            verifier(any(c['name'] == 'Le serveur de documents joint AxiorHub' for c in checks), 'étape de rappel absente')
            resume['etapes'].append({'test_connexion_bon_secret': [(c['name'], c['ok']) for c in checks]})

            # 2. ouverture, modification, enregistrement
            ouvrir(page, poste)
            page.wait_for_function("() => (document.getElementById('ax-docstate') || {}).textContent.indexOf('Prêt') === 0", timeout=120000)
            cadre = page.locator('iframe[name="frameEditor"]')
            cadre.wait_for(state='visible', timeout=60000)
            time.sleep(4)
            page.screenshot(path=str(captures / '02-editeur-ouvert.png'))
            boite = cadre.bounding_box()
            page.mouse.click(boite['x'] + boite['width'] / 2, boite['y'] + boite['height'] * 0.35)
            time.sleep(1)
            page.keyboard.press('Control+End')
            page.keyboard.press('Enter')
            page.keyboard.type(PHRASE, delay=40)
            time.sleep(3)
            page.keyboard.press('Control+s')
            journal('phrase tapée, Ctrl+S envoyé ; attente du rappel')
            fin, apres = time.time() + 90, avant
            while time.time() < fin:
                apres = poste.fichier.read_bytes()
                if apres != avant:
                    break
                time.sleep(2)
            if apres == avant:   # repli : fermeture de l'éditeur (statut 2 quelques secondes après le départ du dernier participant)
                journal('pas de forcesave constaté : fermeture de l’éditeur')
                page.goto('about:blank')
                fin = time.time() + 120
                while time.time() < fin and apres == avant:
                    time.sleep(3)
                    apres = poste.fichier.read_bytes()
            else:
                page.screenshot(path=str(captures / '03-editeur-apres-enregistrement.png'))
            verifier(apres != avant, 'le fichier local n’a pas changé après l’enregistrement')
            verifier(PHRASE in texte_docx(apres), 'la phrase tapée est absente du fichier enregistré')
            d = poste.desk()
            try:
                sauvegardes = [dict(r) for r in d.db.execute('SELECT outcome,status,saved_path FROM office_saves440 ORDER BY id').fetchall()]
                refus = d.db.execute("SELECT COUNT(*) FROM audit WHERE action='editeur_documents_440_callback_refuse'").fetchone()[0]
            finally:
                d.db.close()
            journal('sauvegardes : %s ; rappels refusés : %s' % (sauvegardes, refus))
            verifier(sauvegardes and sauvegardes[-1]['outcome'] == 'replaced', 'aucune sauvegarde « replaced » enregistrée')
            verifier(refus == 0, 'un rappel du serveur de documents a été refusé')
            resume['mesures'] = conteneur.mesurer()
            journal('mesures : %s' % resume['mesures'])
            resume['etapes'].append({'enregistrement': sauvegardes, 'rappels_refuses': refus,
                                     'sha256_avant': hashlib.sha256(avant).hexdigest(), 'sha256_apres': hashlib.sha256(apres).hexdigest()})

            # 3. mauvais secret : test, éditeur, rappel
            mauvais = secrets.token_hex(32)
            regler(page, poste, conteneur, mauvais)
            checks = tester(page, captures, '04-tester-la-connexion-mauvais-secret.png')
            journal('test (mauvais secret) : ' + ' | '.join(('OK ' if c['ok'] else 'KO ') + c['name'] for c in checks))
            ko = {c['name'] for c in checks if not c['ok']}
            verifier('Secret JWT et API de commande' in ko, 'le mauvais secret n’est pas signalé par l’API de commande')
            verifier('Le serveur de documents joint AxiorHub' in ko, 'le mauvais secret n’est pas signalé par l’essai de rappel')
            ouvrir(page, poste)
            time.sleep(15)
            etat = page.locator('#ax-docstate').text_content() or ''
            page.screenshot(path=str(captures / '05-editeur-mauvais-secret.png'))
            journal('éditeur avec un mauvais secret : « %s »' % etat)
            verifier(not etat.startswith('Prêt'), 'l’éditeur s’est ouvert avec un mauvais secret')
            # rappel signé avec le secret du serveur de documents, refusé par AxiorHub qui a un autre secret enregistré
            from agent import office440
            d = poste.desk()
            try:
                jeton = office440.sign(d, {'t': 'callback', 'k': 'essai', 'p': DOCUMENT})
            finally:
                d.db.close()
            corps = json.dumps({'key': 'essai', 'status': 1}).encode()
            req = urllib.request.Request(poste.url + '/office/callback/' + jeton, data=corps, method='POST',
                                         headers={'Content-Type': 'application/json', 'Host': 'host.docker.internal:%d' % poste.port,
                                                  'Authorization': 'Bearer ' + office440.jwt_encode({'payload': {'key': 'essai', 'status': 1}}, secret)})
            try:
                code = urllib.request.urlopen(req, timeout=10).status
            except urllib.error.HTTPError as err:
                code = err.code
            journal('rappel signé avec l’autre secret : HTTP %s' % code)
            verifier(code == 403, 'rappel avec un mauvais secret non refusé (HTTP %s)' % code)
            resume['etapes'].append({'mauvais_secret': {'etapes_en_echec': sorted(ko), 'etat_editeur': etat, 'rappel_http': code}})
            resume['erreurs_js'] = erreurs
            navigateur.close()
        resume['resultat'] = 'réussi'
        journal('ESSAI RÉUSSI')
        return 0
    except Echec as ex:
        resume['resultat'] = 'échec : ' + str(ex)
        journal('ÉCHEC : ' + str(ex))
        return 1
    finally:
        try:
            poste.arreter()
        except Exception:   # noqa: BLE001 — l'arrêt ne masque jamais le résultat
            pass
        if not args.garder_conteneur:
            conteneur.supprimer()
            journal('conteneur arrêté et supprimé')
        (captures / 'resultat.json').write_text(json.dumps(resume, ensure_ascii=False, indent=1), encoding='utf-8')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'essai-56108o'))
    ap.add_argument('--conteneur', default='axh-essai-56108o')
    ap.add_argument('--port-editeur', type=int, default=18080)
    ap.add_argument('--port', type=int, default=18769)
    ap.add_argument('--garder-conteneur', action='store_true', help='ne pas supprimer le conteneur à la fin (diagnostic)')
    ap.add_argument('--fichier-env', default='', help='fichier JWT_ENABLED / JWT_SECRET préparé avec les commandes du guide (docker run --env-file)')
    return essai(ap.parse_args(argv))


if __name__ == '__main__':
    sys.exit(main())
