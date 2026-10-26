"""5.6.74 : associer un téléphone par QR code, page « À valider ». Application réelle (WSGI) en version serveur ; valeurs fictives."""
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import test_v5648 as t48
from agent import appareils5674 as ap, connexion5648 as cx, mobile490


class Association(t48.Base):
    def _invite(self, cookie):
        r = self.api('m5674/inviter', {}, cookie)
        self.assertTrue(r['status'].startswith('200'), r['body'])
        return json.loads(r['body'])

    def test_qr_code_puis_association_du_telephone(self):
        cookie = self.admin_cookie()
        out = self._invite(cookie)
        self.assertTrue(out['adresse'].startswith('https://cabinet.example.test/agent-courriel/connexion/appareil?jeton='))
        self.assertTrue(out['qr'].startswith('/agent-courriel/api440/m5674/qr?id='))   # même origine : la politique des pages refuse « data: »
        qr_path, _, qr_query = out['qr'][len('/agent-courriel'):].partition('?')
        img = self.request(qr_path, query=qr_query, cookie=cookie)
        self.assertEqual(img['status'], '200 OK')
        self.assertTrue(img['headers']['Content-Type'].startswith('image/svg+xml'))
        self.assertIn('<svg', img['body'])
        self.assertNotEqual(self.request(qr_path, query=qr_query)['status'], '200 OK')        # sans session : refusé
        self.assertNotEqual(self.request(qr_path, query='id=inconnu', cookie=cookie)['status'], '200 OK')
        token = parse_qs(urlsplit(out['adresse']).query)['jeton'][0]
        db = ap.connect(self.state)
        try:   # seule l'empreinte du jeton est conservée
            self.assertIsNone(db.execute('SELECT 1 FROM appareils5674_invitations WHERE token_hash=?', (token,)).fetchone())
        finally:
            db.close()
        page = self.request('/connexion/appareil', query='jeton=' + token)          # aperçu : n'associe rien
        self.assertEqual(page['status'], '200 OK')
        self.assertIn('Associer ce téléphone au compte', page['body'])
        self.assertEqual(page['headers'].get('Referrer-Policy'), 'same-origin')   # jamais « no-referrer » : Origin: null au POST
        again = self.request('/connexion/appareil', query='jeton=' + token)
        self.assertEqual(again['status'], '200 OK')                                  # un aperçu de lien ne consomme pas l'invitation
        done = self.request('/connexion/appareil', 'POST', {'jeton': token, 'nom': 'Téléphone fictif'})
        self.assertEqual(done['status'], '303 See Other')
        self.assertTrue(done['headers']['Location'].endswith('/mobile?associe=1'))
        phone = self.token(done)
        self.assertTrue(phone)
        self.assertIn('Max-Age=%d' % (ap.DEVICE_DAYS * 86400), done['cookies'][0])
        mobile = self.request('/mobile', query='associe=1', cookie=phone)
        self.assertEqual(mobile['status'], '200 OK', mobile['body'][:300])
        self.assertIn('Téléphone associé', mobile['body'])
        reuse = self.request('/connexion/appareil', 'POST', {'jeton': token, 'nom': 'Autre'})
        self.assertEqual(reuse['status'], '410 Gone')                                # usage unique
        devices = ap.devices(self.state, cx.admin_user(self.auth_data()))
        self.assertEqual([d['label'] for d in devices], ['Téléphone fictif'])
        removed = self.api('m5674/retirer', {'id': devices[0]['id']}, cookie)
        self.assertTrue(removed['status'].startswith('200'), removed['body'])
        gone = self.request('/mobile', cookie=phone)
        self.assertEqual(gone['status'], '303 See Other')                            # session du téléphone fermée
        self.assertTrue(gone['headers']['Location'].startswith('/agent-courriel/connexion'))

    def auth_data(self):
        return json.loads((Path(self.f.c['state_dir']).parent / 'ui-auth.json').read_text(encoding='utf-8')) \
            if (Path(self.f.c['state_dir']).parent / 'ui-auth.json').is_file() else {'username': 'admin'}

    def test_invitation_expiree_ou_inconnue_et_tentatives_limitees(self):
        cookie = self.admin_cookie()
        out = self._invite(cookie)
        token = parse_qs(urlsplit(out['adresse']).query)['jeton'][0]
        db = ap.connect(self.state)
        try:
            db.execute('UPDATE appareils5674_invitations SET expires=?', ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))
            db.commit()
        finally:
            db.close()
        self.assertEqual(self.request('/connexion/appareil', query='jeton=' + token)['status'], '410 Gone')
        statuses = [self.request('/connexion/appareil', query='jeton=faux%d' % i)['status'] for i in range(22)]
        self.assertIn('429 Too Many Requests', statuses)

    def test_invitation_reservee_a_un_compte_connecte(self):
        r = self.call('/api440/m5674/inviter', 'POST', {}, auth=False)
        self.assertTrue(r['status'].startswith('401'), r['status'])
        cookie = self.admin_cookie()
        with patch.object(ap, 'MAX_DEVICES', 0):
            r = self.api('m5674/inviter', {}, cookie)
        self.assertIn('dix_appareils_maximum', r['body'].decode())


class Reglages(unittest.TestCase):
    def test_poste_sans_association_et_section(self):
        self.assertFalse(ap.available({'poste': True, 'session_login': True}))
        html = ap.settings_html('/tmp', {'poste': True}, None)
        self.assertIn('version serveur', html)

    def test_notification_neutre_compte_les_points_a_decider(self):
        self.assertEqual(mobile490.neutral_text({'drafts': 0, 'deadlines': 0, 'decisions': 2}), '2 points à décider')
        self.assertEqual(mobile490.neutral_text({'drafts': 1, 'deadlines': 0, 'decisions': 1}), '1 brouillon à valider · 1 point à décider')

    def test_cablage_et_licence_segno(self):
        root = Path(ap.__file__).resolve().parents[1]
        from agent import pages5636, web440, shell501
        self.assertIn('/mobile', pages5636.PAGES)
        self.assertIn('/static/v5674.js', web440.STATIC)
        self.assertIn('v5674.css', shell501.BUNDLE)
        self.assertIn('Lars Heuer', (root / 'agent/_vendor/segno/LICENSE').read_text(encoding='utf-8'))
        self.assertIn('Segno', (root / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8'))
        sbom = json.loads((root / 'SBOM.cdx.json').read_text(encoding='utf-8'))
        self.assertTrue(any(c['name'] == 'segno' for c in sbom['components']))
        for lang in ('fr', 'en', 'de', 'es'):
            self.assertIn('fn.mobile.label', json.loads((root / ('agent/locales/%s.json' % lang)).read_text(encoding='utf-8')))
        js = (root / 'agent/static/v5674.js').read_text(encoding='utf-8')
        self.assertNotIn('innerHTML', js)


if __name__ == '__main__':
    unittest.main()
