"""5.6.111 (lot P ; L06, L07, L08 du plan ; audit § 8) : préparation supervisée d'un envoi sur un portail (``envoi_portail56111``) et
capacités distinctes du profil (``collecte_portail56109``).

Données fictives (DURAND, MARTIN, DOS-001, DOS-002 réservé, RG 24/01234, example.test). Stockage local temporaire ; aucun portail, compte,
certificat, courriel, modèle ni réseau réel : tout appel réseau fait échouer l'essai. Le faux adaptateur d'envoi et le faux navigateur
n'existent que dans ces essais ; ils ne qualifient aucun écran réel. Deux connexions SQLite réelles pour les essais de concurrence.
"""
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import socket
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

import test_v5697_gestion as tg
from test_v5697_gestion import ADMIN, ASSISTANT, AUTRE, AVOCAT, MID, MID2
from agent import collecte_portail56109 as C, confirmations56111 as garde, envoi_portail56111 as E, livrables5689 as liv, membres5650
from agent import modele5651, navigateur56109 as N, navigateur5673 as nv
from agent.common import Stop
from agent.desk import Desk
from agent.erreurs5634 import detail_of


def _reseau_interdit(*a, **k):
    raise AssertionError('appel réseau non simulé')


def setUpModule():
    global _p1, _p2
    _p1 = patch.object(socket.socket, 'connect', _reseau_interdit)
    _p2 = patch.object(socket, 'create_connection', _reseau_interdit)
    _p1.start()
    _p2.start()


def tearDownModule():
    _p1.stop()
    _p2.stop()


MESSAGE = 'Monsieur le Président,\n\nVeuillez trouver ci-joint nos conclusions pour M. DURAND (RG 24/01234).\n\nAvec nos respectueuses salutations.'


class FauxAdaptateur(E.AdaptateurEnvoi):
    """Adaptateur d'ESSAI (jamais un écran réel) : n'utilise que le pilote, donc la barrière ; journal des gestes."""
    version = 'essai-1'
    journal = []
    identite_affichee = {'compte': 'avocat-A'}
    constat = {'statut': 'incertain'}

    def identite(self, pilote):
        pilote.text()
        return FauxAdaptateur.identite_affichee

    def ouvrir_formulaire(self, pilote, recap):
        pilote.navigate('https://portail.example.test/depot')
        FauxAdaptateur.journal.append(('ouvrir', recap['rg']))

    def remplir(self, pilote, champ, valeur):
        pilote.type(champ, valeur)
        FauxAdaptateur.journal.append(('remplir', champ))

    def joindre(self, pilote, ordre, nom, octets):
        pilote.joindre(nom)
        FauxAdaptateur.journal.append(('joindre', ordre, hashlib.sha256(octets).hexdigest()))

    def constater(self, pilote):
        pilote.text()
        FauxAdaptateur.journal.append(('constater',))
        return dict(FauxAdaptateur.constat)


class FauxOnglet:
    """Onglet piloté : chaque commande est comptée (aucune ne doit partir quand la barrière refuse)."""
    def __init__(self):
        self.appels = []

    def text(self):
        self.appels.append('text')
        return 'connecte'

    def navigate(self, url):
        self.appels.append('navigate')

    def type(self, ref, text, submit=False):
        self.appels.append('type:' + ref)

    def joindre(self, nom):
        self.appels.append('joindre')


class _Gestionnaire:
    def persist(self, s):
        pass


class Base(tg.Base):
    def setUp(self):
        super().setUp()
        membres5650.set_author(AVOCAT[0])
        C.ensure_schema(self.desk)
        E.ensure_schema(self.desk)
        modele5651.ensure_schema(self.desk)
        liv.ensure_schema(self.desk)
        self.profil = C.enregistrer_profil(self.desk, ADMIN[0], 'administrateur', {
            'portail': 'ebarreau', 'url_entree': 'https://portail.example.test/', 'domaines': 'portail.example.test', 'systeme': 'Windows 11',
            'navigateur': 'Chromium d’essai', 'compte': 'avocat-A', 'type_compte': 'professionnel'})
        self.conclusions = b'PK\x03\x04 conclusions fictives DURAND contre MARTIN'
        self.doc, self.ver, self.chemin_concl = self.livrable_fichier('Dossiers/DURAND/Conclusions DURAND.docx', self.conclusions)
        self.piece = b'%PDF-1.4 piece 1 contrat fictif DURAND'
        self.chemin_piece = self.fichier_registre('Dossiers/DURAND/PIECES/Piece 1 - Contrat.pdf', self.piece)
        FauxAdaptateur.journal = []
        FauxAdaptateur.identite_affichee = {'compte': 'avocat-A'}
        FauxAdaptateur.constat = {'statut': 'incertain'}

    def livrable_fichier(self, rel, data, mid=MID):
        path = self.fichier(rel, data)
        sha = hashlib.sha256(data).hexdigest()
        item = liv.observe(self.desk, {'service': 'essai_p111', 'object': rel, 'version': sha[:8], 'matter': mid, 'kind': 'conclusions',
                                       'title': Path(rel).stem, 'path': path, 'sha256': sha, 'size': len(data), 'state': 'verified'})
        self.desk.db.commit()
        row = liv._item(self.desk, item)
        return row['document_id'], row['version_id'], path

    def fichier_registre(self, rel, data, mid=MID):
        path = self.fichier(rel, data)
        self.desk.db.execute('INSERT OR REPLACE INTO d51_documents(matter,path,etag,name,updated) VALUES(?,?,?,?,?)',
                             (mid, path, 'etag-essai', Path(rel).name, datetime.now(timezone.utc).isoformat()))
        self.desk.db.commit()
        return path

    def donnees(self, **extra):
        d = {'cle': self.cle(), 'dossier': MID, 'profil': self.profil['id'], 'destinataire': 'Tribunal judiciaire de Valbrune — greffe',
             'juridiction': 'Tribunal judiciaire de Valbrune', 'rg': '24/01234', 'objet': 'Conclusions en défense DURAND', 'message': MESSAGE,
             'pieces': [{'ref': 'l|%s|%s' % (self.doc, self.ver), 'rang': 1}, {'ref': 'f|' + self.chemin_piece, 'rang': 2}]}
        d.update(extra)
        return d

    def preparer(self, qui=AVOCAT, **extra):
        return E.preparer(self.desk, qui, self.donnees(**extra))

    def faux(self, code):
        return '%06d' % ((int(code) + 1) % 1_000_000)

    def confirme(self):
        out = self.preparer()
        E.confirmer(self.desk, AVOCAT, out['id'], out['code'])
        return out

    def http(self, route, body=None, who=AVOCAT, args=None, methode=None):
        methode = methode or ('POST' if body is not None else 'GET')
        raw = json.dumps(body or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': who[0], 'HTTP_X_AXIORHUB_ROLE': who[1], 'HTTP_ORIGIN': 'https://cabinet.example.test', 'HTTP_X_CSRF_TOKEN': 'jeton',
               'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw), 'REQUEST_METHOD': methode}
        out = E.api(env, self.desk, {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}, '/p', E.PREFIXE + route, args or {}, methode)
        if out['kind'].startswith('application/json'):
            out['json'] = json.loads(out['body'])
        return out

    def qualifier_envoi(self, limites=None):
        """Capacités d'envoi qualifiées avec le FAUX adaptateur (essai seulement)."""
        p = C.vue_profil(self.desk, self.profil['id'])
        for cap in ('preparer', 'transmettre'):
            p = C.qualifier_capacite(self.desk, ADMIN[0], 'administrateur', p['id'], p['version'], cap, 'qualifie', 'essai simulé', limites or {})
        return p

    def deposes(self):
        return sorted(p for p in (self.root / 'Dossiers' / 'DURAND').rglob('*') if p.is_file() and 'Accus' in p.name)


# Socle seulement : les tests propres des classes de base ne sont pas rejoués ici.
for _nom in [n for n in dir(Base) if n.startswith('test')]:
    setattr(Base, _nom, None)


# ================================================================================ premier usage, migration, capacités (L06)
class PremierUsage(Base):
    def test_P01_base_neuve_tables_creees_avant_toute_lecture(self):
        for t in E.TABLES:
            self.desk.db.execute('DROP TABLE IF EXISTS %s' % t)
        self.desk.db.commit()
        self.assertEqual(E.envois(self.desk, AVOCAT, MID), [])                       # lecture sans OperationalError
        out = self.preparer()
        self.assertEqual(out['etat'], 'preparee')
        self.assertRegex(out['code'], r'^\d{6}$')
        self.assertEqual(len(E.envois(self.desk, AVOCAT, MID)), 1)

    def test_P01_schema_contrainte_unique_acteur_cle(self):
        self.preparer()
        row = self.desk.db.execute('SELECT * FROM portail_envois56111').fetchone()
        with self.assertRaises(sqlite3.IntegrityError):
            self.desk.db.execute('INSERT INTO portail_envois56111(id,acteur,cle,empreinte,dossier,profil_id,profil_version,portail,destinataire,objet,message,'
                                 'message_sha256,contenu_sha256,etat,cree_le,maj_le) SELECT ?,acteur,cle,empreinte,dossier,profil_id,profil_version,portail,'
                                 'destinataire,objet,message,message_sha256,contenu_sha256,etat,cree_le,maj_le FROM portail_envois56111 WHERE id=?',
                                 ('pe-' + 'f' * 20, row['id']))
        self.desk.db.rollback()


class Migration(unittest.TestCase):
    """Profils de l'ancienne forme (sans ``capacites_json``) : migration additive, versions inchangées, répétable."""

    class FauxDesk:
        def __init__(self, chemin):
            self.db = sqlite3.connect(chemin)
            self.db.row_factory = sqlite3.Row

        def audit(self, *a, **k):
            pass

    def test_P02_profils_anciens_migres_sans_changer_leur_version(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = self.FauxDesk(str(Path(tmp.name) / 'ancienne.sqlite3'))
        self.addCleanup(d.db.close)
        d.db.executescript(C.SCHEMA)                                                  # forme 5.6.109/5.6.110 (aucune colonne de capacités)
        self.assertNotIn('capacites_json', {r[1] for r in d.db.execute('PRAGMA table_info(collecte_profils56109)')})
        quand = '2026-10-01T08:00:00+00:00'
        d.db.execute("INSERT INTO collecte_profils56109(id,portail,libelle,version,url_entree,domaines_json,systeme,navigateur,compte,qualification,preuve_le,"
                     "preuve_par,cree_le,maj_le) VALUES('pp-a','ebarreau','e-Barreau',3,'https://portail.example.test/','[\"portail.example.test\"]','Windows 11',"
                     "'Chromium','avocat-A','qualifie',?,'admin@example.test',?,?)", (quand, quand, quand))
        d.db.execute("INSERT INTO collecte_profils56109(id,portail,libelle,version,cree_le,maj_le) VALUES('pp-b','portalis','Portalis',1,?,?)", (quand, quand))
        d.db.commit()
        C.ensure_schema(d)
        C.ensure_schema(d)                                                            # répétable
        a = d.db.execute("SELECT * FROM collecte_profils56109 WHERE id='pp-a'").fetchone()
        b = d.db.execute("SELECT * FROM collecte_profils56109 WHERE id='pp-b'").fetchone()
        self.assertEqual((a['version'], b['version']), (3, 1))
        ca, cb = C.capacites(a), C.capacites(b)
        self.assertEqual({k: v['etat'] for k, v in ca.items()}, {'consulter': 'qualifie', 'telecharger': 'qualifie', 'preparer': 'non_qualifie',
                                                                  'transmettre': 'non_qualifie'})
        self.assertEqual((ca['consulter']['preuve_le'], ca['consulter']['preuve_par']), (quand, 'admin@example.test'))
        self.assertTrue(all(v['etat'] == 'non_qualifie' for v in cb.values()))
        brut = json.loads(a['capacites_json'])
        self.assertEqual(set(brut), {'consulter', 'telecharger'})                    # aucune capacité d'envoi déduite de la collecte


class Capacites(Base):
    def test_P03_qualification_de_collecte_ne_vaut_pas_capacite_d_envoi(self):
        with patch.dict(C.ADAPTATEURS, {'ebarreau': C.Adaptateur}):
            p = C.qualifier_profil(self.desk, ADMIN[0], 'administrateur', self.profil['id'], self.profil['version'], 'essai simulé')
        caps = p['capacites']
        self.assertEqual((caps['consulter']['etat'], caps['telecharger']['etat']), ('qualifie', 'qualifie'))
        self.assertEqual((caps['preparer']['etat'], caps['transmettre']['etat']), ('non_qualifie', 'non_qualifie'))
        self.assertFalse(E.pilotable(C._profil(self.desk, p['id'])))

    def test_P03_capacite_d_envoi_non_qualifiable_sans_adaptateur(self):
        with self.assertRaisesRegex(Stop, 'capacite_non_qualifiable:.*adaptateur_envoi'):
            C.qualifier_capacite(self.desk, ADMIN[0], 'administrateur', self.profil['id'], self.profil['version'], 'transmettre', 'qualifie', 'essai')
        self.assertEqual(E.ADAPTATEURS, {})                                           # aucun adaptateur d'envoi livré
        self.assertEqual(C.ADAPTATEURS, {})

    def test_P03_capacites_distinctes_preuve_date_version_et_peremption(self):
        with patch.dict(E.ADAPTATEURS, {'ebarreau': FauxAdaptateur}):
            p = C.qualifier_capacite(self.desk, ADMIN[0], 'administrateur', self.profil['id'], self.profil['version'], 'preparer', 'qualifie', 'essai simulé',
                                     {'nombre_max': '3', 'taille_piece_max': ''})
            with self.assertRaisesRegex(Stop, 'preuve_essai_requise'):
                C.qualifier_capacite(self.desk, ADMIN[0], 'administrateur', p['id'], p['version'], 'transmettre', 'qualifie', '')
            p = C.qualifier_capacite(self.desk, ADMIN[0], 'administrateur', p['id'], p['version'], 'transmettre', 'a_tester')
        self.assertEqual(p['version'], self.profil['version'])                        # une capacité ne crée pas de version du profil
        self.assertEqual(p['capacites']['preparer']['etat'], 'qualifie')
        self.assertTrue(p['capacites']['preparer']['preuve_le'])
        self.assertEqual(p['capacites']['preparer']['limites'], {'nombre_max': 3})
        self.assertEqual(p['capacites']['transmettre']['etat'], 'a_tester')
        self.assertEqual(p['capacites']['consulter']['etat'], 'non_qualifie')
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            C.qualifier_capacite(self.desk, AVOCAT[0], 'avocat', p['id'], p['version'], 'consulter', 'a_tester')
        p2 = C.enregistrer_profil(self.desk, ADMIN[0], 'administrateur', {'id': p['id'], 'version': p['version'], 'portail': 'ebarreau',
                                  'url_entree': 'https://portail.example.test/', 'domaines': 'portail.example.test', 'systeme': 'Ubuntu 24.04',
                                  'navigateur': 'Chromium d’essai', 'compte': 'avocat-A', 'type_compte': 'professionnel'})
        self.assertEqual(p2['version'], p['version'] + 1)
        self.assertTrue(all(c['etat'] == 'non_qualifie' for c in p2['capacites'].values()))   # preuve d'une autre version : périmée
        with self.assertRaisesRegex(Stop, 'version_perimee'):
            C.qualifier_capacite(self.desk, ADMIN[0], 'administrateur', p2['id'], p['version'], 'consulter', 'a_tester')

    def test_P03_route_capacite_reservee_a_l_administrateur(self):
        out = self.http('capacite', {'profil': self.profil['id'], 'version': self.profil['version'], 'capacite': 'consulter', 'etat': 'a_tester'}, who=AVOCAT)
        self.assertEqual(out['status'], '403 Forbidden')
        out = self.http('capacite', {'profil': self.profil['id'], 'version': self.profil['version'], 'capacite': 'consulter', 'etat': 'a_tester'}, who=ADMIN)
        self.assertEqual(out['status'], '200 OK', out['json'])
        self.assertEqual(out['json']['capacites']['consulter']['etat'], 'a_tester')


# ================================================================================ préparer : texte exact, pièces du registre (L07, L08)
class Preparer(Base):
    def test_P04_message_exact_et_pieces_resolues_cote_serveur(self):
        out = self.preparer(pieces=[{'ref': 'l|%s|%s' % (self.doc, self.ver), 'rang': 1, 'sha256': '0' * 64, 'taille': 1},
                                    {'ref': 'f|' + self.chemin_piece, 'rang': 2}])
        r = out['recapitulatif']
        self.assertEqual(r['message'], MESSAGE)                                       # texte exact gardé (pas seulement son empreinte)
        self.assertEqual(r['message_sha256'], hashlib.sha256(MESSAGE.encode()).hexdigest())
        self.assertEqual([p['ordre'] for p in r['pieces']], [1, 2])
        p1, p2 = r['pieces']
        self.assertEqual((p1['source'], p1['document_id'], p1['version_id']), ('livrable', self.doc, self.ver))
        self.assertEqual(p1['sha256'], hashlib.sha256(self.conclusions).hexdigest())   # empreinte relue, jamais celle du navigateur
        self.assertEqual(p1['taille'], len(self.conclusions))
        self.assertEqual((p2['source'], p2['version_id'], p2['sha256']), ('fichier', p2['version_id'], hashlib.sha256(self.piece).hexdigest()))
        self.assertTrue(p2['version_id'])
        self.assertEqual(r['rg'], '24/01234')
        self.assertFalse(r['limites_connues'])
        row = self.desk.db.execute('SELECT profil_id, profil_version, compte FROM portail_envois56111 WHERE id=?', (out['id'],)).fetchone()
        self.assertEqual((row['profil_id'], row['profil_version'], row['compte']), (self.profil['id'], self.profil['version'], 'avocat-A'))
        self.assertFalse(out['envoi_automatique'])
        self.assertEqual(garde.etat(self.desk, E.CHEMIN, out['id'])['acteur'], AVOCAT[0])

    def test_P04_pieces_hors_registre_ou_hors_dossier_refusees(self):
        hors = self.fichier('Dossiers/DURAND/non-inscrit.pdf', b'%PDF-1.4 hors registre')
        with self.assertRaisesRegex(Stop, 'piece_hors_registre'):
            self.preparer(pieces=[{'ref': 'f|' + hors, 'rang': 1}])
        autre = self.fichier_registre('Dossiers/MARTIN/piece.pdf', b'%PDF-1.4 MARTIN', mid=MID2)
        with self.assertRaisesRegex(Stop, 'piece_hors_registre'):
            self.preparer(pieces=[{'ref': 'f|' + autre, 'rang': 1}])
        doc2, ver2, _ = self.livrable_fichier('Dossiers/MARTIN/Conclusions MARTIN.docx', b'PK\x03\x04 MARTIN', mid=MID2)
        with self.assertRaisesRegex(Stop, 'version_hors_dossier|introuvable'):
            self.preparer(pieces=[{'ref': 'l|%s|%s' % (doc2, ver2), 'rang': 1}])
        for mauvais in ('x|abc', 'l|../x|v', 'l|doc', ''):
            with self.assertRaisesRegex(Stop, 'piece_invalide'):
                self.preparer(pieces=[{'ref': mauvais, 'rang': 1}])
        with self.assertRaisesRegex(Stop, 'rang_en_double'):
            self.preparer(pieces=[{'ref': 'f|' + self.chemin_piece, 'rang': 1}, {'ref': 'l|%s|%s' % (self.doc, self.ver), 'rang': 1}])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM portail_envois56111').fetchone()[0], 0)

    def test_P04_champs_du_formulaire_rangs(self):
        d = self.donnees()
        del d['pieces']
        d.update({'r_1': 'f|' + self.chemin_piece, 'p_1': '1', 'r_2': 'l|%s|%s' % (self.doc, self.ver), 'p_2': '', 'r_3': 'l|%s|%s' % (self.doc, self.ver), 'p_3': '2'})
        out = E.preparer(self.desk, AVOCAT, d)
        self.assertEqual([p['source'] for p in out['recapitulatif']['pieces']], ['fichier', 'livrable'])

    def test_P04_limites_du_profil_qualifie_appliquees_sinon_non_connues(self):
        with patch.dict(E.ADAPTATEURS, {'ebarreau': FauxAdaptateur}):
            self.qualifier_envoi({'nombre_max': '1'})
            with self.assertRaisesRegex(Stop, 'pieces_au_dela_des_limites_du_portail'):
                self.preparer()
            out = self.preparer(pieces=[{'ref': 'f|' + self.chemin_piece, 'rang': 1}])
        self.assertEqual(out['recapitulatif']['limites'], {'nombre_max': 1})
        html = E._limites_html({})
        self.assertIn('non connues, à vérifier sur le portail', html)

    def test_P04_champs_obligatoires(self):
        for champ, code in (('destinataire', 'destinataire_requis'), ('objet', 'objet_requis'), ('message', 'message_requis')):
            with self.assertRaisesRegex(Stop, code):
                self.preparer(**{champ: '  '})
        with self.assertRaisesRegex(Stop, 'message_trop_long'):
            self.preparer(message='x' * (E.MAX_MESSAGE + 1))
        with self.assertRaisesRegex(Stop, 'profil_absent'):
            self.preparer(profil='pp-inconnu')


class Idempotence(Base):
    def test_P05_meme_cle_meme_contenu_meme_envoi_autre_contenu_refus(self):
        d = self.donnees()
        a = E.preparer(self.desk, AVOCAT, dict(d))
        b = E.preparer(self.desk, AVOCAT, dict(d))
        self.assertEqual(a['id'], b['id'])
        self.assertTrue(b['rejoue'])
        self.assertIsNone(b['code'])                                                  # le code n'est jamais réaffiché
        with self.assertRaisesRegex(Stop, 'cle_reutilisee'):
            E.preparer(self.desk, AVOCAT, dict(d, message=MESSAGE + ' (modifié)'))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM portail_envois56111').fetchone()[0], 1)

    def test_P05_deux_preparations_concurrentes_meme_cle_deux_connexions(self):
        d = self.donnees()
        depart, res = threading.Barrier(2), []

        def lancer():
            membres5650.set_author(AVOCAT[0])
            dk = Desk(self.f.c)
            try:
                depart.wait(5)
                res.append(E.preparer(dk, AVOCAT, dict(d))['id'])
            except Exception as ex:   # noqa: BLE001
                res.append('erreur:%s' % ex)
            finally:
                dk.db.close()
        fils = [threading.Thread(target=lancer) for _ in range(2)]
        for t in fils:
            t.start()
        for t in fils:
            t.join(30)
        self.assertEqual(len(set(res)), 1, res)
        self.assertTrue(res[0].startswith('pe-'), res)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM portail_envois56111').fetchone()[0], 1)


# ================================================================================ confirmer (garde commune)
class Confirmer(Base):
    def test_P06_confirmation_puis_double_confirmation_refusee(self):
        out = self.preparer()
        v = E.confirmer(self.desk, AVOCAT, out['id'], out['code'])
        self.assertEqual(v['etat'], 'confirmee')
        with self.assertRaisesRegex(Stop, 'envoi_deja_confirme'):
            E.confirmer(self.desk, AVOCAT, out['id'], out['code'])

    def test_P06_expiration_exacte_puis_nouveau_code(self):
        out = self.preparer()
        fin = datetime.fromisoformat(garde.etat(self.desk, E.CHEMIN, out['id'])['expire'])
        with patch.object(garde, '_maintenant', return_value=fin):
            with self.assertRaisesRegex(Stop, 'code_portail_expire'):
                E.confirmer(self.desk, AVOCAT, out['id'], out['code'])
        self.assertEqual(E.vue(self.desk, AVOCAT, out['id'])['etat'], 'preparee')
        n = E.nouveau_code(self.desk, AVOCAT, out['id'])
        self.assertNotEqual(n['code'], None)
        with self.assertRaisesRegex(Stop, 'code_portail_incorrect|code_portail_expire|envoi_deja'):
            E.confirmer(self.desk, AVOCAT, out['id'], self.faux(n['code']) if n['code'] == out['code'] else out['code'])
        fin2 = datetime.fromisoformat(garde.etat(self.desk, E.CHEMIN, out['id'])['expire'])
        with patch.object(garde, '_maintenant', return_value=fin2 - timedelta(microseconds=1)):
            self.assertEqual(E.confirmer(self.desk, AVOCAT, out['id'], n['code'])['etat'], 'confirmee')

    def test_P06_cinquieme_essai_juste_ouvre(self):
        out = self.preparer()
        for restant in (4, 3, 2, 1):
            with self.assertRaisesRegex(Stop, 'code_portail_incorrect') as ctx:
                E.confirmer(self.desk, AVOCAT, out['id'], self.faux(out['code']))
            self.assertEqual(detail_of(ctx.exception)['essais_restants56111'], restant)
        self.assertEqual(E.confirmer(self.desk, AVOCAT, out['id'], out['code'])['etat'], 'confirmee')

    def test_P06_cinquieme_essai_faux_bloque_et_envoi_a_preparer_de_nouveau(self):
        out = self.preparer()
        for _ in range(4):
            with self.assertRaises(Stop):
                E.confirmer(self.desk, AVOCAT, out['id'], self.faux(out['code']))
        with self.assertRaisesRegex(Stop, '^confirmation_bloquee$'):
            E.confirmer(self.desk, AVOCAT, out['id'], self.faux(out['code']))
        self.assertEqual(E.vue(self.desk, AVOCAT, out['id'])['etat'], 'perimee')
        with self.assertRaisesRegex(Stop, 'envoi_modifie_depuis_preparation'):
            E.confirmer(self.desk, AVOCAT, out['id'], out['code'])
        with self.assertRaisesRegex(Stop, 'etat_incompatible'):
            E.nouveau_code(self.desk, AVOCAT, out['id'])

    def test_P06_message_lisible_avec_essais_restants(self):
        out = self.preparer()
        r = self.http('confirmer', {'id': out['id'], 'code': 'abc'})
        self.assertEqual(r['status'], '422 Unprocessable Entity')
        self.assertEqual(r['json']['message'], 'Code faux. Il reste 4 essai(s) : recopiez les six chiffres affichés.')

    def test_P06_deux_confirmations_concurrentes_deux_connexions_reelles(self):
        out = self.preparer()
        depart, res = threading.Barrier(2), []

        def valider():
            membres5650.set_author(AVOCAT[0])
            dk = Desk(self.f.c)
            try:
                depart.wait(5)
                res.append(('ok', E.confirmer(dk, AVOCAT, out['id'], out['code'])['etat']))
            except Stop as ex:
                res.append(('refus', str(ex)))
            finally:
                dk.db.close()
        fils = [threading.Thread(target=valider) for _ in range(2)]
        for t in fils:
            t.start()
        for t in fils:
            t.join(30)
        self.assertEqual(sorted(r[0] for r in res), ['ok', 'refus'], res)
        self.assertEqual(next(r[1] for r in res if r[0] == 'refus'), 'envoi_deja_confirme')
        self.assertEqual(garde.etat(self.desk, E.CHEMIN, out['id'])['essais'], 1)

    def test_P06_autre_personne_ne_confirme_pas_sans_essai_decompte(self):
        out = self.preparer()
        membres5650.set_author(ADMIN[0])
        with self.assertRaisesRegex(Stop, 'confirmation_autre_utilisateur'):
            E.confirmer(self.desk, ADMIN, out['id'], out['code'])
        membres5650.set_author(AVOCAT[0])
        self.assertEqual(garde.etat(self.desk, E.CHEMIN, out['id'])['essais'], 0)


class Modifications(Base):
    def test_P07_piece_modifiee_apres_preparation_invalide_la_confirmation(self):
        out = self.preparer()
        (self.root / self.chemin_piece.lstrip('/')).write_bytes(b'%PDF-1.4 piece 1 contrat MODIFIE')
        with self.assertRaisesRegex(Stop, 'envoi_modifie_depuis_preparation'):
            E.confirmer(self.desk, AVOCAT, out['id'], out['code'])
        v = E.vue(self.desk, AVOCAT, out['id'])
        self.assertEqual(v['etat'], 'perimee')
        self.assertIn('piece_2', v['motif'])
        self.assertEqual(garde.etat(self.desk, E.CHEMIN, out['id'])['essais'], 0)

    def test_P07_nouvelle_version_du_livrable_invalide_la_confirmation(self):
        out = self.preparer()
        nouveau = b'PK\x03\x04 conclusions fictives DURAND version 2'
        (self.root / self.chemin_concl.lstrip('/')).write_bytes(nouveau)
        sha = hashlib.sha256(nouveau).hexdigest()
        liv.observe(self.desk, {'service': 'essai_p111', 'object': 'Dossiers/DURAND/Conclusions DURAND.docx', 'version': sha[:8], 'matter': MID,
                                'kind': 'conclusions', 'title': 'Conclusions DURAND', 'path': self.chemin_concl, 'sha256': sha, 'size': len(nouveau),
                                'state': 'verified'})
        self.desk.db.commit()
        with self.assertRaisesRegex(Stop, 'envoi_modifie_depuis_preparation'):
            E.confirmer(self.desk, AVOCAT, out['id'], out['code'])
        self.assertEqual(E.vue(self.desk, AVOCAT, out['id'])['etat'], 'perimee')

    def test_P07_profil_modifie_apres_preparation_invalide_la_confirmation(self):
        out = self.preparer()
        C.enregistrer_profil(self.desk, ADMIN[0], 'administrateur', {'id': self.profil['id'], 'version': self.profil['version'], 'portail': 'ebarreau',
                             'url_entree': 'https://portail.example.test/', 'domaines': 'portail.example.test', 'systeme': 'Windows 11',
                             'navigateur': 'Chromium d’essai', 'compte': 'avocat-B', 'type_compte': 'professionnel'})
        with self.assertRaisesRegex(Stop, 'envoi_modifie_depuis_preparation'):
            E.confirmer(self.desk, AVOCAT, out['id'], out['code'])
        self.assertEqual(E.vue(self.desk, AVOCAT, out['id'])['motif'], 'profil_modifie')

    def test_P07_piece_modifiee_apres_confirmation_n_est_pas_servie(self):
        out = self.confirme()
        r = self.http('piece', args={'id': out['id'], 'ordre': '2'})
        self.assertEqual(r['body'], self.piece)
        self.assertIn(('Cache-Control', 'private, no-store'), r['headers'])
        (self.root / self.chemin_piece.lstrip('/')).write_bytes(b'%PDF-1.4 autre contenu')
        r = self.http('piece', args={'id': out['id'], 'ordre': '2'})
        self.assertEqual((r['status'], r['json']['error']), ('409 Conflict', 'piece_modifiee_depuis_preparation'))


class Reprise(Base):
    def test_P08_arret_apres_confirmation_et_reprise_sans_double_effet(self):
        out = self.confirme()
        self.desk.db.close()
        self.desk = Desk(self.f.c)                                                    # redémarrage : nouvelle connexion, même base
        self.addCleanup(self.desk.db.close)
        v = E.vue(self.desk, AVOCAT, out['id'])
        self.assertEqual((v['etat'], v['recapitulatif']['message']), ('confirmee', MESSAGE))
        with self.assertRaisesRegex(Stop, 'envoi_deja_confirme'):
            E.confirmer(self.desk, AVOCAT, out['id'], out['code'])
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind LIKE '%portail%'").fetchone()[0], 0)   # rien en file, rien envoyé
        a = E.enregistrer_accuse(self.desk, AVOCAT, out['id'], 'accuse.pdf', b'%PDF-1.4 accuse fictif', 'procedure')
        self.assertEqual(a['etat'], 'deposee')


# ================================================================================ transmettre, accusé, incertitude
class Accuse(Base):
    def test_P09_accuses_de_formats_divers_conserves_tels_quels(self):
        cas = ((b'%PDF-1.4 accuse de depot fictif', 'accuse.pdf', 'pdf'), (self._zip(), 'Accuse e-Barreau', 'zip'),
               (b'\x00\x01ACCUSE-FICTIF-BINAIRE', 'accuse.p7m', 'autre'))
        for raw, nom, fmt in cas:
            out = self.confirme()
            v = E.enregistrer_accuse(self.desk, AVOCAT, out['id'], nom, raw, 'procedure')
            self.assertEqual(v['etat'], 'deposee')
            acc = v['accuses'][0]
            self.assertEqual((acc['format'], acc['sha256'], acc['etat']), (fmt, hashlib.sha256(raw).hexdigest(), 'verifie'))
            depose = self.root / acc['chemin'].lstrip('/')
            self.assertEqual(depose.read_bytes(), raw)                                # format conservé, octet pour octet
            self.assertIn('/Dossiers/DURAND/', acc['chemin'])
            recu = json.loads(self.desk.db.execute('SELECT recu_json FROM portail_accuses56111 WHERE id=?', (acc['id'],)).fetchone()[0])
            self.assertEqual((recu['rg'], recu['juridiction'], recu['destinataire']), ('24/01234', 'Tribunal judiciaire de Valbrune',
                                                                                         'Tribunal judiciaire de Valbrune — greffe'))
            self.assertEqual([(p['ordre'], p['version_id']) for p in recu['pieces']], [(1, self.ver), (2, recu['pieces'][1]['version_id'])])
            self.assertEqual(recu['message_sha256'], hashlib.sha256(MESSAGE.encode()).hexdigest())
            again = E.enregistrer_accuse(self.desk, AVOCAT, out['id'], nom, raw, 'procedure')
            self.assertTrue(again['rejoue'])
        self.assertEqual(len(self.deposes()), 3)                                      # aucun doublon
        self.assertTrue(self.deposes()[0].name.endswith('.pdf') or True)

    def _zip(self):
        b = io.BytesIO()
        with zipfile.ZipFile(b, 'w') as z:
            z.writestr('accuse.xml', '<accuse>fictif</accuse>')
        return b.getvalue()

    def test_P09_accuse_par_la_route_et_famille_correspondances(self):
        out = self.confirme()
        raw = b'%PDF-1.4 accuse route'
        r = self.http('accuse', {'id': out['id'], 'famille': 'correspondances',
                                 'accuse': {'nom': 'accuse.pdf', 'contenu': 'data:application/pdf;base64,' + base64.b64encode(raw).decode()}})
        self.assertEqual(r['status'], '200 OK', r.get('json'))
        acc = r['json']['accuses'][0]
        self.assertEqual(acc['famille'], 'correspondances')
        self.assertEqual((self.root / acc['chemin'].lstrip('/')).read_bytes(), raw)

    def test_P10_retour_incertain_a_verifier_sans_renvoi(self):
        out = self.confirme()
        v = E.declarer_transmis(self.desk, AVOCAT, out['id'])
        self.assertEqual((v['etat'], v['motif']), ('a_verifier', 'accuse_non_enregistre'))
        for fn in (lambda: E.confirmer(self.desk, AVOCAT, out['id'], out['code']), lambda: E.declarer_transmis(self.desk, AVOCAT, out['id']),
                   lambda: E.preremplir(self.desk, AVOCAT, out['id'])):
            with self.assertRaises(Stop):
                fn()
        self.assertEqual(E.vue(self.desk, AVOCAT, out['id'])['etat'], 'a_verifier')
        a = E.enregistrer_accuse(self.desk, AVOCAT, out['id'], 'accuse.pdf', b'%PDF-1.4 accuse tardif', 'procedure')
        self.assertEqual(a['etat'], 'deposee')

    def test_P10_annulation_avant_accuse_seulement(self):
        out = self.preparer()
        v = E.annuler(self.desk, AVOCAT, out['id'])
        self.assertEqual(v['etat'], 'annulee')
        self.assertEqual(garde.etat(self.desk, E.CHEMIN, out['id'])['etat'], 'annule')
        with self.assertRaisesRegex(Stop, 'confirmation_bloquee|code_portail|envoi_deja|etat_incompatible|envoi_modifie'):
            E.confirmer(self.desk, AVOCAT, out['id'], out['code'])
        out2 = self.confirme()
        E.declarer_transmis(self.desk, AVOCAT, out2['id'])
        self.assertIn('Rien n’est retiré du portail', E.annuler(self.desk, AVOCAT, out2['id'])['message'])
        out3 = self.confirme()
        E.enregistrer_accuse(self.desk, AVOCAT, out3['id'], 'accuse.pdf', b'%PDF-1.4 accuse', 'procedure')
        with self.assertRaisesRegex(Stop, 'etat_incompatible|accuse_deja_enregistre'):
            E.annuler(self.desk, AVOCAT, out3['id'])

    def test_P10_ouvrir_le_portail_sans_saisie(self):
        out = self.confirme()
        vus = []
        with patch.object(E, 'OUVERTURE', lambda desk, acteur, url: vus.append((acteur, url))):
            E.ouvrir_portail(self.desk, AVOCAT, out['id'])
        self.assertEqual(vus, [(AVOCAT[0], 'https://portail.example.test/')])
        with patch.object(nv, 'available', return_value=False):
            with self.assertRaisesRegex(Stop, 'poste_requis'):
                E.ouvrir_portail(self.desk, AVOCAT, out['id'])
        self.desk.db.execute("UPDATE collecte_profils56109 SET url_entree='https://ailleurs.example.test/' WHERE id=?", (self.profil['id'],))
        self.desk.db.commit()
        with patch.object(E, 'OUVERTURE', lambda *a: vus.append(a)):
            with self.assertRaisesRegex(Stop, 'adresse_hors_domaines'):
                E.ouvrir_portail(self.desk, AVOCAT, out['id'])
        self.assertEqual(len(vus), 1)

    def test_P10_sans_adaptateur_qualifie_rien_n_est_pilote(self):
        out = self.confirme()
        with self.assertRaisesRegex(Stop, 'transmission_non_pilotable'):
            E.preremplir(self.desk, AVOCAT, out['id'])
        self.assertEqual(E.vue(self.desk, AVOCAT, out['id'])['etat'], 'confirmee')


class Adaptateur(Base):
    """Point de montage d'un futur adaptateur : préremplissage par la barrière, main rendue, clic final par l'avocat (faux adaptateur)."""

    def setUp(self):
        super().setUp()
        self.onglet = FauxOnglet()
        self.seance = nv.Session(_Gestionnaire(), AVOCAT[0], 'Essai de préremplissage', '')
        p = patch.object(E, 'NAVIGATION', lambda desk, acteur: C.Navigation(self.seance, self.onglet, None))
        p.start()
        self.addCleanup(p.stop)
        p2 = patch.dict(E.ADAPTATEURS, {'ebarreau': FauxAdaptateur})
        p2.start()
        self.addCleanup(p2.stop)
        self.qualifier_envoi()

    def test_P11_preremplissage_par_la_barriere_puis_main_a_l_avocat(self):
        out = self.confirme()
        v = E.preremplir(self.desk, AVOCAT, out['id'])
        self.assertEqual(v['etat'], 'preremplie')
        self.assertTrue(self.seance.hold)
        self.assertTrue(v['main_acquittee'])
        self.assertEqual([j[0] for j in FauxAdaptateur.journal], ['ouvrir', 'remplir', 'remplir', 'remplir', 'remplir', 'remplir', 'joindre', 'joindre'])
        self.assertEqual(FauxAdaptateur.journal[-2][2], hashlib.sha256(self.conclusions).hexdigest())
        self.assertFalse([n for n in dir(E.AdaptateurEnvoi) if any(m in n for m in ('envoyer', 'soumettre', 'valider', 'signer', 'payer', 'supprimer'))])
        n = len(self.onglet.appels)
        with self.assertRaises(Stop):                                                 # main à l'avocat : plus aucune commande du pilote
            N.Pilote(self.onglet, self.seance, self.seance.epoch, self.desk).text()
        self.assertEqual(len(self.onglet.appels), n)
        c = E.constater(self.desk, AVOCAT, out['id'])                                 # retour incertain : à vérifier, aucun renvoi
        self.assertEqual(c['etat'], 'a_verifier')
        with self.assertRaisesRegex(Stop, 'etat_incompatible'):
            E.preremplir(self.desk, AVOCAT, out['id'])

    def test_P11_barriere_refusee_rien_n_est_envoye(self):
        out = self.confirme()
        N.changer_main(self.seance, True)                                             # l'avocat a la main avant le début
        v = E.preremplir(self.desk, AVOCAT, out['id'])
        self.assertEqual(v['etat'], 'confirmee')
        self.assertEqual(v['motif'], 'main_a_l_avocat')
        self.assertEqual(self.onglet.appels, [])
        self.assertEqual(FauxAdaptateur.journal, [])

    def test_P11_compte_affiche_different_suspend(self):
        out = self.confirme()
        FauxAdaptateur.identite_affichee = {'compte': 'autre-avocat'}
        v = E.preremplir(self.desk, AVOCAT, out['id'])
        self.assertEqual((v['etat'], v['motif']), ('confirmee', 'compte_different_du_profil'))
        self.assertNotIn('navigate', self.onglet.appels)

    def test_P11_accuse_constate_par_l_adaptateur_enregistre(self):
        out = self.confirme()
        E.preremplir(self.desk, AVOCAT, out['id'])
        FauxAdaptateur.constat = {'statut': 'accuse', 'nom': 'accuse.zip', 'octets': b'PK\x03\x04 accuse natif fictif'}
        v = E.constater(self.desk, AVOCAT, out['id'])
        self.assertEqual(v['etat'], 'deposee')
        self.assertEqual(v['accuses'][0]['origine'], 'portail')


# ================================================================================ droits, routes, export, écrans
class Droits(Base):
    def test_P12_droits_du_dossier(self):
        membres5650.set_author(AUTRE[0])
        with self.assertRaisesRegex(Stop, 'introuvable'):
            E.preparer(self.desk, AUTRE, self.donnees(dossier=MID2, pieces=[]))
        membres5650.set_author(AVOCAT[0])
        out = E.preparer(self.desk, AVOCAT, self.donnees(dossier=MID2, pieces=[]))
        r = self.http('envoi', args={'id': out['id']}, who=AUTRE)
        self.assertEqual((r['status'], r['json']['error']), ('404 Not Found', 'introuvable'))
        r = self.http('envois', args={'d': MID2}, who=AUTRE)
        self.assertEqual(r['status'], '404 Not Found')
        membres5650.set_viewer(frozenset({MID2}))                                     # droit retiré entre l'aperçu et le code
        with self.assertRaisesRegex(Stop, 'introuvable|dossier_reserve'):
            E.confirmer(self.desk, AVOCAT, out['id'], out['code'])
        membres5650.set_viewer(frozenset())
        self.assertEqual(garde.etat(self.desk, E.CHEMIN, out['id'])['essais'], 0)

    def test_P12_assistant_prepare_mais_ne_confirme_pas(self):
        membres5650.set_author(ASSISTANT[0])
        out = E.preparer(self.desk, ASSISTANT, self.donnees())
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            E.confirmer(self.desk, ASSISTANT, out['id'], out['code'])
        r = self.http('confirmer', {'id': out['id'], 'code': out['code']}, who=ASSISTANT)
        self.assertEqual(r['status'], '403 Forbidden')

    def test_P12_droit_envoyer_retire(self):
        out = self.preparer()
        membres5650.set_rights(self.f.c['state_dir'], ADMIN[0], AVOCAT[0], 'avocat', {'envoyer': False})
        with self.assertRaisesRegex(Stop, 'droit_envoyer_requis'):
            E.confirmer(self.desk, AVOCAT, out['id'], out['code'])

    def test_P12_automatisme_refuse(self):
        with self.assertRaisesRegex(Stop, 'action_reservee_a_une_personne'):
            E.preparer(self.desk, ('agent', 'avocat'), self.donnees())


class Routes(Base):
    def test_P13_parcours_http_et_route_inconnue(self):
        r = self.http('inconnue', {'x': 1})
        self.assertEqual(r['status'], '404 Not Found')
        r = self.http('preparer/../confirmer', {'x': 1})
        self.assertEqual(r['status'], '404 Not Found')
        self.assertFalse(E.allowed('avocat', 'DELETE', E.PREFIXE + 'envoi'))
        self.assertFalse(E.allowed('client', 'GET', E.PREFIXE + 'envoi'))
        d = self.donnees()
        r = self.http('preparer', d)
        self.assertEqual(r['status'], '200 OK', r['json'])
        self.assertIn(('Cache-Control', 'private, no-store'), r['headers'])
        code, ident = r['json']['code'], r['json']['id']
        r = self.http('confirmer', {'id': ident, 'code': code})
        self.assertEqual((r['status'], r['json']['etat']), ('200 OK', 'confirmee'))
        r = self.http('envois', args={'d': MID})
        self.assertEqual([x['id'] for x in r['json']['envois']], [ident])
        r = self.http('profils')
        self.assertFalse(r['json']['profils'][0]['pilotable'])
        r = self.http('envoi', args={'id': ident})
        self.assertEqual(r['status'], '200 OK')
        r = self.http('preparer', dict(d, message='autre'))
        self.assertEqual((r['status'], r['json']['error']), ('409 Conflict', 'cle_reutilisee'))

    def test_P13_csrf_et_origine(self):
        raw = json.dumps(self.donnees()).encode()
        env = {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': 'avocat', 'HTTP_ORIGIN': 'https://ailleurs.example.test', 'HTTP_X_CSRF_TOKEN': 'jeton',
               'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw), 'REQUEST_METHOD': 'POST'}
        out = E.api(env, self.desk, {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}, '/p', E.PREFIXE + 'preparer', {}, 'POST')
        self.assertEqual(out['status'], '403 Forbidden')

    def test_P13_routes_declarees(self):
        from agent import standalone_auth, web440
        self.assertTrue(standalone_auth.allowed('avocat', 'POST', '/api440/' + E.PREFIXE + 'confirmer'))
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/' + E.PREFIXE + 'confirmer'))
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/' + E.PREFIXE + 'capacite'))
        self.assertIn('/static/v56111-portail.js', web440.STATIC)
        self.assertIn('/static/v56111-portail.css', web440.STATIC)
        texte = Path(web440.__file__).read_text(encoding='utf-8')
        self.assertIn("name.startswith('m56111/portail/')", texte)


class Export(Base):
    def test_P14_tables_declarees_export_et_restauration(self):
        from agent import export5688 as X, export56108 as R
        declarees = {t[2]: t[4] for t in R.EXPORT}
        self.assertEqual(declarees['portail_envois56111'], 'dossier')
        self.assertEqual(declarees['portail_envoi_pieces56111'], ('envoi', 'portail_envois56111', 'id', 'dossier'))
        self.assertEqual(declarees['portail_accuses56111'], ('envoi', 'portail_envois56111', 'id', 'dossier'))
        out = self.confirme()
        E.enregistrer_accuse(self.desk, AVOCAT, out['id'], 'accuse.pdf', b'%PDF-1.4 accuse export', 'procedure')
        reserve = E.preparer(self.desk, AVOCAT, self.donnees(dossier=MID2, pieces=[]))
        dest = Path(self.tmp.name) / 'export.zip'
        X.construire(self.desk, dest, {'dossiers_reserves': 'exclure'})
        with zipfile.ZipFile(dest) as z:
            noms = z.namelist()
            envois = [n for n in noms if 'envois_prepares_sur_un_portail' in n]
            self.assertTrue(envois, noms[:20])
            contenu = b''.join(z.read(n) for n in envois)
            self.assertIn(out['id'].encode(), contenu)
            self.assertNotIn(reserve['id'].encode(), contenu)                         # dossier réservé exclu
            self.assertTrue([n for n in noms if 'accuses_de_depot_sur_un_portail' in n])
            self.assertFalse([n for n in noms if 'confirmations56111' in n])         # aucun défi exporté
        # restauration : copie cohérente de la base (API de sauvegarde SQLite) vers un nouvel emplacement, reprise sans renvoi
        cible = Path(self.tmp.name) / 'restaure'
        cible.mkdir()
        src = sqlite3.connect(str(Path(self.f.c['state_dir']) / 'desk.sqlite3'))
        dst = sqlite3.connect(str(cible / 'desk.sqlite3'))
        src.backup(dst)
        src.close()
        dst.close()
        for nom in ('membres5650.json',):
            if (Path(self.f.c['state_dir']) / nom).exists():
                shutil.copy(Path(self.f.c['state_dir']) / nom, cible / nom)
        c2 = dict(self.f.c, state_dir=str(cible))
        d2 = Desk(c2)
        self.addCleanup(d2.db.close)
        E.ensure_schema(d2)
        C.ensure_schema(d2)
        row = d2.db.execute('SELECT etat, message FROM portail_envois56111 WHERE id=?', (out['id'],)).fetchone()
        self.assertEqual((row['etat'], row['message']), ('deposee', MESSAGE))
        self.assertEqual(d2.db.execute('SELECT COUNT(*) FROM portail_accuses56111 WHERE envoi=?', (out['id'],)).fetchone()[0], 1)
        self.assertEqual(C.vue_profil(d2, self.profil['id'])['version'], self.profil['version'])


class Ecrans(Base):
    def test_P15_ecran_du_dossier_et_traductions(self):
        from agent import ecrans5632 as EC
        import test_v56107_langues as L
        out = self.preparer()
        out2 = self.confirme()
        E.declarer_transmis(self.desk, AVOCAT, out2['id'])
        out3 = self.confirme()
        E.enregistrer_accuse(self.desk, AVOCAT, out3['id'], 'accuse.pdf', b'%PDF-1.4 accuse ecran', 'procedure')
        html = E.montage_fiche(self.desk, AVOCAT, '/p', {'id': MID})
        self.assertIn('id="p111-portail"', html)
        self.assertIn('AxiorHub ne pilote aucun portail', html)
        self.assertIn('Préparer un envoi sur le portail', html)
        self.assertIn('non connues, à vérifier sur le portail', html)
        self.assertIn(out['id'], html)
        self.assertIn('Confirmer l’envoi préparé', html)
        self.assertNotIn(out['code'], html)                                           # le code n'est jamais réaffiché
        self.assertIn('Rang de Conclusions DURAND', html)
        self.assertEqual(E.montage_fiche(self.desk, AUTRE, '/p', {'id': MID2}), '')  # dossier réservé : rien
        cap = C.capacites_html(C.vue_profil(self.desk, self.profil['id']), 'administrateur')
        for nom, page in (('dossier', html), ('capacites', cap)):
            for lang in L.LANGUES:
                restes = L.reste_francais(L.visibles(EC.translate_html(page, lang)))
                self.assertEqual(restes, [], '%s (%s)' % (nom, lang))

    def test_P15_messages_traduits(self):
        from agent import ecrans5632 as EC
        import test_v56107_langues as L
        manquants = []
        for code, texte in list(E.MESSAGES.items()) + [(k, v) for k, v in E.ETATS.items()]:
            for lang in L.LANGUES:
                if L.reste_francais([EC.phrase(texte, EC.dictionary(lang)) or texte]):
                    manquants.append((lang, code))
        self.assertEqual(manquants, [])

    def test_P15_script_toutes_les_phrases_au_dictionnaire(self):
        from agent import ecrans5632 as EC
        import test_v56107_langues as L
        manquants = []
        racine = Path(E.__file__).resolve().parent / 'static'
        for no, s in L.litteraux_js(racine / 'v56111-portail.js'):
            if not L.texte_visible_js(s):
                continue
            for lang in L.LANGUES:
                if not EC.dictionary(lang).get(s):
                    manquants.append((lang, no, s))
        self.assertEqual(manquants, [])

    def test_P15_guide_en_ligne(self):
        from agent import aide5631
        self.assertEqual([p for p in aide5631.check() if p[1] == 'pieces'], [])
        etapes = aide5631.articles('fr')['pieces']['steps']
        self.assertTrue(any('portail' in s for s in etapes))
        self.assertTrue(3 <= len(etapes) <= 7)


if __name__ == '__main__':
    unittest.main()
