"""5.6.112 (relecture de justesse 5.6.113) : une session ouverte par cookie ne devient jamais le jeton de service parce que
le navigateur ajoute un en-tête « Authorization: Bearer … ». Avant le correctif, une avocate connectée obtenait les pages et
les actions de l'administrateur (création d'un compte administrateur comprise) en ajoutant un Bearer quelconque."""
import test_v5648 as t


class SessionEtBearer(t.Base):
    add = t.CabinetAccounts.add
    first_login = t.CabinetAccounts.first_login

    def _avocate(self):
        admin = self.admin_cookie()
        provisional = self.add(admin)
        return self.first_login('avocate@example.test', provisional)

    def test_B01_page_administrateur_refusee_meme_avec_un_bearer(self):
        cookie = self._avocate()
        self.assertEqual(self.request('/parametres', cookie=cookie)['status'][:3], '403')
        avec = self.request('/parametres', cookie=cookie, extra={'HTTP_AUTHORIZATION': 'Bearer nimportequoi'})
        self.assertEqual(avec['status'][:3], '403')

    def test_B02_creation_d_un_administrateur_refusee_meme_avec_un_bearer(self):
        cookie = self._avocate()
        corps = {'ident': 'intrus@example.test', 'role': 'administrateur', 'password': 'p' * 12}
        avec = self.call('/api440/m5636/comptes/ajouter', 'POST', corps, auth=False,
                         extra={'HTTP_COOKIE': '%s=%s' % (t.cx.COOKIE, cookie), 'HTTP_AUTHORIZATION': 'Bearer x'})
        self.assertEqual(avec['status'][:3], '403', avec['body'][:200])
        db = t.cx.connect(self.state)
        try:
            self.assertIsNone(db.execute("SELECT 1 FROM users WHERE ident='intrus@example.test'").fetchone())
        finally:
            db.close()
