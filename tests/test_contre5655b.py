"""Complément indépendant à l'audit 5.6.55 : fixtures fictives, aucun service réel.
Les assertions décrivent le comportement attendu après correction.
"""
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

import test_v5652 as fixture
import test_v5653 as billing_fixture
import test_v5650 as web_fixture
from agent import facturation5653 as fact, facturx5653 as fx, crm5636 as crm
from agent import facturation5655 as dashboard
from agent import membres5650 as mb
from agent.common import Stop
from agent.desk import Desk

OBSERVATIONS = {}
ACTOR = billing_fixture.ACTOR
MID = fixture.MID


class Billing(fixture.Base):
    invoice = billing_fixture.Billing.invoice

    def setUp(self):
        super().setUp()
        mb.set_viewer(None)
        self.addCleanup(mb.set_viewer, None)
        self.admin = ('admin@example.test', 'administrateur')
        fact.save_entity(self.desk, *self.admin, billing_fixture.ENTITY)
        fact.save_client(self.desk, ACTOR, 'avocat', MID, billing_fixture.CLIENT)
        p = patch.object(fx, 'office_pdf', return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def emit(self, d, key):
        d = fact.approve(self.desk, ACTOR, 'avocat', d['id'], d['version'])
        with patch.object(fact, 'publish', return_value={}):
            return fact.issue(self.desk, ACTOR, 'avocat', d['id'], key)

    def convention(self):
        d = fact.prepare(self.desk, ACTOR, 'avocat', 'convention', MID, {
            'mode': 'forfait', 'lignes': [{'libelle': 'Mission fictive', 'quantite': '1', 'prix_unitaire_ht': '1200'}]})
        missing = fact.check(self.desk, ACTOR, 'avocat', d['id'])['variables_manquantes']
        d = fact.update(self.desk, ACTOR, 'avocat', d['id'], {'textes': {k: 'Valeur fictive' for k in missing}}, d['version'])
        d = fact.approve(self.desk, ACTOR, 'avocat', d['id'], d['version'])
        return fact.mark_engagement(self.desk, ACTOR, 'avocat', d['id'], 'signe', d['version'], 'Document signé fictif')

    def test_N16a_facture_abandonnee_pendant_controle_non_emise(self):
        d = self.invoice()
        d = fact.approve(self.desk, ACTOR, 'avocat', d['id'], d['version'])
        other = Desk(self.desk.c)
        self.addCleanup(other.db.close)
        original_check = fact.check

        def interleave(*args, **kwargs):
            result = original_check(*args, **kwargs)
            fact.abandon(other, ACTOR, 'avocat', d['id'], fact.get(other, d['id'])['version'])
            return result

        error = ''
        with patch.object(fact, 'check', side_effect=interleave), patch.object(fact, 'publish', return_value={}):
            try:
                fact.issue(self.desk, ACTOR, 'avocat', d['id'], 'concurrence-annulation-001')
            except Stop as exc:
                error = str(exc)
        current = fact.get(self.desk, d['id'])
        OBSERVATIONS['N16a'] = {'etat_final': current['state'], 'numero': current['number'], 'erreur': error}
        self.assertEqual(current['state'], 'abandonne', 'La dernière lecture sous verrou doit faire respecter l’abandon concurrent.')
        self.assertIsNone(current['number'])

    def test_N16b_modification_concurrente_non_ecrasee_par_emission(self):
        d = self.invoice('1200')
        d = fact.approve(self.desk, ACTOR, 'avocat', d['id'], d['version'])
        other = Desk(self.desk.c)
        self.addCleanup(other.db.close)
        original_check = fact.check

        def interleave(*args, **kwargs):
            result = original_check(*args, **kwargs)
            fact.update(other, ACTOR, 'avocat', d['id'], {'lignes': [
                {'libelle': 'Nouvelle quantité validée par l’utilisateur', 'quantite': '1', 'prix_unitaire_ht': '900'}]}, fact.get(other, d['id'])['version'])
            return result

        error = ''
        with patch.object(fact, 'check', side_effect=interleave), patch.object(fact, 'publish', return_value={}):
            try:
                fact.issue(self.desk, ACTOR, 'avocat', d['id'], 'concurrence-modification-001')
            except Stop as exc:
                error = str(exc)
        current = fact.get(self.desk, d['id'])
        OBSERVATIONS['N16b'] = {'etat_final': current['state'], 'ht_cents': current['snapshot']['totaux']['ht_cents'], 'erreur': error}
        self.assertEqual(current['snapshot']['totaux']['ht_cents'], 90000)
        self.assertIsNone(current['number'], 'Le nouveau contenu exige une nouvelle approbation.')

    def test_N17_document_emis_utilise_identite_emetteur_figee(self):
        d = self.emit(self.invoice(), 'emission-identite-001')
        original_name = d['snapshot']['emis']['emetteur']['nom']
        fact.save_entity(self.desk, *self.admin, {'nom': 'Nouveau Cabinet Fictif 772'})
        files, report, _ = fact.build_files(self.desk, fact.get(self.desk, d['id']))
        from xml.etree import ElementTree as ET
        with zipfile.ZipFile(io.BytesIO(files['docx'])) as z:
            xml = ET.fromstring(z.read('word/document.xml'))
            text = ' '.join(n.text or '' for n in xml.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'))
        OBSERVATIONS['N17'] = {'nom_initial': original_name, 'nom_actuel': fact.entity(self.desk)['identite']['nom'],
            'word_contient_nom_actuel': 'Nouveau Cabinet Fictif 772' in text,
            'cii_contient_nom_initial': original_name in files['cii'].decode()}
        self.assertNotIn('Nouveau Cabinet Fictif 772', text, 'Word/PDF et XML doivent provenir du même instantané émis.')
        self.assertIn(original_name, text)

    def test_N18_cumul_avoirs_emis_ne_depasse_pas_facture(self):
        inv = self.emit(self.invoice('1200'), 'emission-avoirs-001')
        lines = [{'libelle': 'Rectification partielle fictive', 'quantite': '1', 'prix_unitaire_ht': '800'}]
        first = self.emit(fact.credit_note(self.desk, ACTOR, 'avocat', inv['id'], 'Première rectification', lines), 'avoir-cumul-001')
        error = ''
        try:
            self.emit(fact.credit_note(self.desk, ACTOR, 'avocat', inv['id'], 'Deuxième rectification', lines), 'avoir-cumul-002')
        except Stop as exc:
            error = str(exc)
        credits = [x for x in fact.documents(self.desk, MID) if x['kind'] == 'avoir' and x['number']]
        total = sum(x['ttc_cents'] for x in credits)
        OBSERVATIONS['N18'] = {'facture_ttc_cents': inv['snapshot']['totaux']['ttc_cents'], 'avoirs_ttc_cents': total,
                                'nombre_avoirs_emis': len(credits), 'erreur': error}
        self.assertLessEqual(total, inv['snapshot']['totaux']['ttc_cents'])

    def test_N19_provision_partiellement_payee_non_facturee_deux_fois(self):
        self.convention()
        p = self.emit(fact.prepare(self.desk, ACTOR, 'avocat', 'facture', MID, {'sous_type': 'provision', 'provision_pct': '30'}), 'provision-partielle-001')
        fact.record_payment(self.desk, ACTOR, 'avocat', p['id'], '200', '2026-10-10', 'virement', 'PAY-200')
        final = self.emit(fact.prepare(self.desk, ACTOR, 'avocat', 'facture', MID, {'sous_type': 'solde'}), 'solde-partiel-001')
        summary = fact.matter_summary(self.desk, MID)
        OBSERVATIONS['N19'] = {'prix_mission_ttc_cents': 144000, 'encaisse_cents': 20000,
            'provision_ttc_cents': p['snapshot']['totaux']['ttc_cents'], 'imputations_solde': final['snapshot']['imputations'],
            'solde_connu_cents': summary['solde_connu_cents']}
        self.assertEqual(summary['solde_connu_cents'], 144000 - 20000, 'Une seule dette économique doit subsister pour la mission.')

    def test_N20_totaux_sur_plus_de_200_documents_non_tronques(self):
        first = self.emit(self.invoice('1'), 'total-200-premier')
        row = dict(self.desk.db.execute('SELECT * FROM b53_documents WHERE id=?', (first['id'],)).fetchone())
        columns = list(row)
        sql = 'INSERT INTO b53_documents(' + ','.join(columns) + ') VALUES(' + ','.join('?' for _ in columns) + ')'
        for i in range(1, 201):
            clone = dict(row)
            clone.update(id='fin-total-fictif-%03d' % i, number='F-TEST-%04d' % i, issue_key='total-fictif-%04d' % i, request_key=None)
            snap = json.loads(clone['snapshot_json'])
            snap['emis']['numero'] = clone['number']
            clone['snapshot_json'] = json.dumps(snap)
            self.desk.db.execute(sql, [clone[k] for k in columns])
        self.desk.db.commit()
        summary = fact.matter_summary(self.desk, MID)
        count = self.desk.db.execute('SELECT COUNT(*) FROM b53_documents WHERE matter=? AND number IS NOT NULL', (MID,)).fetchone()[0]
        OBSERVATIONS['N20'] = {'documents_emis': count, 'ht_attendu_cents': 20100,
                               'ht_affiche_cents': summary['deja_facture_ht_cents'], 'lecture_partielle': summary['lecture_partielle']}
        self.assertEqual(summary['deja_facture_ht_cents'], 20100, 'Le total doit porter sur tous les documents, même si la liste est paginée.')

    def test_N21_reference_paiement_reutilisee_avec_autre_montant_signalee(self):
        inv = self.emit(self.invoice(), 'paiement-conflit-001')
        fact.record_payment(self.desk, ACTOR, 'avocat', inv['id'], '400', '2026-10-10', 'virement', 'MEME-REF')
        error = ''
        try:
            fact.record_payment(self.desk, ACTOR, 'avocat', inv['id'], '500', '2026-10-10', 'virement', 'MEME-REF')
        except Stop as exc:
            error = str(exc)
        pays = fact.get(self.desk, inv['id'])['payments']
        events = [dict(r) for r in self.desk.db.execute("SELECT detail_json FROM b53_events WHERE document=? AND kind='reglement'", (inv['id'],))]
        OBSERVATIONS['N21'] = {'erreur': error, 'montants_enregistres': [p['amount_cents'] for p in pays], 'evenements': events}
        self.assertTrue(error, 'Même référence avec autre contenu : conflit explicite, pas faux succès.')

    def test_N24_fichier_emis_modifie_detecte_avant_republication(self):
        inv = self.emit(self.invoice(), 'publication-integrite-001')
        published = fact.publish(self.desk, inv['id'])
        target = self.root / published['files']['docx']['chemin'].lstrip('/')
        self.assertTrue(target.is_file())
        target.write_bytes(b'Fichier fictif modifie apres emission')
        error = ''
        try:
            result = fact.publish(self.desk, inv['id'])
        except Stop as exc:
            result, error = fact.get(self.desk, inv['id']), str(exc)
        import hashlib
        matches = hashlib.sha256(target.read_bytes()).hexdigest() == result['files']['docx']['sha256']
        OBSERVATIONS['N24'] = {'etat_fichiers': result['files_state'], 'empreinte_concordante': matches, 'erreur': error}
        self.assertTrue(error or result['files_state'] != 'publies' or matches,
                        'Un fichier présent dans files_json doit être relu ou identifié comme non revérifié.')

    def test_N25_date_calendaire_impossible_refusee(self):
        inv = self.emit(self.invoice(), 'paiement-date-001')
        error = ''
        try:
            fact.record_payment(self.desk, ACTOR, 'avocat', inv['id'], '100', '2026-02-31', 'virement', 'DATE-IMPOSSIBLE')
        except Stop as exc:
            error = str(exc)
        OBSERVATIONS['N25'] = {'erreur': error, 'dates_enregistrees': [p['day'] for p in fact.get(self.desk, inv['id'])['payments']]}
        self.assertTrue(error, 'Une expression régulière ne valide pas une date du calendrier.')

    def test_N26_indicateurs_nincluent_pas_dossier_reserve(self):
        inv = self.emit(self.invoice(), 'indicateurs-reserves-001')
        fact.record_payment(self.desk, ACTOR, 'avocat', inv['id'], '400', '2026-10-17', 'virement', 'RESERVE-400')
        mb.set_viewer(frozenset({MID}))
        result = dashboard.dashboard(self.desk)
        OBSERVATIONS['N26'] = {'dossier_masque': MID, 'impayes_cents': result['impayes_cents'],
                                'impayes_nombre': result['impayes_nombre'], 'encaisse_mois_cents': result['encaisse_mois_cents']}
        self.assertEqual(result['impayes_cents'], 0, 'Les agrégats doivent appliquer le même périmètre que la liste des dossiers.')
        self.assertEqual(result['encaisse_mois_cents'], 0)

    def test_N27_echec_emission_annule_reservation_numero(self):
        d = self.invoice()
        d = fact.approve(self.desk, ACTOR, 'avocat', d['id'], d['version'])
        original_client = fact.client
        calls = []

        def client_during_issue(*args, **kwargs):
            was_in_transaction = self.desk.db.in_transaction
            result = original_client(*args, **kwargs)
            if was_in_transaction:
                calls.append({'avant': was_in_transaction, 'apres': self.desk.db.in_transaction})
                raise Stop('panne_fictive_apres_lecture_client')
            return result

        with patch.object(fact, 'client', side_effect=client_during_issue), patch.object(fact, 'publish', return_value={}):
            with self.assertRaisesRegex(Stop, 'panne_fictive_apres_lecture_client'):
                fact.issue(self.desk, ACTOR, 'avocat', d['id'], 'rollback-numero-001')
        current = fact.get(self.desk, d['id'])
        sequences = [dict(r) for r in self.desk.db.execute('SELECT series,last_number FROM b53_sequences')]
        OBSERVATIONS['N27'] = {'transactions': calls, 'numero_document': current['number'], 'etat_document': current['state'],
                                'sequences_apres_rollback': sequences}
        self.assertTrue(calls, 'Le défaut doit être injecté à l’intérieur de la transaction d’émission.')
        self.assertEqual(sequences, [], 'Une lecture de profil ne doit pas valider la numérotation avant le COMMIT métier.')

    def test_C04_repetition_identique_paiement_ne_double_pas_somme(self):
        inv = self.emit(self.invoice(), 'temoin-paiement-001')
        for _ in range(2):
            fact.record_payment(self.desk, ACTOR, 'avocat', inv['id'], '400', '2026-10-10', 'virement', 'REF-IDENTIQUE')
        out = fact.get(self.desk, inv['id'])
        self.assertEqual(len(out['payments']), 1)
        self.assertEqual(out['payments'][0]['amount_cents'], 40000)


class Contacts(fixture.Base):
    def test_N22_homonymes_emails_distincts_non_fusionnes(self):
        a = crm.find_or_create(self.desk, {'name': 'Durand Fictif', 'first_name': 'Camille', 'category': 'client', 'email': 'camille.a@example.test'})
        b = crm.find_or_create(self.desk, {'name': 'Durand Fictif', 'first_name': 'Camille', 'category': 'client', 'email': 'camille.b@example.test'})
        OBSERVATIONS['N22'] = {'meme_identifiant': a['id'] == b['id'], 'email_restitue_second_contact': b['email']}
        self.assertNotEqual(a['id'], b['id'], 'L’homonymie ne suffit pas pour fusionner deux identités portant des adresses différentes.')

    def test_N23_creation_contact_et_lien_atomiques(self):
        before = len(crm.search(self.desk))
        with self.assertRaises(Stop):
            crm.save(self.desk, {'name': 'Contact Fictif Invalide', 'email': 'invalide@example.test', 'matter': MID, 'role': 'role-inexistant'})
        after = len(crm.search(self.desk))
        OBSERVATIONS['N23'] = {'contacts_avant': before, 'contacts_apres_refus': after}
        self.assertEqual(after, before, 'Une opération rejetée ne doit pas laisser une moitié de création validée.')


class ReservedContacts(unittest.TestCase):
    def setUp(self):
        self.f = web_fixture.ServerAccounts('run')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.admin = self.f.admin_cookie()
        self.lawyer = self.f.add(self.admin, 'autre@example.test', 'avocat')
        self.desk = Desk(self.f.f.c)
        self.addCleanup(self.desk.db.close)
        mb.clear()
        self.addCleanup(mb.clear)
        self.contact = crm.save(self.desk, {'name': 'Contact Confidentiel Fictif', 'email': 'reserve@example.test',
            'notes': 'NOTE-RESERVEE-FICTIVE-772', 'matter': MID, 'role': 'client'})
        mb.set_reserve(str(self.f.state), MID, ['avocate@example.test'])

    def test_N15_dossier_reserve_non_expose_via_crm(self):
        direct = self.f.request('/dossier', query='id=' + MID, cookie=self.lawyer)
        self.assertEqual(direct['status'], '403 Forbidden')
        fiche = self.f.request('/api440/m5636/contacts/fiche', query='id=' + self.contact['id'], cookie=self.lawyer)
        listing = self.f.request('/api440/m5636/contacts', cookie=self.lawyer)
        edit = self.f.api('m5636/contacts/archiver', {'contact': self.contact['id'], 'archived': True}, self.lawyer)
        current = crm.get(self.desk, self.contact['id'])
        OBSERVATIONS['N15'] = {'dossier': direct['status'], 'fiche': fiche['status'], 'liste': listing['status'], 'archivage': edit['status'],
            'note_confidentielle_visible': 'NOTE-RESERVEE-FICTIVE-772' in fiche['body'],
            'identifiant_dossier_expose': MID in listing['body'], 'contact_archive': bool(current['archived'])}
        self.assertIn(fiche['status'], ('403 Forbidden', '404 Not Found'))
        self.assertNotIn('NOTE-RESERVEE-FICTIVE-772', listing['body'])
        self.assertFalse(current['archived'])

    def test_C05_administrateur_conserve_acces_contact(self):
        response = self.f.request('/api440/m5636/contacts/fiche', query='id=' + self.contact['id'], cookie=self.admin)
        self.assertEqual(response['status'], '200 OK')
        self.assertIn('NOTE-RESERVEE-FICTIVE-772', response['body'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
