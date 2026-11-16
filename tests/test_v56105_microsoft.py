"""5.6.105 : l'application Microsoft intégrée d'AxiorHub est inscrite ; son identifiant (public, sans secret) est livré.

Faux Microsoft de la 5.6.103 (aucun appel réseau réel). Sans application propre du serveur, c'est l'application intégrée qui est utilisée
(poste : PKCE, serveur : code d'appareil) ; une application propre renseignée dans la configuration passe avant elle."""
import unittest
from urllib.parse import parse_qs, urlsplit

from agent import microsoft56103 as ms, microsoft_web56103 as mw, microsoft5636 as m36
import test_v56103_microsoft as t103

INTEGREE = '0abc87ae-0917-4cd3-afa0-24129143d776'
PROPRE = '00000000-0000-4000-8000-0000000000b2'      # application propre fictive du serveur


class Integree(t103.Base):
    client = ''                                       # aucune application propre : l'application intégrée s'applique

    def test_N01_identifiant_livre_public_et_bien_forme(self):
        self.assertEqual(ms.CLIENT_ID_INTEGRE, INTEGREE)
        self.assertTrue(ms.GUID.match(ms.CLIENT_ID_INTEGRE))
        self.assertEqual(ms.CLIENT_ID_INTEGRE, ms.CLIENT_ID_INTEGRE.lower())
        self.assertEqual(ms.client_id(self.desk), INTEGREE)
        self.assertTrue(ms.disponible(self.desk))

    def test_N02_assistant_et_parametres_proposent_la_connexion_sans_appel(self):
        etapes = ms.etapes_assistant(self.desk, t103.CLAIRE)
        self.assertEqual(etapes[0]['etat'], 'a_faire')
        self.assertEqual(etapes[0]['action']['route'], 'm56103/microsoft/connexion')
        self.assertNotIn('pas encore disponible', mw.section_html(self.desk, '/p', t103.CLAIRE, {}))
        self.assertEqual(self.appels_reseau(), 0)

    def test_N03_poste_pkce_avec_l_application_integree(self):
        depart = ms.demarrer_pkce(self.desk, t103.CLAIRE, ['courrier'], 50123)
        q = {k: v[0] for k, v in parse_qs(urlsplit(depart['url']).query).items()}
        self.assertEqual(q['client_id'], INTEGREE)
        self.assertEqual(q['redirect_uri'], 'http://localhost:50123' + ms.CHEMIN_RETOUR)
        self.assertNotIn('Mail.Send', q['scope'])
        out = ms.terminer_pkce(self.desk, self.ms.consentir(depart['url']))
        self.assertEqual(out['connexion']['etat'], 'active')

    def test_N04_serveur_code_d_appareil_avec_l_application_integree(self):
        ms.demarrer_appareil(self.desk, t103.CLAIRE, ['courrier'])
        self.assertEqual({a['client'] for a in self.ms.appareils.values()}, {INTEGREE})

    def test_N05_agenda_outlook_propose_l_application_integree(self):
        self.assertEqual(m36.config(self.desk)['application'], 'integree')
        self.assertNotIn('pas encore disponible', m36.settings_html(self.desk, '/p', {}))


class Propre(t103.Base):
    client = PROPRE

    def test_N06_application_propre_du_serveur_prioritaire(self):
        self.assertEqual(ms.client_id(self.desk), PROPRE)
        depart = ms.demarrer_pkce(self.desk, t103.CLAIRE, ['courrier'], 50123)
        self.assertEqual(parse_qs(urlsplit(depart['url']).query)['client_id'][0], PROPRE)
        ms.CLIENT_ID_INTEGRE, avant = PROPRE.replace('b2', 'b3'), ms.CLIENT_ID_INTEGRE
        try:                                          # quelle que soit l'application intégrée
            self.assertEqual(ms.client_id(self.desk), PROPRE)
        finally:
            ms.CLIENT_ID_INTEGRE = avant


if __name__ == '__main__':
    unittest.main()
