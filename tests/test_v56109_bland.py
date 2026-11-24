"""5.6.109, lot B : connecteur Bland (téléphonie par IA), « Mes routines | Mes agents IA », routine de rappel de rendez-vous.

Services réels du produit (``bland56109``, ``connexions5652`` : registre, coffre, opérations à valider, ``routines5652``, ``budget_ledger5689``,
``data_policy5689``, ``web5652``) ; le service Bland est un FAUX serveur MCP local (``faux_bland56109``, 127.0.0.1 seulement) : aucun appel vers
bland.ai, aucun compte ni clé réels. Toute autre connexion réseau fait échouer l'essai. Données fictives (DURAND, ZORVAL, DOS-001,
téléphones au format 01 00 00 00 05).
"""
from datetime import datetime, time as dtime, timedelta, timezone
import io
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from agent import bland56109 as B, connexions5652 as cx, routines5652 as rt
from agent.common import Stop
from agent.desk import Desk
import faux_bland56109 as FB
import test_agent as fx

ROOT = Path(__file__).resolve().parents[1]
PREFIX = '/agent-courriel'
ADMIN = ('cabinet', 'administrateur')
DURAND = ('durand@example.test', 'avocat')
ASSISTANT = ('assist@example.test', 'assistant')
_vrai_connect = socket.socket.connect
_vrai_create = socket.create_connection


def _local_seulement(adresse):
    hote = adresse[0] if isinstance(adresse, tuple) else adresse
    if hote not in ('127.0.0.1', 'localhost'):
        raise AssertionError('aucun appel réseau réel dans ces essais : ' + str(hote))


def _connect(self, adresse):
    _local_seulement(adresse)
    return _vrai_connect(self, adresse)


def _create(adresse, *a, **k):
    _local_seulement(adresse)
    return _vrai_create(adresse, *a, **k)


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fx.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        for cible, nom, fn in ((socket.socket, 'connect', _connect), (socket, 'create_connection', _create)):
            p = patch.object(cible, nom, fn)
            p.start()
            self.addCleanup(p.stop)
        self.faux = FB.FauxBland()
        self.addCleanup(self.faux.arreter)
        p = patch.object(cx, 'HTTP_FACTORY', FB.fabrique(self.faux))
        p.start()
        self.addCleanup(p.stop)
        self.d = Desk(self.f.c)
        self.addCleanup(self.d.db.close)
        self.auth = {'origin': 'https://cabinet.example.test', 'csrf': 'test-csrf', 'prefix': PREFIX}
        self.n = 0

    # ------------------------------------------------------------------ outils d'essai
    def cle(self):
        self.n += 1
        return 'cle-b109-essai-%04d' % self.n

    def api(self, route, data=None, acteur=ADMIN, methode='POST', args=None):
        data = dict(data or {})
        if methode == 'POST' and 'cle' not in data:
            data['cle'] = self.cle()
        raw = json.dumps(data).encode()
        env = {'REQUEST_METHOD': methode, 'HTTP_ORIGIN': self.auth['origin'], 'HTTP_X_CSRF_TOKEN': 'test-csrf', 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw), 'HTTP_X_AXIORHUB_ROLE': acteur[1], 'HTTP_X_AXIORHUB_USER': acteur[0]}
        out = B.api(env, self.d, self.auth, PREFIX, B.PREFIXE + route, args or {}, methode)
        out['json'] = json.loads(out['body'])
        return out

    def brancher(self, activer=True):
        self.assertEqual(self.api('cle', {'api_key': FB.CLE_VALIDE})['status'], '200 OK')
        out = self.api('tester')
        self.assertTrue(out['json']['ok'], out['json'])
        if activer:
            self.assertTrue(self.api('activer', {'on': True})['json']['actif'])

    def outils_appeles(self):
        return [a[1] for a in self.faux.appels_outils()]

    def preparer_appel(self, cle=None, numero='01 00 00 00 05', consigne='Rappeler le cabinet DURAND au sujet du rendez-vous.'):
        return B.preparer(self.d, DURAND[0], DURAND[1], 'create_call', {'phone_number': numero, 'task': consigne}, cle or self.cle(), declencheur='assistant')

    def confirmer(self, f, acteur=DURAND, relu=True, empreinte=None):
        return self.api('demande/confirmer', {'id': f['operation'], 'version': f['version'], 'empreinte': empreinte or f['empreinte'], 'relu': relu},
                        acteur=acteur)

    def total_changes(self):
        return self.d.db.total_changes


# ================================================================================ 1. connexion dans les Paramètres
class Connexion(Base):
    def test_B01_desactive_par_defaut_non_configure(self):
        v = B.vue(self.d)
        self.assertEqual((v['etat'], v['actif']), ('non_configure', False))
        with self.assertRaises(Stop):
            B.activer(self.d, *ADMIN, True)           # jamais activé sans connexion prête
        self.assertFalse(B.actif(self.d))

    def test_B01_carte_dans_les_connexions_existantes_liens_et_avertissement(self):
        html = cx.settings_html(self.d, PREFIX, *ADMIN)
        self.assertIn('Bland — téléphonie par IA', html)
        self.assertIn('href="https://app.bland.ai/"', html)
        self.assertIn('href="https://app.bland.ai/settings/api-keys"', html)
        self.assertIn('les appels et les SMS passent par une IA', html)
        self.assertIn('Non configuré', html)
        self.assertIn('n’est pas livrée dans cette version', html)         # OAuth : dit, pas livré

    def test_B01_cle_au_coffre_jamais_rendue_ni_journalisee_ni_en_base(self):
        out = self.api('cle', {'api_key': FB.CLE_VALIDE})
        self.assertNotIn(FB.CLE_VALIDE, out['body'])
        self.api('tester')
        corps = [self.api('etat', methode='GET')['body'], self.api('outils', methode='GET')['body'], cx.settings_html(self.d, PREFIX, *ADMIN),
                 json.dumps(B.vue(self.d))]
        for c in corps:
            self.assertNotIn(FB.CLE_VALIDE, c)
        # aucune table de la base (journal d'audit, connexions, reçus, demandes : donc aucun export) ne contient la clé
        for (table,) in self.d.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            for row in self.d.db.execute('SELECT * FROM "%s"' % table).fetchall():
                self.assertNotIn(FB.CLE_VALIDE, json.dumps([str(x) for x in tuple(row)]), table)
        coffre = list((Path(self.f.c['state_dir']) / 'vault' / 'connexions5652').rglob('api_key.secret'))
        self.assertEqual(len(coffre), 1)
        self.assertNotIn(FB.CLE_VALIDE.encode(), coffre[0].read_bytes())     # chiffrée au coffre
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM connexions5652 WHERE provider='bland'").fetchone()[0], 1)   # une seule connexion

    def test_B01_tester_initialize_puis_tools_list_nombre_affiche_sans_outil_execute(self):
        self.api('cle', {'api_key': FB.CLE_VALIDE})
        out = self.api('tester')['json']
        self.assertTrue(out['ok'])
        self.assertEqual(out['outils'], len(FB.OUTILS))
        self.assertEqual(out['etat'], 'pret')
        self.assertEqual([a[0] for a in self.faux.appels], ['initialize', 'notifications/initialized', 'tools/list'])
        self.assertTrue(all(a[3] for a in self.faux.appels))                 # en-tête Authorization présent
        self.assertIn('%d outil(s) découvert(s)' % len(FB.OUTILS), cx.settings_html(self.d, PREFIX, *ADMIN))

    def test_B01_etats_cle_refusee_hors_ligne_quota(self):
        self.api('cle', {'api_key': 'org_fictive_mauvaise_cle'})
        self.assertEqual(self.api('tester')['json']['etat'], 'cle_refusee')
        self.api('cle', {'api_key': FB.CLE_VALIDE})
        self.faux.panne = 'coupure'
        self.assertEqual(self.api('tester')['json']['etat'], 'hors_ligne')
        self.faux.panne = 'http_429'
        self.assertEqual(self.api('tester')['json']['etat'], 'hors_ligne')
        self.faux.panne = None
        self.assertEqual(self.api('tester')['json']['etat'], 'pret')

    def test_B01_local_strict_refuse_test_et_activation_sans_appel(self):
        self.api('cle', {'api_key': FB.CLE_VALIDE})
        with patch('agent.selection_sources5697.local_strict', lambda desk: True):
            out = self.api('tester')
            self.assertEqual(out['json']['error'], 'mode_local_strict')
            self.assertIn('Mode local strict', out['json']['message'])
            self.assertIn('Mode local strict', cx.settings_html(self.d, PREFIX, *ADMIN))
        self.assertEqual(self.faux.appels, [])
        self.api('tester')
        with patch('agent.selection_sources5697.local_strict', lambda desk: True):
            self.assertEqual(self.api('activer', {'on': True})['json']['error'], 'mode_local_strict')
        self.assertFalse(B.actif(self.d))

    def test_B01_adaptateur_refuse_aussi_le_local_strict_garde_en_profondeur(self):
        self.brancher()
        with patch('agent.selection_sources5697.local_strict', lambda desk: True):
            with self.assertRaises(Stop) as ctx:
                B._adaptateur(self.d).identity()
        self.assertEqual(str(ctx.exception), 'mode_local_strict')

    def test_B01_retirer_la_cle_desactive_et_annule_les_demandes(self):
        self.brancher()
        f = self.preparer_appel()
        self.api('retirer')
        self.assertFalse(B.actif(self.d))
        self.assertEqual(B.vue(self.d)['etat'], 'non_configure')
        self.assertEqual(self.d.db.execute('SELECT state FROM connexion_ops5652 WHERE id=?', (f['operation'],)).fetchone()[0], 'annulee')

    def test_B01_droits_seul_l_administrateur_regle_la_cle(self):
        self.assertEqual(self.api('cle', {'api_key': FB.CLE_VALIDE}, acteur=DURAND)['status'], '403 Forbidden')
        self.assertEqual(self.api('activer', {'on': True}, acteur=ASSISTANT)['status'], '403 Forbidden')


# ================================================================================ 2. outils découverts et classés
class Classement(Base):
    def test_B02_classement_des_outils(self):
        c = {t['name']: B.classer(t) for t in FB.OUTILS}
        self.assertEqual(c['list_calls']['classe'], 'lecture')
        self.assertEqual(c['get_call_log']['classe'], 'lecture')
        self.assertEqual((c['create_call']['motif'], c['create_call']['engageant']), ('appel', True))
        self.assertEqual(c['send_sms']['motif'], 'sms')
        self.assertEqual(c['buy_credits']['motif'], 'achat')
        self.assertEqual(c['publish_agent']['motif'], 'publication')
        self.assertEqual(c['delete_agent']['motif'], 'suppression')
        self.assertEqual((c['create_agent']['classe'], c['create_agent']['engageant']), ('effet', False))
        # annoncé « lecture seule » mais nom d'effet : jamais une lecture
        self.assertEqual((c['send_batch_campaign']['classe'], c['send_batch_campaign']['engageant']), ('effet', True))
        for nom in ('buy_phone_plan', 'purchase_phone_number', 'transfer_call', 'update_inbound_number', 'create_sip_trunk', 'port_in_number',
                    'create_api_key', 'invite_member'):
            got = B.classer({'name': nom, 'annotations': {'readOnlyHint': True, 'destructiveHint': False}})
            self.assertEqual((got['classe'], got['engageant']), ('effet', True), nom)

    def test_B02_outil_sans_annotation_engageant_contre_exemple_annote(self):
        self.assertEqual(B.classer({'name': 'zz_outil'})['motif'], 'non_annote')
        self.assertTrue(B.classer({'name': 'zz_outil', 'annotations': {}})['engageant'])
        self.assertTrue(B.classer({'name': 'zz_outil', 'annotations': 'illisible'})['engageant'])
        self.assertEqual(B.classer({'name': 'zz_outil', 'annotations': {'readOnlyHint': True}})['classe'], 'lecture')   # contre-exemple

    def test_B02_liste_en_cache_avec_revision(self):
        self.brancher()
        r1 = B.dernier_releve(self.d)['revision']
        self.api('tester')
        self.assertEqual(B.dernier_releve(self.d)['revision'], r1)            # liste inchangée : même révision
        self.faux.outils = self.faux.outils + [FB._outil('list_numbers_v2', 'List', True, False)]
        self.api('tester')
        r2 = B.dernier_releve(self.d)
        self.assertEqual((r2['revision'], r2['nombre']), (r1 + 1, len(FB.OUTILS) + 1))


# ================================================================================ 3. confirmation humaine avant tout effet
class Confirmation(Base):
    def setUp(self):
        super().setUp()
        self.brancher()

    def test_B03_preparer_n_execute_rien(self):
        f = self.preparer_appel()
        self.assertEqual(f['etat'], 'a_valider')
        self.assertEqual(f['apercu']['numeros'], ['01 00 00 00 05'])
        self.assertEqual(self.outils_appeles(), [])

    def test_B03_aucun_effet_sans_confirmation_et_mutation_garde_retiree(self):
        f = self.preparer_appel()
        op = self.d.db.execute('SELECT * FROM connexion_ops5652 WHERE id=?', (f['operation'],)).fetchone()
        with self.assertRaises(Stop) as ctx:                                     # un travail de fond qui appellerait le service commun
            cx.validate(self.d, 'cabinet', 'administrateur', op['id'], op['version'], '')
        self.assertEqual(str(ctx.exception), 'confirmation_bland_requise')
        self.assertEqual(self.outils_appeles(), [])
        self.assertEqual(self.d.db.execute('SELECT state FROM connexion_ops5652 WHERE id=?', (op['id'],)).fetchone()[0], 'a_valider')
        # mutation (en mémoire) : gardes retirées → l'appel part ; ce sont donc elles qui l'empêchent
        with patch.object(B, 'exiger_confirmation', lambda op: None), patch.object(B, '_exiger_execution', lambda desk, params: None):
            cx.validate(self.d, 'cabinet', 'administrateur', op['id'], op['version'], '')
        self.assertEqual(self.outils_appeles(), ['create_call'])

    def test_B03_une_routine_ne_confirme_jamais_et_mutation(self):
        f = self.preparer_appel()
        with self.assertRaises(Stop) as ctx:                                     # hors de la route de l'interface : refus
            B.confirmer(self.d, *ADMIN, f['operation'], f['version'], f['empreinte'], True)
        self.assertEqual(str(ctx.exception), 'confirmation_humaine_requise')
        self.assertEqual(self.outils_appeles(), [])
        with patch.object(B, '_exiger_humain', lambda: None):                    # mutation : garde retirée → l'effet part
            B.confirmer(self.d, *ADMIN, f['operation'], f['version'], f['empreinte'], True)
        self.assertEqual(self.outils_appeles(), ['create_call'])

    def test_B03_confirmation_de_l_avocat_un_seul_appel_arguments_exacts(self):
        f = self.preparer_appel()
        out = self.confirmer(f)
        self.assertEqual(out['status'], '200 OK', out['json'])
        self.assertEqual(out['json']['etat'], 'terminee')
        appels = self.faux.appels_outils()
        self.assertEqual(len(appels), 1)
        self.assertEqual(appels[0][2], {'phone_number': '01 00 00 00 05', 'task': 'Rappeler le cabinet DURAND au sujet du rendez-vous.'})
        self.confirmer(f)                                                          # second clic : aucun nouvel effet
        self.assertEqual(len(self.faux.appels_outils()), 1)

    def test_B03_case_de_relecture_et_empreinte_affichee_obligatoires(self):
        f = self.preparer_appel()
        self.assertEqual(self.confirmer(f, relu=False)['json']['error'], 'relecture_requise')
        out = self.confirmer(f, empreinte='0' * 64)
        self.assertEqual((out['status'], out['json']['error']), ('409 Conflict', 'fiche_modifiee_depuis_affichage'))
        self.assertEqual(self.outils_appeles(), [])

    def test_B03_arguments_changes_nouvelle_confirmation(self):
        cle = self.cle()
        f1 = self.preparer_appel(cle=cle)
        with self.assertRaises(Stop) as ctx:
            self.preparer_appel(cle=cle, numero='01 00 00 00 06')
        self.assertEqual(str(ctx.exception), 'cle_reutilisee')
        self.assertEqual(self.preparer_appel(cle=cle)['operation'], f1['operation'])   # même clé, mêmes arguments : même demande
        f2 = self.preparer_appel(numero='01 00 00 00 06')
        self.assertNotEqual(f2['operation'], f1['operation'])
        self.assertEqual(f2['etat'], 'a_valider')
        self.confirmer(f1)
        self.assertEqual([a[2]['phone_number'] for a in self.faux.appels_outils()], ['01 00 00 00 05'])

    def test_B03_reponse_perdue_a_verifier_jamais_rejouee_puis_constat(self):
        f = self.preparer_appel()
        self.faux.panne_outil = 'coupure'
        out = self.confirmer(f)
        self.assertNotEqual(out['status'], '200 OK')
        self.assertEqual(B.fiche(self.d, *ADMIN, f['operation'])['etat'], 'resultat_a_verifier')
        self.assertEqual(len(self.faux.appels_outils()), 1)
        self.faux.panne_outil = None
        self.confirmer(B.fiche(self.d, *ADMIN, f['operation']))                    # nouvelle tentative : rien n'est rejoué
        self.assertEqual(len(self.faux.appels_outils()), 1)
        self.assertIn('jamais rejouée', B.fiche_html(self.d, PREFIX, *ADMIN, f['operation']))
        lu = self.api('demande/rapprocher', {'id': f['operation']}, acteur=DURAND)['json']
        self.assertEqual(lu['outil'], 'list_calls')
        self.assertEqual(self.outils_appeles(), ['create_call', 'list_calls'])     # rapprochement par LECTURE
        self.assertEqual(self.api('demande/constater', {'id': f['operation'], 'constat': 'parti'}, acteur=DURAND)['json']['etat'], 'terminee')

    def test_B03_ia_en_pause_aucun_effet_consultation_possible(self):
        f = self.preparer_appel()
        self.d.setting('missions567:pause', True)
        out = self.confirmer(f)
        self.assertEqual(out['json']['error'], 'automatismes_en_pause')
        self.assertEqual(self.outils_appeles(), [])
        lu = self.api('consulter', {'outil': 'list_calls', 'arguments': {}}, acteur=DURAND)
        self.assertEqual(lu['status'], '200 OK')
        self.assertEqual(self.outils_appeles(), ['list_calls'])

    def test_B03_assistant_prepare_mais_ne_confirme_pas(self):
        f = self.preparer_appel()
        self.assertEqual(self.confirmer(f, acteur=ASSISTANT)['status'], '403 Forbidden')
        self.assertEqual(self.outils_appeles(), [])

    def test_B03_lecture_jamais_preparee_effet_jamais_consulte(self):
        with self.assertRaises(Stop):
            B.preparer(self.d, *DURAND, 'list_calls', {}, self.cle())
        with self.assertRaises(Stop):
            B.consulter(self.d, *DURAND, 'mystery_tool', {})
        with self.assertRaises(Stop):
            B.consulter(self.d, *DURAND, 'send_batch_campaign', {'numbers': [], 'task': 'x'})   # « lecture seule » annoncée, nom d'effet
        self.assertEqual(self.outils_appeles(), [])

    def test_B03_connecteur_desactive_rien_ne_se_prepare(self):
        self.api('activer', {'on': False})
        with self.assertRaises(Stop) as ctx:
            self.preparer_appel()
        self.assertEqual(str(ctx.exception), 'connecteur_bland_desactive')


# ================================================================================ 4. confidentialité et cadre professionnel
class Confidentialite(Base):
    def setUp(self):
        super().setUp()
        self.brancher()

    def test_B04_achat_montant_et_devise_affiches_sinon_non_communique(self):
        f = B.preparer(self.d, *DURAND, 'buy_credits', {'amount': 50, 'currency': 'usd'}, self.cle())
        self.assertEqual(f['apercu']['montant'], '50 USD')
        self.assertIn('50 USD', B.fiche_html(self.d, PREFIX, *DURAND, f['operation']))
        g = B.preparer(self.d, *DURAND, 'buy_credits', {'amount': 'selon offre', 'currency': 'usd'}, self.cle())
        self.assertEqual(g['apercu']['montant'], 'montant non communiqué par Bland')

    def test_B04_avertissement_dans_la_demande_et_numero_au_complet(self):
        f = self.preparer_appel()
        html = B.fiche_html(self.d, PREFIX, *DURAND, f['operation'])
        self.assertIn('les appels et les SMS passent par une IA', html)
        self.assertIn('<strong>01 00 00 00 05</strong>', html)
        self.assertIn('Rappeler le cabinet DURAND au sujet du rendez-vous.', html)
        self.assertIn('Confirmer et transmettre à Bland', html)
        page = cx.sheet_html(self.d, PREFIX, *DURAND, f['operation'])            # page /operation existante : fiche propre à Bland
        self.assertIn('b109-fiche', page)
        self.assertIn('data-api="m56109/bland/demande/confirmer"', page)
        self.assertNotIn('m5652/operation/valider', page)

    def test_B04_politique_des_donnees_refus_arret_explique_aucune_demande(self):
        with patch('agent.data_policy5689.evaluer', lambda *a, **k: {'allowed': False, 'denial_reasons': ['prestataire_non_admis']}):
            with self.assertRaises(Stop) as ctx:
                self.preparer_appel()
        self.assertEqual(str(ctx.exception), 'prestataire_non_admis')
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM connexion_ops5652 WHERE provider='bland'").fetchone()[0], 0)

    def test_B04_enveloppe_identite_hors_champ_confirme_refusee_exception_visible_et_journalisee(self):
        faux_registre = lambda self_, texte, technique=False: 'ZORVAL' in str(texte)
        with patch('agent.pseudo540.Pseudonymizer.residuelle', faux_registre):
            with self.assertRaises(Stop) as ctx:                                   # nom de partie dans un champ non affiché en entier
                B.preparer(self.d, *DURAND, 'create_call', {'phone_number': '01 00 00 00 05', 'task': 'Appel', 'voice': 'ZORVAL'}, self.cle())
            self.assertEqual(str(ctx.exception), 'pseudo_identite_residuelle')
            f = B.preparer(self.d, *DURAND, 'create_call', {'phone_number': '01 00 00 00 05', 'task': 'Appeler M. ZORVAL'}, self.cle())
        self.assertEqual(f['exceptions'], ['task'])
        self.assertIn('Exception explicite', B.fiche_html(self.d, PREFIX, *DURAND, f['operation']))
        self.assertTrue(self.d.db.execute("SELECT 1 FROM audit WHERE action='bland56109_exception_enveloppe'").fetchone())

    def test_B04_registre_illisible_rien_ne_part(self):
        with patch('agent.pseudo540.Pseudonymizer.residuelle', side_effect=OSError('illisible')):
            with self.assertRaises(Stop) as ctx:
                self.preparer_appel()
        self.assertEqual(str(ctx.exception), 'registre_identites_illisible')

    def test_B04_consigne_par_defaut_d_un_agent(self):
        texte = B.consigne_agent({'cabinet': 'DURAND', 'types': 'premier rendez-vous', 'horaires': '9 h à 18 h', 'ton': 'sobre',
                                  'telephone': '01 00 00 00 05', 'enregistrement': False})
        self.assertIn('assistant automatisé du cabinet', texte)
        self.assertIn('jamais de conseil juridique', texte)
        self.assertIn('jamais d’information sur un dossier', texte)
        self.assertIn('L’appel n’est pas enregistré.', texte)


# ================================================================================ 5. compétence native
class Competence(Base):
    def test_B05_competence_installee_desactivee_puis_activee_avec_le_connecteur(self):
        from agent import competences5632 as c32
        self.assertIn('builtin.telephonie109.bland', c32.ensure_builtin(self.d))
        etat = lambda: self.d.db.execute("SELECT enabled FROM skill_state5632 WHERE id='builtin.telephonie109.bland'").fetchone()[0]
        self.assertEqual(etat(), 0)
        self.assertEqual(self.d.db.execute("SELECT enabled FROM skill_state5632 WHERE id='builtin.assistant_juridique' OR id LIKE '%assistant%juridique%'").fetchone()[0], 1)
        self.brancher()
        self.assertEqual(etat(), 1)
        self.api('activer', {'on': False})
        self.assertEqual(etat(), 0)
        manifeste = json.loads((ROOT / 'resources/skills/telephonie109-bland/manifest.json').read_text(encoding='utf-8'))
        self.assertIs(manifeste['enabled_default'], False)


# ================================================================================ 6. agent de prise de rendez-vous enregistré comme routine
class AgentRendezVous(Base):
    def setUp(self):
        super().setUp()
        self.brancher()

    def donnees(self, **k):
        return {'cabinet': 'DURAND Avocats', 'horaires': 'du lundi au vendredi, 9 h - 18 h', 'types': 'premier rendez-vous, suivi',
                'telephone': '01 00 00 00 05', **k}

    def test_B06_questions_ciblees_si_information_manquante(self):
        out = self.api('agent/creer', {'cabinet': 'DURAND Avocats'}, acteur=DURAND)['json']
        self.assertEqual(len(out['questions']), 3)
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM connexion_ops5652 WHERE provider='bland'").fetchone()[0], 0)

    def test_B06_creation_apres_confirmation_routine_agent_ia_lecture_seule(self):
        f = self.api('agent/creer', self.donnees(), acteur=DURAND)['json']
        self.assertEqual(f['outil'], 'create_agent')
        self.assertIn('assistant automatisé', f['apercu']['arguments']['prompt'])
        self.assertIs(f['apercu']['arguments']['record'], False)                  # enregistrement jamais activé en silence
        self.assertEqual(self.outils_appeles(), [])
        self.assertEqual(self.confirmer(f)['json']['etat'], 'terminee')
        agent = self.d.db.execute('SELECT * FROM bland56109_agents').fetchone()
        self.assertEqual((agent['agent_ref'], agent['etat']), ('agent-fictif-0001', 'brouillon'))
        r = rt.get(self.d, agent['routine'])
        self.assertEqual((r['task_type'], r['enabled']), ('agent_ia', 1))
        avant = len(self.faux.appels_outils())
        res = {'produit': [], 'a_decider': [], 'impossible': []}
        B.executer(self.d, '', dict(r), res)
        lectures = {t['name'] for t in B.dernier_releve(self.d)['outils'] if t['classe'] == 'lecture'}
        faits = [a[1] for a in self.faux.appels_outils()[avant:]]
        self.assertTrue(faits and set(faits) <= lectures, faits)                     # passage : lecture seulement
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM bland56109_propositions').fetchone()[0], 1)
        B.executer(self.d, '', dict(r), {'produit': [], 'a_decider': [], 'impossible': []})
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM bland56109_propositions').fetchone()[0], 1)   # reprise sans doublon
        self.assertFalse(self.d.db.execute("SELECT 1 FROM agenda5634").fetchone() if B._table(self.d, 'agenda5634') else None)   # rien dans l'agenda
        # agent supprimé chez Bland : « introuvable », visible
        self.faux.agents_supprimes.add('agent-fictif-0001')
        res = {'produit': [], 'a_decider': [], 'impossible': []}
        B.executer(self.d, '', dict(r), res)
        self.assertEqual(self.d.db.execute('SELECT etat FROM bland56109_agents').fetchone()[0], 'introuvable')
        html = ''.join(B.cartes(self.d, PREFIX, *ADMIN, False)[0])
        self.assertIn('Introuvable chez Bland', html)

    def test_B06_publication_demande_distincte(self):
        f = self.api('agent/creer', self.donnees(), acteur=DURAND)['json']
        self.confirmer(f)
        p = B.preparer(self.d, *DURAND, 'publish_agent', {'agent_id': 'agent-fictif-0001'}, self.cle())
        self.assertEqual((p['etat'], p['engageant'], p['apercu']['motif']), ('a_valider', True, 'publication'))
        self.assertNotIn('publish_agent', self.outils_appeles())

    def test_B06_type_ferme_jamais_cree_par_le_formulaire_generique(self):
        with self.assertRaises(Stop):
            rt.create(self.d, *DURAND, {'title': 'Agent', 'task_type': 'agent_ia', 'freq': 'quotidien', 'time': '08:00'})


# ================================================================================ 7. libellé « Mes routines | Mes agents IA »
class Libelle(Base):
    def test_B07_libelle_page_d_accueil_et_traductions(self):
        from agent import web5652
        for compact in (False, True):
            html = web5652.routines_block(self.d, {'owner': 'cabinet'}, PREFIX, 'administrateur', compact=compact)
            self.assertIn('Mes routines | Mes agents IA', html)
            self.assertIn('Rappel de rendez-vous au client, deux jours avant', html)
            self.assertIn('À activer', html)
            self.assertIn('id="c5614-routines"', html)                              # identifiants inchangés
        for langue, attendu in (('en', 'My routines | My AI agents'), ('de', 'Meine Routinen | Meine KI-Agenten'), ('es', 'Mis rutinas | Mis agentes de IA')):
            table = json.loads((ROOT / 'agent' / 'locales' / ('ecrans_%s.json' % langue)).read_text(encoding='utf-8'))
            self.assertEqual(table['Mes routines | Mes agents IA'], attendu)

    def test_B07_cartes_generiques_jamais_pour_les_types_fermes(self):
        from agent import web5652
        html = web5652.routines_block(self.d, {'owner': 'cabinet'}, PREFIX, 'administrateur')
        self.assertNotIn('data-api="m5652/routine/activer" data-reload><input type="hidden" name="id" value="%s"' % B.ROUTINE_RAPPEL, html)
        self.assertEqual(html.count('id="r52-%s"' % B.ROUTINE_RAPPEL), 1)


# ================================================================================ 8. coûts
class Couts(Base):
    def setUp(self):
        super().setUp()
        self.brancher()

    def quantites(self):
        return {(r['unit_code'], r['etat']): r['quantite_milli'] for r in self.d.db.execute('SELECT * FROM budget_quantites56109')}

    def test_B08_appel_et_achat_inscrits_inconnu_reste_inconnu(self):
        self.confirmer(self.preparer_appel())
        f = B.preparer(self.d, *DURAND, 'buy_credits', {'amount': 50, 'currency': 'usd'}, self.cle())
        self.confirmer(f)
        q = self.quantites()
        self.assertEqual(q[('minute_bland', 'declaree')], 2000)
        self.assertEqual(q[('credit_bland', 'inconnue')], None)
        self.assertEqual(q[('montant_usd_bland', 'declaree')], 50000)
        g = self.preparer_appel(numero='01 00 00 00 06')
        self.faux.panne_outil = 'coupure'
        self.confirmer(g)
        self.assertEqual(self.d.db.execute("SELECT etat, quantite_milli FROM budget_quantites56109 WHERE call_id=?", (g['operation'] + ':minute_bland',)).fetchone()[:],
                         ('inconnue', None))


# ================================================================================ 9. guide et aide
class Guide(Base):
    def test_B09_guide_d_utilisation_renvoi_et_aide_quatre_langues(self):
        guide = (ROOT / 'docs' / 'GUIDE-BLAND.md').read_text(encoding='utf-8')
        for titre in ('Créer le compte et la clé', 'Tester', 'Ce que vous pouvez demander', 'Les confirmations', 'agent de prise de rendez-vous',
                      'Coûts', "Points d'attention", "Ce qui n'est pas livré", 'Désactiver', 'Rappel de rendez-vous au client'):
            self.assertIn(titre, guide)
        self.assertIn('GUIDE-BLAND.md', (ROOT / 'docs' / 'GUIDE-IA-5.6.82.md').read_text(encoding='utf-8'))
        for langue in ('fr', 'en', 'de', 'es'):
            art = json.loads((ROOT / 'agent' / 'aide' / ('%s.json' % langue)).read_text(encoding='utf-8'))['telephonie_bland']
            self.assertTrue(3 <= len(art['steps']) <= 7)
        self.assertIn('Aide : téléphonie par IA', cx.settings_html(self.d, PREFIX, *ADMIN))


# ================================================================================ 10. rappel de rendez-vous au client
class Rappel(Base):
    def setUp(self):
        super().setUp()
        from agent import agenda5634, crm5636
        crm5636.schema(self.d)
        agenda5634.schema(self.d)
        maintenant = datetime.now(timezone.utc).isoformat()
        self.d.db.execute("INSERT INTO contacts5636(id,kind,category,name,first_name,phone,created,updated) VALUES('ct-durand','personne','client','DURAND',"
                          "'Paul','01 00 00 00 05',?,?)", (maintenant, maintenant))
        self.d.db.execute("INSERT INTO contact_links5636(contact,matter,role,created) VALUES('ct-durand','DOS-001','client',?)", (maintenant,))
        self.d.db.commit()
        self.jour = B._jour_cabinet(self.d) + timedelta(days=2)
        self.rdv('ev-1', 10)

    def rdv(self, ident, heure, matter='DOS-001', titre='Stratégie DURAND c/ ZORVAL — RG 24/01234', jour=None):
        tz = B._tz(self.d)
        debut = datetime.combine(jour or self.jour, dtime(heure, 0), tz).astimezone(timezone.utc)
        maintenant = datetime.now(timezone.utc).isoformat()
        self.d.db.execute('INSERT INTO agenda5634(id,uid,kind,title,description,location,starts,ends,matter,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                          (ident, ident, 'rendez_vous', titre, 'Préparer les conclusions contre ZORVAL', '', debut.isoformat(),
                           (debut + timedelta(hours=1)).isoformat(), matter, maintenant, maintenant))
        self.d.db.commit()

    def regler(self, **k):
        base = {'cabinet': 'DURAND Avocats', 'telephone_cabinet': '01 00 00 00 09'}
        return self.api('rappel/regler', {**base, **k}, acteur=DURAND)

    def accorder(self, sms=True, appel=False):
        return self.api('rappel/accord', {'contact': 'ct-durand', 'sms': sms, 'appel': appel}, acteur=DURAND)

    def executer(self):
        res = {'produit': [], 'a_decider': [], 'impossible': []}
        B.executer(self.d, '', dict(rt.get(self.d, B.ROUTINE_RAPPEL)), res)
        return res

    def test_B10_creee_une_fois_desactivee_supprimee_non_recreee(self):
        r = rt.get(self.d, B.ROUTINE_RAPPEL)
        self.assertEqual((r['enabled'], r['task_type'], r['title']), (0, 'rappel_rdv', 'Rappel de rendez-vous au client, deux jours avant'))
        self.assertFalse(B.creer_rappel_par_defaut(self.d))
        rt.delete(self.d, *ADMIN, B.ROUTINE_RAPPEL, r['version'])
        Desk(self.f.c).db.close()                                                  # migration rejouée (nouveau démarrage)
        self.assertFalse(B.creer_rappel_par_defaut(self.d))
        with self.assertRaises(Stop):
            rt.get(self.d, B.ROUTINE_RAPPEL)

    def test_B10_activation_exige_la_connexion_prete(self):
        out = self.regler(activer=True)
        self.assertEqual(out['json']['error'], 'connecteur_bland_non_pret')
        self.assertEqual(rt.get(self.d, B.ROUTINE_RAPPEL)['enabled'], 0)
        self.brancher()
        self.assertEqual(self.regler(activer=True)['status'], '200 OK')
        self.assertEqual(rt.get(self.d, B.ROUTINE_RAPPEL)['enabled'], 1)

    def test_B10_texte_fixe_sans_aucun_element_de_l_affaire(self):
        self.brancher()
        self.regler()
        self.accorder()
        lignes = B.preparer_rappels(self.d)
        self.assertEqual([x['etat'] for x in lignes], ['prepare'])
        texte = B.fiche(self.d, *ADMIN, lignes[0]['op'])['apercu']['arguments']['message']
        for interdit in ('ZORVAL', 'Stratégie', 'RG 24/01234', 'DOS-001', 'conclusions', 'Client DEMO'):
            self.assertNotIn(interdit, texte)
        self.assertIn('DURAND Avocats', texte)
        self.assertIn('01 00 00 00 09', texte)
        self.assertIn(self.jour.strftime('%d/%m/%Y'), texte)
        self.assertEqual(B.fiche(self.d, *ADMIN, lignes[0]['op'])['apercu']['arguments']['phone_number'], '+33100000005')
        with self.assertRaises(Stop):
            B.modele_valide('Bonjour {cabinet}, au sujet de {dossier}')               # variable fermée seulement

    def test_B10_accord_absent_numero_invalide_rdv_sans_client_ignores(self):
        self.brancher()
        self.regler()
        self.rdv('ev-interne', 11, matter='')
        self.assertEqual({x['rdv']: x.get('motif') for x in B.preparer_rappels(self.d)},
                         {'ev-1': 'accord non enregistré', 'ev-interne': 'rendez-vous sans client identifié ou interne'})
        self.d.db.execute("UPDATE contacts5636 SET phone='12' WHERE id='ct-durand'")
        self.d.db.commit()
        self.accorder()
        self.d.db.execute('DELETE FROM bland56109_rappels')
        self.d.db.commit()
        self.assertEqual(B.preparer_rappels(self.d)[0]['motif'], 'numéro absent ou invalide')
        self.assertEqual(self.outils_appeles(), [])

    def test_B10_mode_preparer_une_confirmation_envoie_le_lot_sans_doublon(self):
        self.brancher()
        self.regler(activer=True)
        self.accorder()
        res = self.executer()
        self.assertIn('1 rappel(s) préparé(s), à confirmer', res['produit'])
        self.executer()                                                            # reprise : jamais de doublon
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM connexion_ops5652 WHERE provider='bland'").fetchone()[0], 1)
        self.assertEqual(self.outils_appeles(), [])                                # rien ne part sans l'avocat
        jour = self.jour.isoformat()
        emp, _l = B.empreinte_lot(self.d, jour)
        out = self.api('rappel/confirmer-lot', {'jour': jour, 'empreinte': emp, 'relu': True}, acteur=DURAND)
        self.assertEqual(out['json']['bilan'][0]['etat'], 'envoye', out['json'])
        self.assertEqual(self.outils_appeles(), ['send_sms'])
        self.assertEqual(self.d.db.execute("SELECT etat FROM bland56109_rappels").fetchone()[0], 'envoye')
        self.assertTrue(self.d.db.execute("SELECT 1 FROM audit WHERE action='bland56109_rappel_envoye'").fetchone())
        self.assertEqual(self.d.db.execute("SELECT etat, quantite_milli FROM budget_quantites56109 WHERE unit_code='sms_bland'").fetchone()[:], ('declaree', 1000))
        self.executer()
        self.assertEqual(self.outils_appeles(), ['send_sms'])

    def test_B10_rdv_deplace_ou_annule_relu_avant_l_envoi(self):
        self.brancher()
        self.regler()
        self.accorder()
        B.preparer_rappels(self.d)
        self.d.db.execute("UPDATE agenda5634 SET starts=? WHERE id='ev-1'", ((datetime.combine(self.jour, dtime(15, 0), B._tz(self.d))).astimezone(timezone.utc).isoformat(),))
        self.d.db.commit()
        jour = self.jour.isoformat()
        emp, _l = B.empreinte_lot(self.d, jour)
        out = self.api('rappel/confirmer-lot', {'jour': jour, 'empreinte': emp, 'relu': True}, acteur=DURAND)
        self.assertEqual(out['json']['bilan'][0], {'cle': _l[0][0], 'etat': 'abandonne', 'motif': 'rendez-vous déplacé depuis la préparation'})
        self.assertEqual(self.outils_appeles(), [])

    def test_B10_mode_automatique_borne_plafond_et_modele_modifie(self):
        self.brancher()
        self.rdv('ev-2', 14)
        self.accorder()
        out = self.regler(mode='auto', activer=True)
        self.assertEqual(out['json']['error'], 'ecran_envoi_auto_non_lu')        # choix explicite exigé
        self.regler(mode='auto', lu_envoi_auto=True, valider_modele=True, plafond=1, activer=True, cabinet='Cabinet Valbrune')
        res = self.executer()
        self.assertEqual(self.outils_appeles(), ['send_sms'])                       # un seul : plafond quotidien
        self.assertTrue(any('plafond' in x for x in res['impossible']), res)
        appel = self.faux.appels_outils()[0][2]
        self.assertEqual(appel['phone_number'], '+33100000005')
        # modèle modifié : retour au mode « Préparer » jusqu'à revalidation (nouveau rendez-vous)
        self.rdv('ev-3', 16)
        self.regler(modele='Rappel du cabinet {cabinet} : rendez-vous le {date} à {heure}.', plafond=10, cabinet='Cabinet Valbrune')
        res = self.executer()
        self.assertEqual(self.outils_appeles(), ['send_sms'])
        self.assertTrue(any('modèle de texte modifié' in x for x in res['impossible']), res)

    def test_B10_exception_bornee_ne_s_etend_a_aucun_autre_outil_mutation(self):
        self.brancher()
        f = B.preparer(self.d, *DURAND, 'buy_credits', {'amount': 5, 'currency': 'usd'}, self.cle())
        self.d.db.execute("UPDATE connexion_ops5652 SET trigger='rappel_automatique' WHERE id=?", (f['operation'],))
        self.d.db.commit()
        op = self.d.db.execute('SELECT * FROM connexion_ops5652 WHERE id=?', (f['operation'],)).fetchone()
        exc = {'op': op['id'], 'outil': 'buy_credits', 'numero': None, 'arguments': {'amount': 5, 'currency': 'usd'}, 'cfg': B.config_rappel_brute(self.d)}
        jeton = B._EXCEPTION.set(exc)
        try:
            with self.assertRaises(Stop) as ctx:
                cx.validate(self.d, 'rappel_automatique', 'administrateur', op['id'], op['version'], '')
            self.assertEqual(str(ctx.exception), 'confirmation_bland_requise')
            self.assertEqual(self.outils_appeles(), [])
            with patch.object(B, '_conforme_exception', lambda op_, exc_: True):  # mutation : borne retirée → l'achat partirait
                cx.validate(self.d, 'rappel_automatique', 'administrateur', op['id'], op['version'], '')
        finally:
            B._EXCEPTION.reset(jeton)
        self.assertEqual(self.outils_appeles(), ['buy_credits'])

    def test_B10_pause_ou_connexion_en_panne_rien_ne_part(self):
        self.brancher()
        self.accorder()
        self.regler(mode='auto', lu_envoi_auto=True, valider_modele=True, activer=True)
        self.d.setting('missions567:pause', True)
        self.assertIn('IA en pause : aucun rappel n’est parti', self.executer()['impossible'])
        self.d.setting('missions567:pause', False)
        self.api('activer', {'on': False})
        self.assertIn('connexion Bland non prête ou désactivée : aucun rappel n’est parti', self.executer()['impossible'])
        self.assertEqual(self.outils_appeles(), [])

    def test_B10_accord_et_canal_par_client_depuis_la_carte_appel_a_consigne_fixe(self):
        self.brancher()
        self.regler()
        html = ''.join(B.cartes(self.d, PREFIX, *DURAND, False)[0])
        self.assertIn('accepte les rappels par SMS', html)
        self.api('rappel/accord', {'contact': 'ct-durand', 'sms': False, 'appel': True, 'canal': 'appel'}, acteur=DURAND)
        lignes = B.preparer_rappels(self.d)
        f = B.fiche(self.d, *DURAND, lignes[0]['op'])                               # demande d'une routine du cabinet : visible de l'avocat
        self.assertEqual(f['outil'], 'create_call')
        consigne = f['apercu']['arguments']['task']
        self.assertIn('assistant automatisé du cabinet', consigne)
        self.assertIn('Ne réponds à aucune question sur un dossier', consigne)
        for interdit in ('ZORVAL', 'Stratégie', 'RG 24/01234', 'conclusions'):
            self.assertNotIn(interdit, consigne)
        self.assertEqual(self.outils_appeles(), [])

    def test_B10_rendez_vous_passe_jamais_rappele(self):
        self.brancher()
        self.regler()
        self.accorder()
        self.d.db.execute('DELETE FROM agenda5634')
        self.d.db.commit()
        hier = B._jour_cabinet(self.d) - timedelta(days=1)
        self.rdv('ev-passe', 10, jour=hier)
        self.assertEqual(B.preparer_rappels(self.d, jour=hier)[0]['motif'], 'rendez-vous déjà passé')


# ================================================================================ relecture indépendante : mode « Envoyer automatiquement » (D1 à D4)
class RappelAutomatique(Rappel):
    def regler(self, **k):
        # nom du cabinet sans identité du registre (DURAND est aussi un client fictif : en mode automatique, il bloquerait l'envoi, voir D2)
        return super().regler(**{'cabinet': 'Cabinet Valbrune', **k})

    def activer_auto(self, **k):
        out = self.regler(mode='auto', lu_envoi_auto=True, valider_modele=True, activer=True, **k)
        self.assertEqual(out['status'], '200 OK', out['json'])
        return out

    def test_D1_reponse_perdue_comptee_dans_le_plafond_et_arret_au_premier_a_verifier(self):
        self.brancher()
        self.rdv('ev-2', 14)
        self.rdv('ev-3', 16)
        self.accorder()
        self.activer_auto(plafond=5)
        self.faux.panne_outil = 'coupure'                                           # Bland ne répond pas : envoi peut-être parti
        res = self.executer()
        self.assertEqual(len(self.faux.appels_outils()), 1, 'au premier « à vérifier », plus aucun envoi automatique')
        self.assertTrue(any('à vérifier' in x for x in res['impossible']), res)
        self.assertTrue(B.config_rappel_brute(self.d).get('auto_suspendu'))
        self.assertEqual(B._envois_auto_du_jour(self.d), 1)                         # la tentative compte dans le plafond
        self.faux.panne_outil = None
        self.executer()                                                              # suspendu : rien ne repart, même le lendemain du passage
        self.assertEqual(len(self.faux.appels_outils()), 1)

    def test_D1_tentative_reservee_avant_l_appel_plafond_un(self):
        self.brancher()
        self.rdv('ev-2', 14)
        self.accorder()
        self.activer_auto(plafond=1)
        self.faux.panne_outil = 'http_429'                                           # refus net : rien n'est parti, mais la tentative compte
        self.executer()
        self.faux.panne_outil = None
        self.assertEqual(len(self.faux.appels_outils()), 1)
        self.assertEqual(B._envois_auto_du_jour(self.d), 1)

    def test_D2_lieu_libre_jamais_dans_le_rappel_valeurs_fermees(self):
        self.brancher()
        self.d.db.execute("UPDATE agenda5634 SET location='TJ Lyon, Martin c/ Durand' WHERE id='ev-1'")
        self.d.db.commit()
        self.regler()
        self.accorder()
        lignes = B.preparer_rappels(self.d)
        texte = B.fiche(self.d, *ADMIN, lignes[0]['op'])['apercu']['arguments']['message']
        for interdit in ('TJ Lyon', 'Martin', 'c/ Durand'):
            self.assertNotIn(interdit, texte)
        self.assertNotIn('()', texte)
        self.d.db.execute('DELETE FROM bland56109_rappels')
        self.d.db.execute("UPDATE connexion_ops5652 SET state='annulee' WHERE provider='bland'")
        self.d.db.commit()
        self.regler(adresse_cabinet='3 rue fictive, Valbrune')
        self.rdv('ev-9', 11, jour=self.jour)
        textes = [B.fiche(self.d, *ADMIN, x['op'])['apercu']['arguments']['message'] for x in B.preparer_rappels(self.d) if x.get('op')]
        self.assertTrue(any('3 rue fictive, Valbrune' in t for t in textes), textes)

    def test_D2_mode_automatique_aucune_exception_d_identite(self):
        self.brancher()
        self.accorder()
        self.activer_auto(cabinet='Cabinet ZORVAL')
        with patch('agent.pseudo540.Pseudonymizer.residuelle', lambda self_, texte, technique=False: 'ZORVAL' in str(texte)):
            res = self.executer()
        self.assertEqual(self.outils_appeles(), [])
        rappel = self.d.db.execute('SELECT * FROM bland56109_rappels').fetchone()
        self.assertEqual(self.d.db.execute('SELECT state FROM connexion_ops5652 WHERE id=?', (rappel['op'],)).fetchone()[0], 'a_valider')
        self.assertIn('identité', rappel['motif'])
        self.assertTrue(any('identité' in x for x in res['impossible']), res)

    def test_D3_droit_envoyer_exige_pour_le_mode_automatique_et_relu_a_chaque_passage(self):
        from agent import membres5650
        self.brancher()
        self.accorder()
        membres5650.set_rights(self.d.c['state_dir'], 'cabinet', DURAND[0], 'avocat', {'envoyer': False})
        out = self.regler(mode='auto', lu_envoi_auto=True, valider_modele=True, activer=True)
        self.assertEqual((out['status'], out['json']['error']), ('403 Forbidden', 'role_insuffisant'))
        self.assertEqual(self.regler(activer=True)['json']['error'], 'role_insuffisant')
        membres5650.set_rights(self.d.c['state_dir'], 'cabinet', DURAND[0], 'avocat', {'envoyer': True})
        self.activer_auto()
        membres5650.set_rights(self.d.c['state_dir'], 'cabinet', DURAND[0], 'avocat', {'envoyer': False})   # droit retiré après l'activation
        res = self.executer()
        self.assertEqual(self.outils_appeles(), [])
        self.assertTrue(any('droit « envoyer »' in x for x in res['impossible']), res)

    def test_D4_canal_appel_consigne_et_valeurs_dans_l_empreinte_validee(self):
        self.brancher()
        self.accorder(sms=False, appel=True)
        self.activer_auto(canal='appel')
        cfg = B.config_rappel_brute(self.d)
        self.assertTrue(B._mode_auto_valable(cfg))
        with patch.object(B, 'CONSIGNE_APPEL', B.CONSIGNE_APPEL + ' Dis aussi autre chose.'):
            self.assertFalse(B._mode_auto_valable(cfg))                            # la consigne d'appel fait partie de l'empreinte
        self.regler(telephone_cabinet='01 00 00 00 08', canal='appel')               # valeur du texte modifiée sans revalidation
        res = self.executer()
        self.assertEqual(self.outils_appeles(), [])
        self.assertTrue(any('modifié depuis sa validation' in x for x in res['impossible']), res)

    def test_J_jeton_de_service_ne_confirme_ni_ne_regle(self):
        self.brancher()
        f = self.preparer_appel()
        for route, data in (('demande/confirmer', {'id': f['operation'], 'version': f['version'], 'empreinte': f['empreinte'], 'relu': True}),
                            ('rappel/regler', {'mode': 'auto', 'lu_envoi_auto': True, 'valider_modele': True})):
            raw = json.dumps({**data, 'cle': self.cle()}).encode()
            env = {'REQUEST_METHOD': 'POST', 'HTTP_ORIGIN': self.auth['origin'], 'HTTP_X_CSRF_TOKEN': 'test-csrf', 'CONTENT_TYPE': 'application/json',
                   'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw), 'HTTP_AUTHORIZATION': 'Bearer jeton-de-service-fictif'}
            out = B.api(env, self.d, self.auth, PREFIX, B.PREFIXE + route, {}, 'POST')
            self.assertEqual((out['status'], json.loads(out['body'])['error']), ('403 Forbidden', 'jeton_service_refuse'), route)
        self.assertEqual(self.outils_appeles(), [])
        self.assertEqual(B.config_rappel_brute(self.d)['mode'], 'preparer')


for _nom in [n for n in vars(Rappel) if n.startswith('test_')]:
    setattr(RappelAutomatique, _nom, None)   # mêmes outils d'essai que Rappel, sans rejouer ses tests


# ================================================================================ droits, 404 neutre, aucune écriture pendant un GET
class Droits(Base):
    def test_B11_404_neutre_absent_autre_fournisseur_ou_interdit(self):
        self.brancher()
        f = self.preparer_appel()
        absent = self.api('demande/fiche', methode='GET', args={'id': 'op-inexistante'})
        self.assertEqual(absent['status'], '404 Not Found')
        cx.ensure_schema(self.d)
        self.d.db.execute("INSERT INTO connexion_ops5652(id,connection,provider,capability,owner,state,idem_key,payload_json,payload_hash,created,updated) "
                          "VALUES('op-autre','cx-x','youtrust','signature.send_approved','autre@example.test','a_valider','idem-x','{}','h','t','t')")
        self.d.db.commit()
        autre = self.api('demande/fiche', methode='GET', args={'id': 'op-autre'})
        interdit = self.api('demande/fiche', methode='GET', args={'id': f['operation']}, acteur=('autre@example.test', 'avocat'))
        self.assertEqual((autre['status'], autre['body']), (absent['status'], absent['body']))
        self.assertEqual((interdit['status'], interdit['body']), (absent['status'], absent['body']))
        self.assertIn(('Cache-Control', 'private, no-store'), absent['headers'])

    def test_B11_aucune_ecriture_pendant_un_get(self):
        self.brancher()
        f = self.preparer_appel()
        avant = self.total_changes()
        self.api('etat', methode='GET')
        self.api('outils', methode='GET')
        self.api('demande/fiche', methode='GET', args={'id': f['operation']})
        B.carte_html(self.d, PREFIX, *ADMIN)
        B.cartes(self.d, PREFIX, *ADMIN, False)
        B.fiche_html(self.d, PREFIX, *ADMIN, f['operation'])
        self.assertEqual(self.total_changes(), avant)

    def test_B11_csrf_et_cle_d_idempotence(self):
        raw = json.dumps({'api_key': FB.CLE_VALIDE, 'cle': self.cle()}).encode()
        env = {'REQUEST_METHOD': 'POST', 'HTTP_ORIGIN': 'https://ailleurs.example.test', 'HTTP_X_CSRF_TOKEN': 'faux', 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw), 'HTTP_X_AXIORHUB_ROLE': 'administrateur', 'HTTP_X_AXIORHUB_USER': 'cabinet'}
        self.assertEqual(B.api(env, self.d, self.auth, PREFIX, B.PREFIXE + 'cle', {}, 'POST')['status'], '403 Forbidden')
        self.assertEqual(self.api('tester', {'cle': ''})['json']['error'], 'cle_requise')
        cle = self.cle()
        self.api('cle', {'api_key': FB.CLE_VALIDE, 'cle': cle})
        self.assertEqual(self.api('cle', {'api_key': 'org_autre_cle_fictive', 'cle': cle})['status'], '409 Conflict')

    def test_B11_routes_declarees(self):
        from agent import standalone_auth, web440
        import inspect
        source = inspect.getsource(standalone_auth)
        self.assertIn("m56109/bland/", source)
        self.assertIn("m56109/bland/", (ROOT / 'agent' / 'web440.py').read_text(encoding='utf-8'))
        self.assertIn('/static/v56109-bland.css', web440.STATIC)
        self.assertFalse(B.allowed('assistant', 'POST', 'm56109/bland/demande/confirmer'))
        self.assertTrue(B.allowed('assistant', 'POST', 'm56109/bland/demande/preparer'))
        self.assertFalse(B.allowed('avocat', 'POST', 'm56109/bland/cle'))


if __name__ == '__main__':
    unittest.main()
