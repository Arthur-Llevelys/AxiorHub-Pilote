"""5.6.103, lot G : étapes facultatives « Compte Google » et « Compte Microsoft » de la mise en service (guide d'installation, page
d'installation du serveur, Paramètres › Connexions) : passables, reprises plus tard, état réel recalculé sans écriture, Microsoft absent ou
application intégrée non renseignée → « pas encore disponible ». Données fictives seulement."""
import io
import json
import unittest
from unittest.mock import patch

from agent import assistant56103 as asst, gmail_simple56103 as gs
from agent.common import Stop, private_json
from agent.desk import Desk
from agent.standalone_auth import allowed
import test_agent as fixtures
from test_v56103_gmail import ADRESSE, MDP, FauxGmail

ORIGINE, JETON = 'https://cabinet.example.test', 'jeton-csrf-fictif'
CLAIRE, CAMILLE = 'claire@example.test', 'camille@example.test'


def ms_liste(etat_compte, faites=()):
    """Réponse au format du point de montage du lot M (``microsoft56103.etapes_assistant``)."""
    lignes = [{'id': 'microsoft.compte', 'titre': 'Compte Microsoft', 'etat': etat_compte, 'detail': 'Détail du compte.', 'action': None}]
    for cap in ('courrier', 'agenda', 'fichiers'):
        lignes.append({'id': 'microsoft.' + cap, 'titre': cap.title(), 'etat': 'fait' if cap in faites else 'a_faire', 'detail': '', 'action': None})
    return lignes


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.config = self.f.base / 'config.json'
        private_json(self.config, self.f.c)
        self.env = {'axiorhub.config_path': str(self.config)}
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        FauxGmail.preparer()
        for p in (patch('agent.mailbox.imaplib.IMAP4_SSL', FauxGmail), patch('agent.common.HTTP.request', side_effect=AssertionError('réseau'))):
            p.start()
            self.addCleanup(p.stop)

    def microsoft(self, reponse):
        """Point de montage Microsoft remplacé : ``None`` = module ou fonction absents ; une exception = panne du lot M."""
        if reponse is None:
            return patch.object(asst, '_montage_microsoft', lambda: None)
        if isinstance(reponse, Exception):
            def panne(desk, owner, env=None):
                raise reponse
            return patch.object(asst, '_montage_microsoft', lambda: panne)
        return patch.object(asst, '_montage_microsoft', lambda: (lambda desk, owner, env=None: reponse))

    def appel(self, route, corps=None, owner=CLAIRE, role='avocat', methode='POST', jeton=JETON):
        brut = json.dumps(corps or {}).encode()
        env = {**self.env, 'HTTP_X_AXIORHUB_USER': owner, 'HTTP_X_AXIORHUB_ROLE': role, 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': jeton,
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(brut)), 'wsgi.input': io.BytesIO(brut)}
        out = asst.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm56103/assistant/' + route, {}, methode)
        return out['status'], json.loads(out['body']), out


class Etats(Base):
    def test_A01_etats_initiaux_et_microsoft_absent(self):
        with self.microsoft(None):
            e = asst.etat(self.desk, CLAIRE)
        self.assertEqual((e['google']['etat'], e['microsoft']['etat']), ('a_faire', 'indisponible'))
        self.assertEqual(e['microsoft']['libelle'], 'Pas encore disponible')
        self.assertIn('pas encore disponible', e['microsoft']['message'])
        with self.microsoft(RuntimeError('panne du lot M')):                    # une panne n'est jamais une erreur de l'assistant
            self.assertEqual(asst.etat(self.desk, CLAIRE)['microsoft']['etat'], 'indisponible')
        with self.microsoft([{'id': 'microsoft.compte', 'etat': 'indisponible', 'detail': 'Application intégrée pas encore disponible.'}]):
            r = asst.etat(self.desk, CLAIRE)['microsoft']
        self.assertEqual(r['etat'], 'indisponible')
        self.assertIn('Application intégrée pas encore disponible', r['message'])

    def test_A01_module_du_lot_m_sans_application_integree(self):
        try:
            from agent import microsoft56103 as ms
        except ImportError:
            self.skipTest('module du lot M absent : couvert par test_A01_etats_initiaux_et_microsoft_absent')
        if ms.disponible(self.desk):
            self.skipTest('application intégrée renseignée dans cette version')
        self.assertEqual(asst.etat(self.desk, CLAIRE)['microsoft']['etat'], 'indisponible')

    def test_A02_microsoft_etats_reels(self):
        for reponse, attendu in ((ms_liste('a_faire'), 'a_faire'), (ms_liste('fait', ('courrier',)), 'faite'), (ms_liste('a_refaire'), 'a_refaire')):
            with self.subTest(attendu=attendu), self.microsoft(reponse):
                self.assertEqual(asst.etat(self.desk, CLAIRE)['microsoft']['etat'], attendu)
        with self.microsoft(ms_liste('fait', ('courrier',))):
            self.assertIn('Courrier', asst.etat(self.desk, CLAIRE)['microsoft']['message'])
        with self.microsoft({'etat': 'a_faire', 'lien': 'https://piege.example.test/'}):   # un lien externe n'est jamais repris dans la page
            self.assertEqual(asst.etat(self.desk, CLAIRE)['microsoft']['lien'], '')

    def test_A03_passer_puis_reprendre(self):
        with self.microsoft(None):
            status, body, brut = self.appel('passer', {'etape': 'google', 'cle': 'cle-passer-g-0001'})
            self.assertEqual(status, '200 OK', body)
            self.assertEqual(dict(brut['headers'])['Cache-Control'], 'private, no-store')
            self.assertEqual(body['etat']['etat'], 'passee')
            self.assertIn('Paramètres › Connexions', body['message'])
            self.assertEqual(asst.etat(self.desk, CAMILLE)['google']['etat'], 'a_faire')   # propre à la personne
            self.assertEqual(self.appel('passer', {'etape': 'microsoft', 'cle': 'cle-passer-m-0001'})[1]['etat']['etat'], 'passee')
            status, body, _ = self.appel('etat', methode='GET')
            self.assertEqual({k: v['etat'] for k, v in body['etapes'].items()}, {'google': 'passee', 'microsoft': 'passee'})
            status, body, _ = self.appel('reprendre', {'etape': 'google', 'cle': 'cle-reprendre-g-01'})
            self.assertEqual((status, body['etat']['etat']), ('200 OK', 'a_faire'))
            self.assertEqual(body['lien'], '/p/parametres?rubrique=connexions#gmail56103')
            html = asst.section_html(self.desk, '/p', CLAIRE, 'avocat', self.env)
        self.assertIn('Reprendre cette étape', html)                             # Microsoft encore passée
        self.assertIn('Passer cette étape', html)                                # Google repris

    def test_A04_etat_reel_apres_echec_puis_succes(self):
        with self.microsoft(None):
            asst.passer(self.desk, CLAIRE, 'google')
            FauxGmail.preparer(refus=b'[AUTHENTICATIONFAILED] Invalid credentials (Failure)')
            with self.assertRaises(Stop):
                gs.tester(self.desk, 'admin@example.test', 'administrateur', ADRESSE, MDP)
            r = asst.etat(self.desk, CLAIRE)['google']
            self.assertEqual(r['etat'], 'a_refaire')                             # l'échec réel l'emporte sur « passée »
            self.assertIn('échec', r['message'])
            FauxGmail.preparer()
            from agent.config567 import save
            save(self.desk, {'revision': 0, 'values': {'mail.host': 'imap.gmail.com', 'mail.username': ADRESSE, 'mail.password_file': 'abcdefghijklmnop'}}, self.env)
            self.desk.c['mail'] = json.loads(self.config.read_text(encoding='utf-8'))['mail']
            self.assertEqual(asst.etat(self.desk, CLAIRE)['google']['etat'], 'a_refaire')   # configuré mais pas testé depuis : jamais « faite »
            gs.tester(self.desk, 'admin@example.test', 'administrateur', ADRESSE, '')
            self.assertEqual(asst.etat(self.desk, CLAIRE)['google']['etat'], 'faite')
            # mutation : si « passée » masquait l'état réel, une étape faite paraîtrait seulement passée
            with patch.object(asst, 'etat_google', lambda d, o: {'etat': 'faite', 'message': ''}):
                self.assertEqual(asst.etat(self.desk, CLAIRE)['google']['etat'], 'faite')

    def test_A04_connexion_oauth_de_la_personne(self):
        from agent import gmail5697 as gm, google5697 as gg
        for etat_oauth, attendu in (('active', 'faite'), ('a_reconnecter', 'a_refaire'), ('absente', 'a_faire')):
            with self.subTest(etat_oauth=etat_oauth), patch.object(gm, 'connexion_de', lambda d, o: None), \
                    patch.object(gg, 'vue', lambda row, e=etat_oauth: {'etat': e}):
                self.assertEqual(asst.etat_google(self.desk, CLAIRE)['etat'], attendu)

    def test_A05_lecture_sans_aucune_ecriture(self):
        asst.passer(self.desk, CLAIRE, 'microsoft')
        avant = self.desk.db.total_changes
        with self.microsoft(ms_liste('a_faire')):
            self.appel('etat', methode='GET')
            asst.section_html(self.desk, '/p', CLAIRE, 'avocat', self.env)
            asst.carte_guide(self.desk, self.env, '/p', admin=True, owner=CLAIRE)
            gs.section_html(self.desk, '/p', CLAIRE, 'administrateur', self.env)
        self.assertEqual(self.desk.db.total_changes, avant)

    def test_A06_routes_droits_csrf_idempotence(self):
        for role in ('administrateur', 'avocat', 'assistant'):
            self.assertTrue(allowed(role, 'POST', '/api440/m56103/assistant/passer'))
        self.assertFalse(allowed('avocat', 'POST', '/api440/m56103/assistant/inconnue'))
        self.assertFalse(asst.allowed('invite', 'POST', 'm56103/assistant/passer'))
        self.assertEqual(self.appel('passer', {'etape': 'google', 'cle': 'cle-csrf-00000001'}, jeton='faux')[1]['error'], 'origine_ou_csrf_refuse')
        self.assertEqual(self.appel('passer', {'etape': 'autre', 'cle': 'cle-etape-0000001'})[0], '404 Not Found')
        self.assertEqual(self.appel('passer', {'etape': 'google'})[1]['error'], 'cle_idempotence_requise')
        premier = self.appel('passer', {'etape': 'google', 'cle': 'cle-idem-00000001'})
        n = self.desk.db.execute("SELECT COUNT(*) FROM audit WHERE action='assistant56103_passee'").fetchone()[0]
        self.assertEqual(self.appel('passer', {'etape': 'google', 'cle': 'cle-idem-00000001'})[1], premier[1])
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM audit WHERE action='assistant56103_passee'").fetchone()[0], n)
        self.assertEqual(self.appel('passer', {'etape': 'microsoft', 'cle': 'cle-idem-00000001'})[0], '409 Conflict')
        self.assertEqual(self.appel('inconnue', {})[0], '404 Not Found')


class Ecrans(Base):
    def test_A07_guide_installation_etapes_messagerie_et_fin(self):
        from agent import guide5631
        with self.microsoft(None):
            for etape in ('messagerie', 'fin'):
                with self.subTest(etape=etape):
                    html = guide5631._cartes_etape(self.desk, self.env, '/p', etape, {'id': 'guide-x'}, CLAIRE, True)
                    self.assertIn('as103-comptes', html)
                    self.assertIn('Passer cette étape', html)
                    self.assertIn('id="gmail56103"', html)
                    self.assertIn('Pas encore disponible', html)
        self.assertEqual(guide5631.REGLAGES['comptes'], '/parametres?rubrique=connexions#mise56103')

    def test_A07_guide_carte_microsoft_disponible(self):
        with self.microsoft(ms_liste('a_faire')), patch('agent.microsoft_web56103.section_html', lambda d, p, o, e=None: '<section id="microsoft56103">carte M</section>', create=True):
            html = asst.carte_guide(self.desk, self.env, '/p', admin=True, owner=CLAIRE)
        self.assertIn('carte M', html)

    def test_A08_page_installation_du_serveur(self):
        with self.microsoft(None):
            html = asst.installation_html(self.desk, CLAIRE, 'jeton')
            self.assertIn('value="passer_google"', html)
            self.assertIn('value="passer_microsoft"', html)
            self.assertIn('7 · Comptes Google et Microsoft', html)
            self.assertIn('Passée', asst.installation_action(self.desk, CLAIRE, 'passer_google') + asst.etat(self.desk, CLAIRE)['google']['libelle'])
            self.assertIn('value="reprendre_google"', asst.installation_html(self.desk, CLAIRE, 'jeton'))
            asst.installation_action(self.desk, CLAIRE, 'reprendre_google')
            self.assertEqual(asst.etat(self.desk, CLAIRE)['google']['etat'], 'a_faire')
        for op in ('passer_autre', 'supprimer_google', ''):
            with self.subTest(op=op), self.assertRaises(Stop):
                asst.installation_action(self.desk, CLAIRE, op)

    def test_A09_parametres_connexions(self):
        with self.microsoft(None):
            html = asst.section_html(self.desk, '/p', CLAIRE, 'avocat', self.env)
        self.assertIn('id="mise56103"', html)
        self.assertIn('Configurer maintenant', html)
        self.assertIn('/p/parametres?rubrique=connexions#gmail56103', html)
        self.assertNotIn('Configurer maintenant</a></p></li><li data-etape="microsoft" data-etat="indisponible"><p><a', html)


if __name__ == '__main__':
    unittest.main()
