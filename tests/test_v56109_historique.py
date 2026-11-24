"""5.6.109, lot H (F09) : historique des instructions de l'assistant — attribution, lecture sans écriture, droits actuels, pagination,
recherche, export, statut réel, reprise sans réexécution, brouillon privé.

Services réels (``integration.submit_question``, ``threads``, ``thread_messages``, moteur des travaux, ``acces5697``, ``membres5650``) ;
aucun modèle, aucun réseau. Données fictives (DURAND, MARTIN, BERNARD, ZORVAL, DOS-001, DOS-002, example.test).
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import io
import json
import unittest
from unittest.mock import patch

from agent import historique56109 as H, integration, membres5650
from agent.common import Stop, private_json
from agent.desk import Desk
import test_agent as fx

PREFIX = '/agent-courriel'
DURAND = ('durand@example.test', 'avocat')
MARTIN = ('martin@example.test', 'avocat')
BERNARD = ('bernard@example.test', 'assistant')
ADMIN = ('cabinet', 'administrateur')
DOS2 = {'id': 'DOS-002', 'client_name': 'ZORVAL', 'path': '/Dossiers/ZORVAL', 'references': ['DOS-002'], 'aliases': ['ZORVAL'], 'correspondents': []}


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fx.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        private_json(self.f.matter_file, self.f.matters + [DOS2])
        self.d = Desk(self.f.c)
        self.addCleanup(self.d.db.close)
        self.addCleanup(membres5650.clear)
        self.state = self.f.c['state_dir']
        self.auth = {'origin': 'https://cabinet.example.test', 'csrf': 'test-csrf', 'prefix': PREFIX}
        self.d.db.execute(membres5650.SCHEMA)
        for compte, role in (DURAND, MARTIN, BERNARD):
            self.d.db.execute('INSERT OR REPLACE INTO membres5650(owner,name,role,first_seen,last_seen) VALUES(?,?,?,?,?)', (compte, compte.split('@')[0].upper(), role, 'x', 'x'))
        self.d.db.commit()

    @contextmanager
    def requete(self, qui):
        membres5650.set_author(qui[0])
        membres5650.set_viewer_role(qui[1])
        membres5650.set_viewer(membres5650.hidden_for(self.state, qui[0], qui[1]))
        try:
            yield
        finally:
            membres5650.clear()

    def demander(self, qui, question='Analyse la clause de non-concurrence ZORVAL.', matter='DOS-001', thread=''):
        with self.requete(qui):
            return integration.submit_question(self.d, question, matter, thread_id=thread)

    def reponse(self, tid, texte='Réponse à relire.', sources=()):
        self.d.db.execute("INSERT INTO assistant_messages(thread_id,role,content,status,sources,job_id,created,updated) VALUES(?,?,?,?,?,NULL,?,?)",
                          (tid, 'assistant', texte, 'done', json.dumps(list(sources)), self.d.now(), self.d.now()))
        self.d.db.commit()

    def api(self, route, qui=DURAND, methode='GET', args=None, data=None, csrf='test-csrf'):
        raw = json.dumps(data or {}).encode()
        env = {'REQUEST_METHOD': methode, 'HTTP_ORIGIN': self.auth['origin'], 'HTTP_X_CSRF_TOKEN': csrf, 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw), 'HTTP_X_AXIORHUB_ROLE': qui[1], 'HTTP_X_AXIORHUB_USER': qui[0]}
        out = H.api(env, self.d, self.auth, PREFIX, H.PREFIXE + route, args or {}, methode)
        out['entetes'] = dict(out.get('headers', []))
        out['json'] = json.loads(out['body']) if out['kind'].startswith('application/json') else None
        return out

    def ecritures(self):
        sql = []
        self.d.db.set_trace_callback(sql.append)
        self.addCleanup(self.d.db.set_trace_callback, None)
        return sql


def _ecrit(sql):
    return [s for s in sql if s.lstrip().upper().startswith(('DELETE', 'UPDATE', 'INSERT', 'CREATE', 'ALTER', 'REPLACE'))]


class Schema(Base):
    def test_F09_schema_cree_au_demarrage_et_idempotent(self):
        noms = {r[0] for r in self.d.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ('assistant_thread_owners109', 'assistant_thread_members109', 'assistant_groups109', 'assistant_thread_preferences109',
                  'assistant_thread_links109', 'assistant_thread_contexts109', 'assistant_drafts109', 'assistant_ops109'):
            self.assertIn(t, noms)
        sql = self.ecritures()
        H.ensure_schema(self.d)
        self.assertEqual(_ecrit(sql), [])                       # sans changement : aucune écriture
        cols = {r[1] for r in self.d.db.execute('PRAGMA table_info(assistant_thread_owners109)')}
        self.assertTrue({'deleted_at', 'revision', 'preuve'} <= cols)

    def test_F09_migration_d_une_table_indicative_sans_colonnes(self):
        self.d.db.execute('DROP TABLE assistant_thread_owners109')
        self.d.db.execute('CREATE TABLE assistant_thread_owners109(thread_id TEXT PRIMARY KEY, cabinet_id TEXT NOT NULL, owner_id TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0)')
        self.d.db.commit()
        H.ensure_schema(self.d)
        cols = {r[1] for r in self.d.db.execute('PRAGMA table_info(assistant_thread_owners109)')}
        self.assertTrue({'deleted_at', 'revision', 'deleted_by', 'attribue_le', 'attribue_par', 'preuve'} <= cols)


class Attribution(Base):
    def test_F09_attribution_par_le_serveur_a_la_creation(self):
        out = self.demander(DURAND)
        o = self.d.db.execute('SELECT * FROM assistant_thread_owners109 WHERE thread_id=?', (out['thread_id'],)).fetchone()
        self.assertEqual((o['owner_id'], o['cabinet_id']), (DURAND[0], 'cabinet'))
        # le contexte du navigateur ne désigne jamais un propriétaire : un champ « owner » de page est ignoré
        with self.requete(MARTIN):
            out2 = integration.submit_question(self.d, 'Autre question', 'DOS-001', page_context={'page': '/assistant', 'owner': DURAND[0]})
        self.assertEqual(self.d.db.execute('SELECT owner_id FROM assistant_thread_owners109 WHERE thread_id=?', (out2['thread_id'],)).fetchone()[0], MARTIN[0])

    def test_F09_anciennes_lignes_non_attribuees_invisibles_puis_attribution_prouvee(self):
        quand = self.d.now()
        self.d.db.execute("INSERT INTO assistant_threads VALUES('%s','s','DOS-001','','Ancien échange ZORVAL',?,?)" % ('a' * 32), (quand, quand))
        self.d.db.execute("INSERT INTO assistant_messages(thread_id,role,content,status,sources,created,updated) VALUES(?,?,?,?,?,?,?)", ('a' * 32, 'user', 'Ancienne demande', 'done', '[]', quand, quand))
        self.d.db.commit()
        for qui in (DURAND, ADMIN):            # même sur une installation à un seul compte : aucune attribution implicite
            vue = H.fils(self.d, qui)
            self.assertEqual(vue['items'] + vue['epinglees'], [])
            with self.assertRaises(Stop):
                H.messages(self.d, qui, 'a' * 32)
        with self.requete(DURAND), self.assertRaises(Stop):
            integration.thread_messages(self.d, 'a' * 32)
        liste = H.a_attribuer(self.d, ADMIN)
        self.assertEqual(liste['total'], 1)
        self.assertNotIn('title', liste['items'][0])
        self.assertNotIn('Ancien', json.dumps(liste))
        with self.assertRaises(Stop):
            H.a_attribuer(self.d, DURAND)
        with self.assertRaisesRegex(Stop, 'preuve_requise'):
            H.attribuer_anciens(self.d, ADMIN, ['a' * 32], DURAND[0], 'court', 'cle-attrib-0001')
        with self.assertRaisesRegex(Stop, 'compte_inconnu'):
            H.attribuer_anciens(self.d, ADMIN, ['a' * 32], 'inconnu@example.test', 'Installation à un seul compte depuis l’origine, confirmé par écrit', 'cle-attrib-0002')
        out = H.attribuer_anciens(self.d, ADMIN, ['a' * 32], DURAND[0], 'Installation à un seul compte depuis l’origine, confirmé par écrit', 'cle-attrib-0003')
        self.assertEqual(out['attribues'], 1)
        self.assertEqual([x['id'] for x in H.fils(self.d, DURAND)['items']], ['a' * 32])
        self.assertEqual(H.fils(self.d, MARTIN)['items'], [])


class CompteUnique(Base):
    """Décision du cabinet (5.6.109) : quand UNE SEULE identité peut être l'auteur des anciens fils, ils lui sont attribués d'office au
    démarrage, une seule fois, avec trace. Trois modes : (a) distribution autonome, un seul compte actif ; (b) version serveur avec comptes
    (``comptes5648``) : l'administrateur « cabinet » seul, aucun compte de membre actif ; (c) authentification du serveur (identité
    « cabinet » partagée), sans compte de membre. Plusieurs identités, application Nextcloud ou décompte incertain : rien."""

    def setUp(self):
        super().setUp()
        import os
        import sqlite3
        import tempfile
        self.auth_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.auth_dir.cleanup)
        p = patch.dict(os.environ, {'AXIORHUB_AUTH_STATE': self.auth_dir.name})
        p.start()
        self.addCleanup(p.stop)
        self.sqlite3 = sqlite3
        self.d.db.execute('DELETE FROM membres5650')        # aucun membre vu
        self.d.db.execute('DELETE FROM settings WHERE key=?', (H.CLE_MIGRATION,))   # le démarrage du banc (sans fil) a déjà décidé : on repart
        quand = self.d.now()
        for i in range(2):
            tid = ('%d' % i) * 32
            self.d.db.execute('INSERT INTO assistant_threads VALUES(?,?,?,?,?,?,?)', (tid, 's', 'DOS-001', '', 'Ancien échange %d' % i, quand, quand))
            self.d.db.execute('INSERT INTO assistant_messages(thread_id,role,content,status,sources,created,updated) VALUES(?,?,?,?,?,?,?)',
                              (tid, 'user', 'Ancienne demande %d' % i, 'done', '[]', quand, quand))
        self.d.db.commit()
        self.anciens = ['0' * 32, '1' * 32]

    def comptes(self, *emails, abime=False):
        """(a) distribution autonome : ``users.sqlite3`` des comptes du cabinet."""
        from pathlib import Path
        chemin = Path(self.auth_dir.name) / 'users.sqlite3'
        if chemin.exists():
            chemin.unlink()
        if abime:
            chemin.write_bytes(b'pas une base SQLite')
            return
        db = self.sqlite3.connect(str(chemin))
        db.execute('CREATE TABLE users(id INTEGER PRIMARY KEY, email TEXT UNIQUE, name TEXT, role TEXT, active INTEGER)')
        for e in emails:
            db.execute('INSERT INTO users(email,name,role,active) VALUES(?,?,?,1)', (e, e, 'avocat'))
        db.execute('INSERT INTO users(email,name,role,active) VALUES(?,?,?,0)', ('ancien@example.test', 'Ancien', 'avocat'))   # inactif : ne compte pas
        db.commit()
        db.close()

    def comptes_serveur(self, *idents, abime=False):
        """(b) version serveur avec comptes : ``comptes5648.sqlite3`` (l'administrateur, identité « cabinet », n'y figure pas)."""
        from pathlib import Path
        from agent import connexion5648
        chemin = Path(self.state) / connexion5648.DB_NAME
        if chemin.exists():
            chemin.unlink()
        if abime:
            chemin.write_bytes(b'pas une base SQLite')
            return
        db = self.sqlite3.connect(str(chemin))
        db.executescript(connexion5648.SCHEMA)
        for i in idents:
            db.execute("INSERT INTO users(ident,salt,hash,active,created) VALUES(?,?,?,1,'x')", (i, 's', 'h'))
        db.execute("INSERT INTO users(ident,salt,hash,active,created) VALUES('ancien.compte','s','h',0,'x')")    # inactif : ne compte pas
        db.commit()
        db.close()

    def attribues(self):
        return {r[0]: r[1] for r in self.d.db.execute('SELECT thread_id, owner_id FROM assistant_thread_owners109')}

    def decision(self):
        return self.d.settings(H.CLE_MIGRATION, None)

    # ---- (a) distribution autonome
    def test_F09_a_autonome_compte_unique_fils_visibles_apres_le_demarrage(self):
        self.comptes(DURAND[0])
        self.assertEqual(H.fils(self.d, DURAND)['items'], [])
        H.ensure_schema(self.d)                                     # démarrage (Desk._prepare)
        self.assertEqual(self.attribues(), {t: DURAND[0] for t in self.anciens})
        self.assertEqual(sorted(x['id'] for x in H.fils(self.d, DURAND)['items']), self.anciens)
        preuve = self.d.db.execute('SELECT preuve, attribue_par FROM assistant_thread_owners109').fetchone()
        self.assertIn('identité unique de l’installation à la date de la migration', preuve[0])
        self.assertEqual(preuve[1], 'migration_compte_unique')
        trace = self.d.db.execute("SELECT data FROM audit WHERE action='historique56109_compte_unique' ORDER BY id DESC").fetchone()
        self.assertIn('autonome', trace[0])

    def test_F09_a_second_demarrage_aucune_ecriture(self):
        self.comptes(DURAND[0])
        H.ensure_schema(self.d)
        sql = self.ecritures()
        H.ensure_schema(self.d)
        self.assertEqual(_ecrit(sql), [])

    def test_F09_a_deux_comptes_rien_n_est_attribue_meme_plus_tard(self):
        self.comptes(DURAND[0], MARTIN[0])
        H.ensure_schema(self.d)
        self.assertEqual(self.attribues(), {})
        self.assertEqual(self.decision()['etat'], 'plusieurs_comptes')
        self.comptes(DURAND[0])                                     # décision « plusieurs comptes » : jamais rejouée
        H.ensure_schema(self.d)
        self.assertEqual(self.attribues(), {})

    def test_F09_fil_cree_apres_un_second_compte_jamais_attribue(self):
        self.comptes(DURAND[0])
        H.ensure_schema(self.d)
        self.comptes(DURAND[0], MARTIN[0])
        quand = self.d.now()
        self.d.db.execute('INSERT INTO assistant_threads VALUES(?,?,?,?,?,?,?)', ('9' * 32, 's', 'DOS-001', '', 'Plus récent', quand, quand))
        self.d.db.commit()
        H.ensure_schema(self.d)
        self.assertNotIn('9' * 32, self.attribues())

    # ---- (b) version serveur avec comptes
    def test_F09_b_serveur_administrateur_seul_attribue_a_cabinet(self):
        self.comptes_serveur()
        H.ensure_schema(self.d)
        self.assertEqual(self.attribues(), {t: 'cabinet' for t in self.anciens})
        self.assertEqual(sorted(x['id'] for x in H.fils(self.d, ADMIN)['items']), self.anciens)
        self.assertIn('serveur_comptes', self.d.db.execute("SELECT data FROM audit WHERE action='historique56109_compte_unique' ORDER BY id DESC").fetchone()[0])

    def test_F09_b_serveur_un_compte_de_membre_en_plus_de_l_administrateur_refuse(self):
        self.comptes_serveur(DURAND[0])                             # l'administrateur ET un membre : deux auteurs possibles
        H.ensure_schema(self.d)
        self.assertEqual(self.attribues(), {})
        self.assertEqual(self.decision()['etat'], 'plusieurs_comptes')

    def test_F09_b_serveur_base_des_comptes_illisible_rien(self):
        self.comptes_serveur(abime=True)
        H.ensure_schema(self.d)
        self.assertEqual((self.attribues(), self.decision()), ({}, None))     # incertain : rien, réexaminé au démarrage suivant
        self.comptes_serveur()
        H.ensure_schema(self.d)
        self.assertEqual(self.attribues(), {t: 'cabinet' for t in self.anciens})

    # ---- (c) authentification du serveur : identité « cabinet » partagée, sans compte de membre
    def test_F09_c_authentification_du_serveur_identite_unique(self):
        self.d.db.execute("INSERT INTO membres5650(owner,name,role,first_seen,last_seen) VALUES('cabinet','Cabinet','administrateur','x','x')")
        self.d.db.commit()                                          # la seule identité jamais vue est « cabinet »
        H.ensure_schema(self.d)
        self.assertEqual(self.attribues(), {t: 'cabinet' for t in self.anciens})
        self.assertIn('authentification_serveur', self.d.db.execute("SELECT data FROM audit WHERE action='historique56109_compte_unique' ORDER BY id DESC").fetchone()[0])

    def test_F09_c_autre_identite_deja_vue_refuse(self):
        """Toute autre identité connue du serveur (membre vu, droits ou réservation par compte, propriétaire d'un fil, d'une mission ou d'un
        jeton personnel) contredit « une seule identité »."""
        from agent.missions567 import ensure_schema as missions
        missions(self.d)
        cols = [r[1] for r in self.d.db.execute('PRAGMA table_info(missions_v567)')]
        cas = (lambda: self.d.db.execute("INSERT INTO membres5650(owner,name,role,first_seen,last_seen) VALUES(?,?,?,?,?)", (DURAND[0], 'D', 'avocat', 'x', 'x')),
               lambda: membres5650.set_reserve(self.state, 'DOS-002', [MARTIN[0]]),
               lambda: self.d.db.execute('INSERT INTO missions_v567(%s) VALUES(%s)' % (','.join(cols), ','.join('?' * len(cols))),
                                         [{'id': 'm9', 'owner': BERNARD[0]}.get(c, '') for c in cols]))
        for i, poser in enumerate(cas):
            with self.subTest(cas=i):
                self.d.db.execute('DELETE FROM settings WHERE key=?', (H.CLE_MIGRATION,))
                self.d.db.execute('DELETE FROM membres5650')
                self.d.db.execute('DELETE FROM missions_v567')
                membres5650.set_reserve(self.state, 'DOS-002', [])
                self.d.db.commit()
                poser()
                self.d.db.commit()
                H.ensure_schema(self.d)
                self.assertEqual(self.attribues(), {})
                self.assertEqual(self.decision()['etat'], 'plusieurs_comptes')

    def test_F09_c_application_nextcloud_jamais_d_office(self):
        """Application Nextcloud : les comptes Nextcloud autorisés ne sont pas connus localement : unicité non établie."""
        self.d.c['exapp'] = {'app_id': 'axiorhub', 'service_user': 'axiorhub-service', 'prefix': PREFIX}
        H.ensure_schema(self.d)
        self.assertEqual((self.attribues(), self.decision()), ({}, None))

    def test_F09_registres_contradictoires_ou_panne_rien(self):
        self.comptes(DURAND[0])
        self.comptes_serveur()                                      # deux registres de comptes à la fois : mode non établi
        H.ensure_schema(self.d)
        self.assertEqual((self.attribues(), self.decision()), ({}, None))
        with patch.object(H, '_identites_installation', side_effect=OSError('panne')):
            H.ensure_schema(self.d)
        self.assertEqual((self.attribues(), self.decision()), ({}, None))
        self.comptes(abime=True)
        from pathlib import Path
        from agent import connexion5648
        (Path(self.state) / connexion5648.DB_NAME).unlink()
        H.ensure_schema(self.d)                                     # liste des comptes autonomes illisible
        self.assertEqual((self.attribues(), self.decision()), ({}, None))

    def test_F09_phrase_d_information_traduite(self):
        self.assertIn(H.INFO_COMPTE_UNIQUE, H.attribuer_html(self.d, ADMIN, PREFIX))
        from agent import ecrans5632 as E
        for langue in ('en', 'de', 'es'):
            self.assertIsNotNone(E.phrase(H.INFO_COMPTE_UNIQUE, E.dictionary(langue)), langue)


class Lecture(Base):
    def test_F09_contre_exemple_lecture_du_fil_d_un_autre_refusee(self):
        """Avant 5.6.109 : ``thread_messages`` et ``/assistant?thread=`` rendaient le fil de n'importe qui (identifiant connu)."""
        tid = self.demander(DURAND)['thread_id']
        with self.requete(MARTIN):
            with self.assertRaisesRegex(Stop, 'conversation_absente'):
                integration.thread_messages(self.d, tid)
            self.assertNotIn(tid, [x['id'] for x in integration.threads(self.d, 30)])
        with self.requete(DURAND):
            self.assertEqual(len(integration.thread_messages(self.d, tid)), 1)
            self.assertIn(tid, [x['id'] for x in integration.threads(self.d, 30)])
        # hors requête (travaux du cabinet) : inchangé
        self.assertEqual(len(integration.thread_messages(self.d, tid)), 1)
        # même réponse neutre qu'un fil absent, par l'API
        autre, absent = self.api('fil', MARTIN, args={'id': tid}), self.api('fil', MARTIN, args={'id': 'b' * 32})
        self.assertEqual((autre['status'], autre['json']), (absent['status'], absent['json']))
        self.assertEqual(autre['status'], '404 Not Found')
        self.assertEqual(autre['entetes']['Cache-Control'], 'private, no-store')

    def test_F09_contre_exemple_vue_ancienne_assistant_protegee(self):
        from agent.workspace_ui import assistant_panel
        tid = self.demander(DURAND)['thread_id']
        link = lambda p, label, **kw: label
        form = lambda *a, **k: ''
        with self.requete(MARTIN), self.assertRaises(Stop):
            assistant_panel(self.d.c, self.d, [], {'thread': tid}, form, link)
        with self.requete(DURAND):
            self.assertIn('Analyse la clause', assistant_panel(self.d.c, self.d, [], {'thread': tid}, form, link))

    def test_F09_contre_exemple_lecture_sans_purge(self):
        """Avant : ``threads()`` supprimait les fils de plus de 90 jours et ``workspace.history`` les échanges, à chaque affichage."""
        vieux = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        tid = self.demander(DURAND)['thread_id']
        self.d.db.execute('UPDATE assistant_threads SET updated=?, created=? WHERE id=?', (vieux, vieux, tid))
        self.d.db.execute("INSERT INTO conversations VALUES(NULL,'scope-x','Q','{}','[]',?)", (vieux,))
        self.d.db.commit()
        sql = self.ecritures()
        with self.requete(DURAND):
            integration.threads(self.d, 30)
            integration.threads(self.d, 30, purger=True)
        from agent.workspace import history
        history(self.d, 'scope-x')
        H.fils(self.d, DURAND)
        H.messages(self.d, DURAND, tid)
        H.recherche(self.d, DURAND, 'clause')
        self.assertEqual(_ecrit(sql), [])
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM assistant_threads WHERE id=?', (tid,)).fetchone()[0], 1)
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM conversations WHERE scope='scope-x'").fetchone()[0], 1)

    def test_F09_plus_de_100_messages_pagines_sans_ecriture_ni_mise_en_file(self):
        tid = self.demander(DURAND)['thread_id']
        for i in range(204):
            self.reponse(tid, 'Message %d' % i)
        travaux = self.d.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        sql = self.ecritures()
        vus, apres = [], 0
        while True:
            page = H.messages(self.d, DURAND, tid, apres=apres, limite=50)
            self.assertFalse(page['reexecution'])
            vus += [m['id'] for m in page['items']]
            if page['next'] is None:
                break
            apres = page['next']
        self.assertEqual(len(vus), 205)
        self.assertEqual(vus, sorted(vus))
        self.assertEqual(_ecrit(sql), [])
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], travaux)
        for valeur in (0, 101, True, '20'):
            with self.assertRaises(Stop):
                H.messages(self.d, DURAND, tid, limite=valeur)

    def test_F09_droits_dossier_retires_liste_detail_recherche_export(self):
        tid = self.demander(DURAND, matter='DOS-002')['thread_id']
        self.assertIn(tid, [x['id'] for x in H.fils(self.d, DURAND)['items']])
        membres5650.set_reserve(self.state, 'DOS-002', [MARTIN[0]])       # accès de DURAND retiré
        self.assertNotIn(tid, [x['id'] for x in H.fils(self.d, DURAND)['items']])
        with self.assertRaisesRegex(Stop, 'introuvable'):
            H.messages(self.d, DURAND, tid)
        self.assertEqual(H.recherche(self.d, DURAND, 'clause')['total'], 0)
        with self.assertRaisesRegex(Stop, 'introuvable'):
            H.exporter(self.d, DURAND, tid, 'md')
        membres5650.set_reserve(self.state, 'DOS-002', [])
        self.assertIn(tid, [x['id'] for x in H.fils(self.d, DURAND)['items']])

    def test_F09_source_citee_devenue_interdite_masque_le_message(self):
        tid = self.demander(DURAND)['thread_id']
        self.reponse(tid, 'Extrait confidentiel ZORVAL : 12 345,67 euros.', [{'id': 'k1', 'matter': 'DOS-002', 'path': '/Dossiers/ZORVAL/Contrat.pdf'}])
        membres5650.set_reserve(self.state, 'DOS-002', [MARTIN[0]])
        page = H.messages(self.d, DURAND, tid)
        masque = [m for m in page['items'] if m['role'] == 'assistant'][0]
        self.assertTrue(masque['masque'])
        self.assertEqual(masque['contenu'], H.MASQUE)
        self.assertEqual(masque['sources'], [])
        self.assertNotIn('12 345', json.dumps(H.fils(self.d, DURAND)))
        self.assertEqual(H.recherche(self.d, DURAND, '12 345')['total'], 0)

    def test_F09_curseur_opaque_signe_lie_au_compte_et_aux_filtres(self):
        for i in range(3):
            self.demander(DURAND, 'Question %d' % i)
        page = H.fils(self.d, DURAND, limite=1)
        self.assertIsNotNone(page['next'])
        self.assertNotIn('DOS', page['next'])
        suite = H.fils(self.d, DURAND, apres=page['next'], limite=1)
        self.assertEqual(len(suite['items']), 1)
        for qui, filtres, jeton in ((MARTIN, None, page['next']), (DURAND, {'q': 'Question'}, page['next']), (DURAND, None, page['next'][:-2] + 'zz')):
            with self.assertRaisesRegex(Stop, 'curseur_invalide'):
                H.fils(self.d, qui, filtres, apres=jeton, limite=1)

    def test_F09_filtres_dossier_periode_en_cours_archives(self):
        a = self.demander(DURAND, matter='DOS-001')['thread_id']
        b = self.demander(DURAND, matter='DOS-002')['thread_id']
        self.assertEqual([x['id'] for x in H.fils(self.d, DURAND, {'dossier': 'DOS-002'})['items']], [b])
        self.assertEqual({x['id'] for x in H.fils(self.d, DURAND, {'en_cours': '1'})['items']}, {a, b})
        self.d.db.execute("UPDATE assistant_messages SET status='done' WHERE thread_id=?", (a,))
        self.d.db.commit()
        self.assertEqual([x['id'] for x in H.fils(self.d, DURAND, {'en_cours': '1'})['items']], [b])
        self.assertEqual(len(H.fils(self.d, DURAND, {'periode': '7j'})['items']), 2)
        with self.assertRaises(Stop):
            H.fils(self.d, DURAND, {'periode': '2j'})
        with self.assertRaises(Stop):
            H.fils(self.d, DURAND, {'q': 'x' * 201})
        self.assertEqual(H.fils(self.d, DURAND, {'archives': '1'})['items'], [])


class Etats(Base):
    def test_F09_statut_reel_du_message_et_du_travail(self):
        out = self.demander(DURAND)
        tid, job = out['thread_id'], out['job_id']
        etat = lambda: H.fils(self.d, DURAND)['items'][0]['etat']
        self.assertEqual(etat(), 'en_attente')              # reçu : demande enregistrée, pas une réponse
        self.d.db.execute("UPDATE jobs SET status='suspended' WHERE id=?", (job,))
        self.d.db.commit()
        self.assertEqual(etat(), 'en_pause')
        self.d.db.execute("UPDATE jobs SET status='running' WHERE id=?", (job,))
        self.d.db.commit()
        self.assertEqual(etat(), 'en_cours')
        self.d.db.execute("UPDATE assistant_messages SET status='error' WHERE thread_id=?", (tid,))
        self.d.db.commit()
        self.assertEqual(etat(), 'echec')
        self.d.db.execute("UPDATE assistant_messages SET status='done' WHERE thread_id=?", (tid,))
        self.d.db.commit()
        self.assertEqual(etat(), 'terminee')
        from agent.missions567 import ensure_schema
        ensure_schema(self.d)
        cols = [r[1] for r in self.d.db.execute('PRAGMA table_info(missions_v567)')]
        valeurs = {c: '' for c in cols}
        valeurs.update({'id': 'm1', 'state': 'decision', 'ref': 'ask:' + tid})
        self.d.db.execute('INSERT INTO missions_v567(%s) VALUES(%s)' % (','.join(cols), ','.join('?' * len(cols))), [valeurs[c] for c in cols])
        self.d.db.commit()
        self.assertEqual(etat(), 'a_preciser')
        m = H.messages(self.d, DURAND, tid)['items'][0]
        self.assertEqual((m['etat_source'], m['job_id']), ('done', job))   # état brut conservé à côté du libellé


class Reprise(Base):
    def test_F09_reouvrir_ne_relance_rien_et_propose_reprendre(self):
        tid = self.demander(DURAND)['thread_id']
        travaux = self.d.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        form = lambda action, label, fields=None, extra='': '<form data-action="%s">%s%s</form>' % (action, json.dumps(fields), extra)
        html = H.vue_assistant(self.d, DURAND, PREFIX, {'vue': 'historique', 'thread': tid}, form, self.d and [])
        self.assertIn('Reprendre cette instruction', html)
        self.assertIn('"thread": "%s"' % tid, html)
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], travaux)
        self.assertNotIn('style=', html)

    def test_F09_continuer_reserve_au_proprietaire_et_au_participant(self):
        """Garde-fou : un autre compte ne peut pas ajouter un message dans un fil qui n'est pas le sien (mutation : sans le contrôle, il le peut)."""
        tid = self.demander(DURAND)['thread_id']
        with self.assertRaisesRegex(Stop, 'conversation_autre_contexte'):
            self.demander(MARTIN, 'Ajout non autorisé', thread=tid)
        self.d.db.execute("INSERT INTO assistant_thread_members109(thread_id,cabinet_id,user_id,role,granted_by,created_at) VALUES(?,?,?,?,?,?)",
                          (tid, 'cabinet', MARTIN[0], 'viewer', DURAND[0], 'x'))
        self.d.db.commit()
        with self.assertRaisesRegex(Stop, 'conversation_autre_contexte'):
            self.demander(MARTIN, 'Lecteur : refus', thread=tid)
        self.d.db.execute("UPDATE assistant_thread_members109 SET role='contributor' WHERE thread_id=?", (tid,))
        self.d.db.commit()
        self.demander(MARTIN, 'Participant : accepté', thread=tid)
        self.demander(DURAND, 'Propriétaire : accepté', thread=tid)
        self.d.db.execute("UPDATE assistant_thread_owners109 SET deleted_at='2026-11-20' WHERE thread_id=?", (tid,))
        self.d.db.commit()
        with self.assertRaisesRegex(Stop, 'conversation_supprimee'):
            self.demander(DURAND, 'Fil supprimé', thread=tid)
        with patch.object(H, 'controler_continuation', lambda *a, **k: None):     # mutation en mémoire : le garde-fou retiré
            self.d.db.execute("UPDATE assistant_thread_owners109 SET deleted_at=NULL WHERE thread_id=?", (tid,))
            self.d.db.commit()
            self.demander(BERNARD, 'Sans garde-fou : passerait', thread=tid)

    def test_F09_soumission_atomique(self):
        """Fil, message, propriétaire et travail sont validés ensemble : un refus de mise en file ne laisse ni fil ni message orphelin."""
        avant = [self.d.db.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0] for t in ('assistant_threads', 'assistant_messages', 'assistant_thread_owners109')]
        with patch.object(Desk, 'enqueue', side_effect=Stop('file_attente_pleine')), self.assertRaises(Stop):
            self.demander(DURAND)
        self.d.db.commit()          # même une validation ultérieure ne fait pas apparaître de reste
        apres = [self.d.db.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0] for t in ('assistant_threads', 'assistant_messages', 'assistant_thread_owners109')]
        self.assertEqual(avant, apres)
        out = self.demander(DURAND)
        m = self.d.db.execute('SELECT job_id FROM assistant_messages WHERE id=?', (out['message_id'],)).fetchone()
        self.assertEqual(m['job_id'], out['job_id'])


class RechercheExport(Base):
    def test_F09_recherche_titres_et_messages_seulement_autorises(self):
        a = self.demander(DURAND, 'Bail commercial Valbrune')['thread_id']
        self.reponse(a, 'Le congé doit être délivré par acte de commissaire de justice.')
        b = self.demander(MARTIN, 'Congé dans le dossier MARTIN')['thread_id']
        r = H.recherche(self.d, DURAND, 'congé')
        self.assertEqual([x['id'] for x in r['items']], [a])
        self.assertEqual(r['total'], 1)
        self.assertIn('commissaire', r['items'][0]['extrait'])
        self.assertEqual([x['id'] for x in H.recherche(self.d, MARTIN, 'congé')['items']], [b])
        self.assertEqual(H.recherche(self.d, DURAND, '%')['total'], 0)        # syntaxe échappée, jamais un joker
        with self.assertRaises(Stop):
            H.recherche(self.d, DURAND, 'x' * 201)

    def test_F09_export_markdown_et_json_droit_exporter(self):
        tid = self.demander(DURAND)['thread_id']
        self.reponse(tid, 'Réponse sourcée.', [{'id': 's1', 'matter': 'DOS-001', 'path': '/Dossiers/DEMO/Contrat.pdf'}])
        corps, kind, nom = H.exporter(self.d, DURAND, tid, 'json')
        data = json.loads(corps)
        self.assertEqual(kind, 'application/json; charset=utf-8')
        self.assertEqual(data['schema'], 'historique_ia/1')
        self.assertEqual(len(data['messages']), 2)
        self.assertIn('manifeste', data)
        self.assertNotIn('csrf', corps.lower())
        md = H.exporter(self.d, DURAND, tid, 'md')[0]
        self.assertIn('Réponse sourcée.', md)
        self.assertIn('Pièces jointes : non exportées', md)
        with self.assertRaises(Stop):
            H.exporter(self.d, MARTIN, tid, 'md')
        membres5650.set_rights(self.state, ADMIN[0], DURAND[0], 'avocat', {'exporter': False})
        with self.assertRaisesRegex(Stop, 'export_interdit'):
            H.exporter(self.d, DURAND, tid, 'md')
        r = self.api('exporter', DURAND, args={'id': tid, 'format': 'md'})
        self.assertEqual(r['status'], '403 Forbidden')


class Brouillon(Base):
    def test_F09_brouillon_prive_cote_serveur(self):
        H.brouillon_enregistrer(self.d, DURAND, 'fil:nouvelle', 'Projet de demande DURAND')
        self.assertEqual(H.brouillon_lire(self.d, DURAND, 'fil:nouvelle')['texte'], 'Projet de demande DURAND')
        self.assertEqual(H.brouillon_lire(self.d, MARTIN, 'fil:nouvelle')['texte'], '')
        H.brouillon_enregistrer(self.d, DURAND, 'fil:nouvelle', '')
        self.assertEqual(H.brouillon_lire(self.d, DURAND, 'fil:nouvelle')['texte'], '')
        with self.assertRaises(Stop):
            H.brouillon_enregistrer(self.d, DURAND, '<script>', 'x')
        r = self.api('brouillon', DURAND, 'POST', data={'contexte': 'fil:nouvelle', 'texte': 'x'}, csrf='mauvais')
        self.assertEqual(r['status'], '403 Forbidden')
        r = self.api('brouillon', DURAND, 'POST', data={'contexte': 'fil:nouvelle', 'texte': 'Reprise'})
        self.assertEqual(r['json']['texte'], 'Reprise')
        self.assertEqual(r['entetes']['Cache-Control'], 'private, no-store')

    def test_F09_routes_declarees(self):
        from agent import standalone_auth, web440
        self.assertTrue(H.allowed('assistant', 'GET', H.PREFIXE + 'fils'))
        self.assertFalse(H.allowed('avocat', 'POST', H.PREFIXE + 'attribuer'))
        self.assertTrue(H.allowed('administrateur', 'POST', H.PREFIXE + 'attribuer'))
        source = open(web440.__file__, encoding='utf-8').read() + open(standalone_auth.__file__, encoding='utf-8').read()
        for prefixe in ('m56109/historique/', 'm56109/discussions/', 'm56109/apparence/'):
            self.assertEqual(source.count("'%s'" % prefixe), 2, prefixe)
        for nom in ('v56109-historique.js', 'v56109-discussions.js', 'v56109-apparence.js', 'v56109-historique.css', 'v56109-apparence.css'):
            self.assertIn('/static/' + nom, web440.STATIC)


if __name__ == '__main__':
    unittest.main()
