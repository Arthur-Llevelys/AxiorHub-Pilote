"""5.6.93 (lot « charge ») : surveillance proportionnée sans perte de détection. Faux Nextcloud et faux IMAP qui comptent les requêtes,
horloge simulée. Données fictives uniquement ; aucun appel réseau, aucun modèle réel (les travaux qui appelleraient un modèle sont
remplacés par un faux qui compte les appels).

Ce que ces essais ne prouvent pas : le comportement d'un vrai serveur Nextcloud (propagation réelle des ETag de dossier, que la
documentation de Nextcloud garantit), d'un vrai serveur IMAP, ni le rendu dans un vrai navigateur."""
import base64
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import hashlib
import io
import json
import os
import time
import unittest
from unittest.mock import patch
import urllib.parse as U
import uuid
from zoneinfo import ZoneInfo

from agent import accueil5689, arret5643, charge5693 as charge, charge_web5693 as cw, desk as desk_module, economie569, operations, watch430
from agent import horaires_ia5689 as h
from agent.arret5643 import automatic, pause_all
from agent.common import Stop, private_json
from agent.dav import DAV
from agent.desk import Desk
import test_agent as fixtures
import test_v5689_horaires as ho

NOMS = ('DURAND', 'MARTIN', 'BERNARD', 'ZORVAL', 'KERVILAC', 'ORLANDEC')
SOUS = ('Pieces', 'Correspondance', 'Procedure', 'Notes')
BASE = 'https://nuage.example.test'
VIEUX = datetime.now(timezone.utc) - timedelta(days=60)


# ---------------------------------------------------------------- faux services
class FauxNextcloud:
    """Serveur WebDAV minimal à la manière de Nextcloud : l'ETag d'un dossier change à chaque changement de sa sous-arborescence."""

    def __init__(self):
        self.base = BASE
        self.noeuds = {'/Dossiers': {'dir': True, 'etag': self._etag(), 'mtime': VIEUX}}
        self.enfants = {'/Dossiers': set()}
        self.compte = {}

    @staticmethod
    def _etag():
        return '"%s"' % uuid.uuid4().hex[:16]

    def _parent(self, chemin):
        return chemin.rsplit('/', 1)[0] or '/'

    def dossier(self, chemin):
        if chemin in self.noeuds:
            return
        self.dossier(self._parent(chemin))
        self.noeuds[chemin] = {'dir': True, 'etag': self._etag(), 'mtime': VIEUX}
        self.enfants[chemin] = set()
        self.enfants[self._parent(chemin)].add(chemin)

    def fichier(self, chemin, texte, quand=None, propager=True):
        self.dossier(self._parent(chemin))
        self.noeuds[chemin] = {'dir': False, 'etag': self._etag(), 'mtime': quand or VIEUX, 'data': texte.encode()}
        self.enfants[self._parent(chemin)].add(chemin)
        if propager:
            parent = self._parent(chemin)
            while parent in self.noeuds:
                self.noeuds[parent]['etag'] = self._etag()
                if parent == '/Dossiers':
                    break
                parent = self._parent(parent)

    def compter(self, cle):
        self.compte[cle] = self.compte.get(cle, 0) + 1

    def total(self, prefixe=''):
        return sum(v for k, v in self.compte.items() if k.startswith(prefixe))

    def _href(self, chemin, dossier):
        return '/remote.php/dav/files/cabinet' + U.quote(chemin, safe='/') + ('/' if dossier else '')

    def _reponse(self, chemin):
        n = self.noeuds[chemin]
        type_ = '<d:resourcetype><d:collection/></d:resourcetype>' if n['dir'] else '<d:resourcetype/>'
        taille = 0 if n['dir'] else len(n['data'])
        return ('<d:response><d:href>%s</d:href><d:propstat><d:prop>%s<d:getetag>%s</d:getetag><d:getlastmodified>%s</d:getlastmodified>'
                '<d:getcontentlength>%d</d:getcontentlength></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>') % (
            self._href(chemin, n['dir']), type_, n['etag'], format_datetime(n['mtime'], usegmt=True), taille)

    def request(self, method, url, data=None, headers=None, limit=12_000_000):
        headers = headers or {}
        prefixe = BASE + '/remote.php/dav/files/cabinet'
        if method == 'REPORT':
            self.compter('REPORT')
            return b'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:"></d:multistatus>'
        if not url.startswith(prefixe):
            raise Stop('http_404')
        chemin = U.unquote(url[len(prefixe):]).rstrip('/') or '/'
        if method == 'PROPFIND':
            profondeur = headers.get('Depth', '1')
            self.compter('PROPFIND:' + profondeur)
            if chemin not in self.noeuds:
                raise Stop('http_404')
            corps = [self._reponse(chemin)]
            if profondeur == '1' and self.noeuds[chemin]['dir']:
                corps += [self._reponse(x) for x in sorted(self.enfants[chemin])]
            return ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">%s</d:multistatus>' % ''.join(corps)).encode()
        if method == 'GET':
            self.compter('GET')
            n = self.noeuds.get(chemin)
            if not n or n['dir']:
                raise Stop('http_404')
            if headers.get('If-Match') and headers['If-Match'] != n['etag']:
                raise Stop('http_412')
            return n['data']
        raise Stop('methode_refusee')


class FauxIMAP:
    """Connexion IMAP en lecture : chaque commande est comptée."""

    def __init__(self, boite):
        self.boite, self.capabilities, self.choisi = boite, ('IMAP4REV1',), ''

    def login(self, *a):
        self.boite.compter('LOGIN')
        return 'OK', [b'']

    def logout(self):
        return 'BYE', [b'']

    def select(self, nom, readonly=True):
        self.boite.compter('SELECT')
        self.choisi = nom.strip('"')
        return 'OK', [str(len(self.boite.messages.get(self.choisi, []))).encode()]

    def response(self, code):
        return code, [b'7']

    def uid(self, commande, *args):
        self.boite.compter(commande)
        messages = self.boite.messages.get(self.choisi, [])
        if commande == 'SEARCH':
            choisis = [m for m in messages if 'UNSEEN' not in args or not m['lu']]
            return 'OK', [' '.join(str(m['uid']) for m in choisis).encode()]
        uid = int(args[0])
        m = next(x for x in messages if x['uid'] == uid)
        if 'RFC822.SIZE' in args[1]:
            return 'OK', [('%d (UID %d FLAGS (%s) INTERNALDATE "%s" RFC822.SIZE %d)' % (
                uid, uid, '\\Seen' if m['lu'] else '', m['date'].strftime('%d-%b-%Y %H:%M:%S +0000'), len(m['raw']))).encode()]
        return 'OK', [(('%d (UID %d BODY[HEADER] {%d}' % (uid, uid, len(m['raw']))).encode(), m['raw']), b')']


class FausseBoite:
    def __init__(self):
        self.messages = {'INBOX': [], 'Sent': []}
        self.compte = {}

    def compter(self, cle):
        self.compte[cle] = self.compte.get(cle, 0) + 1

    def total(self):
        return sum(self.compte.values())

    def arrivee(self, dossier, sujet, quand, lu=False):
        uid = len(self.messages[dossier]) + 1
        raw = ('From: correspondant@example.test\r\nTo: cabinet@example.test\r\nSubject: %s\r\nMessage-ID: <m%d-%s@example.test>\r\n\r\n' % (
            sujet, uid, dossier)).encode()
        self.messages[dossier].append({'uid': uid, 'raw': raw, 'date': quand, 'lu': lu})


class FauxEmbedder:
    def __init__(self, *a, **k):
        pass

    def embed(self, textes):
        charge.compter('embeddings', len(textes))
        return [[0.1, 0.2] for _ in textes]

    def embed_query(self, texte):
        return [0.1, 0.2]


def dossiers_fictifs(n=50):
    return [{'id': 'DOS-%03d' % (i + 1), 'client_name': '%s %02d' % (NOMS[i % 6], i + 1),
             'path': '/Dossiers/Clients/%s %02d - %d' % (NOMS[i % 6], i + 1, 2026000001 + i),
             'references': ['DOS-%03d' % (i + 1)], 'aliases': [], 'correspondents': []} for i in range(n)]


class Cabinet(unittest.TestCase):
    """Cabinet fictif : 50 dossiers, 2 000 fichiers (4 sous-répertoires de 10 fichiers par dossier), archives sans dossier, boîte IMAP."""
    DOSSIERS = 50
    PAR_SOUS = 10

    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.matters = dossiers_fictifs(self.DOSSIERS)
        private_json(self.f.matter_file, self.matters)
        self.f.c['nextcloud'] = {'url': BASE, 'username': 'cabinet', 'password_file': 'secret-fictif', 'roots': ['/Dossiers']}
        self.f.c['mail'] = {**self.f.c['mail'], 'password_file': 'secret-fictif'}
        self.f.c['rag'] = {'enabled': True, 'embedding_chunks_per_run': 500}
        self.f.c['calendar'] = {**self.f.c['calendar'], 'urls': [BASE + '/remote.php/dav/calendars/cabinet/agenda/']}
        self.nc = FauxNextcloud()
        for m in self.matters:
            for s in SOUS:
                for k in range(self.PAR_SOUS):
                    self.nc.fichier('%s/%s/piece-%02d.txt' % (m['path'], s, k), 'Pièce fictive %s %s %d du dossier %s.' % (m['id'], s, k, m['client_name']))
        for annee in range(2015, 2025):                # archives sans dossier reconnu : le parcours y descend
            for mois in range(1, 7):
                self.nc.fichier('/Dossiers/Archives/%d/%02d/classement.txt' % (annee, mois), 'Archive fictive.')
        self.nc.fichier('/Dossiers/Modeles/modele.txt', 'Modèle fictif.')
        self.boite = FausseBoite()
        for k in range(100):
            self.boite.arrivee('INBOX', 'Ancien message %d' % k, VIEUX, lu=True)
        for k in range(50):
            self.boite.arrivee('Sent', 'Envoi %d' % k, VIEUX, lu=True)
        for cible, valeur in (('agent.exapp5642.nextcloud_http', lambda url, cfg: self.nc), ('agent.mailbox.connect', lambda cfg: FauxIMAP(self.boite)),
                              ('agent.mailbox.read_secret', lambda p: 'secret-fictif'), ('agent.index.Embedder', FauxEmbedder),
                              ('agent.index.DocumentIndex._semantique', lambda self: True)):
            p = patch(cible, valeur)
            p.start()
            self.addCleanup(p.stop)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        self.desk.setting('documents450:enabled', False)

    def dav(self):
        return DAV(self.desk.c['nextcloud'])

    def matter(self, i=0):
        return self.matters[i]

    def tour(self, dav=None):
        """Un tour complet des documents, lot après lot (sans les 5 minutes entre lots)."""
        dav = dav or self.dav()
        self.desk.setting('live430:document_cursor', 0)
        resultats = []
        for _ in range(100):
            resultats.append(watch430.documents_check(self.desk, dav))
            if not resultats[-1]['continue_scan']:
                break
        return resultats

    def modifier(self, i, nom='Pieces/nouvelle.txt', quand=None):
        chemin = self.matter(i)['path'] + '/' + nom
        self.nc.fichier(chemin, 'Pièce fictive modifiée %s.' % uuid.uuid4().hex[:6], quand or datetime.now(timezone.utc))
        return chemin

    def jobs(self, kind, statut='pending'):
        return self.desk.db.execute('SELECT id,args,not_before,matter_ref FROM jobs WHERE kind=? AND status=?', (kind, statut)).fetchall()


# ---------------------------------------------------------------- ETag de dossier
class Etag(Cabinet):
    DOSSIERS = 6

    def test_sous_arborescence_inchangee_reprise_du_cache(self):
        dav = self.dav()
        m = self.matter(0)
        fichiers, complet, stats = charge.inventaire(self.desk, dav, m)
        self.assertTrue(complet)
        self.assertEqual(len(fichiers), 40)
        self.assertEqual(stats['requetes'], 1 + 5)                        # ETag de la racine, puis la racine et ses 4 sous-répertoires
        avant = self.nc.total('PROPFIND')
        fichiers2, complet2, stats2 = charge.inventaire(self.desk, dav, m)
        self.assertEqual(self.nc.total('PROPFIND') - avant, 1)              # inchangé : une seule requête de profondeur 0
        self.assertEqual(fichiers2, fichiers)
        chemin = self.modifier(0, 'Procedure/conclusions.txt')
        avant = self.nc.total('PROPFIND')
        fichiers3, complet3, _ = charge.inventaire(self.desk, dav, m)
        self.assertEqual(self.nc.total('PROPFIND') - avant, 3)              # ETag, racine, seul « Procedure » relu
        self.assertIn(chemin, fichiers3)
        self.assertEqual(len(fichiers3), 41)
        self.assertEqual({k: v['etag'] for k, v in fichiers3.items()}, {k: v['etag'] for k, v in dav.inventory_step(m['path'])[0]['files'].items()})

    def test_budget_epuise_reprise_sans_tout_relire(self):
        dav = self.dav()
        m = self.matter(1)
        f, complet, stats = charge.inventaire(self.desk, dav, m, budget=3)
        self.assertFalse(complet)
        f, complet, stats = charge.inventaire(self.desk, dav, m, budget=3)
        self.assertTrue(complet)
        self.assertEqual(len(f), 40)
        self.assertLessEqual(stats['requetes'], 1 + 3)                      # les sous-répertoires déjà relus ne le sont plus

    def test_fichier_supprime_detecte(self):
        dav = self.dav()
        m = self.matter(2)
        charge.inventaire(self.desk, dav, m)
        chemin = m['path'] + '/Notes/piece-03.txt'
        del self.nc.noeuds[chemin]
        self.nc.enfants[m['path'] + '/Notes'].discard(chemin)
        for p in (m['path'] + '/Notes', m['path'], '/Dossiers/Clients', '/Dossiers'):
            self.nc.noeuds[p]['etag'] = self.nc._etag()
        fichiers, complet, _ = charge.inventaire(self.desk, dav, m)
        self.assertTrue(complet)
        self.assertNotIn(chemin, fichiers)
        self.assertEqual(len(fichiers), 39)

    def test_lot_un_propfind_pour_les_dossiers_inchanges_puis_detection(self):
        self.tour()
        self.assertEqual(len(self.jobs('index')), 0)                       # pièces anciennes : simple point de départ
        avant = self.nc.total('PROPFIND')
        resultats = self.tour()
        self.assertEqual(len(resultats), 1)                                # un seul lot : tout est inchangé
        self.assertEqual(resultats[0]['unchanged'], self.DOSSIERS)
        self.assertEqual(self.nc.total('PROPFIND') - avant, 1)              # un PROPFIND du répertoire parent pour les six dossiers
        chemin = self.modifier(3)
        resultats = self.tour()
        self.assertEqual(resultats[0]['changed'], 1)
        self.assertEqual({r['matter_ref'] for r in self.jobs('index')}, {self.matter(3)['id']})
        self.assertEqual({r['matter_ref'] for r in self.jobs('monitor_matter')}, {self.matter(3)['id']})
        self.assertTrue(chemin)

    def test_dossier_a_traiter_jamais_passe(self):
        """Un dossier modifié dont l'analyse est reportée (file chargée) reste relu aux tours suivants, même si son ETag ne bouge plus."""
        self.tour()
        self.modifier(4)
        with patch('agent.cadence5662.busy', return_value='file_chargee'):
            r = self.tour()
        self.assertEqual(r[0]['deferred'], 1)
        r = self.tour()
        self.assertEqual(r[0]['changed'], 1)
        self.assertEqual({x['matter_ref'] for x in self.jobs('index')}, {self.matter(4)['id']})

    def test_mutation_regler_un_dossier_a_traiter_perdrait_la_detection(self):
        """Garde-fou : retirer le contrôle « à traiter » de ``regler`` ferait passer le dossier reporté : la détection serait perdue."""
        self.tour()
        self.modifier(4)
        with patch('agent.cadence5662.busy', return_value='file_chargee'), patch.object(charge, '_sale', return_value=False):
            self.tour()
        with patch.object(charge, '_sale', return_value=False):
            r = self.tour()
        self.assertEqual(r[0]['changed'], 0)                               # le défaut que le garde-fou empêche
        self.assertEqual(self.jobs('index'), [])

    def test_relecture_de_securite_apres_24_heures(self):
        self.tour()
        avant = self.nc.total('PROPFIND')
        demain = time.time() + 25 * 3600
        with patch.object(charge, 'horloge', return_value=demain):
            r = self.tour()
        lus = self.nc.total('PROPFIND') - avant
        self.assertGreaterEqual(lus, self.DOSSIERS * 5)                    # toutes les arborescences relues
        self.assertEqual(sum(x['changed'] for x in r), 0)
        avant = self.nc.total('PROPFIND')
        with patch.object(charge, 'horloge', return_value=demain + 60):
            self.tour()
        self.assertEqual(self.nc.total('PROPFIND') - avant, 1)

    def test_changement_cache_pendant_la_relecture_de_securite_detecte(self):
        self.tour()
        demain = time.time() + 25 * 3600
        chemin = self.modifier(5, 'Notes/tardive.txt')
        with patch.object(charge, 'horloge', return_value=demain):
            self.tour()
        self.assertEqual({x['matter_ref'] for x in self.jobs('index')}, {self.matter(5)['id']})
        self.assertTrue(chemin)

    def test_hors_nextcloud_comportement_inchange(self):
        """Dossier local, WebDAV générique, stockage cloud : pas d'ETag propagé garanti, chaque tour relit comme en 5.6.92."""
        dav = self.dav()
        dav.etag_propage = False
        self.tour(dav)
        avant = self.nc.total('PROPFIND')
        self.tour(dav)
        self.assertEqual(self.nc.total('PROPFIND') - avant, self.DOSSIERS * 5)
        self.assertFalse(charge.mode_etag(self.desk, type('Local', (), {'etag_propage': False, 'folder_etag': 1, 'list_folder': 1})()))
        generique = dict(self.desk.c['nextcloud'], webdav_url='https://webdav.example.test/fichiers')
        self.assertFalse(DAV(generique).etag_propage)

    def test_interrupteur_de_secours(self):
        self.desk.setting(charge.ACTIF, False)
        self.tour()
        avant = self.nc.total('PROPFIND')
        self.tour()
        self.assertEqual(self.nc.total('PROPFIND') - avant, self.DOSSIERS * 5)

    def test_surveillance_du_dossier_sans_seconde_relecture(self):
        """monitor_matter après un lot : l'arborescence vient du cache (une requête), les pièces nouvelles sont bien signalées."""
        from agent import proactive
        self.tour()
        proactive.monitor_matter(self.desk, {'matter': self.matter(0)['id']})
        self.modifier(0)
        self.tour()
        avant = self.nc.total('PROPFIND')
        r = proactive.monitor_matter(self.desk, {'matter': self.matter(0)['id']})
        self.assertEqual(self.nc.total('PROPFIND') - avant, 1)
        self.assertTrue(r['changed'])
        signal = self.desk.db.execute("SELECT detail FROM proactive_signals WHERE matter=? AND category='documents_changed'", (self.matter(0)['id'],)).fetchone()
        self.assertIn('nouvelle.txt', signal[0])


# ---------------------------------------------------------------- aucun modèle ni empreinte pour un fichier inchangé
class Indexation(Cabinet):
    DOSSIERS = 3

    def index(self, i=0):
        return self.desk.perform('index', {'matter': self.matter(i)['id']})

    def test_fichier_inchange_ni_lu_ni_extrait_ni_empreinte(self):
        charge.relever()
        r = self.index()
        self.assertEqual(r['fichiers'], 40)
        premier = charge.relever()
        self.assertEqual(premier.get('extraction'), 40)
        self.assertGreater(premier.get('embeddings', 0), 0)
        avant = dict(self.nc.compte)
        r = self.index()
        second = charge.relever()
        self.assertTrue(r['inchange'])
        self.assertFalse(r['contenu_modifie'])
        self.assertEqual(second.get('extraction', 0) + second.get('embeddings', 0) + second.get('lecture_fichier', 0), 0)
        self.assertEqual({k: self.nc.compte[k] - avant.get(k, 0) for k in self.nc.compte if self.nc.compte[k] != avant.get(k, 0)}, {'PROPFIND:0': 1})
        self.modifier(0, 'Notes/ajout.txt')
        r = self.index()
        troisieme = charge.relever()
        self.assertEqual(troisieme.get('extraction'), 1)                    # seul le fichier nouveau
        self.assertEqual(troisieme.get('lecture_fichier'), 1)
        self.assertTrue(r['contenu_modifie'])

    def test_indexation_inchangee_ne_relance_ni_synthese_ni_memoire(self):
        self.desk.setting('automation:autonomy_enabled', True)
        with automatic(self.desk):
            self.desk.enqueue('index', {'matter': self.matter(1)['id']}, priority=50)
        with patch('agent.relevance370.review_result', return_value=None):
            self.desk.work_once()
            self.desk.db.execute("DELETE FROM jobs WHERE status='pending'")
            self.desk.db.commit()
            with automatic(self.desk):
                self.desk.enqueue('index', {'matter': self.matter(1)['id']}, priority=50)
            self.desk.work_once()
        suites = self.desk.db.execute("SELECT kind FROM jobs WHERE status='pending'").fetchall()
        self.assertEqual([r[0] for r in suites], [])
        mesures = dict(self.desk.db.execute("SELECT categorie,SUM(n) FROM charge5693_mesures WHERE travail='index' GROUP BY categorie").fetchall())
        self.assertEqual(mesures.get('extraction'), 40)                     # première indexation seulement
        noms = json.dumps([tuple(r) for r in self.desk.db.execute('SELECT * FROM charge5693_mesures').fetchall()])
        self.assertNotIn('piece-', noms)
        self.assertNotIn(self.matter(1)['path'], noms)


# ---------------------------------------------------------------- regroupement et plafonds
class Admission(Cabinet):
    DOSSIERS = 3

    def auto(self, kind, args=None, priority=60, **k):
        with automatic(self.desk):
            return self.desk.enqueue(kind, args, priority=priority, **k)

    def finir(self, jid, il_y_a=0):
        stamp = (datetime.now(timezone.utc) - timedelta(seconds=il_y_a)).isoformat()
        self.desk.db.execute("UPDATE jobs SET status='done',finished=? WHERE id=?", (stamp, jid))
        self.desk.db.commit()

    def test_un_seul_travail_par_dossier_et_fusion_vers_le_plus_complet(self):
        mid = self.matter(0)['id']
        a = self.auto('monitor_matter', {'matter': mid, 'tour': 'oui'})
        b = self.auto('monitor_matter', {'matter': mid})
        self.assertEqual(a, b)
        args = json.loads(self.desk.db.execute('SELECT args FROM jobs WHERE id=?', (a,)).fetchone()[0])
        self.assertEqual(args, {'matter': mid})                            # le changement de documents l'emporte : Nextcloud sera relu
        autre = self.auto('monitor_matter', {'matter': self.matter(1)['id']})
        self.assertNotEqual(a, autre)
        self.assertEqual(len(self.jobs('monitor_matter')), 2)

    def test_arguments_distincts_jamais_fusionnes(self):
        """Seule une restriction (tour périodique, pièce précise) peut être fusionnée ; d'autres arguments sont un autre travail."""
        mid = self.matter(0)['id']
        ids = [self.auto('refresh_brief', {'matter': mid, 'n': k}, priority=55) for k in range(4)]
        self.assertEqual(len(set(ids)), 4)
        self.assertEqual(self.auto('refresh_brief', {'matter': mid, 'n': 2}, priority=55), ids[2])

    def test_demande_de_l_avocat_jamais_regroupee(self):
        mid = self.matter(0)['id']
        a = self.auto('monitor_matter', {'matter': mid, 'tour': 'oui'})
        b = self.desk.enqueue('monitor_matter', {'matter': mid}, origin='user')
        self.assertNotEqual(a, b)
        self.assertIsNone(self.desk.db.execute('SELECT not_before FROM jobs WHERE id=?', (b,)).fetchone()[0])

    def test_fenetre_par_dossier(self):
        mid = self.matter(0)['id']
        a = self.auto('monitor_matter', {'matter': mid})
        self.finir(a, il_y_a=60)
        b = self.auto('monitor_matter', {'matter': mid})
        depart = datetime.fromisoformat(self.desk.db.execute('SELECT not_before FROM jobs WHERE id=?', (b,)).fetchone()[0])
        fenetre = charge.FENETRES['monitor_matter'][charge.NIVEAUX.index(charge.niveau(self.desk))]
        self.assertAlmostEqual((depart - datetime.now(timezone.utc)).total_seconds(), fenetre * 60 - 60, delta=30)
        c = self.auto('monitor_matter', {'matter': self.matter(1)['id']})   # autre dossier : aucune attente
        self.assertIsNone(self.desk.db.execute('SELECT not_before FROM jobs WHERE id=?', (c,)).fetchone()[0])
        d = self.auto('discover', {}, priority=80)
        self.finir(d, il_y_a=10)
        e = self.auto('discover', {}, priority=80)
        self.assertIsNotNone(self.desk.db.execute('SELECT not_before FROM jobs WHERE id=?', (e,)).fetchone()[0])
        self.finir(e)
        f = self.auto('discover', {}, priority=80, chained=True)           # suite d'un parcours partiel : pas de fenêtre
        self.assertIsNone(self.desk.db.execute('SELECT not_before FROM jobs WHERE id=?', (f,)).fetchone()[0])

    def test_plafond_horaire_differe_sans_refuser(self):
        limite = charge.plafond(self.desk, 'proactive568_cycle')
        ids = []
        for k in range(limite + 2):
            jid = self.auto('proactive568_cycle', {'owner': 'cabinet', 'k': k}, priority=55)
            ids.append(jid)
            self.finir(jid)
        departs = [self.desk.db.execute('SELECT not_before FROM jobs WHERE id=?', (j,)).fetchone()[0] for j in ids]
        self.assertTrue(all(d is None for d in departs[:limite]))
        self.assertTrue(all(d is not None for d in departs[limite:]))
        self.assertGreater(datetime.fromisoformat(departs[-1]), datetime.now(timezone.utc) + timedelta(minutes=50))

    def test_releve_des_courriels_jamais_espace_ni_plafonne(self):
        for k in range(40):
            jid = self.auto('live_mail430', {'k': k}, priority=5)
            self.finir(jid)
            self.assertIsNone(self.desk.db.execute('SELECT not_before FROM jobs WHERE id=?', (jid,)).fetchone()[0])
        self.desk.setting(charge.FREQUENCE, {'valeur': 'minimale', 'revision': 1})
        self.assertEqual(charge.intervalle_minutes(self.desk, 'live_mail430', 5), 5)

    def test_mutation_sans_exclusion_la_releve_serait_plafonnee(self):
        with patch.object(charge, 'JAMAIS', frozenset()), patch.dict(charge.PLAFONDS, {'live_mail430': 2}):
            ids = [self.auto('live_mail430', {'k': k}, priority=5) for k in range(4)]
        self.assertIsNotNone(self.desk.db.execute('SELECT not_before FROM jobs WHERE id=?', (ids[-1],)).fetchone()[0])

    def test_regime_econome_a_un_effet_reel(self):
        self.desk.setting(economie569.SETTING, False)
        self.assertEqual(charge.niveau(self.desk), 'normale')
        normal = (charge.plafond(self.desk, 'monitor_matter'), charge.intervalle_minutes(self.desk, 'sync', 30), charge.repos_tour_secondes(self.desk, 1800))
        self.desk.setting(economie569.SETTING, True)
        self.assertEqual(charge.niveau(self.desk), 'econome')
        econome = (charge.plafond(self.desk, 'monitor_matter'), charge.intervalle_minutes(self.desk, 'sync', 30), charge.repos_tour_secondes(self.desk, 1800))
        self.assertEqual(normal, (60, 30, 1800))
        self.assertEqual(econome, (30, 60, 3600))
        self.desk.setting(charge.FREQUENCE, {'valeur': 'minimale', 'revision': 1})
        self.assertEqual(charge.niveau(self.desk), 'minimale')             # le régime économe n'abaisse jamais un réglage plus sobre
        self.assertEqual(charge.repos_tour_secondes(self.desk, 1800), 4 * 3600)

    def test_suites_de_missions_plus_toutes_les_30_secondes(self):
        from agent import proactive568
        appels = []
        with patch('agent.settings568.owners', return_value=['cabinet']), patch('agent.settings568.profile', return_value={
                'enabled': True, 'timezone': 'Europe/Paris', 'news_enabled': False, 'roles': [], 'weekdays': [], 'briefing_time': '07:00'}), \
                patch('agent.settings568.check_owner'), patch.object(proactive568, 'needs_cycle', return_value=True), \
                patch('agent.cadence5662.allow', return_value=True), patch.object(self.desk, 'enqueue', side_effect=lambda k, *a, **kw: appels.append((k, time.time())) or 1):
            debut = time.time()
            for s in range(0, 600, 15):
                proactive568.schedule(self.desk, debut + s)
        cycles = [a for a in appels if a[0] == 'proactive568_cycle']
        self.assertLessEqual(len(cycles), 3)                                # avant : jusqu'à 20 en 10 minutes
        self.assertGreaterEqual(len(cycles), 1)


# ---------------------------------------------------------------- parcours des dossiers et relevé des correspondants
class Parcours(Cabinet):
    DOSSIERS = 8

    def test_parcours_inchange_sans_relecture(self):
        dav = self.dav()
        from agent import workspace
        r = workspace.discover(self.desk, dav)
        while r['parcours_partiel']:
            r = workspace.discover(self.desk, dav)
        self.assertEqual(r['dossiers_decouverts'], 0)
        avant = dict(self.nc.compte)
        r = workspace.discover(self.desk, dav)
        self.assertTrue(r.get('inchange'))
        self.assertEqual({k: v - avant.get(k, 0) for k, v in self.nc.compte.items() if v != avant.get(k, 0)}, {'PROPFIND:0': 1})
        # un dossier nouveau apparaît : il est découvert, sans relire les archives inchangées
        self.nc.fichier('/Dossiers/Clients/ZORVAL 99 - 2026000999/Pieces/a.txt', 'Pièce fictive.')
        avant = self.nc.total('PROPFIND:1')
        r = workspace.discover(self.desk, dav)
        while r['parcours_partiel']:
            r = workspace.discover(self.desk, dav)
        self.assertEqual(r['dossiers_decouverts'], 1)
        self.assertLessEqual(self.nc.total('PROPFIND:1') - avant, 3)       # /Dossiers et /Dossiers/Clients (les archives sont sautées)

    def test_releve_des_correspondants_incremental(self):
        from agent.mailbox import Mailbox
        box = Mailbox(self.desk.c['mail'])
        self.desk.scan_mail(box, self.matters)
        premier = self.boite.total()
        self.assertGreater(premier, 380)                                  # 80 + 50 messages : trois commandes chacun
        avant = self.boite.total()
        self.boite.arrivee('INBOX', 'Dossier DOS-001 : pièces', datetime.now(timezone.utc))
        self.desk.scan_mail(box, self.matters)
        self.assertLessEqual(self.boite.total() - avant, 2 * 2 + 3 + 2)    # deux recherches, le seul message nouveau
        proposition = self.desk.db.execute("SELECT matter FROM proposals WHERE email='correspondant@example.test'").fetchone()
        self.assertEqual(proposition[0], 'DOS-001')
        avant = self.boite.total()
        self.desk.scan_mail(box, self.matters + dossiers_fictifs(60)[50:])   # liste des dossiers changée : tout est relu
        self.assertGreater(self.boite.total() - avant, 380)


# ---------------------------------------------------------------- fréquence, page, pause
class Frequence(Cabinet):
    DOSSIERS = 3

    def schedule(self, stamp):
        with patch('agent.proactive568.schedule', return_value=[]), patch('agent.activity431.maintenance'):
            return watch430._schedule_5642(self.desk, stamp)

    def test_minimale_espace_les_documents_jamais_les_courriels(self):
        self.desk.setting(charge.FREQUENCE, {'valeur': 'minimale', 'revision': 1})
        now = time.time()
        self.desk.setting('live430:documents_sweep_done', now - 3600)       # tour fini il y a 1 h : 4 h de repos en réglage minimal
        self.desk.setting('live430:document_cursor', 0)
        self.schedule(now)
        kinds = {r[0] for r in self.desk.db.execute("SELECT kind FROM jobs WHERE status='pending'")}
        self.assertIn('live_mail430', kinds)
        self.assertNotIn('live_documents430', kinds)
        self.desk.db.execute("UPDATE jobs SET status='done',finished=?", (self.desk.now(),))
        self.desk.db.commit()
        self.schedule(now + 301)                                           # 5 minutes plus tard : relève des courriels, toujours
        self.assertEqual(len(self.jobs('live_mail430')), 1)
        self.assertEqual(len(self.jobs('live_documents430')), 0)
        self.schedule(now + 3 * 3600 + 10)
        self.assertEqual(len(self.jobs('live_documents430')), 1)

    def test_pause_de_l_ia_arrete_la_surveillance_automatique(self):
        html = accueil5689.pause_html(self.desk)
        self.assertIn('id="a92-pause"', html)
        with automatic(self.desk):
            attente = self.desk.enqueue('live_documents430', priority=55)
        sortie = pause_all(self.desk, True, 'avocat@example.test')
        self.assertTrue(sortie['paused'])
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (attente,)).fetchone()[0], 'suspended')
        self.assertIn('IA en pause — reprendre', accueil5689.pause_html(self.desk))
        now = time.time()
        for s in range(0, 1800, 15):
            self.assertEqual(self.schedule(now + s), [])
        operations._automation_tick_5642(self.desk)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0], 0)
        self.assertFalse(self.desk.work_once())                            # rien à exécuter : la file automatique est suspendue
        pause_all(self.desk, False, 'avocat@example.test')
        self.schedule(now + 2000)
        self.assertGreaterEqual(len(self.jobs('live_mail430')), 1)          # reprise : la relève revient au passage suivant


class Page(unittest.TestCase):
    """Toute la pile web (authentification, droits, ``web5689``, CSRF), compte administrateur fictif."""
    ORIGIN = ho.Application.ORIGIN
    setUp = ho.Application.setUp
    requete = ho.Application.requete

    def test_page_charge_du_serveur_sans_contenu_de_dossier(self):
        self.desk.setting('live430:documents_sweep_done', time.time() - 600)
        with automatic(self.desk):
            self.desk.enqueue('monitor_matter', {'matter': 'DOS-001'}, priority=60)
        charge.noter(self.desk, 'live_documents430', {'stockage': 7, 'imap': 3})
        etat = json.loads(self.requete('/api440/m5689/charge')['body'])
        self.assertEqual(etat['mesures']['stockage']['heure'], 7)
        self.assertEqual(etat['mesures']['imap']['jour'], 3)
        self.assertEqual(next(t for t in etat['travaux'] if t['kind'] == 'monitor_matter')['heure'], 1)
        self.assertTrue(etat['dernier_tour'])
        brut = json.dumps(etat)
        self.assertNotIn('/Dossiers', brut)
        self.assertNotIn('DOS-001', brut)                                   # ni dossier ni argument de travail
        page = self.requete('/parametres', requete='rubrique=poste')
        corps = page['body'].decode()
        self.assertIn('id="charge5693"', corps)
        self.assertIn('Fréquence de surveillance des documents', corps)
        self.assertIn('data-api="m5689/charge/frequence"', corps)
        self.assertNotIn('/Dossiers/DEMO', corps[corps.index('id="charge5693"'):corps.index('</section>', corps.index('id="charge5693"'))])

    def test_reglage_revision_et_droits(self):
        r = self.requete('/api440/m5689/charge/frequence', 'POST', {'valeur': 'minimale', 'revision': 0})
        self.assertTrue(r['status'].startswith('200'), r)
        self.assertEqual(json.loads(r['body'])['effectif'], 'minimale')
        perimee = self.requete('/api440/m5689/charge/frequence', 'POST', {'valeur': 'normale', 'revision': 0})
        self.assertTrue(perimee['status'].startswith('409'))
        self.assertEqual(charge.reglage(self.desk)['valeur'], 'minimale')
        sans_csrf = self.requete('/api440/m5689/charge/frequence', 'POST', {'valeur': 'normale', 'revision': 1}, csrf='faux')
        self.assertFalse(sans_csrf['status'].startswith('200'))
        self.assertEqual(charge.reglage(self.desk)['valeur'], 'minimale')
        invalide = self.requete('/api440/m5689/charge/frequence', 'POST', {'valeur': 'jamais', 'revision': 1})
        self.assertTrue(invalide['status'].startswith('400'))
        self.assertFalse(cw.allowed('assistant', 'charge/frequence'))
        self.assertTrue(cw.allowed('avocat', 'charge/frequence'))
        from agent import membres5650
        self.assertEqual(membres5650.category('POST', '/api440/m5689/charge/frequence'), 'reglages')
        audits = self.desk.db.execute("SELECT COUNT(*) FROM audit WHERE action='charge5693_frequence'").fetchone()[0]
        self.assertEqual(audits, 1)

    def test_echappement_et_lecture_seule(self):
        html = cw.section_html(self.desk, '/p', 'assistant@example.test', 'assistant')
        self.assertIn('Consultation seule', html)
        self.assertNotIn('<button class="ax-btn" type="submit">', html)
        self.assertIn(' disabled', html)


# ---------------------------------------------------------------- N07 (5.6.90) : sous Linux et Windows
class N07(ho.Base):
    def test_N07_T2_soir_et_nuit_une_seule_pause_frontieres_et_changements_d_heure(self):
        annee = datetime.now(timezone.utc).year + 1
        lundi = datetime(annee, 11, 2) + timedelta(days=(7 - datetime(annee, 11, 2).weekday()) % 7)
        jour = lundi.date()
        self.assertFalse(ho.calcule(ho.NUIT, ho.paris(jour.year, jour.month, jour.day, 17, 59, 59))['pause'])
        soir = ho.calcule(ho.NUIT, ho.paris(jour.year, jour.month, jour.day, 18))
        self.assertTrue(soir['pause'])
        lendemain = jour + timedelta(days=1)
        sept = ho.paris(lendemain.year, lendemain.month, lendemain.day, 7)
        self.assertEqual(soir['fin'], sept)                                 # pas de reprise annoncée à minuit
        self.assertTrue(ho.calcule(ho.NUIT, ho.paris(lendemain.year, lendemain.month, lendemain.day, 0))['pause'])
        self.assertTrue(ho.calcule(ho.NUIT, sept - timedelta(seconds=1))['pause'])
        self.assertFalse(ho.calcule(ho.NUIT, sept)['pause'])
        fusion, _ = h.plages_utc(ho.politique(ho.NUIT), jour, jour + timedelta(days=1))
        self.assertIn((ho.paris(jour.year, jour.month, jour.day, 18), sept), fusion)

        def dernier_dimanche(mois):
            d = datetime(annee, mois, 31) if mois in (3, 10) else None
            return (d - timedelta(days=(d.weekday() + 1) % 7)).date()
        for mois in (3, 10):                                               # changements d'heure de l'année suivante, jamais une date figée
            dimanche = dernier_dimanche(mois)
            samedi = dimanche - timedelta(days=1)
            soir = ho.calcule(ho.NUIT, ho.paris(samedi.year, samedi.month, samedi.day, 18))
            fin = ho.paris(dimanche.year, dimanche.month, dimanche.day, 7)
            self.assertEqual(soir['fin'], fin)
            self.assertEqual((fin - ho.paris(samedi.year, samedi.month, samedi.day, 18)).total_seconds() / 3600, 12 if mois == 3 else 14)
            self.assertTrue(ho.calcule(ho.NUIT, ho.paris(dimanche.year, dimanche.month, dimanche.day, 2, 30))['pause'])
            self.assertFalse(ho.calcule(ho.NUIT, fin)['pause'])

    def test_N07_T3_pause_manuelle_independante_annulation_terminale_nuit_sans_tentative(self):
        annee = datetime.now(timezone.utc).year + 1
        jour = datetime(annee, 11, 9).date()
        self.horloge.instant = ho.paris(jour.year, jour.month, jour.day, 17)
        self.activer()
        repris = self.suspendre('refresh_brief', {'matter': ho.MID})
        annule = self.suspendre('extract_facts460', {'matter': ho.MID})
        self.desk.cancel_job(annule)
        self.horloge.instant = ho.paris(jour.year, jour.month, jour.day, 20)
        h.surveiller(self.desk)
        pause_all(self.desk, True, 'avocat@example.test')                   # pause manuelle posée pendant la nuit
        lendemain = jour + timedelta(days=1)
        self.horloge.instant = ho.paris(lendemain.year, lendemain.month, lendemain.day, 7, 1)
        with patch.object(arret5643, '_clock', self.horloge):
            h.surveiller(self.desk)
        self.assertTrue(self.desk.settings('missions567:pause'))            # la fin de la plage ne lève pas la pause manuelle
        self.assertEqual(self.statut(repris)['status'], 'suspended')
        self.assertEqual(h.etat_public(self.desk, self.horloge.instant)['politique']['enabled'], True)
        with patch.object(arret5643, '_clock', self.horloge):
            pause_all(self.desk, False, 'avocat@example.test')              # « Reprendre » : les horaires restent tels quels
        self.assertEqual(h.etat_public(self.desk, self.horloge.instant)['politique']['windows'], ho.NUIT)
        self.assertEqual(self.statut(repris)['status'], 'pending')
        self.assertEqual(self.statut(annule)['status'], 'cancelled')        # une annulation ne reprend jamais
        # attente nocturne : un travail arrêté par la plage ne consomme pas de tentative
        self.horloge.instant = ho.paris(lendemain.year, lendemain.month, lendemain.day, 19)
        h.surveiller(self.desk)
        jid = self.suspendre('refresh_brief', {'matter': ho.MID, 'k': 2})
        self.desk.db.execute("UPDATE jobs SET attempts=1 WHERE id=?", (jid,))
        self.desk.db.commit()
        ligne = self.desk.db.execute('SELECT kind,origin,priority,args,status,result,stop_reason,matter_ref FROM jobs WHERE id=?', (jid,)).fetchone()
        h.apres_suspension(self.desk, jid, ligne)
        self.assertEqual(self.statut(jid)['attempts'], 0)
        self.assertEqual(self.statut(jid)['status'], 'suspended')

    def test_N07_T1_localtime_refuse_sur_ce_systeme(self):
        """Rappel 5.6.91, rejoué ici sous Linux et Windows : listé ou non, « localtime » est refusé."""
        for liste in (frozenset({'localtime', 'Europe/Paris', 'UTC'}), frozenset({'Europe/Paris', 'UTC'})):
            h._noms_iana.cache_clear()
            with patch.object(h, 'available_timezones', return_value=liste):
                with self.assertRaises(Stop):
                    h.valider(dict(h.politique_defaut(), timezone='localtime'))
                self.assertEqual(h.valider(dict(h.politique_defaut(), timezone='Europe/Paris'))['timezone'], 'Europe/Paris')
        h._noms_iana.cache_clear()


# ---------------------------------------------------------------- journée simulée (chiffres avant / après)
class Horloge:
    def __init__(self, debut):
        self.t = debut

    def __call__(self):
        return self.t

    def iso(self):
        return datetime.fromtimestamp(self.t, timezone.utc).isoformat()


MODELES = ('refresh_brief', 'sync_legal_memory', 'analyze_deadline450', 'analyze_notice440', 'refresh_operational_memory')


class Journee(Cabinet):
    """Journée d'un cabinet fictif (50 dossiers, 2 000 fichiers, 30 courriels) : mêmes cadences, mêmes événements, même file de travail
    réelle (``Desk.work_once``), une fois avec le comportement 5.6.92 (interrupteur de secours) et une fois avec les corrections."""
    DOSSIERS = 50

    def simuler(self, corrige):
        self.desk.setting(charge.ACTIF, corrige)
        horloge = Horloge(time.time())
        modeles = []
        vrai_perform = Desk.perform

        def perform(desk, kind, args):
            if kind in MODELES:                                            # un modèle serait appelé : compté, jamais appelé
                modeles.append(kind)
                return {'simule': True}
            return vrai_perform(desk, kind, args)

        patches = [patch.object(time, 'time', horloge), patch.object(desk_module, 'now', horloge.iso), patch.object(Desk, 'perform', perform),
                   patch('agent.relevance370.review_result', return_value=None), patch('agent.proactive568.document_events')]
        for p in patches:
            p.start()
        try:
            self._journee(horloge, warmup=True)
            for m in self.matters:                                         # mise en route : tout le cabinet indexé une fois, dans les deux cas
                for _ in range(10):
                    if not self.desk.perform('index', {'matter': m['id']}).get('en_attente') and self._indexe(m['id']):
                        break
            charge.relever()                                               # compteurs de la mise en route écartés
            self.nc.compte.clear()
            self.boite.compte.clear()
            modeles.clear()
            self.desk.db.execute('DELETE FROM charge5693_mesures')
            jobs_avant = self.desk.db.execute('SELECT MAX(id) FROM jobs').fetchone()[0] or 0
            self._journee(horloge, warmup=False)
            travaux = dict(self.desk.db.execute('SELECT kind,COUNT(*) FROM jobs WHERE id>? GROUP BY kind', (jobs_avant,)).fetchall())
            mesures = dict(self.desk.db.execute('SELECT categorie,SUM(n) FROM charge5693_mesures GROUP BY categorie').fetchall())
        finally:
            for p in reversed(patches):
                p.stop()
        return {'propfind': self.nc.total('PROPFIND'), 'get': self.nc.compte.get('GET', 0), 'report': self.nc.compte.get('REPORT', 0),
                'imap': self.boite.total(), 'extractions': mesures.get('extraction', 0), 'empreintes': mesures.get('embeddings', 0),
                'modeles': len(modeles), 'travaux': travaux, 'stockage_mesure': mesures.get('stockage', 0)}

    def _indexe(self, mid):
        from agent.index import DocumentIndex
        index = DocumentIndex(self.desk.c['state_dir'])
        try:
            return not index.db.execute('SELECT 1 FROM inventory_scans WHERE matter=?', (mid,)).fetchone()
        finally:
            index.db.close()

    def _journee(self, horloge, warmup):
        from agent.mailbox import Mailbox
        debut = horloge.t
        dernier = {'mail': -1e9, 'lot': -1e9, 'sync': -1e9, 'agenda': -1e9}
        modifies = list(range(0, 50, 5))                                   # 10 dossiers modifiés dans la journée, 3 fois chacun
        evenements = {} if warmup else {60 * (40 + 25 * k): ('fichier', modifies[k % 10], k) for k in range(30)}
        if not warmup:
            for k in range(30):
                evenements.setdefault(60 * (35 + 25 * k) + 30, ('courriel', k, k))
        duree = 6 * 3600 if warmup else 24 * 3600
        for minute in range(0, duree, 60):
            horloge.t = debut + minute
            ev = evenements.get(minute) or evenements.get(minute + 30)
            if ev and ev[0] == 'fichier':
                self.modifier(ev[1], 'Pieces/ajout-%02d.txt' % ev[2], datetime.fromtimestamp(horloge.t, timezone.utc))
            if ev and ev[0] == 'courriel':
                self.boite.arrivee('INBOX', 'Courriel fictif %d DOS-%03d' % (ev[1], ev[1] + 1), datetime.fromtimestamp(horloge.t, timezone.utc))
            with automatic(self.desk):
                if horloge.t - dernier['mail'] >= 300:                     # relève des courriels (identique avant / après)
                    box = Mailbox(self.desk.c['mail'])
                    try:
                        for uid in box.unseen():
                            box.fetch('INBOX', uid, headers_only=True)
                            next(m for m in self.boite.messages['INBOX'] if str(m['uid']) == uid)['lu'] = True
                    finally:
                        box.close()
                    dernier['mail'] = horloge.t
                if horloge.t - dernier['agenda'] >= charge.intervalle_minutes(self.desk, 'live_calendar430', 3) * 60:
                    self.desk.enqueue('live_calendar430', priority=55)
                    dernier['agenda'] = horloge.t
                repos = charge.repos_tour_secondes(self.desk, economie569.interval(self.desk, 'live_documents430', 30) * 60)
                curseur = int(self.desk.settings('live430:document_cursor', 0) or 0)
                fini = float(self.desk.settings('live430:documents_sweep_done', 0) or 0)
                if horloge.t - dernier['lot'] >= watch430.LOT_DOCUMENTS and (curseur != 0 or horloge.t - fini >= repos):
                    self.desk.enqueue('live_documents430', priority=55)
                    dernier['lot'] = horloge.t
                if horloge.t - dernier['sync'] >= charge.intervalle_minutes(self.desk, 'sync', 30) * 60:
                    self.desk.enqueue('sync', priority=50)
                    dernier['sync'] = horloge.t
            for _ in range(200):
                if not self.desk.work_once():
                    break

    def test_journee_simulee_avant_apres(self):
        avant = self.simuler(False)
        self.tearDown_partial()
        apres = self.simuler(True)
        rapport = {'avant_5692': avant, 'apres_5693': apres}
        sortie = os.environ.get('AXH_CHARGE5693_RAPPORT')
        if sortie:
            with open(sortie, 'w', encoding='utf-8') as fichier:
                json.dump(rapport, fichier, ensure_ascii=False, indent=2)
        # la détection est conservée : chaque dossier modifié a été indexé et surveillé dans les deux cas
        for cle in ('index', 'monitor_matter'):
            self.assertGreaterEqual(apres['travaux'].get(cle, 0), 10, rapport)
        self.assertLessEqual(apres['extractions'], avant['extractions'], rapport)   # jamais plus de fichiers lus qu'avant
        self.assertGreaterEqual(apres['extractions'], 30, rapport)                  # chaque fichier nouveau de la journée a été lu
        self.assertLess(apres['propfind'], avant['propfind'] / 4, rapport)
        self.assertLess(apres['imap'], avant['imap'] / 4, rapport)
        self.assertLessEqual(apres['travaux'].get('discover', 0), avant['travaux'].get('discover', 0), rapport)
        self.assertLessEqual(apres['travaux'].get('live_documents430', 0), avant['travaux'].get('live_documents430', 0), rapport)
        self.assertLessEqual(apres['modeles'], avant['modeles'], rapport)

    def tearDown_partial(self):
        """Second passage sur un cabinet identique, repartant de zéro (nouvelle base, mêmes fichiers)."""
        self.desk.db.close()
        self.f.doCleanups()
        self.setUp()


if __name__ == '__main__':
    unittest.main()
