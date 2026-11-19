"""5.6.108 (lot T) : M01 — page « Accès mobile » (``/mobile``) : trois situations, lecteur commun, anciens liens, ancienne présentation,
droits, aucun jeton ni QR dans un journal ou un export. Application réelle (WSGI), version serveur, comptes et données fictifs.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import secrets
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import test_v5650 as t50
from agent import appareils5674 as ap, connexion5648 as cx, file5651, interface56100, membres5650 as membres, relecture56100
from agent.desk import Desk

AUTRE = 'autre@example.test'


class Mobile(unittest.TestCase):
    def setUp(self):
        self.w = t50.ServerAccounts('run')
        self.w.setUp()
        self.addCleanup(self.w.doCleanups)
        self.addCleanup(membres.clear)
        self.f, self.state, self.request = self.w.f, self.w.state, self.w.request
        self.admin = self.w.admin_cookie()

    def associer(self, cookie, user_id=cx.ADMIN_ID):
        """Téléphone associé au compte (même enregistrement que l'association par QR : session de l'appareil liée au compte)."""
        db = ap.connect(str(self.state))
        try:
            fin = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
            db.execute('INSERT INTO appareils5674 VALUES(?,?,?,?,?,?)', (secrets.token_hex(8), cx._token_hash(cookie), int(user_id),
                                                                       'Téléphone fictif', datetime.now(timezone.utc).isoformat(), fin))
            db.commit()
        finally:
            db.close()

    # -------------------------------------------------------------------------------------- trois situations
    def test_M01_01_aucun_appareil_explique_sans_ouvrir_de_file(self):
        with patch.object(file5651, 'html', side_effect=AssertionError('ancienne file ouverte')), \
                patch.object(relecture56100, 'page_html', side_effect=AssertionError('file ouverte sans demande')):
            r = self.request('/mobile', cookie=self.admin)
        self.assertEqual(r['status'], '200 OK')
        for texte in ('Accès mobile', 'Aucun téléphone associé', 'Ouvrir ma file de relecture', 'Configurer les téléphones associés',
                      'rubrique=poste#s-telephones-associes', 'ne reçoit ni ne passe'):
            self.assertIn(texte, r['body'])
        # ?associe=1 sans appareil réel : aucun bandeau « Téléphone associé »
        with patch.object(relecture56100, 'page_html', return_value='LECTEUR'):
            r = self.request('/mobile', query='associe=1&vue=file', cookie=self.admin)
        self.assertNotIn('Téléphone associé.', r['body'])

    def test_M01_02_appareil_associe_lecteur_commun(self):
        self.associer(self.admin)
        with patch.object(file5651, 'html', side_effect=AssertionError('ancienne file')), \
                patch.object(relecture56100, 'page_html', wraps=relecture56100.page_html) as rendu:
            r = self.request('/mobile', query='associe=1', cookie=self.admin)
        self.assertEqual(r['status'], '200 OK', r['body'][:300])
        rendu.assert_called_once()
        self.assertEqual(rendu.call_args[0][2], ('cabinet', 'administrateur'))
        self.assertIn('/static/v56100-lecteur.js', r['body'])
        self.assertIn('data-ax100-lecteur', r['body'])
        self.assertIn('Téléphone associé.', r['body'])
        self.assertNotIn('Aucun téléphone associé', r['body'])

    def test_M01_03_application_locale_sans_serveur(self):
        fake = type('DeskFictif', (), {'c': {'state_dir': str(self.state)}})()
        with patch.object(file5651, 'html', side_effect=AssertionError('ancienne file')), \
                patch.object(relecture56100, 'page_html', side_effect=AssertionError('file ouverte sans demande')):
            html = ap.mobile_page(fake, '/agent-courriel', {}, {}, auth={'poste': True})
        self.assertIn('continuent de fonctionner sans serveur', html)
        self.assertIn('association par QR code', html)
        self.assertIn('Ouvrir ma file de relecture', html)
        self.assertNotIn('Téléphone associé.', html)

    def test_M01_04_ancien_lien_avec_element_conserve(self):
        with patch.object(relecture56100, 'page_html', return_value='LECTEUR-FICTIF') as rendu:
            r = self.request('/mobile', query='item=fictif-1', cookie=self.admin)
        self.assertEqual(r['status'], '200 OK')
        self.assertIn('LECTEUR-FICTIF', r['body'])
        self.assertEqual(rendu.call_args[0][3].get('item'), 'fictif-1')

    def test_M01_05_ancienne_presentation_choisie_respectee(self):
        with patch.object(interface56100, 'active', return_value=False), \
                patch.object(file5651, 'html', return_value='FILE-HISTORIQUE-FICTIVE') as ancien:
            r = self.request('/mobile', query='vue=file', cookie=self.admin)
        self.assertEqual(r['status'], '200 OK')
        ancien.assert_called_once()
        self.assertIn('FILE-HISTORIQUE-FICTIVE', r['body'])
        self.assertNotIn('/static/v56100-lecteur.js', r['body'])

    def test_M01_06_memes_droits_que_l_ordinateur(self):
        lawyer = self.w.add(self.admin, AUTRE, 'avocat')
        membres.set_reserve(str(self.state), 'DOS-001', ['avocate@example.test'])
        with patch.object(relecture56100, 'page_html', wraps=relecture56100.page_html) as rendu:
            r = self.request('/mobile', query='vue=file', cookie=lawyer)
        self.assertEqual(r['status'], '200 OK')
        self.assertEqual(rendu.call_args[0][2], (AUTRE, 'avocat'))
        # le même identifiant de dossier réservé, désigné par l'adresse, est refusé comme à l'ordinateur
        self.assertIn(self.request('/mobile', query='vue=file&matter=DOS-001', cookie=lawyer)['status'][:3], ('403', '404'))
        self.assertIn(self.request('/a-relire', query='matter=DOS-001', cookie=lawyer)['status'][:3], ('403', '404'))

    # -------------------------------------------------------------------------------------- jetons et QR
    def test_M01_07_aucun_jeton_ni_qr_dans_un_journal_ou_un_export(self):
        r = self.w.api('m5674/inviter', {}, self.admin)
        self.assertTrue(r['status'].startswith('200'), r['body'])
        out = json.loads(r['body'])
        jeton = re.search(r'jeton=([^&]+)', out['adresse']).group(1)
        qr = re.search(r'id=([0-9a-f]+)', out['qr']).group(1)
        apercu = self.request('/connexion/appareil', query='jeton=' + jeton)       # aperçu du lien : ne consomme rien
        self.assertEqual(apercu['status'], '200 OK')
        d = Desk(self.f.c)
        self.addCleanup(d.db.close)
        journaux = json.dumps([list(x) for x in d.db.execute('SELECT * FROM audit').fetchall()], ensure_ascii=False)
        db = cx.connect(str(self.state))
        try:
            journaux += json.dumps([list(x) for x in db.execute('SELECT * FROM log').fetchall()], ensure_ascii=False)
            invitations = db.execute('SELECT token_hash FROM appareils5674_invitations').fetchall()
        finally:
            db.close()
        self.assertTrue(invitations)
        self.assertNotIn(jeton, [x[0] for x in invitations], 'seule l’empreinte du jeton est conservée')
        for fichier in Path(self.state).rglob('*'):
            if fichier.is_file() and fichier.suffix.lower() in ('.log', '.jsonl', '.json', '.txt'):
                journaux += fichier.read_text(encoding='utf-8', errors='replace')
        self.assertNotIn(jeton, journaux)
        self.assertNotIn(qr, journaux)
        from agent import export5688
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        chemin = Path(tmp.name) / 'export.zip'
        export5688.construire(d, chemin, {'documents': False, 'dossiers_reserves': 'exclure'})
        with zipfile.ZipFile(chemin) as z:
            contenu = '\n'.join(z.read(n).decode('utf-8', 'replace') for n in z.namelist())
        self.assertNotIn(jeton, contenu)
        self.assertNotIn(qr, contenu)
        self.assertNotIn('appareils5674_invitations', contenu)

    # -------------------------------------------------------------------------------------- menu, recherche, téléphone
    def test_M01_08_menu_et_recherche_des_reglages(self):
        racine = Path(ap.__file__).parent / 'locales'
        attendu = {'fr': 'Accès mobile (smartphone)', 'en': 'Mobile access (smartphone)', 'de': 'Mobiler Zugriff (Smartphone)',
                   'es': 'Acceso móvil (smartphone)'}
        for langue, libelle in attendu.items():
            self.assertEqual(json.loads((racine / ('%s.json' % langue)).read_text(encoding='utf-8'))['fn.mobile.label'], libelle)
        from agent import parametres5621
        mots = parametres5621.KEYWORDS['poste']
        for mot in ('telephone', 'smartphone', 'telephones associes', 'qr', 'appareil', 'notification'):
            self.assertIn(mot, mots)
        reglages = self.request('/parametres', query='rubrique=poste', cookie=self.admin)
        self.assertIn('id="s-telephones-associes"', reglages['body'])

    def test_M01_09_commandes_atteignables_clavier_ouvert(self):
        css = (Path(ap.__file__).parent / 'static' / 'app520.css').read_text(encoding='utf-8')
        self.assertIn('.ap74-mobile .ax100-actions', css)
        self.assertRegex(css, r'@media \(max-height: ?32rem\)')


if __name__ == '__main__':
    unittest.main()
