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


def _semis5695():
    """Semis fictifs de la mesure d'Aujourd'hui (``scripts/mesure-aujourdhui.py``) : mêmes tables, mêmes colonnes."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('mesure_aujourdhui5695', ROOT / 'scripts' / 'mesure-aujourdhui.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _nombre5695(p, rubrique):
    texte = p.locator('#ax95-zone li[data-rubrique="%s"] .ax95-nombre' % rubrique).inner_text().strip()
    return int(texte) if texte.isdigit() else None


CACHE5695 = """(hidden) => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => hidden});
                         document.dispatchEvent(new Event('visibilitychange')); }"""


def p95a_actuellement(banc, s):
    """5.6.95 (A01, A02) : encadré « Actuellement » en tête d'Aujourd'hui ; réponse 304 quand rien ne change, 200 et nombre à jour sinon ;
    Arrêter / Reprendre mémorisé ; mise à jour toutes les 60 s, suspendue onglet caché (horloge simulée) ; Écouter puis Arrêter la lecture."""
    from datetime import datetime, timedelta, timezone
    semis = _semis5695()
    maintenant = datetime.now(timezone.utc)
    banc.semer_livrable()
    with banc.desk() as d:
        semis.schemas(d)
        semis.evenement(d, 'banc-ev1', 'Audience fictive de mise en état', maintenant + timedelta(hours=3), matter='DOS-001')
        semis.tache(d, 'banc-t1', 'Relire les conclusions fictives du dossier DURAND contre MARTIN, version longue pour vérifier la troncature du titre',
                    maintenant.date().isoformat(), 'DOS-001')
        semis.courriel(d, 'banc-m1', 'Demande de pièces complémentaires', 'DOS-002')
        d.db.commit()
    p = s.page
    p.clock.install()                          # horloge simulée : les 60 s du rafraîchissement sont avancées sans attendre
    appels = []
    p.on('request', lambda r: appels.append(r.url) if '/actuellement/etat' in r.url else None)
    s.connecter()
    boite = p.locator('#ax95-actuellement')
    verifier(boite.count() == 1, 'encadré « Actuellement » absent d’Aujourd’hui')
    haut, travail = boite.bounding_box(), p.locator('#a89-travail').bounding_box()
    verifier(haut and travail and haut['y'] < travail['y'], 'l’encadré n’est pas en tête de la page (avant Travail préparé)')
    verifier(p.locator('#ax95-zone li[data-rubrique]').count() == 8, 'l’encadré n’a pas ses 8 rubriques')
    nombres = {r: _nombre5695(p, r) for r in ('agenda', 'taches', 'courriels', 'documents')}
    verifier(nombres == {'agenda': 1, 'taches': 1, 'courriels': 1, 'documents': 1}, 'nombres inattendus : %s' % nombres)
    if getattr(banc, 'sortie', None):
        boite.screenshot(path=str(Path(banc.sortie) / 'P95a-actuellement.png'))
        p.emulate_media(color_scheme='dark')
        boite.screenshot(path=str(Path(banc.sortie) / 'P95a-actuellement-sombre.png'))
        p.emulate_media(color_scheme='light')
    s.propre('encadré affiché')
    # Actualiser sans changement : 304, la zone n'est pas remplacée
    with p.expect_response(lambda r: '/actuellement/etat' in r.url) as attendu:
        p.click('[data-ax95="actualiser"]')
    verifier(attendu.value.status == 304, 'sans changement, la mise à jour n’a pas répondu 304 (HTTP %d)' % attendu.value.status)
    # Une donnée change : 200, nombre à jour sans recharger la page
    with banc.desk() as d:
        semis.courriel(d, 'banc-m2', 'Relance fictive du confrère', 'DOS-003')
        d.db.commit()
    with p.expect_response(lambda r: '/actuellement/etat' in r.url) as attendu:
        p.click('[data-ax95="actualiser"]')
    verifier(attendu.value.status == 200, 'après un nouveau courriel, la mise à jour a répondu %d' % attendu.value.status)
    p.wait_for_timeout(200)
    verifier(_nombre5695(p, 'courriels') == 2, 'le nombre de courriels n’a pas été mis à jour sans rechargement')
    s.propre('Actualiser')
    # Mise à jour automatique : une requête après 60 s ; aucune tant que l'onglet est caché ; une au retour
    avant = len(appels)
    p.clock.run_for(61000)
    p.wait_for_timeout(400)
    verifier(len(appels) == avant + 1, 'la mise à jour automatique n’a pas eu lieu après 60 s (%d requête(s))' % (len(appels) - avant))
    p.evaluate(CACHE5695, True)
    avant = len(appels)
    p.clock.run_for(300000)
    p.wait_for_timeout(400)
    verifier(len(appels) == avant, 'des mises à jour ont eu lieu onglet caché (%d)' % (len(appels) - avant))
    p.evaluate(CACHE5695, False)
    p.wait_for_timeout(400)
    verifier(len(appels) == avant + 1, 'au retour sur l’onglet, pas de mise à jour immédiate')
    s.propre('mise à jour automatique')
    # Arrêter / Reprendre : mémorisé pour la personne
    with p.expect_response(lambda r: '/actuellement/auto' in r.url) as attendu:
        p.click('[data-ax95="auto"]')
    verifier(attendu.value.ok and attendu.value.json().get('auto') is False, 'l’arrêt de la mise à jour est refusé')
    avant = len(appels)
    p.clock.run_for(180000)
    p.wait_for_timeout(400)
    verifier(len(appels) == avant, 'mise à jour automatique malgré l’arrêt')
    s.aller('/aujourdhui')
    verifier(p.locator('#ax95-actuellement').get_attribute('data-auto') == '0', 'l’arrêt n’est pas mémorisé après rechargement')
    verifier(p.locator('[data-ax95="auto"]').get_attribute('aria-pressed') == 'true', 'le bouton n’indique pas l’arrêt')
    with p.expect_response(lambda r: '/actuellement/auto' in r.url) as attendu:
        p.click('[data-ax95="auto"]')
    verifier(attendu.value.json().get('auto') is True, 'la reprise est refusée')
    s.propre('Arrêter / Reprendre')
    # Écouter : le texte de l'encadré part à la préparation de la voix commune (abréviations développées) ; Arrêter la lecture
    verifier(not p.locator('[data-ax95="taire"]').is_visible(), '« Arrêter la lecture » est affiché alors que rien n’est lu')
    with p.expect_request(lambda r: '/voix/preparer' in r.url and r.method == 'POST') as demande:
        p.click('[data-ax95="ecouter"]')
    corps = json.loads(demande.value.post_data or '{}')
    verifier(str(corps.get('text', '')).startswith('Actuellement, à '), 'Écouter n’envoie pas le texte de l’encadré : %r' % str(corps.get('text', ''))[:80])
    verifier(p.locator('[data-ax95="taire"]').is_visible(), 'le bouton « Arrêter la lecture » n’apparaît pas pendant la lecture')
    p.click('[data-ax95="taire"]')
    verifier(p.locator('[data-ax95="ecouter"]').is_visible(), 'le bouton Écouter ne revient pas après l’arrêt de la lecture')
    p.wait_for_timeout(500)
    s.propre('Écouter et Arrêter la lecture')
    with banc.desk() as d:
        verifier(not d.db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('pending','running')").fetchone()[0], 'un bouton de l’encadré a mis un travail en file')
    verifier(banc.modele.nombre() == 0, 'le modèle a été appelé pendant le parcours de l’encadré (%d)' % banc.modele.nombre())


def p95b_personnaliser(banc, s):
    """5.6.95 (A04) : « Personnaliser cette page » : case et nom sur la même ligne, Monter et Descendre juste après le nom ; Monter au clavier
    change l'ordre ; l'encadré « Actuellement » décoché puis enregistré n'est plus affiché. A03 : la veille juridique, ouverte par défaut,
    est repliée puis dépliée ; chaque état est enregistré pour la personne et retrouvé après rechargement, son contenu n'est jamais retiré."""
    p = s.page
    s.connecter()
    pli = 'details[data-pli="veille"]'
    verifier(p.locator(pli).count() == 1 and p.locator(pli).get_attribute('open') is not None, 'la veille juridique n’est pas ouverte par défaut')
    verifier(p.locator(pli + ' #c5632-veille').is_visible(), 'contenu de la veille invisible alors que le bloc est ouvert')
    for ouvert in (False, True):
        with p.expect_response(lambda r: '/actuellement/plis' in r.url) as attendu:
            p.click(pli + ' > summary')
        verifier(attendu.value.ok and attendu.value.json().get('ouvert') is ouvert, 'l’état du pli est refusé (%s)' % ('ouvert' if ouvert else 'replié'))
        if not ouvert and getattr(banc, 'sortie', None):
            p.locator(pli).screenshot(path=str(Path(banc.sortie) / 'P95b-veille-repliee.png'))
        s.aller('/aujourdhui')
        verifier((p.locator(pli).get_attribute('open') is not None) is ouvert, 'l’état %s du pli n’est pas retrouvé après rechargement' % ('ouvert' if ouvert else 'replié'))
        verifier(p.locator(pli + ' #c5632-veille').count() == 1, 'replier a retiré le contenu de la veille')
        verifier(p.locator(pli + ' #c5632-veille').is_visible() is ouvert, 'visibilité du contenu incohérente avec l’état du pli')
    s.propre('plis d’Aujourd’hui')
    p.click('#t88-panel > summary')
    lignes = p.locator('#t88-list > li')
    verifier(lignes.count() >= 10, 'liste des blocs incomplète')
    for i in range(lignes.count()):
        li = lignes.nth(i)
        case, nom, monter = li.locator('input[type=checkbox]').bounding_box(), li.locator('.t95-nom').bounding_box(), li.locator('[data-t88-move="-1"]').bounding_box()
        verifier(abs((case['y'] + case['height'] / 2) - (nom['y'] + nom['height'] / 2)) < 6, 'case et libellé décalés (ligne %d)' % (i + 1))
        verifier(case['x'] < nom['x'] and monter['x'] - (nom['x'] + nom['width']) < 260, 'Monter est loin du libellé (ligne %d)' % (i + 1))
    if getattr(banc, 'sortie', None):
        p.locator('#t88-panel').screenshot(path=str(Path(banc.sortie) / 'P95b-personnaliser.png'))
    p.locator('#t88-list > li[data-bloc="routines"] [data-t88-move="-1"]').focus()
    p.keyboard.press('Enter')
    verifier(p.locator('#t88-list > li').first.get_attribute('data-bloc') == 'routines', 'Monter au clavier ne change pas l’ordre')
    p.locator('#t88-list > li[data-bloc="actuellement"] input[type=checkbox]').uncheck()
    envoyer(p, 'tableau/enregistrer', lambda: p.click('#t88-panel form[data-api="m5688/tableau/enregistrer"] button[type=submit]'), 'l’enregistrement de la page')
    verifier(p.locator('#ax95-actuellement').count() == 0, 'l’encadré masqué s’affiche encore')
    s.propre('Personnaliser')


def p96k_mistral(banc, s):
    """5.6.96 (lot mistral, A01-A02) : carte « Mistral · clé Studio » dans Modèles d'IA. « Tester la connexion » vise sa propre route depuis le
    formulaire d'enregistrement ; une clé mal formée est refusée sur le serveur AVANT tout appel réseau (aucun appel à Mistral dans ce
    parcours), sans rechargement : la saisie reste en place. État « À connecter », quota « Non fourni », champ atteignable au clavier."""
    p = s.page
    s.connecter()
    s.aller('/ia-externe')
    carte = p.locator('#k96-mistral')
    verifier(carte.count() == 1 and carte.is_visible(), 'carte Mistral absente de Modèles d’IA')
    verifier('État : À connecter' in carte.inner_text() and 'Quota restant : Non fourni' in carte.inner_text(), 'état ou quota de la carte Mistral illisible')
    champ = carte.locator('input[name="cle"]')
    champ.focus()
    verifier(p.evaluate('document.activeElement && document.activeElement.name') == 'cle', 'champ de la clé hors d’atteinte au clavier')
    champ.fill('court')
    with p.expect_response(lambda r: '/m5682/mistral/tester' in r.url) as attendu:
        carte.locator('button[data-api="m5682/mistral/tester"]').click()
    verifier(attendu.value.status == 400 and attendu.value.json().get('error') == 'secret_fournisseur_invalide', 'clé mal formée non refusée par le serveur')
    p.wait_for_timeout(800)
    verifier(champ.input_value() == 'court', 'la page a été rechargée : la saisie est perdue')
    s.erreurs[:] = [x for x in s.erreurs if 'status of 400' not in x]      # refus attendu ci-dessus (le navigateur le note en console)
    if getattr(banc, 'sortie', None):
        carte.screenshot(path=str(Path(banc.sortie) / 'P96k-mistral.png'))
    s.propre('carte Mistral')



class _FauxOpenWeather96:
    """5.6.96 (lot accueil) : faux OpenWeather du banc, dans le processus du serveur de test (``meteo5696.HTTP_FACTORY``) : aucun appel réseau, clé fictive."""

    def __init__(self):
        self.appels = []

    def request(self, method, url, data=None, headers=None, limit=None):
        from datetime import datetime, timezone
        from urllib.parse import urlsplit
        self.appels.append(url)
        if urlsplit(url).path == '/geo/1.0/direct':
            return json.dumps([{'name': 'Valbrune', 'lat': 45.1234, 'lon': 4.5678, 'country': 'FR', 'state': 'Région fictive'}]).encode()
        t0 = int(datetime.now(timezone.utc).timestamp()) // 10800 * 10800
        codes = (800, 500, 803, 601, 211, 211)
        liste = [{'dt': t0 + i * 10800, 'main': {'temp_min': 4 + i // 8, 'temp_max': 9 + i // 8}, 'weather': [{'id': codes[i // 8]}]} for i in range(44)]
        return json.dumps({'cod': '200', 'list': liste}).encode()


def _accueil96(banc, meteo=True):
    """Semis du lot accueil : prénom du compte, une échéance à vérifier, une échéance confirmée du jour, un document préparé, météo réglée (faux service)."""
    from datetime import date, timedelta
    from agent import assistant567, meteo5696
    semis = _semis5695()
    banc.semer_livrable()
    faux = _FauxOpenWeather96()
    meteo5696.HTTP_FACTORY = lambda base: faux
    meteo5696.vider_cache()
    with banc.desk() as d:
        semis.schemas(d)
        semis.echeance(d, 'banc-ech-verifier', 'DOS-001', (date.today() + timedelta(days=3)).isoformat(), 'a_confirmer')
        semis.echeance(d, 'banc-ech-confirmee', 'DOS-002', date.today().isoformat(), 'confirmee')
        d.db.commit()
        assistant567.save_profile(d, {'prenom': 'Claire'}, 'cabinet')
        if meteo:
            meteo5696.tester_cle(d, 'cabinet', 'cle-de-banc-fictive-0001')
            meteo5696.choisir_lieu(d, 'cabinet', {'ville': 'Valbrune', 'pays': 'FR', 'latitude': 45.123, 'longitude': 4.567})
            meteo5696.activer(d, 'cabinet', True)
    faux.appels.clear()
    return faux


def _sans_recouvrement(a, b):
    return a['x'] + a['width'] <= b['x'] or b['x'] + b['width'] <= a['x'] or a['y'] + a['height'] <= b['y'] or b['y'] + b['height'] <= a['y']


def p96f1_accueil(banc, s):
    """5.6.96 (lot accueil, I01, F01, F02, F04) : en-tête « Bonjour Claire, nous sommes le…, il est… » ; carte météo chargée après l'affichage (faux
    OpenWeather), à droite sans couvrir aucun bouton, sous la salutation en largeur réduite ; ordre Actuellement → rang (2 parts / 1 part) → Travail
    préparé ; « Actuellement » en deux colonnes de quatre lignes à 1920 × 950 ; Ma journée : échéance confirmée seule ; carte masquée par la personne,
    plus rien de chargé ; 125 %, mobile et mode sombre sans défilement horizontal."""
    from agent import meteo5696
    faux = _accueil96(banc)
    try:
        p = s.page
        p.set_viewport_size({'width': 1920, 'height': 950})
        s.connecter()
        p.wait_for_selector('[data-f96-meteo][data-statut="ok"]', timeout=10000)
        bandeau = p.locator('#f96-bandeau').inner_text()
        verifier(re.match(r'^(Bonjour|Bon après-midi|Bonsoir) Claire, nous sommes le .+, il est \d{1,2} h \d{2}\.$', bandeau), 'bandeau inattendu : %r' % bandeau)
        verifier(len([u for u in faux.appels if '/data/2.5/forecast' in u]) == 1, 'la météo n’a pas été demandée une seule fois : %s' % len(faux.appels))
        carte = p.locator('#f96-meteo').bounding_box()
        for bouton in ('#a92-pause', '.a89-mission'):
            boite = p.locator(bouton).bounding_box()
            verifier(boite and _sans_recouvrement(carte, boite), 'la carte météo recouvre %s' % bouton)
        verifier(carte['x'] > 960 and carte['y'] < 300, 'la carte météo n’est pas à droite de l’en-tête : %s' % carte)
        verifier('Données météo fournies par OpenWeather' in p.locator('#f96-meteo').inner_text(), 'attribution absente')
        ys = [p.locator(sel).bounding_box()['y'] for sel in ('#ax95-actuellement', '.f96-rang', '#a89-travail')]
        verifier(ys == sorted(ys), 'ordre des zones inattendu : %s' % ys)
        gauche, droite = p.locator('#f96-decider').bounding_box(), p.locator('.f96-journee').bounding_box()
        verifier(1.6 < gauche['width'] / droite['width'] < 2.4 and abs(gauche['y'] - droite['y']) < 40, 'rang non réparti en 2/1 : %s / %s' % (gauche, droite))
        xs = p.eval_on_selector_all('#ax95-zone li[data-rubrique]', 'l => l.map(e => Math.round(e.getBoundingClientRect().x))')
        verifier(len(xs) == 8 and len(set(xs[:4])) == 1 and len(set(xs[4:])) == 1 and xs[4] > xs[0], '« Actuellement » n’est pas en deux colonnes de quatre : %s' % xs)
        verifier(p.locator('#f96-lignes-zone a[data-f96-action="echeance"]').count() == 1, 'l’échéance à vérifier n’est pas dans le rang')
        journee = p.locator('.f96-echeances-jour').inner_text()
        verifier('banc-ech-verifier' not in p.locator('.f96-echeances-jour').inner_html() and p.locator('.f96-echeances a').count() == 1, 'Ma journée : %r' % journee)
        if getattr(banc, 'sortie', None):
            p.screenshot(path=str(Path(banc.sortie) / 'P96F1-aujourdhui-1920.png'))
            p.emulate_media(color_scheme='dark')
            p.locator('.f96-tete').screenshot(path=str(Path(banc.sortie) / 'P96F1-entete-sombre.png'))
            p.emulate_media(color_scheme='light')
        s.propre('en-tête et rang à 1920 × 950')
        largeur = 'document.scrollingElement.scrollWidth <= window.innerWidth + 1'
        for vue, nom in (({'width': 1536, 'height': 760}, '125 %'), ({'width': 390, 'height': 844}, 'mobile')):
            p.set_viewport_size(vue)
            p.wait_for_timeout(200)
            verifier(p.evaluate(largeur), 'défilement horizontal en %s' % nom)
        b, c = p.locator('#f96-bandeau').bounding_box(), p.locator('#f96-meteo').bounding_box()
        verifier(c['y'] > b['y'] and _sans_recouvrement(c, p.locator('#a92-pause').bounding_box()), 'mobile : la carte n’est pas sous la salutation ou couvre la pause')
        p.set_viewport_size({'width': 1920, 'height': 950})
        # Masquer la carte : rien n'est plus chargé, même après rechargement
        p.click('#f96-meteo summary')
        with p.expect_response(lambda r: '/accueil/options' in r.url) as attendu:
            p.click('[data-f96-masquer]')
        verifier(attendu.value.ok, 'masquer la carte est refusé')
        verifier(p.locator('#f96-meteo').count() == 0, 'la carte reste affichée')
        demandes = []
        p.on('request', lambda r: demandes.append(r.url) if '/accueil/meteo' in r.url else None)
        s.aller('/aujourdhui')
        verifier(p.locator('[data-f96-meteo]').count() == 0 and not demandes, 'carte masquée encore chargée : %s' % demandes)
        s.propre('carte masquée')
        verifier(banc.modele.nombre() == 0, 'le modèle a été appelé sur Aujourd’hui (%d)' % banc.modele.nombre())
    finally:
        meteo5696.HTTP_FACTORY = None
        meteo5696.vider_cache()


def p96f2_fenetre(banc, s):
    """5.6.96 (lot accueil, I02) : une action d'Aujourd'hui s'ouvre en fenêtre flottante (<dialog>) depuis le fragment serveur ; titre, Fermer et
    Plein écran visibles sans défiler ; Échap ferme et rend le focus au bouton d'origine, sans recharger la page ; une saisie non enregistrée
    déclenche le choix Rester / Fermer sans enregistrer ; sur mobile, plein écran d'emblée."""
    from agent import meteo5696
    _accueil96(banc, meteo=False)
    try:
        p = s.page
        p.set_viewport_size({'width': 1920, 'height': 950})
        s.connecter()
        p.evaluate('window.__f96_page = 1')                   # marque : un rechargement de la page l'effacerait
        bouton = p.locator('#f96-lignes-zone a[data-f96-action="echeance"]')
        bouton.click()
        fenetre = p.locator('dialog.f96-fenetre[open]')
        fenetre.wait_for(timeout=5000)
        p.wait_for_selector('dialog.f96-fenetre .f96-echeance', timeout=5000)
        verifier('à vérifier' in fenetre.inner_text(), 'la fenêtre ne dit pas que la date est à vérifier')
        for sel in ('.f96-fenetre-titre', '.f96-fenetre-fermer', '.f96-fenetre-taille'):
            boite = fenetre.locator(sel).bounding_box()
            verifier(boite and boite['y'] >= 0 and boite['y'] + boite['height'] <= 950, '%s hors de la vue' % sel)
        fenetre.locator('.f96-fenetre-taille').click()
        verifier(abs(fenetre.bounding_box()['width'] - 1920) < 2, 'Plein écran n’occupe pas toute la largeur')
        if getattr(banc, 'sortie', None):
            p.screenshot(path=str(Path(banc.sortie) / 'P96F2-fenetre-echeance.png'))
        p.keyboard.press('Escape')
        verifier(p.locator('dialog.f96-fenetre[open]').count() == 0, 'Échap ne ferme pas la fenêtre')
        verifier(p.evaluate('document.activeElement && document.activeElement.getAttribute("data-f96-cle")') == 'banc-ech-verifier', 'le focus n’est pas rendu au bouton d’origine')
        verifier(p.evaluate('window.__f96_page') == 1, 'la page Aujourd’hui a été rechargée')
        s.propre('fenêtre d’une échéance')
        # Document : même détail que la page complète ; saisie non enregistrée → choix explicite
        p.locator('#a89-travail a[data-f96-action="document"]:visible').first.click()
        p.wait_for_selector('dialog.f96-fenetre[open] .l89-detail', timeout=10000)
        fenetre.locator('details.l89-ia > summary').click()
        fenetre.locator('details.l89-ia textarea[name=instruction]').fill('Consigne fictive non envoyée')
        p.keyboard.press('Escape')
        verifier(fenetre.locator('.f96-quitter').is_visible(), 'aucune question avant de perdre la saisie')
        fenetre.locator('.f96-q-rester').click()
        verifier(p.locator('dialog.f96-fenetre[open]').count() == 1, '« Rester » a fermé la fenêtre')
        fenetre.locator('.f96-fenetre-fermer').click()
        fenetre.locator('.f96-q-abandon').click()
        verifier(p.locator('dialog.f96-fenetre[open]').count() == 0, '« Fermer sans enregistrer » ne ferme pas')
        with banc.desk() as d:
            verifier(not d.db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('pending','running')").fetchone()[0], 'la fenêtre a mis un travail en file')
        s.propre('fenêtre d’un document')
        p.set_viewport_size({'width': 390, 'height': 844})
        p.locator('#f96-lignes-zone a[data-f96-action="echeance"]').click()
        p.wait_for_selector('dialog.f96-fenetre.f96-plein[open]', timeout=5000)
        p.keyboard.press('Escape')
        s.propre('fenêtre sur mobile')
        verifier(banc.modele.nombre() == 0, 'le modèle a été appelé (%d)' % banc.modele.nombre())
    finally:
        meteo5696.HTTP_FACTORY = None


def _semer_courriels96(banc):
    """5.6.96 (lot courriels) : deux courriels reçus, relevés par l'agent (état, rapport, fil), projetés comme le fait le collecteur ; option active."""
    from datetime import datetime, timedelta, timezone
    from email.message import EmailMessage
    from agent import integration
    from agent.common import digest
    from agent.mailbox import Mail
    from agent.state import State
    compte = '%s@%s' % (banc.config['mail']['username'], banc.config['mail']['host'])
    fils = []
    with banc.desk() as d:
        etat = State(d.c['state_dir'])
        for n, (dossier, client, objet) in enumerate((('DOS-001', 'DURAND', 'Demande de rendez-vous'), ('DOS-002', 'MARTIN', 'Pièces complémentaires'))):
            m = EmailMessage()
            m['From'], m['To'], m['Subject'], m['Message-ID'] = '%s <%s@example.test>' % (client, client.lower()), 'cabinet@example.test', objet, '<banc96-%d@example.test>' % n
            m.set_content('Bonjour Maître, message fictif.')
            mail = Mail(str(200 + n), '7', 'INBOX', set(), datetime.now(timezone.utc) - timedelta(hours=n + 1), m)
            cle = mail.key(compte)
            etat.set(cle, digest(compte + mail.mid), digest(compte + mail.root), 'observed', '')
            etat.report(cle, {'key': cle, 'subject': objet, 'sender': client.lower() + '@example.test', 'source_uid': mail.uid, 'source_mailbox': 'INBOX',
                              'received_at': mail.timestamp.isoformat(), 'matter': dossier, 'account_key': digest(compte)})
            fils.append(digest(compte + mail.root))
        etat.db.close()
        integration.sync_work_items(d)
        d.setting('courriels5696:vue_assistee', True)
    return fils


def p96c_courriels(banc, s):
    """5.6.96 (C02, I03, C07) : vue « À traiter avec le Pilote » à 1920 × 1080 puis en mobile : ouvrir, lire (messagerie absente : refus clair,
    aucune connexion), reporter (focus dans le champ), écarter (motif exigé près du champ), revenir à la liste filtrée. Rien n'est envoyé."""
    _semer_courriels96(banc)
    p = s.page
    p.set_viewport_size({'width': 1920, 'height': 1080})
    s.connecter()
    s.aller('/courriels?vue=assistante')
    verifier(p.locator('.bx5625-tabs a').count() == 3, 'les trois onglets de Courriels ne sont pas affichés')
    verifier(p.locator('.cx96-ligne').count() == 2, 'la liste n’a pas ses deux fils')
    verifier('DURAND' in p.locator('.cx96-lignes').inner_text() or 'DOS-001' in p.locator('.cx96-lignes').inner_text(), 'le dossier n’est pas affiché')
    s.propre('liste')
    p.locator('.cx96-ligne').first.click()
    p.wait_for_load_state('networkidle')
    verifier(p.locator('.cx96-detail h2').count() == 1, 'le détail du fil ne s’ouvre pas')
    p.locator('.cx96-pourquoi summary').click()
    p.locator('[data-cx96-lire="fil"]').click()
    zone = p.locator('[data-cx96-zone="fil"]')
    zone.filter(has_text='Messagerie non configurée').wait_for(timeout=10000)            # refus clair, sans connexion tentée
    verifier('Lecture' not in zone.inner_text(), 'la lecture du fil reste en attente')
    if getattr(banc, 'sortie', None):
        p.screenshot(path=str(Path(banc.sortie) / 'P96c-courriels-1920.png'))
    s.propre('lecture du fil')
    p.locator('.cx96-menu > summary').click()
    p.locator('.cx96-geste > summary', has_text='Reporter').click()
    p.wait_for_timeout(300)                    # l'événement « toggle » est asynchrone
    verifier(p.evaluate('document.activeElement && document.activeElement.name') == 'jusqua', 'le focus n’est pas dans le champ du report')
    from datetime import date, timedelta
    p.locator('.cx96-geste[open] input[name=jusqua]').fill((date.today() + timedelta(days=2)).isoformat() + 'T09:30')
    envoyer(p, '/courriels/decision', lambda: p.locator('.cx96-geste[open] button[type=submit]').click(), 'reporter')
    verifier(p.locator('.cx96-ligne').count() == 1, 'le fil reporté est resté dans « À traiter »')
    s.propre('report')
    p.locator('.cx96-ligne').first.click()
    p.wait_for_load_state('networkidle')
    p.locator('.cx96-menu > summary').click()
    p.locator('.cx96-geste > summary', has_text='Écarter').click()
    p.wait_for_timeout(300)
    motif = p.locator('.cx96-geste[open] input[name=motif]')
    verifier(p.evaluate('document.activeElement && document.activeElement.name') == 'motif', 'le focus n’est pas dans le motif')
    p.locator('.cx96-geste[open] button[type=submit]').click()
    verifier(motif.get_attribute('aria-invalid') == 'true' or p.evaluate('!document.querySelector(".cx96-geste[open] input[name=motif]").checkValidity()'),
             'le motif vide n’est pas signalé au champ')
    motif.fill('Déjà traité par téléphone')
    envoyer(p, '/courriels/decision', lambda: p.locator('.cx96-geste[open] button[type=submit]').click(), 'écarter')
    verifier(p.locator('.cx96-ligne').count() == 0, 'le fil écarté est resté dans la liste')
    s.aller('/courriels?vue=assistante&filtre=reportes')
    verifier(p.locator('.cx96-ligne').count() == 1, 'le filtre « Reportés » ne retrouve pas le fil')
    p.locator('.cx96-ligne').first.click()
    p.wait_for_load_state('networkidle')
    with p.expect_navigation():
        p.locator('a.ui89-retour').click()
    verifier('filtre=reportes' in p.url and 'fil=' not in p.url, 'le retour ne rend pas la liste filtrée : %s' % p.url)
    s.propre('retour à la liste')
    p.set_viewport_size({'width': 390, 'height': 844})
    p.locator('.cx96-ligne').first.click()
    p.wait_for_load_state('networkidle')
    verifier(not p.locator('.cx96-liste').is_visible() and p.locator('.cx96-retour').is_visible(), 'petite largeur : le détail ne remplace pas la liste')
    if getattr(banc, 'sortie', None):
        p.screenshot(path=str(Path(banc.sortie) / 'P96c-courriels-mobile.png'))
    p.locator('.cx96-retour').click()
    p.wait_for_load_state('networkidle')
    verifier(p.locator('.cx96-liste').is_visible(), 'petite largeur : « Retour à la liste » ne rend pas la liste')
    s.propre('mobile')
    with banc.desk() as d:
        verifier(d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='prepare_reply'").fetchone()[0] == 0, 'un travail de préparation a été créé sans demande')
        verifier(d.db.execute('SELECT COUNT(*) FROM courriels5696_evenements').fetchone()[0] == 2, 'décisions inattendues')


DANS_LA_VUE96 = """(sel) => { const n = document.querySelector(sel); if (!n || !n.getClientRects().length) return false;
    const r = n.getBoundingClientRect(), vv = window.visualViewport || {width: innerWidth, height: innerHeight, offsetTop: 0, offsetLeft: 0};
    if (r.width < 1 || r.height < 1 || r.top < vv.offsetTop - 1 || r.left < -1 || r.bottom > vv.offsetTop + vv.height + 1 || r.right > vv.width + 1) return false;
    const x = Math.min(r.left + r.width / 2, vv.width - 1), y = Math.min(r.top + r.height / 2, vv.offsetTop + vv.height - 1), e = document.elementFromPoint(x, y);
    return !!e && (e === n || n.contains(e) || e.contains(n)); }"""
SANS_DEFILEMENT_HORIZONTAL96 = 'document.scrollingElement.scrollWidth <= window.innerWidth + 1'


def _volet_visible96(p, lieu):
    """Champ, Envoyer, micro et Fermer visibles sans défiler, non recouverts ; le volet n'a pas fait défiler la page."""
    for sel, nom in (('#ws-ai-question', 'le champ Instruction'), ('#ws-ai-send', 'Envoyer'), ('#ws-ai-dock .dictation-bar .micro-button', 'le micro'),
                     ('#ws-ai-close', 'Fermer'), ('#ws-ai-titre', 'le titre')):
        verifier(p.evaluate(DANS_LA_VUE96, sel), '%s : %s hors de la vue ou recouvert' % (lieu, nom))
    verifier(p.evaluate(SANS_DEFILEMENT_HORIZONTAL96), '%s : défilement horizontal' % lieu)


def p96a_volet(banc, s):
    """5.6.96 (lot affichage, U01, U04, I04, I07) : volet de l'assistant à 1920 × 950 : dès le clic, champ, Envoyer, micro et Fermer visibles sans
    défiler, aussi avec un historique long ; un seul titre ; page non déplacée ; fond inerte, Tab reste dans le volet ; Échap ferme et rend le
    focus au lanceur ; Ctrl+Entrée envoie une seule fois ; brouillon gardé à la fermeture ; 125 %, 200 %, sombre et mobile (clavier logiciel
    simulé par une hauteur réduite) : Fermer, Envoyer et micro utilisables, aucun défilement horizontal."""
    p = s.page
    p.set_viewport_size({'width': 1920, 'height': 950})
    s.connecter()
    s.aller('/dossiers')
    p.evaluate('window.scrollTo(0, 120)')
    avant = p.evaluate('window.scrollY')
    p.click('#ws-ai-launcher')
    p.wait_for_selector('#ws-ai-dock:not([hidden])')
    p.wait_for_timeout(250)
    _volet_visible96(p, '1920 × 950')
    verifier(p.evaluate('document.activeElement && document.activeElement.id') == 'ws-ai-question', 'le focus n’est pas sur le champ Instruction à l’ouverture')
    verifier(p.evaluate('window.scrollY') == avant, 'l’ouverture du volet a fait défiler la page (%s → %s)' % (avant, p.evaluate('window.scrollY')))
    titre = p.locator('#ws-ai-titre').inner_text().strip()
    verifier(titre == 'Assistant AxiorHub', 'titre du volet inattendu : %r' % titre)
    verifier('Pilote · Pilote' not in p.locator('#ws-ai-dock').inner_text(), 'titre en double')
    verifier(p.evaluate('document.getElementById("ws-main") ? !!document.getElementById("ws-main").closest("[inert]") : true'), 'le fond de la page n’est pas inerte')
    verifier(p.locator('#ws-ai-portee-dossier').inner_text() == 'Aucun dossier', 'contexte affiché inattendu')
    for _ in range(40):                                                        # Tab et Maj+Tab : le focus ne sort jamais du volet
        p.keyboard.press('Tab')
        verifier(p.evaluate('document.getElementById("ws-ai-dock").contains(document.activeElement)'), 'Tab a fait sortir le focus du volet')
    for _ in range(5):
        p.keyboard.press('Shift+Tab')
        verifier(p.evaluate('document.getElementById("ws-ai-dock").contains(document.activeElement)'), 'Maj+Tab a fait sortir le focus du volet')
    # historique long : le fil défile, le composeur reste
    p.evaluate("""() => { for (let i = 0; i < 30; i++) window.axiorhubPilot.addTurn('Question fictive ' + i + ' sur le dossier DURAND contre MARTIN',
        'Réponse fictive ' + i + ' : ' + 'texte de la réponse, '.repeat(40), 'text'); }""")
    p.wait_for_timeout(100)
    _volet_visible96(p, 'historique long')
    verifier(p.evaluate('(() => { const b = document.querySelector("#ws-ai-dock .ws-ai-body"); return b.scrollHeight > b.clientHeight; })()'), 'le fil long ne défile pas dans sa zone')
    if getattr(banc, 'sortie', None):
        p.screenshot(path=str(Path(banc.sortie) / 'P96a-volet-1920x950.png'))
    s.propre('volet ouvert à 1920 × 950')
    # Ctrl+Entrée : un seul envoi, même touche maintenue (répétition)
    creations = []
    p.on('request', lambda r: creations.append(r.url) if '/m567/mission/create' in r.url and r.method == 'POST' else None)
    p.fill('#ws-ai-question', 'Résume la situation fictive du dossier DURAND.')
    p.focus('#ws-ai-question')
    p.keyboard.down('Control')
    p.keyboard.press('Enter')
    p.evaluate("""() => { const q = document.getElementById('ws-ai-question');
        for (let i = 0; i < 3; i++) q.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', ctrlKey: true, repeat: true, bubbles: true, cancelable: true})); }""")
    p.keyboard.up('Control')
    p.wait_for_timeout(1500)
    verifier(len(creations) == 1, 'Ctrl+Entrée a envoyé %d demande(s) au lieu d’une' % len(creations))
    # mode « Général » et recherche publique confirmée en un clic (contrat du lot discussion ; réponses du serveur simulées : aucune clé)
    corps = []

    def simuler(route):
        corps.append(json.loads(route.request.post_data or '{}'))
        premier = len(corps) == 1
        carte = {'id': '', 'kind': 'question', 'state': 'decision' if premier else 'answered', 'label': 'À décider' if premier else 'Répondu', 'conversation': True,
                 'instruction': corps[-1].get('instruction', ''), 'matter': '', 'matter_label': '', 'lignes': {}, 'context': {},
                 'exceptions': [{'code': 'recherche_publique_a_confirmer', 'message': 'Texte exact qui partirait : « bail commercial jurisprudence ».',
                                 'requete': 'bail commercial jurisprudence'}] if premier else [],
                 'plan': {'deliverable': 'Réponse dans le fil', 'steps': ['Répondre dans le fil'], 'restrictions': ['Aucun envoi']},
                 'result': {} if premier else {'text': 'Résultat fictif de la recherche publique.'}}
        route.fulfill(status=200, content_type='application/json', body=json.dumps(carte))
    p.route('**/api440/m567/mission/create', simuler)
    p.click('#ws-ai-general')
    verifier(p.get_attribute('#ws-ai-general', 'aria-pressed') == 'true', 'le bouton Général ne s’active pas')
    verifier('ni dossier' in p.locator('#ws-ai-portee-dossier').inner_text(), 'l’en-tête ne dit pas le mode Général')
    p.fill('#ws-ai-question', 'Quelle est la jurisprudence récente sur le bail commercial ?')
    p.click('#ws-ai-send')
    p.wait_for_selector('[data-recherche-confirmer]')
    verifier(len(corps) == 1 and corps[0].get('portee') == 'general', 'le mode Général n’est pas transmis : %s' % corps[:1])
    verifier(not corps[0].get('confirmer_recherche'), 'une confirmation est partie sans clic')
    p.click('[data-recherche-confirmer]')
    p.wait_for_timeout(600)
    verifier(len(corps) == 2 and corps[1].get('confirmer_recherche') == 'bail commercial jurisprudence', 'la confirmation ne renvoie pas le texte exact : %s' % corps[1:])
    p.unroute('**/api440/m567/mission/create')
    p.click('#ws-ai-general')
    # Échap : fermeture, focus rendu au lanceur, fond de nouveau actif, page à la même place
    p.fill('#ws-ai-question', 'Brouillon fictif à garder')
    p.keyboard.press('Escape')
    p.wait_for_selector('#ws-ai-dock', state='hidden')
    verifier(p.evaluate('document.activeElement && document.activeElement.id') == 'ws-ai-launcher', 'le focus n’est pas rendu au lanceur')
    verifier(not p.evaluate('!!document.querySelector("[inert]")'), 'le fond reste inerte après la fermeture')
    verifier(p.evaluate('window.scrollY') == avant, 'la page n’a pas retrouvé sa position')
    p.click('#ws-ai-launcher')
    p.wait_for_selector('#ws-ai-dock:not([hidden])')
    verifier(p.input_value('#ws-ai-question') == 'Brouillon fictif à garder', 'le brouillon est perdu à la réouverture')
    p.keyboard.press('Escape')
    s.propre('Ctrl+Entrée, Échap et brouillon')
    # agrandissement (fenêtre de 1920 × 950 à 125 % et 200 %) et mode sombre
    for vue, nom in (({'width': 1536, 'height': 760}, '125 %'), ({'width': 960, 'height': 475}, '200 %')):
        p.set_viewport_size(vue)
        p.click('#ws-ai-launcher')
        p.wait_for_selector('#ws-ai-dock:not([hidden])')
        p.wait_for_timeout(200)
        _volet_visible96(p, nom)
        if getattr(banc, 'sortie', None):
            p.screenshot(path=str(Path(banc.sortie) / ('P96a-volet-%s.png' % nom.replace(' %', ''))))
        p.keyboard.press('Escape')
    p.set_viewport_size({'width': 1920, 'height': 950})
    p.emulate_media(color_scheme='dark')
    p.click('#ws-ai-launcher')
    p.wait_for_selector('#ws-ai-dock:not([hidden])')
    _volet_visible96(p, 'mode sombre')
    if getattr(banc, 'sortie', None):
        p.screenshot(path=str(Path(banc.sortie) / 'P96a-volet-sombre.png'))
    p.keyboard.press('Escape')
    p.emulate_media(color_scheme='light')
    s.propre('125 %, 200 % et sombre')
    # mobile : appareil tactile ; le clavier logiciel ouvert réduit la hauteur visible, l'envoi reste au-dessus
    mobile = s.context.browser.new_context(viewport={'width': 375, 'height': 812}, is_mobile=True, has_touch=True, device_scale_factor=2, locale='fr-FR')
    try:
        m = Session(banc, mobile)
        m.connecter()
        m.aller('/dossiers')
        m.page.click('#ws-ai-launcher')
        m.page.wait_for_selector('#ws-ai-dock:not([hidden])')
        m.page.wait_for_timeout(200)
        _volet_visible96(m.page, 'mobile')
        m.page.focus('#ws-ai-question')
        m.page.set_viewport_size({'width': 375, 'height': 430})                # clavier logiciel ouvert (hauteur visible réduite)
        m.page.wait_for_timeout(300)
        if getattr(banc, 'sortie', None):
            m.page.screenshot(path=str(Path(banc.sortie) / 'P96a-volet-mobile-clavier.png'))
        for sel, nom in (('#ws-ai-question', 'le champ'), ('#ws-ai-send', 'Envoyer'), ('#ws-ai-close', 'Fermer')):
            verifier(m.page.evaluate(DANS_LA_VUE96, sel), 'mobile, clavier ouvert : %s masqué (%s)' % (nom, m.page.locator(sel).bounding_box()))
        m.propre('mobile')
    finally:
        mobile.close()
    verifier(banc.modele.nombre() == 0, 'le modèle a été appelé pendant le parcours du volet (%d)' % banc.modele.nombre())


def p96b_aujourdhui_densite(banc, s):
    """5.6.96 (lot affichage, U02, U04, U06) : Aujourd'hui rempli (jeu fictif) à 1920 × 950 : huit compteurs, au moins trois livrables et le premier
    élément de Ma journée visibles sans défiler ; densité « Compacte » mémorisée ; 125 %, 200 %, sombre : aucun bouton coupé, aucun défilement
    horizontal ; chaque bloc repliable se déplie au clavier ; un bloc replié n'est chargé qu'à son ouverture (mêmes droits)."""
    from datetime import datetime, timedelta, timezone
    semis = _semis5695()
    maintenant = datetime.now(timezone.utc)
    with banc.desk() as d:
        semis.schemas(d)
        for i, (ident, client, adverse) in enumerate(MATTERS):
            semis.prepare(d, 'banc-pr-%d' % i, 'Projet fictif %s contre %s' % (client, adverse), 'contrat', ident, '', 1)
        semis.prepare(d, 'banc-pr-c', 'Réponse fictive au confrère', 'courriel', 'DOS-002', '', 1)
        semis.evenement(d, 'banc-ev96', 'Audience fictive de mise en état', maintenant + timedelta(hours=2), matter='DOS-001')
        semis.tache(d, 'banc-t96', 'Relire les conclusions fictives', maintenant.date().isoformat(), 'DOS-003')
        semis.courriel(d, 'banc-m96', 'Demande de pièces complémentaires', 'DOS-002')
        semis.veille(d, 'banc-vj96', 'Décision fictive sur le bail commercial')
        d.db.commit()
    p = s.page
    p.set_viewport_size({'width': 1920, 'height': 950})
    s.connecter()
    verifier(p.locator('#ax95-zone li[data-rubrique]').count() == 8, 'l’encadré n’a pas ses huit rubriques')
    if getattr(banc, 'sortie', None):
        p.screenshot(path=str(Path(banc.sortie) / 'P96b-aujourdhui-1920x950.png'))
    hauteur = p.evaluate('window.innerHeight')
    def visibles(sel):
        return p.eval_on_selector_all(sel, 'l => l.filter(e => { const r = e.getBoundingClientRect(); return r.height > 0 && r.top >= 0 && r.bottom <= %d; }).length' % hauteur)
    verifier(visibles('#ax95-zone li[data-rubrique]') == 8, 'les huit compteurs ne sont pas visibles sans défiler (%d)' % visibles('#ax95-zone li[data-rubrique]'))
    # ordre I01 (décision du cabinet) : les livrables à relire sont d'abord dans le rang « À décider / À relire », puis dans Travail préparé
    livrables = visibles('#f96-lignes-zone li.f96-ligne, #a89-travail li.a89-ligne')
    verifier(livrables >= 3, 'moins de trois livrables visibles sans défiler (%d)' % livrables)
    jour = p.locator('#c530-day li, .f96-journee li').first
    verifier(jour.count() == 1, 'Ma journée est vide malgré le jeu fictif')
    boite = jour.bounding_box()
    verifier(boite and boite['y'] + boite['height'] <= hauteur, 'le premier élément de Ma journée n’est pas visible sans défiler (%s)' % boite)
    s.propre('premier écran à 1920 × 950')
    # densité « Compacte » : mémorisée pour la personne, la page est plus courte, rien n'est retiré
    longueur = p.evaluate('document.scrollingElement.scrollHeight')
    blocs = p.eval_on_selector_all('[id]', 'l => l.map(e => e.id).filter(i => /^(ax95|a89|c530|c5656|c5632|t88)-/.test(i)).sort()')
    p.click('#t88-panel > summary')
    p.check('#t88-panel input[name=densite][value=compacte]')
    envoyer(p, 'tableau/enregistrer', lambda: p.click('#t88-panel form[data-api="m5688/tableau/enregistrer"] button[type=submit]'), 'l’enregistrement de la densité')
    verifier(p.locator('#c530').get_attribute('data-densite') == 'compacte', 'la densité compacte n’est pas appliquée après rechargement')
    apres = p.eval_on_selector_all('[id]', 'l => l.map(e => e.id).filter(i => /^(ax95|a89|c530|c5656|c5632|t88)-/.test(i)).sort()')
    verifier(apres == blocs, 'la densité compacte a retiré des éléments : %s' % sorted(set(blocs) - set(apres)))
    verifier(p.evaluate('document.scrollingElement.scrollHeight') <= longueur, 'la page compacte n’est pas plus courte (%d > %d)' % (p.evaluate('document.scrollingElement.scrollHeight'), longueur))
    s.propre('densité compacte')
    # 125 %, 200 %, sombre : aucun défilement horizontal, aucun bouton coupé
    coupes = """() => [...document.querySelectorAll('#ws-main button, #ws-main .ax-btn, #ws-main summary')].filter(b => b.getClientRects().length && getComputedStyle(b).visibility !== 'hidden')
        .filter(b => { let d = b.parentElement && b.parentElement.closest('details'); while (d) { if (!d.open && !(b.tagName === 'SUMMARY' && b.parentElement === d)) return false; d = d.parentElement && d.parentElement.closest('details'); } return true; })
        .filter(b => b.scrollWidth > b.clientWidth + 2 || b.getBoundingClientRect().right > window.innerWidth + 1).map(b => (b.id || b.className || b.tagName) + ':' + (b.textContent || '').trim().slice(0, 30) + ' [' + b.scrollWidth + '/' + b.clientWidth + ', droite ' + Math.round(b.getBoundingClientRect().right) + ', ' + (b.closest('[id]') || {}).id + ']')"""
    for vue, nom, sombre in (({'width': 1536, 'height': 760}, '125 %', False), ({'width': 960, 'height': 475}, '200 %', False), ({'width': 1920, 'height': 950}, 'sombre', True)):
        p.set_viewport_size(vue)
        p.emulate_media(color_scheme='dark' if sombre else 'light')
        p.wait_for_timeout(200)
        verifier(p.evaluate(SANS_DEFILEMENT_HORIZONTAL96), '%s : défilement horizontal' % nom)
        if getattr(banc, 'sortie', None):
            p.screenshot(path=str(Path(banc.sortie) / ('P96b-aujourdhui-%s.png' % nom.replace(' %', ''))), full_page=True)
        coupe = p.evaluate(coupes)
        verifier(not coupe, '%s : bouton(s) coupé(s) : %s' % (nom, coupe[:5]))
    p.emulate_media(color_scheme='light')
    p.set_viewport_size({'width': 1920, 'height': 950})
    s.propre('125 %, 200 % et sombre')
    # blocs repliables au clavier ; la veille repliée n'est chargée qu'à l'ouverture
    plis = p.locator('details.t95-pli > summary')
    verifier(plis.count() >= 1, 'aucun bloc repliable')
    for i in range(plis.count()):
        resume = plis.nth(i)
        ouvert = resume.evaluate('s => s.parentElement.open')
        resume.focus()
        p.keyboard.press('Enter')
        p.wait_for_timeout(150)
        verifier(resume.evaluate('s => s.parentElement.open') is (not ouvert), 'le bloc %d ne se replie pas au clavier' % (i + 1))
        p.keyboard.press('Enter')
        p.wait_for_timeout(150)
    pli = 'details[data-pli="veille"]'
    if p.locator(pli).count():
        if p.locator(pli).get_attribute('open') is not None:
            with p.expect_response(lambda r: '/actuellement/plis' in r.url):
                p.click(pli + ' > summary')
        s.aller('/aujourdhui')
        verifier(p.locator(pli + ' [data-a96-part="veille"]').count() == 1, 'la veille repliée a été construite avec la page')
        with p.expect_response(lambda r: '/m530/part' in r.url and 'name=veille' in r.url) as attendu:
            p.click(pli + ' > summary')
        verifier(attendu.value.ok, 'chargement de la veille refusé (HTTP %d)' % attendu.value.status)
        p.wait_for_function('() => document.querySelector(\'%s [data-a96-part="veille"]\').dataset.a96Etat === "charge"' % pli)
        verifier(p.locator(pli + ' .a96-differe').count() == 0 and p.locator(pli + ' #c5632-veille').inner_text().strip() not in ('', 'Veille juridique'),
                 'la veille chargée à l’ouverture n’a pas remplacé le contenu d’attente')
    s.propre('blocs repliables')
    verifier(banc.modele.nombre() == 0, 'le modèle a été appelé sur Aujourd’hui (%d)' % banc.modele.nombre())


def p96v_voix(banc, s):
    """5.6.96 (lot voix, V05, V06) : Paramètres › Voix : « Français posé » proposé par défaut, « Écouter ce profil » lu par Brindille avec
    un son non vide ; aucun module d'avatar sur Aujourd'hui tant qu'aucun portrait n'est choisi, aperçu dessiné après le choix. Aucun micro."""
    s.connecter()
    p = s.page
    s.aller('/aujourdhui')
    verifier(p.locator('script[src*="v5696-avatar.js"]').count() == 0, 'module d’avatar déclaré sur Aujourd’hui sans portrait choisi')
    s.propre('Aujourd’hui sans avatar')
    if not (ROOT / 'agent' / 'static' / 'brindille' / 'tts.js').is_file():
        print('    écoute du profil ignorée : agent/static/brindille/ absent')
        return
    page = s.context.new_page()
    page.add_init_script(AUDIO_JS)
    page.on('pageerror', s._erreur_js)
    page.on('console', s._console)
    page.goto(banc.url + '/parametres?rubrique=voix', wait_until='networkidle')
    choix = page.locator('select[name="voix_brindille"]')
    verifier(choix.count() == 1 and choix.input_value() == 'posee', 'le profil « Français posé » n’est pas proposé par défaut')
    page.locator('[data-v94-ecouter-profil]').click()
    fin = time.monotonic() + 180
    while time.monotonic() < fin and not page.evaluate('() => !!document.querySelector("[data-v94-profil-etat][data-v94-voix], [data-v94-profil-etat][data-v94-erreur]")'):
        page.wait_for_timeout(250)
    etat = page.evaluate('(() => { const n = document.querySelector("[data-v94-profil-etat]"); return n ? Object.assign({}, n.dataset, {texte: n.textContent}) : {}; })()')
    verifier(not etat.get('v94Erreur'), 'l’écoute du profil a échoué : %s' % etat.get('v94Erreur'))
    verifier(etat.get('v94Voix') == 'brindille', 'l’écoute du profil n’a pas été faite par Brindille : %s' % etat)
    verifier('terminée' in etat.get('texte', ''), 'la fin de lecture n’est pas annoncée : %s' % etat.get('texte'))
    joues = page.evaluate('window.__bancAudio')
    verifier(any(a.get('type') == 'buffer' and a.get('samples', 0) > 0 and a.get('peak', 0) > 0.001 for a in joues), 'aucun son produit : %s' % joues[:3])
    diagnostic = page.evaluate('JSON.stringify(window.axiorhubVoix.diagnostic())')
    verifier('"profil":"posee"' in diagnostic and 'DURAND' not in diagnostic, 'diagnostic de lecture inattendu : %s' % diagnostic[:300])
    page.select_option('select[name="avatar"]', 'portrait_a')
    verifier(page.locator('[data-v94-apercu-avatar] svg.v96-portrait').count() == 1, 'l’aperçu du portrait n’est pas dessiné')
    if getattr(banc, 'sortie', None):
        page.locator('#v94-voix').screenshot(path=str(Path(banc.sortie) / 'P96v-voix.png'))
    page.close()
    s.propre('profil de voix et avatar')


PARCOURS = (('P02a', 'connexion puis déconnexion', p02a_connexion), ('P02b', 'pause de l’IA depuis Aujourd’hui', p02b_pause),
            ('P02c', 'annulation puis relance d’une demande', p02c_annulation), ('P02d', 'reprise après une erreur du fournisseur', p02d_reprise_erreur),
            ('P02e', 'facture en brouillon', p02e_facture), ('P02f', 'ouverture et téléchargement d’un document', p02f_livrable),
            ('P02g', 'tour du menu', p02g_menu), ('P02h', 'Charge du serveur et Voix', p02h_charge_voix),
            ('P95a', 'encadré « Actuellement » d’Aujourd’hui', p95a_actuellement), ('P95b', 'Personnaliser Aujourd’hui', p95b_personnaliser),
            ('P96k', 'carte Mistral : tester sans recharger', p96k_mistral),
            ('P96F1', 'en-tête, météo et ordre d’Aujourd’hui', p96f1_accueil), ('P96F2', 'action en fenêtre flottante', p96f2_fenetre),
            ('P96c', 'Courriels : vue « À traiter avec le Pilote »', p96c_courriels),
            ('P96a', 'volet de l’assistant : composeur visible, focus, clavier', p96a_volet), ('P96b', 'Aujourd’hui : premier écran, densité, plis', p96b_aujourdhui_densite),
            ('P96v', 'Voix : profil « Français posé » et avatar facultatif', p96v_voix))


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
                banc.sortie = sortie                   # 5.6.95 : captures d'un parcours réussi (encadré, réglages)
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
