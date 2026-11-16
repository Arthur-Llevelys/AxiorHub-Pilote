"""5.6.104 : veille de sécurité — fausses alertes sur les liens symboliques (S10, S13, S07), Postfix (S07), version « 0.0.0 » (S09).

Données fictives seulement ; chemins génériques des logiciels concernés (aucun chemin du serveur du cabinet)."""
import unittest

from test_v56101_securite import Base, V, serveur_sain

UNITE = ('[Unit]\nDescription=essai\n[Service]\nType=oneshot\nUser=root\n'
         'ExecStart=/usr/bin/python3 -B /opt/axiorhub-mail-agent/current/miseajour.py --executer\n')


class Liens(Base):
    def code_axiorhub(self, proprietaire_version=0):
        """Arbre d'AxiorHub : « current » est un lien (root, rwxrwxrwx comme tout lien) vers releases/9.9.99."""
        r = self.r
        r.d('/opt', '0755', 0, 0)
        r.d('/opt/axiorhub-mail-agent', '0755', 0, 0)
        r.d('/opt/axiorhub-mail-agent/releases', '0755', 0, 0)
        r.d('/opt/axiorhub-mail-agent/releases/9.9.99', '0755', proprietaire_version, proprietaire_version)
        r.f('/opt/axiorhub-mail-agent/releases/9.9.99/miseajour.py', '# script\n', '0644', 0, 0)
        r.f('/opt/axiorhub-mail-agent/current/miseajour.py', '# script\n', '0644', 0, 0)
        r.meta['/opt/axiorhub-mail-agent/current'] = {'lien': '/opt/axiorhub-mail-agent/releases/9.9.99', 'uid': 0, 'gid': 0}
        r.f('/etc/systemd/system/axiorhub-mail-agent-maj.service', UNITE)

    def s10(self, rapport):
        return {c['cle']: c for c in rapport['constats'] if c['id'] == 'S10' and c['cle'].startswith('S10:script|')}

    def test_L01_lien_root_vers_un_dossier_root_n_est_pas_une_alerte(self):
        serveur_sain(self.r)
        self.code_axiorhub()
        self.assertEqual(self.s10(self.passer('complet', sonde=False)), {})

    def test_L02_lien_vers_un_dossier_de_www_data_reste_critique_avec_une_commande_juste(self):
        serveur_sain(self.r)
        self.code_axiorhub(proprietaire_version=33)
        constat = self.s10(self.passer('complet', sonde=False))['S10:script|/opt/axiorhub-mail-agent/current/miseajour.py']
        self.assertEqual(constat['niveau'], 'critique')
        self.assertTrue(any('/opt/axiorhub-mail-agent/releases/9.9.99' in v for v in constat['vu']), constat['vu'])
        self.assertIn('sudo chown -R root:root /opt/axiorhub-mail-agent', constat['commandes'])
        self.assertFalse([c for c in constat['commandes'] if '/usr/local/sbin' in c])   # déplacer le script casserait la mise à jour

    def test_L03_mutation_les_droits_du_lien_lui_meme_redonneraient_la_fausse_alerte(self):
        serveur_sain(self.r)
        self.code_axiorhub()
        classe = next(c for c in vars(V).values() if isinstance(c, type) and hasattr(c, 'ecrivable_web'))
        ancienne = classe.ecrivable_web

        def sans_correctif(self, chemin):
            m = self.meta(chemin)
            if m and V.stat.S_ISLNK(m[0]):
                return True             # ancien comportement : rwxrwxrwx du lien pris pour des droits
            return ancienne(self, chemin)
        classe.ecrivable_web = sans_correctif
        try:
            self.assertTrue(self.s10(self.passer('complet', sonde=False)))
        finally:
            classe.ecrivable_web = ancienne

    def test_L04_config_en_lien_vers_l_etat_du_service_n_est_pas_s13(self):
        serveur_sain(self.r)
        self.r.f('/var/lib/axiorhub-mail-agent/configuration567/config.json', '{}', '0600', 998, 998)
        self.r.f('/etc/axiorhub-mail-agent/config.json', '{}', '0600', 998, 998)
        self.r.meta['/etc/axiorhub-mail-agent/config.json'] = {'lien': '/var/lib/axiorhub-mail-agent/configuration567/config.json'}
        s13 = [c for c in self.passer('complet', sonde=False)['constats'] if c['id'] == 'S13' and c['cle'].endswith('config.json')]
        self.assertEqual(s13, [])

    def test_L05_config_reellement_ouverte_reste_s13(self):
        serveur_sain(self.r)
        self.r.f('/etc/axiorhub-mail-agent/config.json', '{}', '0666', 0, 998)
        s13 = [c for c in self.passer('complet', sonde=False)['constats'] if c['id'] == 'S13' and c['cle'].endswith('config.json')]
        self.assertEqual(len(s13), 1)


class Secrets(Base):
    def test_P01_cf_de_postfix_sans_mot_de_passe_n_est_pas_un_secret(self):
        serveur_sain(self.r)
        self.r.f('/etc/postfix/dynamicmaps.cf', 'mysql\tpostfix-mysql.so.1.0.1\tdict_mysql_open\n', '0644', 0, 0)
        s07 = [c for c in self.passer('complet', sonde=False)['constats'] if c['id'] == 'S07' and 'dynamicmaps' in c['cle']]
        self.assertEqual(s07, [])

    def test_P02_cf_de_postfix_avec_mot_de_passe_reste_signale_et_la_commande_ne_change_ni_proprietaire_ni_groupe(self):
        serveur_sain(self.r)
        self.r.f('/etc/postfix/mysql-virtual.cf', 'user = postfix\npassword = fictif\nhosts = 127.0.0.1\n', '0644', 0, 0)
        s07 = [c for c in self.passer('complet', sonde=False)['constats'] if c['id'] == 'S07' and 'mysql-virtual' in c['cle']]
        self.assertEqual(len(s07), 1)
        commandes = s07[0]['commandes']
        self.assertIn('sudo chmod o-r /etc/postfix/mysql-virtual.cf', commandes)
        self.assertFalse([c for c in commandes if 'chown' in c and not c.startswith('#')])   # le service qui lit le fichier garde son accès

    def test_P03_env_en_lien_vers_un_fichier_protege_n_est_pas_signale(self):
        serveur_sain(self.r)
        self.r.f('/srv/appli/secrets.env', 'API_KEY=fictif\n', '0600', 0, 0)
        self.r.f('/srv/appli/.env', 'API_KEY=fictif\n', '0600', 0, 0)
        self.r.meta['/srv/appli/.env'] = {'lien': '/srv/appli/secrets.env'}
        s07 = [c for c in self.passer('complet', sonde=False)['constats'] if c['id'] == 'S07' and c['cle'].endswith('/srv/appli/.env')]
        self.assertEqual(s07, [])


class Versions(Base):
    def test_W01_snappymail_0_0_0_est_une_version_inconnue_pas_obsolete(self):
        serveur_sain(self.r)
        self.r.f('/var/www/webmail-essai/snappymail/v/0.0.0/index.php', '<?php\n')
        rapport = self.passer('complet', sonde=False)
        self.assertFalse([c for c in rapport['constats'] if c['id'] == 'S09' and '0.0.0' in c['titre']])




class Affichage(unittest.TestCase):
    def test_A01_un_bloc_de_commandes_garde_une_commande_par_ligne(self):
        from pathlib import Path
        css = (Path(__file__).resolve().parent.parent / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8')
        en_ligne = css.index(':root body[data-carbon] #ws-main code,')
        bloc = css.index(':root body[data-carbon] #ws-main pre code {')
        self.assertGreater(bloc, en_ligne)                                   # la règle du bloc vient après celle du code en ligne
        self.assertIn('white-space: inherit', css[bloc:bloc + 120])
        from agent.securite_web56101 import _bloc_commandes
        html = _bloc_commandes(['sudo chmod o-r /srv/appli/.env', '# puis changez les clés'], 'x')
        self.assertIn('sudo chmod o-r /srv/appli/.env\n# puis changez les clés', html)


if __name__ == '__main__':
    unittest.main()
