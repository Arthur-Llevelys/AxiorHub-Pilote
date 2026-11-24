"""5.6.109, lot H (F09) : discussions organisées et partagées — contrats de droits, renommage concurrent, préférences et groupes
personnels, partage nominatif Lecteur / Participant, révocation (liste, lecture, recherche, export), politique de l'administrateur, corbeille,
rattachement et changement de contexte. Chaque garde-fou de droits a un test qui échouerait s'il était retiré (mutation en mémoire).

Services réels (``discussions56109``, ``historique56109``, ``integration``, ``membres5650``, ``acces5697``) ; aucun modèle, aucun réseau.
"""
import io
import json
import unittest
from unittest.mock import patch

from agent import discussions56109 as D, historique56109 as H, membres5650
from agent.common import Stop
from test_v56109_historique import ADMIN, BERNARD, DURAND, MARTIN, PREFIX, Base


class Commun(Base):
    def setUp(self):
        super().setUp()
        self.n = 0

    def cle(self):
        self.n += 1
        return 'cle-h109-essai-%04d' % self.n

    def fil(self, qui=DURAND, matter='DOS-001', question='Analyse du bail ZORVAL'):
        tid = self.demander(qui, question, matter)['thread_id']
        self.d.db.execute("UPDATE assistant_messages SET status='done' WHERE thread_id=?", (tid,))
        self.d.db.execute("UPDATE jobs SET status='done' WHERE id IN (SELECT job_id FROM assistant_messages WHERE thread_id=?)", (tid,))
        self.d.db.commit()
        return tid

    def rev(self, tid):
        return self.d.db.execute('SELECT revision FROM assistant_thread_owners109 WHERE thread_id=?', (tid,)).fetchone()[0]

    def partager(self, tid, qui, role='viewer', par=DURAND):
        return D.partager(self.d, par, tid, qui[0], role, self.rev(tid), self.cle())

    def post(self, route, data, qui=DURAND, csrf='test-csrf'):
        raw = json.dumps(data).encode()
        env = {'REQUEST_METHOD': 'POST', 'HTTP_ORIGIN': self.auth['origin'], 'HTTP_X_CSRF_TOKEN': csrf, 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw), 'HTTP_X_AXIORHUB_ROLE': qui[1], 'HTTP_X_AXIORHUB_USER': qui[0]}
        out = D.api(env, self.d, self.auth, PREFIX, D.PREFIXE + route, {}, 'POST')
        out['json'] = json.loads(out['body'])
        out['entetes'] = dict(out.get('headers', []))
        return out


class Contrats(Commun):
    def test_F09_proprietaire_lecteur_participant_n_ont_pas_les_memes_droits(self):
        f = {'owner_id': 'U1', 'deleted_at': None, 'references': ('matter:DOS-001',)}
        pol, oui = dict(D.POLITIQUE_DEFAUT), (lambda r: True)
        for action in ('lire', 'continuer', 'renommer', 'rattacher', 'partager', 'supprimer', 'contexte'):
            self.assertTrue(D.verifier(action, 'U1', f, pol, None, oui))
        for action in ('lire', 'epingler', 'grouper', 'archiver', 'masquer'):
            self.assertTrue(D.verifier(action, 'U2', f, pol, 'viewer', oui))
        with self.assertRaises(Stop):
            D.verifier('continuer', 'U2', f, pol, 'viewer', oui)
        self.assertTrue(D.verifier('continuer', 'U2', f, pol, 'contributor', oui))
        for action in ('renommer', 'supprimer', 'partager', 'rattacher', 'revoquer', 'contexte'):
            with self.assertRaises(Stop):
                D.verifier(action, 'U2', f, pol, 'contributor', oui)
        for qui in ('U2', 'administrateur'):          # aucun droit de lecture implicite, même pour un administrateur
            with self.assertRaises(Stop):
                D.verifier('lire', qui, f, pol, None, oui)
        with self.assertRaises(Stop):
            D.verifier('lire', 'U1', f, pol, None, lambda r: False)
        self.assertEqual(D.titre_valide('  Analyse contrat  '), 'Analyse contrat')
        for v in ('', 'A' * 121, 'Titre\ncaché', 42):
            with self.assertRaises(Stop):
                D.titre_valide(v)

    def test_F09_administrateur_sans_acces_au_contenu_prive(self):
        tid = self.fil(DURAND)
        with self.assertRaises(Stop):
            H.messages(self.d, ADMIN, tid)
        self.assertNotIn(tid, [x['id'] for x in H.fils(self.d, ADMIN)['items']])


class Renommer(Commun):
    def test_F09_renommage_proprietaire_et_revision_concurrente(self):
        tid = self.fil()
        r = self.rev(tid)
        out = D.renommer(self.d, DURAND, tid, 'Bail ZORVAL — congé', r, self.cle())
        self.assertEqual(out['revision'], r + 1)
        with self.assertRaisesRegex(Stop, 'conflit_de_revision'):              # second poste, ancienne révision
            D.renommer(self.d, DURAND, tid, 'Autre titre', r, self.cle())
        self.assertEqual(self.d.db.execute('SELECT title FROM assistant_threads WHERE id=?', (tid,)).fetchone()[0], 'Bail ZORVAL — congé')
        self.partager(tid, MARTIN, 'contributor')
        with self.assertRaisesRegex(Stop, 'action_interdite'):
            D.renommer(self.d, MARTIN, tid, 'Titre du participant', self.rev(tid), self.cle())
        with self.assertRaisesRegex(Stop, 'introuvable'):
            D.renommer(self.d, BERNARD, tid, 'Titre d’un tiers', self.rev(tid), self.cle())
        # le texte de la consigne et des réponses ne change jamais
        self.assertEqual(self.d.db.execute("SELECT content FROM assistant_messages WHERE thread_id=? AND role='user'", (tid,)).fetchone()[0], 'Analyse du bail ZORVAL')

    def test_F09_idempotence_et_routes(self):
        tid = self.fil()
        data = {'id': tid, 'titre': 'Titre A', 'revision': self.rev(tid), 'cle': 'cle-fixe-0001'}
        a = self.post('renommer', data)
        b = self.post('renommer', data)
        self.assertEqual(a['status'], '200 OK')
        self.assertTrue(b['json'].get('rejoue'))
        c = self.post('renommer', {**data, 'titre': 'Titre B'})
        self.assertEqual(c['status'], '409 Conflict')
        self.assertEqual(self.post('renommer', {**data, 'cle': 'cle-fixe-0002'}, csrf='mauvais')['status'], '403 Forbidden')
        self.assertEqual(a['entetes']['Cache-Control'], 'private, no-store')
        autre, absent = self.post('renommer', {**data, 'cle': 'cle-fixe-0003'}, qui=MARTIN), self.post('renommer', {**data, 'id': 'c' * 32, 'cle': 'cle-fixe-0004'}, qui=MARTIN)
        self.assertEqual((autre['status'], autre['json']), (absent['status'], absent['json']))
        self.assertNotIn('Titre A', json.dumps(autre['json']))


class Preferences(Commun):
    def test_F09_epingle_archive_masque_groupe_personnels(self):
        tid = self.fil()
        self.partager(tid, MARTIN)
        D.preferences(self.d, MARTIN, tid, {'pinned': True}, self.cle())
        self.assertIn(tid, [x['id'] for x in H.fils(self.d, MARTIN)['epinglees']])
        self.assertNotIn(tid, [x['id'] for x in H.fils(self.d, DURAND)['epinglees']])      # l'épingle de l'un ne touche pas l'autre
        D.preferences(self.d, MARTIN, tid, {'archived': True}, self.cle())
        self.assertNotIn(tid, [x['id'] for x in H.fils(self.d, MARTIN)['items'] + H.fils(self.d, MARTIN)['epinglees']])
        self.assertIn(tid, [x['id'] for x in H.fils(self.d, MARTIN, {'archives': '1'})['items']])
        self.assertIn(tid, [x['id'] for x in H.fils(self.d, DURAND)['items']])
        D.preferences(self.d, MARTIN, tid, {'hidden': True}, self.cle())
        self.assertNotIn(tid, [x['id'] for x in H.fils(self.d, MARTIN, {'archives': '1'})['items']])
        self.assertEqual(len(H.messages(self.d, DURAND, tid)['items']), 1)                   # rien n'est supprimé
        with self.assertRaises(Stop):
            D.preferences(self.d, MARTIN, tid, {'pinned': 'oui'}, self.cle())

    def test_F09_groupes_personnels_suppression_sans_perte(self):
        tid = self.fil()
        self.partager(tid, MARTIN)
        g = D.groupe_creer(self.d, DURAND, 'Contentieux', self.cle())
        D.preferences(self.d, DURAND, tid, {'group_id': g['id']}, self.cle())
        self.assertEqual([x['id'] for x in H.fils(self.d, DURAND, {'groupe': g['id']})['items']], [tid])
        with self.assertRaisesRegex(Stop, 'groupe_inconnu'):                                  # groupe d'une autre personne
            D.preferences(self.d, MARTIN, tid, {'group_id': g['id']}, self.cle())
        with self.assertRaises(Stop):
            H.fils(self.d, MARTIN, {'groupe': g['id']})
        D.groupe_modifier(self.d, DURAND, g['id'], 'Conseil', None, 1, self.cle())
        with self.assertRaisesRegex(Stop, 'conflit_de_revision'):
            D.groupe_modifier(self.d, DURAND, g['id'], 'Prospects', None, 1, self.cle())
        out = D.groupe_supprimer(self.d, DURAND, g['id'], 2, self.cle())
        self.assertEqual(out['discussions_sans_groupe'], 1)
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM assistant_threads WHERE id=?', (tid,)).fetchone()[0], 1)
        self.assertEqual([x['id'] for x in H.fils(self.d, DURAND, {'groupe': 'aucun'})['items']], [tid])


class Partage(Commun):
    def test_F09_partage_lecteur_puis_participant_puis_revocation(self):
        tid = self.fil()
        self.reponse(tid, 'Le congé doit être délivré six mois à l’avance.')
        with self.assertRaises(Stop):
            H.messages(self.d, MARTIN, tid)
        self.partager(tid, MARTIN, 'viewer')
        self.assertEqual(len(H.messages(self.d, MARTIN, tid)['items']), 2)
        self.assertFalse(H.messages(self.d, MARTIN, tid)['thread']['peut_continuer'])
        self.assertEqual([x['id'] for x in H.recherche(self.d, MARTIN, 'congé')['items']], [tid])
        self.partager(tid, MARTIN, 'contributor')
        self.assertTrue(H.messages(self.d, MARTIN, tid)['thread']['peut_continuer'])
        D.revoquer(self.d, DURAND, tid, MARTIN[0], self.rev(tid), self.cle())
        # un accès révoqué cesse dans la liste, la lecture, la recherche, l'export et la continuation
        self.assertNotIn(tid, [x['id'] for x in H.fils(self.d, MARTIN)['items']])
        with self.assertRaises(Stop):
            H.messages(self.d, MARTIN, tid)
        self.assertEqual(H.recherche(self.d, MARTIN, 'congé')['total'], 0)
        with self.assertRaises(Stop):
            H.exporter(self.d, MARTIN, tid, 'json')
        with self.assertRaisesRegex(Stop, 'conversation_autre_contexte'):
            self.demander(MARTIN, 'Après révocation', thread=tid)

    def test_F09_mutation_revocation_sans_effet_si_le_controle_de_participation_est_retire(self):
        tid = self.fil()
        with patch.object(H, '_portee', lambda cab, compte: (' FROM assistant_threads t JOIN assistant_thread_owners109 o ON o.thread_id=t.id '
                                                               'LEFT JOIN assistant_thread_preferences109 p ON p.thread_id=t.id AND p.user_id=? '
                                                               'LEFT JOIN assistant_thread_members109 mb ON mb.thread_id=t.id AND mb.user_id=? WHERE ?=? AND ?=?',
                                                               [compte, compte, cab, cab, compte, compte])):
            self.assertEqual(len(H.messages(self.d, MARTIN, tid)['items']), 1)      # sans la portée, un tiers lirait le fil
        with self.assertRaises(Stop):
            H.messages(self.d, MARTIN, tid)

    def test_F09_partage_refuse_si_une_source_n_est_pas_accessible_au_destinataire(self):
        membres5650.set_reserve(self.state, 'DOS-002', [DURAND[0]])
        tid = self.fil(DURAND, 'DOS-002')
        with self.assertRaisesRegex(Stop, 'source_non_partageable'):
            self.partager(tid, MARTIN)
        t2 = self.fil(DURAND, 'DOS-001')
        self.reponse(t2, 'Réponse appuyée sur une pièce ZORVAL.', [{'id': 's1', 'matter': 'DOS-002'}])
        with self.assertRaisesRegex(Stop, 'source_non_partageable'):
            self.partager(t2, MARTIN)
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM assistant_thread_members109').fetchone()[0], 0)
        with patch.object(D, '_peut_lire_ref', lambda *a: True):                      # mutation : sans le contrôle des sources, le partage passerait
            self.partager(t2, MARTIN)
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM assistant_thread_members109').fetchone()[0], 1)

    def test_F09_provenance_illisible_bloque_le_partage(self):
        tid = self.fil()
        self.d.db.execute("INSERT INTO assistant_messages(thread_id,role,content,status,sources,created,updated) VALUES(?,?,?,?,?,?,?)", (tid, 'assistant', 'x', 'done', '{abîmé', 'x', 'x'))
        self.d.db.commit()
        with self.assertRaisesRegex(Stop, 'source_non_partageable'):
            self.partager(tid, MARTIN)

    def test_F09_destinataire_et_role_controles(self):
        tid = self.fil()
        for qui, role, code in ((('inconnu@example.test', 'avocat'), 'viewer', 'destinataire_non_autorise'), (DURAND, 'viewer', 'partage_invalide'),
                                (MARTIN, 'owner', 'partage_invalide')):
            with self.assertRaisesRegex(Stop, code):
                D.partager(self.d, DURAND, tid, qui[0], role, self.rev(tid), self.cle())
        self.partager(tid, MARTIN, 'contributor')
        with self.assertRaisesRegex(Stop, 'action_interdite'):                        # un participant ne partage pas
            self.partager(tid, BERNARD, par=MARTIN)

    def test_F09_partage_desactive_par_la_politique(self):
        tid = self.fil()
        D.regler_politique(self.d, ADMIN, {'internal_share_enabled': False}, 0, self.cle())
        with self.assertRaisesRegex(Stop, 'partage_interdit_par_politique'):
            self.partager(tid, MARTIN)

    def test_F09_membres_et_candidats(self):
        tid = self.fil()
        m = D.membres(self.d, DURAND, tid)
        self.assertIn(MARTIN[0], [c['user_id'] for c in m['candidats']])
        self.assertNotIn(DURAND[0], [c['user_id'] for c in m['candidats']])
        self.partager(tid, MARTIN)
        vu = D.membres(self.d, MARTIN, tid)
        self.assertNotIn('candidats', vu)
        self.assertEqual(vu['membres'][0]['role_libelle'], 'Lecteur')


class Suppression(Commun):
    def test_F09_suppression_politique_droit_propriete(self):
        tid = self.fil()
        self.partager(tid, MARTIN, 'contributor')
        with self.assertRaisesRegex(Stop, 'confirmation_requise'):
            D.supprimer(self.d, DURAND, tid, self.rev(tid), '', self.cle())
        with self.assertRaisesRegex(Stop, 'action_interdite'):
            D.supprimer(self.d, MARTIN, tid, self.rev(tid), D.CONFIRMATION, self.cle())
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            D.regler_politique(self.d, DURAND, {'user_delete_enabled': False}, 0, self.cle())
        D.regler_politique(self.d, ADMIN, {'user_delete_enabled': False}, 0, self.cle())
        with self.assertRaisesRegex(Stop, 'suppression_interdite_par_politique'):
            D.supprimer(self.d, DURAND, tid, self.rev(tid), D.CONFIRMATION, self.cle())
        t_admin = self.fil(ADMIN)                                                       # même un propriétaire administrateur
        with self.assertRaisesRegex(Stop, 'suppression_interdite_par_politique'):
            D.supprimer(self.d, ADMIN, t_admin, self.rev(t_admin), D.CONFIRMATION, self.cle())
        D.regler_politique(self.d, ADMIN, {'user_delete_enabled': True}, 1, self.cle())
        t_assist = self.fil(BERNARD)                                                    # assistant : sans le droit individuel « supprimer »
        with self.assertRaisesRegex(Stop, 'suppression_interdite_par_droit'):
            D.supprimer(self.d, BERNARD, t_assist, self.rev(t_assist), D.CONFIRMATION, self.cle())
        out = D.supprimer(self.d, DURAND, tid, self.rev(tid), D.CONFIRMATION, self.cle())
        self.assertTrue(out['corbeille'])
        for qui in (DURAND, MARTIN):
            self.assertNotIn(tid, [x['id'] for x in H.fils(self.d, qui)['items']])
            with self.assertRaises(Stop):
                H.messages(self.d, qui, tid)
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM assistant_messages WHERE thread_id=?', (tid,)).fetchone()[0], 1)   # suppression logique
        self.assertIn(tid, [x['id'] for x in D.corbeille(self.d, DURAND)['items']])
        self.assertEqual(D.corbeille(self.d, MARTIN)['items'], [])

    def test_F09_mutation_politique_retiree_la_suppression_passerait(self):
        tid = self.fil()
        D.regler_politique(self.d, ADMIN, {'user_delete_enabled': False}, 0, self.cle())
        with patch.object(D, 'politique', lambda desk: dict(D.POLITIQUE_DEFAUT)):
            self.assertTrue(D.supprimer(self.d, DURAND, tid, self.rev(tid), D.CONFIRMATION, self.cle())['corbeille'])

    def test_F09_travail_actif_refuse_la_suppression(self):
        out = self.demander(DURAND)
        tid = out['thread_id']
        with self.assertRaisesRegex(Stop, 'travail_en_cours'):
            D.supprimer(self.d, DURAND, tid, self.rev(tid), D.CONFIRMATION, self.cle())
        r = self.post('supprimer', {'id': tid, 'revision': self.rev(tid), 'confirmation': D.CONFIRMATION, 'cle': self.cle()})
        self.assertEqual(r['status'], '409 Conflict')

    def test_F09_restauration_revoit_les_participants(self):
        tid = self.fil()
        self.partager(tid, MARTIN, 'viewer')
        self.partager(tid, BERNARD, 'viewer')
        D.supprimer(self.d, DURAND, tid, self.rev(tid), D.CONFIRMATION, self.cle())
        self.d.db.execute('DELETE FROM membres5650 WHERE owner=?', (BERNARD[0],))          # compte désactivé entre-temps
        self.d.db.commit()
        out = D.restaurer(self.d, DURAND, tid, self.rev(tid), self.cle())
        self.assertEqual(out['acces_retires'], 1)
        self.assertEqual(len(H.messages(self.d, MARTIN, tid)['items']), 1)
        with self.assertRaises(Stop):
            H.messages(self.d, BERNARD, tid)
        with self.assertRaisesRegex(Stop, 'pas_dans_la_corbeille'):
            D.restaurer(self.d, DURAND, tid, self.rev(tid), self.cle())

    def test_F09_routes_politique_reservees(self):
        self.assertFalse(D.allowed('avocat', 'POST', D.PREFIXE + 'politique'))
        self.assertTrue(D.allowed('administrateur', 'POST', D.PREFIXE + 'politique'))
        self.assertFalse(D.allowed('avocat', 'GET', D.PREFIXE + 'renommer'))
        r = self.post('politique', {'valeurs': {'user_delete_enabled': False}, 'revision': 0, 'cle': self.cle()}, qui=DURAND)
        self.assertEqual(r['status'], '403 Forbidden')
        with self.assertRaisesRegex(Stop, 'politique_invalide'):
            D.regler_politique(self.d, ADMIN, {'user_delete_enabled': 'non'}, 0, self.cle())


class Contexte(Commun):
    def test_F09_rattacher_sans_import_ni_partage(self):
        tid = self.fil()
        out = D.rattacher(self.d, DURAND, tid, 'matter', 'DOS-002', '', self.rev(tid), self.cle())
        self.assertEqual(out['lien']['role'], 'association')
        self.assertEqual(D.liens(self.d, DURAND, tid)[0]['source_id'], 'DOS-002')
        with self.assertRaisesRegex(Stop, 'type_non_pris_en_charge'):
            D.rattacher(self.d, DURAND, tid, 'deliverable', 'L1', '', self.rev(tid), self.cle())
        with self.assertRaisesRegex(Stop, 'reference_indisponible'):
            D.rattacher(self.d, DURAND, tid, 'matter', 'DOS-404', '', self.rev(tid), self.cle())
        autre = self.fil(MARTIN)
        with self.assertRaisesRegex(Stop, 'reference_indisponible'):                  # discussion d'un autre compte
            D.rattacher(self.d, DURAND, tid, 'ai_thread', autre, '', self.rev(tid), self.cle())
        self.d.db.execute("INSERT INTO assistant_thread_links109 VALUES(?,?,?,?,?,?,?,?)", (tid, 'cabinet', 'matter', 'DOS-002', '', 'used_context', DURAND[0], 'x'))
        self.d.db.commit()
        D.detacher(self.d, DURAND, tid, 'matter', 'DOS-002', '', self.rev(tid), self.cle())
        roles = [l['source_role'] for l in D.liens(self.d, DURAND, tid)]
        self.assertEqual(roles, ['used_context'])                                       # la provenance d'une réponse reste
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM assistant_thread_members109').fetchone()[0], 0)

    def test_F09_rattacher_un_dossier_interdit_a_un_participant_refuse(self):
        tid = self.fil()
        self.partager(tid, MARTIN)
        membres5650.set_reserve(self.state, 'DOS-002', [DURAND[0]])
        with self.assertRaisesRegex(Stop, 'source_non_partageable'):
            D.rattacher(self.d, DURAND, tid, 'matter', 'DOS-002', '', self.rev(tid), self.cle())

    def test_F09_changer_de_contexte_garde_la_provenance(self):
        tid = self.fil(DURAND, 'DOS-001')
        out = D.changer_contexte(self.d, DURAND, tid, 'DOS-002', self.rev(tid), self.cle())
        self.assertEqual(out['matter'], 'DOS-002')
        ctx = [dict(r) for r in self.d.db.execute('SELECT revision, matter, from_message_id FROM assistant_thread_contexts109 WHERE thread_id=? ORDER BY revision', (tid,))]
        self.assertEqual([c['matter'] for c in ctx], ['DOS-001', 'DOS-002'])
        with self.assertRaisesRegex(Stop, 'conversation_autre_contexte'):              # ancien contexte refusé, pas mélangé
            self.demander(DURAND, 'Suite dans l’ancien dossier', 'DOS-001', thread=tid)
        self.demander(DURAND, 'Suite dans le nouveau dossier', 'DOS-002', thread=tid)
        with self.assertRaisesRegex(Stop, 'travail_en_cours'):
            D.changer_contexte(self.d, DURAND, tid, 'DOS-001', self.rev(tid), self.cle())
        t2 = self.fil()
        self.partager(t2, MARTIN)
        membres5650.set_reserve(self.state, 'DOS-002', [DURAND[0]])
        with self.assertRaisesRegex(Stop, 'source_non_partageable'):
            D.changer_contexte(self.d, DURAND, t2, 'DOS-002', self.rev(t2), self.cle())


class Creer(Commun):
    def test_F09_nouvelle_discussion_nommee_sans_message_ni_travail(self):
        travaux = self.d.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        out = D.creer(self.d, DURAND, 'Recherche prescription', 'DOS-001', self.cle())
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], travaux)
        self.assertIn(out['thread_id'], [x['id'] for x in H.fils(self.d, DURAND)['items']])
        membres5650.set_reserve(self.state, 'DOS-002', [MARTIN[0]])
        with self.assertRaisesRegex(Stop, 'dossier_inconnu'):
            D.creer(self.d, DURAND, 'Interdit', 'DOS-002', self.cle())
        self.demander(DURAND, 'Premier message', 'DOS-001', thread=out['thread_id'])


if __name__ == '__main__':
    unittest.main()
