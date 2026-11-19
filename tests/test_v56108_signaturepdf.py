"""5.6.108, lot Z : SignaturePDF, service facultatif de signature par lien sur la version serveur.
Z1 guide (français, anglais : étapes, licence, confidentialité, valeur juridique prudente) ; Z2 connecteur (adresse HTTPS, réglage de
l'administrateur, « Tester la connexion » sans effet, 409) ; Z3 « Faire signer par lien » (bouton seulement si configuré, version serveur,
rôle et droit « envoyer », 404 neutre, politique des données, pause, un seul dépôt, idempotence, clés au coffre) ; Z4 retour du PDF signé
(récupération par l'interface d'échange ou dépôt manuel, original intact, relecture, registre, rejeu) ; Z5 clôture ; Z6 aucune écriture
pendant un GET ; Z7 export ; Z8 textes en/de/es.
Faux service SignaturePDF local (serveur HTTP de test sur 127.0.0.1) imitant les routes publiques de SignaturePDF ; stockage simulé ;
données fictives. Aucun vrai SignaturePDF, aucun appel réseau extérieur, aucun modèle d'IA."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import re
import sqlite3
import threading
import unittest
from unittest.mock import patch

from test_v5689_dossier import AVOCAT, ASSISTANT, Base as BaseDossier, DOSSIER, MID
from agent import data_policy5689 as dp, membres5650, signaturepdf56108 as Z, standalone_auth
from agent.acces5683 import today_of
from agent.common import Stop

ROOT = Path(__file__).resolve().parents[1]
ADMIN = ('admin@example.test', 'administrateur')
AUTH = {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}
CHEMIN = DOSSIER + '/Contrat DURAND MARTIN.pdf'
PDF = b'%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n% DURAND c. MARTIN, document fictif\n%%EOF\n'
COMMIT = 'abc1234'


class FauxSignaturePDF(BaseHTTPRequestHandler):
    """Routes publiques de SignaturePDF (app.php, ApiController) : page de signature, partage, nombre de calques, récupération, suppression."""
    etat = None

    def log_message(self, *a):
        pass

    def _rep(self, code, corps=b'', type_='text/html; charset=UTF-8'):
        self.send_response(code)
        self.send_header('Content-Type', type_)
        self.send_header('Content-Length', str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def do_GET(self):
        e = self.etat
        e['get'].append(self.path)
        if self.path == '/signature':
            if e['page'] == 'autre':
                return self._rep(200, b'<html><title>Autre site</title></html>')
            corps = ('<html><head><title>Signature PDF - Signer un PDF en ligne</title></head><body>'
                     + ('<form id="form_sharing" action="/share" method="post"></form><input type="checkbox" id="checkbox_encryption">' if e['partage'] else '')
                     + '<footer><a href="https://github.com/24eme/signaturepdf/tree/%s">%s</a></footer></body></html>' % (COMMIT, COMMIT))
            return self._rep(200, corps.encode('utf-8'))
        if self.path == '/api/share/new':
            return self._rep(405 if e['api'] else 404, b'Method Not Allowed')
        m = re.fullmatch(r'/signature/([a-z0-9]+)/nblayers', self.path)
        if m:
            return self._rep(200 if m.group(1) in e['partages'] else 404, str(e['calques']).encode())
        m = re.fullmatch(r'/api/share/get/([a-z0-9]+)/([A-Za-z0-9]+)', self.path)
        if m:
            p = e['partages'].get(m.group(1))
            if not p or p['cle'] != m.group(2):
                return self._rep(403, b'{"message": "Unauthorized access"}', 'application/json')
            return self._rep(200, p['pdf'] + (b'%% calques de signature : %d\n' % e['calques']), 'application/pdf')
        m = re.fullmatch(r'/api/share/delete/([a-z0-9]+)/([A-Za-z0-9]+)', self.path)
        if m:
            p = e['partages'].get(m.group(1))
            if not p or p['admin'] != m.group(2):
                return self._rep(403, b'{"message": "Unauthorized access"}', 'application/json')
            del e['partages'][m.group(1)]
            e['suppressions'] += 1
            return self._rep(200, b'{"message": "Shared PDF successfully deleted"}', 'application/json')
        return self._rep(404, b'Not Found')

    def do_POST(self):
        e = self.etat
        longueur = int(self.headers.get('Content-Length') or 0)
        corps = self.rfile.read(longueur)
        e['post'].append((self.path, corps))
        if self.path != '/api/share/new':
            return self._rep(404, b'Not Found')
        if e['coupure']:
            self.close_connection = True
            self.wfile.flush()
            self.connection.shutdown(2)
            return None
        if e['refus']:
            return self._rep(500, b'{"message": "Sharing folder is not writable"}', 'application/json')
        if b'name="duration"' not in corps or b'name="pdf"' not in corps or b'%PDF' not in corps:
            return self._rep(400, b'{"message": "Invalid file uploaded"}', 'application/json')
        duree = corps.split(b'name="duration"\r\n\r\n', 1)[1].split(b'\r\n', 1)[0].decode()
        pdf = b'%PDF' + corps.split(b'%PDF', 1)[1].rsplit(b'\r\n--', 1)[0]
        h, cle, admin = 'h%07dabcdef' % len(e['post']), 'Cle%06dSym' % len(e['post']), 'Adm%06dKey' % len(e['post'])
        e['partages'][h] = {'cle': cle, 'admin': admin, 'pdf': pdf, 'duree': duree}
        rep = {'hash': h, 'symmetrickey': cle, 'adminkey': admin, 'url': 'http://interne:8080/signature/%s#%s' % (h, cle)}
        return self._rep(201, json.dumps(rep).encode(), 'application/json')


class Base(BaseDossier):
    def setUp(self):
        super().setUp()
        self.addCleanup(membres5650.clear)
        self.state = self.desk.c['state_dir']
        self.service = {'get': [], 'post': [], 'partages': {}, 'calques': 0, 'api': True, 'partage': True, 'page': 'signaturepdf', 'refus': False,
                        'coupure': False, 'suppressions': 0}
        gestion = type('Gestion', (FauxSignaturePDF,), {'etat': self.service})
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), gestion)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = 'http://127.0.0.1:%d' % self.httpd.server_address[1]
        p = patch.object(Z, 'est_poste', lambda auth=None: False)
        p.start()
        self.addCleanup(p.stop)
        self.nc.add(CHEMIN, PDF)
        self.n = 0

    def cle(self):
        self.n += 1
        return 'cle-essai-%04d' % self.n

    def configurer(self, tester=True, cabinet=True):
        Z.enregistrer_reglages(self.desk, {'version': Z.reglages(self.desk)['version'], 'adresse': self.base, 'duree': '+1 week', 'echange': 'auto',
                                           'cabinet': cabinet}, ADMIN[0])
        if tester:
            return Z.tester(self.desk, {}, ADMIN[0])
        return None

    def api(self, route, corps=None, args=None, qui=AVOCAT, methode='POST', brut=None, type_='application/json', csrf='jeton'):
        raw = brut if brut is not None else json.dumps(corps or {}).encode('utf-8')
        env = {'HTTP_X_AXIORHUB_USER': qui[0], 'HTTP_X_AXIORHUB_ROLE': qui[1], 'HTTP_ORIGIN': AUTH['origin'], 'HTTP_X_CSRF_TOKEN': csrf,
               'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': type_, 'wsgi.input': io.BytesIO(raw)}
        r = Z.api(env, self.desk, AUTH, '/p', Z.PREFIXE + route, args or {}, methode)
        if r['kind'].startswith('application/json'):
            r['json'] = json.loads(r['body'])
        return r

    def preparer(self, chemin=CHEMIN, qui=AVOCAT, cle=None, **plus):
        return self.api('preparer', {'matter': MID, 'chemin': chemin, 'cle': cle or self.cle(), 'confirmer': True, **plus}, qui=qui)

    def ligne(self, ident):
        return dict(self.desk.db.execute('SELECT * FROM signaturepdf56108 WHERE id=?', (ident,)).fetchone())

    def posts(self):
        return [p for p, _ in self.service['post'] if p == '/api/share/new']


# ================================================================================ Z1 : guide
class Guide(unittest.TestCase):
    def test_Z1_01_guide_francais_pas_a_pas(self):
        texte = (ROOT / 'docs' / 'INSTALLATION-SIGNATUREPDF.md').read_text(encoding='utf-8')
        for attendu in ('Ubuntu 24.04', 'Apache', 'certbot --apache', 'PDF_STORAGE_PATH', 'PDF_STORAGE_ENCRYPTION', '/cron', 'Résultat attendu',
                        'En cas de problème', 'AGPL-3.0', 'signature électronique simple', 'Yousign', 'DocuSign', 'Tester la connexion',
                        'Faire signer par lien', 'Déposer le PDF signé', 'Sauvegarde', 'Mettre à jour', 'Arrêter', 'git pull',
                        'upload_max_filesize', 'signature.example.test', 'Require local'):
            self.assertIn(attendu, texte, attendu)
        self.assertIn('non essayé', texte.lower())                       # ce qui n'a pas été essayé est dit
        for attendu in ('## H. Installation locale (poste)', 'Instance du cabinet', 'ne s\'ouvrent que sur cet ordinateur', 'Dockerfile',
                        'docker build -t signaturepdf https://github.com/24eme/signaturepdf.git#master', '-S 127.0.0.1:8090', 'Docker Desktop'):
            self.assertIn(attendu, texte, attendu)                       # complément du 19/11 : postes Ubuntu et Windows
        self.assertNotRegex(texte, r'\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d+\.\d+')   # aucune adresse privée réelle

    def test_Z1_02_guide_anglais(self):
        texte = (ROOT / 'docs' / 'INSTALLATION-SIGNATUREPDF.en.md').read_text(encoding='utf-8')
        for attendu in ('Ubuntu 24.04', 'certbot --apache', 'PDF_STORAGE_PATH', 'AGPL-3.0', 'If something goes wrong', 'Expected result', 'simple electronic signature'):
            self.assertIn(attendu, texte, attendu)


# ================================================================================ Z2 : connecteur
class Connecteur(Base):
    def test_Z2_01_adresse_https_seulement_http_de_bouclage_admis(self):
        self.assertEqual(Z.adresse_valide('https://signature.example.test/'), 'https://signature.example.test')
        self.assertEqual(Z.adresse_valide('https://signature.example.test:8443/pdf'), 'https://signature.example.test:8443/pdf')
        self.assertEqual(Z.adresse_valide('http://127.0.0.1:8090'), 'http://127.0.0.1:8090')          # instance locale d'un poste
        self.assertTrue(Z.est_locale('http://localhost:8090') and not Z.est_locale('https://signature.example.test'))
        for mauvaise in ('http://signature.example.test', 'http://127.0.0.2:8090', 'https://moi:secret@signature.example.test', 'https://signature.example.test/?x=1',
                         'https://signature.example.test/#a', 'ftp://signature.example.test', 'https://exa mple.test', 'javascript:alert(1)'):
            with self.subTest(adresse=mauvaise), self.assertRaises(Stop):
                Z.adresse_valide(mauvaise)

    def test_Z2_02_reglage_administrateur_seulement_et_version(self):
        self.assertFalse(Z.allowed('avocat', 'POST', Z.PREFIXE + 'reglages'))
        self.assertFalse(Z.allowed('avocat', 'POST', Z.PREFIXE + 'tester'))
        self.assertTrue(Z.allowed('administrateur', 'POST', Z.PREFIXE + 'reglages'))
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/' + Z.PREFIXE + 'reglages'))
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/' + Z.PREFIXE + 'preparer'))
        r = self.api('reglages', {'version': 0, 'adresse': self.base}, qui=AVOCAT)
        self.assertTrue(r['status'].startswith('403'))
        r = self.api('reglages', {'version': 0, 'adresse': self.base, 'duree': '+1 day'}, qui=ADMIN)
        self.assertTrue(r['status'].startswith('200'), r['body'])
        r = self.api('reglages', {'version': 0, 'adresse': self.base}, qui=ADMIN)       # version périmée
        self.assertTrue(r['status'].startswith('409'), r['body'])
        r = self.api('reglages', {'version': 1, 'adresse': self.base, 'duree': '+3 days'}, qui=ADMIN)
        self.assertEqual(r['json']['error'], 'duree_invalide')

    def test_Z2_03_tester_la_connexion_sans_effet(self):
        out = self.configurer()
        self.assertTrue(out['ok'], out)
        self.assertTrue(out['api'])
        self.assertEqual(self.service['post'], [])                        # aucun dépôt, aucune écriture chez le service
        self.assertEqual(sorted(set(self.service['get'])), ['/api/share/new', '/signature'])
        self.assertTrue(Z.configure(self.desk))
        self.assertEqual(Z.mode_effectif(self.desk), 'auto')
        self.assertEqual(Z.reglages(self.desk)['test']['version'], COMMIT)
        # autre adresse : le test est à refaire, le bouton disparaît
        Z.enregistrer_reglages(self.desk, {'version': Z.reglages(self.desk)['version'], 'adresse': 'http://127.0.0.1:9'}, ADMIN[0])
        self.assertFalse(Z.configure(self.desk))

    def test_Z2_04_interface_d_echange_absente_parcours_manuel(self):
        self.service['api'] = False
        out = self.configurer()
        self.assertTrue(out['ok'])
        self.assertFalse(out['api'])
        self.assertEqual(Z.mode_effectif(self.desk), 'manuel')

    def test_Z2_05_echecs_du_test_dits(self):
        self.service['page'] = 'autre'
        self.assertFalse(self.configurer()['ok'])
        self.service['page'], self.service['partage'] = 'signaturepdf', False
        out = Z.tester(self.desk, {}, ADMIN[0])
        self.assertFalse(out['ok'])
        self.assertIn('PDF_STORAGE_PATH', json.dumps(out['etapes'], ensure_ascii=False))
        self.assertFalse(Z.configure(self.desk))
        Z.enregistrer_reglages(self.desk, {'version': Z.reglages(self.desk)['version'], 'adresse': 'http://127.0.0.1:9'}, ADMIN[0])
        out = Z.tester(self.desk, {}, ADMIN[0])
        self.assertFalse(out['ok'])
        self.assertEqual(out['etapes'][0]['id'], 'joignable')

    def test_Z2_06_section_parametres_sans_appel_au_service(self):
        self.configurer()
        avant = len(self.service['get'])
        with patch.object(Z, '_ouvrir', side_effect=AssertionError('appel au service pendant l’affichage')):
            html = Z.reglages_html(self.desk, '/p', 'administrateur')
            autre = Z.reglages_html(self.desk, '/p', 'avocat')
        self.assertEqual(len(self.service['get']), avant)
        self.assertIn('data-api="m56108/signaturepdf/reglages"', html)
        self.assertIn('Tester la connexion', html)
        self.assertIn('Connexion vérifiée', html)
        self.assertNotIn('data-api="m56108/signaturepdf/reglages"', autre)
        with patch.object(Z, 'est_poste', lambda auth=None: True):            # complément du 19/11 : proposé sur un poste
            sur_poste = Z.reglages_html(self.desk, '/p', 'administrateur')
        self.assertIn('data-api="m56108/signaturepdf/reglages"', sur_poste)
        self.assertIn('Sur ce poste', sur_poste)


# ================================================================================ Z3 : « Faire signer par lien »
class Demande(Base):
    def test_Z3_01_bouton_seulement_si_configure_serveur_pdf_et_role(self):
        self.assertEqual(Z.bouton_html(self.desk, '/p', MID, CHEMIN, 'avocat'), '')       # non configuré
        self.configurer(tester=False)
        self.assertEqual(Z.bouton_html(self.desk, '/p', MID, CHEMIN, 'avocat'), '')       # pas encore testé
        Z.tester(self.desk, {}, ADMIN[0])
        bouton = Z.bouton_html(self.desk, '/p', MID, CHEMIN, 'avocat')
        self.assertIn('Faire signer par lien', bouton)
        self.assertIn('/p/signature-par-lien?dossier=DOS-001', bouton)
        self.assertEqual(Z.bouton_html(self.desk, '/p', MID, DOSSIER + '/Note.docx', 'avocat'), '')
        self.assertEqual(Z.bouton_html(self.desk, '/p', MID, CHEMIN, 'assistant'), '')

    def test_Z3_01b_entree_du_point_de_montage_du_lot_F(self):
        self.configurer()
        b = Z.boutons_fichier_html(self.desk, None, '/p', 'nextcloud', CHEMIN, 'avocat', 'Contrat DURAND MARTIN.pdf')
        self.assertIn('/p/signature-par-lien?dossier=DOS-001', b)
        self.assertIn('class="sr-only"', b)
        self.assertEqual(Z.boutons_fichier_html(self.desk, None, '/p', 'local', CHEMIN, 'avocat'), '')
        self.assertEqual(Z.boutons_fichier_html(self.desk, None, '/p', 'nextcloud', '/Ailleurs/Contrat.pdf', 'avocat'), '')
        self.assertEqual(Z.boutons_fichier_html(self.desk, None, '/p', 'nextcloud', CHEMIN, 'assistant'), '')
        with patch.object(Z, 'configure', side_effect=RuntimeError('panne')):
            self.assertEqual(Z.boutons_fichier_html(self.desk, None, '/p', 'nextcloud', CHEMIN, 'avocat'), '')   # la liste reste utilisable

    def test_Z3_02_un_seul_depot_lien_rendu_cles_au_coffre(self):
        self.configurer()
        cle = self.cle()
        r = self.preparer(cle=cle)
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertEqual(r['json']['etat'], 'lien_cree')
        self.assertEqual(len(self.posts()), 1)
        h = next(iter(self.service['partages']))
        p = self.service['partages'][h]
        self.assertEqual(p['pdf'], PDF)
        self.assertEqual(p['duree'], '+1 week')
        self.assertEqual(r['json']['lien'], '%s/signature/%s#%s' % (self.base, h, p['cle']))   # construit sur l'adresse réglée, pas sur « url »
        self.assertNotIn(p['admin'], r['body'])
        # rejeu : même clé, aucune seconde transmission
        r2 = self.preparer(cle=cle)
        self.assertTrue(r2['json'].get('rejoue'))
        self.assertEqual(len(self.posts()), 1)
        # aucune clé du partage dans la base, le journal ni l'export ; elles sont au coffre
        dump = '\n'.join(self.desk.db.iterdump())
        for secret in (h, p['cle'], p['admin']):
            self.assertNotIn(secret, dump)
        coffre = Path(self.state) / 'vault' / 'signaturepdf56108' / r['json']['demande']
        self.assertEqual(sorted(x.name for x in coffre.glob('*.secret')), ['admin.secret', 'cle.secret', 'hash.secret'])
        for x in coffre.glob('*.secret'):
            self.assertNotIn(p['admin'], x.read_text(encoding='utf-8'))      # chiffré au repos

    def test_Z3_03_meme_cle_autre_document_409(self):
        self.configurer()
        self.nc.add(DOSSIER + '/Autre.pdf', PDF + b'%autre\n')
        cle = self.cle()
        self.preparer(cle=cle)
        r = self.preparer(chemin=DOSSIER + '/Autre.pdf', cle=cle)
        self.assertTrue(r['status'].startswith('409'), r['body'])
        self.assertEqual(len(self.posts()), 1)

    def test_Z3_04_dossier_reserve_absent_ou_chemin_hors_dossier_meme_404(self):
        self.configurer()
        membres5650.set_reserve(self.state, MID, ['martin@example.test'])
        reserve = self.preparer()
        membres5650.clear()
        membres5650.set_reserve(self.state, 'DOS-002', ['martin@example.test'])
        absent = self.api('preparer', {'matter': 'DOS-999', 'chemin': CHEMIN, 'cle': self.cle(), 'confirmer': True})
        hors = self.preparer(chemin='/Dossiers/DEUX/Bail.pdf')
        remonte = self.preparer(chemin=DOSSIER + '/../DEUX/Bail.pdf')
        for r in (reserve, absent, hors, remonte):
            self.assertTrue(r['status'].startswith('404'), r['body'])
            self.assertEqual(r['json'], {'error': 'introuvable', 'message': 'Élément introuvable.'})
        self.assertEqual(self.posts(), [])

    def test_Z3_05_role_droit_envoyer_confirmation_configuration_poste(self):
        r = self.preparer()
        self.assertEqual(r['json']['error'], 'service_non_configure')
        self.configurer()
        self.assertTrue(self.preparer(qui=ASSISTANT)['status'].startswith('403'))
        with patch('agent.membres5650.effective_rights', return_value={'envoyer': False}):
            r = self.preparer()
        self.assertEqual(r['json']['error'], 'droit_envoyer_requis')
        r = self.api('preparer', {'matter': MID, 'chemin': CHEMIN, 'cle': self.cle()})
        self.assertEqual(r['json']['error'], 'confirmation_requise')
        r = self.api('preparer', {'matter': MID, 'chemin': CHEMIN, 'confirmer': True})
        self.assertEqual(r['json']['error'], 'cle_requise')
        r = self.api('preparer', {'matter': MID, 'chemin': CHEMIN, 'cle': self.cle(), 'confirmer': True}, csrf='autre')
        self.assertTrue(r['status'].startswith('403'))
        self.nc.add(DOSSIER + '/Note.docx', b'PK fictif')
        self.assertEqual(self.preparer(chemin=DOSSIER + '/Note.docx')['json']['error'], 'pdf_requis')
        self.assertEqual(self.posts(), [])

    def test_Z3_06_politique_des_donnees_correspondance_confidentielle(self):
        self.configurer()
        confidentiel = DOSSIER + '/Lettre confidentielle au confrère.pdf'
        self.nc.add(confidentiel, PDF)
        r = self.preparer(chemin=confidentiel)
        self.assertEqual(r['json']['error'], 'correspondance_confidentielle')
        self.assertEqual(self.posts(), [])
        self.assertIn('signature_lien', dp.OUTILS)
        # mutation : sans le contrôle, le PDF partirait
        with patch.object(Z, '_politique', lambda *a, **k: None):
            self.assertTrue(self.preparer(chemin=confidentiel)['status'].startswith('200'))
        self.assertEqual(len(self.posts()), 1)

    def test_Z3_07_garde_dossier_mutation(self):
        self.configurer()
        membres5650.set_reserve(self.state, MID, ['martin@example.test'])
        self.assertTrue(self.preparer()['status'].startswith('404'))
        with patch.object(Z, '_dossier', lambda desk, mid, owner, role: {'id': MID, 'path': DOSSIER}), patch.object(Z, '_politique', lambda *a, **k: None):
            self.assertTrue(self.preparer()['status'].startswith('200'))      # le garde-fou retiré, le dépôt aurait lieu
        self.assertEqual(len(self.posts()), 1)

    def test_Z3_08_controle_d_acces_en_panne_503(self):
        self.configurer()
        with patch('agent.membres5650.hidden_for', side_effect=RuntimeError('registre illisible')):
            r = self.preparer()
        self.assertTrue(r['status'].startswith('503'), r['body'])
        self.assertEqual(self.posts(), [])

    def test_Z3_09_refus_ou_reponse_perdue_etat_reel(self):
        self.configurer()
        self.service['refus'] = True
        r = self.preparer()
        self.assertEqual(r['json']['etat'], 'echec')
        self.assertFalse(r['json']['ok'])
        self.assertIn('rien n’a été créé', r['json']['message'])
        self.service['refus'], self.service['coupure'] = False, True
        r = self.preparer()
        self.assertEqual(r['json']['etat'], 'resultat_a_verifier')
        self.assertNotIn('lien', r['json'])

    def test_Z3_10_pause_revalidee_avant_l_effet(self):
        self.configurer()
        with patch('agent.pause5629.checkpoint', side_effect=Stop('ia_en_pause')):
            r = self.preparer()
        self.assertFalse(r['status'].startswith('200'))
        self.assertEqual(self.posts(), [])

    def test_Z3_11_journal_sans_nom_ni_cle(self):
        self.configurer()
        r = self.preparer()
        lignes = [json.loads(x[0]) for x in self.desk.db.execute("SELECT data FROM audit WHERE action LIKE 'signaturepdf56108%'")]
        texte = json.dumps(lignes, ensure_ascii=False)
        self.assertIn(r['json']['demande'], texte)
        for mot in ('DURAND', 'MARTIN', MID, 'Contrat'):
            self.assertNotIn(mot, texte)


# ================================================================================ Z4 : retour du PDF signé
class Retour(Base):
    def signe_nom(self, n=''):
        return '%s/Contrat DURAND MARTIN (signé le %s)%s.pdf' % (DOSSIER, today_of(self.desk)[0].strftime('%d-%m-%Y'), n)

    def test_Z4_01_recuperer_par_l_interface_d_echange(self):
        self.configurer()
        d = self.preparer()['json']
        r = self.api('recuperer', {'demande': d['demande'], 'version': d['version'], 'cle': self.cle()})
        self.assertEqual(r['json']['error'], 'aucune_signature')
        self.service['calques'] = 2
        cle = self.cle()
        r = self.api('recuperer', {'demande': d['demande'], 'version': d['version'], 'cle': cle})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertEqual(r['json']['etat'], 'signe_range')
        self.assertEqual(self.nc.files[CHEMIN], PDF)                       # original intact
        signe = self.nc.files[self.signe_nom()]
        self.assertTrue(signe.startswith(b'%PDF') and b'calques de signature : 2' in signe)
        self.assertIn('l’original n’a pas été modifié', r['json']['message'])
        ecritures = len(self.nc.ecritures)
        rejeu = self.api('recuperer', {'demande': d['demande'], 'version': d['version'], 'cle': cle})
        self.assertTrue(rejeu['json'].get('rejoue'))
        self.assertEqual(len(self.nc.ecritures), ecritures)
        autre = self.api('recuperer', {'demande': d['demande'], 'version': self.ligne(d['demande'])['version'], 'cle': self.cle()})
        self.assertTrue(autre['status'].startswith('409'))

    def test_Z4_02_version_perimee_409(self):
        self.configurer()
        self.service['calques'] = 1
        d = self.preparer()['json']
        r = self.api('recuperer', {'demande': d['demande'], 'version': d['version'] + 5, 'cle': self.cle()})
        self.assertTrue(r['status'].startswith('409'))
        self.assertNotIn(self.signe_nom(), self.nc.files)

    def test_Z4_03_parcours_manuel_et_depot_du_pdf_signe(self):
        self.service['api'] = False
        self.configurer()
        d = self.preparer()['json']
        self.assertEqual(d['etat'], 'manuel')
        self.assertEqual(d['ouvrir'], self.base + '/signature')
        self.assertEqual(self.posts(), [])                                  # rien ne part en parcours manuel
        g = self.api('pdf', args={'demande': d['demande']}, methode='GET')
        self.assertEqual(g['body'], PDF)
        self.assertIn(('Cache-Control', 'private, no-store'), g['headers'])
        args = {'demande': d['demande'], 'version': str(d['version']), 'cle': self.cle()}
        r = self.api('deposer', args=args, brut=PDF, type_='application/pdf')
        self.assertEqual(r['json']['error'], 'pdf_identique')
        r = self.api('deposer', args=args, brut=b'pas un pdf', type_='application/pdf')
        self.assertEqual(r['json']['error'], 'pdf_invalide')
        r = self.api('deposer', args=args, brut=PDF + b'%signe\n', type_='application/pdf', csrf='autre')
        self.assertTrue(r['status'].startswith('403'))
        r = self.api('deposer', args=args, brut=PDF + b'%signe\n', type_='application/pdf')
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertEqual(self.nc.files[self.signe_nom()], PDF + b'%signe\n')
        rejeu = self.api('deposer', args=args, brut=PDF + b'%signe\n', type_='application/pdf')
        self.assertTrue(rejeu['json'].get('rejoue'))
        args2 = {**args, 'cle': self.cle(), 'version': str(self.ligne(d['demande'])['version'])}
        self.assertEqual(self.api('deposer', args=args2, brut=PDF + b'%autre\n', type_='application/pdf')['json']['error'], 'deja_range')

    def test_Z4_04_nom_existant_jamais_ecrase(self):
        self.service['api'] = False
        self.configurer()
        self.nc.add(self.signe_nom(), b'%PDF deja la')
        d = self.preparer()['json']
        r = self.api('deposer', args={'demande': d['demande'], 'version': str(d['version']), 'cle': self.cle()}, brut=PDF + b'%signe\n', type_='application/pdf')
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertEqual(self.nc.files[self.signe_nom()], b'%PDF deja la')
        self.assertEqual(self.nc.files[self.signe_nom(' (2)')], PDF + b'%signe\n')

    def test_Z4_05_document_modifie_depuis_la_demande(self):
        self.service['api'] = False
        self.configurer()
        d = self.preparer()['json']
        self.nc.files[CHEMIN] = PDF + b'%modifie\n'
        g = self.api('pdf', args={'demande': d['demande']}, methode='GET')
        self.assertTrue(g['status'].startswith('409'))

    def test_Z4_06_nouvelle_version_du_document_au_registre(self):
        from agent import livrables5689 as liv
        self.service['api'] = False
        self.configurer()
        sortie = liv.Sortie(self.desk, 'essai', 'original', 'v1', matter=MID, path=CHEMIN, mime='application/pdf', title='Contrat DURAND MARTIN')
        sortie.constater(self.nc, CHEMIN, PDF)
        doc = self.desk.db.execute("SELECT document_id, version_number FROM prepared_items5689 WHERE path=? AND lifecycle='current'", (CHEMIN,)).fetchone()
        d = self.preparer()['json']
        r = self.api('deposer', args={'demande': d['demande'], 'version': str(d['version']), 'cle': self.cle()}, brut=PDF + b'%signe\n', type_='application/pdf')
        self.assertTrue(r['status'].startswith('200'), r['body'])
        nouveau = self.desk.db.execute("SELECT document_id, version_number, path FROM prepared_items5689 WHERE path=?", (self.signe_nom(),)).fetchone()
        self.assertEqual(nouveau['document_id'], doc['document_id'])
        self.assertEqual(nouveau['version_number'], doc['version_number'] + 1)

    def test_Z4_07_autre_compte_sans_acces_meme_404(self):
        self.configurer()
        d = self.preparer()['json']
        membres5650.set_reserve(self.state, MID, ['martin@example.test'])
        for route, methode, corps, args in (('etat', 'GET', None, {'demande': d['demande']}), ('pdf', 'GET', None, {'demande': d['demande']}),
                                            ('recuperer', 'POST', {'demande': d['demande'], 'version': d['version'], 'cle': self.cle()}, None),
                                            ('clore', 'POST', {'demande': d['demande'], 'version': d['version']}, None)):
            r = self.api(route, corps, args=args, methode=methode)
            self.assertTrue(r['status'].startswith('404'), (route, r['body']))
            self.assertEqual(r['json']['error'], 'introuvable')
        inconnue = self.api('etat', args={'demande': 'sp-inconnue'}, methode='GET')
        self.assertEqual(inconnue['json'], {'error': 'introuvable', 'message': 'Élément introuvable.'})


# ================================================================================ Z5 : clôture
class Cloture(Base):
    def test_Z5_01_clore_sans_supprimer_puis_avec(self):
        self.configurer()
        d = self.preparer()['json']
        r = self.api('clore', {'demande': d['demande'], 'version': d['version']})
        self.assertIn('reste sur le service', r['json']['message'])
        self.assertEqual(self.service['suppressions'], 0)
        self.assertFalse(list((Path(self.state) / 'vault' / 'signaturepdf56108' / d['demande']).glob('*.secret')))
        d2 = self.preparer()['json']
        r = self.api('clore', {'demande': d2['demande'], 'version': d2['version'], 'supprimer_distant': True})
        self.assertIn('supprimé du service', r['json']['message'])
        self.assertEqual(self.service['suppressions'], 1)
        r = self.api('recuperer', {'demande': d2['demande'], 'version': self.ligne(d2['demande'])['version'], 'cle': self.cle()})
        self.assertEqual(r['json']['error'], 'demande_close')


# ================================================================================ Z6 : aucune écriture pendant un GET
class Lectures(Base):
    def test_Z6_01_get_sans_ecriture_et_sans_creation_de_table(self):
        self.configurer()
        avant = self.desk.db.total_changes
        for _ in range(2):
            self.assertIn('Créer le lien de signature', Z.page_html(self.desk, '/p', {'dossier': MID, 'chemin': CHEMIN}, *AVOCAT))
        self.assertFalse(Z._table_existe(self.desk))                        # la table n'est créée que par l'écriture qui en a besoin
        self.assertEqual(self.desk.db.total_changes, avant)
        d = self.preparer()['json']
        self.service['calques'] = 1
        avant = self.desk.db.total_changes
        e = self.api('etat', args={'demande': d['demande']}, methode='GET')
        self.assertEqual(e['json']['signatures'], 1)
        self.assertIn(('Cache-Control', 'private, no-store'), e['headers'])
        html = Z.page_html(self.desk, '/p', {'dossier': MID, 'chemin': CHEMIN}, *AVOCAT)
        self.assertIn('Copier le lien', html)
        self.assertIn('Récupérer le PDF signé', html)
        self.assertIn('Déposer le PDF signé', html)
        self.assertEqual(self.desk.db.total_changes, avant)

    def test_Z6_02_service_injoignable_pas_de_faux_zero(self):
        self.configurer()
        d = self.preparer()['json']
        with patch.object(Z, '_ouvrir', side_effect=Stop('service_injoignable')):
            e = self.api('etat', args={'demande': d['demande']}, methode='GET')
        self.assertIsNone(e['json']['signatures'])

    def test_Z6_03_page_absente_pour_un_dossier_reserve(self):
        self.configurer()
        membres5650.set_reserve(self.state, MID, ['martin@example.test'])
        env = {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1]}
        out =Z.page_route(env, self.desk.c, AUTH, '/p', Z.PAGE, {'dossier': MID, 'chemin': CHEMIN}, lambda *a, **k: 'page')
        self.assertIsNone(out)                                               # même réponse qu'une page absente


# ================================================================================ Z9 : postes et instance tierce (complément du 19/11)
class Postes(Base):
    def regler(self, adresse, cabinet):
        """Réglage direct d'une instance déjà testée (le faux service est en HTTP de bouclage ; une instance HTTPS ne peut pas être jointe ici)."""
        self.desk.setting(Z.SETTING, {'adresse': adresse, 'duree': '+1 week', 'echange': 'auto', 'version': 7, 'cabinet': cabinet,
                                      'test': {'ok': True, 'api': True, 'adresse': adresse, 'le': '2026-11-19T09:00:00+00:00', 'etapes': []}})

    def test_Z9_01_poste_instance_https_du_cabinet_bouton_present(self):
        self.regler('https://signature.example.test', True)
        with patch.object(Z, 'est_poste', lambda auth=None: True):
            bouton = Z.bouton_html(self.desk, '/p', MID, CHEMIN, 'avocat', {'poste': True})
            montage = Z.boutons_fichier_html(self.desk, {'poste': True}, '/p', 'nextcloud', CHEMIN, 'avocat', 'Contrat')
            page = Z.page_html(self.desk, '/p', {'dossier': MID, 'chemin': CHEMIN}, *AVOCAT, auth={'poste': True})
        self.assertIn('Faire signer par lien', bouton)
        self.assertIn('Faire signer par lien', montage)
        self.assertNotIn('confirmer_tiers', page)                              # instance du cabinet : pas de seconde confirmation
        self.assertNotIn('ne s’ouvre que sur cet ordinateur', page)

    def test_Z9_02_poste_instance_tierce_avertissement_et_confirmation(self):
        self.configurer(cabinet=False)
        self.assertFalse(Z.reglages(self.desk)['cabinet'])                     # jamais déduite : case non cochée = tiers
        with patch.object(Z, 'est_poste', lambda auth=None: True):
            page = Z.page_html(self.desk, '/p', {'dossier': MID, 'chemin': CHEMIN}, *AVOCAT)
            self.assertIn('il ne peut pas être pseudonymisé', page)
            self.assertIn('name="confirmer_tiers" required', page)
            self.assertIn('il ne peut pas être pseudonymisé', Z.reglages_html(self.desk, '/p', 'administrateur'))
            r = self.preparer()
            self.assertEqual(r['json']['error'], 'confirmation_tiers_requise')
            self.assertEqual(self.posts(), [])
            r = self.preparer(confirmer_tiers=True)
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertEqual(len(self.posts()), 1)
        journal = [json.loads(x[0]) for x in self.desk.db.execute("SELECT data FROM audit WHERE action='signaturepdf56108_demande'")]
        self.assertIs(journal[-1]['instance_du_cabinet'], False)

    def test_Z9_03_confidentiel_et_dossier_reserve_vers_un_tiers_refuses(self):
        self.configurer(cabinet=False)
        confidentiel = DOSSIER + '/Lettre confidentielle au confrère.pdf'
        self.nc.add(confidentiel, PDF)
        self.assertEqual(self.preparer(chemin=confidentiel, confirmer_tiers=True)['json']['error'], 'correspondance_confidentielle')
        membres5650.set_reserve(self.state, MID, [AVOCAT[0]])                  # dossier réservé à cet avocat : il le voit, mais rien vers un tiers
        self.assertEqual(self.preparer(confirmer_tiers=True)['json']['error'], 'dossier_reserve_tiers')
        self.assertEqual(self.posts(), [])
        # même dossier réservé, instance du cabinet : admis (le dossier reste au cabinet)
        Z.enregistrer_reglages(self.desk, {'version': Z.reglages(self.desk)['version'], 'adresse': self.base, 'cabinet': True}, ADMIN[0])
        self.assertTrue(self.preparer()['status'].startswith('200'))
        # mutation : sans le contrôle propre aux tiers, le dossier réservé partirait chez le tiers
        Z.enregistrer_reglages(self.desk, {'version': Z.reglages(self.desk)['version'], 'adresse': self.base, 'cabinet': False}, ADMIN[0])
        original = Z._politique
        with patch.object(Z, '_politique', lambda desk, mid, chemin, owner, role, cabinet=True: original(desk, mid, chemin, owner, role, True)):
            self.assertTrue(self.preparer(confirmer_tiers=True)['status'].startswith('200'))
        self.assertEqual(len(self.posts()), 2)

    def test_Z9_04_mode_local_strict_refuse_le_tiers_pas_le_cabinet(self):
        dp.enregistrer(self.desk, {'mode': 'local_strict', 'perimetre': 'Serveur du cabinet', 'version': self.desk.settings(dp.KEY, {}).get('revision', 0)}, ADMIN[0])
        self.configurer(cabinet=False)
        self.assertEqual(self.preparer(confirmer_tiers=True)['json']['error'], 'mode_local_strict')
        self.assertEqual(self.posts(), [])
        Z.enregistrer_reglages(self.desk, {'version': Z.reglages(self.desk)['version'], 'adresse': self.base, 'cabinet': True}, ADMIN[0])
        self.assertTrue(self.preparer()['status'].startswith('200'))
        self.assertEqual(len(self.posts()), 1)

    def test_Z9_05_instance_locale_explication(self):
        self.configurer()
        self.assertTrue(Z.est_locale(Z.reglages(self.desk)['adresse']))
        self.assertIn('ne s’ouvre que sur cet ordinateur', Z.reglages_html(self.desk, '/p', 'administrateur'))
        self.assertIn('ne s’ouvre que sur cet ordinateur', Z.page_html(self.desk, '/p', {'dossier': MID, 'chemin': CHEMIN}, *AVOCAT))


# ================================================================================ Z7 : export
class Export(unittest.TestCase):
    def test_Z7_01_table_declaree_avec_filtre_du_dossier(self):
        from agent import export56108 as R
        lignes = [x for x in R.EXPORT if x[2] == 'signaturepdf56108']
        self.assertEqual(len(lignes), 1)
        self.assertEqual(lignes[0][4], 'matter')


# ================================================================================ Z8 : textes en anglais, allemand, espagnol
class Langues(Base):
    def test_Z8_01_ecrans_traduits(self):
        from agent import ecrans5632 as E
        import test_v56107_langues as L
        self.configurer()
        d = self.preparer()['json']
        self.service['api'] = False
        Z.enregistrer_reglages(self.desk, {'version': Z.reglages(self.desk)['version'], 'adresse': self.base, 'echange': 'manuel'}, ADMIN[0])
        Z.tester(self.desk, {}, ADMIN[0])
        self.preparer()
        ecrans = {'reglages': Z.reglages_html(self.desk, '/p', 'administrateur'), 'reglages avocat': Z.reglages_html(self.desk, '/p', 'avocat'),
                  'page': Z.page_html(self.desk, '/p', {'dossier': MID, 'chemin': CHEMIN}, *AVOCAT),
                  'bouton': Z.bouton_html(self.desk, '/p', MID, CHEMIN, 'avocat')}
        self.assertTrue(d['demande'])
        for nom, html in ecrans.items():
            for lang in L.LANGUES:
                restes = L.reste_francais(L.visibles(E.translate_html(html, lang)))
                self.assertEqual(restes, [], '%s (%s)' % (nom, lang))

    def test_Z8_02_script_toutes_les_phrases_au_dictionnaire(self):
        from agent import ecrans5632 as E
        import test_v56107_langues as L
        manquants, vus = [], 0
        for no, s in L.litteraux_js(ROOT / 'agent' / 'static' / 'v56108-signaturepdf.js'):
            if not L.texte_visible_js(s):
                continue
            vus += 1
            for lang in L.LANGUES:
                if E.phrase(s, E.dictionary(lang)) is None:
                    manquants.append('%d (%s) %s' % (no, lang, s))
        self.assertGreater(vus, 5)
        self.assertEqual(manquants, [])

    def test_Z8_03_messages_traduits(self):
        from agent import ecrans5632 as E
        for lang in ('en', 'de', 'es'):
            table = E.dictionary(lang)
            for code in ('introuvable', 'service_non_configure', 'aucune_signature', 'pdf_identique', 'deja_range', 'correspondance_confidentielle',
                         'confirmation_requise', 'droit_envoyer_requis', 'confirmation_tiers_requise', 'dossier_reserve_tiers',
                         'mode_local_strict'):
                with self.subTest(lang=lang, code=code):
                    self.assertIsNotNone(E.phrase(Z.MESSAGES[code], table))


if __name__ == '__main__':
    unittest.main()
