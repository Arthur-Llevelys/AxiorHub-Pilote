"""5.6.106 (lot D) : densité d'affichage de toutes les pages et identité du cabinet (logo carré et nom) en tête de la barre latérale.

- D1 : échelle compacte par défaut à partir de variables ``--ax-*`` définies une fois (``static/v56106-densite.css``), documentées en tête
  du fichier pour les autres lots ; le mode « confortable » garde les valeurs de la 5.6.105 (aucune règle de mise en page hors
  ``body[data-densite=compacte]``) ; aucune règle propre à la page Courriels.
- D2 : corrections génériques (libellé, liste et bouton sur une ligne ; barres de filtres ; groupes de boutons ; titre et onglets).
- D3 : réglage par personne dans Mon compte, posé en ``<body data-densite>`` ; présentation précédente toujours « confortable » ; version
  attendue (409), même choix renvoyé, clé d'idempotence ; aucune écriture pendant l'affichage.
- D4 : mobile (≤ 850 px) : cibles de 40 px au moins ; contraste AA de l'identité en clair et en sombre ; focus visible du dépôt.
- D5 : logo du cabinet : PNG, JPEG, WebP d'après la signature des octets, 1 Mo, dimensions lues dans l'en-tête, SVG refusé, rapport 2:1
  au plus (recadré au centre) ; rangé dans la base, servi avec son type, ``nosniff`` et cache privé ; remplacer et retirer ; administrateur
  seulement ; nom et logo en tête de la barre latérale sur toutes les pages, pastille aux initiales sans logo.
Données fictives uniquement (« Cabinet DURAND Avocats », images générées ici).
"""
import base64
import io
import json
from pathlib import Path
import re
import struct
import unittest
from unittest.mock import patch
import zlib

from agent import identite56106 as idt, interface56100
from agent.common import Stop
import test_v530 as t530

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / 'agent' / 'static' / 'v56106-densite.css').read_text(encoding='utf-8')
NOM = 'Cabinet DURAND Avocats'


def png(largeur=64, hauteur=64):
    lignes = b''.join(b'\x00' + bytes((23, 50, 77)) * largeur for _ in range(hauteur))

    def morceau(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + morceau(b'IHDR', struct.pack('>IIBBBBB', largeur, hauteur, 8, 2, 0, 0, 0)) + morceau(b'IDAT', zlib.compress(lignes))
            + morceau(b'IEND', b''))


def jpeg(largeur=120, hauteur=100):
    """En-tête JPEG minimal (APP0 puis SOF0) : assez pour lire les dimensions, comme le fait le serveur."""
    app0 = b'\xff\xe0' + struct.pack('>H', 16) + b'JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
    sof0 = b'\xff\xc0' + struct.pack('>HBHHB', 11, 8, hauteur, largeur, 1) + b'\x01\x11\x00'
    return b'\xff\xd8' + app0 + sof0 + b'\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00' + b'\x00' * 16 + b'\xff\xd9'


def webp_vp8(largeur=80, hauteur=80):
    donnees = b'\x00\x00\x00' + b'\x9d\x01\x2a' + struct.pack('<HH', largeur, hauteur) + b'\x00' * 10
    return b'RIFF' + struct.pack('<I', 4 + 8 + len(donnees)) + b'WEBP' + b'VP8 ' + struct.pack('<I', len(donnees)) + donnees


def webp_vp8l(largeur=50, hauteur=60):
    w, h = largeur - 1, hauteur - 1
    bits = w | (h << 14)
    donnees = b'\x2f' + struct.pack('<I', bits) + b'\x00' * 8
    return b'RIFF' + struct.pack('<I', 4 + 8 + len(donnees)) + b'WEBP' + b'VP8L' + struct.pack('<I', len(donnees)) + donnees


def webp_vp8x(largeur=300, hauteur=200):
    donnees = b'\x00\x00\x00\x00' + (largeur - 1).to_bytes(3, 'little') + (hauteur - 1).to_bytes(3, 'little')
    return b'RIFF' + struct.pack('<I', 4 + 8 + len(donnees)) + b'WEBP' + b'VP8X' + struct.pack('<I', len(donnees)) + donnees


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><script>alert(1)</script></svg>'


def contraste(a, b):
    def lum(h):
        r, g, b_ = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
        f = lambda c: c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4
        return .2126 * f(r) + .7152 * f(g) + .0722 * f(b_)
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + .05) / (lb + .05)


def regles(css):
    """(sélecteur, déclarations) des règles de premier niveau et des blocs @media (un niveau)."""
    sans_commentaires = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    out = []
    for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', sans_commentaires):
        sel = m.group(1).strip()
        sel = re.sub(r'^@media[^{]*\{', '', sel).strip() if '@media' in sel else sel
        out.append((sel, m.group(2).strip()))
    return out


# ------------------------------------------------------------------------------------------------ feuille de style
class Feuille(unittest.TestCase):
    def test_D1_variables_definies_une_fois_et_documentees(self):
        entete = CSS.split('*/', 1)[0]
        for mot in ('body[data-densite', '--ax-texte', '--ax-controle', '--ax-entete', '--ax-barre-laterale', '--ax-menu-element', '--ax-goutiere', '--ax-titre-page'):
            self.assertIn(mot, entete, mot)
        compacte = re.search(r'body\[data-densite=compacte\] \{([^}]*)\}', CSS).group(1)
        attendu = {'--ax-texte': '14px', '--ax-texte-petit': '13px', '--ax-titre-page': '22px', '--ax-controle': '32px', '--ax-controle-petit': '28px',
                   '--ax-entete': '48px', '--ax-barre-laterale': '208px', '--ax-menu-element': '36px'}
        for nom, valeur in attendu.items():
            self.assertRegex(compacte, re.escape(nom) + r':\s*' + re.escape(valeur), nom)
        self.assertEqual(len(re.findall(r'^body\[data-densite=compacte\] \{', CSS, flags=re.M)), 1)          # définies une seule fois
        self.assertIn('--ui-taille-corps: .875rem', CSS)                                                      # texte courant à 14 px (jetons Carbon)
        self.assertNotRegex(CSS, r'--ax-texte-petit:\s*1[0-2]px')                                              # jamais moins de 13 px

    def test_D1_confortable_garde_les_valeurs_actuelles(self):
        def hors_portee(css):
            fautes = []
            for sel, decl in regles(css):
                if sel.startswith('@') or not decl:
                    continue
                if 'data-densite=compacte' in sel:
                    continue
                # seules exceptions : variables, identité du cabinet (.id106), dépôt du logo (.d106), bande titre et onglets (posée par le script en compacte)
                if all(p.strip().startswith('--') for p in decl.split(';') if p.strip()):
                    continue
                if re.search(r'\.(id106|d106)|\.ax106-bande|\[data-d106', sel):
                    continue
                fautes.append(sel)
            return fautes
        self.assertEqual(hors_portee(CSS), [])
        # mutation : une règle de mise en page non bornée à la densité compacte serait signalée
        self.assertEqual(hors_portee(CSS + '\n#ws-main .ax-btn { min-height: 30px; }'), ['#ws-main .ax-btn'])

    def test_D1_aucune_regle_de_la_page_courriels(self):
        self.assertNotRegex(re.sub(r'/\*.*?\*/', '', CSS, flags=re.S), r'\.bx56(25|28|106)')

    def test_D1_feuille_dans_le_paquet_avant_les_feuilles_des_autres_lots(self):
        from agent.shell501 import BUNDLE
        self.assertIn('v56106-densite.css', BUNDLE)
        autres = [n for n in BUNDLE if n.startswith('v56106-') and n != 'v56106-densite.css']
        self.assertTrue(all(BUNDLE.index('v56106-densite.css') < BUNDLE.index(n) for n in autres))
        paquet = (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8')
        self.assertIn('/* ==== v56106-densite.css ==== */\n' + CSS.rstrip(), paquet)                         # paquet régénéré (scripts/build-css.py)
        from agent.web440 import STATIC
        self.assertEqual(STATIC.get('/static/v56106-densite.js'), 'text/javascript')
        self.assertEqual(STATIC.get('/static/v56106-densite.css'), 'text/css')

    def test_D2_regles_generiques(self):
        compact = ''.join(s for s, _ in regles(CSS))
        self.assertIn('form:not(.m5-form):has(> label + :is(select, input:not([type=hidden])))', compact)          # « Trier par » : libellé, liste et bouton sur une ligne
        self.assertIn('.q51-filters, .d97-filtres, .ax100-filtres', compact)         # barres de filtres sur une ligne, repli propre
        self.assertIn('.ax-btn', compact)
        self.assertIn('.page-title.ax106-bande', compact)                             # titre de page et onglets sur une bande
        self.assertRegex(CSS, r'\.ax-btn[^{]*\{[^}]*min-height: var\(--ax-controle\)')
        js = (ROOT / 'agent' / 'static' / 'v56106-densite.js').read_text(encoding='utf-8')
        self.assertIn("dataset.densite === 'compacte'", js)
        self.assertNotIn('innerHTML', js)
        self.assertNotIn('eval(', js)

    def test_D4_mobile_cibles_de_40_px(self):
        bloc = re.search(r'@media \(max-width: 53\.125rem\), \(pointer: coarse\) \{(.*?)\n\}', CSS, flags=re.S).group(1)
        self.assertIn('--ax-controle: var(--ax-cible-tactile)', bloc)
        self.assertRegex(CSS, r'--ax-cible-tactile: 40px')
        desktop = re.search(r'@media \(min-width: 53\.1875rem\) \{', CSS)
        self.assertIsNotNone(desktop)                                                  # l'enveloppe compacte ne vaut que pour l'écran de bureau

    def test_D4_contraste_de_l_identite_en_clair_et_en_sombre(self):
        jetons = (ROOT / 'agent' / 'static' / 'v5689-carbon-jetons.css').read_text(encoding='utf-8')
        fonds = set(re.findall(r'--ui-nav:(#[0-9a-f]{6})', jetons))
        self.assertTrue(fonds)                                                        # barre latérale : graphite en clair et en sombre
        for fond in fonds:
            self.assertGreaterEqual(contraste('#ffffff', fond), 4.5, fond)          # nom du cabinet
            self.assertGreaterEqual(contraste('#c6c6c6', fond), 4.5, fond)          # marque AxiorHub en petit
        self.assertGreaterEqual(contraste('#ffffff', '#0043ce'), 4.5)               # initiales sur la pastille
        self.assertIn('.d106-fichier:focus-within { outline: 2px solid', CSS)       # focus visible du dépôt (champ fichier visuellement masqué)


# ------------------------------------------------------------------------------------------------ logo : contrôle des octets
class Octets(unittest.TestCase):
    def test_D5_types_lus_dans_la_signature_et_dimensions(self):
        self.assertEqual(idt.detecter(png(64, 48)), ('png', 64, 48))
        self.assertEqual(idt.detecter(jpeg(120, 100)), ('jpeg', 120, 100))
        self.assertEqual(idt.detecter(webp_vp8(80, 70)), ('webp', 80, 70))
        self.assertEqual(idt.detecter(webp_vp8l(50, 60)), ('webp', 50, 60))
        self.assertEqual(idt.detecter(webp_vp8x(300, 200)), ('webp', 300, 200))

    def test_D5_svg_gif_et_contenu_quelconque_refuses(self):
        for octets in (SVG, b'GIF89a' + b'\x00' * 40, b'%PDF-1.7\n', b'', b'\x89PNG\r\n\x1a\n' + b'\x00' * 4, b'\xff\xd8\xff\xd9'):
            with self.assertRaisesRegex(Stop, 'logo_type_refuse'):
                idt.valider(octets)
        with self.assertRaisesRegex(Stop, 'logo_type_refuse'):                     # type annoncé « image/png » : jamais cru
            idt.valider(idt._decoder('data:image/png;base64,' + base64.b64encode(SVG).decode()))

    def test_D5_taille_dimensions_et_rapport(self):
        with self.assertRaisesRegex(Stop, 'logo_trop_lourd'):
            idt.valider(png() + b'\x00' * idt.MAX_LOGO)
        with self.assertRaisesRegex(Stop, 'logo_trop_lourd'):
            idt._decoder(base64.b64encode(b'\x00' * (idt.MAX_LOGO + 10)).decode())
        with self.assertRaisesRegex(Stop, 'logo_dimensions'):
            idt.valider(png(16, 16))
        with self.assertRaisesRegex(Stop, 'logo_dimensions'):
            idt.valider(webp_vp8x(5000, 5000))
        with self.assertRaisesRegex(Stop, 'logo_non_carre'):
            idt.valider(png(300, 100))
        meta = idt.valider(png(150, 100))                                            # presque carré : accepté, recadré au centre à l'affichage
        self.assertEqual((meta['type'], meta['largeur'], meta['hauteur']), ('image/png', 150, 100))
        self.assertIn('object-fit: cover', CSS)

    def test_D5_initiales(self):
        self.assertEqual(idt.initiales(NOM), 'D')
        self.assertEqual(idt.initiales('MARTIN et BERNARD'), 'MB')
        self.assertEqual(idt.initiales('SCP ZORVAL & KERVILAC'), 'ZK')
        self.assertEqual(idt.initiales(''), 'C')


# ------------------------------------------------------------------------------------------------ pages, routes, réglages
class Pages(t530.Base):
    def setUp(self):
        super().setUp()
        idt.oublier()
        self.addCleanup(idt.oublier)

    def brut(self, chemin, methode='GET', corps=None, csrf='test-csrf', origin=None, query=''):
        donnees = json.dumps(corps).encode() if corps is not None else b''
        env = {'REQUEST_METHOD': methode, 'PATH_INFO': chemin, 'QUERY_STRING': query, 'HTTP_HOST': 'cabinet.example.test', 'HTTP_X_FORWARDED_PROTO': 'https',
               'wsgi.url_scheme': 'http', 'wsgi.input': io.BytesIO(donnees), 'CONTENT_LENGTH': str(len(donnees)),
               'HTTP_AUTHORIZATION': 'Basic ' + base64.b64encode(('admin:' + self.password).encode()).decode()}
        if corps is not None:
            env.update({'CONTENT_TYPE': 'application/json', 'HTTP_ORIGIN': origin or self.origin, 'HTTP_X_CSRF_TOKEN': csrf})
        res = {}

        def start(status, headers):
            res.update(status=status, headers=headers)
        res['body'] = b''.join(self.app(env, start))
        return res

    def poster(self, route, corps, **kw):
        r = self.brut('/api440/m56106/' + route, 'POST', corps, **kw)
        return r['status'], json.loads(r['body'].decode() or '{}')

    def profil(self):
        from agent import cabinet_profile5689 as profil
        return profil.lire(self.desk)

    def nommer(self, nom=NOM):
        from agent import cabinet_profile5689 as profil
        profil.enregistrer(self.desk, 'admin', 'administrateur', {'revision': self.profil()['revision'], 'nom_cabinet': nom})
        idt.oublier()

    def deposer(self, octets, cle='cle-logo-0001', revision=None):
        return self.poster('identite/logo', {'cle': cle, 'revision': self.profil()['revision'] if revision is None else revision,
                                             'donnees': 'data:image/png;base64,' + base64.b64encode(octets).decode()})

    def body(self, chemin='/aujourdhui', query=''):
        return self.brut(chemin, query=query)['body'].decode()

    # ---------------------------------------------------------------- D3 : densité
    def test_D3_compacte_par_defaut_sur_toutes_les_pages(self):
        for chemin in ('/aujourdhui', '/dossiers', '/production', '/planning', '/mon-compte'):
            page = self.body(chemin)
            self.assertRegex(page, r'<body[^>]*\sdata-densite="compacte"', chemin)
            self.assertEqual(page.count('data-densite='), 1, chemin)
            self.assertIn('/static/v56106-densite.js', page, chemin)

    def test_D3_reglage_par_personne_version_et_meme_choix(self):
        compte = self.body('/mon-compte')
        self.assertIn('id="d106-densite"', compte)
        self.assertIn('data-api="m56106/densite"', compte)
        self.assertIn('name="revision" value="defaut"', compte)
        statut, out = self.poster('densite', {'cle': 'cle-densite-0001', 'densite': 'confortable', 'revision': 'defaut'})
        self.assertTrue(statut.startswith('200'), out)
        self.assertRegex(self.body('/dossiers'), r'<body[^>]*\sdata-densite="confortable"')
        again = self.poster('densite', {'cle': 'cle-densite-0001', 'densite': 'confortable', 'revision': 'defaut'})   # réponse perdue : même clé, même réponse
        self.assertEqual(again[1], out)
        self.assertEqual(len(self.desk.db.execute("SELECT 1 FROM audit WHERE action='densite56106_reglage'").fetchall()), 1)
        autre = self.poster('densite', {'cle': 'cle-densite-0001', 'densite': 'compacte', 'revision': 'confortable'})
        self.assertTrue(autre[0].startswith('409'), autre)                            # même clé pour une autre demande
        perime = self.poster('densite', {'cle': 'cle-densite-0002', 'densite': 'compacte', 'revision': 'defaut'})
        self.assertTrue(perime[0].startswith('409'), perime)                          # page ouverte avant le changement
        meme = self.poster('densite', {'cle': 'cle-densite-0003', 'densite': 'confortable', 'revision': 'confortable'})
        self.assertTrue(meme[1].get('inchange'))
        inconnu = self.poster('densite', {'cle': 'cle-densite-0004', 'densite': 'minuscule', 'revision': 'confortable'})
        self.assertTrue(inconnu[0].startswith('400'), inconnu)
        sans_csrf = self.poster('densite', {'cle': 'cle-densite-0005', 'densite': 'compacte', 'revision': 'confortable'}, csrf='mauvais')
        self.assertFalse(sans_csrf[0].startswith('200'))
        sans_cle = self.poster('densite', {'densite': 'compacte', 'revision': 'confortable'})
        self.assertTrue(sans_cle[0].startswith('400'), sans_cle)
        self.assertRegex(self.body('/aujourdhui'), r'data-densite="confortable"')
        self.assertEqual(idt.densite_compte(self.f.c, 'quelqu-un-d-autre'), 'compacte')   # les autres comptes gardent le défaut

    def test_D3_presentation_precedente_toujours_confortable(self):
        interface56100.regler_cabinet(self.desk, False)
        idt.oublier()
        self.assertRegex(self.body('/aujourdhui'), r'<body[^>]*\sdata-densite="confortable"')
        # mutation : sans le contrôle de l'interrupteur, la présentation précédente deviendrait compacte
        with patch('agent.shell501.relire_separe', return_value=True):
            idt.oublier()
            self.assertRegex(self.body('/aujourdhui'), r'data-densite="compacte"')

    def test_D3_aucune_ecriture_pendant_l_affichage(self):
        import tempfile
        with tempfile.TemporaryDirectory() as vide:
            self.assertEqual(idt.densite_compte({'state_dir': vide}, 'admin'), 'compacte')
            self.assertEqual(idt.identite({'state_dir': vide}), {'nom': '', 'logo': None})
            self.assertEqual(list(Path(vide).iterdir()), [])                         # aucune base créée par une lecture
        self.body('/aujourdhui')
        self.body('/mon-compte')
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM settings WHERE key LIKE 'densite56106:%'").fetchone()[0], 0)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM identite56106_recus").fetchone()[0], 0)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM identite56106_logos").fetchone()[0], 0)

    def test_D3_base_illisible_rend_le_defaut(self):
        with patch('agent.identite56106._lecture', side_effect=__import__('sqlite3').OperationalError('verrou')):
            idt.oublier()
            self.assertEqual(idt.densite_compte(self.f.c, 'admin'), 'compacte')
            self.assertEqual(idt.identite(self.f.c), {'nom': '', 'logo': None})
            page = self.body('/aujourdhui')
        self.assertRegex(page, r'data-densite="compacte"')
        self.assertIn('ws-brand-lockup', page)                                        # marque d'origine, jamais une page en erreur

    # ---------------------------------------------------------------- D5 : identité dans la barre latérale
    def test_D5_sans_nom_ni_logo_marque_d_origine(self):
        page = self.body('/aujourdhui')
        self.assertIn('ws-brand-lockup', page)
        self.assertNotIn('data-id106', page)

    def test_D5_nom_et_pastille_aux_initiales_sur_toutes_les_pages(self):
        self.nommer()
        for chemin in ('/aujourdhui', '/dossiers', '/production', '/mon-compte'):
            page = self.body(chemin)
            barre = page[page.index('<aside id="ws-sidebar"'):page.index('<nav')]
            self.assertIn('data-id106', barre, chemin)
            self.assertIn('>D</span>', barre, chemin)                               # pastille aux initiales
            self.assertIn('class="id106-nom" title="Cabinet DURAND Avocats">Cabinet DURAND Avocats</span>', barre, chemin)
            self.assertIn('AxiorHub Pilote', barre, chemin)                          # marque dessous, en petit
            self.assertNotIn('ws-brand-lockup', barre, chemin)
        aujourdhui = self.body('/aujourdhui')
        principal = aujourdhui[aujourdhui.index('<main'):]
        self.assertNotIn('data-id106', principal)                                    # aucun bandeau d'identité dans la page Aujourd'hui
        self.assertIn('Aujourd’hui</h1>', principal)                                # le titre reste

    def test_D5_nom_echappe(self):
        self.nommer('Cabinet <b>ZORVAL</b> & associés')
        page = self.body('/aujourdhui')
        self.assertIn('Cabinet &lt;b&gt;ZORVAL&lt;/b&gt; &amp; associés', page)
        self.assertNotIn('<b>ZORVAL</b>', page)

    def test_D5_presentation_precedente_garde_la_marque(self):
        self.nommer()
        interface56100.regler_cabinet(self.desk, False)
        idt.oublier()
        page = self.body('/aujourdhui')
        self.assertIn('ws-brand-lockup', page)
        self.assertNotIn('data-id106', page)

    def test_D5_depot_service_remplacement_retrait(self):
        self.nommer()
        rev = self.profil()['revision']
        statut, out = self.deposer(png(96, 96))
        self.assertTrue(statut.startswith('200'), out)
        self.assertEqual(out['revision'], rev + 1)                                  # une révision du profil du cabinet (pas un second profil)
        self.assertEqual(self.profil()['revision'], rev + 1)
        r = self.brut('/api440/m56106/identite/logo', query='v=1')
        self.assertTrue(r['status'].startswith('200'), r['status'])
        entetes = {}
        for k, v in r['headers']:
            entetes.setdefault(k, []).append(v)
        self.assertEqual(entetes['Content-Type'], ['image/png'])
        self.assertEqual(entetes['X-Content-Type-Options'], ['nosniff'])
        self.assertTrue(all('no-store' in v or 'private' in v for v in entetes['Cache-Control']))
        self.assertIn('private, no-store', entetes['Cache-Control'])
        self.assertEqual(r['body'], png(96, 96))
        page = self.body('/dossiers')
        sha = out['logo']['sha256']
        self.assertIn('<img class="id106-marque" src="/agent-courriel/api440/m56106/identite/logo?v=%s" width="44" height="44" alt="">' % sha[:16], page)
        self.assertNotIn('id106-initiales', page[page.index('<aside'):page.index('<nav')])
        # remplacement : l'ancienne image n'est pas conservée
        statut, out2 = self.deposer(png(120, 100), cle='cle-logo-0002')
        self.assertTrue(statut.startswith('200'), out2)
        self.assertIn('recadrée au centre', out2['message'])
        self.assertEqual([r[0] for r in self.desk.db.execute('SELECT sha256 FROM identite56106_logos')], [out2['logo']['sha256']])
        # retrait : 404 neutre, la pastille revient
        statut, out3 = self.poster('identite/logo/retirer', {'cle': 'cle-logo-0003', 'revision': self.profil()['revision']})
        self.assertTrue(statut.startswith('200'), out3)
        absent = self.brut('/api440/m56106/identite/logo')
        self.assertTrue(absent['status'].startswith('404'))
        self.assertEqual(json.loads(absent['body'])['error'], 'introuvable')
        self.assertIn('id106-initiales', self.body('/aujourdhui'))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM identite56106_logos').fetchone()[0], 0)
        actions = [json.loads(r[0])['action'] for r in self.desk.db.execute("SELECT data FROM audit WHERE action='identite56106_logo' ORDER BY id")]
        self.assertEqual(actions, ['deposer', 'deposer', 'retirer'])

    def test_D5_svg_refuse_par_la_route_rien_n_est_enregistre(self):
        rev = self.profil()['revision']
        statut, out = self.deposer(SVG)
        self.assertTrue(statut.startswith('400'), out)
        self.assertEqual(out['error'], 'logo_type_refuse')
        self.assertEqual(self.profil()['revision'], rev)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM identite56106_logos').fetchone()[0], 0)
        self.assertTrue(self.brut('/api440/m56106/identite/logo')['status'].startswith('404'))

    def test_D5_revision_perimee_409_et_idempotence(self):
        rev = self.profil()['revision']
        perime = self.deposer(png(), revision=rev + 5)
        self.assertTrue(perime[0].startswith('409'), perime)
        statut, out = self.deposer(png(), cle='cle-logo-0100')
        again = self.deposer(png(), cle='cle-logo-0100', revision=rev)              # même clé, même demande : même réponse, aucune nouvelle révision
        self.assertEqual(again[1], out)
        self.assertEqual(self.profil()['revision'], rev + 1)
        autre = self.deposer(png(64, 70), cle='cle-logo-0100', revision=rev + 1)
        self.assertTrue(autre[0].startswith('409'), autre)
        mauvais = self.brut('/api440/m56106/identite/logo', 'POST', {'cle': 'cle-logo-0102', 'revision': rev + 1, 'donnees': ''}, csrf='mauvais')
        self.assertFalse(mauvais['status'].startswith('200'))

    def test_D5_profil_modifie_ensuite_garde_le_logo(self):
        self.deposer(png())
        self.nommer('Cabinet MARTIN')                                                 # enregistrement ordinaire du profil
        from agent import cabinet_profile5689 as profil
        data = json.loads(profil._ligne(self.desk)['data_json'])
        self.assertEqual(data['logo']['type'], 'image/png')
        self.assertTrue(self.brut('/api440/m56106/identite/logo')['status'].startswith('200'))

    def test_D5_contenu_altere_jamais_servi(self):
        self.deposer(png())
        self.desk.db.execute('UPDATE identite56106_logos SET donnees=?', (SVG,))
        self.desk.db.commit()
        r = self.brut('/api440/m56106/identite/logo')
        self.assertTrue(r['status'].startswith('404'))
        self.assertNotIn(b'<svg', r['body'])

    def test_D5_reserve_a_l_administrateur_controle_avant_le_corps(self):
        from agent.standalone_auth import allowed
        self.assertFalse(allowed('avocat', 'POST', '/api440/m56106/identite/logo'))
        self.assertFalse(allowed('assistant', 'POST', '/api440/m56106/identite/logo/retirer'))
        self.assertTrue(allowed('avocat', 'GET', '/api440/m56106/identite/logo'))
        self.assertTrue(allowed('assistant', 'POST', '/api440/m56106/densite'))
        self.assertFalse(allowed('avocat', 'POST', '/api440/m56106/autre'))

        class Corps(io.BytesIO):
            def read(self, *a):
                raise AssertionError('corps lu avant le contrôle du rôle')
        env = {'HTTP_X_AXIORHUB_USER': 'camille', 'HTTP_X_AXIORHUB_ROLE': 'avocat', 'HTTP_ORIGIN': 'https://o.example.test', 'HTTP_X_CSRF_TOKEN': 'j',
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': '20', 'wsgi.input': Corps()}
        out = idt.api(env, self.desk, {'origin': 'https://o.example.test', 'csrf': 'j'}, '/agent-courriel', 'm56106/identite/logo', {}, 'POST')
        self.assertTrue(out['status'].startswith('403'))
        self.assertEqual(json.loads(out['body'])['error'], 'role_insuffisant')
        # mutation : sans le contrôle du rôle, le service irait jusqu'au corps
        with patch('agent.identite56106.allowed', return_value=True), self.assertRaises(AssertionError):
            idt.api(env, self.desk, {'origin': 'https://o.example.test', 'csrf': 'j'}, '/agent-courriel', 'm56106/identite/logo', {}, 'POST')
        self.assertEqual(idt.section_html(self.desk, '/agent-courriel', 'avocat'), '')

    def test_D5_reglage_dans_parametres(self):
        page = self.body('/parametres', query='rubrique=demarrage')
        self.assertIn('data-d106-revision="0"', page)                                  # profil pas encore créé : l'affichage n'écrit rien
        revision = self.profil()['revision']
        page = self.body('/parametres', query='rubrique=demarrage')
        self.assertIn('id="d106-logo"', page)
        self.assertIn('accept="image/png,image/jpeg,image/webp"', page)
        self.assertIn('data-d106-revision="%d"' % revision, page)
        self.assertIn('SVG sont refusés', page)


if __name__ == '__main__':
    unittest.main()
