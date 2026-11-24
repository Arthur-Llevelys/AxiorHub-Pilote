"""5.6.109 (lot G1, G02) : assistance régulière dans la fiche client existante (``gestion56109``, ``facturation5653.convention_qualifiee``).

Contrat versionné (versions immuables, historique conservé), périodes civiles explicites [début inclus, fin exclue], quota partagé par les
seuls dossiers convenus, consommation unique par temps validé et par unité (minutes OU prestations), solde indéterminé dès qu'une durée
manque, convention signée (avec preuve) distinguée d'une convention seulement envoyée, compte rendu de période = un seul livrable du
registre commun. Données fictives (DURAND, DOS-001, DOS-002, example.test) ; aucun réseau, aucun modèle.
"""
from datetime import date
import json
import sqlite3
import unittest

from agent import crm5636, facturation5653 as fact, gestion56109 as G, time500
from agent.common import Stop
from test_v5697_gestion import ADMIN, AUTRE, AVOCAT, MID, MID2
from test_v56109_prefacturation import JOUR, Socle


class Assistance(Socle):
    def setUp(self):
        super().setUp()
        self.client = crm5636.save(self.desk, {'name': 'DURAND SAS (fictive)', 'kind': 'societe', 'category': 'client', 'matter': MID, 'role': 'client'})
        self.n = 0

    def cle(self):
        self.n += 1
        return 'cle-g02-%04d' % self.n

    def contrat(self, version=0, dossiers=(MID,), acteur=AVOCAT, **plus):
        d = {'client': self.client['id'], 'version': version, 'debut': '2026-01-31', 'periodicite': 'mois', 'montant': '500', 'devise': 'EUR',
             'services': [{'id': 'conseil', 'libelle': 'Consultations courantes', 'unite': 'minutes', 'inclus': 120},
                          {'id': 'actes', 'libelle': 'Relecture de contrats', 'unite': 'prestation', 'inclus': 3}],
             'dossiers': list(dossiers), 'report': 'aucun'}
        d.update(plus)
        return G.enregistrer_contrat(self.desk, acteur, d)

    def periode(self, contrat, jour='2026-11-15', acteur=AVOCAT):
        return G.ouvrir_periode(self.desk, acteur, {'contrat': contrat, 'jour': jour})

    def temps(self, mid, minutes, jour=JOUR):
        return time500.add_manual(self.desk, mid, jour, minutes, 'Consultation (fictive)', None, AVOCAT[0])['entry_id']


class G02Quota(Assistance):
    def test_G02_quota_partage_seulement_par_les_dossiers_convenus(self):
        c = self.contrat()
        p = self.periode(c['contrat'])
        self.assertEqual((p['debut'], p['fin'], p['partielle']), ('2026-11-01', '2026-12-01', False))
        t1 = self.temps(MID, 60)
        t2 = self.temps(MID2, 90)                                               # dossier du même client, NON convenu
        out = G.affecter(self.desk, AVOCAT, {'periode': p['periode'], 'service': 'conseil'})
        self.assertEqual(out['ajoutes'], 1)
        with self.assertRaises(G.Refus):
            G.affecter(self.desk, AVOCAT, {'periode': p['periode'], 'service': 'conseil', 'temps': [t2]})
        b = G.bilan_periode(self.desk, AVOCAT, p['periode'])
        s = b['services']['conseil']
        self.assertEqual((s['consommation_connue'], s['reste_exact'], s['unite']), (60, 60, 'minutes'))
        again = G.affecter(self.desk, AVOCAT, {'periode': p['periode'], 'service': 'conseil', 'temps': [t1]})
        self.assertEqual((again['ajoutes'], again['deja_affectes']), (0, 1))    # un temps n'est consommé qu'une fois
        G.affecter(self.desk, AVOCAT, {'periode': p['periode'], 'service': 'actes', 'temps': [t1], 'quantite': '1'})
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM assistance_consommations56109 WHERE diligence=?', (t1,)).fetchone()[0], 1)

    def test_G02_mutation_la_contrainte_unique_refuse_une_double_consommation(self):
        c = self.contrat()
        p = self.periode(c['contrat'])
        t1 = self.temps(MID, 60)
        G.affecter(self.desk, AVOCAT, {'periode': p['periode'], 'service': 'conseil'})
        with self.assertRaises(sqlite3.IntegrityError):                         # même temps, autre prestation : refusé par le schéma
            self.desk.db.execute("INSERT INTO assistance_consommations56109(id,contrat_id,periode_id,diligence,matter,service,unite,quantite,auteur,at) "
                                 "VALUES('cso-x',?,?,?,?,'actes','prestation',1,'x','x')", (c['contrat'], p['periode'], t1, MID))
        self.desk.db.rollback()

    def test_G02_prestations_jamais_converties_en_minutes(self):
        c = self.contrat()
        p = self.periode(c['contrat'])
        t1 = self.temps(MID, 240)
        G.affecter(self.desk, AVOCAT, {'periode': p['periode'], 'service': 'actes', 'temps': [t1], 'quantite': '2'})
        s = G.bilan_periode(self.desk, AVOCAT, p['periode'])['services']['actes']
        self.assertEqual((s['consommation_connue'], s['reste_exact'], s['unite']), (2, 1, 'prestation'))

    def test_G02_solde_indetermine_si_une_duree_est_inconnue(self):
        c = self.contrat()
        p = self.periode(c['contrat'])
        self.temps(MID, 60)
        G.affecter(self.desk, AVOCAT, {'periode': p['periode'], 'service': 'conseil'})
        self.trace('tel-g02-1', '', dossier=MID2)                               # dossier non convenu : sans effet
        self.assertEqual(G.bilan_periode(self.desk, AVOCAT, p['periode'])['services']['conseil']['reste_exact'], 60)
        self.trace('tel-g02-2', '')                                             # dossier convenu, durée inconnue
        s = G.bilan_periode(self.desk, AVOCAT, p['periode'])['services']['conseil']
        self.assertIsNone(s['reste_exact'])
        self.assertTrue(s['a_completer'])
        self.assertEqual(s['consommation_connue'], 60)                          # la part connue reste affichée

    def test_G02_mutation_une_inconnue_comptee_zero_annoncerait_un_reste_exact(self):
        self.assertIsNone(G.consommation_abonnement(120, [60, None])['reste_exact'])
        self.assertEqual(G.consommation_abonnement(120, [60, 0])['reste_exact'], 60)   # sans la garde : un faux reste exact

    def test_G02_estimation_ne_consomme_pas(self):
        c = self.contrat()
        p = self.periode(c['contrat'])
        self.rdv()
        self.collecter()
        b = G.bilan_periode(self.desk, AVOCAT, p['periode'])
        self.assertEqual((b['estimations_non_comptees'], b['services']['conseil']['consommation_connue']), (1, 0))


class G02Contrat(Assistance):
    def convention(self, etat, preuve=''):
        d = fact.prepare(self.desk, *AVOCAT, 'convention', MID, {'mode': 'forfait', 'lignes': [
            {'nature': 'honoraires', 'libelle': 'Assistance mensuelle (fictive)', 'quantite': '1', 'prix_unitaire_ht': '500'}], 'mission': 'Assistance fictive',
            'livrables': 'Consultations courantes'}, '')
        manque = fact.check(self.desk, *AVOCAT, d['id'])['variables_manquantes']
        d = fact.update(self.desk, *AVOCAT, d['id'], {'textes': {k: 'Valeur fictive' for k in manque}}, d['version'])
        d = fact.approve(self.desk, *AVOCAT, d['id'], d['version'])
        if etat in ('envoye', 'signe'):
            d = fact.mark_engagement(self.desk, *AVOCAT, d['id'], 'envoye', d['version'])
        if etat == 'signe':
            d = fact.mark_engagement(self.desk, *AVOCAT, d['id'], 'signe', d['version'], preuve)
        return d

    def test_G02_convention_signee_distinguee_d_une_convention_envoyee(self):
        d = self.convention('envoye')
        self.assertEqual(fact._active_convention(self.desk, MID)['id'], d['id'])   # contre-exemple : l'ancienne sélection la retient
        q = G.convention_qualifiee(self.desk, d['id'])
        self.assertEqual(q['etat'], 'envoyee')
        out = self.contrat(convention=d['id'])
        self.assertIn('non signée', out['message'])
        d2 = fact.mark_engagement(self.desk, *AVOCAT, d['id'], 'signe', fact.get(self.desk, d['id'])['version'], 'Convention signée reçue le 12/11 (fictive)')
        q2 = G.convention_qualifiee(self.desk, d2['id'])
        self.assertEqual((q2['etat'], q2['preuve'][:17]), ('signee', 'Convention signée'))

    def test_G02_mutation_signe_sans_preuve_n_est_pas_signee(self):
        d = self.convention('envoye')
        self.desk.db.execute("UPDATE b53_documents SET state='signe' WHERE id=?", (d['id'],))   # état forcé sans preuve (garde de mark_engagement contournée)
        self.desk.db.commit()
        self.assertEqual(G.convention_qualifiee(self.desk, d['id'])['etat'], 'envoyee')

    def test_G02_contrat_versionne_historique_conserve_et_periode_figee(self):
        c = self.contrat()
        p = self.periode(c['contrat'])
        v2 = self.contrat(version=1, dossiers=(MID, MID2), services=[{'id': 'conseil', 'libelle': 'Consultations courantes', 'unite': 'minutes', 'inclus': 300}])
        self.assertEqual(v2['version'], 2)
        with self.assertRaisesRegex(Stop, 'version_perimee'):
            self.contrat(version=1)
        b = G.bilan_periode(self.desk, AVOCAT, p['periode'])
        self.assertEqual((b['contrat_version'], b['services']['conseil']['quota']), (1, 120))   # la période ouverte garde sa version
        self.temps(MID2, 30)
        self.assertEqual(G.affecter(self.desk, AVOCAT, {'periode': p['periode'], 'service': 'conseil'})['ajoutes'], 0)   # MID2 convenu seulement en v2
        p2 = self.periode(c['contrat'], '2026-12-10')
        self.assertEqual(G.bilan_periode(self.desk, AVOCAT, p2['periode'])['services']['conseil']['quota'], 300)
        versions = [r[0] for r in self.desk.db.execute('SELECT version FROM assistance_versions56109 ORDER BY version')]
        self.assertEqual(versions, [1, 2])
        with self.assertRaises(sqlite3.IntegrityError):
            self.desk.db.execute("UPDATE assistance_versions56109 SET contenu='{}' WHERE version=1")
        self.desk.db.rollback()

    def test_G02_periodes_civiles_fin_de_mois_et_chevauchement_refuse(self):
        contenu = {'debut': '2026-01-31', 'fin': '2026-06-15', 'periodicite': 'mois'}
        self.assertEqual(G.bornes_periode(contenu, date(2026, 1, 31)), (date(2026, 1, 31), date(2026, 2, 1), True))
        self.assertEqual(G.bornes_periode(contenu, date(2026, 2, 15)), (date(2026, 2, 1), date(2026, 3, 1), False))   # février : pas 30 jours fixes
        self.assertEqual(G.bornes_periode(contenu, date(2026, 6, 2)), (date(2026, 6, 1), date(2026, 6, 15), True))
        self.assertEqual(G.bornes_periode({**contenu, 'periodicite': 'trimestre'}, date(2026, 5, 20)), (date(2026, 4, 1), date(2026, 6, 15), True))
        c = self.contrat()
        self.periode(c['contrat'], '2026-11-15')
        with self.assertRaisesRegex(Stop, 'periode_existante'):
            self.periode(c['contrat'], '2026-11-30')

    def test_G02_report_autorise_puis_interdit(self):
        c = self.contrat(report='periode_suivante')
        p = self.periode(c['contrat'])
        self.temps(MID, 60)
        G.affecter(self.desk, AVOCAT, {'periode': p['periode'], 'service': 'conseil'})
        G.cloturer_periode(self.desk, AVOCAT, {'periode': p['periode']})
        dec = self.periode(c['contrat'], '2026-12-05')
        self.assertEqual(G.bilan_periode(self.desk, AVOCAT, dec['periode'])['services']['conseil']['quota'], 180)   # 120 + 60 reportées
        c2 = self.contrat(version=1, report='aucun')
        jan = self.periode(c['contrat'], '2027-01-05')
        self.assertEqual(G.bilan_periode(self.desk, AVOCAT, jan['periode'])['services']['conseil']['quota'], 120)
        self.assertEqual(c2['version'], 2)

    def test_G02_report_indetermine_si_la_periode_precedente_l_etait(self):
        c = self.contrat(report='periode_suivante')
        p = self.periode(c['contrat'])
        self.trace('tel-g02-9', '')
        G.cloturer_periode(self.desk, AVOCAT, {'periode': p['periode']})
        dec = self.periode(c['contrat'], '2026-12-05')
        s = G.bilan_periode(self.desk, AVOCAT, dec['periode'])['services']['conseil']
        self.assertIsNone(s['reste_exact'])
        self.assertTrue(s.get('report_indetermine'))

    def test_G02_compte_rendu_un_seul_livrable_dans_produire(self):
        c = self.contrat()
        p = self.periode(c['contrat'])
        t1, t2 = self.temps(MID, 60), self.temps(MID, 30)
        G.affecter(self.desk, AVOCAT, {'periode': p['periode'], 'service': 'conseil'})
        r1 = G.compte_rendu(self.desk, AVOCAT, {'periode': p['periode'], 'cle': self.cle(), 'exclure': [t2]})
        r2 = G.compte_rendu(self.desk, AVOCAT, {'periode': p['periode'], 'cle': self.cle()})
        self.assertEqual(r1['document_id'], r2['document_id'])                 # même livrable, nouvelle version
        self.assertEqual((r1['revision'], r2['revision']), (1, 2))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(DISTINCT document_id) FROM livrables_contenus56109').fetchone()[0], 1)
        v1 = json.loads(self.desk.db.execute('SELECT payload FROM livrables_contenus56109 WHERE revision=1').fetchone()[0])
        lignes = next(b for b in v1['blocks'] if b['id'] == 'travaux')['rows']
        self.assertEqual(len(lignes), 1)                                       # le temps exclu par l'avocat n'y figure pas
        self.assertEqual(self.desk.db.execute('SELECT compte_rendu FROM assistance_periodes56109 WHERE id=?', (p['periode'],)).fetchone()[0], r1['document_id'])
        self.assertTrue(t1)


class G02Droits(Assistance):
    def test_G02_vue_partielle_signalee_et_cloture_refusee(self):
        c = self.contrat(dossiers=(MID, MID2))
        p = self.periode(c['contrat'])
        v = G.assistance(self.desk, AUTRE, self.client['id'])                  # AUTRE ne voit pas DOS-002
        self.assertTrue(v['vue_partielle'])
        self.assertTrue(v['periodes'][0]['vue_partielle'])
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            G.cloturer_periode(self.desk, AUTRE, {'periode': p['periode']})

    def test_G02_section_dans_la_fiche_client(self):
        c = self.contrat()
        self.periode(c['contrat'])
        env = {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1]}
        vu = []
        habille = G.habiller(self.desk, env, '/p', '/contacts-crm', {'id': self.client['id']}, lambda t, html, *a, **k: vu.append(html) or html)
        avant = self.desk.db.total_changes
        habille('Contacts', '<h1>DURAND</h1><form class="m5-form m5-inline" data-api="m5636/contacts/archiver" data-reload="1"></form>')
        self.assertEqual(self.desk.db.total_changes, avant)
        html = vu[0]
        self.assertLess(html.index('id="g109-assistance"'), html.index('m5636/contacts/archiver'))
        self.assertIn('Période du 2026-11-01 au 2026-12-01', html)
        self.assertIn('Aucune convention reliée', html)


class Raccordement(Assistance):
    def test_G01_G02_G09_sections_dans_les_pages_existantes_par_le_routeur(self):
        """Parcours réel par ``web440.route`` : Temps et honoraires, fiche du contact client, dossier (vue Travaux) ; aucune page nouvelle."""
        from agent import web440
        c = self.contrat()
        self.periode(c['contrat'])
        self.trace('tel-route-1', '')
        G.enregistrer_mouvement(self.desk, AVOCAT, {'cle': 'cle-route-0001', 'dossier': MID, 'nature': 'fonds_tiers', 'sens': 'entree', 'etat': 'constate',
                                                   'montant': '10', 'devise': 'EUR', 'jour': JOUR})
        env = {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1], 'REQUEST_METHOD': 'GET'}
        auth = {'origin': 'https://pilote.example.test', 'csrf': 'jeton-fictif'}
        for path, args, marque in (('/honoraires', {}, 'id="g109-prefacturation"'), ('/contacts-crm', {'id': self.client['id']}, 'id="g109-assistance"'),
                                   ('/dossier', {'id': MID, 'vue': 'travaux'}, 'id="g109-finances"')):
            out = web440.route(env, self.f.c, auth, '/p', path, args, 'GET')
            corps = out['body'] if isinstance(out['body'], str) else out['body'].decode('utf-8')
            self.assertEqual(out['status'], '200 OK', path)
            self.assertEqual(corps.count(marque), 1, path)


if __name__ == '__main__':
    unittest.main()
