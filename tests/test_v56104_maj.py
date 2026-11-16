"""5.6.104 : le service de mise à jour (root) lance une copie du script hors du dossier du code et refuse un code modifiable par
d'autres comptes que root. Système de fichiers simulé (droits fournis par un faux ``lstat``) : les essais tournent aussi sous Windows."""
import importlib.util
import json
import os
import pathlib
import shutil
import stat
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def charger():
    spec = importlib.util.spec_from_file_location('miseajour_56104', ROOT / 'miseajour.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def faux_lstat(droits):
    """droits : {chemin: (type, mode, uid)} ; type 'd', 'f' ou 'l'."""
    types_ = {'d': stat.S_IFDIR, 'f': stat.S_IFREG, 'l': stat.S_IFLNK}

    def lstat(chemin):
        chemin = str(chemin).replace('\\', '/')
        if chemin not in droits:
            raise FileNotFoundError(chemin)
        t, mode, uid = droits[chemin]
        return types.SimpleNamespace(st_mode=types_[t] | mode, st_uid=uid)
    return lstat


class Droits(unittest.TestCase):
    def setUp(self):
        self.m = charger()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.base = self.tmp / 'opt'
        (self.base / 'releases' / '9.9.99' / 'agent').mkdir(parents=True)
        (self.base / 'releases' / '9.9.99' / 'miseajour.py').write_text('#\n')
        self.version = (self.base / 'releases' / '9.9.99').resolve()
        self.lanceur = self.tmp / 'lib' / 'miseajour.py'

    def droits(self, **ecarts):
        b = str(self.base).replace('\\', '/')
        v = str(self.version).replace('\\', '/')
        d = {b: ('d', 0o755, 0), b + '/releases': ('d', 0o755, 0), b + '/current': ('l', 0o777, 0),
             v: ('d', 0o755, 0), v + '/agent': ('d', 0o755, 0), v + '/miseajour.py': ('f', 0o644, 0),
             str(self.lanceur.parent).replace('\\', '/'): ('d', 0o755, 0), str(self.lanceur).replace('\\', '/'): ('f', 0o755, 0)}
        for cle, valeur in ecarts.items():
            d[{'version': v, 'script': v + '/miseajour.py', 'lanceur': str(self.lanceur).replace('\\', '/')}[cle]] = valeur
        return faux_lstat(d)

    def appel(self, **ecarts):
        original = pathlib.Path.resolve
        version = self.version

        def resolve(p, *a, **k):
            return version if p.name == 'current' else original(p, *a, **k)
        pathlib.Path.resolve = resolve
        try:
            return self.m.droits_du_code(self.base, self.lanceur, lstat=self.droits(**ecarts))
        finally:
            pathlib.Path.resolve = original

    def test_M01_code_sain_et_lien_rwxrwxrwx_aucun_probleme(self):
        self.assertEqual(self.appel(), [])

    def test_M02_script_du_code_modifiable_par_www_data_refuse(self):
        problemes = self.appel(script=('f', 0o664, 33))
        self.assertTrue(problemes and 'miseajour.py' in problemes[0], problemes)
        self.assertIn('propriétaire autre que root', problemes[0])

    def test_M03_version_modifiable_par_le_groupe_refusee(self):
        self.assertTrue(self.appel(version=('d', 0o775, 0)))

    def test_M04_copie_lancee_par_root_modifiable_refusee(self):
        self.assertTrue(self.appel(lanceur=('f', 0o777, 0)))


class Lanceur(unittest.TestCase):
    def test_M05_unite_lance_la_copie_hors_du_dossier_du_code(self):
        unite = (ROOT / 'deploy' / 'axiorhub-mail-agent-maj.service').read_text(encoding='utf-8')
        self.assertIn('ExecStart=/usr/bin/python3 -B /usr/local/lib/axiorhub-mail-agent/miseajour.py --executer', unite)
        self.assertNotIn('/opt/axiorhub-mail-agent/current/miseajour.py', unite)
        self.assertEqual(charger().LANCEUR, pathlib.Path('/usr/local/lib/axiorhub-mail-agent/miseajour.py'))

    def test_M06_installer_lanceur_copie_le_script_de_la_version(self):
        m = charger()
        with tempfile.TemporaryDirectory() as t:
            release = pathlib.Path(t) / 'rel'
            release.mkdir()
            (release / 'miseajour.py').write_text('# version 9.9.99\n', encoding='utf-8')
            cible = pathlib.Path(t) / 'lib' / 'miseajour.py'
            m.installer_lanceur(release, cible)
            self.assertEqual(cible.read_text(encoding='utf-8'), '# version 9.9.99\n')
            self.assertEqual([p.name for p in cible.parent.iterdir()], ['miseajour.py'])    # aucun fichier temporaire laissé

    def test_M07_activation_pose_la_copie_avant_les_unites(self):
        source = (ROOT / 'miseajour.py').read_text(encoding='utf-8')
        self.assertLess(source.index('installer_lanceur(release)'), source.index("shutil.copyfile(release / 'deploy' / unit, SYSTEMD / unit)"))


class Refus(unittest.TestCase):
    def test_M08_execute_refuse_sans_rien_telecharger_si_le_code_est_modifiable(self):
        m = charger()
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        m.REQUEST_DIR = tmp / 'maj'
        m.REQUEST_DIR.mkdir()
        m.REQUEST = m.REQUEST_DIR / 'demande.json'
        m.RESULT = m.REQUEST_DIR / 'resultat.json'
        m.WORK = tmp / 'work'
        m.BASE = tmp / 'opt'
        m.REQUEST.write_text(json.dumps({'version': '9.9.99', 'par': 'admin'}), encoding='utf-8')
        m.droits_du_code = lambda *a, **k: ['/opt/axiorhub-mail-agent/releases/9.9.99 : propriétaire autre que root (uid 33)']
        m.fetch = lambda *a, **k: self.fail('aucun téléchargement ne doit avoir lieu')
        self.assertEqual(m.execute(), 1)
        res = json.loads(m.RESULT.read_text(encoding='utf-8'))
        self.assertEqual(res['etat'], 'echec')
        self.assertIn('Mise à jour refusée', res['motif'])
        self.assertTrue(any('sudo chown -R root:root /opt/axiorhub-mail-agent' in e for e in res['etapes']))

    def test_M09_mutation_sans_controle_la_mise_a_jour_irait_plus_loin(self):
        m = charger()
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        m.REQUEST_DIR = tmp / 'maj'
        m.REQUEST_DIR.mkdir()
        m.REQUEST, m.RESULT, m.WORK, m.BASE = m.REQUEST_DIR / 'demande.json', m.REQUEST_DIR / 'resultat.json', tmp / 'work', tmp / 'opt'
        m.REQUEST.write_text(json.dumps({'version': '9.9.99'}), encoding='utf-8')
        m.droits_du_code = lambda *a, **k: []
        appels = []
        m.fetch = lambda *a, **k: appels.append(a) or b''
        m.execute()
        self.assertTrue(appels)                       # le contrôle des droits est bien ce qui arrête M08


if __name__ == '__main__':
    unittest.main()
