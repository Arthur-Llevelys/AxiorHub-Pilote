"""5.6.109, lot Q (G23) : « Fonds juridique local » — lots publics fournis par le cabinet dans un dossier local, versionnés, publiés
atomiquement, recherchés sans réseau, séparés des pièces privées.

Services réels (``fonds_juridique56109``, ``index.DocumentIndex``, ``recherche5695``, ``export56108``, Paramètres) sur un état temporaire ;
FAUX lots (textes fictifs, identifiants ART-FICTIF, source « source-fictive », example.test). Aucun réseau (sockets bloquées), aucun
téléchargement, aucun modèle. La fraîcheur et les droits d'un vrai lot ne peuvent pas être qualifiés ici.
"""
import builtins
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import unittest
from unittest.mock import patch

from agent import export5688 as X, fonds_juridique56109 as F, recherche5695 as R
from agent.common import Stop
from agent.desk import Desk
from agent.index import DocumentIndex
import test_agent as fx

ROOT = Path(__file__).resolve().parents[1]
PREFIX = '/agent-courriel'
ADMIN = ('cabinet', 'administrateur')
ASSISTANT = ('assistant@example.test', 'assistant')


def _refuse_reseau(*a, **k):
    raise AssertionError('aucun appel réseau réel dans ces essais')


def doc(official_id='ART-FICTIF-1', version_id='v1', texte='Le délai de préavis applicable au bail fictif est de un mois.', **kw):
    d = {'official_id': official_id, 'version_id': version_id, 'kind': 'article', 'jurisdiction': 'FR', 'language': 'fr',
         'valid_from': '2020-01-01', 'valid_to_exclusive': '2025-01-01', 'intervalle_verifie': True, 'text': texte,
         'sha256': hashlib.sha256(texte.encode()).hexdigest(), 'title': 'Article fictif 1', 'source_url': 'https://www.legifrance.gouv.fr/fictif/1'}
    d.update(kw)
    return d


V1 = doc()
V2 = doc(version_id='v2', texte='Le délai de préavis applicable au bail fictif est de deux mois.', valid_from='2025-01-01', valid_to_exclusive='')
DEC = doc('DEC-FICTIVE-1', 'v1', 'Décision fictive : le préavis notifié tardivement reste valable pour ZORVAL.', kind='decision', valid_from='',
          valid_to_exclusive='', intervalle_verifie=False, decision_date='2023-05-02', publication_date='2023-06-01', title='Décision fictive')


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fx.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        for cible, nom in ((socket.socket, 'connect'), (socket, 'create_connection'), (socket, 'getaddrinfo')):
            p = patch.object(cible, nom, _refuse_reseau)
            p.start()
            self.addCleanup(p.stop)
        self.d = Desk(self.f.c)
        self.addCleanup(self.d.db.close)
        self.tmp = Path(tempfile.mkdtemp(prefix='lotq-fonds-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.auth = {'origin': 'https://cabinet.example.test', 'csrf': 'test-csrf', 'prefix': PREFIX}
        self.n = 0

    def lot(self, nom, docs, **manifeste):
        dossier = self.tmp / nom
        dossier.mkdir()
        m = {'snapshot_id': nom, 'source': 'source-fictive', 'pays': 'FR', 'langue': 'fr', 'rights_ref': 'licence-fictive', 'licence': 'fictive',
             'licence_verifiee': True, 'coverage': 'selection_limitee', 'expected_count': len(docs), 'complet': True, 'corpus_au': '2026-10-01',
             'fonds': 'fr-baux', 'domaines_officiels': ['www.legifrance.gouv.fr']}
        m.update(manifeste)
        (dossier / F.MANIFESTE).write_text(json.dumps(m), encoding='utf-8')
        (dossier / F.DOCUMENTS).write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in docs), encoding='utf-8')
        return str(dossier)

    def cle(self):
        self.n += 1
        return 'cle-g23-essai-%04d' % self.n

    def publier(self, chemin, pas=200, checkpoint=lambda d: None):
        out = F.importer(self.d, ADMIN, chemin, self.cle(), pas=pas)
        for _ in range(50):
            if out['etat'] not in ('preparation', 'indexation'):
                return out
            out = F.avancer(self.d, ADMIN, out['id'], pas=pas, checkpoint=checkpoint)
        return out

    def actif(self, fonds='fr-baux'):
        row = self.d.db.execute('SELECT lot_actif FROM fonds56109_actifs WHERE fonds=?', (fonds,)).fetchone()
        return row['lot_actif'] if row else None

    def api(self, route, data=None, acteur=ADMIN, methode='POST', args=None):
        raw = json.dumps(data or {}).encode()
        env = {'REQUEST_METHOD': methode, 'HTTP_ORIGIN': self.auth['origin'], 'HTTP_X_CSRF_TOKEN': 'test-csrf', 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw), 'HTTP_X_AXIORHUB_ROLE': acteur[1], 'HTTP_X_AXIORHUB_USER': acteur[0]}
        out = F.api(env, self.d, self.auth, PREFIX, F.PREFIXE + route, args or {}, methode)
        out['json'] = json.loads(out['body'])
        return out


class Publication(Base):
    def test_G23_import_local_publie_et_recherche_sans_reseau(self):
        out = self.publier(self.lot('lot-a', [V1, V2, DEC]))
        self.assertEqual(out['etat'], 'actif')
        resultats, etat = F.rechercher(self.d, 'délai de préavis bail')
        self.assertTrue(resultats)
        self.assertEqual(etat['message'], 'Corpus arrêté au 2026-10-01.')
        self.assertFalse(etat['a_jour'])                                  # jamais « droit à jour » sans preuve
        self.assertFalse(etat['reseau'])
        self.assertEqual(F.etat(self.d)['fonds'][0]['libelle'], 'Partiellement couvert')

    def test_G23_reprise_par_pas_bornes_sans_double_import(self):
        out = F.importer(self.d, ADMIN, self.lot('lot-a', [V1, V2, DEC]), self.cle(), pas=1)
        self.assertEqual((out['etat'], out['importes']), ('preparation', 1))
        while out['etat'] in ('preparation', 'indexation'):
            out = F.avancer(self.d, ADMIN, out['id'], pas=1, checkpoint=lambda d: None)
        self.assertEqual(out['etat'], 'actif')
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM fonds56109_documents WHERE lot=?', (out['id'],)).fetchone()[0], 3)

    def test_G23_pause_laisse_le_curseur_et_l_actif(self):
        a = self.publier(self.lot('lot-a', [V1]))
        out = F.importer(self.d, ADMIN, self.lot('lot-b', [V1, V2, DEC], snapshot_id='lot-b'), self.cle(), pas=1)
        curseur = out['importes']

        def pause(_d):
            raise Stop('operation_annulee')
        with self.assertRaisesRegex(Stop, 'operation_en_pause'):
            F.avancer(self.d, ADMIN, out['id'], pas=1, checkpoint=pause)
        lot = F._lot(self.d, out['id'])
        self.assertEqual((lot['etat'], lot['importes']), ('preparation', curseur))     # ni refusé, ni avancé
        self.assertEqual(self.actif(), a['id'])
        while out['etat'] in ('preparation', 'indexation'):
            out = F.avancer(self.d, ADMIN, out['id'], pas=2, checkpoint=lambda d: None)
        self.assertEqual(self.actif(), out['id'])

    def test_G23_suspendre_puis_reprendre(self):
        out = F.importer(self.d, ADMIN, self.lot('lot-a', [V1, V2, DEC]), self.cle(), pas=1)
        s = F.suspendre(self.d, ADMIN, out['id'])
        self.assertEqual(s['etat'], 'suspendu')
        with self.assertRaisesRegex(Stop, 'lot_suspendu'):
            F.avancer(self.d, ADMIN, out['id'])
        r = F.reprendre(self.d, ADMIN, out['id'])
        while r['etat'] in ('preparation', 'indexation'):
            r = F.avancer(self.d, ADMIN, r['id'], checkpoint=lambda d: None)
        self.assertEqual(r['etat'], 'actif')

    def test_G23_garde_lot_partiel_jamais_promu_l_actif_reste(self):
        a = self.publier(self.lot('lot-a', [V1, DEC]))
        b = self.publier(self.lot('lot-b', [V2], snapshot_id='lot-b', expected_count=2))
        self.assertEqual(b['etat'], 'incomplet')
        self.assertEqual(self.actif(), a['id'])
        c = self.publier(self.lot('lot-c', [V2], snapshot_id='lot-c', complet=False))
        self.assertEqual(c['etat'], 'incomplet')
        self.assertEqual(self.actif(), a['id'])
        resultats, _ = F.rechercher(self.d, 'décision préavis notifié tardivement')
        self.assertIn('DEC-FICTIVE-1', [r['official_id'] for r in resultats])    # l'absence dans un lot partiel ne supprime rien
        # mutation en mémoire : sans ``verifier_lot``, le lot partiel serait publié
        with patch('agent.fonds_juridique56109.verifier_lot', return_value={'etat': 'lot_verifie_avant_indexation'}):
            d = self.publier(self.lot('lot-d', [V2], snapshot_id='lot-d', expected_count=2))
        self.assertEqual(d['etat'], 'actif')

    def test_G23_lot_altere_duplique_ou_modifie_refuse(self):
        a = self.publier(self.lot('lot-a', [V1]))
        faux = dict(V2, text='texte modifié après empreinte')
        self.assertEqual(self.publier(self.lot('lot-b', [faux], snapshot_id='lot-b'))['motif'], 'empreinte_lot_incorrecte')
        self.assertEqual(self.publier(self.lot('lot-c', [V2, V2], snapshot_id='lot-c'))['motif'], 'version_dupliquee_dans_lot')
        chemin = self.lot('lot-e', [V2, DEC], snapshot_id='lot-e')
        out = F.importer(self.d, ADMIN, chemin, self.cle(), pas=1)
        with open(Path(chemin) / F.DOCUMENTS, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(doc('ART-FICTIF-9', 'v1', 'Article ajouté pendant l’import.')) + '\n')
        out = F.avancer(self.d, ADMIN, out['id'], checkpoint=lambda d: None)
        self.assertEqual((out['etat'], out['motif']), ('rejete', 'lot_modifie_pendant_import'))
        self.assertEqual(self.actif(), a['id'])

    def test_G23_licence_non_qualifiee_jamais_publiee(self):
        out = self.publier(self.lot('lot-a', [V1], licence_verifiee=False))
        self.assertEqual(out['etat'], 'licence_a_qualifier')
        self.assertIsNone(self.actif())
        self.assertEqual(F.etat(self.d)['fonds'][0]['libelle'], 'Licence à qualifier')

    def test_G23_publication_atomique_panne_pendant_l_indexation(self):
        a = self.publier(self.lot('lot-a', [V1]))
        out = F.importer(self.d, ADMIN, self.lot('lot-b', [V1, V2, DEC], snapshot_id='lot-b'), self.cle(), pas=10)
        self.assertEqual(out['etat'], 'indexation')
        vrai, appels = DocumentIndex.put_source, []

        def panne(index, *args, **kw):
            appels.append(1)
            if len(appels) == 2:
                raise RuntimeError('extinction simulée')
            return vrai(index, *args, **kw)
        with patch.object(DocumentIndex, 'put_source', panne):
            with self.assertRaises(RuntimeError):
                F.avancer(self.d, ADMIN, out['id'], checkpoint=lambda d: None)
        self.assertEqual(self.actif(), a['id'])                          # aucun mélange exposé : l'actif est l'ancien lot
        resultats, _ = F.rechercher(self.d, 'délai de préavis bail')
        self.assertEqual({r['lot'] for r in resultats}, {a['id']})
        out = F.avancer(self.d, ADMIN, out['id'], checkpoint=lambda d: None)  # reprise sur le curseur vérifié
        self.assertEqual((out['etat'], self.actif()), ('actif', out['id']))
        self.assertEqual(F._lot(self.d, a['id'])['etat'], 'precedent')      # actif précédent conservé
        self.assertTrue(F._index_present(self.d, F._lot(self.d, a['id'])['index_rel']))


class Temps(Base):
    def setUp(self):
        super().setUp()
        self.publier(self.lot('lot-a', [V1, V2, DEC]))

    def test_G23_garde_date_historique_respectee(self):
        r, _ = F.rechercher(self.d, 'délai de préavis bail', date_utile='2024-06-01')
        self.assertEqual([x['version_id'] for x in r if x['official_id'] == 'ART-FICTIF-1'], ['v1'])
        r, _ = F.rechercher(self.d, 'délai de préavis bail', date_utile='2025-06-01')
        self.assertEqual([x['version_id'] for x in r if x['official_id'] == 'ART-FICTIF-1'], ['v2'])
        # mutation : une sélection qui prendrait la dernière version ignorerait la date demandée
        with patch('agent.fonds_juridique56109.choisir_article', return_value={'etat': 'version_selectionnee', 'version': dict(F._doc_par_chemin(
                self.d, self.d.db.execute('SELECT lot_actif FROM fonds56109_actifs').fetchone()[0], 'fonds56109://FR/ART-FICTIF-1/fr/v2'))}):
            r, _ = F.rechercher(self.d, 'délai de préavis bail', date_utile='2024-06-01')
        self.assertEqual([x['version_id'] for x in r if x['official_id'] == 'ART-FICTIF-1'], ['v2'])

    def test_G23_version_courante_ne_reconstitue_pas_le_passe(self):
        r, etat = F.rechercher(self.d, 'délai de préavis bail', date_utile='2019-06-01')
        self.assertNotIn('ART-FICTIF-1', [x['official_id'] for x in r])
        self.assertIn({'official_id': 'ART-FICTIF-1', 'etat': 'version_absente_a_cette_date'}, etat['constats'])

    def test_G23_dates_non_prouvees_jamais_certaines(self):
        rows = [dict(V1, intervalle_verifie=False)]
        self.assertEqual(F.choisir_article(rows, official_id='ART-FICTIF-1', jurisdiction='FR', language='fr', date_utile='2021-01-01')['etat'],
                         'couverture_temporelle_a_verifier')
        rows = [V1, dict(V1, version_id='autre', sha256='b' * 64)]
        self.assertEqual(F.choisir_article(rows, official_id='ART-FICTIF-1', jurisdiction='FR', language='fr', date_utile='2021-01-01')['etat'],
                         'versions_en_conflit')
        with self.assertRaises(Stop):
            F.choisir_article([dict(V1, valid_to_exclusive='2019-01-01')], official_id='ART-FICTIF-1', jurisdiction='FR', language='fr',
                              date_utile='2021-01-01')

    def test_G23_filtres_pays_langue_avant_classement(self):
        self.assertEqual(F.rechercher(self.d, 'délai de préavis', pays='DE')[1]['etat'], 'aucun_fonds_local')
        self.assertEqual(F.rechercher(self.d, 'délai de préavis', langue='en')[1]['etat'], 'aucun_fonds_local')

    def test_G23_citation_relue_dans_le_texte_officiel(self):
        lot = self.actif()
        self.assertEqual(F.verifier_citation(self.d, lot, 'ART-FICTIF-1', 'v1', 'préavis applicable au bail fictif est de un mois')['etat'], 'verifiee')
        self.assertEqual(F.verifier_citation(self.d, lot, 'ART-FICTIF-1', 'v1', 'préavis applicable est de six mois')['etat'], 'a_verifier')
        self.assertEqual(F.verifier_citation(self.d, lot, 'DEC-FICTIVE-1', 'v1', 'inexistante')['etat'], 'a_verifier')


class Separation(Base):
    def test_G23_garde_fonds_public_separe_des_pieces_privees(self):
        prive = DocumentIndex(self.f.c['state_dir'])
        self.addCleanup(prive.db.close)
        prive.put_source('DOS-001', '/Dossiers/DEMO/Note confidentielle ZORVAL.txt', 'Note privée : préavis de ZORVAL contesté par DURAND.', '"p1"')
        self.publier(self.lot('lot-a', [V1, DEC]))
        resultats, _ = F.rechercher(self.d, 'préavis ZORVAL')
        self.assertTrue(resultats)
        self.assertNotIn('Note privée', json.dumps(resultats, ensure_ascii=False))         # aucune pièce privée dans le fonds
        passages, _ = R.chercher(self.d, 'DOS-001', 'préavis bail fictif décision', 10, index=prive, memoire=False)
        self.assertFalse([p for p in passages if 'fictif' in p['extrait'] or 'Décision fictive' in p['extrait']])
        globaux, _ = prive.global_sources(['préavis', 'décision', 'fictive'])
        self.assertFalse([g for g in globaux if str(g['matter']).startswith('fonds56109')])   # la recherche du cabinet ne lit pas le fonds
        # mutation : écrire le fonds dans l'index des dossiers le ferait apparaître dans la recherche du cabinet
        prive.put_source('fonds56109:essai', 'fonds56109://FR/DEC/fr/v1', DEC['text'], DEC['sha256'])
        globaux, _ = prive.global_sources(['préavis', 'décision', 'fictive'])
        self.assertTrue([g for g in globaux if str(g['matter']).startswith('fonds56109')])

    def test_G23_aucun_sqlite_ouvert_autrement_que_par_sqlite3(self):
        vrai_open, ouverts = builtins.open, []

        def espion(fichier, *a, **k):
            ouverts.append(str(fichier))
            return vrai_open(fichier, *a, **k)
        with patch('builtins.open', espion), patch('agent.fonds_juridique56109.open', espion, create=True):
            self.publier(self.lot('lot-a', [V1, V2]))
            F.rechercher(self.d, 'préavis')
            fonds = F.etat(self.d)['fonds'][0]['fonds']
            F.supprimer_index(self.d, ADMIN, fonds)
            F.reconstruire(self.d, ADMIN, fonds, checkpoint=lambda d: None)
        self.assertFalse([p for p in ouverts if '.sqlite3' in p.lower()])

    def test_G23_aucun_telechargement_ni_publication_tierce(self):
        source = (ROOT / 'agent' / 'fonds_juridique56109.py').read_text(encoding='utf-8')
        code = '\n'.join(l for l in source.splitlines() if not l.lstrip().startswith(('#', '-', '"""')) and 'aucune publication' not in l)
        for interdit in ('urlopen', 'urllib.request', 'http.client', 'requests', 'socket', 'push_to_hub(', 'upload_file(', 'subprocess'):
            self.assertNotIn(interdit, code, interdit)


class IndexEtRestauration(Base):
    def test_G23_supprimer_puis_reconstruire_l_index_garde_les_textes(self):
        self.publier(self.lot('lot-a', [V1, DEC]))
        fonds = F.etat(self.d)['fonds'][0]['fonds']
        F.supprimer_index(self.d, ADMIN, fonds)
        resultats, etat = F.rechercher(self.d, 'préavis')
        self.assertEqual((resultats, etat['etat']), ([], 'index_a_reconstruire'))      # jamais « aucun résultat » trompeur
        self.assertEqual(F.etat(self.d)['fonds'][0]['libelle'], 'Index à reconstruire')
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM fonds56109_documents').fetchone()[0], 2)
        F.reconstruire(self.d, ADMIN, fonds, checkpoint=lambda d: None)
        self.assertTrue(F.rechercher(self.d, 'préavis')[0])

    def test_G23_restauration_index_absent_puis_reconstruit(self):
        self.publier(self.lot('lot-a', [V1]))
        shutil.rmtree(Path(self.f.c['state_dir']) / F.RACINE)               # export restauré : les fichiers d'index ne sont pas exportés
        self.assertEqual(F.rechercher(self.d, 'préavis')[1]['etat'], 'index_a_reconstruire')
        F.reconstruire(self.d, ADMIN, F.etat(self.d)['fonds'][0]['fonds'], checkpoint=lambda d: None)
        self.assertEqual(F.rechercher(self.d, 'préavis')[1]['etat'], 'resultats')

    def test_G23_contrat_d_export(self):
        exportees = {t[2] for t in X.tables_exportees()}
        for table in ('fonds56109_lots', 'fonds56109_documents', 'fonds56109_actifs'):
            self.assertIn(table, exportees)
        self.assertTrue(X.motif_exclusion('fonds56109_ops'))


class Routes(Base):
    def test_G23_roles_no_store_idempotence_et_version(self):
        chemin = self.lot('lot-a', [V1, V2, DEC])
        self.assertEqual(self.api('importer', {'chemin': chemin, 'cle': 'cle-g23-route-01'}, acteur=ASSISTANT)['status'], '403 Forbidden')
        out = self.api('importer', {'chemin': chemin, 'cle': 'cle-g23-route-01'})
        self.assertEqual(out['status'], '200 OK')
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        again = self.api('importer', {'chemin': chemin, 'cle': 'cle-g23-route-01'})
        self.assertEqual(again['json']['lot'], out['json']['id'])
        self.assertEqual(self.api('importer', {'chemin': chemin + 'x', 'cle': 'cle-g23-route-01'})['status'], '409 Conflict')
        lot = out['json']['id']
        self.assertEqual(self.api('suspendre', {'lot': lot, 'version': 999, 'cle': 'cle-g23-route-02'})['status'], '409 Conflict')
        r = self.api('rechercher', methode='GET', acteur=ASSISTANT, args={'q': 'préavis'})
        self.assertEqual(r['status'], '200 OK')
        self.assertEqual(self.api('etat', methode='GET', acteur=ASSISTANT)['status'], '200 OK')
        self.assertEqual(self.api('inconnu', methode='GET')['status'], '404 Not Found')

    def test_G23_dossier_d_import_refuse(self):
        self.assertEqual(self.api('importer', {'chemin': 'relatif/lot', 'cle': 'cle-g23-route-03'})['json']['error'], 'dossier_import_invalide')
        vide = self.tmp / 'vide'
        vide.mkdir()
        self.assertEqual(self.api('importer', {'chemin': str(vide), 'cle': 'cle-g23-route-04'})['json']['error'], 'manifeste_absent')

    def test_G23_panneau_dans_les_parametres_existants(self):
        from agent import parametres5621
        self.publier(self.lot('lot-a', [V1]))

        class Cap:
            head = ''
        env = {'HTTP_X_AXIORHUB_USER': 'cabinet', 'HTTP_X_AXIORHUB_ROLE': 'administrateur', 'QUERY_STRING': ''}
        html = parametres5621.BUILDERS['recherche'](self.d, self.auth, PREFIX, env, Cap())
        self.assertIn('Fonds juridique local', html)
        self.assertIn('Corpus arrêté au 2026-10-01.', html)
        self.assertIn('/static/v56109-fonds.js', html)
        self.assertNotIn('<script>', html)


if __name__ == '__main__':
    unittest.main()
