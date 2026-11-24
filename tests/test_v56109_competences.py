"""5.6.109, lot K, F06 : compétences utiles par étape et questionnaire des informations manquantes.

- Sélection native : une demande d'analyse ne charge pas une méthode de création ; Assistant juridique reste par défaut et prioritaire ;
  au plus deux méthodes ; compétences personnelles, choix manuel et désactivations conservés (installation et mise à jour).
- Écran Compétences existant : une méthode nouvelle est présentée désactivée, avec ce qu'elle fait ; l'activation est un geste explicite.
- Questionnaire : seule la valeur manquante est demandée ; « Je ne sais pas » ne bloque pas les sections indépendantes ; conditions
  déclaratives (égalité, appartenance, présence, conjonction bornée), jamais de code ; réponse confirmée réutilisée dans un autre
  questionnaire du même dossier sans être redemandée ; correction de l'avocat durable (réexamen, jamais d'écrasement).
- Outils des imports : rapprochés des services natifs, jamais exécutés.
Données fictives, aucun modèle ni réseau. Ces tests vérifient le circuit, pas la qualité juridique des méthodes."""
import io
import json
import unittest
from unittest.mock import patch

import test_v530 as fixtures
from test_v5689_ia import AVOCAT, MID, muter
from agent import competences5632 as c32, competences5643 as c43, competences5683 as c83, competences56109 as c109
from agent import questionnaires56108 as Q

AJ = 'builtin.assistant_juridique'
METIER = 'builtin.metier109.'


class Base(fixtures.Base):
    def setUp(self):
        super().setUp()
        c32.ensure_builtin(self.desk)
        self.desk.setting('competences5632:auto', True)

    def activer(self, *noms):
        for n in noms:
            c32.set_enabled(self.desk, 'cabinet', METIER + n, True)

    def ids(self, texte='', kind='assistant_answer', phase='analyse', **kw):
        return [s['id'] for s in c43.select(self.desk, texte, phase, kind=kind, **kw)]

    def etat(self, sid):
        return self.desk.db.execute('SELECT enabled FROM skill_state5632 WHERE id=?', (sid,)).fetchone()[0]


# ------------------------------------------------------------------------------------------------ sélection par étape
class Selection(Base):
    def setUp(self):
        super().setUp()
        for r in c32.listing(self.desk):
            if r['id'].startswith(METIER):
                c32.set_enabled(self.desk, 'cabinet', r['id'], True)   # pire cas : toutes les méthodes nouvelles activées

    def test_F06_analyse_d_une_assignation_recue_ne_charge_pas_la_methode_de_creation(self):
        for kind in ('analyser_dossier5624', 'assistant_answer'):
            for texte in ('Analyser l’assignation reçue', 'Résume cette assignation et ses demandes',
                          'Quels sont les moyens de cette requête prud’hommes ?'):
                ids = self.ids(texte, kind=kind)
                self.assertEqual(ids[0], AJ, texte)
                self.assertFalse(any(x in ids for x in c109.CREATION), (texte, ids))
        # la même méthode reste utile quand l'avocat demande de préparer l'acte
        self.assertEqual(self.ids('Préparer un projet d’assignation en paiement', kind='docrequest520', phase='redaction_document')[-1],
                         METIER + 'assignation_generale')
        self.assertIn(METIER + 'assignation_jex', self.ids('Rédiger une assignation JEX', kind='assistant_answer', phase='analyse'))

    def test_F06_garde_fou_creation_retire_la_methode_reviendrait(self):
        with muter(c109, '        if not any(\' \' + v + \' \' in hay for v in VERBES_CREATION):\n            return False\n', '') as copie, \
                patch.dict('sys.modules', {'agent.competences56109': copie}):
            ids = self.ids('Analyser l’assignation reçue', kind='assistant_answer')
        self.assertTrue(any(x in ids for x in c109.CREATION))                    # sans le contrôle, la méthode de création serait chargée

    def test_F06_assistant_juridique_par_defaut_premier_et_deux_au_plus(self):
        for kind, phase, texte in (('prepare_legal_opinion', 'redaction_document', 'Quelle issue pour ce litige ?'),
                                   ('docrequest520', 'redaction_document', 'Rédiger une assignation JEX'),
                                   ('analyser_dossier5624', 'analyse', 'Analyser les conclusions adverses'),
                                   ('assistant_answer', 'analyse', 'Stratégie contentieuse et simulation de décision')):
            ids = self.ids(texte, kind=kind, phase=phase)
            self.assertEqual(ids[0], AJ, (kind, ids))
            self.assertLessEqual(len(ids), c32.MAX_SELECTED)
            self.assertLessEqual(sum(1 for x in ids if x.startswith(METIER)), 1)

    def test_F06_garde_fou_assistant_juridique_retire_le_test_echouerait(self):
        with muter(c43, '        general = general_skill(desk, rows) if kind in GENERAL_KINDS else None', '        general = None') as copie:
            ids = [s['id'] for s in copie.select(self.desk, 'Analyser les conclusions adverses', 'analyse', kind='analyser_dossier5624')]
        self.assertNotEqual(ids[:1], [AJ])                                     # sans la priorité, la spécialisation passerait seule

    def test_F06_choix_manuel_et_mode_automatique_coupe_respectes(self):
        self.assertEqual(self.ids('conclusions adverses', manual=[METIER + 'conclusions_adverses']), [METIER + 'conclusions_adverses'])
        self.assertEqual(self.ids('conclusions adverses', auto=False), [])

    def test_F06_surveillance_et_tri_non_alourdis(self):
        for kind in ('orchestrate', 'proactive34_cycle', 'run', 'live_mail430'):
            self.assertFalse(any(x.startswith(METIER) for x in self.ids('conclusions adverses assignation', kind=kind, phase='decision')))


# ------------------------------------------------------------------------------------------------ activation explicite et préservation
class Activation(Base):
    def test_F06_methodes_nouvelles_proposees_desactivees(self):
        rows = {r['id']: r for r in c32.listing(self.desk)}
        # méthodes du lot K seulement : les parcours ``parcours109-*`` (autres lots) sont signalés au compte rendu
        nouvelles = [r for sid, r in rows.items() if sid.startswith(METIER)]
        self.assertEqual(len(nouvelles), 13)
        self.assertFalse(any(r['enabled'] for r in nouvelles))
        self.assertTrue(rows[AJ]['enabled'])
        self.assertEqual(self.ids('Analyser les conclusions adverses', kind='analyser_dossier5624'), [AJ])   # rien de nouveau sans activation

    def test_F06_ecran_existant_decrit_la_methode_et_propose_l_activation(self):
        from agent import web5632
        env = {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1]}
        page = web5632.skills_page(self.desk, {'csrf': 'jeton'}, '/p', env, lambda titre, corps, *a, **k: corps)
        carte = page[page.index('Analyse des conclusions adverses'):]
        carte = carte[:carte.index('</article>')]
        self.assertIn('Proposée à l’activation', carte)
        self.assertIn('désactivée', carte)
        self.assertIn(c109.DESCRIPTIONS[METIER + 'conclusions_adverses'][:40], carte)   # ce qu'elle fait, en clair
        self.assertIn('n’accorde aucun droit', carte)
        self.assertIn('name="on" value="1"', carte)                             # bouton d'activation explicite de l'écran existant
        self.assertNotIn('<script>', carte)
        rows = {r['id']: r for r in c32.listing(self.desk)}
        self.assertEqual(set(c109.DESCRIPTIONS), {sid for sid in rows if sid.startswith(METIER)})

    def test_F06_activation_par_la_route_existante_puis_selection(self):
        from agent import web5632
        raw = json.dumps({'id': METIER + 'conclusions_adverses', 'on': '1'}).encode()
        env = {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1], 'HTTP_ORIGIN': 'https://cabinet.example.test', 'HTTP_X_CSRF_TOKEN': 'jeton',
               'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw), 'REQUEST_METHOD': 'POST'}
        out = web5632.api(env, self.desk, {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}, '/p', 'm5632/skills/enable', {}, 'POST')
        self.assertTrue(json.loads(out['body'] if isinstance(out, dict) else out[2])['enabled'])
        self.assertEqual(self.ids('Analyser les conclusions adverses', kind='analyser_dossier5624'), [AJ, METIER + 'conclusions_adverses'])

    def test_F06_desactivation_et_activation_preservees_a_la_reinstallation_et_a_la_mise_a_jour(self):
        c32.set_enabled(self.desk, 'cabinet', AJ, False)                       # méthode fournie activée par défaut, désactivée par l'avocat
        self.activer('simulation_decision')                                    # méthode nouvelle activée explicitement
        c32.ensure_builtin(self.desk)
        vrai = c32._builtin_package

        def mise_a_jour(folder):                                               # nouvelle version publiée des deux paquets
            m, texte = vrai(folder)
            if folder in ('assistant-juridique', 'metier109-simulation-decision'):
                m = {**m, 'version': m['version'] + '.maj9'}
            return m, texte
        with patch.object(c32, '_builtin_package', mise_a_jour):
            c32.ensure_builtin(self.desk)
        self.assertEqual((self.etat(AJ), self.etat(METIER + 'simulation_decision')), (0, 1))
        self.assertTrue(c32._row(self.desk, AJ)['version'].endswith('.maj9'))

    def test_F06_garde_fou_preservation_retire_le_test_echouerait(self):
        c32.set_enabled(self.desk, 'cabinet', AJ, False)
        with muter(c32, "        if not state:\n            desk.db.execute('INSERT INTO skill_state5632 VALUES",
                   "        if True:\n            desk.db.execute('INSERT OR REPLACE INTO skill_state5632 VALUES") as copie:
            copie.ensure_builtin(self.desk)
        self.assertEqual(self.etat(AJ), 1)                                     # sans la préservation, la désactivation serait perdue

    def test_F06_competence_personnelle_et_generale_du_cabinet_conservees(self):
        perso = c32.create(self.desk, AVOCAT[0], {'nom': 'Méthode DURAND fictive', 'objectif': 'Relire les baux fictifs', 'situations': 'bail fictif'})
        c32.set_enabled(self.desk, AVOCAT[0], perso['id'], True)
        self.desk.setting(c43.GENERAL_SETTING, perso['id'])                    # le cabinet a choisi sa propre méthode générale
        c32.ensure_builtin(self.desk)
        self.assertTrue(any(r['id'] == perso['id'] and r['enabled'] for r in c32.listing(self.desk, AVOCAT[0])))
        ids = [s['id'] for s in c43.select(self.desk, 'Analyser les pièces', 'analyse', AVOCAT[0], kind='analyser_dossier5624')]
        self.assertEqual(ids[0], perso['id'])


# ------------------------------------------------------------------------------------------------ outils des imports
class Outils(unittest.TestCase):
    def test_F06_outils_importes_rapproches_des_services_natifs_jamais_executes(self):
        out = {d['path']: d for d in c83.script_decisions(['extract_references.py', 'format_citation.py', 'verify_links.py', 'hal_search.py',
                                                             'generate_bibliography.py', 'llm_council.py', 'frontier_ui.py'])}
        self.assertTrue(all(d['executed'] is False for d in out.values()))
        self.assertEqual(out['extract_references.py']['service'], 'references56108.extraire')
        self.assertEqual(out['format_citation.py']['service'], 'citations5697.normaliser')
        self.assertEqual(out['hal_search.py']['decision'], 'indisponible')
        self.assertIn('HAL', out['hal_search.py']['reason'])
        import importlib
        for stem, (decision, service, _r) in c83.SCRIPT_SERVICES.items():
            if decision != 'service':
                continue
            module, fonction = service.split('.', 1)
            self.assertTrue(callable(getattr(importlib.import_module('agent.' + module), fonction)), stem)   # chaque service cité existe


# ------------------------------------------------------------------------------------------------ questionnaire : valeur manquante seulement
CHAMPS = [{'id': 'client', 'libelle': 'Client', 'type': 'partie', 'groupe': 'parties'},
          {'id': 'prix', 'libelle': 'Prix HT convenu', 'type': 'montant', 'confirmation_requise': True},
          {'id': 'mode', 'libelle': 'Mode de paiement', 'type': 'choix', 'options': ['comptant', 'echelonne', 'mixte']},
          {'id': 'echeances', 'libelle': 'Nombre d’échéances', 'type': 'texte', 'actif_si': {'champ': 'mode', 'dans': ['echelonne', 'mixte']}},
          {'id': 'banque', 'libelle': 'Banque du client', 'type': 'texte', 'actif_si': {'champ': 'client', 'present': True}},
          {'id': 'garantie', 'libelle': 'Garantie demandée', 'type': 'texte',
           'actif_si': {'tous': [{'champ': 'mode', 'egal': 'echelonne'}, {'champ': 'client', 'present': True}]}}]
SCHEMA = {'format': Q.FORMAT, 'champs': CHAMPS, 'dependances': {'parties': ['client'], 'prix': ['prix'], 'paiement': ['mode', 'echeances']}}


def source(valeur, ident='S1', version='a', **kw):
    return dict(valeur=valeur, source_id=ident, source_version=version, accessible=True, source_verifiee=True, **kw)


class Questionnaire(unittest.TestCase):
    def test_F06_seule_la_valeur_manquante_est_demandee_sections_independantes_pretes(self):
        s = Q.valider_schema(SCHEMA)
        plan = Q.planifier(s, {'client': [source('ZORVAL SAS', fait_valide=True)], 'prix': [source('1200')]},
                           {'prix': {'statut': 'confirme', 'valeur': '1200.00', 'sources': {'S1': 'a'}, 'nature': 'fait_confirme'},
                            'mode': {'statut': 'inconnu', 'valeur': None, 'sources': {}, 'nature': ''}})
        self.assertEqual([q['champ'] for q in plan['questions'] if q['motif'] != 'condition'], ['mode', 'banque'])
        self.assertIn('parties', plan['sections_pretes'])
        self.assertIn('prix', plan['sections_pretes'])
        self.assertEqual(plan['sections_bloquees'], {'paiement': ['mode', 'echeances']})   # « Je ne sais pas » ne bloque que sa section
        self.assertEqual(plan['etat'], 'partiel')

    def test_F06_conditions_declaratives_appartenance_presence_conjonction(self):
        s = Q.valider_schema(SCHEMA)
        r = lambda **v: {k: {'statut': 'confirme', 'valeur': x, 'sources': {}, 'nature': 'choix_avocat'} for k, x in v.items()}
        plan = Q.planifier(s, {}, r(mode='comptant'))
        self.assertIn('echeances', plan['inactifs'])
        self.assertIn('banque', plan['attente_condition'])                    # client inconnu pour l'instant : on demande d'abord le client
        plan = Q.planifier(s, {}, r(mode='mixte', client='KERVILAC'))
        self.assertNotIn('echeances', plan['inactifs'] + plan['attente_condition'])
        self.assertIn('garantie', plan['inactifs'])                           # conjonction : « mixte » n'est pas « echelonne »
        plan = Q.planifier(s, {}, r(mode='echelonne', client='KERVILAC'))
        self.assertIn('garantie', [q['champ'] for q in plan['questions']])
        rep = r(mode='echelonne')
        rep['client'] = {'statut': 'inconnu', 'valeur': None, 'sources': {}, 'nature': ''}
        plan = Q.planifier(s, {}, rep)
        self.assertIn('banque', plan['inactifs'])                             # présence : client déclaré inconnu, la question ne s'ouvre pas
        self.assertIn('garantie', plan['inactifs'])

    def test_F06_conditions_hors_contrat_refusees(self):
        def champ(cond):
            return {**SCHEMA, 'champs': CHAMPS[:3] + [{'id': 'x', 'libelle': 'X', 'type': 'texte', 'actif_si': cond}]}
        for cond in ({'champ': 'mode', 'dans': []}, {'champ': 'mode', 'dans': ['autre']}, {'champ': 'mode', 'dans': 'echelonne'},
                     {'champ': 'client', 'present': False}, {'champ': 'client', 'present': True, 'egal': 'x'},
                     {'tous': [{'champ': 'mode', 'egal': 'mixte'}]}, {'tous': [{'tous': [{'champ': 'mode', 'egal': 'mixte'}]}, {'champ': 'client', 'present': True}]},
                     {'tous': [{'champ': 'mode', 'egal': 'mixte'}] * 5}, {'champ': 'mode', 'egal': 'mixte', 'code': 'eval(1)'},
                     {'champ': 'apres', 'present': True}):
            with self.subTest(cond=cond), self.assertRaises(Exception):
                Q.valider_schema(champ(cond))


class Reprise(__import__('test_v56108_questionnaires').Base):
    def champ_partie(self, v):
        return next(c for c in v['champs'] if c['type'] == 'partie')

    def test_F06_reponse_confirmee_reutilisee_dans_l_autre_questionnaire_du_dossier(self):
        a = self.ouvrir()
        va = self.vue(a)
        partie = self.champ_partie(va)
        st, out, _ = self.route('repondre', {'id': a, 'revision': va['revision'], 'reponses': {partie['id']: {'statut': 'confirme', 'valeur': 'ZORVAL SAS'}}})
        self.assertEqual(st, '200 OK', out)
        b = self.ouvrir()
        vb = self.vue(b)
        self.assertEqual(vb['valeurs'].get(partie['id']), 'ZORVAL SAS')          # reprise sans nouvelle question
        self.assertEqual(vb['natures'][partie['id']], 'reprise_confirmee')
        self.assertNotIn(partie['id'], [q['champ'] for q in vb['questions']])
        # un assistant ou « Je ne sais pas » ne sont pas repris comme faits confirmés
        c = self.ouvrir()
        vc = self.vue(c)
        autre = next(x for x in vc['champs'] if x['type'] in ('texte', 'partie') and x['id'] != partie['id'] and x['groupe'] != 'redaction')
        self.route('repondre', {'id': a, 'revision': self.vue(a)['revision'], 'reponses': {autre['id']: {'statut': 'inconnu'}}})
        self.assertNotIn(autre['id'], self.vue(c)['valeurs'])

    def test_F06_correction_de_l_avocat_durable_reexamen_sans_ecrasement(self):
        a, b = self.ouvrir(), self.ouvrir()
        partie = self.champ_partie(self.vue(a))
        self.route('repondre', {'id': a, 'revision': self.vue(a)['revision'], 'reponses': {partie['id']: {'statut': 'confirme', 'valeur': 'ZORVAL SAS'}}})
        vb = self.vue(b)
        source = next(s['id'] for s in vb['questions'][0]['sources']) if vb['questions'] and vb['questions'][0]['sources'] else None
        # l'avocat confirme explicitement dans B, en citant la reprise
        st, out, _ = self.route('repondre', {'id': b, 'revision': vb['revision'], 'reponses': {partie['id']: {'statut': 'confirme', 'valeur': 'ZORVAL SAS', 'source': 'questionnaire:' + a}}})
        self.assertEqual(st, '200 OK', (out, source))
        # correction dans A : B ne l'écrase pas, il remet la valeur en examen et montre l'ancienne réponse
        self.route('repondre', {'id': a, 'revision': self.vue(a)['revision'], 'reponses': {partie['id']: {'statut': 'confirme', 'valeur': 'ZORVAL ET ASSOCIES SAS'}}})
        vb = self.vue(b)
        q = next(x for x in vb['questions'] if x['champ'] == partie['id'])
        self.assertEqual((q['motif'], q['ancienne_reponse']), ('sources_modifiees', 'ZORVAL SAS'))
        self.assertEqual(vb['reponses'][partie['id']]['valeur'], 'ZORVAL SAS')
        journal = self.desk.db.execute('SELECT revision, version_questionnaire FROM questionnaires56108_journal WHERE questionnaire=? ORDER BY revision', (b,)).fetchall()
        self.assertTrue(journal and all(j['version_questionnaire'] for j in journal))   # versions des questions et des réponses conservées

    def test_F06_reprise_bornee_au_dossier_et_au_compte(self):
        a = self.ouvrir()
        partie = self.champ_partie(self.vue(a))
        self.route('repondre', {'id': a, 'revision': self.vue(a)['revision'], 'reponses': {partie['id']: {'statut': 'confirme', 'valeur': 'ZORVAL SAS'}}})
        self.desk.db.execute('UPDATE questionnaires56108 SET owner=? WHERE id=?', ('autre@example.test', a))   # questionnaire d'un autre compte
        self.desk.db.commit()
        self.assertNotIn(partie['id'], self.vue(self.ouvrir())['valeurs'])


if __name__ == '__main__':
    unittest.main()
