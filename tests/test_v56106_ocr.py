"""5.6.106 (lot W) : Tesseract et Poppler embarqués dans l'application Windows, langues espagnol et allemand en option.

Recherche des outils (Windows simulé et Linux), fermeture des DLL sur de faux en-têtes PE, copie réduite et inclusion dans la
construction, téléchargement d'une langue par un faux transport (bonne empreinte gardée, mauvaise rejetée, aucun fichier partiel),
droits, idempotence et version. Les essais réels (copie réduite des vrais binaires) ne tournent que si ``AXIORHUB_WIN_OCR`` désigne
cette copie ; sinon ils sont sautés (la recette GitHub ne dépend d'aucun binaire). Aucune connexion réseau."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent import documents, outils_ocr56106 as O, portable
from agent.common import Stop

ROOT = Path(__file__).resolve().parents[1]


def _charger(nom, fichier):
    spec = importlib.util.spec_from_file_location(nom, ROOT / 'scripts' / fichier)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


PREP = _charger('preparer_ocr_56106', 'preparer-ocr-windows.py')
BUILD = _charger('build_windows_56106', 'build-windows.py')


# ------------------------------------------------------------------------------------------------ faux en-têtes PE
def faux_pe(imports=(), differes=(), pe32=False, version=None):
    """Exécutable PE minimal : une section (VA 0x1000, fichier 0x400) avec table d'import, table d'import différé et noms."""
    descr, delay = 20 * (len(imports) + 1), 32 * (len(differes) + 1)
    noms, pos = b'', descr + delay
    adresses = []
    for n in list(imports) + list(differes):
        adresses.append(0x1000 + pos + len(noms))
        noms += n.encode() + b'\0'
    data = bytearray(pos) + noms
    for i, _ in enumerate(imports):
        struct.pack_into('<IIIII', data, 20 * i, 0, 0, 0, adresses[i], 0)
    for j, _ in enumerate(differes):
        struct.pack_into('<II', data, descr + 32 * j, 1, adresses[len(imports) + j])
    for k, v in (version or {}).items():          # chaînes de la ressource de version, alignées sur 32 bits comme dans un vrai binaire
        data += bytes((-len(data)) % 4) + (k + '\0').encode('utf-16-le')
        data += bytes((-len(data)) % 4) + (v + '\0').encode('utf-16-le')
    taille_opt = 224 if pe32 else 240
    entete = bytearray(0x400)
    entete[:2] = b'MZ'
    struct.pack_into('<I', entete, 0x3C, 0x40)
    entete[0x40:0x44] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', entete, 0x44, 0x14C if pe32 else 0x8664, 1, 0, 0, 0, taille_opt, 0x22)
    opt = 0x58
    struct.pack_into('<H', entete, opt, 0x10B if pe32 else 0x20B)
    rep = opt + (96 if pe32 else 112)
    struct.pack_into('<I', entete, rep - 4, 16)
    if imports:
        struct.pack_into('<II', entete, rep + 8, 0x1000, descr)
    if differes:
        struct.pack_into('<II', entete, rep + 8 * 13, 0x1000 + descr, delay)
    sec = opt + taille_opt
    entete[sec:sec + 8] = b'.idata\0\0'
    struct.pack_into('<IIII', entete, sec + 8, len(data), 0x1000, len(data), 0x400)
    return bytes(entete) + bytes(data)


def ecrire(dossier, nom, contenu):
    p = Path(dossier) / nom
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(contenu)
    return p


def faux_ocr(base):
    """Dossier ``ocr`` simulé : faux exécutables et données de base de Tesseract."""
    for rel in ('tesseract/tesseract.exe', 'poppler/pdftoppm.exe', 'poppler/pdftotext.exe', 'poppler/pdfinfo.exe'):
        ecrire(base, rel, b'MZ')
    for rel in O.BASE:
        ecrire(base, 'tesseract/tessdata/' + rel, ('donnees ' + rel).encode())
    return Path(base)


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='axh-lotw-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ocr = faux_ocr(self.tmp / 'ocr')
        self.etat = self.tmp / 'etat'
        self.etat.mkdir()
        for p in (patch.object(portable, 'WINDOWS', True), patch.dict(os.environ, {'AXIORHUB_WIN_OCR': str(self.ocr)}),
                  patch.object(portable, '_which', lambda nom: None)):
            p.start()
            self.addCleanup(p.stop)
        O.configurer(self.etat)
        self.addCleanup(O.configurer, None)


# ================================================================================================ recherche des outils
class Recherche(_Env):
    def test_W3_windows_embarque_avant_le_path(self):
        with patch.object(portable, '_which', lambda nom: r'C:\Autre\%s.exe' % nom):
            self.assertEqual(O.chemin('tesseract'), str(self.ocr / 'tesseract' / 'tesseract.exe'))
            self.assertEqual(O.chemin('pdftoppm'), str(self.ocr / 'poppler' / 'pdftoppm.exe'))
            self.assertEqual(portable.which('pdfinfo'), str(self.ocr / 'poppler' / 'pdfinfo.exe'))   # aperçu des Word (cabinet_docs33)
            self.assertEqual(documents._tool('pdftotext'), str(self.ocr / 'poppler' / 'pdftotext.exe'))
            from agent import vision56106
            self.assertEqual(vision56106._outil('tesseract'), str(self.ocr / 'tesseract' / 'tesseract.exe'))
            self.assertFalse(documents._poppler_missing())
            self.assertEqual(O.origine('tesseract'), 'intégré')
            self.assertEqual(O.chemin('libreoffice'), r'C:\Autre\libreoffice.exe')   # hors OCR : inchangé
            with patch.dict(os.environ, {'AXIORHUB_WIN_OCR': ''}):
                self.assertEqual(O.chemin('tesseract'), r'C:\Autre\tesseract.exe')   # ni empaqueté ni variable : PATH
                self.assertEqual(O.origine('tesseract'), 'poste')

    def test_W3_application_empaquetee_dossier_ocr_a_cote_de_l_executable(self):
        app = self.tmp / 'app'
        shutil.copytree(self.ocr, app / 'ocr')
        ecrire(app, 'AxiorHub Pilote.exe', b'MZ')
        with patch.dict(os.environ, {'AXIORHUB_WIN_OCR': ''}), patch.object(sys, 'frozen', True, create=True), \
                patch.object(sys, 'executable', str(app / 'AxiorHub Pilote.exe')):
            self.assertEqual(O.chemin('tesseract'), str((app / 'ocr' / 'tesseract' / 'tesseract.exe').resolve()))
            self.assertEqual(portable.tool_env()['TESSDATA_PREFIX'], str((app / 'ocr' / 'tesseract' / 'tessdata').resolve()))

    def test_W3_sans_ocr_integre_rien_ne_change(self):
        with patch.dict(os.environ, {'AXIORHUB_WIN_OCR': ''}):
            self.assertIsNone(O.chemin('tesseract'))
            self.assertNotIn('TESSDATA_PREFIX', portable.tool_env())
            self.assertTrue(documents._poppler_missing())     # repli pypdf existant
            self.assertEqual(O.origine('pdftoppm'), 'absent')

    def test_W3_linux_chemins_fixes_jamais_la_variable(self):
        with patch.object(portable, 'WINDOWS', False):
            self.assertIsNone(O.dossier_embarque())
            self.assertIsNone(O.embarque('tesseract'))
            self.assertEqual(documents._tool('pdfinfo'), '/usr/bin/pdfinfo')
            self.assertFalse(documents._poppler_missing())
            with patch('agent.outils_ocr56106.Path.is_file', lambda p: str(p).replace('\\', '/') == '/usr/bin/tesseract'):
                self.assertEqual(O.chemin('tesseract'), '/usr/bin/tesseract')
                self.assertIsNone(O.chemin('pdftoppm'))
            self.assertNotIn('TESSDATA_PREFIX', portable.tool_env())
            self.assertEqual(set(portable.tool_env()), {'PATH', 'LANG'})
            self.assertIsNone(O.tessdata())

    def test_W3_tessdata_prefix_et_langues(self):
        self.assertEqual(portable.tool_env(OMP_THREAD_LIMIT=1)['TESSDATA_PREFIX'], str(self.ocr / 'tesseract' / 'tessdata'))
        self.assertEqual(O.installees(), ['eng', 'fra', 'osd'])
        self.assertEqual(O.langues(), 'fra+eng')
        cab = O.tessdata_cabinet()
        for rel in O.BASE:
            ecrire(cab, rel, b'x')
        self.assertEqual(O.tessdata(), self.ocr / 'tesseract' / 'tessdata')   # sans langue ajoutée : dossier de l'application
        ecrire(cab, 'spa.traineddata', b'espagnol')
        self.assertEqual(O.tessdata(), cab)
        self.assertEqual(portable.tool_env()['TESSDATA_PREFIX'], str(cab))
        with patch.object(O, 'reglages_de', lambda s: {'langues': 'fra+spa+deu', 'version': 3}):
            O._CACHE.clear()
            self.assertEqual(O.langues(), 'fra+spa')               # deu non installé : ignoré
        (cab / 'fra.traineddata').unlink()                        # dossier du cabinet incomplet : jamais utilisé
        self.assertEqual(O.tessdata(), self.ocr / 'tesseract' / 'tessdata')

    def test_W3_documents_passe_les_langues_choisies(self):
        vus = []

        def faux(args, timeout=90):
            vus.append(args)
            return 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n5\t1\t1\t1\t1\t1\t0\t0\t9\t9\t96\tDURAND\n'
        with patch.object(documents, 'command', faux), patch.object(O, 'langues', lambda: 'fra+deu'):
            self.assertIn('DURAND', documents.ocr('page.png'))
        self.assertEqual(vus[0][vus[0].index('-l') + 1], 'fra+deu')
        self.assertEqual(vus[0][0], str(self.ocr / 'tesseract' / 'tesseract.exe'))

    def test_W3_ocr_des_pieces_suit_le_choix_du_cabinet(self):
        from agent import pieces520
        appels = []

        def run(args, timeout=120):
            appels.append(args)
            return b'List of available languages (3):\neng\nfra\nspa\n'
        exe = str(self.ocr / 'tesseract' / 'tesseract.exe')
        with patch.object(pieces520, 'TESSERACT', exe), patch.object(pieces520, 'PDFTOPPM', str(self.ocr / 'poppler' / 'pdftoppm.exe')), \
                patch.object(pieces520, '_run', run):
            pieces520._langs.clear()
            with patch.object(O, 'langues', lambda: 'fra+spa'):
                self.assertEqual(pieces520.ocr_available(), 'fra+spa')
            with patch.object(O, 'langues', lambda: 'deu'):
                self.assertEqual(pieces520.ocr_available(), 'fra+eng')       # langue absente : repli
            self.assertEqual(len(appels), 1)                                  # liste des langues gardée
            O._oublier()                                                      # langue ajoutée ou retirée : relue
            self.assertNotIn('langs', pieces520._langs)


# ================================================================================================ fermeture des DLL
class FermeturePE(unittest.TestCase):
    def test_W1_imports_et_imports_differes(self):
        raw = faux_pe(['libb.dll', 'KERNEL32.dll'], ['libc.dll'])
        self.assertEqual(PREP.imports_pe(raw), ['libb.dll', 'KERNEL32.dll', 'libc.dll'])
        self.assertEqual(PREP.imports_pe(faux_pe(['x.dll'], pe32=True)), ['x.dll'])
        for mauvais in (b'', b'ZM' + bytes(100), faux_pe(['a.dll'])[:0x45]):
            with self.assertRaises(PREP.PEInvalide):
                PREP.imports_pe(mauvais)

    def test_W1_fermeture_transitive_sans_dll_inutile(self):
        with tempfile.TemporaryDirectory() as d:
            ecrire(d, 'outil.exe', faux_pe(['libA.dll', 'KERNEL32.dll', 'api-ms-win-crt-heap-l1-1-0.dll']))
            ecrire(d, 'libA.dll', faux_pe(['libB.dll', 'msvcrt.dll'], ['libC.dll']))
            ecrire(d, 'libB.dll', faux_pe(['liba.DLL']))            # cycle, casse différente
            ecrire(d, 'libC.dll', faux_pe(['ucrtbase.dll']))
            ecrire(d, 'inutile.dll', faux_pe(['libA.dll']))
            ecrire(d, 'api-ms-win-crt-heap-l1-1-0.dll', faux_pe())   # présent localement mais fourni par Windows : jamais copié
            garder, sys_ = PREP.fermeture(d, ['outil.exe'])
        self.assertEqual(garder, ['libA.dll', 'libB.dll', 'libC.dll', 'outil.exe'])
        self.assertEqual(sys_, ['api-ms-win-crt-heap-l1-1-0.dll', 'kernel32.dll', 'msvcrt.dll', 'ucrtbase.dll'])

    def test_W1_version_info(self):
        info = PREP.version_info(faux_pe(['a.dll'], version={'LegalCopyright': 'Copyright ZORVAL', 'ProductName': 'Essai'}))
        self.assertEqual(info.get('LegalCopyright'), 'Copyright ZORVAL')

    def test_W1_copie_reduite_manifeste_et_licences(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            tess = t / 'extrait' / 'tesseract'
            ecrire(tess, 'tesseract.exe', faux_pe(['libtesseract-5.dll', 'KERNEL32.dll']))
            ecrire(tess, 'libtesseract-5.dll', faux_pe(['libleptonica-6.dll']))
            ecrire(tess, 'libleptonica-6.dll', faux_pe(['zlib1.dll']))
            ecrire(tess, 'zlib1.dll', faux_pe())
            ecrire(tess, 'text2image.exe', faux_pe(['libpango-1.0-0.dll']))     # outil d'entraînement : jamais repris
            ecrire(tess, 'libpango-1.0-0.dll', faux_pe())
            ecrire(tess, 'doc/LICENSE', b'Apache License 2.0')
            for f in ('osd.traineddata', 'pdf.ttf', 'eng.traineddata') + tuple('configs/' + c for c in PREP.CONFIGS + ('box.train',)):
                ecrire(tess / 'tessdata', f, b'installateur ' + f.encode())
            pop = t / 'extrait' / 'poppler' / 'poppler-26.09.0'
            for exe in PREP.POPPLER_EXE + ('pdfimages.exe',):
                ecrire(pop / 'Library' / 'bin', exe, faux_pe(['poppler.dll', 'VCRUNTIME140.dll']))
            ecrire(pop / 'Library' / 'bin', 'poppler.dll', faux_pe(['freetype.dll']))
            ecrire(pop / 'Library' / 'bin', 'freetype.dll', faux_pe())
            ecrire(pop / 'Library' / 'bin', 'vcruntime140.dll', faux_pe())
            ecrire(pop / 'Library' / 'bin', 'cairo.dll', faux_pe())
            ecrire(pop / 'share' / 'poppler', 'COPYING.gpl2', b'GNU GENERAL PUBLIC LICENSE Version 2')
            orig = {}
            for f in ('fra.traineddata', 'eng.traineddata'):
                orig[f] = hashlib.sha256(ecrire(t / 'telechargements', f, b'tessdata ' + f.encode()).read_bytes()).hexdigest()
            m = PREP.preparer(t, originaux=orig)
            r = t / 'reduit'
            noms = {p.relative_to(r).as_posix() for p in r.rglob('*') if p.is_file()}
            self.assertEqual(m['composants']['tesseract'], ['libleptonica-6.dll', 'libtesseract-5.dll', 'tesseract.exe', 'zlib1.dll'])
            self.assertEqual(m['composants']['poppler'], ['freetype.dll', 'pdfinfo.exe', 'pdftoppm.exe', 'pdftotext.exe', 'poppler.dll', 'vcruntime140.dll'])
            for absent in ('tesseract/text2image.exe', 'tesseract/libpango-1.0-0.dll', 'poppler/pdfimages.exe', 'poppler/cairo.dll',
                           'tesseract/tessdata/configs/box.train'):
                self.assertNotIn(absent, noms)
            self.assertEqual((r / 'tesseract' / 'tessdata' / 'eng.traineddata').read_bytes(), b'tessdata eng.traineddata')   # tessdata, pas l'installateur
            self.assertEqual(m['fichiers']['tesseract/tesseract.exe']['sha256'], hashlib.sha256((tess / 'tesseract.exe').read_bytes()).hexdigest())
            self.assertEqual((r / 'tesseract' / 'tesseract.exe').read_bytes(), (tess / 'tesseract.exe').read_bytes())   # binaire non modifié
            self.assertIn('licences/tesseract/LICENSE', noms)
            self.assertEqual(m['licences']['Tesseract']['statut'], 'trouvée')            # AUTHORS facultatif, LICENSE requis
            # aucune licence amont, aucune source : tout est signalé, rien n'est inventé, copie incomplète
            for nom in ('Leptonica', 'zlib', 'Poppler', 'FreeType', 'Runtime Microsoft Visual C++'):
                self.assertEqual(m['licences'][nom]['statut'], 'manquante', nom)
            self.assertFalse(m['complet'])
            self.assertIsNone(m['source_poppler'])
            for nom in ('Poppler', 'JBIG-KIT', 'libiconv'):
                self.assertTrue(any('source de %s' % nom in x for x in m['manques']), nom)
            avis = (r / 'THIRD_PARTY_NOTICES-OCR.txt').read_text(encoding='utf-8')
            self.assertIn('https://poppler.freedesktop.org/poppler-26.09.0.tar.xz', avis)
            self.assertIn('oschwartz10612/poppler-windows/releases/tag/v26.09.0-0', avis)
            self.assertIn('poppler-26.09.0.tar.xz', avis)
            self.assertNotIn('trois ans', avis)
            self.assertIn('MANQUES', avis)
            # licences amont (fichier + SOURCES.txt) et source vérifiée : copie complète
            for dossier in ('leptonica', 'zlib', 'poppler', 'freetype', 'microsoft-vcruntime'):
                ecrire(t / 'licences-amont' / dossier, 'LICENSE', ('licence ' + dossier).encode())
                ecrire(t / 'licences-amont' / dossier, 'SOURCES.txt', b'LICENSE adresse officielle')
            (t / 'licences-amont' / 'zlib' / 'SOURCES.txt').unlink()               # sans SOURCES.txt : non retenue
            essais = {}
            for nom, s in PREP.SOURCES.items():
                f = ecrire(t / 'telechargements', s['nom'], ('source fictive ' + nom).encode())
                essais[nom] = dict(s, sha256=hashlib.sha256(f.read_bytes()).hexdigest())
            with patch.dict(PREP.SOURCES, essais):
                m = PREP.preparer(t, originaux=orig)
                self.assertEqual(m['licences']['zlib']['statut'], 'manquante')
                self.assertEqual(m['manques'], ['zlib (tesseract/zlib1.dll)'])
                ecrire(t / 'licences-amont' / 'zlib', 'SOURCES.txt', b'LICENSE adresse officielle')
                m = PREP.preparer(t, originaux=orig)
                self.assertTrue(m['complet'], m['manques'])
                for nom in ('Poppler', 'JBIG-KIT', 'libiconv'):
                    self.assertEqual(m['sources'][nom]['sha256'], essais[nom]['sha256'])
                    self.assertTrue(m['sources'][nom]['url'].startswith('https://'))
                avis = (r / 'THIRD_PARTY_NOTICES-OCR.txt').read_text(encoding='utf-8')
                self.assertIn('« jbigkit-2.1.tar.gz »', avis)
                self.assertIn('« libiconv-1.17.tar.gz »', avis)
                self.assertIn('version amont correspondante', avis)
                (t / 'telechargements' / 'libiconv-1.17.tar.gz').write_bytes(b'altere')          # source différente : copie incomplète
                m = PREP.preparer(t, originaux=orig)
                self.assertFalse(m['complet'])
                self.assertIsNone(m['sources']['libiconv'])
                self.assertEqual(m['manques'], ['source de libiconv absente ou différente : libiconv-1.17.tar.gz'])
                (t / 'telechargements' / 'libiconv-1.17.tar.gz').write_bytes(b'source fictive libiconv')
                # version embarquée lue dans le binaire différente de la source : non correspondante
                ecrire(tess, 'libjbig-0.dll', faux_pe() + b'JBIG-KIT 2.2 -- (c) Kuhn')
                ecrire(tess, 'libtesseract-5.dll', faux_pe(['libleptonica-6.dll', 'libjbig-0.dll']))
                m = PREP.preparer(t, originaux=orig)
                self.assertIn('source de JBIG-KIT non correspondante : version embarquée 2.2, source 2.1', m['manques'])
                ecrire(tess, 'libjbig-0.dll', faux_pe() + b'JBIG-KIT 2.1 -- (c) Kuhn')
                ecrire(t / 'licences-amont' / 'jbigkit', 'gpl-2.0.txt', b'GPL v2')
                ecrire(t / 'licences-amont' / 'jbigkit', 'SOURCES.txt', b'adresse officielle')
                m = PREP.preparer(t, originaux=orig)
                self.assertTrue(m['complet'], m['manques'])
                self.assertEqual(m['sources']['JBIG-KIT']['version_lue'], '2.1')
            self.assertIn('licences/leptonica/SOURCES.txt', m['fichiers'])
            for s in PREP.SOURCES.values():                                      # sources jointes à la publication, pas copiées
                self.assertNotIn(s['nom'], {Path(f).name for f in m['fichiers']})
            ecrire(t / 'telechargements', 'fra.traineddata', b'autre')               # original altéré : arrêt
            with self.assertRaises(SystemExit):
                PREP.preparer(t, originaux=orig)


# ================================================================================================ construction Windows
def sources_essai(**remplace):
    s = {n: {'nom': f, 'sha256': '8' * 64, 'url': 'https://example.test/' + f} for n, f in (
        ('Poppler', 'poppler-26.09.0.tar.xz'), ('JBIG-KIT', 'jbigkit-2.1.tar.gz'), ('libiconv', 'libiconv-1.17.tar.gz'))}
    s.update(remplace)
    return s


class Construction(unittest.TestCase):
    def _reduit(self, d, **remplace):
        r = Path(d) / 'reduit'
        for rel in ('tesseract/tesseract.exe', 'poppler/pdftoppm.exe', 'poppler/pdftotext.exe', 'poppler/pdfinfo.exe', 'licences/zlib/LICENSE'):
            ecrire(r, rel, rel.encode())
        ecrire(r, 'THIRD_PARTY_NOTICES-OCR.txt', b'SOURCE DE POPPLER')
        fichiers = {p.relative_to(r).as_posix(): {'octets': p.stat().st_size, 'sha256': BUILD.sha256(p)} for p in r.rglob('*') if p.is_file()}
        m = {'fichiers': fichiers, 'licences': {'zlib': {'statut': 'trouvée', 'fichiers': ['licences/zlib/LICENSE']}}, 'licences_manquantes': [],
             'sources': sources_essai()}
        m.update(remplace)
        (r / 'MANIFEST-ocr.json').write_text(json.dumps(m), encoding='utf-8')
        return r

    def test_W2_inclusion_verifiee(self):
        with tempfile.TemporaryDirectory() as d:
            r, app = self._reduit(d), Path(d) / 'app'
            app.mkdir()
            out = BUILD.inclure_ocr(app, str(r))
            self.assertEqual(out['fichiers'], 6)
            self.assertTrue((app / 'ocr' / 'tesseract' / 'tesseract.exe').is_file())
            self.assertEqual((app / 'THIRD_PARTY_NOTICES-OCR.txt').read_bytes(), b'SOURCE DE POPPLER')
            (r / 'poppler' / 'pdftoppm.exe').write_bytes(b'modifie')                        # binaire altéré : construction arrêtée
            with self.assertRaisesRegex(SystemExit, 'modifié'):
                BUILD.inclure_ocr(app, str(r))

    def test_W2_fichier_hors_manifeste_et_chemin_refuse(self):
        with tempfile.TemporaryDirectory() as d:
            r = self._reduit(d)
            ecrire(r, 'poppler/glisse.dll', b'x')
            with self.assertRaisesRegex(SystemExit, 'hors manifeste'):
                BUILD.inclure_ocr(Path(d), str(r))
            (r / 'poppler' / 'glisse.dll').unlink()
            m = json.loads((r / 'MANIFEST-ocr.json').read_text(encoding='utf-8'))
            m['fichiers']['../dehors.txt'] = {'octets': 1, 'sha256': '0'}
            (r / 'MANIFEST-ocr.json').write_text(json.dumps(m), encoding='utf-8')
            with self.assertRaisesRegex(SystemExit, 'chemin refusé'):
                BUILD.inclure_ocr(Path(d), str(r))

    def test_W2_garde_copie_complete_incluse(self):
        with tempfile.TemporaryDirectory() as d:
            r, app = self._reduit(d), Path(d) / 'app'
            app.mkdir()
            err = io.StringIO()
            with patch.object(sys, 'stderr', err):
                out = BUILD.inclure_ocr(app, str(r))
            self.assertIsNotNone(out)
            self.assertTrue((app / 'ocr' / 'licences' / 'zlib' / 'LICENSE').is_file())
            self.assertNotIn('AVERTISSEMENT', err.getvalue())
            m = json.loads((r / 'MANIFEST-ocr.json').read_text(encoding='utf-8'))
            self.assertEqual(BUILD.manques_ocr(m, set(m['fichiers'])), [])

    def test_W2_garde_copie_incomplete_construite_sans_ocr(self):
        cas = {'licence manquante': ({'licences': {'zlib': {'statut': 'manquante', 'fichiers': []}}}, 'licence manquante : zlib'),
               'licence hors copie': ({'licences': {'zlib': {'statut': 'trouvée', 'fichiers': ['licences/zlib/ABSENT']}}}, 'licence manquante : zlib'),
               'liste des manques': ({'licences_manquantes': ['ICU (poppler/icuuc78.dll)']}, 'licence manquante : ICU (poppler/icuuc78.dll)'),
               'sans licences': ({'licences': {}}, 'licences des composants absentes'),
               'sans sources': ({'sources': None}, 'source de Poppler non référencée'),
               'source sans empreinte': ({'sources': sources_essai(Poppler={'nom': 'poppler-26.09.0.tar.xz', 'sha256': '', 'url': 'https://x.test'})},
                                         'source de Poppler non référencée'),
               'source mal nommée': ({'sources': sources_essai(libiconv={'nom': 'autre.tar.gz', 'sha256': '8' * 64, 'url': 'https://x.test'})},
                                     'source de libiconv non référencée')}
        for composant in ('Poppler', 'JBIG-KIT', 'libiconv'):    # chacune des trois sources exigée
            cas['source %s absente' % composant] = ({'sources': sources_essai(**{composant: None})}, 'source de %s non référencée' % composant)
        for nom, (remplace, attendu) in cas.items():
            with self.subTest(nom), tempfile.TemporaryDirectory() as d:
                r, app = self._reduit(d, **remplace), Path(d) / 'app'
                app.mkdir()
                err = io.StringIO()
                with patch.object(sys, 'stderr', err):
                    self.assertIsNone(BUILD.inclure_ocr(app, str(r)))
                self.assertFalse((app / 'ocr').exists())
                self.assertFalse((app / 'THIRD_PARTY_NOTICES-OCR.txt').exists())
                self.assertIn('copie OCR incomplète', err.getvalue())
                self.assertIn(attendu, err.getvalue())

    def test_W2_sans_variable_avertissement_clair(self):
        err = io.StringIO()
        with patch.dict(os.environ, {'AXIORHUB_WIN_OCR': ''}), patch.object(sys, 'stderr', err):
            self.assertIsNone(BUILD.inclure_ocr(Path(tempfile.gettempdir()) / 'inutile-lotw'))
        self.assertIn('SANS Tesseract ni Poppler', err.getvalue())
        texte = (ROOT / 'scripts' / 'build-windows.py').read_text(encoding='utf-8')
        self.assertIn('ocr = inclure_ocr(app_dir)', texte)
        self.assertLess(texte.index('ocr = inclure_ocr(app_dir)'), texte.index("archive = out / ('AxiorHub-Pilote-%s-windows.zip' % ver)"))

    def test_W2_aucun_binaire_tiers_dans_le_depot(self):
        import subprocess
        try:
            suivis = subprocess.run(['git', 'ls-files'], cwd=str(ROOT), capture_output=True, timeout=60, check=True).stdout.decode('utf-8', 'replace').split('\n')
        except (OSError, subprocess.SubprocessError):
            self.skipTest('git indisponible')
        interdits = [f for f in suivis if f.lower().endswith(('.traineddata', 'tesseract.exe', 'pdftoppm.exe', 'libtesseract-5.dll', 'poppler.dll'))]
        self.assertEqual(interdits, [])


# ================================================================================================ langues facultatives
CONTENU = b'donnees fictives de langue ' * 1000


def meta_essai(contenu=CONTENU, sha256=True):
    return {'nom': 'espagnol', 'bouton': 'Ajouter l’espagnol', 'octets': len(contenu), 'apt': 'tesseract-ocr-spa',
            'git': hashlib.sha1(b'blob %d\0' % len(contenu) + contenu).hexdigest(),
            'sha256': hashlib.sha256(contenu).hexdigest() if sha256 else None}


class FauxTransport:
    def __init__(self, contenu=CONTENU, panne=None):
        self.contenu, self.panne, self.urls = contenu, panne, []

    def __call__(self, url, timeout):
        self.urls.append(url)
        if self.panne:
            raise self.panne
        return io.BytesIO(self.contenu)


class Telechargement(_Env):
    def setUp(self):
        super().setUp()
        p = patch.dict(O.LANGUES_OPTIONNELLES, {'spa': meta_essai(), 'deu': dict(meta_essai(), nom='allemand', sha256=None)})
        p.start()
        self.addCleanup(p.stop)
        self.cab = O.tessdata_cabinet()

    def restes(self):
        return sorted(p.name for p in self.tmp.rglob('*.part'))

    def test_W4_bonne_empreinte_gardee(self):
        t = FauxTransport()
        out = O.telecharger('spa', t)
        self.assertEqual(t.urls, ['https://github.com/tesseract-ocr/tessdata/raw/%s/spa.traineddata' % O.COMMIT])
        self.assertEqual(out['sha256'], hashlib.sha256(CONTENU).hexdigest())
        self.assertEqual((self.cab / 'spa.traineddata').read_bytes(), CONTENU)
        self.assertEqual((self.cab / 'fra.traineddata').read_bytes(), b'donnees fra.traineddata')   # langues intégrées reprises
        self.assertEqual(O.tessdata(), self.cab)
        self.assertIn('spa', O.installees())
        self.assertEqual(self.restes(), [])
        O.retirer('spa')
        self.assertFalse(self.cab.exists())                       # plus de langue ajoutée : dossier du cabinet retiré
        self.assertEqual(O.tessdata(), self.ocr / 'tesseract' / 'tessdata')

    def test_W4_mauvaise_empreinte_rejetee_sans_reste(self):
        for nom, transport in (('contenu altéré', FauxTransport(CONTENU[:-1] + b'X')), ('trop court', FauxTransport(CONTENU[:-10])),
                               ('trop long', FauxTransport(CONTENU + b'en plus')), ('panne', FauxTransport(panne=OSError('réseau')))):
            with self.subTest(nom):
                with self.assertRaises(Stop) as ex:
                    O.telecharger('spa', transport)
                self.assertIn(str(ex.exception), ('empreinte_incorrecte', 'telechargement_impossible'))
                self.assertFalse((self.cab / 'spa.traineddata').exists())
                self.assertFalse(self.cab.exists())               # rien n'est gardé, pas même les copies de base
                self.assertEqual(self.restes(), [])
                self.assertEqual(O.tessdata(), self.ocr / 'tesseract' / 'tessdata')

    def test_W4_chaque_empreinte_compte(self):
        """Mutation en mémoire : une seule empreinte fausse (taille, Git ou SHA-256) suffit à rejeter le fichier."""
        bonne = meta_essai()
        for champ, valeur in (('octets', bonne['octets'] + 1), ('git', '0' * 40), ('sha256', '0' * 64)):
            with self.subTest(champ), patch.dict(O.LANGUES_OPTIONNELLES, {'spa': dict(bonne, **{champ: valeur})}):
                with self.assertRaisesRegex(Stop, 'empreinte_incorrecte'):
                    O.telecharger('spa', FauxTransport())
                self.assertFalse((self.cab / 'spa.traineddata').exists())

    def test_W4_empreinte_absente_linux_et_langue_inconnue_sans_appel(self):
        t = FauxTransport()
        with self.assertRaisesRegex(Stop, 'empreinte_a_completer'):
            O.telecharger('deu', t)
        with self.assertRaisesRegex(Stop, 'langue_inconnue'):
            O.telecharger('ita', t)
        with patch.object(portable, 'WINDOWS', False), self.assertRaisesRegex(Stop, 'linux_apt'):
            O.telecharger('spa', t)
        with patch.dict(os.environ, {'AXIORHUB_WIN_OCR': ''}), self.assertRaisesRegex(Stop, 'tesseract_integre_absent'):
            O.telecharger('spa', t)
        self.assertEqual(t.urls, [])

    def test_W4_redirections_limitees_a_github(self):
        for url in ('https://raw.githubusercontent.com/tesseract-ocr/tessdata/x/spa.traineddata', 'https://github.com/tesseract-ocr/tessdata/raw/x/spa.traineddata'):
            self.assertTrue(O.hote_permis(url))
        for url in ('http://raw.githubusercontent.com/x', 'https://example.test/spa.traineddata', 'https://github.com.example.test/x',
                    'https://' + 'zorval' + '@' + 'github.com/x','https://github.com:8443/x', 'file:///C:/x'):
            self.assertFalse(O.hote_permis(url), url)

    def test_W4_empreintes_des_langues_reelles(self):
        """Taille et objet Git lus par l'API GitHub ; SHA-256 calculé sur les fichiers téléchargés au même commit (accord de l'avocat)."""
        src = (ROOT / 'agent' / 'outils_ocr56106.py').read_text(encoding='utf-8')
        self.assertIn("'octets': 18256019, 'git': 'f703e0535e08fce166b3554b330b28f86a369de1',\n"
                      "            'sha256': '0b0fcbb4665189e01ab8019e591f014dd7260460de072543edd4b2cb4ede7c96'", src)
        self.assertIn("'octets': 15437534, 'git': '36f623a0b724d546ba831e7eeead33bf4c6cb24f',\n"
                      "            'sha256': '896b3b4956503ab9daa10285db330881b2d74b70d889b79262cc534b9ec699a4'", src)
        self.assertEqual(O.COMMIT, 'c2b2e0df86272ce11be323f23f96cf656565ed41')


# ================================================================================================ routes, droits, idempotence
def env_de(corps=b'', csrf='jeton'):
    return {'REQUEST_METHOD': 'POST', 'HTTP_ORIGIN': 'https://axiorhub.example.test', 'HTTP_X_CSRF_TOKEN': csrf, 'CONTENT_TYPE': 'application/json',
            'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps)}


AUTH = {'origin': 'https://axiorhub.example.test', 'csrf': 'jeton'}


class Routes(_Env):
    def setUp(self):
        super().setUp()
        import test_agent as fixtures
        from agent.desk import Desk
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.d = Desk(self.f.c)
        O.configurer(self.etat)                       # données de langues du cabinet dans le dossier d'essai
        p = patch.dict(O.LANGUES_OPTIONNELLES, {'spa': meta_essai()})
        p.start()
        self.addCleanup(p.stop)
        self.transport = FauxTransport()
        p = patch.object(O, '_ouvrir', self.transport)
        p.start()
        self.addCleanup(p.stop)

    def api(self, action, data, role='administrateur', owner='admin', csrf='jeton'):
        corps = json.dumps(data).encode()
        with patch('agent.web567.actor', return_value=(owner, role)), patch.object(O, 'configurer', lambda s: None):
            out = O.api(env_de(corps, csrf), self.d, AUTH, '', O.PREFIXE + action, {}, 'POST')
        return out['status'], json.loads(out['body']), out

    def audits(self, action):
        return [json.loads(r[0]) for r in self.d.db.execute('SELECT data FROM audit WHERE action=?', (action,))]

    def test_W5_droits_declares(self):
        for action in O.ECRITURES:
            self.assertTrue(O.allowed('administrateur', 'POST', O.PREFIXE + action))
            for role in ('avocat', 'assistant', 'client'):
                self.assertFalse(O.allowed(role, 'POST', O.PREFIXE + action))
            self.assertFalse(O.allowed('administrateur', 'GET', O.PREFIXE + action))
        self.assertFalse(O.allowed('administrateur', 'POST', O.PREFIXE + 'autre'))
        from agent import standalone_auth, web440
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/m56106/ocr/ajouter'))
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/m56106/ocr/langues'))
        self.assertTrue(standalone_auth.allowed('administrateur', 'POST', '/api440/m56106/ocr/ajouter'))
        self.assertIn("name.startswith('m56106/ocr/')", (ROOT / 'agent' / 'web440.py').read_text(encoding='utf-8'))
        self.assertTrue(hasattr(web440, 'api'))

    def test_W5_avocat_refuse_meme_si_le_controle_central_laisse_passer(self):
        code, corps, out = self.api('ajouter', {'langue': 'spa', 'version': 0, 'cle': 'cle-essai-0001'}, role='avocat', owner='avocat1')
        self.assertEqual((code, corps['error']), ('403 Forbidden', 'role_insuffisant'))
        self.assertEqual(self.transport.urls, [])
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        # mutation en mémoire : sans le garde-fou du service, l'avocat déclencherait le téléchargement
        with patch.object(O, 'allowed', lambda *a: True):
            code, _, _ = self.api('ajouter', {'langue': 'spa', 'version': 0, 'cle': 'cle-essai-0002'}, role='avocat', owner='avocat1')
        self.assertEqual(code, '200 OK')
        self.assertEqual(len(self.transport.urls), 1)

    def test_W5_ajouter_retirer_audit_idempotence_et_409(self):
        data = {'langue': 'spa', 'version': 0, 'cle': 'cle-ajout-0001'}
        code, corps, _ = self.api('ajouter', data)
        self.assertEqual(code, '200 OK', corps)
        self.assertEqual(corps['version'], 1)
        self.assertEqual(self.audits('ocr56106_langue_ajoutee')[0]['sha256'], hashlib.sha256(CONTENU).hexdigest())
        code2, corps2, _ = self.api('ajouter', data)                    # même clé, même demande : réponse gardée, rien de rejoué
        self.assertEqual((code2, corps2), (code, corps))
        self.assertEqual(len(self.transport.urls), 1)
        code, corps, _ = self.api('ajouter', dict(data, langue='deu'))    # même clé, autre demande : 409
        self.assertEqual((code, corps['error']), ('409 Conflict', 'cle_reutilisee'))
        code, corps, _ = self.api('langues', {'l:fra': 'oui', 'l:spa': 'oui', 'version': 0, 'cle': 'cle-langues-01'})
        self.assertEqual((code, corps['error']), ('409 Conflict', 'version_perimee'))
        code, corps, _ = self.api('langues', {'l:fra': 'oui', 'l:spa': 'oui', 'version': 1, 'cle': 'cle-langues-01'})
        self.assertEqual(code, '200 OK', corps)
        self.assertEqual(O.reglages_de(self.d.c['state_dir'])['langues'], 'fra+spa')
        O._CACHE.clear()
        with patch.object(O, '_ETAT', {'state_dir': self.d.c['state_dir']}), patch.object(O, 'tessdata_cabinet', lambda: self.etat / 'ocr56106' / 'tessdata'):
            self.assertEqual(O.langues(), 'fra+spa')
        code, corps, _ = self.api('retirer', {'langue': 'spa', 'version': 2, 'cle': 'cle-retrait-01'})
        self.assertEqual(code, '200 OK', corps)
        self.assertEqual(O.reglages_de(self.d.c['state_dir'])['langues'], 'fra')
        self.assertEqual(self.audits('ocr56106_langue_retiree')[0].get('langue'), 'spa')

    def test_W5_echec_journalise_et_langue_non_installee_refusee(self):
        self.transport.contenu = b'faux'
        code, corps, _ = self.api('ajouter', {'langue': 'spa', 'version': 0, 'cle': 'cle-ajout-0002'})
        self.assertEqual((code, corps['error']), ('502 Bad Gateway', 'empreinte_incorrecte'))
        self.assertEqual(self.audits('ocr56106_langue_refusee')[0].get('motif'), 'empreinte_incorrecte')
        self.assertEqual(O.reglages_de(self.d.c['state_dir'])['version'], 0)
        code, corps, _ = self.api('langues', {'l:spa': 'oui', 'version': 0, 'cle': 'cle-langues-02'})
        self.assertEqual((code, corps['error']), ('400 Bad Request', 'langue_non_installee'))
        code, corps, _ = self.api('langues', {'version': 0, 'cle': 'cle-langues-03'})
        self.assertEqual(corps['error'], 'aucune_langue')
        code, corps, _ = self.api('langues', {'l:fra': 'oui', 'version': 0, 'cle': 'cle-langues-04'}, csrf='faux')
        self.assertEqual(code, '403 Forbidden')
        code, corps, _ = self.api('langues', {'l:fra': 'oui', 'version': 0})
        self.assertEqual(corps['error'], 'cle_requise')

    def test_W6_section_parametres(self):
        html = O.section_html(self.d, '', 'administrateur')
        self.assertIn('Lecture des scans (OCR)', html)
        self.assertIn('data-api="m56106/ocr/ajouter"', html)
        self.assertIn('Ajouter l’espagnol (0,0 Mo)', html)
        self.assertIn('Ajouter l’allemand (15,4 Mo)</button>', html)          # empreinte réelle inscrite : bouton actif
        self.assertNotIn('empreinte SHA-256 non renseignée', html)
        self.assertIn('intégré à l’application', html)
        avocat = O.section_html(self.d, '', 'avocat')
        self.assertNotIn('data-api="m56106/ocr/', avocat)
        self.assertIn('Seul l’administrateur', avocat)
        with patch.dict(O.LANGUES_OPTIONNELLES, {'spa': meta_essai(sha256=False)}):
            self.assertIn('empreinte SHA-256 non renseignée', O.section_html(self.d, '', 'administrateur'))
        with patch.object(portable, 'WINDOWS', False), patch.object(O, 'installees_systeme', lambda timeout=20: ['eng', 'fra']):
            linux = O.section_html(self.d, '', 'administrateur')
        self.assertIn('sudo apt install tesseract-ocr-spa', linux)
        self.assertIn('sudo apt install tesseract-ocr-deu', linux)
        self.assertNotIn('m56106/ocr/ajouter', linux)
        from agent import parametres5621
        self.assertIn('outils_ocr56106 import section_html', (ROOT / 'agent' / 'parametres5621.py').read_text(encoding='utf-8'))
        self.assertTrue(parametres5621)


# ================================================================================================ essais réels (copie réduite)
@unittest.skipUnless(os.name == 'nt' and os.environ.get('AXIORHUB_WIN_OCR'), 'AXIORHUB_WIN_OCR non défini : essais réels sautés')
class EssaisReels(unittest.TestCase):
    def setUp(self):
        O.configurer(None)
        if not O.embarque('tesseract'):
            self.fail('AXIORHUB_WIN_OCR ne contient pas la copie réduite attendue')

    def run_outil(self, args, timeout=120):
        import subprocess
        return portable.run_tool(args, timeout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=portable.tool_env(OMP_THREAD_LIMIT=1))

    def test_W7_tesseract_version_et_langues(self):
        p = self.run_outil([O.chemin('tesseract'), '--version'])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(b'tesseract v5.4', p.stdout + p.stderr)
        p = self.run_outil([O.chemin('tesseract'), '--list-langs'])
        self.assertTrue({'fra', 'eng', 'osd'} <= set((p.stdout + p.stderr).decode().split()))

    def test_W7_ajout_reel_de_l_espagnol_par_un_transport_local(self):
        """Vrai fichier spa.traineddata (téléchargé par le coordinateur) servi localement : vraies empreintes, vrai Tesseract."""
        local = Path(os.environ['AXIORHUB_WIN_OCR']).parent / 'telechargements' / 'spa.traineddata'
        if not local.is_file():
            self.skipTest('spa.traineddata absent de tiers-ocr/telechargements')
        with tempfile.TemporaryDirectory() as etat:
            O.configurer(etat)
            try:
                with self.assertRaisesRegex(Stop, 'empreinte_incorrecte'):          # même fichier tronqué : rejeté
                    O.telecharger('spa', lambda url, timeout: io.BytesIO(local.read_bytes()[:-1]))
                bilan = O.telecharger('spa', lambda url, timeout: open(local, 'rb'))
                self.assertEqual(bilan['sha256'], O.LANGUES_OPTIONNELLES['spa']['sha256'])
                p = self.run_outil([O.chemin('tesseract'), '--list-langs'])
                self.assertIn('spa', (p.stdout + p.stderr).decode().split())
                self.assertEqual(portable.tool_env()['TESSDATA_PREFIX'], str(Path(etat) / 'ocr56106' / 'tessdata'))
                with tempfile.TemporaryDirectory() as d:          # OCR réel avec fra+spa (dossier du cabinet)
                    png = Path(d) / 'page.png'
                    self._image().save(png)
                    with patch.object(O, 'langues', lambda: 'fra+spa'):
                        self.assertIn('DURAND', documents.ocr(png))
                O.retirer('spa')
                self.assertFalse((Path(etat) / 'ocr56106').exists())
            finally:
                O.configurer(None)

    def _image(self):
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            self.skipTest('Pillow absent')
        img = Image.new('L', (1400, 220), 255)
        try:
            police = ImageFont.truetype('arial.ttf', 56)
        except OSError:
            police = ImageFont.load_default(size=56)
        ImageDraw.Draw(img).text((40, 70), 'Cabinet DURAND, RG 24/01234', fill=0, font=police)
        return img

    def test_W7_ocr_d_une_image_generee(self):
        with tempfile.TemporaryDirectory() as d:
            png = Path(d) / 'page.png'
            self._image().save(png)
            texte = documents.ocr(png)
        self.assertIn('DURAND', texte)
        self.assertIn('24/01234', texte)

    def test_W7_pdftoppm_pdftotext_et_scan_complet(self):
        raw = (ROOT / 'tests' / 'fixtures' / 'document-demo.pdf').read_bytes()
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 'demo.pdf'
            src.write_bytes(raw)
            p = self.run_outil([O.chemin('pdftoppm'), '-f', '1', '-l', '1', '-r', '50', '-singlefile', '-png', str(src), str(Path(d) / 'p')])
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual((Path(d) / 'p.png').read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
        self.assertIn('Client DEMO', documents.extract(raw, 'demo.pdf', {}))
        # scan fictif : PDF d'une page qui ne contient que l'image → pdftotext vide → pdftoppm → Tesseract
        from agent import vision56106
        img = self._image()
        scan = vision56106.pdf_image(img.width, img.height, 'FlateDecode', 'DeviceGray', __import__('zlib').compress(img.tobytes()))
        texte = documents.extract(scan, 'scan.pdf', {})
        self.assertIn('DURAND', texte)
        self.assertIn('[Texte OCR local', texte)


if __name__ == '__main__':
    unittest.main()
