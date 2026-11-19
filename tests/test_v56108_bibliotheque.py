"""5.6.108 (lot B) : bibliothèque d'actes par droit applicable — systèmes en liste fermée, métadonnées, migration « EN » → « UK-EAW », cycle de vie,
sélection filtrée (droit, langue, type, applicabilité, état), premiers ensembles de trames étrangères à l'état de brouillon, page Bibliothèque.

Données fictives seulement ; aucun appel réseau ; aucun modèle d'IA."""
from datetime import date, timedelta
import io
import re
import unittest
from unittest.mock import patch
import zipfile

import test_v5639 as t39
from agent import bibliotheque5639 as b, bibliotheque_droits56108 as nouvelles, droits56108 as D, modeles5684 as M
from agent.common import Stop
from agent.erreurs5634 import Qualified

MID = t39.t34.MID
ENSEMBLE = {'questionnaire-mission', 'note-consultation', 'confidentialite', 'prestation-b2b', 'lettre-intention', 'suivi-obligations'}


def _tpl(ident):
    return dict(b.BUILTIN[ident], origine='axiorhub', personnalise=False, word='')


class Systemes(unittest.TestCase):
    """B1 : liste fermée ; la langue ne choisit jamais le système ; « CA », « UK », « EN » seuls ne suffisent pas."""

    def test_B1_liste_fermee_et_codes_ambigus(self):
        for code in ('FR', 'UK-EAW', 'DE', 'ES', 'CA-QC', 'CA-ON', 'CA-BC', 'CA-FED'):
            self.assertIn(code, D.SYSTEMES)
        for code in ('CA', 'UK', 'EN', 'GB', '', 'XX'):
            self.assertNotIn(code, D.SYSTEMES)
            with self.assertRaisesRegex(Stop, 'systeme_juridique_a_preciser'):
                D.systeme_requis(code)
        self.assertEqual(D.migrer('EN'), 'UK-EAW')                                   # alias explicite des seules données enregistrées
        self.assertEqual(D.lire('EN')['statut'], 'ambigu')                           # une demande « EN » reste à préciser
        self.assertEqual(D.lire('Canada')['statut'], 'ambigu')
        self.assertEqual(D.lire('Royaume-Uni')['statut'], 'ambigu')
        self.assertEqual(D.lire('Droit québécois')['code'], 'CA-QC')
        self.assertEqual(D.lire('Canada (Ontario)')['code'], 'CA-ON')
        self.assertEqual(D.lire('droit suisse')['statut'], 'inconnu')
        self.assertEqual(D.lire('Droit français')['code'], 'FR')

    def test_B1_la_langue_ne_designe_pas_un_droit(self):
        self.assertIsNone(D.droit_mentionne('Rédige en anglais un accord de confidentialité'))
        self.assertIsNone(D.droit_mentionne('Please draft an NDA in English for the client in Toronto'))
        self.assertEqual(D.droit_mentionne('Rédige en français un contrat de service, droit québécois')['code'], 'CA-QC')
        self.assertEqual(D.droit_mentionne('NDA governed by the laws of the Province of Ontario')['code'], 'CA-ON')
        self.assertEqual(D.droit_mentionne('Vertrag nach deutschem Recht')['code'], 'DE')
        self.assertEqual(D.droit_mentionne('Rédige un NDA de droit canadien')['statut'], 'ambigu')
        self.assertEqual(D.droit_mentionne('NDA under English law')['code'], 'UK-EAW')
        self.assertIsNone(D.droit_mentionne('Note sur la loi applicable au litige'))
        # un acte en anglais peut être canadien ; un acte en français n'est pas forcément français
        self.assertEqual({t['langue'] for t in b.BUILTIN.values() if t['droit'] == 'CA-ON'}, {'en'})
        self.assertEqual({t['langue'] for t in b.BUILTIN.values() if t['droit'] == 'CA-QC'}, {'fr'})


class Qualification(unittest.TestCase):
    """B2 et B5 : métadonnées obligatoires ; trames existantes qualifiées ; premiers ensembles par système, en brouillon."""

    CLES = ('version', 'systeme', 'langue', 'type_acte', 'profils_parties', 'auteur', 'provenance', 'licence', 'sources', 'valide_par', 'valide_le',
            'reexamen_le', 'applicable_depuis', 'applicable_jusquau', 'champs_requis', 'options', 'incompatibilites')

    def test_B2_chaque_modele_porte_ses_metadonnees(self):
        for ident in b.BUILTIN:
            q = D.qualification(_tpl(ident))
            for cle in self.CLES:
                self.assertIn(cle, q, ident)
            self.assertIn(q['systeme'], D.SYSTEMES, ident)
            self.assertTrue(q['sources'] and q['auteur'] and q['provenance'] and q['licence'] and q['type_acte'], ident)
            self.assertEqual((q['etat_initial'], q['valide_par'], q['valide_le']), ('draft', '', ''), ident)   # jamais livré validé
        self.assertEqual(D.qualification(_tpl('en_nda'))['systeme'], 'UK-EAW')
        self.assertEqual(D.qualification(_tpl('en_nda'))['type_acte'], 'confidentialite')
        self.assertEqual(D.qualification(_tpl('de_geheimhaltung'))['sources'][0], 'Gesetze im Internet — https://www.gesetze-im-internet.de/')
        self.assertIn('Lois du Canada', ' '.join(D.qualification(_tpl('qc_contrat_service'))['sources']))   # province : droit fédéral pertinent

    def test_B2_trames_existantes_qualifiees(self):
        from collections import Counter
        anciennes = Counter(t['droit'] for t in b.BUILTIN.values() if not t.get('nouveau56108') and not t.get('famille'))
        self.assertEqual(anciennes, Counter({'FR': 21, 'DE': 9, 'ES': 9, 'UK-EAW': 8}))   # 21 FR, 9 DE, 9 ES, 8 EN → UK-EAW
        self.assertNotIn('EN', {t['droit'] for t in b.BUILTIN.values()})
        self.assertTrue(all(D.qualification(t)['selection_assistee'] for t in b.BUILTIN.values() if not t.get('nouveau56108')))

    def test_B5_premier_ensemble_par_systeme_en_brouillon(self):
        for systeme in D.PRIORITAIRES:
            types = {D.type_acte(t) for t in b.BUILTIN.values() if t['droit'] == systeme}
            self.assertTrue(ENSEMBLE <= types, (systeme, ENSEMBLE - types))
        self.assertEqual(len(nouvelles.TEMPLATES), 24)
        for t in nouvelles.TEMPLATES:
            q = D.qualification(t)
            self.assertFalse(q['selection_assistee'], t['id'])                               # brouillon nouveau : jamais proposé d'office
            self.assertIn(t['langue'], D.SYSTEMES[t['droit']][1], t['id'])                    # langue d'usage du droit
            self.assertNotIn(D.type_acte(t), ('convention-honoraires', 'formalite'), t['id'])   # hors du premier ensemble
            self.assertTrue(t['sources'], t['id'])
            for flags in ({}, {'clause_' + k: True for k, _, _, _ in t['clauses']}, {'clause_' + k: False for k, _, _, _ in t['clauses']}):
                tpl = dict(t, origine='axiorhub', personnalise=False, word='')
                text = b.plain_text(b.render(tpl, b.values_for(tpl, {}), b.flags_for(tpl, flags)))
                for leftover in ('[[', ']]', '{{', '}}', '\n## '):
                    self.assertNotIn(leftover, text, t['id'])

    def test_B5_aucune_trame_etrangere_n_est_une_traduction_d_un_acte_francais(self):
        francais = {ligne.strip() for t in b.BUILTIN.values() if t['droit'] == 'FR' for ligne in t['texte'] if len(ligne.strip()) > 40}
        for t in nouvelles.TEMPLATES:
            if t['droit'] == 'FR':
                continue
            self.assertFalse(francais & {ligne.strip() for ligne in t['texte']}, t['id'])
        qc = '\n'.join(b.BUILTIN['qc_entente_confidentialite']['texte'])
        self.assertIn('Code civil du Québec', qc)
        self.assertNotIn('Code de commerce', qc)
        self.assertNotIn('droit français', qc)
        on = '\n'.join(b.BUILTIN['on_services_agreement']['texte'])
        self.assertIn('Province of Ontario', on)
        self.assertNotIn('England', on)


class Registre(t39._Corp):
    """B2, B3, B4, B6 sur une base réelle (bureau fictif)."""

    def valider(self, ident, owner='avocate', **extra):
        row = M.current(self.desk, ident)
        if row['state'] == 'draft':
            M.move(self.desk, {'modele': ident, 'etat': 'review'}, owner, 'avocat')
        return M.move(self.desk, {'modele': ident, 'etat': 'validated', **extra}, owner, 'avocat')

    # -------------------------------------------------------------------------------- B2 : registre et migration
    def test_B2_registre_qualifie_review_date_et_reexamen_distincts(self):
        row = M.current(self.desk, 'en_nda')
        self.assertEqual((row['systeme'], row['language'], row['document_type']), ('UK-EAW', 'en', 'confidentialite'))
        self.assertIn('legislation.gov.uk', row['legal_sources'])
        self.assertEqual((row['review_date'], row['reexamen_le']), ('', ''))
        out = self.valider('en_nda', reexamen_le='2099-01-31')
        self.assertIn('Réexamen prévu le 2099-01-31', out['message'])
        row = M.current(self.desk, 'en_nda')
        self.assertEqual(row['reexamen_le'], '2099-01-31')
        self.assertEqual(row['review_date'], D.jour_du_cabinet(self.desk))                # date de validation, sens inchangé
        self.assertNotEqual(row['review_date'], row['reexamen_le'])

    def test_B2_migration_EN_vers_UK_EAW_sans_perte(self):
        self.desk.db.execute("INSERT INTO bibliotheque5639(id, base, titre, langue, droit, categorie, texte, word, owner, created, updated, meta, version) "
                             "VALUES('cab_ancienfictif','','NDA du cabinet fictif','en','EN','contrat','## Purpose\nTEXTE_PERSONNALISE_FICTIF','',"
                             "'avocate','2025-01-01','2025-01-01','{}',3)")
        self.desk.db.execute("INSERT INTO template_versions5684(template_id,version,state,sha256,text,author,validated_by,review_date,created,systeme) "
                             "VALUES('cab_ancienfictif',3,'validated','x','## Purpose','avocate','avocate','2026-06-01','2026-06-01','EN')")
        self.desk.setting(b.SETTING_DROIT, 'EN')
        self.desk.db.commit()
        b._READY.clear()
        M._READY56108.clear()
        D.ensure_schema(self.desk)
        row = self.desk.db.execute("SELECT * FROM bibliotheque5639 WHERE id='cab_ancienfictif'").fetchone()
        self.assertEqual((row['droit'], row['version'], row['titre']), ('UK-EAW', 3, 'NDA du cabinet fictif'))
        self.assertIn('TEXTE_PERSONNALISE_FICTIF', row['texte'])                         # personnalisation conservée
        self.assertEqual(b.default_law(self.desk), 'UK-EAW')
        self.assertEqual(self.desk.settings(b.SETTING_DROIT, ''), 'UK-EAW')
        ver = M.current(self.desk, 'cab_ancienfictif')
        self.assertEqual((ver['systeme'], ver['state'], ver['reexamen_le']), ('UK-EAW', 'validated', '2027-06-01'))
        self.assertEqual(b.template(self.desk, 'cab_ancienfictif')['droit'], 'UK-EAW')
        D.ensure_schema(self.desk)                                                        # idempotent
        self.assertEqual(M.current(self.desk, 'cab_ancienfictif')['reexamen_le'], '2027-06-01')

    def test_B2_migration_contre_exemple_sans_alias(self):
        self.desk.db.execute("INSERT INTO bibliotheque5639(id, base, titre, langue, droit, categorie, texte, word, owner, created, updated, meta, version) "
                             "VALUES('cab_autrefictif','','Modèle fictif','en','EN','contrat','## A\nB','','avocate','2025-01-01','2025-01-01','{}',1)")
        self.desk.db.commit()
        b._READY.clear()
        with patch.object(D, 'ALIAS_MIGRATION', {}):                                      # garde-fou retiré : l'ancien code subsiste
            b.schema(self.desk)
        self.assertEqual(self.desk.db.execute("SELECT droit FROM bibliotheque5639 WHERE id='cab_autrefictif'").fetchone()[0], 'EN')
        b._READY.clear()
        b.schema(self.desk)
        self.assertEqual(self.desk.db.execute("SELECT droit FROM bibliotheque5639 WHERE id='cab_autrefictif'").fetchone()[0], 'UK-EAW')

    def test_B2_migration_ne_remplace_jamais_une_saisie(self):
        M.current(self.desk, 'cgv_b2b')
        self.desk.db.execute("UPDATE template_versions5684 SET qualification='', document_type='type_du_cabinet', licence='licence du cabinet' "
                             "WHERE template_id='cgv_b2b'")
        self.desk.db.commit()
        M.migrer56108(self.desk)
        row = M.current(self.desk, 'cgv_b2b')
        self.assertEqual((row['document_type'], row['licence'], row['systeme']), ('type_du_cabinet', 'licence du cabinet', 'FR'))

    # -------------------------------------------------------------------------------- B3 : cycle de vie
    def test_B3_une_ia_ne_valide_jamais(self):
        M.move(self.desk, {'modele': 'en_nda', 'etat': 'review'}, 'avocate', 'avocat')
        for ia in ('ia', 'pilote', 'agent', 'claude:session-fictive', 'mcp:connecteur', 'axiorhub', ''):
            with self.assertRaisesRegex(Stop, 'etat_modele_reserve_aux_personnes'):
                M.move(self.desk, {'modele': 'en_nda', 'etat': 'validated'}, ia, 'administrateur')
        self.assertEqual(M.current(self.desk, 'en_nda')['state'], 'review')
        with self.assertRaisesRegex(Stop, 'validation_avocat_requise'):
            M.move(self.desk, {'modele': 'en_nda', 'etat': 'validated'}, 'assistante', 'assistant')
        with patch.object(D, 'est_identite_ia', return_value=False):                    # mutation : garde-fou retiré, l'IA validerait
            M.move(self.desk, {'modele': 'en_nda', 'etat': 'validated'}, 'ia', 'administrateur')
        self.assertEqual(M.current(self.desk, 'en_nda')['validated_by'], 'ia')

    def test_B3_validation_exige_sources_droit_et_reexamen_futur(self):
        M.move(self.desk, {'modele': 'de_geheimhaltung', 'etat': 'review'}, 'avocate', 'avocat')
        with self.assertRaisesRegex(Stop, 'date_reexamen_invalide'):
            M.move(self.desk, {'modele': 'de_geheimhaltung', 'etat': 'validated', 'reexamen_le': '2020-01-01'}, 'avocate', 'avocat')
        with self.assertRaisesRegex(Stop, 'date_reexamen_invalide'):
            M.move(self.desk, {'modele': 'de_geheimhaltung', 'etat': 'validated', 'reexamen_le': 'bientôt'}, 'avocate', 'avocat')
        self.desk.db.execute("UPDATE template_versions5684 SET legal_sources='[]' WHERE template_id='de_geheimhaltung'")
        self.desk.db.commit()
        with self.assertRaisesRegex(Stop, 'sources_de_controle_requises'):
            M.move(self.desk, {'modele': 'de_geheimhaltung', 'etat': 'validated'}, 'avocate', 'avocat')
        M.renseigner(self.desk, {'modele': 'de_geheimhaltung', 'sources': 'BGB, § 241 — Gesetze im Internet'}, 'avocate')
        out = M.move(self.desk, {'modele': 'de_geheimhaltung', 'etat': 'validated'}, 'avocate', 'avocat')
        attendu = (date.fromisoformat(D.jour_du_cabinet(self.desk)) + timedelta(days=365)).isoformat()
        self.assertEqual(M.current(self.desk, 'de_geheimhaltung')['reexamen_le'], attendu)  # réexamen proposé : un an
        self.assertIn(attendu, out['message'])
        with self.assertRaisesRegex(Stop, 'modele_valide_fige'):
            M.renseigner(self.desk, {'modele': 'de_geheimhaltung', 'sources': ''}, 'avocate')

    def test_B3_droit_d_un_modele_livre_fixe_et_codes_ambigus_refuses(self):
        with self.assertRaisesRegex(Stop, 'droit_du_modele_livre_fixe'):                   # une traduction ne vaut pas adaptation
            M.renseigner(self.desk, {'modele': 'contrat_prestation', 'systeme': 'CA-QC'}, 'avocate')
        with self.assertRaisesRegex(Stop, 'systeme_juridique_a_preciser'):
            M.renseigner(self.desk, {'modele': 'contrat_prestation', 'systeme': 'CA'}, 'avocate')
        out = b.add(self.desk, {'titre': 'Contrat fictif du cabinet', 'categorie': 'contrat', 'droit': 'FR', 'texte': '## Objet\nTexte.'}, 'avocate')
        M.renseigner(self.desk, {'modele': out['id'], 'systeme': 'CA-ON'}, 'avocate')
        self.assertEqual(b.template(self.desk, out['id'])['droit'], 'CA-ON')
        for code in ('CA', 'UK', 'EN'):
            with self.assertRaisesRegex(Stop, 'systeme_juridique_a_preciser'):
                b.add(self.desk, {'titre': 'Ambigu', 'categorie': 'contrat', 'droit': code, 'texte': '## A\nB'})
            with self.assertRaisesRegex(Stop, 'systeme_juridique_a_preciser'):
                b.set_default_law(self.desk, {'droit': code})

    def test_B3_langue_choisie_independamment_du_droit(self):
        out = b.add(self.desk, {'titre': 'Service agreement fictif', 'categorie': 'contrat', 'droit': 'CA-QC', 'langue': 'en',
                                'type_acte': 'prestation-b2b', 'texte': '## Services\nTexte.'}, 'avocate')
        t = b.template(self.desk, out['id'])
        self.assertEqual((t['droit'], t['langue']), ('CA-QC', 'en'))
        self.assertEqual(M.current(self.desk, out['id'])['document_type'], 'prestation-b2b')

    # -------------------------------------------------------------------------------- B4 : sélection
    def test_B4_selection_autonome_ecarte_brouillon_perime_et_autre_droit(self):
        with self.assertRaisesRegex(Stop, 'modele_juridique_valide_absent'):
            M.choisir(self.desk, 'UK-EAW', 'en', 'confidentialite')                       # rien de validé à la livraison
        self.valider('en_nda')
        self.assertEqual(M.choisir(self.desk, 'UK-EAW', 'en', 'confidentialite')['template_id'], 'en_nda')
        self.assertEqual(M.selectionner(self.desk, 'en_nda', autonome=True, systeme='UK-EAW')['state'], 'validated')
        for systeme, motif in (('FR', 'modele_autre_droit'), ('CA', 'systeme_juridique_a_preciser'), ('EN', 'systeme_juridique_a_preciser')):
            with self.assertRaisesRegex(Stop, motif):
                M.selectionner(self.desk, 'en_nda', autonome=True, systeme=systeme)
        with self.assertRaisesRegex(Stop, 'modele_juridique_valide_absent'):
            M.choisir(self.desk, 'CA-ON', 'en', 'confidentialite')                        # jamais le modèle anglais pour l'Ontario
        self.desk.db.execute("UPDATE template_versions5684 SET reexamen_le='2020-01-01' WHERE template_id='en_nda'")
        self.desk.db.commit()
        with self.assertRaisesRegex(Stop, 'modele_a_reexaminer'):
            M.selectionner(self.desk, 'en_nda', autonome=True)
        self.desk.db.execute("UPDATE template_versions5684 SET reexamen_le='2099-01-01', applicable_jusquau='2020-12-31' WHERE template_id='en_nda'")
        self.desk.db.commit()
        with self.assertRaisesRegex(Stop, 'modele_hors_periode'):
            M.selectionner(self.desk, 'en_nda', autonome=True)
        self.desk.db.execute("UPDATE template_versions5684 SET applicable_jusquau='', validated_by='pilote' WHERE template_id='en_nda'")
        self.desk.db.commit()
        with self.assertRaisesRegex(Stop, 'modele_non_valide'):                          # validation attribuée à une IA : sans effet
            M.selectionner(self.desk, 'en_nda', autonome=True)

    def test_B4_plusieurs_candidats_meme_date_version_active_designee(self):
        cab = b.add(self.desk, {'titre': 'NDA fictif du cabinet', 'categorie': 'contrat', 'droit': 'UK-EAW', 'langue': 'en',
                                'type_acte': 'confidentialite', 'texte': '## Purpose\nTexte fictif.'}, 'avocate')['id']
        self.valider('en_nda')
        self.valider(cab)
        with self.assertRaisesRegex(Stop, 'plusieurs_modeles_valides_a_departager'):
            M.choisir(self.desk, 'UK-EAW', 'en', 'confidentialite')
        M.move(self.desk, {'modele': cab, 'etat': 'needs_update'}, 'avocate', 'avocat')
        M.move(self.desk, {'modele': cab, 'etat': 'review'}, 'avocate', 'avocat')
        M.move(self.desk, {'modele': cab, 'etat': 'validated', 'actif': '1'}, 'avocate', 'avocat')
        self.assertEqual(M.choisir(self.desk, 'UK-EAW', 'en', 'confidentialite')['template_id'], cab)

    def test_B4_versions_comparees_comme_nombres(self):
        base = {'template_id': 'x', 'systeme': 'FR', 'language': 'fr', 'document_type': 'prestation-b2b', 'state': 'validated',
                'validated_by': 'avocate', 'review_date': '2026-11-01', 'reexamen_le': '2027-05-01', 'legal_sources': '["Légifrance"]'}
        lignes = [dict(base, version=2), dict(base, version=10)]
        with self.assertRaisesRegex(Stop, 'plusieurs_modeles_valides_a_departager'):     # jamais « 2 » > « 10 »
            D.choisir(lignes, 'FR', 'fr', 'prestation-b2b', '2026-11-18')
        self.assertEqual(D.choisir(lignes, 'FR', 'fr', 'prestation-b2b', '2026-11-18', {('FR', 'fr', 'prestation-b2b'): ('x', '10')})['version'], 10)
        self.assertEqual(D.choisir([dict(base, version=2), dict(base, version=10, review_date='2026-10-01')], 'FR', 'fr', 'prestation-b2b',
                                   '2026-11-18')['version'], 2)

    def test_B4_dossier_canadien_ou_ambigu_jamais_le_droit_francais(self):
        demande = 'Rédige un accord de confidentialité pour ce dossier'
        self.assertEqual(b.selection(self.desk, demande)['modele']['id'], 'accord_confidentialite')   # sans droit désigné : comportement existant
        with patch('agent.competences5683.droit_du_dossier', return_value={'valeur': 'Canada', 'source': 'fiche du dossier', 'juridiction': ''}):
            choix = b.selection(self.desk, demande, matter=MID)
            self.assertIsNone(choix['modele'])
            self.assertIn('province', choix['question'])
            trame = b.trame_for_request(self.desk, demande, matter=MID)
            self.assertEqual((trame['texte'], trame['sections_attendues']), ('', []))
            self.assertIn('Ne présume pas le droit français', trame['consigne'])
        with patch('agent.competences5683.droit_du_dossier', return_value={'valeur': 'Québec', 'source': 'fiche du dossier', 'juridiction': ''}):
            choix = b.selection(self.desk, demande, matter=MID)
            self.assertIsNone(choix['modele'])                                            # la trame québécoise est en brouillon
            self.assertIn(('qc_entente_confidentialite', 'brouillon'), choix['ecartes'])
            self.assertIn(('accord_confidentialite', 'autre droit (FR)'), choix['ecartes'])
            M.move(self.desk, {'modele': 'qc_entente_confidentialite', 'etat': 'review'}, 'avocate', 'avocat')
            self.assertEqual(b.selection(self.desk, demande, matter=MID)['modele']['id'], 'qc_entente_confidentialite')
            trame = b.trame_for_request(self.desk, demande, matter=MID)
            self.assertIn('Code civil du Québec', trame['texte'])
            self.assertIn('Barreau du Québec', trame['texte'] + b.notice_for('fr', 'CA-QC'))
            self.assertIn('n’est pas validé', trame['consigne'])
        with patch.object(b, '_droit_de_la_demande', return_value=(None, '')):            # mutation : droit du dossier ignoré
            with patch('agent.competences5683.droit_du_dossier', return_value={'valeur': 'Canada', 'source': 'fiche du dossier'}):
                self.assertEqual(b.selection(self.desk, demande, matter=MID)['modele']['id'], 'accord_confidentialite')

    def test_B4_province_canadienne_jamais_retenue_sans_designation(self):
        M.move(self.desk, {'modele': 'on_nda', 'etat': 'review'}, 'avocate', 'avocat')
        demande = 'Please prepare a confidentiality agreement'
        self.assertNotEqual((b.selection(self.desk, demande)['modele'] or {}).get('id'), 'on_nda')
        self.assertEqual(b.selection(self.desk, demande + ' governed by the laws of Ontario')['modele']['id'], 'on_nda')
        b.set_default_law(self.desk, {'droit': 'CA-ON'})                                   # province fixée par le cabinet
        self.assertEqual(b.selection(self.desk, demande)['modele']['id'], 'on_nda')

    def test_B4_brouillon_nouveau_retire_et_a_revoir_jamais_proposes(self):
        demande = 'Prépare une note de consultation avec options'
        self.assertIsNone(b.match(self.desk, demande))                                     # trame 5.6.108 en brouillon
        with patch.object(b, '_ecart_assiste', return_value=''):
            self.assertEqual(b.match(self.desk, demande)['id'], 'fr_note_consultation')   # mutation : filtre d'état retiré
        M.move(self.desk, {'modele': 'fr_note_consultation', 'etat': 'review'}, 'avocate', 'avocat')
        self.assertEqual(b.match(self.desk, demande)['id'], 'fr_note_consultation')
        M.move(self.desk, {'modele': 'fr_note_consultation', 'etat': 'withdrawn'}, 'avocate', 'avocat')
        self.assertIsNone(b.match(self.desk, demande))
        self.valider('accord_confidentialite')
        M.move(self.desk, {'modele': 'accord_confidentialite', 'etat': 'needs_update'}, 'avocate', 'avocat')
        self.assertNotEqual((b.match(self.desk, 'Rédige un accord de confidentialité') or {}).get('id'), 'accord_confidentialite')

    def test_B4_lecture_de_selection_sans_ecriture(self):
        b.catalog(self.desk)
        seen = []
        self.desk.db.set_trace_callback(seen.append)
        try:
            b.selection(self.desk, 'Rédige un accord de confidentialité', matter=MID)
        finally:
            self.desk.db.set_trace_callback(None)
        self.assertFalse([s for s in seen if re.match(r'\s*(INSERT|UPDATE|DELETE|CREATE|ALTER)', s, re.I)])

    def test_B4_instantane_des_missions_existantes_preserve(self):
        out = b.generate(self.desk, {'modele': 'accord_confidentialite', 'societe': self.cid, 'destination': 'ia'})
        request = self.desk.db.execute('SELECT request FROM docreq520 WHERE id=?', (out['request'],)).fetchone()['request']
        avant = b.trame_for_request(self.desk, request)
        M.move(self.desk, {'modele': 'accord_confidentialite', 'etat': 'withdrawn'}, 'avocate', 'avocat')
        with patch('agent.competences5683.droit_du_dossier', return_value={'valeur': 'Canada', 'source': 'fiche du dossier'}):
            apres = b.trame_for_request(self.desk, request, matter=MID)
        self.assertEqual((apres['empreinte'], apres['texte']), (avant['empreinte'], avant['texte']))
        self.assertEqual(apres['droit'], 'Droit français')

    # -------------------------------------------------------------------------------- incompatibilités, mention finale
    def test_B2_incompatibilites_refusees_et_mention_du_droit(self):
        with self.assertRaises(Qualified) as ex:
            b.generate(self.desk, {'modele': 'qc_contrat_service', 'clause_propriete_intellectuelle': True, 'clause_licence_seule': True})
        self.assertEqual(str(ex.exception), 'clauses_incompatibles')
        out = b.generate(self.desk, {'modele': 'on_nda', 'party_a': 'ZORVAL INC. (fictive)'}, 'avocate')
        raw = __import__('base64').b64decode(b.download(self.desk, out['goto'].rsplit('fichier=', 1)[1], 'avocate')['download']['base64'])
        z = zipfile.ZipFile(io.BytesIO(raw))
        self.assertIn('Law Society of Ontario', z.read('word/document.xml').decode())
        self.assertNotIn('England and Wales', z.read('word/document.xml').decode())
        self.assertIn('en-CA', z.read('word/styles.xml').decode())

    # -------------------------------------------------------------------------------- B6 : page
    def test_B6_page_filtre_droit_etat_badge(self):
        page = self.request('/corporate', query='vue=bibliotheque&droit=CA-QC')['body']
        self.assertIn('Entente de confidentialité (Québec)', page)
        self.assertIn('Brouillon', page)
        self.assertNotIn('Accord de confidentialité</strong>', page)
        self.assertIn('name="etat"', page)
        self.assertIn('Droit de l’Ontario (Canada)', page)
        old = self.request('/corporate', query='vue=bibliotheque&droit=EN')['body']       # ancien lien : alias
        self.assertIn('Non-Disclosure Agreement', old)
        self.valider('en_nda')
        page = self.request('/corporate', query='vue=bibliotheque&droit=tous&etat=validated')['body']
        self.assertIn('Non-Disclosure Agreement', page)
        self.assertNotIn('Services Agreement', page)
        detail = self.request('/corporate', query='vue=bibliotheque&modele=on_services_agreement')['body']
        for attendu in ('Qualification du modèle', 'Droit de l’Ontario (Canada)', 'Marquer à relire', 'Sources de contrôle', 'Interest Act'):
            self.assertIn(attendu, detail)

    def test_B6_action_reservee_aux_comptes_habilites(self):
        t = b.template(self.desk, 'on_nda')
        M.move(self.desk, {'modele': 'on_nda', 'etat': 'review'}, 'avocate', 'avocat')
        self.assertIn('Marquer validé', M.panel_html(self.desk, t))                       # administrateur (installation serveur)
        self.assertIn('name="reexamen_le"', M.panel_html(self.desk, t))
        with patch('agent.membres5650.viewer_role', return_value='assistant'):
            html = M.panel_html(self.desk, t)
        self.assertNotIn('Marquer validé', html)
        self.assertIn('réservé aux comptes habilités', html)
        with patch('agent.membres5650.effective_rights', side_effect=OSError('registre illisible')):
            self.assertNotIn('Marquer validé', M.panel_html(self.desk, t))               # contrôle en panne : aucun bouton
        with patch.object(M, 'habilite', return_value=True), patch('agent.membres5650.viewer_role', return_value='assistant'):
            self.assertIn('Marquer validé', M.panel_html(self.desk, t))                   # mutation : contrôle d'affichage retiré


if __name__ == '__main__':
    unittest.main()
