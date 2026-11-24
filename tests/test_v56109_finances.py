"""5.6.109 (lot G1, G09) : fonds, frais et débours dans la vue financière existante du dossier (``gestion56109``, ``honoraires5697``,
``facturation5653``).

Honoraires, frais, débours et fonds de tiers distincts ; devises séparées (aucun total mélangé, aucune conversion) ; un règlement compté une
fois ; prévu distinct du constaté ; correction par mouvement compensateur ; débours qualifiés par l'avocat ; changement de coordonnées
bancaires versionné, masqué et « À vérifier » jusqu'à la vérification indépendante d'une personne habilitée (jamais l'IA). Aucun paiement.
Données fictives (DURAND, MARTIN, DOS-001, example.test, IBAN d'essai) ; aucun réseau, aucun modèle.
"""
from datetime import date
import io
import json
import unittest

from agent import facturation5653 as fact, gestion56109 as G, honoraires5697 as hon
from agent.common import Stop
from test_v5697_gestion import ADMIN, ASSISTANT, AUTRE, AVOCAT, MID, MID2
from test_v56109_prefacturation import JOUR, ORIGINE, JETON, Socle

IBAN = 'FR7630006000011234567890189'          # IBAN d'essai (exemple public de format), jamais un compte réel
IA = ('agent-ia', 'agent')


class Finances(Socle):
    def setUp(self):
        super().setUp()
        self.n = 0

    def cle(self):
        self.n += 1
        return 'cle-g09-%04d' % self.n

    def facture(self, ht='750'):
        d = fact.prepare(self.desk, *AVOCAT, 'facture', MID, {'sous_type': 'libre', 'date_prestation': JOUR, 'lignes': [
            {'nature': 'honoraires', 'libelle': 'Consultation (fictive)', 'quantite': '1', 'prix_unitaire_ht': ht}]}, '')
        d = fact.approve(self.desk, *AVOCAT, d['id'], d['version'])
        return fact.issue(self.desk, *AVOCAT, d['id'], 'emission-' + self.cle())

    def mouvement(self, acteur=AVOCAT, **d):
        base = {'cle': self.cle(), 'dossier': MID, 'nature': 'fonds_tiers', 'sens': 'entree', 'etat': 'constate', 'montant': '5000', 'devise': 'EUR', 'jour': JOUR,
                'contrepartie': 'MARTIN (fictif)'}
        base.update(d)
        return G.enregistrer_mouvement(self.desk, acteur, base)

    def vue(self, acteur=AVOCAT):
        return G.vue_financiere(self.desk, acteur, MID)


class G09Familles(Finances):
    def test_G09_fonds_de_tiers_jamais_des_honoraires(self):
        d = self.facture()
        fact.record_payment(self.desk, *AVOCAT, d['id'], '300', JOUR, 'virement', 'VIR-G09-0001')
        self.mouvement()                                                        # 5 000 € reçus pour le compte du client
        v = self.vue()
        self.assertEqual(v['familles']['honoraires']['EUR']['constate_entrees'], 30000)
        self.assertEqual(v['familles']['honoraires']['EUR']['prevu_entrees'], 60000)   # reste dû : prévu, pas encaissé
        self.assertEqual(v['familles']['fonds_tiers']['EUR']['solde_constate'], 500000)
        self.assertEqual(hon.indicateurs(self.desk, AVOCAT, MID)['encaisse']['ttc']['montant'], '300.00')   # l'encaissé ignore les fonds de tiers
        self.assertTrue(any('ni des honoraires' in m for m in v['mentions']))

    def test_G09_mutation_un_fonds_classe_en_honoraires_gonflerait_l_encaisse(self):
        d = self.facture()
        fact.record_payment(self.desk, *AVOCAT, d['id'], '300', JOUR, 'virement', 'VIR-G09-0002')
        m = self.mouvement()
        self.desk.db.execute("UPDATE mouvements56109 SET nature='honoraires' WHERE id=?", (m['mouvement'],))   # garde contournée (en base)
        self.desk.db.commit()
        self.assertEqual(self.vue()['familles']['honoraires']['EUR']['constate_entrees'], 530000)   # c'est la nature distincte qui l'en tient écarté

    def test_G09_devises_separees_sans_conversion(self):
        self.mouvement(montant='1000', devise='CHF')
        self.mouvement(montant='500', devise='EUR')
        self.mouvement(montant='1500', devise='JPY')
        f = self.vue()['familles']['fonds_tiers']
        self.assertEqual({k: v['solde_constate'] for k, v in f.items()}, {'CHF': 100000, 'EUR': 50000, 'JPY': 1500})
        self.assertEqual(G.afficher(1500, 'JPY'), '1 500 JPY')
        with self.assertRaises(G.Refus):
            self.mouvement(montant='15,5', devise='JPY')                         # pas de décimale pour le yen
        with self.assertRaises(ValueError):
            G.montant_minor(12.5, 'EUR')                                         # jamais un flottant

    def test_G09_paiement_compte_une_fois(self):
        d = self.facture()
        fact.record_payment(self.desk, *AVOCAT, d['id'], '300', JOUR, 'virement', 'VIR-G09-0003')
        fact.record_payment(self.desk, *AVOCAT, d['id'], '300', JOUR, 'virement', 'VIR-G09-0003')   # même référence rejouée
        a = self.mouvement(source_kind='import', source_id='REL-2026-11-0001', montant='120')
        b = self.mouvement(source_kind='import', source_id='REL-2026-11-0001', montant='120')   # double import
        self.assertTrue(b['deja'])
        self.assertEqual(a['mouvement'], b['mouvement'])
        with self.assertRaisesRegex(Stop, 'paiement_deja_compte'):
            self.mouvement(source_kind='import', source_id='REL-2026-11-0001', montant='130')
        with self.assertRaisesRegex(Stop, 'paiement_deja_compte'):
            self.mouvement(source_kind='paiement', source_id='pay-quelconque', nature='frais')   # un règlement b53 n'est jamais recopié
        with self.assertRaisesRegex(Stop, 'honoraires_par_reglement'):
            self.mouvement(nature='honoraires', montant='300')                  # un encaissement d'honoraires reste un règlement de facture
        v = self.vue()
        self.assertEqual(v['familles']['honoraires']['EUR']['constate_entrees'], 30000)
        self.assertEqual(v['familles']['fonds_tiers']['EUR']['constate_entrees'], 12000)

    def test_G09_mutation_un_reglement_recopie_serait_compte_deux_fois(self):
        d = self.facture()
        fact.record_payment(self.desk, *AVOCAT, d['id'], '300', JOUR, 'virement', 'VIR-G09-0004')
        self.desk.db.execute("INSERT INTO mouvements56109(id,matter,nature,sens,etat,montant_minor,devise,jour,source_kind,source_id,auteur,cree_le) "
                             "VALUES('mvt-copie',?,'honoraires','entree','constate',30000,'EUR',?,'saisie','copie',?,?)", (MID, JOUR, AVOCAT[0], JOUR))
        self.desk.db.commit()                                                   # recopie insérée sans la garde
        self.assertEqual(self.vue()['familles']['honoraires']['EUR']['constate_entrees'], 60000)

    def test_G09_prevu_distinct_du_constate_et_rapprochement_humain(self):
        m = self.mouvement(nature='debours', sens='sortie', etat='prevu', montant='35', contrepartie='Greffe de Valbrune (fictif)')
        c = self.vue()['familles']['debours']['EUR']
        self.assertEqual((c['prevu_sorties'], c['constate_sorties']), (3500, 0))
        row = self.desk.db.execute('SELECT version FROM mouvements56109 WHERE id=?', (m['mouvement'],)).fetchone()
        with self.assertRaisesRegex(Stop, 'verification_humaine_requise'):
            G.rapprocher_mouvement(self.desk, IA, {'id': m['mouvement'], 'version': row[0], 'reference': 'Relevé fictif'})
        G.rapprocher_mouvement(self.desk, AVOCAT, {'id': m['mouvement'], 'version': row[0], 'reference': 'Relevé bancaire du 12/11 (fictif)'})
        c = self.vue()['familles']['debours']['EUR']
        self.assertEqual((c['prevu_sorties'], c['constate_sorties']), (0, 3500))

    def test_G09_debours_qualifies_par_l_avocat_jamais_automatiquement(self):
        m = self.mouvement(nature='debours', sens='sortie', montant='35', justificatif='')
        ligne = self.vue()['familles']['debours']['EUR']['lignes'][-1]
        self.assertEqual(ligne['qualification'], 'à qualifier par l’avocat')
        v = self.desk.db.execute('SELECT version FROM mouvements56109 WHERE id=?', (m['mouvement'],)).fetchone()[0]
        with self.assertRaises(G.Refus):
            G.qualifier_debours(self.desk, AVOCAT, {'id': m['mouvement'], 'version': v, 'motif': 'Convention art. 4'})   # sans justificatif
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            G.qualifier_debours(self.desk, ASSISTANT, {'id': m['mouvement'], 'version': v, 'motif': 'x', 'justificatif': 'reçu.pdf'})
        G.qualifier_debours(self.desk, AVOCAT, {'id': m['mouvement'], 'version': v, 'motif': 'Convention art. 4 (fictive)', 'justificatif': 'Reçu du greffe (fictif)'})
        self.assertIn('qualifié par', self.vue()['familles']['debours']['EUR']['lignes'][-1]['qualification'])

    def test_G09_correction_par_mouvement_compensateur(self):
        m = self.mouvement(montant='100')
        with self.assertRaises(G.Refus):
            self.mouvement(montant='100', compense=m['mouvement'])               # même sens : pas une compensation
        self.mouvement(montant='100', sens='sortie', compense=m['mouvement'], contrepartie='Erreur de saisie (fictive)')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM mouvements56109').fetchone()[0], 2)   # la trace d'origine reste
        self.assertEqual(self.vue()['familles']['fonds_tiers']['EUR']['solde_constate'], 0)

    def test_G09_paiement_partiel_expose_pour_g3_g4(self):
        d = self.facture()
        fact.record_payment(self.desk, *AVOCAT, d['id'], '300', JOUR, 'virement', 'VIR-G09-0005')
        p = G.paiement_partiel(self.desk, AVOCAT, d['id'])
        self.assertEqual((p['du_cents'], p['regle_cents'], p['reste_cents'], p['etat']), (90000, 30000, 60000, 'partielle'))
        f = G.enregistrer_frais_achat(self.desk, AVOCAT, MID, {'cle': self.cle(), 'nature': 'frais', 'montant_ht': '40', 'libelle': 'Extrait Kbis (fictif)', 'jour': JOUR})
        self.assertEqual(f['montant_ht_cents'], 4000)
        self.assertEqual(self.vue()['familles']['frais']['EUR']['constate_sorties'], 4000)


class G09Coordonnees(Finances):
    def test_G09_changement_bancaire_a_verifier_par_une_autre_personne(self):
        out = G.changer_coordonnees(self.desk, AVOCAT, {'dossier': MID, 'beneficiaire': 'MARTIN (fictif)', 'iban': IBAN, 'provenance': 'courriel', 'recu_le': JOUR})
        self.assertEqual(out['etat'], 'a_verifier')
        self.assertEqual(out['iban'], 'FR76 **** **** 0189')
        self.mouvement(nature='fonds_tiers', sens='sortie', etat='prevu', montant='5000', beneficiaire='MARTIN (fictif)')
        v = self.vue()
        self.assertTrue(any('À vérifier' in a for a in v['alertes']))
        dump = '\n'.join(str(tuple(r)) for r in self.desk.db.execute('SELECT * FROM coordonnees56109'))
        dump += '\n'.join(str(tuple(r)) for r in self.desk.db.execute('SELECT * FROM gestion_journal5697'))
        self.assertNotIn(IBAN, dump)                                            # jamais l'IBAN complet en base ni au journal
        with self.assertRaisesRegex(Stop, 'verification_independante_requise'):
            G.verifier_coordonnees(self.desk, AVOCAT, {'id': out['coordonnees'], 'reference': 'Appel au numéro connu'})
        with self.assertRaisesRegex(Stop, 'verification_humaine_requise'):
            G.verifier_coordonnees(self.desk, IA, {'id': out['coordonnees'], 'reference': 'Somme de contrôle IBAN correcte'})
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            G.verifier_coordonnees(self.desk, ASSISTANT, {'id': out['coordonnees'], 'reference': 'Appel'})
        G.verifier_coordonnees(self.desk, ADMIN, {'id': out['coordonnees'], 'reference': 'Appel au 01 00 00 00 05, numéro connu du dossier (fictif)'})
        self.assertEqual(self.vue()['alertes'], [])
        self.assertEqual(G.coordonnees_a_verifier(self.desk), {})

    def test_G09_nouvelle_version_conserve_l_ancienne(self):
        G.changer_coordonnees(self.desk, AVOCAT, {'beneficiaire': 'ZORVAL (fictif)', 'iban': IBAN, 'provenance': 'courrier'})
        v2 = G.changer_coordonnees(self.desk, AVOCAT, {'beneficiaire': 'ZORVAL (fictif)', 'iban': 'DE89370400440532013000', 'provenance': 'courriel'})
        self.assertEqual(v2['version'], 2)
        rows = self.desk.db.execute("SELECT version, etat FROM coordonnees56109 WHERE beneficiaire='ZORVAL (fictif)' ORDER BY version").fetchall()
        self.assertEqual([tuple(r) for r in rows], [(1, 'a_verifier'), (2, 'a_verifier')])


class G09Routes(Finances):
    def appel(self, route, data=None, acteur=AVOCAT, args=None):
        from agent import web440
        corps = json.dumps(data or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': acteur[0], 'HTTP_X_AXIORHUB_ROLE': acteur[1], 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON,
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps),
               'REQUEST_METHOD': 'GET' if data is None else 'POST'}
        out = web440.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm56109/gestion/' + route, args or {}, env['REQUEST_METHOD'])
        return out['status'][:3], json.loads(out['body'])

    def test_G09_routes_404_neutre_201_et_rejeu(self):
        for mid in (MID2, 'DOS-999'):
            self.assertEqual(self.appel('finances', None, AUTRE, {'dossier': mid})[0], '404')
        corps = {'cle': 'mvt-route-0001', 'dossier': MID, 'nature': 'fonds_tiers', 'sens': 'entree', 'etat': 'constate', 'montant': '10', 'devise': 'EUR', 'jour': JOUR}
        st, out = self.appel('finances/mouvement', corps)
        self.assertEqual(st, '201')
        st2, out2 = self.appel('finances/mouvement', corps)
        self.assertTrue(out2.get('rejoue'))
        self.assertEqual(out2['mouvement'], out['mouvement'])
        self.assertEqual(self.appel('finances/coordonnees/verifier', {'cle': 'v-route-0001', 'id': 'x'}, ASSISTANT)[0], '403')

    def test_G09_section_dans_la_vue_financiere_du_dossier(self):
        self.mouvement()
        env = {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1]}
        vu = []
        habille = G.habiller(self.desk, env, '/p', '/dossier', {'id': MID}, lambda t, html, *a, **k: vu.append(html) or html)
        avant = self.desk.db.total_changes
        habille('DOS-001', '<details><section class="d51-block b53-matter-block"><h3>Honoraires et facturation</h3></section><p>suite</p></details>')
        self.assertEqual(self.desk.db.total_changes, avant)
        html = vu[0]
        self.assertLess(html.index('b53-matter-block'), html.index('id="g109-finances"'))
        self.assertLess(html.index('id="g109-finances"'), html.index('<p>suite</p>'))
        self.assertIn('Fonds de tiers (EUR)', html)
        self.assertIn('Relevé (constaté)', html)


if __name__ == '__main__':
    unittest.main()
