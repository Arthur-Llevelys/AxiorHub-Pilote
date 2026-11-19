"""5.6.108, lot Q : questionnaires des modèles d'actes et fonction « Demander ».

Q1 schéma typé figé dans le registre ; Q2 préremplissage sourcé (trouvé, confirmé, choisi : distincts ; conflit sans choix par récence ; aucun
score lu) ; Q3 formulaire (Enregistrer et continuer, Je ne sais pas, Répondre plus tard ; révision, auteur, version) ; Q4 rédaction partielle
(sections indépendantes, À compléter, corrections préservées, source modifiée) ; Q5 contrôleur (ask_user commun, petit modèle : le serveur
pose les questions ; attente sans travailleur bloqué ; reprise ciblée) ; Q6 IA en pause.
Données fictives, faux fournisseurs : aucun appel réseau, aucun modèle réel. Les tests vérifient le circuit, pas la qualité juridique."""
from datetime import date
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import unittest
from unittest.mock import patch

import test_v5689_ia as base
from test_v5689_ia import AVOCAT, CHEMIN, MID, muter
from test_v5689_dossier import ASSISTANT, DOSSIER
from agent import bibliotheque5639 as lib, membres5650, missions567, modeles5684, questionnaires56108 as Q, standalone_auth, ui_ai5689 as ia
from agent.common import Stop
from agent.erreurs5634 import Qualified

MODELE = 'contrat_prestation'
COLLEGUE = ('martin@example.test', 'avocat')

CHAMPS = [{'id': 'partie', 'libelle': 'Client', 'type': 'texte'},
          {'id': 'prix', 'libelle': 'Prix HT convenu', 'type': 'montant', 'confirmation_requise': True},
          {'id': 'echelonne', 'libelle': 'Paiement échelonné', 'type': 'booleen'},
          {'id': 'date', 'libelle': 'Premier paiement', 'type': 'date', 'actif_si': {'champ': 'echelonne', 'egal': True}}]
SCHEMA = {'format': Q.FORMAT, 'champs': CHAMPS, 'dependances': {'parties': ['partie'], 'prix': ['prix'], 'paiement': ['echelonne', 'date']}}


def source(valeur, ident='S1', version='a', **kw):
    return dict(valeur=valeur, source_id=ident, source_version=version, accessible=True, source_verifiee=True, **kw)


def schema():
    return Q.valider_schema(SCHEMA)


# ------------------------------------------------------------------------------------------------ Q1 : schéma
class Schema(unittest.TestCase):
    def test_Q1_valeurs_typees_zero_false_et_refus(self):
        c = {c['id']: c for c in schema()['champs']}
        self.assertEqual(Q.valider_valeur(c['prix'], '125 000,5'), '125000.50')
        self.assertEqual(Q.valider_valeur(c['prix'], '0'), '0.00')
        self.assertIs(Q.valider_valeur(c['echelonne'], False), False)
        for champ, v in [(c['prix'], 'NaN'), (c['prix'], True), (c['prix'], '1.234'), (c['prix'], 1.5), (c['date'], '2026-02-30'),
                         (c['date'], '30/01/2026'), (c['echelonne'], 'false'), (c['partie'], '   ')]:
            with self.assertRaises(ValueError, msg=repr(v)):
                Q.valider_valeur(champ, v)
        with self.assertRaisesRegex(ValueError, 'texte_trop_long'):      # refusé avec un message utile, jamais coupé
            Q.valider_valeur({**c['partie'], 'longueur_max': 5}, 'DURAND et associés')

    def test_Q1_schema_importe_controle(self):
        mauvais = [
            {**SCHEMA, 'script': 'x'},                                                                    # clé inconnue
            {**SCHEMA, 'champs': CHAMPS + [{'id': 'prix', 'libelle': 'Doublon', 'type': 'texte'}]},       # identifiant en double
            {**SCHEMA, 'champs': [CHAMPS[3]] + CHAMPS[:3]},                                               # condition sur un champ placé après
            {**SCHEMA, 'champs': CHAMPS[:2] + [{**CHAMPS[2], 'actif_si': {'champ': 'prix', 'egal': True, 'code': 'eval(1)'}}]},   # pas d'expression
            {**SCHEMA, 'dependances': {'parties': ['inconnu']}},                                         # référence inexistante
            {**SCHEMA, 'champs': CHAMPS + [{'id': 'x', 'libelle': 'X', 'type': 'texte', 'defaut_redaction': 'exemple'}]},   # défaut de fond interdit
            {**SCHEMA, 'champs': [{'id': 'c', 'libelle': 'C', 'type': 'choix', 'options': ['A']}]},     # options insuffisantes
            {**SCHEMA, 'champs': [{'id': 'm', 'libelle': 'M', 'type': 'montant', 'devise': 'euro'}]},   # devise invalide
        ]
        for brut in mauvais:
            with self.assertRaises(Qualified, msg=json.dumps(brut, ensure_ascii=False)[:120]):
                Q.valider_schema(brut)
        profond = [{'id': 'a0', 'libelle': 'A0', 'type': 'booleen'}] + [
            {'id': 'a%d' % i, 'libelle': 'A%d' % i, 'type': 'booleen', 'actif_si': {'champ': 'a%d' % (i - 1), 'egal': True}} for i in range(1, 7)]
        with self.assertRaisesRegex(Qualified, 'questionnaire_invalide'):
            Q.valider_schema({'champs': profond})
        with self.assertRaises(Qualified):
            Q.valider_schema('{' * 10)

    def test_Q1_empreinte_change_avec_une_option_ou_une_dependance(self):
        a = Q.empreinte(schema())
        self.assertEqual(a, Q.empreinte(Q.valider_schema(json.loads(json.dumps(SCHEMA)))))
        autre = json.loads(json.dumps(SCHEMA))
        autre['dependances']['prix'] = ['prix', 'partie']
        self.assertNotEqual(a, Q.empreinte(Q.valider_schema(autre)))

    def test_Q1_derive_de_la_trame_suggestion_jamais_reponse(self):
        s = Q.deriver(lib.BUILTIN[MODELE])
        c = {x['id']: x for x in s['champs']}
        self.assertEqual(c['prestataire']['type'], 'partie')
        self.assertTrue(c['prestataire']['bloquant_signature'])
        self.assertEqual(c['prix']['type'], 'texte')                          # « Prix et modalités » : une formule, pas un montant
        self.assertEqual(c['pi']['type'], 'booleen')
        self.assertIs(c['pi']['defaut_redaction'], True)                      # option de rédaction déclarée
        self.assertIn('suggestion', c['delai_paiement'])
        plan = Q.planifier(s, {}, {})
        self.assertNotIn('delai_paiement', plan['valeurs'])                   # l'exemple du modèle n'est jamais une réponse
        self.assertEqual(plan['natures']['pi'], 'option_modele')
        capital = {x['id']: x for x in Q.deriver(lib.BUILTIN['statuts_sas'])['champs']}['capital']
        self.assertEqual((capital['type'], capital['devise']), ('montant', 'EUR'))


# ------------------------------------------------------------------------------------------------ Q2 : préremplissage et plan
class Plan(unittest.TestCase):
    def test_Q2_fait_valide_reutilise_et_sections_independantes(self):
        r = Q.planifier(schema(), {'partie': [source('Société fictive', fait_valide=True)]}, {})
        self.assertIn('parties', r['sections_pretes'])
        self.assertIn('prix', r['sections_bloquees'])
        self.assertEqual(r['natures']['partie'], 'trouve')                     # trouvée, pas « confirmée »
        self.assertNotIn('date', [q['champ'] for q in r['questions']])        # conditionnelle : le choix qui la commande d'abord
        self.assertIn('date', r['attente_condition'])

    def test_Q2_contradiction_ne_choisit_pas_le_plus_recent(self):
        r = Q.planifier(schema(), {'prix': [source('125000', 'S1', '2026-01-01'), source('130000', 'S2', '2026-03-01')]}, {})
        q = next(q for q in r['questions'] if q['champ'] == 'prix')
        self.assertEqual(q['motif'], 'contradiction')
        self.assertEqual(sorted(s['valeur'] for s in q['sources']), ['125000.00', '130000.00'])
        self.assertNotIn('prix', r['valeurs'])

    def test_Q2_score_du_modele_ignore_source_inaccessible_retiree(self):
        a = source('1000', score_llm=.999, confiance=1.0, fait_valide=True)
        b = {**source('1200', 'cachee'), 'accessible': False}
        c = {**source('1300', 'S3'), 'source_verifiee': False}
        r = Q.planifier(schema(), {'prix': [a, b, c]}, {})
        q = next(q for q in r['questions'] if q['champ'] == 'prix')
        self.assertEqual(q['motif'], 'a_confirmer')                           # confirmation requise : jamais validé par un score
        self.assertEqual([s['id'] for s in q['sources']], ['S1'])
        self.assertNotIn('score_llm', json.dumps(r))

    def test_Q2_zero_false_et_condition_non_applicable(self):
        reponses = {'prix': {'statut': 'confirme', 'valeur': '0', 'sources': {}}, 'echelonne': {'statut': 'confirme', 'valeur': False, 'sources': {}}}
        r = Q.planifier(schema(), {}, reponses)
        self.assertEqual(r['valeurs']['prix'], '0.00')
        self.assertIs(r['valeurs']['echelonne'], False)
        self.assertIn('paiement', r['sections_pretes'])
        self.assertIn('date', r['inactifs'])

    def test_Q3_je_ne_sais_pas_et_plus_tard_restent_des_absences(self):
        s = Q.valider_schema({'champs': CHAMPS + [{'id': 'clause', 'libelle': 'Clause pénale', 'type': 'booleen', 'groupe': 'redaction', 'defaut_redaction': True}]})
        rev, reponses = Q.appliquer_reponse(s, {}, revision=1, revision_attendue=1, champ_id='prix', statut='inconnu')
        rev, reponses = Q.appliquer_reponse(s, reponses, revision=rev, revision_attendue=2, champ_id='clause', statut='plus_tard')
        r = Q.planifier(s, {'prix': [source('900', fait_valide=True)]}, reponses)
        self.assertEqual(rev, 3)
        self.assertNotIn('prix', r['valeurs'])
        self.assertNotIn('clause', r['valeurs'])                              # le défaut du modèle ne remplace pas « plus tard »
        self.assertEqual({q['champ']: q['statut'] for q in r['questions']}['prix'], 'inconnu')
        self.assertEqual(Q.planifier(s, {}, {})['valeurs']['clause'], True)   # sans réponse : l'option déclarée s'applique, visible
        with self.assertRaisesRegex(ValueError, 'version_en_conflit'):
            Q.appliquer_reponse(s, {}, revision=2, revision_attendue=1, champ_id='prix', statut='confirme', valeur='1000')
        with self.assertRaisesRegex(ValueError, 'champ_inconnu'):
            Q.appliquer_reponse(s, {}, revision=1, revision_attendue=1, champ_id='role_admin', statut='confirme', valeur='oui')

    def test_Q4_source_modifiee_reexamen_et_ancienne_reponse_conservee(self):
        reponses = {'prix': {'statut': 'confirme', 'valeur': '1000.00', 'sources': {'S1': 'a'}, 'nature': 'fait_confirme'}}
        r = Q.planifier(schema(), {'prix': [source('1200', version='b')]}, reponses)
        q = next(q for q in r['questions'] if q['champ'] == 'prix')
        self.assertEqual((q['motif'], q['ancienne_reponse'], q['statut']), ('sources_modifiees', '1000.00', 'a_reexaminer'))
        self.assertNotIn('prix', r['valeurs'])
        r = Q.planifier(schema(), {'prix': [source('1000', version='a'), source('1000', 'S9', 'z')]}, reponses)
        self.assertEqual(r['valeurs']['prix'], '1000.00')                      # une source de plus, même valeur : rien à réexaminer


# ------------------------------------------------------------------------------------------------ Q4 : aperçu par sections (sans modèle)
TRAME = ['# CONTRAT FICTIF', 'Entre {{partie}} et le Prestataire.', '## Prix', 'Le prix est de {{prix}} hors taxes.', '[[si echelonne]]', '## Échéancier',
         'Premier paiement le {{date}}.', '[[fin]]', '## Confidentialité', 'Les parties gardent le secret.']


class Apercu(unittest.TestCase):
    def inst(self):
        return {'texte': TRAME, 'langue': 'fr', 'titre': 'Contrat fictif', 'fixes': {}}

    def test_Q4_sections_independantes_avancent_le_reste_a_completer(self):
        s = Q.valider_schema({'champs': CHAMPS[:2] + [{**CHAMPS[2], 'groupe': 'redaction'}, CHAMPS[3]]})
        plan = Q.planifier(s, {}, {'partie': {'statut': 'confirme', 'valeur': 'DURAND', 'sources': {}, 'nature': 'fait_confirme'}})
        a = Q.assembler(self.inst(), s, plan)
        etats = {x['id']: x['etat'] for x in a['sections']}
        self.assertEqual(etats['preambule'], 'redigee')
        self.assertEqual(etats['art-prix'], 'a_completer')
        self.assertEqual(etats['art-confidentialite'], 'redigee')
        self.assertEqual(etats['clause-echelonne'], 'a_completer')             # choix non tranché : l'article attend, sans « sinon » rédigé
        self.assertTrue(a['partiel'])
        self.assertFalse(a['pret_signature'])
        self.assertTrue(a['entete'].startswith('BROUILLON PARTIEL'))
        self.assertIn('[À COMPLÉTER : Prix HT convenu]', a['texte'])
        self.assertIn('Entre DURAND', a['texte'])

    def test_Q4_valeurs_typees_dans_l_acte_et_pret_pour_signature(self):
        s = Q.valider_schema({'champs': [{**CHAMPS[0], 'bloquant_signature': True}, CHAMPS[1], {**CHAMPS[2], 'groupe': 'redaction'}, CHAMPS[3]]})
        reponses = {'partie': {'statut': 'confirme', 'valeur': 'DURAND', 'nature': 'fait_confirme'},
                    'prix': {'statut': 'confirme', 'valeur': '125000', 'nature': 'fait_confirme'},
                    'echelonne': {'statut': 'confirme', 'valeur': True, 'nature': 'choix_avocat'},
                    'date': {'statut': 'confirme', 'valeur': '2027-01-14', 'nature': 'fait_confirme'}}
        a = Q.assembler(self.inst(), s, Q.planifier(s, {}, reponses))
        self.assertIn('125 000,00 €', a['texte'])
        self.assertIn('Premier paiement le 14/01/2027', a['texte'])
        self.assertTrue(a['pret_signature'])
        self.assertEqual(a['entete'], '')
        assistant = {**reponses, 'partie': {**reponses['partie'], 'nature': 'saisie_assistant'}}
        self.assertFalse(Q.assembler(self.inst(), s, Q.planifier(s, {}, assistant))['pret_signature'])   # à confirmer par l'avocat

    def test_Q4_correction_manuelle_preservee_et_comparaison(self):
        s = schema()
        r1 = {'partie': {'statut': 'confirme', 'valeur': 'DURAND', 'nature': 'fait_confirme'}, 'prix': {'statut': 'confirme', 'valeur': '1000', 'nature': 'fait_confirme'}}
        a = Q.assembler(self.inst(), s, Q.planifier(s, {}, r1))
        prix = next(x for x in a['sections'] if x['id'] == 'art-prix')
        corrections = {'art-prix': {'texte': 'Prix ferme et définitif, corrigé par l’avocat.', 'base': Q._sha(prix['calcule'])}}
        b = Q.assembler(self.inst(), s, Q.planifier(s, {}, r1), corrections)
        self.assertEqual(next(x for x in b['sections'] if x['id'] == 'art-prix')['etat'], 'corrigee')
        r2 = {**r1, 'prix': {'statut': 'confirme', 'valeur': '2000', 'nature': 'fait_confirme'}}
        c = Q.assembler(self.inst(), s, Q.planifier(s, {}, r2), corrections)
        sec = next(x for x in c['sections'] if x['id'] == 'art-prix')
        self.assertEqual(sec['etat'], 'corrigee_a_comparer')
        self.assertEqual(sec['texte'], 'Prix ferme et définitif, corrigé par l’avocat.')   # jamais écrasée
        self.assertIn('2 000,00 €', sec['proposition'])
        self.assertFalse(c['pret_signature'])


# ------------------------------------------------------------------------------------------------ service (base de données, routes)
class Base(base.Base):
    def setUp(self):
        super().setUp()
        Q.ensure_schema(self.desk)

    def route(self, route, corps=None, args=None, who=AVOCAT, cle=None):
        raw = json.dumps(corps if corps is None else {**corps, **({'cle': cle} if cle else {'cle': 'cle-' + route.replace('/', '-') + '-%06d' % self.compteur()})}).encode()
        env = {'HTTP_X_AXIORHUB_USER': who[0], 'HTTP_X_AXIORHUB_ROLE': who[1], 'HTTP_ORIGIN': 'https://cabinet.example.test', 'HTTP_X_CSRF_TOKEN': 'jeton',
               'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw), 'REQUEST_METHOD': 'POST' if corps is not None else 'GET'}
        out = Q.api(env, self.desk, {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}, '/p', Q.PREFIXE + route, args or {}, env['REQUEST_METHOD'])
        return out['status'], json.loads(out['body']), dict(out['headers'])

    _n = 0

    def compteur(self):
        Base._n += 1
        return Base._n

    def ouvrir(self, who=AVOCAT, objets=()):
        st, v, _ = self.route('creer', {'modele': MODELE, 'dossier': MID, 'objets': list(objets)}, who=who)
        self.assertEqual(st, '200 OK', v)
        return v['id']

    def vue(self, qid, who=AVOCAT):
        st, v, h = self.route('vue', args={'id': qid}, who=who)
        self.assertEqual(st, '200 OK', v)
        self.assertEqual(h['Cache-Control'], 'private, no-store')
        return v


class Service(Base):
    def test_Q3_enregistrer_et_continuer_revision_auteur_version(self):
        qid = self.ouvrir()
        v = self.vue(qid)
        self.assertEqual(v['revision'], 1)
        self.assertEqual(v['modele_version'], 1)
        st, out, _ = self.route('repondre', {'id': qid, 'revision': 1, 'reponses': {'prestataire': 'ZORVAL SAS', 'client': {'statut': 'confirme', 'valeur': 'KERVILAC SARL'},
                                                                                    'plafond': {'statut': 'inconnu'}, 'tribunal': {'statut': 'plus_tard'}}, 'position': 'conditions'})
        self.assertEqual((st, out['revision']), ('200 OK', 2), out)
        v = self.vue(qid)
        self.assertEqual(v['reponses']['prestataire']['nature'], 'fait_confirme')
        self.assertEqual(v['reponses']['prestataire']['auteur'], AVOCAT[0])
        self.assertEqual(v['reponses']['plafond']['statut'], 'inconnu')
        self.assertEqual(v['groupe_ouvert'], 'conditions')                     # position du formulaire retrouvée
        self.assertIn('Entre :', v['apercu']['texte'].replace('ENTRE :', 'Entre :'))
        journal = self.desk.db.execute('SELECT * FROM questionnaires56108_journal WHERE questionnaire=?', (qid,)).fetchall()
        self.assertEqual({r['champ'] for r in journal}, {'prestataire', 'client', 'plafond', 'tribunal'})
        self.assertTrue(all(r['version_questionnaire'] and r['auteur'] == AVOCAT[0] for r in journal))
        sec = {s['id']: s['etat'] for s in v['apercu']['sections']}
        self.assertEqual(sec['preambule'], 'redigee')
        self.assertEqual(sec['art-responsabilite'], 'a_completer')            # « je ne sais pas » : réserve, pas de valeur inventée
        self.assertIn('[À COMPLÉTER : Plafond de responsabilité]', v['apercu']['texte'])

    def test_Q3_revision_perimee_409_et_rien_d_ecrase(self):
        qid = self.ouvrir()
        self.assertEqual(self.route('repondre', {'id': qid, 'revision': 1, 'reponses': {'client': 'KERVILAC SARL'}})[0], '200 OK')
        st, out, _ = self.route('repondre', {'id': qid, 'revision': 1, 'reponses': {'client': 'AUTRE SARL'}})   # second onglet resté à la révision 1
        self.assertEqual(st, '409 Conflict')
        self.assertIn('saisie est conservée', out['message'])
        self.assertEqual(self.vue(qid)['reponses']['client']['valeur'], 'KERVILAC SARL')

    def test_Q3_mutation_sans_controle_atomique_la_reponse_perimee_ecraserait(self):
        qid = self.ouvrir()
        q = Q._ligne(self.desk, qid)
        rep, journal = Q.preparer_reponses(self.desk, q, {'client': 'KERVILAC SARL'}, *AVOCAT)
        Q.ecrire_reponses(self.desk, q, 1, rep, journal, *AVOCAT)
        self.desk.db.commit()
        rep2, journal2 = Q.preparer_reponses(self.desk, q, {'client': 'AUTRE SARL'}, *AVOCAT)
        with self.assertRaisesRegex(Stop, 'version_en_conflit'):
            Q.ecrire_reponses(self.desk, q, 1, rep2, journal2, *AVOCAT)
        with muter(Q, "WHERE id=? AND revision=?' % champs", "WHERE id=? AND ?>0' % champs") as copie:
            copie.ecrire_reponses(self.desk, q, 1, rep2, journal2, *AVOCAT)
            self.desk.db.commit()
        self.assertEqual(json.loads(Q._ligne(self.desk, qid)['reponses'])['client']['valeur'], 'AUTRE SARL')

    def test_Q3_cle_idempotente_et_refus_lisibles(self):
        qid = self.ouvrir()
        corps = {'id': qid, 'revision': 1, 'reponses': {'client': 'KERVILAC SARL'}}
        a = self.route('repondre', corps, cle='cle-identique-000001')
        b = self.route('repondre', corps, cle='cle-identique-000001')
        self.assertEqual(a[1], b[1])                                            # même clé, même demande : aucun second effet
        self.assertEqual(self.vue(qid)['revision'], 2)
        self.assertEqual(self.route('repondre', {**corps, 'reponses': {'client': 'X'}}, cle='cle-identique-000001')[0], '409 Conflict')
        st, out, _ = self.route('repondre', {'id': qid, 'revision': 2, 'reponses': {'role_admin': 'oui'}})
        self.assertEqual((st, out['error']), ('400 Bad Request', 'champ_inconnu'))
        st, out, _ = self.route('repondre', {'id': qid, 'revision': 2, 'reponses': {'date_acte': '31/12/2026'}})
        self.assertEqual(out['error'], 'valeur_invalide')
        self.assertIn('AAAA-MM-JJ', out['message'])
        st, out, _ = self.route('repondre', {'id': qid, 'revision': 2, 'reponses': {'client': {'statut': 'confirme', 'valeur': 'KERVILAC', 'source': 'S9'}}})
        self.assertEqual(out['error'], 'source_inaccessible')                  # une source non proposée pour ce champ n'est pas « retenue »
        self.assertEqual(self.vue(qid)['revision'], 2)

    def test_Q3_404_neutre_autre_compte_dossier_reserve_et_403_role(self):
        qid = self.ouvrir()
        self.assertEqual(self.route('vue', args={'id': qid}, who=COLLEGUE)[0], '404 Not Found')
        self.assertEqual(self.route('vue', args={'id': 'f' * 32})[0], '404 Not Found')
        self.assertEqual(self.route('repondre', {'id': qid, 'revision': 1, 'reponses': {'client': 'X'}}, who=COLLEGUE)[0], '404 Not Found')
        with patch.object(Q, '_masques', return_value={MID}):
            st, out, _ = self.route('vue', args={'id': qid})
        self.assertEqual((st, out['error']), ('404 Not Found', 'introuvable'))   # même réponse que pour un questionnaire absent
        with patch.object(Q, '_masques', side_effect=Stop('controle_indisponible')):
            self.assertEqual(self.route('vue', args={'id': qid})[0], '503 Service Unavailable')
        self.assertEqual(self.route('publier', {'modele': MODELE, 'questionnaire': SCHEMA, 'commentaire': 'Essai'}, who=ASSISTANT)[0], '403 Forbidden')
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/m56108/questionnaire/publier'))
        self.assertTrue(standalone_auth.allowed('assistant', 'POST', '/api440/m56108/questionnaire/repondre'))
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/m56108/questionnaire/inconnue'))

    def test_Q3_saisie_d_un_assistant_distincte_d_une_confirmation(self):
        qid = self.ouvrir(who=ASSISTANT)
        self.route('repondre', {'id': qid, 'revision': 1, 'reponses': {'client': 'KERVILAC SARL'}}, who=ASSISTANT)
        v = self.vue(qid, who=ASSISTANT)
        self.assertEqual(v['natures']['client'], 'saisie_assistant')
        self.assertIn('client', v['manque_signature'])

    def test_R05_lecture_sans_ecriture(self):
        qid = self.ouvrir()
        avant = self.desk.db.total_changes
        self.vue(qid)
        membres5650.set_author(AVOCAT[0])
        membres5650.set_viewer_role(AVOCAT[1])
        html = lib.page_html(self.desk, '/p', {'questionnaire': qid})
        self.assertEqual(self.desk.db.total_changes, avant)
        self.assertIn('Informations pour préparer l’acte', html)

    def test_Q6_ia_en_pause_formulaire_apercu_et_brouillon(self):
        from agent import arret5643
        qid = self.ouvrir()
        arret5643.pause_all(self.desk, True, AVOCAT[0])
        self.addCleanup(arret5643.pause_all, self.desk, False, AVOCAT[0])
        self.assertEqual(self.route('repondre', {'id': qid, 'revision': 1, 'reponses': {'client': 'KERVILAC SARL', 'prestataire': 'ZORVAL SAS'}})[0], '200 OK')
        v = self.vue(qid)
        self.assertIn('KERVILAC SARL', v['apercu']['texte'])
        st, out, _ = self.route('telecharger', {'id': qid, 'revision': 2})
        self.assertEqual(st, '200 OK', out)
        jeton = out['url'].rsplit('=', 1)[1]
        donnees = self.desk.db.execute('SELECT nom, donnees FROM bibliotheque5639_fichiers WHERE jeton=?', (jeton,)).fetchone()
        self.assertIn('brouillon partiel', donnees['nom'])
        from agent.bibliotheque_word5640 import docx_text
        self.assertIn('BROUILLON PARTIEL', docx_text(donnees['donnees']))
        st, out, _ = self.route('rediger', {'id': qid, 'revision': 2})
        self.assertEqual(st, '200 OK', out)
        ligne = self.desk.db.execute('SELECT etat FROM assistances5689 WHERE mission_id=?', (out['mission'],)).fetchone()
        self.assertEqual(ligne['etat'], 'suspendu')                            # reprise différée, état visible ; la saisie reste
        self.assertEqual(self.vue(qid)['reponses']['client']['valeur'], 'KERVILAC SARL')

    def test_Q1_fige_a_la_validation_et_nouvelle_version_du_questionnaire(self):
        ligne = modeles5684.current(self.desk, MODELE)
        self.assertEqual(ligne.get('questionnaire'), '')
        Q.figer(self.desk, ligne)
        self.desk.db.commit()
        fige = modeles5684.current(self.desk, MODELE)
        self.assertEqual(json.loads(fige['questionnaire'])['origine'], 'trame')
        self.assertEqual(fige['questionnaire_sha'], Q.empreinte(json.loads(fige['questionnaire'])))
        qid = self.ouvrir()                                                     # instantané de la version 1
        propre = {'champs': [{'id': 'prix', 'libelle': 'Prix HT', 'type': 'montant', 'groupe': 'operation', 'bloquant_signature': True, 'confirmation_requise': True}]}
        st, out, _ = self.route('publier', {'modele': MODELE, 'questionnaire': propre, 'commentaire': 'Prix chiffré'})
        self.assertEqual((st, out['version']), ('200 OK', 2), out)
        v2 = modeles5684.current(self.desk, MODELE)
        self.assertEqual((v2['state'], v2['text'], v2['validated_by']), ('draft', fige['text'], ''))
        self.assertNotEqual(v2['questionnaire_sha'], fige['questionnaire_sha'])
        s2 = Q.questionnaire_de(lib.template(self.desk, MODELE), v2)
        self.assertEqual({c['id']: c['type'] for c in s2['champs']}['prix'], 'montant')
        self.assertIn('prestataire', {c['id'] for c in s2['champs']})          # variables non couvertes : complétées depuis la trame
        self.assertEqual(self.vue(qid)['modele_version'], 1)                   # le questionnaire ouvert garde son instantané
        self.assertEqual(self.route('publier', {'modele': MODELE, 'questionnaire': propre, 'commentaire': 'Prix chiffré'})[1]['version'], 2)   # rien de nouveau


# ------------------------------------------------------------------------------------------------ Q5 : contrôleur et mission liée
PRIX_DOC = DOSSIER + '/Proposition commerciale.pdf'


class FauxActe(base.Faux):
    """Fournisseur simulé : propositions sourcées (dont une inventée et un score), puis rédaction d'une section en recopiant la trame."""

    def __init__(self, petit=True, propositions=None, demande=None, alterer=False):
        cfg = {'provider_type': 'ollama', 'purpose': 'assistant', 'model': 'petit-modele-fictif' if petit else 'modele-fictif'}
        if petit:
            cfg['num_ctx'] = 8192
        super().__init__(cfg=cfg)
        self.propositions, self.demande, self.alterer = propositions, demande, alterer
        self.sections, self.etapes = [], []

    def complete(self, messages, temperature=None, max_tokens=0, json_schema=None):
        charge = json.loads(messages[1]['content'])
        self.systemes.append(messages[0]['content'])
        self.charges.append(charge)
        if messages[0]['content'] == Q.SYSTEME_PROPOSITIONS:
            self.etapes.append('propositions')
            return json.dumps({'propositions': self.propositions or []}, ensure_ascii=False)
        self.etapes.append('section')
        self.sections.append(charge['section']['titre'])
        texte = charge['section']['trame']
        if self.alterer:
            texte = texte.replace('2016', '2018')
        out = {'texte': texte}
        if self.demande:
            out['ask_user'] = self.demande
        return json.dumps(out, ensure_ascii=False)


class Controleur(Base):
    def lancer(self, modele, objets=None):
        self.doc_indexe(PRIX_DOC, 'Proposition fictive : le Prestataire ZORVAL SAS facture un forfait mensuel de 4 000 euros HT. Client : KERVILAC SARL.')
        qid = self.ouvrir(objets=objets if objets is not None else [{'id': 'doc:' + PRIX_DOC, 'version_id': ''}])
        st, out, _ = self.route('rediger', {'id': qid, 'revision': 1})
        self.assertEqual(st, '200 OK', out)
        with patch('agent.chaine5689.modele_pour', return_value=modele):
            self.assertTrue(self.desk.work_once())
        m = missions567.get(self.desk, out['mission'], missions567.owner_for(AVOCAT[0]))
        return qid, out['mission'], m['resultat5689']

    def relancer(self, modele):
        with patch('agent.chaine5689.modele_pour', return_value=modele):
            self.assertTrue(self.desk.work_once())

    def test_Q5_petit_modele_le_serveur_pose_les_questions_sans_outil(self):
        modele = FauxActe(petit=True, propositions=[
            {'champ': 'prestataire', 'valeur': 'ZORVAL SAS', 'source_id': 'S1', 'extrait': 'le Prestataire ZORVAL SAS facture', 'score': 0.99},
            {'champ': 'client', 'valeur': 'BERNARD SA', 'source_id': 'S1', 'extrait': 'Client : BERNARD SA.'},               # extrait absent de la source
            {'champ': 'tribunal', 'valeur': 'Valbrune', 'source_id': 'S7', 'extrait': 'Valbrune'}])                        # source non fournie
        qid, mission, r = self.lancer(modele)
        self.assertEqual(r['etat'], 'partiel')
        self.assertIn('prestataire', r['champs_manquants'])                      # trouvée : à confirmer (partie), jamais validée par un score
        self.assertEqual(r['types_champs']['prestataire']['type'], 'partie')
        self.assertTrue(r['question_regroupee'].startswith('Pour préparer l’acte'))
        self.assertTrue(any('2 proposition(s) du modèle écartée(s)' in x for x in r['limites']), r['limites'])
        self.assertTrue(r['questionnaire']['url'].endswith(qid))
        self.assertFalse(any('outil_ask_user' in c for c in modele.charges))   # rien d'outil n'est demandé à un petit modèle
        self.assertTrue(all('ask_user' not in s for s in modele.systemes))
        lot = self.desk.db.execute("SELECT * FROM questionnaires56108_questions WHERE questionnaire=? AND origine='controleur'", (qid,)).fetchall()
        self.assertEqual(len(lot), 1)
        v = self.vue(qid)
        q = next(x for x in v['questions'] if x['champ'] == 'prestataire')
        self.assertEqual((q['motif'], q['sources'][0]['id'], q['sources'][0]['lien'][:23]), ('a_confirmer', 'S1', '/p/dossier/fichier?matt'))
        self.assertNotIn('client', {x['champ'] for x in v['questions'] if x['sources']})
        # les sections indépendantes sont rédigées par le modèle ; les autres restent « À compléter » et ne lui sont pas confiées
        pretes = {s['titre'] for s in v['apercu']['sections'] if s['etat'] == 'redigee' and s['titre']}
        self.assertTrue(pretes)
        self.assertEqual(set(modele.sections) - {''}, pretes)
        etat = self.desk.db.execute('SELECT status FROM jobs WHERE id=(SELECT job_id FROM missions_v567 WHERE id=?)', (mission,)).fetchone()['status']
        self.assertEqual(etat, 'done')                                          # attente d'information : aucun travailleur bloqué
        self.assertFalse(self.desk.work_once())                                 # et aucune relance seule

    def test_Q5_reponse_par_la_mission_revision_idempotence_reprise_ciblee(self):
        modele = FauxActe(petit=True)
        qid, mission, r = self.lancer(modele)
        appels = len(modele.sections)
        corps = {'id': mission, 'revision': r['revision'], 'champs': {'plafond': 'le montant payé sur douze mois', 'tribunal': ''}, 'cle': 'cle-mission-rep-0001'}
        with self.assertRaisesRegex(Stop, 'version_en_conflit'):
            ia.repondre(self.desk, {**corps, 'revision': r['revision'] - 1, 'cle': 'cle-mission-rep-0000'}, *AVOCAT)
        ia.repondre(self.desk, corps, *AVOCAT)
        ia.repondre(self.desk, corps, *AVOCAT)                                   # même clé : la réponse n'est pas écrite deux fois
        self.assertEqual(self.vue(qid)['revision'], 2)
        self.relancer(modele)
        self.assertEqual(modele.sections[appels:], ['Article 6 — Responsabilité'])   # seule la section concernée repart
        self.assertEqual(modele.etapes.count('propositions'), 1)                 # mêmes sources : pas de nouvelle étape de propositions
        with self.assertRaises(Qualified):
            ia.repondre(self.desk, {'id': mission, 'revision': missions567.get(self.desk, mission, missions567.owner_for(AVOCAT[0]))['resultat5689']['revision'],
                                    'champs': {'date_acte': '31/12/2026'}, 'cle': 'cle-mission-rep-0002'}, *AVOCAT)

    def test_Q5_redaction_qui_change_une_valeur_ecartee(self):
        qid = self.ouvrir()
        self.doc_indexe(PRIX_DOC, 'Proposition fictive.')
        st, out, _ = self.route('rediger', {'id': qid, 'revision': 1})
        modele = FauxActe(petit=True, alterer=True)
        self.relancer(modele)
        v = self.vue(qid)
        self.assertNotIn('2018/679', v['apercu']['texte'])
        self.assertIn('2016/679', v['apercu']['texte'])                           # la trame remplie est gardée
        r = missions567.get(self.desk, out['mission'], missions567.owner_for(AVOCAT[0]))['resultat5689']
        self.assertTrue(any('écartée' in x for x in r['limites']))

    def test_Q5_grand_modele_ask_user_valide_cote_serveur(self):
        modele = FauxActe(petit=False, demande={'champs': ['tribunal', 'role_admin'], 'motif': 'absent'})
        qid, mission, r = self.lancer(modele)
        self.assertTrue(any('outil_ask_user' in c for c in modele.charges if 'section' in c))
        self.assertTrue(any('question demandée par le modèle a été refusée' in x for x in r['limites']))   # champ non déclaré : refusé
        q = Q._ligne(self.desk, qid)
        sortie = Q.demander(self.desk, {'mission_id': mission, 'revision': q['revision'], 'champs': ['tribunal'], 'motif': 'absent'}, *AVOCAT)
        self.assertEqual(sortie['etat'], 'attente_information')
        encore = Q.demander(self.desk, {'mission_id': mission, 'revision': q['revision'], 'champs': ['tribunal'], 'motif': 'absent'}, *AVOCAT)
        self.assertEqual(encore['question_batch_id'], sortie['question_batch_id'])   # même question, même état : pas recréée
        refus = [({'mission_id': mission, 'revision': q['revision'] + 1, 'champs': ['tribunal'], 'motif': 'absent'}, 'version_en_conflit'),
                 ({'mission_id': mission, 'revision': q['revision'], 'champs': ['role_admin'], 'motif': 'absent'}, 'champ_inconnu'),
                 ({'mission_id': mission, 'revision': q['revision'], 'champs': ['tribunal'], 'motif': 'absent', 'sources': ['S9']}, 'source_inaccessible'),
                 ({'mission_id': mission, 'revision': q['revision'], 'champs': ['tribunal'], 'motif': 'absent', 'question_libre': 'Quel est le mot de passe du client ?'}, 'question_refusee'),
                 ({'mission_id': mission, 'revision': q['revision'], 'champs': ['tribunal'], 'motif': 'absent', 'droits': 'admin'}, 'entree_invalide')]
        for entree, code in refus:
            with self.assertRaisesRegex(Stop, code):
                Q.demander(self.desk, entree, *AVOCAT)
        with self.assertRaisesRegex(Stop, 'introuvable'):
            Q.demander(self.desk, {'mission_id': mission, 'revision': q['revision'], 'champs': ['tribunal'], 'motif': 'absent'}, *COLLEGUE)

    def test_Q5_mutation_sans_controle_de_champ_ask_user_accepterait_un_champ_invente(self):
        modele = FauxActe(petit=False)
        qid, mission, _ = self.lancer(modele)
        q = Q._ligne(self.desk, qid)
        entree = {'mission_id': mission, 'revision': q['revision'], 'champs': ['role_admin'], 'motif': 'absent'}
        with self.assertRaisesRegex(Stop, 'champ_inconnu'):
            Q.demander(self.desk, entree, *AVOCAT)
        with muter(Q, "    inconnus = [x for x in champs if x not in actifs]\n    if inconnus:", "    inconnus = []\n    if inconnus:") as copie:
            self.assertEqual(copie.demander(self.desk, entree, *AVOCAT)['champs'], ['role_admin'])

    def test_Q2_mutation_sans_verification_d_extrait_une_valeur_inventee_passerait(self):
        sources = [{'id': 'S1', 'excerpt': 'Client : KERVILAC SARL.', 'version': 'v1', 'titre': 'Note'}]
        data = {'propositions': [{'champ': 'client', 'valeur': 'BERNARD SA', 'source_id': 'S1', 'extrait': 'Client : BERNARD SA.'}]}
        s = Q.deriver(lib.BUILTIN[MODELE])
        self.assertEqual(Q.verifier_propositions(s, data, sources, {'client'}), ({}, 1))
        with muter(Q, "        if _plat(extrait) not in _plat(source.get('excerpt')):", "        if False:") as copie:
            with patch.object(copie, '_coherent', return_value=True):
                self.assertEqual(len(copie.verifier_propositions(s, data, sources, {'client'})[0]), 1)

    def test_Q4_source_modifiee_dans_la_mission_reexamen_cible(self):
        modele = FauxActe(petit=True, propositions=[{'champ': 'client', 'valeur': 'KERVILAC SARL', 'source_id': 'S1', 'extrait': 'Client : KERVILAC SARL.'}])
        qid, mission, r = self.lancer(modele)
        v = self.vue(qid)
        self.route('repondre', {'id': qid, 'revision': v['revision'], 'reponses': {'client': {'statut': 'confirme', 'valeur': 'KERVILAC SARL', 'source': 'S1'}}})
        self.assertEqual(self.vue(qid)['natures']['client'], 'fait_confirme')
        self.doc_indexe(PRIX_DOC, 'Proposition modifiée : Client : ORLANDEC SAS.')   # la pièce change
        modele.propositions = [{'champ': 'client', 'valeur': 'ORLANDEC SAS', 'source_id': 'S1', 'extrait': 'Client : ORLANDEC SAS.'}]
        st, out, _ = self.route('rediger', {'id': qid, 'revision': self.vue(qid)['revision']})
        self.relancer(modele)
        v = self.vue(qid)
        q = next(x for x in v['questions'] if x['champ'] == 'client')
        self.assertEqual((q['motif'], q['ancienne_reponse']), ('sources_modifiees', 'KERVILAC SARL'))
        self.assertEqual(v['reponses']['client']['valeur'], 'KERVILAC SARL')   # l'ancienne réponse est conservée, rien n'est substitué

    def test_Q5_dossier_reserve_apres_coup_rien_ne_part(self):
        modele = FauxActe(petit=True)
        self.doc_indexe(PRIX_DOC, 'Proposition fictive.')
        qid = self.ouvrir(objets=[{'id': 'doc:' + PRIX_DOC, 'version_id': ''}])
        st, out, _ = self.route('rediger', {'id': qid, 'revision': 1})
        with patch.object(Q, '_masques', return_value={MID}):
            self.relancer(modele)
        self.assertEqual(modele.charges, [])
        ligne = self.desk.db.execute('SELECT etat FROM assistances5689 WHERE mission_id=?', (out['mission'],)).fetchone()
        self.assertEqual(ligne['etat'], 'refuse')


class Mutations(Base):
    """Chaque garde-fou retiré sur une copie en mémoire : le test correspondant échouerait."""

    def test_Q3_mutation_sans_controle_du_proprietaire_un_collegue_lirait(self):
        qid = self.ouvrir()
        with self.assertRaisesRegex(Stop, 'introuvable'):
            Q.autorise(self.desk, qid, *COLLEGUE)
        with muter(Q, "(ligne['owner'] != str(owner) and role != 'administrateur')", "False") as copie:
            self.assertEqual(copie.autorise(self.desk, qid, *COLLEGUE)['id'], qid)

    def test_Q1_mutation_sans_reserve_avocat_un_assistant_publierait(self):
        self.assertFalse(Q.allowed('assistant', 'POST', Q.PREFIXE + 'publier'))
        with muter(Q, "return role in (('administrateur', 'avocat') if route in ROUTES_AVOCAT else ROLES)", "return role in ROLES") as copie:
            self.assertTrue(copie.allowed('assistant', 'POST', Q.PREFIXE + 'publier'))

    def test_Q2_mutation_sans_controle_d_acces_une_source_cachee_serait_proposee(self):
        cachee = {**source('1200', 'cachee'), 'accessible': False}
        self.assertEqual(Q.planifier(schema(), {'prix': [cachee]}, {})['questions'][1]['sources'], [])
        with muter(Q, "if not (isinstance(c, dict) and c.get('accessible') and", "if not (isinstance(c, dict) and") as copie:
            self.assertEqual(len(copie.planifier(schema(), {'prix': [cachee]}, {})['questions'][1]['sources']), 1)

    def test_Q3_mutation_le_defaut_du_modele_remplacerait_je_ne_sais_pas(self):
        s = Q.valider_schema({'champs': [{'id': 'clause', 'libelle': 'Clause', 'type': 'booleen', 'groupe': 'redaction', 'defaut_redaction': True}]})
        reponses = {'clause': {'statut': 'inconnu'}}
        self.assertNotIn('clause', Q.planifier(s, {}, reponses)['valeurs'])
        with muter(Q, "if reponse is None and 'defaut_redaction' in champ", "if 'defaut_redaction' in champ") as copie:
            self.assertIn('clause', copie.planifier(s, {}, reponses)['valeurs'])

    def test_Q1_schema_de_la_version_en_lecture(self):
        st, v, h = self.route('schema', args={'modele': MODELE})
        self.assertEqual(st, '200 OK')
        self.assertEqual(v['questionnaire']['format'], Q.FORMAT)
        self.assertFalse(v['fige'])
        self.assertEqual(h['Cache-Control'], 'private, no-store')


class Pages(Base):
    def test_Q3_formulaire_groupes_replies_apercu_a_cote(self):
        qid = self.ouvrir()
        membres5650.set_author(AVOCAT[0])
        membres5650.set_viewer_role(AVOCAT[1])
        html = lib.page_html(self.desk, '/p', {'questionnaire': qid})
        self.assertEqual(html.count('<details class="q108-groupe"'), len(self.vue(qid)['groupes']))
        self.assertEqual(html.count(' open><summary>'), 1)                    # un seul groupe ouvert
        for texte in ('Enregistrer et continuer', 'Je ne sais pas', 'Répondre plus tard', 'Télécharger le brouillon (Word)', 'Aperçu', 'Brouillon partiel',
                      'Proposition du modèle (exemple, non retenue)', 'v56108-questionnaire.js'):
            self.assertIn(texte, html)
        self.assertNotIn('<script>', html)
        carte = lib.detail_html(self.desk, '/p', {}, MODELE)
        self.assertIn('Préparer avec le questionnaire', carte)
        self.assertIn('dérivé de la trame', carte)
        self.assertIn('Questionnaire introuvable', lib.page_html(self.desk, '/p', {'questionnaire': 'f' * 32}))


# ------------------------------------------------------------------------------------------------ Q7 : « Dicter la réponse » (demande du 19/11)
RACINE = Path(__file__).resolve().parents[1]
STATIQUES = RACINE / 'agent' / 'static'


class Dictee(Base):
    @unittest.skipUnless(shutil.which('node'), 'Node.js indisponible')
    def test_Q7_date_montant_valides_seulement_et_insertion_au_curseur_dans_node(self):
        r = subprocess.run([shutil.which('node'), str(RACINE / 'tests' / 'dictee56108.cjs'), str(RACINE)], capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertTrue(r.stdout.strip().splitlines()[-1].startswith('dictee56108 : ok'))

    def test_Q7_script_charge_apres_la_dictee_commune_sur_chaque_page(self):
        from agent import ui_helpers, web440
        html = ui_helpers.enhance('<html><head></head><body><main>x</main></body></html>', '/p', 'jeton', True)
        commun, dictee = html.find('/static/v56106-redaction.js'), html.find('/static/v56108-dictee.js')
        self.assertTrue(0 < commun < dictee, 'v56108-dictee.js doit suivre la dictée commune')
        self.assertIn('/static/v56108-dictee.js', web440.STATIC)
        self.assertIn('<script defer src="/p/static/v56108-dictee.js', html)
        self.assertLess(dictee, html.find('/static/v5689-ia.js'))                  # avant la carte des questions regroupées

    def test_Q7_reutilise_la_dictee_commune_sans_autre_envoi(self):
        js = (STATIQUES / 'v56108-dictee.js').read_text(encoding='utf-8')
        self.assertIn('axiorhubDictee.attacher(', js)                              # micro « dictee », transcription locale ou mode Rapide avec avertissement
        self.assertNotRegex(js, r'fetch\(|XMLHttpRequest|sendBeacon|https?://')   # aucun envoi propre : rien n'est enregistré avant « Enregistrer »
        self.assertIn("aria-disabled", js)                                         # inactif mais focusable, info-bulle claire
        commun = (STATIQUES / 'v56106-redaction.js').read_text(encoding='utf-8')
        self.assertIn('rec.continuous = !opts.silence', commun)                   # silence = fin (mode Rapide)
        self.assertIn('surveillerSilence(stream, rec)', commun)                   # silence = fin (vocal local)
        self.assertIn("window.axiorhubDictee108.monter(champ", (STATIQUES / 'v56108-questionnaire.js').read_text(encoding='utf-8'))
        self.assertIn("window.axiorhubDictee108.monter(i,", (STATIQUES / 'v5689-ia.js').read_text(encoding='utf-8'))   # questions regroupées (grille, conseil)

    def test_Q7_textes_traduits_et_transmis_aux_scripts(self):
        from agent.i18n5631 import js_messages
        for langue in ('fr', 'en', 'de', 'es'):
            m = js_messages(('js.',), langue)
            for cle in ('dicter', 'indisponible', 'desactivee', 'refuse', 'insere', 'a_corriger', 'trop_long'):
                self.assertTrue(m.get('js.56108q.' + cle), (langue, cle))
        self.assertEqual(js_messages(('js.',), 'en')['js.56108q.dicter'], 'Dictate the answer')

    def test_Q7_champs_du_formulaire_reperables_par_type(self):
        qid = self.ouvrir()
        membres5650.set_author(AVOCAT[0])
        membres5650.set_viewer_role(AVOCAT[1])
        html = lib.page_html(self.desk, '/p', {'questionnaire': qid})
        for nom, genre in (('prestataire', 'partie'), ('prestations', 'texte'), ('date_acte', 'date')):
            self.assertIn('data-q108-champ="%s" data-type="%s"' % (nom, genre), html)
        self.assertIn('name="date_acte"', html)


if __name__ == '__main__':
    unittest.main()
