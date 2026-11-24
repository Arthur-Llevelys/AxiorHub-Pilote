"""5.6.109, lot V (F16) : éditeurs Craft facultatifs (GridCraft, PdfCraft, DeckCraft) — désactivés par défaut, activables seulement si les
ressources compilées sont présentes et vérifiées ; séances, candidats contrôlés, exports distincts, commandes IA validées par le serveur.

Faux WebDAV en mémoire (``test_v56103_fichiers.FakeNC``), classeur et présentation fictifs (DURAND, MARTIN, DOS-001) ; aucune archive réelle :
une archive d'ESSAI est fabriquée ici et son empreinte substituée en mémoire à celle de la release publiée. Aucun réseau, aucun binaire,
aucun navigateur : le rendu et la fidélité réelle des formats XLSX, PDF et PPTX ne sont PAS qualifiés par ces tests.

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


def archive_essai(app='gridcraft', extra=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('%s-web-0123abcd.js' % app, 'export default async function init(){ return 1; }')
        z.writestr('%s-web-0123abcd_bg.wasm' % app, b'\x00asm\x01\x00\x00\x00')
        z.writestr('index.html', '<!doctype html><title>essai</title>')
        z.writestr('LICENSE-MIT', 'licence fictive')
        z.writestr('outil.exe', b'MZ')                 # type non admis : ni décompressé ni servi
        for n, d in (extra or {}).items():
            z.writestr(n, d)
    return out.getvalue()


class Base(BaseEditeur):
    def setUp(self):
        super().setUp()
        C.ensure_schema(self.desk)
        self.nc.put(XLSX, xlsx())
        self.nc.put(PPTX, pptx())

    def deposer_archive(self, app='gridcraft', data=None):
        data = data or archive_essai(app)
        racine = C.racine(self.desk)
        racine.mkdir(parents=True, exist_ok=True)
        r = C.RELEASES[app]
        (racine / r['archive']).write_bytes(data)
        p = patch.dict(C.RELEASES[app], {'taille': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        p.start()
        self.addCleanup(p.stop)
        return data

    def activer(self, app='gridcraft'):
        self.deposer_archive(app)
        C.installer(self.desk, ADMIN[0], ADMIN[1], app)
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


# ====================================================================================== garde-fou : désactivé par défaut
class Desactive(Base):
    def test_F16_desactive_par_defaut_rien_propose(self):
        self.assertEqual(C.reglages(self.desk), {'gridcraft': False, 'pdfcraft': False, 'deckcraft': False})
        self.assertTrue(all(not C.actif(self.desk, a) for a in C.RELEASES))
        self.assertEqual(C.bouton_html(self.desk, PREFIX, 'nextcloud', XLSX, 'avocat'), '')
        self.assertNotIn('GridCraft', WC.boutons_html(self.desk, None, PREFIX, 'nextcloud', XLSX, 'avocat'))
        out = self.craft('seances', {'app': 'gridcraft', 's': 'nextcloud', 'p': XLSX, 'cle': self.cle()})
        self.assertEqual((out['status'], out['json']['error']), ('403 Forbidden', 'craft_desactive'))
        self.assertTrue(all(v['etat'] == 'absent' for v in C.detecter(self.desk).values()))

    def test_F16_garde_fou_activation_refusee_sans_ressources_compilees(self):
        with self.assertRaisesRegex(Stop, 'ressources_craft_absentes'):
            C.regler(self.desk, ADMIN[0], ADMIN[1], 'gridcraft', True)
        self.assertFalse(C.reglages(self.desk)['gridcraft'])
        # même un réglage forcé dans la base ne propose rien sans ressources installées et vérifiées
        self.desk.setting(C.REGLAGE, {'gridcraft': True})
        self.assertFalse(C.actif(self.desk, 'gridcraft'))
        self.assertEqual(self.craft('reglage', {'app': 'gridcraft', 'active': True, 'cle': self.cle()}, acteur=ADMIN)['json']['error'], 'ressources_craft_absentes')
        self.assertEqual(self.craft('reglage', {'app': 'gridcraft', 'active': True, 'cle': self.cle()})['status'], '403 Forbidden')   # avocat : jamais

    def test_F16_aucun_binaire_livre_ni_telechargement(self):
        statiques = ROOT / 'agent' / 'static'
        self.assertFalse([p for p in statiques.rglob('*') if any(a in p.name.lower() for a in C.RELEASES) and p.suffix in ('.wasm', '.zip', '.js')])
        src = (ROOT / 'agent' / 'craft56109.py').read_text(encoding='utf-8')
        for interdit in ('urlopen', 'urllib.request', 'requests', 'http.client', 'socket', 'subprocess', 'cargo', 'rustup'):
            self.assertNotIn(interdit, src.split('"""', 2)[2])


# ====================================================================================== ressources compilées : détection, installation
class Ressources(Base):
    def test_F16_archive_detectee_installee_puis_servie_selon_le_manifeste(self):
        self.deposer_archive()
        self.assertEqual(C.detecter(self.desk)['gridcraft']['etat'], 'archive_verifiee')
        out = self.craft('installer', {'app': 'gridcraft', 'cle': self.cle()}, acteur=ADMIN)
        self.assertEqual(out['json']['etat'], 'installe', out['json'])
        self.assertEqual(C.detecter(self.desk)['gridcraft']['etat'], 'installe')
        dossier = C.dossier_version(self.desk, 'gridcraft')
        self.assertFalse((dossier / 'outil.exe').exists())
        C.regler(self.desk, ADMIN[0], ADMIN[1], 'gridcraft', True)
        data, kind = C.ressource(self.desk, 'gridcraft-0.19.1', 'gridcraft-web-0123abcd_bg.wasm')
        self.assertEqual(kind, 'application/wasm')
        for absent in ('LICENSE-MIT', 'outil.exe', '../manifeste-axiorhub.json', 'manifeste-axiorhub.json'):
            with self.assertRaises(Stop):
                C.ressource(self.desk, 'gridcraft-0.19.1', absent)
        (dossier / 'gridcraft-web-0123abcd.js').write_text('modifié', encoding='utf-8')     # fichier altéré après installation : jamais servi
        with self.assertRaises(Stop):
            C.ressource(self.desk, 'gridcraft-0.19.1', 'gridcraft-web-0123abcd.js')
        cadre = C._cadre(self.desk, PREFIX, 'gridcraft-0.19.1')
        self.assertIn("frame-ancestors 'self'", cadre['csp'])
        self.assertNotIn('unsafe-inline', cadre['csp'])
        self.assertIn('data-c109-glue="gridcraft-web-0123abcd.js"', cadre['body'])
        self.assertEqual(C.bouton_html(self.desk, PREFIX, 'nextcloud', XLSX, 'avocat').count('GridCraft'), 1)
        self.assertEqual(C.bouton_html(self.desk, PREFIX, 'nextcloud', PPTX, 'avocat'), '')   # DeckCraft non installé

    def test_F16_archive_differente_ou_dangereuse_refusee(self):
        r = C.RELEASES['gridcraft']
        racine = C.racine(self.desk)
        racine.mkdir(parents=True, exist_ok=True)
        (racine / r['archive']).write_bytes(archive_essai())                                # empreinte publiée réelle : différente
        self.assertEqual(C.detecter(self.desk)['gridcraft']['etat'], 'archive_differente')
        with self.assertRaisesRegex(Stop, 'archive_craft_non_verifiee'):
            C.installer(self.desk, ADMIN[0], ADMIN[1], 'gridcraft')
        self.deposer_archive(data=archive_essai(extra={'../echappe.js': 'x'}))
        with self.assertRaisesRegex(Stop, 'archive_craft_refusee'):
            C.installer(self.desk, ADMIN[0], ADMIN[1], 'gridcraft')
        self.assertFalse(C.dossier_version(self.desk, 'gridcraft').exists())
        self.assertFalse((racine.parent / 'echappe.js').exists())
        with self.assertRaisesRegex(Stop, 'role_interdit'):
            C.installer(self.desk, DURAND[0], DURAND[1], 'gridcraft')

    def test_F16_desactiver_sans_perte(self):
        self.activer()
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


# ====================================================================================== commandes IA validées
class CommandesIA(Base):
    def test_F16_plan_ferme_valide_par_le_serveur(self):
        self.activer()
        ident = C.ouvrir(self.desk, self.ident(), DURAND, 'gridcraft', 'nextcloud', XLSX, self.src())['seance']
        ok = self.craft('seances/%s/actions-ia' % ident, {'plan': [{'commande': 'cell.set', 'parametres': {'sheet': 1, 'cell': 'B2', 'value': '1500'}}], 'revision': 0, 'cle': 'plan-c-0001'})
        self.assertEqual(ok['status'], '200 OK', ok['json'])
        self.assertEqual(ok['json']['commandes'][0]['commande'], 'cell.set')
        refus = [([{'commande': 'file.open', 'parametres': {'path': 'C:/x'}}], 'commande_non_autorisee'),
                 ([{'commande': 'execute_command', 'parametres': {}}], 'commande_non_autorisee'),
                 ([{'commande': 'cell.set', 'parametres': {'sheet': 1, 'cell': 'B2', 'value': '=1+1'}}], 'formule_non_demandee'),
                 ([{'commande': 'cell.set', 'parametres': {'sheet': 1, 'cell': 'B2', 'value': '=WEBSERVICE("https://x.example.test")', 'formula': True}}], 'formule_externe_refusee'),
                 ([{'commande': 'cell.set', 'parametres': {'sheet': 1, 'cell': '../B2', 'value': 'x'}}], 'parametre_refuse'),
                 ([{'commande': 'cell.set', 'parametres': {'sheet': 1, 'cell': 'B2', 'value': 'x', 'script': 'y'}}], 'parametre_refuse'),
                 ([{'commande': 'cell.set', 'parametres': {'sheet': 1, 'cell': 'B2', 'value': 'x'}}] * 21, 'plan_refuse')]
        for i, (plan, code) in enumerate(refus):
            out = self.craft('seances/%s/actions-ia' % ident, {'plan': plan, 'revision': 0, 'cle': 'plan-r-%04d' % i})
            self.assertEqual(out['json']['error'], code, plan)
        self.assertEqual(self.craft('seances/%s/actions-ia' % ident, {'plan': [{'commande': 'cell.set', 'parametres': {'sheet': 1, 'cell': 'B3', 'value': 'x'}}],
                                                                       'revision': 5, 'cle': 'plan-p-0001'})['json']['error'], 'version_perimee')

    def test_F16_ia_en_pause_commandes_refusees_saisie_manuelle_possible(self):
        self.activer()
        ident = C.ouvrir(self.desk, self.ident(), DURAND, 'gridcraft', 'nextcloud', XLSX, self.src())['seance']
        with patch('agent.arret5643.manual_paused', return_value=True):
            out = self.craft('seances/%s/actions-ia' % ident, {'plan': [{'commande': 'range.read', 'parametres': {'sheet': 1, 'range': 'A1:B2'}}], 'revision': 0, 'cle': 'plan-z-0001'})
            self.assertEqual(out['json']['error'], 'ia_en_pause')
            cand = self.craft('seances/%s/candidats' % ident, args={'revision': '0', 'cle': 'cand-z-0001'}, binaire=xlsx(('x', '1')))
            self.assertEqual(cand['status'], '200 OK')                                       # l'enregistrement manuel reste possible


class PontJavaScript(unittest.TestCase):
    def test_F16_contrats_du_pont_sous_node(self):
        if not shutil.which('node'):
            self.skipTest('Node absent : contrats du pont non exécutés ici')
        r = subprocess.run(['node', str(ROOT / 'tests' / 'craft56109.cjs')], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('pass 25', r.stdout)

    def test_F16_pont_sans_module_es_ni_html_injecte(self):
        js = (ROOT / 'agent' / 'static' / 'v56109-craft-host.js').read_text(encoding='utf-8')
        self.assertNotIn('innerHTML', js)
        self.assertNotRegex(js, r'^\s*(?:import|export)\s', )
        self.assertIn('event.origin !== this.origin || event.source !== this.frame.contentWindow', js)
        self.assertNotIn("postMessage(", js.replace("postMessage({ type: app + ':host'", '').replace("postMessage({ type: app + ':saved'", '')
                         .replace("postMessage({ type: this.app + ':open'", '').replace("postMessage({ type: appCadre + ':failed'", ''))


if __name__ == '__main__':
    unittest.main()
