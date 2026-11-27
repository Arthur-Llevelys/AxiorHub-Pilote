"""5.6.111 (lot C) : C04 — connexion des liens vers l'adresse vérifiée ; L02 — indicateur d'activité htmx.

Aucun réseau réel : résolveur simulé, connexion simulée, et pour le TLS un serveur local (127.0.0.1) avec une autorité fictive fabriquée par
l'essai (bibliothèque ``cryptography`` ; essai sauté si elle manque)."""
from datetime import datetime, timedelta, timezone
import http.server
import json
from pathlib import Path
import re
import socket
import ssl
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import agent
from agent import liens5683, ui_helpers
from agent.common import Stop

ROOT = Path(agent.__file__).resolve().parents[1]
PUBLIQUE_4 = '1.1.1.1'   # adresse publique ; aucune connexion réelle (connexion simulée)
PUBLIQUE_6 = '2606:4700:4700::1111'
TEXTE = 'Le preneur doit délivrer congé par acte extrajudiciaire au bailleur.'


class Reponse:
    def __init__(self, status=200, headers=None, corps=b''):
        self.status, self._headers, self._corps = status, dict(headers or {}), corps

    def getheaders(self):
        return list(self._headers.items())

    def read(self, n):
        bloc, self._corps = self._corps[:n], self._corps[n:]
        return bloc


class ConnexionSimulee:
    """Remplace la connexion épinglée : note (nom, adresse) et rend la réponse prévue pour le nom ; aucun socket."""
    journal = []
    reponses = {}
    echecs = set()

    def __init__(self, hote, adresse, port=443, timeout=None, context=None):
        self.hote, self.adresse = hote, adresse

    def connect(self):
        ConnexionSimulee.journal.append((self.hote, self.adresse))
        if self.adresse in ConnexionSimulee.echecs:
            raise OSError('connexion refusée (simulée)')

    def request(self, method, chemin, headers=None):
        self.chemin = chemin

    def getresponse(self):
        return ConnexionSimulee.reponses[self.hote]

    def close(self):
        pass


def resolveur(table):
    appels = []

    def resoudre(hote):
        appels.append(hote)
        valeur = table[hote]
        return valeur(len([a for a in appels if a == hote])) if callable(valeur) else valeur
    resoudre.appels = appels
    return resoudre


class LiensAdresseVerifiee(unittest.TestCase):
    def setUp(self):
        ConnexionSimulee.journal, ConnexionSimulee.reponses, ConnexionSimulee.echecs = [], {}, set()
        original = liens5683._fetch
        self.patch = patch.object(liens5683, '_fetch', lambda u, resoudre=None, contexte=None: original(u, resoudre=resoudre, contexte=contexte,
                                                                                                         connexion=ConnexionSimulee))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_une_seule_resolution_et_connexion_a_l_adresse_verifiee_meme_si_le_dns_change(self):
        # premier appel : adresse publique ; tout appel suivant rendrait une adresse interne (rebinding) : il ne doit pas avoir lieu
        res = resolveur({'site.example.test': lambda n: [PUBLIQUE_4] if n == 1 else ['127.0.0.1']})
        ConnexionSimulee.reponses['site.example.test'] = Reponse(200, {'Content-Type': 'text/html'}, ('<p>%s</p>' % TEXTE).encode())
        out = liens5683.verifier('https://site.example.test/bail', 'Le preneur doit délivrer congé par acte extrajudiciaire', resoudre=res)
        self.assertEqual(res.appels, ['site.example.test'])
        self.assertEqual(ConnexionSimulee.journal, [('site.example.test', PUBLIQUE_4)])
        self.assertEqual(out['state'], 'concordance_a_confirmer')

    def test_adresse_privee_ou_melangee_refusee_sans_connexion(self):
        for adresses in (['10.0.0.5'], [PUBLIQUE_4, '192.168.1.2'], ['::1'], ['::ffff:127.0.0.1'], ['fe80::1%2'], ['169.254.169.254']):
            with self.subTest(adresses=adresses):
                with self.assertRaisesRegex(Stop, '^lien_refuse$'):
                    liens5683.verifier('https://site.example.test/', '', resoudre=resolveur({'site.example.test': adresses}))
        self.assertEqual(ConnexionSimulee.journal, [])
        for url in ('https://[::1]/admin', 'https://127.0.0.1/', 'http://site.example.test/', 'https://localhost/'):
            with self.subTest(url=url), self.assertRaisesRegex(Stop, '^lien_refuse$'):
                liens5683.verifier(url, '', resoudre=resolveur({}))

    def test_redirection_vers_une_adresse_interdite_revérifiee(self):
        res = resolveur({'site.example.test': [PUBLIQUE_4], 'interne.example.test': ['10.1.2.3']})
        ConnexionSimulee.reponses['site.example.test'] = Reponse(302, {'Location': 'https://interne.example.test/latest'})
        with self.assertRaisesRegex(Stop, '^lien_refuse$'):
            liens5683.verifier('https://site.example.test/a', '', resoudre=res)
        self.assertEqual(ConnexionSimulee.journal, [('site.example.test', PUBLIQUE_4)])
        self.assertEqual(res.appels, ['site.example.test', 'interne.example.test'])

    def test_ipv6_puis_ipv4_si_la_connexion_echoue_toutes_verifiees(self):
        res = resolveur({'site.example.test': [PUBLIQUE_6, PUBLIQUE_4]})
        ConnexionSimulee.echecs = {PUBLIQUE_6}
        ConnexionSimulee.reponses['site.example.test'] = Reponse(200, {}, TEXTE.encode())
        out = liens5683.verifier('https://site.example.test/', '', resoudre=res)
        self.assertEqual(ConnexionSimulee.journal, [('site.example.test', PUBLIQUE_6), ('site.example.test', PUBLIQUE_4)])
        self.assertEqual(out['state'], 'texte_lu')

    def test_mandataire_ignore(self):
        res = resolveur({'site.example.test': [PUBLIQUE_4]})
        ConnexionSimulee.reponses['site.example.test'] = Reponse(200, {}, TEXTE.encode())
        with patch.dict('os.environ', {'HTTPS_PROXY': 'http://10.9.9.9:3128', 'https_proxy': 'http://10.9.9.9:3128'}):
            liens5683.verifier('https://site.example.test/', '', resoudre=res)
        self.assertEqual(ConnexionSimulee.journal, [('site.example.test', PUBLIQUE_4)])

    def test_taille_et_duree_bornees(self):
        res = resolveur({'site.example.test': [PUBLIQUE_4]})
        ConnexionSimulee.reponses['site.example.test'] = Reponse(200, {}, b'x' * (liens5683.MAX_BYTES + 5000))
        out = liens5683.verifier('https://site.example.test/', '', resoudre=res)
        self.assertEqual(out['motif'], 'page_trop_volumineuse')
        ConnexionSimulee.reponses['site.example.test'] = Reponse(200, {}, b'y' * 200000)
        appels = []

        def horloge():   # premier relevé : départ ; les suivants : délai total dépassé (serveur qui répond goutte à goutte)
            appels.append(1)
            return 0.0 if len(appels) == 1 else liens5683.TIMEOUT + 1.0
        with patch.object(liens5683, 'time', SimpleNamespace(monotonic=horloge)):
            with self.assertRaisesRegex(Stop, '^lien_injoignable$'):
                liens5683.verifier('https://site.example.test/', '', resoudre=res)

    def test_ancien_contrat_fetch_fourni_inchange(self):
        out = liens5683.verifier('https://example.test/a', 'congé', lambda u: (200, {}, b'<p>Page sans rapport.</p>'), resolve=False)
        self.assertEqual(out['state'], 'non_verifie')

    def test_contexte_sans_verification_refuse(self):
        with self.assertRaisesRegex(Stop, '^lien_tls_non_verifie$'):
            liens5683.ConnexionEpinglee('site.example.test', PUBLIQUE_4, context=ssl._create_unverified_context())


def _autorite_et_certificat(nom):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    maintenant = datetime.now(timezone.utc)
    cle_ac = ec.generate_private_key(ec.SECP256R1())
    sujet_ac = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Autorite fictive AxiorHub essais')])
    ac = (x509.CertificateBuilder().subject_name(sujet_ac).issuer_name(sujet_ac).public_key(cle_ac.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(maintenant - timedelta(days=1)).not_valid_after(maintenant + timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(cle_ac.public_key()), critical=False)
          .sign(cle_ac, hashes.SHA256()))
    cle = ec.generate_private_key(ec.SECP256R1())
    feuille = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nom)])).issuer_name(sujet_ac)
               .public_key(cle.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(maintenant - timedelta(days=1)).not_valid_after(maintenant + timedelta(days=2))
               .add_extension(x509.SubjectAlternativeName([x509.DNSName(nom)]), critical=False)
               .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
               .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), critical=True)
               .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
               .add_extension(x509.SubjectKeyIdentifier.from_public_key(cle.public_key()), critical=False)
               .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(cle_ac.public_key()), critical=False)
               .sign(cle_ac, hashes.SHA256()))
    pem = lambda c: c.public_bytes(serialization.Encoding.PEM)
    cle_pem = cle.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return pem(ac).decode(), pem(feuille), cle_pem


class Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        corps = ('<p>%s</p><p>Hôte demandé : %s</p>' % (TEXTE, self.headers.get('Host'))).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def log_message(self, *a):
        pass


@unittest.skipUnless(__import__('importlib').util.find_spec('cryptography'), 'bibliothèque cryptography absente')
class TLSNomDOrigine(unittest.TestCase):
    """Connexion réelle (serveur local) : socket ouvert vers l'adresse donnée, aucune résolution du nom, certificat vérifié pour le nom."""

    def serveur(self, nom_certificat):
        ac, cert, cle = _autorite_et_certificat(nom_certificat)
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        (Path(d.name) / 'c.pem').write_bytes(cert)
        (Path(d.name) / 'k.pem').write_bytes(cle)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(Path(d.name) / 'c.pem'), str(Path(d.name) / 'k.pem'))
        httpd = http.server.HTTPServer(('127.0.0.1', 0), Page)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close()))
        client = ssl.create_default_context(cadata=ac)
        return httpd.server_address[1], client

    def lire(self, port, client):
        vrai = socket.getaddrinfo

        def sans_dns(hote, *a, **k):
            if hote not in ('127.0.0.1',):
                raise AssertionError('résolution DNS inattendue : %s' % hote)
            return vrai(hote, *a, **k)
        with patch.object(socket, 'getaddrinfo', sans_dns):
            conn = liens5683.ConnexionEpinglee('site.example.test', '127.0.0.1', port, timeout=5, context=client)
            try:
                conn.request('GET', '/bail')
                rep = conn.getresponse()
                return rep.status, rep.read().decode('utf-8')
            finally:
                conn.close()

    def test_certificat_du_nom_d_origine_accepte_sans_resolution(self):
        port, client = self.serveur('site.example.test')
        status, corps = self.lire(port, client)
        self.assertEqual(status, 200)
        self.assertIn(TEXTE, corps)
        self.assertIn('Hôte demandé : site.example.test', corps)

    def test_certificat_d_un_autre_nom_refuse(self):
        port, client = self.serveur('autre.example.test')
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.lire(port, client)

    def test_erreur_de_certificat_sans_essai_d_une_autre_adresse(self):
        port, client = self.serveur('autre.example.test')

        class Locale(liens5683.ConnexionEpinglee):
            def __init__(self, hote, adresse, port_=443, timeout=None, context=None):
                super().__init__(hote, '127.0.0.1', port, timeout=5, context=client)
        essais = []
        res = lambda h: essais.append(h) or [PUBLIQUE_4, PUBLIQUE_6]
        with self.assertRaisesRegex(Stop, '^lien_certificat_refuse$'):
            liens5683._fetch('https://site.example.test/bail', resoudre=res, connexion=Locale)
        self.assertEqual(essais, ['site.example.test'])


class IndicateurActivite(unittest.TestCase):
    """L02 : indicateur htmx accessible, configuration JSON valide et sûre, styles dans la feuille unique, aucune route WhatsApp ajoutée."""

    def page(self):
        html = ('<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Essai</title></head><body>'
                '<main id="ws-main"><button hx-post="/agent-courriel/essai">Essai</button></main></body></html>')
        return ui_helpers.enhance(html, '/agent-courriel', 'jeton-fictif', False, [], '/aujourdhui')

    def test_configuration_htmx_json_valide_et_options_de_securite(self):
        html = self.page()
        m = re.search(r'<meta name="htmx-config" content=\'([^\']*)\'>', html)
        self.assertIsNotNone(m)
        config = json.loads(m.group(1))
        self.assertEqual(config, {'allowEval': False, 'allowScriptTags': False, 'includeIndicatorStyles': False, 'selfRequestsOnly': True})

    def test_indicateur_accessible_rattache_a_la_zone_principale(self):
        html = self.page()
        self.assertEqual(html.count('id="ax-occupe"'), 1)
        self.assertIn('<div id="ax-occupe" class="htmx-indicator" role="status" aria-live="polite">Mise à jour…</div>', html)
        self.assertIn('<main id="ws-main" hx-indicator="closest *, #ax-occupe"', html)
        self.assertNotIn('style="', html[html.index('id="ax-occupe"') - 60:html.index('id="ax-occupe"') + 120])

    def test_styles_dans_la_feuille_unique(self):
        from agent.shell501 import BUNDLE
        self.assertIn('v56111-activite.css', BUNDLE)
        css = (ROOT / 'agent' / 'static' / 'v56111-activite.css').read_text(encoding='utf-8')
        self.assertIn('#ax-occupe.htmx-indicator{display:none}', css)
        self.assertIn('#ax-occupe.htmx-indicator.htmx-request{display:block', css)
        self.assertIn('#ax-occupe.htmx-indicator.htmx-request', (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8'))

    def test_aucune_route_whatsapp_du_lot_original(self):
        self.assertFalse((ROOT / 'agent' / 'whatsapp56110.py').exists())
        for nom in ('web440.py', 'standalone_auth.py', 'web.py'):
            source = (ROOT / 'agent' / nom).read_text(encoding='utf-8')
            self.assertNotIn('m56110/whatsapp', source)
            self.assertNotIn('whatsapp56110', source)


if __name__ == '__main__':
    unittest.main()
