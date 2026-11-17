"""5.6.107 (lot V) : enregistrement automatique de l'image de la vidéo de démonstration (Playwright, Chromium, 1920 × 1080).

Source des données (toujours fictives, dans un dossier temporaire, serveur sur 127.0.0.1 et un port libre) :
- ``demo`` : instance créée par l'API du mode Démo (lot M) ``agent.demo56107.creer(dossier) -> chemin_config`` ;
- ``banc`` : banc des parcours (``scripts/parcours-navigateur.py``) complété ici de courriels, pièces et projets fictifs (fausse messagerie
  IMAP des tests ``tests/faux_imap56106.py``, faux modèle de bouclage du banc) ;
- ``auto`` (défaut) : ``demo`` si l'API du lot M existe et fonctionne, sinon ``banc`` (le motif est écrit dans le minutage).
- Langues : la démo est créée dans la langue (``creer(..., langue=...)`` du lot M) et le navigateur reçoit la locale de ``langues.json`` ;
  hors du français, pas de repli sur le banc (ses données sont en français) : ``LangueIndisponible`` si le mode Démo ne connaît pas la
  langue. Légendes, carton de fin, cibles et textes des boutons filmés viennent de ``langues.json`` (repli sur le français).

Le compte est celui du banc (identifiant et mot de passe aléatoires en mémoire, jamais affichés) ; la connexion se fait dans un contexte
non filmé, puis le contexte filmé reprend la session. Toute requête du navigateur vers une autre adresse que le serveur local est bloquée.
Le calque (``calque.js``) dessine le curseur, l'onde des clics, les légendes et le carton de fin ; les mouvements de souris sont lissés.
Chaque plan dure au moins la durée calculée (voix mesurée, ou durée visée) : le script attend la fin du temps du plan. Une étape qui ne
trouve pas son élément ne fait pas échouer l'enregistrement : elle est notée dans ``remarques`` (minutage et compte rendu).
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time

from . import texte as tx

ICI = Path(__file__).resolve().parent
RACINE = ICI.parent.parent
MOIS = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre')
JOURS = ('lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche')
PDF = (b'%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n'
       b'trailer<</Root 1 0 R>>\n%%EOF\n')
SYNTHESE = ('Trois courriels : M. DURAND transmet les pièces complémentaires du dossier DURAND c. MARTIN, M. ZORVAL demande le calendrier '
            'de la procédure, Me KERVILAC communique ses pièces dans le dossier ZORVAL c. KERVILAC.')
ATTENTE_RESUME_S = 8.0
GRAINE = 56107                  # graine des données du mode Démo : la même vidéo à chaque version


class LangueIndisponible(RuntimeError):
    """Le mode Démo ne sait pas (encore) créer ses données dans cette langue : rien n'est filmé."""
DEMANDES = {'DURAND': 'Transmet le bail et les quittances ; souhaite faire le point avant l’audience.',
            'ZORVAL': 'Demande les prochaines étapes de la procédure et les dates à retenir.',
            'KERVILAC': 'Communique ses pièces n° 1 à 4 dans l’affaire ZORVAL c. KERVILAC.'}


def _nav():
    """Banc des parcours chargé comme module (son nom de fichier contient un tiret)."""
    if 'parcours_navigateur' in sys.modules:
        return sys.modules['parcours_navigateur']
    for chemin in (RACINE, RACINE / 'tests', RACINE / 'scripts'):
        if str(chemin) not in sys.path:
            sys.path.insert(0, str(chemin))
    spec = importlib.util.spec_from_file_location('parcours_navigateur', str(RACINE / 'scripts' / 'parcours-navigateur.py'))
    nav = importlib.util.module_from_spec(spec)
    sys.modules['parcours_navigateur'] = nav
    spec.loader.exec_module(nav)
    return nav


def maintenant():
    """Heure du cabinet (jamais ``date.today()`` : la recette tourne en UTC)."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo('Europe/Paris'))


def date_audience(depuis):
    """Date d'audience fictive : environ trois semaines plus tard, un mardi."""
    jour = (depuis + timedelta(days=21)).date()
    while jour.weekday() != 1:
        jour += timedelta(days=1)
    return jour


def date_longue(jour):
    return '%s %d %s %d' % (JOURS[jour.weekday()], jour.day, MOIS[jour.month - 1], jour.year)


# ------------------------------------------------------------------------------------------------ données fictives du banc
def _auth(domaine, ok=True):
    if ok:
        return ('mail.example.test; spf=pass smtp.mailfrom=%s; dkim=pass header.d=%s; dmarc=pass header.from=%s' % (domaine, domaine, domaine))
    return ('mail.example.test; spf=fail smtp.mailfrom=%s; dkim=none; dmarc=fail header.from=%s' % (domaine, domaine))


def courriel(n, de, objet, corps, quand, pieces=(), authentifie=True, repondre_a=None):
    m = EmailMessage()
    domaine = de.rsplit('@', 1)[1].rstrip('>')
    m['Authentication-Results'] = _auth(domaine, authentifie)
    m['From'], m['To'], m['Subject'] = de, 'Cabinet <cabinet@example.test>', objet
    if repondre_a:
        m['Reply-To'] = repondre_a
    m['Message-ID'], m['Date'] = '<video56107-%d@example.test>' % n, format_datetime(quand)
    m.set_content(corps)
    for nom in pieces:
        m.add_attachment(PDF, maintype='application', subtype='pdf', filename=nom)
    return m.as_bytes(policy=policy.SMTP)


def courriels_fictifs(t):
    """Boîte de réception fictive (du plus récent au plus ancien) : ``(octets, lu, date)``."""
    audience = date_audience(t)
    h = lambda minutes: t - timedelta(minutes=minutes)   # noqa: E731
    return [
        (courriel(1, 'M. DURAND <durand@example.test>', 'Pièces complémentaires — DURAND c. MARTIN (RG 24/01234)',
                  'Bonjour Maître,\n\nComme convenu, je vous adresse les pièces complémentaires pour notre affaire contre la société MARTIN '
                  '(RG 24/01234) : le bail commercial et les trois dernières quittances.\n\nPouvez-vous me dire quand nous pourrons faire le '
                  'point avant l’audience ?\n\nBien cordialement,\nM. DURAND', h(4), ('Bail commercial DURAND.pdf', 'Quittances DURAND.pdf')),
         False, h(4)),
        (courriel(2, 'e-barreau RPVA <notification@rpva.example.test>', 'RPVA — Avis de fixation — RG 24/01234 — DURAND c. MARTIN',
                  'Avis de fixation.\n\nAffaire : DURAND c. MARTIN, RG 24/01234, TJ Valbrune, 2e chambre civile.\n\nL’affaire est fixée '
                  'à l’audience du %s à 9 h 30, salle 2.\n\nMessage fictif de démonstration.' % date_longue(audience), h(38)), False, h(38)),
        (courriel(3, 'MARTIN <comptabilite@martin-reglements.example.test>', 'Changement de coordonnées bancaires',
                  'Bonjour Maître,\n\nSuite à un changement de banque, voici nos nouvelles coordonnées bancaires pour le versement de '
                  'l’indemnité convenue. Merci de procéder au virement dès que possible sur ce nouveau compte, en toute discrétion.\n\n'
                  'Cordialement,\nService comptable MARTIN', h(55), authentifie=False, repondre_a='paiement@reglements-rapides.example.test'),
         False, h(55)),
        (courriel(4, 'M. ZORVAL <zorval@example.test>', 'DOS-003 — Calendrier de la procédure',
                  'Bonjour Maître,\n\nPourriez-vous me rappeler les prochaines étapes de la procédure contre KERVILAC et les dates à '
                  'retenir ?\n\nMerci,\nM. ZORVAL', h(80)), False, h(80)),
        (courriel(5, 'Me KERVILAC <kervilac@example.test>', 'DOS-003 — Communication de pièces',
                  'Mon cher confrère,\n\nJe vous prie de trouver ci-joint mes pièces n° 1 à 4, communiquées dans l’affaire ZORVAL c. '
                  'KERVILAC.\n\nBien confraternellement,\nMe KERVILAC', h(125), ('Bordereau KERVILAC.pdf',)), True, h(125)),
        (courriel(6, 'Mme ORLANDEC <orlandec@example.test>', 'Demande de rendez-vous',
                  'Bonjour Maître,\n\nJe souhaiterais vous consulter au sujet d’un bail commercial. Seriez-vous disponible la semaine '
                  'prochaine ?\n\nMme ORLANDEC', h(180)), True, h(180)),
        (courriel(7, 'Lettre d’information <lettre@editions.example.test>', 'Lettre d’information — baux commerciaux',
                  'Lettre d’information fictive : sélection d’actualités pour les cabinets.', h(300)), True, h(300)),
        (courriel(8, 'MARTIN <martin@example.test>', 'DOS-002 — Documents signés',
                  'Bonjour Maître,\n\nVoici les documents signés pour le dossier MARTIN c. BERNARD.\n\nMARTIN', h(1500),
                  ('Documents signés MARTIN.pdf',)), True, h(1500)),
        (courriel(9, 'M. BERNARD <bernard@example.test>', 'DOS-002 — Réponse à votre courrier',
                  'Maître,\n\nJ’accuse réception de votre courrier et reviens vers vous rapidement.\n\nBERNARD', h(1620)), True, h(1620)),
    ]


def projets_fictifs(t):
    """Projets de réponse « À relire » (producteur ``manual_drafts``) : ``(clé, objet, expéditeur, dossier, corps, date)``."""
    return [
        (hashlib.sha256(b'video56107-durand').hexdigest(), 'Pièces complémentaires — DURAND c. MARTIN (RG 24/01234)', 'durand@example.test',
         'DOS-001', 'Bonjour Monsieur DURAND,\n\nJe vous remercie pour l’envoi du bail commercial et des quittances, que je verse au '
         'dossier.\n\nJe vous propose de faire le point par téléphone mardi, avant l’audience.\n\nBien cordialement,', t - timedelta(minutes=2)),
        (hashlib.sha256(b'video56107-zorval').hexdigest(), 'DOS-003 — Calendrier de la procédure', 'zorval@example.test', 'DOS-003',
         'Bonjour Monsieur ZORVAL,\n\nVoici les prochaines étapes de la procédure ; je vous adresse le calendrier détaillé.\n\n'
         'Bien cordialement,', t - timedelta(minutes=70)),
    ]


def reponse_resume(nav):
    """Faux modèle du banc : résumé des courriels cochés au format attendu (lecteur 5.6.106), sinon la réponse habituelle du banc."""
    def repondre(corps):
        for message in corps.get('messages') or []:
            try:
                donnees = json.loads(message.get('content') or '')
            except (TypeError, ValueError):
                continue
            if isinstance(donnees, dict) and isinstance(donnees.get('courriels'), list):
                points = []
                for c in donnees['courriels']:
                    brut = json.dumps(c, ensure_ascii=False).upper()
                    places = sorted((brut.find(nom), texte) for nom, texte in DEMANDES.items() if nom in brut)   # le premier nommé est l'expéditeur
                    demande = places[0][1] if places else 'Message fictif.'
                    points.append({'ref': c.get('ref'), 'demande': demande, 'actions': ['creer_tache']})
                return json.dumps({'synthese': SYNTHESE, 'courriels': points,
                                   'actions': [{'type': 'repondre', 'ref': donnees['courriels'][0].get('ref'), 'libelle': 'Accuser réception'}]},
                                  ensure_ascii=False)
        return nav.reponse_fictive(corps)
    return repondre


def semer_banc(banc, nav):
    """Complète le banc : pièces du dossier DOS-001, fausse messagerie, projets À relire, faux modèle qui sait résumer."""
    from agent import mailbox as mbx
    from agent import relecture56100 as rl
    from agent.common import private_json
    from faux_imap56106 import Serveur
    t = maintenant()
    m = nav.MATTERS[0]
    pieces = banc.cloud / ('Dossiers/%s - %s c. %s/PIECES' % m)
    pieces.mkdir(parents=True, exist_ok=True)
    for nom in ('Pièce 01 - Bail commercial.pdf', 'Pièce 02 - Quittances.pdf', 'Pièce 03 - Commandement de payer.pdf', 'Pièce 04 - Assignation.pdf'):
        (pieces / nom).write_bytes(PDF)
    serveur = Serveur(separateur='/')
    for brut, lu, quand in reversed(courriels_fictifs(t)):
        serveur.ajouter('INBOX', brut, {'\\Seen'} if lu else set(), quand.astimezone(timezone.utc))
    secret = Path(banc.base) / 'donnees' / 'imap.secret'
    secret.write_text('mot-de-passe-fictif-du-banc', encoding='utf-8')
    banc.config['mail'].update({'enabled': True, 'username': 'cabinet', 'host': 'mail.example.test', 'password_file': str(secret), 'trash': 'Trash'})
    private_json(banc.config_path, banc.config)
    banc._connect_origine = mbx.connect
    mbx.connect = serveur.connexion
    with banc.desk() as d:
        rapports = Path(d.c['state_dir']) / 'reports'
        rapports.mkdir(exist_ok=True)
        for cle, objet, de, dossier, corps, quand in projets_fictifs(t):
            cree = quand.astimezone().isoformat()
            data = {'result': {'body': corps, 'requires_decision': True, 'limits': ['À relire.']}, 'verification': None, 'matter': dossier,
                    'matter_path': '', 'key': cle, 'source_hash': 'video56107', 'created_at': cree, 'recipients': [de],
                    'sources': [{'id': 'incoming', 'title': 'Courriel reçu'}]}
            private_json(rapports / (cle + '.json'), {'subject': objet, 'sender': de, 'matter': dossier, 'received_at': cree})
            d.db.execute('INSERT OR REPLACE INTO manual_drafts VALUES(?,?,?)', (cle, json.dumps(data, ensure_ascii=False), cree))
            d.db.commit()
            rl.projeter_cle(d, cle)
    banc.modele.reponse = reponse_resume(nav)
    return serveur


def restaurer_banc(banc):
    from agent import mailbox as mbx
    if getattr(banc, '_connect_origine', None) is not None:
        mbx.connect = banc._connect_origine


# ------------------------------------------------------------------------------------------------ source
class Source:
    """Serveur local et données fictives ; ``mode`` vaut ``demo`` ou ``banc`` ; ``motif`` dit pourquoi le banc a été retenu.

    ``demo`` : instance du lot M créée dans le dossier temporaire (graine fixe ``GRAINE`` : mêmes données à chaque version), servie par ses
    deux processus (``demo56107.commandes`` : interface et travailleur, sur 127.0.0.1 et un port libre) ; la session est ouverte par le jeton
    de lancement à usage unique (``demo56107.jeton_de_session``), jamais par un mot de passe.
    ``banc`` : serveur du banc des parcours dans ce processus, compte aléatoire, données de ``semer_banc``."""

    def __init__(self, base, mode='auto', langue='fr'):
        self.nav = _nav()
        self.base = Path(base)
        self.langue = langue
        self.mode, self.motif, self.banc, self.processus, self.chemin = None, '', None, [], None
        if langue != 'fr' and mode == 'banc':
            raise LangueIndisponible('le banc des parcours n’a que des données françaises : --source demo pour « %s »' % langue)
        if mode in ('auto', 'demo'):
            try:
                self._demo()
            except Exception as ex:   # noqa: BLE001 — le mode Démo du lot M peut être absent ou inachevé : repli sur le banc, motif écrit
                self._arreter_processus()
                if mode == 'demo' or langue != 'fr':
                    raise
                self.motif = 'mode Démo indisponible (%s : %s) : banc des parcours' % (type(ex).__name__, str(ex)[:200])
        if self.mode is None:
            self._banc()

    # -------------------------------------------------------------- mode Démo (lot M)
    def _demo(self):
        import socket
        import subprocess
        from agent import demo56107   # API du lot M (5.6.107)
        for nom in ('creer', 'commandes', 'jeton_de_session'):
            if not callable(getattr(demo56107, nom, None)):
                raise RuntimeError('agent.demo56107.%s absent' % nom)
        with socket.socket() as sonde:
            sonde.bind(('127.0.0.1', 0))
            port = sonde.getsockname()[1]
        self.origine_demo = 'http://127.0.0.1:%d' % port
        options = {}
        if self.langue != 'fr':
            import inspect
            if 'langue' not in inspect.signature(demo56107.creer).parameters:
                raise LangueIndisponible('le mode Démo (lot M) ne crée pas encore ses données en « %s » (creer(..., langue=...) absent)' % self.langue)
            options['langue'] = self.langue
        self.chemin = demo56107.creer(str(self.base / 'demo'), graine=GRAINE, origine=self.origine_demo, prefixe=self.nav.PREFIX, **options)
        cmd = demo56107.commandes(self.chemin, port)
        env = dict(os.environ)
        env.update(cmd['env'])
        journal = open(self.base / 'demo-processus.log', 'ab')
        self._journal = journal
        for role in ('interface', 'travailleur'):
            self.processus.append(subprocess.Popen(cmd[role], cwd=cmd.get('cwd') or str(RACINE), env=env, stdout=journal, stderr=subprocess.STDOUT))
        self._attendre_serveur(port)
        self.demo = demo56107
        self.mode = 'demo'

    def _attendre_serveur(self, port, delai=60.0):
        import http.client
        fin = time.monotonic() + delai
        while time.monotonic() < fin:
            if any(p.poll() is not None for p in self.processus):
                raise RuntimeError('un processus de la démo s’est arrêté (code %s) ; journal : %s'
                                   % ([p.poll() for p in self.processus], self.base / 'demo-processus.log'))
            try:
                c = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                c.request('GET', self.nav.PREFIX + '/connexion')
                c.getresponse().read()
                c.close()
                return
            except OSError:
                time.sleep(0.3)
        raise RuntimeError('la démo ne répond pas sur le port %d après %.0f s' % (port, delai))

    def _arreter_processus(self):
        for p in self.processus:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=10)
                except Exception:   # noqa: BLE001
                    p.kill()
        self.processus = []
        if getattr(self, '_journal', None) is not None:
            self._journal.close()
            self._journal = None

    # -------------------------------------------------------------- banc des parcours (repli)
    def _banc(self):
        banc = self.nav.Banc(self.base / 'banc')
        banc.ident = 'me.durand'        # nom du compte visible dans le menu (le mot de passe reste aléatoire, en mémoire seulement)
        self.banc = banc
        banc.preparer()
        semer_banc(banc, self.nav)
        banc.demarrer()
        self.mode = 'banc'

    def _arreter_banc(self):
        if self.banc is not None:
            try:
                restaurer_banc(self.banc)
                self.banc.arreter()
            except Exception:   # noqa: BLE001
                pass
            self.banc = None

    # -------------------------------------------------------------- commun
    @property
    def origine(self):
        return self.origine_demo if self.mode == 'demo' else self.banc.origine

    @property
    def url(self):
        return self.origine + self.nav.PREFIX

    def connecter(self, contexte):
        """Ouvre la session dans ``contexte`` (non filmé) et ferme la visite guidée si elle s'ouvre seule ; rend l'état du navigateur."""
        page = contexte.new_page()
        if self.mode == 'demo':
            page.goto(self.url + self.demo.jeton_de_session(self.chemin, '/aujourdhui'), wait_until='load')
        else:
            session = self.nav.Session(self.banc, contexte)
            page.close()
            session.connecter()
            page = session.page
        page.wait_for_timeout(800)
        if '/connexion' in page.url:
            raise RuntimeError('la session de %s ne s’est pas ouverte' % self.mode)
        try:   # « Ne plus proposer la visite » (préférence du lot M, stockage du navigateur) : gardée par l'état de session filmé
            page.evaluate("() => localStorage.setItem('demo107-visite-off', '1')")
        except Exception:   # noqa: BLE001
            pass
        for chemin in ('/aujourdhui', '/courriels?vue=boite'):     # la visite guidée du mode Démo s'ouvre d'elle-même : refusée ici
            page.goto(self.url + chemin, wait_until='load')
            page.wait_for_timeout(1200)
            fermer_visite(page)
        etat = contexte.storage_state()
        page.close()
        return etat

    def travailler(self):
        """File des travaux (résumé, projet) : le travailleur de la démo s'en charge ; le banc l'exécute ici. Erreur rendue en texte."""
        if self.mode == 'demo':
            return 0
        try:
            return self.banc.travailler()
        except Exception as ex:   # noqa: BLE001
            return 'file : %s' % ex

    def arreter(self):
        self._arreter_processus()
        self._arreter_banc()


def fermer_visite(page):
    """Visite guidée du mode Démo ouverte : « Ne plus proposer la visite » puis « Passer la visite ». Rend vrai si elle était ouverte."""
    ouverte = False
    try:
        case = page.locator('.demo107-plus input[type=checkbox]')
        if case.count() and case.first.is_visible():
            case.first.check()
            ouverte = True
        for selecteur in ('[data-demo107-passer]', 'button:has-text("Passer la visite")'):
            bouton = page.locator(selecteur).first
            if bouton.count() and bouton.is_visible():
                bouton.click()
                page.wait_for_timeout(250)
                ouverte = True
                break
    except Exception:   # noqa: BLE001 — la visite ne bloque jamais le tournage
        pass
    return ouverte


# ------------------------------------------------------------------------------------------------ plateau
class Plateau:
    """Page filmée : navigation, légendes, curseur lissé, recherche tolérante des éléments, remarques par plan."""

    def __init__(self, page, source, regl):
        self.p, self.source, self.regl = page, source, regl
        img = regl['image']
        self.pos = (img['largeur'] / 2.0, img['hauteur'] / 2.0)
        self.duree_deplacement = float(img['deplacement_souris_s'])
        self.pas = float(img['pas_par_seconde'])
        self.remarques, self.echelle, self.etapes, self.t0 = [], 1.0, [], time.monotonic()
        self.langue = getattr(source, 'langue', 'fr')
        self.captures = None            # dossier des captures de vérification (--captures), sinon aucune capture

    def capture(self, nom):
        if self.captures is not None:
            self.p.screenshot(path=str(Path(self.captures) / (nom + '.png')))

    def noter(self, texte):
        self.remarques.append(texte)

    def jalon(self, quoi):
        """Instant (secondes depuis le début du plan) de chaque navigation et de chaque clic : diagnostic d'un plan trop long."""
        self.etapes.append((round(time.monotonic() - self.t0, 2), quoi[:80]))

    def pause(self, secondes):
        self.p.wait_for_timeout(int(max(0.0, secondes) * 1000 * self.echelle))

    def aller(self, chemin):
        self.jalon('aller ' + chemin)
        self.p.goto(self.source.url + chemin, wait_until='load')
        self.calme()

    def calme(self, repos=0.25):
        """Attend le chargement de la page puis ``repos`` secondes (jamais « networkidle » : plusieurs pages interrogent le serveur en
        continu et ne l'atteignent pas), puis replace le curseur du calque à la position courante."""
        try:
            self.p.wait_for_load_state('load', timeout=8000)
        except Exception:   # noqa: BLE001
            pass
        self.p.wait_for_timeout(int(repos * 1000))
        if self.source.mode == 'demo' and fermer_visite(self.p):
            self.noter('visite guidée du mode Démo ouverte pendant le tournage (fermée)')
        self.jalon('chargée')
        try:
            self.p.evaluate('([x, y]) => window.__axhVideo && window.__axhVideo.placer(x, y)', list(self.pos))
        except Exception:   # noqa: BLE001
            pass

    def legende(self, texte):
        self.p.evaluate('(t) => window.__axhVideo && window.__axhVideo.legende(t)', texte or '')

    def trouver(self, *selecteurs, attente=2500, mots=()):
        """Premier élément visible parmi les sélecteurs (filtré par les ``mots``, insensible à la casse) ; None sinon."""
        fin = time.monotonic() + attente / 1000.0
        while True:
            for s in selecteurs:
                loc = self.p.locator(s)
                for mot in mots:
                    loc = loc.filter(has_text=re.compile(re.escape(mot), re.I))
                try:
                    n = loc.count()
                    for i in range(min(n, 20)):
                        if loc.nth(i).is_visible():
                            return loc.nth(i)
                except Exception:   # noqa: BLE001
                    continue
            if time.monotonic() >= fin:
                return None
            self.p.wait_for_timeout(150)

    def vers(self, cible, duree=None):
        """Déplacement lissé (accélération puis freinage) vers un élément (centre) ou un point ``(x, y)``."""
        if cible is None:
            return False
        if isinstance(cible, tuple):
            x, y = cible
        else:
            try:
                cible.scroll_into_view_if_needed(timeout=2000)
                b = cible.bounding_box()
            except Exception:   # noqa: BLE001
                b = None
            if not b:
                return False
            x, y = b['x'] + min(b['width'] / 2.0, 140), b['y'] + b['height'] / 2.0
        duree = self.duree_deplacement if duree is None else duree
        x0, y0 = self.pos
        n = max(6, int(duree * self.pas))
        debut = time.monotonic()
        for i in range(1, n + 1):
            u = i / float(n)
            e = 4 * u ** 3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2   # easeInOutCubic
            self.p.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e)
            reste = debut + duree * u - time.monotonic()
            if reste > 0:
                time.sleep(reste)
        self.pos = (x, y)
        return True

    def cliquer(self, cible, quoi='', duree=None, navigation=False):
        """Déplacement lissé puis clic réel de la souris. ``navigation`` : le clic charge une autre page, attendue (au plus 8 s)."""
        if cible is None:
            if quoi:
                self.noter('introuvable : %s' % quoi)
            return False
        if not self.vers(cible, duree):
            self.noter('hors écran : %s' % (quoi or 'élément'))
            return False
        self.p.wait_for_timeout(140)
        self.jalon('clic ' + (quoi or 'élément'))
        masque = self.masque(cible)
        if masque:
            self.noter('%s masqué par %s : clic transmis à l’élément (le curseur filmé est au-dessus du masque)' % (quoi or 'élément', masque))

        def appuyer():
            if masque:
                cible.evaluate('(e) => e.click()')
            else:
                self.p.mouse.down()
                self.p.mouse.up()
        try:
            if navigation:
                try:
                    with self.p.expect_navigation(wait_until='load', timeout=8000):
                        appuyer()
                except Exception:   # noqa: BLE001 — lien qui ne recharge pas la page : on continue sur place
                    pass
                self.calme()
            else:
                appuyer()
        except Exception as ex:   # noqa: BLE001
            self.noter('clic impossible sur %s : %s' % (quoi or 'élément', str(ex)[:120]))
            return False
        return True

    def masque(self, cible):
        """Élément qui recouvre la cible au point du curseur (bandeau flottant…) : sa description, sinon ''."""
        try:
            return cible.evaluate("""(e, [x, y]) => { const h = document.elementFromPoint(x, y);
                if (!h || h === e || e.contains(h) || h.closest('[data-axh-video]')) return '';
                const b = h.closest('[id],[class]') || h; return (b.id ? '#' + b.id : '.' + String(b.className).split(' ')[0]); }""",
                                  list(self.pos))
        except Exception:   # noqa: BLE001
            return ''

    def menu(self, chemin, quoi):
        """Entrée du menu latéral (navigation réelle, comme l'utilisateur) ; à défaut, chargement direct de la page."""
        lien = self.trouver('#ws-sidebar a[href$="%s"]' % chemin, 'nav a[href$="%s"]' % chemin, attente=800)
        if not self.cliquer(lien, quoi, duree=0.7, navigation=True):
            self.noter('%s introuvable : page chargée directement' % quoi)
            self.aller(chemin)

    def libelles(self, cle):
        """Textes possibles d'un bouton filmé : ceux de la langue, puis ceux du français (interface pas encore traduite)."""
        textes = list(tx.langue(self.langue).get('libelles', {}).get(cle, []))
        return textes + [t for t in tx.langue('fr')['libelles'].get(cle, []) if t not in textes]

    def bouton_texte(self, portees, cle, attente=2000):
        selecteurs = ['%s button:has-text("%s")' % (portee, t) for t in self.libelles(cle) for portee in portees]
        return self.trouver(*selecteurs, attente=attente)

    def variantes(self, cible):
        """Variantes d'une cible dans l'ordre utile : celle du mode Démo d'abord, celle du banc ensuite (ordre inverse sur le banc)."""
        variantes = alternatives(cible)
        return variantes[::-1] if self.source.mode == 'banc' else variantes

    def ligne(self, cible, attente=6000):
        """Ligne de la liste des courriels : ``cible`` = liste de mots, ou liste de variantes (listes de mots) essayées dans l'ordre
        (données du mode Démo, puis celles du banc)."""
        variantes = self.variantes(cible)
        for n, mots in enumerate(variantes):
            dernier = n == len(variantes) - 1
            loc = self.trouver('#bx5625-rows .ax-row[data-uid]', '.ax-row[data-uid]', attente=attente if dernier else min(attente, 1500), mots=mots)
            if loc is not None:
                return loc
        return None


# ------------------------------------------------------------------------------------------------ plans
def alternatives(cible):
    """``['a', 'b']`` → ``[['a', 'b']]`` ; ``[['a'], ['b', 'c']]`` inchangé (variantes essayées dans l'ordre)."""
    cible = list(cible or [])
    return [list(x) for x in cible] if cible and isinstance(cible[0], (list, tuple)) else [cible]


def libelle(cible):
    return ' ou '.join(' + '.join(v) for v in alternatives(cible))


def dossier_filme(pl, plan=None):
    """Dossier du plan 3 : repère du bandeau du mode Démo (``data-demo107-dossier``, premier dossier de la langue : DOS-001, MAT-001,
    AKT-001, EXP-001), sinon l'adresse du plan dans la langue (``langues.json``), sinon celle du scénario."""
    try:
        lu = pl.p.get_attribute('[data-demo107]', 'data-demo107-dossier', timeout=500)
    except Exception:   # noqa: BLE001 — banc des parcours : pas de bandeau
        lu = None
    if lu:
        return lu
    plan = plan or tx.plan_en(next(p for p in tx.scenario()['plans'] if p['nom'] == 'dossier'), pl.langue)
    return re.search(r'id=([^&]+)', plan['page']).group(1)


def _boite(pl, menu=False):
    """Boîte de réception : par le menu latéral (« Courriels », puis l'onglet de la boîte s'il le faut) ou par l'adresse de la vue."""
    if menu:
        pl.menu('/courriels', 'entrée « Courriels » du menu')
        if not pl.p.locator('#bx5625-rows').count():
            onglet = pl.trouver('#ws-main a[href*="vue=boite"]', attente=600)
            if not pl.cliquer(onglet, '', duree=0.6, navigation=True):
                pl.aller('/courriels?vue=boite')
    else:
        pl.aller('/courriels?vue=boite')
    if pl.trouver('#bx5625-rows .ax-row[data-uid]', '.ax-row[data-uid]', attente=12000) is None:
        pl.noter('liste des courriels vide ou absente')
    pl.jalon('liste affichée')


def _lecteur(pl, quoi):
    if pl.trouver('#bx5625-panel .lx56106', '[data-demo-lecteur]', attente=10000) is None:
        pl.noter('lecteur non affiché : %s' % quoi)
        return False
    pl.p.wait_for_timeout(300)
    return True


def plan_aujourdhui(pl, plan):
    pl.legende(plan['legende'])
    pl.pause(0.5)
    titre = pl.trouver('#ws-main h1', 'main h1', 'h1', attente=1500)
    pl.vers(titre or (700, 300), duree=1.1)
    pl.pause(0.5)
    bloc = pl.trouver('#ws-main section h2', '#ws-main h2', 'main h2', attente=800)
    pl.vers(bloc or (900, 520), duree=1.2)
    pl.capture('plan-01')


def plan_courriel(pl, plan):
    _boite(pl, menu=True)
    pl.legende(plan['legende'])
    ligne = pl.ligne(plan['cibles']['courriel'])
    pl.pause(0.8)
    pl.vers(ligne, duree=1.0)
    pl.pause(0.7)
    if pl.cliquer(ligne, 'courriel du client (%s)' % libelle(plan['cibles']['courriel'])):
        _lecteur(pl, 'courriel du client')
    pl.pause(1.2)
    mid = dossier_filme(pl)
    pastille = pl.trouver('#bx5625-panel .lx56106-meta:has-text("%s")' % mid, '#bx5625-panel .lx56106-meta:has-text("Dossier :")',
                          '#bx5625-panel [data-demo-dossier="%s"]' % mid, '#bx5625-panel [data-demo-dossier]', attente=2500)
    if pastille is None:
        pl.noter('pastille du dossier introuvable dans le lecteur')
    pl.vers(pastille, duree=1.0)
    pl.capture('plan-02')
    pl.pause(2.0)
    piece = pl.trouver('#bx5625-panel .lx56106-piece', '#bx5625-panel :text("Bail commercial")', attente=800)
    pl.vers(piece, duree=0.9)


def plan_dossier(pl, plan):
    mid = dossier_filme(pl, plan)
    lien = pl.trouver('#bx5625-panel a[data-demo-dossier="%s"]' % mid, '#bx5625-panel a[href*="/dossier?id=%s"]' % mid, attente=400)
    if not pl.cliquer(lien, duree=0.7, navigation=True):
        pl.menu('/dossiers', 'entrée « Dossiers » du menu')
        lien = pl.trouver('#ws-main a[href*="/dossier?id=%s"]' % mid, 'a[href*="/dossier?id=%s"]' % mid, attente=4000)
        if not pl.cliquer(lien, 'lien du dossier %s' % mid, duree=0.8, navigation=True):
            pl.aller(plan['page'])
    pl.legende(plan['legende'])
    pl.capture('plan-03-0')
    pl.pause(0.5)
    for n, choix in enumerate(plan['cibles']['onglets'], 1):
        choix = [choix] if isinstance(choix, str) else choix
        cible = None
        for libelle in choix:
            cible = pl.trouver('#ws-main [role=tab]:has-text("%s")' % libelle, '#ws-main nav a:has-text("%s")' % libelle,
                               '#ws-main a:has-text("%s")' % libelle, '#ws-main button:has-text("%s")' % libelle, attente=600)
            if cible is not None:
                break
        pl.cliquer(cible, 'onglet « %s » du dossier' % ' / '.join(choix), duree=0.5, navigation=True)
        pl.capture('plan-03-%d' % n)
        pl.pause(0.6)
    # Parties, pièces, chronologie : survolées sur la synthèse du dossier (chaque onglet recharge la page, environ 3 s en démo : trois
    # onglets ne tiendraient pas dans le plan). Chaque entrée est une liste de libellés essayés dans l'ordre.
    for choix in plan['cibles'].get('survol', []):
        choix = [choix] if isinstance(choix, str) else choix
        cible = None
        for libelle in choix:
            cible = pl.trouver('#ws-main h2:has-text("%s")' % libelle, '#ws-main h3:has-text("%s")' % libelle, '#ws-main h4:has-text("%s")' % libelle,
                               '#ws-main a:has-text("%s")' % libelle, '#ws-main :text("%s")' % libelle, attente=400)
            if cible is not None:
                break
        if cible is None:
            pl.noter('« %s » non visible dans le dossier' % ' / '.join(choix))
            continue
        pl.vers(cible, duree=0.8)
        pl.capture('plan-03-survol-%s' % choix[0][:12].replace(' ', '-'))
        pl.pause(0.8)


def plan_reponse(pl, plan):
    pl.menu('/a-relire', 'entrée « À relire » du menu')
    pl.legende(plan['legende'])
    pl.pause(0.4)
    ligne = None
    for mots in pl.variantes(plan['cibles']['projet']):
        ligne = pl.trouver('[data-ax100-lignes] li[data-review-id] a', '[data-demo-projet]', attente=2500, mots=mots)
        if ligne is not None:
            break
    if pl.cliquer(ligne, 'projet de réponse (%s)' % libelle(plan['cibles']['projet'])):
        try:
            pl.p.wait_for_function("() => { const t = document.querySelector('[data-ax100-texte]'); return t && t.value && !t.readOnly; }", timeout=10000)
        except Exception:   # noqa: BLE001
            pl.noter('texte du projet non chargé ou en lecture seule')
    pl.pause(1.2)
    zone = pl.trouver('[data-ax100-texte]', '[data-demo-texte]', attente=1500)
    if zone is not None:
        pl.vers(zone, duree=0.8)
        pos, nouveau = -1, ''
        for ancien, nouveau in alternatives(plan['cibles']['mot']):   # première paire (mot, correction) présente dans le projet
            pos = pl.p.evaluate("""([a]) => { const t = document.querySelector('[data-ax100-texte], [data-demo-texte]'); if (!t) return -1;
                const L = /[A-Za-zÀ-ÿœŒ]/; let k = t.value.indexOf(a);   // mot entier : ni lettre avant, ni lettre après
                while (k >= 0 && (L.test(t.value.charAt(k - 1)) || L.test(t.value.charAt(k + a.length)))) k = t.value.indexOf(a, k + 1);
                if (k < 0) return -1; t.focus(); t.setSelectionRange(k, k + a.length); return k; }""", [ancien])
            if pos >= 0:
                break
        if pos < 0:
            pl.noter('aucun des mots %s dans le projet : correction non filmée' % [m[0] for m in alternatives(plan['cibles']['mot'])])
        else:
            pl.pause(0.6)
            pl.p.keyboard.type(nouveau, delay=110)
            pl.capture('plan-04-correction')
            pl.pause(0.7)
    # Décision de l'avocat (17/11/2026) : relecture, correction d'un mot, puis la décision ; rien n'est envoyé. Pour une proposition de
    # réponse, le bouton s'appelle « Retenir » (« Valider » n'existe que pour les notes de l'IA et les documents) : la voix dit « vous décidez ».
    bouton = pl.trouver('[data-ax100-actions] [data-action="valider"]:not([hidden]):enabled', '[data-ax100-actions] [data-action="retenir"]:enabled',
                        attente=3000)
    if pl.cliquer(bouton, 'bouton « Valider » ou « Retenir »'):
        pl.pause(0.4)
        statut = pl.trouver('[data-ax100-statut]', attente=1500)
        pl.capture('plan-04-valide')
        pl.vers(statut, duree=0.8)
        pl.pause(1.2)


def plan_resume(pl, plan):
    _boite(pl, menu=True)
    pl.legende(plan['legende'])
    pl.pause(0.2)
    for mots in plan['cibles']['courriels']:
        ligne = pl.ligne(mots, attente=3000)
        uid = ligne.get_attribute('data-uid') if ligne is not None else None
        case = pl.trouver('#bx5625-rows .c5670-pick input[data-uid="%s"]' % uid, '[data-demo-coche="%s"]' % uid, attente=800) if uid else None
        if pl.cliquer(case, 'case à cocher du courriel %s' % libelle(mots), duree=0.5):
            pl.p.wait_for_timeout(100)
    bouton = pl.bouton_texte(('#bx5625', ''), 'resumer')
    if pl.cliquer(bouton, 'bouton « %s »' % pl.libelles('resumer')[0]):
        pl.pause(0.4)
        pris = pl.source.travailler()
        if isinstance(pris, str):
            pl.noter(pris)
        synthese = pl.trouver('#bx5625-panel .lx56106-synthese', '.lx56106-synthese', attente=int(ATTENTE_RESUME_S * 1000))
        if synthese is None:
            pl.noter('résumé non affiché')
        pl.vers(synthese, duree=0.9)
        pl.capture('plan-05')
        pl.pause(0.7)
        point = pl.trouver('#bx5625-panel .lx56106-point', attente=500)
        pl.vers(point, duree=0.8)


def plan_fraude(pl, plan):
    pl.legende(plan['legende'])
    # Courriels cochés au plan précédent : décochés d'un coup (sans mouvement de souris, au raccord entre les deux plans).
    pl.p.evaluate("() => document.querySelectorAll('#bx5625-rows .c5670-pick input:checked').forEach((c) => c.click())")
    ligne = pl.ligne(plan['cibles']['courriel'], attente=2000)
    if ligne is None:            # résumé encore ouvert ou liste rechargée : retour à la boîte
        _boite(pl)
        ligne = pl.ligne(plan['cibles']['courriel'])
    pastille = ligne.locator('.fr106-pastille').first if ligne is not None and ligne.locator('.fr106-pastille').count() else None
    if pastille is None:
        pl.noter('pastille « courriel frauduleux » absente de la liste')
    pl.vers(pastille or ligne, duree=0.9)
    pl.pause(0.5)
    if pl.cliquer(ligne, 'courriel frauduleux (%s)' % libelle(plan['cibles']['courriel'])):
        bandeau = pl.trouver('#bx5625-panel .fr106-bandeau', '.fr106-bandeau', attente=8000)
        if bandeau is None:
            pl.noter('bandeau d’alerte absent du lecteur')
        else:
            titre = bandeau.locator('.fr106-titre').first
            pl.vers(titre if titre.count() else bandeau, duree=0.8)
            pl.capture('plan-06')
            pl.pause(1.0)
            motif = bandeau.locator('.fr106-motifs li', has_text=re.compile('bancaires', re.I)).first
            if motif.count():
                pl.vers(motif, duree=0.8)


def plan_rpva(pl, plan):
    ligne = pl.ligne(plan['cibles']['courriel'], attente=2000)
    pl.legende(plan['legende'])
    pl.vers(ligne, duree=0.8)
    if pl.cliquer(ligne, 'message RPVA'):
        _lecteur(pl, 'message RPVA')
    pl.pause(0.8)
    corps = pl.trouver('#bx5625-panel :text("fixée")', '#bx5625-panel pre', attente=800)
    pl.vers(corps, duree=0.7)
    pl.pause(0.4)
    bouton = pl.trouver('[data-demo-agenda]', attente=300) or pl.bouton_texte(('#bx5625-panel .lx56106-barre', ''), 'agenda', attente=2500)
    if pl.cliquer(bouton, 'bouton « %s »' % pl.libelles('agenda')[0]):
        form = pl.trouver('#bx5625-panel form.lx56106-formulaire', 'form.lx56106-formulaire', attente=3000)
        if form is None:
            pl.noter('formulaire d’agenda non affiché')
        else:
            jour = form.locator('input[type=date]').first
            pl.vers(jour if jour.count() else form, duree=0.8)
            heure = form.locator('input[type=time]').first
            if heure.count():
                pl.pause(0.5)
                pl.vers(heure, duree=0.5)
            pl.capture('plan-07-formulaire')
            pl.pause(0.8)
            date_lue = jour.input_value() if jour.count() else 'sans champ'
            if not date_lue:
                # Date d'audience non repérée par le produit : on ne clique pas « Créer » (le formulaire afficherait une erreur à
                # l'écran) ; le défaut est noté pour le lot qui reconnaît les messages de juridiction.
                pl.noter('date d’audience non repérée dans le message (formulaire sans date) : événement non créé')
                pl.vers(form.locator('p, .lx56106-note').first if form.locator('p').count() else form, duree=0.8)
            elif pl.cliquer(form.locator('button[type=submit]').first, 'bouton « Créer » de l’événement'):
                pl.pause(0.6)
                etat = pl.trouver('#bx5625-panel [role=status]', '[role=status]', attente=1200)
                pl.vers(etat, duree=0.6)
                pl.capture('plan-07-cree')


def plan_fin(pl, plan):
    f = plan['fin']
    pl.p.evaluate('([m, d, a, l]) => window.__axhVideo && window.__axhVideo.carton(m, d, a, l)',
                  [f['marque'], f['devise'], f['adresse'], pl.source.url + '/static/axiorhub-icon-512.png'])
    pl.p.wait_for_timeout(1200)
    pl.capture('plan-08')


PLANS = {'aujourdhui': plan_aujourdhui, 'courriel': plan_courriel, 'dossier': plan_dossier, 'reponse': plan_reponse,
         'resume': plan_resume, 'fraude': plan_fraude, 'rpva': plan_rpva, 'fin': plan_fin}


# ------------------------------------------------------------------------------------------------ tournage
def filtre_requetes(origine, bloquees):
    """Gestionnaire de routes Playwright : seules passent les requêtes vers ``origine`` (serveur local de la démo) et les ``data:`` /
    ``blob:`` ; toute autre est interrompue et notée dans ``bloquees`` (aucune requête vers l'extérieur pendant le tournage)."""
    hote = origine.split('//', 1)[1].split('/', 1)[0]

    def filtre(route):
        u = route.request.url
        if u.startswith(('data:', 'blob:')) or (u.split(':', 1)[0] in ('http', 'https') and u.split('//', 1)[-1].split('/', 1)[0] == hote):
            return route.continue_()
        bloquees.append(u[:200])
        return route.abort()
    return filtre


@contextmanager
def _playwright():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        yield pw


def options_langue(locale):
    """Navigateur dans la langue de la vidéo : ``--lang`` au lancement (champs de date natifs, « 26.11.2026 » en allemand), locale du
    contexte et en-tête ``Accept-Language`` (« de-DE,de;q=0.9 »)."""
    court = locale.split('-')[0]
    entete = locale if court == locale else '%s,%s;q=0.9' % (locale, court)
    return {'args': ['--lang=' + locale]}, {'locale': locale, 'extra_http_headers': {'Accept-Language': entete}}


def lancer_navigateur(pw, regl, voir=False, locale='fr-FR'):
    nom = regl['image'].get('navigateur', 'chromium')
    lancement, _contexte = options_langue(locale)
    try:
        return pw.chromium.launch(headless=not voir, **lancement, **({'channel': nom} if nom not in ('chromium', '') else {})), nom
    except Exception:   # noqa: BLE001 — Chromium de Playwright absent : Edge du poste (aucun téléchargement)
        return pw.chromium.launch(headless=not voir, channel='msedge', **lancement), 'msedge'


def tourner(sortie, minutage_plans, mode='auto', choisis=None, voir=False, journal=print, captures=False, langue='fr', brut=None, minutage=None):
    """Filme les plans ; écrit ``brut/axiorhub-demo-brut.webm`` et ``minutage.json`` dans ``sortie`` ; rend le minutage."""
    regl, scen = tx.reglages(), tx.scenario()
    img = regl['image']
    sortie = Path(sortie)
    (sortie / 'brut').mkdir(parents=True, exist_ok=True)
    durees = {p['id']: p for p in minutage_plans}
    conf = tx.langue(langue)
    plans = [tx.plan_en(p, langue) for p in scen['plans'] if not choisis or p['id'] in choisis]
    brut = Path(brut or sortie / 'brut' / 'axiorhub-demo-brut.webm')
    minutage = Path(minutage or sortie / 'minutage.json')
    brut.parent.mkdir(parents=True, exist_ok=True)
    base = Path(tempfile.mkdtemp(prefix='axh-video56107-'))
    calque = (ICI / 'calque.js').read_text(encoding='utf-8')
    import logging
    logging.getLogger('waitress').setLevel(logging.CRITICAL)
    source, resultat = None, None
    try:
        source = Source(base, mode, langue)
        journal('Source : %s%s' % (source.mode, (' — ' + source.motif) if source.motif else ''))
        with _playwright() as pw:
            navigateur, nom_navigateur = lancer_navigateur(pw, regl, voir, conf['locale'])
            try:
                taille = {'width': int(img['largeur']), 'height': int(img['hauteur'])}
                commun = dict({'viewport': taille, 'timezone_id': 'Europe/Paris', 'device_scale_factor': 1, 'color_scheme': 'light'},
                              **options_langue(conf['locale'])[1])
                connexion = navigateur.new_context(**commun)
                etat = source.connecter(connexion)
                connexion.close()
                video_tmp = base / 'video'
                ctx = navigateur.new_context(storage_state=etat, record_video_dir=str(video_tmp), record_video_size=taille, **commun)
                bloquees = []
                ctx.route('**/*', filtre_requetes(source.origine, bloquees))
                ctx.add_init_script(script=calque)
                erreurs = []
                page = ctx.new_page()
                t0 = time.monotonic()
                page.on('pageerror', lambda ex: erreurs.append(str(ex)[:200]))
                pl = Plateau(page, source, regl)
                if captures:
                    pl.captures = sortie / 'captures'
                    pl.captures.mkdir(exist_ok=True)
                premier = plans[0]
                if premier.get('page'):
                    pl.aller(premier['page'])
                page.wait_for_timeout(300)
                releves = []
                for plan in plans:
                    cible = float(durees.get(plan['id'], {}).get('duree', plan['duree_visee']))
                    pl.echelle = max(1.0, cible / float(plan['duree_visee']))
                    pl.remarques, pl.etapes = [], []
                    debut = pl.t0 = time.monotonic()
                    journal('  plan %d — %s (%.1f s visées)…' % (plan['id'], plan['titre'], cible))
                    try:
                        PLANS[plan['nom']](pl, plan)
                    except Exception as ex:   # noqa: BLE001 — un plan raté ne casse pas la vidéo : il est signalé
                        pl.noter('erreur : %s : %s' % (type(ex).__name__, str(ex)[:200]))
                    actions = time.monotonic() - debut
                    reste = debut + cible - time.monotonic()
                    if reste > 0:
                        page.wait_for_timeout(int(reste * 1000))
                    fin = time.monotonic()
                    releves.append({'id': plan['id'], 'titre': plan['titre'], 'duree_visee': plan['duree_visee'], 'duree_cible': round(cible, 2),
                                    'debut': round(debut - t0, 3), 'fin': round(fin - t0, 3), 'duree_reelle': round(fin - debut, 2),
                                    'voix_s': durees.get(plan['id'], {}).get('voix_s'), 'actions_s': round(actions, 2),
                                    'remarques': list(pl.remarques), 'etapes': list(pl.etapes)})
                    for r in pl.remarques:
                        journal('    remarque : %s' % r)
                chemin_video = page.video.path() if page.video else None
                ctx.close()
                if chemin_video and Path(chemin_video).exists():
                    shutil.copyfile(chemin_video, brut)
                resultat = {'source': source.mode, 'motif_source': source.motif, 'langue': langue, 'navigateur': nom_navigateur,
                            'image': '%dx%d' % (taille['width'], taille['height']), 'video_brute': str(brut) if brut.exists() else '',
                            'debut_premier_plan_s': releves[0]['debut'] if releves else 0.0,
                            'duree_totale_s': round(sum(r['duree_reelle'] for r in releves), 2), 'plans': releves,
                            'requetes_bloquees': bloquees[:20], 'erreurs_javascript': erreurs[:20],
                            'date': maintenant().isoformat(timespec='seconds')}
            finally:
                navigateur.close()
    finally:
        if source is not None:
            source.arreter()
        shutil.rmtree(base, ignore_errors=True)
    minutage.write_text(json.dumps(resultat, ensure_ascii=False, indent=1), encoding='utf-8')
    return resultat
