#!/usr/bin/env python3
"""5.6.94 (lot parcours, P01) : banc de parcours dans un vrai navigateur.

Le banc démarre AxiorHub tel qu'il est servi en production (``agent.web.App`` derrière ``waitress``, page de connexion de la version serveur)
sur ``127.0.0.1`` et un port libre, avec un dossier d'état temporaire rempli de données fictives, puis joue les parcours P02 avec Playwright
pour Python. Outil de vérification seulement : il tourne sur le poste de développement et dans la recette GitHub, jamais sur le serveur
du cabinet, et n'entre dans aucun paquet.

- Compte : l'administrateur de la version serveur, créé au lancement ; identifiant et mot de passe aléatoires, gardés en mémoire seulement
  (jamais affichés, jamais écrits dans un journal ni dans une capture : le champ du mot de passe est masqué par le navigateur).
- Données : dossiers DOS-001 à DOS-003 (DURAND, MARTIN, ZORVAL), un document préparé au registre, stockage local (pas de Nextcloud), pas
  de messagerie, faux fournisseur de modèle (serveur de bouclage qui parle comme Ollama et compte ses appels).
- Travaux : aucun travailleur de fond. Le banc exécute lui-même la file (``Desk.work_once``), au moment voulu par le parcours.
- Réseau : toute requête du navigateur vers une autre adresse que le serveur du banc est bloquée et fait échouer le parcours.

Usage : ``python scripts/parcours-navigateur.py [--navigateur chromium|msedge] [--sortie DOSSIER] [--parcours P02a,P02b] [--voir]``.
Code de sortie : 0 si tous les parcours réussissent, 1 au premier échec (capture d'écran et journal de la console dans le dossier de
sortie), 2 si Playwright est absent.
"""
import argparse
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
import traceback
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
PREFIX = '/agent-courriel'
MATTERS = (('DOS-001', 'DURAND', 'MARTIN'), ('DOS-002', 'MARTIN', 'BERNARD'), ('DOS-003', 'ZORVAL', 'KERVILAC'))
CONSOLE_IGNOREE = ()   # motifs de console tolérés (aucun pour l'instant : toute erreur JavaScript fait échouer le parcours)


class Echec(AssertionError):
    """Résultat métier différent de l'attendu."""


def verifier(condition, message):
    if not condition:
        raise Echec(message)


def docx_fictif(*paragraphes):
    """Document Word minimal (un paragraphe, un run, un texte par ligne)."""
    import io
    import zipfile
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


def reponse_fictive(corps):
    """Réponse du faux modèle : une proposition de modification quand on lui en demande une, sinon un texte court."""
    for message in corps.get('messages') or []:
        try:
            donnees = json.loads(message.get('content') or '')
        except (TypeError, ValueError):
            continue
        if isinstance(donnees, dict) and isinstance(donnees.get('paragraphes'), list):
            cible = next((p for p in donnees['paragraphes'] if 'mille' in str(p.get('texte', ''))), None)
            if cible is not None:
                return json.dumps({'propositions': [{'index': cible['index'], 'texte': 'Le prix est de deux mille euros.', 'motif': 'Consigne du banc', 'sources': ['S1']}]})
            return json.dumps({'propositions': []})
    return 'Réponse fictive du banc.'


# ------------------------------------------------------------------------------------------------ faux fournisseur de modèle
class FauxModele:
    """Serveur de bouclage qui parle comme Ollama (``/api/show``, ``/api/chat``, ``/api/tags``) ; compte les appels de génération.

    ``pannes`` : nombre de prochaines générations refusées par une erreur du fournisseur (HTTP 503), pour la reprise après erreur."""

    def __init__(self):
        self.appels, self.pannes, self.verrou = [], 0, threading.Lock()
        self.reponse = reponse_fictive
        faux = self

        class Reponses(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _rendre(self, valeur, code=200):
                brut = (json.dumps(valeur, ensure_ascii=False) + '\n').encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(brut)))
                self.end_headers()
                self.wfile.write(brut)

            def do_GET(self):
                self._rendre({'version': '0.0-banc', 'models': [{'name': 'banc-local', 'model': 'banc-local'}]})

            def do_POST(self):
                corps = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
                if self.path == '/api/chat':
                    with faux.verrou:
                        faux.appels.append(time.time())
                        panne = faux.pannes > 0
                        faux.pannes -= 1 if panne else 0
                    if panne:
                        return self._rendre({'error': 'fournisseur fictif indisponible'}, 503)
                    return self._rendre({'message': {'role': 'assistant', 'content': faux.reponse(corps)}, 'done': True, 'done_reason': 'stop',
                                         'prompt_eval_count': 10, 'eval_count': 5})
                if self.path == '/api/embed':
                    return self._rendre({'embeddings': [[0.1] * 16 for _ in corps.get('input') or []]})
                return self._rendre({'capabilities': ['completion'], 'details': {'family': 'banc'}, 'model_info': {'banc.context_length': 65536}})

        self.serveur = ThreadingHTTPServer(('127.0.0.1', 0), Reponses)
        self.serveur.daemon_threads = True
        self.url = 'http://127.0.0.1:%d' % self.serveur.server_address[1]
        threading.Thread(target=self.serveur.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True).start()

    def nombre(self):
        with self.verrou:
            return len(self.appels)

    def arreter(self):
        self.serveur.shutdown()
        self.serveur.server_close()


# ------------------------------------------------------------------------------------------------ installation fictive
class Banc:
    """Dossier d'état temporaire, configuration, compte, serveur ``waitress`` et file exécutée à la demande."""

    def __init__(self, base):
        self.base = Path(base)
        self.modele = FauxModele()
        self.ident = 'banc-' + secrets.token_hex(4)
        self.mot_de_passe = secrets.token_urlsafe(24)      # jamais affiché ni écrit en clair : seule son empreinte scrypt est sur le disque
        self.serveur = self.port = None
        self.verrou_file = threading.Lock()

    # -------------------------------------------------------------- préparation
    def preparer(self):
        from agent.common import private_json
        data = self.base / 'donnees'
        state, cloud = data / 'state', data / 'stockage'
        state.mkdir(parents=True)
        os.environ['AXIORHUB_DATA_DIR'] = str(data)              # jamais /data ni le dossier d'un vrai poste
        os.environ['AXIORHUB_AUTH_STATE'] = str(data / 'auth')   # pas de comptes de la distribution autonome : page de connexion du serveur
        matters = []
        for ident, client, adverse in MATTERS:
            path = '/Dossiers/%s - %s c. %s' % (ident, client, adverse)
            (cloud / path.lstrip('/')).mkdir(parents=True)
            matters.append({'id': ident, 'client_name': client, 'path': path, 'references': [ident, 'RG 24/01234'] if ident == 'DOS-001' else [ident],
                            'aliases': [client], 'correspondents': [{'email': client.lower() + '@example.test', 'role': 'client'}]})
        private_json(data / 'matters.json', matters)
        self.config = {
            'mode': 'drafts', 'state_dir': str(state), 'matters_file': str(data / 'matters.json'), 'max_messages_per_run': 2,
            'installation': {'done': True, 'at': 'banc', 'by': 'banc'},
            'mail': {'enabled': False, 'inbox': 'INBOX', 'drafts': 'Drafts', 'sent': 'Sent', 'host': 'mail.example.test', 'username': '',
                     'own_addresses': ['cabinet@example.test'], 'from_address': 'cabinet@example.test', 'from_name': 'Cabinet Valbrune'},
            'nextcloud': {'url': 'https://cloud.example.test', 'local_path': str(cloud), 'roots': ['/Dossiers'], 'matter_roots': ['/Dossiers']},
            'documents': {}, 'calendar': {'urls': [], 'timezone': 'Europe/Paris', 'notice_hours': 0},
            'ollama': {'url': self.modele.url, 'model': 'banc-local', 'timeout_seconds': 20, 'num_ctx': 65536},
            'model_routing': {'fast_model': 'banc-local', 'complex_model': 'banc-local', 'control_model': 'banc-local'},
            'speech568': {'external_allowed': False, 'fallback_local': True},   # aucun fournisseur choisi : celui d'une installation neuve
            'automation': {'health_enabled': False, 'sync_enabled': False}, 'autonomy': {'enabled': False}, 'orchestrator': {'enabled': False},
            'production': {'enabled': False}, 'updates': {'channel': 'stable', 'metadata_url': ''}, 'ai_providers': {}, 'lawve_extensions': {},
            'workstation': {}, 'audio': {'enabled': False}}
        self.config_path = data / 'config.json'
        private_json(self.config_path, self.config)
        salt = secrets.token_hex(16)
        self.auth_path = data / 'ui-auth.json'
        self.auth = {'username': self.ident, 'salt': salt, 'hash': hashlib.scrypt(self.mot_de_passe.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex(),
                     'csrf': secrets.token_urlsafe(32), 'origin': '', 'prefix': PREFIX}
        self.cloud = cloud

    def demarrer(self):
        from waitress import create_server
        from agent.common import private_json
        from agent.web import App
        # Port libre choisi avant l'écriture de l'origine (le contrôle d'hôte de l'application compare l'hôte et le port).
        import socket
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]
        self.origine = 'http://127.0.0.1:%d' % self.port
        self.auth['origin'] = self.origine
        private_json(self.auth_path, self.auth)
        from agent import __version__, budget_ledger5689, passerelle5689
        budget_ledger5689.preparer(self.config, 'web', __version__)      # comme ``agent.web.serve`` : registre du budget, passerelle sortante
        passerelle5689.installer(self.config)
        app = App(str(self.config_path), str(self.auth_path))
        self.serveur = create_server(app, host='127.0.0.1', port=self.port, threads=8, send_bytes=1, max_request_body_size=20_100_000)
        threading.Thread(target=self.serveur.run, daemon=True, name='banc-waitress').start()
        self.url = self.origine + PREFIX

    def arreter(self):
        if self.serveur is not None:
            self.serveur.close()
        self.modele.arreter()

    # -------------------------------------------------------------- outils des parcours
    @contextmanager
    def desk(self):
        from agent.common import load_config
        from agent.desk import Desk
        d = Desk(load_config(str(self.config_path)))
        try:
            yield d
        finally:
            d.db.close()

    def semer_livrable(self):
        """Un document préparé au registre (contrat fictif du dossier DOS-001), comme le journal d'une demande de document l'inscrit."""
        from agent import docrequest520, livrables5689
        m = MATTERS[0]
        chemin = '/Dossiers/%s - %s c. %s/PROJETS/Contrat %s.docx' % (m[0], m[1], m[2], m[1])
        brut = docx_fictif('Contrat entre %s et %s.' % (m[1], m[2]), 'Le prix est de mille euros.', 'Fin du contrat.')
        fichier = self.cloud / chemin.lstrip('/')
        fichier.parent.mkdir(parents=True, exist_ok=True)
        fichier.write_bytes(brut)
        sha = hashlib.sha256(brut).hexdigest()
        maintenant = time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime())
        with self.desk() as d:
            docrequest520.ensure_schema(d)
            d.db.execute('INSERT INTO docreq520(id,matter,request,kind,status,path,result,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                         ('banc-' + secrets.token_hex(6), m[0], 'Préparer un contrat fictif', 'contrat', 'cree', chemin,
                          json.dumps({'title': 'Contrat ' + m[1], 'sha256': sha, 'readback_sha256': sha}), maintenant, maintenant))
            d.db.commit()
            livrables5689.synchroniser(d, force=True)
            row = d.db.execute('SELECT document_id FROM prepared_items5689 WHERE path=? ORDER BY version_number DESC LIMIT 1', (chemin,)).fetchone()
        verifier(row, 'le document fictif n’a pas été inscrit au registre')
        return {'document_id': row[0], 'path': chemin, 'sha256': sha, 'octets': brut}

    def travailler(self, limite=20):
        """Exécute la file comme le travailleur (``Desk.work_once``) jusqu'à ce qu'elle soit vide ; rend le nombre de travaux pris."""
        pris = 0
        with self.verrou_file, self.desk() as d:
            d.worker_id = 'banc'
            while pris < limite and d.work_once():
                pris += 1
        return pris

    def travaux(self, kind=None):
        with self.desk() as d:
            sql, args = 'SELECT id,kind,status,attempts FROM jobs', ()
            if kind:
                sql, args = sql + ' WHERE kind=?', (kind,)
            return [dict(r) for r in d.db.execute(sql + ' ORDER BY id', args)]


# ------------------------------------------------------------------------------------------------ navigateur
class Session:
    """Page Playwright surveillée : erreurs JavaScript, messages d'erreur de la console (dont la CSP), requêtes vers une autre adresse."""

    def __init__(self, banc, context):
        self.banc, self.context = banc, context
        self.erreurs, self.console, self.externes = [], [], []
        hote = urlsplit(banc.origine).netloc

        def filtre(route):
            netloc = urlsplit(route.request.url).netloc
            if route.request.url.startswith(('data:', 'blob:')) or netloc == hote:
                return route.continue_()
            self.externes.append(route.request.url[:200])
            return route.abort()
        context.route('**/*', filtre)
        self.page = context.new_page()
        self.page.on('pageerror', self._erreur_js)
        self.page.on('console', self._console)

    def _erreur_js(self, exc):
        # Message et premier emplacement de la pile (fichier servi, ligne) : l'échec désigne le script en faute.
        lieu = next((l.strip() for l in str(getattr(exc, 'stack', '') or '').splitlines() if 'http' in l), '')
        self.erreurs.append('Erreur JavaScript : %s%s' % (str(getattr(exc, 'message', exc))[:300], (' (%s)' % lieu.replace(self.banc.origine, '')[:200]) if lieu else ''))

    def _console(self, message):
        texte = message.text[:400]
        self.console.append('[%s] %s' % (message.type, texte))
        if message.type == 'error' and not any(re.search(m, texte) for m in CONSOLE_IGNOREE):
            self.erreurs.append('Console : %s' % texte)

    def aller(self, chemin):
        reponse = self.page.goto(self.banc.url + chemin, wait_until='load')
        self.page.wait_for_load_state('networkidle')
        return reponse

    def propre(self, lieu):
        """Aucune erreur JavaScript ni CSP, aucune requête externe depuis le dernier contrôle."""
        erreurs, externes = list(self.erreurs), list(self.externes)
        self.erreurs.clear()
        self.externes.clear()
        verifier(not externes, '%s : requête vers une autre adresse que le banc : %s' % (lieu, externes[:3]))
        verifier(not erreurs, '%s : %s' % (lieu, ' | '.join(erreurs[:5])))

    def connecter(self):
        p = self.page
        self.aller('/connexion')
        p.fill('#cx48-ident', self.banc.ident)
        p.fill('#cx48-pass', self.banc.mot_de_passe)
        with p.expect_navigation():
            p.click('form.cx48-form button[type=submit]')
        p.wait_for_load_state('networkidle')

    def capture(self, chemin):
        """Capture d'écran sans l'identifiant du compte (bloc du compte et champs de connexion masqués)."""
        masques = [self.page.locator(s) for s in ('.cx48-user', '.ws-user', '#cx48-ident', '#cx48-pass')]
        self.page.screenshot(path=str(chemin), full_page=True, mask=masques)


# ------------------------------------------------------------------------------------------------ parcours (P02)
def envoyer(p, motif, clic, quoi):
    """Envoi d'un formulaire ``data-reload`` : réponse de l'API attendue, puis rechargement de la page par son script (600 ms plus tard)."""
    from playwright.sync_api import TimeoutError as Delai
    reponse = None
    try:
        with p.expect_navigation(timeout=15000):
            with p.expect_response(lambda r: motif in r.url and r.request.method == 'POST') as attendu:
                clic()
            reponse = attendu.value
    except Delai:
        verifier(reponse is not None and reponse.ok, '%s : refusé (HTTP %s)' % (quoi, reponse.status if reponse else '?'))
        raise Echec('%s : la page ne s’est pas rechargée après l’envoi' % quoi) from None
    verifier(reponse.ok, '%s : refusé (HTTP %d)' % (quoi, reponse.status))
    p.wait_for_load_state('networkidle')
    return reponse


def _proposition(session, doc, consigne='Porter le prix à deux mille euros'):
    """« Modifier avec l'IA » sur la page du document : une demande de proposition."""
    p = session.page
    session.aller('/livrables/' + doc['document_id'])
    p.click('details.l89-ia > summary')
    p.fill('details.l89-ia textarea[name=instruction]', consigne)
    envoyer(p, '/modifier-ia', lambda: p.click('details.l89-ia button[type=submit]'), 'la demande de proposition')


def _propositions(banc, doc):
    with banc.desk() as d:
        return [dict(r) for r in d.db.execute('SELECT id,state,job_id FROM document_patches5689 WHERE document_id=? ORDER BY created,rowid', (doc['document_id'],))]


def _etat_travail(banc, job_id):
    return next((j['status'] for j in banc.travaux() if j['id'] == job_id), None)


def p02a_connexion(banc, s):
    """Connexion par la page du serveur, puis déconnexion : la session est réellement fermée côté serveur."""
    p = s.page
    s.connecter()
    verifier(p.url.startswith(banc.url + '/aujourdhui'), 'après connexion, la page n’est pas Aujourd’hui : %s' % p.url.replace(banc.origine, ''))
    jeton = next((c['value'] for c in s.context.cookies() if c['name'] == 'axh_session'), '')
    verifier(jeton, 'aucun cookie de session après la connexion')
    s.propre('connexion')
    with p.expect_navigation():
        p.click('.cx48-user .cx48-logout')
    verifier('/connexion' in p.url and 'sortie=1' in p.url, 'la déconnexion ne ramène pas à la page de connexion')
    verifier(not any(c['name'] == 'axh_session' and c['value'] for c in s.context.cookies()), 'le cookie de session reste après la déconnexion')
    # Le jeton d'avant n'ouvre plus rien : la session est close sur le serveur, pas seulement oubliée par le navigateur.
    rejeu = s.context.request.get(banc.url + '/aujourdhui', headers={'Cookie': 'axh_session=' + jeton}, max_redirects=0)
    verifier(rejeu.status == 303 and '/connexion' in (rejeu.headers.get('location') or ''), 'l’ancien jeton de session ouvre encore une page (HTTP %d)' % rejeu.status)
    s.aller('/aujourdhui')
    verifier('/connexion' in p.url, 'Aujourd’hui s’ouvre encore après la déconnexion')
    s.propre('déconnexion')


def p02b_pause(banc, s):
    """Pause de l'IA depuis l'en-tête d'Aujourd'hui : persistée, aucun appel au modèle pendant la pause, reprise."""
    p = s.page
    doc = banc.semer_livrable()
    s.connecter()
    bouton = p.locator('#a92-pause')
    verifier(bouton.get_attribute('data-paused') == '0', 'l’IA est déjà en pause au départ')
    with p.expect_response(lambda r: 'automatismes/pause' in r.url) as attendu:
        bouton.click()
    verifier(attendu.value.ok and attendu.value.json().get('paused') is True, 'la pause est refusée')
    s.aller('/aujourdhui')
    verifier(p.locator('#a92-pause').get_attribute('data-paused') == '1', 'la pause n’est plus affichée après rechargement')
    with banc.desk() as d:
        verifier(d.settings('missions567:pause', False) is True, 'la pause n’est pas enregistrée sur le serveur')
    avant = banc.modele.nombre()
    _proposition(s, doc)                       # demande de l'avocat pendant la pause : acceptée, elle attend la reprise
    props = _propositions(banc, doc)
    verifier(len(props) == 1 and props[0]['job_id'], 'la demande faite pendant la pause n’est pas enregistrée')
    for _ in range(3):
        banc.travailler()                      # le travailleur passe plusieurs fois : rien ne doit partir chez le modèle
    verifier(banc.modele.nombre() == avant, 'le modèle a été appelé pendant la pause (%d appel(s))' % (banc.modele.nombre() - avant))
    etat = _etat_travail(banc, props[0]['job_id'])
    verifier(etat in ('pending', 'suspended'), 'le travail IA a démarré pendant la pause (état %s)' % etat)
    s.aller('/aujourdhui')
    with p.expect_response(lambda r: 'automatismes/pause' in r.url) as attendu:
        p.locator('#a92-pause').click()
    verifier(attendu.value.ok and attendu.value.json().get('paused') is False, 'la reprise est refusée')
    s.aller('/aujourdhui')
    verifier(p.locator('#a92-pause').get_attribute('data-paused') == '0', 'la reprise n’est pas affichée après rechargement')
    banc.travailler()
    verifier(banc.modele.nombre() == avant + 1, 'après la reprise, %d appel(s) au modèle au lieu d’un seul' % (banc.modele.nombre() - avant))
    verifier(_propositions(banc, doc)[0]['state'] == 'proposee', 'la demande n’a pas abouti après la reprise')
    s.propre('pause et reprise')


def p02c_annulation(banc, s):
    """Annulation d'une demande : elle reste annulée après rechargement ; « Relancer » crée une nouvelle demande."""
    p = s.page
    doc = banc.semer_livrable()
    s.connecter()
    _proposition(s, doc)
    premiere = _propositions(banc, doc)[0]
    s.aller('/aujourdhui')
    p.once('dialog', lambda dialog: dialog.accept())
    with p.expect_response(lambda r: 'm530/cancel' in r.url) as attendu:
        p.locator('[data-cancel-job="%d"]' % premiere['job_id']).first.click()
    verifier(attendu.value.ok, 'l’annulation est refusée (HTTP %d)' % attendu.value.status)
    banc.travailler()
    s.aller('/livrables/' + doc['document_id'])
    verifier(_etat_travail(banc, premiere['job_id']) == 'cancelled', 'le travail annulé n’est pas resté annulé')
    verifier(_propositions(banc, doc)[0]['state'] == 'annule', 'la demande annulée n’est pas restée annulée après rechargement')
    verifier(banc.modele.nombre() == 0, 'le modèle a été appelé pour une demande annulée')
    relancer = p.locator('form[data-api$="/modifier-ia"] button', has_text='Relancer')
    verifier(relancer.count() == 1, 'aucun bouton « Relancer » pour la demande annulée')
    envoyer(p, '/modifier-ia', relancer.click, 'la relance')
    props = _propositions(banc, doc)
    verifier(len(props) == 2 and props[0]['state'] == 'annule' and props[1]['id'] != premiere['id'] and props[1]['job_id'] != premiere['job_id'],
             'la relance n’a pas créé une nouvelle demande distincte (%s)' % [x['state'] for x in props])
    banc.travailler()
    verifier(_propositions(banc, doc)[1]['state'] == 'proposee' and banc.modele.nombre() == 1, 'la nouvelle demande n’a pas abouti')
    s.aller('/livrables/' + doc['document_id'])
    verifier(_propositions(banc, doc)[0]['state'] == 'annule', 'l’ancienne demande a été réactivée')
    s.propre('annulation et relance')


def p02d_reprise_erreur(banc, s):
    """Erreur simulée du fournisseur : la demande échoue sans rafale d'essais ; « Relancer » fait un seul nouvel essai."""
    p = s.page
    doc = banc.semer_livrable()
    s.connecter()
    banc.modele.pannes = 1
    _proposition(s, doc)
    for _ in range(3):
        banc.travailler()
    verifier(banc.modele.nombre() == 1, 'erreur du fournisseur : %d appel(s) au lieu d’un seul (rafale)' % banc.modele.nombre())
    verifier(_propositions(banc, doc)[0]['state'] == 'echec', 'la demande n’est pas marquée en échec')
    s.aller('/livrables/' + doc['document_id'])
    relancer = p.locator('form[data-api$="/modifier-ia"] button', has_text='Relancer')
    verifier(relancer.count() == 1, 'aucun bouton « Relancer » après l’échec')
    envoyer(p, '/modifier-ia', relancer.dblclick, 'la relance')    # double clic impatient : une seule nouvelle demande attendue
    for _ in range(3):
        banc.travailler()
    props = _propositions(banc, doc)
    verifier(banc.modele.nombre() == 2, 'après la relance, %d appel(s) au modèle au lieu de 2 (un échec, un essai)' % banc.modele.nombre())
    verifier([x['state'] for x in props] == ['echec', 'proposee'], 'états des demandes inattendus : %s' % [x['state'] for x in props])
    s.propre('reprise après erreur')


def p02e_facture(banc, s):
    """Facture préparée en brouillon depuis la page Facturation : enregistrée, sans numéro, rien d'envoyé."""
    p = s.page
    s.connecter()
    s.aller('/facturation')
    p.click('details.b55-new > summary')
    formulaire = p.locator('form[data-api="m5653/document/preparer"]')
    formulaire.locator('input[name=type][value=facture]').check()
    formulaire.locator('select[name=matter]').select_option(MATTERS[0][0])
    envoyer(p, 'm5653/document/preparer', formulaire.locator('button[type=submit]').click, 'la préparation du brouillon')
    s.aller('/facturation')
    with banc.desk() as d:
        docs = [dict(r) for r in d.db.execute('SELECT * FROM b53_documents')]
    verifier(len(docs) == 1, '%d document(s) financier(s) au lieu d’un' % len(docs))
    doc = docs[0]
    verifier(doc['kind'] == 'facture' and doc['matter'] == MATTERS[0][0] and doc['state'] == 'brouillon',
             'le document n’est pas une facture en brouillon du dossier : %s/%s/%s' % (doc['kind'], doc['matter'], doc['state']))
    verifier(not str(doc.get('number') or '').strip(), 'un numéro fiscal a été attribué à un brouillon')
    verifier(not any(str(doc.get(k) or '').strip() for k in ('issue_date', 'issue_key', 'delivery_op', 'remote_status', 'approved_at')),
             'le brouillon porte une trace d’émission, d’approbation ou d’envoi')
    verifier(p.locator('#ax-main', has_text='Brouillon').count() >= 1, 'le brouillon n’apparaît pas sur la page après rechargement')
    s.propre('facture en brouillon')


def p02f_livrable(banc, s):
    """Document du registre ouvert depuis la liste, puis téléchargé : octets identiques au fichier du dossier."""
    p = s.page
    doc = banc.semer_livrable()
    s.connecter()
    s.aller('/livrables')
    lien = p.locator('#l89-liste a[href*="/livrables/%s"]' % doc['document_id']).first
    verifier(lien.count() == 1, 'le document fictif n’est pas dans la liste du registre')
    with p.expect_navigation():
        lien.click()
    p.wait_for_load_state('networkidle')
    verifier(p.locator('h1', has_text='Contrat ' + MATTERS[0][1]).count() == 1, 'la page du document ne s’ouvre pas')
    with p.expect_download() as attendu:
        p.locator('a.ax-btn', has_text='Télécharger').click()
    telechargement = attendu.value
    brut = Path(telechargement.path()).read_bytes()
    verifier(hashlib.sha256(brut).hexdigest() == doc['sha256'], 'le fichier téléchargé diffère du document du dossier (%d octets)' % len(brut))
    verifier(telechargement.suggested_filename.endswith('.docx'), 'nom de fichier téléchargé inattendu : %s' % telechargement.suggested_filename)
    s.propre('ouverture et téléchargement')


def _liens_du_menu(s):
    """Pages du menu latéral et de « Toutes les fonctions », sans les liens qui déconnectent, téléchargent ou sortent du banc."""
    p, base = s.page, s.banc.url
    liens = set()
    for chemin, selecteur in (('/aujourdhui', 'aside a[href]'), ('/toutes-les-fonctions', 'aside a[href], #ax-main a[href]')):
        s.aller(chemin)
        for href in p.eval_on_selector_all(selecteur, 'els => els.map(e => e.href)'):
            href = href.split('#', 1)[0]
            if not href.startswith(base + '/') or re.search(r'/(deconnexion|logout|api440|api/|static/|live/)', href[len(base):]):
                continue
            liens.add(href[len(base):])
    return sorted(liens)


def p02g_menu(banc, s):
    """Tour du menu : chaque page se charge (HTTP 200) sans erreur JavaScript, sans erreur CSP et sans requête externe."""
    s.connecter()
    s.propre('Aujourd’hui')
    pages = _liens_du_menu(s)
    verifier(len(pages) >= 10, 'menu trop court : %d page(s)' % len(pages))
    fautes = []
    for chemin in pages:
        reponse = s.aller(chemin)
        if reponse is None or reponse.status != 200:
            fautes.append('%s : HTTP %s' % (chemin, reponse.status if reponse else '?'))
        try:
            s.propre(chemin)
        except Echec as ex:
            fautes.append(str(ex))
    verifier(not fautes, '%d page(s) en défaut sur %d : %s' % (len(fautes), len(pages), ' || '.join(fautes[:8])))
    print('    %d pages du menu parcourues' % len(pages))


# Relevé du son joué (tampon audio ou média blob:), injecté avant les scripts de la page.
AUDIO_JS = """(() => {
  window.__bancAudio = [];
  const start = AudioBufferSourceNode.prototype.start;
  AudioBufferSourceNode.prototype.start = function (...a) {
    try { const b = this.buffer; let m = 0; if (b) for (let c = 0; c < b.numberOfChannels; c++) { const d = b.getChannelData(c); for (let i = 0; i < d.length; i++) m = Math.max(m, Math.abs(d[i])); }
          window.__bancAudio.push({type: 'buffer', samples: b ? b.length : 0, peak: m}); } catch (e) {}
    return start.apply(this, a);
  };
  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function (...a) { window.__bancAudio.push({type: 'media', src: String(this.currentSrc || this.src).slice(0, 20)}); return play.apply(this, a); };
})();"""


def p02h_charge_voix(banc, s):
    """Page Charge du serveur (les travaux exécutés y figurent) ; page Voix ; lecture Brindille si le lot voix a livré ses fichiers."""
    p = s.page
    doc = banc.semer_livrable()
    s.connecter()
    _proposition(s, doc)
    banc.travailler()
    s.aller('/parametres?rubrique=poste')
    section = p.locator('#charge5693')
    verifier(section.count() == 1, 'section Charge du serveur absente')
    repli = p.locator('details:has(#charge5693) > summary')
    if repli.count() and not section.is_visible():
        repli.first.click()                    # rubrique repliée par défaut : la dépliée comme l'utilisateur
    verifier(section.is_visible(), 'la section Charge du serveur ne s’affiche pas')
    lignes = section.locator('table:has(caption:has-text("Travaux créés")) tbody tr').all_inner_texts()
    verifier(any(re.search(r'\t[1-9]\d*\t', '\t' + l.replace('\n', '\t') + '\t') for l in lignes),
             'le travail exécuté pendant le parcours n’apparaît pas dans « Travaux créés » : %s' % lignes[:5])
    s.propre('Charge du serveur')
    reponse = s.aller('/parametres?rubrique=voix')
    verifier(reponse is not None and reponse.status == 200, 'page Voix indisponible')
    s.propre('Voix')
    if not (ROOT / 'agent' / 'static' / 'brindille' / 'tts.js').is_file():
        print('    lecture Brindille ignorée : le lot voix n’a pas livré agent/static/brindille/')
        return
    page = s.context.new_page()
    page.add_init_script(AUDIO_JS)
    page.on('pageerror', s._erreur_js)
    page.on('console', s._console)
    page.goto(banc.url + '/parametres?rubrique=voix', wait_until='networkidle')
    verifier(page.locator('input[value=brindille], option[value=brindille]').count() >= 1, 'Brindille n’est pas proposée sur la page Voix')
    essai = page.locator('[data-v94-ecouter]')
    if not essai.count():
        essai = page.get_by_role('button', name=re.compile(r'(Essayer|Écouter|Tester)', re.I))
    verifier(essai.count() >= 1, 'aucun bouton d’essai d’écoute sur la page Voix')
    essai.first.click()
    # Son joué (tampon ou média blob:), ou fin de l'essai signalée par la page (voix utilisée, ou erreur)
    # Attente par relevés successifs : ``wait_for_function`` évalue une chaîne, ce que la CSP de la page refuse (pas de 'unsafe-eval').
    fin = time.monotonic() + 180
    while time.monotonic() < fin and not page.evaluate('() => (window.__bancAudio || []).some(a => a.type === "media" || a.peak > 0) || '
                                                       '!!document.querySelector("[data-v94-etat][data-v94-voix], [data-v94-etat][data-v94-erreur]")'):
        page.wait_for_timeout(250)
    page.wait_for_timeout(500)
    joues = page.evaluate('window.__bancAudio')
    etat = page.evaluate('(() => { const n = document.querySelector("[data-v94-etat]"); return n ? Object.assign({}, n.dataset) : {}; })()')
    verifier(not etat.get('v94Erreur'), 'la lecture Brindille a échoué : %s' % etat.get('v94Erreur'))
    verifier(etat.get('v94Voix', 'brindille') == 'brindille', 'la lecture n’a pas été faite par Brindille mais par « %s »' % etat.get('v94Voix'))
    verifier(any((a.get('type') == 'buffer' and a.get('samples', 0) > 0 and a.get('peak', 0) > 0.001) or (a.get('type') == 'media' and a.get('src', '').startswith('blob:'))
                 for a in joues), 'la lecture Brindille n’a produit aucun son : %s' % joues[:3])
    page.close()
    s.propre('lecture Brindille')


PARCOURS = (('P02a', 'connexion puis déconnexion', p02a_connexion), ('P02b', 'pause de l’IA depuis Aujourd’hui', p02b_pause),
            ('P02c', 'annulation puis relance d’une demande', p02c_annulation), ('P02d', 'reprise après une erreur du fournisseur', p02d_reprise_erreur),
            ('P02e', 'facture en brouillon', p02e_facture), ('P02f', 'ouverture et téléchargement d’un document', p02f_livrable),
            ('P02g', 'tour du menu', p02g_menu), ('P02h', 'Charge du serveur et Voix', p02h_charge_voix))


# ------------------------------------------------------------------------------------------------ lancement
def lancer(navigateur, sortie, choisis=(), voir=False):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print('Playwright pour Python est absent : pip install playwright (outil de développement seulement).', file=sys.stderr)
        return 2
    import logging
    # « Task queue depth » et flux d'activité coupés à l'arrêt du serveur de test : bruit sans rapport avec les parcours. Les erreurs de
    # l'application restent écrites par elle-même (``log_interface_error``) et toute réponse 500 fait échouer le parcours par la console.
    logging.getLogger('waitress').setLevel(logging.CRITICAL)
    sortie = Path(sortie)
    sortie.mkdir(parents=True, exist_ok=True)
    resultats = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not voir, **({'channel': 'msedge'} if navigateur == 'msedge' else {}))
        try:
            for ident, libelle, fonction in PARCOURS:
                if choisis and ident not in choisis:
                    continue
                base = tempfile.mkdtemp(prefix='axh-parcours-')
                banc, context, session, debut = Banc(base), None, None, time.monotonic()
                try:
                    banc.preparer()
                    banc.demarrer()
                    context = browser.new_context(viewport={'width': 1366, 'height': 900}, accept_downloads=True, locale='fr-FR')
                    session = Session(banc, context)
                    print('%s %s…' % (ident, libelle), flush=True)
                    fonction(banc, session)
                    resultats.append((ident, 'réussi', time.monotonic() - debut))
                    print('  réussi (%.1f s)' % (time.monotonic() - debut), flush=True)
                except Exception as ex:   # noqa: BLE001 — tout échec est rapporté avec sa capture et la console
                    resultats.append((ident, 'échec', time.monotonic() - debut))
                    print('  ÉCHEC : %s' % ex, flush=True)
                    if not isinstance(ex, Echec):
                        traceback.print_exc()
                    if session is not None:
                        try:
                            session.capture(sortie / ('%s-echec.png' % ident))
                        except Exception:   # noqa: BLE001 — la capture ne remplace jamais le motif
                            pass
                        (sortie / ('%s-console.log' % ident)).write_text('\n'.join(session.console + ['--- requêtes externes bloquées'] + session.externes)
                                                                         + '\n--- erreur\n' + str(ex) + '\n', encoding='utf-8')
                    break
                finally:
                    if context is not None:
                        context.close()
                    banc.arreter()
                    shutil.rmtree(base, ignore_errors=True)
        finally:
            browser.close()
    (sortie / 'resultats.json').write_text(json.dumps([{'parcours': i, 'resultat': r, 'secondes': round(t, 1)} for i, r, t in resultats],
                                                      ensure_ascii=False, indent=1), encoding='utf-8')
    return 0 if resultats and all(r == 'réussi' for _, r, _ in resultats) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description='Parcours navigateur d’AxiorHub (outil de développement, jamais sur le serveur du cabinet).')
    parser.add_argument('--navigateur', choices=('chromium', 'msedge'), default='msedge' if os.name == 'nt' else 'chromium')
    parser.add_argument('--sortie', default=str(Path(tempfile.gettempdir()) / 'axiorhub-parcours'))
    parser.add_argument('--parcours', default='', help='identifiants séparés par des virgules (P02a,P02c…) ; vide : tous')
    parser.add_argument('--voir', action='store_true', help='navigateur visible')
    args = parser.parse_args(argv)
    choisis = tuple(x.strip() for x in args.parcours.split(',') if x.strip())
    return lancer(args.navigateur, args.sortie, choisis, args.voir)


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    sys.exit(main())
