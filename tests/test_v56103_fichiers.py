"""5.6.103, lot F : page « Fichiers » (explorateur Nextcloud à deux volets, disque local du poste).

Faux WebDAV en mémoire (ETag, 412 sur If-Match périmé, versions, LOCK refusé ou accepté) branché sur le client existant ``dav.DAV`` ; disque
local dans un dossier temporaire. Données fictives (DURAND, KERVILAC, DOS-001), aucun réseau, aucun modèle. Les dates viennent de l'horloge
du test ou du fuseau du cabinet, jamais de ``date.today()``.

Identifiants : F1 à F8 = fonctions de la feuille de lot (volets et transferts, filtre et recherche, renommage par lot, notes et couleurs,
propriétés, ZIP, versions Nextcloud, disque local) ; S = sûreté (chemins, droits, idempotence) ; R05 = aucune écriture pendant une lecture ;
W = page et routes.
"""
import base64
import email.utils
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.parse as U
import zipfile

import test_desk
from agent import fichiers56103 as F, fichiers_web56103 as W, membres5650
from agent.common import Stop, load_config, private_json
from agent.desk import Desk
from agent.erreurs5634 import http_error

PREFIX = '/agent-courriel'
DURAND = ('durand@example.test', 'avocat')
ASSISTANT = ('bernard@example.test', 'assistant')
ADMIN = ('cabinet', 'administrateur')
BASE = 'https://nc.example.test'
FILES = '/remote.php/dav/files/cabinet'
VERS = '/remote.php/dav/versions/cabinet/versions/'


class FakeNC:
    """Serveur Nextcloud en mémoire : PROPFIND, GET, PUT, MKCOL, MOVE, COPY, DELETE (corbeille), LOCK, UNLOCK, versions."""

    def __init__(self):
        self.base = BASE
        self.t, self.n, self.journal, self.trash, self.last_headers = {}, 100, [], [], {}
        self.lock_mode, self.locks, self.horloge = 'refuse', {}, 1_770_000_000
        self.mkdir('/Dossiers')

    def _etag(self):
        self.n += 1
        return '"e%d"' % self.n

    def mkdir(self, p):
        self.t[p] = {'dir': True, 'etag': self._etag(), 'fileid': self.n, 'mtime': self.horloge, 'versions': []}

    def put(self, p, data, auteur='DURAND Claire'):
        self.horloge += 60
        old = self.t.get(p)
        if old and not old['dir']:
            old['versions'].append((str(old['mtime']), old['data'], old.get('auteur', '')))
            old.update(data=data, etag=self._etag(), mtime=self.horloge, auteur=auteur)
        else:
            self.t[p] = {'dir': False, 'data': data, 'etag': self._etag(), 'fileid': self.n, 'mtime': self.horloge, 'versions': [], 'auteur': auteur}
        return self.t[p]

    def lire(self, p):
        return self.t[p]['data']

    # --------------------------------------------------------------------------------------------------- HTTP
    def _chemin(self, url):
        s = U.urlsplit(url)
        assert (s.scheme + '://' + s.netloc) == BASE, url
        chemin = U.unquote(s.path)
        if chemin.startswith(VERS):
            return 'v', chemin[len(VERS):].strip('/')
        assert chemin.startswith(FILES), url
        return 'f', ('/' + chemin[len(FILES):].strip('/')) if chemin[len(FILES):].strip('/') else '/'

    def _enfants(self, p):
        return sorted(k for k in self.t if k != p and k.startswith(p.rstrip('/') + '/') and '/' not in k[len(p.rstrip('/')) + 1:])

    def _prop(self, p, n):
        mod = email.utils.formatdate(n['mtime'], usegmt=True)
        corps = '<d:resourcetype>%s</d:resourcetype><d:getetag>%s</d:getetag><d:getlastmodified>%s</d:getlastmodified><oc:fileid>%d</oc:fileid>' % (
            '<d:collection/>' if n['dir'] else '', n['etag'], mod, n['fileid'])
        if not n['dir']:
            corps += '<d:getcontentlength>%d</d:getcontentlength><oc:owner-display-name>%s</oc:owner-display-name><d:getcontenttype>application/pdf</d:getcontenttype>' % (
                len(n['data']), n.get('auteur', ''))
        href = U.quote(FILES + p) + ('/' if n['dir'] else '')
        return '<d:response><d:href>%s</d:href><d:propstat><d:prop>%s</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>' % (href, corps)

    @staticmethod
    def _ms(corps):
        return ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">%s</d:multistatus>' % corps).encode()

    def _par_id(self, fid):
        return next((k for k, n in self.t.items() if str(n['fileid']) == str(fid)), None)

    def request(self, method, url, data=None, headers=None, limit=12_000_000):
        headers = headers or {}
        self.journal.append((method, url))
        self.last_headers = {}
        genre, p = self._chemin(url)
        err = lambda code: http_error(code, method, url)
        if genre == 'v':
            parts = p.split('/')
            k = self._par_id(parts[0])
            if k is None:
                raise err(404)
            n = self.t[k]
            if method == 'PROPFIND':
                corps = '<d:response><d:href>%s</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>' % U.quote(VERS + parts[0] + '/')
                for vid, contenu, auteur in n['versions']:
                    corps += ('<d:response><d:href>%s</d:href><d:propstat><d:prop><d:getlastmodified>%s</d:getlastmodified><d:getcontentlength>%d</d:getcontentlength>'
                              '<nc:version-author>%s</nc:version-author></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>') % (
                        U.quote(VERS + parts[0] + '/' + vid), email.utils.formatdate(int(vid), usegmt=True), len(contenu), auteur)
                return self._ms(corps)
            if method == 'GET' and len(parts) == 2:
                for vid, contenu, auteur in n['versions']:
                    if vid == parts[1]:
                        return contenu
            raise err(404)
        n = self.t.get(p)
        if method == 'PROPFIND':
            if n is None:
                raise err(404)
            corps = self._prop(p, n)
            if headers.get('Depth') == '1' and n['dir']:
                corps += ''.join(self._prop(k, self.t[k]) for k in self._enfants(p))
            return self._ms(corps)
        if method == 'GET':
            if n is None or n['dir']:
                raise err(404)
            if headers.get('If-Match') and headers['If-Match'] != n['etag']:
                raise err(412)
            if len(n['data']) > limit:
                raise Stop('reponse_trop_volumineuse')
            return n['data']
        if method == 'PUT':
            parent = p.rsplit('/', 1)[0] or '/'
            if parent not in self.t:
                raise err(409)
            if headers.get('If-None-Match') == '*' and n is not None:
                raise err(412)
            if headers.get('If-Match') and (n is None or headers['If-Match'] != n['etag']):
                raise err(412)
            self.put(p, bytes(data), 'Compte du cabinet')
            self.last_headers = {'ETag': self.t[p]['etag']}
            return b''
        if method == 'MKCOL':
            if n is not None:
                raise err(405)
            if (p.rsplit('/', 1)[0] or '/') not in self.t:
                raise err(409)
            self.mkdir(p)
            return b''
        if method in ('MOVE', 'COPY'):
            if n is None:
                raise err(404)
            if headers.get('If-Match') and headers['If-Match'] != n['etag']:
                raise err(412)
            dest = self._chemin(headers['Destination'])[1]
            if dest in self.t and headers.get('Overwrite') == 'F':
                raise err(412)
            if (dest.rsplit('/', 1)[0] or '/') not in self.t:
                raise err(409)
            sous = [k for k in self.t if k == p or k.startswith(p + '/')]
            for k in sorted(sous):
                nouveau = dest + k[len(p):]
                if method == 'COPY':
                    copie = dict(self.t[k], versions=[], etag=self._etag())
                    self.n += 1
                    copie['fileid'] = self.n
                    self.t[nouveau] = copie
                else:
                    self.t[nouveau] = self.t.pop(k)
            return b''
        if method == 'DELETE':
            if n is None:
                raise err(404)
            if headers.get('If-Match') and headers['If-Match'] != n['etag']:
                raise err(412)
            for k in [k for k in self.t if k == p or k.startswith(p + '/')]:
                self.trash.append((k, self.t.pop(k)))
            return b''
        if method == 'LOCK':
            if self.lock_mode == 'refuse':
                raise err(405)
            if p in self.locks:
                raise err(423)
            self.locks[p] = 'opaquelocktoken:essai-%d' % len(self.locks)
            self.last_headers = {'Lock-Token': '<%s>' % self.locks[p]}
            return b'<?xml version="1.0"?><d:prop xmlns:d="DAV:"/>'
        if method == 'UNLOCK':
            if self.locks.get(p) and headers.get('Lock-Token') == '<%s>' % self.locks[p]:
                del self.locks[p]
                return b''
            raise err(409)
        raise err(405)


def nouveau_dav(fake):
    from agent.dav import DAV
    with patch('agent.exapp5642.nextcloud_http', return_value=fake):
        return DAV({'url': BASE, 'username': 'cabinet', 'password_file': 'inutile', 'roots': ['/Dossiers']})


def zip_de(entrees):
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, 'w', zipfile.ZIP_DEFLATED) as zf:
        for nom, contenu in entrees:
            if isinstance(nom, zipfile.ZipInfo):
                zf.writestr(nom, contenu)
            else:
                zf.writestr(nom, contenu)
    return tampon.getvalue()


class Base(unittest.TestCase):
    setUp0 = test_desk.WebTests.setUp

    def setUp(self):
        self.setUp0()
        os.environ.pop('AXIORHUB_POSTE_HOME', None)
        self.f.matters.append({'id': 'DOS-002', 'client_name': 'KERVILAC', 'path': '/Dossiers/KERVILAC', 'references': ['DOS-002'], 'aliases': ['KERVILAC'],
                               'correspondents': []})
        private_json(self.f.matter_file, self.f.matters)
        self.f.c['nextcloud'].update(url=BASE, username='cabinet', password_file=str(self.f.base / 'nc.secret'))
        private_json(self.config, self.f.c)
        self.nc = FakeNC()
        self.nc.mkdir('/Dossiers/DEMO')
        self.nc.mkdir('/Dossiers/KERVILAC')
        self.nc.put('/Dossiers/DEMO/Assignation.pdf', b'%PDF assignation DURAND')
        self.nc.put('/Dossiers/DEMO/Bail.pdf', b'%PDF bail commercial')
        self.nc.put('/Dossiers/DEMO/Courrier.pdf', b'%PDF courrier ORLANDEC')
        self.nc.put('/Dossiers/KERVILAC/Secret DURAND.pdf', b'%PDF reserve')
        self.dav = nouveau_dav(self.nc)
        self.desk = Desk(load_config(str(self.config)))
        self.addCleanup(self.desk.db.close)
        membres5650.set_reserve(self.desk.c['state_dir'], 'DOS-002', ['martin@example.test'])
        self.n = 0

    def src(self):
        return {'nextcloud': F.SourceNextcloud(self.desk, self.dav)}

    def cle(self):
        self.n += 1
        return 'cle-essai-fichiers-%04d' % self.n

    def entree(self, chemin, acteur=DURAND):
        dossier = chemin.rsplit('/', 1)[0]
        return next(x for x in F.lister(self.desk, {}, acteur, 'nextcloud', dossier, self.src())['entrees'] if x['chemin'] == chemin)

    def appel(self, route, data=None, acteur=DURAND, methode='POST', args=None, csrf='test-csrf', auth=None):
        """Appel direct du service web avec l'identité posée par la couche de connexion."""
        raw = json.dumps(data or {}).encode()
        env = {'REQUEST_METHOD': methode, 'HTTP_ORIGIN': self.origin, 'HTTP_X_CSRF_TOKEN': csrf, 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw), 'HTTP_X_AXIORHUB_ROLE': acteur[1], 'HTTP_X_AXIORHUB_USER': acteur[0]}
        auth = auth or {'origin': self.origin, 'csrf': 'test-csrf'}
        with patch('agent.fichiers5670.client', return_value=self.dav):
            out = W.api(env, self.desk, auth, PREFIX, W.PREFIXE + route, args or {}, methode)
        out['json'] = json.loads(out['body'])
        out['entetes'] = dict(out.get('headers', []))
        return out


# ------------------------------------------------------------------------------------------------ S · chemins et droits
class Surete(Base):
    def test_S1_dossier_reserve_meme_404_qu_un_absent_et_invisible_dans_la_liste(self):
        racine = F.lister(self.desk, {}, DURAND, 'nextcloud', '/Dossiers', self.src())
        self.assertEqual([x['nom'] for x in racine['entrees']], ['DEMO'])                 # KERVILAC, réservé, n'apparaît pas
        admin = F.lister(self.desk, {}, ADMIN, 'nextcloud', '/Dossiers', self.src())
        self.assertEqual([x['nom'] for x in admin['entrees']], ['DEMO', 'KERVILAC'])
        reserve = self.appel('lister', methode='GET', args={'s': 'nextcloud', 'p': '/Dossiers/KERVILAC'})
        absent = self.appel('lister', methode='GET', args={'s': 'nextcloud', 'p': '/Dossiers/ABSENT'})
        self.assertEqual((reserve['status'], reserve['json']), (absent['status'], absent['json']))
        self.assertEqual(reserve['status'], '404 Not Found')
        self.assertNotIn('Secret', reserve['body'])
        for route, args in (('proprietes', {'s': 'nextcloud', 'p': '/Dossiers/KERVILAC/Secret DURAND.pdf', 'empreinte': '1'}),
                            ('versions', {'s': 'nextcloud', 'p': '/Dossiers/KERVILAC/Secret DURAND.pdf'})):
            r = self.appel(route, methode='GET', args=args)
            self.assertEqual((r['status'], r['json']['error']), ('404 Not Found', 'introuvable'))
        avant = len(self.nc.journal)
        r = self.appel('note', {'s': 'nextcloud', 'p': '/Dossiers/KERVILAC/Secret DURAND.pdf', 'couleur': 'rouge', 'note': 'x', 'version': 0, 'cle': self.cle()})
        self.assertEqual(r['status'], '404 Not Found')
        self.assertEqual(len(self.nc.journal), avant)                                   # refus AVANT tout accès au stockage
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM fichiers56103_notes').fetchone()[0], 0)

    def test_S1_mutation_sans_controle_des_dossiers_reserves_le_contenu_fuirait(self):
        with patch.object(F.SourceNextcloud, '_caches', return_value=[]):
            noms = [x['nom'] for x in F.lister(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/KERVILAC', self.src())['entrees']]
        self.assertEqual(noms, ['Secret DURAND.pdf'])                                     # le garde-fou est bien ce qui masque

    def test_S1_chemins_sortants_et_hors_racines_refuses(self):
        for p in ('/Autre', '/Dossiers/../etc', '../Dossiers', '/Dossiers/DEMO/%2e%2e/KERVILAC', 'C:\\Windows', '/Dossiers/.cache', '/Dossiers/secrets'):
            with self.assertRaises(Stop) as ex:
                F.lister(self.desk, {}, DURAND, 'nextcloud', p, self.src())
            self.assertEqual(str(ex.exception), 'introuvable', p)
        with self.assertRaises(Stop) as ex:
            F.ouvrir(self.desk, {}, DURAND, 'inconnue', '/Dossiers', self.src())
        self.assertEqual(str(ex.exception), 'introuvable')

    def test_S2_controle_des_acces_illisible_503_jamais_un_acces_elargi(self):
        with patch('agent.membres5650.load', side_effect=Stop('controle_acces_indisponible')):
            r = self.appel('lister', methode='GET', args={'s': 'nextcloud', 'p': '/Dossiers'})
        self.assertEqual(r['status'], '503 Service Unavailable')
        self.assertNotIn('DEMO', r['body'])

    def test_S3_idempotence_meme_cle_meme_recu_autre_usage_409(self):
        donnees = {'mode': 'copier', 's': 'nextcloud', 'p': '/Dossiers/DEMO/Bail.pdf', 'etag': '', 'source_cible': 'nextcloud',
                   'dossier_cible': '/Dossiers/DEMO/Copies', 'cle': 'cle-unique-essai-01'}
        self.nc.mkdir('/Dossiers/DEMO/Copies')
        un = self.appel('transferer', donnees)
        deux = self.appel('transferer', donnees)
        self.assertEqual(un['status'], '200 OK')
        self.assertTrue(deux['json'].get('rejoue'))
        self.assertEqual(sum(1 for m, _ in self.nc.journal if m == 'COPY'), 1)            # un seul effet
        autre = self.appel('transferer', dict(donnees, p='/Dossiers/DEMO/Courrier.pdf'))
        self.assertEqual((autre['status'], autre['json']['error']), ('409 Conflict', 'cle_reutilisee'))
        sans = self.appel('transferer', dict(donnees, cle=''))
        self.assertEqual(sans['json']['error'], 'cle_requise')
        self.assertEqual(un['entetes']['Cache-Control'], 'private, no-store')

    def test_S4_ecritures_journalisees_avec_l_auteur(self):
        self.appel('dossier', {'s': 'nextcloud', 'p': '/Dossiers/DEMO', 'nom': 'Pièces', 'cle': self.cle()})
        row = self.desk.db.execute("SELECT data FROM audit WHERE action='fichiers56103_dossier_cree'").fetchone()
        self.assertIn('durand@example.test', row[0])

    def test_S5_aucune_lecture_par_un_modele_depuis_cette_page(self):
        source = (Path(F.__file__).read_text(encoding='utf-8') + Path(W.__file__).read_text(encoding='utf-8'))
        modules = set(re.findall(r'^\s*(?:from\s+\.?([\w.]*)\s+import\s+([\w, ]+)|import\s+([\w.]+))', source, re.M))
        noms = {x for t in modules for part in t for x in re.split(r'[\s,.]+', part) if x}
        for interdit in ('ai_gateway', 'ollama', 'ollama_client', 'missions567', 'chatgpt5662', 'chatgpt5697', 'claude5675', 'docrequest520', 'routage5689', 'model'):
            self.assertNotIn(interdit, noms, interdit)
        self.assertIn('pause5629', noms)                                                  # contrôle de la liste : les imports sont bien lus


# ------------------------------------------------------------------------------------------------ R05
class LectureSeule(Base):
    def compte(self):
        d = self.desk.db
        return tuple(d.execute(q).fetchone()[0] for q in ('SELECT COUNT(*) FROM settings', 'SELECT COUNT(*) FROM audit', 'SELECT COUNT(*) FROM fichiers56103_ops',
                                                          'SELECT COUNT(*) FROM fichiers56103_notes', 'SELECT COUNT(*) FROM fichiers56103_local_versions'))

    def test_R05_lectures_et_page_sans_aucune_ecriture(self):
        avant = self.compte()
        changements = self.desk.db.total_changes
        F.lister(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO', self.src())
        F.proprietes(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', True, self.src())
        F.versions(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', self.src())
        F.chercher(self.desk, {}, DURAND, 'nextcloud', '/Dossiers', 'bail', None, self.src())
        with patch('agent.fichiers5670.client', return_value=self.dav):
            W.page_html(self.desk, {}, DURAND, PREFIX)
        self.assertEqual(self.desk.db.total_changes, changements)
        self.assertEqual(self.compte(), avant)
        self.assertFalse([m for m, _ in self.nc.journal if m not in ('PROPFIND', 'GET')])


# ------------------------------------------------------------------------------------------------ F1 · volets, transferts
class Transferts(Base):
    def test_F1_copier_sans_ecraser_puis_deplacer_avec_etag(self):
        self.nc.mkdir('/Dossiers/DEMO/Envoi')
        out = F.transferer(self.desk, {}, DURAND, 'copier', 'nextcloud', '/Dossiers/DEMO/Bail.pdf', '', 'nextcloud', '/Dossiers/DEMO/Envoi', self.cle(), self.src())
        self.assertEqual(out['chemin'], '/Dossiers/DEMO/Envoi/Bail.pdf')
        self.assertEqual(self.nc.lire('/Dossiers/DEMO/Envoi/Bail.pdf'), b'%PDF bail commercial')
        self.nc.put('/Dossiers/DEMO/Envoi/Bail.pdf', b'%PDF version de l envoi')
        with self.assertRaises(Stop) as ex:                                              # nom pris : jamais d'écrasement
            F.transferer(self.desk, {}, DURAND, 'copier', 'nextcloud', '/Dossiers/DEMO/Bail.pdf', '', 'nextcloud', '/Dossiers/DEMO/Envoi', self.cle(), self.src())
        self.assertEqual(str(ex.exception), 'deja_existant')
        self.assertEqual(self.nc.lire('/Dossiers/DEMO/Envoi/Bail.pdf'), b'%PDF version de l envoi')
        etag = self.entree('/Dossiers/DEMO/Courrier.pdf')['etag']
        self.nc.put('/Dossiers/DEMO/Courrier.pdf', b'%PDF courrier modifie ailleurs')
        with self.assertRaises(Stop) as ex:                                              # ETag périmé : 409, rien n'est déplacé
            F.transferer(self.desk, {}, DURAND, 'deplacer', 'nextcloud', '/Dossiers/DEMO/Courrier.pdf', etag, 'nextcloud', '/Dossiers/DEMO/Envoi', self.cle(), self.src())
        self.assertEqual(str(ex.exception), 'version_perimee')
        self.assertIn('/Dossiers/DEMO/Courrier.pdf', self.nc.t)
        F.noter(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Courrier.pdf', 'vert', 'À joindre', 0, self.cle(), self.src())
        etag = self.entree('/Dossiers/DEMO/Courrier.pdf')['etag']
        F.transferer(self.desk, {}, DURAND, 'deplacer', 'nextcloud', '/Dossiers/DEMO/Courrier.pdf', etag, 'nextcloud', '/Dossiers/DEMO/Envoi', self.cle(), self.src())
        self.assertNotIn('/Dossiers/DEMO/Courrier.pdf', self.nc.t)
        liste = F.lister(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Envoi', self.src())
        self.assertEqual(liste['notes']['/Dossiers/DEMO/Envoi/Courrier.pdf']['note'], 'À joindre')   # la note suit le fichier

    def test_F1_dossier_dans_lui_meme_refuse_et_vers_un_dossier_reserve_404(self):
        with self.assertRaises(Stop) as ex:
            F.transferer(self.desk, {}, DURAND, 'deplacer', 'nextcloud', '/Dossiers/DEMO', '', 'nextcloud', '/Dossiers/DEMO', self.cle(), self.src())
        self.assertIn(str(ex.exception), ('racine_non_modifiable', 'destination_dans_la_source'))
        with self.assertRaises(Stop) as ex:
            F.transferer(self.desk, {}, DURAND, 'copier', 'nextcloud', '/Dossiers/DEMO/Bail.pdf', '', 'nextcloud', '/Dossiers/KERVILAC', self.cle(), self.src())
        self.assertEqual(str(ex.exception), 'introuvable')

    def test_F1_corbeille_nextcloud_conditionnelle(self):
        etag = self.entree('/Dossiers/DEMO/Bail.pdf')['etag']
        with self.assertRaises(Stop) as ex:
            F.corbeille(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', '"perime"', self.cle(), self.src())
        self.assertEqual(str(ex.exception), 'version_perimee')
        out = F.corbeille(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', etag, self.cle(), self.src())
        self.assertIn('corbeille', out['message'])
        self.assertEqual(self.nc.trash[-1][0], '/Dossiers/DEMO/Bail.pdf')                # dans la corbeille Nextcloud (DELETE If-Match)
        self.assertIn(('DELETE', BASE + FILES + '/Dossiers/DEMO/Bail.pdf'), self.nc.journal)


# ------------------------------------------------------------------------------------------------ F2 · recherche bornée
class Recherche(Base):
    def test_F2_recherche_bornee_reprenable_et_sans_dossier_reserve(self):
        chemin = '/Dossiers/DEMO'
        for i in range(9):                                                              # 11 niveaux sous /Dossiers (profondeur bornée à 12)
            chemin += '/N%02d' % i
            self.nc.mkdir(chemin)
        for i in range(30):
            self.nc.mkdir('/Dossiers/DEMO/Large%02d' % i)
        self.nc.put(chemin + '/Bail renouvele.pdf', b'%PDF')
        self.nc.put('/Dossiers/KERVILAC/Bail KERVILAC.pdf', b'%PDF')
        avant = len(self.nc.journal)
        un = F.chercher(self.desk, {}, DURAND, 'nextcloud', '/Dossiers', 'bail', None, self.src())
        self.assertLessEqual(un['dossiers_lus'], F.RECHERCHE_DOSSIERS)
        self.assertLessEqual(len(self.nc.journal) - avant, F.RECHERCHE_DOSSIERS + 2)         # jamais un parcours complet par appel
        self.assertTrue(un['partiel'] and un['curseur'])
        trouves = {x['chemin'] for x in un['resultats']}
        curseur = un['curseur']
        for _ in range(10):
            if not curseur:
                break
            suite = F.chercher(self.desk, {}, DURAND, 'nextcloud', '/Dossiers', 'bail', curseur, self.src())
            trouves |= {x['chemin'] for x in suite['resultats']}
            curseur = suite['curseur']
        self.assertIn(chemin + '/Bail renouvele.pdf', trouves)
        self.assertIn('/Dossiers/DEMO/Bail.pdf', trouves)
        self.assertFalse([t for t in trouves if 'KERVILAC' in t])
        faux = F.chercher(self.desk, {}, DURAND, 'nextcloud', '/Dossiers', 'bail', [['/Dossiers/KERVILAC', 0], ['/Autre', 0], ['/Dossiers/../x', 0]], self.src())
        self.assertEqual(faux['resultats'], [])                                          # curseur falsifié : chemins revalidés et écartés

    def test_F2_index_des_pieces_utilise_et_filtre_par_les_droits(self):
        db = sqlite3.connect(Path(self.desk.c['state_dir']) / 'documents.sqlite3')
        db.execute('CREATE TABLE IF NOT EXISTS docs (matter TEXT,path TEXT,etag TEXT,modified TEXT,text TEXT,error TEXT,PRIMARY KEY(matter,path))')
        db.executemany("INSERT INTO docs VALUES(?,?,'','','','')", [('DOS-001', '/Dossiers/DEMO/Archives/Bail 2019.pdf'), ('DOS-002', '/Dossiers/KERVILAC/Bail reserve.pdf')])
        db.commit()
        db.close()
        out = F.chercher(self.desk, {}, DURAND, 'nextcloud', '/Dossiers', 'bail', None, self.src())
        self.assertTrue(out['index'])
        chemins = [x['chemin'] for x in out['resultats']]
        self.assertIn('/Dossiers/DEMO/Archives/Bail 2019.pdf', chemins)
        self.assertNotIn('/Dossiers/KERVILAC/Bail reserve.pdf', chemins)
        with self.assertRaises(Stop):
            F.chercher(self.desk, {}, DURAND, 'nextcloud', '/Dossiers', 'b', None, self.src())


# ------------------------------------------------------------------------------------------------ F3 · renommage par lot
class Renommage(Base):
    def elements(self, *noms):
        return [{'chemin': '/Dossiers/DEMO/' + n, 'etag': self.entree('/Dossiers/DEMO/' + n)['etag']} for n in noms]

    def test_F3_numerotation_des_pieces_dans_l_ordre_choisi_avec_zeros(self):
        regle = {'mode': 'pieces', 'debut': 1, 'zeros': 2, 'garder_nom': True}
        elements = self.elements('Courrier.pdf', 'Assignation.pdf', 'Bail.pdf')
        apercu = F.apercu_renommage(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO', elements, regle, self.src())
        self.assertEqual([l['apres'] for l in apercu['lignes']], ['Pièce n° 01 – Courrier.pdf', 'Pièce n° 02 – Assignation.pdf', 'Pièce n° 03 – Bail.pdf'])
        self.assertTrue(apercu['ok'])
        self.assertFalse([m for m, _ in self.nc.journal if m == 'MOVE'])                    # l'aperçu n'écrit rien
        out = F.renommer_lot(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO', elements, regle, self.cle(), self.src())
        self.assertTrue(out['ok'])
        self.assertIn('/Dossiers/DEMO/Pièce n° 02 – Assignation.pdf', self.nc.t)
        sans_zero = F._nouveau_nom('Pièce n° 02 – Assignation.pdf', {'mode': 'pieces', 'debut': 9, 'zeros': 0, 'garder_nom': True}, 1)
        self.assertEqual(sans_zero, 'Pièce n° 10 – Assignation.pdf')                         # renuméroter ne double pas le préfixe

    def test_F3_conflits_nom_pris_doublon_et_nom_invalide_rien_n_est_renomme(self):
        self.nc.put('/Dossiers/DEMO/Acte.pdf', b'a')
        self.nc.put('/Dossiers/DEMO/acte.PDF', b'b')
        lignes = F.apercu_renommage(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO', self.elements('Acte.pdf', 'acte.PDF'),
                                    {'mode': 'ajouter', 'prefixe': 'X '}, self.src())['lignes']
        self.assertEqual([l['conflit'] for l in lignes], ['', 'doublon_lot'])
        pris = F.apercu_renommage(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO', self.elements('Bail.pdf'),
                                  {'mode': 'remplacer', 'cherche': 'Bail', 'remplace': 'Assignation'}, self.src())
        self.assertEqual(pris['lignes'][0]['conflit'], 'nom_pris')
        invalide = F.apercu_renommage(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO', self.elements('Bail.pdf'),
                                      {'mode': 'remplacer', 'cherche': 'Bail', 'remplace': '../Bail'}, self.src())
        self.assertEqual(invalide['lignes'][0]['conflit'], 'nom_invalide')
        r = self.appel('renommer', {'s': 'nextcloud', 'p': '/Dossiers/DEMO', 'elements': self.elements('Bail.pdf', 'Courrier.pdf'),
                                    'regle': {'mode': 'remplacer', 'cherche': 'Bail', 'remplace': 'Assignation'}, 'cle': self.cle()})
        self.assertEqual((r['status'], r['json']['error']), ('409 Conflict', 'conflits_renommage'))
        self.assertEqual(r['json']['lignes'][0]['conflit'], 'nom_pris')
        self.assertFalse([m for m, _ in self.nc.journal if m == 'MOVE'])                    # au moindre conflit, rien n'est renommé
        self.assertEqual(self.nc.lire('/Dossiers/DEMO/Assignation.pdf'), b'%PDF assignation DURAND')

    def test_F3_element_hors_du_dossier_ou_reserve_refuse(self):
        with self.assertRaises(Stop) as ex:
            F.apercu_renommage(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO', [{'chemin': '/Dossiers/KERVILAC/Secret DURAND.pdf'}],
                               {'mode': 'ajouter', 'prefixe': 'A '}, self.src())
        self.assertEqual(str(ex.exception), 'introuvable')

    def test_F3_version_perimee_en_cours_de_lot_arret_et_compte_rendu(self):
        elements = self.elements('Assignation.pdf', 'Bail.pdf')
        self.nc.put('/Dossiers/DEMO/Bail.pdf', b'%PDF bail modifie')
        out = F.renommer_lot(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO', elements, {'mode': 'ajouter', 'suffixe': ' (copie client)'}, self.cle(), self.src())
        self.assertFalse(out['ok'])
        self.assertEqual(len(out['faits']), 1)
        self.assertEqual(out['echec']['raison'], 'version_perimee')
        self.assertIn('/Dossiers/DEMO/Bail.pdf', self.nc.t)


# ------------------------------------------------------------------------------------------------ F4 · notes et couleurs
class Notes(Base):
    def test_F4_note_et_couleur_en_base_versionnees_jamais_dans_le_fichier(self):
        out = F.noter(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', 'orange', 'Vérifier la clause de révision', 0, self.cle(), self.src())
        self.assertEqual(out['version'], 1)
        self.assertEqual(self.nc.lire('/Dossiers/DEMO/Bail.pdf'), b'%PDF bail commercial')
        self.assertFalse([m for m, _ in self.nc.journal if m in ('PUT', 'PROPPATCH')])
        with self.assertRaises(Stop) as ex:                                              # note changée entre-temps : 409, rien d'écrasé
            F.noter(self.desk, {}, ASSISTANT, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', 'rouge', 'Autre', 0, self.cle(), self.src())
        self.assertEqual(str(ex.exception), 'version_perimee')
        vu = F.lister(self.desk, {}, ASSISTANT, 'nextcloud', '/Dossiers/DEMO', self.src())['notes']['/Dossiers/DEMO/Bail.pdf']
        self.assertEqual((vu['couleur'], vu['note'], vu['auteur']), ('orange', 'Vérifier la clause de révision', 'durand@example.test'))   # partagée au cabinet
        with self.assertRaises(Stop):
            F.noter(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', 'fuchsia', '', 1, self.cle(), self.src())
        F.noter(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', '', '', 1, self.cle(), self.src())
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM fichiers56103_notes').fetchone()[0], 0)

    def test_F4_favoris_par_compte_filtres_par_les_droits_actuels(self):
        F.favori(self.desk, {}, ADMIN, 'ajouter', 'nextcloud', '/Dossiers/KERVILAC', self.cle(), self.src())
        F.favori(self.desk, {}, DURAND, 'ajouter', 'nextcloud', '/Dossiers/DEMO', self.cle(), self.src())
        self.assertEqual([f['chemin'] for f in F.favoris(self.desk, {}, DURAND, self.src())], ['/Dossiers/DEMO'])
        self.assertEqual([f['chemin'] for f in F.favoris(self.desk, {}, ADMIN, self.src())], ['/Dossiers/KERVILAC'])
        membres5650.set_reserve(self.desk.c['state_dir'], 'DOS-001', ['martin@example.test'])
        self.assertEqual(F.favoris(self.desk, {}, DURAND, self.src()), [])                  # dossier devenu réservé : disparu des favoris


# ------------------------------------------------------------------------------------------------ F5 · propriétés
class Proprietes(Base):
    def test_F5_proprietes_et_empreinte_a_la_demande_seulement(self):
        p = F.proprietes(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', False, self.src())
        self.assertEqual((p['taille'], p['auteur'], p['type']), (len(b'%PDF bail commercial'), 'DURAND Claire', 'application/pdf'))
        self.assertTrue(p['modifie'].startswith('20'))
        self.assertNotIn('sha256', p)
        self.assertFalse([m for m, _ in self.nc.journal if m == 'GET'])                     # aucune lecture du contenu sans demande
        p = F.proprietes(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', True, self.src())
        self.assertEqual(p['sha256'], hashlib.sha256(b'%PDF bail commercial').hexdigest())


# ------------------------------------------------------------------------------------------------ F6 · ZIP
class Zip(Base):
    def test_F6_creer_puis_extraire_une_archive(self):
        self.nc.mkdir('/Dossiers/DEMO/Annexes')
        self.nc.put('/Dossiers/DEMO/Annexes/Plan.pdf', b'%PDF plan Valbrune')
        out = F.creer_zip(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO', ['/Dossiers/DEMO/Bail.pdf', '/Dossiers/DEMO/Annexes'], 'Envoi confrère',
                          self.cle(), self.src())
        self.assertEqual(out['chemin'], '/Dossiers/DEMO/Envoi confrère.zip')
        noms = zipfile.ZipFile(io.BytesIO(self.nc.lire(out['chemin']))).namelist()
        self.assertEqual(sorted(noms), ['Annexes/', 'Annexes/Plan.pdf', 'Bail.pdf'])
        with self.assertRaises(Stop) as ex:                                              # nom pris : jamais d'écrasement
            F.creer_zip(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO', ['/Dossiers/DEMO/Bail.pdf'], 'Envoi confrère', self.cle(), self.src())
        self.assertEqual(str(ex.exception), 'deja_existant')
        ext = F.extraire_zip(self.desk, {}, DURAND, 'nextcloud', out['chemin'], self.cle(), self.src())
        self.assertTrue(ext['ok'], ext)
        self.assertEqual(ext['chemin'], '/Dossiers/DEMO/Envoi confrère')
        self.assertEqual(self.nc.lire('/Dossiers/DEMO/Envoi confrère/Annexes/Plan.pdf'), b'%PDF plan Valbrune')
        ext2 = F.extraire_zip(self.desk, {}, DURAND, 'nextcloud', out['chemin'], self.cle(), self.src())
        self.assertEqual(ext2['chemin'], '/Dossiers/DEMO/Envoi confrère (2)')               # dossier existant : nouveau dossier voisin

    def test_F6_archives_malveillantes_refusees_sans_rien_ecrire(self):
        lien = zipfile.ZipInfo('lien')
        lien.external_attr = (0o120777 << 16)
        chiffre = zipfile.ZipInfo('chiffre.txt')
        chiffre.flag_bits |= 0x1
        cas = {
            'chemin_sortant': [('../evil.txt', b'x')],
            'chemin_sortant ': [('/absolu.txt', b'x')],
            'chemin_sortant  ': [('C:/Windows/evil.txt', b'x')],
            'chemin_sortant   ': [('a\\..\\..\\evil.txt', b'x')],
            'lien_symbolique': [(lien, b'/etc/passwd')],
            'doublon': [('A.txt', b'1'), ('a.TXT', b'2')],
            'taux_de_compression_suspect': [('bombe.bin', b'\0' * 3_000_000)],
            'nom_interdit': [('dossier/fi:chier.txt', b'x')],
        }
        for raison, entrees in cas.items():
            self.nc.put('/Dossiers/DEMO/Archive.zip', zip_de(entrees))
            etat = set(self.nc.t)
            with self.assertRaises(Stop) as ex:
                F.analyser_zip(self.nc.lire('/Dossiers/DEMO/Archive.zip'))
            self.assertEqual(str(ex.exception), 'archive_refusee:' + raison.strip(), raison)
            r = self.appel('extraire', {'s': 'nextcloud', 'p': '/Dossiers/DEMO/Archive.zip', 'cle': self.cle()})
            self.assertEqual(r['status'], '422 Unprocessable Entity', raison)
            self.assertIn('Archive refusée, rien n’a été extrait', r['json']['message'])
            self.assertEqual(set(self.nc.t), etat, raison)                                # rien n'est écrit
        raw = bytearray(zip_de([('chiffre.txt', b'secret')]))
        with patch('zipfile.ZipFile.infolist', lambda z: [chiffre]):
            with self.assertRaises(Stop) as ex:
                F.analyser_zip(bytes(raw))
        self.assertEqual(str(ex.exception), 'archive_refusee:archive_chiffree')
        with patch.object(F, 'ZIP_ENTREES_MAX', 3):
            with self.assertRaises(Stop) as ex:
                F.analyser_zip(zip_de([('%d.txt' % i, b'x') for i in range(4)]))
        self.assertEqual(str(ex.exception), 'archive_refusee:trop_d_entrees')
        with patch.object(F, 'ZIP_TOTAL_MAX', 10):
            with self.assertRaises(Stop) as ex:
                F.analyser_zip(zip_de([('gros.txt', b'x' * 11)]))
        self.assertEqual(str(ex.exception), 'archive_refusee:trop_volumineuse_decompressee')

    def test_F6_mutation_sans_controle_du_taux_la_bombe_passerait(self):
        bombe = zip_de([('bombe.bin', b'\0' * 3_000_000)])
        with patch.object(F, 'ZIP_RATIO_MAX', 10 ** 9):
            _, membres = F.analyser_zip(bombe)
        self.assertEqual(len(membres), 1)                                               # c'est bien le contrôle du taux qui refuse


# ------------------------------------------------------------------------------------------------ F7 · versions Nextcloud, verrou
class Versions(Base):
    def setUp(self):
        super().setUp()
        self.nc.put('/Dossiers/DEMO/Bail.pdf', b'%PDF bail v2', 'MARTIN Paul')
        self.nc.put('/Dossiers/DEMO/Bail.pdf', b'%PDF bail v3', 'BERNARD Lea')

    def test_F7_liste_des_versions_date_heure_auteur(self):
        v = F.versions(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', self.src())
        self.assertTrue(v['disponible'])
        self.assertEqual([x['auteur'] for x in v['versions']], ['MARTIN Paul', 'DURAND Claire'])   # la plus récente d'abord
        self.assertTrue(all(re.match(r'\d{4}-\d\d-\d\dT\d\d:\d\d', x['date']) for x in v['versions']))
        self.assertEqual(v['actuel']['auteur'], 'BERNARD Lea')

    def test_F7_restauration_conditionnelle_et_copie_de_conflit(self):
        v = F.versions(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', self.src())
        ancienne = v['versions'][-1]['id']
        etag = v['actuel']['etag']
        self.nc.put('/Dossiers/DEMO/Bail.pdf', b'%PDF bail v4 par un confrere', 'MARTIN Paul')    # modifié après l'affichage
        out = F.restaurer(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', ancienne, etag, self.cle(), self.src())
        self.assertTrue(out['conflit'])
        self.assertEqual(self.nc.lire('/Dossiers/DEMO/Bail.pdf'), b'%PDF bail v4 par un confrere')   # jamais écrasé
        self.assertEqual(self.nc.lire(out['copie']), b'%PDF bail commercial')
        self.assertIn('copie de conflit', out['copie'])
        self.assertIn('n’a pas été remplacé', out['message'])
        etag = F.versions(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', self.src())['actuel']['etag']
        ok = F.restaurer(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', ancienne, etag, self.cle(), self.src())
        self.assertTrue(ok['ok'])
        self.assertEqual(self.nc.lire('/Dossiers/DEMO/Bail.pdf'), b'%PDF bail commercial')
        puts = [u for m, u in self.nc.journal if m == 'PUT']
        self.assertTrue(puts)

    def test_F7_mutation_sans_if_match_la_version_recente_serait_ecrasee(self):
        v = F.versions(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', self.src())
        etag = v['actuel']['etag']
        self.nc.put('/Dossiers/DEMO/Bail.pdf', b'%PDF bail v4 recent')
        def sans_if_match(source, chemin, octets, etag=None):          # mutation en mémoire : écriture sans condition sur l'ETag
            source.dav.http.request('PUT', source.dav.file_url(chemin), bytes(octets), {}, 100)
            return ''
        with patch.object(F.SourceNextcloud, 'ecrire', sans_if_match):
            F.restaurer(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', v['versions'][-1]['id'], etag, self.cle(), self.src())
        self.assertEqual(self.nc.lire('/Dossiers/DEMO/Bail.pdf'), b'%PDF bail commercial')      # sans le garde-fou : écrasée

    def test_F7_verrou_refuse_dit_clairement_puis_verrou_accepte(self):
        out = F.verrouiller(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', 'verrouiller', self.cle(), self.src())
        self.assertFalse(out['ok'])
        self.assertIn('n’accepte pas le verrou', out['message'])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM fichiers56103_verrous').fetchone()[0], 0)
        self.nc.lock_mode = 'accepte'
        out = F.verrouiller(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', 'verrouiller', self.cle(), self.src())
        self.assertTrue(out['ok'])
        self.assertIn('/Dossiers/DEMO/Bail.pdf', F.lister(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO', self.src())['verrous'])
        out = F.verrouiller(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', 'deverrouiller', self.cle(), self.src())
        self.assertTrue(out['ok'])
        self.assertEqual(self.nc.locks, {})


# ------------------------------------------------------------------------------------------------ F8 · disque local (poste)
class Local(Base):
    POSTE = {'poste': True}

    def setUp(self):
        super().setUp()
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.racine = Path(self.t.name) / 'Travail'
        self.racine.mkdir()
        (self.racine / 'Contrats').mkdir()
        self.fichier = self.racine / 'Contrats' / 'Bail ZORVAL.docx'
        self.fichier.write_bytes(b'PK version 1')
        self.passe(self.fichier)
        F.regler_local(self.desk, self.POSTE, DURAND, [{'chemin': str(self.racine), 'nom': 'Travail', 'surveille': True}],
                       {'plafond_mo': 100, 'jours': 30, 'intervalle': 60}, self.cle())
        self.l = F.SourceLocale(self.desk)
        self.ident = F.dossiers_locaux(self.desk)[0]['id']

    @staticmethod
    def passe(chemin, secondes=30):
        t = time.time() - secondes
        os.utime(chemin, (t, t))

    def toutes(self):
        return {**self.src(), 'local': self.l}

    def logique(self, rel=''):
        return '/' + self.ident + ('/' + rel if rel else '')

    def versions(self):
        return self.desk.db.execute('SELECT COUNT(*) FROM fichiers56103_local_versions').fetchone()[0]

    def test_F8_disque_local_seulement_en_mode_poste(self):
        self.assertNotIn('local', F.sources(self.desk, {}, DURAND))
        self.assertIn('local', F.sources(self.desk, self.POSTE, DURAND))
        with self.assertRaises(Stop) as ex:
            F.regler_local(self.desk, {}, DURAND, [], {}, self.cle())
        self.assertEqual(str(ex.exception), 'introuvable')
        with self.assertRaises(Stop) as ex:
            F.regler_local(self.desk, self.POSTE, ASSISTANT, [], {}, self.cle())
        self.assertEqual(str(ex.exception), 'role_insuffisant')
        r = self.appel('poste', {'dossiers': [], 'reglages': {}, 'cle': self.cle()}, acteur=ASSISTANT, auth={'origin': self.origin, 'csrf': 'test-csrf', 'poste': True})
        self.assertEqual(r['status'], '403 Forbidden')
        liste = F.lister(self.desk, self.POSTE, DURAND, 'local', self.logique('Contrats'), self.toutes())
        self.assertEqual([x['nom'] for x in liste['entrees']], ['Bail ZORVAL.docx'])

    def test_F8_chemins_sortants_et_liens_refuses_sur_le_disque(self):
        dehors = Path(self.t.name) / 'Dehors'
        dehors.mkdir()
        (dehors / 'prive.txt').write_text('confidentiel', encoding='utf-8')
        for p in (self.logique('..'), self.logique('Contrats/../../Dehors'), '/' + str(dehors).replace('\\', '/'), '/p999', self.logique('C:'), '/' + self.ident + '/..%2fDehors'):
            with self.assertRaises(Stop, msg=p) as ex:
                F.lister(self.desk, self.POSTE, DURAND, 'local', p, self.toutes())
            self.assertEqual(str(ex.exception), 'introuvable', p)
        try:
            os.symlink(str(dehors), str(self.racine / 'raccourci'), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('liens symboliques non autorisés sur ce poste')
        noms = [x['nom'] for x in F.lister(self.desk, self.POSTE, DURAND, 'local', self.logique(), self.toutes())['entrees']]
        self.assertNotIn('raccourci', noms)                                              # lien sortant : jamais listé
        with self.assertRaises(Stop):
            F.lister(self.desk, self.POSTE, DURAND, 'local', self.logique('raccourci'), self.toutes())
        with self.assertRaises(Stop):
            F.proprietes(self.desk, self.POSTE, DURAND, 'local', self.logique('raccourci/prive.txt'), True, self.toutes())

    def test_F8_une_modification_une_version_word_et_temporaires(self):
        maintenant = time.time()
        F.scruter(self.desk, maintenant)
        self.assertEqual(self.versions(), 1)                                             # premier relevé
        F.scruter(self.desk, maintenant + 60)
        self.assertEqual(self.versions(), 1)                                             # rien n'a changé : aucune version
        (self.racine / 'Contrats' / '~$il ZORVAL.docx').write_bytes(b'verrou de Word')
        (self.racine / 'Contrats' / '~WRL0005.tmp').write_bytes(b'temporaire de Word')
        self.fichier.write_bytes(b'PK version 2 enregistree par Word')
        self.passe(self.fichier, 10)
        bilan = F.scruter(self.desk, time.time())
        self.assertEqual((self.versions(), bilan['versions']), (2, 1))                  # une modification : une version, temporaires ignorés
        lignes = self.desk.db.execute('SELECT chemin, origine FROM fichiers56103_local_versions ORDER BY id').fetchall()
        self.assertEqual([tuple(x) for x in lignes], [('Contrats/Bail ZORVAL.docx', 'premiere'), ('Contrats/Bail ZORVAL.docx', 'changement')])
        self.fichier.write_bytes(b'PK version 3 en cours')                              # encore en cours d'écriture : repris plus tard
        bilan = F.scruter(self.desk, time.time())
        self.assertEqual((bilan['en_cours'], self.versions()), (1, 2))
        F.scruter(self.desk, time.time() + 10)
        self.assertEqual(self.versions(), 3)

    def test_F8_plafond_et_duree_appliques_la_plus_recente_gardee(self):
        maintenant = time.time()
        for i in range(4):
            self.fichier.write_bytes(bytes([65 + i]) * 400_000)
            self.passe(self.fichier, 100 - i)
            F.scruter(self.desk, maintenant + i)
        self.assertEqual(self.versions(), 4)                                             # premier relevé + 3 changements
        reserve = Path(self.desk.c['state_dir']) / 'fichiers56103' / 'versions'
        with patch.object(F, 'reglages_locaux', return_value={'plafond_mo': 1, 'jours': 30, 'intervalle': 60}):
            bilan = F.retention(self.desk, maintenant)
        self.assertGreater(bilan['retirees_plafond'], 0)
        total = self.desk.db.execute('SELECT SUM(taille) FROM (SELECT sha256, MAX(taille) AS taille FROM fichiers56103_local_versions GROUP BY sha256)').fetchone()[0]
        self.assertLessEqual(total, 1_000_000)
        dernier = self.desk.db.execute('SELECT sha256 FROM fichiers56103_local_versions ORDER BY id DESC LIMIT 1').fetchone()[0]
        self.assertEqual(dernier, hashlib.sha256(bytes([68]) * 400_000).hexdigest())    # la plus récente est gardée
        copies = {p.name for p in reserve.rglob('*') if p.is_file() and len(p.name) == 64}
        utiles = {r[0] for r in self.desk.db.execute('SELECT sha256 FROM fichiers56103_local_versions')}
        self.assertEqual(copies, utiles)                                                 # copies orphelines effacées
        with patch.object(F, 'reglages_locaux', return_value={'plafond_mo': 100, 'jours': 1, 'intervalle': 60}):
            bilan = F.retention(self.desk, maintenant + 3 * 86400)
        self.assertEqual(self.versions(), 1)
        self.assertGreaterEqual(bilan['expirees'], 1)

    def test_F8_mutation_sans_plafond_rien_ne_serait_retire(self):
        maintenant = time.time()
        for i in range(3):
            self.fichier.write_bytes(bytes([70 + i]) * 400_000)
            self.passe(self.fichier, 100 - i)
            F.scruter(self.desk, maintenant + i)
        with patch.object(F, 'reglages_locaux', return_value={'plafond_mo': 10 ** 6, 'jours': 30, 'intervalle': 60}):
            self.assertEqual(F.retention(self.desk, maintenant)['retirees_plafond'], 0)

    def test_F8_restauration_locale_et_copie_de_conflit(self):
        F.scruter(self.desk, time.time())
        self.fichier.write_bytes(b'PK version 2')
        self.passe(self.fichier, 5)
        F.scruter(self.desk, time.time())
        chemin = self.logique('Contrats/Bail ZORVAL.docx')
        v = F.versions(self.desk, self.POSTE, DURAND, 'local', chemin, self.toutes())
        premiere = v['versions'][-1]['id']
        out = F.restaurer(self.desk, self.POSTE, DURAND, 'local', chemin, premiere, v['actuel']['etag'], self.cle(), self.toutes())
        self.assertTrue(out['ok'], out)
        self.assertEqual(self.fichier.read_bytes(), b'PK version 1')
        origines = [r[0] for r in self.desk.db.execute('SELECT origine FROM fichiers56103_local_versions ORDER BY id')]
        self.assertEqual(origines[-1], 'avant_restauration')                             # le contenu remplacé est gardé
        etag = F.versions(self.desk, self.POSTE, DURAND, 'local', chemin, self.toutes())['actuel']['etag']
        self.fichier.write_bytes(b'PK modifie dans Word entre-temps')
        out = F.restaurer(self.desk, self.POSTE, DURAND, 'local', chemin, premiere, etag, self.cle(), self.toutes())
        self.assertTrue(out['conflit'])
        self.assertEqual(self.fichier.read_bytes(), b'PK modifie dans Word entre-temps')
        copie = [p for p in (self.racine / 'Contrats').iterdir() if 'copie de conflit' in p.name]
        self.assertEqual(len(copie), 1)
        self.assertEqual(copie[0].read_bytes(), b'PK version 1')

    def test_F8_copie_entre_nextcloud_et_le_poste_mais_pas_de_deplacement(self):
        out = F.transferer(self.desk, self.POSTE, DURAND, 'copier', 'nextcloud', '/Dossiers/DEMO/Bail.pdf', '', 'local', self.logique('Contrats'), self.cle(), self.toutes())
        self.assertEqual((self.racine / 'Contrats' / 'Bail.pdf').read_bytes(), b'%PDF bail commercial')
        self.assertTrue(out['ok'])
        with self.assertRaises(Stop) as ex:
            F.transferer(self.desk, self.POSTE, DURAND, 'deplacer', 'local', self.logique('Contrats/Bail ZORVAL.docx'), '', 'nextcloud', '/Dossiers/DEMO',
                         self.cle(), self.toutes())
        self.assertEqual(str(ex.exception), 'deplacement_entre_sources')
        self.assertTrue(self.fichier.exists())

    def test_F8_extraction_locale_reste_dans_le_dossier(self):
        (self.racine / 'Lot.zip').write_bytes(zip_de([('Pieces/Piece 1.pdf', b'%PDF 1'), ('Note.txt', b'DOS-001')]))
        out = F.extraire_zip(self.desk, self.POSTE, DURAND, 'local', self.logique('Lot.zip'), self.cle(), self.toutes())
        self.assertTrue(out['ok'], out)
        self.assertEqual((self.racine / 'Lot' / 'Pieces' / 'Piece 1.pdf').read_bytes(), b'%PDF 1')
        (self.racine / 'Mal.zip').write_bytes(zip_de([('../../echappe.txt', b'x')]))
        with self.assertRaises(Stop):
            F.extraire_zip(self.desk, self.POSTE, DURAND, 'local', self.logique('Mal.zip'), self.cle(), self.toutes())
        self.assertFalse((Path(self.t.name) / 'echappe.txt').exists())
        self.assertFalse((self.racine / 'Mal').exists())

    def test_F8_maintenance_respecte_l_intervalle(self):
        stamp = time.time()
        self.assertIsNotNone(F.maintenance(self.desk, stamp))
        self.assertIsNone(F.maintenance(self.desk, stamp + 10))                             # intervalle de 60 s non écoulé
        self.assertIsNotNone(F.maintenance(self.desk, stamp + 61))


# ------------------------------------------------------------------------------------------------ W · page, routes, registre
class Page(Base):
    def requete(self, path, query=''):
        env = {'REQUEST_METHOD': 'GET', 'PATH_INFO': path, 'QUERY_STRING': query, 'HTTP_HOST': 'cabinet.example.test', 'HTTP_X_FORWARDED_PROTO': 'https',
               'wsgi.url_scheme': 'http', 'wsgi.input': io.BytesIO(b''), 'CONTENT_LENGTH': '0', 'CONTENT_TYPE': 'text/plain',
               'HTTP_AUTHORIZATION': 'Basic ' + base64.b64encode(('admin:' + self.password).encode()).decode()}
        out = {}

        def start(status, hdrs):
            out.update(status=status, headers=dict(hdrs))
        with patch('agent.fichiers5670.client', return_value=self.dav):
            out['body'] = b''.join(self.app(env, start)).decode()
        return out

    def test_W1_page_deux_volets_onglets_clavier_famille_et_no_store(self):
        r = self.requete('/fichiers')
        self.assertTrue(r['status'].startswith('200'), r['status'])
        corps = r['body']
        self.assertEqual(r['headers'].get('Cache-Control'), 'private, no-store')
        self.assertEqual(corps.count('class="f103-volet"'), 2)
        self.assertIn('data-f103-bascule="1"', corps)                                    # volets en onglets à 390 px
        for libelle in ('Renommer par lot', 'Chercher', 'Versions', 'Propriétés', 'Créer une archive ZIP', 'Voir l’aperçu', 'Mettre à la corbeille'):
            self.assertIn(libelle, corps)
        self.assertIn('/static/v56103-fichiers.js', corps)
        self.assertIn('data-f101-famille="pieces_contentieux"', corps)
        self.assertNotIn('Secret DURAND', corps)
        self.assertNotRegex(corps, r'<script>(?!\s*</script>)[^<]')                         # aucun script en ligne ajouté par la page
        js = self.requete('/static/v56103-fichiers.js')
        self.assertTrue(js['status'].startswith('200'))
        self.assertNotIn('innerHTML', js['body'])

    def test_W1_sans_stockage_la_page_le_dit_et_garde_ses_boutons(self):
        self.desk.c['nextcloud'] = {'roots': ['/Dossiers']}
        html = W.page_html(self.desk, {}, DURAND, PREFIX)
        self.assertIn('Aucun stockage de fichiers n’est configuré', html)
        self.assertIn('Renommer par lot', html)
        self.assertIn('&quot;sources&quot;: []', html)

    def test_W1_interrupteur_coupe_un_seul_volet(self):
        from agent import interface56100
        interface56100.regler_compte(self.desk, DURAND[0], False)
        with patch('agent.fichiers5670.client', return_value=self.dav):
            html = W.page_html(self.desk, {}, DURAND, PREFIX)
        self.assertIn('f103 f103-simple', html)
        self.assertEqual(html.count('class="f103-volet"'), 1)

    def test_W2_routes_liste_fermee_roles_csrf_et_taille(self):
        from agent.standalone_auth import allowed
        self.assertTrue(allowed('assistant', 'POST', '/api440/m56103/fichiers/renommer'))
        self.assertTrue(allowed('avocat', 'POST', '/api440/m56103/fichiers/poste'))
        self.assertFalse(allowed('assistant', 'POST', '/api440/m56103/fichiers/poste'))
        self.assertFalse(allowed('avocat', 'POST', '/api440/m56103/fichiers/inconnue'))
        r = self.appel('inconnue', {})
        self.assertEqual(r['status'], '404 Not Found')
        r = self.appel('dossier', {'s': 'nextcloud', 'p': '/Dossiers/DEMO', 'nom': 'X', 'cle': self.cle()}, csrf='mauvais')
        self.assertEqual(r['status'], '403 Forbidden')
        self.assertNotIn('/Dossiers/DEMO/X', self.nc.t)
        r = self.appel('note', {'s': 'nextcloud', 'p': '/Dossiers/DEMO/Bail.pdf', 'note': 'x' * 70_000, 'cle': self.cle()})
        self.assertEqual(r['status'], '413 Payload Too Large')
        r = self.appel('lister', methode='GET', args={'s': 'nextcloud', 'p': '/Dossiers/DEMO'})
        self.assertEqual([x['nom'] for x in r['json']['entrees']], ['Assignation.pdf', 'Bail.pdf', 'Courrier.pdf'])
        self.assertEqual(r['entetes']['Cache-Control'], 'private, no-store')

    def test_W2_stockage_injoignable_est_un_etat_jamais_un_dossier_vide(self):
        with patch.object(FakeNC, 'request', side_effect=Stop('connexion_http_indisponible')):
            r = self.appel('lister', methode='GET', args={'s': 'nextcloud', 'p': '/Dossiers/DEMO'})
        self.assertEqual((r['status'], r['json']['error']), ('503 Service Unavailable', 'stockage_injoignable'))
        self.assertNotIn('entrees', r['json'])

    def test_W3_registre_famille_et_ctrl_k(self):
        from agent import familles56101 as fam, fonctions5631 as reg, palette5688
        self.assertEqual(reg.FAMILLE['fichiers'], 'pieces_contentieux')
        self.assertEqual(reg.INDEX['fichiers'][2], '/fichiers')
        self.assertIn('fichiers', fam.membres('pieces_contentieux'))
        res = palette5688.search(self.desk, 'explorateur')
        self.assertIn('/fichiers', [r['url'] for g in res['groupes'] if g['id'] == 'fonctions' for r in g['resultats']])

    def test_W4_source_ajoutee_par_un_autre_lot_suit_les_memes_garde_fous(self):
        class Fausse(F.Source):
            ident, libelle = 'essai', 'Source d’essai'

            def racines(self):
                return [{'chemin': '/Bibliotheque', 'nom': 'Bibliothèque'}]

            def lister(self, chemin):
                return [F.entree('/Bibliotheque/Modele.docx', False, 10)]
        with patch.dict(F.SOURCES, {}):
            F.inscrire_source('essai', 'Source d’essai', lambda desk, auth, acteur: Fausse())
            toutes = F.sources(self.desk, {}, DURAND)
            self.assertIn('essai', toutes)
            self.assertEqual(F.lister(self.desk, {}, DURAND, 'essai', '/Bibliotheque', toutes)['entrees'][0]['nom'], 'Modele.docx')
            with self.assertRaises(Stop):
                F.lister(self.desk, {}, DURAND, 'essai', '/Autre', toutes)
            with self.assertRaises(Stop) as ex:
                Fausse().ecrire('/Bibliotheque/x', b'')
            self.assertEqual(str(ex.exception), 'non_pris_en_charge')
        with self.assertRaises(ValueError):
            F.inscrire_source('nextcloud', 'x', lambda *a: None)


if __name__ == '__main__':
    unittest.main()
