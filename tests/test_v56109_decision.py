"""5.6.109, lot K, F07 : décision locale et choix des actions du LLM (``agent/decision56109.py``).

Contrat fermé (intentions et actions distinguées, abstention, « indéterminé », version du moteur), mode observation d'abord, mode actif
désactivé par défaut (dispatch revalidé), aucun second orchestrateur (point de montage unique dans le travailleur), transport commun
(``model.Model``) sur un faux Ollama, local strict sans aucun repli externe, question ciblée unique (prospect et honoraires, catalogue du
lot D), banc de comparaison. Faux services uniquement : aucun réseau, aucun modèle réel ; les durées et taux du banc ne qualifient rien.
Le gain réel de la décision locale sur des demandes françaises du cabinet n'est pas mesurable ici."""
from dataclasses import replace
import importlib.util
import io
import json
from pathlib import Path
import socket
import tempfile
from threading import BoundedSemaphore
import unittest
import uuid
from unittest.mock import patch

from test_v5689_ia import muter
from test_v56109_honoraires import ADMIN, CLAIRE, JETON, ORIGINE, CatalogueBase
from agent import decision56109 as dec, export56108, standalone_auth
from agent.common import Stop
from agent.decision56109 import Contexte, CoucheDecision, ErreurMoteur, competences_proposees, expediteur_du_cabinet, valider_sortie

RACINE = Path(__file__).resolve().parents[1]
AVOCATE = (CLAIRE, 'avocat')


def _reseau_interdit(*a, **k):
    raise AssertionError('appel réseau non simulé')


def setUpModule():
    global _p1, _p2
    _p1 = patch.object(socket.socket, 'connect', _reseau_interdit)
    _p2 = patch.object(socket, 'create_connection', _reseau_interdit)
    _p1.start()
    _p2.start()


def tearDownModule():
    _p1.stop()
    _p2.stop()


def sortie(**changes):
    obj = {'intention': 'honoraires', 'action': 'consulter_le_bareme', 'complexite': 'simple', 'manquants': [], 'source_ids': []}
    obj.update(changes)
    return json.dumps(obj)


class FauxMoteur:
    nom, local = 'simule', True

    def __init__(self, reponse=None, appel=None):
        self.reponse, self.appel, self.appels, self.cfg = reponse or sortie(), appel, 0, {'model': 'modele-local-fictif'}

    def decider(self, contexte, checkpoint):
        self.appels += 1
        if self.appel:
            self.appel()
        return self.reponse


class ArretProduit(Exception):
    pass


# ------------------------------------------------------------------------------------------------ contrat fermé
class Contrat(unittest.TestCase):
    def couche(self, backend=None, *, checkpoint=lambda: None, autoriser=lambda c, a: None, **kw):
        return CoucheDecision(backend or FauxMoteur(), checkpoint=checkpoint, autoriser=autoriser, **kw)

    def test_F07_decisions_fermees_distinguees_et_sans_probabilite(self):
        for intention, action in (('honoraires', 'consulter_le_bareme'), ('rendez_vous', 'preparer_rendez_vous'),
                                  ('analyse_juridique', 'rechercher_dans_le_dossier'), ('devis', 'consulter_le_bareme'), ('autre', 'rediger')):
            d = self.couche(FauxMoteur(sortie(intention=intention, action=action))).proposer(Contexte('Demande fictive'))
            self.assertEqual((d.statut, d.intention, d.action), ('propose', intention, action))
            self.assertIsNone(d.probabilite)
        self.assertEqual(set(dec.INTENTIONS), {'devis', 'rendez_vous', 'honoraires', 'analyse_juridique', 'autre', 'indetermine'})

    def test_F07_abstention_et_indetermine(self):
        d = self.couche(FauxMoteur(sortie(intention='indetermine'))).proposer(Contexte('Fais le nécessaire'))
        self.assertEqual((d.statut, d.action, d.motif), ('indetermine', 'indetermine', 'decision_incertaine'))
        self.assertEqual(dec.raison_courte(d), dec.INCERTAIN)

    def test_F07_sortie_hors_contrat_refusee(self):
        for s in (sortie(action='envoyer'), sortie(confidence=0.99), sortie(montant=1500), sortie(manquants=['secret']), 'not json',
                  sortie()[:-1] + ',"action":"rediger"}', sortie().replace('"simple"', 'NaN'), ' ' * 8193):
            with self.subTest(s=s[:40]), self.assertRaises(ErreurMoteur):
                valider_sortie(s, Contexte('Conseil'))
        with self.assertRaises(ErreurMoteur):
            valider_sortie(sortie(source_ids=['autre-dossier']), Contexte('Conseil', source_ids=('S1',)))
        with self.assertRaises(ErreurMoteur):
            valider_sortie(sortie(manquants=['type_intervention']), Contexte('Conseil'))     # le serveur sait ce champ connu

    def test_F07_chercher_avant_de_questionner_puis_question_unique_du_formulaire(self):
        c = Contexte('Honoraires', champs_manquants=('type_intervention', 'juridiction'))
        m = self.couche(FauxMoteur(sortie(action='demander_une_precision', manquants=['juridiction'])))
        self.assertEqual(m.proposer(c).action, 'consulter_le_bareme')
        d = m.proposer(replace(c, recherches_terminees=True))
        self.assertEqual((d.action, d.manquants), ('demander_une_precision', ('type_intervention',)))
        self.assertEqual(d.question, 'Pour quelle intervention souhaitez-vous proposer des honoraires ?')
        d = self.couche(FauxMoteur(sortie(action='rediger'))).proposer(replace(c, recherches_terminees=True))
        self.assertEqual(d.action, 'demander_une_precision')

    def test_F07_complexite_jamais_abaissee(self):
        self.assertEqual(self.couche().proposer(Contexte('Conclusions', niveau_minimum='complexe')).complexite, 'complexe')

    def test_F07_message_du_cabinet_automatique_sans_appel(self):
        b = FauxMoteur()
        d = self.couche(b).proposer(Contexte('Copie', automatique=True, message_du_cabinet=True))
        self.assertEqual((d.statut, b.appels), ('ignore', 0))
        self.assertTrue(expediteur_du_cabinet('Cabinet <CONTACT@example.test>', ('contact@example.test',)))
        self.assertFalse(expediteur_du_cabinet('contact+x@example.test', ('contact@example.test',)))

    def test_F07_pause_avant_et_pendant_aucun_resultat_publie(self):
        b = FauxMoteur()

        def stop():
            raise ArretProduit('ia_en_pause')
        with self.assertRaises(ArretProduit):
            self.couche(b, checkpoint=stop).proposer(Contexte('Devis'))
        self.assertEqual(b.appels, 0)
        pause = [False]
        b = FauxMoteur(appel=lambda: pause.__setitem__(0, True))
        with self.assertRaises(ArretProduit):
            self.couche(b, checkpoint=lambda: (_ for _ in ()).throw(ArretProduit('ia_en_pause')) if pause[0] else None).proposer(Contexte('Devis'))

    def test_F07_droit_retire_au_retour_et_action_non_autorisee(self):
        retire = [False]

        def auth(c, action):
            if retire[0] or action == 'consulter_le_bareme':
                raise ArretProduit('droit_retire')
        with self.assertRaises(ArretProduit):
            self.couche(FauxMoteur(appel=lambda: retire.__setitem__(0, True)), autoriser=auth).proposer(Contexte('Devis'))
        retire[0] = False
        with self.assertRaises(ArretProduit):
            self.couche(autoriser=auth).proposer(Contexte('Devis'))

    def test_F07_moteur_occupe_entree_longue_moteur_externe(self):
        verrou = BoundedSemaphore(1)
        verrou.acquire()
        b = FauxMoteur()
        self.assertEqual(self.couche(b, verrou=verrou).proposer(Contexte('Devis')).motif, 'moteur_occupe')
        self.assertEqual(self.couche(b).proposer(Contexte('x' * 6001)).motif, 'contexte_inadapte')
        b.local = False
        self.assertEqual(self.couche(b).proposer(Contexte('Devis')).statut, 'bloque')
        self.assertEqual(b.appels, 0)

    def test_F07_competences_methode_generale_d_abord_deux_au_plus(self):
        self.assertEqual(competences_proposees('aj', 'complement', {'aj', 'complement'}), ('aj', 'complement'))
        self.assertEqual(competences_proposees('aj', 'complement', {'aj'}), ('aj',))
        self.assertEqual(competences_proposees('aj', 'x', {'aj', 'x'}, choix_manuel=('x',)), ('x',))


# ------------------------------------------------------------------------------------------------ faux Ollama derrière le transport commun
class FauxHTTP:
    """Remplace ``common.HTTP`` dans ``model`` : /api/show puis /api/chat ; aucune socket."""
    appels = []
    show = {'details': {'family': 'qwen', 'parameter_size': '3B'}, 'capabilities': ['completion']}
    chat = None

    def __init__(self, url, local_only=False, timeout=0, local_hosts=()):
        self.url, self.headers = url, {}
        FauxHTTP.appels.append(('init', url, local_only, timeout))

    def json(self, method, path, payload=None):
        FauxHTTP.appels.append((method, path, payload))
        if path == '/api/show':
            return dict(FauxHTTP.show)
        if isinstance(FauxHTTP.chat, BaseException):
            raise FauxHTTP.chat
        return FauxHTTP.chat or {'done': True, 'message': {'content': sortie()}, 'prompt_eval_count': 40, 'eval_count': 20}


class Produit(CatalogueBase):
    def setUp(self):
        super().setUp()
        dec.ensure_schema(self.desk)
        self.desk.c['ollama'] = {'url': 'http://127.0.0.1:11434', 'model': 'modele-local-fictif', 'num_ctx': 32768,
                                 'secours': {'delay': 1, 'candidates': [{'provider_id': 'externe', 'provider_type': 'openai'}]},
                                 'hybrid_external': True, 'budget_fallback': {'model': 'autre'}}
        FauxHTTP.appels, FauxHTTP.chat, FauxHTTP.show = [], None, {'details': {'family': 'qwen'}, 'capabilities': ['completion']}
        p = patch('agent.model.HTTP', FauxHTTP)
        p.start()
        self.addCleanup(p.stop)

    def regler(self, mode, **plus):
        r = dec.lire_reglage(self.desk)
        return dec.ecrire_reglage(self.desk, 'cabinet', 'administrateur', {'version': r['version'], 'mode': mode, **plus})

    def travail(self, kind='assistant_answer', question='Prépare une réponse à ce prospect sur mes honoraires.', **plus):
        args = {'question': question, 'owner': CLAIRE, 'role': 'avocat', 'matter': self.did, **plus}
        jid = self.desk.enqueue(kind, args, origin='user')
        self.desk.db.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))
        self.desk.db.commit()
        self.desk.active_job_id = jid
        self.addCleanup(setattr, self.desk, 'active_job_id', None)
        return self.desk.db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone(), args

    def chats(self):
        return [a for a in FauxHTTP.appels if a[1] == '/api/chat']

    def lignes(self):
        return self.desk.db.execute('SELECT * FROM decisions56109').fetchall()


class Reglage(Produit):
    def test_F07_desactive_par_defaut_aucun_appel_aucune_ecriture(self):
        self.assertEqual(dec.lire_reglage(self.desk)['mode'], 'desactive')
        row, args = self.travail()
        self.assertIsNone(dec.au_travail(self.desk, row, args))
        self.assertEqual((FauxHTTP.appels, len(self.lignes())), ([], 0))

    def test_F07_garde_fou_desactive_par_defaut_retire_le_test_echouerait(self):
        with muter(dec, "DEFAUT_MODE = 'desactive'", "DEFAUT_MODE = 'actif'") as copie:
            copie.ensure_schema(self.desk)
            row, args = self.travail()
            out = copie.au_travail(self.desk, row, args)
        self.assertIsNotNone(out)                                              # sans le défaut, un modèle partirait à chaque travail
        self.assertTrue(self.chats())

    def test_F07_reglage_administrateur_version_et_route(self):
        st, body = self.route('reglage', {'mode': 'observation', 'version': 0, 'cle': self.cle()}, who=AVOCATE)
        self.assertEqual(st, '403')
        cle = self.cle()
        st, body = self.route('reglage', {'mode': 'observation', 'version': 0, 'cle': cle})
        self.assertEqual((st, body['reglage']['mode'], body['reglage']['version']), ('200', 'observation', 1))
        self.assertEqual(self.route('reglage', {'mode': 'observation', 'version': 0, 'cle': cle})[1]['reglage']['version'], 1)   # même clé : même réponse
        st, body = self.route('reglage', {'mode': 'actif', 'version': 0, 'cle': self.cle()})
        self.assertEqual(st, '409')
        st, body = self.route('etat')
        self.assertEqual((st, body['reglage']['mode'], body['reglage']['repli_externe']), ('200', 'observation', False))
        self.assertIn('skejja', body['moteurs_experimentaux'])
        self.assertTrue(standalone_auth.allowed('administrateur', 'POST', '/api440/m56109/decision/reglage'))
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/m56109/decision/reglage'))
        self.assertTrue(standalone_auth.allowed('assistant', 'POST', '/api440/m56109/decision/repondre'))
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/m56109/decision/inconnue'))

    def route(self, route, data=None, who=('cabinet', 'administrateur'), args=None):
        from agent import web440
        corps = json.dumps(data or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': who[0], 'HTTP_X_AXIORHUB_ROLE': who[1], 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON, 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps), 'REQUEST_METHOD': 'GET' if data is None else 'POST'}
        out = web440.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', dec.PREFIXE + route, args or {}, env['REQUEST_METHOD'])
        self.assertIn(('Cache-Control', 'private, no-store'), out.get('headers') or [])
        return out['status'][:3], json.loads(out['body'])

    def test_F07_section_parametres_lecture_seule(self):
        avant = self.desk.db.total_changes
        html = dec.section_html(self.desk, '/p', 'administrateur', 'cabinet')
        self.assertEqual(self.desk.db.total_changes, avant)
        self.assertIn('Organisation locale des demandes', html)
        self.assertIn('data-api="m56109/decision/reglage"', html)
        self.assertIn('Désactivée : aucune demande n’est classée', html)
        self.assertNotIn('data-api="m56109/decision/reglage"', dec.section_html(self.desk, '/p', 'avocat', CLAIRE))


class Observation(Produit):
    def test_F07_observation_trace_la_decision_parcours_inchange_sans_contenu(self):
        self.regler('observation')
        row, args = self.travail()
        out = dec.au_travail(self.desk, row, args)
        self.assertEqual((out['mode'], out['statut'], out['effet']), ('observation', 'propose', 'aucun'))
        [l] = self.lignes()
        self.assertEqual((l['version_moteur'], l['moteur'], l['modele'], l['action_retenue']), (dec.VERSION_MOTEUR, 'ollama', 'modele-local-fictif', 'consulter_le_bareme'))
        self.assertNotIn('prospect', json.dumps(dict(l), ensure_ascii=False))       # jamais la consigne au journal
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM decision_questions56109').fetchone()[0], 0)
        self.assertEqual(len(self.chats()), 1)
        again = dec.au_travail(self.desk, row, args)                           # même étape : décision reprise, aucun second appel
        self.assertTrue(again['reprise'])
        self.assertEqual(len(self.chats()), 1)

    def test_F07_transport_commun_ollama_seul_fenetre_bornee_sans_methode_du_travail(self):
        self.regler('observation')
        row, args = self.travail()
        from agent import competences5632 as c32
        with c32.for_job(self.desk, row, args):                                # le travail a sa méthode (Assistant juridique) ; le classement ne la reçoit pas
            dec.au_travail(self.desk, row, args)
        init = [a for a in FauxHTTP.appels if a[0] == 'init']
        self.assertEqual([a[1:3] for a in init], [('http://127.0.0.1:11434', True)])
        [chat] = self.chats()
        req = chat[2]
        self.assertEqual((req['options']['num_ctx'], req['options']['num_predict'], req['keep_alive'], req['format']['additionalProperties']),
                         (4096, 384, '2m', False))
        self.assertEqual(len(req['messages']), 2)                              # système du classement + paquet : aucune compétence jointe
        self.assertNotIn('COMPÉTENCE', json.dumps(req['messages'], ensure_ascii=False))

    def test_F07_instruction_explicite_et_type_de_travail_non_classes(self):
        self.regler('observation')
        for kind, plus in (('assistant_answer', {'action': 'relire'}), ('assistant_answer', {'skills': ['x']}), ('prepare_legal_opinion', {})):
            row, args = self.travail(kind, **plus)
            self.assertIsNone(dec.au_travail(self.desk, row, args), kind)
        self.assertEqual(self.chats(), [])

    def test_F07_point_de_montage_dans_le_travailleur(self):
        self.regler('observation')
        jid = self.desk.enqueue('assistant_answer', {'question': 'Quels honoraires pour un divorce ?', 'owner': CLAIRE, 'role': 'avocat', 'matter': self.did}, origin='user')
        vus = []
        with patch.object(self.desk, 'perform', lambda kind, args: vus.append(kind) or {'ok': True, 'sans_controle': True}):
            self.assertTrue(self.desk.work_once())
        self.assertEqual(vus, ['assistant_answer'])                            # le parcours existant s'exécute toujours
        self.assertEqual([l['job_id'] for l in self.lignes()], [jid])
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()[0], 'done')

    def test_F07_panne_du_classement_ne_bloque_jamais_le_travail(self):
        self.regler('observation')
        jid = self.desk.enqueue('assistant_answer', {'question': 'Honoraires ?', 'owner': CLAIRE, 'role': 'avocat', 'matter': self.did}, origin='user')
        with patch.object(dec, 'contexte_du_travail', side_effect=RuntimeError('panne fictive')), \
                patch.object(self.desk, 'perform', lambda kind, args: {'ok': True, 'sans_controle': True}):
            self.assertTrue(self.desk.work_once())
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()[0], 'done')

    def test_F07_pause_propage_l_arret(self):
        self.regler('observation')
        row, args = self.travail()
        with patch('agent.pause5629.checkpoint', side_effect=Stop('ia_en_pause')):
            with self.assertRaises(Stop):
                dec.au_travail(self.desk, row, args)
        self.assertEqual((self.chats(), len(self.lignes())), ([], 0))


class LocalStrict(Produit):
    def test_F07_panne_locale_indeterminee_aucun_repli_externe(self):
        self.regler('observation')
        FauxHTTP.chat = Stop('connexion_http_indisponible')
        row, args = self.travail()
        out = dec.au_travail(self.desk, row, args)
        self.assertEqual(out['statut'], 'indetermine')
        [l] = self.lignes()
        self.assertEqual(l['motif'], 'fournisseur_ia_injoignable')
        self.assertTrue(all(a[1].startswith('http://127.0.0.1') for a in FauxHTTP.appels if a[0] == 'init'))   # aucun fournisseur externe construit
        self.assertEqual(len(self.chats()), 1)                                 # aucune nouvelle tentative

    def test_F07_garde_fou_local_strict_retire_le_secours_partirait(self):
        moteur = dec.MoteurOllama(self.desk)
        self.assertFalse({'secours', 'hybrid_external', 'budget_fallback'} & set(moteur.cfg))
        self.assertTrue(moteur.cfg['relance_interdite56109'])
        with muter(dec, "    cfg = {k: v for k, v in base.items() if k not in CLES_EXCLUES}", "    cfg = dict(base)") as copie:
            self.assertIn('secours', copie.config_locale(self.desk))          # sans le filtre, le secours externe serait transmis au transport

    def test_F07_sortie_tronquee_sans_relance(self):
        self.regler('observation')
        FauxHTTP.chat = {'done': True, 'done_reason': 'length', 'message': {'content': '{"intention"'}, 'prompt_eval_count': 50, 'eval_count': 384}
        row, args = self.travail()
        out = dec.au_travail(self.desk, row, args)
        self.assertEqual(out['statut'], 'indetermine')
        self.assertEqual(len(self.chats()), 1)                                 # ``model._feuille`` aurait relancé plus long sans le réglage

    def test_F07_garde_fou_zero_relance_retire_deux_generations(self):
        from agent import model
        self.regler('observation')
        FauxHTTP.chat = {'done': True, 'done_reason': 'length', 'message': {'content': '{"intention"'}, 'prompt_eval_count': 50, 'eval_count': 384}
        row, args = self.travail()
        with muter(model, " and not self.cfg.get('relance_interdite56109')", '') as copie:
            copie.HTTP = FauxHTTP
            dec.au_travail(self.desk, row, args, moteur=dec.MoteurOllama(self.desk, fabrique=copie.Model))
        self.assertEqual(len(self.chats()), 2)                                 # sans le point de montage, le transport relancerait

    def test_F07_modele_distant_refuse_avant_toute_donnee(self):
        self.regler('observation')
        FauxHTTP.show = {'details': {'family': 'qwen'}, 'remote_host': 'cloud.example.test'}
        row, args = self.travail()
        self.assertEqual(dec.au_travail(self.desk, row, args)['statut'], 'indetermine')
        self.assertEqual(self.chats(), [])

    def test_F07_politique_exclusive_n_appelle_pas_ollama_en_secret(self):
        self.regler('observation')
        self.desk.setting('ai:politique', {'mode': 'mistral'})
        row, args = self.travail()
        out = dec.au_travail(self.desk, row, args)
        self.assertEqual(out['statut'], 'bloque')
        self.assertEqual(self.lignes()[0]['motif'], 'ollama_exclu_par_la_politique')
        self.assertEqual(FauxHTTP.appels, [])


class Actif(Produit):
    def test_F07_actif_question_unique_prestations_du_catalogue_et_reutilisation(self):
        self.publiee()
        self.regler('actif')
        FauxHTTP.chat = {'done': True, 'message': {'content': sortie(action='rediger', intention='honoraires')}}
        row, args = self.travail(question='Prépare une réponse à ce prospect sur mes honoraires.')
        out = dec.au_travail(self.desk, row, args)
        self.assertEqual((out['action'], out['effet'][:9]), ('demander_une_precision', 'question:'))
        [q] = dec.questions(self.desk, CLAIRE, 'avocat')
        self.assertEqual(q['question'], 'Pour quelle intervention souhaitez-vous proposer des honoraires ?')
        ids = [o['id'] for o in q['options']]
        self.assertTrue(ids[0].startswith('offre:'))
        self.assertEqual(ids[-2:], ['autre', 'inconnu'])
        # nouvelle étape du même travail (sources inchangées) : aucune seconde question, aucun second appel
        dec.au_travail(self.desk, row, args)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM decision_questions56109').fetchone()[0], 1)
        self.assertEqual(len(self.chats()), 1)
        # réponse de l'avocat : versionnée, chiffrage du catalogue, puis plus redemandée
        r = dec.repondre(self.desk, CLAIRE, 'avocat', {'id': q['id'], 'version': q['version'], 'valeur': ids[0]})
        self.assertEqual((r['etat'], r['version']), ('repondue', 2))
        self.assertEqual(r['chiffrage']['origine'], 'catalogue_cabinet')
        with self.assertRaises(Stop):
            dec.repondre(self.desk, CLAIRE, 'avocat', {'id': q['id'], 'version': 1, 'valeur': 'inconnu'})   # version périmée : 409
        ctx = dec.contexte_du_travail(self.desk, row, args)
        self.assertNotIn('type_intervention', ctx.champs_manquants)

    def test_F07_question_d_un_autre_compte_404_neutre(self):
        self.regler('actif')
        FauxHTTP.chat = {'done': True, 'message': {'content': sortie(action='rediger')}}
        row, args = self.travail()
        dec.au_travail(self.desk, row, args)
        [q] = dec.questions(self.desk, CLAIRE, 'avocat')
        self.assertEqual(dec.questions(self.desk, 'autre@example.test', 'avocat'), [])
        with self.assertRaisesRegex(Stop, 'introuvable'):
            dec.repondre(self.desk, 'autre@example.test', 'avocat', {'id': q['id'], 'version': 1, 'valeur': 'inconnu'})
        with self.assertRaisesRegex(Stop, 'introuvable'):
            dec.repondre(self.desk, CLAIRE, 'avocat', {'id': uuid.uuid4().hex, 'version': 1, 'valeur': 'inconnu'})

    def test_F07_observation_n_ouvre_aucune_question(self):
        self.regler('observation')
        FauxHTTP.chat = {'done': True, 'message': {'content': sortie(action='rediger')}}
        row, args = self.travail()
        self.assertEqual(dec.au_travail(self.desk, row, args)['action'], 'demander_une_precision')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM decision_questions56109').fetchone()[0], 0)

    def test_F07_garde_fou_dispatch_revalide_les_droits(self):
        self.regler('actif')
        FauxHTTP.chat = {'done': True, 'message': {'content': sortie(action='rediger')}}
        row, args = self.travail()
        appels = []
        vrai = dec.autorisation

        def autorisation(desk, r, a):
            base = vrai(desk, r, a)

            def autoriser(ctx, action):
                appels.append(action)
                if len(appels) > 3:                                            # droit retiré entre la décision et l'effet
                    raise Stop('droit_retire')
                return base(ctx, action)
            return autoriser
        with patch.object(dec, 'autorisation', autorisation):
            out = dec.au_travail(self.desk, row, args)
        self.assertEqual(out['effet'], 'refuse:droit_retire')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM decision_questions56109').fetchone()[0], 0)


# ------------------------------------------------------------------------------------------------ export, banc, montage
class Raccordement(unittest.TestCase):
    def test_F07_tables_declarees_dans_le_contrat_d_export(self):
        texte = (RACINE / 'agent' / 'export56108.py').read_text(encoding='utf-8')   # contrat complet : test_v56108_export
        for table in ('decisions56109', 'decision_questions56109', 'decision_recus56109'):
            self.assertIn("'%s'" % table, texte)

    def test_F07_un_seul_point_de_montage_aucun_second_orchestrateur(self):
        desk = (RACINE / 'agent' / 'desk.py').read_text(encoding='utf-8')
        self.assertEqual(desk.count('from .decision56109 import au_travail'), 1)
        lignes = desk.splitlines()
        ix = next(i for i, l in enumerate(lignes) if 'from .decision56109 import au_travail' in l)
        self.assertIn('for_job(self,row,_args)', lignes[ix - 2])               # après check_running, contrôle de mission et compétences du travail
        self.assertIn('self.perform(', lignes[ix + 1])
        source = (RACINE / 'agent' / 'decision56109.py').read_text(encoding='utf-8')
        for interdit in ('import_module', 'getattr(sys.modules', 'eval(', 'exec(', 'subprocess', 'enqueue('):
            self.assertNotIn(interdit, source)
        model = (RACINE / 'agent' / 'model.py').read_text(encoding='utf-8')
        self.assertEqual(model.count("not self.cfg.get('relance_interdite56109')"), 1)
        self.assertEqual(model.count('interruptible(lambda:self.http.json('), 4)

    def test_F07_banc_comparer_et_executer_sur_confirmation_seulement(self):
        spec = importlib.util.spec_from_file_location('banc_decision56109', RACINE / 'scripts' / 'banc-decision56109.py')
        banc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(banc)
        cas = banc.lire(RACINE / 'tests' / 'decision56109-cas-amorcage.jsonl')
        self.assertEqual(len(cas), 24)
        self.assertEqual({c['attendu']['intention'] for c in cas}, set(dec.INTENTIONS))
        ref = [{'id': c['id'], 'decision': {'statut': 'propose', 'intention': c['attendu']['intention'], 'action': c['attendu']['actions'][0]}, 'duree_ms': 10}
               if c['attendu']['intention'] != 'indetermine' else {'id': c['id'], 'decision': {'statut': 'indetermine', 'intention': 'indetermine', 'action': 'indetermine'}, 'duree_ms': 10}
               for c in cas]
        cand = [{**r, 'decision': {'statut': 'indetermine', 'intention': 'indetermine', 'action': 'indetermine'}, 'duree_ms': 20} for r in ref]
        rapport = banc.comparer(cas, ref, cand)
        self.assertEqual((rapport['reference']['taux_correct'], rapport['candidat']['corrects']), (1.0, 4))
        with self.assertRaises(ValueError):
            banc.mesurer(cas, ref[:-1])                                        # couverture différente refusée
        with tempfile.TemporaryDirectory() as d:
            sortie_ = Path(d) / 'c.jsonl'
            with self.assertRaises(SystemExit), patch.object(banc, 'executer', side_effect=AssertionError('jamais sans confirmation')):
                banc.main(['executer', '--cas', str(RACINE / 'tests' / 'decision56109-cas-amorcage.jsonl'), '--modele', 'fictif', '--sortie', str(sortie_)])
            self.assertFalse(sortie_.exists())

    def test_F07_transport_de_reference_boucle_locale_seulement(self):
        spec = importlib.util.spec_from_file_location('banc_decision56109', RACINE / 'scripts' / 'banc-decision56109.py')
        banc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(banc)
        for url in ('https://api.example.test', 'http://localhost:11434', 'http://127.0.0.1:11434/path', 'http://127.0.0.1:11434@autre.example.test'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                banc.OllamaLocal('qwen', url=url)
        banc.OllamaLocal('qwen', url='http://[::1]:11434')
        self.assertFalse(hasattr(dec, 'OllamaLocal'))                          # aucun transport propre dans le produit
        self.assertNotIn('/api/chat', (RACINE / 'agent' / 'decision56109.py').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
