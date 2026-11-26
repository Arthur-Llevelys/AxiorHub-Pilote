"""5.6.110 : session de la fenêtre du poste prolongée par l'usage ; session expirée sans demande d'identifiants du navigateur.

Retour d'essai du cabinet : sur un poste Windows 11 ou Ubuntu 24.04, « au bout d'un certain temps », l'application demandait de se
connecter à ``http://127.0.0.1:<port>``. Cause : session de douze heures fixes, puis réponse 401 avec ``WWW-Authenticate``.
Aucun réseau, aucun service réel ; l'horloge du module est remplacée dans chaque essai et remise en place.
"""
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from agent import poste_session   # noqa: E402
import test_v5625 as base   # noqa: E402 — banc de la session du poste (fixture seulement, ses essais ne sont pas rejoués ici)

JOUR = 86400


class Horloge:
    """Remplace ``time`` dans ``poste_session`` seulement."""

    def __init__(self):
        self.maintenant = time.time()

    def time(self):
        return self.maintenant


class SessionProlongee(unittest.TestCase):
    _base_setup = base.Session._base_setup
    call = base.Session.call

    def setUp(self):
        base.Session.setUp(self)
        self.horloge = Horloge()
        p = patch.object(poste_session, 'time', self.horloge)
        p.start()
        self.addCleanup(p.stop)
        self.fichier = Path(self.auth).parent / poste_session.SESSIONS

    def ouvrir(self):
        token = poste_session.new_launch_token(self.auth)
        r = self.call('/agent-courriel/poste-session', 'jeton=%s&suite=%%2Faujourdhui' % token)
        self.assertTrue(r['status'].startswith('303'), r)
        return dict(r['headers'])['Set-Cookie'].split(';')[0]

    def dates(self):
        return list(json.loads(self.fichier.read_text(encoding='utf-8')).values())[0]

    def test_la_session_ne_s_arrete_plus_au_bout_de_douze_heures(self):
        sid = self.ouvrir()
        debut = self.horloge.maintenant
        expire, limite = self.dates()
        self.assertAlmostEqual(expire, debut + 7 * JOUR, delta=5)
        self.assertAlmostEqual(limite, debut + 30 * JOUR, delta=5)
        self.horloge.maintenant = debut + 13 * 3600                     # avant la 5.6.110 : session expirée
        self.assertTrue(self.call('/agent-courriel/', cookie=sid)['status'].startswith('200'))
        self.assertAlmostEqual(self.dates()[0], debut + 13 * 3600 + 7 * JOUR, delta=5)   # prolongée par l'usage
        self.assertAlmostEqual(self.dates()[1], limite, delta=1)                         # la limite absolue ne bouge pas

    def test_prolongation_ecrite_au_plus_toutes_les_dix_minutes(self):
        sid = self.ouvrir()
        debut = self.horloge.maintenant
        self.horloge.maintenant = debut + 300
        self.assertTrue(poste_session.cookie_ok(self.auth, {'HTTP_COOKIE': sid}))
        self.assertAlmostEqual(self.dates()[0], debut + 7 * JOUR, delta=5)               # cinq minutes : rien n'est récrit
        self.horloge.maintenant = debut + 900
        self.assertTrue(poste_session.cookie_ok(self.auth, {'HTTP_COOKIE': sid}))
        self.assertAlmostEqual(self.dates()[0], debut + 900 + 7 * JOUR, delta=5)

    def test_sept_jours_sans_activite_page_claire_sans_demande_d_identifiants(self):
        sid = self.ouvrir()
        self.horloge.maintenant += 7 * JOUR + 60
        r = self.call('/agent-courriel/aujourdhui', cookie=sid)
        self.assertTrue(r['status'].startswith('401'), r['status'])
        self.assertNotIn('WWW-Authenticate', dict(r['headers']))        # plus de fenêtre « Se connecter à http://127.0.0.1:… »
        self.assertIn('session sur ce poste a expiré', r['body'])
        self.assertIn('rouvrez AxiorHub Pilote', r['body'])
        api = self.call('/agent-courriel/api440/m567/missions', cookie=sid)
        self.assertTrue(api['status'].startswith('401'))
        self.assertNotIn('WWW-Authenticate', dict(api['headers']))      # une requête de fond ne déclenche pas non plus la demande
        self.assertEqual(json.loads(api['body'])['error'], 'authentification_requise')

    def test_trente_jours_au_plus_meme_avec_un_usage_regulier(self):
        sid = self.ouvrir()
        debut = self.horloge.maintenant
        for jour in (5, 10, 15, 20, 25, 29):
            self.horloge.maintenant = debut + jour * JOUR
            self.assertTrue(poste_session.cookie_ok(self.auth, {'HTTP_COOKIE': sid}), jour)
        self.assertLessEqual(self.dates()[0], debut + 30 * JOUR + 1)
        self.horloge.maintenant = debut + 30 * JOUR + 60
        self.assertFalse(poste_session.cookie_ok(self.auth, {'HTTP_COOKIE': sid}))

    def test_sans_cookie_ou_avec_des_identifiants_la_demande_habituelle_reste(self):
        r = self.call('/agent-courriel/aujourdhui')
        self.assertTrue(r['status'].startswith('401'))
        self.assertIn('WWW-Authenticate', dict(r['headers']))           # accès par identifiants toujours possible sur un poste
        self.assertFalse(poste_session.cookie_presente({}))
        self.assertTrue(poste_session.cookie_presente({'HTTP_COOKIE': 'autre=1; axh_poste=perime'}))
        self.assertFalse(poste_session.cookie_presente({'HTTP_COOKIE': 'autre=axh_poste'}))

    def test_session_ouverte_avant_la_5_6_110_reconnue_et_prolongee(self):
        sid = self.ouvrir()
        cle = list(json.loads(self.fichier.read_text(encoding='utf-8')))[0]
        debut = self.horloge.maintenant
        self.fichier.write_text(json.dumps({cle: debut + 3600}), encoding='utf-8')       # ancien format : une seule date, une heure restante
        self.assertTrue(poste_session.cookie_ok(self.auth, {'HTTP_COOKIE': sid}))
        expire, limite = self.dates()
        self.assertGreater(expire, debut + JOUR)
        self.assertLessEqual(expire, limite)
        self.fichier.write_text('pas du json', encoding='utf-8')
        self.assertFalse(poste_session.cookie_ok(self.auth, {'HTTP_COOKIE': sid}))        # fichier illisible : jamais un accès élargi

    def test_page_de_session_expiree_sans_donnee_ni_lien(self):
        page = poste_session.page_expiree()
        self.assertIn('lang="fr"', page)
        self.assertNotIn('href', page)
        self.assertNotIn('jeton', page)


if __name__ == '__main__':
    unittest.main()
