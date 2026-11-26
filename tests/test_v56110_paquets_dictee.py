"""5.6.110 (lot D, partie D2) : moteur de dictée whisper.cpp dans les paquets du poste.

Copie réduite (``scripts/preparer-dictee.py``) à partir de fausses archives minuscules fabriquées ici : empreinte refusée,
sous-ensemble exact, manifeste, liens symboliques résolus en copies, chemins hostiles refusés, sortie reproductible. Inclusion dans
l'application Windows (``inclure_dictee`` : complète, altérée, incomplète, absente). Paquet Debian du poste avec et sans ``--dictee``
(dépendance libgomp1, droits, contrôle, taille installée) ; sans l'option, paquet identique à l'octet près à celui d'avant le lot
(empreintes figées des archives tar internes) ; paquet serveur intact. Aucun réseau, aucun vrai binaire, aucune lecture de Git."""
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def _charger(nom, fichier):
    spec = importlib.util.spec_from_file_location(nom, ROOT / 'scripts' / fichier)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


PREP = _charger('preparer_dictee_56110', 'preparer-dictee.py')
BUILDW = _charger('build_windows_56110', 'build-windows.py')
DEB = _charger('build_deb_56110', 'build-deb.py')

LICENCE_FICTIVE = b'MIT License\n\nCopyright (c) 2023-2026 The ggml authors\n\nTexte fictif de licence pour les essais.\n'
VERSIONNES = {'libwhisper.so.1': 'libwhisper.so.1.9.6', 'libggml.so.1': 'libggml.so.1.0.0', 'libggml-base.so.1': 'libggml-base.so.1.0.0'}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _contenu(nom):
    return ('binaire fictif : %s\n' % nom).encode('utf-8')


# ------------------------------------------------------------------------------------------------ fausses archives
def zip_fictif(chemin, membres=None, liens=()):
    """Archive Windows minuscule : ``Release/`` avec les binaires retenus et quelques fichiers inutiles."""
    if membres is None:
        membres = {'Release/' + f: _contenu(f) for f in PREP.BINAIRES['windows']}
        membres.update({'Release/bench.exe': b'inutile', 'Release/SDL2.dll': b'inutile', 'Release/whisper-server.exe': b'inutile'})
    with zipfile.ZipFile(chemin, 'w', zipfile.ZIP_DEFLATED) as z:
        for nom, data in membres.items():
            info = zipfile.ZipInfo(nom, date_time=(2026, 1, 1, 0, 0, 0))
            if nom in liens:
                info.external_attr = (0o120777 << 16)
            z.writestr(info, data)
    return chemin


def tar_fictif(chemin, retire=(), ajoute=(), licence=LICENCE_FICTIVE):
    """Archive Linux minuscule, rangée comme l'officielle : fichiers versionnés et liens symboliques SONAME, LICENSE, inutiles.
    ``ajoute`` : membres supplémentaires (nom, type, cible) ; ``retire`` : noms (sans préfixe) à omettre."""
    p = 'whisper-bin-ubuntu-x64/'
    membres = [(p.rstrip('/'), tarfile.DIRTYPE, None, None)]
    for f in PREP.BINAIRES['linux']:
        if f in VERSIONNES:
            membres.append((p + VERSIONNES[f], tarfile.REGTYPE, _contenu(VERSIONNES[f]), None))
            membres.append((p + f, tarfile.SYMTYPE, None, VERSIONNES[f]))
            membres.append((p + f.replace('.1', ''), tarfile.SYMTYPE, None, f))   # libwhisper.so -> libwhisper.so.1
        else:
            membres.append((p + f, tarfile.REGTYPE, _contenu(f), None))
    membres += [(p + 'LICENSE', tarfile.REGTYPE, licence, None), (p + 'whisper-server', tarfile.REGTYPE, b'inutile', None),
                (p + 'libparakeet.so.1.9.6', tarfile.REGTYPE, b'inutile', None)]
    membres = [m for m in membres if m[0][len(p):] not in retire] + list(ajoute)
    with tarfile.open(chemin, 'w:gz') as tar:
        for nom, type_, data, cible in membres:
            info = tarfile.TarInfo(nom)
            info.type, info.mtime, info.mode = type_, 1_700_000_000, 0o755
            if type_ == tarfile.SYMTYPE or type_ == tarfile.LNKTYPE:
                info.linkname = cible
                tar.addfile(info)
            elif type_ == tarfile.DIRTYPE:
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return chemin


def attendus_pour(dossier):
    """ARCHIVES de preparer-dictee.py, avec la taille et l'empreinte des fausses archives (comme le seraient les vraies)."""
    out = {}
    for plateforme, a in PREP.ARCHIVES.items():
        f = Path(dossier) / a['nom']
        out[plateforme] = dict(a, octets=f.stat().st_size, sha256=_sha(f.read_bytes()))
    return out


def archives_fictives(dossier, **options_tar):
    d = Path(dossier) / 'archives'
    d.mkdir(parents=True, exist_ok=True)
    zip_fictif(d / PREP.ARCHIVES['windows']['nom'])
    tar_fictif(d / PREP.ARCHIVES['linux']['nom'], **options_tar)
    return d, attendus_pour(d)


def copie_reduite(dossier):
    """Copie réduite fictive complète (``reduit/windows`` et ``reduit/linux``) produite par le vrai script."""
    archives, attendus = archives_fictives(dossier)
    sortie = Path(dossier) / 'reduit'
    PREP.preparer(archives, sortie, attendus)
    return sortie


def _arbre(sortie):
    return {p.relative_to(sortie).as_posix(): p.read_bytes() for p in sorted(Path(sortie).rglob('*')) if p.is_file()}


# ------------------------------------------------------------------------------------------------ P01 : copie réduite
class Preparation(unittest.TestCase):
    def test_D2_empreinte_refusee_rien_ecrit(self):
        with tempfile.TemporaryDirectory() as d:
            archives, attendus = archives_fictives(d)
            sortie = Path(d) / 'reduit'
            with self.assertRaisesRegex(SystemExit, 'empreinte inattendue'):
                PREP.preparer(archives, sortie)                       # empreintes officielles : les fausses archives sont refusées
            for plateforme, champ, valeur in (('linux', 'sha256', '0' * 64), ('windows', 'octets', 1)):
                faux = {k: dict(v) for k, v in attendus.items()}
                faux[plateforme][champ] = valeur
                with self.subTest(plateforme=plateforme), self.assertRaisesRegex(SystemExit, 'empreinte inattendue'):
                    PREP.preparer(archives, sortie, faux)
            (archives / PREP.ARCHIVES['windows']['nom']).unlink()
            with self.assertRaisesRegex(SystemExit, 'archive absente'):
                PREP.preparer(archives, sortie, attendus)
            self.assertFalse(sortie.exists())
            # empreintes officielles inscrites dans le script (feuille du lot)
            self.assertEqual(PREP.ARCHIVES['windows']['sha256'], 'ecca07b277d943e9b9f4712856a144b857271a6b333104e511e1988e80db582b')
            self.assertEqual(PREP.ARCHIVES['linux']['sha256'], 'a6199027fe5b4239c84171d2bca4c0ff44ec86a515494d5a39b237b212d429bd')
            self.assertEqual((PREP.ARCHIVES['windows']['octets'], PREP.ARCHIVES['linux']['octets']), (9168428, 10413708))

    def test_D2_sous_ensemble_exact_et_liens_resolus(self):
        with tempfile.TemporaryDirectory() as d:
            sortie = copie_reduite(d)
            arbre = _arbre(sortie)
            textes = set(PREP.TEXTES) | {PREP.MANIFESTE}
            for plateforme, nombre in (('windows', 13), ('linux', 18)):
                noms = {k.split('/', 1)[1] for k in arbre if k.startswith(plateforme + '/')}
                self.assertEqual(len(PREP.BINAIRES[plateforme]), nombre)
                self.assertEqual(noms, set(PREP.BINAIRES[plateforme]) | textes)
            self.assertEqual({k.split('/', 1)[0] for k in arbre}, {'windows', 'linux'})
            for f in PREP.BINAIRES['windows']:
                self.assertEqual(arbre['windows/' + f], _contenu(f))
            for soname, versionne in VERSIONNES.items():        # copie du fichier versionné, sous le nom SONAME, sans lien
                self.assertEqual(arbre['linux/' + soname], _contenu(versionne))
                self.assertFalse((sortie / 'linux' / soname).is_symlink())
                self.assertFalse((sortie / 'linux' / versionne).exists())
                self.assertFalse((sortie / 'linux' / soname.replace('.1', '')).exists())
            self.assertFalse(any(p.is_symlink() for p in sortie.rglob('*')))
            self.assertEqual(arbre['linux/LICENSE-whisper.cpp.txt'], LICENCE_FICTIVE)
            self.assertEqual(arbre['windows/LICENSE-whisper.cpp.txt'], LICENCE_FICTIVE)   # l'archive Windows n'a pas de LICENSE

    def test_D2_manifeste_et_notice(self):
        with tempfile.TemporaryDirectory() as d:
            sortie = copie_reduite(d)
            attendus = attendus_pour(Path(d) / 'archives')
            for plateforme in ('windows', 'linux'):
                dossier = sortie / plateforme
                m = json.loads((dossier / PREP.MANIFESTE).read_text(encoding='utf-8'))
                self.assertEqual((m['composant'], m['version'], m['commit'], m['licence'], m['plateforme']),
                                 ('whisper.cpp', 'b5907', '5eaee0cc9024eb9e9a99469199920c8b7f5cb9ec', 'MIT', plateforme))
                self.assertEqual(m['executable'], 'whisper-cli.exe' if plateforme == 'windows' else 'whisper-cli')
                self.assertEqual(m['archive'], {k: attendus[plateforme][k] for k in ('nom', 'octets', 'sha256')})
                self.assertEqual(set(m['fichiers']), set(PREP.BINAIRES[plateforme]) | set(PREP.TEXTES))
                for nom, v in m['fichiers'].items():
                    data = (dossier / nom).read_bytes()
                    self.assertEqual((v['octets'], v['sha256']), (len(data), _sha(data)), nom)
                self.assertEqual(m['octets'], sum(v['octets'] for v in m['fichiers'].values()))
                notice = (dossier / PREP.NOTICE).read_text(encoding='utf-8')
                for attendu in ('whisper.cpp', 'b5907', '5eaee0cc9024eb9e9a99469199920c8b7f5cb9ec', 'licence MIT',
                                'https://github.com/ggml-org/whisper.cpp', 'Copyright (c) 2023-2026 The ggml authors', 'ne sont pas inclus'):
                    self.assertIn(attendu, notice)
                if plateforme == 'windows':
                    self.assertIn('Microsoft Visual C++', notice)
                    self.assertIn('PAS redistribué', notice)
                    self.assertIn('https://aka.ms/vs/17/release/vc_redist.x64.exe', notice)
                else:
                    self.assertIn('libgomp1', notice)

    def test_D2_sortie_reproductible(self):
        with tempfile.TemporaryDirectory() as d:
            sortie = copie_reduite(d)
            premier = _arbre(sortie)
            (sortie / 'windows' / 'reste-ancien.dll').write_bytes(b'ancien')     # une préparation précédente ne laisse rien
            PREP.preparer(Path(d) / 'archives', sortie, attendus_pour(Path(d) / 'archives'))
            self.assertEqual(_arbre(sortie), premier)

    def test_D2_chemins_hostiles_et_membres_refuses(self):
        p = 'whisper-bin-ubuntu-x64/'
        cas_tar = {
            'chemin sortant': dict(ajoute=[('../dehors', tarfile.REGTYPE, b'x', None)]),
            'chemin absolu': dict(ajoute=[('/etc/dehors', tarfile.REGTYPE, b'x', None)]),
            'remontée dans le chemin': dict(ajoute=[(p + '../../dehors', tarfile.REGTYPE, b'x', None)]),
            'lien sortant': dict(retire=('libggml.so.1',), ajoute=[(p + 'libggml.so.1', tarfile.SYMTYPE, None, '../../etc/passwd')]),
            'lien absolu': dict(retire=('libggml.so.1',), ajoute=[(p + 'libggml.so.1', tarfile.SYMTYPE, None, '/etc/passwd')]),
            'lien en boucle': dict(retire=('libggml.so.1',), ajoute=[(p + 'libggml.so.1', tarfile.SYMTYPE, None, 'libggml.so.1')]),
            'lien dur': dict(retire=('whisper-cli',), ajoute=[(p + 'whisper-cli', tarfile.LNKTYPE, None, p + 'whisper-server')]),
            'fichier absent': dict(retire=('libggml-cpu-zen4.so',)),
            'licence absente': dict(retire=('LICENSE',)),
        }
        for nom, options in cas_tar.items():
            with self.subTest(nom), tempfile.TemporaryDirectory() as d:
                archives, attendus = archives_fictives(d, **options)
                with self.assertRaises(PREP.ArchiveRefusee):
                    PREP.preparer(archives, Path(d) / 'reduit', attendus)
                self.assertFalse((Path(d) / 'reduit').exists())
                self.assertFalse((Path(d) / 'dehors').exists())
        complets = {'Release/' + f: _contenu(f) for f in PREP.BINAIRES['windows']}
        cas_zip = {'chemin sortant': (dict(complets, **{'../dehors.dll': b'x'}), ()),
                   'remontée dans le chemin': (dict(complets, **{'Release/../../dehors.dll': b'x'}), ()),
                   'barre inverse': (dict(complets, **{'Release\\..\\dehors.dll': b'x'}), ()),
                   'lecteur': (dict(complets, **{'C:/dehors.dll': b'x'}), ()),
                   'lien symbolique': (complets, ('Release/whisper.dll',)),
                   'fichier absent': ({k: v for k, v in complets.items() if k != 'Release/ggml-cpu-x64.dll'}, ()),
                   'rangé ailleurs': ({k.replace('Release/', 'Autre/'): v for k, v in complets.items()}, ())}
        for nom, (membres, liens) in cas_zip.items():
            with self.subTest(nom), tempfile.TemporaryDirectory() as d:
                archives, _ = archives_fictives(d)
                zip_fictif(archives / PREP.ARCHIVES['windows']['nom'], membres, liens)
                with self.assertRaises(PREP.ArchiveRefusee):
                    PREP.preparer(archives, Path(d) / 'reduit', attendus_pour(archives))
                self.assertFalse((Path(d) / 'reduit').exists())

    def test_D2_licence_sans_copyright_refusee(self):
        with tempfile.TemporaryDirectory() as d:
            archives, _ = archives_fictives(d)
            tar_fictif(archives / PREP.ARCHIVES['linux']['nom'], licence=b'Texte sans mention\n')
            with self.assertRaisesRegex(SystemExit, 'copyright introuvable'):
                PREP.preparer(archives, Path(d) / 'reduit', attendus_pour(archives))
            self.assertFalse((Path(d) / 'reduit').exists())

    def test_D2_listes_coherentes_entre_scripts(self):
        self.assertEqual(BUILDW.DICTEE_FICHIERS, PREP.BINAIRES['windows'] + PREP.TEXTES)
        self.assertEqual(DEB.DICTEE_BINAIRES, PREP.BINAIRES['linux'])
        self.assertEqual(DEB.DICTEE_TEXTES, PREP.TEXTES + (PREP.MANIFESTE,))
        self.assertEqual((BUILDW.DICTEE_MANIFESTE, DEB.DICTEE_MANIFESTE, BUILDW.DICTEE_NOTICE), (PREP.MANIFESTE, PREP.MANIFESTE, PREP.NOTICE))
        self.assertEqual(DEB.DICTEE_ROOT, './usr/lib/axiorhub-pilote/dictee/')
        self.assertFalse(DEB.DICTEE_ROOT.startswith(DEB.POSTE_ROOT))      # hors de current/ : survit aux mises à jour de l'arbre
        self.assertEqual(len(PREP.CPU_WINDOWS), 9)
        self.assertEqual(len(PREP.CPU_LINUX), 14)


# ------------------------------------------------------------------------------------------------ P02 : application Windows
class ApplicationWindows(unittest.TestCase):
    def _copie(self, d):
        return copie_reduite(d) / 'windows'

    def _manifeste(self, src, modifier):
        m = json.loads((src / PREP.MANIFESTE).read_text(encoding='utf-8'))
        modifier(m)
        (src / PREP.MANIFESTE).write_text(json.dumps(m), encoding='utf-8')

    def test_D2_inclusion_complete(self):
        with tempfile.TemporaryDirectory() as d:
            src, app = self._copie(d), Path(d) / 'app'
            app.mkdir()
            (app / 'dictee').mkdir()
            (app / 'dictee' / 'ancien.dll').write_bytes(b'ancien')              # reste d'une construction précédente : retiré
            err = io.StringIO()
            with patch.object(sys, 'stderr', err):
                out = BUILDW.inclure_dictee(app, str(src))
            self.assertEqual(out['fichiers'], 15)
            self.assertEqual(out['version'], 'b5907')
            self.assertEqual(out['octets'], sum(p.stat().st_size for p in src.iterdir() if p.name != PREP.MANIFESTE))
            self.assertEqual({p.name for p in (app / 'dictee').iterdir()}, set(BUILDW.DICTEE_FICHIERS) | {PREP.MANIFESTE})
            for p in src.iterdir():
                self.assertEqual((app / 'dictee' / p.name).read_bytes(), p.read_bytes())
            self.assertEqual((app / PREP.NOTICE).read_bytes(), (src / PREP.NOTICE).read_bytes())
            self.assertEqual(err.getvalue(), '')

    def test_D2_copie_alteree_ou_incomplete_arrete_la_construction(self):
        def ajoute_fichier(src):
            (src / 'en-trop.dll').write_bytes(b'x')

        def sous_dossier(src):
            (src / 'sous').mkdir()
            (src / 'sous' / 'x.dll').write_bytes(b'x')

        def binaire_modifie(src):
            (src / 'ggml-cpu-haswell.dll').write_bytes(b'modifie')

        def binaire_retire(src):
            (src / 'whisper.dll').unlink()
            self._manifeste(src, lambda m: m['fichiers'].pop('whisper.dll'))

        def notice_retiree(src):
            (src / PREP.NOTICE).unlink()
            self._manifeste(src, lambda m: m['fichiers'].pop(PREP.NOTICE))

        def inattendu_declare(src):
            (src / 'SDL2.dll').write_bytes(b'x')
            self._manifeste(src, lambda m: m['fichiers'].__setitem__('SDL2.dll', {'octets': 1, 'sha256': _sha(b'x')}))

        def chemin_sortant(src):
            self._manifeste(src, lambda m: m['fichiers'].__setitem__('../dehors.dll', {'octets': 1, 'sha256': '0' * 64}))

        def autre_plateforme(src):
            self._manifeste(src, lambda m: m.__setitem__('plateforme', 'linux'))

        def manifeste_illisible(src):
            (src / PREP.MANIFESTE).write_text('{pas du json', encoding='utf-8')

        cas = {'fichier en trop': (ajoute_fichier, 'hors manifeste'), 'sous-dossier': (sous_dossier, 'hors manifeste'),
               'binaire modifié': (binaire_modifie, 'modifié'), 'binaire retiré': (binaire_retire, 'incomplète'),
               'notice retirée': (notice_retiree, 'incomplète'), 'fichier inattendu déclaré': (inattendu_declare, 'inattendus'),
               'chemin sortant': (chemin_sortant, 'chemin refusé'), 'autre plateforme': (autre_plateforme, 'non destinée à Windows'),
               'manifeste illisible': (manifeste_illisible, 'manifeste illisible')}
        for nom, (alterer, message) in cas.items():
            with self.subTest(nom), tempfile.TemporaryDirectory() as d:
                src, app = self._copie(d), Path(d) / 'app'
                app.mkdir()
                alterer(src)
                with self.assertRaisesRegex(SystemExit, message):
                    BUILDW.inclure_dictee(app, str(src))
                self.assertFalse((app / 'dictee').exists())
                self.assertFalse((app / PREP.NOTICE).exists())

    def test_D2_sans_variable_avertissement_et_construction_poursuivie(self):
        with tempfile.TemporaryDirectory() as d:
            app = Path(d) / 'app'
            app.mkdir()
            err = io.StringIO()
            with patch.dict(os.environ, {'AXIORHUB_WIN_DICTEE': ''}), patch.object(sys, 'stderr', err):
                self.assertIsNone(BUILDW.inclure_dictee(app))
            self.assertIn('SANS moteur de dictée', err.getvalue())
            self.assertFalse((app / 'dictee').exists())
            env = {k: v for k, v in os.environ.items() if k != 'AXIORHUB_WIN_DICTEE'}
            with patch.dict(os.environ, env, clear=True), patch.object(sys, 'stderr', io.StringIO()) as err2:
                self.assertIsNone(BUILDW.inclure_dictee(app))
            self.assertIn('application construite SANS moteur de dictée', err2.getvalue())
            src = self._copie(d)
            with patch.dict(os.environ, {'AXIORHUB_WIN_DICTEE': str(src)}):     # la variable désigne la copie
                self.assertEqual(BUILDW.inclure_dictee(app)['fichiers'], 15)
        texte = (ROOT / 'scripts' / 'build-windows.py').read_text(encoding='utf-8')
        self.assertIn('dictee = inclure_dictee(app_dir)', texte)
        self.assertLess(texte.index('dictee = inclure_dictee(app_dir)'), texte.index("archive = out / ('AxiorHub-Pilote-%s-windows.zip' % ver)"))
        self.assertIn("report['dictee'] = dictee or 'non inclus'", texte)


# ------------------------------------------------------------------------------------------------ P03 : paquet Debian
VERSION_FICTIVE = '9.9.9'
MTIME = 1_700_000_000


def archive_livraison(root):
    """Archive de livraison fictive, entièrement déterminée par cet essai (contenu, dates, gzip sans compression ni date)."""
    tree = 'axiorhub-mail-agent-' + VERSION_FICTIVE
    fichiers = {
        'agent/__init__.py': "__version__ = '%s'\n" % VERSION_FICTIVE,
        'agent/static/axiorhub-icon-512.png': 'icône fictive\n',
        'upgrade.py': 'def verify(root):\n    return None\n',
        'poste.py': '# poste fictif\n',
        'deploy/debian/copyright': 'Copyright fictif : cabinet DURAND\n',
        'deploy/debian/postinst': '#!/usr/bin/python3\n# postinst fictif @VERSION@\n',
        'deploy/debian/prerm': '#!/usr/bin/python3\n# prerm fictif\n',
        'deploy/debian/postrm': '#!/usr/bin/python3\n# postrm fictif\n',
        'deploy/poste/axiorhub-pilote': '#!/bin/sh\nexec python3 /usr/lib/axiorhub-pilote/current/poste.py "$@"\n',
        'deploy/poste/axiorhub-pilote.desktop': '[Desktop Entry]\nName=AxiorHub Pilote\nExec=axiorhub-pilote\n\n'
                                                '[Desktop Action demo]\nName=Essayer la démo\nExec=axiorhub-pilote --demo\n',
        'deploy/poste/axiorhub-pilote-liaison.desktop': '[Desktop Entry]\nNoDisplay=true\n',
        'deploy/poste/axiorhub-pilote.service': '[Service]\nExecStart=/usr/bin/axiorhub-pilote --services\n',
    }
    fichiers['MANIFEST.sha256'] = ''.join('%s  %s\n' % (_sha(v.encode('utf-8')), k) for k, v in sorted(fichiers.items()))
    brut = io.BytesIO()
    with tarfile.open(fileobj=brut, mode='w', format=tarfile.GNU_FORMAT) as tar:
        for nom in [tree] + sorted(fichiers):
            info = tarfile.TarInfo(nom if nom == tree else tree + '/' + nom)
            info.mtime, info.uid, info.gid, info.uname, info.gname = MTIME, 0, 0, 'root', 'root'
            if nom == tree:
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                tar.addfile(info)
            else:
                data = fichiers[nom].encode('utf-8')
                info.size, info.mode = len(data), 0o644
                tar.addfile(info, io.BytesIO(data))
    sortie = io.BytesIO()
    with gzip.GzipFile(fileobj=sortie, mode='wb', compresslevel=0, mtime=0) as gz:   # blocs non compressés : mêmes octets partout
        gz.write(brut.getvalue())
    archive = Path(root) / (tree + '.tar.gz')
    archive.write_bytes(sortie.getvalue())
    return archive


def _membres_ar(deb):
    raw, pos, out = Path(deb).read_bytes(), 8, []
    while pos + 60 <= len(raw):
        nom, taille = raw[pos:pos + 16].decode('ascii').rstrip(), int(raw[pos + 48:pos + 58].decode('ascii'))
        out.append((nom, raw[pos + 60:pos + 60 + taille]))
        pos += 60 + taille + taille % 2
    return out


def empreintes_internes(deb):
    """SHA-256 du contenu décompressé de control.tar.gz et data.tar.gz (la compression gzip dépend de la version de zlib)."""
    membres = dict(_membres_ar(deb))
    return _sha(gzip.decompress(membres['control.tar.gz'])), _sha(gzip.decompress(membres['data.tar.gz']))


# Calculées avec scripts/build-deb.py tel qu'il était avant le lot D (5.6.109), sur l'archive fictive ci-dessus. L'ordre des fichiers
# du paquet poste suit le tri des chemins de pathlib, insensible à la casse sous Windows : une empreinte par famille de système.
AVANT_LOT_POSTE = {
    'nt': ('63ebe3b7e83997cd8535eb114622d899cb035c9e54870ca1cdbba96eff55c1dd', '75eccaa369d75edc8995a3807b5d9b935e1f3011097630cd527367857629d2aa'),
    'posix': ('19cf6684ae41bbe421ee557e6289c5f56ae04a2b906dc396cb84da29d87e2cac', 'b16436324e6a0beae5313f5019c96938e8fd869baff5d056d2993bb3dfd9ca36'),
}
AVANT_LOT_SERVEUR = ('4dc9bc54e8e084baaa55172df2b47bbc944a611903e2bc8b16e1f5b2b5452133', 'af292b686e7edeefa68eb8155d8e5ba87a27df99792ce3f9aee78095191f5ab5')


class PaquetDebian(unittest.TestCase):
    def test_D2_poste_sans_dictee_identique_a_l_octet_pres(self):
        with tempfile.TemporaryDirectory() as d:
            archive = archive_livraison(d)
            premier = DEB.build(Path(d) / 'a.deb', archive, mtime=MTIME, variant='poste')
            second = DEB.build(Path(d) / 'b.deb', archive, mtime=MTIME, variant='poste', dictee=None)
            self.assertEqual(Path(premier['path']).read_bytes(), Path(second['path']).read_bytes())
            self.assertEqual(premier['dictee'], 0)
            self.assertEqual([n for n, _ in _membres_ar(premier['path'])], ['debian-binary', 'control.tar.gz', 'data.tar.gz'])
            self.assertEqual(empreintes_internes(premier['path']), AVANT_LOT_POSTE[os.name])
            info = DEB.read_deb(premier['path'])
            self.assertNotIn('libgomp1', info['fields']['Depends'])
            self.assertFalse([n for n in info['data'] if 'dictee' in n])

    def test_D2_serveur_intact(self):
        with tempfile.TemporaryDirectory() as d:
            archive = archive_livraison(d)
            r = DEB.build(Path(d) / 's.deb', archive, mtime=MTIME)
            self.assertEqual(empreintes_internes(r['path']), AVANT_LOT_SERVEUR)
            info = DEB.read_deb(r['path'])
            self.assertEqual(info['fields']['Depends'], ', '.join(DEB.DEPENDS))
            self.assertFalse([n for n in info['data'] if 'dictee' in n])
            src = copie_reduite(d) / 'linux'
            with self.assertRaisesRegex(RuntimeError, 'paquet poste'):
                DEB.build(Path(d) / 's2.deb', archive, mtime=MTIME, dictee=str(src))
            self.assertFalse((Path(d) / 's2.deb').exists())

    def test_D2_poste_avec_dictee(self):
        with tempfile.TemporaryDirectory() as d:
            archive, src = archive_livraison(d), copie_reduite(d) / 'linux'
            sans = DEB.build(Path(d) / 'sans.deb', archive, mtime=MTIME, variant='poste')
            avec = DEB.build(Path(d) / 'avec.deb', archive, mtime=MTIME, variant='poste', dictee=str(src))
            self.assertEqual(avec['dictee'], 21)
            info_sans, info = DEB.read_deb(sans['path']), DEB.check(avec['path'])
            self.assertEqual(info['fields']['Depends'], info_sans['fields']['Depends'] + ', libgomp1')
            racine = DEB.DICTEE_ROOT
            moteur = {n[len(racine):]: e for n, e in info['data'].items() if n.startswith(racine) and not e['dir']}
            self.assertEqual(set(moteur), {p.name for p in src.iterdir()})
            for nom, e in moteur.items():
                self.assertEqual(e['data'], (src / nom).read_bytes(), nom)
                self.assertEqual(e['mode'], 0o755 if nom == 'whisper-cli' or '.so' in nom else 0o644, nom)
                self.assertEqual((e['uid'], e['gid']), (0, 0))
                self.assertIn(racine[2:] + nom, info['md5sums'])
            self.assertEqual(oct(info['data'][racine.rstrip('/')]['mode']), oct(0o755))
            # le reste du paquet est inchangé
            hors_moteur = {n: e['data'] for n, e in info['data'].items() if not n.startswith(racine.rstrip('/'))}
            self.assertEqual(hors_moteur, {n: e['data'] for n, e in info_sans['data'].items()})
            # taille installée : tous les fichiers de données, moteur compris
            fichiers = [e['size'] for e in info['data'].values() if not e['dir']]
            self.assertEqual(int(info['fields']['Installed-Size']), max(1, sum(fichiers) // 1024 + 1))
            octets_moteur = sum(e['size'] for e in moteur.values())
            self.assertEqual(int(info['fields']['Installed-Size']),
                             max(1, (sum(e['size'] for e in info_sans['data'].values() if not e['dir']) + octets_moteur) // 1024 + 1))

    def test_D2_copie_linux_verifiee(self):
        def manifeste(src, modifier):
            m = json.loads((src / PREP.MANIFESTE).read_text(encoding='utf-8'))
            modifier(m)
            (src / PREP.MANIFESTE).write_text(json.dumps(m), encoding='utf-8')

        cas = {'fichier en trop': (lambda s: (s / 'whisper-server').write_bytes(b'x'), 'hors manifeste'),
               'binaire modifié': (lambda s: (s / 'libggml.so.1').write_bytes(b'modifie'), 'modifié'),
               'binaire retiré': (lambda s: (s / 'libggml-cpu-zen4.so').unlink(), 'absent'),
               'déclaré absent du manifeste': (lambda s: manifeste(s, lambda m: m['fichiers'].pop('whisper-cli')), 'incomplète'),
               'copie Windows': (lambda s: manifeste(s, lambda m: m.__setitem__('plateforme', 'windows')), 'non destinée à Linux'),
               'manifeste absent': (lambda s: (s / PREP.MANIFESTE).unlink(), 'manifeste illisible')}
        for nom, (alterer, message) in cas.items():
            with self.subTest(nom), tempfile.TemporaryDirectory() as d:
                archive, src = archive_livraison(d), copie_reduite(d) / 'linux'
                alterer(src)
                with self.assertRaisesRegex(RuntimeError, message):
                    DEB.build(Path(d) / 'p.deb', archive, mtime=MTIME, variant='poste', dictee=str(src))
                self.assertFalse((Path(d) / 'p.deb').exists())

    def test_D2_controle_du_moteur_dans_le_paquet(self):
        with tempfile.TemporaryDirectory() as d:
            archive, src = archive_livraison(d), copie_reduite(d) / 'linux'
            info = DEB.read_deb(DEB.build(Path(d) / 'avec.deb', archive, mtime=MTIME, variant='poste', dictee=str(src))['path'])
            info_sans = DEB.read_deb(DEB.build(Path(d) / 'sans.deb', archive, mtime=MTIME, variant='poste')['path'])
            DEB.check_dictee(info, True)
            DEB.check_dictee(info_sans, True)
            r = DEB.DICTEE_ROOT

            def copie(base):
                return {'fields': dict(base['fields']), 'data': {k: dict(v) for k, v in base['data'].items()}}

            def sans_fichier(i):
                del i['data'][r + 'libggml-cpu-x64.so']

            def en_trop(i):
                i['data'][r + 'whisper-server'] = dict(i['data'][r + 'whisper-cli'])

            def sous_dossier(i):
                i['data'][r + 'sous'] = {'dir': True, 'mode': 0o755}

            def droits_bibliotheque(i):
                i['data'][r + 'libwhisper.so.1']['mode'] = 0o644

            def droits_texte(i):
                i['data'][r + 'LICENSE-whisper.cpp.txt']['mode'] = 0o755

            def sans_libgomp(i):
                i['fields']['Depends'] = i['fields']['Depends'].replace(', libgomp1', '')

            cas = {'fichier retiré': (sans_fichier, 'incomplet'), 'fichier en trop': (en_trop, 'en trop'),
                   'sous-dossier': (sous_dossier, 'Sous-dossier'), 'bibliothèque non exécutable': (droits_bibliotheque, 'Droits'),
                   'texte exécutable': (droits_texte, 'Droits'), 'sans libgomp1': (sans_libgomp, 'libgomp1')}
            for nom, (alterer, message) in cas.items():
                with self.subTest(nom):
                    i = copie(info)
                    alterer(i)
                    with self.assertRaisesRegex(RuntimeError, message):
                        DEB.check_dictee(i, True)
            with self.assertRaisesRegex(RuntimeError, 'hors du paquet poste'):
                DEB.check_dictee(copie(info), False)
            i = copie(info_sans)
            i['fields']['Depends'] += ', libgomp1'
            with self.assertRaisesRegex(RuntimeError, 'sans moteur'):
                DEB.check_dictee(i, True)

    def test_D2_option_en_ligne_de_commande(self):
        texte = (ROOT / 'scripts' / 'build-deb.py').read_text(encoding='utf-8')
        self.assertIn("parser.add_argument('--dictee'", texte)
        self.assertIn('dictee=args.dictee', texte)


if __name__ == '__main__':
    unittest.main()
