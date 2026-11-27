"""5.6.111 (lot C) : C07 — jeton d'aperçu d'envoi conservé en empreinte (expiration, usage unique, migration de l'ancienne forme) ;
S7 — suppression différée de la désinstallation Windows sans chemin dans la commande ``cmd``.

Aucun envoi réel : messagerie et SMTP simulés (``test_v490``) ; aucun processus lancé hors de l'essai Windows réservé à un dossier temporaire."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_v490 import FakeSMTP, SendBase

from agent import send490, web490
from agent.common import Stop


class JetonEnEmpreinte(SendBase):
    def setUp(self):
        super().setUp()
        self.enable()

    def tous_les_jetons(self):
        return [r[0] for r in self.desk.db.execute('SELECT token FROM send_pending490')]

    def test_jeton_jamais_conserve_en_clair_et_envoi_unique(self):
        p = send490.prepare(self.desk, self.uid, '7')
        self.assertEqual(self.tous_les_jetons(), [send490.empreinte_jeton(p['token'])])
        self.assertNotIn(p['token'], ''.join(self.tous_les_jetons()))
        self.assertEqual(web490.brouillon_du_jeton(self.desk, p['token'])['uid'], str(self.uid))
        self.assertTrue(web490.file_jeton(self.desk, p['token']))
        with self.assertRaisesRegex(Stop, 'apercu_envoi_absent'):   # l'empreinte elle-même n'ouvre rien
            send490.confirm(self.desk, send490.empreinte_jeton(p['token']), now=time.time() + 2)
        send490.confirm(self.desk, p['token'], now=time.time() + 2)
        self.assertEqual(len(FakeSMTP.sent), 1)
        with self.assertRaisesRegex(Stop, 'apercu_envoi_absent'):
            send490.confirm(self.desk, p['token'], now=time.time() + 3)
        self.assertEqual(len(FakeSMTP.sent), 1)

    def test_jeton_expire(self):
        p = send490.prepare(self.desk, self.uid, '7')
        with self.assertRaisesRegex(Stop, 'apercu_envoi_expire'):
            send490.confirm(self.desk, p['token'], now=time.time() + send490.TOKEN_TTL + 5)
        self.assertEqual(FakeSMTP.sent, [])

    def ancienne_forme(self, *lignes):
        """Lignes telles que les écrivait la 5.6.110 : jeton en clair dans la clé primaire. Les aperçus sont tous préparés d'abord (la
        préparation migre l'ancienne forme), puis remis dans l'ancienne forme. ``lignes`` : (jeton en clair, utilisé)."""
        apercus = [send490.prepare(self.desk, self.uid, '7') for _ in lignes]
        for p, (clair, used) in zip(apercus, lignes):
            self.desk.db.execute('UPDATE send_pending490 SET token=?, used=?, created=? WHERE token=?',
                                 (clair, used, time.time() - 2.0, send490.empreinte_jeton(p['token'])))
        self.desk.db.commit()

    def test_migration_de_l_ancienne_forme_sans_casser_ni_doubler(self):
        self.ancienne_forme(('jeton-en-cours-' + 'a' * 33, 0), ('jeton-deja-utilise-' + 'b' * 29, 1))
        self.assertEqual(sum(1 for t in self.tous_les_jetons() if not t.startswith('h1:')), 2)
        self.assertEqual(send490.migrer_jetons(self.desk), 2)
        self.assertEqual(send490.migrer_jetons(self.desk), 0)   # idempotente
        jetons = self.tous_les_jetons()
        self.assertTrue(all(t.startswith('h1:') for t in jetons))
        self.assertFalse(any('jeton-' in t for t in jetons))
        # l'aperçu encore valable reste confirmable avec le jeton que le navigateur détient
        send490.confirm(self.desk, 'jeton-en-cours-' + 'a' * 33, now=time.time())
        self.assertEqual(len(FakeSMTP.sent), 1)
        # un jeton déjà consommé avant la migration ne peut pas faire repartir le message
        with self.assertRaisesRegex(Stop, 'apercu_envoi_absent'):
            send490.confirm(self.desk, 'jeton-deja-utilise-' + 'b' * 29, now=time.time())
        with self.assertRaisesRegex(Stop, 'apercu_envoi_absent'):
            send490.confirm(self.desk, 'jeton-en-cours-' + 'a' * 33, now=time.time())
        self.assertEqual(len(FakeSMTP.sent), 1)

    def test_migration_au_demarrage_du_bureau(self):
        self.ancienne_forme(('jeton-demarrage-' + 'c' * 32, 0))
        from agent.desk import Desk
        d = Desk(self.f.c)
        d._prepare()   # préparation du démarrage (une fois par processus en service) : migration comprise
        self.assertTrue(all(t.startswith('h1:') for t in self.tous_les_jetons()))
        d.db.close()


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from windows import deploiement  # noqa: E402


class SuppressionDiffereeWindows(unittest.TestCase):
    CIBLES = [r'C:\Users\a%TEMP%b\AppData\Local\Programs\AxiorHub Pilote', r'C:\Users\x & y ^z (1)\AppData\Local\AxiorHub Pilote']

    def test_aucun_chemin_dans_la_commande(self):
        commande, env = deploiement.commande_suppression_differee(self.CIBLES)
        for cible in self.CIBLES:
            self.assertNotIn(cible, commande)
        self.assertNotIn('%TEMP%', commande)
        self.assertEqual(env['AXH_SUPPR_0'], self.CIBLES[0])
        self.assertEqual(env['AXH_SUPPR_1'], self.CIBLES[1])
        self.assertTrue(commande.startswith('cmd /d /v:off /s /c "'))
        self.assertIn('rmdir /s /q "%AXH_SUPPR_1%"', commande)

    def test_caracteres_impossibles_et_chemin_relatif_refuses(self):
        for cible in ('C:\\Users\\a"b', 'C:\\Users\\a\nrmdir C:\\', 'AxiorHub', ''):
            with self.subTest(cible=cible), self.assertRaisesRegex(ValueError, 'chemin_de_suppression_refuse'):
                deploiement.commande_suppression_differee([cible])

    def test_desinstallation_lance_la_commande_sans_liste_d_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp) / 'Programs' / 'AxiorHub Pilote'
            exe = racine / 'app-1' / 'AxiorHub.exe'
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b'')
            appels = []
            with patch.object(deploiement, 'stop_running', lambda: None), \
                    patch.object(deploiement.subprocess, 'Popen', lambda *a, **k: appels.append((a, k))), \
                    patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(exe)):
                deploiement.uninstall(root=racine, shortcuts=False, registry=False)
            self.assertEqual(len(appels), 1)
            (args, kwargs) = appels[0]
            self.assertIsInstance(args[0], str)
            self.assertNotIn(str(racine), args[0])
            self.assertEqual(kwargs['env']['AXH_SUPPR_0'], str(racine))
            self.assertTrue(racine.exists())   # rien n'a été supprimé par l'essai

    @unittest.skipUnless(os.name == 'nt', 'commande cmd réelle : Windows seulement')
    def test_commande_reelle_supprime_les_seuls_dossiers_vises(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            cibles = [base / 'a%TEMP%b', base / 'x & y ^z (1)']
            garde = base / 'garde'
            for d in cibles + [garde]:
                d.mkdir()
                (d / 'fichier.txt').write_text('fictif', encoding='utf-8')
            commande, env = deploiement.commande_suppression_differee(cibles)
            commande = commande.replace('ping -n 4 127.0.0.1 >nul & ', '')
            subprocess.run(commande, env=env, check=True, capture_output=True, timeout=60)
            self.assertFalse(any(d.exists() for d in cibles))
            self.assertTrue((garde / 'fichier.txt').exists())


if __name__ == '__main__':
    unittest.main()
