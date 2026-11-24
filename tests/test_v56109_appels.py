"""5.6.109, lot G2 (G06) : appels, comptes rendus et rappels dans les fiches existantes (contact, dossier, demande du CRM).

Données fictives (DURAND, MARTIN, ZORVAL, KERVILAC, DOS-001, DOS-002, example.test, 01 00 00 00 05) ; stockage local temporaire ; tout appel réseau
non simulé fait échouer l'essai ; aucun modèle (tout est utilisable IA en pause). La vraie dictée vocale et le vrai navigateur ne sont pas
qualifiés ici. Le garde-fou « une action par décision » a un essai qui échouerait s'il était retiré.
"""
import json
import unittest
from unittest.mock import patch

from test_v56109_partage_client import Base
from test_v5697_gestion import ASSISTANT, AUTRE, AVOCAT, MID, MID2
from agent import appels56109 as AP, bland56109, crm5636, crm5676, meeting500, pause5629
from agent.common import Stop


class AppelsBase(Base):
    def setUp(self):
        super().setUp()
        crm5636.schema(self.desk)
        crm5676.ensure_schema(self.desk)

    def contact(self, nom, prenom='', email='', categorie='client'):
        return crm5636.save(self.desk, {'kind': 'personne', 'category': categorie, 'name': nom, 'first_name': prenom, 'email': email, 'phone': '01 00 00 00 05'})

    def consigner(self, who=AVOCAT, **data):
        base = {'sens': 'entrant', 'quand': '2026-11-23T10:15', 'texte': 'Le client demande où en est le dossier. Actions : rappeler le client avant le 30/11/2026.'}
        base.update(data)
        return AP.consigner(self.desk, who, base, data.pop('cle', None) or self.cle())

    def nb(self, sql, params=()):
        return self.desk.db.execute(sql, params).fetchone()[0]


class Appels(AppelsBase):
    def test_G06_appel_de_prospect_sans_dossier_puis_rattachement_sans_faux_evenement(self):
        opp = crm5676.create(self.desk, AVOCAT[0], {'title': 'Litige commercial ZORVAL', 'name': 'ZORVAL', 'email': 'zorval@example.test'})
        avant_agenda = self.nb('SELECT COUNT(*) FROM agenda5634') if self.desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='agenda5634'").fetchone() else 0
        a = self.consigner(demande=opp['id'], interlocuteur='ZORVAL')
        self.assertEqual(a['matter_id'], '')
        self.assertEqual(a['demande_crm_id'], opp['id'])
        apres_agenda = self.nb('SELECT COUNT(*) FROM agenda5634') if self.desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='agenda5634'").fetchone() else 0
        self.assertEqual(apres_agenda, avant_agenda)                                  # aucun rendez-vous inventé pour un appel spontané
        cr = AP.compte_rendu(self.desk, AVOCAT, a['id'], {'version': a['version']})
        self.assertEqual(self.nb('SELECT COUNT(*) FROM meeting500_reports'), 0)     # jamais un faux event_id
        r = AP.rattacher(self.desk, AVOCAT, a['id'], {'version': cr['version'], 'dossier': MID})
        self.assertEqual(r['matter_id'], MID)
        self.assertEqual([h['quoi'] for h in r['historique']], ['consigne', 'rattache'])
        self.assertEqual(r['historique'][1]['avant'], {'dossier': '', 'demande': opp['id']})
        # B1 : la version passée garde son dossier (vide) ; le rattachement crée une copie à relire dans le nouveau dossier
        self.assertEqual(sorted(r[0] for r in self.desk.db.execute("SELECT matter_id FROM prepared_items5689 WHERE source_service='appels56109' ORDER BY created")),
                         sorted(['', MID]))
        detail = crm5676._detail(self.desk, '/p', opp['id'], 'avocat')
        self.assertIn('<h2>Appels</h2>', detail)
        self.assertIn('ZORVAL', crm5676._appels_de_la_demande(self.desk, opp['id']))

    def test_G06_duree_inconnue_conservee_jamais_deduite_du_texte(self):
        a = self.consigner(texte='Très long compte rendu. ' * 400)
        self.assertIsNone(a['duree_minutes'])
        self.assertEqual(a['duree']['libelle'], 'À compléter')
        cr = AP.compte_rendu(self.desk, AVOCAT, a['id'], {'version': a['version']})
        self.assertIn('Durée : à compléter', cr['texte'])
        self.assertIsNone(AP.duree_prefacturation(self.desk, a['id'])['minutes'])
        b = self.consigner(duree='12')
        self.assertEqual(AP.duree_prefacturation(self.desk, b['id'])['minutes'], 12)
        with self.assertRaisesRegex(Stop, 'duree_invalide'):
            self.consigner(duree='0')

    def test_G06_une_action_par_decision_double_clic_autre_poste_et_mutation(self):
        a = self.consigner(dossier=MID, prochaine_action='Rappeler DURAND pour le calendrier')
        avant = self.nb('SELECT COUNT(*) FROM tasks')
        d1 = AP.decider(self.desk, AVOCAT, a['id'], {'nature': 'rappel', 'rang': 0, 'echeance': '2026-11-30'})
        d2 = AP.decider(self.desk, AVOCAT, a['id'], {'nature': 'rappel', 'rang': 0, 'echeance': '2026-11-30'})        # double clic
        d3 = AP.decider(self.desk, ASSISTANT, a['id'], {'nature': 'rappel', 'rang': 0, 'echeance': '2026-12-01'})     # reprise sur un autre poste
        self.assertEqual(self.nb('SELECT COUNT(*) FROM tasks'), avant + 1)
        self.assertEqual({d1['tache_id'], d2['tache_id'], d3['tache_id']}, {d1['tache_id']})
        self.assertTrue(d2['rejoue'] and d3['rejoue'])
        tache = self.desk.db.execute('SELECT matter, title, due FROM tasks WHERE id=?', (d1['tache_id'],)).fetchone()
        self.assertEqual((tache['matter'], tache['due']), (MID, '2026-11-30'))
        AP.decider(self.desk, AVOCAT, a['id'], {'nature': 'tache', 'rang': 0})       # une autre décision : une autre tâche
        self.assertEqual(self.nb('SELECT COUNT(*) FROM tasks'), avant + 2)
        # garde retirée (réservation de la décision oubliée) : la reprise par un autre compte créerait une seconde tâche
        self.desk.db.execute("DELETE FROM appels_actions56109 WHERE appel_id=? AND decision='rappel:0'", (a['id'],))
        self.desk.db.commit()
        AP.decider(self.desk, ASSISTANT, a['id'], {'nature': 'rappel', 'rang': 0, 'echeance': '2026-11-30'})
        self.assertEqual(self.nb('SELECT COUNT(*) FROM tasks'), avant + 3)

    def test_G06_dictee_rejouee_ne_cree_qu_un_appel_et_qu_un_rappel(self):
        cle = self.cle()
        texte = 'Dictée : le client confirme la date. Actions : relancer le greffe avant le 2 décembre 2026.'
        a = AP.consigner(self.desk, AVOCAT, {'sens': 'sortant', 'texte': texte, 'saisie': 'dictee', 'dossier': MID}, cle)
        b = AP.consigner(self.desk, AVOCAT, {'sens': 'sortant', 'texte': texte, 'saisie': 'dictee', 'dossier': MID}, cle)   # même dictée envoyée deux fois
        self.assertEqual(a['id'], b['id'])
        self.assertTrue(b['rejoue'])
        self.assertEqual(self.nb('SELECT COUNT(*) FROM appels56109'), 1)
        avant = self.nb('SELECT COUNT(*) FROM tasks')
        for _ in range(2):
            AP.decider(self.desk, AVOCAT, a['id'], {'nature': 'rappel', 'rang': 1, 'titre': 'Relancer le greffe'})
        self.assertEqual(self.nb('SELECT COUNT(*) FROM tasks'), avant + 1)
        with self.assertRaisesRegex(Stop, 'cle_reutilisee'):
            AP.consigner(self.desk, AVOCAT, {'sens': 'sortant', 'texte': 'Autre texte', 'dossier': MID}, cle)

    def test_G06_compte_rendu_canonique_original_et_corrections_conserves(self):
        a = self.consigner(dossier=MID, interlocuteur='DURAND', texte='Points abordés : calendrier. Actions : envoyer le projet de protocole.')
        cr = AP.compte_rendu(self.desk, AVOCAT, a['id'], {'version': a['version']})
        self.assertEqual(cr['revision'], 1)
        self.assertIn('COMPTE RENDU D’APPEL (PROJET)', cr['texte'])
        self.assertTrue(any('Action sans' in w for w in cr['warnings']))           # signalée, jamais complétée
        item1 = self.desk.db.execute("SELECT * FROM prepared_items5689 WHERE source_service='appels56109'").fetchone()
        self.assertEqual((item1['matter_id'], item1['review_state']), (MID, 'to_review'))
        corr = AP.compte_rendu(self.desk, AVOCAT, a['id'], {'version': cr['version'], 'texte': 'Compte rendu corrigé par l’avocate.'})
        self.assertEqual(corr['revision'], 2)
        rows = self.desk.db.execute("SELECT document_id, version_id, lifecycle FROM prepared_items5689 WHERE source_service='appels56109' ORDER BY version_number").fetchall()
        self.assertEqual(len({r['document_id'] for r in rows}), 1)                 # même document, deux versions
        self.assertEqual([r['lifecycle'] for r in rows], ['replaced', 'current'])
        v = AP.vue(self.desk, AVOCAT, a['id'])
        self.assertEqual(v['texte_original'], a['texte_original'])
        self.assertEqual([c['origine'] for c in v['comptes_rendus']], ['projet', 'correction'])
        with self.assertRaisesRegex(Stop, 'version_perimee'):
            AP.compte_rendu(self.desk, AVOCAT, a['id'], {'version': cr['version'], 'texte': 'Écrasement tardif'})

    def test_G06_contact_homonyme_reste_a_confirmer_sans_fusion(self):
        c1 = self.contact('DURAND', 'Alice', 'alice.durand@example.test')
        self.contact('DURAND', 'Bernard', 'bernard.durand@example.test')
        avant = self.nb('SELECT COUNT(*) FROM contacts5636')
        a = self.consigner(interlocuteur='DURAND')
        self.assertEqual(a['contact_id'], '')
        self.assertEqual(a['contact_a_confirmer'], 1)
        self.assertEqual(len(a['candidats']), 2)
        self.assertEqual(self.nb('SELECT COUNT(*) FROM contacts5636'), avant)
        ok = AP.confirmer_contact(self.desk, AVOCAT, a['id'], {'version': a['version'], 'contact': c1['id']})
        self.assertEqual((ok['contact_id'], ok['contact_a_confirmer']), (c1['id'], 0))
        self.assertEqual(len(AP.appels_contact(self.desk, AVOCAT, c1['id'])), 1)

    def test_G06_ia_en_pause_tout_reste_manuel(self):
        pause5629.set_pause(self.desk, MID, True, AVOCAT[0])
        self.assertTrue(pause5629.is_paused(self.desk, MID))
        travaux = self.nb('SELECT COUNT(*) FROM jobs')
        a = self.consigner(dossier=MID)
        cr = AP.compte_rendu(self.desk, AVOCAT, a['id'], {'version': a['version']})
        d = AP.decider(self.desk, AVOCAT, a['id'], {'nature': 'tache', 'rang': 0, 'titre': 'Préparer le courrier DURAND'})
        self.assertTrue(cr['texte'] and d['tache_id'])
        self.assertEqual(self.nb('SELECT COUNT(*) FROM jobs'), travaux)             # aucun travail d'IA mis en file

    def test_G06_appel_d_un_dossier_reserve_invisible_et_routes(self):
        a = self.consigner(dossier=MID2)
        for ident in (a['id'], 'ap-inexistant'):
            out = self.http(AP, ident, who=AUTRE)
            self.assertEqual((out['status'], out['json']['error']), ('404 Not Found', 'introuvable'))
        self.assertEqual(self.http(AP, 'dossier/' + MID2, who=AUTRE)['status'], '404 Not Found')
        out = self.http(AP, 'consigner', {'sens': 'entrant', 'texte': 'Appel du greffe', 'cle': self.cle()}, who=ASSISTANT)
        self.assertEqual(out['status'], '200 OK', out['json'])
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        avant = self.desk.db.total_changes
        self.assertEqual(self.http(AP, out['json']['id'])['status'], '200 OK')
        self.assertEqual(self.desk.db.total_changes, avant)

    def test_G06_fiche_contact_appels_et_accord_bland_lu_sans_copie(self):
        c = self.contact('MARTIN', 'Claire', 'claire.martin@example.test')
        self.consigner(contact=c['id'])
        bland56109.noter_accord(self.desk, AVOCAT[0], 'avocat', c['id'], True, False)
        html = crm5676.bloc_contact(self.desk, '/p', c)
        self.assertIn('Consigner un appel', html)
        self.assertIn('par SMS : oui', html)
        self.assertIn('par appel : non', html)
        self.assertEqual(self.nb('SELECT COUNT(*) FROM bland56109_accords'), 1)    # la donnée reste dans la table de bland56109
        page = crm5636.page_html(self.desk, '/p', {'id': c['id']})
        self.assertIn('Rappels de rendez-vous', page)


class RattachementRegistre(AppelsBase):
    """B1 (relecture de l'intégrateur) : rattacher un appel ne réécrit jamais l'historique du registre des livrables."""

    def test_G06_B1_rattachement_cree_une_copie_a_relire_sans_toucher_les_versions_passees(self):
        from agent import livrables5689 as liv, partage_client56109 as P
        a = self.consigner(dossier=MID, interlocuteur='DURAND', texte='Points abordés : calendrier de procédure.')
        cr = AP.compte_rendu(self.desk, AVOCAT, a['id'], {'version': a['version']})
        ancien = dict(self.desk.db.execute("SELECT * FROM prepared_items5689 WHERE source_service='appels56109'").fetchone())
        liv.relire(self.desk, ancien['document_id'], ancien['version_id'], 'valide', AVOCAT[0], 'avocat')     # validée pour le client de DOS-001
        partage = P.preparer(self.desk, AVOCAT, MID, {'destinataire': 'durand@example.test', 'expiration': self.expiration(),
                                                      'ressources': [{'document_id': ancien['document_id'], 'version_id': ancien['version_id']}]}, self.cle())
        audits = []
        reel = self.desk.audit
        with patch.object(self.desk, 'audit', lambda action, data: (audits.append((action, data)), reel(action, data))):
            r = AP.rattacher(self.desk, AVOCAT, a['id'], {'version': cr['version'], 'dossier': MID2})
        avant = self.desk.db.execute('SELECT matter_id, review_state, lifecycle FROM prepared_items5689 WHERE id=?', (ancien['id'],)).fetchone()
        self.assertEqual((avant['matter_id'], avant['review_state']), (MID, 'reviewed'))      # version passée intacte : dossier et validation
        self.assertEqual(P.vue(self.desk, AVOCAT, partage['id'])['ressources'][0]['version_id'], ancien['version_id'])
        P.version_registre(self.desk, AVOCAT, MID, ancien['document_id'], ancien['version_id'])   # toujours partageable dans DOS-001
        neuf = dict(self.desk.db.execute("SELECT * FROM prepared_items5689 WHERE source_service='appels56109' AND matter_id=?", (MID2,)).fetchone())
        self.assertNotEqual(neuf['document_id'], ancien['document_id'])
        self.assertEqual(neuf['review_state'], 'to_review')                                     # à relire avant tout partage au client de DOS-002
        with self.assertRaisesRegex(Stop, 'version_non_approuvee'):
            P.version_registre(self.desk, AVOCAT, MID2, neuf['document_id'], neuf['version_id'])
        with self.assertRaisesRegex(Stop, 'version_hors_dossier'):
            P.version_registre(self.desk, AVOCAT, MID2, ancien['document_id'], ancien['version_id'])
        self.assertTrue(self.desk.db.execute("SELECT 1 FROM prepared_source_links5689 WHERE item_id=? AND link_kind='source' AND source_object_id=?",
                                             (neuf['id'], ancien['id'])).fetchone())             # inscrit au registre : provenance
        trace = next(d for act, d in audits if act == 'appel56109_rattache')
        self.assertEqual((trace['document'], trace['ancien_dossier'], trace['nouveau_dossier'], trace['par']),
                         (neuf['document_id'], MID, MID2, AVOCAT[0]))
        self.assertEqual(r['document_id'], neuf['document_id'])
        corr = AP.compte_rendu(self.desk, AVOCAT, a['id'], {'version': r['version'], 'texte': 'Correction après rattachement.'})
        doc_corr = self.desk.db.execute("SELECT document_id, matter_id FROM prepared_items5689 WHERE source_service='appels56109' ORDER BY created DESC LIMIT 1").fetchone()
        self.assertEqual((doc_corr['document_id'], doc_corr['matter_id']), (neuf['document_id'], MID2))
        self.assertEqual(corr['revision'], 3)

    def test_G06_B1_droits_exiges_sur_les_deux_dossiers(self):
        a = self.consigner(dossier=MID2)
        with self.assertRaisesRegex(Stop, 'introuvable'):
            AP.rattacher(self.desk, AUTRE, a['id'], {'version': a['version'], 'dossier': MID})    # ancien dossier interdit
        b = self.consigner(dossier=MID)
        with self.assertRaisesRegex(Stop, 'introuvable'):
            AP.rattacher(self.desk, AUTRE, b['id'], {'version': b['version'], 'dossier': MID2})   # nouveau dossier interdit


if __name__ == '__main__':
    unittest.main()
