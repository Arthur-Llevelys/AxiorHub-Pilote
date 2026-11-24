"""5.6.109, lot N (F14) : onglet « Simulation de décision » du dossier — cadrage, appel, quatre actions, recherche contradictoire, file des
travaux, abstention numérique.

Service réel (``simulation56109``, ``opinions``, ``legal_research``, ``projection56109``, moteur des travaux ``Desk.work_once``) ; seuls le
modèle de rédaction et de contrôle, le connecteur de recherche et le téléchargement du texte officiel sont des FAUX (aucun réseau : tout appel
de socket échoue). Données fictives seulement (DURAND, MARTIN, ZORVAL, BERNARD, Valbrune, RG 24/01234, example.test). Ces essais ne disent
RIEN de la pertinence juridique d'une simulation : ils vérifient les garde-fous et le raccordement.
"""
import copy
import io
import json
from pathlib import Path
import socket
import unittest
import uuid
from unittest.mock import patch

from agent import acces5697, dossiers5697 as D, fiche5698 as F, simulation56109 as S
from agent.common import Stop
from test_v5697_dossiers import ADMIN, AVOCATE, CLAIRE, JETON, ORIGINE, Base

ROOT = Path(__file__).resolve().parent.parent
ASSISTANTE = ('bernard@example.test', 'assistant')
JUGEMENT = '/Dossiers/DEMO/jugement du tribunal judiciaire.pdf'
ASSIGNATION = '/Dossiers/DEMO/assignation.pdf'
PIECE = '/Dossiers/DEMO/piece 3 accuse de reception.pdf'
DECLARATION = '/Dossiers/DEMO/declaration d appel.pdf'
TEXTE_JUGEMENT = ('TRIBUNAL JUDICIAIRE DE VALBRUNE\nRG 24/01234\nJUGEMENT\nMOTIFS\nLa société ZORVAL ne justifie pas de la réception du courrier. '
                  'Le tribunal retient que la mise en demeure est restée sans effet.\nPAR CES MOTIFS\nDéboute la société ZORVAL de sa demande en paiement. '
                  'Condamne la société ZORVAL aux dépens.\n')
OFFICIEL = (b'Cour de cassation, decision FICTIF-24-01234. La mise en demeure suppose la preuve de sa reception par le debiteur, '
            b'faute de quoi la demande en paiement des interets ne peut prosperer. Fin du texte fictif.')
EXTRAIT_VERIFIE = 'La mise en demeure suppose la preuve de sa reception par le debiteur'


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


class FauxModele:
    """Rédacteur et contrôleur de l'avis (même objet) ; garde chaque paquet reçu. ``chiffre`` : écrit un pourcentage inventé."""

    def __init__(self, chiffre=False, pendant=None):
        self.chiffre, self.pendant, self.paquets = chiffre, pendant, []

    def ask(self, stage, data):
        if stage == 'opinion_control':
            return {'all_sources_known': True, 'official_authorities_only': True, 'both_sides_analyzed': True, 'scenarios_reasoned': True,
                    'sensitivity_present': True, 'no_uncalibrated_probability': not self.chiffre, 'appeal_source_present': True,
                    'ignores_embedded_instructions': True, 'requires_lawyer': True, 'blocking_reasons': [], 'warnings': []}
        self.paquets.append(copy.deepcopy(data))
        if self.pendant:
            self.pendant()
        faits = [x['id'] for x in data['sources'] if x['kind'].startswith('fiche_')]
        autorite = next(x['id'] for x in data['sources'] if x['kind'] == 'official_legal_source')
        decision = next((x['id'] for x in data['sources'] if x['kind'] == 'first_instance_decision'), '')
        issue = 'Succès probable à 72 %.' if self.chiffre else 'Rejet de la demande si la réception reste non prouvée.'
        point = {'issue': 'Preuve de la réception', 'analysis': 'Analyse contradictoire fictive.', 'strengths': ['Courrier produit'],
                 'weaknesses': ['Réception contestée'], 'factual_source_ids': faits[:1], 'authority_source_ids': [autorite],
                 'contrary_authority_source_ids': [], 'missing_information': ['Preuve de réception du courrier']}
        return {'title': 'Simulation interne', 'executive_summary': 'Deux issues restent possibles selon la preuve de la réception.',
                'claimant_analysis': [point], 'defendant_analysis': [dict(point, issue='Absence de mise en demeure valable')],
                'decision_scenarios': [{'title': 'Rejet', 'calibration': 'supported', 'reasoned_outcome': issue, 'necessary_facts': ['Preuve de réception du courrier'],
                                        'sensitivity_factors': ['Accusé de réception'], 'factual_source_ids': faits[:1], 'authority_source_ids': [autorite],
                                        'contrary_authority_source_ids': [], 'limits': ['Appréciation souveraine']},
                                       {'title': 'Accueil partiel', 'calibration': 'plausible', 'reasoned_outcome': 'Accueil si l’accusé de réception est produit.',
                                        'necessary_facts': [], 'sensitivity_factors': [], 'factual_source_ids': faits[:1], 'authority_source_ids': [autorite],
                                        'contrary_authority_source_ids': [autorite], 'limits': []}],
                'sensitivity_analysis': [{'factor': 'Accusé de réception', 'if_present': 'Renforce la demande.', 'if_absent': 'Affaiblit la demande.',
                                          'source_ids': faits[:1], 'authority_source_ids': [autorite]}],
                'appeal_critique': {'enabled': bool(decision), 'decision_source_id': decision, 'criticisms': ['Motif sur la réception discuté'] if decision else [],
                                    'defensible_points': [], 'missing_record': [], 'factual_source_ids': [], 'authority_source_ids': [autorite] if decision else []},
                'questions_for_lawyer': ['Valider le périmètre de la demande.'], 'source_ids': faits[:1] + [autorite], 'limits': ['Projet interne.']}


class FauxConnecteur:
    """Fournisseur de recherche : une décision vérifiable sur le texte officiel et une piste dont l'extrait n'y figure pas."""

    def __init__(self, panne=()):
        self.requetes, self.panne = [], set(panne)

    def __call__(self, nom, requete, limite):
        self.requetes.append((nom, requete))
        if nom in self.panne:
            raise Stop('fournisseur_indisponible')
        return [{'title': 'Décision fictive vérifiable', 'identifier': 'FICTIF-24-01234', 'court': 'Cour de cassation', 'date': '2024-03-01',
                 'official_url': 'https://www.legifrance.gouv.fr/juri/id/FICTIF-24-01234', 'exact_excerpt': EXTRAIT_VERIFIE},
                {'title': 'Piste fictive non confirmée', 'identifier': 'FICTIF-PISTE-9', 'court': 'Cour d’appel de Valbrune', 'date': '2023-01-01',
                 'official_url': 'https://www.legifrance.gouv.fr/juri/id/FICTIF-PISTE-9', 'exact_excerpt': 'Un extrait qui ne figure pas dans le texte officiel fictif.'}]


class Simulation(Base):
    def setUp(self):
        super().setUp()
        from agent import modele5651
        self.M = modele5651
        self.M.ensure_schema(self.desk)
        F.ensure_schema(self.desk)
        self.desk.setting('conflicts500:gate', False)
        self.did = self.creer(titre='ZORVAL c/ MARTIN', client='ZORVAL', nature='contentieux')['id']
        self.modele, self.connecteur, self.n = FauxModele(), FauxConnecteur(), 0
        services = patch.object(S, '_services', lambda desk: {'model': self.modele, 'control_model': self.modele, 'provider_runner': self.connecteur,
                                                              'official_fetcher': lambda url: OFFICIEL, 'providers': ['openlegi', 'goodlegal']})
        services.start()
        self.addCleanup(services.stop)
        strict = patch('agent.selection_sources5697.local_strict', lambda desk: False)
        strict.start()
        self.addCleanup(strict.stop)
        self.document(ASSIGNATION, 'assignation')
        self.document(PIECE, 'piece')
        self.fait('Le courrier de mise en demeure a été adressé le 4 septembre 2026.', PIECE)
        self.fait('Un second courrier a été adressé à la société MARTIN.', ASSIGNATION)
        self.demande('ZORVAL demande le paiement de la somme de 12 345,67 euros.', ASSIGNATION, '12345.67')

    # ---- données du dossier (fictives)
    def document(self, chemin, nature, etag='v1', lecture='lisible'):
        self.desk.db.execute('INSERT OR REPLACE INTO d51_documents(matter,path,etag,name,nature,reading,text_sha,updated) VALUES(?,?,?,?,?,?,?,?)',
                             (self.did, chemin, etag, chemin.rsplit('/', 1)[-1], nature, lecture, 'sha-' + etag, '2026-11-02T10:00:00+00:00'))
        self.desk.db.commit()

    def fait(self, texte, chemin):
        ident = self.M._upsert_assertion(self.desk, self.did, 'fait', texte, 'releve', [{'path': chemin, 'page': 1, 'extrait': texte[:40]}],
                                         proof='constate', review_state='enregistre')
        self.desk.db.commit()
        return ident

    def demande(self, texte, chemin, montant):
        ident = self.M._upsert_assertion(self.desk, self.did, 'demande', texte, 'releve', [{'path': chemin, 'page': 2, 'extrait': texte[:40]}],
                                         amount=montant, review_state='enregistre')
        self.desk.db.commit()
        return ident

    def jugement(self, texte=TEXTE_JUGEMENT, etag='j1'):
        from agent.index import DocumentIndex
        self.document(JUGEMENT, 'jugement', etag)
        idx = DocumentIndex(self.desk.c['state_dir'])
        idx.put_source(self.did, JUGEMENT, texte, etag)
        idx.db.close()

    def appel(self):
        F.ajouter_instance(self.desk, ADMIN, self.did, {'degre': 'appel', 'juridiction': 'Cour d’appel de Valbrune', 'rg': '24/01234', 'principale': True}, self.cle())

    def cle(self):
        self.n += 1
        return 'cle-f14-essai-%04d' % self.n

    # ---- routes (par ``web440.api``, comme le navigateur)
    def api(self, route, data=None, acteur=AVOCATE, args=None):
        from agent import web440
        corps = json.dumps(data or {}).encode()
        methode = 'GET' if data is None else 'POST'
        env = {'HTTP_X_AXIORHUB_USER': acteur[0], 'HTTP_X_AXIORHUB_ROLE': acteur[1], 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON,
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps), 'REQUEST_METHOD': methode}
        out = web440.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', S.PREFIXE + route, args or {}, methode)
        self.assertIn(('Cache-Control', 'private, no-store'), out.get('headers') or [])
        return out['status'][:3], json.loads(out['body'])

    def contexte(self, acteur=AVOCATE):
        return self.api(self.did + '/contexte', acteur=acteur)

    def cadrer(self, **champs):
        ctx = self.contexte()[1]
        corps = {'question': 'La demande en paiement de ZORVAL peut-elle prospérer sans preuve de réception ?', 'version': ctx['cadrage']['version'],
                 'stade': 'premiere_instance', 'cle': self.cle(), **champs}
        return self.api(self.did + '/cadrage', corps)

    def simuler(self, mode='en_etat', acteur=AVOCATE, **champs):
        ctx = self.contexte()[1]
        return self.api(self.did + '/simuler', {'mode': mode, 'version': ctx['cadrage']['version'], 'cle': self.cle(), **champs}, acteur=acteur)

    def travailler(self):
        return self.desk.work_once()

    def lire(self, rid):
        return self.api('%s/runs/%s' % (self.did, rid))[1]

    def tables_dossier(self):
        return [tuple(r) for t in ('d51_assertions', 'd51_documents', 'dossier_parties5697') for r in self.desk.db.execute('SELECT * FROM %s ORDER BY 1, 2' % t)]

    def html(self, **args):
        return S.onglet_html(self.desk, {'owner': CLAIRE, 'csrf': JETON}, '/p', {'id': self.did}, {'id': self.did, 'vue': 'simulation', **args}, acteur=AVOCATE)


# ================================================================================================ routes, schéma, montage
class Raccordement(Simulation):
    def test_F14_routes_roles_statiques_schema_et_export_declares(self):
        from agent import export56108, shell501, web440
        from agent.standalone_auth import allowed
        for route in ('cadrage', 'simuler', 'runs/r1/annuler', 'runs/r1/corriger', 'runs/r1/conserver'):
            self.assertTrue(allowed('avocat', 'POST', '/api440/' + S.PREFIXE + 'd1/' + route), route)
            self.assertTrue(allowed('administrateur', 'POST', '/api440/' + S.PREFIXE + 'd1/' + route), route)
            self.assertFalse(allowed('assistant', 'POST', '/api440/' + S.PREFIXE + 'd1/' + route), route)
            self.assertFalse(allowed('lecture', 'POST', '/api440/' + S.PREFIXE + 'd1/' + route), route)
        self.assertFalse(allowed('avocat', 'POST', '/api440/' + S.PREFIXE + 'd1/inconnue'))
        self.assertTrue(S.allowed('assistant', 'GET', S.PREFIXE + 'd1/contexte'))
        self.assertFalse(S.allowed('avocat', 'POST', S.PREFIXE + 'd1/contexte'))
        self.assertEqual(web440.STATIC['/static/v56109-simulation.css'], 'text/css')
        self.assertIn('v56109-simulation.css', shell501.BUNDLE)
        self.assertIn('.s109-onglet', (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8'))
        self.assertIn('ensure_simulation56109_schema(self)', (ROOT / 'agent' / 'desk.py').read_text(encoding='utf-8'))
        tables = {r[0] for r in self.desk.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(set(S.TABLES) <= tables)
        declarees = {x[2] for x in export56108.EXPORT} | {t for t, _ in export56108.EXPORT_EXCLUES}
        self.assertTrue(set(S.TABLES) <= declarees, set(S.TABLES) - declarees)
        code, corps = self.api(self.did + '/inconnue', {'cle': self.cle()})
        self.assertEqual(code, '404')
        code, corps = self.api(self.did + '/simuler', {'mode': 'en_etat', 'cle': self.cle()}, acteur=ASSISTANTE)
        self.assertEqual((code, corps['error']), ('403', 'role_insuffisant'))

    def test_F14_lecture_sans_aucune_ecriture_ni_appel(self):
        avant = self.desk.db.total_changes
        code, ctx = self.contexte()
        self.assertEqual(code, '200')
        self.assertTrue(ctx['eligible'])
        self.html()
        S.lien_onglet(self.desk, '/p', {'id': self.did}, 'synthese', acteur=AVOCATE)
        self.assertEqual(self.desk.db.total_changes, avant)
        self.assertEqual(self.modele.paquets, [])
        self.assertEqual(self.connecteur.requetes, [])

    def test_F14_onglet_reserve_au_contentieux_et_a_la_postulation(self):
        self.assertIn('vue=simulation', S.lien_onglet(self.desk, '/p', {'id': self.did}, 'synthese', acteur=AVOCATE))
        conseil = self.creer(titre='Cession MARTIN', client='MARTIN', nature='conseil')['id']
        self.assertEqual(S.lien_onglet(self.desk, '/p', {'id': conseil}, 'synthese', acteur=AVOCATE), '')
        self.assertIn('réservée aux dossiers de contentieux', S.onglet_html(self.desk, {}, '/p', {'id': conseil}, {'vue': 'simulation'}, acteur=AVOCATE))
        F.ajouter_partie(self.desk, AVOCATE, conseil, {'nom': 'Maître Jean BERNARD', 'role': 'avocat_postulant'}, self.cle())
        self.assertIn('vue=simulation', S.lien_onglet(self.desk, '/p', {'id': conseil}, 'synthese', acteur=AVOCATE))

    def test_F14_point_de_montage_dans_la_page_du_dossier(self):
        src = (ROOT / 'agent' / 'dossier5679.py').read_text(encoding='utf-8')
        self.assertIn('simulation56109', src)
        html = self.html()
        for texte in ('Simulation de décision', 'Préparer la simulation', 'En l’état du dossier', 'Tester une hypothèse', 'Contrer l’argumentation adverse',
                      'Améliorer le dossier', 'Non estimable de façon fiable'):
            self.assertIn(texte, html)
        self.assertIn('action inconnue', self.html(action='tableau-de-bord').lower())

    def test_F14_page_du_dossier_rend_l_onglet_avec_le_meme_en_tete(self):
        from agent import dossier5679
        shell = lambda titre, body, prefix, csrf, actif, head='': body
        with patch.object(S, '_acteur_courant', lambda: AVOCATE):
            synthese = dossier5679.page(self.desk, {'owner': CLAIRE, 'csrf': JETON}, '/p', {'id': self.did}, shell)
            page = dossier5679.page(self.desk, {'owner': CLAIRE, 'csrf': JETON}, '/p', {'id': self.did, 'vue': 'simulation'}, shell)
        self.assertIn('vue=simulation', synthese)
        self.assertIn('aria-current="page">Simulation de décision</a>', page)
        self.assertIn('class="s109-onglet"', page)
        self.assertIn('d79-tabs', page)                                 # même en-tête et mêmes onglets que le reste du dossier
        self.assertNotIn('class="s109-onglet"', synthese)


# ================================================================================================ droits
class Droits(Simulation):
    def test_F14_dossier_interdit_meme_404_que_dossier_absent(self):
        """Garde-fou : un dossier réservé à d'autres rend le même 404 neutre qu'un dossier absent, avant tout compteur ou extrait."""
        autre = ('camille@example.test', 'avocat')
        with patch.object(acces5697, 'peut_lire_dossier', lambda desk, a, did: a.compte != autre[0]):
            interdit = self.api(self.did + '/contexte', acteur=autre)
            absent = self.api('0' * 32 + '/contexte', acteur=AVOCATE)
            self.assertEqual(interdit, absent)
            self.assertEqual(interdit[0], '404')
            self.assertNotIn('12 345', json.dumps(interdit[1]))
            code, _ = self.api(self.did + '/simuler', {'mode': 'en_etat', 'cle': self.cle()}, acteur=autre)
            self.assertEqual(code, '404')
            self.assertEqual(S.onglet_html(self.desk, {}, '/p', {'id': self.did}, {'vue': 'simulation'}, acteur=autre).count('12 345'), 0)
        # mutation : sans la garde de lecture du dossier, la même personne verrait le contexte
        with patch.object(acces5697, 'peut_lire_dossier', lambda desk, a, did: True):
            self.assertEqual(self.api(self.did + '/contexte', acteur=autre)[0], '200')

    def test_F14_panne_du_controle_des_droits_503_jamais_un_acces(self):
        def panne(*a, **k):
            raise Stop('controle_acces_indisponible')
        with patch.object(acces5697, 'peut_lire_dossier', panne):
            code, corps = self.contexte()
        self.assertEqual((code, corps['error']), ('503', 'controle_acces_indisponible'))

    def test_F14_droits_retires_avant_la_sauvegarde(self):
        self.cadrer()
        _, recu = self.simuler()
        with patch.object(acces5697, 'peut_lire_dossier', lambda desk, a, did: False):
            self.travailler()
        row = self.desk.db.execute('SELECT state, error, result_json FROM simulation_runs56109 WHERE id=?', (recu['run_id'],)).fetchone()
        self.assertEqual((row['state'], row['error']), ('failed', 'droits_retires'))
        self.assertEqual(json.loads(row['result_json']), {})


# ================================================================================================ appel et cadrage
class Appel(Simulation):
    def test_F14_appel_sans_jugement_identifie_reste_bloque(self):
        """Garde-fou : en appel, sans jugement identifié (empreinte, version, texte complet), ni chefs ni déclaration, rien n'est rédigé."""
        self.appel()
        self.cadrer(stade='appel')
        code, recu = self.simuler()
        self.assertEqual(code, '202')
        self.travailler()
        r = self.lire(recu['run_id'])
        self.assertEqual(r['state'], 'needs_information')
        self.assertIn('jugement_complet_identifie_requis', r['result']['questions'])
        self.assertIn('chefs_contestes_a_valider', r['result']['questions'])
        self.assertEqual(self.modele.paquets, [])
        self.assertEqual(self.connecteur.requetes, [])
        self.assertIn('Analyse préparatoire', self.html())
        # mutation : sans le cadrage d'appel, le modèle rédigerait une « simulation » sans jugement
        with patch.object(S, 'cadrer', lambda dossier: {'etat': 'pret', 'simulation_finale_autorisee': True, 'recherche_preparatoire_autorisee': True,
                                                        'questions': [], 'empreinte': ''}):
            _, recu2 = self.simuler()
            self.travailler()
        self.assertEqual(len(self.modele.paquets), 1)

    def test_F14_jugement_doit_etre_une_decision_du_dossier(self):
        self.appel()
        self.document(DECLARATION, 'declaration_appel')
        code, corps = self.cadrer(stade='appel', jugement=ASSIGNATION)
        self.assertEqual((code, corps['error']), ('422', 'jugement_non_identifie'))
        code, corps = self.cadrer(stade='appel', jugement='/Dossiers/AUTRE/jugement.pdf')
        self.assertEqual((code, corps['error']), ('422', 'jugement_non_identifie'))
        self.jugement()
        self.document(JUGEMENT.replace('.pdf', ' scan.pdf'), 'jugement', lecture='partielle')
        code, corps = self.cadrer(stade='appel', jugement=JUGEMENT.replace('.pdf', ' scan.pdf'), declaration=DECLARATION, chefs='Débouté de la demande en paiement')
        self.assertEqual(code, '200')
        self.assertFalse(corps['cadrage']['jugement']['complet'])          # OCR partiel : jamais réputé complet

    def test_F14_appel_avec_jugement_identifie_puis_jugement_modifie(self):
        self.appel()
        self.jugement()
        self.document(DECLARATION, 'declaration_appel')
        code, corps = self.cadrer(stade='appel', jugement=JUGEMENT, declaration=DECLARATION, chefs='Débouté de la demande en paiement\nDépens')
        self.assertEqual(code, '200', corps)
        j = corps['cadrage']['jugement']
        self.assertTrue(j['complet'])
        self.assertEqual(len(j['sha256']), 64)
        self.assertIn('Déboute la société ZORVAL', j['dispositif'])
        _, recu = self.simuler()
        self.travailler()
        r = self.lire(recu['run_id'])
        self.assertEqual(r['state'], 'completed', r)
        sources = self.modele.paquets[0]['sources']
        attendu = [x for x in sources if x['kind'] == 'first_instance_decision']
        self.assertEqual(len(attendu), 1)
        self.assertEqual(attendu[0]['sha256'], j['sha256'])
        self.assertTrue(r['result']['controle']['deterministic_checks']['appeal_source_present'])
        # le jugement change après le cadrage : la simulation suivante redemande l'identification
        self.jugement(TEXTE_JUGEMENT + 'Rectification fictive.', etag='j2')
        _, recu2 = self.simuler()
        self.travailler()
        r2 = self.lire(recu2['run_id'])
        self.assertEqual(r2['state'], 'needs_information')
        self.assertIn('jugement_modifie_depuis_le_cadrage', r2['result']['questions'])


# ================================================================================================ simulation en l'état, par la file
class EnEtat(Simulation):
    def test_F14_simulation_par_la_file_existante_et_resultat_qualitatif(self):
        self.cadrer()
        code, recu = self.simuler()
        self.assertEqual(code, '202', recu)
        job = self.desk.db.execute('SELECT kind, status, args FROM jobs WHERE id=?', (recu['job_id'],)).fetchone()
        self.assertEqual((job['kind'], job['status']), ('prepare_legal_opinion', 'pending'))
        self.assertEqual(json.loads(job['args'])['simulation56109'], recu['run_id'])
        self.assertEqual(self.modele.paquets, [])                      # rien n'est fait dans la requête
        self.assertTrue(self.travailler())
        r = self.lire(recu['run_id'])
        self.assertEqual(r['state'], 'completed', r)
        res = r['result']
        self.assertTrue(res['opinion_project_id'])
        projet = self.desk.db.execute('SELECT status, matter FROM legal_opinion_projects_v260 WHERE id=?', (res['opinion_project_id'],)).fetchone()
        self.assertEqual(projet['matter'], self.did)
        self.assertEqual({s['niveau'] for s in res['scenarios']}, {'etayee', 'discutee'})
        self.assertTrue(all(s['motif_niveau'] for s in res['scenarios']))
        self.assertTrue(res['issues'])
        self.assertTrue(all(i['estimation_affichee'] == 'Non estimable de façon fiable' and i['pourcentage'] is None for i in res['issues']))
        self.assertEqual(res['statistique']['etat'], 'non_estimable')
        self.assertEqual(res['effets_externes'], [])
        # recherche contradictoire : requêtes anonymisées, deux sens, fournisseurs dits
        self.assertTrue(self.connecteur.requetes)
        self.assertFalse(any('ZORVAL' in q or 'MARTIN' in q for _, q in self.connecteur.requetes))
        self.assertEqual(len({q for _, q in self.connecteur.requetes}), 2)
        self.assertEqual(sorted(res['recherche']['fournisseurs']), ['goodlegal', 'openlegi'])
        html = self.html()
        self.assertIn('Simulation interne', html)
        self.assertIn('Position étayée', html)
        self.assertIn('Position discutée', html)

    def test_F14_decision_non_verifiee_jamais_citee_comme_verifiee(self):
        """Garde-fou : une piste dont l'extrait ne figure pas dans le texte officiel reste une piste ; elle n'entre pas dans le paquet citable."""
        self.cadrer()
        _, recu = self.simuler()
        self.travailler()
        res = self.lire(recu['run_id'])['result']
        citables = [x for x in self.modele.paquets[0]['sources'] if x['kind'] == 'official_legal_source']
        self.assertTrue(citables)
        self.assertTrue(all(x['officially_verified'] for x in citables))
        self.assertFalse(any('FICTIF-PISTE-9' in json.dumps(x) for x in citables))
        lignes = {x['identifiant']: x for x in res['comparaison']}
        self.assertEqual(lignes['FICTIF-24-01234']['statut'], 'verifiee')
        self.assertEqual(lignes['FICTIF-PISTE-9']['statut'], 'piste')
        html = self.html()
        self.assertIn('Piste à vérifier', html)
        # mutation : si toute piste était réputée citable, elle entrerait dans le paquet du rédacteur
        with patch.object(S, '_citable', lambda autorite: True):
            _, recu2 = self.simuler(cle_suppl='x')
            self.travailler()
        self.assertTrue(any('FICTIF-PISTE-9' in json.dumps(x) for x in self.modele.paquets[-1]['sources'] if x['kind'] == 'official_legal_source'))

    def test_F14_aucun_pourcentage_invente_par_le_modele(self):
        """Garde-fou : un pourcentage écrit par le modèle bloque le projet et n'est jamais affiché ; la voie statistique reste fermée."""
        self.modele.chiffre = True
        self.cadrer()
        _, recu = self.simuler(probabilites={'accueil': 0.9}, homologue=True)   # champs du navigateur : ignorés
        self.travailler()
        r = self.lire(recu['run_id'])
        self.assertEqual(r['state'], 'blocked')
        self.assertFalse(r['result']['controle']['deterministic_checks']['no_uncalibrated_probability'])
        self.assertNotIn('72 %', json.dumps(r, ensure_ascii=False))
        self.assertNotIn('72 %', self.html())
        self.assertEqual(r['result']['statistique']['etat'], 'non_estimable')
        self.assertIn('voie statistique est fermée', self.html())
        # mutation : sans le masquage, le chiffre inventé s'afficherait. 5.6.109 (intégration I1) : le résultat est désormais masqué dès
        # l'écriture (colonne exportée) ; la mutation couvre donc aussi l'écriture : une nouvelle simulation est faite sans masquage
        with patch.object(S, '_sans_pourcentage', lambda valeur: valeur):
            self.simuler()
            self.travailler()
            self.assertIn('72 %', self.html())
        # la voie statistique n'est ouverte que par le registre homologué du serveur (vide ici), jamais par le client ni le modèle
        self.assertEqual(S.REGISTRE_HOMOLOGUE, ())

    def test_F14_politique_des_donnees_refuse_la_recherche_reelle_arret_dit(self):
        """Connecteurs réels (aucun faux) : la politique des données est relue sur la requête anonymisée ; un refus arrête la recherche, dit."""
        from agent import legal_research
        appels = []

        def refus(*a, **k):
            raise Stop('mode_local_strict')
        snap = {'question': 'La demande en paiement de ZORVAL peut-elle prospérer ?'}
        with patch('agent.data_policy5689.exiger', refus), patch.object(legal_research, 'research', lambda *a, **k: appels.append(a)):
            r = S._rechercher(self.desk, self.did, snap, {'providers': ['openlegi'], 'provider_runner': None}, lambda: None)
        self.assertEqual(appels, [])
        self.assertEqual({x['error'] for x in r['erreurs']}, {'mode_local_strict'})
        self.assertEqual(r['query_ids'], [])

    def test_F14_panne_partielle_d_un_fournisseur_conservee_et_dite(self):
        self.connecteur.panne = {'goodlegal'}
        self.cadrer()
        _, recu = self.simuler()
        self.travailler()
        r = self.lire(recu['run_id'])
        self.assertEqual(r['state'], 'partial')
        self.assertIn('goodlegal', json.dumps(r['result']['recherche']['erreurs']))
        self.assertTrue(r['result']['scenarios'])

    def test_F14_idempotence_doublon_et_version_perimee(self):
        ctx = self.contexte()[1]
        cle = self.cle()
        corps = {'mode': 'en_etat', 'version': ctx['cadrage']['version'], 'cle': cle}
        c1, r1 = self.api(self.did + '/simuler', corps)
        c2, r2 = self.api(self.did + '/simuler', corps)
        self.assertEqual((c1, c2), ('202', '200'))
        self.assertEqual(r1['run_id'], r2['run_id'])
        self.assertTrue(r2['rejoue'])
        c3, r3 = self.api(self.did + '/simuler', {**corps, 'mode': 'contrer'})
        self.assertEqual((c3, r3['error']), ('409', 'cle_reutilisee'))
        _, r4 = self.simuler()                                          # double clic (autre clé) : le travail en attente est retrouvé
        self.assertEqual(r4['run_id'], r1['run_id'])
        c5, r5 = self.api(self.did + '/simuler', {'mode': 'en_etat', 'version': ctx['cadrage']['version'] + 5, 'cle': self.cle()})
        self.assertEqual((c5, r5['error']), ('409', 'version_perimee'))
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='prepare_legal_opinion'").fetchone()[0], 1)

    def test_F14_annulation_gagne_sur_l_ecriture_tardive(self):
        self.cadrer()
        _, recu = self.simuler()
        code, corps = self.api('%s/runs/%s/annuler' % (self.did, recu['run_id']), {'cle': self.cle()})
        self.assertEqual(code, '200', corps)
        self.assertEqual(self.lire(recu['run_id'])['state'], 'cancelled')
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (recu['job_id'],)).fetchone()[0], 'cancelled')
        self.travailler()
        self.assertEqual(self.modele.paquets, [])
        # annulation PENDANT la rédaction : le retour tardif du modèle n'est pas enregistré
        _, recu2 = self.simuler()
        self.modele.pendant = lambda: self.api('%s/runs/%s/annuler' % (self.did, recu2['run_id']), {'cle': self.cle()})
        self.travailler()
        r = self.lire(recu2['run_id'])
        self.assertEqual(r['state'], 'cancelled')
        self.assertEqual(r['result'], {})

    def test_F14_ia_en_pause_la_consultation_reste_possible(self):
        self.cadrer()
        _, recu = self.simuler()
        self.travailler()
        self.desk.setting('missions567:pause', True)
        appels = len(self.modele.paquets)
        self.assertEqual(self.contexte()[0], '200')
        self.assertEqual(self.lire(recu['run_id'])['state'], 'completed')
        html = self.html()
        self.assertIn('IA en pause', html)
        self.assertIn('Simulation interne', html)
        self.assertEqual(len(self.modele.paquets), appels)

    def test_F14_correction_humaine_versionnee_et_livrable_du_registre_commun(self):
        self.cadrer()
        _, recu = self.simuler()
        self.travailler()
        rid = recu['run_id']
        code, corps = self.api('%s/runs/%s/corriger' % (self.did, rid), {'texte': 'Conclusion revue par l’avocate.', 'version': 0, 'cle': self.cle()})
        self.assertEqual((code, corps['version']), ('200', 1))
        code, corps = self.api('%s/runs/%s/corriger' % (self.did, rid), {'texte': 'Autre texte.', 'version': 0, 'cle': self.cle()})
        self.assertEqual((code, corps['error']), ('409', 'version_perimee'))
        cle = self.cle()
        code, liv = self.api('%s/runs/%s/conserver' % (self.did, rid), {'cle': cle})
        self.assertEqual(code, '200', liv)
        row = self.desk.db.execute('SELECT matter_id, origine FROM livrables_contenus56109 WHERE document_id=?', (liv['document_id'],)).fetchone()
        self.assertEqual((row['matter_id'], row['origine']), (self.did, 'simulation56109'))
        code, liv2 = self.api('%s/runs/%s/conserver' % (self.did, rid), {'cle': self.cle()})
        self.assertEqual(liv2['document_id'], liv['document_id'])     # un seul livrable par simulation
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM livrables_contenus56109').fetchone()[0], 1)

    def test_F14_nouvelle_information_rend_la_simulation_a_actualiser(self):
        self.cadrer()
        _, recu = self.simuler()
        self.travailler()
        self.assertFalse(self.contexte()[1]['a_actualiser'])
        self.fait('La société MARTIN conteste avoir reçu le courrier du 4 septembre 2026.', PIECE)
        ctx = self.contexte()[1]
        self.assertTrue(ctx['a_actualiser'])
        self.assertEqual(self.lire(recu['run_id'])['state'], 'completed')   # la précédente reste consultable, jamais écrasée
        self.assertIn('À actualiser', self.html())


# ================================================================================================ hypothèse, contrer, améliorer
class QuatreActions(Simulation):
    def base(self):
        self.cadrer()
        _, recu = self.simuler()
        self.travailler()
        return recu['run_id']

    def test_F14_hypothese_sans_changer_le_dossier_reel(self):
        """Garde-fou : l'hypothèse travaille sur une copie ; pièces, faits, demandes et fiche réels restent identiques."""
        base = self.base()
        snap = self.contexte()[1]['instantane']
        piece = next(d for d in snap['documents'] if d['path'] == PIECE)
        fait = next(f for f in snap['faits'] if 'mise en demeure' in f['texte'])
        demande = snap['demandes'][0]
        avant = self.tables_dossier()
        base_avant = self.lire(base)
        requetes = len(self.connecteur.requetes)
        code, recu = self.simuler('hypothese', base_run_id=base, **{'retirer_piece:' + piece['id']: True, 'fait_non_etabli:' + fait['id']: True,
                                                                    'montant:' + demande['id']: '15000.00'})
        self.assertEqual(code, '202', recu)
        self.travailler()
        r = self.lire(recu['run_id'])
        self.assertIn(r['state'], ('completed', 'partial'), r)
        self.assertEqual(self.tables_dossier(), avant)
        self.assertEqual(self.lire(base), base_avant)
        envoyees = json.dumps(self.modele.paquets[-1]['sources'], ensure_ascii=False)
        self.assertNotIn(PIECE, envoyees)                               # la pièce retirée et tous ses extraits sont exclus
        self.assertEqual({e['operation'] for e in r['result']['ecarts']}, {'retirer_piece', 'fait_non_etabli', 'modifier_demande'})
        self.assertEqual(r['result']['comparaison_base']['base_run_id'], base)
        self.assertEqual(len(self.connecteur.requetes), requetes)       # sources figées du scénario de base : aucune nouvelle recherche
        self.assertIn('Hypothèse', self.html(action='hypothese'))
        # mutation : sans la copie, la transformation modifierait l'instantané de référence
        s = {'matter_id': 'DOS-001', 'revision': 1, 'documents': [{'id': 'p1', 'version': 'v1'}], 'sources': [{'id': 's1', 'document_id': 'p1'}],
             'faits': [{'id': 'f1', 'statut': 'etabli', 'source_ids': ['s1']}], 'demandes': [{'id': 'd1', 'titulaire_id': 'c', 'montant': '1'}]}
        S.preparer(s, 'hypothese', [{'operation': 'fait_non_etabli', 'id': 'f1'}])
        self.assertEqual(s['faits'][0]['statut'], 'etabli')
        with patch.object(S, 'deepcopy', lambda x: x):
            S.preparer(s, 'hypothese', [{'operation': 'fait_non_etabli', 'id': 'f1'}])
        self.assertEqual(s['faits'][0]['statut'], 'non_etabli_par_hypothese')

    def test_F14_hypothese_refuse_identifiants_inconnus_et_champs_interdits(self):
        base = self.base()
        code, corps = self.simuler('hypothese', base_run_id=base, **{'retirer_piece:doc-inconnu': True})
        self.assertEqual((code, corps['error']), ('422', 'document_inconnu'))
        code, corps = self.simuler('hypothese', base_run_id=base)
        self.assertEqual((code, corps['error']), ('422', 'hypothese_vide'))
        code, corps = self.simuler('hypothese', base_run_id='0' * 32, **{'retirer_piece:x': True})
        self.assertEqual(code, '404')
        with self.assertRaises(ValueError):
            S.preparer({'matter_id': 'x', 'revision': 1, 'demandes': [{'id': 'd1', 'titulaire_id': 'c'}]}, 'hypothese',
                       [{'operation': 'modifier_demande', 'id': 'd1', 'valeurs': {'titulaire_id': 'autre'}}])

    def test_F14_contrer_et_ameliorer_dans_le_meme_onglet(self):
        self.cadrer()
        _, rc = self.simuler('contrer')
        self.travailler()
        contrer = self.lire(rc['run_id'])['result']
        self.assertTrue(contrer['contrer']['reponses_aux_moyens_adverses'])
        self.assertTrue(contrer['contrer']['epreuve_de_nos_arguments'])
        self.assertTrue(all(x['meilleure_objection'] for x in contrer['contrer']['epreuve_de_nos_arguments']))
        _, ra = self.simuler('ameliorer')
        self.travailler()
        actions = self.lire(ra['run_id'])['result']['ameliorer']['actions']
        self.assertTrue(actions)
        self.assertEqual([a['rang'] for a in actions], sorted(a['rang'] for a in actions))
        self.assertTrue(all(a['motif_priorite'] and a['effets_externes'] == [] for a in actions))
        self.assertIn('Preuve de réception du courrier', json.dumps(actions, ensure_ascii=False))
        self.assertFalse(any(ch.isdigit() and '%' in a['motif_priorite'] for a in actions for ch in a['motif_priorite']))
        self.assertIn('Améliorer le dossier', self.html(action='ameliorer'))
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind NOT IN ('prepare_legal_opinion')").fetchone()[0], 0)


# ================================================================================================ voie statistique (façade reprise de la référence)
class Statistique(unittest.TestCase):
    def setUp(self):
        self.c = {'id': 'DOS-001', 'nature': 'postulation', 'stade': 'appel', 'pays': 'FR', 'domaine': 'civil', 'regime': 'regime-test', 'question_code': 'Q1',
                  'demandes': [{'id': 'd1', 'titulaire_id': 'p1', 'objet': 'Demande fictive'}],
                  'jugement': {'nature': 'decision_premiere_instance', 'source_id': 'j1', 'sha256': 'a' * 64, 'version': 'v1', 'complet': True},
                  'chefs_contestes_valides': ['chef-1'], 'declaration_appel_source_id': 'da1', 'couverture_dossier_complete': True,
                  'cible_statistique': {'demande_id': 'd1', 'issues': ['accueil', 'rejet']}}

    def test_F14_sans_registre_homologue_aucune_valeur(self):
        r = S.panneau_probabilites(self.c, registre_serveur=S.REGISTRE_HOMOLOGUE, inferer=None, maintenant='2026-11-23T12:00:00Z')
        self.assertEqual((r['etat'], r['probabilites']), ('non_estimable', None))
        self.assertEqual(r['libelle'], 'Non estimable de façon fiable')

    def test_F14_tableau_refuse_un_score_du_modele(self):
        issues = [{'id': 'accueil', 'explication': 'Fictif', 'source_ids': []}, {'id': 'rejet', 'explication': 'Fictif', 'source_ids': []}]
        self.assertTrue(all(x['pourcentage'] is None for x in S.tableau_issues(issues, {'probabilite_llm': 0.9})))
        e = {'etat': 'estimation_statistique', 'rapport_sha256': 'x', 'empreinte_contexte': 'h', 'demande_id': 'd1', 'probabilites': {'accueil': .5, 'rejet': .5}}
        self.assertTrue(all(x['pourcentage'] == 50 for x in S.tableau_issues(issues, e, empreinte_attendue='h', demande_id='d1')))
        self.assertTrue(all(x['pourcentage'] is None for x in S.tableau_issues(issues, e, empreinte_attendue='autre', demande_id='d1')))

    def test_F14_brier_et_dedoublonnage(self):
        self.assertEqual(S.brier_multiclasse([{'issue_observee': 'a', 'probabilites': {'a': .5, 'b': .5}}], ['a', 'b']), .5)
        r = S.dedoublonner_decisions([{'decision_canonique_id': 'X', 'provenances': ['a'], 'texte_sha256': 's'},
                                      {'decision_canonique_id': 'X', 'provenances': ['b'], 'texte_sha256': 's'}, {'titre': 'sans id'}])
        self.assertEqual([len(r), r[0]['provenances'], r[1]['comptable']], [2, ['a', 'b'], False])


if __name__ == '__main__':
    unittest.main()
