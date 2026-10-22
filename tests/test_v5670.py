"""5.6.70 : facturation électronique (SuperPDP, B2Brouter), messagerie intégrée (fichiers Nextcloud, onglet Courriels), recherche de
dossier, traitement des courriels par lot, visioconférence Jitsi, garde des accès et correctifs PHP, objet « [À COMPLÉTER] »,
bandeau masquable (demande du 22 octobre 2026). Données fictives (example.test, adresses d'essai)."""
import base64
from email.message import EmailMessage
import gzip
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import test_agent as fixtures
import test_v5652 as base
from agent import efacture5670 as ef, fichiers5670 as fic, garde5670 as garde, php5670, traitement5670 as lot, visio5670 as visio
from agent.common import Stop, load_matters

MID = base.MID
OWNER = 'avocat@example.test'
PUBLIC = '8.8.4.4'


# ---------------------------------------------------------------------------------------------------- objet « [À COMPLÉTER] »
class Objet(unittest.TestCase):
    def test_brouillon_avec_marqueur_marque_dans_l_objet(self):
        from agent.mailbox import make_draft
        from agent.prudent5667 import mark_subject
        mail = fixtures.mail(sender='client@example.test', subject='Point sur le dossier')
        cfg = {'from_name': 'Cabinet', 'from_address': 'cabinet@example.test', 'signature': 'Le cabinet', 'own_addresses': ['cabinet@example.test']}
        with patch('agent.sens5643.check_recipients'):
            plain = make_draft(mail, cfg, 'Bonjour,\nVoici la réponse.', 'k' * 16)
            marked = make_draft(mail, cfg, 'Bonjour,\n[À COMPLÉTER : position sur le renvoi]', 'k' * 16)
        self.assertEqual(plain['Subject'], 'Re: Point sur le dossier')
        self.assertTrue(marked['Subject'].startswith('[À COMPLÉTER] '))
        mark_subject(marked)
        self.assertEqual(len(marked.get_all('X-AxiorHub-A-Completer')), 1)   # appel répété : pas d'en-tête en double


# ---------------------------------------------------------------------------------------------------- facturation électronique
class Facturation(base.Base):
    def _setup(self, provider):
        caps = ['einvoice.receive', 'einvoice.status', 'einvoice.download']
        view = self.connect(provider, caps=caps)
        from agent import connexions5652 as cx
        cx.test(self.desk, OWNER, 'avocat', view['id'])
        return view

    def test_superpdp_reception_suivi_et_comptes_rendus(self):
        self._setup('superpdp')
        en = {'number': 'FA-2026-0042', 'issue_date': '2026-10-15', 'seller': {'name': 'Fournisseur Fictif SAS'},
              'totals': {'total_with_vat': '120.00', 'total_without_vat': '100.00', 'total_vat_amount': {'value': '20.00', 'currency_code': 'EUR'}}}
        self.fake.on('GET', '/v1.beta/invoices?', {'data': [{'id': 77, 'direction': 'in', 'en_invoice': en, 'events': [{'status_code': 'fr:202'}]}]})
        self.fake.on('GET', '/v1.beta/ereportings?', {'data': [{'id': 5, 'kind': 'payment', 'role_code': 'SE', 'start_period': '2026-10-01',
                                                                 'end_period': '2026-10-10', 'events': [{'status_code': 'ppf:flow-10-ack'}]}]})
        out = ef.sync(self.desk, OWNER)
        self.assertEqual((out['recues'], out['rapports']), (1, 1))
        r = ef.received(self.desk)[0]
        self.assertEqual((r['numero'], r['emetteur'], r['total_ttc'], r['etat']), ('FA-2026-0042', 'Fournisseur Fictif SAS', '120.00', 'fr:202'))
        self.assertEqual(ef.label('fr:202'), 'Reçue par la plateforme du destinataire')
        rep = ef.reports(self.desk)[0]
        self.assertEqual((rep['nature'], rep['role'], rep['debut']), ('Paiements', 'Vendeur', '2026-10-01'))
        calls = [c[1] for c in self.fake.calls if '/v1.beta/invoices?' in c[1]]
        self.assertTrue(any('expand%5B%5D=en_invoice' in u and 'direction=in' in u for u in calls))
        # téléchargement et enregistrement dans Nextcloud
        self.fake.on('GET', '/invoices/77/download', b'%PDF-1.4 facture fictive')
        dl = ef.download(self.desk, OWNER, 'avocat', r['id'])['download']
        self.assertEqual(base64.b64decode(dl['base64'])[:4], b'%PDF')
        self.assertTrue(dl['filename'].endswith('.pdf'))
        html = ef.html(self.desk, '/p', OWNER, 'avocat')
        for text in ('Factures reçues', 'FA-2026-0042', 'Comptes rendus de déclaration', 'Export pour le comptable', 'm5670/efacture/actualiser'):
            self.assertIn(text, html)

    def test_b2brouter_reception_et_envoi_du_factur_x(self):
        self._setup('b2brouter')
        self.fake.on('GET', '/accounts/compte-fictif/invoices?', {'invoices': [{'id': 9, 'number': 'B-12', 'date': '2026-10-12', 'state': 'new',
                                                                                 'contact': {'name': 'Prestataire Fictif'}, 'total': 60, 'taxable_base': 50,
                                                                                 'tax_amount': 10, 'currency': 'EUR'}]})
        self.fake.on('GET', '/accounts/compte-fictif/tax_reports?', {'tax_reports': [{'id': 3, 'type': 'TaxReport', 'state': 'registered',
                                                                                     'sent_at': '2026-10-13T08:00:00Z', 'errors': []}]})
        out = ef.sync(self.desk, OWNER)
        self.assertEqual((out['recues'], out['rapports']), (1, 1))
        self.assertEqual(ef.received(self.desk)[0]['emetteur'], 'Prestataire Fictif')
        self.assertEqual(ef.label(ef.reports(self.desk)[0]['etat']), 'Enregistrée')
        from agent import adaptateurs5652 as ad
        from agent.facturation5653 import QUALIFIED_TRANSPORTS
        self.assertIn('b2brouter', QUALIFIED_TRANSPORTS)
        self.fake.on('POST', '/invoices/import?', {'invoice': {'id': 41, 'state': 'sending'}})
        ctx = ad.Context('b2brouter', 'test', {'account_id': 'compte-fictif'}, lambda n: 'cle-fictive-123456', lambda: '', None, self.fake)
        res = ad.B2Brouter(ctx).call('einvoice.send_approved', {'content': b'%PDF-1.4 factur-x', 'reference': 'op-1'})
        self.assertEqual(res, {'remote_ref': '41', 'etat': 'sending'})
        method, url, data, headers = self.fake.calls[-1]
        self.assertIn('/accounts/compte-fictif/invoices/import?send_after_import=true', url)
        self.assertEqual((data, headers['Content-Type']), (b'%PDF-1.4 factur-x', 'application/octet-stream'))

    def test_sans_plateforme_message_clair(self):
        with self.assertRaisesRegex(Stop, 'aucune_plateforme_de_facturation_verifiee'):
            ef.sync(self.desk, OWNER)

    def test_export_comptable_par_periode(self):
        ef.ensure_schema(self.desk)
        self.desk.db.execute("INSERT INTO ef5670_recues(id,provider,remote_id,numero,date,emetteur,total_ttc,total_ht,tva,etat,recu_le,maj) "
                             "VALUES('superpdp:1','superpdp','1','FA-1','2026-09-10','Fournisseur Fictif','12.00','10.00','2.00','fr:205','x','x')")
        self.desk.db.execute("INSERT INTO ef5670_recues(id,provider,remote_id,numero,date,emetteur,total_ttc,total_ht,tva,etat,recu_le,maj) "
                             "VALUES('superpdp:2','superpdp','2','FA-2','2026-08-01','Hors période','12.00','10.00','2.00','fr:205','x','x')")
        self.desk.db.commit()
        doc = {'id': 'd1', 'number': 'F-2026-0007', 'issue_date': '2026-09-20', 'kind_label': 'Facture', 'client': 'Client Fictif', 'matter_name': 'DEMO',
               'ht_cents': 100000, 'ttc_cents': 120000, 'state_label': 'Émise', 'suivi': 'Approuvée', 'suivi_mauvais': False, 'payment_label': 'Réglée'}
        with patch.object(ef, 'issued', return_value=[doc]):
            out = ef.export(self.desk, OWNER, 'avocat', '2026-09-01', '2026-09-30', with_files=False)
        z = zipfile.ZipFile(io.BytesIO(base64.b64decode(out['download']['base64'])))
        name = lambda start: next(n for n in z.namelist() if n.startswith(start) and n.endswith('.csv'))
        emises = z.read(name('factures-emises-')).decode('utf-8-sig')
        recues = z.read(name('factures-recues-')).decode('utf-8-sig')
        self.assertIn('F-2026-0007;2026-09-20;Facture;Client Fictif;DEMO;1000,00;200,00;1200,00;Émise;Approuvée;Réglée', emises)
        self.assertIn('FA-1', recues)
        self.assertNotIn('Hors période', recues)
        self.assertIn('LISEZMOI.txt', z.namelist())
        with self.assertRaisesRegex(Stop, 'periode_invalide'):
            ef.period('2026-09-30', '2026-09-01')
        self.assertEqual(len(ef.period()), 2)   # par défaut : le mois précédent

    def test_droit_facturation_requis(self):
        with self.assertRaises(Stop):
            ef.export(self.desk, 'assistant@example.test', 'assistant', '', '', with_files=False)


# ---------------------------------------------------------------------------------------------------- garde des accès
class Garde(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_adresse_reelle_derriere_le_proxy(self):
        self.assertEqual(garde.client_ip({'REMOTE_ADDR': '127.0.0.1', 'HTTP_X_FORWARDED_FOR': '8.8.8.8, ' + PUBLIC}), PUBLIC)   # 1re adresse forgée
        self.assertEqual(garde.client_ip({'REMOTE_ADDR': PUBLIC, 'HTTP_X_FORWARDED_FOR': '8.8.8.8'}), PUBLIC)   # client direct : en-tête ignoré
        self.assertEqual(garde.client_ip({'REMOTE_ADDR': '127.0.0.1', 'HTTP_X_FORWARDED_FOR': 'pas-une-adresse'}), '127.0.0.1')
        from agent.connexion5648 import _client
        self.assertEqual(_client({'REMOTE_ADDR': '127.0.0.1', 'HTTP_X_FORWARDED_FOR': '1.1.1.1, ' + PUBLIC}), PUBLIC)

    def test_sondes_puis_blocage_progressif(self):
        g = garde.open_garde(self.tmp.name)
        try:
            self.assertTrue(garde.is_probe('/wp-login.php'))
            self.assertTrue(garde.is_probe('/.env'))
            self.assertFalse(garde.is_probe('/aujourdhui'))
            for _ in range(4):
                self.assertIsNone(g.record(PUBLIC, 'sonde'))
            ban = g.record(PUBLIC, 'sonde')
            self.assertEqual((ban['reason'], ban['duration']), ('sondes', 900))
            self.assertTrue(g.banned(PUBLIC))
            g.lift(PUBLIC, 'admin@example.test')
            self.assertIsNone(g.banned(PUBLIC))
            self.assertEqual(g.ban(PUBLIC, 'connexions')['duration'], 3600)   # récidive : 1 heure
            self.assertEqual(g.ban(PUBLIC, 'connexions')['duration'], 86400)
            for ip in ('127.0.0.1', '10.0.0.5', '192.168.1.20'):   # boucle locale, réseau privé (proxy Docker de Nextcloud)
                for _ in range(12):
                    g.record(ip, 'echec_connexion')
                self.assertIsNone(g.banned(ip))
            g.allow('1.1.1.1', 'bureau', 'admin@example.test')
            for _ in range(12):
                g.record('1.1.1.1', 'sonde')
            self.assertIsNone(g.banned('1.1.1.1'))
        finally:
            g.db.close()
        log = (Path(self.tmp.name) / 'securite-acces.log').read_text(encoding='utf-8')
        self.assertIn('AXIORHUB-BLOCAGE ip=%s motif=sondes' % PUBLIC, log)

    def test_service_web_refuse_avant_tout_traitement(self):
        from agent.web import App
        app = SimpleNamespace(_state_dir5642=lambda: self.tmp.name)
        seen = []
        start = lambda status, headers: seen.append(status)
        env = lambda path: {'PATH_INFO': path, 'REMOTE_ADDR': '127.0.0.1', 'HTTP_X_FORWARDED_FOR': PUBLIC}
        for _ in range(5):
            App._guard5670(app, env('/wp-admin/setup.php'), start)
        self.assertEqual(seen[-1], '404 Not Found')
        self.assertIsNone(App._guard5670(app, env('/healthz'), start))
        App._guard5670(app, env('/aujourdhui'), start)
        self.assertEqual(seen[-1], '403 Forbidden')   # adresse bloquée après 5 sondes
        self.assertIsNone(App._guard5670(app, {'PATH_INFO': '/aujourdhui', 'REMOTE_ADDR': '127.0.0.1'}, start))

    def test_echecs_de_connexion_comptes(self):
        from agent import connexion5648 as cx48
        db = cx48.connect(self.tmp.name)
        try:
            g = garde.open_garde(self.tmp.name)
            for _ in range(10):
                g.record(PUBLIC, 'echec_connexion')
            self.assertEqual(g.banned(PUBLIC)['reason'], 'connexions')
            g.db.close()
        finally:
            db.close()
        html = garde.section_html(self.tmp.name, PUBLIC)
        self.assertIn('m5670/securite/lever', html)
        self.assertIn('Échecs de connexion', html)


# ---------------------------------------------------------------------------------------------------- PHP
class Php(unittest.TestCase):
    def test_versions(self):
        self.assertEqual(php5670.assess('8.3.36')['etat'], 'corrige')
        self.assertEqual(php5670.assess('8.4.26')['etat'], 'vulnerable')
        self.assertEqual(php5670.assess('8.4.26')['corrigee'], '8.4.27')
        self.assertEqual(php5670.assess('8.1.30')['etat'], 'vulnerable')
        self.assertEqual(php5670.assess('8.3.6-0ubuntu0.24.04.9', packaged=True)['etat'], 'corrige')

    def test_machine_et_paquet(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / 'php8.3-common'
            doc.mkdir()
            with gzip.open(doc / 'changelog.Debian.gz', 'wt', encoding='utf-8') as f:
                f.write('php8.3 (8.3.6-0ubuntu0.24.04.9) noble-security\n  * SECURITY UPDATE: CVE-2026-109618\n  * CVE-2026-109619\n')
            runner = lambda *a, **k: SimpleNamespace(stdout='8.3.6')
            php5670._CACHE.clear()
            st = php5670.host_status(runner=runner, which=lambda n: '/usr/bin/php', doc_root=tmp, now=1)
            self.assertEqual((st['etat'], st['source']), ('corrige', 'paquet de la distribution'))
            php5670._CACHE.clear()
            st = php5670.host_status(runner=runner, which=lambda n: '/usr/bin/php', doc_root=tmp + '/vide', now=1)
            self.assertEqual(st['etat'], 'vulnerable')
            self.assertIn('PHP2Shell', php5670.health_item(st)['text'])
            php5670._CACHE.clear()
            with patch.object(php5670.glob, 'glob', return_value=[]):   # machine de recette : un /usr/bin/php8.x réel ne doit pas compter
                self.assertEqual(php5670.host_status(which=lambda n: None, doc_root=tmp + '/autre', now=1)['present'], False)
            self.assertIsNone(php5670.health_item({'present': False}))
            php5670._CACHE.clear()


# ---------------------------------------------------------------------------------------------------- visio, lot, fichiers, bandeau
class Visio(base.Base):
    def test_reglage_et_liens(self):
        with self.assertRaisesRegex(Stop, 'adresse_visio_https_requise'):
            visio.save(self.desk, 'admin@example.test', 'http://visio.example.test')
        with self.assertRaisesRegex(Stop, 'visio_non_configuree'):
            visio.room(self.desk)
        visio.save(self.desk, 'admin@example.test', 'https://visio.example.test/')
        a, b = visio.room(self.desk)['url'], visio.room(self.desk)['url']
        self.assertTrue(a.startswith('https://visio.example.test/Cabinet-'))
        self.assertNotEqual(a, b)
        data = visio.with_link(self.desk, {'title': 'Rendez-vous', 'location': '', 'notes': 'Ordre du jour', 'visio': '1'})
        self.assertIn('https://visio.example.test/Cabinet-', data['location'])
        self.assertTrue(data['notes'].startswith('Ordre du jour\n\nVisioconférence (Jitsi) : https://'))
        self.assertEqual(visio.with_link(self.desk, {'title': 'x'}), {'title': 'x'})
        from agent.web5634 import _visio5670
        self.assertIn('name="visio"', _visio5670(self.desk))


class Lot(base.Base):
    def test_dossier_etabli_sinon_sans_dossier(self):
        calls = []
        reports = {'a' * 64: {'matter': MID, 'source_uid': '11', 'source_mailbox': 'INBOX'}}

        def reply(desk, uid, matter, instruction, with_attachments, owner, box=None, folder=None, without_matter=False):
            calls.append((uid, matter, folder, without_matter))
            return {'job_id': len(calls)}
        with patch('agent.boite5625.request_reply', side_effect=reply), patch('agent.mailbox.Mailbox'), \
                patch('agent.desk.report_for', side_effect=lambda c, k: reports[k] if k in reports else (_ for _ in ()).throw(Stop('rapport_absent'))):
            out = lot.traiter(self.desk, OWNER, [{'key': 'a' * 64}, {'uid': '12', 'dossier': 'INBOX', 'key': 'b' * 64}])
            with patch('agent.membres5650.viewer_hidden', return_value=frozenset({MID})):   # dossier réservé à un autre membre
                with self.assertRaisesRegex(Stop, 'dossier_reserve'):
                    lot.traiter(self.desk, OWNER, [{'key': 'a' * 64}])
        self.assertEqual(calls, [('11', MID, 'INBOX', False), ('12', '', 'INBOX', True)])
        self.assertIn('1 dans leur dossier, 1 sans dossier', out['message'])
        with self.assertRaisesRegex(Stop, 'vingt_cinq_courriels_maximum'):
            lot.traiter(self.desk, OWNER, [{'uid': str(i)} for i in range(26)])
        with self.assertRaisesRegex(Stop, 'aucun_courriel_selectionne'):
            lot.traiter(self.desk, OWNER, [])

    def test_onglet_courriels_du_dossier(self):
        self.desk.db.execute('INSERT OR REPLACE INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?)',
                             ('c' * 64, 'needs_action', 'observed', 'x', MID, 'Demande de pièces', 'client@example.test', '2026-10-20T08:00:00+00:00', self.desk.now(), ''))
        self.desk.db.commit()
        from agent.vue5651 import courriels_html, TABS
        self.assertIn(('courriels', 'Courriels'), TABS)
        m = next(x for x in load_matters(self.desk.c) if x['id'] == MID)
        html = courriels_html(self.desk, {}, '/p', m, {})
        self.assertIn('data-c5670-key="%s"' % ('c' * 64), html)
        self.assertIn('Traiter avec l’IA', html)


class Fichiers(base.Base):
    def test_racines_et_dossiers_reserves(self):
        root = load_matters(self.desk.c)[0]['path'].rsplit('/', 1)[0]
        self.desk.c['nextcloud']['roots'] = [root]
        self.assertTrue(fic.allowed(self.desk, root + '/DEMO/piece.pdf').endswith('piece.pdf'))
        with self.assertRaisesRegex(Stop, 'chemin_hors_espace_du_cabinet'):
            fic.allowed(self.desk, '/Ailleurs/secret.pdf')
        mid_path = next(x for x in load_matters(self.desk.c) if x['id'] == MID)['path']
        with patch('agent.membres5650.viewer_hidden', return_value=frozenset({MID})):
            with self.assertRaisesRegex(Stop, 'dossier_reserve'):
                fic.allowed(self.desk, mid_path + '/note.docx')

        class Dav:
            def list_folder(self, path):
                return [{'path': path + '/Sous-dossier', 'directory': True, 'size': 0}, {'path': path + '/Convention.pdf', 'directory': False, 'size': 2048}]

            def stat(self, path):
                return {'path': path, 'size': 12, 'etag': 'e'}

            def download(self, meta):
                return b'%PDF-1.4 ok'
        out = fic.browse(self.desk, mid_path, dav=Dav())
        self.assertEqual([x['nom'] for x in out['entrees']], ['Sous-dossier', 'Convention.pdf'])
        files = fic.attachments(self.desk, [mid_path + '/Convention.pdf'], dav=Dav())
        self.assertEqual(files, [('Convention.pdf', b'%PDF-1.4 ok', 'application/pdf')])
        with self.assertRaisesRegex(Stop, 'dix_pieces_jointes_maximum'):
            fic.attachments(self.desk, [mid_path + '/f%d.pdf' % i for i in range(11)], dav=Dav())

    def test_courriel_avec_documents_nextcloud(self):
        from agent import webmail5628
        with patch('agent.fichiers5670.attachments', return_value=[('Convention.pdf', b'%PDF-1.4 ok', 'application/pdf')]):
            msg, _ = webmail5628.build(self.desk, {'mode': 'nouveau', 'to': 'client@example.test', 'subject': 'Pièces', 'html': '<p>Bonjour</p>',
                                                   'nextcloud': ['/Dossiers/DEMO/Convention.pdf']})
        att = [p for p in msg.iter_attachments()]
        self.assertEqual([(a.get_filename(), a.get_content_type()) for a in att], [('Convention.pdf', 'application/pdf')])


class Bandeau(base.Base):
    def test_masquer_jusqu_au_changement(self):
        from agent import budget5668
        rows = [{'provider': 'ext', 'enabled': True, 'monthly': 20.0, 'exhausted': False, 'ratio': 0.95, 'spent': 19.0, 'uncertain': 0.0,
                 'uncertain_count': 0, 'in_flight': 0.0}]
        with patch.object(budget5668, 'status', return_value=rows):
            self.assertIn('Masquer', budget5668.banner_html(self.desk, '/p', owner=OWNER))
            budget5668.dismiss(self.desk, OWNER)
            self.assertEqual(budget5668.banner_html(self.desk, '/p', owner=OWNER), '')
            self.assertIn('bientôt atteint', budget5668.banner_html(self.desk, '/p', owner='autre@example.test'))   # par membre
        rows[0].update(exhausted=True, ratio=1.0)
        with patch.object(budget5668, 'status', return_value=rows):
            self.assertIn('atteint', budget5668.banner_html(self.desk, '/p', owner=OWNER))   # nouvel état : le bandeau revient


class Routes(unittest.TestCase):
    def test_droits(self):
        from agent import web5670, membres5650, standalone_auth
        self.assertTrue(web5670.allowed('assistant', 'courriels/traiter'))
        self.assertFalse(web5670.allowed('assistant', 'efacture/actualiser'))
        self.assertTrue(web5670.allowed('avocat', 'efacture/enregistrer'))
        self.assertFalse(web5670.allowed('avocat', 'securite/lever'))
        self.assertTrue(web5670.allowed('administrateur', 'visio/reglage'))
        self.assertEqual(membres5650.category('POST', '/api440/m5670/courriels/traiter'), 'deposer')
        self.assertEqual(membres5650.category('POST', '/api440/m5670/efacture/actualiser'), 'facturation')
        self.assertEqual(membres5650.category('POST', '/api440/m5670/securite/lever'), 'reglages')
        self.assertTrue(standalone_auth.allowed('avocat', 'POST', '/api440/m5670/courriels/traiter'))
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/m5670/visio/reglage'))

    def test_fichiers_servis_et_styles(self):
        from agent.web440 import STATIC
        root = Path(__file__).resolve().parents[1]
        self.assertIn('/static/v5670.js', STATIC)
        css = (root / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8')
        self.assertIn('.c5670-searchbar', css)
        self.assertIn('v5670.js', (root / 'agent' / 'shell501.py').read_text(encoding='utf-8'))

    @unittest.skipUnless(shutil.which('bash') and __import__('os').name != 'nt', 'bash Linux absent (sous Windows : bash de WSL)')
    def test_scripts_du_serveur(self):
        root = Path(__file__).resolve().parents[1]
        for rel in ('deploy/securite/durcir-serveur.sh', 'deploy/jitsi/installer-jitsi.sh'):
            r = subprocess.run(['bash', '-n', str(root / rel)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        text = (root / 'deploy/jitsi/installer-jitsi.sh').read_text(encoding='utf-8')
        for needle in ('127.0.0.1:$PORT_WEB', 'ENABLE_AUTH 1', "PORT_MEDIA='10010'"):
            self.assertIn(needle, text)


if __name__ == '__main__':
    unittest.main()
