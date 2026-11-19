"""5.6.108, lot W : modifier un .docx sans serveur de documents — éditeur intégré WordCraft (version d'essai) et « Ouvrir dans Word /
LibreOffice » sur les postes.

Faux WebDAV en mémoire de la page « Fichiers » (``test_v56103_fichiers.FakeNC`` : ETag, 412, dossiers réservés) ; documents .docx fictifs
(``fixtures_editeur56108``) ; aucun réseau, aucun modèle, aucun logiciel lancé (détection et lancement du traitement de texte injectés).

Identifiants : W1 audit et fichiers servis, W2 routes, rôles et boutons, W3 document passé par AxiorHub (droits, CSRF, corps borné,
idempotence, 409), W4 jamais d'écrasement, W5 garde avant ouverture, W6 contrôle après enregistrement, W7 mesure de fidélité, W8 Word /
LibreOffice du poste, W9 traductions ; R05 = aucune écriture pendant une lecture.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import time
import unittest
from unittest.mock import patch
import zipfile

from test_v56103_fichiers import Base as BaseFichiers, DURAND, ASSISTANT, ADMIN, PREFIX
import fixtures_editeur56108 as FX
from agent import fichiers56103 as F, ouvrir_word56108 as OW, wordcraft56108 as WC
from agent.common import Stop

ROOT = Path(__file__).resolve().parent.parent
STAGIAIRE = ('lecture@example.test', 'lecture')
DOC = '/Dossiers/DEMO/Conclusions DURAND.docx'
RESERVE = '/Dossiers/KERVILAC/Projet KERVILAC.docx'


class Base(BaseFichiers):
    def setUp(self):
        super().setUp()
        self.original = FX.docx_fictif('tableau', 'numerotation', 'notes', 'entetes', 'mise_en_forme')
        self.nc.put(DOC, self.original)
        self.nc.put(RESERVE, FX.docx_fictif(titre='Projet KERVILAC'))
        self.nc.put('/Dossiers/DEMO/Note.pdf', b'%PDF note')
        self.nc.put('/Dossiers/DEMO/Avec date.docx', FX.docx_fictif('champs'))

    def ident(self, poste=False):
        return {'origin': self.origin, 'csrf': 'test-csrf', 'prefix': PREFIX, **({'poste': True} if poste else {})}

    def api(self, route, data=None, acteur=DURAND, methode='POST', args=None, csrf='test-csrf', binaire=None, poste=False, type_=None):
        if binaire is not None:
            raw, ctype = binaire, type_ or 'application/octet-stream'
        else:
            raw, ctype = json.dumps(data or {}).encode(), type_ or 'application/json'
        env = {'REQUEST_METHOD': methode, 'HTTP_ORIGIN': self.origin, 'HTTP_X_CSRF_TOKEN': csrf, 'CONTENT_TYPE': ctype,
               'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw), 'HTTP_X_AXIORHUB_ROLE': acteur[1], 'HTTP_X_AXIORHUB_USER': acteur[0]}
        out = WC.api(env, self.desk, self.ident(poste), PREFIX, WC.PREFIXE + route, args or {}, methode, self.src())
        out['entetes'] = dict(out.get('headers', []))
        try:
            out['json'] = json.loads(out['body'])
        except (ValueError, TypeError):
            out['json'] = None
        return out

    def ouvrir(self, acteur=DURAND, chemin=DOC, accepte=False):
        return WC.ouvrir_seance(self.desk, self.ident(), acteur, 'nextcloud', chemin, 'wordcraft', accepte, self.src())

    def modifie(self, data, texte='[essai] '):
        """Version « enregistrée » : le premier paragraphe reçoit un préfixe, le reste est identique (comme une petite modification)."""
        tampon = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as zin, zipfile.ZipFile(tampon, 'w', zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                contenu = zin.read(info)
                if info.filename == 'word/document.xml':
                    contenu = contenu.replace(b'<w:t xml:space="preserve">Conclusions', ('<w:t xml:space="preserve">' + texte + 'Conclusions').encode(), 1)
                zout.writestr(info, contenu)
        return tampon.getvalue()

    def fichiers_demo(self):
        return sorted(p for p, v in self.nc.t.items() if p.startswith('/Dossiers/DEMO/') and not v['dir'])


# ------------------------------------------------------------------------------------------------ W1 · audit et fichiers servis
class Audit(Base):
    def test_W1_fichiers_de_l_archive_inchanges_empreintes_et_licences(self):
        d = WC.dossier_statique()
        for nom, (kind, sha) in WC.FICHIERS.items():
            self.assertEqual(hashlib.sha256((d / nom).read_bytes()).hexdigest(), sha, nom)
        for nom in WC.LICENCES + ('HOSTING.md', 'README.md', 'index.html'):
            self.assertTrue((d / nom).is_file(), nom)
        self.assertEqual(sum(1 for p in d.rglob('*') if p.is_file()), 26)          # 29 entrées de l'archive, dont 3 dossiers
        self.assertTrue(any(p.name.startswith('OFL-') for p in d.iterdir()))

    def test_W1_audit_reseau_de_la_colle_javascript(self):
        """La colle ne donne au WebAssembly aucun moyen réseau : seul ``init`` lit le .wasm (adresse passée par AxiorHub)."""
        js = (WC.dossier_statique() / WC.GLUE).read_text(encoding='utf-8')
        for interdit in ('XMLHttpRequest', 'WebSocket', 'sendBeacon', 'EventSource', 'importScripts', 'RTCPeerConnection', 'http://', 'https://'):
            self.assertNotIn(interdit, js, interdit)
        self.assertEqual(len(re.findall(r'\bfetch\(', js)), 1)                        # fetch(module_or_path) dans init, rien d'autre
        self.assertNotRegex(js, r'__wbg_fetch_')                                      # aucune liaison fetch exposée au WebAssembly
        self.assertIn('__wbg_open_', js)                                               # window.open (liens cliqués) : neutralisé par le cadre

    def test_W1_csp_inchangee_et_cadre_sans_source_externe(self):
        web = (ROOT / 'agent' / 'web.py').read_text(encoding='utf-8')
        self.assertIn("script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'", web)
        for csp in (WC.CADRE_CSP,):
            self.assertIn("connect-src 'self'", csp)
            self.assertIn("frame-ancestors 'self'", csp)
            self.assertNotRegex(csp, r"https?:|\*|'unsafe-inline'|'unsafe-eval'")

    def test_W1_script_du_lot_neutralise_window_open_et_n_injecte_aucun_html(self):
        js = (ROOT / 'agent' / 'static' / 'v56108-editeur.js').read_text(encoding='utf-8')
        self.assertLess(js.index('window.open = function'), js.index('import(base'))
        self.assertNotIn('innerHTML', js)
        self.assertNotRegex(js, r'https?://')
        self.assertIn("ev.origin !== location.origin", js)                             # messages du cadre : même origine, même fenêtre
        self.assertIn('ev.source !== iframe.contentWindow', js)


# ------------------------------------------------------------------------------------------------ W2 · routes, rôles, réglage, boutons
class Routes(Base):
    def test_W2_statiques_declares_et_servis_en_octets(self):
        from agent import web440
        for nom, (kind, _) in WC.FICHIERS.items():
            self.assertEqual(web440.STATIC['/static/wordcraft-0.18.1/' + nom], kind)
        self.assertIn('/static/v56108-editeur.js', web440.STATIC)
        self.assertNotIn('/static/wordcraft-0.18.1/index.html', web440.STATIC)        # page d'origine (script en ligne) jamais servie
        out = web440.route({'REQUEST_METHOD': 'GET'}, self.desk.c, self.ident(), PREFIX, '/static/wordcraft-0.18.1/' + WC.SNIPPET, {}, 'GET')
        self.assertTrue(out['status'].startswith('200'))
        from agent import shell501
        self.assertIn('v56108-editeur.css', shell501.BUNDLE)
        self.assertIn('.w108-zone', (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8'))

    def test_W2_liste_fermee_des_routes_et_roles(self):
        from agent.standalone_auth import allowed
        for role in ('avocat', 'assistant'):
            for r in ('ouvrir', 'enregistrer', 'decider', 'fermer', 'word/ouvrir', 'word/reprendre'):
                self.assertTrue(allowed(role, 'POST', '/api440/m56108/editeur/' + r), (role, r))
            self.assertFalse(allowed(role, 'POST', '/api440/m56108/editeur/reglage'))
            self.assertFalse(allowed(role, 'POST', '/api440/m56108/editeur/inconnue'))
        self.assertTrue(allowed('administrateur', 'POST', '/api440/m56108/editeur/reglage'))
        self.assertFalse(WC.allowed('lecture', 'POST', WC.PREFIXE + 'ouvrir'))
        self.assertTrue(WC.allowed('lecture', 'GET', WC.PREFIXE + 'fichier'))
        r = self.api('reglage', {'active': False, 'cle': 'cle-reglage-0001'}, acteur=DURAND)
        self.assertTrue(r['status'].startswith('403'))
        r = self.api('inconnue', {})
        self.assertTrue(r['status'].startswith('404'))

    def test_W2_reglage_administrateur_coupe_wordcraft_partout(self):
        self.assertTrue(WC.actif(self.desk))                                          # décision après mesure : proposé par défaut
        r = self.api('reglage', {'active': False, 'cle': 'cle-reglage-0002'}, acteur=ADMIN)
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertFalse(WC.actif(self.desk))
        self.assertEqual(WC.liens(self.desk, self.ident(), PREFIX, 'nextcloud', DOC, 'avocat'), {})
        r = self.api('ouvrir', {'s': 'nextcloud', 'p': DOC, 'cle': 'cle-ouvrir-0001'})
        self.assertTrue(r['status'].startswith('403'))
        self.assertEqual(r['json']['error'], 'wordcraft_desactive')
        self.assertIn('Proposer « Modifier dans l’éditeur intégré »', WC.section_reglages(self.desk))
        with patch.object(self.desk, 'settings', side_effect=RuntimeError('base illisible')):
            self.assertFalse(WC.actif(self.desk))                                     # panne : rien de proposé, jamais l'inverse

    def test_W2_liens_seulement_pour_un_docx_et_les_roles_d_edition(self):
        l = WC.liens(self.desk, self.ident(), PREFIX, 'nextcloud', DOC, 'avocat')
        self.assertTrue(l['wordcraft_url'].startswith(PREFIX + '/editeur/wordcraft?'))
        self.assertNotIn('traitement_url', l)                                         # version serveur : pas de Word du poste
        self.assertIn('traitement_url', WC.liens(self.desk, self.ident(poste=True), PREFIX, 'nextcloud', DOC, 'avocat'))
        self.assertEqual(WC.liens(self.desk, self.ident(), PREFIX, 'nextcloud', '/Dossiers/DEMO/Note.pdf', 'avocat'), {})
        self.assertEqual(WC.liens(self.desk, self.ident(), PREFIX, 'nextcloud', DOC, 'lecture'), {})

    def test_W2_boutons_apercu_fichiers_et_produire(self):
        from agent import fichiers_web56103, web5679
        with patch('agent.fichiers5670.client', return_value=self.dav):
            html = fichiers_web56103.page_html(self.desk, self.ident(), DURAND, PREFIX)
        self.assertIn('data-f103="wordcraft"', html)
        self.assertIn('Modifier dans l’éditeur intégré (WordCraft, version d’essai)', html)
        self.assertNotIn('data-f103="traitement"', html)
        js = (ROOT / 'agent' / 'static' / 'v56103-fichiers.js').read_text(encoding='utf-8')
        self.assertIn("nom === 'wordcraft' || nom === 'traitement'", js)
        from agent import dossier5679
        self.assertIn('data-d79-wordcraft', dossier5679.PREVIEW)
        self.assertIn('data-d79-traitement', dossier5679.PREVIEW)
        with patch('agent.apercu5679._download', return_value=self.original), patch('agent.apercu5679._matter_file', return_value=({'id': 'DOS-001'}, DOC)):
            out = web5679.route(self.desk.c, self.ident(poste=True), PREFIX, '/dossier/apercu', {'matter': 'DOS-001', 'path': DOC})
        vue = json.loads(out['body'])
        self.assertTrue(vue['wordcraft_url'].startswith(PREFIX + '/editeur/wordcraft?'))
        self.assertTrue(vue['traitement_url'].startswith(PREFIX + '/editeur/traitement?'))
        b = WC.boutons_html(self.desk, None, PREFIX, 'nextcloud', DOC, 'avocat', 'Conclusions')
        self.assertIn('Modifier dans l’éditeur intégré (WordCraft, version d’essai)<span class="sr-only"> : Conclusions</span>', b)
        src = (ROOT / 'agent' / 'produire56100.py').read_text(encoding='utf-8')
        self.assertIn('wordcraft56108.boutons_html(desk, None, prefix, \'nextcloud\', row[\'path\']', src)

    def test_W2_reglage_affiche_dans_les_reglages_de_l_atelier(self):
        from agent import web440
        html = web440.settings_page(self.desk, self.ident(), PREFIX, {}, render=lambda t, b, *a, **k: b)
        self.assertIn('id="ax-wordcraft-56108"', html)
        self.assertIn('data-api="m56108/editeur/reglage"', html)


# ------------------------------------------------------------------------------------------------ W3 · le document passe par AxiorHub
class Passage(Base):
    def test_W3_dossier_reserve_et_absent_meme_404_neutre(self):
        for chemin in (RESERVE, '/Dossiers/DEMO/Absent.docx', '/Dossiers/../etc/passwd'):
            with self.assertRaises(Stop) as ex:
                self.ouvrir(chemin=chemin)
            self.assertEqual(str(ex.exception), 'introuvable', chemin)
            r = self.api('ouvrir', {'s': 'nextcloud', 'p': chemin, 'cle': 'cle-o-' + hashlib.sha256(chemin.encode()).hexdigest()[:12]})
            self.assertTrue(r['status'].startswith('404'), (chemin, r['status']))
            self.assertEqual(r['json']['message'], WC.MESSAGES['introuvable'])
        s = self.ouvrir(chemin=RESERVE.replace('KERVILAC/Projet KERVILAC', 'DEMO/Conclusions DURAND'))
        self.assertTrue(s['seance'])

    def test_W3_controle_des_acces_illisible_503(self):
        with patch('agent.membres5650.load', side_effect=Stop('controle_acces_indisponible')):
            r = self.api('ouvrir', {'s': 'nextcloud', 'p': DOC, 'cle': 'cle-ouvrir-0503'})
        self.assertTrue(r['status'].startswith('503'), r['status'])

    def test_W3_seance_d_un_autre_compte_404_et_droits_revus_a_chaque_lecture(self):
        s = self.ouvrir()
        r = self.api('fichier', methode='GET', args={'seance': s['seance']}, acteur=ASSISTANT)
        self.assertTrue(r['status'].startswith('404'))
        r = self.api('fichier', methode='GET', args={'seance': s['seance']})
        self.assertTrue(r['status'].startswith('200'))
        self.assertEqual(r['body'], self.original)
        self.assertEqual(r['entetes']['Cache-Control'], 'private, no-store')
        from agent import acces5697
        with patch.object(F.SourceNextcloud, '_caches', return_value=['/Dossiers/DEMO']):   # dossier réservé depuis l'ouverture
            r = self.api('fichier', methode='GET', args={'seance': s['seance']})
        self.assertTrue(r['status'].startswith('404'))

    def test_W3_enregistrement_csrf_type_corps_borne(self):
        s = self.ouvrir()
        args = {'seance': s['seance'], 'revision': '0', 'cle': 'cle-enr-00001'}
        r = self.api('enregistrer', binaire=self.original, args=args, csrf='mauvais')
        self.assertTrue(r['status'].startswith('403'))
        r = self.api('enregistrer', binaire=self.original, args=args, type_='application/json')
        self.assertEqual(r['json']['error'], 'type_document_invalide')
        with patch.object(WC, 'MAX_DOCX', 1000):
            r = self.api('enregistrer', binaire=self.original, args=args)
        self.assertTrue(r['status'].startswith('413'))
        r = self.api('enregistrer', binaire=b'PK\x03\x04pas un docx', args={**args, 'cle': 'cle-enr-00002'})
        self.assertEqual(r['json']['error'], 'document_word_illisible')
        self.assertEqual(self.nc.lire(DOC), self.original)

    def test_W3_idempotence_et_revision_attendue_409(self):
        s = self.ouvrir()
        v1 = self.modifie(self.original)
        args = {'seance': s['seance'], 'revision': '0', 'cle': 'cle-enr-00010'}
        r1 = self.api('enregistrer', binaire=v1, args=args)
        self.assertTrue(r1['status'].startswith('200'), r1['body'])
        self.assertEqual(r1['json']['revision'], 1)
        r2 = self.api('enregistrer', binaire=v1, args=args)                            # même clé, même corps : même reçu
        self.assertTrue(r2['json'].get('rejoue'))
        self.assertEqual(r2['json']['revision'], 1)
        r3 = self.api('enregistrer', binaire=self.modifie(self.original, 'autre '), args=args)   # même clé, autre corps
        self.assertEqual(r3['json']['error'], 'cle_reutilisee')
        self.assertTrue(r3['status'].startswith('409'))
        r4 = self.api('enregistrer', binaire=v1, args={**args, 'cle': 'cle-enr-00011'})          # révision périmée (autre onglet)
        self.assertEqual(r4['json']['error'], 'version_perimee')
        self.assertTrue(r4['status'].startswith('409'))
        r5 = self.api('decider', {'seance': s['seance'], 'revision': 0, 'decision': 'garder', 'cle': 'cle-dec-00001'})
        self.assertTrue(r5['status'].startswith('409'))


# ------------------------------------------------------------------------------------------------ W4 · jamais d'écrasement
class Ecrasement(Base):
    def garder(self, s, data, cle, malgre=False):
        p = WC.proposer(self.desk, self.ident(), DURAND, s['seance'], s.get('revision', 0), data, self.src())
        s['revision'] = p['revision']
        return WC.decider(self.desk, self.ident(), DURAND, s['seance'], p['revision'], 'garder', malgre, self.src())

    def test_W4_garder_cree_un_fichier_voisin_l_original_reste_intact(self):
        s = self.ouvrir()
        out = self.garder(s, self.modifie(self.original), 'k1')
        self.assertEqual(self.nc.lire(DOC), self.original)
        self.assertRegex(out['chemin'], r'^/Dossiers/DEMO/Conclusions DURAND \(modifié dans WordCraft \d\d-\d\d-\d{4} \d\dh\d\d\)\.docx$')
        self.assertEqual(hashlib.sha256(self.nc.lire(out['chemin'])).hexdigest(), out['sha256'])
        out2 = self.garder(s, self.modifie(self.original, 'deux '), 'k2')       # même minute : nom suivant, rien de remplacé
        self.assertNotEqual(out2['chemin'], out['chemin'])
        self.assertTrue(out2['chemin'].endswith(' 2).docx'))
        self.assertEqual(self.nc.lire(DOC), self.original)
        journal = [r[0] for r in self.desk.db.execute("SELECT action FROM audit WHERE action LIKE 'editeur56108_%'")]
        self.assertIn('editeur56108_version_gardee', journal)

    def test_W4_mutation_une_ecriture_conditionnelle_remplacerait_l_original(self):
        """Garde-fou : l'écriture passe ``etag=None`` (création exclusive). Mutation en mémoire : avec l'ETag de l'original, la même décision
        le remplacerait — le contrôle « original intact » de ce test le verrait."""
        s = self.ouvrir()
        vraie = F.SourceNextcloud.ecrire
        etag = self.entree(DOC)['etag']

        def mutante(source, chemin, octets, etag_=None):
            return vraie(source, DOC, octets, etag)
        with patch.object(F.SourceNextcloud, 'ecrire', mutante), patch.object(F.SourceNextcloud, 'lire', lambda *a, **k: self.nc.lire(DOC)):
            try:
                self.garder(s, self.modifie(self.original), 'k3')
            except Stop:
                pass
        self.assertNotEqual(self.nc.lire(DOC), self.original)                       # le contrôle détecte bien un écrasement

    def test_W4_original_change_depuis_l_ouverture_409_puis_copie_distincte(self):
        s = self.ouvrir()
        self.nc.put(DOC, self.modifie(self.original, 'MARTIN '))                          # quelqu'un modifie l'original entre-temps
        change = self.nc.lire(DOC)
        with self.assertRaises(Stop) as ex:
            self.garder(s, self.modifie(self.original), 'k4')
        self.assertEqual(str(ex.exception), 'original_modifie')
        p = WC.seance(self.desk, DURAND, s['seance'])['candidat_rev']
        out = WC.decider(self.desk, self.ident(), DURAND, s['seance'], p, 'garder', True, self.src())
        self.assertEqual(self.nc.lire(DOC), change)
        self.assertIn(out['chemin'], self.nc.t)

    def test_W4_abandonner_n_ecrit_rien_et_efface_le_candidat(self):
        s = self.ouvrir()
        avant = self.fichiers_demo()
        p = WC.proposer(self.desk, self.ident(), DURAND, s['seance'], 0, self.modifie(self.original), self.src())
        self.assertTrue((WC.dossier_seance(self.desk, s['seance']) / 'candidat-1.docx').is_file())
        out = WC.decider(self.desk, self.ident(), DURAND, s['seance'], p['revision'], 'abandonner', False, self.src())
        self.assertEqual(out['decision'], 'abandonner')
        self.assertFalse((WC.dossier_seance(self.desk, s['seance']) / 'candidat-1.docx').exists())
        self.assertEqual(self.fichiers_demo(), avant)
        with self.assertRaises(Stop):
            WC.decider(self.desk, self.ident(), DURAND, s['seance'], p['revision'], 'garder', False, self.src())

    def test_W4_fermer_efface_les_copies_privees(self):
        s = self.ouvrir()
        d = WC.dossier_seance(self.desk, s['seance'])
        self.assertTrue((d / 'original.docx').is_file())
        WC.fermer(self.desk, DURAND, s['seance'])
        self.assertFalse(d.exists())
        with self.assertRaises(Stop):
            WC.seance(self.desk, DURAND, s['seance'])
        vieux = WC.dossier_seance(self.desk, 'a' * 32)
        vieux.mkdir(parents=True)
        self.assertEqual(WC.maintenance(self.desk), 1)                                  # dossier orphelin repris par la maintenance

    def test_W4_copie_privee_alteree_rien_n_est_ecrit(self):
        s = self.ouvrir()
        p = WC.proposer(self.desk, self.ident(), DURAND, s['seance'], 0, self.modifie(self.original), self.src())
        (WC.dossier_seance(self.desk, s['seance']) / 'candidat-1.docx').write_bytes(self.original)
        avant = self.fichiers_demo()
        with self.assertRaises(Stop) as ex:
            WC.decider(self.desk, self.ident(), DURAND, s['seance'], p['revision'], 'garder', False, self.src())
        self.assertEqual(str(ex.exception), 'copie_privee_alteree')
        self.assertEqual(self.fichiers_demo(), avant)


# ------------------------------------------------------------------------------------------------ W5 · garde avant ouverture
class Garde(Base):
    def test_W5_analyse_deterministe_des_caracteristiques(self):
        a = WC.analyser(FX.docx_fictif(*FX.JEU['fictif-complet']))
        for code in ('suivi', 'commentaires', 'champs', 'champs_date', 'controles', 'numerotation', 'notes', 'tableaux', 'entetes', 'pieds',
                     'entetes_complexes', 'images', 'zones_texte', 'signets', 'liens', 'gras', 'italique', 'souligne', 'sauts_page'):
            self.assertGreater(a[code], 0, code)
        self.assertEqual(a['sections'], 2)
        self.assertEqual(WC.analyser(FX.docx_fictif('macros'))['macros'], 1)
        self.assertEqual(WC.analyser(FX.docx_fictif('objet'))['objets'], 1)
        self.assertEqual(WC.analyser(FX.docx_fictif('protection'))['protection'], 1)
        self.assertEqual(a, WC.analyser(FX.docx_fictif(*FX.JEU['fictif-complet'])))

    def test_W5_fichier_hostile_refuse_proprement(self):
        for raw in (b'', b'pas un zip', b'PK\x03\x04abc'):
            with self.assertRaises(Stop):
                WC.analyser(raw)
        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, 'w') as z:
            z.writestr('word/document.xml', '<!DOCTYPE x [<!ENTITY a "b">]><w:document xmlns:w="%s"><w:body/></w:document>' % FX.W)
        with self.assertRaises(Stop) as ex:
            WC.analyser(tampon.getvalue())
        self.assertEqual(str(ex.exception), 'document_word_illisible')

    def test_W5_niveaux_de_la_garde_d_apres_la_mesure(self):
        self.assertEqual(WC.garde(WC.analyser(FX.docx_fictif()))['niveau'], 'ok')
        for opt in ('champs', 'macros', 'objet'):
            g = WC.garde(WC.analyser(FX.docx_fictif(opt)))
            self.assertEqual(g['niveau'], 'risque', opt)
        g = WC.garde(WC.analyser(FX.docx_fictif('suivi', 'commentaires')))
        self.assertEqual(g['niveau'], 'ok')
        self.assertEqual({c['code'] for c in g['constats']}, {'suivi', 'commentaires'})
        texte = ' '.join(c['etat'] for c in WC.garde(WC.analyser(FX.docx_fictif('champs')))['constats'])
        self.assertIn('November', texte)

    def test_W5_pas_d_ouverture_silencieuse_d_un_document_a_risque(self):
        chemin = '/Dossiers/DEMO/Avec date.docx'
        with self.assertRaises(Stop) as ex:
            self.ouvrir(chemin=chemin)
        self.assertEqual(str(ex.exception), 'garde_avertissement')
        r = self.api('ouvrir', {'s': 'nextcloud', 'p': chemin, 'cle': 'cle-ouvrir-r001'})
        self.assertTrue(r['status'].startswith('409'))
        s = self.ouvrir(chemin=chemin, accepte=True)
        self.assertEqual(s['garde']['niveau'], 'risque')
        trace = json.loads(self.desk.db.execute("SELECT data FROM audit WHERE action='editeur56108_ouvert' ORDER BY id DESC").fetchone()[0])
        self.assertTrue(trace['risques_acceptes'])

    def test_W5_mutation_garde_retiree_le_test_le_verrait(self):
        with patch.object(WC, 'garde', lambda a: {'niveau': 'ok', 'constats': []}):
            s = self.ouvrir(chemin='/Dossiers/DEMO/Avec date.docx')                     # sans la garde : ouverture silencieuse
        self.assertTrue(s['seance'])

    def test_W5_page_avertit_et_propose_word_ou_le_serveur(self):
        corps = self.page('/editeur/wordcraft', {'s': 'nextcloud', 'p': '/Dossiers/DEMO/Avec date.docx'}, poste=True)
        self.assertIn('Attention : ce document contient des éléments que l’éditeur intégré abîme', corps)
        self.assertIn('Ouvrir dans Word / LibreOffice', corps)
        self.assertIn('data-w108="ouvrir" disabled', corps)
        self.assertIn('data-w108-accepte', corps)

    def page(self, chemin, args, acteur=DURAND, poste=False):
        out = WC.page_route({'HTTP_X_AXIORHUB_USER': acteur[0], 'HTTP_X_AXIORHUB_ROLE': acteur[1]}, self.desk.c, self.ident(poste), PREFIX, chemin, args,
                            lambda titre, corps, *a, **k: corps, self.src())
        self.dernier = out
        return out['body']


# ------------------------------------------------------------------------------------------------ W6 · contrôle après enregistrement
class Controle(Base):
    def retirer(self, data, *motifs):
        tampon = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as zin, zipfile.ZipFile(tampon, 'w', zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                contenu = zin.read(info)
                if info.filename == 'word/document.xml':
                    for a, b in motifs:
                        contenu = re.sub(a.encode(), b.encode(), contenu, flags=re.S)
                zout.writestr(info, contenu)
        return tampon.getvalue()

    def test_W6_identique_aucune_perte(self):
        a = WC.analyser(self.original)
        r = WC.controler(a, WC.analyser(self.original))
        self.assertTrue(r['identique'])
        self.assertEqual(r['pertes'], [])

    def test_W6_chaque_perte_est_signalee_avant_de_garder(self):
        complet = FX.docx_fictif(*FX.JEU['fictif-complet'])
        a = WC.analyser(complet)
        cas = {
            'suivi': [(r'<w:del .*?</w:del>', ''), (r'<w:ins [^>]*>(.*?)</w:ins>', r'\1')],
            'commentaires': [(r'<w:commentRangeStart[^>]*/>|<w:commentRangeEnd[^>]*/>|<w:r><w:commentReference[^>]*/></w:r>', '')],
            'tableaux': [(r'<w:tbl>.*?</w:tbl>', '')],
            'notes': [(r'<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:footnoteReference w:id="1"/></w:r>', '')],
            'numerotation': [(r'<w:numPr>.*?</w:numPr>', '')],
            'sections': [(r'<w:pPr><w:sectPr>.*?</w:sectPr></w:pPr>', '')],
            'controles': [(r'<w:sdt><w:sdtPr>.*?</w:sdtPr><w:sdtContent>(.*?)</w:sdtContent></w:sdt>', r'\1')],
            'texte': [(r'<w:p><w:r><w:t xml:space="preserve">Sous toutes réserves.</w:t></w:r></w:p>', '')],
            'gras': [(r'<w:rPr><w:b/></w:rPr>', '')],
        }
        for code, motifs in cas.items():
            r = WC.controler(a, WC.analyser(self.retirer(complet, *motifs)))
            self.assertIn(code, [p['code'] for p in r['pertes']], code)
            self.assertFalse(r['identique'], code)
            self.assertIn('à vérifier', r['resume'], code)

    def test_W6_texte_paragraphe_par_paragraphe(self):
        a = WC.analyser(self.original)
        r = WC.controler(a, WC.analyser(self.modifie(self.original)))
        self.assertEqual((r['texte']['modifies'], r['texte']['ajoutes'], r['texte']['supprimes']), (1, 0, 0))
        self.assertEqual(r['pertes'], [])
        self.assertEqual(r['texte']['details'][0]['position'], 1)
        self.assertIn('[essai] Conclusions', r['texte']['details'][0]['apres'])

    def test_W6_police_du_theme_signalee_sans_alarme(self):
        a = WC.analyser(self.original)
        b = dict(a)
        a = dict(a, polices_theme=12)
        r = WC.controler(a, b)
        self.assertTrue(r['identique'])
        self.assertTrue(r['pertes'][0]['mineur'])
        self.assertIn('sans effet visible', r['resume'])

    def test_W6_la_proposition_rend_le_controle_avant_toute_ecriture(self):
        s = self.ouvrir()
        avant = self.fichiers_demo()
        abime = self.retirer(self.original, (r'<w:tbl>.*?</w:tbl>', ''))
        r = self.api('enregistrer', binaire=abime, args={'seance': s['seance'], 'revision': '0', 'cle': 'cle-enr-06001'})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertIn('tableaux', [p['code'] for p in r['json']['controle']['pertes']])
        self.assertEqual(self.fichiers_demo(), avant)                                   # rien d'écrit avant « Garder »


# ------------------------------------------------------------------------------------------------ W7 · mesure de fidélité
class Fidelite(unittest.TestCase):
    def test_W7_rapport_de_mesure_et_table_de_la_garde_concordent(self):
        doc = (ROOT / 'docs' / 'WORDCRAFT-FIDELITE-5.6.108.md').read_text(encoding='utf-8')
        for mot in ('257 fichiers', 'Audit réseau', 'Chromium', 'activé par défaut', 'champs de date', 'macros'):
            self.assertIn(mot, doc)
        for code, etat in WC.FIDELITE.items():
            self.assertIn(etat, ('conserve', 'altere', 'perdu', 'mineur', 'non_mesure'), code)
        self.assertEqual({c for c, e in WC.FIDELITE.items() if e in ('altere', 'perdu')}, {'champs_date', 'objets', 'macros'})
        script = (ROOT / 'scripts' / 'fidelite_wordcraft56108.py').read_text(encoding='utf-8')
        self.assertIn("'127.0.0.1'", script)
        self.assertNotRegex(script, r'https?://(?!127\.0\.0\.1)')


# ------------------------------------------------------------------------------------------------ W8 · Word / LibreOffice du poste
class Poste(Base):
    def setUp(self):
        super().setUp()
        self.lances = []
        self.formats = []
        p1 = patch.object(OW, 'DETECTER', lambda f='docx': self.formats.append(f) or {'nom': 'LibreOffice' if f == 'docx' else 'PDF Chisel', 'systeme': 'linux',
                                                                                      'format': f})
        p2 = patch.object(OW, 'LANCER', lambda chemin, info: self.lances.append(Path(chemin)))
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def test_W8_indisponible_en_version_serveur_et_sans_traitement_de_texte(self):
        with self.assertRaises(Stop) as ex:
            OW.ouvrir(self.desk, self.ident(), DURAND, 'nextcloud', DOC, self.src())
        self.assertEqual(str(ex.exception), 'indisponible_sur_serveur')
        with patch.object(OW, 'DETECTER', lambda *a: None), self.assertRaises(Stop) as ex:
            OW.ouvrir(self.desk, self.ident(poste=True), DURAND, 'nextcloud', DOC, self.src())
        self.assertEqual(str(ex.exception), 'aucun_traitement_de_texte')
        self.assertEqual(self.lances, [])
        page = WC.page_route({'HTTP_X_AXIORHUB_USER': DURAND[0], 'HTTP_X_AXIORHUB_ROLE': DURAND[1]}, self.desk.c, self.ident(), PREFIX,
                             '/editeur/traitement', {'s': 'nextcloud', 'p': DOC}, lambda t, c, *a, **k: c, self.src())['body']
        self.assertIn('Indisponible en version serveur', page)
        with patch.object(OW, 'DETECTER', lambda *a: None):
            page = WC.page_route({'HTTP_X_AXIORHUB_USER': DURAND[0], 'HTTP_X_AXIORHUB_ROLE': DURAND[1]}, self.desk.c, self.ident(poste=True), PREFIX,
                                 '/editeur/traitement', {'s': 'nextcloud', 'p': DOC}, lambda t, c, *a, **k: c, self.src())['body']
        self.assertIn('Aucun traitement de texte n’est installé sur ce poste', page)

    def test_W8_copie_de_travail_surveillee_puis_reprise_comme_nouvelle_version(self):
        auth = self.ident(poste=True)
        s = OW.ouvrir(self.desk, auth, DURAND, 'nextcloud', DOC, self.src())
        copie = self.lances[0]
        self.assertTrue(str(copie).startswith(str(Path(self.desk.c['state_dir']) / 'editeur56108')))   # jamais l'original
        self.assertEqual(copie.read_bytes(), self.original)
        e0 = OW.etat(self.desk, auth, DURAND, s['seance'], self.src())
        self.assertFalse(e0['modifie'])
        with self.assertRaises(Stop) as ex:
            OW.reprendre(self.desk, auth, DURAND, s['seance'], e0['sha256'], self.src())
        self.assertEqual(str(ex.exception), 'aucune_modification')
        copie.write_bytes(self.modifie(self.original))                                  # « Ctrl+S » dans le traitement de texte
        e1 = OW.etat(self.desk, auth, DURAND, s['seance'], self.src())
        self.assertTrue(e1['modifie'])
        with self.assertRaises(Stop) as ex:
            OW.reprendre(self.desk, auth, DURAND, s['seance'], e0['sha256'], self.src())   # empreinte montrée périmée
        self.assertEqual(str(ex.exception), 'version_perimee')
        p = OW.reprendre(self.desk, auth, DURAND, s['seance'], e1['sha256'], self.src())
        self.assertEqual(p['controle']['texte']['modifies'], 1)
        self.assertTrue(OW.etat(self.desk, auth, DURAND, s['seance'], self.src())['deja_repris'])
        r = self.api('decider', {'seance': s['seance'], 'revision': p['revision'], 'decision': 'garder', 'cle': 'cle-dec-08001'}, poste=True)
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertIn('(modifié sur le poste ', r['json']['chemin'])
        self.assertEqual(self.nc.lire(DOC), self.original)
        self.assertFalse(copie.exists())                                                 # copie de travail nettoyée après reprise
        with self.assertRaises(Stop):
            OW.etat(self.desk, auth, DURAND, s['seance'], self.src())

    def test_W8_abandon_de_la_copie_de_travail(self):
        auth = self.ident(poste=True)
        s = OW.ouvrir(self.desk, auth, DURAND, 'nextcloud', DOC, self.src())
        r = self.api('fermer', {'seance': s['seance'], 'cle': 'cle-fer-08002'}, poste=True)
        self.assertTrue(r['status'].startswith('200'))
        self.assertFalse(self.lances[0].exists())
        self.assertEqual(self.nc.lire(DOC), self.original)

    def test_W8_lancement_sans_interpreteur_de_commandes(self):
        src = (ROOT / 'agent' / 'ouvrir_word56108.py').read_text(encoding='utf-8')
        self.assertNotIn('shell=True', src)
        self.assertNotIn('os.system', src)
        appels = []
        with patch('subprocess.Popen', lambda cmd, **k: appels.append((cmd, k))):
            OW._lancer(Path('/tmp/x/doc.docx'), {'systeme': 'linux'})
            OW._lancer(Path('/tmp/x/doc.pdf'), {'systeme': 'linux', 'format': 'pdf'})
            OW._lancer(Path('/tmp/x/doc.docx'), {'systeme': 'linux', 'commande': '/usr/bin/soffice', 'format': 'docx'})
        self.assertEqual(appels[0][0], ['xdg-open', str(Path('/tmp/x/doc.docx'))])
        self.assertEqual(appels[1][0], ['xdg-open', str(Path('/tmp/x/doc.pdf'))])
        self.assertEqual(appels[2][0], ['/usr/bin/soffice', '--writer', str(Path('/tmp/x/doc.docx'))])


def pdf_fictif(pages):
    from agent._vendor.pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=595, height=842)
    tampon = io.BytesIO()
    w.write(tampon)
    return tampon.getvalue()


class PostePdf(Poste):
    PDF = '/Dossiers/DEMO/Bordereau DURAND.pdf'

    def setUp(self):
        super().setUp()
        self.pdf = pdf_fictif(2)
        self.nc.put(self.PDF, self.pdf)

    def page_t(self, chemin, poste=True):
        return WC.page_route({'HTTP_X_AXIORHUB_USER': DURAND[0], 'HTTP_X_AXIORHUB_ROLE': DURAND[1]}, self.desk.c, self.ident(poste), PREFIX,
                             '/editeur/traitement', {'s': 'nextcloud', 'p': chemin}, lambda t, c, *a, **k: c, self.src())['body']

    def test_W8_pdf_libelle_et_liens_application_du_poste(self):
        l = WC.liens(self.desk, self.ident(poste=True), PREFIX, 'nextcloud', self.PDF, 'avocat')
        self.assertNotIn('wordcraft_url', l)                                             # WordCraft : .docx seulement
        self.assertEqual(l['traitement_libelle'], 'Ouvrir avec l’application du poste')
        self.assertEqual(WC.liens(self.desk, self.ident(poste=True), PREFIX, 'nextcloud', DOC, 'avocat')['traitement_libelle'], 'Ouvrir dans Word / LibreOffice')
        self.assertEqual(WC.liens(self.desk, self.ident(), PREFIX, 'nextcloud', self.PDF, 'avocat'), {})          # version serveur : rien
        self.assertIn('Ouvrir avec l’application du poste', WC.boutons_html(self.desk, self.ident(poste=True), PREFIX, 'nextcloud', self.PDF, 'avocat'))
        page = self.page_t(self.PDF)
        self.assertIn('Ouvrir avec l’application du poste', page)
        self.assertIn('copie de travail dans PDF Chisel', page)
        self.assertIn('Indisponible en version serveur', self.page_t(self.PDF, poste=False))
        with patch.object(OW, 'DETECTER', lambda *a: None):
            self.assertIn('Aucune application n’est associée aux PDF sur ce poste', self.page_t(self.PDF))
            with self.assertRaises(Stop) as ex:
                OW.ouvrir(self.desk, self.ident(poste=True), DURAND, 'nextcloud', self.PDF, self.src())
            self.assertEqual(str(ex.exception), 'aucune_application_pdf')
        self.assertEqual(self.lances, [])
        with self.assertRaises(Stop) as ex:   # WordCraft ne s'ouvre jamais sur un PDF
            self.ouvrir(chemin=self.PDF)
        self.assertEqual(str(ex.exception), 'format_non_docx')

    def test_W8_pdf_copie_reprise_controle_et_nouvelle_version(self):
        auth = self.ident(poste=True)
        s = OW.ouvrir(self.desk, auth, DURAND, 'nextcloud', self.PDF, self.src())
        self.assertEqual(self.formats[-1], 'pdf')
        copie = self.lances[0]
        self.assertTrue(copie.name.endswith('.pdf'))
        self.assertTrue(str(copie).startswith(str(Path(self.desk.c['state_dir']) / 'editeur56108')))   # jamais l'original
        copie.write_bytes(pdf_fictif(1))                                                  # une page retirée dans l'application
        e1 = OW.etat(self.desk, auth, DURAND, s['seance'], self.src())
        p = OW.reprendre(self.desk, auth, DURAND, s['seance'], e1['sha256'], self.src())
        self.assertEqual([x['code'] for x in p['controle']['pertes']], ['pages'])          # perte montrée avant « Garder »
        copie.write_bytes(pdf_fictif(3))                                                  # une page ajoutée
        e2 = OW.etat(self.desk, auth, DURAND, s['seance'], self.src())
        p = OW.reprendre(self.desk, auth, DURAND, s['seance'], e2['sha256'], self.src())
        self.assertEqual(p['controle']['pertes'], [])
        self.assertEqual(p['controle']['texte']['ajoutes'], 1)
        out = WC.decider(self.desk, auth, DURAND, s['seance'], p['revision'], 'garder', False, self.src())
        self.assertRegex(out['chemin'], r'^/Dossiers/DEMO/Bordereau DURAND \(modifié sur le poste .*\)\.pdf$')
        self.assertEqual(self.nc.lire(self.PDF), self.pdf)
        from agent._vendor.pypdf import PdfReader
        self.assertEqual(len(PdfReader(io.BytesIO(self.nc.lire(out['chemin']))).pages), 3)

    def test_W8_pdf_illisible_refuse(self):
        for raw in (b'%PDF-1.7 tronque', self.original, b''):
            with self.assertRaises(Stop) as ex:
                WC.analyser_pdf(raw)
            self.assertEqual(str(ex.exception), 'document_pdf_illisible')


# ------------------------------------------------------------------------------------------------ R05 · lectures sans écriture
class Lectures(Base):
    def test_R05_page_et_lectures_n_ecrivent_rien(self):
        s = self.ouvrir()
        avant = self.desk.db.total_changes
        corps = WC.page_route({'HTTP_X_AXIORHUB_USER': DURAND[0], 'HTTP_X_AXIORHUB_ROLE': DURAND[1]}, self.desk.c, self.ident(), PREFIX,
                              '/editeur/wordcraft', {'s': 'nextcloud', 'p': DOC}, lambda t, c, *a, **k: c, self.src())
        self.assertTrue(corps['status'].startswith('200'))
        self.assertEqual(corps['headers'][-1], ('Cache-Control', 'private, no-store'))
        self.api('fichier', methode='GET', args={'seance': s['seance']})
        self.assertEqual(self.desk.db.total_changes, avant)
        nb = self.desk.db.execute('SELECT COUNT(*) FROM editeur56108_seances').fetchone()[0]
        self.assertEqual(nb, 1)                                                          # afficher la page n'ouvre aucune séance

    def test_R05_cadre_page_a_part_sans_gabarit_ni_source_externe(self):
        out = WC.page_cadre(PREFIX)
        self.assertEqual(out['csp'], WC.CADRE_CSP)
        self.assertTrue(out['frame_self'])
        self.assertIn('id="wordcraft_canvas"', out['body'])
        self.assertNotRegex(out['body'], r'<script(?![^>]*\bsrc=)')                     # aucun script en ligne
        self.assertNotIn('data-host=', out['body'])                                      # mode cadre (?host=parent), pas le mode page

    def test_export_tables_declarees(self):
        from agent import export56108
        exclues = {t for t, _ in export56108.EXPORT_EXCLUES} if hasattr(export56108, 'EXPORT_EXCLUES') else set()
        src = (ROOT / 'agent' / 'export56108.py').read_text(encoding='utf-8')
        for table in ('editeur56108_seances', 'editeur56108_ops'):
            self.assertTrue(table in exclues or "'%s'" % table in src, table)


# ------------------------------------------------------------------------------------------------ W9 · traductions
class Traductions(unittest.TestCase):
    PHRASES = ('Modifier dans l’éditeur intégré (WordCraft, version d’essai)', 'Ouvrir dans Word / LibreOffice', 'Ouvrir avec l’application du poste', 'Garder cette version',
               'Abandonner cette version', 'Contrôle de la version enregistrée', 'Ouvrir dans WordCraft', 'Avant d’ouvrir',
               'Reprendre cette version', 'Abandonner la copie de travail', 'Fermer l’éditeur')

    def test_W9_textes_traduits_en_de_es(self):
        for lang in ('en', 'de', 'es'):
            d = json.loads((ROOT / 'agent' / 'locales' / ('ecrans_%s.json' % lang)).read_text(encoding='utf-8'))
            for p in self.PHRASES:
                self.assertTrue(d.get(p), (lang, p))


if __name__ == '__main__':
    unittest.main()
