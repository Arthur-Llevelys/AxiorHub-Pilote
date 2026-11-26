"""5.6.110, lot E : GridCraft, PdfCraft et DeckCraft embarqués et proposés par défaut (versions d'essai).

- E1 fichiers embarqués : chaque fichier de ``agent/static/<app>-<version>/`` a la taille et l'empreinte déclarées dans ``craft56109`` (aucun en
  plus, aucun en moins) ; licences, NOTICE et licences des modèles de PdfCraft présentes ; textes d'accompagnement jamais servis ;
- E2 types servis, en-têtes, droits : fichiers servis par la route du produit (type exact, ``nosniff``, morceaux), 404 neutre sinon ;
- E3 CSP du cadre la plus stricte qui laisse les applications fonctionner (mesurée dans Edge : ``scripts/parcours_p110e.py``) ;
- E4 réglage par défaut (proposés), case de désactivation, texte des Paramètres, boutons de l'aperçu, de Fichiers et de Produire ;
- E5 sauvegarde à côté de l'original pour les trois formats (documents fictifs valides), refus d'un fichier produit invalide ;
- E6 traductions (anglais, allemand, espagnol) des écrans nouveaux.
Faux WebDAV en mémoire, données fictives (DURAND, MARTIN, DOS-001), aucun réseau, aucun navigateur.
"""
import hashlib
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from test_v56109_craft import Base, DURAND, ASSISTANT, ADMIN, PREFIX, ROOT
import fixtures_craft56110 as FX
from agent import craft56109 as C, wordcraft56108 as WC
from agent.common import Stop

LECTURE = ('lecture@example.test', 'lecture')
DOSSIER = '/Dossiers/DEMO/'
DOCS = {'gridcraft': ('Créances DURAND.xlsx', FX.xlsx_fictif), 'pdfcraft': ('Bordereau DURAND.pdf', FX.pdf_fictif),
        'deckcraft': ('Restitution MARTIN.pptx', FX.pptx_fictif)}


# ------------------------------------------------------------------------------------------------ E1 · fichiers embarqués
class Embarques(unittest.TestCase):
    def test_E1_empreintes_de_chaque_fichier_embarque(self):
        for app, r in C.RELEASES.items():
            self.assertEqual(C.verifier_fichiers(app), [], app)
            base = C.dossier_statique(app)
            self.assertEqual(base.name, '%s-%s' % (app, r['version']))
            for nom, (_, sha, taille) in r['fichiers'].items():
                data = (base / nom).read_bytes()
                self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (taille, sha), (app, nom))
        self.assertEqual({a: len(r['fichiers']) for a, r in C.RELEASES.items()}, {'gridcraft': 13, 'pdfcraft': 19, 'deckcraft': 12})

    def test_E1_licences_gardees_et_jamais_servies(self):
        for app, r in C.RELEASES.items():
            for nom in ('LICENSE-MIT', 'LICENSE-APACHE', 'NOTICE', 'HOSTING.md', 'README.md', 'index.html'):
                self.assertIn(nom, r['fichiers'], (app, nom))
                self.assertEqual(r['fichiers'][nom][0], '', (app, nom))                  # accompagnement : jamais servi
            self.assertEqual(set(C.servis(app).values()) - {'text/javascript', 'application/wasm', 'application/octet-stream'}, set(), app)
        self.assertEqual(C.RELEASES['gridcraft']['fichiers']['ATTRIBUTION.md'][0], '')
        for nom in ('models/ATTRIBUTION.txt', 'models/text-detection.onnx.LICENCE.txt', 'models/text-recognition.onnx.LICENCE.txt'):
            self.assertEqual(C.RELEASES['pdfcraft']['fichiers'][nom][0], '')
        self.assertIn('CC-BY-SA-4.0', (C.dossier_statique('pdfcraft') / 'models' / 'ATTRIBUTION.txt').read_text(encoding='utf-8'))
        notices = (ROOT / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')
        for mot in ('GridCraft 0.19.1', 'PdfCraft 0.21.1', 'DeckCraft 0.18.1', 'CC-BY-SA-4.0', 'agent/static/pdfcraft-0.21.1/models/'):
            self.assertIn(mot, notices)

    def test_E1_archives_officielles_et_colle_sans_reseau(self):
        """Archives de release déclarées (taille, SHA-256) ; la colle JavaScript ne contient aucune adresse ni moyen réseau autre que fetch."""
        self.assertEqual({a: (r['archive'], r['taille']) for a, r in C.RELEASES.items()},
                         {'gridcraft': ('gridcraft-web-0.19.1.zip', 9679862), 'pdfcraft': ('pdfcraft-web-0.21.1.zip', 34367297),
                          'deckcraft': ('deckcraft-web-0.18.1.zip', 11910503)})
        for app, r in C.RELEASES.items():
            for nom, kind in C.servis(app).items():
                if kind != 'text/javascript':
                    continue
                js = (C.dossier_statique(app) / nom).read_text(encoding='utf-8')
                for interdit in ('XMLHttpRequest', 'WebSocket', 'sendBeacon', 'EventSource', 'RTCPeerConnection', 'http://', 'https://'):
                    self.assertNotIn(interdit, js, (app, nom, interdit))
        # le .wasm d'une application dépasse l'ancien plafond (25 Mo) qui les refusait toutes : il n'existe plus pour les fichiers servis
        self.assertGreater(C.RELEASES['pdfcraft']['fichiers'][C.RELEASES['pdfcraft']['wasm']][2], 60_000_000)

    def test_E1_cadre_pdfcraft_travailleur_et_toiles_attendues(self):
        self.assertEqual({a: r['toile'] for a, r in C.RELEASES.items()}, {'gridcraft': 'gridcraft_canvas', 'pdfcraft': 'pdfcraft', 'deckcraft': 'deckcraft_canvas'})
        for app, r in C.RELEASES.items():
            index = (C.dossier_statique(app) / 'index.html').read_text(encoding='utf-8')
            self.assertIn('id="%s"' % r['toile'], index)                                  # même toile que la page d'origine de l'application
            self.assertIn("from './%s'" % r['glue'], index)
            self.assertIn("module_or_path: './%s'" % r['wasm'], index)
        self.assertIn('content="./%s"' % C.RELEASES['pdfcraft']['travailleur'], (C.dossier_statique('pdfcraft') / 'index.html').read_text(encoding='utf-8'))

    def test_E1_paquets_et_publication(self):
        attributs = (ROOT / '.gitattributes').read_text(encoding='utf-8')
        for motif in ('*.wasm binary', '*.onnx binary', '*.ico binary'):
            self.assertIn(motif, attributs)                                                # jamais de conversion de fin de ligne
        scan = (ROOT / 'scripts' / 'privacy-scan.py').read_text(encoding='utf-8')
        for app, r in C.RELEASES.items():
            self.assertIn("'agent/static/%s-%s/'" % (app, r['version']), scan)


# ------------------------------------------------------------------------------------------------ E2 à E6 · avec une installation
class Installation(Base):
    def setUp(self):
        super().setUp()
        for app, (nom, fabrique) in DOCS.items():
            self.nc.put(DOSSIER + nom, fabrique())

    def route(self, chemin, acteur=DURAND, args=None):
        env = {'HTTP_X_AXIORHUB_USER': acteur[0], 'HTTP_X_AXIORHUB_ROLE': acteur[1]}
        return C.page_route(env, self.desk.c, self.ident(), PREFIX, chemin, args or {}, lambda t, c, *a, **k: c, self.src())

    def test_E2_types_servis_entetes_et_droits(self):
        for app, r in C.RELEASES.items():
            base = '/editeur/craft/%s-%s/' % (app, r['version'])
            for nom, kind in C.servis(app).items():
                out = self.route(base + nom)
                self.assertEqual((out['status'], out['kind']), ('200 OK', kind), (app, nom))
                entetes = dict(out['headers'])
                self.assertEqual(entetes['X-Content-Type-Options'], 'nosniff')
                self.assertIn('immutable', entetes['Cache-Control'])
                self.assertEqual(out['length'], r['fichiers'][nom][2])
                if out['length'] < 300_000:
                    self.assertEqual(hashlib.sha256(b''.join(out['stream'])).hexdigest(), r['fichiers'][nom][1])
            for nom in ('index.html', 'LICENSE-MIT', 'NOTICE', 'README.md', '.htaccess', '_headers', '../../craft56109.py', 'inconnu.js'):
                self.assertTrue(self.route(base + nom)['status'].startswith('404'), (app, nom))
            self.assertTrue(self.route(base + r['glue'], acteur=LECTURE)['status'].startswith('404'))      # rôle sans édition : 404 neutre
            self.assertTrue(self.route(base + 'cadre', acteur=LECTURE)['status'].startswith('404'))
        modele = self.route('/editeur/craft/pdfcraft-0.21.1/models/text-recognition.onnx')
        self.assertEqual((modele['status'], modele['kind'], modele['length']), ('200 OK', 'application/octet-stream', 9713177))

    def test_E3_csp_du_cadre_stricte_et_page_hote(self):
        for app, r in C.RELEASES.items():
            cadre = self.route('/editeur/craft/%s-%s/cadre' % (app, r['version']))
            self.assertEqual(cadre['csp'], C.CADRE_CSP)
            self.assertTrue(cadre['frame_self'])
            directives = dict(x.strip().split(' ', 1) for x in C.CADRE_CSP.split(';'))
            self.assertEqual(directives['default-src'], "'none'")
            self.assertEqual(directives['connect-src'], "'self'")
            self.assertEqual(directives['script-src'], "'self' 'wasm-unsafe-eval'")
            self.assertEqual(directives['frame-ancestors'], "'self'")
            self.assertNotRegex(C.CADRE_CSP, r"https?:|\*|'unsafe-inline'|'unsafe-eval'")
            self.assertIn('<canvas id="%s"' % r['toile'], cadre['body'])
            self.assertNotIn('<script>', cadre['body'])                                     # aucun script en ligne
        self.assertIn('<meta name="pdfcraft-ocr-worker" content="./pdfcraft-ocr-worker-b363fe94bbdd0923_loader.js">',
                      self.route('/editeur/craft/pdfcraft-0.21.1/cadre')['body'])
        page = self.route('/editeur/craft', args={'app': 'gridcraft', 's': 'nextcloud', 'p': DOSSIER + DOCS['gridcraft'][0]})
        config = json.loads(re.search(r'data-c109-config="([^"]+)"', page['body']).group(1).replace('&quot;', '"').replace('&amp;', '&'))
        self.assertTrue(config['cadre'].startswith(PREFIX + '/editeur/craft/gridcraft-0.19.1/cadre?host=parent&chrome=notitlebar&author='))
        self.assertNotIn('control', config['cadre'])                                         # API de pilotage de PdfCraft jamais activée
        self.assertNotIn('csp', page)                                                         # page hôte : CSP par défaut d'AxiorHub

    def test_E4_propose_par_defaut_texte_et_boutons(self):
        self.assertEqual(C.DEFAUT, {'gridcraft': True, 'pdfcraft': True, 'deckcraft': True})
        section = C.section_reglages(self.desk, PREFIX)
        self.assertEqual(section.count(' checked'), 3)
        for absent in ('déposez', 'Déposez', 'désactivés par défaut', 'archive', 'Installer', 'non qualifiés'):
            self.assertNotIn(absent, section)
        for present in ('versions d’essai', '« alpha » selon leurs auteurs', 'contrôlé', 'à côté de l’original', 'tableur', 'PDF', 'présentation',
                        'Les commandes de l’IA ne sont pas proposées'):
            self.assertIn(present, section)
        C.regler(self.desk, ADMIN[0], ADMIN[1], 'pdfcraft', False)
        self.assertEqual(C.section_reglages(self.desk, PREFIX).count(' checked'), 2)
        # aperçu d'un document, page Fichiers, Produire (point de montage commun ``wordcraft56108.boutons_html``)
        self.assertEqual(C.liens(self.desk, PREFIX, 'nextcloud', DOSSIER + DOCS['gridcraft'][0], 'assistant')['craft_libelle'], 'Modifier dans GridCraft (version d’essai)')
        self.assertEqual(C.liens(self.desk, PREFIX, 'nextcloud', DOSSIER + DOCS['pdfcraft'][0], 'assistant'), {})       # désactivé ci-dessus
        self.assertEqual(C.liens(self.desk, PREFIX, 'nextcloud', '/Dossiers/DEMO/Acte.docx', 'avocat'), {})              # .docx : WordCraft
        self.assertIn('Modifier dans DeckCraft (version d’essai)', WC.boutons_html(self.desk, None, PREFIX, 'nextcloud', DOSSIER + DOCS['deckcraft'][0], 'avocat'))
        config = C.config_fichiers(self.desk, PREFIX, 'avocat')
        self.assertEqual(config, {'url': PREFIX + '/editeur/craft', 'apps': {'xlsx': 'gridcraft', 'pptx': 'deckcraft'}})
        self.assertEqual(C.libelle_fichiers(config), 'Modifier dans GridCraft, DeckCraft (version d’essai)')
        self.assertEqual(C.config_fichiers(self.desk, PREFIX, 'lecture'), {})
        js = (ROOT / 'agent' / 'static' / 'v56103-fichiers.js').read_text(encoding='utf-8')
        self.assertIn("nom === 'craft'", js)
        self.assertIn('data-d79-craft', (ROOT / 'agent' / 'dossier5679.py').read_text(encoding='utf-8'))
        self.assertIn('v.craft_url', (ROOT / 'agent' / 'static' / 'v5679.js').read_text(encoding='utf-8'))

    def test_E4_droits_reglage_et_installation_disparue(self):
        self.assertEqual(self.craft('reglage', {'app': 'deckcraft', 'active': False, 'cle': self.cle()})['status'], '403 Forbidden')       # avocat
        self.assertEqual(self.craft('installer', {'app': 'deckcraft', 'cle': self.cle()}, acteur=ADMIN)['status'], '403 Forbidden')       # route retirée
        self.assertFalse(hasattr(C, 'installer'))
        out = self.craft('seances', {'app': 'deckcraft', 's': 'nextcloud', 'p': DOSSIER + DOCS['deckcraft'][0], 'cle': self.cle()}, acteur=LECTURE)
        self.assertEqual(out['status'], '403 Forbidden')
        ok = self.craft('reglage', {'app': 'deckcraft', 'active': False, 'cle': self.cle()}, acteur=ADMIN)
        self.assertEqual(ok['json']['reglages']['deckcraft'], False)
        self.assertTrue(self.desk.db.execute("SELECT 1 FROM audit WHERE action='craft56109_regle'").fetchone())

    def test_E5_sauvegarde_a_cote_de_l_original_trois_formats(self):
        modifies = {'gridcraft': FX.xlsx_fictif((('Créancier', 'Montant'), ('DURAND', 1500), ('MARTIN', 800))),
                    'pdfcraft': FX.pdf_fictif(('Bordereau fictif DOS-001', 'Client : DURAND', 'Pièce 3 ajoutée')),
                    'deckcraft': FX.pptx_fictif((('Restitution fictive', 'Dossier DOS-001 : MARTIN [modifié]'),))}
        for app, (nom, fabrique) in DOCS.items():
            chemin = DOSSIER + nom
            ident = self.craft('seances', {'app': app, 's': 'nextcloud', 'p': chemin, 'cle': self.cle()})['json']['seance']
            self.assertEqual(self.craft('seances/%s/contenu' % ident, methode='GET')['body'], fabrique())
            cand = self.craft('seances/%s/candidats' % ident, args={'revision': '0', 'cle': self.cle(), 'instantane': '1'}, binaire=modifies[app])
            self.assertEqual(cand['status'], '200 OK', (app, cand['json']))
            self.assertEqual(cand['json']['controle']['pertes'], [], app)
            avant = sorted(self.nc.t)
            garder = self.craft('seances/%s/decision' % ident, {'decision': 'garder', 'revision': 1, 'cle': self.cle()})
            self.assertEqual(garder['json']['etat'], 'verifie', (app, garder['json']))
            self.assertRegex(garder['json']['chemin'], r'^/Dossiers/DEMO/.+ \(modifié dans %s \d\d-\d\d-\d{4} \d\dh\d\d\)\.%s$' % (
                C.RELEASES[app]['libelle'], C.RELEASES[app]['documents'][0]))
            self.assertEqual(self.nc.lire(chemin), fabrique())                               # original intact, jamais remplacé
            self.assertEqual(self.nc.lire(garder['json']['chemin']), modifies[app])
            self.assertEqual(sorted(set(self.nc.t) - set(avant)), [garder['json']['chemin']])

    def test_E5_fichier_produit_invalide_refuse(self):
        chemin = DOSSIER + DOCS['gridcraft'][0]
        ident = self.craft('seances', {'app': 'gridcraft', 's': 'nextcloud', 'p': chemin, 'cle': self.cle()})['json']['seance']
        avant = dict((k, v.get('data')) for k, v in self.nc.t.items())
        for brut, code in ((b'PK\x03\x04 pas un classeur', 'document_illisible'), (FX.pptx_fictif(), 'document_illisible'),
                           (b'%PDF-1.4 un PDF a la place', 'document_illisible')):
            out = self.craft('seances/%s/candidats' % ident, args={'revision': '0', 'cle': self.cle()}, binaire=brut)
            self.assertEqual(out['json']['error'], code, brut[:20])
            self.assertTrue(out['json']['message'])
        self.assertEqual(WC.seance(self.desk, DURAND, ident)['revision'], 0)                 # rien d'acquitté
        export = self.craft('seances/%s/exports' % ident, args={'format': 'pdf', 'cle': self.cle()}, binaire=b'PK pas un pdf')
        self.assertEqual(export['json']['error'], 'export_refuse')
        html = self.craft('seances/%s/exports' % ident, args={'format': 'html', 'cle': self.cle()}, binaire=b'<html></html>')
        self.assertEqual(html['json']['error'], 'format_export_refuse')
        self.assertEqual(dict((k, v.get('data')) for k, v in self.nc.t.items()), avant)      # aucune écriture dans le dossier
        # PDF : un fichier qui n'est pas un PDF est refusé ; présentation avec macros : refusée
        ident_pdf = self.craft('seances', {'app': 'pdfcraft', 's': 'nextcloud', 'p': DOSSIER + DOCS['pdfcraft'][0], 'cle': self.cle()})['json']['seance']
        self.assertEqual(self.craft('seances/%s/candidats' % ident_pdf, args={'revision': '0', 'cle': self.cle()}, binaire=FX.xlsx_fictif())['json']['error'],
                         'document_pdf_illisible')
        import io
        import zipfile
        tampon = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(FX.pptx_fictif())) as zin, zipfile.ZipFile(tampon, 'w') as zout:
            for info in zin.infolist():
                zout.writestr(info, zin.read(info))
            zout.writestr('ppt/vbaProject.bin', b'macro')
        ident_pptx = self.craft('seances', {'app': 'deckcraft', 's': 'nextcloud', 'p': DOSSIER + DOCS['deckcraft'][0], 'cle': self.cle()})['json']['seance']
        out = self.craft('seances/%s/candidats' % ident_pptx, args={'revision': '0', 'cle': self.cle()}, binaire=tampon.getvalue())
        self.assertEqual(out['json']['error'], 'contenu_actif_refuse')

    def test_E6_ecrans_traduits(self):
        from agent import ecrans5632 as E
        from test_v56107_langues import reste_francais, visibles
        page = self.route('/editeur/craft', args={'app': 'deckcraft', 's': 'nextcloud', 'p': DOSSIER + DOCS['deckcraft'][0]})['body']
        textes = {'section': C.section_reglages(self.desk, PREFIX), 'page': page.replace('Restitution MARTIN.pptx', 'X.pptx')}
        for app in C.RELEASES:
            textes['page ' + app] = self.route('/editeur/craft', args={'app': app, 's': 'nextcloud', 'p': DOSSIER + DOCS[app][0]})['body']
        for lang in ('en', 'de', 'es'):
            table = E.dictionary(lang)
            for nom, html in textes.items():
                restes = [t for t in reste_francais(visibles(E.translate_html(html, lang))) if 'DURAND' not in t and 'MARTIN' not in t]
                self.assertEqual(restes, [], '%s (%s)' % (nom, lang))
            for code in ('craft_desactive', 'ressources_craft_absentes', 'commandes_ia_indisponibles', 'document_illisible', 'contenu_actif_refuse'):
                self.assertIsNotNone(E.phrase(C.MESSAGES[code], table), (lang, code))
            for texte in C.DESCRIPTIONS.values():
                self.assertIsNotNone(E.phrase(texte, table), (lang, texte))
        js = (ROOT / 'agent' / 'static' / 'v56109-craft-host.js').read_text(encoding='utf-8')
        for phrase in ('Document ouvert. Enregistrez dans l’éditeur (Ctrl+S) pour faire contrôler une nouvelle version.',
                       'Version reçue et contrôlée : comparez-la ci-dessous avant de la conserver.'):
            self.assertIn(phrase, js)
            for lang in ('en', 'de', 'es'):
                self.assertIsNotNone(E.phrase(phrase, E.dictionary(lang)), (lang, phrase))

    def test_R05_lectures_sans_ecriture(self):
        avant = self.desk.db.total_changes
        for app, r in C.RELEASES.items():
            self.route('/editeur/craft/%s-%s/cadre' % (app, r['version']))
            self.route('/editeur/craft', args={'app': app, 's': 'nextcloud', 'p': DOSSIER + DOCS[app][0]})
        C.section_reglages(self.desk, PREFIX)
        C.detecter(self.desk)
        self.craft('etat', methode='GET')
        self.assertEqual(self.desk.db.total_changes, avant)


if __name__ == '__main__':
    unittest.main()
