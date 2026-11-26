"""5.6.110 (lot G) : Google Agenda et Gmail sur un poste (Windows 11, Ubuntu 24.04) — plus d'« Erreur 400 : redirect_uri_mismatch ».

- G01/G02 : sur un poste SEULEMENT (``ui-auth.json`` voisin de la configuration, ``"poste": true``), adresse de retour de bouclage en http,
  calculée par AxiorHub quand le réglage est vide, d'exemple, ou la valeur sans préfixe écrite par l'installation ; ailleurs ``https`` inchangé.
  ``localhost``, un nom, une adresse du réseau, un autre port, un autre chemin, ``[::1]`` quand le poste écoute sur 127.0.0.1 : refus.
- G03 : retour en deux temps (retour déposé sans session, échange depuis la fenêtre avec sa session) ; rejeu, mauvais état, autre session,
  autre personne, expiration, adresse non locale, serveur : refus, rien d'échangé.
- G04 : valeur d'exemple refusée AVANT l'envoi chez Google.
- Chemin serveur inchangé ; ``calendar568`` (agenda 5.6.8) : mêmes règles.

Faux Google des essais 5.6.97 (``google5697.TRANSPORT``) ; aucun réseau (``common.HTTP.request`` bloqué) ; données fictives (DURAND, example.test).
"""
import base64
import io
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from agent import calendar568, google5697 as gg, google_web5697 as gw, gmail5697 as gm, membres5650
from agent.common import Stop, load_config, private_json
from agent.desk import Desk
from test_v5697_gmail import FauxGoogle, CLIENT_ID, SECRET
import test_agent as fixtures

PORT = 8769
ORIGINE = 'http://127.0.0.1:%d' % PORT
PREFIXE = '/agent-courriel'
CALCULEE = ORIGINE + PREFIXE + '/api440/m5689/oauth/google/callback'
CALCULEE_AGENDA = ORIGINE + PREFIXE + '/api440/m568/google/callback'
SERVEUR = 'https://pilote.example.test/p/api440/m5689/oauth/google/callback'
OWNER = 'cabinet'


def q(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


class Base(unittest.TestCase):
    poste = True

    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        from agent.vault567 import write
        secret = self.f.base / 'secrets' / 'google_oauth.secret'
        write(secret, SECRET)
        self.secret = str(secret)
        self.f.c['google5697'] = {'client_id': CLIENT_ID, 'client_secret_file': self.secret, 'redirect_uri': ''}
        self.config = self.f.base / 'config.json'
        self.f.c['_path'] = str(self.config)
        self.ecrire_auth({'poste': self.poste, 'origin': ORIGINE if self.poste else 'https://pilote.example.test', 'prefix': PREFIXE})
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        self.google = FauxGoogle()
        self.horloge = [time.time()]
        bloque = AssertionError('appel réseau non simulé')
        for p in (patch.object(gg, 'TRANSPORT', self.google), patch.object(gg, 'HORLOGE', lambda: self.horloge[0]),
                  patch('agent.common.HTTP.request', side_effect=bloque)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(membres5650.clear)

    def ecrire_auth(self, data):
        private_json(self.f.base / 'ui-auth.json', {'username': 'admin', 'csrf': 'jeton-csrf-fictif', **data})

    def retour(self, valeur):
        self.desk.c['google5697'] = {**self.desk.c['google5697'], 'redirect_uri': valeur}

    def session(self, nom='fenetre-poste'):
        return gg.session_de({'axiorhub.user': {'session': nom}})

    def env_session(self, nom='fenetre-poste'):
        return {'HTTP_X_AXIORHUB_USER': OWNER, 'HTTP_X_AXIORHUB_ROLE': 'administrateur', 'axiorhub.user': {'session': nom}}

    def depot(self, args, remote='127.0.0.1', chemin='/api440/m5689/oauth/google/callback', extra=None):
        env = {'REQUEST_METHOD': 'GET', 'REMOTE_ADDR': remote, 'QUERY_STRING': urlencode(args), **(extra or {})}
        return gw.retour_poste(self.desk, env, PREFIXE, chemin)

    def compter_jetons(self):
        return len([a for a in self.google.appels if a[1].startswith('https://oauth2.googleapis.com/token')])


# ------------------------------------------------------------------------------------------------ G01, G02, G04 : adresse de retour
class AdressePoste(Base):
    def test_G02_reglage_vide_ou_exemple_adresse_calculee_par_axiorhub(self):
        for valeur in ('', 'https://agent.example.com/api440/m5689/oauth/google/callback',
                       'http://127.0.0.1:%d/api440/m5689/oauth/google/callback' % PORT,      # valeur sans préfixe écrite par l'installation
                       'http://127.0.0.1:9999/api440/m5689/oauth/google/callback'):        # même forme, ancien port
            self.retour(valeur)
            self.assertEqual(gg.configuration(self.desk, 'gmail')['redirect_uri'], CALCULEE, valeur)
            etat = gg.etat_configuration(self.desk, 'gmail')
            self.assertEqual((etat['configure'], etat['uri_retour'], etat['poste']), (True, CALCULEE, True))
        depart = gg.demarrer(self.desk, OWNER, 'gmail', self.session())
        p = q(depart['url'])
        self.assertEqual((p['redirect_uri'], p['code_challenge_method'], p['client_id']), (CALCULEE, 'S256', CLIENT_ID))
        self.assertTrue(depart['url'].startswith(gg.AUTORISATION + '?'))

    def test_G01_bouclage_exact_admis_sur_le_poste_https_inchange(self):
        self.retour(CALCULEE)
        self.assertEqual(gg.configuration(self.desk, 'gmail')['redirect_uri'], CALCULEE)
        self.retour(SERVEUR)                                                # https reste admis tel quel (règle inchangée)
        self.assertEqual(gg.configuration(self.desk, 'gmail')['redirect_uri'], SERVEUR)

    def test_G01_toute_autre_adresse_http_refusee_sur_le_poste(self):
        mauvaises = ('http://localhost:%d%s/api440/m5689/oauth/google/callback' % (PORT, PREFIXE),       # localhost
                     'http://pilote.example.test:%d%s/api440/m5689/oauth/google/callback' % (PORT, PREFIXE),   # un nom
                     'http://192.168.1.10:%d%s/api440/m5689/oauth/google/callback' % (PORT, PREFIXE),  # adresse du réseau
                     'http://127.0.0.1:8770%s/api440/m5689/oauth/google/callback' % PREFIXE,           # autre port
                     'http://127.0.0.1:%d/autre/api440/m5689/oauth/google/callback' % PORT,            # autre chemin
                     'http://127.0.0.1:%d%s/api440/m5689/oauth/google/callback/' % (PORT, PREFIXE),    # chemin différent (barre finale)
                     'http://[::1]:%d%s/api440/m5689/oauth/google/callback' % (PORT, PREFIXE),         # le poste écoute sur 127.0.0.1
                     'http://127.0.0.2:%d%s/api440/m5689/oauth/google/callback' % (PORT, PREFIXE),     # autre adresse de bouclage
                     CALCULEE + '?suite=x', 'http://admin:x' + '@127.0.0.1:%d%s/api440/m5689/oauth/google/callback' % (PORT, PREFIXE),
                     'ftp://127.0.0.1:%d%s/api440/m5689/oauth/google/callback' % (PORT, PREFIXE))
        for mauvaise in mauvaises:
            self.retour(mauvaise)
            with self.assertRaises(Stop, msg=mauvaise) as ctx:
                gg.demarrer(self.desk, OWNER, 'gmail', self.session())
            self.assertEqual(str(ctx.exception), 'google_uri_retour_invalide', mauvaise)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM google5697_etats').fetchone()[0], 0)   # rien n'est parti chez Google

    def test_G01_adresse_jamais_tiree_de_l_en_tete_host(self):
        from agent import gmail_web5697
        gm.regler(self.desk, 'administrateur', True)
        corps = b'{}'
        env = {**self.env_session(), 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': 'jeton-csrf-fictif', 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': '2', 'wsgi.input': io.BytesIO(corps), 'HTTP_HOST': 'piege.example.test:4444'}
        out = gmail_web5697.api(env, self.desk, {'origin': ORIGINE, 'csrf': 'jeton-csrf-fictif'}, PREFIXE, 'm5689/gmail/connecter', {}, 'POST')
        url = json.loads(out['body'])['oauth_url']
        self.assertEqual(q(url)['redirect_uri'], CALCULEE)
        self.assertNotIn('piege', url)

    def test_G01_origine_du_poste_anormale_regle_du_serveur(self):
        for auth in ({'poste': True, 'origin': 'http://localhost:%d' % PORT}, {'poste': True, 'origin': 'https://127.0.0.1:%d' % PORT},
                     {'poste': True, 'origin': 'http://192.168.1.10:%d' % PORT}, {'poste': 'oui', 'origin': ORIGINE}, {'origin': ORIGINE}):
            self.ecrire_auth({**auth, 'prefix': PREFIXE})
            self.assertIsNone(gg.poste_local(self.desk.c), auth)
            self.retour('')
            with self.assertRaises(Stop):
                gg.configuration(self.desk, 'gmail')

    def test_G05_reglages_du_cabinet_acceptent_vide_ou_bouclage_jamais_http_ailleurs(self):
        from agent.config567 import INDEX, _validate
        for cle in ('google5697.redirect_uri', 'google_calendar568.redirect_uri'):
            kind = INDEX[cle][2]
            self.assertIn('sur un poste', INDEX[cle][1])
            self.assertEqual(_validate(kind, '', cle), '')
            self.assertEqual(_validate(kind, CALCULEE, cle), CALCULEE)
            self.assertEqual(_validate(kind, SERVEUR, cle), SERVEUR)
            for mauvaise in ('http://pilote.example.test/oauth/google/callback', 'http://localhost:8769/x', SERVEUR + '?a=1'):
                with self.assertRaises(Stop):
                    _validate(kind, mauvaise, cle)


class AdresseServeur(Base):
    poste = False

    def test_G01_serveur_bouclage_http_refuse_https_inchange(self):
        self.assertIsNone(gg.poste_local(self.desk.c))
        for mauvaise in (CALCULEE, 'http://pilote.example.test/p/api440/m5689/oauth/google/callback'):
            self.retour(mauvaise)
            with self.assertRaises(Stop) as ctx:
                gg.demarrer(self.desk, OWNER, 'gmail', self.session())
            self.assertEqual(str(ctx.exception), 'google_uri_retour_invalide')
        self.retour(SERVEUR)
        self.assertEqual(q(gg.demarrer(self.desk, OWNER, 'gmail', self.session())['url'])['redirect_uri'], SERVEUR)
        self.retour('')
        with self.assertRaises(Stop) as ctx:
            gg.configuration(self.desk, 'gmail')
        self.assertEqual(str(ctx.exception), 'google_non_configure')

    def test_G04_valeur_d_exemple_refusee_avant_google(self):
        self.retour('https://agent.example.com/api440/m5689/oauth/google/callback')
        with self.assertRaises(Stop) as ctx:
            gg.demarrer(self.desk, OWNER, 'gmail', self.session())
        self.assertEqual(str(ctx.exception), 'google_uri_retour_exemple')
        self.assertIn('agent.example.com', gg.MESSAGES['google_uri_retour_exemple'])
        self.assertEqual(self.google.appels, [])
        etat = gg.etat_configuration(self.desk, 'gmail')
        self.assertEqual((etat['configure'], etat['code'], etat['uri_retour'], etat['poste']), (False, 'google_uri_retour_exemple', '', False))

    def test_G03_retour_sans_session_refuse_hors_poste(self):
        self.retour(SERVEUR)
        p = q(gg.demarrer(self.desk, OWNER, 'gmail', self.session())['url'])
        out = self.depot({'state': p['state'], 'code': 'code-x'})
        self.assertEqual(out['status'][:3], '403')
        with self.assertRaises(Stop) as ctx:
            gg.deposer_retour(self.desk, {'state': p['state'], 'code': 'code-x'})
        self.assertEqual(str(ctx.exception), 'google_retour_poste_seulement')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM google5697_retours').fetchone()[0], 0)


# ------------------------------------------------------------------------------------------------ G03 : retour en deux temps sur le poste
class RetourPoste(Base):
    def demarrer(self, session='fenetre-poste', capacites=None):
        gm.regler(self.desk, 'administrateur', True)
        depart = gg.demarrer(self.desk, OWNER, 'gmail', self.session(session), capacites)
        return self.google.consentir(depart['url'], 'durand.gmail@example.test')

    def terminer(self, session='fenetre-poste'):
        return gw.api(self.env_session(session), self.desk, {'csrf': 'jeton-csrf-fictif'}, PREFIXE, 'm5689/oauth/google/terminer', {}, 'GET')

    def attente(self, session='fenetre-poste'):
        out = gw.api(self.env_session(session), self.desk, {'csrf': 'jeton-csrf-fictif'}, PREFIXE, 'm5689/oauth/google/attente', {}, 'GET')
        return json.loads(out['body'])['etat']

    def test_G03_parcours_complet_depart_retour_jetons_avec_pkce(self):
        retour = self.demarrer()
        self.assertEqual(self.attente(), 'attente')
        page = self.depot(retour)
        self.assertEqual(page['status'][:3], '200')
        self.assertNotIn(retour['code'], page['body'])                 # aucun paramètre OAuth dans la page
        self.assertNotIn(retour['state'], page['body'])
        self.assertIn(PREFIXE + '/api440/m5689/oauth/google/terminer', page['body'])
        self.assertIn(('Referrer-Policy', 'no-referrer'), page['headers'])
        self.assertIn("default-src 'none'", page['csp'])
        self.assertEqual(self.compter_jetons(), 0)                       # le dépôt n'échange rien
        self.assertNotEqual((gm.connexion_de(self.desk, OWNER) or {}).get('etat'), 'active')
        self.assertEqual(self.attente(), 'recu')
        self.assertEqual(self.attente('autre-fenetre'), 'attente')      # une autre session ne voit rien
        out = self.terminer()
        entetes = dict(out['headers'])
        self.assertEqual(out['status'][:3], '303')
        self.assertTrue(entetes['Location'].startswith(PREFIXE + '/parametres?rubrique=connexions&google=connecte'), entetes)
        self.assertNotIn(retour['code'], entetes['Location'])
        self.assertEqual(self.compter_jetons(), 1)                       # faux Google : code + vérificateur PKCE S256 + adresse de retour exacte
        conn = gm.connexion_de(self.desk, OWNER)
        self.assertEqual((conn['etat'], conn['adresse']), ('active', 'durand.gmail@example.test'))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM google5697_retours').fetchone()[0], 0)   # code effacé après usage

    def test_G03_rejeu_du_retour_refuse(self):
        retour = self.demarrer()
        self.assertEqual(self.depot(retour)['status'][:3], '200')
        self.assertEqual(self.depot(retour)['status'][:3], '400')       # second dépôt du même état
        self.assertIn('google=connecte', dict(self.terminer()['headers'])['Location'])
        self.assertEqual(self.depot(retour)['status'][:3], '400')       # état déjà utilisé
        self.assertIn('google=google_retour_absent', dict(self.terminer()['headers'])['Location'])
        with self.assertRaises(Stop):                                    # le chemin authentifié direct ne rejoue pas non plus
            gg.terminer(self.desk, OWNER, self.session(), retour)
        self.assertEqual(self.compter_jetons(), 1)

    def test_G03_mauvais_etat_rien_garde(self):
        self.demarrer()
        for args in ({'state': 'etat-inconnu', 'code': 'code-1'}, {'state': '', 'code': 'code-1'}, {'code': 'code-1'}, {'state': 'x' * 300, 'code': 'c'}):
            self.assertEqual(self.depot(args)['status'][:3], '400', args)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM google5697_retours').fetchone()[0], 0)
        self.assertIn('google=google_retour_absent', dict(self.terminer()['headers'])['Location'])
        self.assertEqual(self.compter_jetons(), 0)

    def test_G03_autre_session_ou_autre_personne_refusees(self):
        retour = self.demarrer()
        self.depot(retour)
        self.assertIn('google=google_retour_absent', dict(self.terminer('autre-fenetre')['headers'])['Location'])
        with self.assertRaises(Stop) as ctx:
            gg.terminer_depose(self.desk, 'martin@example.test', self.session())
        self.assertEqual(str(ctx.exception), 'google_retour_absent')
        self.assertEqual(self.compter_jetons(), 0)
        self.assertIn('google=connecte', dict(self.terminer()['headers'])['Location'])   # le dépôt attendait la bonne session

    def test_G03_dix_minutes_puis_expire(self):
        retour = self.demarrer()
        self.horloge[0] += 601
        self.assertEqual(self.depot(retour)['status'][:3], '400')       # dépôt refusé après dix minutes
        retour2 = self.demarrer()
        self.depot(retour2)
        self.horloge[0] += 601
        self.assertIn('google=google_etat_expire', dict(self.terminer()['headers'])['Location'])
        self.assertEqual(self.compter_jetons(), 0)

    def test_G03_refus_chez_google_aucune_connexion(self):
        retour = self.demarrer()
        self.assertEqual(self.depot({'state': retour['state'], 'error': 'access_denied'})['status'][:3], '200')
        self.assertIn('google=google_autorisation_refusee', dict(self.terminer()['headers'])['Location'])
        self.assertEqual(self.compter_jetons(), 0)
        self.assertNotEqual((gm.connexion_de(self.desk, OWNER) or {}).get('etat'), 'active')

    def test_G03_adresse_non_locale_ou_relais_refuses(self):
        retour = self.demarrer()
        for remote, extra in (('192.168.1.20', None), ('10.0.0.5', None), ('127.0.0.1', {'HTTP_X_FORWARDED_FOR': '203.0.113.9'}),
                              ('127.0.0.1', {'HTTP_FORWARDED': 'for=203.0.113.9'})):
            self.assertEqual(self.depot(retour, remote, extra=extra)['status'][:3], '403', (remote, extra))
        self.assertEqual(self.depot(retour, chemin='/api440/autre')['status'][:3], '403')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM google5697_retours').fetchone()[0], 0)
        self.assertEqual(self.depot(retour)['status'][:3], '200')       # l'état n'a pas été consommé par les refus

    def test_G03_chemin_authentifie_inchange_avec_la_session(self):
        retour = self.demarrer()
        out = gw.api(self.env_session(), self.desk, {'csrf': 'jeton-csrf-fictif'}, PREFIXE, 'm5689/oauth/google/callback', retour, 'GET')
        self.assertIn('google=connecte', dict(out['headers'])['Location'])

    def test_G02_carte_gmail_adresse_bouton_copier_et_trois_lignes(self):
        from agent import gmail_web5697
        gm.regler(self.desk, 'administrateur', True)
        html = gmail_web5697.section_html(self.desk, PREFIXE, OWNER, 'administrateur')
        self.assertIn(CALCULEE, html)
        self.assertIn('data-g110-copier', html)
        self.assertIn('Application de bureau', html)
        self.assertIn('URI de redirection autorisés', html)
        self.assertIn(PREFIXE + '/static/v56110-google-poste.js', html)
        self.assertIn(PREFIXE + '/api440/m5689/oauth/google/attente', html)


# ------------------------------------------------------------------------------------------------ calendar568 (agenda 5.6.8) : mêmes règles
class Agenda568(Base):
    def setUp(self):
        super().setUp()
        self.desk.c['google_calendar568'] = {'client_id': 'client-agenda-fictif', 'client_secret_file': self.secret, 'redirect_uri': ''}
        self.echanges = []

        def jeton(data):
            self.echanges.append(dict(data))
            return {'access_token': 'acc', 'token_type': 'Bearer', 'scope': ' '.join(calendar568.SCOPES), 'refresh_token': 'ref-agenda'}
        p = patch.object(calendar568, '_token_request', jeton)
        p.start()
        self.addCleanup(p.stop)

    def demarrer(self, owner=OWNER):
        out = calendar568.oauth_start(self.desk, owner)
        return q(out['url'])

    def test_adresse_calculee_exemple_et_installation(self):
        for valeur in ('', 'https://agent.example.com/api440/m568/google/callback', 'http://127.0.0.1:%d/api440/m568/google/callback' % PORT):
            self.desk.c['google_calendar568']['redirect_uri'] = valeur
            self.assertEqual(self.demarrer()['redirect_uri'], CALCULEE_AGENDA, valeur)
        for mauvaise in ('http://localhost:%d%s/api440/m568/google/callback' % (PORT, PREFIXE), 'http://127.0.0.1:1%s/api440/m568/google/callback' % PREFIXE):
            self.desk.c['google_calendar568']['redirect_uri'] = mauvaise
            with self.assertRaises(Stop) as ctx:
                self.demarrer()
            self.assertEqual(str(ctx.exception), 'google_redirect_invalide')

    def test_retour_en_deux_temps_rejeu_et_autre_personne(self):
        p = self.demarrer()
        self.assertEqual(p['code_challenge_method'], 'S256')
        page = self.depot({'state': p['state'], 'code': 'code-agenda'}, chemin='/api440/m568/google/callback')
        self.assertEqual(page['status'][:3], '200')
        self.assertIn(PREFIXE + '/api440/m568/google/terminer', page['body'])
        self.assertEqual(self.depot({'state': p['state'], 'code': 'code-agenda'}, chemin='/api440/m568/google/callback')['status'][:3], '400')
        self.assertEqual(calendar568.oauth_waiting(self.desk, 'martin@example.test')['etat'], 'attente')
        with self.assertRaises(Stop):
            calendar568.oauth_finish_deposit(self.desk, 'martin@example.test')
        self.assertEqual(self.echanges, [])
        self.assertEqual(calendar568.oauth_waiting(self.desk, OWNER)['etat'], 'recu')
        self.assertTrue(calendar568.oauth_finish_deposit(self.desk, OWNER)['connected'])
        self.assertEqual(len(self.echanges), 1)
        e = self.echanges[0]
        self.assertEqual((e['redirect_uri'], e['code'], e['grant_type']), (CALCULEE_AGENDA, 'code-agenda', 'authorization_code'))
        self.assertTrue(e['code_verifier'])
        with self.assertRaises(Stop):
            calendar568.oauth_finish_deposit(self.desk, OWNER)               # rejeu
        with self.assertRaises(Stop):
            calendar568.oauth_finish(self.desk, OWNER, {'state': p['state'], 'code': 'code-agenda'})
        self.assertEqual(len(self.echanges), 1)

    def test_route_web_terminer(self):
        from agent import web_rules568
        p = self.demarrer()
        self.depot({'state': p['state'], 'code': 'code-agenda'}, chemin='/api440/m568/google/callback')
        env = {'HTTP_X_AXIORHUB_USER': OWNER, 'HTTP_X_AXIORHUB_ROLE': 'administrateur'}
        attente = web_rules568.api(env, self.desk, {'csrf': 'x'}, PREFIXE, 'google/attente', {}, 'GET')
        self.assertEqual(json.loads(attente['body'])['etat'], 'recu')
        out = web_rules568.api(env, self.desk, {'csrf': 'x'}, PREFIXE, 'google/terminer', {}, 'GET')
        self.assertEqual(dict(out['headers'])['Location'], PREFIXE + '/parametres/agendas?google=connected')


class Agenda568Serveur(Base):
    poste = False

    def test_serveur_exemple_et_bouclage_refuses_https_inchange(self):
        self.desk.c['google_calendar568'] = {'client_id': 'c', 'client_secret_file': self.secret, 'redirect_uri': 'https://agent.example.com/api440/m568/google/callback'}
        with self.assertRaises(Stop) as ctx:
            calendar568.oauth_start(self.desk, OWNER)
        self.assertEqual(str(ctx.exception), 'google_redirect_exemple')
        self.desk.c['google_calendar568']['redirect_uri'] = CALCULEE_AGENDA
        with self.assertRaises(Stop) as ctx:
            calendar568.oauth_start(self.desk, OWNER)
        self.assertEqual(str(ctx.exception), 'google_redirect_invalide')
        self.desk.c['google_calendar568']['redirect_uri'] = 'https://cabinet.example.test/agent-courriel/api440/m568/google/callback'
        self.assertEqual(q(calendar568.oauth_start(self.desk, OWNER)['url'])['redirect_uri'], 'https://cabinet.example.test/agent-courriel/api440/m568/google/callback')
        with self.assertRaises(Stop) as ctx:
            calendar568.oauth_deposit(self.desk, {'state': 'x', 'code': 'y'})
        self.assertEqual(str(ctx.exception), 'google_retour_poste_seulement')


# ------------------------------------------------------------------------------------------------ G03 de bout en bout : interface WSGI du poste
class InterfacePoste(Base):
    def setUp(self):
        super().setUp()
        from agent.web import App
        private_json(self.config, {k: v for k, v in self.desk.c.items() if k != '_path'})
        env = patch.dict(os.environ, {})                                   # App() pose AXIORHUB_AUTH_FILE : remis en place après l'essai
        env.start()
        self.addCleanup(env.stop)
        self.password = 'mot-de-passe-local-fictif'
        import hashlib
        sel = 'bb' * 16
        self.ecrire_auth({'poste': True, 'origin': ORIGINE, 'prefix': PREFIXE, 'salt': sel,
                          'hash': hashlib.scrypt(self.password.encode(), salt=bytes.fromhex(sel), n=16384, r=8, p=1).hex()})
        self.app = App(str(self.config), str(self.f.base / 'ui-auth.json'))
        self.basic = 'Basic ' + base64.b64encode(('admin:' + self.password).encode()).decode()

    def get(self, chemin, query='', basic=False, remote='127.0.0.1'):
        env = {'REQUEST_METHOD': 'GET', 'PATH_INFO': PREFIXE + chemin, 'QUERY_STRING': query, 'HTTP_HOST': '127.0.0.1:%d' % PORT, 'REMOTE_ADDR': remote,
               'wsgi.url_scheme': 'http', 'wsgi.input': io.BytesIO(b''), 'CONTENT_LENGTH': '0'}
        if basic:
            env['HTTP_AUTHORIZATION'] = self.basic
        out = {}
        out['body'] = b''.join(self.app(env, lambda status, headers: out.update(status=status, headers=dict(headers)))).decode('utf-8', 'replace')
        return out

    def test_G03_retour_sans_session_depose_puis_la_fenetre_termine(self):
        desk = Desk(load_config(self.config))
        self.addCleanup(desk.db.close)
        gm.regler(desk, 'administrateur', True)
        depart = gg.demarrer(desk, OWNER, 'gmail', gg.session_de({'HTTP_AUTHORIZATION': self.basic}))
        self.assertEqual(q(depart['url'])['redirect_uri'], CALCULEE)
        retour = self.google.consentir(depart['url'], 'durand.gmail@example.test')
        page = self.get('/api440/m5689/oauth/google/callback', urlencode(retour))       # navigateur sans session (cookie Strict, Ubuntu)
        self.assertTrue(page['status'].startswith('200'), page['status'])
        self.assertIn('Terminer la connexion', page['body'])
        self.assertEqual(self.compter_jetons(), 0)
        self.assertTrue(self.get('/api440/m5689/oauth/google/terminer')['status'].startswith(('401', '303')))   # sans session : rien
        self.assertEqual(self.compter_jetons(), 0)
        fin = self.get('/api440/m5689/oauth/google/terminer', basic=True)
        self.assertTrue(fin['status'].startswith('303'), fin)
        self.assertIn('google=connecte', fin['headers']['Location'])
        self.assertEqual(self.compter_jetons(), 1)

    def test_G03_adresse_distante_refusee_par_l_interface(self):
        page = self.get('/api440/m5689/oauth/google/callback', 'state=x&code=y', remote='192.168.1.20')
        self.assertFalse(page['status'].startswith('200'), page['status'])


class InterfaceServeur(InterfacePoste):
    def test_G03_retour_sans_session_hors_poste_reste_refuse(self):
        self.ecrire_auth({'poste': False, 'origin': ORIGINE, 'prefix': PREFIXE, 'salt': 'bb' * 16, 'hash': 'x'})
        page = self.get('/api440/m5689/oauth/google/callback', 'state=x&code=y')
        self.assertTrue(page['status'].startswith(('401', '303')), page['status'])
        self.assertNotIn('Terminer la connexion', page['body'])

    test_G03_retour_sans_session_depose_puis_la_fenetre_termine = None
    test_G03_adresse_distante_refusee_par_l_interface = None


if __name__ == '__main__':
    unittest.main()
