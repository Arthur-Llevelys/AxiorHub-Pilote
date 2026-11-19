"""5.6.108 (lot X) : export complet et départ vers un autre logiciel.

X1 : registre complet (aucune table « non couverte » sur une installation fictive représentative, ni dans le code), filtre des dossiers réservés sur
     chaque table qui porte un dossier, règles de résolution (courriel classé, chemin, portée, liste, parent, empreinte de discussion), dossier non
     déterminable jamais exporté en clair, secrets jamais exportés.
X2 : profil « Préparer un départ vers un autre logiciel » : originaux, EML, ICS (VEVENT, VTODO), vCard, sommaire HTML, dictionnaire, rapports ;
     annonce avant lancement ; disponible sans modèle ni réseau.
X3 : manifeste : attendu / exporté / exclu par domaine ; « complet » seulement si rien n'est non couvert et si chaque fichier déclaré est présent.
X4 : recette : dossier fictif complet, export, relecture de l'archive SANS AxiorHub (sous-processus ``python -I`` : zipfile, json, csv, email, lecteur
     ICS minimal) ; un dossier réservé à un autre avocat n'apparaît nulle part.

Données fictives (DURAND, MARTIN, ZORVAL, DOS-001, RG 24/01234, example.test) ; aucun réseau, aucun modèle.
"""
import contextlib
import csv
import hashlib
import importlib
import inspect
import io
import json
import os
from pathlib import Path
import pkgutil
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import test_agent as fixtures
import test_v5652 as base
from agent import export5688 as X, export56108 as R, membres5650
from agent.common import Stop, private_json
from agent.desk import Desk

ROOT = Path(__file__).resolve().parents[1]
RESERVE, VISIBLE = 'DOS-RES', 'DOS-001'
CREATE = re.compile(r'CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?["`]?([A-Za-z_][A-Za-z_0-9]*)["`]?\s*(?:\(|USING\b)')


def sans_reseau():
    """Toute tentative de connexion fait échouer le test : l'export est local."""
    def refuse(*a, **k):
        raise AssertionError('connexion réseau pendant l’export')
    pile = contextlib.ExitStack()
    pile.enter_context(patch.object(socket.socket, 'connect', refuse))
    pile.enter_context(patch.object(socket, 'create_connection', refuse))
    return pile


def toutes_les_tables(desk):
    """Installation représentative : chaque fonction de schéma des modules (``ensure_schema``, ``schema``…) appelée sur la base du bureau."""
    noms = ('ensure_schema', '_ensure_schema', 'schema', '_schema', 'ensure', '_ensure', '_tables')
    with sans_reseau():
        for info in sorted(pkgutil.iter_modules([str(ROOT / 'agent')]), key=lambda m: m.name):
            if info.name.startswith('_'):
                continue
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    module = importlib.import_module('agent.' + info.name)
            except BaseException:   # noqa: BLE001 — module d'un lot en cours ou dépendance absente : ses tables sont vues par le contrôle statique
                continue
            for nom in noms:
                fn = getattr(module, nom, None)
                if not callable(fn) or getattr(fn, '__module__', '') != module.__name__:
                    continue
                try:
                    params = [p for p in inspect.signature(fn).parameters.values() if p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
                except (TypeError, ValueError):
                    continue
                if len(params) != 1:
                    continue
                try:
                    with contextlib.redirect_stderr(io.StringIO()):
                        fn(desk.db if params[0].name in ('db', 'conn', 'connection') else desk)
                    desk.db.commit()
                except BaseException:   # noqa: BLE001
                    try:
                        desk.db.rollback()
                    except sqlite3.Error:
                        pass


def colonnes(db, table):
    return [(r[1], (r[2] or '').upper()) for r in db.execute('PRAGMA table_info("%s")' % table)]


def inserer(db, table, valeurs, marque):
    """Ligne complète : valeurs imposées, entiers à 0 (ou au numéro imposé), textes marqués ``<marque>_<table>_<colonne>``."""
    cols = colonnes(db, table)
    ligne = {}
    for nom, genre in cols:
        if nom in valeurs:
            ligne[nom] = valeurs[nom]
        elif 'INT' in genre or 'REAL' in genre or 'NUM' in genre:
            ligne[nom] = valeurs.get('__n', 0)
        else:
            ligne[nom] = '%s_%s_%s' % (marque, table, nom)
    db.execute('INSERT INTO "%s"(%s) VALUES(%s)' % (table, ','.join('"%s"' % c for c in ligne), ','.join('?' * len(ligne))), tuple(ligne.values()))


def lire_zip(chemin):
    with zipfile.ZipFile(chemin) as z:
        return {n: z.read(n) for n in z.namelist()}


def ajouter_reserve(f):
    """Registre fictif : DOS-001 (visible) et DOS-RES, réservé à un autre avocat."""
    matters = list(f.matters) + [{'id': RESERVE, 'client_name': 'Client ZORVAL', 'path': '/Dossiers/RES', 'references': [RESERVE], 'aliases': ['ZORVAL'],
                                  'correspondents': [{'email': 'zorval@example.test', 'role': 'client'}]}]
    private_json(f.matter_file, matters)
    os.utime(f.matter_file, (1, 1))   # signature ancienne : le registre relu n'est pas tenu pour « récent »
    membres5650.set_reserve(f.c['state_dir'], RESERVE, ['martin@example.test'])


# ================================================================================ X1 : registre complet sur une installation représentative
class Installation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = fixtures.EngineTests('test_seen_never_reaches_model')
        cls.f.setUp()
        cls.desk = Desk(cls.f.c)
        toutes_les_tables(cls.desk)
        ajouter_reserve(cls.f)

    @classmethod
    def tearDownClass(cls):
        cls.desk.db.close()
        cls.f.doCleanups()

    def exporter(self, **options):
        dest = Path(self.f.base) / ('x-%d.zip' % len(list(Path(self.f.base).glob('x-*.zip'))))
        with sans_reseau():
            meta, _ = X.construire(self.desk, dest, {'dossiers_reserves': 'exclure', **options})
        return dest, meta

    def test_X1_01_installation_representative_aucune_table_non_couverte(self):
        nombre = self.desk.db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        self.assertGreater(nombre, 500, 'installation représentative : toutes les fonctions de schéma ont été appelées')
        dest, meta = self.exporter()
        self.assertEqual(meta['perimetre']['non_couvertes'], [])
        self.assertTrue(meta['perimetre']['complet'])
        controle = X.verifier_manifeste(dest)
        self.assertTrue(controle['ok'], controle['anomalies'])
        self.assertTrue(controle['complet'])
        self.assertIn('COMPLET', lire_zip(dest)['LISEZMOI.txt'].decode('utf-8'))

    def test_X1_02_chaque_table_du_code_est_exportee_ou_exclue_avec_motif(self):
        trouvees = {}
        for p in list((ROOT / 'agent').rglob('*.py')) + list((ROOT / 'agent').rglob('*.sql')):
            if '_vendor' in p.parts:
                continue
            for nom in CREATE.findall(p.read_text(encoding='utf-8', errors='replace')):
                trouvees.setdefault(nom, set()).add(p.name)
        self.assertGreater(len(trouvees), 600)
        exportees, exclues = {t[2] for t in X.tables_exportees()}, X.tables_exclues()
        orphelines = {t: sorted(m) for t, m in trouvees.items() if t not in exportees and not X.motif_exclusion(t, exclues)}
        self.assertEqual(orphelines, {}, 'table nouvelle : la déclarer dans le module (EXPORT / EXPORT_EXCLUES) ou dans export56108')

    def test_X1_03_registre_coherent(self):
        exportees = X.tables_exportees()
        tables = [t[2] for t in exportees]
        self.assertEqual(len(tables), len(set(tables)))
        paires = [(t[0], t[1]) for t in exportees]
        self.assertEqual(len(paires), len(set(paires)), 'deux fichiers du même nom dans un domaine')
        self.assertFalse(set(tables) & set(dict(R.EXPORT_EXCLUES)), 'une table exportée et exclue à la fois')
        self.assertTrue(all(len(m) > 30 for _t, m in R.EXPORT_EXCLUES))
        self.assertEqual({t[3] for t in exportees}, {X.DESK})
        regles = X.resolveurs()
        self.assertLessEqual(set(regles), set(tables), 'une règle de résolution sans table exportée')
        for table, spec in regles.items():
            self.assertIn(spec[0], ('colonne', 'courriel', 'chemin', 'portee', 'json', 'enfants', 'parent', 'conversation', 'inconnu'), table)
        db = self.desk.db
        for domaine, nom, table, _b, filtre in exportees:      # chaque filtre désigne une colonne réelle (sinon : aucune ligne en clair)
            cols = [c for c, _ in colonnes(db, table)]
            if not cols or table in regles:
                continue
            if isinstance(filtre, str):
                self.assertIn(filtre, cols, table)
            elif isinstance(filtre, tuple):
                self.assertIn(filtre[0], cols, table)
                parent = [c for c, _ in colonnes(db, filtre[1])]
                self.assertTrue({filtre[2], filtre[3]} <= set(parent), (table, filtre))
            elif table not in ('notes', 'contacts5636'):
                portant = set(cols) & {'matter', 'matter_id', 'dossier', 'dossier_id', 'mail_key'}
                self.assertFalse(portant, '%s porte un dossier mais est exportée sans filtre' % table)

    def test_X1_04_jamais_de_secret_ni_de_compte(self):
        tables = {t[2] for t in X.tables_exportees()}
        for interdite in ('users', 'sessions', 'login_attempts', 'reset_tokens', 'integration_keys5636', 'claude5675_jetons', 'claude5675_codes',
                          'google5697_connexions', 'google5697_etats', 'push_subs490', 'appareils5674', 'ia_liaisons', 'talk_rooms_v568', 'send_pending490',
                          'guide_sessions5631', 'connection_receipts5631', 'calendar_targets568', 'ecosystem_services_v380', 'jobs', 'settings', 'audit',
                          'send_log490', 'sync56108_membres', 'membres5650'):
            self.assertNotIn(interdite, tables)
            self.assertTrue(X.motif_exclusion(interdite), interdite)
        for table in ('integration_keys5636', 'claude5675_jetons', 'push_subs490', 'google5697_connexions'):
            self.assertEqual(X.motif_exclusion(table), R.MOTIFS['secrets'])

    def test_X1_05_filtre_des_dossiers_reserves_sur_chaque_table(self):
        """Une ligne visible et une ligne du dossier réservé dans CHAQUE table exportée qui porte un dossier (colonne ou parent) : aucune marque du
        dossier réservé ne sort ; la ligne visible sort."""
        db, regles = self.desk.db, X.resolveurs()
        essayees, echecs = [], []
        for numero, (_d, _n, table, _b, filtre) in enumerate(X.tables_exportees()):
            if filtre is None or table in regles or not colonnes(db, table):
                continue
            try:
                db.execute('SAVEPOINT x')
                for n, (marque, dossier) in enumerate((('VISX', VISIBLE), ('RESX', RESERVE)), 1):
                    if isinstance(filtre, str):
                        inserer(db, table, {filtre: dossier, '__n': 900000 + numero * 4 + n}, marque)
                    else:
                        col, parent, cle, pcol = filtre
                        entier = any(c == cle and 'INT' in g for c, g in colonnes(db, parent))
                        ident = 800000 + numero * 4 + n if entier else 'P%s_%s_%d' % (marque, table, n)
                        inserer(db, parent, {cle: ident, pcol: dossier, '__n': 700000 + numero * 4 + n}, marque + 'P')
                        inserer(db, table, {col: ident, '__n': 900000 + numero * 4 + n}, marque)
                db.execute('RELEASE x')
                essayees.append(table)
            except sqlite3.Error:
                db.execute('ROLLBACK TO x')
                db.execute('RELEASE x')
                echecs.append(table)
        db.commit()
        self.assertGreater(len(essayees), 200, echecs)
        dest, meta = self.exporter()
        contenu = lire_zip(dest)
        tout = b'\n'.join(contenu.values()) + '\n'.join(contenu).encode()
        fuites = sorted({m.decode() for m in re.findall(rb'RESXP?_[A-Za-z0-9_]+', tout)})
        self.assertEqual(fuites, [], 'lignes du dossier réservé exportées')
        absentes = [t for t in essayees if ('VISX_%s_' % t).encode() not in tout
                    and any(g in ('', 'TEXT') or 'CHAR' in g for c, g in colonnes(db, t) if not X._secrete(c))]
        self.assertEqual(absentes, [], 'lignes visibles non exportées')
        self.assertTrue(X.verifier_manifeste(dest)['ok'])
        # mutation : sans filtre, le même contrôle voit la fuite (le test échouerait si le garde-fou était retiré)
        sans_filtre = tuple((d, n, t, b, None) for d, n, t, b, f in X.tables_exportees())
        with patch.object(X, 'tables_exportees', lambda: sans_filtre):
            dest2, _ = self.exporter()
        self.assertIn(b'RESX_', b'\n'.join(lire_zip(dest2).values()))


# ================================================================================ X1 : règles de résolution du dossier
class Base(base.Base):
    def setUp(self):
        super().setUp()
        ajouter_reserve(self.f)

    def exporter(self, **options):
        dest = Path(self.tmp.name) / ('x-%d.zip' % len(list(Path(self.tmp.name).glob('x-*.zip'))))
        with sans_reseau():
            meta, _ = X.construire(self.desk, dest, {'dossiers_reserves': 'exclure', **options})
        return dest, meta, lire_zip(dest)

    def table(self, nom):
        """Crée une table du registre si elle manque (le module n'a pas encore servi sur cette base)."""
        if not colonnes(self.desk.db, nom):
            toutes_les_tables(self.desk)


class Resolveurs(Base):
    def setUp(self):
        super().setUp()
        toutes_les_tables(self.desk)

    def test_X1_10_courriel_classe_brouillon_conserve(self):
        db = self.desk.db
        db.execute("INSERT INTO proposals VALUES('k-vis','DOS-001','a@example.test','{}','associe','x')")
        db.execute("INSERT INTO proposals VALUES('k-res','DOS-RES','b@example.test','{}','associe','x')")
        for cle, texte in (('k-vis', 'BROUILLON_VISIBLE'), ('k-res', 'BROUILLON_RESERVE'), ('k-inconnu', 'BROUILLON_SANS_DOSSIER')):
            db.execute('INSERT INTO manual_drafts VALUES(?,?,?)', (cle, json.dumps({'texte': texte}), 'x'))
        db.commit()
        _d, meta, fichiers = self.exporter()
        brouillons = fichiers['courriels/brouillons_conserves.json']
        self.assertIn(b'BROUILLON_VISIBLE', brouillons)
        self.assertNotIn(b'BROUILLON_RESERVE', brouillons)
        self.assertNotIn(b'BROUILLON_SANS_DOSSIER', brouillons, 'courriel non classé : dossier non déterminable')
        self.assertEqual(meta['perimetre']['domaines']['courriels']['exclu'] >= 2, True)
        _d, _m, tout = self.exporter(dossiers_reserves='inclure', confirmation_reserves=True)
        self.assertIn(b'BROUILLON_SANS_DOSSIER', tout['courriels/brouillons_conserves.json'])

    def test_X1_11_aucun_dossier_reserve_tout_est_determinable(self):
        membres5650.set_reserve(self.f.c['state_dir'], RESERVE, [])
        self.desk.db.execute('INSERT INTO manual_drafts VALUES(?,?,?)', ('k-inconnu', json.dumps({'texte': 'BROUILLON_SANS_DOSSIER'}), 'x'))
        self.desk.db.commit()
        _d, _m, fichiers = self.exporter()
        self.assertIn(b'BROUILLON_SANS_DOSSIER', fichiers['courriels/brouillons_conserves.json'])

    def test_X1_12_discussion_rattachee_par_son_empreinte(self):
        from agent import workspace
        vis = workspace.chat_scope(self.desk.c, {'matter': VISIBLE})[0]
        empreintes = R._empreintes_conversation(R.Contexte(self.desk.c, self.desk.db))
        self.assertEqual(empreintes.get(vis), VISIBLE, 'même empreinte que la discussion du produit')
        res = next(k for k, v in empreintes.items() if v == RESERVE)
        general = workspace.chat_scope(self.desk.c, {})[0]
        for scope, texte in ((vis, 'QUESTION_VISIBLE'), (res, 'QUESTION_RESERVEE'), (general, 'QUESTION_GENERALE')):
            self.desk.db.execute('INSERT INTO conversations(scope,question,response,sources,created) VALUES(?,?,?,?,?)', (scope, texte, '{}', '[]', 'x'))
        self.desk.db.commit()
        _d, _m, fichiers = self.exporter()
        data = fichiers['conversations/questions_au_dossier.json']
        self.assertIn(b'QUESTION_VISIBLE', data)
        self.assertNotIn(b'QUESTION_RESERVEE', data)
        self.assertNotIn(b'QUESTION_GENERALE', data, 'question générale : dossier non déterminable')

    def test_X1_13_annotation_rattachee_par_le_chemin(self):
        db = self.desk.db
        for chemin, note in (('/Dossiers/DEMO/Convention.pdf', 'NOTE_VISIBLE'), ('/Dossiers/RES/Acte.pdf', 'NOTE_RESERVEE'), ('/Ailleurs/x.pdf', 'NOTE_HORS_DOSSIER')):
            db.execute('INSERT INTO fichiers56103_notes VALUES(?,?,?,?,?,?,?)', ('nextcloud', chemin, 'vert', note, 'durand', 'x', 1))
        db.commit()
        _d, _m, fichiers = self.exporter()
        data = fichiers['dossiers/annotations_des_fichiers.json']
        self.assertIn(b'NOTE_VISIBLE', data)
        self.assertNotIn(b'NOTE_RESERVEE', data)
        self.assertNotIn(b'NOTE_HORS_DOSSIER', data)

    def test_X1_14_portee_liste_et_parent(self):
        db = self.desk.db
        inserer(db, 'business_rules_v410', {'scope': 'matter', 'scope_value': RESERVE, 'instruction': 'REGLE_RESERVEE', '__n': 1}, 'B')
        inserer(db, 'business_rules_v410', {'scope': 'cabinet', 'scope_value': '', 'instruction': 'REGLE_CABINET', '__n': 2}, 'B')
        for ident, liste in (('rt-res', [RESERVE]), ('rt-tous', [])):
            inserer(db, 'routines5652', {'id': ident, 'matters_json': json.dumps(liste), 'title': 'ROUTINE_' + ident, 'request_key': 'rk-' + ident}, 'R')
            inserer(db, 'routines5652_versions', {'routine_id': ident, 'snapshot_json': '{"t":"VERSION_%s"}' % ident, '__n': 1}, 'V')
            inserer(db, 'routines5652_occ', {'id': 'occ-' + ident, 'routine_id': ident, 'result_json': '{"r":"RESULTAT_%s"}' % ident}, 'O')
        db.commit()
        _d, _m, fichiers = self.exporter()
        tout = b''.join(fichiers.values())
        for present in (b'REGLE_CABINET', b'ROUTINE_rt-tous', b'VERSION_rt-tous'):
            self.assertIn(present, tout)
        for absent in (b'REGLE_RESERVEE', b'ROUTINE_rt-res', b'VERSION_rt-res', b'RESULTAT_rt-res', b'RESULTAT_rt-tous'):
            self.assertNotIn(absent, tout)   # occurrence d'une routine sur tous les dossiers : contenu non déterminable

    def test_X1_15_mutation_regle_de_resolution_retiree(self):
        self.desk.db.execute("INSERT INTO proposals VALUES('k-res','DOS-RES','b@example.test','{}','associe','x')")
        self.desk.db.execute('INSERT INTO manual_drafts VALUES(?,?,?)', ('k-res', json.dumps({'texte': 'BROUILLON_RESERVE'}), 'x'))
        self.desk.db.commit()
        with patch.object(R, 'garder', lambda dossiers, reserves: True):
            _d, _m, fichiers = self.exporter()
        self.assertIn(b'BROUILLON_RESERVE', fichiers['courriels/brouillons_conserves.json'])   # le garde-fou est bien celui qui protège

    def test_X1_16_colonne_de_filtre_absente_rien_en_clair(self):
        self.desk.db.execute('CREATE TABLE IF NOT EXISTS essai_x108(id TEXT, valeur TEXT)')
        self.desk.db.execute("INSERT INTO essai_x108 VALUES('1','VALEUR_SANS_DOSSIER_108')")
        self.desk.db.commit()
        export = R.EXPORT + (('essais', 'essai', 'essai_x108', X.DESK, 'matter'),)
        with patch.object(R, 'EXPORT', export):
            _d, meta, fichiers = self.exporter()
            self.assertNotIn(b'VALEUR_SANS_DOSSIER_108', fichiers['essais/essai.json'])
            self.assertEqual(meta['perimetre']['domaines']['essais']['exclu'], 1)
            _d, _m, fichiers = self.exporter(dossiers_reserves='inclure', confirmation_reserves=True)
            self.assertIn(b'VALEUR_SANS_DOSSIER_108', fichiers['essais/essai.json'])

    def test_X1_17_formules_neutralisees_dans_les_nouvelles_tables(self):
        self.desk.db.execute("INSERT INTO proposals VALUES('k-f','DOS-001','=HYPERLINK(\"http://example.test\")','{}','associe','x')")
        self.desk.db.commit()
        _d, _m, fichiers = self.exporter()
        lignes = list(csv.DictReader(io.StringIO(fichiers['courriels/associations_courriel_dossier.csv'].decode('utf-8-sig')), delimiter=';'))
        self.assertEqual([r['email'] for r in lignes if r['key'] == 'k-f'], ["'=HYPERLINK(\"http://example.test\")"])
        self.assertEqual([r['email'] for r in json.loads(fichiers['courriels/associations_courriel_dossier.json']) if r['key'] == 'k-f'],
                         ['=HYPERLINK("http://example.test")'])


# ================================================================================ X2, X3, X4 : profil de départ et recette hors AxiorHub
LECTEUR = r'''
import csv, email, hashlib, io, json, sys, zipfile
from email import policy
from pathlib import PurePosixPath

def ics(texte):
    lignes, cur = [], None
    for brute in texte.replace('\r\n', '\n').split('\n'):
        if brute[:1] in (' ', '\t') and lignes:
            lignes[-1] += brute[1:]
        elif brute:
            lignes.append(brute)
    comps, pile = [], []
    for l in lignes:
        if l.startswith('BEGIN:'):
            pile.append({'_type': l[6:]})
        elif l.startswith('END:'):
            c = pile.pop()
            if c['_type'] in ('VEVENT', 'VTODO', 'VCARD'):
                comps.append(c)
        elif pile:
            nom, _, valeur = l.partition(':')
            pile[-1].setdefault(nom.split(';')[0], valeur)
    return comps

out = {'anomalies': [], 'csv': {}, 'eml': [], 'vevent': [], 'vtodo': [], 'vcard': [], 'liens_casses': []}
with zipfile.ZipFile(sys.argv[1]) as z:
    noms = set(z.namelist())
    manifeste = json.loads(z.read('MANIFEST.json'))
    out['manifeste'] = {'complet': manifeste['perimetre']['complet'], 'profil': manifeste.get('profil'), 'fichiers': len(manifeste['fichiers'])}
    for f in manifeste['fichiers']:
        data = z.read(f['chemin'])
        if hashlib.sha256(data).hexdigest() != f['sha256'] or len(data) != f['taille']:
            out['anomalies'].append(f['chemin'])
        if f['chemin'].endswith('.csv'):
            lignes = list(csv.reader(io.StringIO(data.decode('utf-8-sig')), delimiter=';'))
            out['csv'][f['chemin']] = len(lignes) - 1
            if 'lignes' in f and f['lignes'] != len(lignes) - 1 and not f['chemin'].startswith('dictionnaire/'):
                out['anomalies'].append('compte ' + f['chemin'])
        elif f['chemin'].endswith('.json'):
            json.loads(data)
        elif f['chemin'].endswith('.eml'):
            m = email.message_from_bytes(data, policy=policy.default)
            out['eml'].append({'chemin': f['chemin'], 'objet': str(m['Subject']), 'pieces': [p.get_filename() for p in m.iter_attachments()]})
    out['vevent'] = [c for c in ics(z.read('agenda/evenements.ics').decode('utf-8')) if c['_type'] == 'VEVENT']
    out['vtodo'] = [c for c in ics(z.read('taches/taches.ics').decode('utf-8')) if c['_type'] == 'VTODO']
    out['vcard'] = [c for c in ics(z.read('contacts/contacts.vcf').decode('utf-8')) if c['_type'] == 'VCARD']
    sommaire = z.read('index.html').decode('utf-8')
    out['script_dans_le_sommaire'] = '<script' in sommaire.lower()
    import re
    for lien in re.findall(r'href="([^"]+)"', sommaire):
        if not lien.startswith('#') and lien not in noms:
            out['liens_casses'].append(lien)
    decrits = {e['fichier'] for e in json.loads(z.read('dictionnaire/dictionnaire.json'))}
    out['non_decrits'] = sorted(n for n in noms if n not in decrits)
    out['dictionnaire_csv'] = sum(1 for _ in csv.reader(io.StringIO(z.read('dictionnaire/dictionnaire.csv').decode('utf-8-sig')), delimiter=';'))
print(json.dumps(out))   # ASCII : la console du sous-processus peut ne pas être en UTF-8
'''

EML = ('From: {nom} <{adresse}>\r\nTo: cabinet@example.test\r\nSubject: {objet}\r\nMessage-ID: <{mid}@example.test>\r\n'
       'Date: Mon, 02 Nov 2026 09:00:00 +0100\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary="b"\r\n\r\n--b\r\n'
       'Content-Type: text/plain; charset=utf-8\r\n\r\nCorps fictif RG 24/01234.\r\n--b\r\nContent-Type: application/pdf\r\n'
       'Content-Disposition: attachment; filename="{piece}"\r\nContent-Transfer-Encoding: base64\r\n\r\nJVBERi0xLjQgZmljdGlm\r\n--b--\r\n')


class Depart(Base):
    def setUp(self):
        super().setUp()
        toutes_les_tables(self.desk)
        self.dossier_complet()

    def fichier(self, chemin, data):
        cible = self.root / chemin.lstrip('/')
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def courriel(self, matter, archive, objet, piece):
        db = self.desk.db
        nom = ('ZORVAL', 'zorval@example.test') if matter == RESERVE else ('DURAND', 'durand@example.test')
        raw = EML.format(objet=objet, mid=archive, piece=piece, nom=nom[0], adresse=nom[1]).encode()
        racine = '/Dossiers/DEMO' if matter == VISIBLE else '/Dossiers/RES'
        chemin = '%s/correspondances/2026-11-02--%s/original.eml' % (racine, archive)
        sha = self.fichier(chemin, raw)
        inserer(db, 'mail_archives', {'id': archive, 'raw_sha256': sha, 'raw_size': len(raw), 'message_id': '<%s@example.test>' % archive,
                                      'direction': 'received', 'headers_json': '{}', 'capture_state': 'captured', '__n': 1}, 'ARCH' + matter)
        inserer(db, 'mail_archive_links', {'id': 'l-' + archive, 'archive_id': archive, 'matter_id': matter, 'title': objet, 'tags_json': '[]', 'mode': 'manual',
                                           'resolution_json': '{}', 'email_requested': 1, 'attachments_requested': 0, 'conservation_state': 'verified',
                                           'attachments_state': 'not_requested', 'extraction_state': 'complete', 'indexing_state': 'indexed', 'version': 1},
                'LIEN' + matter)
        inserer(db, 'mail_archive_assets', {'id': 'a-' + archive, 'link_id': 'l-' + archive, 'kind': 'original', 'destination_usage': 'correspondances',
                                            'path': chemin, 'sha256': sha, 'size_bytes': len(raw), 'state': 'verified', 'version': 1, 'detail_json': '{}',
                                            'provenance': 'mail_original'},
                'ASSET' + matter)
        return raw

    def dossier_complet(self):
        """Dossier fictif complet (DOS-001) et dossier réservé à un autre avocat (DOS-RES), avec des marques à retrouver ou à ne jamais voir."""
        from agent import redaction56106
        from agent.index import DocumentIndex
        db = self.desk.db
        index = DocumentIndex(self.desk.c['state_dir'])
        docs = {'/Dossiers/DEMO/Convention.pdf': b'%PDF-1.4 convention fictive', '/Dossiers/DEMO/Conclusions v2.docx': b'PK\x03\x04 docx fictif modifie',
                '/Dossiers/DEMO/Bareme.xlsx': b'PK\x03\x04 xlsx fictif', '/Dossiers/RES/Acte ZORVAL.docx': b'PK\x03\x04 RESERVE_DOCUMENT'}
        for chemin, data in docs.items():
            if not chemin.endswith('Convention.pdf'):
                self.fichier(chemin, data)
            index.db.execute('INSERT INTO docs VALUES (?,?,?,?,?,?)', (RESERVE if '/RES/' in chemin else VISIBLE, chemin, 'e', '2026-11-01', '', ''))
        index.db.commit()
        index.db.close()
        self.docs = docs
        for ident, nom, matter in (('p1', 'DURAND', VISIBLE), ('p2', 'MARTIN', VISIBLE), ('p3', 'RESERVE_PARTIE_ZORVAL', RESERVE)):
            inserer(db, 'd51_parties', {'id': ident, 'matter': matter, 'display_name': nom, '__n': 1}, 'PARTIE' + matter)
        self.eml = [self.courriel(VISIBLE, 'arch-1', 'Convocation DOS-001', 'convocation.pdf'), self.courriel(VISIBLE, 'arch-2', 'Pièces adverses', 'piece-3.pdf'),
                    self.courriel(RESERVE, 'arch-r', 'RESERVE_COURRIEL', 'reserve.pdf')]
        for ident, matter, rid, debut in (('ev1', VISIBLE, '20261105T090000Z', '2026-11-05T10:00:00+01:00'), ('ev2', VISIBLE, '20261112T090000Z', '2026-11-12T10:00:00+01:00'),
                                          ('ev-r', RESERVE, '', '2026-11-06T10:00:00+01:00')):
            inserer(db, 'agenda5634', {'id': ident, 'uid': 'serie-' + matter, 'recurrence_id': rid, 'kind': 'rdv', 'matter': matter, 'starts': debut,
                                       'ends': debut.replace('T10', 'T11'), 'reminders': '[30]', 'status': 'actif', 'all_day': 0,
                                       'title': 'RESERVE_EVENEMENT' if matter == RESERVE else 'Audience RG 24/01234'}, 'EV' + matter)
        inserer(db, 'tasks', {'id': 'ta1', 'matter': VISIBLE, 'title': 'Relancer MARTIN', 'due': '2026-11-20', 'status': 'open'}, 'T')
        inserer(db, 'tasks', {'id': 'ta-r', 'matter': RESERVE, 'title': 'RESERVE_TACHE', 'due': '2026-11-21', 'status': 'open'}, 'T')
        inserer(db, 'b53_documents', {'id': 'f1', 'matter': VISIBLE, 'kind': 'facture', 'number': 'F-2026-001', 'snapshot_json': '{"total": 1200}'}, 'FACT')
        inserer(db, 'b53_documents', {'id': 'f-r', 'matter': RESERVE, 'kind': 'facture', 'number': 'RESERVE_FACTURE', 'snapshot_json': '{}'}, 'FACTR')
        inserer(db, 'correction_events_v410', {'id': 1, 'matter': VISIBLE, 'corrected_text': 'CORRECTION_AVOCAT_108', '__n': 1}, 'C')
        inserer(db, 'correction_events_v410', {'id': 2, 'matter': RESERVE, 'corrected_text': 'RESERVE_CORRECTION', '__n': 2}, 'CR')
        redaction56106.creer(self.desk, 'durand', 'MODELE_PERSONNALISE_108', '<p>Cher Confrère,</p>')
        maintenant = '2026-11-18T09:00:00+00:00'
        for ident, matter, titre in (('rout-1', VISIBLE, 'ROUTINE_108'), ('rout-r', RESERVE, 'RESERVE_ROUTINE')):
            db.execute("INSERT INTO routines56106(id,owner,scope,title,criteres_json,actions_json,matter,created_by,created_at,updated_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?)", (ident, 'durand', 'personnel', titre, '{}', '[]', matter, 'durand', maintenant, maintenant))
        for ident, matter, note in (('it1', VISIBLE, 'DECISION_RELECTURE_108'), ('it-r', RESERVE, 'RESERVE_DECISION')):
            db.execute("INSERT INTO file56100_items(cabinet,id,kind,matter,ref_source,revision,titre,search_norm,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                       ('cabinet', ident, 'decision', matter, 'r', '1', 'A relire', 'x', 1))
            db.execute('INSERT INTO file56100_historique VALUES(?,?,?,?,?,?,?)', ('cabinet', ident, '1', 'retenir', 'durand', 1, json.dumps({'note': note})))
        inserer(db, 'contacts5636', {'id': 'c1', 'kind': 'personne', 'name': 'DURAND', 'first_name': 'Alice', 'email': 'durand@example.test',
                                     'phone': '01 00 00 00 05', 'archived': 0}, 'CO')
        inserer(db, 'contacts5636', {'id': 'c-r', 'kind': 'personne', 'name': 'RESERVE_CONTACT', 'email': 'zorval@example.test', 'archived': 0}, 'COR')
        db.execute('INSERT INTO contact_links5636(contact,matter,role,created) VALUES(?,?,?,?)', ('c1', VISIBLE, 'client', 'x'))
        db.execute('INSERT INTO contact_links5636(contact,matter,role,created) VALUES(?,?,?,?)', ('c-r', RESERVE, 'client', 'x'))
        db.commit()

    def lire_sans_axiorhub(self, archive):
        """Lecteurs indépendants : sous-processus isolé (``-I``), dossier vide, aucun module d'AxiorHub importable."""
        vide = Path(tempfile.mkdtemp(dir=self.tmp.name))
        script = vide / 'lecteur.py'
        script.write_text(LECTEUR, encoding='utf-8')
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
        sortie = subprocess.run([sys.executable, '-I', str(script), str(archive)], cwd=str(vide), env=env, capture_output=True, timeout=120)
        self.assertEqual(sortie.returncode, 0, sortie.stderr.decode('utf-8', 'replace'))
        return json.loads(sortie.stdout.decode('utf-8'))

    def test_X4_01_recette_dossier_complet_lu_sans_axiorhub(self):
        self.desk.c['nextcloud'] = {**self.desk.c['nextcloud'], 'roots': ['/Dossiers']}
        dest, meta, fichiers = self.exporter(profil='depart')
        self.assertEqual(meta['profil'], 'depart')
        self.assertTrue(meta['documents_inclus'])
        controle = X.verifier_manifeste(dest)
        self.assertTrue(controle['ok'], controle['anomalies'])
        self.assertTrue(controle['complet'], controle['non_couvertes'])
        lu = self.lire_sans_axiorhub(dest)
        self.assertEqual(lu['anomalies'], [])
        self.assertEqual(lu['manifeste']['profil'], 'depart')
        # documents : originaux, octets exacts (le DOCX modifié compris)
        inventaire = {r['chemin']: r for r in json.loads(fichiers['documents/inventaire.json'])}
        for chemin, data in self.docs.items():
            if '/RES/' in chemin:
                self.assertNotIn(chemin, inventaire)
                continue
            self.assertEqual(inventaire[chemin]['statut'], 'inclus', inventaire[chemin])
            self.assertEqual(fichiers[inventaire[chemin]['dans_export']], data)
        # courriels : EML originaux, pièces lisibles par le module email
        self.assertEqual(sorted(e['objet'] for e in lu['eml']), ['Convocation DOS-001', 'Pièces adverses'])
        self.assertEqual(sorted(p for e in lu['eml'] for p in e['pieces']), ['convocation.pdf', 'piece-3.pdf'])
        self.assertEqual([fichiers[e['chemin']] for e in lu['eml'] if 'arch-1' in e['chemin']], [self.eml[0]])
        # agenda : série en deux occurrences, UID distincts et stables, rappel ; tâches en VTODO ; contacts en vCard
        self.assertEqual(len(lu['vevent']), 2)
        self.assertEqual(len({v['UID'] for v in lu['vevent']}), 2)
        self.assertTrue(all(v['X-AXIORHUB-SERIES'] == 'serie-DOS-001' for v in lu['vevent']))
        self.assertEqual(sorted(v['DTSTART'] for v in lu['vevent']), ['20261105T090000Z', '20261112T090000Z'])
        self.assertEqual([t['SUMMARY'] for t in lu['vtodo']], ['Relancer MARTIN'])
        self.assertEqual([t['DUE'] for t in lu['vtodo']], ['20261120'])
        self.assertEqual([c['FN'] for c in lu['vcard']], ['Alice DURAND'])
        # tableaux : comptes du manifeste = lignes relues ; marques du dossier fictif présentes
        tout = b'\n'.join(fichiers.values())
        for marque in (b'CORRECTION_AVOCAT_108', b'MODELE_PERSONNALISE_108', b'ROUTINE_108', b'DECISION_RELECTURE_108', b'F-2026-001', b'MARTIN'):
            self.assertIn(marque, tout)
        # sommaire, dictionnaire, rapports
        self.assertFalse(lu['script_dans_le_sommaire'])
        self.assertEqual(lu['liens_casses'], [])
        self.assertEqual(lu['non_decrits'], [])
        self.assertGreater(lu['dictionnaire_csv'], 500)
        rapport = json.loads(fichiers['rapports/exclusions.json'])
        self.assertEqual(rapport['courriels_inclus'], 2)
        self.assertTrue(rapport['tables_exclues_par_motif'])
        # le dossier réservé à un autre avocat n'apparaît nulle part (contenu ni noms de fichiers)
        noms = '\n'.join(fichiers).encode()
        for marque in (b'RESERVE_', b'ZORVAL', b'zorval', RESERVE.encode(), b'/Dossiers/RES'):
            ou = (tout + noms).find(marque)
            self.assertEqual(ou, -1, (marque, (tout + noms)[max(0, ou - 300):ou + 100]))

    def test_X4_02_original_modifie_signale_jamais_certifie(self):
        chemin = '/Dossiers/DEMO/correspondances/2026-11-02--arch-2/original.eml'
        (self.root / chemin.lstrip('/')).write_bytes(b'Subject: remplace\r\n\r\nautre contenu')
        _d, _m, fichiers = self.exporter(profil='depart')
        inv = {r['archive']: r for r in json.loads(fichiers['correspondances/inventaire_eml.json'])}
        self.assertEqual(inv['arch-2']['statut'], 'non_inclus:empreinte_differente')
        self.assertEqual(inv['arch-1']['statut'], 'inclus')
        self.assertFalse([n for n in fichiers if 'arch-2' in n and n.endswith('.eml')])
        self.assertIn('arch-2', json.dumps(json.loads(fichiers['rapports/exclusions.json'])['courriels_non_inclus']))

    def test_X2_01_annonce_avant_lancement_et_demande(self):
        from agent import reversibilite5688 as rv
        est = R.estimer(self.desk)
        self.assertEqual(est['courriels_eml'], 2)
        self.assertEqual(est['documents'], 3)
        self.assertEqual(est['dossiers_reserves'], 1)
        self.assertEqual(est['non_couvertes'], 0)
        self.assertTrue(est['motifs'])
        html = rv.depart_html(self.desk, '/p')
        for texte in ('Préparer un départ vers un autre logiciel', 'name="profil" value="depart"', 'Courriels archivés en EML originaux : 2',
                      'Dossiers réservés : 1', 'pendant la pause de l’IA', 'Exclusions volontaires et leurs motifs', 'onglet Gestion'):
            self.assertIn(texte, html)
        self.assertNotIn('<script', html)
        self.assertIn('id="depart56108"', ''.join(rv.sections(self.desk, '/p')))
        avant = self.desk.db.total_changes
        rv.depart_html(self.desk, '/p')
        self.assertEqual(self.desk.db.total_changes, avant, 'l’annonce n’écrit rien')
        out = X.demander(self.desk, {'profil': 'depart'}, 'admin@example.test')
        options = json.loads(self.desk.db.execute('SELECT options FROM exports5688 WHERE id=?', (out['id'],)).fetchone()[0])
        self.assertEqual((options['profil'], options['documents'], options['dossiers_reserves']), ('depart', True, 'exclure'))
        self.desk.db.execute("UPDATE exports5688 SET statut='echec'")
        self.desk.db.commit()
        with self.assertRaisesRegex(Stop, 'option_export_invalide'):
            X.demander(self.desk, {'profil': 'tout_copier'}, 'admin@example.test')

    def test_X2_02_sans_modele_ni_reseau_pendant_la_pause(self):
        from agent import desk as desk_module
        source = Path(desk_module.__file__).read_text(encoding='utf-8')
        self.assertIn("'export5688'", re.search(r"free_kinds=\"[^\"]+\"", source).group(0))   # travail admis pendant la pause de l'IA
        self.assertIn('export5688', desk_module.CONTROL_JOBS)

        def interdit(*a, **k):
            raise AssertionError('appel de modèle pendant l’export')
        from agent import model
        with patch.object(model, 'generate', interdit, create=True), patch.object(model, 'chat', interdit, create=True):
            dest, meta, _f = self.exporter(profil='depart')      # sans_reseau() : toute connexion échouerait
        self.assertTrue(X.verifier_manifeste(dest)['ok'])

    def test_X3_01_fichier_declare_absent_interdit_complet(self):
        dest, meta, fichiers = self.exporter(profil='depart')
        domaine = meta['perimetre']['domaines']['dossiers']
        retire = next(n for n in domaine['fichiers'])
        manifeste = json.loads(fichiers['MANIFEST.json'])
        manifeste['fichiers'] = [f for f in manifeste['fichiers'] if f['chemin'] != retire]
        altere = Path(self.tmp.name) / 'altere.zip'
        with zipfile.ZipFile(altere, 'w') as z:
            for nom, data in fichiers.items():
                if nom != retire:
                    z.writestr(nom, json.dumps(manifeste) if nom == 'MANIFEST.json' else data)
        controle = X.verifier_manifeste(altere)
        self.assertFalse(controle['ok'])
        self.assertFalse(controle['complet'])
        self.assertIn('fichier déclaré par le bilan absent', [a['motif'] for a in controle['anomalies']])

    def test_X3_02_bilan_par_domaine(self):
        _d, meta, _f = self.exporter(profil='depart')
        domaines = meta['perimetre']['domaines']
        for nom in ('dossiers', 'courriels', 'agenda', 'taches', 'factures', 'relecture', 'routines', 'modeles', 'conversations', 'missions'):
            self.assertIn(nom, domaines)
            d = domaines[nom]
            self.assertEqual(d['attendu'], d['exporte'] + d['exclu'], nom)
        self.assertGreaterEqual(domaines['agenda']['exclu'], 1)      # l'événement du dossier réservé
        self.assertGreaterEqual(domaines['factures']['exclu'], 1)


if __name__ == '__main__':
    unittest.main()
