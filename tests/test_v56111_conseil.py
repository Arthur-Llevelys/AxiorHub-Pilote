"""5.6.111, lot K : parcours Conseil — modèles et questionnaires par droit applicable (F06 à F08 ; défauts L08 à L11 du lot original évités).

K1 six systèmes distincts, UK/GB/CA refusés ; K2 matrice de couverture exacte (« À préparer » jamais comblé par un autre droit), état du registre
seulement ; K3 questionnaires natifs enrichis (sections, questions conditionnelles, parties étrangères), validateur natif seul (date impossible,
montant booléen ou infini, réponse vide) ; K4 empreintes résolues par le serveur, droits du dossier revérifiés à chaque étape, ouverture commune
(un seul exemplaire) ; K5 préremplissage avec origine et états, sections indépendantes malgré un manque ; K6 contrôle serveur, document rouvert ;
K7 IA en pause et réglages accessibles ; K8 brief et rédaction réservée aux sections à rédiger.
Données fictives, faux fournisseurs : aucun réseau, aucun modèle réel. Les essais vérifient le circuit, jamais la qualité juridique des trames."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_v56108_questionnaires as TQ
from agent import bibliotheque5639 as lib, bibliotheque_droits56108 as BD, droits56108 as D, membres5650, missions567, modeles5684
from agent import questionnaires56108 as Q
from agent.common import Stop
from agent.erreurs5634 import Qualified

AVOCAT, ASSISTANT, MID = TQ.AVOCAT, TQ.ASSISTANT, TQ.MID
RACINE = Path(__file__).resolve().parents[1]
ATTENDU = {   # cases couvertes / cases de la matrice des familles Conseil, par système (langues d'usage du système)
    'FR': (16, 16), 'UK-EAW': (7, 16), 'DE': (7, 16), 'ES': (7, 16), 'CA-QC': (4, 32), 'CA-ON': (4, 32)}


# ------------------------------------------------------------------------------------------------ K1, K2 : droits et matrice (pur)
class Matrice(unittest.TestCase):
    def variantes(self):
        out = []
        for ident in lib.BUILTIN:
            t = lib._build(ident, None)
            q = D.qualification(t)
            out.append({'id': ident, 'titre': t['titre'], 'type_acte': q['type_acte'], 'systeme': q['systeme'], 'langue': t['langue'], 'version': 1,
                        'etat': 'draft'})
        return out

    def test_K1_six_systemes_distincts_et_ambigus_refuses(self):
        self.assertEqual(D.PRIORITAIRES, ('FR', 'UK-EAW', 'DE', 'ES', 'CA-QC', 'CA-ON'))
        self.assertEqual({D.migrer(t['droit']) for t in lib.BUILTIN.values()}, set(D.PRIORITAIRES))
        for code in ('UK', 'GB', 'CA', 'EN', 'uk', ''):
            with self.assertRaisesRegex(Stop, 'systeme_juridique_a_preciser'):
                D.systeme_requis(code)
        self.assertEqual(D.systeme_requis('ca-fed'), 'CA-FED')            # fédéral seulement : code distinct, jamais une province
        self.assertNotIn('UK', D.SYSTEMES)
        self.assertNotIn('CA', D.SYSTEMES)
        self.assertEqual(D.lire('Royaume-Uni')['statut'], 'ambigu')
        self.assertEqual(D.lire('droit canadien')['statut'], 'ambigu')
        self.assertEqual(D.droit_mentionne('Rédige en anglais un accord'), None)   # la langue ne désigne jamais un droit

    def test_K2_matrice_exacte_et_absences_visibles(self):
        m = D.couverture(self.variantes())
        self.assertEqual(m['colonnes'], [('FR', 'fr'), ('UK-EAW', 'en'), ('DE', 'de'), ('ES', 'es'), ('CA-QC', 'fr'), ('CA-QC', 'en'), ('CA-ON', 'en'),
                                         ('CA-ON', 'fr')])
        self.assertEqual([r['type'] for r in m['lignes']], list(D.FAMILLES_CONSEIL))
        for s, (couvertes, cases) in ATTENDU.items():
            with self.subTest(systeme=s):
                self.assertEqual((m['totaux'][s]['cases'] - m['totaux'][s]['absent'], m['totaux'][s]['cases']), (couvertes, cases))
                self.assertEqual(m['totaux'][s]['validated'], 0)          # aucune validation fabriquée
        case = {(r['type'], c['systeme'], c['langue']): c for r in m['lignes'] for c in r['cases']}
        self.assertEqual(case[('cgv-b2c', 'DE', 'de')]['etat'], 'absent')   # jamais la trame française à la place
        self.assertEqual(case[('cgv-b2c', 'DE', 'de')]['variantes'], [])
        self.assertEqual(case[('confidentialite', 'CA-QC', 'en')]['etat'], 'absent')   # la version française du Québec ne couvre pas l'anglais
        self.assertEqual([v['id'] for v in case[('confidentialite', 'CA-QC', 'fr')]['variantes']], ['qc_entente_confidentialite'])
        self.assertEqual({v['id'] for v in case[('pacte', 'FR', 'fr')]['variantes']}, {'pacte_associes', 'pacte_actionnaires'})
        self.assertEqual(case[('cgv-b2b', 'FR', 'fr')]['variantes'][0]['id'], 'cgv_b2b')   # B2B et B2C distinctes
        self.assertEqual(case[('cgv-b2c', 'FR', 'fr')]['variantes'][0]['id'], 'fr_cgv_consommateurs')

    def test_K2_etat_de_la_case_vient_du_registre(self):
        v = self.variantes()
        nda = next(x for x in v if x['id'] == 'en_nda')
        nda.update(etat='validated', valide_par='avocate@example.test', reexamen_le='2027-11-26')
        m = D.couverture(v)
        c = next(c for r in m['lignes'] if r['type'] == 'confidentialite' for c in r['cases'] if c['systeme'] == 'UK-EAW')
        self.assertEqual(c['etat'], 'validated')
        self.assertEqual(m['totaux']['UK-EAW']['validated'], 1)

    def test_K2_trames_livrees_restent_a_l_etat_initial(self):
        for ident, t in lib.BUILTIN.items():
            q = D.qualification(t)
            self.assertEqual((q['etat_initial'], q['valide_par'], q['valide_le']), ('draft', '', ''), ident)
        for t in BD.TEMPLATES56111:   # trames ajoutées : brouillons jamais proposés automatiquement, structure valide, droit français seulement
            lib.check_structure(t['texte'])
            self.assertTrue(t['nouveau56108'])
            self.assertFalse(D.qualification(t)['selection_assistee'])
            self.assertEqual((t['droit'], t['langue']), ('FR', 'fr'))
            self.assertTrue(t['sources'])
        self.assertEqual(len(BD.TEMPLATES), 24)                          # ensemble 5.6.108 inchangé


# ------------------------------------------------------------------------------------------------ K3 : questionnaires natifs enrichis (pur)
class Questionnaires(unittest.TestCase):
    def test_K3_chaque_specification_vise_une_trame_et_ses_champs(self):
        groupes = set(Q.LIBELLES_GROUPES)
        for ident, spec in BD.QUESTIONS.items():
            with self.subTest(modele=ident):
                t = lib._build(ident, None)
                s = Q.deriver(t, requis=D.qualification(t)['champs_requis'])
                ids = [c['id'] for c in s['champs']]
                self.assertEqual(len(ids), len(set(ids)))                 # aucune question en double
                self.assertTrue(set(spec['groupes']) <= set(ids), set(spec['groupes']) - set(ids))
                self.assertTrue(set(spec['aides']) <= set(ids))
                self.assertTrue({c['groupe'] for c in s['champs']} <= groupes)
                for c in s['champs']:                                      # une clause reste un choix de rédaction
                    if 'defaut_redaction' in c or (c['type'] == 'booleen' and c['id'] in {k for k, *_ in t['clauses']}):
                        self.assertEqual(c['groupe'], 'redaction')
                self.assertEqual(s['systeme_juridique'], t['droit'])
                self.assertEqual(s['origine'], 'trame')

    def test_K3_familles_prioritaires_couvertes_dans_les_six_droits(self):
        couverts = {(D.type_acte(lib.BUILTIN[k]), D.migrer(lib.BUILTIN[k]['droit'])) for k in BD.QUESTIONS}
        for ta in ('note-consultation', 'confidentialite', 'prestation-b2b', 'questionnaire-mission'):
            for s in D.PRIORITAIRES:
                self.assertIn((ta, s), couverts)
        for ta in ('analyse-contrat', 'cgv-b2b', 'cgv-b2c', 'cgu', 'cga', 'agence-commerciale', 'licence-logiciel', 'pacte', 'cession-titres',
                   'bail-commercial', 'lettre-mission', 'convention-honoraires'):
            self.assertIn((ta, 'FR'), couverts)

    def test_K3_sections_du_parcours_et_question_conditionnelle(self):
        s = Q.deriver(lib.BUILTIN['contrat_prestation'])
        c = {x['id']: x for x in s['champs']}
        self.assertEqual((c['prix']['groupe'], c['plafond']['groupe'], c['duree']['groupe'], c['tribunal']['groupe']),
                         ('economie', 'responsabilite', 'duree', 'litiges'))
        self.assertEqual(c['donnees_precisions']['actif_si'], {'champ': 'donnees_personnelles', 'egal': True})
        self.assertTrue(c['signataires']['bloquant_signature'])
        ordre = [x['groupe'] for x in s['champs']]
        self.assertEqual(ordre, sorted(ordre, key=[g for g, _ in Q.GROUPES].index))
        p = Q.planifier(s, {}, {})
        self.assertIn('donnees_precisions', p['attente_condition'])        # posée seulement après la réponse qui la commande
        r = {'donnees_personnelles': {'statut': 'confirme', 'valeur': False, 'nature': 'choix_avocat'}}
        self.assertIn('donnees_precisions', Q.planifier(s, {}, r)['inactifs'])
        self.assertNotIn('delai_paiement', p['valeurs'])                    # la suggestion de la trame n'est jamais une réponse

    def test_K3_parties_d_une_trame_etrangere_bloquantes_comme_en_francais(self):
        for ident, champs in (('en_nda', ('party_a', 'party_b')), ('de_geheimhaltung', ('partei_a', 'partei_b')), ('es_confidencialidad', ('parte_a', 'parte_b'))):
            c = {x['id']: x for x in Q.deriver(lib.BUILTIN[ident])['champs']}
            for f in champs:
                self.assertEqual((c[f]['groupe'], c[f]['type'], c[f]['bloquant_signature'], c[f]['confirmation_requise']), ('parties', 'partie', True, True))
        sans = {x['id']: x for x in Q.deriver({**lib.BUILTIN['en_nda'], 'id': 'cab_essai', 'origine': 'cabinet'})['champs']}
        self.assertNotIn('signataires', sans)                               # modèle du cabinet : rien n'est ajouté

    def test_K3_validateur_natif_seul(self):
        s = Q.deriver(lib.BUILTIN['fr_bail_commercial'])
        c = {x['id']: x for x in s['champs']}
        self.assertEqual((c['loyer']['type'], c['date_effet']['type']), ('montant', 'date'))
        for champ, valeur, code in ((c['date_effet'], '2026-02-31', 'date_invalide'), (c['loyer'], True, 'texte_attendu'),
                                    (c['loyer'], 'Infinity', 'montant_invalide'), (c['loyer'], 'NaN', 'montant_invalide'), (c['locaux'], '   ', 'texte_vide'),
                                    (c['echelle_mobile'], 'true', 'booleen_attendu')):
            with self.assertRaisesRegex(ValueError, code):
                Q.valider_valeur(champ, valeur)
        self.assertEqual(Q.valider_valeur(c['loyer'], '36 000'), '36000.00')
        with self.assertRaises(Qualified):   # une question ajoutée mal formée serait refusée par le validateur natif, pas acceptée en silence
            Q.valider_schema({'champs': [{'id': 'clientele', 'libelle': 'Clientèle', 'type': 'choix_multiple'}]})

    def test_K3_aucun_vestige_du_format_concurrent(self):
        source = (RACINE / 'agent' / 'bibliotheque_droits56108.py').read_text(encoding='utf-8') + (RACINE / 'agent' / 'questionnaires56108.py').read_text(encoding='utf-8')
        for interdit in ('briefs_redaction5611', 'atelier_redaction', 'choix_multiple', "'obligatoire'", 'modele_sha256', 'texte_long'):
            self.assertNotIn(interdit, source)
        self.assertFalse((RACINE / 'agent' / 'atelier_redaction5611.py').exists())


# ------------------------------------------------------------------------------------------------ service (base, routes)
class Service(TQ.Base):
    def ouvrir_modele(self, modele, who=AVOCAT, **extra):
        st, v, _ = self.route('creer', {'modele': modele, 'dossier': MID, **extra}, who=who)
        self.assertEqual(st, '200 OK', v)
        return v

    # K4 ---------------------------------------------------------------------------------------------
    def test_K4_empreinte_et_version_resolues_par_le_serveur(self):
        v = self.ouvrir_modele('fr_bail_commercial', modele_version=99, empreinte_version='f' * 64, schema={'champs': []}, modele_sha256='0' * 64)
        q = Q._ligne(self.desk, v['id'])
        ligne = modeles5684.current(self.desk, 'fr_bail_commercial')
        schema = Q.questionnaire_de(lib.template(self.desk, 'fr_bail_commercial'), ligne)
        self.assertEqual(q['modele_version'], ligne['version'])
        self.assertEqual(q['empreinte_version'], Q.empreinte_version(ligne, schema))
        self.assertNotEqual(q['empreinte_version'], 'f' * 64)
        self.assertEqual(json.loads(q['schema']), schema)

    def test_K4_ouverture_commune_un_seul_exemplaire(self):
        a = self.ouvrir_modele('fr_cgv_consommateurs', reprendre=True)
        b = self.ouvrir_modele('fr_cgv_consommateurs', reprendre=True)
        self.assertEqual(a['id'], b['id'])
        self.assertFalse(a['repris'])
        self.assertTrue(b['repris'])
        autre = self.ouvrir_modele('fr_cgv_consommateurs', who=TQ.COLLEGUE, reprendre=True)
        self.assertNotEqual(autre['id'], a['id'])                          # le questionnaire d'un collègue n'est jamais repris
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM questionnaires56108 WHERE modele='fr_cgv_consommateurs' AND owner=?",
                                              (AVOCAT[0],)).fetchone()[0], 1)
        with self.assertRaisesRegex(Stop, 'dossier_requis'):
            Q.ouvrir(self.desk, {'modele': 'fr_cgv_consommateurs', 'dossier': 'DOS-INCONNU'}, *AVOCAT)
        with patch.object(Q, '_masques', return_value={MID}):               # dossier réservé : rien n'est révélé ni repris
            with self.assertRaisesRegex(Stop, 'dossier_requis'):
                Q.ouvrir(self.desk, {'modele': 'fr_cgv_consommateurs', 'dossier': MID}, *AVOCAT)

    def test_K4_droits_du_dossier_revérifies_a_chaque_etape(self):
        qid = self.ouvrir_modele('fr_note_analyse_contrat')['id']
        with patch.object(Q, '_masques', return_value={MID}):
            for route, corps, args in (('vue', None, {'id': qid}), ('repondre', {'id': qid, 'revision': 1, 'reponses': {'auteur': 'Me DURAND'}}, None),
                                       ('telecharger', {'id': qid, 'revision': 1}, None), ('corriger', {'id': qid, 'revision': 1, 'section': 'art-synthese',
                                                                                                         'texte': 'x'}, None)):
                st, out, _ = self.route(route, corps, args)
                self.assertEqual((st, out['error']), ('404 Not Found', 'introuvable'), route)
        self.assertEqual(self.route('vue', args={'id': qid}, who=TQ.COLLEGUE)[0], '404 Not Found')

    def test_K4_cartes_d_ouverture_produire_cession_dossier(self):
        membres5650.set_author(AVOCAT[0])
        membres5650.set_viewer_role(AVOCAT[1])
        qid = self.ouvrir_modele('fr_licence_logiciel', reprendre=True)['id']
        html = Q.carte_ouverture_html(self.desk, '/p', MID)
        for texte in ('data-q108-creer data-q108-reprendre', 'Ouvrir le questionnaire', 'Contrat de licence de logiciel', '/p/corporate?vue=bibliotheque&amp;matrice=1',
                      '/p/corporate?vue=bibliotheque&amp;questionnaire=' + qid, 'Questionnaires de ce dossier', 'v56108-questionnaire.js'):
            self.assertIn(texte, html)
        self.assertNotIn('<script>', html)
        cession = Q.carte_ouverture_html(self.desk, '/p', MID, ('cession-titres', 'bail-commercial'))
        self.assertIn('Bail commercial', cession)
        self.assertNotIn('value="fr_licence_logiciel"', cession)
        self.assertIn('value="fr_licence_logiciel"', html)
        from agent import production420_ui
        studio = production420_ui.studio_page(self.desk, {'matter': MID}, lambda *a, **k: '<form></form>',
                                              lambda p, label, **kw: '<a href="/p' + p + '">' + label + '</a>')
        self.assertIn('Préparer un acte avec un modèle et son questionnaire', studio)
        self.assertIn('/p/static/v56108-questionnaire.js', studio)
        js = (RACINE / 'agent' / 'static' / 'v56108-questionnaire.js').read_text(encoding='utf-8')
        self.assertIn("form.hasAttribute('data-q108-reprendre')", js)

    # K5 ---------------------------------------------------------------------------------------------
    def test_K5_parties_du_dossier_proposees_avec_leur_origine(self):
        from agent import dossiers5697
        dossiers5697.ensure_schema(self.desk)
        quand = '2026-11-26T09:00:00+00:00'
        self.desk.db.execute('INSERT OR REPLACE INTO dossiers5697(id,cabinet_id,ref,ref_norm,titre,juridiction,rg,chemin,cree_le,modifie_le) VALUES(?,?,?,?,?,?,?,?,?,?)',
                             (MID, 'cabinet', 'DOS-001', 'dos001', 'DURAND c/ MARTIN', '', '', '/Dossiers/DEMO', quand, quand))
        for pid, nom, role in (('p111k1', 'KERVILAC SARL', 'client'), ('p111k2', 'ZORVAL SAS', 'cocontractant')):
            self.desk.db.execute('INSERT OR REPLACE INTO dossier_parties5697(id,dossier_id,nom,nom_norm,role,courriel,cree_le) VALUES(?,?,?,?,?,?,?)',
                                 (pid, MID, nom, nom.lower(), role, '', quand))
        self.desk.db.commit()
        self.assertEqual(dossiers5697.resoudre(self.desk, MID), MID)
        qid = self.ouvrir_modele('lettre_mission')['id']
        v = self.vue(qid)
        q = next(x for x in v['questions'] if x['champ'] == 'partie_conseillee')
        self.assertEqual((q['motif'], q['proposition'], q['sources'][0]['origine']), ('a_confirmer', 'KERVILAC SARL', 'dossier'))
        self.assertIn('Onglet Parties', q['sources'][0]['emplacement'])
        self.assertEqual(v['etats']['partie_conseillee'], 'a_confirmer')     # trouvé : à confirmer, jamais une réponse
        self.assertNotIn('partie_conseillee', v['valeurs'])
        st, out, _ = self.route('repondre', {'id': qid, 'revision': 1, 'reponses': {'partie_conseillee': {'statut': 'confirme', 'valeur': 'KERVILAC SARL',
                                                                                                             'source': q['sources'][0]['id']}}})
        self.assertEqual(st, '200 OK', out)
        self.assertEqual(self.vue(qid)['etats']['partie_conseillee'], 'confirme')

    def test_K5_etats_inconnu_contradictoire_et_sections_independantes(self):
        qid = self.ouvrir_modele('fr_bail_commercial')['id']
        st, out, _ = self.route('repondre', {'id': qid, 'revision': 1, 'reponses': {
            'bailleur': 'DURAND SCI', 'preneur': 'MARTIN SARL', 'locaux': 'Local fictif de 80 m² au rez-de-chaussée', 'destination': 'Commerce de détail',
            'date_effet': '2027-01-01', 'loyer': {'statut': 'inconnu'}, 'indice': {'statut': 'plus_tard'}}})
        self.assertEqual(st, '200 OK', out)
        v = self.vue(qid)
        self.assertEqual((v['etats']['loyer'], v['etats']['indice'], v['etats']['bailleur']), ('inconnu', 'inconnu', 'confirme'))
        sec = {s['id']: s['etat'] for s in v['apercu']['sections']}
        self.assertEqual(sec['art-designation'], 'redigee')                 # préparée malgré le loyer inconnu
        self.assertEqual(sec['art-loyer'], 'a_completer')
        self.assertFalse(v['apercu']['pret_signature'])
        self.assertTrue(any(c['code'] == 'donnees_bloquantes' and c['bloquant'] for c in v['controles']))
        self.assertIn('Signataires et pouvoirs', ' '.join(c['message'] for c in v['controles']))
        s = json.loads(Q._ligne(self.desk, qid)['schema'])
        plan = Q.planifier(s, {'loyer': [TQ.source('36000', 'S1'), TQ.source('38000', 'S2')]}, {})
        index = {x['champ']: x for x in plan['questions']}
        self.assertEqual(Q.etat_champ(next(c for c in s['champs'] if c['id'] == 'loyer'), plan, {}, index), 'contradictoire')
        trouve = Q.planifier(s, {'destination': [TQ.source('Commerce de détail', 'S1', fait_valide=True)]}, {})
        self.assertEqual(Q.etat_champ(next(c for c in s['champs'] if c['id'] == 'destination'), trouve, {}, {}), 'trouve')

    # K6 ---------------------------------------------------------------------------------------------
    def test_K6_clauses_incompatibles_et_document_rouvert(self):
        qid = self.ouvrir_modele('fr_licence_logiciel')['id']
        st, out, _ = self.route('repondre', {'id': qid, 'revision': 1, 'reponses': {'acces_en_ligne': True, 'installation': True}})
        self.assertEqual(st, '200 OK', out)
        v = self.vue(qid)
        clash = [c for c in v['controles'] if c['code'] == 'clauses_incompatibles']
        self.assertTrue(clash and clash[0]['bloquant'])
        self.assertIn('Accès en ligne', clash[0]['message'])
        st, out, _ = self.route('telecharger', {'id': qid, 'revision': 2})
        self.assertEqual(st, '200 OK', out)
        self.assertFalse(out['controle']['pret'])
        self.assertEqual((out['controle']['source'], out['controle']['ecarts']), ('texte', []))
        self.assertIn('Document rouvert et contrôlé', out['message'])
        self.assertEqual(Q.controler_docx(b'pas un docx', []), ['document produit illisible'])
        docx = lib.build_docx([('Article 1 — Objet {{prix}}', 'article')], 'fr')
        self.assertIn('balise de variable restée dans le document', Q.controler_docx(docx, None))
        self.assertTrue(Q.controler_docx(lib.build_docx([('Objet', 'texte')], 'fr'), [('Objet', 'texte'), ('Paragraphe perdu', 'texte')]))

    def test_K6_version_plus_recente_signalee_ancien_questionnaire_garde(self):
        qid = self.ouvrir_modele('accord_confidentialite')['id']
        t = lib.template(self.desk, 'accord_confidentialite')
        modeles5684.publish(self.desk, {'modele': t['id'], 'texte': '\n'.join(t['texte']) + '\nAnnexe : liste des informations.', 'commentaire': 'Annexe'},
                            AVOCAT[0])
        v = self.vue(qid)
        self.assertEqual(v['modele_version'], 1)
        self.assertTrue(any(c['code'] == 'modele_plus_recent' and not c['bloquant'] for c in v['controles']))
        nouveau = self.ouvrir_modele('accord_confidentialite', reprendre=True)
        self.assertNotEqual(nouveau['id'], qid)                              # nouvelle version : nouveau questionnaire, l'ancien reste

    # K7 ---------------------------------------------------------------------------------------------
    def test_K7_ia_en_pause_parcours_manuel_et_reglages_accessibles(self):
        from agent import arret5643
        arret5643.pause_all(self.desk, True, AVOCAT[0])
        self.addCleanup(arret5643.pause_all, self.desk, False, AVOCAT[0])
        qid = self.ouvrir_modele('fr_agence_commerciale', reprendre=True)['id']
        self.assertEqual(self.route('repondre', {'id': qid, 'revision': 1, 'reponses': {'mandant': 'DURAND SAS', 'agent': 'MARTIN'}})[0], '200 OK')
        self.assertEqual(self.route('telecharger', {'id': qid, 'revision': 2})[0], '200 OK')
        st, out, _ = self.route('rediger', {'id': qid, 'revision': 2})
        self.assertEqual((st, out['etat']), ('200 OK', 'suspendu'))
        # réglages et registre restent accessibles à l'administrateur et à l'avocat pendant la pause (aucun interrupteur ne les verrouille)
        self.assertIn('Droit applicable par défaut du cabinet', lib.set_default_law(self.desk, {'droit': 'DE'}, AVOCAT[0])['message'])
        lib.set_default_law(self.desk, {'droit': 'FR'}, AVOCAT[0])
        with self.assertRaisesRegex(Stop, 'systeme_juridique_a_preciser'):
            lib.set_default_law(self.desk, {'droit': 'GB'}, AVOCAT[0])
        self.assertEqual(modeles5684.move(self.desk, {'modele': 'fr_agence_commerciale', 'etat': 'review'}, AVOCAT[0], 'avocat')['message'], 'Modèle : À relire.')
        with self.assertRaisesRegex(Stop, 'etat_modele_reserve_aux_personnes'):
            modeles5684.move(self.desk, {'modele': 'fr_agence_commerciale', 'etat': 'validated'}, 'ia:assistant', 'avocat')
        html = lib.page_html(self.desk, '/p', {'matrice': '1'})
        self.assertIn('Couverture des modèles par droit applicable', html)
        self.assertIn('À relire', html)
        with self.assertRaisesRegex(Stop, 'systeme_juridique_a_preciser'):
            modeles5684.selectionner(self.desk, 'en_nda', systeme='UK')

    def test_K7_page_matrice_lecture_seule_accessible(self):
        avant = self.desk.db.total_changes
        html = lib.page_html(self.desk, '/p', {'matrice': '1'})
        tous = lib.page_html(self.desk, '/p', {'matrice': '1', 'types': 'tous'})
        self.assertEqual(self.desk.db.total_changes, avant)
        self.assertEqual(html.count('<th scope="row">'), len(D.FAMILLES_CONSEIL) + len(D.PRIORITAIRES))
        self.assertGreater(tous.count('<th scope="row">'), html.count('<th scope="row">'))
        for texte in ('<caption>', 'À préparer', 'tabindex="0" role="region"', 'Couverture par droit', '/p/corporate?vue=bibliotheque&amp;modele=fr_bail_commercial',
                      'Une traduction ne vaut pas adaptation'):
            self.assertIn(texte, html)
        self.assertIn('/p/corporate?vue=bibliotheque&amp;matrice=1', lib.page_html(self.desk, '/p', {}))
        self.assertNotIn('<script', html)

    # K8 ---------------------------------------------------------------------------------------------
    def test_K8_brief_structure_et_redaction_reservee_aux_mentions(self):
        qid = self.ouvrir_modele('fr_note_consultation')['id']
        st, out, _ = self.route('repondre', {'id': qid, 'revision': 1, 'reponses': {
            'destinataire': 'DURAND SAS', 'auteur': 'Me MARTIN', 'reference': 'DOS-001', 'objet': 'Résiliation fictive', 'question': 'Peut-on résilier ?',
            'faits': 'Contrat fictif conclu en 2025.', 'date_acte': '2026-11-26', 'option_c': False, 'urgence': False}})
        self.assertEqual(st, '200 OK', out)
        q = Q._ligne(self.desk, qid)
        v = Q.etat(self.desk, q, *AVOCAT)
        b = Q.brief(q, json.loads(q['instantane']), v)
        self.assertEqual((b['modele']['id'], b['modele']['version'], b['droit'], b['langue']), ('fr_note_consultation', 1, 'FR', 'fr'))
        self.assertEqual(b['modele']['empreinte'], q['empreinte_version'][:16])
        self.assertIn('Clause : Troisième option', b['clauses_ecartees'])
        self.assertEqual(b['donnees_retenues']['Question posée']['valeur'], 'Peut-on résilier ?')
        self.assertEqual(b['passages_utiles'], [])                        # aucune source retenue : aucun passage, jamais le dossier entier
        modele = TQ.FauxActe(petit=True)
        st, out, _ = self.route('rediger', {'id': qid, 'revision': 2})
        self.assertEqual(st, '200 OK', out)
        with patch('agent.chaine5689.modele_pour', return_value=modele):
            self.assertTrue(self.desk.work_once())
        self.assertTrue(modele.sections)
        a_rediger = {s['titre'] for s in self.vue(qid)['apercu']['sections'] if Q.MENTION.search(s['texte'])}
        self.assertTrue(set(modele.sections) <= a_rediger, (modele.sections, a_rediger))   # « Question posée », « Faits » : remplis par le code
        self.assertTrue(all('brief' in c for c in modele.charges if 'section' in c))
        r = missions567.get(self.desk, out['mission'], missions567.owner_for(AVOCAT[0]))['resultat5689']
        self.assertTrue(any('remplie(s) par le code' in x for x in r['limites']), r['limites'])


# ------------------------------------------------------------------------------------------------ libellés de l'écran traduits
class Traductions(unittest.TestCase):
    def test_libelles_nouveaux_traduits_en_de_es(self):
        phrases = set(lib.TEXTES56111.values()) | set(Q.TEXTES_OUVERTURE.values()) | set(Q.ETATS_CHAMP.values()) | {v for _, v in Q.GROUPES}
        phrases |= set(D.ETATS_COUVERTURE.values()) | {D.TYPES[k] for k in D.FAMILLES_CONSEIL}
        phrases |= {'Contrôle avant « prêt » : # point(s)', 'Données bloquantes pour la signature à confirmer : %s.', 'Brouillon Word prêt.',
                    'Document rouvert et contrôlé : aucun écart de structure.', 'Document rouvert et contrôlé : # point(s) relevé(s).',
                    '# section(s) remplie(s) par le code à partir du questionnaire, sans rédaction par l’IA.'}
        for lang in ('en', 'de', 'es'):
            table = json.loads((RACINE / 'agent' / 'locales' / ('ecrans_%s.json' % lang)).read_text(encoding='utf-8'))
            manque = sorted(p for p in phrases if not table.get(p))
            self.assertEqual(manque, [], lang)


if __name__ == '__main__':
    unittest.main()
