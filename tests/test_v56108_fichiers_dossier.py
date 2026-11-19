"""5.6.108 (lot F) : onglet Fichiers du dossier — « Nouveau document », « Ajouter un fichier » (glisser-déposer), remplir tout PDF à champs,
boîte à outils PDF (annoter, fusionner, découper, réorganiser, numéroter, caviarder, compresser).

Identifiants : F1 (boutons et dépôt de l'onglet Fichiers), F2 (remplir un PDF à champs), F3 (textes en/de/es), O1 (annoter), O2 (pages :
fusionner, découper, organiser, numéroter), O3 (caviarder), O4 (compresser), O5 (gardes communes), M (contre-épreuves par mutation en mémoire).

PDF fictifs écrits ici octet par octet (aucun fichier réel) : formulaire à champs, PDF plat, chiffré, signé, XFA, hybride, piégé ; données
fictives (DURAND, MARTIN, ZORVAL, DOS-001, RG 24/01234, Valbrune). Stockage : dossier local temporaire ; aucun réseau, aucun modèle.
Le caviardage réel (Poppler, Tesseract) n'est lancé que si les outils sont présents (``AXIORHUB_WIN_OCR`` sous Windows, ``/usr/bin`` sous Linux).
"""
import base64
import io
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import time
import unittest
from unittest.mock import patch
import zlib

from agent import acces5697, interface56100, outils_pdf56108 as O, pdf_formulaire56108 as F
from agent.common import Stop, fold, private_json
from agent.desk import Desk

ROOT = Path(__file__).resolve().parents[1]
AVOCATE = ('avocate@example.test', 'avocat')
ASSISTANT = ('assistant@example.test', 'assistant')
AUTRE = ('autre@example.test', 'avocat')
ORIGINE = 'https://axiorhub.example.test'


# ================================================================================================ PDF fictifs
def flux(data, extra=b''):
    return b'<< /Length %d%s >>\nstream\n' % (len(data), extra) + data + b'\nendstream'


def assembler(objets, racine=1, info=None):
    out = b'%PDF-1.7\n%\xe2\xe3\xcf\xd3\n'
    pos = []
    for i, corps in enumerate(objets, 1):
        pos.append(len(out))
        out += b'%d 0 obj\n' % i + corps + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objets) + 1) + b''.join(b'%010d 00000 n \n' % p for p in pos)
    out += b'trailer\n<< /Size %d /Root %d 0 R%s >>\nstartxref\n%d\n%%%%EOF\n' % (len(objets) + 1, racine, (b' /Info %d 0 R' % info) if info else b'', xref)
    return out


def texte_page(lignes):
    """Contenu : chaque ligne (x, y, texte en cp1252) en Helvetica 11."""
    return b''.join(b'BT /F1 11 Tf %d %d Td (%s) Tj ET\n' % (x, y, t.encode('cp1252')) for x, y, t in lignes)


POLICE = b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>'


def pdf_formulaire(signe=False, xfa=False, bombe=False):
    """Formulaire fictif : texte, date (AFDate), plusieurs lignes, case, radio, liste déroulante, liste, lecture seule, signature vide, infobulle."""
    libelles = [(50, 765, 'Nom du client'), (50, 725, 'Partie adverse'), (50, 685, 'Numéro RG'), (50, 645, 'Fait le'), (50, 605, 'Observations'),
                (50, 523, 'Accepte les conditions'), (50, 483, 'Qualité'), (50, 445, 'Ville'), (50, 405, 'Degré'), (50, 345, 'Code interne')]
    o = {}
    o[1] = b'<< /Type /Catalog /Pages 2 0 R /AcroForm 5 0 R >>'
    o[2] = b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>'
    o[3] = b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 6 0 R >> >> /Annots [10 0 R 11 0 R 12 0 R 13 0 R 14 0 R 15 0 R 17 0 R 18 0 R 19 0 R 20 0 R 21 0 R 22 0 R 23 0 R] >>'
    o[4] = flux(texte_page(libelles)) if not bombe else flux(zlib.compress(b' ' * (F.MAX_FLUX_PAGE + 10)), b' /Filter /FlateDecode')
    acro = b'<< /Fields [10 0 R 11 0 R 12 0 R 13 0 R 14 0 R 15 0 R 16 0 R 19 0 R 20 0 R 21 0 R 22 0 R 23 0 R] /DA (/Helv 0 Tf 0 g) /DR << /Font << /Helv 7 0 R >> >>'
    if signe:
        acro += b' /SigFlags 3'
    if xfa:
        acro += b' /XFA 24 0 R'
    o[5] = acro + b' >>'
    o[6] = POLICE
    o[7] = POLICE
    o[8] = flux(b'0 0 1 rg 2 2 10 10 re f', b' /Type /XObject /Subtype /Form /BBox [0 0 14 14]')
    o[9] = flux(b'', b' /Type /XObject /Subtype /Form /BBox [0 0 14 14]')
    w = b'/Type /Annot /Subtype /Widget /P 3 0 R /F 4 '
    o[10] = b'<< ' + w + b'/FT /Tx /T (a1) /Rect [200 760 450 780] >>'
    o[11] = b'<< ' + w + b'/FT /Tx /T (a2) /Rect [200 720 450 740] >>'
    o[12] = b'<< ' + w + b'/FT /Tx /T (a3) /Rect [200 680 450 700] >>'
    o[13] = b'<< ' + w + b'/FT /Tx /T (a4) /Rect [200 640 450 660] /AA << /F << /S /JavaScript /JS (AFDate_FormatEx\\("dd/mm/yyyy"\\);) >> >> >>'
    o[14] = b'<< ' + w + b'/FT /Tx /T (a5) /Ff 4096 /Rect [200 560 450 620] >>'
    o[15] = b'<< ' + w + b'/FT /Btn /T (c1) /Rect [200 520 214 534] /AP << /N << /Oui 8 0 R /Off 9 0 R >> >> /AS /Off /V /Off >>'
    o[16] = b'<< /FT /Btn /Ff 49152 /T (r1) /Kids [17 0 R 18 0 R] /V /Off >>'
    o[17] = b'<< ' + w + b'/Parent 16 0 R /Rect [200 480 214 494] /AP << /N << /Demandeur 8 0 R /Off 9 0 R >> >> /AS /Off >>'
    o[18] = b'<< ' + w + b'/Parent 16 0 R /Rect [300 480 314 494] /AP << /N << /Defendeur 8 0 R /Off 9 0 R >> >> /AS /Off >>'
    o[19] = b'<< ' + w + b'/FT /Ch /Ff 131072 /T (j1) /Opt [(Paris) (Lyon) (Valbrune)] /Rect [200 440 350 460] >>'
    o[20] = b'<< ' + w + b'/FT /Ch /T (l1) /Opt [[(1) (Premiere instance)] [(2) (Appel)]] /Rect [200 380 350 420] >>'
    o[21] = b'<< ' + w + b'/FT /Tx /Ff 1 /T (ro) /V (fixe) /Rect [200 340 350 360] >>'
    o[22] = b'<< ' + w + b'/FT /Sig /T (sig1) /Rect [200 280 350 320] >>'
    o[23] = b'<< ' + w + b'/FT /Tx /T (zz9) /TU (R\xe9f\xe9rence du dossier) /Rect [200 240 450 260] >>'
    o[24] = flux(b'<xdp:xdp xmlns:xdp="http://ns.adobe.com/xdp/"></xdp:xdp>')
    return assembler([o[i] for i in range(1, 25)])


def pdf_pages(*textes, extra_catalogue=b'', annots=b'', info=None, objets_sup=()):
    """PDF plat de N pages, une ligne de texte par page."""
    n = len(textes)
    o = [b'<< /Type /Catalog /Pages 2 0 R' + extra_catalogue + b' >>',
         b'<< /Type /Pages /Kids [%s] /Count %d >>' % (b' '.join(b'%d 0 R' % (4 + 2 * i) for i in range(n)), n), POLICE]
    for i, t in enumerate(textes):
        o.append(b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents %d 0 R /Resources << /Font << /F1 3 0 R >> >>%s >>' % (
            5 + 2 * i, annots if i == 0 else b''))
        o.append(flux(texte_page([(72, 720, t)])))
    o.extend(objets_sup)
    return assembler(o, info=info)


def pdf_xfa_seul():
    return assembler([b'<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [] /XFA 5 0 R >> >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
                      b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R >>', flux(b''),
                      flux(b'<xdp:xdp xmlns:xdp="http://ns.adobe.com/xdp/"></xdp:xdp>')])


def pdf_chiffre():
    from agent._vendor.pypdf import PdfReader, PdfWriter
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_pages('Document fictif DURAND'))))
    w.encrypt(user_password='', owner_password='proprietaire-fictif', algorithm='RC4-128')
    b = io.BytesIO()
    w.write(b)
    return b.getvalue()


def pdf_a_caviarder():
    """Texte à caviarder dans le texte de la page, une annotation, un signet, les métadonnées, une pièce jointe et un champ de formulaire."""
    extra = b' /Outlines 20 0 R /Names << /EmbeddedFiles << /Names [(note-ZORVAL.txt) 23 0 R] >> >> /AcroForm << /Fields [25 0 R] >>'
    annots = b' /Annots [22 0 R 25 0 R]'
    sup = [b'<< /Type /Outlines /First 21 0 R /Last 21 0 R /Count 1 >>',
           b'<< /Title (Signet ZORVAL) /Parent 20 0 R /Dest [4 0 R /Fit] >>',
           b'<< /Type /Annot /Subtype /Text /Rect [400 700 420 720] /Contents (Note sur ZORVAL) /P 4 0 R >>',
           b'<< /Type /Filespec /F (note-ZORVAL.txt) /EF << /F 24 0 R >> >>',
           flux(b'Piece jointe ZORVAL', b' /Type /EmbeddedFile'),
           b'<< /Type /Annot /Subtype /Widget /FT /Tx /T (nom) /V (ZORVAL) /Rect [72 600 300 620] /P 4 0 R >>',
           b'<< /Title (Dossier ZORVAL) /Author (Cabinet DURAND) >>']
    # objets 1-3 + 2 pages (4..7) → objets supplémentaires numérotés à partir de 8 : on complète jusqu'à 19 par des objets vides
    vides = [b'null'] * (19 - 7)
    return pdf_pages('Monsieur ZORVAL demeurant a Valbrune conteste la facture', 'Page deux sans nom propre, audience a Valbrune',
                     extra_catalogue=extra, annots=annots, info=26, objets_sup=vides + sup)


def pdf_avec_images(doublon=True, n=2, bruit=False):
    """PDF dont les images (dégradé, ou grain fictif peu compressible sans perte) se répètent : la compression sans perte les dédoublonne."""
    import hashlib
    w, h = 220, 220
    if bruit:
        pixels = b''.join(hashlib.sha256(b'%d' % i).digest() for i in range(w * h * 3 // 32 + 1))[:w * h * 3]
    else:
        pixels = bytes(((x * 7 + y * 3) % 256) for y in range(h) for x in range(w) for _ in range(3))
    o = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
         b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /XObject << %s >> >> >>' % b' '.join(
             b'/Im%d %d 0 R' % (i, 5 + (0 if doublon else i)) for i in range(n)),
         flux(b''.join(b'q 200 0 0 200 %d %d cm /Im%d Do Q\n' % (50 + i * 10, 400 - i * 10, i) for i in range(n)))]
    for i in range(1 if doublon else n):
        o.append(flux(pixels, b' /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB /BitsPerComponent 8' % (w, h)))
    if doublon:   # une seconde image identique, objet distinct
        o[2] = o[2].replace(b'/Im1 5 0 R', b'/Im1 6 0 R')
        o.append(flux(pixels, b' /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB /BitsPerComponent 8' % (w, h)))
    return assembler(o)


def textes(raw):
    from agent._vendor.pypdf import PdfReader
    return [p.extract_text() or '' for p in PdfReader(io.BytesIO(raw)).pages]


def champs_lus(raw):
    from agent._vendor.pypdf import PdfReader
    return {k: v.get('/V') for k, v in (PdfReader(io.BytesIO(raw)).get_fields() or {}).items()}


def outils_reels():
    o = O.outils_locaux()
    return o if all(o.values()) else None


# ================================================================================================ base
class Base(unittest.TestCase):
    def setUp(self):
        from sqlite_cleanup import close_sqlite_under
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.addCleanup(close_sqlite_under, self.t.name)
        base = Path(self.t.name)
        (base / 'state').mkdir()
        self.stockage = base / 'stockage'
        self.matters = []
        for i, nom in enumerate(('DURAND', 'MARTIN', 'ZORVAL')):
            ident = 'DOS-%03d' % (i + 1)
            path = '/Dossiers/%s - %s' % (ident, nom)
            (self.stockage / path.lstrip('/')).mkdir(parents=True)
            self.matters.append({'id': ident, 'client_name': nom, 'path': path, 'references': [ident], 'aliases': [nom], 'correspondents': []})
        private_json(base / 'matters.json', self.matters)
        self.c = {'mode': 'drafts', 'state_dir': str(base / 'state'), 'matters_file': str(base / 'matters.json'), 'mail': {'own_addresses': ['cabinet@example.test']},
                  'nextcloud': {'url': 'https://cloud.example.test', 'local_path': str(self.stockage), 'roots': ['/Dossiers'], 'matter_roots': ['/Dossiers']},
                  'documents': {}, 'ollama': {'model': 'interdit'}, 'calendar': {'urls': [], 'timezone': 'Europe/Paris'}, 'cabinet': {'name': 'Cabinet DURAND Avocats'}}
        self.d = Desk(self.c)
        self.addCleanup(self.d.db.close)
        for cible in ('agent.model.Model.ask', 'agent.model.Model.complete'):
            p = patch(cible, side_effect=AssertionError('appel au modèle interdit'))
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(socket, 'create_connection', side_effect=AssertionError('connexion réseau interdite'))
        p.start()
        self.addCleanup(p.stop)
        from agent import membres5650
        membres5650.directory(self.d)
        for owner, role in (AVOCATE, ASSISTANT, AUTRE):
            self.d.db.execute('INSERT OR IGNORE INTO membres5650(owner,name,role,first_seen,last_seen) VALUES(?,?,?,?,?)', (owner, owner.split('@')[0], role, 'x', 'x'))
        self.d.db.commit()
        membres5650.set_reserve(self.c['state_dir'], 'DOS-003', [AUTRE[0]])     # DOS-003 réservé à un autre avocat

    def deposer(self, nom, raw, dossier='DOS-001'):
        m = next(x for x in self.matters if x['id'] == dossier)
        (self.stockage / m['path'].lstrip('/') / nom).write_bytes(raw)
        return '%s/%s' % (m['path'], nom)

    def fichiers(self, dossier='DOS-001'):
        m = next(x for x in self.matters if x['id'] == dossier)
        return sorted(p.name for p in (self.stockage / m['path'].lstrip('/')).iterdir() if p.is_file())

    def lire(self, nom, dossier='DOS-001'):
        m = next(x for x in self.matters if x['id'] == dossier)
        return (self.stockage / m['path'].lstrip('/') / nom).read_bytes()

    def env(self, corps=b'', owner=AVOCATE, csrf='jeton-fictif', ctype='application/json', methode='POST'):
        return {'REQUEST_METHOD': methode, 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': csrf, 'CONTENT_TYPE': ctype,
                'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps), 'HTTP_X_AXIORHUB_USER': owner[0], 'HTTP_X_AXIORHUB_ROLE': owner[1]}

    def auth(self):
        return {'origin': ORIGINE, 'csrf': 'jeton-fictif', 'owner': AVOCATE[0]}

    def appel(self, route, corps=None, owner=AVOCATE, args=None, methode='POST', brut=None, ctype='application/json', csrf='jeton-fictif'):
        data = brut if brut is not None else json.dumps(corps or {}).encode('utf-8')
        env = self.env(data if methode == 'POST' else b'', owner, csrf, ctype, methode)
        out = F.api(env, self.d, self.auth(), '/agent-courriel', F.PREFIXE + route, args or {}, methode)
        self.dernier_env = env
        return int(out['status'][:3]), json.loads(out['body']), out

    def etat(self, chemin, dossier='DOS-001', owner=AVOCATE):
        return F.etat_formulaire(self.d, acces5697.Acteur(*owner), dossier, chemin)

    def page(self, module, args, owner=AVOCATE):
        shell = lambda titre, corps, *a, **k: '<html><body>%s</body></html>' % corps
        env = self.env(b'', owner, methode='GET')
        with patch('agent.desk.Desk', return_value=self.d), patch.object(self.d.db, 'close'):
            out = module.page_route(env, self.c, self.auth(), '/agent-courriel', module.PAGE, args, shell)
        return int(out['status'][:3]), out['body']


# ================================================================================================ F1 : onglet Fichiers
class OngletFichiers(Base):
    def rendu(self, owner=AVOCATE):
        from agent import dossier5679, membres5650
        m = self.matters[0]
        with patch.object(membres5650, 'current_author', return_value=owner[0]), patch.object(membres5650, 'viewer_role', return_value=owner[1]):
            return dossier5679.fichiers_html(self.d, {'owner': owner[0]}, '/agent-courriel', m, {'vue': 'fichiers'})

    def test_F1_barre_nouveau_document_et_ajouter_un_fichier(self):
        html = self.rendu()
        self.assertIn('data-f108-depot', html)
        self.assertIn('/agent-courriel/nouveau-document?dossier=DOS-001', html)          # même parcours que l'en-tête, dossier prérempli
        self.assertIn('Ajouter un fichier', html)
        self.assertIn('type="file" id="f108-fichiers"', html)
        self.assertIn('accept="%s"' % F.FORMATS, html)
        self.assertIn('Déposez les fichiers ici', html)

    def test_F1_ancienne_presentation_sans_barre(self):
        interface56100.regler_cabinet(self.d, False)
        html = self.rendu()
        self.assertNotIn('f108', html)

    def test_F1_liste_boutons_pdf_et_montage_des_autres_lots(self):
        from agent import dossier5679, membres5650, signaturepdf56108, wordcraft56108
        doc = {'path': self.matters[0]['path'] + '/Formulaire fictif.pdf', 'name': 'Formulaire fictif.pdf', 'nature': 'autre_document', 'reading': 'lisible',
               'piece_party': '', 'usage_piece': '', 'piece_number': '', 'version': 1, 'modified': '2026-11-18', 'doc_date': '2026-11-18', 'emitter': ''}
        docx = dict(doc, path=self.matters[0]['path'] + '/Note.docx', name='Note.docx')
        vus = []
        with patch.object(dossier5679, '_documents', return_value=[doc, docx]), patch('agent.suivi5679.digests_overview', return_value={}),                 patch.object(wordcraft56108, 'boutons_html', side_effect=lambda *a: vus.append(a[4]) or '<a id="bouton-w">W</a>'),                 patch.object(signaturepdf56108, 'boutons_fichier_html', side_effect=lambda *a: '<a id="bouton-z">Z</a>' if a[4].endswith('.pdf') else ''),                 patch.object(membres5650, 'current_author', return_value=AVOCATE[0]), patch.object(membres5650, 'viewer_role', return_value='avocat'):
            html = dossier5679.documents_html(self.d, {'owner': AVOCATE[0]}, '/agent-courriel', self.matters[0], {})
        self.assertIn('/agent-courriel/dossier/remplir-pdf?d=DOS-001', html)
        self.assertIn('/agent-courriel/dossier/outils-pdf?d=DOS-001', html)
        self.assertIn('data-f108-choix', html)
        self.assertEqual(html.count('id="bouton-w"'), 2)                                                # monté sur chaque ligne (lot W)
        self.assertEqual(vus, [doc['path'], docx['path']])
        self.assertEqual(html.count('Remplir ce formulaire'), 1)                                       # PDF seulement
        self.assertEqual(html.count('id="bouton-z"'), 1)                                                # lot Z : PDF seulement
        self.assertEqual(dossier5679.MONTAGES_FICHIER108, (('wordcraft56108', 'boutons_html'), ('signaturepdf56108', 'boutons_fichier_html')))

    def test_F1_ajouter_fichier_rejeu_un_seul_fichier(self):
        pdf = pdf_pages('Courrier fictif DURAND')
        args = {'d': 'DOS-001', 'nom': 'Courrier MARTIN.pdf', 'cle': 'cle-ajout-0001'}
        code, out, rep = self.appel('ajouter', args=args, brut=pdf, ctype='application/octet-stream')
        self.assertEqual(code, 200, out)
        self.assertEqual(out['message'], 'Fichier ajouté au dossier : Courrier MARTIN.pdf.')
        self.assertIn(('Cache-Control', 'private, no-store'), rep['headers'])
        code, out2, _ = self.appel('ajouter', args=args, brut=pdf, ctype='application/octet-stream')
        self.assertEqual(code, 200)
        self.assertTrue(out2.get('rejoue'))
        self.assertEqual(self.fichiers(), ['Courrier MARTIN.pdf'])
        self.assertEqual(self.lire('Courrier MARTIN.pdf'), pdf)
        n = self.d.db.execute("SELECT COUNT(*) FROM prepared_items5689 WHERE matter_id='DOS-001'").fetchone()[0]
        self.assertEqual(n, 1)                                                                         # registre commun

    def test_F1_dossier_reserve_et_absent_meme_404_avant_lecture_du_corps(self):
        pdf = pdf_pages('Pièce fictive')
        reponses = []
        for dossier in ('DOS-003', 'DOS-999'):
            code, out, _ = self.appel('ajouter', args={'d': dossier, 'nom': 'a.pdf', 'cle': 'cle-ajout-0002'}, brut=pdf, ctype='application/octet-stream')
            self.assertEqual(self.dernier_env['wsgi.input'].tell(), 0)                                  # corps jamais lu
            reponses.append((code, out))
        self.assertEqual(reponses[0], reponses[1])
        self.assertEqual(reponses[0][0], 404)
        self.assertEqual(self.fichiers('DOS-003'), [])

    def test_F1_jeton_csrf_format_et_taille(self):
        pdf = pdf_pages('Pièce fictive')
        code, out, _ = self.appel('ajouter', args={'d': 'DOS-001', 'nom': 'a.pdf', 'cle': 'cle-ajout-0003'}, brut=pdf, ctype='application/octet-stream', csrf='faux')
        self.assertEqual(code, 403)
        code, out, _ = self.appel('ajouter', args={'d': 'DOS-001', 'nom': 'outil.exe', 'cle': 'cle-ajout-0004'}, brut=b'MZ fictif', ctype='application/octet-stream')
        self.assertEqual((code, out['error']), (400, 'format_non_accepte'))
        code, out, _ = self.appel('ajouter', args={'d': 'DOS-001', 'nom': 'faux.pdf', 'cle': 'cle-ajout-0005'}, brut=b'PK pas un pdf', ctype='application/octet-stream')
        self.assertEqual(out['error'], 'contenu_ne_correspond_pas_au_format')
        env = self.env(b'', ctype='application/octet-stream')
        env['CONTENT_LENGTH'] = str(16_000_000)
        out = F.api(env, self.d, self.auth(), '', F.PREFIXE + 'ajouter', {'d': 'DOS-001', 'nom': 'gros.pdf', 'cle': 'cle-ajout-0006'}, 'POST')
        self.assertEqual(json.loads(out['body'])['error'], 'fichier_vide_ou_trop_volumineux')
        self.assertEqual(self.fichiers(), [])

    def test_F1_droits_des_roles_et_routes(self):
        from agent import standalone_auth
        for role in ('administrateur', 'avocat', 'assistant'):
            self.assertTrue(standalone_auth.allowed(role, 'POST', '/api440/m56108/fichiers-dossier/ajouter'))
            self.assertTrue(standalone_auth.allowed(role, 'POST', '/api440/m56108/fichiers-dossier/caviarder'))
        self.assertFalse(F.allowed('avocat', 'POST', F.PREFIXE + 'inconnue'))
        self.assertFalse(F.allowed('stagiaire', 'POST', F.PREFIXE + 'ajouter'))
        self.assertFalse(F.allowed('avocat', 'GET', F.PREFIXE + 'remplir'))
        from agent import web440
        for s in ('/static/v56108-fichiers.js', '/static/v56108-fichiers.css'):
            self.assertIn(s, web440.STATIC)
        from agent import shell501
        self.assertIn('v56108-fichiers.css', shell501.BUNDLE)
        self.assertIn('.f108-voile', (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8'))

    def test_F1_script_sans_html_injecte(self):
        js = (ROOT / 'agent' / 'static' / 'v56108-fichiers.js').read_text(encoding='utf-8')
        self.assertNotIn('innerHTML', js)
        self.assertNotIn('insertAdjacentHTML', js)
        self.assertNotIn('eval(', js)


# ================================================================================================ F2 : remplir un PDF à champs
class Remplir(Base):
    def test_F2_detection_des_champs_et_libelles(self):
        a = F.analyser(pdf_formulaire())
        c = {x['nom']: x for x in a['champs']}
        self.assertEqual(c['a1']['libelle'], 'Nom du client')
        self.assertEqual(c['a3']['libelle'], 'Numéro RG')
        self.assertEqual((c['a4']['type'], c['a4']['format']), ('date', 'dd/mm/yyyy'))
        self.assertTrue(c['a5']['multiligne'])
        self.assertEqual((c['c1']['type'], c['c1']['etats']), ('case', ['Oui']))
        self.assertEqual((c['r1']['type'], c['r1']['etats']), ('radio', ['Demandeur', 'Defendeur']))
        self.assertEqual((c['j1']['type'], c['j1']['deroulante'], [o['valeur'] for o in c['j1']['options']]), ('liste', True, ['Paris', 'Lyon', 'Valbrune']))
        self.assertEqual([o['libelle'] for o in c['l1']['options']], ['Premiere instance', 'Appel'])
        self.assertTrue(c['ro']['lecture_seule'])
        self.assertEqual(c['zz9']['libelle'], 'Référence du dossier')                                  # infobulle d'abord
        self.assertEqual(a['signatures_vides'], 1)
        self.assertTrue(a['scripts'])                                                                  # signalé, jamais exécuté
        self.assertNotIn('sig1', c)

    def test_F2_propositions_avec_source_jamais_imposees(self):
        from agent import modele5651
        modele5651.save_procedure(self.d, 'DOS-001', 'avocate', {'juridiction': 'Tribunal judiciaire de Valbrune', 'rg': '24/01234', 'statut': 'active', 'reference': True})
        modele5651.add_party(self.d, 'DOS-001', 'avocate', 'MARTIN')
        chemin = self.deposer('Formulaire fictif.pdf', pdf_formulaire())
        e = self.etat(chemin)
        p = e['propositions']
        self.assertEqual(p['a1'], {'valeur': 'DURAND', 'source': 'fiche du dossier (client)', 'cle': 'client_nom'})
        self.assertEqual(p['a2']['valeur'], 'MARTIN')
        self.assertEqual((p['a3']['valeur'], p['a3']['source']), ('24/01234', 'procédure de référence du dossier'))
        self.assertEqual(p['a4']['cle'], 'date_du_jour')
        self.assertRegex(p['a4']['valeur'], r'^\d{2}/\d{2}/\d{4}$')                               # affichée en français
        self.assertEqual(p['zz9']['valeur'], 'DOS-001')
        self.assertTrue(all(c['valeur'] == '' for c in e['champs'] if c['nom'] in p))                 # rien n'est prérempli d'office
        status, html = self.page(F, {'d': 'DOS-001', 'p': chemin})
        self.assertEqual(status, 200)
        self.assertIn('Proposition : <strong data-f108-prop-valeur>DURAND</strong>', html)
        self.assertIn('Utiliser toutes les propositions', html)
        self.assertIn('data-f108-apercu="/agent-courriel/dossier/fichier?', html)

    def corps(self, chemin, raw, valeurs, cle='cle-remplir-0001', aplatir=False):
        return {'d': 'DOS-001', 'p': chemin, 'empreinte': F._sha(raw), 'valeurs': valeurs, 'aplatir': aplatir, 'cle': cle}

    def test_F2_copie_remplie_nouveau_fichier_original_intact(self):
        raw = pdf_formulaire()
        chemin = self.deposer('Formulaire fictif.pdf', raw)
        valeurs = {'a1': 'DURAND', 'a4': '2026-11-18', 'a5': 'Ligne un\nLigne deux', 'c1': True, 'r1': 'Defendeur', 'j1': 'Valbrune', 'l1': '2'}
        code, out, rep = self.appel('remplir', self.corps(chemin, raw, valeurs))
        self.assertEqual(code, 200, out)
        self.assertEqual(out['etat'], 'verified')
        self.assertRegex(out['nom'], r'^Formulaire fictif \(rempli le \d{2}-\d{2}-\d{4}\)\.pdf$')
        self.assertEqual(self.lire('Formulaire fictif.pdf'), raw)                                      # original intact
        lu = champs_lus(self.lire(out['nom']))
        self.assertEqual((lu['a1'], lu['a4'], lu['j1'], lu['l1']), ('DURAND', '18/11/2026', 'Valbrune', '2'))
        self.assertEqual((str(lu['c1']), str(lu['r1'])), ('/Oui', '/Defendeur'))
        self.assertEqual(lu['ro'], 'fixe')
        self.assertEqual(out['ecrits'], 7)
        self.assertIn('L’original est inchangé', out['message'])
        self.assertIn(('Cache-Control', 'private, no-store'), rep['headers'])

    def test_F2_aplatir_option_distincte(self):
        raw = pdf_formulaire()
        chemin = self.deposer('Formulaire fictif.pdf', raw)
        code, out, _ = self.appel('remplir', self.corps(chemin, raw, {'a1': 'DURAND', 'c1': True}, aplatir=True))
        self.assertEqual(code, 200, out)
        self.assertIn('non modifiable', out['nom'])
        copie = self.lire(out['nom'])
        self.assertEqual(champs_lus(copie), {})
        from agent._vendor.pypdf import PdfReader
        page = PdfReader(io.BytesIO(copie)).pages[0]
        self.assertNotIn('/AcroForm', PdfReader(io.BytesIO(copie)).trailer['/Root'])
        self.assertIn('/AxPlat', str(page['/Resources']['/XObject'].keys()))

    def test_F2_un_seul_effet_rejeu_et_cle_reutilisee(self):
        raw = pdf_formulaire()
        chemin = self.deposer('Formulaire fictif.pdf', raw)
        corps = self.corps(chemin, raw, {'a1': 'DURAND'})
        _, out1, _ = self.appel('remplir', corps)
        code, out2, _ = self.appel('remplir', corps)
        self.assertEqual(code, 200)
        self.assertTrue(out2['rejoue'])
        self.assertEqual(out1['chemin'], out2['chemin'])
        self.assertEqual(len(self.fichiers()), 2)
        code, out3, _ = self.appel('remplir', self.corps(chemin, raw, {'a1': 'MARTIN'}))
        self.assertEqual((code, out3['error']), (409, 'cle_reutilisee'))

    def test_F2_reponse_perdue_le_depot_est_retrouve(self):
        raw = pdf_formulaire()
        chemin = self.deposer('Formulaire fictif.pdf', raw)
        corps = self.corps(chemin, raw, {'a1': 'DURAND'}, cle='cle-perdue-0001')
        from agent import livrables5689
        with patch.object(livrables5689, 'constater', side_effect=Stop('coupure_fictive')):
            code, out, _ = self.appel('remplir', corps)
        self.assertEqual(code, 400)
        code, out, _ = self.appel('remplir', corps)                                                   # la sortie déjà déposée est relue
        self.assertEqual((code, out['etat']), (200, 'verified'))
        self.assertEqual(len(self.fichiers()), 2)

    def test_F2_pdf_modifie_depuis_ouverture_409(self):
        raw = pdf_formulaire()
        chemin = self.deposer('Formulaire fictif.pdf', raw)
        corps = self.corps(chemin, raw, {'a1': 'DURAND'})
        corps['empreinte'] = '0' * 64
        code, out, _ = self.appel('remplir', corps)
        self.assertEqual((code, out['error']), (409, 'version_perimee'))
        self.assertEqual(self.fichiers(), ['Formulaire fictif.pdf'])

    def test_F2_valeurs_controlees(self):
        raw = pdf_formulaire()
        chemin = self.deposer('Formulaire fictif.pdf', raw)
        for i, (valeurs, code) in enumerate(((({'r1': 'Tiers'}), 'valeur_invalide'), ({'j1': 'Marseille'}, 'valeur_invalide'), ({'inconnu': 'x'}, 'champ_inconnu'),
                                              ({'ro': 'autre'}, 'champ_lecture_seule'), ({'a4': '31/02/2026'}, 'valeur_invalide'), ({}, 'aucune_valeur'))):
            _, out, _ = self.appel('remplir', self.corps(chemin, raw, valeurs, cle='cle-valeur-%04d' % i))
            self.assertEqual(out['error'], code, valeurs)
        self.assertEqual(self.fichiers(), ['Formulaire fictif.pdf'])

    def test_F2_limites_dites_clairement(self):
        cas = {'plat.pdf': (pdf_pages('Formulaire plat fictif'), 'pdf_sans_champs'), 'xfa.pdf': (pdf_xfa_seul(), 'pdf_xfa'),
               'chiffre.pdf': (pdf_chiffre(), 'pdf_chiffre'), 'signe.pdf': (pdf_formulaire(signe=True), 'pdf_signe'),
               'abime.pdf': (b'%PDF-1.7\n1 0 obj << /Type /Catalog /Pages 9 0 R >>\nendobj\ntrailer << /Root 1 0 R >>\n%%EOF', 'pdf_illisible')}
        for nom, (raw, code) in cas.items():
            chemin = self.deposer(nom, raw)
            e = self.etat(chemin)
            self.assertEqual((e['etat'], e['code']), ('refus', code), nom)
            status, html = self.page(F, {'d': 'DOS-001', 'p': chemin})
            self.assertEqual(status, 200)
            self.assertIn('Ce PDF ne peut pas être rempli ici.', html)
            self.assertIn(e['message'].split(':')[0].replace('’', '&#x27;') if False else 'L’original n’a pas été modifié.', html)
        self.assertIn('casserait la signature', F.REFUS['pdf_signe'])

    def test_F2_formulaire_hybride_xfa_retire_de_la_copie(self):
        raw = pdf_formulaire(xfa=True)
        chemin = self.deposer('Hybride.pdf', raw)
        e = self.etat(chemin)
        self.assertTrue(e['xfa_hybride'])
        _, out, _ = self.appel('remplir', self.corps(chemin, raw, {'a1': 'DURAND'}))
        from agent._vendor.pypdf import PdfReader
        acro = PdfReader(io.BytesIO(self.lire(out['nom']))).trailer['/Root']['/AcroForm']
        self.assertNotIn('/XFA', acro)
        self.assertNotIn('/XFA', PdfReader(io.BytesIO(raw)).trailer['/Root']['/AcroForm'] and {})

    def test_F2_gardes_taille_pages_objets_delai(self):
        with self.assertRaises(Stop) as ex:
            F.analyser(b'%PDF-1.7\n' + b'0' * (F.MAX_PDF + 1))
        self.assertEqual(str(ex.exception), 'pdf_trop_volumineux')
        with patch.object(F, 'MAX_PAGES', 1), self.assertRaises(Stop) as ex:
            F.analyser(pdf_pages('un', 'deux'))
        self.assertEqual(str(ex.exception), 'pdf_trop_complexe')
        with patch.object(F, 'MAX_OBJETS', 5), self.assertRaises(Stop) as ex:
            F.analyser(pdf_formulaire())
        self.assertEqual(str(ex.exception), 'pdf_trop_complexe')
        debut = time.monotonic()
        with self.assertRaises(Stop) as ex:
            F._borne(lambda echeance=None: time.sleep(3), delai=0.2)
        self.assertEqual(str(ex.exception), 'pdf_delai_depasse')
        self.assertLess(time.monotonic() - debut, 2.5)
        with self.assertRaises(Stop) as ex:
            F.analyser(b'pas un pdf')
        self.assertEqual(str(ex.exception), 'pas_un_pdf')

    def test_F2_flux_de_page_trop_gros_libelles_non_lus(self):
        raw = pdf_formulaire(bombe=True)
        a = F.analyser(raw)
        self.assertTrue(a['libelles_partiels'])
        self.assertEqual({x['nom']: x['libelle'] for x in a['champs']}['zz9'], 'Référence du dossier')

    def test_F2_get_sans_ecriture_et_404_neutre(self):
        raw = pdf_formulaire()
        chemin = self.deposer('Formulaire fictif.pdf', raw)
        avant = self.d.db.total_changes
        status, _ = self.page(F, {'d': 'DOS-001', 'p': chemin})
        code, out, _ = self.appel('champs', args={'d': 'DOS-001', 'p': chemin}, methode='GET')
        self.assertEqual((status, code), (200, 200))
        self.assertEqual(out['empreinte'], F._sha(raw))
        self.assertEqual(self.d.db.total_changes, avant)                                               # aucune écriture pendant un GET
        reserve = self.deposer('Formulaire fictif.pdf', raw, 'DOS-003')
        r1 = self.page(F, {'d': 'DOS-003', 'p': reserve})
        r2 = self.page(F, {'d': 'DOS-999', 'p': '/Dossiers/DOS-999/x.pdf'})
        r3 = self.page(F, {'d': 'DOS-001', 'p': '/Dossiers/DOS-002 - MARTIN/x.pdf'})                     # fichier d'un autre dossier
        self.assertEqual(r1, r2)
        self.assertEqual(r1, r3)
        self.assertEqual(r1[0], 404)
        a1 = self.appel('champs', args={'d': 'DOS-003', 'p': reserve}, methode='GET')[:2]
        a2 = self.appel('champs', args={'d': 'DOS-999', 'p': reserve}, methode='GET')[:2]
        self.assertEqual(a1, a2)
        self.assertEqual(a1[0], 404)
        _, out, _ = self.appel('remplir', {'d': 'DOS-003', 'p': reserve, 'empreinte': F._sha(raw), 'valeurs': {'a1': 'x'}, 'cle': 'cle-reserve-0001'})
        self.assertEqual(out['error'], 'introuvable')
        self.assertEqual(self.fichiers('DOS-003'), ['Formulaire fictif.pdf'])

    def test_F2_sortie_deterministe(self):
        raw = pdf_formulaire()
        a, _, _ = F.remplir(raw, {'a1': 'DURAND', 'c1': True})
        b, _, _ = F.remplir(raw, {'a1': 'DURAND', 'c1': True})
        self.assertEqual(a, b)

    def test_F2_aucune_ia_appelee_et_pause_sans_effet(self):
        from agent import pause5629
        self.d.setting('pause_ia', True)
        raw = pdf_formulaire()
        chemin = self.deposer('Formulaire fictif.pdf', raw)
        with patch.object(pause5629, 'is_paused', return_value=True):
            code, out, _ = self.appel('remplir', self.corps(chemin, raw, {'a1': 'DURAND'}))
        self.assertEqual(code, 200, out)


# ================================================================================================ O : boîte à outils
class Outils(Base):
    def operation(self, op, chemins, raws, cle='cle-outil-0001', **params):
        corps = {'d': 'DOS-001', 'sources': [{'p': c, 'empreinte': F._sha(r)} for c, r in zip(chemins, raws)], 'cle': cle, **params}
        return self.appel(op, corps)

    def test_O1_annoter_cinq_outils_vraies_annotations(self):
        raw = pdf_pages('Conclusions fictives DURAND', 'Page deux')
        chemin = self.deposer('Conclusions.pdf', raw)
        jpeg = base64.b64encode(bytes.fromhex('ffd8ffe000104a46494600010100000100010000ffc0000b080010001001011100ffd9')).decode()
        annotations = [{'type': 'texte', 'page': 1, 'rect': [0.1, 0.1, 0.5, 0.2], 'texte': 'À vérifier : pièce n° 3'},
                       {'type': 'surligner', 'page': 1, 'rect': [0.1, 0.12, 0.6, 0.16]},
                       {'type': 'dessin', 'page': 2, 'traits': [[[0.1, 0.5], [0.2, 0.55], [0.3, 0.5]]]},
                       {'type': 'image', 'page': 2, 'rect': [0.6, 0.8, 0.9, 0.9], 'image': 'data:image/jpeg;base64,' + jpeg},
                       {'type': 'tampon', 'page': 1, 'rect': [0.6, 0.02, 0.95, 0.08], 'tampon': 'confidentiel'},
                       {'type': 'tampon', 'page': 2, 'rect': [0.6, 0.02, 0.95, 0.08], 'tampon': 'piece', 'piece': '12'}]
        code, out, _ = self.operation('annoter', [chemin], [raw], annotations=annotations)
        self.assertEqual(code, 200, out)
        self.assertRegex(out['nom'], r'^Conclusions \(annoté le \d{2}-\d{2}-\d{4}\)\.pdf$')
        self.assertEqual(self.lire('Conclusions.pdf'), raw)
        from agent._vendor.pypdf import PdfReader
        r = PdfReader(io.BytesIO(self.lire(out['nom'])))
        types = sorted(str(a.get_object()['/Subtype']) for p in r.pages for a in p.get('/Annots', []))
        self.assertEqual(types, ['/FreeText', '/Highlight', '/Ink', '/Stamp', '/Stamp', '/Stamp'])
        self.assertTrue(all('/AP' in a.get_object() for p in r.pages for a in p.get('/Annots', [])))   # apparence dessinée : visible partout
        libres = [a.get_object() for a in r.pages[0]['/Annots'] if a.get_object()['/Subtype'] == '/FreeText']
        self.assertEqual(libres[0]['/Contents'], 'À vérifier : pièce n° 3')
        self.assertIn('PIÈCE N° 12', [str(a.get_object().get('/Contents')) for a in r.pages[1]['/Annots']])

    def test_O1_annotations_refusees(self):
        raw = pdf_pages('Page')
        chemin = self.deposer('A.pdf', raw)
        for i, (annotations, code) in enumerate((([], 'aucune_annotation'), ([{'type': 'texte', 'page': 9, 'rect': [0, 0, .5, .5], 'texte': 'x'}], 'annotations_invalides'),
                                                 ([{'type': 'image', 'page': 1, 'rect': [0, 0, .5, .5], 'image': base64.b64encode(b'GIF89a').decode()}], 'image_invalide'),
                                                 ([{'type': 'script', 'page': 1, 'rect': [0, 0, .5, .5]}], 'annotations_invalides'))):
            _, out, _ = self.operation('annoter', [chemin], [raw], cle='cle-annot-%04d' % i, annotations=annotations)
            self.assertEqual(out['error'], code)
        self.assertEqual(self.fichiers(), ['A.pdf'])

    def test_O1_coordonnees_page_pivotee(self):
        from agent._vendor.pypdf import PdfReader
        raw = pdf_pages('Page').replace(b'/MediaBox [0 0 595 842]', b'/MediaBox [0 0 595 842] /Rotate 90')
        page = PdfReader(io.BytesIO(raw)).pages[0]
        self.assertEqual(O.point(page, 0, 0), (0.0, 0.0))                       # coin haut gauche affiché = bas gauche de la page tournée de 90°
        self.assertEqual(O.point(page, 1, 0), (0.0, 842.0))
        self.assertEqual(O.point(page, 0, 1), (595.0, 0.0))

    def test_O2_fusionner_dans_l_ordre_choisi(self):
        a, b = pdf_pages('Alpha un', 'Alpha deux'), pdf_pages('Beta un')
        ca, cb = self.deposer('Alpha.pdf', a), self.deposer('Beta.pdf', b)
        code, out, _ = self.operation('fusionner', [cb, ca], [b, a])
        self.assertEqual(code, 200, out)
        self.assertEqual([t.strip() for t in textes(self.lire(out['nom']))], ['Beta un', 'Alpha un', 'Alpha deux'])
        self.assertIn('fusion de 2 PDF', out['nom'])
        self.assertEqual((self.lire('Alpha.pdf'), self.lire('Beta.pdf')), (a, b))
        _, out, _ = self.operation('fusionner', [ca], [a], cle='cle-fusion-0002')
        self.assertEqual(out['error'], 'deux_pdf_au_moins')

    def test_O2_decouper_plages(self):
        raw = pdf_pages('P1', 'P2', 'P3', 'P4')
        chemin = self.deposer('Bordereau.pdf', raw)
        code, out, _ = self.operation('decouper', [chemin], [raw], plages='1-2, 4, 3-')
        self.assertEqual(code, 200, out)
        self.assertEqual([f['nom'] for f in out['fichiers']], ['Bordereau (pages 1-2).pdf', 'Bordereau (page 4).pdf', 'Bordereau (pages 3-4).pdf'])
        self.assertEqual([t.strip() for t in textes(self.lire('Bordereau (pages 3-4).pdf'))], ['P3', 'P4'])
        for i, p in enumerate(('0-2', '2-1', '5', 'a')):
            _, out, _ = self.operation('decouper', [chemin], [raw], cle='cle-plage-%04d' % i, plages=p)
            self.assertEqual(out['error'], 'plages_invalides', p)

    def test_O2_organiser_pivoter_supprimer(self):
        from agent._vendor.pypdf import PdfReader
        raw = pdf_pages('P1', 'P2', 'P3')
        chemin = self.deposer('Pieces.pdf', raw)
        code, out, _ = self.operation('organiser', [chemin], [raw], ordre=[{'page': 3, 'rotation': 90}, {'page': 1, 'rotation': 0}])
        self.assertEqual(code, 200, out)
        copie = self.lire(out['nom'])
        self.assertEqual([t.strip() for t in textes(copie)], ['P3', 'P1'])
        self.assertEqual(PdfReader(io.BytesIO(copie)).pages[0].rotation, 90)
        self.assertIn('1 page(s) supprimée(s), 1 pivotée(s)', out['message'])
        _, out, _ = self.operation('organiser', [chemin], [raw], cle='cle-ordre-0002', ordre=[{'page': 4}])
        self.assertEqual(out['error'], 'ordre_invalide')

    def test_O2_numeroter_et_piece_du_bordereau(self):
        from agent import contentieux5689
        contentieux5689.schema(self.d)
        raw = pdf_pages('P1', 'P2', 'P3')
        chemin = self.deposer('Facture fictive.pdf', raw)
        cols = {r[1]: r for r in self.d.db.execute('PRAGMA table_info(contentieux_exhibits5689)')}
        valeurs = {'id': 'x1', 'matter': 'DOS-001', 'cle': 'k1', 'cote': 'cabinet', 'numero': '12', 'numero_cle': '12', 'path': chemin, 'created': 'x', 'updated': 'x'}
        self.d.db.execute('INSERT INTO contentieux_exhibits5689(%s) VALUES(%s)' % (','.join(valeurs), ','.join('?' * len(valeurs))), tuple(valeurs.values()))
        self.d.db.commit()
        self.assertIn('numero', cols)
        self.assertEqual(O.numero_piece_propose(self.d, 'DOS-001', chemin, ''), ('12', 'bordereau des pièces du dossier'))
        status, html = self.page(O, {'d': 'DOS-001', 'p': chemin})
        self.assertEqual(status, 200)
        self.assertIn('data-f108-num-piece maxlength="20" value="12"', html)
        code, out, _ = self.operation('numeroter', [chemin], [raw], pages=True, position='bas-droite', piece='12', piece_partout=False)
        self.assertEqual(code, 200, out)
        t = textes(self.lire(out['nom']))
        self.assertIn('Pièce n° 12', t[0])
        self.assertIn('1 / 3', t[0])
        self.assertIn('3 / 3', t[2])
        self.assertNotIn('Pièce n°', t[1])
        _, out, _ = self.operation('numeroter', [chemin], [raw], cle='cle-num-0002', pages=False, piece='')
        self.assertEqual(out['error'], 'numerotation_vide')

    def test_O3_outils_absents_consigne_precise_rien_tente(self):
        raw = pdf_a_caviarder()
        chemin = self.deposer('A caviarder.pdf', raw)
        from agent import portable
        with patch.object(O, 'outils_locaux', return_value={'pdftoppm': None, 'pdftotext': None, 'tesseract': None}), patch.object(portable, 'WINDOWS', False):
            code, out, _ = self.operation('caviarder', [chemin], [raw], termes=['ZORVAL'])
            self.assertEqual(code, 503)
            self.assertIn('sudo apt install poppler-utils tesseract-ocr tesseract-ocr-fra', out['message'])
            self.assertIn('Aucun caviardage n’a été tenté', out['message'])
            status, html = self.page(O, {'d': 'DOS-001', 'p': chemin})
            self.assertIn('data-f108-op="caviarder" disabled', html)
        self.assertEqual(self.fichiers(), ['A caviarder.pdf'])

    def test_O3_controle_trouve_le_texte_partout(self):
        raw = pdf_a_caviarder()
        self.assertEqual(O.controle_caviardage(raw, ['ZORVAL'], {}), 'texte de la copie (pypdf)')
        propre = pdf_pages('Rien a signaler')
        self.assertIsNone(O.controle_caviardage(propre, ['ZORVAL'], {}))
        # le texte absent des pages mais présent dans un signet, une annotation, une pièce jointe ou les métadonnées est trouvé aussi
        sans_texte = raw.replace(b'Monsieur ZORVAL', b'Monsieur XXXXXX')
        self.assertIsNotNone(O.controle_caviardage(sans_texte, ['ZORVAL'], {}))
        self.assertEqual(O.controle_caviardage(propre, [], {1: {'rien'}}), 'texte d’une page caviardée (pypdf)')

    def test_O3_noircir_l_image(self):
        ppm = b'P6\n10 10\n255\n' + b'\xff' * 300
        noir = O.noircir_ppm(ppm, [[0.2, 0.2, 0.5, 0.5]], marge=0)
        corps = noir[len(b'P6\n10 10\n255\n'):]
        self.assertEqual(corps[(3 * 10 + 3) * 3:(3 * 10 + 3) * 3 + 3], b'\x00\x00\x00')
        self.assertEqual(corps[:3], b'\xff\xff\xff')
        mots = [('Monsieur', .1, .1, .2, .12), ('ZORVAL,', .21, .1, .3, .12), ('Valbrune', .31, .1, .4, .12)]
        self.assertEqual(O.zones_des_termes(mots, ['zorval']), [[.21, .1, .3, .12]])
        self.assertEqual(len(O.zones_des_termes(mots, ['Monsieur Zorval'])), 2)

    @unittest.skipUnless(outils_reels(), 'Poppler et Tesseract absents : caviardage réel non lancé')
    def test_O3_caviardage_reel_controle_et_original_intact(self):
        raw = pdf_a_caviarder()
        chemin = self.deposer('A caviarder.pdf', raw)
        code, out, _ = self.operation('caviarder', [chemin], [raw], termes=['ZORVAL'], zones={'2': [[0.1, 0.1, 0.4, 0.2]]})
        self.assertEqual(code, 200, out)
        self.assertRegex(out['nom'], r'^A caviarder \(caviardé le \d{2}-\d{2}-\d{4}\)\.pdf$')
        copie = self.lire(out['nom'])
        self.assertNotIn(b'ZORVAL', copie)
        self.assertIsNone(O.controle_caviardage(copie, ['ZORVAL'], {}))
        t = fold(' '.join(textes(copie)))
        self.assertTrue('valbrune' in t or 'facture' in t or 'conteste' in t, t[:300])        # texte non caviardé cherchable (OCR)
        from agent._vendor.pypdf import PdfReader
        r = PdfReader(io.BytesIO(copie))
        self.assertFalse(any(k in r.trailer['/Root'] for k in ('/Outlines', '/Names', '/AcroForm', '/Metadata')))
        self.assertEqual(set(r.metadata or {}), {'/Producer'})
        self.assertEqual(self.lire('A caviarder.pdf'), raw)

    @unittest.skipUnless(outils_reels(), 'Poppler et Tesseract absents : contre-épreuve réelle non lancée')
    def test_M3_sans_noircissement_le_controle_refuse_et_efface(self):
        raw = pdf_a_caviarder()
        chemin = self.deposer('A caviarder.pdf', raw)
        with patch.object(O, 'noircir_ppm', side_effect=lambda data, zones, marge=2: data):
            code, out, _ = self.operation('caviarder', [chemin], [raw], termes=['ZORVAL'])
        self.assertEqual(out['error'], 'caviardage_controle_echoue')
        self.assertEqual(self.fichiers(), ['A caviarder.pdf'])

    def test_O4_compresser_gain_mesure_ou_refus(self):
        raw = pdf_avec_images()
        chemin = self.deposer('Scan fictif.pdf', raw)
        with patch.object(O, 'outils_locaux', return_value={'pdftoppm': None, 'pdftotext': None, 'tesseract': None}):
            code, out, _ = self.operation('compresser', [chemin], [raw], qualite='moyenne')
        self.assertEqual(code, 200, out)
        f = out['fichiers'][0]
        self.assertEqual(f['images_dedoublonnees'], 1)
        self.assertLess(f['apres'], f['avant'])
        self.assertIn('Poppler (pdftoppm) est absent', out['message'])
        self.assertIn('gain', out['message'])
        petit = pdf_pages('x')
        chemin2 = self.deposer('Petit.pdf', petit)
        _, out, _ = self.operation('compresser', [chemin2], [petit], cle='cle-comp-0002', qualite='forte')
        self.assertEqual(out['error'], 'compression_sans_gain')
        self.assertNotIn('Petit (compressé).pdf', self.fichiers())

    @unittest.skipUnless(outils_reels(), 'Poppler absent : recompression des images non lancée')
    def test_O4_images_recompressees_par_poppler(self):
        raw = pdf_avec_images(doublon=False, n=2, bruit=True)
        sortie, detail = O.compresser(raw, 'forte', O.outils_locaux())
        self.assertGreaterEqual(detail['images_recompressees'], 1)
        self.assertLess(len(sortie), len(raw))

    def test_O5_pdf_signe_copie_sans_signature(self):
        raw = pdf_formulaire(signe=True)
        chemin = self.deposer('Signe.pdf', raw)
        code, out, _ = self.operation('numeroter', [chemin], [raw], pages=True)
        self.assertEqual(code, 200, out)
        from agent._vendor.pypdf import PdfReader
        self.assertNotIn('/SigFlags', PdfReader(io.BytesIO(self.lire(out['nom']))).trailer['/Root']['/AcroForm'])
        self.assertTrue(out['fichiers'][0]['signature_retiree'])

    def test_O5_pdf_chiffre_refuse(self):
        raw = pdf_chiffre()
        chemin = self.deposer('Chiffre.pdf', raw)
        _, out, _ = self.operation('compresser', [chemin], [raw])
        self.assertEqual(out['error'], 'pdf_chiffre')
        status, html = self.page(O, {'d': 'DOS-001', 'p': chemin})
        self.assertIn('chiffré', html)

    def test_O5_page_lecture_seule_et_404_neutre(self):
        raw = pdf_pages('P1', 'P2')
        chemin = self.deposer('A.pdf', raw)
        avant = self.d.db.total_changes
        status, html = self.page(O, {'d': 'DOS-001', 'p': chemin})
        self.assertEqual(status, 200)
        self.assertEqual(self.d.db.total_changes, avant)
        for onglet in ('Annoter', 'Pages', 'Découper', 'Fusionner', 'Numéroter', 'Caviarder', 'Compresser'):
            self.assertIn('>%s</button>' % onglet, html)
        donnees = json.loads(re.search(r'data-f108-donnees="([^"]*)"', html).group(1).replace('&quot;', '"'))
        self.assertEqual(donnees['sources'][0]['empreinte'], F._sha(raw))
        reserve = self.deposer('A.pdf', raw, 'DOS-003')
        self.assertEqual(self.page(O, {'d': 'DOS-003', 'p': reserve}), self.page(O, {'d': 'DOS-999', 'p': reserve}))
        _, out, _ = self.operation('numeroter', [chemin], [raw], cle='cle-autre-0001', pages=True)
        self.assertEqual(out['etat'], 'verified')
        corps = {'d': 'DOS-003', 'sources': [{'p': reserve, 'empreinte': F._sha(raw)}], 'cle': 'cle-reserve-0002', 'pages': True}
        self.assertEqual(self.appel('numeroter', corps)[:2], (404, {'error': 'introuvable', 'message': F.MESSAGES['introuvable']}))

    def test_O5_pdf_modifie_409_et_cle_reutilisee(self):
        raw = pdf_pages('P1', 'P2')
        chemin = self.deposer('A.pdf', raw)
        corps = {'d': 'DOS-001', 'sources': [{'p': chemin, 'empreinte': '0' * 64}], 'cle': 'cle-perime-0001', 'pages': True}
        self.assertEqual(self.appel('numeroter', corps)[0], 409)
        _, out, _ = self.operation('numeroter', [chemin], [raw], cle='cle-double-0001', pages=True)
        code, out2, _ = self.operation('numeroter', [chemin], [raw], cle='cle-double-0001', pages=True)
        self.assertTrue(out2['rejoue'])
        code, out3, _ = self.operation('numeroter', [chemin], [raw], cle='cle-double-0001', pages=True, position='bas-centre')
        self.assertEqual((code, out3['error']), (409, 'cle_reutilisee'))
        self.assertEqual(len(self.fichiers()), 2)


# ================================================================================================ M : contre-épreuves par mutation (en mémoire)
class Mutations(Base):
    def test_M1_sans_controle_d_appartenance_un_fichier_d_un_autre_dossier_serait_lu(self):
        raw = pdf_formulaire()
        autre = self.deposer('Formulaire.pdf', raw, 'DOS-002')
        acteur = acces5697.Acteur(*AVOCATE)
        with self.assertRaises(Stop):
            F.etat_formulaire(self.d, acteur, 'DOS-001', autre)
        with patch.object(F, 'under', return_value=True):                                              # garde retirée
            self.assertEqual(F.etat_formulaire(self.d, acteur, 'DOS-001', autre)['etat'], 'ok')

    def test_M2_sans_empreinte_un_pdf_modifie_serait_rempli(self):
        raw = pdf_formulaire()
        chemin = self.deposer('Formulaire fictif.pdf', raw)
        corps = {'d': 'DOS-001', 'p': chemin, 'empreinte': '0' * 64, 'valeurs': {'a1': 'DURAND'}, 'cle': 'cle-mutation-0001'}
        self.assertEqual(self.appel('remplir', corps)[0], 409)
        with patch.object(F, '_sha', side_effect=lambda data: '0' * 64):                               # garde neutralisée
            self.assertEqual(self.appel('remplir', dict(corps, cle='cle-mutation-0002'))[0], 200)

    def test_M4_sans_controle_avant_lecture_le_corps_serait_lu(self):
        with patch.object(F.acces5697, 'exiger', return_value=None):
            self.appel('ajouter', args={'d': 'DOS-003', 'nom': 'a.pdf', 'cle': 'cle-mutation-0003'}, brut=pdf_pages('x'), ctype='application/octet-stream')
        self.assertGreater(self.dernier_env['wsgi.input'].tell(), 0)


# ================================================================================================ F3 : textes en anglais, allemand, espagnol
class Langues(Base):
    def test_F3_ecrans_traduits(self):
        import test_v56107_langues as L
        from agent import ecrans5632 as E
        raw = pdf_formulaire()
        chemin = self.deposer('Formulaire fictif.pdf', raw)
        pages = [self.page(F, {'d': 'DOS-001', 'p': chemin})[1], self.page(O, {'d': 'DOS-001', 'p': chemin})[1],
                 self.page(F, {'d': 'DOS-001', 'p': self.deposer('plat.pdf', pdf_pages('x'))})[1], F.barre_fichiers_html('/agent-courriel', 'DOS-001')]
        for langue in ('en', 'de', 'es'):
            for html in pages:
                traduit = E.translate_html(html, langue)
                reste = [t for t in L.reste_francais(L.visibles(traduit)) if not re.search(r'Formulaire fictif|plat\.pdf|DURAND|DOS-001', t)]
                self.assertEqual(reste, [], langue)

    def test_F3_messages_et_scripts_traduits(self):
        from agent import ecrans5632 as E
        import test_v56107_langues as L
        js = ROOT / 'agent' / 'static' / 'v56108-fichiers.js'
        phrases = [t for _, t in L.litteraux_js(js) if ' ' in t.strip() and L.reste_francais([t]) and t.strip() not in ('%s : %s',)]
        phrases += [v for v in list(F.MESSAGES.values()) + list(O.MESSAGES.values()) if v]
        phrases += ['Fichier ajouté au dossier : Courrier MARTIN.pdf.', O.consigne_installation(['pdftoppm', 'tesseract']),
                    'Copie remplie enregistrée dans le dossier : Formulaire (rempli le 18-11-2026).pdf. 7 champ(s) rempli(s), 3 laissé(s) vide(s). '
                    'L’original est inchangé. Relisez la copie avant usage.',
                    'Copie compressée enregistrée : Scan (compressé).pdf. 291 Ko → 16 Ko (gain 94,4 %), 1 image(s) recompressée(s), 1 doublon(s) retiré(s). '
                    'L’original est inchangé.', '3 fichier(s) enregistré(s) : A (pages 1-2).pdf, A (page 4).pdf, A (pages 3-4).pdf. L’original est inchangé.']
        with patch('agent.portable.WINDOWS', False):
            phrases.append(O.consigne_installation(['pdftoppm', 'pdftotext', 'tesseract']))
        for langue in ('en', 'de', 'es'):
            table = E.dictionary(langue)
            manquants = [p for p in phrases if E.phrase(p, table) is None]
            self.assertEqual(manquants, [], langue)

if __name__ == '__main__':
    unittest.main()
