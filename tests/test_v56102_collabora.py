"""5.6.102 (lot C) : veille de sécurité — le Collabora intégré à Nextcloud (« Built-in CODE Server », application richdocumentscode)
lancé en mode --appimage-extract-and-run n'est plus signalé comme programme d'un dossier temporaire ou web (S03), sans exception
fondée sur le seul nom.

Règle vérifiée : l'AppImage n'est reconnue qu'à sa place (``<racine Nextcloud>/apps/richdocumentscode(_arm64)/collabora/
Collabora_Online.AppImage``, fichier régulier, non effacé, hors dossier temporaire) ; un programme de ``/tmp/appimage_extracted_<hexa>/``
n'est reconnu que si la chaîne de ses parents remonte jusqu'à cette AppImage en ne passant que par des programmes du même dossier.

Données fictives : faux /proc de ``test_v56101_securite`` (FausseRacine), Nextcloud dans /var/www/nextcloud, domaine cloud.example.test,
adresse extérieure de la plage documentaire 203.0.113.0/24. Exécuter avec PYTHONPATH=<racine AxiorHub>:<racine AxiorHub>/tests.
"""
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import test_v56101_securite as base
import test_agent as fixtures
from agent import securite56101 as s, securite_web56101 as sw
from agent.desk import Desk

V = base.V
NC = '/var/www/nextcloud'
APPIMAGE = NC + '/apps/richdocumentscode/collabora/Collabora_Online.AppImage'
HEXA = '0123456789abcdef0123456789abcdef'
AUTRE_HEXA = 'fedcba9876543210fedcba9876543210'
EXTRAIT = '/tmp/appimage_extracted_%s' % HEXA
EXTERIEURE = '203.0.113.60'


def avec_parent(r, pid, ppid):
    """Le faux /proc de 5.6.101 écrit toujours ppid = 1 : on fixe ici le parent réel."""
    p = r.base / ('proc/%d/stat' % pid)
    texte = p.read_text(encoding='utf-8')
    tete, reste = texte.rsplit(') ', 1)
    champs = reste.split(' ')
    champs[1] = str(ppid)
    p.write_text(tete + ') ' + ' '.join(champs), encoding='utf-8')


def proc(r, pid, ppid, comm, exe, cmd, uid=33, **k):
    r.proc(pid, comm, exe, cmd, uid=uid, **k)
    avec_parent(r, pid, ppid)


def sans_fuse(r):
    """Retire le Collabora en mode FUSE (processus 600) de ``serveur_sain`` : les comptes de la ligne d'information portent alors sur la
    seule famille --appimage-extract-and-run (l'exception FUSE est vérifiée à part, C06)."""
    import shutil
    shutil.rmtree(r.base / 'proc/600')
    return r


def collabora(r, appimage=APPIMAGE, extrait=EXTRAIT, parent=200, fichier=True, sockets_wsd=('8811',)):
    """Collabora intégré lancé par le serveur web (apache2, processus 200 de ``serveur_sain``) sans FUSE."""
    if fichier:
        r.f(appimage, 'AppImage fictive')
        if '/collabora/' in appimage:
            r.f(appimage.rsplit('/collabora/', 1)[0] + '/appinfo/info.xml', '<info><id>richdocumentscode</id></info>')
    proc(r, 810, parent, 'Collabora_Onlin', appimage,
         appimage + ' --appimage-extract-and-run --o:remote_font_config.url=https://cloud.example.test/apps/richdocuments/settings/fonts.json')
    proc(r, 811, 810, 'coolwsd', extrait + '/usr/bin/coolwsd',
         'coolwsd --config-file=%s/etc/coolwsd/coolwsd.xml --disable-cool-user-checking --port=9983 --lo-template-path=%s/opt/collaboraoffice'
         % (extrait, extrait), sockets=sockets_wsd)
    proc(r, 812, 811, 'coolforkit-ns', extrait + '/usr/bin/coolforkit-ns',
         '--systemplate=/tmp/coolwsd.Qx81Za/systemplate/ --lotemplate=%s/opt/collaboraoffice' % extrait)
    proc(r, 813, 812, 'kitbroker_001', extrait + '/usr/bin/coolforkit-ns', '--systemplate=/tmp/coolwsd.Qx81Za/systemplate/')
    r.connexion('127.0.0.1', 40100, '127.0.0.1', 6379, '01', '8811', 33)          # Redis de Nextcloud : local, rien à signaler
    return r


class Collecteur(base.Base):
    def setUp(self):
        super().setUp()
        sans_fuse(base.serveur_sain(self.r))

    def constats(self, rapport, ident):
        return [c for c in rapport['constats'] if c['id'] == ident]

    def test_C01_appimage_richdocumentscode_et_enfants_extraits_aucun_S03_ni_S02_une_ligne_d_information(self):
        collabora(self.r)
        rapport = self.passer('rapide')
        self.assertEqual(self.constats(rapport, 'S03'), [])
        self.assertEqual(self.constats(rapport, 'S02'), [])
        self.assertEqual(rapport['controles']['S03'], 'ok')
        self.assertEqual(rapport['erreurs'], [])
        self.assertEqual(len(rapport['reconnus']), 1)
        info = rapport['reconnus'][0]
        self.assertEqual((info['id'], info['processus']), ('collabora', 4))
        self.assertIn('Collabora intégré à Nextcloud reconnu (4 processus)', info['titre'])
        self.assertNotIn('/var/www', json.dumps(info, ensure_ascii=False))      # aucun chemin inutile
        # passage complet : pas davantage de S02 ou S03
        rapport = self.passer('complet', sonde=False)
        self.assertEqual(self.constats(rapport, 'S03') + self.constats(rapport, 'S02'), [])

    def test_C01_variante_arm64_et_programme_extrait_efface_en_cours_d_execution(self):
        collabora(self.r, appimage=NC + '/apps/richdocumentscode_arm64/collabora/Collabora_Online.AppImage')
        proc(self.r, 814, 812, 'kit_spare', EXTRAIT + '/usr/bin/coolforkit-ns (deleted)', '--systemplate=/tmp/coolwsd.Qx81Za/systemplate/')
        rapport = self.passer('rapide')
        self.assertEqual(self.constats(rapport, 'S03') + self.constats(rapport, 'S02'), [])
        self.assertEqual(rapport['reconnus'][0]['processus'], 5)

    def test_C01_serveur_sans_collabora_aucune_ligne_d_information(self):
        rapport = self.passer('rapide')
        self.assertEqual(rapport['reconnus'], [])

    def test_C02_meme_coolwsd_extrait_lance_par_un_shell_www_data_S03_critique(self):
        proc(self.r, 820, 200, 'bash', '/usr/bin/bash', 'bash -c ./coolwsd')
        proc(self.r, 821, 820, 'coolwsd', EXTRAIT + '/usr/bin/coolwsd', 'coolwsd --port=9983')
        rapport = self.passer('rapide')
        s03 = self.constats(rapport, 'S03')
        self.assertEqual([c['niveau'] for c in s03], ['critique'])
        self.assertIn('appimage_extracted_', s03[0]['titre'])
        self.assertEqual(rapport['reconnus'], [])

    def test_C02_parent_absent_ou_dossier_extrait_d_une_autre_AppImage_S03(self):
        collabora(self.r)
        proc(self.r, 830, 99999, 'coolwsd', EXTRAIT + '/usr/bin/coolwsd', 'coolwsd --port=9983')                 # parent absent
        proc(self.r, 834, 820, 'bash', '/usr/bin/bash', 'bash')
        proc(self.r, 831, 834, 'coolwsd', '/tmp/appimage_extracted_%s/usr/bin/coolwsd' % AUTRE_HEXA, 'coolwsd')     # autre dossier
        proc(self.r, 832, 831, 'coolforkit', EXTRAIT + '/usr/bin/coolforkit', 'coolforkit')                         # via un non reconnu
        proc(self.r, 820, 200, 'bash', '/usr/bin/bash', 'bash')
        # enfant DIRECT de l'AppImage reconnue : reconnu quel que soit le dossier (règle : « directement ou via le même dossier »)
        proc(self.r, 835, 810, 'coolmount', '/tmp/appimage_extracted_%s/usr/bin/coolmount' % AUTRE_HEXA, 'coolmount')
        proc(self.r, 833, 811, 'xmr', '/tmp/appimage_extracted_%s/usr/bin/x' % AUTRE_HEXA, 'x')                     # enfant d'un reconnu, autre dossier
        rapport = self.passer('rapide')
        signales = {c['cle'].split('|')[0] for c in self.constats(rapport, 'S03')}
        self.assertEqual(signales, {'S03:%s/usr/bin/coolwsd' % EXTRAIT, 'S03:/tmp/appimage_extracted_%s/usr/bin/coolwsd' % AUTRE_HEXA,
                                    'S03:%s/usr/bin/coolforkit' % EXTRAIT, 'S03:/tmp/appimage_extracted_%s/usr/bin/x' % AUTRE_HEXA})
        self.assertTrue(all(c['niveau'] == 'critique' for c in self.constats(rapport, 'S03')))
        self.assertEqual(rapport['reconnus'][0]['processus'], 5)                 # la vraie famille (4) et l'enfant direct 835

    def test_C03_appimage_du_meme_nom_hors_de_richdocumentscode_S03(self):
        for chemin in ('/tmp/Collabora_Online.AppImage', '/var/www/html/upload/Collabora_Online.AppImage',
                       '/var/www/html/upload/apps/richdocumentscode/collabora/Collabora_Online.AppImage',   # pas de Nextcloud au-dessus
                       '/tmp/x/apps/richdocumentscode/collabora/Collabora_Online.AppImage'):                 # dossier temporaire
            with self.subTest(chemin=chemin):
                self.tearDown_racine()
                sans_fuse(base.serveur_sain(self.r))
                if chemin.startswith('/var/www/html/upload/apps/'):
                    pass
                elif '/apps/' in chemin:
                    self.r.f('/tmp/x/version.php', '<?php\n')
                collabora(self.r, appimage=chemin)
                rapport = self.passer('rapide')
                signales = {c['cle'].split('|')[0] for c in self.constats(rapport, 'S03')}
                self.assertIn('S03:' + chemin, signales)
                self.assertIn('S03:%s/usr/bin/coolwsd' % EXTRAIT, signales)    # ses enfants ne sont pas reconnus non plus
                self.assertEqual(rapport['reconnus'], [])

    def test_C03_appimage_a_sa_place_mais_effacee_ou_absente_S02_et_S03(self):
        collabora(self.r, appimage=APPIMAGE + ' (deleted)', fichier=False)
        rapport = self.passer('rapide')
        self.assertTrue(self.constats(rapport, 'S02'))
        signales = {c['cle'].split('|')[0] for c in self.constats(rapport, 'S03')}
        self.assertIn('S03:' + APPIMAGE, signales)
        self.assertIn('S03:%s/usr/bin/coolwsd' % EXTRAIT, signales)

    def test_C04_enfant_reconnu_vers_un_port_de_minage_exterieur_S04_critique(self):
        collabora(self.r, sockets_wsd=('8811', '8812'))
        self.r.connexion('192.0.2.10', 51300, EXTERIEURE, 3333, '01', '8812', 33)
        rapport = self.passer('rapide')
        s04 = self.constats(rapport, 'S04')
        self.assertEqual([c['niveau'] for c in s04], ['critique'])
        self.assertTrue(any(EXTERIEURE + ':3333' in v for v in s04[0]['vu']))
        self.assertEqual(self.constats(rapport, 'S03'), [])                     # reconnu pour S03, pas pour S04

    def test_C04_enfant_reconnu_nomme_comme_un_mineur_S04(self):
        collabora(self.r)
        proc(self.r, 815, 811, 'xmrig', EXTRAIT + '/usr/bin/xmrig', 'xmrig')
        rapport = self.passer('rapide')
        self.assertTrue(any('xmrig' in c['titre'] for c in self.constats(rapport, 'S04')))

    def test_C05_mutation_sans_verification_des_parents_C02_echouerait(self):
        proc(self.r, 820, 200, 'bash', '/usr/bin/bash', 'bash -c ./coolwsd')
        proc(self.r, 821, 820, 'coolwsd', EXTRAIT + '/usr/bin/coolwsd', 'coolwsd --port=9983')
        racine = self.r.fin()
        sortie = Path(racine) / 'var/lib/axiorhub-mail-agent/securite'
        with patch.object(V.Veille, '_parents_collabora', lambda self, *a, **k: True):
            mute = V.passer(racine, 'rapide', None, {80: self.http.server_address[1]}, False, sortie=sortie)
        self.assertEqual(self.constats(mute, 'S03'), [])                        # garde-fou retiré : le faux coolwsd passerait
        rapport = V.passer(racine, 'rapide', None, {80: self.http.server_address[1]}, False, sortie=sortie)
        self.assertEqual(len(self.constats(rapport, 'S03')), 1)                 # garde-fou en place : signalé

    def test_C06_exception_FUSE_conservee_mais_ancree_dans_un_dossier_temporaire(self):
        self.r.proc(600, 'soffice.bin', '/tmp/.mount_CollaboraX/usr/bin/soffice.bin', '/tmp/.mount_CollaboraX/AppRun --collabora', uid=998)
        rapport = self.passer('rapide')                                          # même processus que serveur_sain (5.6.101)
        self.assertEqual(self.constats(rapport, 'S03'), [])
        self.assertEqual(rapport['reconnus'][0]['processus'], 1)
        proc(self.r, 840, 200, 'outil', '/var/www/html/upload/.mount_collabora/outil', '/var/www/html/upload/.mount_collabora/outil --collabora')
        proc(self.r, 841, 200, 'outil', '/tmp/depot/.mount_collabora/outil', '/tmp/depot/.mount_collabora/outil --collabora')
        rapport = self.passer('rapide')
        signales = {c['cle'].split('|')[0] for c in self.constats(rapport, 'S03')}
        self.assertEqual(signales, {'S03:/var/www/html/upload/.mount_collabora/outil', 'S03:/tmp/depot/.mount_collabora/outil'})

    def test_C07_custom_apps_et_apps_extra_reconnus(self):
        for dossier in ('custom_apps', 'apps-extra'):
            with self.subTest(dossier=dossier):
                self.tearDown_racine()
                sans_fuse(base.serveur_sain(self.r))
                collabora(self.r, appimage=NC + '/%s/richdocumentscode/collabora/Collabora_Online.AppImage' % dossier)
                rapport = self.passer('rapide')
                self.assertEqual(self.constats(rapport, 'S03') + self.constats(rapport, 'S02'), [])
                self.assertEqual(rapport['reconnus'][0]['processus'], 4)

    def test_C07_custom_apps_sans_version_php_S03(self):
        chemin = '/var/www/html/upload/custom_apps/richdocumentscode/collabora/Collabora_Online.AppImage'
        collabora(self.r, appimage=chemin)
        rapport = self.passer('rapide')
        signales = {c['cle'].split('|')[0] for c in self.constats(rapport, 'S03')}
        self.assertIn('S03:' + chemin, signales)
        self.assertIn('S03:%s/usr/bin/coolwsd' % EXTRAIT, signales)
        self.assertEqual(rapport['reconnus'], [])
        # autre nom de dossier d'applications : non reconnu
        self.tearDown_racine()
        sans_fuse(base.serveur_sain(self.r))
        chemin = NC + '/mes_apps/richdocumentscode/collabora/Collabora_Online.AppImage'
        collabora(self.r, appimage=chemin)
        self.assertIn('S03:' + chemin, {c['cle'].split('|')[0] for c in self.constats(self.passer('rapide'), 'S03')})

    def test_C08_coolwsd_reconnu_a_forte_charge_pas_de_S01_programme_inconnu_S01(self):
        collabora(self.r, sockets_wsd=('8811', '8812'))
        proc(self.r, 811, 810, 'coolwsd', EXTRAIT + '/usr/bin/coolwsd', 'coolwsd --port=9983', cpu=0.95, age=7200, sockets=('8811', '8812'))
        proc(self.r, 850, 1, 'rendu', '/usr/local/bin/rendu', '/usr/local/bin/rendu --lot', uid=33, cpu=0.95, age=7200)
        self.r.connexion('192.0.2.10', 51301, EXTERIEURE, 3333, '01', '8812', 33)
        rapport = self.passer('rapide')
        s01 = {c['cle'].split('|')[0]: c['niveau'] for c in self.constats(rapport, 'S01')}
        self.assertEqual(s01, {'S01:/usr/local/bin/rendu': 'important'})       # même charge, programme non reconnu : S01
        self.assertEqual([c['niveau'] for c in self.constats(rapport, 'S04')], ['critique'])   # S04 reste pour le reconnu
        self.assertEqual(self.constats(rapport, 'S03'), [])
        # contre-exemple : même coolwsd sans parent reconnu → S01 (et S03), comme tout programme inconnu
        proc(self.r, 811, 99999, 'coolwsd', EXTRAIT + '/usr/bin/coolwsd', 'coolwsd --port=9983', cpu=0.95, age=7200, sockets=('8811',))
        rapport = self.passer('rapide')
        self.assertIn('S01:%s/usr/bin/coolwsd' % EXTRAIT, {c['cle'].split('|')[0] for c in self.constats(rapport, 'S01')})

    def test_C06_motif_autorises_toujours_applique(self):
        proc(self.r, 821, 99999, 'coolwsd', EXTRAIT + '/usr/bin/coolwsd', 'coolwsd')
        self.r.f('/etc/axiorhub-mail-agent/veille-securite.json', json.dumps({'autorises': ['/tmp/appimage_extracted_*/usr/bin/coolwsd']}))
        rapport = self.passer('rapide')
        self.assertEqual(self.constats(rapport, 'S03'), [])

    def tearDown_racine(self):
        import shutil
        shutil.rmtree(self.r.base, ignore_errors=True)
        self.r = base.FausseRacine(self.r.base)
        self.r.base.mkdir(parents=True, exist_ok=True)


class Interface(base.Base):
    """Constat S03 d'une version précédente : il disparaît au passage suivant ; ligne d'information et aide sur la page."""

    def setUp(self):
        super().setUp()
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        p = patch.object(s, 'plateforme', lambda auth=None: 'serveur')
        p.start()
        self.addCleanup(p.stop)
        self.dossier = Path(self.f.c['state_dir']) / 'securite'
        self.dossier.mkdir()
        (Path(self.f.c['state_dir']) / 'securite-demande').mkdir()
        collabora(sans_fuse(base.serveur_sain(self.r)))

    def passage(self, t):
        return V.passer(self.r.fin(), 'rapide', t, {80: self.http.server_address[1]}, False, sortie=self.dossier)

    def page(self):
        from agent import web440
        env = {'HTTP_X_AXIORHUB_USER': 'cabinet', 'HTTP_X_AXIORHUB_ROLE': 'administrateur', 'REQUEST_METHOD': 'GET'}
        return web440.route(env, self.f.c, {'origin': base.ORIGINE, 'csrf': base.JETON}, '/p', '/securite-serveur', {}, 'GET')

    def test_C_ancien_constat_S03_collabora_connu_disparait_sans_fantome(self):
        t = time.time() - 7200
        with patch.object(V.Veille, '_collabora', lambda self: (set(), set(), set())):   # comportement de la 5.6.101
            ancien = self.passage(t)
        s03 = [c for c in ancien['constats'] if c['id'] == 'S03']
        self.assertEqual(len(s03), 4)
        s.ingerer(self.desk, now=t + 1)
        cle = next(c['cle'] for c in s03 if 'coolwsd' in c['cle'])
        x = s.constat(self.desk, cle)
        s.marquer_connu(self.desk, 'cabinet', 'administrateur', cle, x['empreinte'], 'Collabora de Nextcloud', 'cle-c0000001')
        self.assertTrue(s.constats(self.desk, connus=True))
        self.passage(t + 600)
        s.ingerer(self.desk, now=t + 601)
        ouverts = [c['cle'] for c in s.constats(self.desk) + s.constats(self.desk, connus=True)]
        self.assertFalse([k for k in ouverts if k.startswith('S03:')], ouverts)
        self.assertEqual(s.constat(self.desk, cle)['etat'], 'resolu')
        self.assertEqual(s.nombre_alerte(self.desk, now=t + 601 + 3700), 0)    # passé l'anti-rebond d'une heure : plus rien
        self.assertEqual(s.etat(self.desk, now=t + 602)['critiques'], 0)

    def test_C_page_ligne_d_information_et_aide_S03(self):
        self.passage(time.time())
        s.ingerer(self.desk)
        html = self.page()['body']
        self.assertIn('Éléments reconnus', html)
        self.assertIn('Collabora intégré à Nextcloud reconnu (4 processus)', html)
        self.assertNotIn(HEXA, html)
        aide = html.split('id="aide-S03"', 1)[1].split('</dd>', 1)[0]
        self.assertIn('apps/richdocumentscode', aide)
        self.assertIn('sudo -u www-data php occ integrity:check-app richdocumentscode', aide)
        self.assertIn('veille-securite.json', aide)
        self.assertIn('déconseillé', aide)
        # Collabora arrêté : la ligne disparaît au rapport suivant
        for pid in (810, 811, 812, 813):
            import shutil
            shutil.rmtree(self.r.base / ('proc/%d' % pid))
        self.passage(time.time() + 600)
        s.ingerer(self.desk)
        self.assertNotIn('Collabora intégré à Nextcloud reconnu', self.page()['body'])

    def test_C_rapport_altere_aucune_injection_dans_la_ligne(self):
        rapport = self.passage(time.time())
        rapport['reconnus'] = [{'id': '<script>x</script>', 'processus': 3, 'titre': '<b>faux</b>'},
                               {'id': 'collabora', 'processus': 'beaucoup', 'titre': '<img src=x>'}]
        nom = sorted(self.dossier.glob('rapport-*.json'))[-1]
        nom.write_text(json.dumps(rapport, ensure_ascii=False), encoding='utf-8')
        s.ingerer(self.desk)
        html = sw.page_html(self.desk, '/p')
        self.assertNotIn('<b>faux</b>', html)
        self.assertNotIn('<img src=x>', html)
        self.assertNotIn('<script>x', html)


if __name__ == '__main__':
    unittest.main()
