"""5.6.88 : réversibilité : sauvegarde chiffrée, rétention, second emplacement (faux services sur la boucle locale), export intégral, vérification du manifeste.
Données fictives ; aucun appel réseau réel."""
import base64
import csv
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import inspect
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qsl, unquote, urlsplit
import zipfile

import test_v5652 as base
import test_agent as fixtures
from agent import cadence5662, export5688 as ex, jetons5688, membres5650, operations, reversibilite5688 as rv, sauvegarde5688 as sv, standalone_auth, stockage5688 as st, totp5688
from agent.common import Stop, private_json, read_secret
from agent.desk import CONTROL_JOBS, Desk, NON_EXCLUSIVE_JOBS, run_lock
from agent.web import App

PHRASE = 'valbrune ardoise kervilac orlandec zorval'
ROOT = Path(__file__).resolve().parents[1]


def nom_archive(jour, heure='0200', n=0):
    return 'axiorhub-%s-%s-%012x.axbk' % (jour.strftime('%Y%m%d'), heure, n)


class Base(base.Base):
    def phrase(self):
        sv.definir_phrase(self.desk, PHRASE, PHRASE, 'admin@example.test')

    def actif(self, **extra):
        self.phrase()
        sv.enregistrer(self.desk, {'actif': True, **extra}, 'admin@example.test')

    def sauver(self, **kw):
        return sv.executer(self.desk, 'manuel', **kw)

    def dest(self):
        return sv.dossier(self.desk)

    def archive(self):
        row = self.desk.db.execute("SELECT nom FROM sauvegardes5688 WHERE nom<>'' ORDER BY debut DESC LIMIT 1").fetchone()   # deux archives de la même minute : le nom seul ne les ordonne pas
        return self.dest() / row['nom']

    def extraire(self):
        cible = Path(tempfile.mkdtemp(dir=self.tmp.name))
        shutil.rmtree(cible)
        sv.extraire(self.archive(), cible, phrase=PHRASE)
        return cible


# ------------------------------------------------------------------------------------------------ phrase
class Phrase(Base):
    def test_sans_phrase_la_sauvegarde_ne_demarre_pas(self):
        with self.assertRaisesRegex(Stop, 'phrase_de_sauvegarde_absente'):
            self.sauver()
        self.assertFalse(self.dest().exists() and list(self.dest().iterdir()))
        with self.assertRaisesRegex(Stop, 'phrase_de_sauvegarde_absente'):
            sv.enregistrer(self.desk, {'actif': True}, 'admin')
        with self.assertRaisesRegex(Stop, 'phrase_de_sauvegarde_absente'):
            sv.lancer(self.desk, 'admin')
        self.assertFalse(sv.due(self.desk))
        html = rv.sauvegarde_html(self.desk)
        self.assertIn('la sauvegarde ne peut pas démarrer', html)
        self.assertNotIn('Sauvegarder maintenant', html)

    def test_phrase_ni_stockee_ni_journalisee(self):
        self.phrase()
        self.sauver()
        self.assertTrue(sv.tester_phrase(self.desk, PHRASE))
        needles = (PHRASE.encode(), PHRASE.encode('utf-16-le'), base64.b64encode(PHRASE.encode()))
        self.desk.db.commit()
        for path in Path(self.desk.c['state_dir']).rglob('*'):
            if path.is_file():
                data = path.read_bytes()
                for needle in needles:
                    self.assertNotIn(needle, data, path.name)
        args = [r[0] for r in self.desk.db.execute('SELECT args FROM jobs')]
        self.assertFalse([a for a in args if 'valbrune' in a])

    def test_cle_chiffree_dans_le_coffre(self):
        self.phrase()
        raw = (Path(self.desk.c['state_dir']) / 'vault/sauvegarde5688/cle.secret').read_text()
        self.assertTrue(raw.startswith('AXIORHUB-VAULT-V1:'))
        self.assertNotIn(sv.cle_active(self.desk).hex(), json.dumps(self.desk.settings(sv.META_CLE)))

    def test_controles_de_saisie(self):
        with self.assertRaisesRegex(Stop, 'trop_courte'):
            sv.definir_phrase(self.desk, 'courte', 'courte', 'admin')
        with self.assertRaisesRegex(Stop, 'differente'):
            sv.definir_phrase(self.desk, PHRASE, PHRASE + 'x', 'admin')

    def test_changement_exige_la_phrase_actuelle(self):
        self.phrase()
        autre = 'une autre phrase fictive et longue'
        with self.assertRaisesRegex(Stop, 'phrase_actuelle_incorrecte'):
            sv.definir_phrase(self.desk, autre, autre, 'admin', 'mauvaise')
        sv.definir_phrase(self.desk, autre, autre, 'admin', PHRASE)
        self.assertFalse(sv.tester_phrase(self.desk, PHRASE))
        self.assertTrue(sv.tester_phrase(self.desk, autre))

    def test_audit_sans_phrase(self):
        self.phrase()
        rows = self.desk.db.execute("SELECT data FROM audit WHERE action='sauvegarde5688_phrase'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotIn('valbrune', rows[0][0])

    def test_phrase_normalisee(self):
        self.assertEqual(sv._phrase_bytes('e\u0301tude'), sv._phrase_bytes('\u00e9tude'))


# ------------------------------------------------------------------------------------------------ format chiffré
class Format(unittest.TestCase):
    KDF = {'n': 2 ** 12, 'r': 8, 'p': 1, 'sel': base64.b64encode(b'0' * 16).decode()}

    def setUp(self):
        self.cle = sv.deriver(PHRASE, b'0' * 16, 2 ** 12, 8, 1)
        self.clair = os.urandom(sv.BLOC // 2) * 7 + b'fin'

    def chiffrer(self):
        sortie = io.BytesIO()
        w = sv.Ecriture(sortie, self.cle, self.KDF, 'essai')
        for i in range(0, len(self.clair), 70000):
            w.write(self.clair[i:i + 70000])
            self.assertLessEqual(len(w.tampon), sv.BLOC)     # jamais plus d'un bloc en mémoire
        w.close()
        return sortie.getvalue()

    def lire(self, data, **kw):
        lecture = sv.Lecture(io.BytesIO(data), **(kw or {'cle': self.cle}))
        out = lecture.read()
        lecture.jusqu_a_la_fin()
        return out

    def test_aller_retour(self):
        data = self.chiffrer()
        self.assertEqual(self.lire(data), self.clair)
        self.assertEqual(self.lire(data, phrase=PHRASE), self.clair)
        self.assertNotIn(self.clair[:64], data)

    def test_mauvaise_phrase(self):
        with self.assertRaisesRegex(Stop, 'phrase_ou_cle_incorrecte'):
            self.lire(self.chiffrer(), phrase='ce n est pas la bonne phrase')

    def _blocs(self, data):
        taille = struct.unpack('>I', data[len(sv.MAGIC):len(sv.MAGIC) + 4])[0]
        pos = len(sv.MAGIC) + 4 + taille
        tete, blocs = data[:pos], []
        while pos < len(data):
            n = struct.unpack('>I', data[pos:pos + 4])[0]
            blocs.append(data[pos:pos + 4 + n])
            pos += 4 + n
        return tete, blocs

    def test_octet_modifie(self):
        data = bytearray(self.chiffrer())
        data[len(data) // 2] ^= 1
        with self.assertRaises(Stop):
            self.lire(bytes(data))

    def test_fin_coupee_refusee(self):
        tete, blocs = self._blocs(self.chiffrer())
        self.assertGreater(len(blocs), 2)
        with self.assertRaisesRegex(Stop, 'archive_tronquee'):
            self.lire(tete + b''.join(blocs[:-1]))     # coupée pile entre deux blocs : seul l'indicateur de fin le révèle

    def test_blocs_echanges_refuses(self):
        tete, blocs = self._blocs(self.chiffrer())
        with self.assertRaisesRegex(Stop, 'archive_alteree'):
            self.lire(tete + blocs[1] + blocs[0] + b''.join(blocs[2:]))

    def test_donnees_ajoutees_refusees(self):
        with self.assertRaisesRegex(Stop, 'archive_alteree'):
            self.lire(self.chiffrer() + b'x')

    def test_entete_modifie_refuse(self):
        data = self.chiffrer()
        modifie = data.replace(b'"compression": "gzip"', b'"compression": "gzip "'.replace(b'gzip ', b'gzi2'), 1)
        self.assertNotEqual(data, modifie)
        with self.assertRaises(Stop):
            self.lire(modifie)

    def test_parametres_scrypt_bornes(self):
        data = self.chiffrer().replace(b'"n": 4096', b'"n": 99999999', 1)
        taille = struct.pack('>I', struct.unpack('>I', data[len(sv.MAGIC):len(sv.MAGIC) + 4])[0] + 4)
        with self.assertRaisesRegex(Stop, 'archive_inconnue'):
            self.lire(sv.MAGIC + taille + data[len(sv.MAGIC) + 4:], phrase=PHRASE)

    def test_fichier_quelconque(self):
        with self.assertRaisesRegex(Stop, 'archive_inconnue'):
            self.lire(b'pas une archive du tout')


# ------------------------------------------------------------------------------------------------ archive
class Archive(Base):
    def test_copie_coherente_et_restauration(self):
        self.desk.setting('essai5688', 'DURAND')
        self.phrase()
        out = self.sauver()
        self.assertEqual(out['statut'], 'reussie')
        cible = self.extraire()
        db = sqlite3.connect(cible / 'etat/desk.sqlite3')
        self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(json.loads(db.execute("SELECT value FROM settings WHERE key='essai5688'").fetchone()[0]), 'DURAND')
        db.close()

    def test_api_de_sauvegarde_et_pas_copie_du_fichier_ouvert(self):
        ecrivain = sqlite3.connect(Path(self.desk.c['state_dir']) / 'desk.sqlite3', isolation_level=None)
        self.addCleanup(ecrivain.close)
        self.phrase()
        ecrivain.execute('BEGIN IMMEDIATE')
        ecrivain.execute("INSERT OR REPLACE INTO settings VALUES ('non_valide5688','\"KERVILAC\"')")   # transaction ouverte : ne doit pas apparaître
        real = shutil.copyfile
        vus = []
        with patch.object(sv.shutil, 'copyfile', side_effect=lambda a, b, **k: (vus.append(str(a)), real(a, b, **k))[1]):
            desk2 = Desk(self.desk.c)
            self.addCleanup(desk2.db.close)
            desk2.db.execute('PRAGMA busy_timeout=100')
            entrees, _ = sv.plan(desk2)
            travail = Path(self.desk.c['state_dir']) / sv.TRAVAIL
            travail.mkdir(exist_ok=True)
            copie = travail / 'essai'
            self.assertTrue(sv.copie_sqlite(Path(self.desk.c['state_dir']) / 'desk.sqlite3', copie))
        ecrivain.execute('COMMIT')
        self.assertFalse([v for v in vus if v.endswith('.sqlite3')])
        db = sqlite3.connect(copie)
        self.assertIsNone(db.execute("SELECT 1 FROM settings WHERE key='non_valide5688'").fetchone())
        db.close()
        self.assertFalse(Path(str(copie) + '-wal').exists())

    def test_wal_non_reporte_inclus(self):
        lecteur = sqlite3.connect(Path(self.desk.c['state_dir']) / 'desk.sqlite3')
        self.addCleanup(lecteur.close)
        lecteur.execute('SELECT COUNT(*) FROM settings').fetchall()
        lecteur.execute('BEGIN')
        lecteur.execute('SELECT COUNT(*) FROM settings').fetchall()     # empêche le report du journal dans le fichier principal
        self.desk.setting('wal5688', 'MARTIN')
        self.phrase()
        self.sauver()
        lecteur.rollback()
        db = sqlite3.connect(self.extraire() / 'etat/desk.sqlite3')
        self.assertIsNotNone(db.execute("SELECT 1 FROM settings WHERE key='wal5688'").fetchone())
        db.close()

    def test_rien_de_la_sauvegarde_dans_la_sauvegarde(self):
        state = Path(self.desk.c['state_dir'])
        self.phrase()
        self.sauver()
        (state / 'exports5688').mkdir(exist_ok=True)
        for rel in ('exports5688/x.zip', 'voice568-cache/a.wav', 'maj/paquet.tar', 'backups/desk-before-5.6.87-abc.sqlite3', 'desk-before-5.6.87-abc.sqlite3', 'run.lock', 'autre.sqlite3-shm',
                    'navigateur5673/cookies', 'bootstrap-token', 'reports/garde.json'):
            (state / rel).parent.mkdir(parents=True, exist_ok=True)
            (state / rel).write_bytes(b'fictif')
        self.sauver()
        cible = self.extraire()
        noms = {p.relative_to(cible).as_posix() for p in cible.rglob('*') if p.is_file()}
        self.assertIn('etat/reports/garde.json', noms)
        self.assertFalse([n for n in noms if re.search(r'sauvegardes5688|exports5688|\.axbk|voice568|/maj/|backups|before|\.lock|-shm|navigateur|bootstrap', n)], noms)

    def test_cle_et_identifiants_hors_archive(self):
        self.phrase()
        (sv._coffre(self.desk) / 'distant.secret').write_text('fictif')
        self.sauver()
        cible = self.extraire()
        self.assertFalse((cible / 'etat/vault/sauvegarde5688').exists())

    def test_dossier_de_sauvegarde_personnalise_saute(self):
        autre = Path(self.desk.c['state_dir']) / 'mes-archives'
        self.actif(dossier=str(autre))
        (autre / 'note-du-cabinet.txt').write_text('fictif', encoding='utf-8')    # fichier étranger : reste en place, n'entre pas dans l'archive
        self.sauver()
        self.sauver()
        self.assertTrue(list(autre.glob('*.axbk')))
        self.assertTrue((autre / 'note-du-cabinet.txt').exists())
        cible = self.extraire()
        self.assertFalse((cible / 'etat/mes-archives').exists())

    def test_lien_symbolique_jamais_suivi(self):
        state = Path(self.desk.c['state_dir'])
        try:
            (state / 'lien').symlink_to(self.root)
        except OSError:
            self.skipTest('liens symboliques non autorisés sur ce poste')
        self.assertFalse([n for n, _ in sv.plan(self.desk)[0] if n.startswith('etat/lien')])

    def test_archive_illisible_sans_la_phrase(self):
        self.desk.setting('secret5688', 'BERNARD-DONNEE-FICTIVE')
        self.phrase()
        self.sauver()
        data = self.archive().read_bytes()
        self.assertNotIn(b'BERNARD-DONNEE-FICTIVE', data)
        self.assertNotIn(b'SQLite format 3', data)

    def test_extraction_refuse_un_dossier_non_vide(self):
        self.phrase()
        self.sauver()
        cible = Path(tempfile.mkdtemp(dir=self.tmp.name))
        (cible / 'existant').write_text('à garder')
        with self.assertRaisesRegex(Stop, 'dossier_extraction_non_vide'):
            sv.extraire(self.archive(), cible, phrase=PHRASE)
        self.assertEqual((cible / 'existant').read_text(encoding='utf-8'), 'à garder')

    def test_chemin_dangereux_refuse(self):
        for nom in ('../sortie', '/etc/passwd', 'etat/../../x', 'autre/x', 'C:/x', 'etat\\..\\x'):
            with self.assertRaisesRegex(Stop, 'archive_alteree'):
                sv._nom_sur(nom)

    def test_controle_sans_toucher_aux_donnees(self):
        self.phrase()
        self.sauver()
        avant = {p: p.stat().st_mtime_ns for p in Path(self.desk.c['state_dir']).glob('*.sqlite3')}
        out = sv.controler(self.archive(), phrase=PHRASE)
        self.assertTrue(out['ok'])
        self.assertGreater(out['fichiers'], 1)
        self.assertEqual(avant, {p: p.stat().st_mtime_ns for p in Path(self.desk.c['state_dir']).glob('*.sqlite3')})

    def test_controle_detecte_une_alteration(self):
        self.phrase()
        self.sauver()
        chemin = self.archive()
        data = bytearray(chemin.read_bytes())
        data[len(data) // 2] ^= 1
        chemin.write_bytes(bytes(data))
        with self.assertRaises(Stop):
            sv.controler(chemin, phrase=PHRASE)

    def test_controle_detecte_une_archive_coupee(self):
        self.phrase()
        self.sauver()
        chemin = self.archive()
        data = chemin.read_bytes()
        pos = len(sv.MAGIC) + 4 + struct.unpack('>I', data[len(sv.MAGIC):len(sv.MAGIC) + 4])[0]
        dernier = pos
        while pos < len(data):
            dernier = pos
            pos += 4 + struct.unpack('>I', data[pos:pos + 4])[0]
        chemin.write_bytes(data[:dernier])                  # dernier bloc retiré, coupure nette entre deux blocs
        with self.assertRaisesRegex(Stop, 'archive_tronquee'):
            sv.controler(chemin, phrase=PHRASE)

    def test_controle_compare_le_manifeste(self):
        with self.assertRaisesRegex(Stop, 'archive_incomplete'):
            sv._comparer({'etat/a': (1, 'x')}, {'fichiers': []})
        with self.assertRaisesRegex(Stop, 'archive_alteree'):
            sv._comparer({'etat/a': (1, 'x')}, {'fichiers': [{'chemin': 'etat/a', 'taille': 1, 'sha256': 'y'}]})

    def test_verification_apres_ecriture_empeche_tout_effet(self):
        self.actif()
        self.sauver()
        premiere = self.archive()
        ancienne = self.dest() / nom_archive(datetime(2020, 1, 1))
        ancienne.write_bytes(b'x')
        with patch.object(sv, 'controler', side_effect=Stop('archive_alteree')):
            with self.assertRaisesRegex(Stop, 'archive_alteree'):
                self.sauver()
        self.assertTrue(premiere.exists())
        self.assertTrue(ancienne.exists())              # aucune rétention après un échec
        self.assertFalse(list(self.dest().glob('*.part')))
        row = self.desk.db.execute('SELECT statut,erreur FROM sauvegardes5688 ORDER BY debut DESC LIMIT 1').fetchone()
        self.assertEqual((row['statut'], row['erreur']), ('echec', 'archive_alteree'))

    def test_empreinte_relue(self):
        self.phrase()
        self.sauver()
        row = self.desk.db.execute('SELECT sha256,taille FROM sauvegardes5688').fetchone()
        self.assertEqual(hashlib.sha256(self.archive().read_bytes()).hexdigest(), row['sha256'])
        self.assertEqual(self.archive().stat().st_size, row['taille'])
        with self.assertRaisesRegex(Stop, 'archive_alteree'):
            sv.controler(self.archive(), phrase=PHRASE, empreinte='0' * 64)

    def test_aucune_copie_en_clair_ne_reste(self):
        self.phrase()
        self.sauver()
        self.assertFalse((Path(self.desk.c['state_dir']) / sv.TRAVAIL).exists())
        self.assertEqual([p.name for p in self.dest().iterdir() if p.suffix != '.axbk'], [])

    def test_espace_insuffisant(self):
        self.phrase()
        Usage = type('Usage', (), {'free': 1024})
        with patch.object(sv.shutil, 'disk_usage', return_value=Usage):
            with self.assertRaisesRegex(Stop, 'espace_disque_insuffisant'):
                self.sauver()
        self.assertFalse(list(self.dest().glob('*.axbk*')))

    def test_garde_disque_pendant_lecriture(self):
        Usage = type('Usage', (), {'free': 1024})
        garde = sv._garde_disque(Path(self.tmp.name))
        with patch.object(sv.shutil, 'disk_usage', return_value=Usage):
            garde(1000)
            with self.assertRaisesRegex(Stop, 'espace_disque_insuffisant'):
                garde(sv.PROGRES_OCTETS)

    def test_volume_maximal(self):
        self.actif(taille_max_go=1)
        (Path(self.desk.c['state_dir']) / 'gros.bin').write_bytes(b'0')
        with patch.object(sv.os.path, 'getsize', return_value=0), patch.object(sv, '_espace', side_effect=Stop('sauvegarde_trop_volumineuse')):
            with self.assertRaisesRegex(Stop, 'sauvegarde_trop_volumineuse'):
                self.sauver()
        cfg = {**sv.config(self.desk), 'taille_max_go': 0}
        with self.assertRaisesRegex(Stop, 'sauvegarde_trop_volumineuse'):
            sv._espace(self.desk, cfg, self.dest(), sv.plan(self.desk)[0])

    def test_demande_darret_respectee(self):
        self.phrase()
        with patch('agent.pause5629.checkpoint', side_effect=Stop('operation_annulee')):
            with self.assertRaisesRegex(Stop, 'operation_annulee'):
                self.sauver()
        self.assertFalse(list(self.dest().glob('*.axbk*')))

    def test_travail_interrompu_signale(self):
        self.phrase()
        sv.schema(self.desk)
        self.desk.db.execute("INSERT INTO sauvegardes5688(id,debut,declencheur,statut) VALUES ('a','2026-10-30T00:00:00+00:00','automatique','en_cours')")
        self.desk.db.commit()
        self.sauver()
        self.assertEqual(self.desk.db.execute("SELECT statut FROM sauvegardes5688 WHERE id='a'").fetchone()[0], 'interrompue')

    def test_historique_borne(self):
        self.phrase()
        with patch.object(sv, 'HISTORIQUE', 3):
            for _ in range(5):
                self.sauver()
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM sauvegardes5688').fetchone()[0], 3)

    def test_ligne_de_commande(self):
        self.phrase()
        self.sauver()
        fichier = Path(self.tmp.name) / 'phrase.txt'
        fichier.write_text(PHRASE, encoding='utf-8')
        cible = Path(self.tmp.name) / 'cli'
        with patch('sys.stdout', io.StringIO()):
            self.assertEqual(sv.main(['controler', str(self.archive()), '--phrase-fichier', str(fichier)]), 0)
            self.assertEqual(sv.main(['extraire', str(self.archive()), str(cible), '--phrase-fichier', str(fichier)]), 0)
        self.assertTrue((cible / 'etat/desk.sqlite3').is_file())
        fichier.write_text('ce n est pas la bonne phrase', encoding='utf-8')
        with patch('sys.stderr', io.StringIO()):
            self.assertEqual(sv.main(['controler', str(self.archive()), '--phrase-fichier', str(fichier)]), 1)


# ------------------------------------------------------------------------------------------------ rétention
class Retention(Base):
    def jours(self, n, depart=datetime(2026, 10, 31)):
        return [nom_archive(depart - timedelta(days=i), n=i) for i in range(n)]

    def test_7_quotidiennes_4_hebdomadaires(self):
        noms = self.jours(120)
        garde = sv.a_conserver(noms, 7, 4)
        self.assertTrue(set(noms[:7]) <= garde)
        self.assertEqual(len(garde), 11)
        self.assertIn(noms[0], garde)
        plus_ancienne = min(sv._date(n) for n in garde)
        self.assertGreater(datetime(2026, 10, 31) - plus_ancienne, timedelta(days=25))     # les hebdomadaires remontent plus loin que les quotidiennes
        self.assertLessEqual(datetime(2026, 10, 31) - plus_ancienne, timedelta(days=36))

    def test_une_par_jour(self):
        jour = datetime(2026, 10, 31)
        noms = [nom_archive(jour, '0200', 1), nom_archive(jour, '0900', 2), nom_archive(jour, '2300', 3)]
        self.assertEqual(sv.a_conserver(noms, 7, 0), {noms[2]})

    def test_reglable(self):
        self.assertEqual(len(sv.a_conserver(self.jours(30), 2, 0)), 2)
        self.assertEqual(len(sv.a_conserver(self.jours(3), 7, 4)), 3)
        self.assertEqual(len(sv.a_conserver(self.jours(5), 0, 0)), 1)      # jamais tout

    def test_seuls_les_noms_exacts_sont_supprimes(self):
        dest = Path(self.tmp.name) / 'dest'
        dest.mkdir()
        noms = self.jours(30)
        for n in noms:
            (dest / n).write_bytes(b'x')
        etrangers = ('notes.txt', 'axiorhub-sauvegarde.axbk', 'axiorhub-2026-x.axbk', 'desk.sqlite3', noms[-1] + '.bak', 'axiorhub-20261001-0200-XYZ.axbk')
        for n in etrangers:
            (dest / n).write_bytes(b'x')
        (dest / 'axiorhub-20261001-0200-000000000abc.axbk.part').write_bytes(b'x')
        retires = sv.purger_local(dest, {'quotidiennes': 7, 'hebdomadaires': 4}, noms[0])
        self.assertTrue(retires)
        for n in etrangers:
            self.assertTrue((dest / n).exists(), n)
        self.assertLessEqual(len([p for p in dest.iterdir() if sv.NOM.match(p.name)]), 11)
        self.assertFalse(list(dest.glob('*.part')))

    def test_rien_ne_part_sans_larchive_du_jour(self):
        dest = Path(self.tmp.name) / 'dest'
        dest.mkdir()
        noms = self.jours(30)
        for n in noms:
            (dest / n).write_bytes(b'x')
        self.assertEqual(sv.purger_local(dest, {'quotidiennes': 1, 'hebdomadaires': 0}, 'axiorhub-20261101-0200-00000000abcd.axbk'), [])
        self.assertEqual(sv.purger_local(dest, {'quotidiennes': 1, 'hebdomadaires': 0}, ''), [])
        self.assertEqual(len(list(dest.iterdir())), 30)

    def test_volume_stable_dans_la_duree(self):
        self.actif(quotidiennes=3, hebdomadaires=1)
        for jour in range(25):
            moment = datetime(2026, 9, 1) + timedelta(days=jour)

            class Fige(datetime):
                @classmethod
                def now(cls, tz=None):
                    return moment.replace(tzinfo=tz)
            with patch.object(sv, 'datetime', Fige):
                self.sauver()
        self.assertLessEqual(len(list(self.dest().glob('*.axbk'))), 4)

    def test_lien_symbolique_de_dossier_jamais_compte(self):
        self.assertEqual(sv.a_conserver(['autre.axbk'], 7, 4), set())


# ------------------------------------------------------------------------------------------------ faux services
class FauxDAV(BaseHTTPRequestHandler):
    fichiers, corrompre, journal, dossier_cree = {}, False, [], False

    def log_message(self, *a):
        pass

    def _rep(self, code, data=b'', entetes=()):
        self.send_response(code)
        for k, v in entetes:
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _nom(self):
        return unquote(urlsplit(self.path).path).rstrip('/').rsplit('/', 1)[-1]

    def _corps(self):
        return self.rfile.read(int(self.headers.get('Content-Length') or 0))

    def do_MKCOL(self):
        self._corps()
        cls = type(self)
        code = 405 if cls.dossier_cree else 201
        cls.dossier_cree = True
        self._rep(code)

    def do_PUT(self):
        type(self).journal.append(('PUT', self._nom(), self.headers.get('Authorization', '')))
        self.fichiers[self._nom()] = self._corps()
        self._rep(201)

    def do_GET(self):
        data = self.fichiers.get(self._nom())
        if data is None:
            return self._rep(404)
        self._rep(200, data + (b'!' if self.corrompre else b''))

    def do_MOVE(self):
        cible = unquote(urlsplit(self.headers['Destination']).path).rsplit('/', 1)[-1]
        self.fichiers[cible] = self.fichiers.pop(self._nom())
        self._rep(201)

    def do_DELETE(self):
        self.fichiers.pop(self._nom(), None)
        self._rep(204)

    def do_PROPFIND(self):
        self._corps()
        lignes = ''.join('<d:response><d:href>/dav/%s</d:href></d:response>' % n for n in ['', *self.fichiers])
        self._rep(207, ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">%s</d:multistatus>' % lignes).encode(), [('Content-Type', 'application/xml')])


class FauxS3(BaseHTTPRequestHandler):
    objets, parties, corrompre, erreurs, abandons, secret, refuser_part = {}, {}, False, [], [], 'cle-secrete-fictive', False

    def log_message(self, *a):
        pass

    def _rep(self, code, data=b'', entetes=()):
        self.send_response(code)
        for k, v in entetes:
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _verifier(self, corps):
        """Refait la signature à partir de ce qui a réellement été reçu ; le service compare aussi l'empreinte annoncée au corps reçu."""
        p = urlsplit(self.path)
        empreinte = self.headers['X-Amz-Content-Sha256']
        if corps is not None and hashlib.sha256(corps).hexdigest() != empreinte:
            type(self).erreurs.append('empreinte')
            return False
        entetes = {'host': self.headers['Host'], 'x-amz-content-sha256': empreinte, 'x-amz-date': self.headers['X-Amz-Date']}
        requete = [tuple(x.split('=', 1)) for x in p.query.split('&')] if p.query else []
        attendu = st.signature_s3(self.command, entetes['host'], p.path, requete, entetes, empreinte, entetes['x-amz-date'], 'eu-west-3', 'ACCES-FICTIF', self.secret)
        if attendu != self.headers['Authorization']:
            type(self).erreurs.append('signature')
            return False
        return True

    def _cle(self):
        return unquote(urlsplit(self.path).path).split('/', 2)[2] if unquote(urlsplit(self.path).path).count('/') >= 2 else ''

    def _q(self):
        return dict(parse_qsl(urlsplit(self.path).query, keep_blank_values=True))

    def do_PUT(self):
        corps = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        if not self._verifier(corps):
            return self._rep(403)
        q = self._q()
        if 'partNumber' in q:
            if self.refuser_part:
                return self._rep(500)
            self.parties.setdefault(q['uploadId'], {})[int(q['partNumber'])] = corps
            return self._rep(200, entetes=[('ETag', '"p%s"' % q['partNumber'])])
        self.objets[self._cle()] = corps
        self._rep(200)

    def do_POST(self):
        corps = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        if not self._verifier(corps):
            return self._rep(403)
        q = self._q()
        if 'uploads' in q:
            return self._rep(200, b'<?xml version="1.0"?><InitiateMultipartUploadResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><UploadId>u1</UploadId></InitiateMultipartUploadResult>')
        self.objets[self._cle()] = b''.join(v for _, v in sorted(self.parties.pop(q['uploadId']).items()))
        self._rep(200, b'<?xml version="1.0"?><CompleteMultipartUploadResult/>')

    def do_GET(self):
        if not self._verifier(None):
            return self._rep(403)
        q = self._q()
        if q.get('list-type') == '2':
            cles = ''.join('<Contents><Key>%s</Key></Contents>' % k for k in self.objets if k.startswith(q.get('prefix', '')))
            return self._rep(200, ('<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">%s</ListBucketResult>' % cles).encode())
        data = self.objets.get(self._cle())
        if data is None:
            return self._rep(404)
        self._rep(200, data + (b'!' if self.corrompre else b''))

    def do_DELETE(self):
        if not self._verifier(None):
            return self._rep(403)
        q = self._q()
        if 'uploadId' in q:
            type(self).abandons.append(q['uploadId'])
        else:
            self.objets.pop(self._cle(), None)
        self._rep(204)


class Distant(Base):
    def serveur(self, classe):
        srv = ThreadingHTTPServer(('127.0.0.1', 0), classe)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return 'http://127.0.0.1:%d' % srv.server_address[1]

    def dav(self):
        FauxDAV.fichiers, FauxDAV.corrompre, FauxDAV.journal, FauxDAV.dossier_cree = {}, False, [], False
        url = self.serveur(FauxDAV) + '/dav'
        self.actif()
        st.enregistrer(self.desk, {'type': 'webdav', 'url': url, 'identifiant': 'cabinet', 'secret': 'mot-de-passe-fictif', 'autorise': True}, 'admin')
        return url

    def s3(self):
        FauxS3.objets, FauxS3.parties, FauxS3.corrompre, FauxS3.erreurs, FauxS3.abandons, FauxS3.refuser_part = {}, {}, False, [], [], False
        url = self.serveur(FauxS3)
        self.actif()
        st.enregistrer(self.desk, {'type': 's3', 'url': url, 'identifiant': 'ACCES-FICTIF', 'secret': FauxS3.secret, 'seau': 'cabinet-sauvegardes', 'region': 'eu-west-3', 'prefixe': 'axiorhub', 'autorise': True}, 'admin')
        return url

    def test_webdav_depose_et_relit(self):
        self.dav()
        out = self.sauver()
        self.assertEqual(out['statut'], 'reussie')
        nom = self.archive().name
        self.assertEqual(list(FauxDAV.fichiers), [nom])                       # plus aucun nom provisoire
        self.assertEqual(FauxDAV.fichiers[nom], self.archive().read_bytes())
        self.assertEqual(FauxDAV.journal[0][1], nom + '.part')
        self.assertTrue(FauxDAV.journal[0][2].startswith('Basic '))
        row = self.desk.db.execute('SELECT distant,statut FROM sauvegardes5688').fetchone()
        self.assertIn('vérifiée', row['distant'])
        self.assertTrue(self.desk.settings(sv.REUSSITE))

    def test_webdav_copie_alteree(self):
        self.dav()
        FauxDAV.corrompre = True
        out = self.sauver()
        self.assertEqual(out['statut'], 'partielle')
        self.assertEqual(FauxDAV.fichiers, {})
        self.assertTrue(self.archive().exists())
        self.assertEqual(self.desk.settings(sv.REUSSITE, ''), '')             # l'alerte finira par se déclencher
        row = self.desk.db.execute('SELECT erreur FROM sauvegardes5688').fetchone()
        self.assertEqual(row['erreur'], 'second_emplacement:verification_distante_echouee')
        self.assertIn('Second emplacement', rv.motif(row['erreur']))

    def test_retention_distante(self):
        self.dav()
        anciens = [nom_archive(datetime(2026, 1, 1) + timedelta(days=i), n=i) for i in range(30)]
        for n in anciens:
            FauxDAV.fichiers[n] = b'x'
        FauxDAV.fichiers['photo-du-cabinet.jpg'] = b'x'
        FauxDAV.fichiers['axiorhub-20260101-0200-000000000abc.axbk.part'] = b'x'
        self.sauver()
        noms = set(FauxDAV.fichiers)
        self.assertIn('photo-du-cabinet.jpg', noms)
        self.assertIn(self.archive().name, noms)
        self.assertFalse([n for n in noms if n.endswith('.part')])
        self.assertLessEqual(len([n for n in noms if sv.NOM.match(n)]), 11)

    def test_liste_distante_fausse_ne_supprime_rien(self):
        class Client:
            supprimes = []

            def lister(self):
                return [nom_archive(datetime(2026, 1, 1) + timedelta(days=i), n=i) for i in range(30)]

            def supprimer(self, nom):
                self.supprimes.append(nom)
        client = Client()
        self.assertEqual(st.retention(client, sv.DEFAUTS, 'axiorhub-20261101-0200-00000000abcd.axbk'), [])
        self.assertEqual(client.supprimes, [])

    def test_s3_signature_reference(self):
        vide = hashlib.sha256(b'').hexdigest()
        entetes = {'host': 'examplebucket.s3.amazonaws.com', 'range': 'bytes=0-9', 'x-amz-content-sha256': vide, 'x-amz-date': '20130524T000000Z'}
        sortie = st.signature_s3('GET', entetes['host'], '/test.txt', [], entetes, vide, '20130524T000000Z', 'us-east-1', 'AKIAIOSFODNN7EXAMPLE', 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY')
        self.assertTrue(sortie.endswith('Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41'), sortie)

    def test_s3_depose_relit_et_retient(self):
        self.s3()
        FauxS3.objets['axiorhub/' + nom_archive(datetime(2026, 1, 1))] = b'x'
        FauxS3.objets['axiorhub/autre.txt'] = b'x'
        self.assertEqual(self.sauver()['statut'], 'reussie')
        self.assertEqual(FauxS3.erreurs, [])
        self.assertEqual(FauxS3.objets['axiorhub/' + self.archive().name], self.archive().read_bytes())
        self.assertIn('axiorhub/autre.txt', FauxS3.objets)

    def test_s3_empreinte_refusee_par_le_service(self):
        self.s3()
        client = st._client(self.desk, sv.config(self.desk))
        fichier = Path(self.tmp.name) / 'a.bin'
        fichier.write_bytes(b'contenu')
        with self.assertRaises(Stop):
            client.deposer(fichier, 7, '0' * 64, 'axiorhub-20261101-0200-00000000abcd.axbk')
        self.assertEqual(FauxS3.erreurs, ['empreinte'])

    def test_s3_copie_alteree(self):
        self.s3()
        FauxS3.corrompre = True
        self.assertEqual(self.sauver()['statut'], 'partielle')
        self.assertEqual(FauxS3.objets, {})

    def test_s3_plusieurs_parties(self):
        self.s3()
        with patch.object(st, 'TRANCHE', 1024):
            self.assertEqual(self.sauver()['statut'], 'reussie')
        self.assertEqual(FauxS3.erreurs, [])
        self.assertEqual(FauxS3.objets['axiorhub/' + self.archive().name], self.archive().read_bytes())
        self.assertEqual(FauxS3.parties, {})

    def test_s3_envoi_abandonne_proprement(self):
        self.s3()
        FauxS3.refuser_part = True
        with patch.object(st, 'TRANCHE', 1024):
            self.assertEqual(self.sauver()['statut'], 'partielle')
        self.assertEqual(FauxS3.abandons, ['u1'])                             # aucune partie laissée à payer sur le service

    def test_essai_de_lemplacement(self):
        self.dav()
        self.assertTrue(st.tester(self.desk, 'admin')['ok'])
        self.assertEqual(FauxDAV.fichiers, {})

    def test_identifiants_dans_le_coffre_seulement(self):
        self.dav()
        reglages = json.dumps(self.desk.settings(sv.CONFIG))
        self.assertNotIn('mot-de-passe-fictif', reglages)
        self.assertNotIn('"cabinet"', reglages)
        brut = (sv._coffre(self.desk) / 'distant.secret').read_text()
        self.assertTrue(brut.startswith('AXIORHUB-VAULT-V1:'))
        self.assertEqual(st.identifiants(self.desk)['secret'], 'mot-de-passe-fictif')
        self.assertNotIn('mot-de-passe-fictif', rv.sauvegarde_html(self.desk))

    def test_secret_vide_conserve_lancien(self):
        url = self.dav()
        st.enregistrer(self.desk, {'type': 'webdav', 'url': url, 'autorise': True}, 'admin')
        self.assertEqual(st.identifiants(self.desk)['identifiant'], 'cabinet')

    def test_depot_automatique_soumis_a_autorisation(self):
        url = self.dav()
        with self.assertRaisesRegex(Stop, 'second_emplacement_non_autorise'):
            st.enregistrer(self.desk, {'type': 'webdav', 'url': url, 'identifiant': 'x', 'secret': 'y'}, 'admin')
        cfg = sv.config(self.desk)
        cfg['distant'].pop('autorise_par')
        self.desk.setting(sv.CONFIG, cfg)
        self.assertEqual(self.sauver()['statut'], 'partielle')
        self.assertEqual(FauxDAV.fichiers, {})

    def test_https_obligatoire(self):
        self.phrase()
        for url in ('http://nas.example.test/dav', 'https://user:mdp@nas.example.test/dav', 'https://nas.example.test/dav?x=1', 'ftp://nas.example.test', ''):
            with self.assertRaises(Stop, msg=url):
                st.enregistrer(self.desk, {'type': 'webdav', 'url': url, 'identifiant': 'x', 'secret': 'y', 'autorise': True}, 'admin')

    def test_redirection_non_suivie(self):
        from agent.common import NoRedirect
        self.assertTrue(any(isinstance(h, NoRedirect) for h in st.Liaison('https://nas.example.test').http.opener.handlers))

    def test_reglages_s3_invalides(self):
        self.phrase()
        for champs in ({'seau': 'A B'}, {'seau': 'ok-seau', 'prefixe': '../haut'}, {'seau': 'ok-seau', 'region': 'é'}):
            with self.assertRaisesRegex(Stop, 'second_emplacement_invalide'):
                st.enregistrer(self.desk, {'type': 's3', 'url': 'https://s3.example.test', 'identifiant': 'x', 'secret': 'y', 'autorise': True, **champs}, 'admin')

    def test_retrait_de_lemplacement_efface_les_identifiants(self):
        self.dav()
        st.enregistrer(self.desk, {'type': ''}, 'admin')
        self.assertFalse((sv._coffre(self.desk) / 'distant.secret').exists())
        self.assertEqual(self.sauver()['statut'], 'reussie')


# ------------------------------------------------------------------------------------------------ planification
class Planification(Base):
    def test_due(self):
        self.actif(heure=2)
        tz = sv._tz(self.desk)
        nuit = datetime(2026, 11, 2, 1, 0, tzinfo=tz)
        matin = datetime(2026, 11, 2, 3, 0, tzinfo=tz)
        self.desk.setting(sv.REUSSITE, datetime(2026, 11, 1, 3, 0, tzinfo=tz).isoformat())
        self.assertFalse(sv.due(self.desk, nuit))
        self.assertTrue(sv.due(self.desk, matin))
        self.desk.setting(sv.REUSSITE, matin.isoformat())
        self.assertFalse(sv.due(self.desk, matin + timedelta(hours=4)))         # une par jour

    def test_rattrapage_poste_eteint(self):
        self.actif(heure=23)
        tz = sv._tz(self.desk)
        now = datetime(2026, 11, 3, 9, 0, tzinfo=tz)
        self.desk.setting(sv.REUSSITE, (now - timedelta(hours=31)).isoformat())
        self.assertTrue(sv.due(self.desk, now))
        self.desk.setting(sv.REUSSITE, (now - timedelta(hours=20)).isoformat())
        self.assertFalse(sv.due(self.desk, now))

    def test_nouvel_essai_apres_six_heures(self):
        self.actif(heure=0)
        now = datetime.now(timezone.utc)
        self.assertTrue(sv.schedule(self.desk, now.timestamp()))
        self.assertFalse(sv.schedule(self.desk, now.timestamp() + 60))
        self.assertFalse(sv.due(self.desk, now + timedelta(hours=5)))
        self.assertTrue(sv.due(self.desk, now + timedelta(hours=7)))

    def test_desactivee(self):
        self.phrase()
        self.assertFalse(sv.due(self.desk))
        self.assertFalse(sv.schedule(self.desk))

    def test_file_chargee_reporte_sans_abandonner(self):
        self.actif(heure=0)
        now = datetime.now(timezone.utc)
        self.desk.setting(sv.REUSSITE, (now - timedelta(hours=25)).isoformat())
        with patch.object(cadence5662, 'busy', return_value='file_chargee'):
            self.assertFalse(sv.schedule(self.desk))
        self.desk.setting(sv.REUSSITE, (now - timedelta(hours=31)).isoformat())
        with patch.object(cadence5662, 'busy', return_value='file_chargee'):
            self.assertTrue(sv.schedule(self.desk))

    def test_boucle_des_automatismes_meme_en_pause_de_lia(self):
        self.actif(heure=0)
        self.desk.setting('missions567:pause', True)
        operations.automation_tick(self.desk)
        row = self.desk.db.execute("SELECT origin,status FROM jobs WHERE kind='sauvegarde5688'").fetchone()
        self.assertEqual((row['origin'], row['status']), ('control', 'pending'))

    def test_une_erreur_narrete_pas_la_boucle(self):
        with patch.object(sv, 'schedule', side_effect=RuntimeError('fictif')):
            with patch.object(operations, '_automation_tick_5642', return_value='suite') as suite:
                operations.automation_tick(self.desk)
        suite.assert_called_once()

    def test_travail_sans_verrou_general(self):
        self.assertIn('sauvegarde5688', NON_EXCLUSIVE_JOBS)
        self.assertIn('export5688', CONTROL_JOBS)
        self.actif(heure=0)
        sv.schedule(self.desk)
        with run_lock(self.desk.c):                                             # une longue rédaction en cours : la sauvegarde passe quand même
            self.assertTrue(self.desk.work_once())
        row = self.desk.db.execute("SELECT status,result FROM jobs WHERE kind='sauvegarde5688'").fetchone()
        self.assertEqual(row['status'], 'done', row['result'])
        self.assertTrue(self.archive().exists())

    def test_travail_passe_pendant_la_pause_de_lia(self):
        self.actif(heure=0)
        sv.schedule(self.desk)
        self.desk.setting('missions567:pause', True)
        self.assertTrue(self.desk.work_once())
        self.assertEqual(self.desk.db.execute("SELECT status FROM jobs WHERE kind='sauvegarde5688'").fetchone()[0], 'done')

    def test_un_seul_travail_a_la_fois(self):
        self.phrase()
        with sv.verrou(self.desk):
            desk2 = Desk(self.desk.c)
            self.addCleanup(desk2.db.close)
            with self.assertRaisesRegex(Stop, 'traitement_deja_en_cours'):
                sv.executer(desk2, 'manuel')
            job = ex.demander(desk2, {}, 'admin')
            with self.assertRaisesRegex(Stop, 'traitement_deja_en_cours'):
                ex.executer(desk2, job['id'])
        self.assertEqual(self.desk.db.execute("SELECT statut FROM exports5688").fetchone()[0], 'en_attente')

    def test_travail_occupe_repris_sans_echec(self):
        self.actif(heure=0)
        sv.schedule(self.desk)
        desk2 = Desk(self.desk.c)
        self.addCleanup(desk2.db.close)
        with sv.verrou(desk2):
            self.assertFalse(self.desk.work_once())
        row = self.desk.db.execute("SELECT status,attempts FROM jobs WHERE kind='sauvegarde5688'").fetchone()
        self.assertEqual((row['status'], row['attempts']), ('pending', 0))

    def test_duree_maximale_adaptee(self):
        from agent.blocage5660 import limit_for
        self.assertGreaterEqual(limit_for('sauvegarde5688'), 240)


class Alerte(Base):
    def test_jamais_en_place(self):
        self.assertEqual(sv.alerte(self.desk), 'non_configuree')
        self.assertIn('n’est pas en place', rv.banner_html(self.desk, '/p'))

    def test_recente_sans_alerte(self):
        self.actif()
        self.desk.setting(sv.REUSSITE, self.desk.now())
        self.assertEqual(sv.alerte(self.desk), '')
        self.assertEqual(rv.banner_html(self.desk, '/p'), '')

    def test_plus_de_48_heures(self):
        self.actif()
        now = datetime.now(timezone.utc)
        self.desk.setting(sv.REUSSITE, (now - timedelta(hours=47)).isoformat())
        self.assertEqual(sv.alerte(self.desk, now), '')
        self.desk.setting(sv.REUSSITE, (now - timedelta(hours=49)).isoformat())
        self.assertEqual(sv.alerte(self.desk, now), 'ancienne')
        html = rv.banner_html(self.desk, '/p')
        self.assertIn('Dernière sauvegarde réussie', html)
        self.assertIn('/p/parametres?rubrique=poste', html)

    def test_activee_sans_aucune_reussite(self):
        self.actif()
        now = datetime.now(timezone.utc)
        self.assertEqual(sv.alerte(self.desk, now), '')
        self.assertEqual(sv.alerte(self.desk, now + timedelta(hours=50)), 'ancienne')

    def test_desactivee_par_ladministrateur(self):
        self.actif()
        sv.enregistrer(self.desk, {'actif': False}, 'admin')
        self.assertEqual(sv.alerte(self.desk), '')

    def test_sur_aujourdhui(self):
        from agent import cockpit530
        self.assertIn('rev88-banner', cockpit530.page(self.desk, '/p', 'csrf', 'cabinet'))
        self.actif()
        self.desk.setting(sv.REUSSITE, self.desk.now())
        self.assertNotIn('rev88-banner', cockpit530.page(self.desk, '/p', 'csrf', 'cabinet'))


# ------------------------------------------------------------------------------------------------ export
SECRET_API = 'sk-' + 'fictif-secret-' * 3
JETON = 'Bearer ' + 'jetonfictif' * 3


class ExportBase(Base):
    def setUp(self):
        super().setUp()
        self.mid = 'DOS-001'
        self.remplir()

    def remplir(self):
        from agent import crm5636, facturation5653, messagerie5650, metier500, outils5636, modele5651, taches5614, agenda5634, competences5632, modeles5684
        desk = self.desk
        for module in (crm5636, facturation5653, metier500):
            for nom in ('ensure_schema', 'schema', 'ensure', '_schema'):
                fn = getattr(module, nom, None)
                if fn:
                    try:
                        fn(desk)
                    except TypeError:
                        pass
                    break
        db = desk.db
        db.executescript('''CREATE TABLE IF NOT EXISTS contacts5636(id TEXT PRIMARY KEY, kind TEXT, category TEXT, name TEXT, first_name TEXT, company TEXT, function TEXT, email TEXT, phone TEXT, address TEXT, siren TEXT, barreau TEXT, notes TEXT, source TEXT, archived INTEGER, created TEXT, updated TEXT);
          CREATE TABLE IF NOT EXISTS contact_links5636(contact TEXT, matter TEXT, role TEXT, created TEXT);
          CREATE TABLE IF NOT EXISTS b53_documents(id TEXT PRIMARY KEY, kind TEXT, matter TEXT, snapshot_json TEXT, api_token TEXT);
          CREATE TABLE IF NOT EXISTS b53_payments(id TEXT PRIMARY KEY, document TEXT, amount_cents INTEGER);
          CREATE TABLE IF NOT EXISTS b53_entities(id TEXT PRIMARY KEY, identity_json TEXT, fiscal_json TEXT, terms_json TEXT);
          CREATE TABLE IF NOT EXISTS time500_entries(id TEXT PRIMARY KEY, matter TEXT, label TEXT, minutes INTEGER);
          CREATE TABLE IF NOT EXISTS agenda5634(id TEXT PRIMARY KEY, title TEXT, matter TEXT, starts TEXT);
          CREATE TABLE IF NOT EXISTS fils5650(id INTEGER PRIMARY KEY, title TEXT, matter TEXT);
          CREATE TABLE IF NOT EXISTS messages5650(id INTEGER PRIMARY KEY, thread INTEGER, text TEXT);
          CREATE TABLE IF NOT EXISTS memos5636(id TEXT PRIMARY KEY, title TEXT, matter TEXT);
          CREATE TABLE IF NOT EXISTS skills5632(id TEXT, version TEXT, name TEXT, PRIMARY KEY(id,version));
          CREATE TABLE IF NOT EXISTS cabinet_templates_v330(id TEXT PRIMARY KEY, label TEXT);''')

        def colonnes(table):
            return [r[1] for r in db.execute('PRAGMA table_info("%s")' % table)]

        def inserer(table, **valeurs):
            cols = colonnes(table)
            remplis = {c: '' for c in cols}
            remplis.update({k: v for k, v in valeurs.items() if k in cols})
            infos = {r[1]: r for r in db.execute('PRAGMA table_info("%s")' % table)}
            for c, info in infos.items():
                if remplis[c] == '' and 'INT' in (info[2] or '').upper():
                    remplis[c] = 0
            db.execute('INSERT INTO "%s"(%s) VALUES (%s)' % (table, ','.join('"%s"' % c for c in cols), ','.join('?' * len(cols))), tuple(remplis.values()))
        self.inserer = inserer
        inserer('contacts5636', id='c1', kind='personne', category='client', name='DURAND', notes='=1+1', created='x', updated='x')
        inserer('contacts5636', id='c2', kind='personne', category='client', name='ZORVAL', notes=JETON, created='x', updated='x')
        inserer('contacts5636', id='c3', kind='personne', category='confrere', name='BERNARD', notes='Note : ' + JETON, created='x', updated='x')
        inserer('contact_links5636', contact='c1', matter=self.mid, role='client', created='x')
        inserer('contact_links5636', contact='c2', matter='DOS-RES', role='client', created='x')
        inserer('b53_documents', id='f1', request_key='r1', number='F1', issue_key='i1', kind='facture', matter=self.mid, state='emise', snapshot_json=json.dumps({'total': 1200, 'client': {'nom': 'DURAND', 'accessToken': SECRET_API}}), created='x', updated='x', created_by='x')
        inserer('b53_documents', id='f2', request_key='r2', number='F2', issue_key='i2', kind='facture', matter='DOS-RES', state='emise', snapshot_json='{"client":"KERVILAC"}', created='x', updated='x', created_by='x')
        inserer('b53_payments', id='p1', document='f1', amount_cents=1200, day='x', method='virement', source='x', actor='x', created='x')
        inserer('b53_payments', id='p2', document='f2', amount_cents=7777, day='x', method='KERVILAC-PAIEMENT', source='x', actor='x', created='x')
        inserer('b53_entities', id='cabinet', identity_json=json.dumps({'nom': 'Cabinet fictif', 'password': 'mot-de-passe-fictif-123', 'client_secret': 'secret-fictif-123'}), updated='x')
        inserer('time500_entries', id='t1', matter=self.mid, label='Rédaction', minutes=30, day='x', origin='x', validated_at='x', created='x')
        inserer('time500_entries', id='t2', matter='DOS-RES', label='KERVILAC-TEMPS', minutes=30, day='x', origin='x', validated_at='x', created='x')
        inserer('agenda5634', id='a1', uid='u', kind='rdv', title='Audience RG 24/01234', matter=self.mid, starts='x', ends='x', created='x', updated='x')
        inserer('fils5650', id=1, kind='dossier', title='Fil DURAND', matter=self.mid, created_by='x', created='x', updated='x')
        inserer('fils5650', id=2, kind='dossier', title='Fil réservé', matter='DOS-RES', created_by='x', created='x', updated='x')
        inserer('messages5650', id=1, thread=1, author='x', author_name='x', author_role='x', text='Bonjour', at='x')
        inserer('messages5650', id=2, thread=2, author='x', author_name='x', author_role='x', text='KERVILAC-MESSAGE', at='x')
        inserer('memos5636', id='m1', title='Mémo', question='q', body='b', status='s', created='x', updated='x', matter=self.mid)
        inserer('skills5632', id='s1', version='1', origin='x', owner='x', scope='x', name='Compétence fictive', description='d', manifest='{}', instructions='i', sha256='x', parent='', created='x')
        inserer('cabinet_templates_v330', id='tpl1', label='Courrier type', version=1, sha256='x', status='x', placeholders='[]', pages='[]', created='x', approved='x')
        inserer('tasks', id='ta1', matter=self.mid, title='Relancer MARTIN', status='open', created='x')
        db.execute("INSERT INTO notes VALUES ('k1',?,'x')", (json.dumps({'result': {'texte': 'Note fictive'}, 'created_at': 'x'}),))
        db.execute("INSERT INTO notes VALUES ('k2',?,'x')", (json.dumps({'result': {'texte': 'KERVILAC-NOTE', 'matter': 'DOS-RES'}}),))
        db.commit()
        membres5650.set_reserve(self.desk.c['state_dir'], 'DOS-RES', ['avocat@example.test'])

    def construire(self, **options):
        chemin = Path(self.tmp.name) / ('export-%d.zip' % len(list(Path(self.tmp.name).glob('export-*.zip'))))
        meta, n = ex.construire(self.desk, chemin, options)
        return chemin, meta

    def lire(self, chemin):
        with zipfile.ZipFile(chemin) as z:
            return {n: z.read(n) for n in z.namelist()}


class Export(ExportBase):
    def test_domaines_et_formats(self):
        chemin, _ = self.construire()
        fichiers = self.lire(chemin)
        for domaine in ('dossiers', 'contacts', 'factures', 'temps', 'agenda', 'taches', 'notes', 'messagerie_interne', 'modeles', 'competences', 'documents'):
            self.assertTrue([n for n in fichiers if n.startswith(domaine + '/') and n.endswith('.json')], domaine)
            self.assertTrue([n for n in fichiers if n.startswith(domaine + '/') and n.endswith('.csv')], domaine)
        self.assertIn('MANIFEST.json', fichiers)
        self.assertIn('LISEZMOI.txt', fichiers)
        registre = json.loads(fichiers['dossiers/registre.json'])
        self.assertTrue(registre and 'id' in registre[0])
        factures = json.loads(fichiers['factures/documents_de_facturation.json'])
        self.assertEqual(factures[0]['snapshot_json']['total'], 1200)                       # JSON stocké en texte : relu, lisible
        lignes = list(csv.reader(io.StringIO(fichiers['contacts/contacts.csv'].decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(lignes[1][lignes[0].index('name')], 'DURAND')
        self.assertEqual(len(lignes), 3)                                                     # DURAND et BERNARD ; ZORVAL suit son dossier réservé
        self.assertTrue(fichiers['contacts/contacts.csv'].startswith(b'\xef\xbb\xbf'))        # accents corrects à l'ouverture dans un tableur

    def test_manifeste(self):
        chemin, _ = self.construire()
        fichiers = self.lire(chemin)
        manifeste = json.loads(fichiers['MANIFEST.json'])
        self.assertEqual(manifeste['format'], ex.FORMAT)
        presents = set(fichiers) - {'MANIFEST.json'}
        self.assertEqual({f['chemin'] for f in manifeste['fichiers']}, presents)
        for f in manifeste['fichiers']:
            self.assertEqual(f['taille'], len(fichiers[f['chemin']]))
            self.assertEqual(f['sha256'], hashlib.sha256(fichiers[f['chemin']]).hexdigest())
        self.assertIn('LISEZMOI.txt', presents)

    def test_compression_reelle(self):
        chemin, _ = self.construire()
        with zipfile.ZipFile(chemin) as z:
            self.assertEqual({i.compress_type for i in z.infolist()}, {zipfile.ZIP_DEFLATED})

    def test_secrets_exclus(self):
        self.desk.db.execute('ALTER TABLE b53_documents ADD COLUMN api_token TEXT')      # colonne secrète, quelle que soit la table autorisée
        self.desk.db.execute("UPDATE b53_documents SET api_token='valeur-de-colonne-a-retirer'")
        self.desk.db.commit()
        chemin, meta = self.construire()
        fichiers = self.lire(chemin)
        tout = b''.join(fichiers.values())
        for secret in (SECRET_API, JETON, 'mot-de-passe-fictif-123', 'secret-fictif-123', 'valeur-de-colonne-a-retirer'):
            self.assertNotIn(secret.encode(), tout, secret)
        self.assertNotIn('api_token', fichiers['factures/documents_de_facturation.csv'].decode('utf-8-sig').splitlines()[0])
        self.assertIn(ex.RETIRE, json.dumps(json.loads(fichiers['contacts/contacts.json']), ensure_ascii=False))
        self.assertGreater(meta['secrets_retires'], 2)
        self.assertEqual(meta['secrets_retires'], json.loads(fichiers['MANIFEST.json'])['secrets_retires'])

    def test_aucune_table_de_comptes(self):
        tables = {t[2] for t in ex.TABLES}
        for interdite in ('users', 'sessions', 'login_attempts', 'reset_tokens', 'settings', 'audit', 'connexions5652', 'claude5675_jetons', 'integration_keys5636', 'jobs'):
            self.assertNotIn(interdite, tables)
        self.assertFalse([t for t in tables - {'contrib5688_cles'} if re.search(r'token|jeton|session|secret|cle|key', t)])   # clés de rétrocession : des pourcentages

    def test_donnees_metier_de_la_version_exportees(self):
        tables = {t[2] for t in ex.TABLES}
        for table in ('audiences5688', 'taches5688', 'accuses5688', 'contrib5688_parts', 'contrib5688_cles', 'note_commune5688', 'note_versions5688',
                      'societes5688', 'annonces5688', 'fiche_registre5688', 'memoire5688', 'registre_docs5688'):
            self.assertIn(table, tables)

    def test_table_absente_sans_effet_et_tache_reservee_ecartee(self):
        from agent import desk as _desk   # les tables des lots naissent à leur première utilisation : l'export ne doit pas en dépendre
        self.assertTrue(_desk)
        self.desk.db.executescript('''CREATE TABLE IF NOT EXISTS taches5688(task_id TEXT PRIMARY KEY, responsable TEXT NOT NULL DEFAULT '');''')
        self.desk.db.execute("INSERT OR REPLACE INTO tasks(id,matter,title,due,status,created) VALUES('T-RES','DOS-RES','Tâche fictive','','a_faire','')")
        self.desk.db.execute("INSERT OR REPLACE INTO taches5688(task_id,responsable) VALUES('T-RES','RESPONSABLE-FICTIF')")
        self.desk.db.commit()
        chemin, _meta = self.construire()
        with zipfile.ZipFile(chemin) as z:
            contenu = b''.join(z.read(n) for n in z.namelist())
        self.assertNotIn(b'RESPONSABLE-FICTIF', contenu)

    def test_nettoyage(self):
        net = ex.Nettoyage()
        self.assertEqual(net.valeur({'nom': 'DURAND', 'apiKey': 'x', 'mot_de_passe': 'x', 'motDePasse': 'x', 'client_secret': 'x', 'issue_key': 'garde', 'mail_key': 'garde', 'approved_hash': 'garde'}),
                         {'nom': 'DURAND', 'issue_key': 'garde', 'mail_key': 'garde', 'approved_hash': 'garde'})
        self.assertEqual(net.valeur('début AXIORHUB-VAULT-V1:gAAAAAabc fin'), 'début ' + ex.RETIRE + ' fin')
        self.assertEqual(net.valeur([{'jeton': 'x'}, 'clair']), [{}, 'clair'])
        self.assertEqual(net.valeur(b'ab'), {'base64': 'YWI='})
        self.assertIn(ex.RETIRE, net.valeur('-----BEGIN RSA ' + 'PRIVATE KEY-----\nabc\n-----END RSA ' + 'PRIVATE KEY-----'))

    def test_formule_neutralisee_dans_le_csv(self):
        chemin, _ = self.construire()
        fichiers = self.lire(chemin)
        lignes = list(csv.reader(io.StringIO(fichiers['contacts/contacts.csv'].decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(lignes[1][lignes[0].index('notes')], "'=1+1")
        self.assertEqual(json.loads(fichiers['contacts/contacts.json'])[0]['notes'], '=1+1')      # valeur exacte
        for valeur in ('=A1', '+1', '-1', '@x', '\tx'):
            self.assertTrue(ex.cellule(valeur).startswith("'"))

    def test_dossiers_reserves_exclus_par_defaut(self):
        chemin, meta = self.construire()
        fichiers = self.lire(chemin)
        tout = b''.join(fichiers.values())
        for marque in (b'KERVILAC', b'ZORVAL'):
            self.assertNotIn(marque, tout)
        self.assertNotIn(b'7777', fichiers['factures/paiements.json'])
        self.assertEqual(meta['dossiers_reserves'], {'traitement': 'exclus', 'nombre': 1})
        self.assertIn('exclus', fichiers['LISEZMOI.txt'].decode('utf-8'))
        self.assertNotIn(b'DOS-RES', tout)

    def test_dossiers_reserves_inclus_explicitement(self):
        chemin, meta = self.construire(dossiers_reserves='inclure')
        fichiers = self.lire(chemin)
        self.assertIn(b'KERVILAC-MESSAGE', fichiers['messagerie_interne/messages.json'])
        self.assertEqual(meta['dossiers_reserves']['traitement'], 'inclus')
        self.assertIn('INCLUS', fichiers['LISEZMOI.txt'].decode('utf-8'))

    def test_inclure_les_reserves_demande_confirmation(self):
        with self.assertRaisesRegex(Stop, 'confirmation_dossiers_reserves_requise'):
            ex.demander(self.desk, {'dossiers_reserves': 'inclure'}, 'admin')
        out = ex.demander(self.desk, {'dossiers_reserves': 'inclure', 'confirmation_reserves': True}, 'admin')
        rows = self.desk.db.execute("SELECT data FROM audit WHERE action='export5688_demande'").fetchall()
        self.assertIn('inclure', rows[-1][0])
        self.assertTrue(out['id'])

    def test_note_dun_dossier_reserve_par_lassociation(self):
        self.desk.db.execute("INSERT INTO proposals(key,matter) VALUES ('k9','DOS-RES')")
        self.desk.db.execute("INSERT INTO notes VALUES ('k9',?,'x')", (json.dumps({'result': {'texte': 'NOTE-ASSOCIEE'}}),))
        self.desk.db.commit()
        chemin, _ = self.construire()
        self.assertNotIn(b'NOTE-ASSOCIEE', self.lire(chemin)['notes/notes.json'])

    def test_inventaire_sans_texte_des_pieces(self):
        from agent.index import DocumentIndex
        index = DocumentIndex(self.desk.c['state_dir'])
        index.db.execute("INSERT INTO docs VALUES (?,?,?,?,?,?)", (self.mid, '/Dossiers/DEMO/Convention.pdf', 'etag1', '2026-10-01', 'TEXTE-CONFIDENTIEL-DE-LA-PIECE', ''))
        index.db.execute("INSERT INTO docs VALUES (?,?,?,?,?,?)", ('DOS-RES', '/Dossiers/RES/Secret.pdf', 'etag2', '2026-10-01', 'x', ''))
        index.db.commit()
        index.db.close()
        chemin, meta = self.construire()
        fichiers = self.lire(chemin)
        inventaire = json.loads(fichiers['documents/inventaire.json'])
        self.assertEqual([r['chemin'] for r in inventaire], ['/Dossiers/DEMO/Convention.pdf'])
        self.assertEqual(inventaire[0]['statut'], 'dans_nextcloud')
        self.assertEqual(inventaire[0]['etag'], 'etag1')
        self.assertNotIn(b'TEXTE-CONFIDENTIEL', b''.join(fichiers.values()))
        self.assertFalse(meta['documents_inclus'])

    def test_option_documents(self):
        from agent.index import DocumentIndex
        index = DocumentIndex(self.desk.c['state_dir'])
        index.db.execute("INSERT INTO docs VALUES (?,?,?,?,?,?)", (self.mid, '/Dossiers/DEMO/Convention.pdf', 'e', 'd', '', ''))
        index.db.execute("INSERT INTO docs VALUES (?,?,?,?,?,?)", (self.mid, '/Dossiers/DEMO/Absent.pdf', 'e', 'd', '', ''))
        index.db.commit()
        index.db.close()
        self.desk.c['nextcloud'] = {**self.desk.c['nextcloud'], 'roots': ['/Dossiers']}
        chemin, meta = self.construire(documents=True)
        fichiers = self.lire(chemin)
        inventaire = {r['chemin']: r for r in json.loads(fichiers['documents/inventaire.json'])}
        pris = inventaire['/Dossiers/DEMO/Convention.pdf']
        self.assertEqual(pris['statut'], 'inclus', pris)
        self.assertEqual(fichiers[pris['dans_export']], b'%PDF-1.4 convention fictive')
        self.assertEqual(pris['sha256_fichier'], hashlib.sha256(b'%PDF-1.4 convention fictive').hexdigest())
        self.assertTrue(inventaire['/Dossiers/DEMO/Absent.pdf']['statut'].startswith('non_inclus'))     # signalé, pas perdu en silence
        self.assertTrue(meta['documents_inclus'])
        self.assertTrue(ex.verifier_manifeste(chemin)['ok'])

    def test_plafond_de_volume(self):
        with patch.object(ex, 'MAX_OCTETS', 100):
            with self.assertRaisesRegex(Stop, 'export_trop_volumineux'):
                self.construire()

    def test_plafond_de_lignes(self):
        with patch.object(ex, 'MAX_LIGNES', 0):
            with self.assertRaisesRegex(Stop, 'export_trop_volumineux'):
                self.construire()

    def test_table_absente_sans_erreur(self):
        self.desk.db.execute('DROP TABLE IF EXISTS memos5636')
        self.desk.db.commit()
        chemin, _ = self.construire()
        self.assertNotIn('notes/memos.json', self.lire(chemin))

    # -------------------------------------------------------------------------------- travail et rétention
    def pret(self, **data):
        out = ex.demander(self.desk, data, 'admin@example.test')
        ex.executer(self.desk, out['id'])
        return out['id']

    def fichier(self, ident):
        return Path(self.desk.c['state_dir']) / ex.DOSSIER / (ident + '.zip')

    def test_travail_en_arriere_plan(self):
        ident = ex.demander(self.desk, {}, 'admin')['id']
        self.assertTrue(self.desk.work_once())
        job = self.desk.db.execute("SELECT status,result FROM jobs WHERE kind='export5688'").fetchone()
        self.assertEqual(job['status'], 'done', job['result'])
        row = self.desk.db.execute('SELECT * FROM exports5688 WHERE id=?', (ident,)).fetchone()
        self.assertEqual((row['statut'], row['progression']), ('pret', 100))
        self.assertEqual(row['sha256'], hashlib.sha256(self.fichier(ident).read_bytes()).hexdigest())
        self.assertTrue(row['expire'])
        self.assertEqual(os.listdir(self.fichier(ident).parent), [ident + '.zip'])

    def test_progression_visible(self):
        ident = ex.demander(self.desk, {}, 'admin')['id']
        vus = []
        vrai = ex._avancer
        with patch.object(ex, '_avancer', side_effect=lambda d, i, t, p=None: (vus.append(p), vrai(d, i, t, p))[1]):
            ex.executer(self.desk, ident)
        nombres = [p for p in vus if p is not None]
        self.assertEqual(nombres, sorted(nombres))
        self.assertGreater(len(nombres), 10)

    def test_une_seule_demande(self):
        ex.demander(self.desk, {}, 'admin')
        with self.assertRaisesRegex(Stop, 'export_deja_en_cours'):
            ex.demander(self.desk, {}, 'admin')

    def test_demande_perdue_ne_bloque_pas(self):
        ident = ex.demander(self.desk, {}, 'admin')['id']
        self.desk.db.execute("UPDATE jobs SET status='cancelled' WHERE kind='export5688'")
        self.desk.db.commit()
        ex.purge(self.desk)
        self.assertEqual(self.desk.db.execute('SELECT statut FROM exports5688 WHERE id=?', (ident,)).fetchone()[0], 'echec')
        ex.demander(self.desk, {}, 'admin')

    def test_echec_ne_laisse_aucun_fichier(self):
        ident = ex.demander(self.desk, {}, 'admin')['id']
        with patch.object(ex, 'MAX_OCTETS', 100):
            with self.assertRaisesRegex(Stop, 'export_trop_volumineux'):
                ex.executer(self.desk, ident)
        self.assertEqual(os.listdir(Path(self.desk.c['state_dir']) / ex.DOSSIER), [])
        self.assertEqual(self.desk.db.execute('SELECT statut,erreur FROM exports5688').fetchone()[1], 'export_trop_volumineux')

    def test_suppression_apres_telechargement_complet(self):
        ident = self.pret()
        flux, taille, nom = ex.telecharger(self.desk, ident, 'admin@example.test')
        self.assertTrue(nom.endswith('.zip'))
        data = b''.join(flux)
        flux.close()
        self.assertEqual(len(data), taille)
        self.assertFalse(self.fichier(ident).exists())
        self.assertEqual(self.desk.db.execute('SELECT statut FROM exports5688 WHERE id=?', (ident,)).fetchone()[0], 'telecharge')
        with self.assertRaisesRegex(Stop, 'export_inconnu'):
            ex.telecharger(self.desk, ident, 'admin@example.test')

    def test_telechargement_interrompu_garde_le_fichier(self):
        ident = self.pret()
        flux, _, _ = ex.telecharger(self.desk, ident, 'admin')
        next(flux) if self.fichier(ident).stat().st_size > 1024 * 1024 else None
        flux.reste = 1                                         # coupure de la connexion avant la fin
        flux.close()
        self.assertTrue(self.fichier(ident).exists())

    def test_expiration(self):
        ident = self.pret()
        ex.purge(self.desk, datetime.now(timezone.utc) + timedelta(hours=23))
        self.assertTrue(self.fichier(ident).exists())
        ex.purge(self.desk, datetime.now(timezone.utc) + timedelta(hours=25))
        self.assertFalse(self.fichier(ident).exists())
        self.assertEqual(self.desk.db.execute('SELECT statut FROM exports5688 WHERE id=?', (ident,)).fetchone()[0], 'expire')

    def test_expiration_par_la_boucle(self):
        ident = self.pret()
        self.desk.db.execute("UPDATE exports5688 SET expire='2020-01-01T00:00:00+00:00'")
        self.desk.db.commit()
        ex.schedule(self.desk)
        self.assertFalse(self.fichier(ident).exists())

    def test_purge_au_plus_une_fois_par_heure(self):
        now = datetime.now(timezone.utc).timestamp()
        with patch.object(ex, 'purge') as purge:
            ex.schedule(self.desk, now)
            ex.schedule(self.desk, now + 60)
        purge.assert_called_once()

    def test_trois_exports_au_plus(self):
        ids = [self.pret() for _ in range(4)]
        self.assertFalse(self.fichier(ids[0]).exists())
        self.assertTrue(self.fichier(ids[3]).exists())
        self.assertEqual(len(list(self.fichier(ids[3]).parent.glob('*.zip'))), 3)

    def test_reste_orphelin_supprime(self):
        racine = ex._racine(self.desk)
        (racine / ('a' * 32 + '.zip.part')).write_bytes(b'x')
        ex.purge(self.desk)
        self.assertEqual(os.listdir(racine), [])

    def test_suppression_manuelle(self):
        ident = self.pret()
        ex.supprimer(self.desk, ident, 'admin')
        self.assertFalse(self.fichier(ident).exists())
        with self.assertRaisesRegex(Stop, 'export_inconnu'):
            ex.supprimer(self.desk, ident, 'admin')

    def test_identifiant_force_refuse(self):
        for ident in ('../../desk', 'a' * 31, '', 'A' * 32):
            with self.assertRaisesRegex(Stop, 'export_inconnu'):
                ex.telecharger(self.desk, ident, 'admin')
            with self.assertRaisesRegex(Stop, 'export_inconnu'):
                ex.supprimer(self.desk, ident, 'admin')
            with self.assertRaisesRegex(Stop, 'export_inconnu'):
                ex.controle_fichier(self.desk, ident)

    def test_trace_daudit(self):
        ident = self.pret()
        flux, _, _ = ex.telecharger(self.desk, ident, 'admin@example.test')
        b''.join(flux)
        flux.close()
        actions = [r[0] for r in self.desk.db.execute("SELECT action FROM audit WHERE action LIKE 'export5688%' ORDER BY id")]
        self.assertEqual(actions, ['export5688_demande', 'export5688_termine', 'export5688_telecharge', 'export5688_fichier_retire'])

    def test_export_absent_de_la_sauvegarde(self):
        self.pret()
        self.assertFalse([n for n, _ in sv.plan(self.desk)[0] if 'exports5688' in n])


# ------------------------------------------------------------------------------------------------ vérification du manifeste
class Manifeste(ExportBase):
    def refaire(self, chemin, change=None, ajouter=None, retirer=None, manifeste=None):
        sortie = Path(self.tmp.name) / ('modifie-%d.zip' % len(list(Path(self.tmp.name).glob('modifie-*'))))
        with zipfile.ZipFile(chemin) as a, zipfile.ZipFile(sortie, 'w', zipfile.ZIP_DEFLATED) as b:
            for nom in a.namelist():
                if nom == retirer:
                    continue
                data = a.read(nom)
                if nom == 'MANIFEST.json' and manifeste:
                    data = manifeste(data)
                b.writestr(nom, (change or {}).get(nom, data))
            for nom, data in (ajouter or {}).items():
                b.writestr(nom, data)
        return sortie

    def test_intact(self):
        chemin, _ = self.construire()
        out = ex.verifier_manifeste(chemin)
        self.assertTrue(out['ok'], out)
        self.assertEqual(out['verifies'], out['fichiers'])

    def test_fichier_modifie(self):
        chemin, _ = self.construire()
        cible = 'contacts/contacts.json'
        data = self.lire(chemin)[cible]
        out = ex.verifier_manifeste(self.refaire(chemin, {cible: data.replace(b'DURAND', b'MARTIN')}))
        self.assertFalse(out['ok'])
        self.assertEqual(out['anomalies'], [{'fichier': cible, 'motif': 'empreinte différente'}])

    def test_fichier_tronque(self):
        chemin, _ = self.construire()
        cible = 'contacts/contacts.json'
        out = ex.verifier_manifeste(self.refaire(chemin, {cible: b'[]'}))
        self.assertEqual(out['anomalies'][0]['motif'], 'taille différente')

    def test_fichier_agrandi(self):
        chemin, _ = self.construire()
        cible = 'contacts/contacts.json'
        out = ex.verifier_manifeste(self.refaire(chemin, {cible: self.lire(chemin)[cible] + b'x' * 5000000}))
        self.assertEqual(out['anomalies'][0]['motif'], 'taille différente')

    def test_fichier_manquant(self):
        chemin, _ = self.construire()
        out = ex.verifier_manifeste(self.refaire(chemin, retirer='temps/temps_valides.csv'))
        self.assertEqual(out['anomalies'], [{'fichier': 'temps/temps_valides.csv', 'motif': 'fichier manquant'}])

    def test_fichier_ajoute(self):
        chemin, _ = self.construire()
        out = ex.verifier_manifeste(self.refaire(chemin, ajouter={'contacts/ajout.csv': b'x'}))
        self.assertEqual(out['anomalies'], [{'fichier': 'contacts/ajout.csv', 'motif': 'fichier absent du manifeste'}])

    def test_chemin_dangereux(self):
        chemin, _ = self.construire()
        out = ex.verifier_manifeste(self.refaire(chemin, ajouter={'../sortie.txt': b'x'}))
        self.assertFalse(out['ok'])
        self.assertEqual(out['anomalies'][0]['motif'], 'chemin dangereux')

    def test_manifeste_absent_ou_illisible(self):
        chemin, _ = self.construire()
        with self.assertRaisesRegex(Stop, 'manifeste_absent'):
            ex.verifier_manifeste(self.refaire(chemin, retirer='MANIFEST.json'))
        with self.assertRaisesRegex(Stop, 'manifeste_illisible'):
            ex.verifier_manifeste(self.refaire(chemin, manifeste=lambda d: b'pas du json'))
        with self.assertRaisesRegex(Stop, 'manifeste_illisible'):
            ex.verifier_manifeste(self.refaire(chemin, manifeste=lambda d: b'{"format":"autre","fichiers":[]}'))
        with self.assertRaisesRegex(Stop, 'manifeste_illisible'):
            ex.verifier_manifeste(self.refaire(chemin, manifeste=lambda d: b'{"format":"axiorhub-export-1","fichiers":[{"chemin":"a"}]}'))

    def test_pas_un_zip(self):
        faux = Path(self.tmp.name) / 'faux.zip'
        faux.write_bytes(b'pas un zip')
        with self.assertRaisesRegex(Stop, 'archive_zip_illisible'):
            ex.verifier_manifeste(faux)

    def test_rien_nest_extrait(self):
        chemin, _ = self.construire()
        self.assertNotIn('extract', inspect.getsource(ex.verifier_manifeste))
        self.assertTrue(ex.verifier_manifeste(chemin)['ok'])

    def test_ligne_de_commande(self):
        chemin, _ = self.construire()
        with patch('sys.stdout', io.StringIO()):
            self.assertEqual(ex.main(['verifier', str(chemin)]), 0)
            self.assertEqual(ex.main(['verifier', str(self.refaire(chemin, retirer='LISEZMOI.txt'))]), 1)

    def test_depot_par_morceaux(self):
        chemin, _ = self.construire()
        data = chemin.read_bytes()
        depot = ex.depot_ouvrir(self.desk, len(data), 'admin')
        coupe = len(data) // 2
        self.assertFalse(ex.depot_morceau(self.desk, depot['id'], 0, io.BytesIO(data[:coupe]), coupe)['complet'])
        with self.assertRaisesRegex(Stop, 'depot_invalide'):
            ex.depot_morceau(self.desk, depot['id'], 0, io.BytesIO(data[coupe:]), len(data) - coupe)     # hors d'ordre
        self.assertTrue(ex.depot_morceau(self.desk, depot['id'], 1, io.BytesIO(data[coupe:]), len(data) - coupe)['complet'])
        sv.demander_controle(self.desk, 'export', depot['id'], 'admin')
        self.assertTrue(self.desk.work_once())
        ligne = sv.controles(self.desk)[0]
        self.assertEqual(ligne['statut'], 'ok', ligne)
        self.assertEqual(os.listdir(Path(self.desk.c['state_dir']) / ex.DOSSIER), [])                     # retiré aussitôt après

    def test_depot_borne(self):
        with self.assertRaisesRegex(Stop, 'depot_trop_volumineux'):
            ex.depot_ouvrir(self.desk, ex.MAX_DEPOT + 1, 'admin')
        depot = ex.depot_ouvrir(self.desk, 10, 'admin')
        with self.assertRaisesRegex(Stop, 'depot_invalide'):
            ex.depot_morceau(self.desk, depot['id'], 0, io.BytesIO(b'x' * 20), 20)
        with self.assertRaisesRegex(Stop, 'depot_invalide'):
            ex.depot_morceau(self.desk, depot['id'], 0, io.BytesIO(b'x'), ex.MORCEAU_DEPOT + 1)
        with self.assertRaisesRegex(Stop, 'depot_invalide'):
            ex.depot_morceau(self.desk, '../x', 0, io.BytesIO(b'x'), 1)

    def test_depot_abandonne_retire(self):
        depot = ex.depot_ouvrir(self.desk, 10, 'admin')
        ex.depot_morceau(self.desk, depot['id'], 0, io.BytesIO(b'x'), 1)
        ex.purge(self.desk, datetime.now(timezone.utc) + timedelta(hours=1))
        self.assertEqual(len(os.listdir(ex._racine(self.desk))), 1)
        ex.purge(self.desk, datetime.now(timezone.utc) + timedelta(hours=7))
        self.assertEqual(os.listdir(ex._racine(self.desk)), [])


class ControleArchive(Base):
    def test_controle_en_arriere_plan(self):
        self.phrase()
        self.sauver()
        sv.demander_controle(self.desk, 'archive', self.archive().name, 'admin')
        self.assertTrue(self.desk.work_once())
        ligne = sv.controles(self.desk)[0]
        self.assertEqual(ligne['statut'], 'ok', ligne)
        self.assertTrue(ligne['resultat']['bases'])
        self.assertTrue(all(b['integre'] for b in ligne['resultat']['bases']))
        self.assertFalse((Path(self.desk.c['state_dir']) / sv.TRAVAIL).exists())

    def test_archive_alteree_signalee(self):
        self.phrase()
        self.sauver()
        chemin = self.archive()
        data = bytearray(chemin.read_bytes())
        data[-40] ^= 1
        chemin.write_bytes(bytes(data))
        sv.demander_controle(self.desk, 'archive', chemin.name, 'admin')
        self.desk.work_once()
        ligne = sv.controles(self.desk)[0]
        self.assertEqual(ligne['statut'], 'anomalie')
        self.assertEqual(ligne['resultat']['erreur'], 'archive_alteree')

    def test_ancienne_phrase_explique(self):
        self.phrase()
        self.sauver()
        sv.definir_phrase(self.desk, 'une nouvelle phrase bien longue', 'une nouvelle phrase bien longue', 'admin', PHRASE)
        sv.demander_controle(self.desk, 'archive', self.archive().name, 'admin')
        self.desk.work_once()
        self.assertEqual(sv.controles(self.desk)[0]['resultat']['erreur'], 'archive_autre_phrase')
        self.assertTrue(sv.controler(self.archive(), phrase=PHRASE)['ok'])

    def test_nom_force_refuse(self):
        self.phrase()
        for nom in ('../desk.sqlite3', 'axiorhub-20261101-0200-00000000abcd.axbk', 'desk.sqlite3'):
            with self.assertRaisesRegex(Stop, 'archive_inconnue'):
                sv.demander_controle(self.desk, 'archive', nom, 'admin')

    def test_pas_de_phrase_dans_la_file(self):
        self.assertNotIn('phrase', inspect.getsource(sv.demander_controle).split('"""')[2])


# ------------------------------------------------------------------------------------------------ écrans et routes
class Web(unittest.TestCase):
    ORIGIN = 'https://cabinet.example.test'

    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.config = self.f.base / 'config.json'
        private_json(self.config, self.f.c)
        self.auth = self.f.base / 'auth.json'
        salt = 'aa' * 16
        self.password = 'password-test-only-long'
        private_json(self.auth, {'username': 'admin', 'salt': salt, 'hash': hashlib.scrypt(self.password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex(),
                                 'origin': self.ORIGIN, 'prefix': '/agent-courriel', 'csrf': 'test-csrf'})
        self.app = App(str(self.config), str(self.auth))
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)

    def request(self, path, method='GET', body=None, query='', csrf='test-csrf', origin=ORIGIN, raw=None, keep=False):
        data = raw if raw is not None else json.dumps(body or {}).encode()
        env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': query, 'HTTP_HOST': 'cabinet.example.test', 'HTTP_X_FORWARDED_PROTO': 'https', 'wsgi.url_scheme': 'http',
               'wsgi.input': io.BytesIO(data), 'CONTENT_LENGTH': str(len(data)), 'CONTENT_TYPE': 'application/octet-stream' if raw is not None else 'application/json',
               'HTTP_AUTHORIZATION': 'Basic ' + base64.b64encode(('admin:' + self.password).encode()).decode(), 'HTTP_ORIGIN': origin, 'HTTP_X_CSRF_TOKEN': csrf}
        out = {}

        def start(status, headers):
            out.update(status=status, headers=dict(headers))
        reponse = self.app(env, start)
        out['iter'] = reponse
        if keep:
            return out
        out['body'] = b''.join(reponse)
        close = getattr(reponse, 'close', None)
        if close:
            close()
        return out

    def api(self, route, body=None, **kw):
        return self.request('/api440/rev5688/' + route, 'POST', body, **kw)

    def test_parcours_complet(self):
        r = self.api('phrase', {'phrase': PHRASE, 'confirmation': PHRASE})
        self.assertTrue(r['status'].startswith('200'), r)
        self.assertNotIn(b'valbrune', r['body'])
        self.assertTrue(self.api('reglages', {'actif': True, 'heure': 3, 'quotidiennes': 5, 'hebdomadaires': 2, 'dossier': '', 'taille_max_go': 10})['status'].startswith('200'))
        self.assertTrue(self.api('lancer')['status'].startswith('200'))
        self.assertTrue(self.desk.work_once())
        etat = json.loads(self.request('/api440/rev5688/etat')['body'])
        self.assertFalse(etat['sauvegarde_en_cours'])
        self.assertEqual(sv.etat(self.desk)['derniere']['statut'], 'reussie')

    def test_page_de_parametres(self):
        r = self.request('/parametres', query='rubrique=poste')
        html = r['body'].decode()
        for texte in ('Sauvegarde automatique', 'Export intégral du cabinet', 'Vérifier un export', 'Aucune phrase n’est définie'):
            self.assertIn(texte, html)
        self.assertIn('id="s-sauvegarde-automatique"', html)
        self.assertIn('v5688-reversibilite.js', html)
        self.assertTrue(re.search(r'<script defer src="[^"]*v5688-reversibilite\.js\?v=', html))
        self.assertTrue(self.request('/static/v5688-reversibilite.js')['status'].startswith('200'))

    def test_erreur_en_francais(self):
        r = self.api('phrase', {'phrase': 'courte', 'confirmation': 'courte'})
        self.assertTrue(r['status'].startswith('400'))
        self.assertEqual(json.loads(r['body'])['message'], 'La phrase doit compter de 16 à 200 caractères.')

    def test_telechargement_en_flux_et_suppression(self):
        ident = json.loads(self.api('export')['body'])['id']
        self.assertTrue(self.desk.work_once())
        chemin = Path(self.desk.c['state_dir']) / ex.DOSSIER / (ident + '.zip')
        attendu = chemin.read_bytes()
        r = self.request('/api440/rev5688/export/telecharger', query='id=' + ident)
        self.assertTrue(r['status'].startswith('200'), r)
        self.assertEqual(r['body'], attendu)
        self.assertEqual(r['headers']['Content-Type'], 'application/zip')
        self.assertEqual(r['headers']['Content-Length'], str(len(attendu)))
        self.assertIn('attachment', r['headers']['Content-Disposition'])
        self.assertEqual(r['headers']['Cache-Control'], 'no-store')
        self.assertIn('X-Content-Type-Options', r['headers'])
        self.assertIsInstance(r['iter'], ex.Telechargement)                  # pas de lecture complète en mémoire
        self.assertFalse(chemin.exists())

    def test_csrf_et_origine(self):
        self.assertTrue(self.api('lancer', csrf='faux')['status'].startswith('403'))
        self.assertTrue(self.api('lancer', origin='https://attaquant.example.test')['status'].startswith('403'))
        self.assertTrue(self.request('/api440/rev5688/depot/morceau', 'POST', raw=b'x', query='id=%s&n=0' % ('a' * 32), csrf='faux')['status'].startswith('403'))

    def test_get_ne_modifie_rien(self):
        self.assertTrue(self.request('/api440/rev5688/lancer')['status'].startswith('400'))

    def test_depot_par_morceaux_via_http(self):
        data = b'x' * 100
        ouvert = json.loads(self.api('depot/ouvrir', {'taille': 100})['body'])
        r = self.request('/api440/rev5688/depot/morceau', 'POST', raw=data, query='id=%s&n=0' % ouvert['id'])
        self.assertTrue(json.loads(r['body'])['complet'])
        self.assertTrue(self.api('depot/verifier', {'id': ouvert['id']})['status'].startswith('200'))
        self.desk.work_once()
        ligne = sv.controles(self.desk)[0]
        self.assertEqual(ligne['resultat']['erreur'], 'archive_zip_illisible')
        self.assertIn('ZIP', rv._controles_html(self.desk))


class Droits(Base):
    def test_reserve_a_ladministrateur(self):
        for role in ('avocat', 'assistant'):
            self.assertFalse(standalone_auth.allowed(role, 'POST', '/api440/rev5688/export'))
            self.assertFalse(standalone_auth.allowed(role, 'POST', '/api440/rev5688/phrase'))
        self.assertTrue(standalone_auth.allowed('administrateur', 'POST', '/api440/rev5688/export'))

    def test_revérifie_cote_service_y_compris_en_lecture(self):
        env = {'HTTP_X_AXIORHUB_USER': 'avocat@example.test', 'HTTP_X_AXIORHUB_ROLE': 'avocat'}
        for route, methode in (('export/telecharger', 'GET'), ('etat', 'GET'), ('lancer', 'POST'), ('export', 'POST')):
            out = rv.api(env, self.desk, {'origin': 'x', 'csrf': 'y'}, '/p', 'rev5688/' + route, {'id': 'a' * 32}, methode)
            self.assertTrue(out['status'].startswith('403'), route)
            self.assertEqual(json.loads(out['body'])['error'], 'role_insuffisant')

    def test_section_absente_pour_un_autre_role(self):
        from agent import parametres5621
        src = inspect.getsource(parametres5621._poste)
        self.assertIn("_actor88(env)[1] == 'administrateur'", src)


class Interface(Base):
    def test_valeurs_echappees(self):
        self.phrase()
        cfg = sv.config(self.desk)
        hostile = '<script>alert(1)</script>'
        cfg['dossier'] = '/tmp/' + hostile
        cfg['distant'] = {'type': 'webdav', 'url': 'https://x.example.test/' + hostile, 'autorise_par': 'a'}
        self.desk.setting(sv.CONFIG, cfg)
        sv.schema(self.desk)
        self.desk.db.execute("INSERT INTO sauvegardes5688(id,debut,fin,declencheur,statut,distant,emplacement,erreur,avertissements) VALUES ('a','2026-10-30T00:00:00+00:00','','manuel','echec',?,?,?,?)",
                             (hostile, hostile, hostile, json.dumps([hostile])))
        self.desk.db.commit()
        html = rv.sauvegarde_html(self.desk)
        self.assertNotIn('<script>alert', html)
        self.assertIn('&lt;script&gt;', html)

    def test_export_echappe(self):
        ex.schema(self.desk)
        self.desk.db.execute("INSERT INTO exports5688(id,par,demande,statut,etape) VALUES ('a','x','2026-10-30T00:00:00+00:00','en_cours','<b>x</b>')")
        self.desk.db.commit()
        self.assertNotIn('<b>x</b>', rv.export_html(self.desk, '/p'))

    def test_phrase_jamais_reaffichee(self):
        self.phrase()
        html = rv.sauvegarde_html(self.desk)
        self.assertNotIn('valbrune', html)
        self.assertIn('type="password"', html)

    def test_pas_de_script_en_ligne(self):
        self.phrase()
        pieces = ''.join(rv.sections(self.desk, '/p'))
        self.assertFalse(re.search(r'<script(?![^>]*\bdefer\b)', pieces))
        self.assertFalse(re.search(r'\son\w+=', pieces))

    def test_apercu(self):
        self.assertIn('désactivée', rv.apercu(self.desk))
        self.actif()
        self.assertIn('Aucune sauvegarde réussie', rv.apercu(self.desk))

    def test_tailles_lisibles(self):
        self.assertEqual(rv._taille(500), '500 octets')
        self.assertEqual(rv._taille(2048), '2.0 Ko')
        self.assertEqual(rv._taille(3 * 1024 ** 3), '3.0 Go')

    def test_dossier_non_inscriptible(self):
        self.phrase()
        for valeur in ('relatif/dossier', '/'):
            with self.assertRaises(Stop, msg=valeur):
                sv.enregistrer(self.desk, {'dossier': valeur}, 'admin')

    def test_retention_bornee_dans_les_reglages(self):
        self.phrase()
        for champs in ({'quotidiennes': 0}, {'quotidiennes': 61}, {'hebdomadaires': -1}, {'heure': 24}, {'taille_max_go': 0}, {'heure': 'x'}):
            with self.assertRaises(Stop, msg=champs):
                sv.enregistrer(self.desk, champs, 'admin')


# ------------------------------------------------------------------------------------------------ comptes protégés par double authentification
class ComptesProteges(Base):
    OWNER = 'admin@example.test'
    SECRET = 'SECRETTOTPFICTIF5688'

    def remplir(self, db):
        db.execute("INSERT INTO totp5688(owner,actif,cree) VALUES (?,1,'x')", (self.OWNER,))
        db.execute("INSERT INTO secours5688(owner,sel,empreinte) VALUES (?,'s','e')", (self.OWNER,))
        db.execute('INSERT INTO generations5688(owner,gen) VALUES (?,3)', (self.OWNER,))
        db.execute("INSERT INTO jetons5688(id,token_hash,owner,nom,portee,cree,expire,stamp,gen) VALUES ('j','h',?,'n','p','c','e','s',3)", (self.OWNER,))
        db.commit()
        totp5688._write_secret(db, self.OWNER, self.SECRET)
        return Path(db.execute('PRAGMA database_list').fetchone()[2]).parent

    def verifier(self, cible, base, source):
        db = sqlite3.connect(cible / base)
        for table, attendu in (('totp5688', 1), ('secours5688', 1), ('generations5688', 1), ('jetons5688', 1)):
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], attendu, table)
        db.close()
        coffre = (cible / base).parent / 'secrets5688'
        self.assertEqual((coffre / '.master.key').read_bytes(), (source / 'secrets5688/.master.key').read_bytes())
        secrets = list(coffre.glob('totp-*.secret'))
        self.assertEqual(len(secrets), 1)
        self.assertEqual(read_secret(secrets[0]), self.SECRET)       # relisible avec la seule clé restaurée : pas de réinitialisation du compte

    def test_base_de_la_page_de_connexion(self):
        with patch('agent.comptes5642.standalone', return_value=False):
            db = jetons5688.connect(self.desk.c['state_dir'])
            self.addCleanup(db.close)
            source = self.remplir(db)
            self.phrase()
            self.sauver()
        self.assertEqual(source.resolve(), Path(self.desk.c['state_dir']).resolve())
        self.verifier(self.extraire(), 'etat/comptes5648.sqlite3', source)

    def test_base_de_la_distribution_autonome(self):
        racine = Path(tempfile.mkdtemp(dir=self.tmp.name))
        sqlite3.connect(racine / 'users.sqlite3').close()
        with patch.dict(os.environ, {'AXIORHUB_AUTH_STATE': str(racine)}):
            db = jetons5688.connect(self.desk.c['state_dir'])
            self.addCleanup(db.close)
            source = self.remplir(db)
            self.phrase()
            self.sauver()
        self.assertEqual(source.resolve(), racine.resolve())
        self.verifier(self.extraire(), 'comptes/users.sqlite3', source)

    def test_comptes_sous_letat_pas_en_double(self):
        racine = Path(self.desk.c['state_dir']) / 'auth'
        racine.mkdir()
        (racine / 'session.key').write_bytes(b'x')
        with patch.dict(os.environ, {'AXIORHUB_AUTH_STATE': str(racine)}):
            noms = [n for n, _ in sv.plan(self.desk)[0]]
        self.assertIn('etat/auth/session.key', noms)
        self.assertFalse([n for n in noms if n.startswith('comptes/')])

    def test_absents_de_lexport(self):
        tables = {t[2] for t in ex.TABLES}
        for interdite in ('totp5688', 'secours5688', 'generations5688', 'jetons5688', 'attente5688', 'users', 'comptes5648'):
            self.assertNotIn(interdite, tables)
        self.assertEqual({t[3] for t in ex.TABLES}, {ex.DESK})        # jamais la base des comptes
        racine = Path(tempfile.mkdtemp(dir=self.tmp.name))
        sqlite3.connect(racine / 'users.sqlite3').close()
        with patch.dict(os.environ, {'AXIORHUB_AUTH_STATE': str(racine)}):
            db = jetons5688.connect(self.desk.c['state_dir'])
            self.addCleanup(db.close)
            self.remplir(db)
            chemin = Path(self.tmp.name) / 'export-comptes.zip'
            ex.construire(self.desk, chemin, {'dossiers_reserves': 'inclure', 'documents': True})
        with zipfile.ZipFile(chemin) as z:
            contenu = b''.join(z.read(n) for n in z.namelist())
            noms = '\n'.join(z.namelist())
        for marque in (self.OWNER.encode() + b'\x00', self.SECRET.encode(), b'token_hash', b'totp5688', b'secours5688', b'jetons5688', b'.master.key', b'-----BEGIN'):
            self.assertNotIn(marque, contenu)
        self.assertNotRegex(noms, r'secrets5688|\.master\.key|\.secret|sqlite3')


class Fichiers(unittest.TestCase):
    def test_script_vps_relie(self):
        texte = (ROOT / 'deploy/vps/backup.sh').read_text(encoding='utf-8')
        for motif in ("--exclude='sauvegardes5688'", "--exclude='exports5688'", "--exclude='.travail5688'", 'REVERSIBILITE-5.6.88.md'):
            self.assertIn(motif, texte)

    def test_documentation(self):
        texte = (ROOT / 'docs/REVERSIBILITE-5.6.88.md').read_text(encoding='utf-8')
        for motif in ('python3 -m agent.sauvegarde5688 extraire', 'python3 -m agent.export5688 verifier', 'ReadWritePaths', 'Format de l\'archive'):
            self.assertIn(motif, texte)

    def test_feuille_de_style_dans_le_paquet(self):
        from agent.shell501 import BUNDLE
        self.assertIn('v5688-reversibilite.css', BUNDLE)
        self.assertIn('.rev88-form', (ROOT / 'agent/static/app520.css').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
