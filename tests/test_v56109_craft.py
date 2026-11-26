"""5.6.109, lot V (F16) : éditeurs Craft (GridCraft, PdfCraft, DeckCraft) — séances, candidats contrôlés, exports distincts, commandes IA
validées par le serveur. 5.6.110 (lot E) : éditeurs EMBARQUÉS et proposés par défaut (désactivables par l'administrateur, jamais proposés
si leurs fichiers manquent) ; l'installation par archive déposée a disparu ; commandes de l'IA refusées par la route (validation gardée).

Faux WebDAV en mémoire (``test_v56103_fichiers.FakeNC``), classeur et présentation fictifs (DURAND, MARTIN, DOS-001) ; aucun réseau, aucun
navigateur ici (le parcours ``scripts/parcours_p110e.py`` fait tourner les vraies applications ; empreintes : ``test_v56110_craft``).

Identifiants : F16 = ticket F16 ; R05 = aucune écriture pendant une lecture.
"""
import functools
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch
import zipfile

from test_v56108_editeur import Base as BaseEditeur, DURAND, ASSISTANT, ADMIN, PREFIX
from agent import craft56109 as C, wordcraft56108 as WC
from agent.common import Stop

ROOT = Path(__file__).resolve().parent.parent
XLSX = '/Dossiers/DEMO/Tableau des créances DURAND.xlsx'
PPTX = '/Dossiers/DEMO/Restitution MARTIN.pptx'


@functools.lru_cache(maxsize=None)   # mêmes octets à chaque appel : l'heure d'archive change à la seconde paire (échec intermittent sinon)
def xlsx(valeurs=('Créance fictive', '1200'), formule=True):
    cellules = ''.join('<c r="%s1" t="inlineStr"><is><t>%s</t></is></c>' % (chr(65 + i), v) for i, v in enumerate(valeurs))
    if formule:
        cellules += '<c r="Z1"><f>SUM(B1:B1)</f><v>1200</v></c>'
    parts = {'[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
             '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
             'xl/workbook.xml': '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets><sheet name="F1" sheetId="1"/></sheets></workbook>',
             'xl/worksheets/sheet1.xml': '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1">%s</row></sheetData></worksheet>' % cellules}
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for n, d in parts.items():
            z.writestr(n, d)
    return out.getvalue()


@functools.lru_cache(maxsize=None)
def pptx(textes=('Synthèse fictive MARTIN',)):
    parts = {'[Content_Types].xml': '<Types/>', '_rels/.rels': '<Relationships/>', 'ppt/presentation.xml': '<p:presentation xmlns:p="urn:p"/>'}
    for i, t in enumerate(textes, 1):
        parts['ppt/slides/slide%d.xml' % i] = '<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><a:t>%s</a:t></p:sld>' % t
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for n, d in parts.items():
            z.writestr(n, d)
    return out.getvalue()


class Base(BaseEditeur):
    def setUp(self):
        super().setUp()
        C.ensure_schema(self.desk)
        self.nc.put(XLSX, xlsx())
        self.nc.put(PPTX, pptx())

    def activer(self, app='gridcraft'):
        """5.6.110 (lot E) : éditeurs embarqués et proposés par défaut ; le réglage explicite reste celui de l'administrateur."""
        C.regler(self.desk, ADMIN[0], ADMIN[1], app, True)

    def craft(self, route, data=None, acteur=DURAND, methode='POST', args=None, binaire=None):
        raw = binaire if binaire is not None else json.dumps(data or {}).encode()
        env = {'REQUEST_METHOD': methode, 'HTTP_ORIGIN': self.origin, 'HTTP_X_CSRF_TOKEN': 'test-csrf',
               'CONTENT_TYPE': 'application/octet-stream' if binaire is not None else 'application/json', 'CONTENT_LENGTH': str(len(raw)),
               'wsgi.input': io.BytesIO(raw), 'HTTP_X_AXIORHUB_ROLE': acteur[1], 'HTTP_X_AXIORHUB_USER': acteur[0]}
        out = C.api(env, self.desk, self.ident(), PREFIX, C.PREFIXE + route, args or {}, methode, self.src())
        try:
            out['json'] = json.loads(out['body'])
        except (ValueError, TypeError):
            out['json'] = None
        return out


# ====================================================================================== réglage : proposé par défaut, désactivable
class Reglage(Base):
    def test_F16_propose_par_defaut_desactivable_par_l_administrateur(self):
        self.assertEqual(C.reglages(self.desk), {'gridcraft': True, 'pdfcraft': True, 'deckcraft': True})       # 5.6.110 (lot E)
        self.assertTrue(all(C.actif(self.desk, a) for a in C.RELEASES))
        self.assertIn('Modifier dans GridCraft', C.bouton_html(self.desk, PREFIX, 'nextcloud', XLSX, 'avocat'))
        self.assertEqual(C.bouton_html(self.desk, PREFIX, 'nextcloud', XLSX, 'lecture'), '')                     # rôle sans édition : rien
        C.regler(self.desk, ADMIN[0], ADMIN[1], 'gridcraft', False)
        self.assertEqual(C.bouton_html(self.desk, PREFIX, 'nextcloud', XLSX, 'avocat'), '')
        self.assertNotIn('GridCraft', WC.boutons_html(self.desk, None, PREFIX, 'nextcloud', XLSX, 'avocat'))
        out = self.craft('seances', {'app': 'gridcraft', 's': 'nextcloud', 'p': XLSX, 'cle': self.cle()})
        self.assertEqual((out['status'], out['json']['error']), ('403 Forbidden', 'craft_desactive'))
        self.assertIn('Modifier dans DeckCraft', C.bouton_html(self.desk, PREFIX, 'nextcloud', PPTX, 'avocat'))   # les autres restent proposés

    def test_F16_garde_fou_fichiers_absents_rien_propose(self):
        with patch.object(C, 'disponible', return_value=False):
            with self.assertRaisesRegex(Stop, 'ressources_craft_absentes'):
                C.regler(self.desk, ADMIN[0], ADMIN[1], 'gridcraft', True)
            self.assertFalse(C.actif(self.desk, 'gridcraft'))                                    # même un réglage actif ne propose rien
            self.assertEqual(C.bouton_html(self.desk, PREFIX, 'nextcloud', XLSX, 'avocat'), '')
            self.assertEqual(self.craft('reglage', {'app': 'gridcraft', 'active': True, 'cle': self.cle()}, acteur=ADMIN)['json']['error'], 'ressources_craft_absentes')
        self.assertEqual(self.craft('reglage', {'app': 'gridcraft', 'active': False, 'cle': self.cle()})['status'], '403 Forbidden')   # avocat : jamais

    def test_F16_reglage_illisible_rien_propose(self):
        with patch.object(self.desk, 'settings', side_effect=RuntimeError('base illisible')):
            self.assertEqual(C.reglages(self.desk), {'gridcraft': False, 'pdfcraft': False, 'deckcraft': False})

    def test_F16_aucun_telechargement_ni_compilation(self):
        src = (ROOT / 'agent' / 'craft56109.py').read_text(encoding='utf-8')
        for interdit in ('urlopen', 'urllib.request', 'requests', 'http.client', 'socket', 'subprocess', 'cargo', 'rustup'):
            self.assertNotIn(interdit, src.split('"""', 2)[2])
        # binaires des éditeurs : seulement dans les dossiers embarqués déclarés (empreintes vérifiées par test_v56110_craft)
        statiques = ROOT / 'agent' / 'static'
        dossiers = {'%s-%s' % (a, r['version']) for a, r in C.RELEASES.items()}
        hors = [p for p in statiques.rglob('*') if p.is_file() and any(a in p.name.lower() for a in C.RELEASES) and p.suffix in ('.wasm', '.zip')
                and p.relative_to(statiques).parts[0] not in dossiers]
        self.assertEqual(hors, [])


# ====================================================================================== fichiers servis
class Ressources(Base):
    def test_F16_fichiers_servis_a_leur_empreinte_seulement(self):
        fichier, kind = C.ressource(self.desk, 'gridcraft-0.19.1', 'gridcraft-web-fdc723373f2aa0d8_bg.wasm')
        self.assertEqual(kind, 'application/wasm')
        self.assertEqual(fichier.stat().st_size, 26298748)
        for absent in ('LICENSE-MIT', 'index.html', 'NOTICE', '../craft56109.py', 'HOSTING.md', 'manifeste-axiorhub.json'):
            with self.assertRaises(Stop):
                C.ressource(self.desk, 'gridcraft-0.19.1', absent)
        with self.assertRaises(Stop):
            C.ressource(self.desk, 'gridcraft-0.19.0', 'gridcraft-web-fdc723373f2aa0d8.js')       # autre version : introuvable
        faux = dict(C.RELEASES['gridcraft']['fichiers'])
        faux['gridcraft-web-fdc723373f2aa0d8.js'] = ('text/javascript', '0' * 64, 174295)         # empreinte différente : jamais servi
        with patch.dict(C.RELEASES['gridcraft'], {'fichiers': faux}):
            with self.assertRaises(Stop):
                C.ressource(self.desk, 'gridcraft-0.19.1', 'gridcraft-web-fdc723373f2aa0d8.js')
        C.regler(self.desk, ADMIN[0], ADMIN[1], 'gridcraft', False)
        with self.assertRaises(Stop):
            C.ressource(self.desk, 'gridcraft-0.19.1', 'gridcraft-web-fdc723373f2aa0d8.js')        # éditeur désactivé : rien servi
        cadre = C._cadre(self.desk, PREFIX, 'gridcraft-0.19.1')
        self.assertTrue(cadre['status'].startswith('404'))

    def test_F16_cadre_csp_stricte(self):
        cadre = C._cadre(self.desk, PREFIX, 'gridcraft-0.19.1')
        self.assertIn("frame-ancestors 'self'", cadre['csp'])
        self.assertIn("connect-src 'self'", cadre['csp'])
        self.assertNotRegex(cadre['csp'], r"https?:|\*|'unsafe-inline'|'unsafe-eval'")
        self.assertIn('data-c109-glue="gridcraft-web-fdc723373f2aa0d8.js"', cadre['body'])
        self.assertIn('<canvas id="gridcraft_canvas"', cadre['body'])

    def test_F16_desactiver_sans_perte(self):
        s = C.ouvrir(self.desk, self.ident(), DURAND, 'gridcraft', 'nextcloud', XLSX, self.src())
        C.regler(self.desk, ADMIN[0], ADMIN[1], 'gridcraft', False)
        self.assertFalse(C.actif(self.desk, 'gridcraft'))
        self.assertEqual(self.nc.lire(XLSX), xlsx())                                        # document intact, toujours dans le dossier
        self.assertTrue((WC.dossier_seance(self.desk, s['seance']) / 'original.docx').exists())


# ====================================================================================== séances, sauvegardes et exports protégés
class Seances(Base):
    def test_F16_candidat_controle_puis_version_voisine_original_intact(self):
        self.activer()
        out = self.craft('seances', {'app': 'gridcraft', 's': 'nextcloud', 'p': XLSX, 'cle': self.cle()})
        self.assertEqual(out['status'], '200 OK', out['json'])
        ident = out['json']['seance']
        contenu = self.craft('seances/%s/contenu' % ident, methode='GET')
        self.assertEqual(contenu['body'], xlsx())
        self.assertIn('no-store', dict(contenu['headers'])['Cache-Control'])
        modifie = xlsx(('Créance fictive', '1500'), formule=False)
        cand = self.craft('seances/%s/candidats' % ident, args={'revision': '0', 'cle': 'cand-cle-0001', 'instantane': '1'}, binaire=modifie)
        self.assertEqual(cand['status'], '200 OK', cand['json'])
        self.assertEqual((cand['json']['revision'], cand['json']['durable'], cand['json']['instantane']), (1, True, 1))
        self.assertEqual([p['code'] for p in cand['json']['controle']['pertes']], ['formules'])   # la formule disparue est montrée avant « Conserver »
        perime = self.craft('seances/%s/candidats' % ident, args={'revision': '0', 'cle': 'cand-cle-0002'}, binaire=modifie)
        self.assertEqual(perime['status'], '409 Conflict')
        garder = self.craft('seances/%s/decision' % ident, {'decision': 'garder', 'revision': 1, 'cle': 'dec-cle-0001'})
        self.assertEqual(garder['json']['etat'], 'verifie', garder['json'])
        self.assertRegex(garder['json']['chemin'], r'\(modifié dans GridCraft \d\d-\d\d-\d{4} \d\dh\d\d\)\.xlsx$')
        self.assertEqual(self.nc.lire(XLSX), xlsx())
        self.assertEqual(self.nc.lire(garder['json']['chemin']), modifie)
        op = self.craft('seances/%s/operations/dec-cle-0001' % ident, methode='GET')
        self.assertEqual(op['json']['rapprochement'], 'verifiee')

    def test_F16_export_fichier_distinct_jamais_le_document(self):
        self.activer()
        ident = C.ouvrir(self.desk, self.ident(), DURAND, 'gridcraft', 'nextcloud', XLSX, self.src())['seance']
        pdf = b'%PDF-1.4 export fictif'
        out = self.craft('seances/%s/exports' % ident, args={'format': 'pdf', 'cle': 'exp-cle-0001'}, binaire=pdf)
        self.assertEqual(out['json']['etat'], 'verifie', out['json'])
        exports = [p for p in self.nc.t if p.endswith('.pdf') and 'export GridCraft' in p]
        self.assertEqual(len(exports), 1)
        self.assertEqual(self.nc.lire(exports[0]), pdf)
        self.assertEqual(self.nc.lire(XLSX), xlsx())                                        # le classeur n'est jamais remplacé par l'export
        rejeu = self.craft('seances/%s/exports' % ident, args={'format': 'pdf', 'cle': 'exp-cle-0001'}, binaire=pdf)
        self.assertTrue(rejeu['json']['rejoue'])
        self.assertEqual(len([p for p in self.nc.t if 'export GridCraft' in p]), 1)
        self.assertEqual(self.craft('seances/%s/exports' % ident, args={'format': 'pdf', 'cle': 'exp-cle-0002'}, binaire=b'PK pas un pdf')['json']['error'], 'export_refuse')
        self.assertEqual(self.craft('seances/%s/exports' % ident, args={'format': 'xlsx', 'cle': 'exp-cle-0003'}, binaire=xlsx())['json']['error'], 'format_export_refuse')
        self.assertEqual(self.craft('seances/%s/exports' % ident, args={'format': 'pdf', 'cle': 'exp-cle-0001'}, binaire=b'%PDF-1.4 autre')['json']['error'], 'cle_reutilisee')

    def test_F16_export_reponse_perdue_meme_cible_relue(self):
        self.activer()
        ident = C.ouvrir(self.desk, self.ident(), DURAND, 'gridcraft', 'nextcloud', XLSX, self.src())['seance']
        from agent import fichiers56103 as F
        vraie = F.SourceNextcloud.lire
        with patch.object(F.SourceNextcloud, 'lire', side_effect=Stop('stockage_injoignable')):
            out = self.craft('seances/%s/exports' % ident, args={'format': 'pdf', 'cle': 'exp-cle-0009'}, binaire=b'%PDF-1.4 x')
        self.assertEqual(out['json']['etat'], 'a_verifier')
        self.assertTrue(vraie)
        again = self.craft('seances/%s/exports' % ident, args={'format': 'pdf', 'cle': 'exp-cle-0009'}, binaire=b'%PDF-1.4 x')
        self.assertEqual(again['json']['etat'], 'verifie')
        self.assertEqual(len([p for p in self.nc.t if 'export GridCraft' in p]), 1)

    def test_F16_seance_d_un_autre_compte_et_dossier_reserve_404(self):
        self.activer()
        ident = C.ouvrir(self.desk, self.ident(), DURAND, 'gridcraft', 'nextcloud', XLSX, self.src())['seance']
        autre = self.craft('seances/%s/contenu' % ident, methode='GET', acteur=ASSISTANT)
        inconnue = self.craft('seances/%s/contenu' % ('0' * 32), methode='GET')
        self.assertEqual((autre['status'], autre['json']), (inconnue['status'], inconnue['json']))
        self.assertEqual(autre['status'], '404 Not Found')
        self.nc.put('/Dossiers/KERVILAC/Comptes KERVILAC.xlsx', xlsx())
        reserve = self.craft('seances', {'app': 'gridcraft', 's': 'nextcloud', 'p': '/Dossiers/KERVILAC/Comptes KERVILAC.xlsx', 'cle': self.cle()}, acteur=DURAND)
        self.assertEqual(reserve['status'], '404 Not Found')

    def test_F16_pptx_capacite_de_format(self):
        avant, apres = C.analyser_pptx(pptx(('A', 'B'))), C.analyser_pptx(pptx(('A',)))
        r = C.controler_pptx(avant, apres)
        self.assertEqual([p['code'] for p in r['pertes']], ['diapositives'])
        with self.assertRaisesRegex(Stop, 'contenu_actif_refuse'):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w') as z:
                z.writestr('ppt/presentation.xml', '<x/>')
                z.writestr('ppt/vbaProject.bin', 'x')
            C.analyser_pptx(buf.getvalue())

    def test_R05_lectures_sans_ecriture(self):
        self.activer()
        ident = C.ouvrir(self.desk, self.ident(), DURAND, 'gridcraft', 'nextcloud', XLSX, self.src())['seance']
        avant = self.desk.db.total_changes
        self.craft('etat', methode='GET')
        self.craft('seances/%s/contenu' % ident, methode='GET')
        C.detecter(self.desk)
        C.section_reglages(self.desk, PREFIX)
        self.assertEqual(self.desk.db.total_changes, avant)


# ====================================================================================== commandes IA : désactivées (5.6.110), validation conservée
class CommandesIA(Base):
    def test_F16_route_refusee_rien_applique(self):
        ident = C.ouvrir(self.desk, self.ident(), DURAND, 'gridcraft', 'nextcloud', XLSX, self.src())['seance']
        out = self.craft('seances/%s/actions-ia' % ident, {'plan': [{'commande': 'cell.set', 'parametres': {'sheet': 1, 'cell': 'B2', 'value': '1500'}}],
                                                          'revision': 0, 'cle': 'plan-c-0001'})
        self.assertEqual((out['status'], out['json']['error']), ('409 Conflict', 'commandes_ia_indisponibles'))
        self.assertFalse(C.COMMANDES_IA)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM craft56109_plans').fetchone()[0], 0)
        autre = self.craft('seances/%s/actions-ia' % ident, {'plan': [], 'revision': 0, 'cle': 'plan-c-0002'}, acteur=ASSISTANT)
        self.assertEqual(autre['status'], '404 Not Found')                                   # séance d'un autre compte : 404 neutre d'abord

    def test_F16_plan_ferme_valide_par_le_serveur(self):
        ident = C.ouvrir(self.desk, self.ident(), DURAND, 'gridcraft', 'nextcloud', XLSX, self.src())['seance']
        ok = C.valider_plan(self.desk, DURAND, ident, [{'commande': 'cell.set', 'parametres': {'sheet': 1, 'cell': 'B2', 'value': '1500'}}], 0, 'plan-c-0001')
        self.assertEqual(ok['commandes'][0]['commande'], 'cell.set')
        refus = [([{'commande': 'file.open', 'parametres': {'path': 'C:/x'}}], 'commande_non_autorisee'),
                 ([{'commande': 'execute_command', 'parametres': {}}], 'commande_non_autorisee'),
                 ([{'commande': 'cell.set', 'parametres': {'sheet': 1, 'cell': 'B2', 'value': '=1+1'}}], 'formule_non_demandee'),
                 ([{'commande': 'cell.set', 'parametres': {'sheet': 1, 'cell': 'B2', 'value': '=WEBSERVICE("https://x.example.test")', 'formula': True}}], 'formule_externe_refusee'),
                 ([{'commande': 'cell.set', 'parametres': {'sheet': 1, 'cell': '../B2', 'value': 'x'}}], 'parametre_refuse'),
                 ([{'commande': 'cell.set', 'parametres': {'sheet': 1, 'cell': 'B2', 'value': 'x', 'script': 'y'}}], 'parametre_refuse'),
                 ([{'commande': 'cell.set', 'parametres': {'sheet': 1, 'cell': 'B2', 'value': 'x'}}] * 21, 'plan_refuse')]
        for i, (plan, code) in enumerate(refus):
            with self.assertRaisesRegex(Stop, code):
                C.valider_plan(self.desk, DURAND, ident, plan, 0, 'plan-r-%04d' % i)
        with self.assertRaisesRegex(Stop, 'version_perimee'):
            C.valider_plan(self.desk, DURAND, ident, [{'commande': 'cell.set', 'parametres': {'sheet': 1, 'cell': 'B3', 'value': 'x'}}], 5, 'plan-p-0001')

    def test_F16_ia_en_pause_commandes_refusees_saisie_manuelle_possible(self):
        ident = C.ouvrir(self.desk, self.ident(), DURAND, 'gridcraft', 'nextcloud', XLSX, self.src())['seance']
        with patch('agent.arret5643.manual_paused', return_value=True):
            with self.assertRaisesRegex(Stop, 'ia_en_pause'):
                C.valider_plan(self.desk, DURAND, ident, [{'commande': 'range.read', 'parametres': {'sheet': 1, 'range': 'A1:B2'}}], 0, 'plan-z-0001')
            cand = self.craft('seances/%s/candidats' % ident, args={'revision': '0', 'cle': 'cand-z-0001'}, binaire=xlsx(('x', '1')))
            self.assertEqual(cand['status'], '200 OK')                                       # l'enregistrement manuel reste possible


class PontJavaScript(unittest.TestCase):
    def test_F16_contrats_du_pont_sous_node(self):
        if not shutil.which('node'):
            self.skipTest('Node absent : contrats du pont non exécutés ici')
        r = subprocess.run(['node', str(ROOT / 'tests' / 'craft56109.cjs')], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('pass 26', r.stdout)

    def test_F16_pont_sans_module_es_ni_html_injecte(self):
        js = (ROOT / 'agent' / 'static' / 'v56109-craft-host.js').read_text(encoding='utf-8')
        self.assertNotIn('innerHTML', js)
        self.assertNotRegex(js, r'^\s*(?:import|export)\s', )
        self.assertIn('event.origin !== this.origin || event.source !== this.frame.contentWindow', js)
        self.assertNotIn(":host'", js)                                                       # 5.6.110 : aucun message hors du contrat documenté
        self.assertNotIn("postMessage(", js.replace("postMessage({ type: app + ':saved'", '')
                         .replace("postMessage({ type: this.app + ':open'", '').replace("postMessage({ type: appCadre + ':failed'", ''))


if __name__ == '__main__':
    unittest.main()
