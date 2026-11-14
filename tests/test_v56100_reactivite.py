"""5.6.100 (lot F) : réactivité, navigation, mesure et réglage (P5, R07, R08, V100-08).

- R07 : liste de la boîte de réception sur une vraie ``Mailbox`` branchée sur un faux serveur IMAP qui compte les commandes
  (``faux_imap56100``) : une sélection, un UID SEARCH et un lot d'en-têtes par page, aucun corps ; même résultat que la lecture message par
  message ; droits avant total et page ; UIDVALIDITY ; recherche accentuée bornée avec couverture annoncée.
- R08 : registre des dossiers non relu quand rien n'a changé ; ``Server-Timing`` par route (pages et API JSON) avec temps et nombre de
  commandes IMAP, des nombres seulement ; bureau ouvert avant le routage refermé, même origine Open WebUI dans la CSP.
- P5 : réglage de la nouvelle interface pour le cabinet (administrateur) et par compte (Mon compte), version attendue, même choix renvoyé.
- V100-08 : retour contextuel (``navigation56100.cjs`` dans Node) et script de mesure ``scripts/mesure56100.py`` (petit jeu, rapport JSON).
"""
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from agent import boite5625, interface56100, mailbox
from agent.common import Stop, private_json
from faux_imap56100 import FauxIMAP
import test_v530 as t530

ROOT = Path(__file__).resolve().parents[1]
CFG = {'host': 'imap.example.test', 'username': 'cabinet', 'inbox': 'INBOX', 'drafts': 'Drafts', 'sent': 'Sent'}


def boite(conn, cfg=CFG):
    """Vraie ``Mailbox`` (méthodes du dépôt) sur une connexion simulée."""
    box = mailbox.Mailbox.__new__(mailbox.Mailbox)
    box.cfg, box.conn, box.selected, box.validity = cfg, conn, '', ''
    return box


class Ancienne(mailbox.Mailbox):
    """Lecture message par message (chemin historique, gardé pour les doubles d'essai) : sert de référence de comportement."""
    def fetch(self, *args, **kwargs):
        return mailbox.Mailbox.fetch(self, *args, **kwargs)


def ancienne(conn, cfg=CFG):
    box = Ancienne.__new__(Ancienne)
    box.cfg, box.conn, box.selected, box.validity = cfg, conn, '', ''
    return box


class _Bureau:
    def __init__(self, tmp):
        self.c = {'mail': CFG, 'state_dir': tmp}


class Boite(unittest.TestCase):
    def setUp(self):
        from sqlite_cleanup import TempDir
        self.tmp = TempDir()
        self.desk = _Bureau(self.tmp.__enter__())
        self.addCleanup(self.tmp.__exit__, None, None, None)

    def test_R07_une_selection_un_lot_entetes_aucun_corps(self):
        conn = FauxIMAP.boite_de(10000)
        out = boite5625.listing(self.desk, box=boite(conn))
        self.assertEqual((conn.compte('EXAMINE'), conn.compte('SEARCH'), conn.compte('FETCH')), (1, 1, 1))
        self.assertEqual((conn.compte('SELECT'), conn.corps_lus), (0, 0))       # lecture seule, aucun corps pour lister
        self.assertEqual((len(out['items']), out['total'], out['pages']), (25, 10000, 400))
        self.assertEqual([m['uid'] for m in out['items']][:3], ['10000', '9999', '9998'])
        page3 = FauxIMAP.boite_de(10000)
        out3 = boite5625.listing(self.desk, page=3, box=boite(page3))
        self.assertEqual(out3['items'][0]['uid'], '9950')
        self.assertEqual(page3.compte('EXAMINE') + page3.compte('SEARCH') + page3.compte('FETCH'), 3)

    def test_R07_contre_exemple_lecture_message_par_message(self):
        # Référence : l'ancien chemin coûtait 26 EXAMINE, 1 SEARCH, 50 FETCH pour 25 lignes (mesure de l'audit, reproduite ici).
        conn = FauxIMAP.boite_de(10000)
        boite5625.listing(self.desk, box=ancienne(conn))
        self.assertEqual((conn.compte('EXAMINE'), conn.compte('SEARCH'), conn.compte('FETCH')), (26, 1, 50))

    def test_R07_meme_resultat_que_l_ancien_chemin(self):
        for q in ('', 'MARTIN', 'procédure', 'àbsent'):
            nouveau, vieux = FauxIMAP.boite_de(400), FauxIMAP.boite_de(400)
            a = boite5625.listing(self.desk, query=q, box=boite(nouveau))
            b = boite5625.listing(self.desk, query=q, box=ancienne(vieux))
            self.assertEqual(a.pop('couverture', None) is None, b.pop('couverture', None) is None, q)
            self.assertEqual(a, b, q)
        pieces = boite5625.listing(self.desk, box=boite(FauxIMAP.boite_de(30)))
        self.assertEqual({m['uid'] for m in pieces['items'] if m['attachments']}, {'28', '21', '14', '7'})
        self.assertTrue(all(m['seen'] == (int(m['uid']) % 3 != 0) for m in pieces['items']))

    def test_R07_recherche_accentuee_bornee_et_couverture_annoncee(self):
        conn = FauxIMAP.boite_de(10000)
        out = boite5625.listing(self.desk, query='procédure', box=boite(conn))
        self.assertEqual(len(out['items']), 25)
        self.assertTrue(all('procédure' in m['subject'].lower() for m in out['items']))
        self.assertEqual(out['couverture'], {'partielle': True, 'examines': 300})
        self.assertLessEqual(conn.compte('FETCH'), 6)                           # avant : 400 FETCH et 200 EXAMINE pour 25 résultats
        self.assertEqual((conn.compte('EXAMINE'), conn.corps_lus), (1, 0))
        rare = FauxIMAP.boite_de(10000)
        vide = boite5625.listing(self.desk, query='àbsent', box=boite(rare))
        self.assertEqual((vide['items'], vide['total'], rare.compte('FETCH')), ([], 0, 6))   # 300 en-têtes en 6 lots de 50
        petite = boite5625.listing(self.desk, query='procédure', box=boite(FauxIMAP.boite_de(100)))
        self.assertEqual(petite['couverture'], {'partielle': False, 'examines': 100})

    def test_R07_uidvalidity_de_la_selection_et_changement(self):
        conn = FauxIMAP.boite_de(30, validites={'INBOX': 4242})
        out = boite5625.listing(self.desk, box=boite(conn))
        cle = mailbox.digest('|'.join(['cabinet@imap.example.test', 'INBOX', '4242', out['items'][0]['uid']]))
        self.assertEqual(out['items'][0]['key'], cle)
        box = boite(FauxIMAP.boite_de(5))
        box.select('INBOX')
        with self.assertRaisesRegex(Stop, 'uidvalidity_modifiee'):
            box.entetes('INBOX', ['1', '2'], expected_validity='8')
        changeante = boite(FauxIMAP.boite_de(30))
        with patch.object(mailbox.Mailbox, 'entetes', side_effect=Stop('uidvalidity_modifiee')):
            with self.assertRaisesRegex(Stop, '^introuvable$'):                # même 404 neutre que la lecture d'un message
                boite5625.listing(self.desk, box=changeante)

    def test_R07_message_disparu_entre_recherche_et_lecture(self):
        conn = FauxIMAP.boite_de(30)
        reel = conn.uid

        def uid(commande, *args):
            if commande.upper() == 'FETCH':
                conn.boites['INBOX'].pop(29, None)                              # supprimé par une autre messagerie entre-temps
            return reel(commande, *args)
        conn.uid = uid
        out = boite5625.listing(self.desk, box=boite(conn))
        self.assertNotIn('29', [m['uid'] for m in out['items']])
        self.assertEqual(len(out['items']), 24)

    def test_R07_droits_avant_total_page_et_lecture(self):
        conn = FauxIMAP.boite_de(60)
        demandes = []
        reel = conn.uid

        def uid(commande, *args):
            if commande.upper() == 'FETCH':
                demandes.extend(args[0].split(','))
            return reel(commande, *args)
        conn.uid = uid
        reserve = {mailbox.digest('|'.join(['cabinet@imap.example.test', 'INBOX', '7', str(u)])) for u in range(1, 61) if u % 2}

        def dossiers(desk, cles):
            return {c: ('DOS-RES',) for c in cles if c in reserve}
        with patch('agent.acces5697.masques', return_value=frozenset({'DOS-RES'})), patch('agent.messages5697.dossiers_par_cle', side_effect=dossiers):
            out = boite5625.listing(self.desk, box=boite(conn), acteur=('camille', 'avocat'))
            accent = boite5625.listing(self.desk, query='procédure', box=boite(FauxIMAP.boite_de(60)), acteur=('camille', 'avocat'))
        self.assertEqual(out['total'], 30)                                      # totaux sans les messages masqués
        self.assertTrue(all(int(m['uid']) % 2 == 0 for m in out['items']))
        self.assertTrue(demandes and all(int(u) % 2 == 0 for u in demandes))   # aucun en-tête de message masqué demandé
        self.assertEqual(conn.compte('EXAMINE'), 1)
        self.assertTrue(all(int(m['uid']) % 2 == 0 for m in accent['items']))
        self.assertEqual(accent['couverture']['examines'], 30)

    def test_R07_doubles_historiques_gardent_leur_chemin(self):
        import test_v520_routines as tr
        self.assertFalse(mailbox.lot_possible(tr.FakeBox(None)))
        self.assertFalse(mailbox.lot_possible(ancienne(FauxIMAP())))
        self.assertTrue(mailbox.lot_possible(boite(FauxIMAP())))

    def test_R07_reponse_groupee_drapeaux_apres_le_litteral(self):
        data = [(b'5 (UID 5 INTERNALDATE "02-Nov-2026 08:00:00 +0000" BODY[HEADER.FIELDS (SUBJECT)] {18}', b'Subject: Essai\r\n\r\n'), b' FLAGS (\\Seen))',
                b'6 (FLAGS (\\Seen))', (b'7 (UID 7 FLAGS () INTERNALDATE "02-Nov-2026 08:00:00 +0000" BODY[HEADER.FIELDS (SUBJECT)] {18}', b'Subject: Autre\r\n\r\n'), b')']
        lots = mailbox._reponses_fetch(data)
        self.assertEqual(len(lots), 3)
        self.assertIn(b'FLAGS (\\Seen)', lots[0][0])
        self.assertIsNone(lots[1][1])


class Registre(unittest.TestCase):
    def setUp(self):
        from sqlite_cleanup import TempDir
        self.tmp = TempDir()
        base = Path(self.tmp.__enter__())
        self.addCleanup(self.tmp.__exit__, None, None, None)
        (base / 'state').mkdir()
        self.fichier = base / 'matters.json'
        private_json(self.fichier, [{'id': 'DOS-%03d' % i, 'client_name': 'DURAND %d' % i, 'path': '/Dossiers/DOS-%03d' % i, 'references': [], 'aliases': [],
                                     'correspondents': [{'email': 'client%d@example.test' % i, 'role': 'client'}]} for i in range(1, 51)])
        self.c = {'matters_file': str(self.fichier), 'state_dir': str(base / 'state'), 'nextcloud': {'roots': ['/Dossiers']}}
        self._vieillir(self.fichier)
        from agent import common
        common.oublier_registre()
        self.addCleanup(common.oublier_registre)

    @staticmethod
    def _vieillir(chemin):
        t = time.time() - 60
        os.utime(chemin, (t, t))

    def test_R08_registre_non_relu_quand_rien_n_a_change(self):
        from agent import common
        with patch.object(common, '_lire_registre', wraps=common._lire_registre) as lecture:
            a = common.load_matters(self.c)
            b = common.load_matters(self.c)
            self.assertEqual(lecture.call_count, 1)
            self.assertEqual(a, b)
            a[0]['correspondents'].append({'email': 'intrus@example.test', 'role': 'client'})   # copie propre à chaque appelant
            a[1]['client_name'] = 'MODIFIÉ'
            c = common.load_matters(self.c)
            self.assertEqual(c, b)
            self.assertEqual(lecture.call_count, 1)
            # surcouche du navigateur ajoutée : relue
            surcouche = Path(self.c['state_dir']) / 'registry-web.json'
            private_json(surcouche, [{'id': 'DOS-051', 'client_name': 'MARTIN', 'path': '/Dossiers/DOS-051', 'references': [], 'aliases': [], 'correspondents': []}])
            self._vieillir(surcouche)
            self.assertEqual(len(common.load_matters(self.c)), 51)
            self.assertEqual(lecture.call_count, 2)
            # registre de l'administrateur modifié : relu
            rows = json.loads(self.fichier.read_text(encoding='utf-8'))[:10]
            private_json(self.fichier, rows)
            self._vieillir(self.fichier)
            self.assertEqual(len(common.load_matters(self.c)), 11)
            self.assertEqual(lecture.call_count, 3)

    def test_R08_fichier_tout_juste_modifie_toujours_relu(self):
        from agent import common
        os.utime(self.fichier, None)                                            # écrit à l'instant : horodatage non fiable
        with patch.object(common, '_lire_registre', wraps=common._lire_registre) as lecture:
            common.load_matters(self.c)
            common.load_matters(self.c)
        self.assertEqual(lecture.call_count, 2)

    def test_R08_registre_invalide_jamais_garde(self):
        from agent import common
        private_json(self.fichier, [{'id': '../x', 'client_name': 'X', 'path': '/Dossiers/x', 'references': [], 'aliases': [], 'correspondents': []}])
        self._vieillir(self.fichier)
        for _ in range(2):
            with self.assertRaisesRegex(Stop, 'identifiant_dossier_invalide'):
                common.load_matters(self.c)

    def test_R08_dossiers_reserves_filtres_a_chaque_appel(self):
        from agent import common
        common.load_matters(self.c)
        with patch('agent.membres5650.viewer_hidden', return_value={'DOS-001'}):
            self.assertNotIn('DOS-001', {m['id'] for m in common.load_matters(self.c)})
            self.assertIn('DOS-001', {m['id'] for m in common.load_matters(self.c, unfiltered=True)})


class Web(t530.Base):
    def requete_json(self, chemin, corps, role=None, owner=None, csrf='test-csrf'):
        brut = json.dumps(corps).encode()
        env = {'REQUEST_METHOD': 'POST', 'PATH_INFO': chemin, 'QUERY_STRING': '', 'HTTP_HOST': 'cabinet.example.test', 'HTTP_X_FORWARDED_PROTO': 'https',
               'wsgi.url_scheme': 'http', 'wsgi.input': io.BytesIO(brut), 'CONTENT_LENGTH': str(len(brut)), 'CONTENT_TYPE': 'application/json',
               'HTTP_ORIGIN': self.origin, 'HTTP_X_CSRF_TOKEN': csrf}
        import base64
        env['HTTP_AUTHORIZATION'] = 'Basic ' + base64.b64encode(('admin:' + self.password).encode()).decode()
        res = {}

        def start(status, headers):
            res.update(status=status, headers=dict(headers))
        res['body'] = b''.join(self.app(env, start)).decode()
        return res

    def test_R08_server_timing_api_json_avec_imap_des_nombres_seulement(self):
        conn = FauxIMAP.boite_de(200)
        self.f.c['mail']['password_file'] = str(Path(self.f.c['state_dir']) / 'secret-essai')
        private_json(self.config, self.f.c)
        with patch('agent.mailbox.Mailbox', mailbox._MAILBOX_REELLE), patch('agent.mailbox.connect', return_value=conn), \
                patch('agent.mailbox.read_secret', return_value='mot-de-passe-essai'):
            r = self.request('/api440/m5625/boite/liste')
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertEqual(len(json.loads(r['body'])['items']), 25)
        entete = r['headers'].get('Server-Timing', '')
        commandes = int(re.search(r'imap;dur=[\d.]+;desc="(\d+)"', entete).group(1))
        self.assertEqual(commandes, conn.compte('LOGIN') + conn.compte('EXAMINE') + conn.compte('SEARCH') + conn.compte('FETCH') + conn.compte('LOGOUT'))
        self.assertLessEqual(commandes, 5)                                      # connexion, sélection, recherche, lot d'en-têtes, fin
        self.assertEqual(re.sub(r'(auth|sqlite|imap|blocs|total);dur=[\d.]+(;desc="\d+")?(, )?', '', entete), '')
        self.assertNotIn('Server-Timing', self.request('/static/v5696-affichage.js', query='v=1')['headers'])
        page = self.request('/aujourdhui')['headers'].get('Server-Timing', '')
        self.assertNotIn('imap', page)                                         # pas de mesure IMAP pour une route qui n'y touche pas

    def test_R08_bureau_avant_routage_ferme_et_meme_origine_open_webui(self):
        from agent.workstation import save_external_url
        save_external_url(self.desk, 'openwebui', 'https://ia.example.test/espace/')
        self.desk.db.commit()
        with patch('agent.workstation.external_links', side_effect=AssertionError('tous les raccourcis lus avant le routage')):
            r = self.request('/api440/m5689/affichage/session')                 # route qui ne lit aucun raccourci elle-même
        self.assertTrue(r['status'].startswith('200'), r['body'])
        page = self.request('/dossiers')
        self.assertTrue(page['status'].startswith('200'))
        self.assertIn("frame-src 'self' https://ia.example.test;", page['headers']['Content-Security-Policy'])

    def test_P5_reglage_cabinet_administrateur_version_et_audit(self):
        self.assertTrue(interface56100.active(self.desk, 'cabinet'))
        page = self.request('/parametres', query='rubrique=interface')['body']
        self.assertIn('id="i100-cabinet"', page)
        self.assertIn('data-api="m56100f/interface/cabinet"', page)
        self.assertIn('name="revision" value="active"', page)
        r = self.requete_json('/api440/m56100f/interface/cabinet', {'choix': 'ancienne', 'revision': 'active'})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertFalse(interface56100.reglage_cabinet(self.desk))
        self.assertEqual(r['headers']['Cache-Control'], 'no-store')
        audit = self.desk.db.execute("SELECT data FROM audit WHERE action='interface56100_reglage'").fetchall()
        self.assertEqual(len(audit), 1)
        self.assertEqual(json.loads(audit[0][0])['choix'], 'ancienne')
        again = self.requete_json('/api440/m56100f/interface/cabinet', {'choix': 'ancienne', 'revision': 'active'})   # réponse perdue, nouvel essai
        self.assertTrue(json.loads(again['body'])['inchange'])
        self.assertEqual(len(self.desk.db.execute("SELECT 1 FROM audit WHERE action='interface56100_reglage'").fetchall()), 1)
        perime = self.requete_json('/api440/m56100f/interface/cabinet', {'choix': 'active', 'revision': 'active'})   # page ouverte avant le changement
        self.assertTrue(perime['status'].startswith('409'), perime['body'])
        self.assertFalse(interface56100.reglage_cabinet(self.desk))
        sans_csrf = self.requete_json('/api440/m56100f/interface/cabinet', {'choix': 'active', 'revision': 'ancienne'}, csrf='mauvais')
        self.assertFalse(sans_csrf['status'].startswith('200'))
        self.assertFalse(interface56100.reglage_cabinet(self.desk))
        inconnu = self.requete_json('/api440/m56100f/interface/cabinet', {'choix': 'autre', 'revision': 'ancienne'})
        self.assertTrue(inconnu['status'].startswith('400'))

    def test_P5_reglage_reserve_a_l_administrateur_et_compte_pour_soi(self):
        from agent.standalone_auth import allowed
        from agent import reactivite56100
        self.assertFalse(allowed('avocat', 'POST', '/api440/m56100f/interface/cabinet'))
        self.assertTrue(allowed('avocat', 'POST', '/api440/m56100f/interface/compte'))
        self.assertTrue(allowed('assistant', 'POST', '/api440/m56100f/interface/compte'))
        self.assertFalse(allowed('avocat', 'POST', '/api440/m56100f/interface/autre'))
        env = {'HTTP_X_AXIORHUB_USER': 'camille', 'HTTP_X_AXIORHUB_ROLE': 'avocat'}
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):                 # contrôle du service aussi, pas seulement de la porte
            reactivite56100.api(env, self.desk, {}, '/agent-courriel', 'm56100f/interface/cabinet', {}, 'POST')
        self.assertTrue(reactivite56100.section_cabinet(self.desk, '/agent-courriel', 'avocat').count('<form') == 0)

        def poster(owner, choix, revision):
            brut = json.dumps({'choix': choix, 'revision': revision}).encode()
            e = {'HTTP_X_AXIORHUB_USER': owner, 'HTTP_X_AXIORHUB_ROLE': 'avocat', 'HTTP_ORIGIN': 'https://o.example.test', 'HTTP_X_CSRF_TOKEN': 'j',
                 'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(brut)), 'wsgi.input': io.BytesIO(brut)}
            return reactivite56100.api(e, self.desk, {'origin': 'https://o.example.test', 'csrf': 'j'}, '/agent-courriel', 'm56100f/interface/compte', {}, 'POST')
        poster('camille', 'ancienne', 'cabinet')
        self.assertFalse(interface56100.active(self.desk, 'camille'))
        self.assertTrue(interface56100.active(self.desk, 'dominique'))          # les autres comptes suivent le cabinet
        self.assertTrue(interface56100.reglage_cabinet(self.desk))
        with self.assertRaisesRegex(Stop, 'version_en_conflit'):
            poster('camille', 'active', 'cabinet')
        poster('camille', 'cabinet', 'ancienne')
        self.assertIsNone(interface56100.reglage_compte(self.desk, 'camille'))
        compte = self.request('/mon-compte')['body']
        self.assertIn('id="i100-compte"', compte)
        self.assertIn('data-api="m56100f/interface/compte"', compte)

    def test_P5_css_dans_le_paquet(self):
        self.assertIn("'v56100-reactivite.css'", (ROOT / 'agent' / 'shell501.py').read_text(encoding='utf-8'))
        self.assertIn('.i100-choix', (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8'))


class Navigation(unittest.TestCase):
    def test_V100_08_retour_ancre_ligne_suivante_liste_differee_et_cache(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node absent : le parcours navigateur couvre aussi ce cas')
        r = subprocess.run([node, str(Path(__file__).with_name('navigation56100.cjs')), str(ROOT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('ok 11 cas', r.stdout)
        anciens = subprocess.run([node, str(Path(__file__).with_name('navigation5696.cjs')), str(ROOT)], capture_output=True, text=True, timeout=60)
        self.assertIn('ok 4 cas', anciens.stdout)


class Mesure(unittest.TestCase):
    def test_V100_08_script_de_mesure_rapport_json_sans_donnee(self):
        from sqlite_cleanup import TempDir
        with TempDir() as tmp:
            sortie = Path(tmp) / 'rapport.json'
            r = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'mesure56100.py'), '--essais', '3', '--dossiers', '12,30', '--entetes', '600',
                                '--propositions', '20', '--retard-imap-ms', '5', '--sortie', str(sortie)],
                               capture_output=True, text=True, timeout=600, env={**os.environ, 'PYTHONPATH': os.pathsep.join([str(ROOT), str(ROOT / 'tests')])})
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-4000:])
            rapport = json.loads(sortie.read_text(encoding='utf-8'))
        self.assertEqual(rapport['essais'], 3)
        noms = {s['scenario'] for s in rapport['scenarios']}
        self.assertTrue({'dossiers_12', 'dossiers_30', 'reception_imap', 'reception_imap_lent', 'recherche_accentuee'} <= noms, noms)
        for s in rapport['scenarios']:
            for cle in ('ttfb_ms', 'octets', 'requetes_sqlite', 'commandes_imap'):
                self.assertIn(cle, s, s['scenario'])
            self.assertTrue({'p50', 'p95', 'froid'} <= set(s['ttfb_ms']), s)
        imap = next(s for s in rapport['scenarios'] if s['scenario'] == 'reception_imap')
        self.assertLessEqual(imap['commandes_imap']['p50'], 5)
        texte = json.dumps(rapport, ensure_ascii=False)
        for interdit in ('DURAND', 'MARTIN', 'example.test', 'Demande de pièces', 'DOS-0'):
            self.assertNotIn(interdit, texte)


if __name__ == '__main__':
    unittest.main()
