"""5.6.53 : audit 5.6.52 — fiabilité des opérations externes et des routines (F01 à F08, U01), chiffrage des honoraires (Honorium
corrigé : H01 à H03), registre de facturation, factures structurées (Factur-X, UBL), dossier FACTURATION. Données fictives."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile
import io

import test_v5652 as base
from agent import connexions5652 as cx, routines5652 as rt, membres5650 as membres
from agent import honoraires5653 as fees, fiscal5653 as fiscal, modeles5653 as models, facturx5653 as fx, facturation5653 as fact
from agent.common import Stop

ACTOR = 'avocat@example.test'
MID = base.MID
ENTITY = {'nom': 'Cabinet Fictif Avocat', 'barreau': 'Exempleville', 'adresse_ligne1': '1 rue de l’Exemple', 'code_postal': '75001', 'ville': 'Paris',
          'pays': 'FR', 'siren': '000000000', 'siret': '00000000000000', 'tva': 'FR00000000000', 'email': 'cabinet@example.test',
          'iban': 'FR7600000000000000000000000', 'regime_cabinet': 'normal', 'categorie_entreprise': 'micro', 'delai_paiement_jours': '30'}
CLIENT = {'nom': 'Société Alpha Fictive', 'kind': 'professionnel', 'adresse_ligne1': '2 avenue Fictive', 'code_postal': '69001', 'ville': 'Lyon',
          'country': 'FR', 'siren': '000000001', 'email': 'compta@example.test'}


class FeeEngine(unittest.TestCase):
    class Desk:
        c = {'state_dir': '.'}

        def settings(self, key, default=None):
            return {'economie': {'cout_horaire': '100'}}

    def test_exemple_de_reference_de_l_audit(self):
        r = fees.estimate(self.Desk(), {'mode': 'forfait', 'taux_horaire': '200', 'diligences': [
            {'libelle_client': 'Analyse', 'minutes': 120}, {'libelle_client': 'Rédaction', 'minutes': 180}, {'libelle_client': 'Échanges', 'minutes': 60}]})
        self.assertEqual(r['propose_ht_cents'], 120000)
        self.assertEqual(r['interne']['cout_previsible_cents'], 60000)
        self.assertEqual(r['interne']['marge_interne_cents'], 60000)
        self.assertEqual(r['provision_ht_cents'], 36000)
        self.assertNotIn('interne', fees.client_view(r))                    # coût et marge restent internes

    def test_H02_zero_explicite_et_null_distincts(self):
        d = self.Desk()
        lines = [{'minutes': 60}]
        self.assertEqual(fees.estimate(d, {'mode': 'forfait', 'forfait': None, 'taux_horaire': '250', 'diligences': lines})['propose_ht_cents'], 25000)
        with self.assertRaisesRegex(Stop, 'forfait_zero_sans_gratuite'):      # un zéro n'est jamais remplacé par le montant horaire
            fees.estimate(d, {'mode': 'forfait', 'forfait': '0', 'taux_horaire': '250', 'diligences': lines})
        with self.assertRaisesRegex(Stop, 'motif_gratuite_requis'):
            fees.estimate(d, {'mode': 'forfait', 'gratuite': True, 'taux_horaire': '250', 'diligences': lines})
        free = fees.estimate(d, {'mode': 'forfait', 'gratuite': True, 'motif_gratuite': 'mission pro bono', 'taux_horaire': '250', 'diligences': lines})
        self.assertEqual(free['propose_ht_cents'], 0)
        self.assertEqual(fees.estimate(d, {'mode': 'forfait', 'forfait': '1500', 'taux_horaire': '250', 'diligences': lines})['propose_ht_cents'], 150000)

    def test_H03_provision_au_centime_et_nombres_refuses(self):
        self.assertEqual(fees.provision(100125, '30'), 30038)
        self.assertEqual(fees.provision(100125, '30', 'euro'), 30000)       # arrondi à l'euro seulement s'il est choisi
        for bad in ('NaN', 'Infinity', '-5', 'douze'):
            with self.assertRaises(Stop):
                fees.dec(bad)

    def test_bibliotheque_honorium_chargee(self):
        self.assertEqual(len(fees.library()['diligenceLibrary']), 354)
        self.assertEqual(len(fees.models()), 50)
        rows = fees.diligences_for(None, 'Consultation juridique', 'complex')
        self.assertTrue(all(r['source'] == 'bibliotheque' for r in rows))


class Fiscal(unittest.TestCase):
    def test_H01_taux_zero_ne_prouve_pas_la_franchise(self):
        out = fiscal.qualify({'fiscal': {}}, {'kind': 'professionnel', 'country': 'FR'}, {'taux': '0'})
        self.assertEqual(out['regime'], 'a_qualifier')
        self.assertNotIn('293 B', out['mention'])
        self.assertTrue(out['bloquants'])

    def test_franchise_choisie_et_justifiee(self):
        ent = {'fiscal': {'regime_cabinet': 'franchise'}}
        out = fiscal.qualify(ent, {'kind': 'consommateur'})
        self.assertIn('293 B', out['mention'])
        self.assertTrue(any('non justifiée' in b for b in out['bloquants']))
        ent['fiscal']['justification_franchise'] = 'Seuils vérifiés le 2026-10-16'
        self.assertFalse(fiscal.qualify(ent, {'kind': 'consommateur'})['bloquants'])

    def test_international_et_ue_sans_substitution_automatique(self):
        ent = {'fiscal': {'regime_cabinet': 'normal'}}
        eu = fiscal.qualify(ent, {'kind': 'professionnel', 'country': 'DE'})
        self.assertEqual((eu['regime'], eu['categorie_tva']), ('autoliquidation', 'AE'))
        self.assertTrue(any('intracommunautaire' in b for b in eu['bloquants']))
        world = fiscal.qualify(ent, {'kind': 'professionnel', 'country': 'US'})
        self.assertEqual(world['categorie_tva'], 'O')
        self.assertTrue(world['bloquants'])                                   # confirmation exigée
        self.assertTrue(fiscal.qualify(ent, {'kind': ''})['bloquants'])        # qualité du client inconnue
        self.assertNotIn('40 €', fiscal.late_payment_clause('b2c'))
        self.assertIn('40 €', fiscal.late_payment_clause('b2b_fr'))


class Billing(base.Base):
    def setUp(self):
        super().setUp()
        self.admin = ('admin@example.test', 'administrateur')
        fact.save_entity(self.desk, *self.admin, ENTITY)
        fact.save_client(self.desk, ACTOR, 'avocat', MID, CLIENT)

    def invoice(self, amount='1200', key=''):
        return fact.prepare(self.desk, ACTOR, 'avocat', 'facture', MID, {'sous_type': 'libre', 'date_prestation': '2026-10-01', 'lignes': [
            {'nature': 'honoraires', 'libelle': 'Consultation sur le bail (détail client)', 'quantite': '1', 'prix_unitaire_ht': amount}]}, key)

    def approve_issue(self, d, key='cle-emission-0001'):
        d = fact.approve(self.desk, ACTOR, 'avocat', d['id'], d['version'])
        return fact.issue(self.desk, ACTOR, 'avocat', d['id'], key)

    def test_brouillon_sans_numero_puis_emission_unique(self):
        d = self.invoice()
        self.assertIsNone(d['number'])
        self.assertEqual((d['snapshot']['totaux']['ht_cents'], d['snapshot']['totaux']['tva_cents'], d['snapshot']['totaux']['ttc_cents']), (120000, 24000, 144000))
        prev = fact.preview(self.desk, ACTOR, 'avocat', d['id'])
        self.assertNotIn('F-', prev['texte'].split('Facture', 1)[1][:30])     # aperçu non fiscal
        with self.assertRaisesRegex(Stop, 'approbation_requise_ou_perimee'):
            fact.issue(self.desk, ACTOR, 'avocat', d['id'], 'cle-emission-0001')
        out = self.approve_issue(d)
        year = fact._today(self.desk).year
        self.assertEqual(out['number'], 'F-%d-0001' % year)
        self.assertEqual(out['state'], 'emise')
        again = fact.issue(self.desk, ACTOR, 'avocat', d['id'], 'cle-emission-0001')
        self.assertEqual(again['number'], out['number'])                      # même clic : aucune seconde émission
        second = self.approve_issue(self.invoice('300'), 'cle-emission-0002')
        self.assertEqual(second['number'], 'F-%d-0002' % year)                 # séquence continue
        folder = self.root / 'Dossiers/DEMO/FACTURATION'
        names = sorted(p.name for p in folder.iterdir())
        self.assertTrue(any(n.endswith('.docx') for n in names), names)
        self.assertTrue(any(n.endswith('factur-x.xml') for n in names), names)
        self.assertTrue(any(n.endswith('UBL.xml') for n in names), names)
        self.assertEqual(out['files_state'], 'publies')
        structure = out['checks']['structure']
        self.assertTrue(structure['ok'], [c for c in structure['controles'] if not c['ok']])
        self.assertIn('Validation XSD officielle du format', structure['non_verifie'])
        with self.assertRaisesRegex(Stop, 'document_emis_non_modifiable'):
            fact.update(self.desk, ACTOR, 'avocat', out['id'], {'lignes': []}, out['version'])

    def test_xml_concordant_et_secret_professionnel(self):
        out = self.approve_issue(self.invoice())
        xml = (self.root / Path(out['files']['cii']['chemin']).relative_to('/')).read_text(encoding='utf-8')
        self.assertIn('<ram:GrandTotalAmount>1440.00</ram:GrandTotalAmount>', xml)
        self.assertIn('Prestations juridiques', xml)
        self.assertNotIn('bail', xml.lower())                                  # détail réservé au client : absent des données structurées
        ubl = (self.root / Path(out['files']['ubl']['chemin']).relative_to('/')).read_text(encoding='utf-8')
        self.assertIn('<cbc:PayableAmount currencyID="EUR">1440.00</cbc:PayableAmount>', ubl)
        self.assertNotIn('bail', ubl.lower())
        if 'pdf' in out['files']:
            pdf = (self.root / Path(out['files']['pdf']['chemin']).relative_to('/')).read_bytes()
            self.assertEqual(fx.embedded_xml(pdf), xml.encode('utf-8'))

    def test_approbation_invalidee_par_modification(self):
        d = self.invoice()
        d = fact.approve(self.desk, ACTOR, 'avocat', d['id'], d['version'])
        d = fact.update(self.desk, ACTOR, 'avocat', d['id'], {'lignes': [{'libelle': 'Consultation', 'quantite': '2', 'prix_unitaire_ht': '1200'}]}, d['version'])
        self.assertEqual(d['state'], 'brouillon')
        self.assertEqual(d['snapshot']['totaux']['ht_cents'], 240000)          # recalcul intégral
        with self.assertRaisesRegex(Stop, 'approbation_requise_ou_perimee'):
            fact.issue(self.desk, ACTOR, 'avocat', d['id'], 'cle-emission-0003')

    def test_regime_non_qualifie_bloque_l_approbation(self):
        fact.save_entity(self.desk, *self.admin, {'regime_cabinet': '', 'version': fact.entity(self.desk)['version']})
        d = self.invoice()
        report = fact.check(self.desk, ACTOR, 'avocat', d['id'])
        self.assertTrue(any('régime de TVA du cabinet' in b for b in report['bloquants']))
        with self.assertRaisesRegex(Stop, 'document_a_completer'):
            fact.approve(self.desk, ACTOR, 'avocat', d['id'], d['version'])

    def test_temps_valides_reserves_une_seule_fois(self):
        from agent import time500
        time500.add_manual(self.desk, MID, '2026-10-02', 90, 'Analyse des pièces', '200')
        first = fact.prepare(self.desk, ACTOR, 'avocat', 'facture', MID, {'sous_type': 'temps'})
        self.assertEqual(first['snapshot']['totaux']['ht_cents'], 30000)
        with self.assertRaisesRegex(Stop, 'aucun_temps_a_facturer'):          # déjà réservé : rien d'autre à facturer
            fact.prepare(self.desk, ACTOR, 'avocat', 'facture', MID, {'sous_type': 'temps'})
        fact.abandon(self.desk, ACTOR, 'avocat', first['id'], first['version'])
        self.assertEqual(len(fact.billable_time(self.desk, MID)), 1)           # brouillon abandonné : réservation libérée

    def test_avoir_et_reglements(self):
        inv = self.approve_issue(self.invoice())
        paid = fact.record_payment(self.desk, ACTOR, 'avocat', inv['id'], '400', '2026-10-10', 'virement', 'VIR-1')
        self.assertEqual(paid['payment_state'], 'partielle')
        paid = fact.record_payment(self.desk, ACTOR, 'avocat', inv['id'], '1040', '2026-10-12', 'virement', 'VIR-2')
        self.assertEqual(paid['payment_state'], 'reglee')
        again = fact.record_payment(self.desk, ACTOR, 'avocat', inv['id'], '1040', '2026-10-12', 'virement', 'VIR-2')   # même référence : une fois
        self.assertEqual(len(again['payments']), 2)
        av = fact.credit_note(self.desk, ACTOR, 'avocat', inv['id'], 'Erreur de quantité')
        self.assertEqual(av['origin'], inv['id'])
        av = fact.approve(self.desk, ACTOR, 'avocat', av['id'], av['version'])
        av = fact.issue(self.desk, ACTOR, 'avocat', av['id'], 'cle-avoir-0001')
        self.assertTrue(av['number'].startswith('AV-'))
        self.assertEqual(fact.get(self.desk, inv['id'])['state'], 'rectifiee')
        self.assertEqual(fact.get(self.desk, inv['id'])['number'], inv['number'])   # l'original n'est jamais réécrit
        xml = (self.root / Path(av['files']['cii']['chemin']).relative_to('/')).read_text(encoding='utf-8')
        self.assertIn('<ram:TypeCode>381</ram:TypeCode>', xml)
        self.assertIn(inv['number'], xml)

    def test_droits_et_dossier_reserve(self):
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            fact.prepare(self.desk, 'assistant@example.test', 'assistant', 'facture', MID, {'sous_type': 'libre'})
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            fact.save_entity(self.desk, ACTOR, 'avocat', {'nom': 'Autre'})       # fiche du cabinet : droit « réglages »
        d = self.invoice()
        membres.set_viewer(frozenset({MID}))
        self.addCleanup(membres.set_viewer, None)
        with self.assertRaisesRegex(Stop, 'dossier_reserve'):
            fact.get(self.desk, d['id'])

    def test_convention_provision_et_solde(self):
        est = fees.save_estimate(self.desk, ACTOR, MID, {'mode': 'forfait', 'taux_horaire': '200', 'diligences': [{'libelle_client': 'Analyse', 'minutes': 360}],
                                                         'provision_pct': '30'})
        est = fees.retain(self.desk, ACTOR, est['id'], '1200', est['version'])
        conv = fact.prepare(self.desk, ACTOR, 'avocat', 'convention', MID, {'estimation': est['id'], 'mission': 'Conseil fictif sur un bail',
                                                                            'livrables': 'Note écrite'})
        self.assertEqual(conv['snapshot']['totaux']['ht_cents'], 120000)
        missing = fact.check(self.desk, ACTOR, 'avocat', conv['id'])['variables_manquantes']
        texts = {k: 'Valeur fictive' for k in missing}
        conv = fact.update(self.desk, ACTOR, 'avocat', conv['id'], {'textes': texts}, conv['version'])
        self.assertEqual(fact.check(self.desk, ACTOR, 'avocat', conv['id'])['bloquants'], [])
        conv = fact.approve(self.desk, ACTOR, 'avocat', conv['id'], conv['version'])
        self.assertTrue(conv['number'].startswith('C-'))
        with self.assertRaisesRegex(Stop, 'preuve_accord_requise'):
            fact.mark_engagement(self.desk, ACTOR, 'avocat', conv['id'], 'signe', conv['version'])
        conv = fact.mark_engagement(self.desk, ACTOR, 'avocat', conv['id'], 'signe', conv['version'], 'Convention signée reçue le 2026-10-05')
        self.assertEqual(fees.get_estimate(self.desk, est['id'])['signed_cents'], 120000)
        kinds = {s['kind'] for s in fact.suggestions(self.desk, [MID])}
        self.assertIn('provision_prevue', kinds)
        prov = fact.prepare(self.desk, ACTOR, 'avocat', 'facture', MID, {'sous_type': 'provision'})
        self.assertEqual(prov['snapshot']['totaux']['ht_cents'], 36000)
        prov = self.approve_issue(prov, 'cle-provision-0001')
        fact.record_payment(self.desk, ACTOR, 'avocat', prov['id'], '432', '2026-10-06', 'virement')
        solde = fact.prepare(self.desk, ACTOR, 'avocat', 'facture', MID, {'sous_type': 'solde'})
        self.assertEqual(solde['snapshot']['totaux']['ttc_cents'], 144000)
        self.assertEqual(solde['snapshot']['totaux']['net_a_payer_cents'], 144000 - 43200)   # provision imputée, pas de seconde TVA
        summary = fact.matter_summary(self.desk, MID)
        self.assertEqual(summary['convention']['numero'], conv['number'])

    def test_suggestion_refusee_ne_revient_pas(self):
        from agent import facturation5655 as f55          # 5.6.55 : « sans convention » ne vise que les dossiers apparus après la mise en service
        f55.note_seen(self.desk)
        self.desk.db.execute('UPDATE b55_matter_seen SET baseline=0 WHERE matter=?', (MID,))
        self.desk.db.commit()
        props = [s for s in fact.suggestions(self.desk, [MID]) if s['kind'] == 'sans_convention']
        self.assertTrue(props)
        fact.decide_suggestion(self.desk, ACTOR, 'avocat', props[0]['key'], 'refusee')
        again = [s for s in fact.suggestions(self.desk, [MID]) if s['kind'] == 'sans_convention']
        self.assertEqual(again, [])

    def test_fichier_classe_dans_facturation_jamais_piece(self):
        from agent import modele5651
        self.assertEqual(modele5651.classify_name('/Dossiers/DEMO/FACTURATION/F-2026-0001 - Facture - Alpha.pdf'), 'gestion')


class SameSecond(base.Base):
    """5.6.54 : deux brouillons, avoirs ou estimations créés dans la même seconde (machine rapide, double clic) restent distincts."""

    invoice = Billing.invoice

    def setUp(self):
        base.Base.setUp(self)
        fact.save_entity(self.desk, 'admin@example.test', 'administrateur', ENTITY)
        fact.save_client(self.desk, ACTOR, 'avocat', MID, CLIENT)

    def test_creations_dans_la_meme_seconde(self):
        with patch.object(fact, '_now', return_value='2026-10-16T10:00:00+00:00'), patch.object(fees, '_now', return_value='2026-10-16T10:00:00+00:00'):
            a, b = self.invoice(), self.invoice()
            self.assertNotEqual(a['id'], b['id'])
            e1 = fees.save_estimate(self.desk, ACTOR, MID, {'mode': 'forfait', 'taux_horaire': '200', 'diligences': [{'minutes': 60}]})
            e2 = fees.save_estimate(self.desk, ACTOR, MID, {'mode': 'forfait', 'taux_horaire': '200', 'diligences': [{'minutes': 60}]})
            self.assertNotEqual(e1['id'], e2['id'])


class Payments(base.Base):
    """5.6.54 : deux règlements identiques sans référence le même jour sont deux règlements."""
    invoice = Billing.invoice
    approve_issue = Billing.approve_issue

    def setUp(self):
        base.Base.setUp(self)
        fact.save_entity(self.desk, 'admin@example.test', 'administrateur', ENTITY)
        fact.save_client(self.desk, ACTOR, 'avocat', MID, CLIENT)

    def test_reglements_sans_reference_distincts(self):
        inv = self.approve_issue(self.invoice())
        fact.record_payment(self.desk, ACTOR, 'avocat', inv['id'], '720', '2026-10-10', 'chèque')
        out = fact.record_payment(self.desk, ACTOR, 'avocat', inv['id'], '720', '2026-10-10', 'chèque')
        self.assertEqual(len(out['payments']), 2)
        self.assertEqual(out['payment_state'], 'reglee')


class Templates(unittest.TestCase):
    def test_huit_modeles_et_import_controle(self):
        self.assertEqual(len(models._catalog()), 8)
        good = io.BytesIO()
        with zipfile.ZipFile(good, 'w') as z:
            z.writestr('[Content_Types].xml', '<Types/>')
            z.writestr('word/document.xml', '<w:document xmlns:w="%s"><w:body><w:p><w:r><w:t>{{client_identite}}</w:t></w:r></w:p></w:body></w:document>' % models.W)
        self.assertEqual(models.inspect_docx(good.getvalue()), ['client_identite'])
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, 'w') as z:
            z.writestr('word/document.xml', '<w:document/>')
            z.writestr('word/vbaProject.bin', b'macro')
        with self.assertRaisesRegex(Stop, 'modele_avec_macros_refuse'):
            models.inspect_docx(bad.getvalue())
        with self.assertRaisesRegex(Stop, 'logo_png_ou_jpeg_requis'):
            models.check_logo(b'<svg onload="alert(1)"/>')

    def test_rendu_word_lignes_et_variables_manquantes(self):
        t = [x for x in models._catalog() if x['kind'] == 'facture'][0]
        raw, missing = models.render_docx(t, {'cabinet_identite': 'Cabinet Fictif'}, [{'libelle': 'A', 'qte': '1', 'prix_ht': '1 €', 'tva': '20 %', 'montant_ht': '1 €'},
                                                                                     {'libelle': 'B', 'qte': '2', 'prix_ht': '1 €', 'tva': '20 %', 'montant_ht': '2 €'}])
        xml = zipfile.ZipFile(io.BytesIO(raw)).read('word/document.xml').decode('utf-8')
        self.assertIn('BROUILLON', xml)
        self.assertNotIn('{{', xml)
        self.assertIn('numero_facture', missing)
        self.assertEqual(xml.count('>A<') + xml.count('>B<'), 2)               # une ligne de tableau par ligne du document


class Lot0Extra(base.Base):
    def setUp(self):
        super().setUp()
        self.view = self.connect('youtrust', caps=('signature.send_approved',))
        cx.test(self.desk, ACTOR, 'avocat', self.view['id'])

    prepare = base.Operations.prepare

    def test_F01_compte_change_avant_validation_aucun_post(self):
        op = self.prepare()
        cx.save(self.desk, ACTOR, 'avocat', {'id': self.view['id'], 'provider': 'youtrust', 'api_key': 'autre-cle-fictive-compte-b'})
        cx.test(self.desk, ACTOR, 'avocat', self.view['id'])                   # un test réussi ne revalide pas l'ancienne proposition
        self.fake.calls.clear()
        with self.assertRaisesRegex(Stop, 'compte_a_revalider'):
            cx.validate(self.desk, ACTOR, 'avocat', op['id'], op['version'])
        self.assertFalse([c for c in self.fake.calls if c[0] == 'POST'])
        new = cx.reprepare(self.desk, ACTOR, 'avocat', op['id'])
        self.assertEqual(new['state'], 'a_valider')

    def test_F02_reprise_sans_doublon(self):
        op = self.prepare()
        with patch.object(cx, '_document', side_effect=Stop('connexion_http_indisponible')):
            with self.assertRaises(Stop):
                cx.validate(self.desk, ACTOR, 'avocat', op['id'], op['version'])
        cur = self.desk.db.execute('SELECT state,version FROM connexion_ops5652 WHERE id=?', (op['id'],)).fetchone()
        self.assertEqual(cur['state'], 'echec_avant_envoi')
        self.fake.on('POST', '/v3/signature_requests', {'id': 'sr-9', 'status': 'draft'})
        self.fake.on('POST', '/signature_requests/sr-9/documents', {'id': 'doc-9'})
        self.fake.on('POST', '/signature_requests/sr-9/signers', {'id': 'sg-9'})
        self.fake.on('POST', '/signature_requests/sr-9/activate', {'status': 'ongoing'})
        out = cx.validate(self.desk, ACTOR, 'avocat', op['id'], cur['version'])
        self.assertEqual(out['state'], 'envoyee')
        self.assertEqual(len([c for c in self.fake.calls if c[0] == 'POST' and c[1].endswith('/signature_requests')]), 1)

    def test_F05_delegation_accordee_puis_retiree(self):
        view = cx.save(self.desk, ACTOR, 'avocat', {'provider': 'youtrust', 'portee': 'personnel', 'api_key': 'cle-perso-fictive',
                                                   'cap_signature_send_approved': True, 'delegues': 'collegue@example.test'})
        cx.test(self.desk, ACTOR, 'avocat', view['id'])
        row = cx._row(self.desk, view['id'])
        self.assertTrue(cx.can_use(row, 'collegue@example.test'))
        self.assertFalse(cx.can_use(row, 'autre@example.test'))
        cx.save(self.desk, ACTOR, 'avocat', {'id': view['id'], 'provider': 'youtrust', 'delegues': ''})
        self.assertFalse(cx.can_use(cx._row(self.desk, view['id']), 'collegue@example.test'))

    def test_U01_fiche_et_empreinte(self):
        op = self.prepare()
        s = cx.sheet(self.desk, ACTOR, 'avocat', op['id'])
        self.assertEqual(s['personnes'][0]['courriel'], 'alice@example.test')
        self.assertEqual(s['compte']['fournisseur'], 'Youtrust (Yousign)')
        with self.assertRaisesRegex(Stop, 'fiche_modifiee_depuis_affichage'):
            cx.validate(self.desk, ACTOR, 'avocat', op['id'], op['version'], 'empreinte-perimee')
        html = cx.sheet_html(self.desk, '/p', ACTOR, 'avocat', op['id'])
        self.assertIn('Destinataires et ordre', html)
        self.assertIn(s['empreinte'], html)


class RoutinesExtra(base.Base):
    form = base.Routines.form

    def test_F06_meme_cle_contenu_different_409(self):
        rt.create(self.desk, ACTOR, 'avocat', self.form(scope='cabinet'))
        with self.assertRaisesRegex(Stop, 'cle_idempotence_deja_utilisee'):
            rt.create(self.desk, ACTOR, 'avocat', self.form(scope='cabinet', title='Autre intention'))

    def test_F07_lancement_manuel_permis_en_pause(self):
        row = rt.create(self.desk, ACTOR, 'avocat', self.form())
        row = rt.set_enabled(self.desk, ACTOR, 'avocat', row['id'], False, row['version'])
        occ = rt.run_now(self.desk, ACTOR, 'avocat', row['id'], 'clic-manuel-0001')
        started = rt.start_occurrence(self.desk, occ['id'])
        self.assertEqual(rt._occurrence(self.desk, occ['id'])['state'], 'en_cours')
        self.assertEqual(started['manual'], 1)

    def test_F08_categories_de_droits(self):
        self.assertEqual(membres.category('POST', '/api440/m5652/connexion/enregistrer'), 'reglages')
        self.assertEqual(membres.category('POST', '/api440/m5652/file/decider'), 'valider')
        self.assertIsNone(membres.category('POST', '/api440/m5652/connexion-perso/enregistrer'))


if __name__ == '__main__':
    unittest.main()
