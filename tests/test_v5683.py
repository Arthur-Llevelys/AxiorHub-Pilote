"""5.6.83 : recette du complément : compétences (SK), cessions (CT), interface (UX), formalités (OP), formulaires officiels, lecture des courriels et de
l'agenda par la discussion. Données fictives, aucun appel réseau réel."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

import test_v5636 as t36
from agent import acces5683, apercu5679, competences5632, competences5683, corporate5636 as co, formulaires5683 as forms, liaisons5683, liens5683, membres5650
from agent import operations5683 as ops, regles5683 as rules, web5683, yaml5683
from agent.common import Stop
from agent.erreurs5634 import Qualified
from agent.index import DocumentIndex

MID = t36.MID
FULL = {'cessionnaire_nom': 'Lucie BERNARD', 'cessionnaire_email': 'lucie@example.test', 'acquereur_qualite': 'tiers', 'nombre': '100', 'categorie': 'ordinaires', 'prix_unitaire': '5',
        'date_cession': '2026-10-12', 'acte_ecrit': 'oui', 'preponderance': 'non', 'regime': 'ordinaire', 'pacte_lu': 'aucun', 'demembrement': 'non', 'indivision': 'non',
        'regime_matrimonial': 'non', 'nantissement': 'non', 'options': 'non', 'financement': 'non', 'ville': 'Valbrune', 'ctrl_agrement': 'requis', 'ctrl_preemption': 'non_requis',
        'ctrl_inalienabilite': 'non_requis', 'ctrl_sortie_conjointe': 'non_requis', 'ctrl_salaries': 'non_requis', 'ctrl_pouvoirs': 'non_requis', 'ctrl_prix': 'non_requis'}


class Droits(t36._CorporateBase):
    def calc(self, **kw):
        base = {'nature': 'actions', 'prix_total': '50000', 'nombre': '100', 'total_titres': '1000', 'preponderance': 'non', 'regime': 'ordinaire', 'date_cession': '2026-10-12'}
        return rules.droits_cession(self.desk, {**base, **kw})

    def test_ct09_actions_parts_preponderance(self):
        self.assertEqual(self.calc()['droits'], '50.00')                              # 0,1 %
        parts = self.calc(nature='parts')                                             # (500 − 23) × 100 × 3 %
        self.assertEqual(parts['droits_exacts'], '1431.00')
        self.assertIn(('Assiette nette', '47700.00'), parts['composantes'])
        heavy = self.calc(nature='parts', preponderance='oui')                        # 5 %, sans abattement
        self.assertEqual(heavy['droits'], '2500.00')
        self.assertFalse([c for c in heavy['composantes'] if 'Abattement' in c[0]])

    def test_ct09_assiette_arrondi_et_abattement_plafonne(self):
        self.assertEqual(self.calc(charges='10000')['droits'], '60.00')               # prix + charges
        self.assertEqual(self.calc(valeur_reelle='80000')['droits'], '80.00')         # valeur réelle supérieure
        self.assertEqual(self.calc(valeur_reelle='100')['droits'], '50.00')           # valeur inférieure : ignorée
        self.assertEqual(self.calc(prix_total='12345')['droits_exacts'], '12.35')
        self.assertEqual(self.calc(prix_total='12345')['droits'], '12.00')
        self.assertEqual(self.calc(prix_total='12500')['droits'], '13.00')            # 12,50 : la moitié compte pour un euro
        small = self.calc(nature='parts', prix_total='1000', nombre='500')            # abattement supérieur à la valeur : base nulle
        self.assertEqual(small['droits'], '0.00')

    def test_donnee_absente_a_confirmer_jamais_supposee(self):
        for key, word in (('prix_total', 'prix'), ('nombre', 'nombre'), ('preponderance', 'prépondérance'), ('regime', 'régime'), ('date_cession', 'date')):
            out = self.calc(**{key: ''})
            self.assertEqual(out['etat'], 'a_confirmer', key)
            self.assertIsNone(out['droits'])
            self.assertTrue(any(word in m for m in out['manque']), key)
        self.assertEqual(self.calc(nature='parts', total_titres='')['etat'], 'a_confirmer')
        self.assertEqual(self.calc(date_cession='2023-12-31')['etat'], 'a_confirmer')

    def test_exclusion_sauf_preponderance(self):
        self.assertEqual(self.calc(regime='exclusion')['etat'], 'hors_perception')
        self.assertEqual(self.calc(regime='exclusion', preponderance='oui')['droits'], '2500.00')

    def test_regles_datees_et_validation_par_version(self):
        rows = rules.catalogue(self.desk)
        self.assertTrue(all(r['source'] and r['version'] and r['application'] and r['lu_le'] for r in rows))
        self.assertTrue(all(r['etat'] == 'a_valider' for r in rows))
        rules.validate(self.desk, 'droits.parts', 'avocate')
        self.assertEqual(rules.state_of(self.desk, 'droits.parts')['etat'], 'validee')
        with patch.dict(rules.RULES['droits.parts'], {'version': '2030-01-01'}):       # nouvelle version : à revalider
            self.assertEqual(rules.state_of(self.desk, 'droits.parts')['etat'], 'a_valider')

    def test_ct08_2759(self):
        self.assertFalse(rules.formulaire_2759('actions', True)['applicable'])
        self.assertFalse(rules.formulaire_2759('parts', True)['applicable'])
        self.assertFalse(rules.formulaire_2759('parts', False)['applicable'])          # un écrit reste exigé
        self.assertTrue(rules.formulaire_2759('actions', False)['applicable'])

    def test_delais(self):
        self.assertEqual(rules.echeance('enregistrement.acte', '2026-01-31', 1)['date'], '2026-02-28')
        self.assertEqual(rules.echeance('enregistrement.acte', '', 1)['etat'], 'a_confirmer')
        self.assertEqual(rules.delai_agrement(['2026-03-01'], 3)['etat'], 'a_confirmer')           # notifications incomplètes
        self.assertEqual(rules.delai_agrement(['2026-03-01', '2026-03-09', '2026-03-04'], 3)['date'], '2026-06-09')     # dernière notification


class Cessions(t36._CorporateBase):
    def setUp(self):
        super().setUp()
        self.docs.files['/Clients/Signe.pdf'] = b'%PDF-1.4 fictif'

    def matter_path(self):
        from agent.common import load_matters
        return next(m for m in load_matters(self.desk.c) if m['id'] == MID)['path'].rstrip('/')

    def piece(self, name='Acte signe.pdf'):
        path = self.matter_path() + '/' + name
        self.docs.files[path] = b'%PDF-1.4 fictif'
        return path

    def company(self, forme, name='ZORVAL'):
        cid = co.company_save(self.desk, {'denomination': name, 'forme': forme, 'capital': '1000', 'nominal': '1', 'siege': '1 rue Test, Valbrune', 'matter': MID})['id']
        co.subscription(self.desk, {'company': cid, 'name': 'Paul MARTIN', 'quantity': '600'})
        co.subscription(self.desk, {'company': cid, 'name': 'Anne DUPONT', 'quantity': '400'})
        return cid

    def dossier(self, cid=None, **extra):
        out = ops.open_cession(self.desk, {'company': cid or self.cid})
        statuts = self.piece('Statuts.pdf')
        ops.update_fiche(self.desk, {'id': out['id'], 'cedant': 'Paul MARTIN', 'statuts_lus': statuts, **FULL, **extra}, 'avocate')
        return out['id']

    def suite(self, did, **kw):
        return ops.prepare_next(self.desk, {'id': did, 'confirmer': '1', **kw}, 'avocate', 'avocat', self.docs)

    def to_redaction(self, did):
        for _ in range(5):
            self.suite(did)
        self.assertEqual(ops.next_step(self.desk, did)['id'], 'redaction')

    def generated(self, did):
        self.to_redaction(did)
        out = self.suite(did)
        co.generate(self.desk, ops._row(self.desk, did)['operation'], dav=self.docs)
        return out

    def to_signature(self, did):
        self.generated(did)
        self.suite(did)        # documents prêts
        self.suite(did)        # relecture
        ops.add_proof(self.desk, {'id': did, 'type': 'signature_constatee', 'date': '2026-10-12', 'piece': self.piece()}, 'avocate', self.docs)
        self.suite(did)        # signature rattachée

    def test_ct01_parts_sans_ordre_de_mouvement(self):
        cid = self.company('SARL')
        did = self.dossier(cid)
        self.generated(did)
        names = [p.rsplit('/', 1)[-1] for p in json.loads(self.desk.db.execute('SELECT result FROM operations5636 WHERE id=?', (ops._row(self.desk, did)['operation'],)).fetchone()[0])['files']]
        self.assertFalse([n for n in names if 'mouvement' in n.lower() or 'teneur' in n.lower() or 'inscription' in n.lower()], names)
        self.assertTrue(any(n.startswith('Acte de cession de parts') for n in names))
        self.assertTrue(any(n.startswith('Notification') for n in names))
        self.assertTrue(any('agrément' in n for n in names))         # acquéreur tiers
        self.assertNotIn('ordre_mouvement', co.documents_of('cession', 'parts sociales'))

    def test_ct02_actions_objets_distincts(self):
        did = self.dossier()
        self.generated(did)
        row = self.desk.db.execute('SELECT docs FROM operations5636 WHERE id=?', (ops._row(self.desk, did)['operation'],)).fetchone()
        docs = json.loads(row['docs'])
        self.assertIn('ordre_mouvement', docs)
        self.assertIn('instruction_teneur', docs)
        self.assertNotIn('attestation_inscription', docs)            # seulement après l'inscription réelle

    def test_ct02_statuts_et_pacte_requis(self):
        out = ops.open_cession(self.desk, {'company': self.cid})
        ops.update_fiche(self.desk, {'id': out['id'], 'cedant': 'Paul MARTIN', **{k: v for k, v in FULL.items() if k != 'pacte_lu'}}, 'avocate')
        self.suite(out['id'])
        res = self.suite(out['id'])
        self.assertEqual(res['etat'], 'bloque')
        self.assertIn('Statuts', res['message'])
        self.assertIn('Pacte', res['message'])

    def test_ct03_generer_ne_touche_pas_les_registres(self):
        did = self.dossier()
        before = self.desk.db.execute('SELECT COUNT(*) FROM movements5636').fetchone()[0], co.holdings(self.desk, self.cid)
        self.generated(did)
        self.assertEqual(before, (self.desk.db.execute('SELECT COUNT(*) FROM movements5636').fetchone()[0], co.holdings(self.desk, self.cid)))

    def test_ct04_realisation_sans_preuve_refusee(self):
        did = self.dossier()
        self.generated(did)
        with self.assertRaises(Stop) as ctx:
            ops.realize(self.desk, {'id': did, 'date_signature': '2026-10-12', 'date_ordre': '2026-10-12', 'date_inscription': '2026-10-13'}, 'avocate', self.docs)
        self.assertEqual(str(ctx.exception), 'signature_non_constatee')
        op = ops._row(self.desk, did)['operation']
        count = self.desk.db.execute("SELECT COUNT(*) FROM movements5636 WHERE kind='cession'").fetchone()[0]
        with self.assertRaises(Stop) as direct:                       # le service de base refuse aussi un simple texte
            co.realize(self.desk, {'id': op, 'date': '2026-10-12', 'justificatif': 'acte signé fictif'}, dav=self.docs)
        self.assertEqual(str(direct.exception), 'piece_justificative_requise')
        with self.assertRaises(Stop) as outside:
            co.realize(self.desk, {'id': op, 'date': '2026-10-12', 'justificatif': 'acte', 'piece': '/Clients/Signe.pdf'}, dav=self.docs)
        self.assertEqual(str(outside.exception), 'piece_hors_dossier')
        with self.assertRaises(Stop):                                 # pièce absente de Nextcloud
            co.realize(self.desk, {'id': op, 'date': '2026-10-12', 'justificatif': 'acte', 'piece': self.matter_path() + '/Absente.pdf'}, dav=self.docs)
        self.assertEqual(count, self.desk.db.execute("SELECT COUNT(*) FROM movements5636 WHERE kind='cession'").fetchone()[0])

    def test_ct05_deux_cessions_des_memes_titres(self):
        a, b = self.dossier(nombre='5000'), self.dossier(nombre='5000')
        self.to_signature(a)
        self.to_signature(b)
        dates = {'date_signature': '2026-10-12', 'date_ordre': '2026-10-12', 'date_inscription': '2026-10-13'}
        ops.realize(self.desk, {'id': a, **dates}, 'avocate', self.docs)
        with self.assertRaises(Stop) as ctx:
            ops.realize(self.desk, {'id': b, **dates}, 'avocate', self.docs)
        self.assertEqual(str(ctx.exception), 'cession_titres_insuffisants')
        self.assertEqual({r['name']: r['quantity'] for r in co.holdings(self.desk, self.cid)[0]}['Paul MARTIN'], 1000)

    def test_ct06_realisation_repetee_une_seule_ecriture(self):
        did = self.dossier()
        self.to_signature(did)
        data = {'id': did, 'date_signature': '2026-10-12', 'date_ordre': '2026-10-12', 'date_inscription': '2026-10-13'}
        ops.realize(self.desk, data, 'avocate', self.docs)
        again = ops.realize(self.desk, data, 'avocate', self.docs)
        self.assertTrue(again['deja'])
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM movements5636 WHERE kind='cession'").fetchone()[0], 1)

    def test_op02_reprise_apres_panne_rapproche_sans_double_mouvement(self):
        did = self.dossier()
        self.to_signature(did)
        data = {'id': did, 'date_signature': '2026-10-12', 'date_ordre': '2026-10-12', 'date_inscription': '2026-10-13'}
        with patch.object(ops, '_closed', side_effect=RuntimeError('panne')):
            with self.assertRaises(RuntimeError):
                ops.realize(self.desk, data, 'avocate', self.docs)
        self.assertEqual(ops._row(self.desk, did)['state'], 'en_cours')
        ops.realize(self.desk, data, 'avocate', self.docs)
        self.assertEqual(ops._row(self.desk, did)['state'], 'realisee')
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM movements5636 WHERE kind='cession'").fetchone()[0], 1)

    def test_ct07_dates_distinctes_et_transfert_a_l_inscription(self):
        did = self.dossier()
        self.to_signature(did)
        with self.assertRaises(Qualified):
            ops.realize(self.desk, {'id': did, 'date_signature': '2026-10-12'}, 'avocate', self.docs)
        ops.realize(self.desk, {'id': did, 'date_signature': '2026-10-12', 'date_ordre': '2026-10-13', 'date_inscription': '2026-10-20'}, 'avocate', self.docs)
        move = self.desk.db.execute("SELECT day FROM movements5636 WHERE kind='cession'").fetchone()
        self.assertEqual(move['day'], '2026-10-20')
        dates = ops._row(self.desk, did)['data']['dates']
        self.assertEqual((dates['date_signature'], dates['date_ordre'], dates['date_inscription']), ('2026-10-12', '2026-10-13', '2026-10-20'))

    def test_attestation_apres_inscription_reelle(self):
        did = self.dossier()
        self.to_signature(did)
        ops.realize(self.desk, {'id': did, 'date_signature': '2026-10-12', 'date_ordre': '2026-10-13', 'date_inscription': '2026-10-20'}, 'avocate', self.docs)
        self.suite(did)
        self.suite(did) if ops.next_step(self.desk, did)['id'] == 'formalites' else None
        rows = self.desk.db.execute("SELECT path FROM deliverables5683 WHERE dossier=? AND doc='attestation_inscription'", (did,)).fetchall()
        self.assertEqual(len(rows), 1)
        ops.final_attestation(self.desk, ops._row(self.desk, did), self.docs)      # reprise : pas de second dépôt
        self.assertEqual(len(self.desk.db.execute("SELECT path FROM deliverables5683 WHERE dossier=? AND doc='attestation_inscription'", (did,)).fetchall()), 1)

    def test_ct08_parts_sans_acte_bloquees(self):
        cid = self.company('SARL')
        did = self.dossier(cid, acte_ecrit='non')
        for _ in range(3):
            self.suite(did)
        res = self.suite(did)
        self.assertEqual(res['etat'], 'bloque')
        self.assertIn('écrit', res['message'])

    def test_ct10_forme_non_couverte(self):
        cid = self.company('SCI')
        self.assertEqual(ops.branch_of('SCI'), ('parts', 'limite'))
        did = self.dossier(cid)
        self.to_redaction(did)
        with self.assertRaises(Stop) as ctx:
            self.suite(did)
        self.assertEqual(str(ctx.exception), 'perimetre_limite_a_confirmer')
        ops.update_fiche(self.desk, {'id': did, 'preparation_manuelle': '1'}, 'avocate')
        self.assertIn('job', self.suite(did))
        html = ops.page_html(self.desk, '', co.company(self.desk, cid), {'dossier': did})
        self.assertIn('Périmètre limité', html)
        self.assertNotIn('conforme', html.lower())

    def test_ux01_prix_change_documents_perimes_signe_conserve(self):
        did = self.dossier()
        self.generated(did)
        dos = ops._row(self.desk, did)
        ops.sync_deliverables(self.desk, dos)
        rows = self.desk.db.execute('SELECT id, doc FROM deliverables5683 WHERE dossier=?', (did,)).fetchall()
        signed = next(r for r in rows if r['doc'] == 'acte_cession')
        ops.mark_signed(self.desk, {'id': did, 'document': signed['id']}, 'avocate')
        with self.assertRaises(Qualified) as ctx:
            ops.update_fiche(self.desk, {'id': did, 'prix_unitaire': '6'}, 'avocate')
        self.assertIn('Ordre de mouvement', str(ctx.exception.detail['precision']))
        self.assertEqual(ops._row(self.desk, did)['data']['prix_unitaire'], '5')
        ops.update_fiche(self.desk, {'id': did, 'prix_unitaire': '6', 'confirmer': '1'}, 'avocate')
        states = {r['id']: r['state'] for r in self.desk.db.execute('SELECT id, state FROM deliverables5683 WHERE dossier=?', (did,))}
        self.assertEqual(states[signed['id']], 'signe')
        self.assertEqual({v for k, v in states.items() if k != signed['id']}, {'perime'})
        self.assertEqual(ops.next_step(self.desk, did)['id'], 'redaction')
        first = dos['operation']
        self.suite(did)
        co.generate(self.desk, ops._row(self.desk, did)['operation'], dav=self.docs)
        ops.sync_deliverables(self.desk, ops._row(self.desk, did))
        self.assertNotEqual(first, ops._row(self.desk, did)['operation'])
        self.assertEqual(self.desk.db.execute('SELECT state FROM operations5636 WHERE id=?', (first,)).fetchone()[0], 'abandonnee')
        self.assertGreater(self.desk.db.execute("SELECT MAX(version) FROM deliverables5683 WHERE dossier=? AND doc='ordre_mouvement'", (did,)).fetchone()[0], 1)

    def test_ux03_correction_conserve_valeur_et_source_sans_fait(self):
        did = self.dossier()
        eid = ops.propose_field(self.desk, did, 'societe_siren', '123456789', 'Kbis', 'SIREN 123456789')
        self.desk.db.commit()
        before = self.desk.db.execute('SELECT COUNT(*) FROM decisions5636').fetchone()[0]
        ops.set_evidence(self.desk, {'id': did, 'champ': eid, 'statut': 'confirme'}, 'avocate')
        row = self.desk.db.execute('SELECT * FROM field_evidence5683 WHERE id=?', (eid,)).fetchone()
        self.assertEqual((row['value'], row['origin'], row['status'], row['confirmed_by']), ('123456789', 'Kbis', 'confirme', 'avocate'))
        self.assertEqual(before, self.desk.db.execute('SELECT COUNT(*) FROM decisions5636').fetchone()[0])

    def test_donnees_contradictoires(self):
        did = self.dossier()
        ops.propose_field(self.desk, did, 'capital', '10000', 'Statuts')
        ops.propose_field(self.desk, did, 'capital', '12000', 'Kbis')
        self.assertEqual(self.desk.db.execute("SELECT status FROM field_evidence5683 WHERE dossier=? AND value='12000'", (did,)).fetchone()[0], 'contradictoire')

    def test_anomalie_excel_bloque_sans_reecrire_le_registre(self):
        did = self.dossier()
        path = self.piece('Detention.xlsx')
        self.docs.files[path] = xlsx([['Associé', 'Nombre de titres'], ['Paul MARTIN', '5500'], ['Anne DUPONT', '4000'], ['Total', '9500']])
        out = ops.read_excel(self.desk, {'id': did, 'chemin': path}, 'avocate', self.docs)
        self.assertIn('2 ligne', out['message'])
        before = co.holdings(self.desk, self.cid)
        wrong = ops.reconcile(self.desk, ops._row(self.desk, did))
        self.assertEqual([(w['registre'], w['source']) for w in wrong], [('6000', '5500')])
        self.suite(did)
        self.suite(did)
        res = self.suite(did)
        self.assertEqual(res['etat'], 'bloque')
        self.assertEqual(before, co.holdings(self.desk, self.cid))
        ops.set_evidence(self.desk, {'id': did, 'champ': wrong[0]['evidence'], 'statut': 'ecarte'}, 'avocate')
        self.assertEqual(self.suite(did)['etat'], 'fait')

    def test_anomalie_apparue_apres_l_audit_bloque_la_realisation(self):
        did = self.dossier()
        self.to_signature(did)
        ops.propose_field(self.desk, did, 'detention:Paul MARTIN', '5500', 'tableau Excel')
        self.desk.db.commit()
        with self.assertRaises(Stop) as ctx:
            ops.realize(self.desk, {'id': did, 'date_signature': '2026-10-12', 'date_ordre': '2026-10-12', 'date_inscription': '2026-10-13'}, 'avocate', self.docs)
        self.assertEqual(str(ctx.exception), 'anomalie_a_resoudre')
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM movements5636 WHERE kind='cession'").fetchone()[0], 0)

    def test_avant_apres_sans_ecriture(self):
        did = self.dossier(nombre='1000')
        lines = {x['titulaire']: x for x in ops.impact(self.desk, ops._row(self.desk, did))['lignes']}
        self.assertEqual((lines['Paul MARTIN']['avant'], lines['Paul MARTIN']['apres'], lines['Lucie BERNARD']['apres']), (6000, 5000, 1000))
        self.assertEqual(lines['Lucie BERNARD']['pct_apres'], '10.00')
        self.assertEqual(co.holdings(self.desk, self.cid)[1], 10000)

    def test_conditions_tirees_du_texte_ou_a_confirmer(self):
        cid = self.company('SARL')
        out = ops.open_cession(self.desk, {'company': cid})
        ops.update_fiche(self.desk, {'id': out['id'], 'acquereur_qualite': 'tiers'}, 'avocate')
        got = {c['id']: c for c in ops.controls(self.desk, ops._row(self.desk, out['id']))}
        self.assertEqual(got['agrement']['statut'], 'requis')
        self.assertIn('L223-14', got['agrement']['source'])
        self.assertEqual(got['preemption']['statut'], 'a_confirmer')
        sas = ops.open_cession(self.desk, {'company': self.cid})                    # SAS : les statuts seuls répondent
        self.assertEqual({c['id']: c for c in ops.controls(self.desk, ops._row(self.desk, sas['id']))}['agrement']['statut'], 'a_confirmer')

    def test_delai_agrement_dans_la_fiche(self):
        cid = self.company('SARL')
        did = self.dossier(cid, associes_a_notifier='2', notifications='2026-03-01 ; 2026-03-05')
        line = next(x for x in ops.deadlines(self.desk, ops._row(self.desk, did)) if 'agrément' in x['label'])
        self.assertEqual(line['etat'], 'a_confirmer')                                # trois destinataires, deux preuves
        ops.update_fiche(self.desk, {'id': did, 'notifications': '2026-03-01 ; 2026-03-05 ; 2026-03-09'}, 'avocate')
        self.assertEqual(next(x for x in ops.deadlines(self.desk, ops._row(self.desk, did)) if 'agrément' in x['label'])['date'], '2026-06-09')

    def test_evenements_doubles_ou_desordonnes_sans_realisation(self):
        did = self.dossier()
        self.assertTrue(ops.record_verified_signature(self.desk, did, 'ref-1', '2026-10-12'))
        self.assertFalse(ops.record_verified_signature(self.desk, did, 'ref-1', '2026-10-12'))
        self.assertEqual(ops._row(self.desk, did)['state'], 'en_cours')
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM movements5636 WHERE kind='cession'").fetchone()[0], 0)

    def test_signature_verifiee_tient_lieu_de_preuve(self):
        did = self.dossier()
        self.generated(did)
        self.suite(did)
        self.suite(did)
        ops.record_verified_signature(self.desk, did, 'ref-2', '2026-10-12')
        self.suite(did)
        ops.realize(self.desk, {'id': did, 'date_ordre': '2026-10-12', 'date_inscription': '2026-10-13'}, 'avocate', self.docs)
        self.assertEqual(ops._row(self.desk, did)['state'], 'realisee')
        data = json.loads(self.desk.db.execute('SELECT data FROM operations5636 WHERE id=?', (ops._row(self.desk, did)['operation'],)).fetchone()[0])
        self.assertEqual(data['preuve']['type'], 'evenement')

    def test_faux_evenement_refuse(self):
        did = self.dossier()
        self.generated(did)
        with self.assertRaises(Stop) as ctx:
            co.realize(self.desk, {'id': ops._row(self.desk, did)['operation'], 'date': '2026-10-12', 'justificatif': 'acte', 'evenement': 'inconnu'}, dav=self.docs)
        self.assertEqual(str(ctx.exception), 'evenement_signature_inconnu')

    def test_justificatif_formulaire_ne_peut_pas_declarer_une_signature_verifiee(self):
        did = self.dossier()
        with self.assertRaises(Stop):
            ops.add_proof(self.desk, {'id': did, 'type': 'signature_verifiee', 'date': '2026-10-12', 'piece': self.piece()}, 'avocate', self.docs)

    def test_rectification_ecriture_corrective_historique_conserve(self):
        did = self.dossier()
        self.to_signature(did)
        ops.realize(self.desk, {'id': did, 'date_signature': '2026-10-12', 'date_ordre': '2026-10-12', 'date_inscription': '2026-10-13'}, 'avocate', self.docs)
        before = [dict(r) for r in self.desk.db.execute('SELECT * FROM movements5636 WHERE company=? ORDER BY numero', (self.cid,))]
        ops.rectify(self.desk, {'id': did, 'nombre': '100', 'motif': 'Erreur de quantité fictive', 'idem': 'k1'}, 'avocate')
        ops.rectify(self.desk, {'id': did, 'nombre': '100', 'motif': 'Erreur de quantité fictive', 'idem': 'k1'}, 'avocate')
        after = [dict(r) for r in self.desk.db.execute('SELECT * FROM movements5636 WHERE company=? ORDER BY numero', (self.cid,))]
        self.assertEqual(after[:len(before)], before)
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual(after[-1]['kind'], 'correction')
        self.assertEqual({r['name']: r['quantity'] for r in co.holdings(self.desk, self.cid)[0]}['Paul MARTIN'], 6000)

    def realised_filings(self, branch='SAS'):
        did = self.dossier() if branch == 'SAS' else self.dossier(self.company(branch))
        self.to_signature(did)
        dates = {'date_signature': '2026-10-12', 'date_ordre': '2026-10-12', 'date_inscription': '2026-10-13', 'date_realisation': '2026-10-12'}
        ops.realize(self.desk, {'id': did, **dates}, 'avocate', self.docs)
        self.suite(did)
        return did, self.desk.db.execute('SELECT * FROM filings5683 WHERE dossier=? ORDER BY rowid', (did,)).fetchall()

    def test_op01_transmis_distinct_d_accepte_recepisse_et_rejet(self):
        did, rows = self.realised_filings()
        fid = rows[0]['id']
        for step in ('prete', 'autorisee', 'transmise'):
            ops.filing_update(self.desk, {'id': did, 'formalite': fid, 'statut': step}, 'avocate', 'avocat', self.docs)
        self.assertEqual(self.desk.db.execute('SELECT statut FROM filings5683 WHERE id=?', (fid,)).fetchone()[0], 'transmise')
        with self.assertRaises(Stop) as ctx:
            ops.filing_update(self.desk, {'id': did, 'formalite': fid, 'statut': 'acceptee'}, 'avocate', 'avocat', self.docs)
        self.assertEqual(str(ctx.exception), 'recepisse_requis')
        ops.filing_update(self.desk, {'id': did, 'formalite': fid, 'statut': 'rejetee', 'motif': 'Pièce manquante', 'piece': self.piece('Rejet.pdf')}, 'avocate', 'avocat', self.docs)
        ops.filing_update(self.desk, {'id': did, 'formalite': fid, 'statut': 'a_preparer'}, 'avocate', 'avocat', self.docs)

    def test_op01_ordre_des_etats_et_role(self):
        did, rows = self.realised_filings()
        fid = rows[0]['id']
        with self.assertRaises(Stop):
            ops.filing_update(self.desk, {'id': did, 'formalite': fid, 'statut': 'acceptee', 'piece': self.piece()}, 'avocate', 'avocat', self.docs)
        ops.filing_update(self.desk, {'id': did, 'formalite': fid, 'statut': 'prete'}, 'assistant', 'assistant', self.docs)
        with self.assertRaises(Stop) as ctx:
            ops.filing_update(self.desk, {'id': did, 'formalite': fid, 'statut': 'autorisee'}, 'assistant', 'assistant', self.docs)
        self.assertEqual(str(ctx.exception), 'validation_avocat_requise')

    def test_op02_reprise_sans_double_formalite(self):
        did, rows = self.realised_filings()
        ops.create_filings(self.desk, ops._row(self.desk, did), 'avocate')
        self.assertEqual(len(rows), self.desk.db.execute('SELECT COUNT(*) FROM filings5683 WHERE dossier=?', (did,)).fetchone()[0])
        ops.filing_update(self.desk, {'id': did, 'formalite': rows[0]['id'], 'statut': 'prete'}, 'avocate', 'avocat', self.docs)
        out = ops.filing_update(self.desk, {'id': did, 'formalite': rows[0]['id'], 'statut': 'prete'}, 'avocate', 'avocat', self.docs)
        self.assertIn('déjà', out['message'])

    def test_formalites_par_branche(self):
        _, parts = self.realised_filings('SARL')
        _, shares = self.realised_filings()
        self.assertIn('depot_statuts', {r['kind'] for r in parts})
        self.assertIn('opposabilite', {r['kind'] for r in parts})
        self.assertNotIn('depot_statuts', {r['kind'] for r in shares})
        self.assertEqual({r['destinataire'] for r in shares if r['kind'] == 'enregistrement'}, {'Service des impôts compétent'})

    def test_cession_d_actions_sans_acte_ouvre_le_2759(self):
        did = self.dossier(acte_ecrit='non')
        self.assertNotIn('acte_cession', ops.documents_for(self.desk, ops._row(self.desk, did)))
        dos = ops._row(self.desk, did)
        ops.create_filings(self.desk, dos, 'avocate')
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM filings5683 WHERE dossier=? AND kind='declaration_2759'", (did,)).fetchone()[0], 1)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM filings5683 WHERE dossier=? AND kind='enregistrement'", (did,)).fetchone()[0], 0)

    def test_assistant_ne_valide_pas_une_etape(self):
        did = self.dossier()
        with self.assertRaises(Stop) as ctx:
            ops.prepare_next(self.desk, {'id': did, 'confirmer': '1'}, 'assistant', 'assistant', self.docs)
        self.assertEqual(str(ctx.exception), 'validation_avocat_requise')
        self.assertFalse(web5683.allowed('assistant', 'realiser'))
        self.assertFalse(web5683.allowed('assistant', 'rectifier'))
        self.assertTrue(web5683.allowed('assistant', 'fiche'))

    def test_case_cochee_transmise_comme_booleen(self):
        did = self.dossier()
        self.assertEqual(ops.prepare_next(self.desk, {'id': did, 'confirmer': True}, 'avocate', 'avocat', self.docs)['etat'], 'fait')

    def test_fiche_version_perimee(self):
        did = self.dossier()
        version = ops._row(self.desk, did)['version']
        ops.update_fiche(self.desk, {'id': did, 'prix_unitaire': '7'}, 'avocate')
        with self.assertRaises(Stop) as ctx:
            ops.update_fiche(self.desk, {'id': did, 'version': version, 'prix_unitaire': '8'}, 'avocate')
        self.assertEqual(str(ctx.exception), 'fiche_modifiee_entre_temps')

    def test_idempotence_ouverture(self):
        a = ops.open_cession(self.desk, {'company': self.cid, 'idem': 'cle1'})
        b = ops.open_cession(self.desk, {'company': self.cid, 'idem': 'cle1'})
        self.assertEqual(a['id'], b['id'])

    def test_interface_quatre_vues_et_operation_dans_le_dossier(self):
        did = self.dossier()
        c = co.company(self.desk, self.cid)
        html = ops.page_html(self.desk, '/p', c, {'dossier': did})
        for label in ('Dossier de l’opération', 'Étapes', 'Documents', 'Formalités', 'Préparer la suite', 'Prochaine action'):
            self.assertIn(label, html)
        self.assertIn('aria-current="page"', html)
        for sub in ('etapes', 'documents', 'formalites'):
            self.assertIn('o83-tabs', ops.page_html(self.desk, '/p', c, {'dossier': did, 'sous': sub}))
        self.assertIn(did, [o['id'] for o in ops.for_matter(self.desk, MID)])
        self.assertIn('Cessions de parts et actions', co.page_html(self.desk, '/p', {'societe': self.cid}))

    def test_ancien_parcours_cession_parts_sans_ordre(self):
        cid = self.company('EURL')
        out = co.operation_submit(self.desk, {'kind': 'cession', 'company': cid, 'cedant': 'Paul MARTIN', 'cessionnaire_nom': 'Lucie BERNARD', 'nombre': '10', 'prix_unitaire': '1',
                                              'date_cession': '2026-10-12', 'categorie': 'ordinaires'})
        docs = json.loads(self.desk.db.execute('SELECT docs FROM operations5636 WHERE id=?', (out['id'],)).fetchone()[0])
        self.assertNotIn('ordre_mouvement', docs)
        self.assertIn('acte_cession_parts', docs)

    def test_modeles_marques_a_completer_sans_invention(self):
        for doc in ('acte_cession_parts', 'notification_cession', 'instruction_teneur', 'renonciation_preemption', 'attestation_depot'):
            self.assertIn('[À COMPLÉTER', ' '.join(co.DOCS[doc][1]), doc)
        self.assertIn('Compte du titulaire', ' '.join(co.DOCS['ordre_mouvement'][1]))


def xlsx(rows):
    buffer = io.BytesIO()
    cell = lambda c, i, j: '<c r="%s%d" t="inlineStr"><is><t>%s</t></is></c>' % ('ABCDEF'[j], i + 1, c)
    sheet = ''.join('<row r="%d">%s</row>' % (i + 1, ''.join(cell(c, i, j) for j, c in enumerate(r))) for i, r in enumerate(rows))
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Capital" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>%s</sheetData></worksheet>' % sheet)
    return buffer.getvalue()


class Excel(unittest.TestCase):
    def test_apercu_echappe_et_lecture_seule(self):
        raw = xlsx([['Nom', 'Valeur'], ['&lt;script&gt;alert(1)&lt;/script&gt;', '12']])
        html = apercu5679.xlsx_html(raw)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertEqual(apercu5679.kind_of('/a/b.XLSX'), 'xlsx')

    def test_classeur_invalide_ou_entites_refuses(self):
        with self.assertRaises(Stop):
            apercu5679.xlsx_sheets(b'pas un zip')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as z:
            z.writestr('xl/workbook.xml', '<!DOCTYPE x [<!ENTITY a "b">]><workbook/>')
        with self.assertRaises(Stop):
            apercu5679.xlsx_sheets(buffer.getvalue())


def blank_pdf():
    from agent._vendor.pypdf import PdfWriter
    from agent._vendor.pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject
    w = PdfWriter()
    page = w.add_blank_page(300, 300)
    widget = DictionaryObject({NameObject('/Type'): NameObject('/Annot'), NameObject('/Subtype'): NameObject('/Widget'), NameObject('/FT'): NameObject('/Tx'), NameObject('/T'): TextStringObject('a1'),
                               NameObject('/TU'): TextStringObject('Date de la cession'), NameObject('/Rect'): ArrayObject([FloatObject(x) for x in (10, 10, 200, 30)])})
    other = DictionaryObject({**widget, NameObject('/T'): TextStringObject('a2'), NameObject('/TU'): TextStringObject('Observation libre')})
    refs = [w._add_object(widget), w._add_object(other)]
    page[NameObject('/Annots')] = ArrayObject(refs)
    w._root_object[NameObject('/AcroForm')] = w._add_object(DictionaryObject({NameObject('/Fields'): ArrayObject(refs)}))
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


class Formulaires(t36._CorporateBase):
    def getter(self, raw):
        return lambda url, limit=0: raw

    def test_catalogue_complet_sans_pdf_dans_le_depot(self):
        cat = forms.catalogue()
        self.assertEqual(len(cat['formulaires']), 13)
        self.assertEqual(sum(1 for f in cat['formulaires'] if f['format'] == 'champs'), 3)
        self.assertTrue(all(len(f['sha256']) == 64 and f['page'].startswith('https://') for f in cat['formulaires']))
        self.assertEqual({x['demande'] for x in cat['non_identifies']}, {'2670', '1674', 'Requête en conciliation'})
        self.assertFalse(list((Path(forms.__file__).parent).rglob('*.pdf')))

    def test_champs_et_remplissage_sans_toucher_le_vierge(self):
        raw = blank_pdf()
        rows = forms.fields(raw)
        self.assertEqual([r['label'] for r in rows], ['Date de la cession', 'Observation libre'])
        out, written, empty = forms.fill(raw, {'a1': '12/10/2026', 'inconnu': 'x'})
        self.assertEqual((written, empty), (['a1'], ['a2']))
        from agent._vendor.pypdf import PdfReader
        self.assertEqual(str(PdfReader(io.BytesIO(out)).get_fields()['a1'].get('/V')), '12/10/2026')
        self.assertEqual(raw, blank_pdf())

    def test_version_differente_a_verifier_avant_usage(self):
        form = forms.form_of('2759-sd')
        out = forms.fetch(self.desk, '2759-sd', lambda url, limit=0: b'<a href="/sites/default/files/formulaires/2759-sd/x.pdf">x</a>' if 'sites/default' not in url else blank_pdf())
        self.assertEqual(out['etat'], 'version_a_verifier')
        with self.assertRaises(Stop) as ctx:
            forms.blank(self.desk, '2759-sd')
        self.assertEqual(str(ctx.exception), 'formulaire_version_a_verifier')
        forms.confirm_version(self.desk, '2759-sd', 'avocate')
        self.assertEqual(forms.blank(self.desk, '2759-sd')[0]['id'], form['id'])

    def test_empreinte_egale_version_verifiee(self):
        raw = blank_pdf()
        catalogue = forms.catalogue()
        catalogue['formulaires'][0]['sha256'] = hashlib.sha256(raw).hexdigest()
        catalogue['formulaires'][0]['page'] = 'https://www.formulaires.service-public.gouv.fr/gf/cerfa_1.do'
        with patch.object(forms, 'catalogue', lambda: catalogue):
            self.assertEqual(forms.fetch(self.desk, catalogue['formulaires'][0]['id'], self.getter(raw))['etat'], 'version_verifiee')

    def test_fichier_modifie_dans_le_cache_refuse(self):
        forms.fetch(self.desk, 'cerfa-12948', self.getter(blank_pdf()))
        forms.confirm_version(self.desk, 'cerfa-12948', 'avocate')
        forms._cache(self.desk, forms.form_of('cerfa-12948')).write_bytes(b'%PDF-autre')
        with self.assertRaises(Stop):
            forms.blank(self.desk, 'cerfa-12948')

    def test_seuls_les_sites_officiels(self):
        for url in ('https://exemple.test/a.pdf', 'http://www.impots.gouv.fr/a.pdf', 'https://www.impots.gouv.fr.exemple.test/a.pdf'):
            with self.assertRaises(Stop) as ctx:
                forms._get(url)
            self.assertEqual(str(ctx.exception), 'formulaire_hote_refuse')
        with self.assertRaises(Stop):
            forms._SameHosts().redirect_request(None, None, 302, '', {}, 'https://exemple.test/a.pdf')

    def test_page_non_pdf_refusee(self):
        with self.assertRaises(Stop):
            forms.fetch(self.desk, 'cerfa-12948', self.getter(b'<html>erreur</html>'))

    def test_pdf_plat_fiche_de_donnees_et_rien_d_imite(self):
        out = forms.generate(self.desk, {'formulaire': 'cerfa-12948', 'matter': MID}, 'avocate', self.docs)
        self.assertEqual(out['format'], 'plat')
        self.assertIn('/FORMULAIRES/', out['path'])
        self.assertTrue(out['path'].endswith('.docx'))
        self.assertIn('simulateur', out['service_guide'])

    def test_champs_depot_dans_le_dossier_et_copie_nommee(self):
        catalogue = forms.catalogue()
        with patch.object(forms, 'blank', lambda desk, fid: (forms.form_of('2759-sd'), blank_pdf())):
            first = forms.generate(self.desk, {'formulaire': '2759-sd', 'matter': MID, 'champ:a1': '12/10/2026'}, 'avocate', self.docs)
            second = forms.generate(self.desk, {'formulaire': '2759-sd', 'matter': MID, 'champ:a1': '13/10/2026'}, 'avocate', self.docs)
        self.assertEqual(first['ecrits'], 1)
        self.assertNotEqual(first['path'], second['path'])
        self.assertTrue(catalogue)

    def test_mots_cles_puis_ia_avec_libelles_seulement(self):
        rows = forms.fields(blank_pdf())
        known = forms.propose(rows, {'date_cession': '2026-10-12', 'cedant_nom': 'Paul MARTIN'})
        self.assertEqual(list(known), ['a1'])

        class M:
            sent = []

            def complete(self, messages, temperature=None, max_tokens=0):
                M.sent.append(json.dumps(messages, ensure_ascii=False))
                return '{"a2": "cedant_nom", "a1": "droits", "zzz": "cedant_nom"}'
        out = forms.ai_mapping(M(), rows, known)
        self.assertEqual(out, {'a2': 'cedant_nom'})                        # champ déjà rapproché ou inconnu : ignoré
        self.assertNotIn('Paul MARTIN', M.sent[0])
        self.assertIn('Observation libre', M.sent[0])

    def test_sortie_invalide_une_seule_reparation(self):
        class M:
            calls = 0

            def complete(self, messages, temperature=None, max_tokens=0):
                M.calls += 1
                return 'pas du json'
        self.assertEqual(forms.ai_mapping(M(), forms.fields(blank_pdf()), {}), {})
        self.assertEqual(M.calls, 2)

    def test_donnees_d_une_cession_reprises_avec_droits(self):
        out = ops.open_cession(self.desk, {'company': self.cid})
        ops.update_fiche(self.desk, {'id': out['id'], 'cedant': 'Paul MARTIN', **FULL}, 'avocate')
        mid, values = forms.cession_values(self.desk, out['id'])
        self.assertEqual(mid, MID)
        self.assertEqual((values['nombre_titres'], values['prix_total'], values['droits']), ('100', '500', '1.00'))


class FakeMsg(dict):
    pass


class FakeMail:
    def __init__(self, uid, subject, sender, day, text, key=''):
        self.uid, self.subject, self.timestamp, self.text = uid, subject, day, text
        self.msg, self.flags, self._key = FakeMsg({'From': sender}), (), key or uid * 64

    def key(self, account):
        return self._key


class FakeBox:
    instances = []

    def __init__(self, mails, error=None):
        self.mails, self.error, self.criteria, self.bodies, self.closed = mails, error, [], 0, False

    def search(self, folder, *criteria):
        if self.error:
            raise self.error
        self.criteria.append(criteria)
        return [m.uid for m in self.mails]

    def fetch(self, folder, uid, headers_only=False):
        self.bodies += 0 if headers_only else 1
        return next(m for m in self.mails if m.uid == uid)

    def close(self):
        self.closed = True


class Discussion(t36._CorporateBase):
    def setUp(self):
        super().setUp()
        # 5.6.96 (M02) : les bornes sont vérifiées sur l'INTERNALDATE en heure locale ; midi local du jour, pour que « aujourd'hui » reste vrai à toute heure
        from zoneinfo import ZoneInfo
        self.now = datetime.combine(acces5683.today_of(self.desk)[0], datetime.min.time(), ZoneInfo('Europe/Paris')) + timedelta(hours=12)
        self.mails = [FakeMail('2', 'Relance DURAND', 'Client <client@example.test>', self.now, 'Merci de rappeler avant vendredi.'),
                      FakeMail('1', 'Pièce adverse', 'Confrère <confrere@example.test>', self.now - timedelta(hours=2), 'Bordereau joint.')]

    def ask(self, question, matter='', **kw):
        from agent import workspace

        class M:
            payload = None

            def ask(self, stage, data):
                M.payload = data
                return {'answer': 'Synthèse fictive.', 'source_ids': [s['id'] for s in data['sources'] if s.get('kind') in ('courriel', 'liste_courriels', 'agenda')][:3], 'limits': [], 'proposed_actions': []}

            def complete(self, *a, **k):
                raise Stop('generation_ia_delai_depasse')
        box = kw.pop('box', FakeBox(self.mails))
        self.box = box
        with patch('agent.mailbox.Mailbox', lambda cfg: box):
            out = workspace.chat(self.desk, {'matter': matter, 'key': '', 'question': question, 'rapide': True, 'purpose': 'assistant'}, None, M(), None, return_result=True)
        return out, M.payload

    def test_resume_des_mails_du_jour_sans_les_transmettre(self):
        with patch.object(DocumentIndex, 'global_sources', side_effect=AssertionError('pièces sans rapport')):
            out, payload = self.ask('J’aimerais que tu me fasses un résumé des mails que j’ai reçus aujourd’hui.')
        kinds = [s['kind'] for s in payload['sources']]
        self.assertEqual(kinds.count('courriel'), 2)
        self.assertIn('liste_courriels', kinds)
        self.assertIn('Merci de rappeler', json.dumps(payload['sources'], ensure_ascii=False))
        self.assertFalse([s for s in payload['sources'] if s.get('kind') == 'document' or str(s.get('id', '')).startswith('cabinet-')])
        self.assertEqual(payload['acces_cabinet']['courriels']['periode'], 'aujourd’hui')
        self.assertTrue(payload['acces_cabinet']['courriels']['consultes'])
        self.assertTrue(any('2 trouvé' in x for x in out['limits']))

    def test_date_du_jour_toujours_transmise(self):
        _, payload = self.ask('Quelle est la prescription applicable au contrat ?')
        self.assertRegex(payload['acces_cabinet']['date_du_jour'], r'^\d{4}-\d{2}-\d{2}$')
        self.assertNotIn('courriels', payload['acces_cabinet'])

    def test_periode_imap_du_jour_et_lecture_sans_marquer_lu(self):
        today = acces5683.today_of(self.desk)[0]
        self.ask('Résume mes mails d’hier')
        criteria = self.box.criteria[0]
        yesterday = today - timedelta(days=1)
        # 5.6.96 (M02) : SINCE/BEFORE ne connaissent que le jour du serveur : la fenêtre IMAP couvre « hier » avec un jour de marge de chaque
        # côté, puis chaque en-tête est filtré sur son INTERNALDATE en heure locale (test_v5696_courriels, M02)
        imap_day = lambda value: datetime.strptime(value, '%d-%b-%Y').date()
        self.assertEqual(criteria[1], 'SINCE')
        self.assertLessEqual(imap_day(criteria[2]), yesterday)
        self.assertIn('BEFORE', criteria)
        self.assertGreaterEqual(imap_day(criteria[criteria.index('BEFORE') + 1]), today)
        from agent import mailbox
        self.assertIn('BODY.PEEK[]', Path(mailbox.__file__).read_text(encoding='utf-8'))

    def test_panne_de_messagerie_dite_jamais_absence(self):
        out, payload = self.ask('Résume mes mails du jour', box=FakeBox([], error=OSError('injoignable')))
        self.assertIn('erreur', payload['acces_cabinet']['courriels'])
        self.assertTrue(any('non consultés' in x for x in out['limits']))
        self.assertTrue(self.box.error)

    def test_dossier_reserve_jamais_transmis(self):
        self.desk.db.execute("INSERT INTO work_items(mail_key,state,matter,subject,sender,received,updated) VALUES(?,?,?,?,?,?,?)", ('2' * 64, 'drafted', MID, 's', 'x', 'd', 'd'))
        self.desk.db.commit()
        membres5650.set_viewer({MID})
        self.addCleanup(membres5650.clear)
        _, payload = self.ask('Résume mes mails du jour')
        text = json.dumps(payload['sources'], ensure_ascii=False)
        self.assertNotIn('Relance DURAND', text)
        self.assertIn('Pièce adverse', text)

    def test_question_d_un_dossier_filtre_ses_courriels(self):
        self.desk.db.execute("INSERT INTO work_items(mail_key,state,matter,subject,sender,received,updated) VALUES(?,?,?,?,?,?,?)", ('1' * 64, 'drafted', MID, 's', 'x', 'd', 'd'))
        self.desk.db.commit()
        sources, state = acces5683.read_mail(self.desk, 'derniers mails', MID, box=FakeBox(self.mails))
        self.assertEqual(state['trouves'], 1)
        self.assertEqual([s['subject'] for s in sources if s['kind'] == 'courriel'], ['Pièce adverse'])

    def test_recherche_par_nom_sans_date(self):
        sources, state = acces5683.read_mail(self.desk, 'retrouve le mail de DURAND', box=FakeBox(self.mails))
        self.assertEqual([s['subject'] for s in sources if s['kind'] == 'courriel'], ['Relance DURAND'])
        self.assertEqual(state['periode'], 'les douze derniers mois')

    def test_agenda_periode_et_sans_appel_reseau(self):
        from agent import workplan
        workplan.ensure_schema(self.desk) if hasattr(workplan, 'ensure_schema') else None
        today = acces5683.today_of(self.desk)[0]
        self.desk.db.execute('INSERT INTO calendar_cache VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', ('e1', 'u', '', 'c', 'h', 'e', 'Audience ZORVAL', '', 'Valbrune', today.isoformat() + 'T09:00:00+00:00',
                                                                                                  today.isoformat() + 'T10:00:00+00:00', 1, '', '[]', self.now.isoformat()))
        self.desk.db.execute('INSERT INTO calendar_cache VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', ('e2', 'u', '', 'c', 'h', 'e', 'Réunion KERVILAC', '', '', (today + timedelta(days=30)).isoformat() + 'T09:00:00+00:00',
                                                                                                  (today + timedelta(days=30)).isoformat() + 'T10:00:00+00:00', 1, '', '[]', self.now.isoformat()))
        self.desk.db.commit()
        out, payload = self.ask('Qu’est-ce que j’ai à mon agenda aujourd’hui ?')
        text = json.dumps(payload['sources'], ensure_ascii=False)
        self.assertIn('Audience ZORVAL', text)
        self.assertNotIn('KERVILAC', text)
        self.assertEqual(self.box.criteria, [])                               # aucune lecture de la messagerie
        self.assertTrue(any('Agenda consulté' in x for x in out['limits']))

    def test_agenda_dossier_reserve_masque(self):
        from agent import workplan
        workplan.ensure_schema(self.desk) if hasattr(workplan, 'ensure_schema') else None
        today = acces5683.today_of(self.desk)[0]
        self.desk.db.execute('INSERT INTO calendar_cache VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', ('e1', 'u', '', 'c', 'h', 'e', 'Audience XARVOL', '', '', today.isoformat() + 'T09:00:00+00:00',
                                                                                                  today.isoformat() + 'T10:00:00+00:00', 1, MID, '[]', self.now.isoformat()))
        self.desk.db.commit()
        sources, state = acces5683.read_agenda(self.desk, 'mon agenda aujourd’hui', hidden={MID})
        self.assertEqual((sources, state['trouves']), ([], 0))

    def test_suite_de_conversation_garde_l_objet(self):
        self.assertEqual(acces5683.follow_up('Et ceux d’hier ?', 'résume mes mails du jour'), (True, False))
        self.assertEqual(acces5683.follow_up('Quelle est la clause de non-concurrence applicable ?', 'résume mes mails'), (False, False))

    def test_modele_occupe_liste_rendue_sans_redaction(self):
        from agent import workspace

        class M:
            def ask(self, stage, data):
                raise Stop('generation_ia_delai_depasse')

            def complete(self, *a, **k):
                raise Stop('generation_ia_delai_depasse')
        with patch('agent.mailbox.Mailbox', lambda cfg: FakeBox(self.mails)):
            out = workspace.chat(self.desk, {'matter': '', 'key': '', 'question': 'résume mes mails du jour', 'rapide': True}, None, M(), None, return_result=True)
        self.assertTrue(out['repli'])
        self.assertIn('Relance DURAND', out['answer'])

    def test_intention_redaction_n_ouvre_pas_la_messagerie(self):
        self.assertFalse(acces5683.wants_mail('Rédige un mail à DURAND pour fixer un rendez-vous'))
        self.assertTrue(acces5683.wants_mail('Résume le dernier mail de DURAND'))
        self.assertTrue(acces5683.wants_mail('quels courriels ai-je reçus ?'))
        self.assertFalse(acces5683.wants_mail('Analyse cette clause de garantie'))

    def test_periodes(self):
        day = date(2026, 10, 28)     # un mercredi
        self.assertEqual(acces5683.period('mails de la semaine dernière', day)[:2], (date(2026, 10, 19), date(2026, 10, 25)))
        self.assertEqual(acces5683.period('mes mails de cette semaine', day)[:2], (date(2026, 10, 26), day))
        self.assertEqual(acces5683.period('agenda de cette semaine', day, True)[:2], (date(2026, 10, 26), date(2026, 11, 1)))
        self.assertEqual(acces5683.period('mail du 12/10/2026', day)[:2], (date(2026, 10, 12), date(2026, 10, 12)))
        self.assertEqual(acces5683.period('mail du 3 mars', day)[0], date(2026, 3, 3))
        self.assertEqual(acces5683.period('mes mails', day)[3], False)

    def test_mots_de_la_demande_retires(self):
        self.assertEqual(acces5683.focus_terms('Fais-moi un résumé des mails que j’ai reçus aujourd’hui'), [])

    def test_document_recent_et_dossier_actif_d_abord(self):
        index = DocumentIndex(self.desk.c['state_dir'])
        recent = self.now.isoformat()
        old = (self.now - timedelta(days=2000)).isoformat()
        for mid, source, modified in (('M-ANCIEN', 's1', old), ('M-RECENT', 's2', recent)):
            index.db.execute('INSERT INTO knowledge_chunks(matter,source_id,path,etag,modified,kind,chunk_no,text,meta,indexed_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                             (mid, source, '/x/' + source + '.pdf', 'e', modified, 'document', 0, 'bail commercial fictif', '{}', recent))
            index.db.execute('INSERT INTO knowledge_fts(matter,source_id,chunk_no,text) VALUES(?,?,?,?)', (mid, source, 0, 'bail commercial fictif'))
        index.db.commit()
        rows, _ = index.global_sources(['bail commercial'], limit=2, weights={})
        self.assertEqual(rows[0]['matter'], 'M-RECENT')                         # à pertinence égale, la plus récente
        with patch('agent.index.fuse', lambda lexical, semantic: {k: 1.0 for k in lexical}):
            self.assertEqual(index.global_sources(['bail commercial'], limit=2)[0][0]['matter'], 'M-RECENT')       # égalité stricte, sans pondération
        rows, _ = index.global_sources(['bail commercial'], limit=2, weights={'M-ANCIEN': 1.25, 'M-RECENT': 0.65}, recent=False)
        self.assertEqual(rows[0]['matter'], 'M-ANCIEN')                         # la fraîcheur reste un complément
        self.assertGreater(acces5683.freshness(recent, True), acces5683.freshness(recent, False))
        self.assertAlmostEqual(acces5683.freshness(old), 1.0, places=2)
        self.assertEqual(acces5683.freshness('illisible'), 1.0)

    def test_dossier_nomme_borne_la_recherche(self):
        from agent.common import load_matters
        m = load_matters(self.desk.c)[0]
        found = acces5683.named_matters(self.desk, 'Quels sont les derniers éléments du dossier %s ?' % m['client_name'])
        self.assertEqual(found[0]['id'], m['id'])
        self.assertEqual(acces5683.named_matters(self.desk, 'Quels sont les derniers éléments du dossier %s ?' % m['client_name'], {m['id']}), [])

    def test_question_sans_objet_pas_de_piece_au_hasard(self):
        with patch.object(DocumentIndex, 'global_sources', side_effect=AssertionError('recherche au hasard')):
            _, payload = self.ask('Peux-tu me faire un résumé ?')
        self.assertIn('recherche', payload['couverture_documentaire'])

    def test_quoi_de_neuf_consulte_courriels_et_agenda(self):
        self.assertTrue(acces5683.wants_mail('Quoi de neuf ce matin ?'))
        self.assertTrue(acces5683.wants_agenda('Quoi de neuf ce matin ?'))
        self.assertEqual(acces5683.period('quoi de neuf', date(2026, 10, 28))[2], 'aujourd’hui')

    def test_dossier_selectionne_piece_recente_d_abord(self):
        index = DocumentIndex(self.desk.c['state_dir'])
        recent = self.now.isoformat()
        for source, modified in (('s1', (self.now - timedelta(days=2000)).isoformat()), ('s2', recent)):
            index.db.execute('INSERT INTO knowledge_chunks(matter,source_id,path,etag,modified,kind,chunk_no,text,meta,indexed_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                             (MID, source, '/x/' + source + '.pdf', 'e', modified, 'document', 0, 'courrier adverse fictif', '{}', recent))
            index.db.execute('INSERT INTO knowledge_fts(matter,source_id,chunk_no,text) VALUES(?,?,?,?)', (MID, source, 0, 'courrier adverse fictif'))
        index.db.commit()
        ranked, _ = index.ranked_chunks(MID, ['courrier adverse'], limit=2, recent=True)
        self.assertEqual(ranked[0][0][1], 's2')

    def test_masques_transmis_a_la_file(self):
        from agent import integration
        membres5650.set_viewer({'M-AUTRE'})
        self.addCleanup(membres5650.clear)
        out = integration.submit_question(self.desk, 'Résume mes mails', '')
        args = json.loads(self.desk.db.execute('SELECT args FROM jobs WHERE id=?', (out['job_id'],)).fetchone()[0])
        self.assertEqual(args['masques'], ['M-AUTRE'])


class Competences(t36._CorporateBase):
    def archive(self, entries):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as z:
            for name, data in entries.items():
                z.writestr(name, data)
        return buffer.getvalue()

    SKILL = '---\nname: essai-fictif\ndescription: |\n  Première ligne\n  deuxième ligne\nmetadata:\n  version: "2.0"\n  license: MIT\nsituations:\n  - contrat\n  - bail\n---\n# Rôle\nTu es rigoureux.\n# Méthode\nLire puis analyser.\n'

    def test_sk01_yaml_blocs_listes_et_refus(self):
        meta = yaml5683.frontmatter(self.SKILL)[0]
        self.assertEqual(meta['description'].strip(), 'Première ligne\ndeuxième ligne')
        self.assertEqual(meta['metadata']['version'], '2.0')
        self.assertEqual(meta['situations'], ['contrat', 'bail'])
        with self.assertRaises(Stop):
            yaml5683.parse('a: 1\na: 2\n')

    def test_sk02_sk03_ressources_versionnees_et_export_fidele(self):
        raw = self.archive({'SKILL.md': self.SKILL, 'references/guide.md': '# Guide\nTexte fictif.\n', 'scripts/verify_links.py': 'print(1)', 'scripts/autre.py': 'print(2)'})
        out = competences5632.import_skill(self.desk, 'cabinet', raw, 'essai.zip')
        self.assertEqual(bytes(competences5683.resource(self.desk, out['id'], out['version'], 'references/guide.md')['content']), b'# Guide\nTexte fictif.\n')
        package = zipfile.ZipFile(io.BytesIO(competences5632.export(self.desk, out['id'])))
        self.assertIn('references/guide.md', package.namelist())
        self.assertEqual(package.read('references/guide.md'), b'# Guide\nTexte fictif.\n')

    def test_sk05_trop_d_entrees_refus_sans_troncature(self):
        raw = self.archive({'SKILL.md': self.SKILL, **{'r/%d.md' % i: 'x' for i in range(300)}})
        with self.assertRaises(Stop) as ctx:
            competences5683.inspect(raw)
        self.assertEqual(str(ctx.exception), 'archive_competence_trop_de_fichiers')

    def test_sk06_traversee_et_lien_symbolique_refuses(self):
        with self.assertRaises(Stop):
            competences5683.inspect(self.archive({'SKILL.md': self.SKILL, '../evasion.md': 'x'}))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as z:
            z.writestr('SKILL.md', self.SKILL)
            info = zipfile.ZipInfo('lien.md')
            info.external_attr = 0o120777 << 16
            z.writestr(info, '/etc/passwd')
        with self.assertRaises(Stop):
            competences5683.inspect(buffer.getvalue())

    def test_sk07_licence_non_commerciale_et_droits_inconnus(self):
        files = [{'path': 'LICENSE', 'data': b'Creative Commons BY-NC-SA 4.0'}]
        rights = competences5683.rights_of({}, files)
        self.assertTrue(rights['non_commercial'])
        self.assertEqual(rights['status'], 'restreint')
        self.assertFalse(rights['commercial_distribution_ready'])
        self.assertEqual(competences5683.rights_of({}, [])['status'], 'a_confirmer')

    def test_sk17_scripts_jamais_executes_decision_explicite(self):
        rows = competences5683.script_decisions(['scripts/verify_links.py', 'scripts/autre.py'])
        self.assertEqual([r['decision'] for r in rows], ['service', 'indisponible'])
        self.assertFalse(any(r['executed'] for r in rows))

    def test_sk04_section_entiere_et_omissions_signalees(self):
        text = '# Rôle\nRègle.\n' + ''.join('# Partie %d\n%s\n' % (i, 'mot ' * 400) for i in range(6))
        kept, omitted, _ = competences5683.fit(text, 3000, 'partie')
        self.assertLessEqual(len(kept), 3000)
        self.assertTrue(omitted)
        self.assertNotIn('mot mot\n# Partie 99', kept)

    def test_sk11_page_200_sans_contenu_pertinent(self):
        fetch = lambda url: (200, {}, '<html><body>Page d’accueil du site, rien sur ce sujet.</body></html>'.encode())
        out = liens5683.verifier('https://example.test/a', 'Le preneur doit délivrer congé par acte extrajudiciaire', fetch, resolve=False)
        self.assertEqual(out['state'], 'non_verifie')

    def test_sk12_reseau_prive_et_redirection_interne_refuses(self):
        with self.assertRaises(Stop):
            liens5683.verifier('http://127.0.0.1/admin', '', lambda u: (200, {}, b'x'))
        calls = []

        def fetch(url):
            calls.append(url)
            return 302, {'Location': 'http://169.254.169.254/latest'}, b''
        with self.assertRaises(Stop):
            liens5683.verifier('https://example.test/a', '', fetch, resolve=False)
        self.assertEqual(len(calls), 1)

    def test_sk08_capacite_sans_connecteur(self):
        status = liaisons5683.overview(self.desk)
        self.assertTrue(status)
        self.assertEqual({c['etat'] for c in status}, {'connexion'})

    def test_acces_refuse_affiche(self):
        self.desk.setting('lawve:item:essai', {'id': 'essai', 'last_test': {'status': 'error', 'error': 'http_403'}})
        self.assertEqual(liaisons5683.capability_status(self.desk, next(iter(liaisons5683.CAPABILITIES))), 'refuse')

    def test_budget_selon_le_contexte_du_modele(self):
        small = competences5683.budget_chars({'ollama': {'max_context_chars': 16000}, 'num_ctx': 8192}, [{'role': 'user', 'content': 'x' * 1000}], 1000)
        self.assertGreaterEqual(small, 0)


class Aide(unittest.TestCase):
    def test_fiches_dans_les_quatre_langues(self):
        root = Path(forms.__file__).parent / 'aide'
        for lang in ('fr', 'en', 'de', 'es'):
            sheet = json.loads((root / (lang + '.json')).read_text(encoding='utf-8'))['formulaires']
            self.assertEqual(sheet['version'], '5.6.36')
            self.assertTrue(sheet['objective'])


if __name__ == '__main__':
    unittest.main()
