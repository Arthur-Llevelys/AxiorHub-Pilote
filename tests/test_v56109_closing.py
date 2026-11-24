"""5.6.109 (lot G3, G04) : « À finaliser » sur l'opération EXISTANTE de Corporate et de Cession (``closing56109``, ``cessions5684``,
``corporate5636``).

Conditions persistantes (responsable, jalon, preuve, valideur), dépendances sans cycle dans la même opération, preuves versionnées au journal
immuable ; aucun « prêt à signer » avec une condition non vérifiée ; un acte modifié invalide la validation ; décision qualifiée sur des
blocages énumérés ; aucun paiement ni libération de séquestre. Chaque garde-fou a son test de mutation (garde retirée en mémoire).
Données fictives (ZORVAL, KERVILAC, Valbrune, example.test) ; aucun réseau, aucun modèle.
"""
import io
import json
import sqlite3
import unittest
from unittest.mock import patch

import test_v5684 as t84
from agent import bibliotheque5639 as lib, cessions5684 as ces, closing56109 as C, membres5650, office_bridge5684 as bridge
from agent.common import Stop
from agent.erreurs5634 import Qualified

AVOCAT, ASSISTANT, ADMIN, IA = ('avocate@example.test', 'avocat'), ('assistant@example.test', 'assistant'), ('admin@example.test', 'administrateur'), ('agent_cabinet', 'agent')
AUTRE = ('autre.avocat@example.test', 'avocat')
ORIGINE, JETON = 'https://pilote.example.test', 'jeton-csrf-fictif'


class Socle(t84.Base):
    def setUp(self):
        super().setUp()
        self.n = 0
        self.did = self.op()

    def cle(self):
        self.n += 1
        return 'cle-g04-%04d' % self.n

    def init(self, did=None):
        return C.initialiser(self.desk, AVOCAT, {'op_kind': 'dossier5683', 'op_id': did or self.did})

    def ligne(self, cle, did=None):
        return next(y for g in C.vue(self.desk, AVOCAT, 'dossier5683', did or self.did)['groupes'].values() for y in g if y['cle'] == cle)

    def verifier(self, cle, acteur=AVOCAT, **plus):
        x = self.ligne(cle)
        return C.decider(self.desk, acteur, {'condition': x['id'], 'version': x['version'], 'etat': 'verifie', 'preuve': self.matter_path() + '/Preuve.pdf',
                                             'preuve_version': 'sha-fictif-1', **plus})

    def tout_lever(self, sauf=()):
        for g in C.vue(self.desk, AVOCAT, 'dossier5683', self.did)['groupes'].values():
            for x in g:
                if x['cle'] not in sauf and x['etat'] != 'verifie':
                    C.decider(self.desk, AVOCAT, {'condition': x['id'], 'version': x['version'], 'etat': 'non_applicable', 'motif': 'Sans objet pour cette cession (essai)'})


class G04Conditions(Socle):
    def test_G04_liste_reprise_de_l_operation_existante_sans_seconde_operation(self):
        avant = self.desk.db.execute('SELECT COUNT(*) FROM operation_dossiers5683').fetchone()[0]
        out = self.init()
        self.assertGreater(out['ajoutees'], 5)
        self.assertEqual(self.init()['ajoutees'], 0)                                          # idempotent : clé unique par opération
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM operation_dossiers5683').fetchone()[0], avant)
        v = C.vue(self.desk, AVOCAT, 'dossier5683', self.did)
        self.assertEqual(set(v['groupes']), set(C.GROUPES))
        self.assertTrue(any(x['cle'].startswith('cond:') for x in v['groupes']['conditions']))      # conditions de cessions5684 reprises
        self.assertTrue(any(x['cle'].startswith('formalite:') for x in v['groupes']['formalites']))  # formalités du parcours reprises
        self.assertEqual(v['etat'], 'a_finaliser')
        self.assertEqual(len(v['priorites']), 3)

    def test_G04_piece_presente_non_verifiee_reste_un_blocage(self):
        self.init()
        x = self.ligne('cond:preemption')
        C.decider(self.desk, ASSISTANT, {'condition': x['id'], 'version': x['version'], 'etat': 'recu_a_verifier', 'preuve': self.matter_path() + '/AR.pdf',
                                         'preuve_version': 'v1'})
        self.assertEqual(self.ligne('cond:preemption')['motif_blocage'], 'recu_a_verifier')
        dos = ces.load(self.desk, self.did)
        self.assertTrue(any('Droit de préemption' in b for b in ces.ready_to_sign_blockers(self.desk, dos)))

    def test_G04_verifie_exige_preuve_version_et_valideur_humain(self):
        self.init()
        x = self.ligne('cond:preemption')
        with self.assertRaises(C.Refus) as ctx:
            C.decider(self.desk, AVOCAT, {'condition': x['id'], 'version': x['version'], 'etat': 'verifie'})
        self.assertIn('preuve', ctx.exception.detail)
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            self.verifier('cond:preemption', ASSISTANT)
        with self.assertRaisesRegex(Stop, 'verification_humaine_requise'):
            self.verifier('cond:preemption', IA)
        self.verifier('cond:preemption')
        y = self.ligne('cond:preemption')
        self.assertTrue(y['levee'])
        self.assertEqual(y['validateur'], AVOCAT[0])

    def test_G04_mutation_contrainte_de_table_verifie_sans_valideur(self):
        self.init()
        x = self.ligne('cond:preemption')
        with self.assertRaises(sqlite3.IntegrityError):                                     # même en contournant le service
            self.desk.db.execute("UPDATE closing_conditions56109 SET etat='verifie' WHERE id=?", (x['id'],))
        self.desk.db.rollback()
        # et l'évaluation ne lève pas une ligne « vérifiée » sans preuve si la contrainte manquait
        lignes = [dict(x, etat='verifie', preuve='', validateur='')]
        self.assertEqual(C._propre(lignes[0], {}), (False, 'preuve_manquante'))

    def test_G04_non_applicable_exige_motif_et_version_lue(self):
        self.init()
        x = self.ligne('paiement')
        with self.assertRaises(C.Refus):
            C.decider(self.desk, AVOCAT, {'condition': x['id'], 'version': x['version'], 'etat': 'non_applicable', 'motif': ''})
        with self.assertRaisesRegex(Stop, 'version_perimee'):
            C.decider(self.desk, AVOCAT, {'condition': x['id'], 'version': x['version'] + 7, 'etat': 'non_applicable', 'motif': 'Prix payé hors séquestre (essai)'})
        C.decider(self.desk, AVOCAT, {'condition': x['id'], 'version': x['version'], 'etat': 'non_applicable', 'motif': 'Prix payé hors séquestre (essai)'})
        self.assertTrue(self.ligne('paiement')['levee'])

    def test_G04_preuves_versionnees_au_journal_immuable(self):
        self.init()
        self.verifier('cond:preemption')
        x = self.ligne('cond:preemption')
        C.decider(self.desk, AVOCAT, {'condition': x['id'], 'version': x['version'], 'etat': 'verifie', 'preuve': self.matter_path() + '/Preuve-v2.pdf',
                                      'preuve_version': 'sha-fictif-2'})
        hist = [json.loads(r['apres']).get('preuve') for r in self.desk.db.execute("SELECT apres FROM closing_journal56109 WHERE condition=? AND action='etat' ORDER BY id", (x['id'],))]
        self.assertEqual([h.rsplit('/', 1)[-1] for h in hist], ['Preuve.pdf', 'Preuve-v2.pdf'])
        with self.assertRaises(sqlite3.DatabaseError):
            self.desk.db.execute('DELETE FROM closing_journal56109')
        self.desk.db.rollback()


class G04Graphe(Socle):
    def test_G04_cycle_reference_absente_et_autre_operation_refuses(self):
        self.init()
        a, b = self.ligne('signature'), self.ligne('paiement')
        C.dependre(self.desk, AVOCAT, {'condition': b['id'], 'depend_de': a['id']})
        with self.assertRaises(C.Refus) as ctx:
            C.dependre(self.desk, AVOCAT, {'condition': a['id'], 'depend_de': b['id']})
        self.assertEqual(str(ctx.exception), 'cycle_conditions')
        with self.assertRaisesRegex(Stop, 'dependance_absente'):
            C.dependre(self.desk, AVOCAT, {'condition': a['id'], 'depend_de': 'cl-inexistante'})
        autre = self.op('droit_bail')
        C.initialiser(self.desk, AVOCAT, {'op_kind': 'dossier5683', 'op_id': autre})
        x = self.ligne('signature', autre)
        with self.assertRaisesRegex(Stop, 'dependance_hors_operation'):
            C.dependre(self.desk, AVOCAT, {'condition': a['id'], 'depend_de': x['id']})

    def test_G04_dependance_non_levee_bloque_meme_une_ligne_verifiee(self):
        self.init()
        a, b = self.ligne('signature'), self.ligne('paiement')
        C.dependre(self.desk, AVOCAT, {'condition': b['id'], 'depend_de': a['id']})
        C.decider(self.desk, AVOCAT, {'condition': b['id'], 'version': b['version'], 'etat': 'verifie', 'preuve': '/x.pdf', 'preuve_version': 'v1'})
        self.assertEqual(self.ligne('paiement')['motif_blocage'], 'dependance')

    def test_G04_mutation_cycle_insere_en_base_bloque_tout(self):
        self.init()
        self.tout_lever()
        self.assertEqual(C.vue(self.desk, AVOCAT, 'dossier5683', self.did)['etat'], 'prete_a_signer')
        a, b = self.ligne('signature'), self.ligne('paiement')
        self.desk.db.executemany('INSERT INTO closing_dependances56109 VALUES(?,?,?,?)', [(a['id'], b['id'], 'x', 'x'), (b['id'], a['id'], 'x', 'x')])
        self.desk.db.commit()
        v = C.vue(self.desk, AVOCAT, 'dossier5683', self.did)
        self.assertTrue(v['cycle'])
        self.assertEqual(v['etat'], 'a_finaliser')                                            # jamais « prêt » avec un graphe invalide


class G04PretASigner(Socle):
    def acte(self):
        self.fill(self.did)
        self.validate('acte_cession_fonds')
        with patch.object(lib, 'missing_mentions', lambda *a: []):
            out = ces.prepare_document(self.desk, {'id': self.did, 'reference': 'acte_cession_fonds'}, 'avocate', self.docs)
        return out, ces.documents(self.desk, self.dos(self.did))[0]

    def test_G04_aucun_pret_a_signer_avec_condition_non_verifiee(self):
        out, row = self.acte()
        self.init()
        with patch.object(ces, 'conditions', lambda desk, dos: []), patch.object(ces, 'missing', lambda desk, dos: []):
            with self.assertRaises(Qualified) as ctx:
                ces.mark_ready(self.desk, {'id': self.did, 'document': row['id']}, 'avocate')
            self.assertIn('À finaliser', ctx.exception.detail['precision'])
            self.tout_lever()
            ces.mark_ready(self.desk, {'id': self.did, 'document': row['id']}, 'avocate')       # tout est levé : « prêt à signer » permis

    def test_G04_mutation_sans_garde_le_pret_a_signer_passerait(self):
        out, row = self.acte()
        self.init()
        with patch.object(ces, 'conditions', lambda desk, dos: []), patch.object(ces, 'missing', lambda desk, dos: []), \
                patch.object(C, 'libelles_blocages', lambda *a: []):
            ces.mark_ready(self.desk, {'id': self.did, 'document': row['id']}, 'avocate')       # garde retirée : la condition non vérifiée passerait
        self.assertEqual(ces.documents(self.desk, self.dos(self.did))[0]['state'], ces.DOC_READY)

    def test_G04_acte_modifie_invalide_la_validation_sans_effacer_l_historique(self):
        out, row = self.acte()
        self.init()
        x = self.ligne('doc:acte_cession_fonds')
        self.verifier('doc:acte_cession_fonds')
        self.assertTrue(self.ligne('doc:acte_cession_fonds')['levee'])
        bridge.schema(self.desk)
        now = self.desk.now()
        self.desk.db.execute("INSERT INTO office_documents5684 VALUES('doc-g04',?,'','',?,?)", (out['path'], now, now))
        self.desk.db.commit()
        ces.document_edited(self.desk, 'doc-g04')                                            # édition de l'acte dans EuroOffice
        y = self.ligne('doc:acte_cession_fonds')
        self.assertEqual(y['etat'], 'recu_a_verifier')
        self.assertFalse(y['levee'])
        j = self.desk.db.execute("SELECT avant FROM closing_journal56109 WHERE condition=? AND action='validation_invalidee'", (x['id'],)).fetchone()
        self.assertEqual(json.loads(j['avant'])['validateur'], AVOCAT[0])                    # l'ancienne validation reste au journal

    def test_G04_nouvelle_version_d_acte_vue_sans_ecriture_puis_invalidee(self):
        out, row = self.acte()
        self.init()
        self.verifier('doc:acte_cession_fonds')
        self.desk.db.execute("UPDATE deliverables5683 SET version=version+1 WHERE id=?", (row['id'],))     # une version nouvelle de l'acte
        self.desk.db.commit()
        y = self.ligne('doc:acte_cession_fonds')
        self.assertEqual((y['etat'], y['motif_blocage']), ('verifie', 'acte_modifie'))       # constaté à la lecture, sans écriture
        z = self.ligne('paiement')
        C.decider(self.desk, AVOCAT, {'condition': z['id'], 'version': z['version'], 'etat': 'bloque', 'motif': 'Séquestre non constitué'})
        self.assertEqual(self.ligne('doc:acte_cession_fonds')['etat'], 'recu_a_verifier')     # persisté par l'écriture suivante

    def test_G04_mutation_version_d_acte_ignoree_la_validation_survivrait(self):
        out, row = self.acte()
        self.init()
        self.verifier('doc:acte_cession_fonds')
        self.desk.db.execute("UPDATE deliverables5683 SET version=version+1 WHERE id=?", (row['id'],))
        self.desk.db.commit()
        ancienne = self.ligne('doc:acte_cession_fonds')['acte_version']
        with patch.object(C, 'versions_actes', lambda desk, op: {'acte_cession_fonds': ancienne}):
            self.assertTrue(self.ligne('doc:acte_cession_fonds')['levee'])                    # sans le contrôle de version, l'acte modifié passerait


class G04Realisation(Socle):
    def preparer(self):
        self.init()
        ces.add_event(self.desk, {'id': self.did, 'evenement': 'signature', 'date': '2026-03-01', 'piece': self.piece()}, 'avocate', self.docs)
        ces.add_event(self.desk, {'id': self.did, 'evenement': 'realisation', 'date': '2026-03-02', 'piece': self.piece('R.pdf')}, 'avocate', self.docs)

    def test_G04_case_globale_ne_leve_pas_les_nouveaux_controles(self):
        self.preparer()
        with self.assertRaises(Qualified) as ctx:
            ces.realize(self.desk, {'id': self.did, 'decision_qualifiee': True}, 'avocate')
        self.assertEqual(str(ctx.exception), 'closing_a_finaliser')

    def test_G04_decision_qualifiee_sur_blocages_enumeres(self):
        self.preparer()
        self.tout_lever(sauf=('paiement', 'signature'))
        bl = C.vue(self.desk, AVOCAT, 'dossier5683', self.did)
        ids = [x['id'] for g in bl['groupes'].values() for x in g if not x['levee'] and x['jalon'] in ('signature', 'realisation')]
        self.assertEqual(len(ids), 2)
        with self.assertRaises(C.Refus):
            C.decision_qualifiee(self.desk, AVOCAT, {'op_kind': 'dossier5683', 'op_id': self.did, 'jalon': 'realisation', 'motif': 'Trop court'})
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            C.decision_qualifiee(self.desk, ASSISTANT, {'op_kind': 'dossier5683', 'op_id': self.did, 'blocages': ids, 'motif': 'Décision de l’avocat (essai fictif)'})
        partielle = C.decision_qualifiee(self.desk, AVOCAT, {'op_kind': 'dossier5683', 'op_id': self.did, 'blocages': ids[:1],
                                                             'motif': 'Paiement constaté par le notaire (essai fictif)'})
        with self.assertRaises(Qualified):
            ces.realize(self.desk, {'id': self.did, 'decision_qualifiee': True, 'decision_closing': partielle['decision']}, 'avocate')
        totale = C.decision_qualifiee(self.desk, AVOCAT, {'op_kind': 'dossier5683', 'op_id': self.did, 'blocages': ids,
                                                          'motif': 'Signature et paiement constatés hors AxiorHub (essai fictif)'})
        out = ces.realize(self.desk, {'id': self.did, 'decision_qualifiee': True, 'decision_closing': totale['decision']}, 'avocate')
        self.assertIn('Aucune libération', out['message'])
        traces = [r['origine'] for r in self.desk.db.execute('SELECT origine FROM closing_decisions56109 WHERE op_id=?', (self.did,))]
        self.assertIn('case_5684', traces)                                                     # l'ancienne case est tracée

    def test_G04_decision_ne_couvre_pas_une_ligne_modifiee_ensuite(self):
        self.preparer()
        self.tout_lever(sauf=('paiement',))
        x = self.ligne('paiement')
        d = C.decision_qualifiee(self.desk, AVOCAT, {'op_kind': 'dossier5683', 'op_id': self.did, 'blocages': [x['id']], 'motif': 'Paiement hors séquestre (essai)'})
        self.assertTrue(C.decision_couvre(self.desk, 'dossier5683', self.did, d['decision'], 'realisation'))
        C.decider(self.desk, AVOCAT, {'condition': x['id'], 'version': x['version'], 'etat': 'bloque', 'motif': 'Opposition reçue (essai)'})
        self.assertFalse(C.decision_couvre(self.desk, 'dossier5683', self.did, d['decision'], 'realisation'))

    def test_G04_aucun_paiement_ni_liberation_de_sequestre(self):
        self.preparer()
        avant = {t: self.desk.db.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0] for t in ('mouvements56109', 'connexion_ops5652')}
        self.verifier('paiement')
        self.tout_lever()
        ces.realize(self.desk, {'id': self.did, 'decision_qualifiee': True}, 'avocate')
        with self.assertRaisesRegex(Stop, 'liberation_sequestre_interdite'):
            ces.release_sequestre(self.desk, {'id': self.did}, 'avocate')
        apres = {t: self.desk.db.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0] for t in avant}
        self.assertEqual(avant, apres)                                                         # aucun mouvement, aucun effet externe
        self.assertEqual(C.vue(self.desk, AVOCAT, 'dossier5683', self.did)['etat'], 'formalites_terminees')


class G04Corporate(t84.Base):
    def test_G04_projet_corporate_meme_contrat_et_realisation_gardee(self):
        out = self.co.operation_submit(self.desk, {'kind': 'siege', 'company': self.cid, 'nouveau_siege': '5 place Fictive, 69003 Lyon', 'date_effet': '2026-11-02',
                                                   'organe': 'assemblee'})
        self.run_job(out['id'])
        C.initialiser(self.desk, AVOCAT, {'op_kind': 'corporate5636', 'op_id': out['id']})
        v = C.vue(self.desk, AVOCAT, 'corporate5636', out['id'])
        self.assertTrue(any(x['cle'] == 'formalite:inpi' for x in v['groupes']['formalites']))
        with self.assertRaises(Qualified):
            self.realize(out)
        dec = next(x for x in v['groupes']['signature'] if x['cle'] == 'decision')
        restants = [x['id'] for g in v['groupes'].values() for x in g if x['jalon'] in ('signature', 'realisation')]
        d = C.decision_qualifiee(self.desk, AVOCAT, {'op_kind': 'corporate5636', 'op_id': out['id'], 'blocages': restants, 'motif': 'Décision prise en AG, actes relus (essai)'})
        from agent.common import load_matters
        piece = next(m for m in load_matters(self.desk.c) if m['id'] == t84.MID)['path'].rstrip('/') + '/Acte signe.pdf'
        self.docs.files[piece] = b'%PDF-1.4 fictif'
        row = self.desk.db.execute('SELECT data FROM operations5636 WHERE id=?', (out['id'],)).fetchone()
        self.co.realize(self.desk, {'id': out['id'], 'date': json.loads(row['data']).get('date_ag') or '2026-10-12', 'justificatif': 'PV signé (essai fictif)',
                                    'decision_closing': d['decision'], 'confirmer_date': '1'}, dav=self.docs)
        self.assertEqual(self.desk.db.execute('SELECT state FROM operations5636 WHERE id=?', (out['id'],)).fetchone()[0], 'realisee')
        self.assertTrue(dec)

    def test_G04_html_dans_les_pages_existantes_sans_page_closing(self):
        did = self.op()
        html = ces.page_html(self.desk, '/p', {'type': 'fonds', 'operation': did, 'vue': 'finaliser'})
        self.assertIn('À finaliser', html)
        self.assertIn('m56109/operations/closing/initialiser', html)
        from agent import web440
        self.assertFalse(any('/closing' in p for p in getattr(web440, 'STATIC', {})))


class G04Routes(Socle):
    def appel(self, route, data=None, acteur=AVOCAT, args=None):
        from agent import web440
        corps = json.dumps(data or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': acteur[0], 'HTTP_X_AXIORHUB_ROLE': acteur[1], 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON,
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps),
               'REQUEST_METHOD': 'GET' if data is None else 'POST'}
        out = web440.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm56109/operations/' + route, args or {}, env['REQUEST_METHOD'])
        self.assertIn(('Cache-Control', 'private, no-store'), out.get('headers', []))
        return out['status'][:3], json.loads(out['body'])

    def test_G04_routes_cle_409_404_neutre_et_roles(self):
        st, out = self.appel('closing/initialiser', {'cle': self.cle(), 'op_kind': 'dossier5683', 'op_id': self.did})
        self.assertEqual(st, '200', out)
        st, out2 = self.appel('closing/initialiser', {'cle': 'cle-g04-0001', 'op_kind': 'dossier5683', 'op_id': self.did})
        self.assertTrue(out2.get('rejoue'))
        st, _ = self.appel('closing/initialiser', {'cle': 'cle-g04-0001', 'op_kind': 'dossier5683', 'op_id': 'autre'})
        self.assertEqual(st, '409')
        st, a = self.appel('closing', args={'op_kind': 'dossier5683', 'op_id': 'inexistante'})
        membres5650.set_reserve(self.desk.c['state_dir'], t84.MID, [AVOCAT[0]])
        self.addCleanup(membres5650.clear)
        st2, b = self.appel('closing', args={'op_kind': 'dossier5683', 'op_id': self.did}, acteur=AUTRE)
        self.assertEqual((st, st2), ('404', '404'))
        self.assertEqual(a, b)                                                                  # absent et interdit : même réponse neutre
        st, _ = self.appel('closing/decision', {'cle': self.cle(), 'op_kind': 'dossier5683', 'op_id': self.did}, acteur=ASSISTANT)
        self.assertEqual(st, '403')
        st, out = self.appel('closing/decider', {'cle': self.cle(), 'condition': 'x', 'pin': '1234'})
        self.assertEqual((st, out['error']), ('422', 'secret_refuse'))


if __name__ == '__main__':
    unittest.main()
