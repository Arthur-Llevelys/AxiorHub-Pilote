"""5.6.93, lot opérations : droits des travaux différés (N01), opération et travail créés ensemble (N02), suivi Open WebUI (N08), état métier après arrêt (N10).

Données fictives (DURAND, MARTIN, DOS-001, example.test), stockage, modèle et serveur Open WebUI simulés : aucun réseau, aucun modèle réel.
Ce qui exige un vrai navigateur (bouton « Relancer », suivi dans la page) ou un vrai Open WebUI (copie importée de l'outil) n'est pas prouvé ici.
"""
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import threading
import types
import unittest
from unittest.mock import patch

import test_v5689_livrables as tl
from agent import api, livrables_export5689 as export, membres5650 as membres, operations5693 as ops, propositions5689 as patches
from agent.common import Stop
from agent.desk import Desk, USER_LIMIT

AVOCAT, ASSISTANT, MID = tl.AVOCAT, tl.ASSISTANT, tl.MID
AUTRE = ('assistante2@example.test', 'assistant')
ADMIN = 'admin@example.test'
ROOT = Path(__file__).resolve().parents[1]


def outil():
    """Outil Open WebUI tel qu'il est distribué ; pydantic simulé s'il n'est pas installé (recette Linux minimale)."""
    if 'pydantic' not in sys.modules:
        try:
            import pydantic  # noqa: F401
        except ImportError:
            fake = types.ModuleType('pydantic')
            fake.BaseModel = type('BaseModel', (), {})
            fake.Field = lambda default=None, **kw: default
            sys.modules['pydantic'] = fake
    spec = importlib.util.spec_from_file_location('outil_openwebui_5693', ROOT / 'integrations' / 'openwebui' / 'axiorhub_tool.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Modele:
    """Modèle simulé : note ce qu'il reçoit et qui agit à ce moment-là."""

    def __init__(self, erreur=None):
        self.appels, self.auteurs, self.erreur = [], [], erreur

    def complete(self, messages, **kw):
        if self.erreur:
            raise Stop(self.erreur)
        self.appels.append(json.loads(messages[-1]['content']))
        self.auteurs.append(membres.current_author())
        index = self.appels[-1]['paragraphes'][0]['index']
        return json.dumps({'propositions': [{'index': index, 'texte': 'Contrat révisé entre DURAND et MARTIN.', 'motif': 'Consigne', 'sources': []}]})


class Base(tl.Base):
    def setUp(self):
        super().setUp()
        self.addCleanup(membres.clear)
        self.state = self.desk.c['state_dir']

    def exporter(self, row, cle, who=AVOCAT, fmt='docx', **extra):
        return self.api('livrables/%s/exporter' % row['document_id'], body={'format': fmt, 'destination': 'dest-exports', 'cle': cle, 'version_id': row['version_id'], **extra}, who=who)

    def modifier(self, row, cle, who=AVOCAT, **extra):
        return self.api('livrables/%s/modifier-ia' % row['document_id'], body={'cle': cle, 'version_id': row['version_id'], 'sha256': row['sha256'], 'instruction': 'Améliore la lisibilité du contrat.', **extra}, who=who)

    def travailler(self, modele=None, maximum=10):
        """Le vrai travailleur (``Desk.work_once``), modèle simulé, contrôle qualité neutre."""
        modele = modele or Modele()
        with patch('agent.relecture5651._model', return_value=modele), patch('agent.relevance370.review_result', return_value=None), patch('agent.contexte5651.for_drafting', return_value={}):
            n = 0
            while n < maximum and self.desk.work_once():
                n += 1
        return modele

    def job(self, job_id):
        return self.desk.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()

    def compte(self, kind):
        return self.desk.db.execute('SELECT COUNT(*) FROM jobs WHERE kind=?', (kind,)).fetchone()[0]

    def exports_ecrits(self):
        return [p for k, p in self.nc.ecritures if '/EXPORTS/' in p]

    def retirer(self, owner=ASSISTANT[0], reserve_a=('autre@example.test',)):
        membres.set_reserve(self.state, MID, list(reserve_a))
        membres.set_rights(self.state, ADMIN, owner, 'assistant', {'exporter': False})

    def pdf(self):
        from agent._vendor.pypdf import PdfWriter
        w = PdfWriter()
        w.add_blank_page(100, 100)
        buf = io.BytesIO()
        w.write(buf)
        return buf.getvalue()


# ------------------------------------------------------------------------------------------------ N01
class N01(Base):
    def test_N01_T1_dossier_et_export_retires_entre_demande_et_demarrage_aucune_lecture_ni_ecriture(self):
        row = self.demande()
        out = self.js(self.exporter(row, 'cle-n01-t1-export', ASSISTANT))
        self.assertTrue(out['job_id'])
        self.retirer()
        lectures = self.nc.lectures
        self.travailler()
        self.assertEqual(self.nc.lectures, lectures)                       # le document n'a pas été lu
        self.assertEqual(self.nc.ecritures, [])
        op = export.etat(self.desk, out['id'])
        self.assertEqual((op['state'], op['error']), ('acces_retire', 'dossier_reserve'))
        self.assertEqual(self.job(out['job_id'])['status'], 'error')
        vue = export._public(op, self.desk)
        self.assertEqual((vue['terminal'], vue['done'], vue['action']), (True, False, 'corriger_les_droits'))

    def test_N01_T1_proposition_ia_revoquee_aucune_requete_au_modele(self):
        row = self.demande()
        out = self.js(self.modifier(row, 'cle-n01-t1-ia', ASSISTANT))
        membres.set_reserve(self.state, MID, ['autre@example.test'])
        lectures = self.nc.lectures
        modele = self.travailler()
        self.assertEqual(modele.appels, [])
        self.assertEqual(self.nc.lectures, lectures)
        self.assertEqual(patches._get(self.desk, out['id'])['state'], 'acces_retire')

    def test_N01_T1_operation_ancienne_sans_acteur_demande_une_nouvelle_autorisation(self):
        row = self.demande()
        out = self.js(self.exporter(row, 'cle-n01-t1-ancienne'))
        self.desk.db.execute("UPDATE prepared_exports5689 SET actor_id='', actor_role='', cabinet_id='' WHERE id=?", (out['id'],))     # ligne écrite par la 5.6.90
        self.desk.db.commit()
        self.travailler()
        op = export.etat(self.desk, out['id'])
        self.assertEqual((op['state'], op['error'], op['actor_id']), ('a_autoriser', 'autorisation_a_renouveler', ''))     # jamais attribuée à « cabinet »
        self.assertEqual(self.nc.ecritures, [])
        again = self.js(self.exporter(row, 'cle-n01-t1-ancienne'))
        self.assertEqual((again['state'], self.compte(export.JOB)), ('a_autoriser', 1))               # la même clé ne vaut pas autorisation
        nouvelle = self.js(self.exporter(row, 'cle-n01-t1-nouvelle'))
        self.travailler()
        self.assertEqual(export.etat(self.desk, nouvelle['id'])['state'], 'exporte')

    def test_N01_T1_mutation_sans_garde_le_defaut_reapparait(self):
        """Garde retirée en mémoire : le travail exporte malgré la révocation. Le test ci-dessus échouerait donc sans elle."""
        row = self.demande()
        out = self.js(self.exporter(row, 'cle-n01-mutation', ASSISTANT))
        self.retirer()
        with patch.object(ops, 'garde', return_value={'actor_id': ASSISTANT[0]}):
            self.travailler()
        self.assertTrue(self.exports_ecrits())
        self.assertEqual(export.etat(self.desk, out['id'])['state'], 'exporte')

    def test_N01_T2_revocation_juste_apres_conversion_aucune_publication(self):
        row = self.demande()
        out = self.js(self.exporter(row, 'cle-n01-t2-conv', ASSISTANT))
        real = export.convertir

        def convertit_puis_revoque(raw, source, cible):
            data = real(raw, source, cible)
            membres.set_rights(self.state, ADMIN, ASSISTANT[0], 'assistant', {'exporter': False})
            return data
        with patch.object(export, 'convertir', side_effect=convertit_puis_revoque):
            self.travailler()
        self.assertEqual(self.nc.ecritures, [])
        op = export.etat(self.desk, out['id'])
        self.assertEqual((op['state'], op['error']), ('acces_retire', 'droit_export_retire'))

    def test_N01_T2_revocation_avant_reprise_d_un_export_deja_ecrit(self):
        row = self.demande()
        real = self.nc.stat
        seen = {'n': 0}

        def coupe(path):
            info = real(path)
            if path.endswith('.pdf'):
                seen['n'] += 1
                if seen['n'] == 1:      # le fichier est écrit, la relecture n'a jamais lieu : coupure
                    raise RuntimeError('coupure')
            return info
        with patch('agent.pieces510.docx_to_pdf', return_value=self.pdf()):
            out = self.js(self.exporter(row, 'cle-n01-t2-reprise', ASSISTANT, fmt='pdf'))
            with patch.object(self.nc, 'stat', side_effect=coupe), self.assertRaises(RuntimeError):
                export.executer(self.desk, {'export': out['id']}, self.nc)
            self.assertEqual(len(self.nc.ecritures), 1)
            membres.set_reserve(self.state, MID, ['autre@example.test'])
            lectures = self.nc.lectures
            with self.assertRaisesRegex(Stop, 'dossier_reserve'):
                export.executer(self.desk, {'export': out['id']}, self.nc)
        self.assertEqual(self.nc.lectures, lectures)                        # la reprise n'a pas relu le fichier écrit
        self.assertEqual(len(self.nc.ecritures), 1)                         # aucun nouvel effet
        op = export.etat(self.desk, out['id'])
        vue = export._public(op, self.desk)
        self.assertEqual((vue['state'], vue['done'], vue['fichier']), ('acces_retire', False, ''))
        self.assertTrue(vue['fichier_partiel'].endswith('.pdf'))           # le fichier écrit avant l'arrêt reste tracé, jamais « Exporté »
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM prepared_representations5689 WHERE role='export' AND verified=1").fetchone())

    def test_N01_T3_droits_conserves_export_et_proposition_aboutissent_une_seule_fois(self):
        row = self.demande()
        e = self.js(self.exporter(row, 'cle-n01-t3-export', ASSISTANT))
        p = self.js(self.modifier(row, 'cle-n01-t3-ia', ASSISTANT))
        modele = self.travailler()
        self.assertEqual(export.etat(self.desk, e['id'])['state'], 'exporte')
        self.assertEqual(patches._get(self.desk, p['id'])['state'], 'proposee')
        self.assertEqual((len(modele.appels), len(self.exports_ecrits())), (1, 1))
        self.assertEqual(modele.auteurs, [ASSISTANT[0]])                    # l'acteur est propagé au contexte du modèle
        self.assertEqual(membres.current_author(), '')                       # et ne survit pas au travail
        self.assertEqual(self.js(self.exporter(row, 'cle-n01-t3-export', ASSISTANT))['id'], e['id'])
        self.assertEqual(self.js(self.modifier(row, 'cle-n01-t3-ia', ASSISTANT))['id'], p['id'])
        self.assertFalse(self.travailler().appels)
        self.assertEqual((self.compte(export.JOB), self.compte(patches.JOB), len(self.exports_ecrits())), (1, 1, 1))

    def test_N01_T3_deux_comptes_ne_reutilisent_pas_leurs_autorisations(self):
        row = self.demande()
        a = self.js(self.exporter(row, 'cle-n01-t3-a-exp', ASSISTANT))
        b = self.js(self.exporter(row, 'cle-n01-t3-b-exp', AUTRE))
        pa = self.js(self.modifier(row, 'cle-n01-t3-a-ia', ASSISTANT))
        pb = self.js(self.modifier(row, 'cle-n01-t3-b-ia', AUTRE))
        self.assertEqual(self.exporter(row, 'cle-n01-t3-a-exp', AUTRE)['status'], '409 Conflict')     # la clé d'un autre compte ne se réutilise pas
        membres.set_reserve(self.state, MID, [ASSISTANT[0]])
        membres.set_rights(self.state, ADMIN, AUTRE[0], 'assistant', {'exporter': False})
        modele = self.travailler()
        self.assertEqual(export.etat(self.desk, a['id'])['state'], 'exporte')
        self.assertEqual(export.etat(self.desk, b['id'])['state'], 'acces_retire')
        self.assertEqual(patches._get(self.desk, pa['id'])['state'], 'proposee')
        self.assertEqual(patches._get(self.desk, pb['id'])['state'], 'acces_retire')
        self.assertEqual((modele.auteurs, len(self.exports_ecrits())), ([ASSISTANT[0]], 1))

    def test_N01_T3_compte_desactive_ou_role_abaisse(self):
        row = self.demande()
        out = self.js(self.exporter(row, 'cle-n01-t3-role', ASSISTANT))
        self.desk.db.execute(membres.SCHEMA)
        self.desk.db.execute("INSERT INTO membres5650(owner,name,role,first_seen,last_seen) VALUES(?,?,?,?,?)", (ASSISTANT[0], 'Assistant fictif', 'assistant', 'x', 'x'))
        self.desk.db.commit()
        with patch.object(membres, 'directory', return_value={}):            # compte connu, retiré de l'annuaire : désactivé
            self.travailler()
        op = export.etat(self.desk, out['id'])
        self.assertEqual((op['state'], op['error']), ('acces_retire', 'compte_desactive'))
        acteur = ops.acteur(self.desk, AVOCAT[0], 'administrateur')         # un rôle noté plus haut n'est jamais repris au-dessus de l'annuaire
        self.assertEqual(acteur['role'], 'administrateur')
        with patch.object(membres, 'directory', return_value={AVOCAT[0]: {'owner': AVOCAT[0], 'role': 'assistant'}}):
            self.assertEqual(ops.acteur(self.desk, AVOCAT[0], 'avocat')['role'], 'assistant')


# ------------------------------------------------------------------------------------------------ N02
class Coupure:
    """Connexion dont le processus « meurt » à une étape donnée de la transaction : ni annulation propre, ni validation."""

    def __init__(self, real, cible):
        self._real, self._cible, self._arme, self.atteinte = real, cible, False, False

    def execute(self, sql, *args):
        out = self._real.execute(sql, *args)
        if 'BEGIN IMMEDIATE' in sql:
            self._arme = True
        elif self._arme and not self.atteinte and self._cible in sql:
            self.atteinte = True
            raise RuntimeError('coupure')
        return out

    def commit(self):
        if self._arme and not self.atteinte and self._cible == 'COMMIT':
            self.atteinte = True
            raise RuntimeError('coupure')
        return self._real.commit()

    def rollback(self):
        return None

    def __getattr__(self, name):
        return getattr(self._real, name)


class N02(Base):
    def remplir(self):
        now = self.desk.now()
        self.desk.db.executemany("INSERT INTO jobs(kind,args,status,created,priority,origin) VALUES('health',?,'pending',?,1,'user')", [(json.dumps({'numero': i}), now) for i in range(USER_LIMIT)])
        self.desk.db.commit()

    def liberer_une_place(self):
        self.desk.db.execute("DELETE FROM jobs WHERE id=(SELECT MIN(id) FROM jobs WHERE kind='health')")
        self.desk.db.commit()

    def lignes(self, table, cle):
        return self.desk.db.execute('SELECT COUNT(*) FROM %s WHERE idempotency_key=?' % table, (cle,)).fetchone()[0]

    def test_N02_T1_file_saturee_refus_puis_meme_cle_un_seul_travail_executable(self):
        row = self.demande()
        self.remplir()
        refus = self.exporter(row, 'cle-n02-t1-export')
        refus_ia = self.modifier(row, 'cle-n02-t1-ia')
        self.assertEqual((refus['status'], self.js(refus)['error']), ('503 Service Unavailable', 'file_attente_pleine'))
        self.assertEqual(refus_ia['status'], '503 Service Unavailable')
        self.assertEqual((self.lignes('prepared_exports5689', 'cle-n02-t1-export'), self.lignes('document_patches5689', 'cle-n02-t1-ia')), (0, 0))   # rien de validé seul
        self.liberer_une_place()
        out = self.js(self.exporter(row, 'cle-n02-t1-export'))
        self.liberer_une_place()
        ia = self.js(self.modifier(row, 'cle-n02-t1-ia'))
        for kind, item, arg in ((export.JOB, out, 'export'), (patches.JOB, ia, 'proposition')):
            jobs = self.desk.db.execute('SELECT * FROM jobs WHERE kind=?', (kind,)).fetchall()
            self.assertEqual(len(jobs), 1)
            self.assertEqual((jobs[0]['id'], jobs[0]['status'], json.loads(jobs[0]['args'])[arg]), (item['job_id'], 'pending', item['id']))
        self.assertEqual(self.js(self.exporter(row, 'cle-n02-t1-export'))['job_id'], out['job_id'])
        self.assertEqual(self.js(self.modifier(row, 'cle-n02-t1-ia'))['job_id'], ia['job_id'])
        self.assertEqual((self.compte(export.JOB), self.compte(patches.JOB)), (1, 1))

    def test_N02_T1_mutation_creation_non_atomique_laisse_une_orpheline(self):
        """Ancienne séquence (validation avant la mise en file) rejouée en mémoire : l'opération survit seule. Le test ci-dessus le détecterait."""
        row = self.demande()
        self.remplir()

        def ancienne(desk, genre, colonnes, args_travail, origin=None):
            g = ops.GENRES[genre]
            desk.db.execute('INSERT INTO %s(%s) VALUES(%s)' % (g['table'], ','.join(colonnes), ','.join('?' * len(colonnes))), tuple(colonnes.values()))
            desk.db.commit()
            desk.enqueue(g['job'], {g['argument']: colonnes['id'], **args_travail})
        with patch.object(ops, 'creer', side_effect=ancienne):
            self.exporter(row, 'cle-n02-mutation')
        self.assertEqual(self.lignes('prepared_exports5689', 'cle-n02-mutation'), 1)
        self.assertEqual(self.compte(export.JOB), 0)

    def test_N02_T1_ligne_orpheline_ancienne_reparee_par_la_meme_cle_ou_la_maintenance(self):
        row = self.demande()
        a = self.js(self.exporter(row, 'cle-n02-orph-a'))
        b = self.js(self.exporter(row, 'cle-n02-orph-b'))
        self.desk.db.execute('DELETE FROM jobs WHERE kind=?', (export.JOB,))
        self.desk.db.execute('UPDATE prepared_exports5689 SET job_id=NULL WHERE id=?', (a['id'],))
        self.desk.db.execute('UPDATE prepared_exports5689 SET job_id=987654 WHERE id=?', (b['id'],))     # pointe vers un travail disparu
        self.desk.db.commit()
        self.assertEqual(ops.diagnostic(self.desk)['export']['sans_travail'], 2)
        repris = self.js(self.exporter(row, 'cle-n02-orph-a'))
        self.assertEqual(repris['id'], a['id'])
        self.assertEqual(self.job(repris['job_id'])['status'], 'pending')
        self.assertEqual(ops.maintenance(self.desk)['export']['orphelines_reparees'], 1)
        self.assertEqual(ops.maintenance(self.desk)['export']['orphelines_reparees'], 0)                  # au plus un travail
        self.assertEqual(self.compte(export.JOB), 2)
        self.assertEqual(ops.diagnostic(self.desk)['export']['sans_travail'], 0)

    def test_N02_T1_diagnostic_de_la_file_montre_les_demandes_sans_travail(self):
        from agent import blocage5660
        row = self.demande()
        out = self.js(self.exporter(row, 'cle-n02-diag'))
        codes = lambda: {f['code'] for f in blocage5660.diagnose(self.desk.db, self.state)}
        self.assertNotIn('operation_sans_travail', codes())
        self.desk.db.execute('DELETE FROM jobs WHERE id=?', (out['job_id'],))
        self.desk.db.commit()
        self.assertIn('operation_sans_travail', codes())
        ops.maintenance(self.desk)
        self.assertNotIn('operation_sans_travail', codes())

    def test_N02_T2_coupure_a_chaque_ecriture_avant_commit_aucune_orpheline_ni_doublon(self):
        row = self.demande()
        etapes = {'export': ('INSERT INTO prepared_exports5689', 'INSERT INTO jobs', 'UPDATE prepared_exports5689 SET job_id', 'COMMIT'),
                  'proposition': ('INSERT INTO document_patches5689', 'INSERT INTO jobs', 'UPDATE document_patches5689 SET job_id', 'COMMIT')}
        for genre, points in etapes.items():
            for n, point in enumerate(points):
                cle = 'cle-n02-t2-%s-%d' % (genre, n)
                d = Desk(self.f.c)
                d.db = Coupure(d.db, point)
                with self.assertRaises(RuntimeError):
                    if genre == 'export':
                        export.demander(d, row, 'docx', 'dest-exports', cle, AVOCAT[0], role='avocat')
                    else:
                        patches.demander_ia(d, row, {'cle': cle, 'version_id': row['version_id'], 'sha256': row['sha256'], 'instruction': 'Corrige le style.'}, AVOCAT[0], 'avocat')
                self.assertTrue(d.db.atteinte, point)
                d.db._real.close()                                       # le processus s'arrête : la transaction ouverte disparaît
                redemarre = Desk(self.f.c)
                table, kind = ops.GENRES[genre]['table'], ops.GENRES[genre]['job']
                self.assertEqual(redemarre.db.execute('SELECT COUNT(*) FROM %s WHERE idempotency_key=?' % table, (cle,)).fetchone()[0], 0, point)
                avant = redemarre.db.execute('SELECT COUNT(*) FROM jobs WHERE kind=?', (kind,)).fetchone()[0]
                redemarre.db.close()
                out = self.js(self.exporter(row, cle) if genre == 'export' else self.api('livrables/%s/modifier-ia' % row['document_id'], body={'cle': cle, 'version_id': row['version_id'], 'sha256': row['sha256'], 'instruction': 'Corrige le style.'}))
                self.assertTrue(out['job_id'], point)
                self.assertEqual(self.compte(kind), avant + 1, point)
        self.assertEqual(ops.diagnostic(self.desk), {'export': {'sans_travail': 0, 'actives_travail_termine': 0, 'sans_acteur': 0},
                                                     'proposition': {'sans_travail': 0, 'actives_travail_termine': 0, 'sans_acteur': 0}})

    def test_N02_T3_deux_requetes_concurrentes_meme_cle_meme_identifiant(self):
        row = self.demande()
        for genre in ('export', 'proposition'):
            depart, ids, erreurs = threading.Barrier(2), [], []

            def requete():
                d = Desk(self.f.c)
                try:
                    depart.wait(5)
                    if genre == 'export':
                        ids.append(export.demander(d, row, 'docx', 'dest-exports', 'cle-n02-t3-concurrente', AVOCAT[0], role='avocat')['id'])
                    else:
                        ids.append(patches.demander_ia(d, row, {'cle': 'cle-n02-t3-concurrente-ia', 'version_id': row['version_id'], 'sha256': row['sha256'], 'instruction': 'Corrige le style.'}, AVOCAT[0], 'avocat')['id'])
                except Exception as ex:      # noqa: BLE001
                    erreurs.append(ex)
                finally:
                    d.db.close()
            fils = [threading.Thread(target=requete) for _ in range(2)]
            for t in fils:
                t.start()
            for t in fils:
                t.join(30)
            self.assertEqual(erreurs, [])
            self.assertEqual(len(ids), 2)
            self.assertEqual(len(set(ids)), 1)
            self.assertEqual(self.compte(ops.GENRES[genre]['job']), 1)
        self.assertEqual(self.exporter(row, 'cle-n02-t3-concurrente', fmt='pdf')['status'], '409 Conflict')     # autres paramètres, même clé
        self.assertEqual(self.modifier(row, 'cle-n02-t3-concurrente-ia', instruction='Autre consigne.')['status'], '409 Conflict')


# ------------------------------------------------------------------------------------------------ N08
class Serveur:
    """Serveur AxiorHub simulé : une réponse par lecture, horloge simulée, aucun réseau."""

    def __init__(self, reponses, apres_attente=None):
        self.reponses, self.appels, self.horloge, self.apres_attente = list(reponses), [], [0], apres_attente

    def call(self, path, method='GET', payload=None):
        self.appels.append((path, method))
        reponse = self.reponses.pop(0) if len(self.reponses) > 1 else self.reponses[0]
        return reponse(path, method, payload) if callable(reponse) else dict(reponse)

    def attendre(self, seconds):
        self.horloge[0] += seconds
        if self.apres_attente:
            self.apres_attente(len(self.appels))


class N08(Base):
    def suivre(self, serveur, job_id=17, attente=240):
        module = outil()
        t = module.Tools()
        t.valves.JOB_WAIT_SECONDS = attente
        t._call = serveur.call
        with patch.object(module.time, 'monotonic', side_effect=lambda: serveur.horloge[0]), patch.object(module.time, 'sleep', side_effect=serveur.attendre):
            return t._wait(job_id), module

    def reel(self):
        return lambda path, method='GET', payload=None: api.dispatch(self.desk, path, method, payload)

    def inserer(self, status, **cols):
        cols = {'kind': 'assistant_answer', 'args': '{}', 'status': status, 'created': self.desk.now(), 'priority': 10, 'origin': 'user', **cols}
        jid = self.desk.db.execute('INSERT INTO jobs(%s) VALUES(%s)' % (','.join(cols), ','.join('?' * len(cols))), tuple(cols.values())).lastrowid
        self.desk.db.commit()
        return jid

    def test_N08_T1_pause_horaire_connue_rendue_en_une_lecture(self):
        serveur = Serveur([{'id': 17, 'status': 'suspended', 'stop_reason': 'pause_horaire', 'result': {'message': 'Pause programmée jusqu’à 07 h.'}}])
        out, module = self.suivre(serveur)
        self.assertEqual((len(serveur.appels), serveur.horloge[0]), (1, 0))
        self.assertEqual((out['status'], out['etat'], out['motif'], out['job_id']), ('suspended', 'suspendu', 'pause_horaire', 17))
        self.assertIn('07 h', out['message'])
        self.assertEqual(out['outil_version'], module.OUTIL_VERSION)
        reprise = Serveur([{'id': 17, 'status': 'suspended', 'suivi': {'etat': 'suspendu', 'libelle': 'Suspendu : pause programmée', 'terminal': False, 'motif': 'pause_horaire', 'reprise': '2026-11-04T06:00:00+00:00', 'message': ''}}])
        self.assertEqual(self.suivre(reprise)[0]['reprise'], '2026-11-04T06:00:00+00:00')
        jid = self.inserer('suspended', stop_reason='pause_horaire', result=json.dumps({'message': 'Suspendu : pause programmée de l’IA.'}))
        vrai = Serveur([self.reel()])
        out = self.suivre(vrai, jid)[0]
        self.assertEqual((len(vrai.appels), out['etat'], out['motif'], out['terminal']), (1, 'suspendu', 'pause_horaire', False))

    def test_N08_T2_execution_lente_suivie_sur_le_meme_travail_puis_annulation_confirmee(self):
        lente = Serveur([{'id': 17, 'status': 'pending'}, {'id': 17, 'status': 'running'}, {'id': 17, 'status': 'running'}, {'id': 17, 'status': 'done', 'result': '{"ok": true}'}])
        out = self.suivre(lente)[0]
        self.assertEqual((out['status'], out['etat'], out['result']), ('done', 'termine', {'ok': True}))
        self.assertEqual(set(lente.appels), {('/jobs/17', 'GET')})            # même travail, aucune nouvelle demande
        jid = self.inserer('running', started=self.desk.now())

        def worker(n):
            if n == 1:
                self.desk.cancel_job(jid)                                     # arrêt demandé pendant l'exécution
            elif n == 2:
                self.desk.db.execute("UPDATE jobs SET status='cancelled', finished=? WHERE id=? AND status='cancel_requested'", (self.desk.now(), jid))
                self.desk.db.commit()                                         # le travailleur confirme
        vrai = Serveur([self.reel()], worker)
        out = self.suivre(vrai, jid)[0]
        self.assertEqual((out['status'], out['etat'], out['terminal'], len(vrai.appels)), ('cancelled', 'annule', True, 3))

    def test_N08_T3_erreur_reseau_ou_budget_jamais_presentes_comme_un_calcul_en_cours(self):
        panne = Serveur([{'id': 17, 'status': 'running'}, {'error': 'API AxiorHub momentanément indisponible'}])
        out = self.suivre(panne)[0]
        self.assertEqual((out['status'], out['etat'], out['job_id']), ('inconnu', 'inconnu', 17))
        self.assertNotIn('continue', out['message'])
        self.assertIn('En cours', out['message'])                             # dernier état constaté, présenté comme tel
        jid = self.inserer('pending', blocked_by='budget_mois_atteint', not_before='2026-12-01T00:00:05+00:00')
        vrai = Serveur([self.reel()])
        out = self.suivre(vrai, jid)[0]
        self.assertEqual((len(vrai.appels), out['etat'], out['motif'], out['reprise']), (1, 'attente_budget', 'budget_mois_atteint', '2026-12-01T00:00:05+00:00'))
        longue = Serveur([{'id': 17, 'status': 'running'}])
        out = self.suivre(longue, attente=10)[0]
        self.assertEqual((out['status'], out['etat']), ('running', 'en_cours'))
        self.assertNotIn('continue', out['message'])
        self.assertTrue(all(m == 'GET' for _, m in longue.appels))

    def test_N08_contrat_de_statut_partage_et_version_affichee(self):
        module = outil()
        self.assertEqual(module.CONTRAT_STATUT, ops.STATUTS)
        self.assertEqual(module.ATTENTES, ops.ATTENTES)
        self.assertEqual(module.SUSPENSIONS, ops.SUSPENSIONS)
        t = module.Tools()
        t._call = lambda path, method='GET', payload=None: {'version': '9.9.99'}
        out = t.version_de_l_outil()
        self.assertEqual((out['outil_version'], out['serveur_version']), (module.OUTIL_VERSION, '9.9.99'))
        self.assertIn('Remplacez', out['action'].replace('remplacez', 'Remplacez'))
        self.assertNotEqual(module.OUTIL_VERSION, 'inconnue')


# ------------------------------------------------------------------------------------------------ N10
class N10(Base):
    def test_N10_T1_annulation_avant_demarrage_visible_partout_zero_effet(self):
        row = self.demande()
        out = self.js(self.exporter(row, 'cle-n10-t1-export'))
        self.desk.cancel_job(out['job_id'])
        livrable = self.js(self.api('livrables/exports/' + out['id']))
        self.assertEqual((livrable['state'], livrable['terminal'], livrable['suivi']['etat'], livrable['action']), ('annule', True, 'annule', 'relancer'))
        self.assertEqual(self.job(out['job_id'])['status'], 'cancelled')                                  # la file
        self.assertEqual(api.dispatch(self.desk, '/jobs/%d' % out['job_id'], 'GET')['suivi']['etat'], 'annule')
        serveur = Serveur([lambda path, method, payload: api.dispatch(self.desk, path, method, payload)])
        module = outil()
        t = module.Tools()
        t._call = serveur.call
        self.assertEqual((t._wait(out['job_id'])['status'], len(serveur.appels)), ('cancelled', 1))       # Open WebUI
        again = self.js(self.exporter(row, 'cle-n10-t1-export'))
        self.assertEqual((again['id'], again['state']), (out['id'], 'annule'))
        self.assertTrue(again['message'].startswith('Demande déjà annulée'))
        self.assertFalse(self.desk.work_once())
        self.assertEqual((self.nc.ecritures, self.compte(export.JOB), self.job(out['job_id'])['status']), ([], 1, 'cancelled'))
        relance = self.js(self.exporter(row, 'cle-n10-t1-relance', relance_de=out['id']))
        self.assertEqual((relance['relance_de'], relance['state']), (out['id'], 'demande'))
        self.assertNotEqual(relance['id'], out['id'])
        self.assertEqual(self.exporter(row, 'cle-n10-t1-relance-2', relance_de=relance['id'])['status'], '409 Conflict')   # la précédente est encore active
        p = self.js(self.modifier(row, 'cle-n10-t1-ia'))
        self.desk.cancel_job(p['job_id'])
        stored = self.js(self.api('documents/%s/propositions' % row['document_id']))['propositions'][0]
        self.assertEqual((stored['state'], stored['terminal']), ('annule', True))
        self.assertTrue(self.js(self.modifier(row, 'cle-n10-t1-ia'))['message'].startswith('Demande déjà annulée'))

    def test_N10_T2_echec_du_modele_http_400_motif_lisible_et_relance_explicite(self):
        row = self.demande()
        out = self.js(self.modifier(row, 'cle-n10-t2-ia'))
        self.travailler(Modele(erreur='http_400'))
        self.assertEqual(self.job(out['job_id'])['status'], 'error')
        stored = self.js(self.api('documents/%s/propositions' % row['document_id']))['propositions'][0]
        self.assertEqual((stored['state'], stored['error'], stored['action'], stored['terminal']), ('echec', 'http_400', 'relancer', True))
        self.assertTrue(stored['motif'])
        self.assertNotEqual(stored['motif'], 'http_400')
        again = self.js(self.modifier(row, 'cle-n10-t2-ia'))
        self.assertEqual((again['state'], self.compte(patches.JOB)), ('echec', 1))            # une erreur reste une erreur jusqu'à une relance explicite
        relance = self.js(self.modifier(row, 'cle-n10-t2-relance', relance_de=out['id']))
        self.assertEqual((relance['state'], relance['relance_de']), ('demandee', out['id']))
        self.assertEqual(patches._get(self.desk, out['id'])['state'], 'echec')
        self.travailler()
        self.assertEqual(patches._get(self.desk, relance['id'])['state'], 'proposee')

    def test_N10_T3_annulation_pendant_l_ecriture_resultat_partiel_sans_resurrection(self):
        row = self.demande()
        out = self.js(self.exporter(row, 'cle-n10-t3-ecriture'))
        real = self.nc.put_file

        def ecrit_puis_annule(path, data, kind=''):
            done = real(path, data, kind)
            self.desk.cancel_job(out['job_id'])           # l'avocat arrête pendant l'écriture
            return done
        with patch.object(self.nc, 'put_file', side_effect=ecrit_puis_annule):
            self.travailler()
        self.assertEqual(self.job(out['job_id'])['status'], 'cancelled')
        vue = self.js(self.api('livrables/exports/' + out['id']))
        self.assertEqual((vue['state'], vue['done'], vue['terminal']), ('annule', False, True))
        self.assertTrue(vue['fichier_partiel_ecrit'])
        self.assertTrue(vue['fichier_partiel'].endswith('.docx'))
        self.assertFalse(self.desk.work_once())
        with self.assertRaisesRegex(Stop, 'operation_annulee'):
            export.executer(self.desk, {'export': out['id']}, self.nc)
        ops.maintenance(self.desk)
        self.assertEqual((self.job(out['job_id'])['status'], len(self.exports_ecrits())), ('cancelled', 1))

    def test_N10_T3_interruption_apres_finalisation_aucune_seconde_ecriture(self):
        from agent.queue567 import recover
        row = self.demande()
        out = self.js(self.exporter(row, 'cle-n10-t3-final'))
        export.executer(self.desk, {'export': out['id']}, self.nc)          # fin enregistrée, puis le serveur s'arrête avant de clore le travail
        self.desk.db.execute("UPDATE jobs SET status='running', worker='w-n10' WHERE id=?", (out['job_id'],))
        self.desk.db.commit()
        recover(self.desk, 'w-n10')
        self.travailler()
        self.assertEqual((export.etat(self.desk, out['id'])['state'], len(self.exports_ecrits())), ('exporte', 1))
        # ancienne désynchronisation : travail annulé, opération restée « demandée » ; la lecture la répare, rien ne la remet en file
        stale = self.js(self.exporter(row, 'cle-n10-t3-ancien'))
        self.desk.db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (stale['job_id'],))
        self.desk.db.commit()
        self.assertEqual(ops.diagnostic(self.desk)['export']['actives_travail_termine'], 1)
        self.assertEqual(self.js(self.api('livrables/exports/' + stale['id']))['state'], 'annule')
        ops.maintenance(self.desk)
        self.assertFalse(self.desk.work_once())
        self.assertEqual(self.job(stale['job_id'])['status'], 'cancelled')

    def test_N10_mutation_sans_projection_l_export_reste_demande(self):
        """Projection retirée en mémoire : le défaut N10 réapparaît, le test T1 le détecterait."""
        row = self.demande()
        out = self.js(self.exporter(row, 'cle-n10-mutation'))
        with patch.object(ops, '_terminer', return_value=False):
            self.desk.cancel_job(out['job_id'])
            self.assertEqual(self.js(self.api('livrables/exports/' + out['id']))['state'], 'demande')


# ------------------------------------------------------------------------------------------------ migration
class Migration(Base):
    def test_N02_N10_migration_diagnostic_puis_reparation_repetable(self):
        row = self.demande()
        a = self.js(self.exporter(row, 'cle-migr-orpheline'))
        b = self.js(self.exporter(row, 'cle-migr-pointeur'))
        c = self.js(self.exporter(row, 'cle-migr-annulee'))
        e = self.js(self.exporter(row, 'cle-migr-anonyme'))
        f = self.js(self.exporter(row, 'cle-migr-terminale'))
        d = self.js(self.modifier(row, 'cle-migr-ia-erreur'))
        db = self.desk.db
        db.execute('DELETE FROM jobs WHERE id IN (?,?,?)', (a['job_id'], b['job_id'], e['job_id']))
        db.execute('UPDATE prepared_exports5689 SET job_id=NULL WHERE id IN (?,?)', (a['id'], e['id']))
        db.execute("UPDATE prepared_exports5689 SET actor_id='', actor_role='', cabinet_id='' WHERE id=?", (e['id'],))
        db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (c['job_id'],))
        db.execute("UPDATE jobs SET status='error', result=? WHERE id=?", (json.dumps({'erreur': 'http_400'}), d['job_id']))
        db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (f['job_id'],))
        db.execute("UPDATE prepared_exports5689 SET state='annule' WHERE id=?", (f['id'],))
        db.execute('DELETE FROM jobs WHERE id=?', (f['job_id'],))                            # annulation enregistrée, travail purgé : reste terminale
        db.commit()
        avant = ops.migrer(self.desk)
        self.assertFalse(avant['applique'])
        self.assertEqual(avant['diagnostic']['export'], {'sans_travail': 3, 'actives_travail_termine': 1, 'sans_acteur': 1})
        self.assertEqual(avant['diagnostic']['proposition'], {'sans_travail': 0, 'actives_travail_termine': 1, 'sans_acteur': 0})
        self.assertEqual(db.execute("SELECT state FROM prepared_exports5689 WHERE id=?", (c['id'],)).fetchone()[0], 'demande')   # diagnostic seul : rien changé
        fait = ops.migrer(self.desk, appliquer=True)
        etats = {k: db.execute('SELECT state FROM prepared_exports5689 WHERE id=?', (v['id'],)).fetchone()[0] for k, v in (('a', a), ('b', b), ('c', c), ('e', e), ('f', f))}
        self.assertEqual(etats, {'a': 'demande', 'b': 'demande', 'c': 'annule', 'e': 'a_autoriser', 'f': 'annule'})
        self.assertEqual(patches._get(self.desk, d['id'])['state'], 'echec')
        self.assertEqual(fait['apres']['export'], {'sans_travail': 0, 'actives_travail_termine': 0, 'sans_acteur': 0})
        self.assertEqual(self.job(c['job_id'])['status'], 'cancelled')                      # jamais remis en file
        jobs = self.compte(export.JOB)
        ops.migrer(self.desk, appliquer=True)
        self.assertEqual(self.compte(export.JOB), jobs)                                     # répétable : aucun second remplaçant
        self.assertFalse(db.execute("SELECT 1 FROM prepared_exports5689 o JOIN jobs j ON j.id=o.job_id WHERE o.id IN (?,?)", (e['id'], f['id'])).fetchone())    # ni l'anonyme ni l'annulée ne reçoivent de travail

    def test_N02_colonnes_ajoutees_sans_perte_d_historique(self):
        """Une base 5.6.89 (tables sans les nouvelles colonnes) migre en place, plusieurs fois, sans perdre ses lignes."""
        self.desk.db.execute('DROP TABLE IF EXISTS prepared_exports5689')
        self.desk.db.execute(export.SCHEMA)
        self.desk.db.execute("INSERT INTO prepared_exports5689(id,idempotency_key,parameters,item_id,document_id,version_id,format,destination,requested_by,created,updated) VALUES('x','cle-ancienne-1','[]','i','d','v','docx','dest-exports','avocate@example.test','2026-11-01','2026-11-01')")
        self.desk.db.commit()
        for _ in range(2):
            d = Desk(self.f.c)
            export.schema(d)
            d.db.close()
        cols = {r[1] for r in self.desk.db.execute('PRAGMA table_info(prepared_exports5689)')}
        self.assertTrue({'actor_id', 'actor_role', 'cabinet_id', 'relance_de'} <= cols)
        self.assertEqual(self.desk.db.execute("SELECT requested_by, actor_id FROM prepared_exports5689 WHERE id='x'").fetchone()[:], ('avocate@example.test', ''))


if __name__ == '__main__':
    unittest.main()
