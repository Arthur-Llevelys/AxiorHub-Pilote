"""5.6.109 (lot G3, G05) : « Suites et exécution » dans la fiche contentieux (``suites56109``, montage ``fiche_ui5698``).

Décision ou accord désignés exactement ; dispositif et clauses distingués des motifs, prétentions citées et jurisprudence ; obligations
confirmées par l'avocat ; aucune date inventée ni calculée (source ou moteur de délais existant, sinon « À vérifier ») ; actions reliées
aux tâches existantes (``agenda5697.creer``) et accomplies sur preuve ; échéancier lié aux mouvements et factures du lot G1, paiement
partiel compté une fois. Données fictives (DURAND, ZORVAL, DOS-001, RG 24/01234) ; aucun réseau, aucun modèle.
"""
from datetime import timedelta
import io
import json
import sqlite3
import unittest
from unittest.mock import patch

from agent import facturation5653 as fact, limitation500, membres5650, suites56109 as S
from agent.common import Stop
from test_v5697_gestion import ASSISTANT, AUTRE, AVOCAT, MID
from test_v56109_finances import Finances
from test_v56109_prefacturation import JETON, ORIGINE

IA = ('agent_cabinet', 'agent')
JUGEMENT = ('TRIBUNAL JUDICIAIRE DE VALBRUNE — RG 24/01234. Jugement du 10 novembre 2026, signifié le 12 novembre 2026.\n'
            'MOTIFS. M. DURAND sollicite la condamnation de la société ZORVAL à lui payer 20 000 euros.\n'
            'Selon Cass. civ. 1re, arrêt du 3 mars 2010, le juge peut condamner à verser des intérêts.\n'
            'Le tribunal estime que la société ZORVAL doit être condamnée à payer une indemnité réduite.\n'
            'PAR CES MOTIFS,\nCondamne la société ZORVAL à payer à M. DURAND la somme de 1 500,00 euros ;\n'
            'Ordonne la remise des bulletins de paie rectifiés ;\nDéboute M. DURAND du surplus de ses demandes.\n')
PROTOCOLE = ('PROTOCOLE TRANSACTIONNEL entre DURAND et ZORVAL.\nArticle 1 — Objet. Les parties mettent fin au litige.\n'
             'Article 2 — ZORVAL versera à DURAND la somme de 3 000 euros en deux échéances.\nArticle 3 — Calendrier : 15 janvier 2027 et 15 février 2027.\n')


class Socle(Finances):
    def suite(self, kind='decision', texte=JUGEMENT, **plus):
        s = S.creer(self.desk, AVOCAT, {'dossier': MID, 'source_kind': kind, 'document': '/Dossiers/DURAND/Jugement.pdf' if kind == 'decision' else
                                        '/Dossiers/DURAND/Protocole.pdf', 'document_version': 'sha-fictif-1', 'date_acte': '2026-11-10', **plus})['suite']
        S.proposer(self.desk, AVOCAT, {'suite': s, 'texte': texte})
        return s

    def obligations(self, s):
        return [o for x in S.vue(self.desk, AVOCAT, MID)['suites'] if x['id'] == s for o in x['obligations']]

    def ob(self, s, debut):
        return next(o for o in self.obligations(s) if o['passage'].startswith(debut))


class G05Sources(Socle):
    def test_G05_dispositif_distingue_des_motifs_pretentions_et_jurisprudence(self):
        s = self.suite()
        types = {o['passage'][:20]: o['passage_type'] for o in self.obligations(s)}
        self.assertEqual(self.ob(s, 'Condamne la société')['passage_type'], 'dispositif')
        self.assertEqual(self.ob(s, 'Ordonne la remise')['passage_type'], 'dispositif')
        self.assertIn('pretention', types.values())                                          # « sollicite la condamnation » : prétention citée
        self.assertIn('antecedent', types.values())                                          # jurisprudence citée
        self.assertIn('motifs', types.values())
        motif = next(o for o in self.obligations(s) if o['passage_type'] == 'motifs')
        with self.assertRaisesRegex(Stop, 'passage_non_executoire'):
            S.confirmer(self.desk, AVOCAT, {'obligation': motif['id'], 'version': motif['version'], 'montant': '20000'})

    def test_G05_mutation_contrainte_un_motif_ne_devient_jamais_une_obligation(self):
        s = self.suite()
        motif = next(o for o in self.obligations(s) if o['passage_type'] == 'motifs')
        with self.assertRaises(sqlite3.IntegrityError):                                    # même en contournant le service
            self.desk.db.execute("UPDATE suites_obligations56109 SET statut='confirmee' WHERE id=?", (motif['id'],))
        self.desk.db.rollback()

    def test_G05_aucune_date_inventee_ni_calculee(self):
        s = self.suite()
        o = self.ob(s, 'Condamne la société')
        out = S.confirmer(self.desk, AVOCAT, {'obligation': o['id'], 'version': o['version'], 'debiteur': 'ZORVAL', 'creancier': 'DURAND', 'montant': '1 500,00'})
        self.assertEqual(out['echeance_statut'], 'a_verifier')                                # signification lue dans l'acte : jamais un point de départ
        o = self.ob(s, 'Condamne la société')
        self.assertEqual((o['echeance'], o['montant_minor'], o['devise']), ('', 150000, 'EUR'))
        p = self.ob(s, 'Ordonne la remise')
        with self.assertRaises(S.Refus) as ctx:
            S.confirmer(self.desk, AVOCAT, {'obligation': p['id'], 'version': p['version'], 'echeance': '2026-12-12'})
        self.assertIn('echeance_source', ctx.exception.detail)                                # aucune date sans source
        S.confirmer(self.desk, AVOCAT, {'obligation': p['id'], 'version': p['version'], 'echeance': '2026-12-12', 'echeance_source': 'Dispositif, p. 4 : « sous un mois » — date retenue par l’avocat'})
        self.assertEqual(self.ob(s, 'Ordonne la remise')['echeance_statut'], 'confirmee')

    def test_G05_mutation_contrainte_date_sans_source(self):
        s = self.suite()
        o = self.ob(s, 'Condamne la société')
        with self.assertRaises(sqlite3.IntegrityError):
            self.desk.db.execute("UPDATE suites_obligations56109 SET echeance='2027-01-01' WHERE id=?", (o['id'],))
        self.desk.db.rollback()

    def test_G05_proposition_d_un_modele_sourcee_et_type_recalcule(self):
        s = S.creer(self.desk, AVOCAT, {'dossier': MID, 'source_kind': 'decision', 'document': '/Dossiers/DURAND/J2.pdf', 'document_version': 'v1'})['suite']
        out = S.proposer_lignes(self.desk, IA, s, JUGEMENT, [
            {'passage': 'Le tribunal estime que la société ZORVAL doit être condamnée à payer une indemnité réduite.', 'type': 'dispositif'},
            {'passage': 'Condamne la société ZORVAL à payer à M. DURAND la somme de 10 000,00 euros', 'type': 'dispositif'}])   # absent de l'acte
        self.assertEqual((out['ajoutees'], out['rejetees_hors_acte']), (1, 1))
        o = self.obligations(s)[0]
        self.assertEqual(o['passage_type'], 'motifs')                                         # l'étiquette du modèle n'est pas reprise
        self.assertEqual(o['origine'], 'modele')
        self.assertEqual(o['statut'], 'proposee')
        with self.assertRaisesRegex(Stop, 'verification_humaine_requise'):
            S.confirmer(self.desk, IA, {'obligation': o['id'], 'version': o['version']})

    def test_G05_mutation_sans_recalcul_le_motif_serait_confirmable(self):
        s = S.creer(self.desk, AVOCAT, {'dossier': MID, 'source_kind': 'decision', 'document': '/Dossiers/DURAND/J3.pdf', 'document_version': 'v1'})['suite']
        with patch.object(S, 'type_par_position', lambda t, k, p: 'dispositif'):              # garde retirée : l'étiquette ferait foi
            S.proposer_lignes(self.desk, IA, s, JUGEMENT, [{'passage': 'Le tribunal estime que la société ZORVAL doit être condamnée à payer une indemnité réduite.'}])
        o = self.obligations(s)[0]
        self.assertEqual(S.confirmer(self.desk, AVOCAT, {'obligation': o['id'], 'version': o['version']})['statut'], 'confirmee')

    def test_G05_accord_clauses_et_notification_prouvee(self):
        with self.assertRaises(S.Refus):
            S.creer(self.desk, AVOCAT, {'dossier': MID, 'source_kind': 'accord', 'document': '/Dossiers/DURAND/P.pdf', 'document_version': 'v1',
                                        'notification_date': '2026-11-12'})                    # date de notification sans preuve
        s = self.suite('accord', PROTOCOLE)
        self.assertEqual([o['passage_type'] for o in self.obligations(s)], ['clause'])
        self.assertEqual(S.creer(self.desk, AVOCAT, {'dossier': MID, 'source_kind': 'accord', 'document': '/Dossiers/DURAND/Protocole.pdf',
                                                     'document_version': 'sha-fictif-1'})['suite'], s)   # même acte, même version : même suite


class G05Actions(Socle):
    def test_G05_action_reliee_a_une_tache_du_registre_commun_et_accomplie_sur_preuve(self):
        s = self.suite()
        o = self.ob(s, 'Ordonne la remise')
        S.confirmer(self.desk, AVOCAT, {'obligation': o['id'], 'version': o['version'], 'echeance': '2026-12-12', 'echeance_source': 'Dispositif p. 4 (essai)'})
        out = S.action(self.desk, AVOCAT, {'suite': s, 'obligation': o['id'], 'kind': 'transmettre_commissaire', 'libelle': 'Transmettre le jugement au commissaire de justice',
                                           'creer_tache': True})
        t = self.desk.db.execute('SELECT * FROM tasks WHERE id=?', (out['tache'],)).fetchone()
        self.assertEqual((t['matter'], t['due'], t['status']), (MID, '2026-12-12', 'open'))
        again = S.action(self.desk, AVOCAT, {'suite': s, 'obligation': o['id'], 'kind': 'transmettre_commissaire', 'libelle': 'Transmettre le jugement au commissaire de justice',
                                             'creer_tache': True})
        self.assertTrue(again['deja'])
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM tasks WHERE title='Transmettre le jugement au commissaire de justice'").fetchone()[0], 1)
        a = next(x for x in S.vue(self.desk, AVOCAT, MID)['suites'][0]['actions'])
        with self.assertRaises(S.Refus):
            S.accomplir(self.desk, AVOCAT, {'action': a['id'], 'version': a['version'], 'preuve': ''})
        S.accomplir(self.desk, AVOCAT, {'action': a['id'], 'version': a['version'], 'preuve': 'Courriel au commissaire de justice du 20/11/2026 (pièce 12)'})
        self.assertEqual(S.vue(self.desk, AVOCAT, MID)['suites'][0]['actions'][0]['etat'], 'accomplie')

    def test_G05_echeance_non_confirmee_jamais_recopiee_dans_la_tache(self):
        s = self.suite()
        o = self.ob(s, 'Condamne la société')
        S.confirmer(self.desk, AVOCAT, {'obligation': o['id'], 'version': o['version'], 'montant': '1500'})
        out = S.action(self.desk, AVOCAT, {'suite': s, 'obligation': o['id'], 'kind': 'suivre_paiement', 'creer_tache': True})
        self.assertEqual(self.desk.db.execute('SELECT due FROM tasks WHERE id=?', (out['tache'],)).fetchone()[0], '')

    def test_G05_lien_vers_le_moteur_de_delais_existant_et_son_journal(self):
        s = self.suite()
        lim = limitation500.create(self.desk, MID, 'droit_commun_2224', '2026-11-10', source='Jugement (essai)')
        o = self.ob(s, 'Condamne la société')
        S.confirmer(self.desk, AVOCAT, {'obligation': o['id'], 'version': o['version'], 'echeance_lien_kind': 'limitation500', 'echeance_lien_id': lim['id']})
        o = self.ob(s, 'Condamne la société')
        self.assertEqual(o['echeance'], '')                                                   # rien n'est recopié
        self.assertEqual(o['echeance_liee']['date'], lim['due'])
        self.assertTrue(self.desk.db.execute("SELECT 1 FROM limitation500_journal WHERE limitation_id=? AND action='lien_suite56109'", (lim['id'],)).fetchone())
        with self.assertRaises(S.Refus):
            S.confirmer(self.desk, AVOCAT, {'obligation': self.ob(s, 'Ordonne la remise')['id'], 'version': self.ob(s, 'Ordonne la remise')['version'],
                                            'echeance_lien_kind': 'limitation500', 'echeance_lien_id': 'inconnu'})


class G05Echeancier(Socle):
    def test_G05_paiement_partiel_compte_une_fois_par_les_fonctions_du_lot_G1(self):
        s = self.suite('accord', PROTOCOLE)
        d = self.facture('750')
        fact.record_payment(self.desk, *AVOCAT, d['id'], '300', '2026-11-10', 'virement', 'VIR-G05-0001')
        futur = (S._aujourdhui(self.desk) + timedelta(days=40)).isoformat()
        l1 = S.echeance(self.desk, AVOCAT, {'suite': s, 'jour': futur, 'jour_source': 'Protocole, article 3', 'montant': '900,00'})['ligne']
        l2 = S.echeance(self.desk, AVOCAT, {'suite': s, 'jour': futur, 'jour_source': 'Protocole, article 3', 'montant': '900,00'})['ligne']
        lignes = {x['id']: x for x in S.vue(self.desk, AVOCAT, MID)['suites'][0]['echeancier']}
        S.lier(self.desk, AVOCAT, {'ligne': l1, 'version': lignes[l1]['version'], 'lien_kind': 'facture', 'lien_id': d['id']})
        with self.assertRaisesRegex(Stop, 'paiement_deja_compte'):
            S.lier(self.desk, AVOCAT, {'ligne': l2, 'version': lignes[l2]['version'], 'lien_kind': 'facture', 'lien_id': d['id']})
        lignes = {x['id']: x for x in S.vue(self.desk, AVOCAT, MID)['suites'][0]['echeancier']}
        self.assertEqual((lignes[l1]['statut'], lignes[l1]['recu_minor']), ('partiel', 30000))
        self.assertEqual(lignes[l2]['statut'], 'attendu')

    def test_G05_mutation_index_unique_un_mouvement_ne_se_lie_qu_une_fois(self):
        s = self.suite('accord', PROTOCOLE)
        m = self.mouvement(montant='3000')['mouvement']
        l1 = S.echeance(self.desk, AVOCAT, {'suite': s, 'jour': '2027-01-15', 'jour_source': 'Protocole, article 3', 'montant': '3000'})['ligne']
        l2 = S.echeance(self.desk, AVOCAT, {'suite': s, 'jour': '2027-02-15', 'jour_source': 'Protocole, article 3', 'montant': '3000'})['ligne']
        S.lier(self.desk, AVOCAT, {'ligne': l1, 'version': 1, 'lien_kind': 'mouvement', 'lien_id': m})
        self.assertEqual(S.vue(self.desk, AVOCAT, MID)['suites'][0]['echeancier'][0]['statut'], 'recu')
        with self.assertRaises(sqlite3.IntegrityError):                                    # même en contournant le service
            self.desk.db.execute("UPDATE suites_echeancier56109 SET lien_kind='mouvement', lien_id=? WHERE id=?", (m, l2))
        self.desk.db.rollback()

    def test_G05_date_d_echeancier_sans_source_refusee_et_retard_constate(self):
        s = self.suite('accord', PROTOCOLE)
        with self.assertRaises(S.Refus):
            S.echeance(self.desk, AVOCAT, {'suite': s, 'jour': '2027-01-15', 'jour_source': '', 'montant': '1500'})
        passe = (S._aujourdhui(self.desk) - timedelta(days=3)).isoformat()
        S.echeance(self.desk, AVOCAT, {'suite': s, 'jour': passe, 'jour_source': 'Protocole, article 3', 'montant': '1500'})
        self.assertEqual(S.vue(self.desk, AVOCAT, MID)['suites'][0]['echeancier'][0]['statut'], 'en_retard')
        avant = self.desk.db.execute('SELECT COUNT(*) FROM mouvements56109').fetchone()[0]
        S.vue(self.desk, AVOCAT, MID)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM mouvements56109').fetchone()[0], avant)   # aucun paiement, aucune écriture


class G05Ecran(Socle):
    def appel(self, route, data=None, acteur=AVOCAT, args=None):
        from agent import web440
        corps = json.dumps(data or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': acteur[0], 'HTTP_X_AXIORHUB_ROLE': acteur[1], 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON,
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps),
               'REQUEST_METHOD': 'GET' if data is None else 'POST'}
        out = web440.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm56109/operations/' + route, args or {}, env['REQUEST_METHOD'])
        return out['status'][:3], json.loads(out['body'])

    def test_G05_bloc_dans_la_fiche_et_droits(self):
        s = self.suite()
        html = S.montage_fiche(self.desk, AVOCAT, '/p', {'id': MID})
        self.assertIn('Suites et exécution', html)
        self.assertIn('Dispositif', html)
        self.assertIn('m56109/operations/suites/confirmer', html)
        membres5650.set_reserve(self.desk.c['state_dir'], MID, [AVOCAT[0]])
        self.assertEqual(S.montage_fiche(self.desk, AUTRE, '/p', {'id': MID}), '')           # dossier réservé : rien
        st, a = self.appel('suites', args={'dossier': MID}, acteur=AUTRE)
        st2, b = self.appel('suites', args={'dossier': 'DOS-INEXISTANT'})
        self.assertEqual((st, st2, a), ('404', '404', b))
        o = self.ob(s, 'Condamne la société')
        st, _ = self.appel('suites/confirmer', {'cle': 'cle-g05-0001', 'obligation': o['id'], 'version': o['version']}, acteur=ASSISTANT)
        self.assertEqual(st, '403')
        st, out = self.appel('suites/confirmer', {'cle': 'cle-g05-0002', 'obligation': o['id'], 'version': o['version'], 'montant': '1500'})
        self.assertEqual((st, out['statut']), ('200', 'confirmee'))

    def test_G05_fiche_juridiction_porte_le_bloc(self):
        from agent import fiche_ui5698
        self.suite()
        html = fiche_ui5698.juridiction_html(self.desk, AVOCAT, '/p', {'id': MID})
        self.assertIn('id="g3-suites"', html)


if __name__ == '__main__':
    unittest.main()
