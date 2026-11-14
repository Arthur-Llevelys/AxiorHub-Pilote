"""5.6.100, lot C : intégrité du portefeuille et clôture (R05, R10, V100-02).

R05 — une lecture n'efface rien : cinq tables du portefeuille semées pour un dossier réservé et un dossier visible ; un compte restreint
ouvre Dossiers (replié, trié, tous les états), le volet détaillé (route de lecture), l'API du portefeuille et le tableau de bord : mêmes
lignes avant/après et AUCUNE requête d'écriture (INSERT, UPDATE, DELETE, REPLACE) sur une table métier, comptée par
``sqlite3.Connection.set_trace_callback`` sur toutes les connexions ouvertes pendant la requête. Seule la présence du compte (table
``membres5650``, dernière visite) est écrite par l'authentification, pas par la page. Mutations en mémoire : l'ancienne purge et l'ancien
tableau enregistré à l'affichage sont détectés. Maintenance explicite (simulation, jeton, sauvegarde, source complète) et rapprochement en
lecture seule.

R10 — clôture unique : une échéance confirmée ouverte bloque le menu, le PATCH, le POST générique et le service ; contrôles relus sous
verrou ; coupure entre registre et journal (rien n'est validé), entre journal et pause (reprise sans doublon) ; double clic = une
clôture ; réouverture motivée ; dossier historique et dossier du registre unique ; dossiers archivés sans décision de clôture signalés,
jamais rouverts automatiquement ; la liste Archivés signale l'échéance critique.

Données fictives (DURAND, MARTIN, DOS-0001, example.test).
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4
from zoneinfo import ZoneInfo

import test_v5648 as fixtures
from test_v5697_gestion import FauxAgenda
from agent import cloture5697, connexion5648, dossiers5697, gestion5697, gestion_ui5697, membres5650, pause5629, portfolio, portefeuille56100
from agent import procedure5697
from agent.common import Stop, digest, private_json

TABLES = portefeuille56100.TABLES
DML = re.compile(r'^\s*(?:INSERT|UPDATE|DELETE|REPLACE)\b', re.I)
CIBLE = re.compile(r'^\s*(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM)\s+["\[`]?(\w+)', re.I)
TECHNIQUES = frozenset(('membres5650',))   # présence du compte écrite par l'authentification (dernière visite), pas par l'écran
ADMIN = ('cabinet', 'administrateur')
RESERVE, VISIBLE = 'DOS-0002', 'DOS-0001'


def affaire(i):
    return {'id': 'DOS-%04d' % i, 'client_name': 'Client fictif %d' % i, 'path': '/Dossiers/%d' % i,
            'references': ['DOS-%04d' % i], 'aliases': [], 'correspondents': []}


def jour_cabinet():
    return datetime.now(ZoneInfo('Europe/Paris')).date()


@contextmanager
def compter_ecritures():
    """Toutes les requêtes SQL des connexions ouvertes pendant le bloc (``set_trace_callback``)."""
    vues = []
    vrai = sqlite3.connect

    def connect(*a, **k):
        cx = vrai(*a, **k)
        cx.set_trace_callback(vues.append)
        return cx
    with patch('sqlite3.connect', connect):
        yield vues


def ecritures_metier(vues):
    out = []
    for sql in vues:
        if DML.match(sql):
            m = CIBLE.match(sql)
            if (m.group(1) if m else '?') not in TECHNIQUES:
                out.append(sql[:160])
    return out


def semer(db, mid, n):
    """Cinq tables du portefeuille, avec des décisions humaines (épingle, état confirmé, rattachement et groupe confirmés)."""
    maintenant = '2026-11-02T09:00:00+00:00'
    db.execute("UPDATE matter_portfolio SET manual_state='active',pinned=1 WHERE matter=?", (mid,))
    db.execute('INSERT INTO portfolio_mail_links VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
               ('lien-%d' % n, 'cle-%d' % n, '<m%d@fictif.example.test>' % n, '', 'INBOX', str(n), mid, 100, 'certain', 'confirmation_avocat',
                'durand@example.test', '[]', maintenant, 'confirmed', maintenant))
    db.execute('INSERT INTO association_groups VALUES (?,?,?,?,?,?,?,?,?)',
               ('groupe-%d' % n, mid, 'durand@example.test', 100, 'certain', 1, '[]', 'confirmed', maintenant))
    db.execute('INSERT INTO portfolio_document_activity VALUES (?,?,?,?,?)', (mid, maintenant, '/Dossiers/%d/piece.pdf' % n, maintenant, ''))
    db.execute('INSERT INTO portfolio_calendar_links VALUES (?,?,?,?,?,?)', ('agenda-%d' % n, mid, '2026-12-01T09:00:00+00:00', 'Audience fictive', 'agenda', maintenant))
    db.commit()


def instantane(db):
    return {t: sorted(map(tuple, db.execute('SELECT * FROM %s' % t))) for t in TABLES}


class Base(unittest.TestCase):
    def setUp(self):
        self.b = fixtures.CabinetAccounts()
        self.b.setUp()
        self.addCleanup(self.b.doCleanups)
        self.addCleanup(membres5650.clear)
        self.addCleanup(self.b.desk.db.close)
        self.desk = self.b.desk
        private_json(self.b.f.matter_file, [affaire(1), affaire(2)])
        membres5650.set_reserve(self.b.f.c['state_dir'], RESERVE, ['titulaire@example.test'])

    def restreint(self):
        if not hasattr(self, '_cookie'):
            self._cookie = self.b.first_login('avocate@example.test', self.b.add(self.b.admin_cookie()))
        return self._cookie

    def lire(self, path, cookie=None, **args):
        extra = {'HTTP_COOKIE': '%s=%s' % (connexion5648.COOKIE, cookie)} if cookie else None
        return self.b.call(path, auth=cookie is None, query=urlencode(args), extra=extra)


# =========================================================================================== R05
class LectureSansEcriture(Base):
    PAGES = (('/dossiers', {}), ('/dossiers', {'tri': 'nom'}), ('/dossiers', {'state': 'all'}), ('/dossiers', {'volet': 'portefeuille'}),
             ('/dashboard', {}), ('/pilotage', {}), ('/api/v1/portfolio', {}), ('/api/v1/daily-dashboard', {}))

    def setUp(self):
        super().setUp()
        for i, mid in ((1, VISIBLE), (2, RESERVE)):
            portfolio.matter_status(self.desk, affaire(i))
            semer(self.desk.db, mid, i)

    def parcourir(self, cookie):
        """Chaque écran lu deux fois ; renvoie les écritures métier vues et les corps."""
        ecrites, corps = [], {}
        for path, args in self.PAGES:
            for _ in range(2):
                with compter_ecritures() as vues:
                    r = self.lire(path, cookie, **args)
                self.assertTrue(r['status'].startswith('200'), (path, args, r['status']))
                ecrites += [(path, s) for s in ecritures_metier(vues)]
                corps[(path, json.dumps(args))] = r['body'].decode('utf-8', 'replace')
        return ecrites, corps

    def test_R05_V100_02_compte_restreint_ne_modifie_aucune_ligne(self):
        avant = instantane(self.desk.db)
        self.assertTrue(all(avant[t] for t in TABLES))
        ecrites, corps = self.parcourir(self.restreint())
        self.assertEqual(ecrites, [])
        self.assertEqual(instantane(self.desk.db), avant)
        for (path, args), texte in corps.items():   # rien du dossier réservé : ni intitulé, ni identifiant
            self.assertNotIn('Client fictif 2', texte, (path, args))
            self.assertNotIn(RESERVE, texte, (path, args))
        resume = json.loads(corps[('/api/v1/portfolio', '{}')])['summary']
        self.assertEqual(sum(resume['counts'].values()), 1)                    # compteur sur le seul dossier visible
        # L'administrateur retrouve l'épingle et le lien confirmé du dossier réservé après le passage de l'autre compte.
        db = self.desk.db
        self.assertEqual(tuple(db.execute('SELECT manual_state,pinned FROM matter_portfolio WHERE matter=?', (RESERVE,)).fetchone()), ('active', 1))
        self.assertEqual(db.execute("SELECT status FROM portfolio_mail_links WHERE matter=?", (RESERVE,)).fetchone()[0], 'confirmed')
        page = self.lire('/dossiers', state='all')['body'].decode()
        self.assertIn('Client fictif 2', page)

    def test_R05_administrateur_lit_aussi_sans_ecrire(self):
        avant = instantane(self.desk.db)
        ecrites, _ = self.parcourir(None)
        self.assertEqual(ecrites, [])
        self.assertEqual(instantane(self.desk.db), avant)

    def test_R05_mutation_ancienne_purge_detectee(self):
        """Contre-épreuve : la purge d'avant la 5.6.100 (dossier masqué pris pour un dossier supprimé) est vue par ce test."""
        from agent.common import load_matters
        vrai = portfolio.portfolio_summary

        def ancienne(desk):
            valides = {m['id'] for m in load_matters(desk.c)}
            for (mid,) in desk.db.execute('SELECT matter FROM matter_portfolio').fetchall():
                if mid not in valides:
                    for t in TABLES:
                        desk.db.execute('DELETE FROM %s WHERE matter=?' % t, (mid,))
            desk.db.commit()
            return vrai(desk)
        avant = instantane(self.desk.db)
        cookie = self.restreint()
        with patch.object(portfolio, 'portfolio_summary', ancienne):
            with compter_ecritures() as vues:
                self.lire('/dossiers', cookie, volet='portefeuille')
        self.assertTrue(any('DELETE FROM matter_portfolio' in s for s in ecritures_metier(vues)))
        self.assertNotEqual(instantane(self.desk.db), avant)

    def test_R05_mutation_tableau_enregistre_a_l_affichage_detecte(self):
        from agent import proactive
        cookie = self.restreint()
        with patch.object(proactive, 'latest_dashboard', lambda desk: proactive.build_daily_dashboard(desk)):
            with compter_ecritures() as vues:
                self.assertTrue(self.lire('/dashboard', cookie)['status'].startswith('200'))
        self.assertTrue(any('daily_dashboards' in s for s in ecritures_metier(vues)))

    def test_R05_tableau_enregistre_jamais_servi_a_un_compte_restreint(self):
        from agent import proactive
        proactive.build_daily_dashboard(self.desk)                      # travail de fond : tout le cabinet, enregistré
        self.assertEqual(sum(proactive.latest_dashboard(self.desk)['portfolio']['counts'].values()), 2)
        r = self.lire('/api/v1/daily-dashboard', self.restreint())
        self.assertEqual(sum(json.loads(r['body'])['portfolio']['counts'].values()), 1)   # calcul en mémoire, périmètre de la personne


class VoletDiffere(Base):
    def test_R05_volet_replie_ne_lit_pas_le_portefeuille(self):
        def interdit(*a, **k):
            raise AssertionError('portefeuille lu alors que le volet est replié')
        with patch.object(portfolio, 'portfolio_summary', interdit), patch.object(portfolio, 'portfolio_rows', interdit):
            r = self.lire('/dossiers', self.restreint())
        page = r['body'].decode()
        self.assertTrue(r['status'].startswith('200'))
        self.assertIn('data-c100-volet="', page)
        self.assertIn('volet=portefeuille', page)                         # repli sans script : lien vers la page du volet
        self.assertIn('/static/v56100-integrite.js', page)
        self.assertNotIn('d79-matters', page)
        volet = self.lire('/dossiers', self.restreint(), volet='portefeuille')['body'].decode()
        self.assertIn('data-c100-volet-contenu', volet)
        self.assertIn('d79-matters', volet)
        self.assertNotIn('data-d97-liste', volet)                         # la route du volet ne recalcule pas l'écran des dossiers
        ouvert = self.lire('/dossiers', self.restreint(), state='all')['body'].decode()
        self.assertIn('<details class="d97-ancien" open>', ouvert)        # filtre explicite : rendu ouvert, sans script
        js = self.lire('/static/v56100-integrite.js', self.restreint())
        self.assertTrue(js['status'].startswith('200'))
        self.assertIn(b'data-c100-volet-contenu', js['body'])

    def test_R05_dossier_sans_projection_a_calculer_sans_ecriture(self):
        portfolio.matter_status(self.desk, affaire(1))                     # DOS-0001 classé, DOS-0003 pas encore
        private_json(self.b.f.matter_file, [affaire(1), affaire(2), affaire(3)])
        cookie = self.restreint()
        with compter_ecritures() as vues:
            page = self.lire('/dossiers', cookie, volet='portefeuille', state='all')['body'].decode()
        self.assertEqual(ecritures_metier(vues), [])
        self.assertIn('à calculer', page)
        self.assertIn('À calculer', page)
        self.assertIsNone(self.desk.db.execute('SELECT 1 FROM matter_portfolio WHERE matter=?', ('DOS-0003',)).fetchone())
        portfolio.completer_projection(self.desk)                          # travail de fond : complète sans rien supprimer
        self.assertIsNotNone(self.desk.db.execute('SELECT 1 FROM matter_portfolio WHERE matter=?', ('DOS-0003',)).fetchone())

    def test_R05_travail_de_fond_sous_un_regard_restreint_ne_supprime_rien(self):
        for i, mid in ((1, VISIBLE), (2, RESERVE)):
            portfolio.matter_status(self.desk, affaire(i))
            semer(self.desk.db, mid, i)
        avant = instantane(self.desk.db)
        membres5650.set_viewer({RESERVE})
        try:
            portfolio.classify_all(self.desk)
            portfolio.completer_projection(self.desk)
            resume = portfolio.portfolio_summary(self.desk)
        finally:
            membres5650.clear()
        apres = instantane(self.desk.db)
        for t in TABLES:
            self.assertEqual([r for r in apres[t] if RESERVE in r], [r for r in avant[t] if RESERVE in r], t)
        self.assertEqual(resume['grouped_confirmations'], 0)
        self.assertEqual(sum(resume['counts'].values()), 1)


class Maintenance(Base):
    def setUp(self):
        super().setUp()
        for i, mid in ((1, VISIBLE), (2, RESERVE)):
            portfolio.matter_status(self.desk, affaire(i))
            semer(self.desk.db, mid, i)
        db = self.desk.db
        maintenant = '2026-11-02T09:00:00+00:00'
        # DOS-0098 : ancien dossier disparu, sans décision ; DOS-0099 : disparu mais épinglé ; ANCIEN-7 : alias d'un dossier courant
        for mid, pinned in (('DOS-0098', 0), ('DOS-0099', 1), ('ANCIEN-7', 0)):
            db.execute('INSERT INTO matter_portfolio VALUES(?,?,?,?,?,?,?,?,?,?,?)', (mid, 'dormant', '', pinned, '', '', '', '', 0, '[]', maintenant))
            db.execute('INSERT INTO portfolio_calendar_links VALUES (?,?,?,?,?,?)', ('agenda-' + mid, mid, maintenant, 'Rendez-vous fictif', 'agenda', maintenant))
        dossiers5697.ensure_schema(self.desk)
        db.execute('INSERT INTO dossier_alias5697(genre,alias,dossier_id,cree_le) VALUES(?,?,?,?)', ('id', 'ANCIEN-7', VISIBLE, maintenant))
        db.commit()

    def test_R05_simulation_puis_nettoyage_sauvegarde_et_audite(self):
        avant = instantane(self.desk.db)
        plan = portefeuille56100.simuler(self.desk)
        self.assertEqual(instantane(self.desk.db), avant)                  # l'aperçu n'écrit rien
        self.assertTrue(plan['source']['saine'])
        self.assertEqual({(c['table'], c['dossier']) for c in plan['candidats']},
                         {('matter_portfolio', 'DOS-0098'), ('portfolio_calendar_links', 'DOS-0098'), ('portfolio_calendar_links', 'DOS-0099')})
        self.assertEqual([(c['table'], c['dossier']) for c in plan['decisions_conservees']], [('matter_portfolio', 'DOS-0099')])
        self.assertEqual({c['dossier_courant'] for c in plan['rattachables']}, {VISIBLE})
        with self.assertRaisesRegex(Stop, 'simulation_perimee'):
            portefeuille56100.nettoyer(self.desk, 'jeton-faux', 'Me MARTIN')
        self.assertEqual(instantane(self.desk.db), avant)
        out = portefeuille56100.nettoyer(self.desk, plan['jeton'], 'Me MARTIN')
        self.assertEqual(out['supprimees'], 3)
        sauvegarde = Path(out['sauvegarde'])
        self.assertTrue(sauvegarde.is_file())
        copie = sqlite3.connect(sauvegarde)
        try:
            self.assertEqual(copie.execute("SELECT COUNT(*) FROM matter_portfolio WHERE matter='DOS-0098'").fetchone()[0], 1)
        finally:
            copie.close()
        db = self.desk.db
        self.assertIsNone(db.execute("SELECT 1 FROM matter_portfolio WHERE matter='DOS-0098'").fetchone())
        self.assertIsNotNone(db.execute("SELECT 1 FROM matter_portfolio WHERE matter='DOS-0099'").fetchone())   # décision humaine gardée
        self.assertIsNotNone(db.execute("SELECT 1 FROM matter_portfolio WHERE matter='ANCIEN-7'").fetchone())    # alias : jamais supprimé
        for mid in (VISIBLE, RESERVE):
            self.assertEqual([r for r in instantane(db)['portfolio_mail_links'] if mid in r], [r for r in avant['portfolio_mail_links'] if mid in r])
        audit = db.execute("SELECT data FROM audit WHERE action='portefeuille56100_nettoyage'").fetchall()
        self.assertEqual(len(audit), 1)
        self.assertEqual(json.loads(audit[0][0])['supprimees'], 3)

    def test_R05_source_incomplete_ou_contexte_personnel_aucune_suppression(self):
        avant = instantane(self.desk.db)
        plan = portefeuille56100.simuler(self.desk)
        Path(self.b.f.matter_file).write_text('{illisible', encoding='utf-8')
        incomplet = portefeuille56100.simuler(self.desk)
        self.assertFalse(incomplet['source']['saine'])
        with self.assertRaisesRegex(Stop, 'source_incomplete'):
            portefeuille56100.nettoyer(self.desk, incomplet['jeton'], 'Me MARTIN')
        private_json(self.b.f.matter_file, [affaire(1), affaire(2)])
        membres5650.set_viewer({RESERVE})
        try:
            with self.assertRaisesRegex(Stop, 'maintenance_hors_requete'):
                portefeuille56100.nettoyer(self.desk, plan['jeton'], 'Me MARTIN')
        finally:
            membres5650.clear()
        self.assertEqual(instantane(self.desk.db), avant)

    def test_R05_rapprochement_lecture_seule_puis_restauration_d_un_dossier(self):
        sauvegarde = portefeuille56100.sauvegarder(self.desk, 'essai')
        db = self.desk.db
        for t in TABLES:                                                    # simulation de la purge de la 5.6.99 sur le dossier réservé
            db.execute('DELETE FROM %s WHERE matter=?' % t, (RESERVE,))
        db.execute("UPDATE matter_portfolio SET pinned=0 WHERE matter=?", (VISIBLE,))
        db.commit()
        courant = Path(self.b.f.c['state_dir']) / 'desk.sqlite3'
        empreintes = {p: p.read_bytes() for p in (sauvegarde,)}
        with compter_ecritures() as vues:
            rapport = portefeuille56100.rapprocher(sauvegarde, courant)
        self.assertEqual([s for s in vues if DML.match(s)], [])
        self.assertEqual(sauvegarde.read_bytes(), empreintes[sauvegarde])
        self.assertEqual(rapport['par_dossier'][RESERVE]['manquantes'], 5)
        self.assertEqual(rapport['par_dossier'][RESERVE]['decisions_manquantes'], 3)   # épingle, lien confirmé, groupe confirmé
        self.assertEqual(rapport['tables']['matter_portfolio']['differentes'][0]['dossier'], VISIBLE)
        apercu = portefeuille56100.restaurer_dossier(self.desk, sauvegarde, RESERVE, 'Me MARTIN')
        self.assertFalse(apercu['applique'])
        self.assertEqual(len(apercu['a_restaurer']), 5)
        self.assertIsNone(db.execute('SELECT 1 FROM matter_portfolio WHERE matter=?', (RESERVE,)).fetchone())
        fait = portefeuille56100.restaurer_dossier(self.desk, sauvegarde, RESERVE, 'Me MARTIN', appliquer=True)
        self.assertEqual(fait['restaurees'], 5)
        self.assertEqual(tuple(db.execute('SELECT manual_state,pinned FROM matter_portfolio WHERE matter=?', (RESERVE,)).fetchone()), ('active', 1))
        self.assertEqual(db.execute('SELECT pinned FROM matter_portfolio WHERE matter=?', (VISIBLE,)).fetchone()[0], 0)   # jamais d'écrasement
        with self.assertRaisesRegex(Stop, 'dossier_absent_du_registre'):
            portefeuille56100.restaurer_dossier(self.desk, sauvegarde, 'DOS-0098', 'Me MARTIN', appliquer=True)

    def test_R05_commande_rapprocher_en_json(self):
        import contextlib
        import importlib.util
        spec = importlib.util.spec_from_file_location('maintenance56100', Path(__file__).resolve().parents[1] / 'scripts' / 'maintenance56100.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sauvegarde = portefeuille56100.sauvegarder(self.desk, 'essai')
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            code = module.main(['rapprocher', str(sauvegarde), str(Path(self.b.f.c['state_dir']) / 'desk.sqlite3'), '--json'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(sortie.getvalue())['par_dossier'], {})


# =========================================================================================== R10
class Cloture(Base):
    def setUp(self):
        super().setUp()
        self.n = 0

    def cle(self):
        self.n += 1
        return 'cle-cloture-%04d' % self.n

    def dossier(self):
        d = dossiers5697.creer(self.desk, ADMIN, {'titre': 'DURAND c/ MARTIN', 'client': 'DURAND', 'nature': 'contentieux'}, uuid4().hex)
        return d['id']

    def echeance(self, mid):
        out = procedure5697.saisir_echeance(self.desk, ADMIN, mid, {'regle': 'appel_jugement_contentieux', 'depart': jour_cabinet().isoformat(),
                                                                    'source': 'Jugement fictif p. 1', 'depart_certain': True}, FauxAgenda())
        self.assertEqual(out['status'], 'confirmee')
        return out

    def accomplir(self, ech):
        procedure5697.decider(self.desk, ADMIN, ech['id'], {'action': 'accomplir', 'preuve': 'Acte fictif déposé', 'version': ech['revision'], 'cle': uuid4().hex})

    def donnees(self, mid, **extra):
        e = cloture5697._etat(self.desk, mid)
        return {'cle': self.cle(), 'version': e['version'], 'motif': 'Mission terminée (fictif)',
                'decisions': {'information_client': 'Client informé oralement (fictif)'}, **extra}

    def traces(self, mid):
        db = self.desk.db
        return {'clotures': [r[0] for r in db.execute('SELECT decision FROM clotures5697 WHERE matter=? ORDER BY at', (mid,))],
                'journal': [r[0] for r in db.execute('SELECT action FROM gestion_journal5697 WHERE matter=? AND objet=? ORDER BY id', (mid, 'dossier'))],
                'statut': cloture5697._etat(self.desk, mid)['etat'], 'pause': pause5629.is_paused(self.desk, mid),
                'pauses_auditees': db.execute("SELECT COUNT(*) FROM audit WHERE action='pause5629_dossier' AND data LIKE ?", ('%' + mid + '%',)).fetchone()[0]}

    def test_R10_V100_02_echeance_confirmee_bloque_menu_patch_post_et_service(self):
        mid = self.dossier()
        ech = self.echeance(mid)
        version = dossiers5697.lire(self.desk, ADMIN, mid)['version']
        liste = self.lire('/dossiers')['body'].decode()
        self.assertIn('vue=cloture', liste)
        self.assertNotIn('name="statut" value="archive"', liste)          # le menu n'archive plus : il ouvre la fiche de clôture
        self.assertNotIn('>Archiver<', liste)
        fiche = self.lire('/dossier', id=mid, vue='cloture')['body'].decode()
        self.assertIn('Bloquant', fiche)
        self.assertIn('vue=faits#g97-proc-t', fiche)                      # l'échéance bloquante mène au registre procédural
        for route, methode, corps in (('/modifier', 'POST', {'cle': uuid4().hex, 'version': version, 'statut': 'archive'}),
                                      ('', 'PATCH', {'cle': uuid4().hex, 'version': version, 'champs': {'statut': 'archive'}}),
                                      ('/modifier', 'POST', {'cle': uuid4().hex, 'version': version, 'statut': 'actif'})):
            r = self.b.call('/api440/m57/dossiers/' + mid + route, methode, corps)
            self.assertTrue(r['status'].startswith('409'), r['body'])
            self.assertEqual(json.loads(r['body'])['error'], 'utiliser_cloture')
        r = self.b.call('/api440/m5689/gestion/cloture/cloturer', 'POST', {'dossier': mid, **self.donnees(mid)})
        self.assertTrue(r['status'].startswith('409'), r['body'])
        refus = json.loads(r['body'])
        self.assertEqual(refus['error'], 'cloture_bloquee')
        self.assertIn('vue=faits', refus['points'][0]['lien'])
        self.assertEqual(self.traces(mid), {'clotures': [], 'journal': [], 'statut': 'actif', 'pause': False, 'pauses_auditees': 0})
        self.assertEqual(dossiers5697.lire(self.desk, ADMIN, mid)['statut'], 'actif')
        # Après résolution : un double clic (même clé) = une seule clôture ; puis une réouverture motivée.
        self.accomplir(ech)
        donnees = {'dossier': mid, **self.donnees(mid)}
        for _ in range(2):
            r = self.b.call('/api440/m5689/gestion/cloture/cloturer', 'POST', donnees)
            self.assertTrue(r['status'].startswith('200'), r['body'])
        t = self.traces(mid)
        self.assertEqual((t['clotures'], t['journal'], t['statut'], t['pause'], t['pauses_auditees']), (['cloture'], ['cloture'], 'archive', True, 1))
        self.assertEqual(dossiers5697.lire(self.desk, ADMIN, mid)['statut'], 'archive')
        version = cloture5697._etat(self.desk, mid)['version']
        r = self.b.call('/api440/m5689/gestion/cloture/rouvrir', 'POST', {'dossier': mid, 'cle': self.cle(), 'version': version, 'motif': ''})
        self.assertFalse(r['status'].startswith('200'))
        r = self.b.call('/api440/m5689/gestion/cloture/rouvrir', 'POST', {'dossier': mid, 'cle': self.cle(), 'version': version, 'motif': 'Nouvelle demande (fictive)'})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        t = self.traces(mid)
        self.assertEqual((t['clotures'], t['journal'], t['statut']), (['cloture', 'reouverture'], ['cloture', 'reouverture'], 'actif'))
        self.assertTrue(t['pause'])                                         # la pause se lève à part, depuis la fiche
        projection = next(m for m in json.loads(Path(self.b.f.matter_file).read_text(encoding='utf-8')) + self._surcouche() if m['id'] == mid)
        self.assertEqual(projection['registre5697']['statut'], 'actif')

    def _surcouche(self):
        p = Path(self.b.f.c['state_dir']) / 'registry-web.json'
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else []

    def test_R10_coupure_entre_registre_et_journal_rien_n_est_valide(self):
        mid = self.dossier()
        donnees = self.donnees(mid)

        def coupure(*a, **k):
            raise Stop('connexion_http_indisponible')
        with patch.object(cloture5697.g, 'journal', coupure):
            with self.assertRaises(Stop):
                cloture5697.cloturer(self.desk, ADMIN, mid, donnees)
        self.assertEqual(self.traces(mid), {'clotures': [], 'journal': [], 'statut': 'actif', 'pause': False, 'pauses_auditees': 0})
        self.assertEqual(dossiers5697.lire(self.desk, ADMIN, mid)['statut'], 'actif')   # transition annulée avec le reste
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM cloture_suites56100 WHERE matter=?', (mid,)).fetchone()[0], 0)
        for _ in range(2):                                                  # reprise avec la même clé, puis double clic
            self.assertEqual(cloture5697.cloturer(self.desk, ADMIN, mid, donnees)['etat'], 'archive')
        t = self.traces(mid)
        self.assertEqual((t['clotures'], t['journal'], t['pause'], t['pauses_auditees']), (['cloture'], ['cloture'], True, 1))

    def test_R10_coupure_entre_journal_et_pause_reprise_sans_doublon(self):
        mid = self.dossier()
        donnees = self.donnees(mid)
        with patch.object(pause5629, 'set_pause', side_effect=Stop('coupure_fictive')):
            with self.assertRaises(Stop):
                cloture5697.cloturer(self.desk, ADMIN, mid, donnees)
        t = self.traces(mid)
        self.assertEqual((t['clotures'], t['journal'], t['statut'], t['pause']), (['cloture'], ['cloture'], 'archive', False))
        suite = self.desk.db.execute("SELECT etat,erreur FROM cloture_suites56100 WHERE matter=? AND etape='pause'", (mid,)).fetchone()
        self.assertEqual(suite['etat'], 'a_faire')
        self.assertIn('coupure_fictive', suite['erreur'])
        for _ in range(2):                                                  # réponse perdue : le rejeu termine la pause, une seule fois
            cloture5697.cloturer(self.desk, ADMIN, mid, donnees)
        t = self.traces(mid)
        self.assertEqual((t['clotures'], t['journal'], t['statut'], t['pause'], t['pauses_auditees']), (['cloture'], ['cloture'], 'archive', True, 1))
        self.assertEqual(self.desk.db.execute("SELECT etat FROM cloture_suites56100 WHERE matter=? AND etape='pause'", (mid,)).fetchone()[0], 'fait')

    def test_R10_reouverture_annule_une_pause_restee_a_faire(self):
        mid = self.dossier()
        donnees = self.donnees(mid)
        with patch.object(pause5629, 'set_pause', side_effect=Stop('coupure_fictive')):
            with self.assertRaises(Stop):
                cloture5697.cloturer(self.desk, ADMIN, mid, donnees)
        cloture5697.rouvrir(self.desk, ADMIN, mid, {'cle': self.cle(), 'version': cloture5697._etat(self.desk, mid)['version'], 'motif': 'Erreur de clôture (fictive)'})
        cloture5697.cloturer(self.desk, ADMIN, mid, donnees)                # rejeu tardif de l'ancienne clôture : rien de plus
        t = self.traces(mid)
        self.assertEqual((t['statut'], t['pause'], t['clotures']), ('actif', False, ['cloture', 'reouverture']))
        self.assertEqual(self.desk.db.execute("SELECT etat FROM cloture_suites56100 WHERE matter=? AND etape='pause'", (mid,)).fetchone()[0], 'annulee')

    def test_R10_controles_relus_sous_verrou(self):
        """Une échéance confirmée écrite par une autre connexion entre le premier contrôle et la transition bloque la clôture."""
        mid = self.dossier()
        donnees = self.donnees(mid)
        vrai, appels = cloture5697.controles, []
        chemin = Path(self.b.f.c['state_dir']) / 'desk.sqlite3'

        def concurrent(*a, **k):
            out = vrai(*a, **k)
            if not appels:
                appels.append(1)
                autre = sqlite3.connect(chemin)
                try:
                    autre.execute("INSERT INTO deadlines450(id,matter,rule_id,start_event,start_date,status,origin,created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                                  ('ech-concurrente', mid, 'appel_jugement_contentieux', 'jugement', jour_cabinet().isoformat(), 'confirmee', 'manuel', 'x', 'x'))
                    autre.commit()
                finally:
                    autre.close()
            return out
        with patch.object(cloture5697, 'controles', concurrent):
            with self.assertRaisesRegex(Stop, 'cloture_bloquee'):
                cloture5697.cloturer(self.desk, ADMIN, mid, donnees)
        self.assertEqual(self.traces(mid)['statut'], 'actif')
        self.assertEqual(dossiers5697.lire(self.desk, ADMIN, mid)['statut'], 'actif')

    def test_R10_dossier_historique_du_registre_local(self):
        mid = VISIBLE                                                        # dossier du registre JSON, pas encore importé
        self.assertIsNone(gestion5697.registre_unique(self.desk, mid))
        ech = self.echeance(mid)
        with self.assertRaisesRegex(Stop, 'cloture_bloquee'):
            cloture5697.cloturer(self.desk, ADMIN, mid, self.donnees(mid))
        self.assertEqual(self.traces(mid)['clotures'], [])
        self.accomplir(ech)
        donnees = self.donnees(mid)
        for _ in range(2):
            cloture5697.cloturer(self.desk, ADMIN, mid, donnees)
        t = self.traces(mid)
        self.assertEqual((t['clotures'], t['statut'], t['pause'], t['pauses_auditees']), (['cloture'], 'archive', True, 1))
        e = cloture5697._etat(self.desk, mid)
        self.assertEqual((e['registre'], e['version']), ('local', 2))
        cloture5697.rouvrir(self.desk, ADMIN, mid, {'cle': self.cle(), 'version': 2, 'motif': 'Appel incident (fictif)'})
        self.assertEqual(self.traces(mid)['statut'], 'actif')

    def test_R10_archive_sans_decision_signale_jamais_rouvert_puis_regularise(self):
        mid = self.dossier()
        ech = self.echeance(mid)
        db = self.desk.db
        db.execute("UPDATE dossiers5697 SET statut='archive', version=version+1 WHERE id=?", (mid,))   # archivé par le menu de la 5.6.99
        db.commit()
        from agent.acces5697 import AGENT
        traces = []
        db.set_trace_callback(traces.append)
        try:
            etat = cloture5697.rapprochement_clotures(self.desk, AGENT)
            alertes = gestion_ui5697.alertes_archives_html(self.desk, '/p', [mid])
            fiche = gestion_ui5697.cloture_html(self.desk, ADMIN, '/p', mid)
        finally:
            db.set_trace_callback(None)
        self.assertEqual([s for s in traces if DML.match(s)], [])
        ligne = next(x for x in etat if x['dossier'] == mid)
        self.assertTrue(ligne['sans_decision'])
        self.assertEqual(ligne['echeances_critiques'], 1)
        self.assertIn('vue=cloture', ligne['lien'])
        self.assertIn('1 échéance critique ouverte', alertes[mid])
        self.assertIn('archivé sans décision de clôture', alertes[mid])
        for texte in ('Archivé sans décision de clôture', 'Une échéance critique est encore ouverte', 'cloture/regulariser', 'cloture/rouvrir'):
            self.assertIn(texte, fiche)
        self.assertEqual(self.traces(mid)['statut'], 'archive')            # rien n'est rouvert automatiquement
        self.assertEqual(db.execute('SELECT status FROM deadlines450 WHERE id=?', (ech['id'],)).fetchone()[0], 'confirmee')   # ni annulé
        liste = self.lire('/dossiers', statut='archive')['body'].decode()   # la liste Archivés ne masque pas l'échéance
        self.assertIn('1 échéance critique ouverte', liste)
        version = cloture5697._etat(self.desk, mid)['version']
        with self.assertRaisesRegex(Stop, 'cloture_bloquee'):
            cloture5697.regulariser(self.desk, ADMIN, mid, self.donnees(mid))
        self.accomplir(ech)
        r = self.b.call('/api440/m5689/gestion/cloture/regulariser', 'POST', {'dossier': mid, **self.donnees(mid)})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        t = self.traces(mid)
        self.assertEqual((t['clotures'], t['journal'], t['statut'], t['pause']), (['regularisation'], ['cloture_regularisee'], 'archive', True))
        self.assertEqual(cloture5697._etat(self.desk, mid)['version'], version)   # le statut n'a pas bougé
        with self.assertRaisesRegex(Stop, 'cloture_deja_enregistree'):
            cloture5697.regulariser(self.desk, ADMIN, mid, self.donnees(mid))
        self.assertEqual([x for x in cloture5697.rapprochement_clotures(self.desk, AGENT) if x['dossier'] == mid], [])

    def test_R10_liens_des_points_de_cloture_et_routes(self):
        from agent import gestion_web5697, standalone_auth
        mid = self.dossier()
        prep = cloture5697.preparer(self.desk, ADMIN, mid)
        liens = {c['id']: c['lien'] for c in prep['controles']}
        self.assertEqual(liens['echeances'], '/dossier?id=%s&vue=faits#g97-proc-t' % mid)
        self.assertEqual(liens['taches'], '/dossier?id=%s&vue=agenda#d97-agenda-t' % mid)
        self.assertEqual(liens['travail_a_facturer'], '/dossier?id=%s&vue=gestion#g97-hon-t' % mid)
        self.assertTrue(liens['factures_non_reglees'].startswith('/facturation?'))
        self.assertIn('#g97-rc-t', liens['partages'])
        self.assertTrue(gestion_web5697.allowed('avocat', 'gestion/cloture/regulariser'))
        self.assertFalse(gestion_web5697.allowed('assistant', 'gestion/cloture/regulariser'))
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/m5689/gestion/cloture/regulariser'))
        self.assertTrue(standalone_auth.allowed('avocat', 'POST', '/api440/m5689/gestion/cloture/regulariser'))

    def test_R10_transition_hors_transaction_refusee(self):
        mid = self.dossier()
        with self.assertRaisesRegex(Stop, 'transaction_requise'):
            dossiers5697.transition_cloture(self.desk, ADMIN, mid, 1, 'archive', uuid4().hex)
        self.assertEqual(dossiers5697.lire(self.desk, ADMIN, mid)['statut'], 'actif')


if __name__ == '__main__':
    unittest.main()
