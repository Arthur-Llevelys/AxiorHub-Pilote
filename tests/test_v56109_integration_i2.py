"""5.6.109, intégration I2 : raccords fonctionnels entre lots (éditeur de règles à quatre conditions, durée des appels dans la préfacturation,
bandeau de réanalyse et repères de la fiche du dossier, compteurs de consommation renvoyés à la section unique, guide et libellés Bland,
propositions de l'analyse déposées sur la fiche, coûts des documents étrangers au registre de consommation).

Aucun réseau (tout appel de socket échoue), aucun modèle ; données fictives seulement (DURAND, MARTIN, DOS-001, example.test)."""
import json
import socket
import unittest
import uuid
from html.parser import HTMLParser
from unittest.mock import patch

from agent import catalogue56109 as C
from test_v56109_honoraires import CONTENU, CatalogueBase
from test_v5697_dossiers import ADMIN


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


class _Formulaire(HTMLParser):
    """Ce qu'un navigateur enverrait pour un formulaire rendu : champs texte et cachés, cases cochées, option choisie des listes."""

    def __init__(self):
        super().__init__()
        self.valeurs, self._liste, self._premiere = {}, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'input' and a.get('name'):
            if a.get('type') == 'checkbox':
                if 'checked' in a:
                    self.valeurs[a['name']] = 'on'
            else:
                self.valeurs[a['name']] = a.get('value') or ''
        elif tag == 'select':
            self._liste, self._premiere = a.get('name'), True
        elif tag == 'option' and self._liste:
            if self._premiere or 'selected' in a:
                self.valeurs[self._liste] = a.get('value') or ''
            self._premiere = False

    def handle_endtag(self, tag):
        if tag == 'select':
            self._liste = None


def soumettre(html_):
    p = _Formulaire()
    p.feed(html_)
    return p.valeurs


# ------------------------------------------------------------------------------------------------ 1. éditeur de règles (lot D)
QUATRE = {'id': 'quatre-conditions', 'libelle': 'Divorce fictif', 'categorie': 'famille', 'type_mission': 'contentieux', 'mode': 'forfait',
          'fixe_ht_cents': 100000, 'tva_statut': 'confirme', 'tva_pct': '20', 'inclus': ['Projet'],
          'champs': {'enfants': {'type': 'booleen', 'question': 'Enfants ?'}, 'biens': {'type': 'booleen', 'question': 'Biens ?'},
                     'pension': {'type': 'booleen', 'question': 'Pension ?'}, 'rdv': {'type': 'entier','question': 'Rendez-vous ?'}},
          'regles': [{'id': 'complexe', 'libelle_client': 'Dossier complexe', 'action': {'type': 'ajout_fixe', 'montant_ht_cents': 50000},
                      'si': [{'champ': 'enfants', 'op': 'eq', 'valeur': True}, {'champ': 'biens', 'op': 'eq', 'valeur': True},
                             {'champ': 'pension', 'op': 'eq', 'valeur': True}, {'champ': 'rdv', 'op': 'ge', 'valeur': 3}]}]}


class I2Editeur(CatalogueBase):
    def test_F05_regle_importee_a_quatre_conditions_survit_au_formulaire(self):
        C.importer(self.desk, ADMIN, json.dumps({'prestations': [QUATRE]}), appliquer=True)
        v = next(x for x in C.lister(self.desk, ADMIN)['prestations'] if x['id'] == 'quatre-conditions')
        self.assertEqual(len(v['contenu']['regles'][0]['si']), 4)
        envoi = soumettre(C._editeur(self.desk, v, C.categories(self.desk)))
        self.assertEqual((envoi.get('regle_1_champ3'), envoi.get('regle_1_champ4'), envoi.get('regle_1_op4'), envoi.get('regle_1_valeur4')),
                         ('pension', 'rdv', 'ge', '3'))                                              # les quatre conditions sont affichées
        code, corps = self.api('offres/modifier', {**envoi, 'id': v['id'], 'version': v['version'], 'cle': uuid.uuid4().hex})
        self.assertEqual(code, '200', corps)
        apres = next(x for x in C.lister(self.desk, ADMIN)['prestations'] if x['id'] == 'quatre-conditions')
        self.assertEqual(apres['contenu']['regles'], v['contenu']['regles'])                          # rien n'est perdu à l'enregistrement

    def test_F05_formulaire_ajoute_une_troisieme_condition_et_refuse_une_cinquieme(self):
        ident = C.creer(self.desk, ADMIN, {**CONTENU, 'champ_2_id': 'biens', 'champ_2_question': 'Biens ?', 'champ_2_type': 'booleen',
                                           'regle_1_champ2': 'biens', 'regle_1_valeur2': 'oui', 'regle_1_champ3': 'enfants', 'regle_1_valeur3': 'non'})['id']
        regle = next(x for x in C.lister(self.desk, ADMIN)['prestations'] if x['id'] == ident)['contenu']['regles'][0]
        self.assertEqual([c['champ'] for c in regle['si']], ['enfants', 'biens', 'enfants'])
        cinq = dict(QUATRE, id='cinq-conditions')
        cinq['regles'] = [dict(QUATRE['regles'][0], si=QUATRE['regles'][0]['si'] + [{'champ': 'enfants', 'op': 'eq', 'valeur': False}])]
        err = {}
        C.normaliser(cinq, err)
        self.assertIn('regle_1_champ', err)                                                           # jamais une cinquième condition tronquée en silence


# ------------------------------------------------------------------------------------------------ 2. préfacturation (G1) et appels (G2)
from agent import gestion56109 as G                                                                    # noqa: E402
from test_v56109_prefacturation import MID, Socle                                                     # noqa: E402


class I2Prefacturation(Socle):
    def appel(self, ident, minutes=None, origine='inconnue', instant='2026-11-10T09:30:00+00:00', mid=MID):
        self.desk.db.execute('INSERT INTO appels56109(id,cabinet,sens,instant_utc,zone,interlocuteur,matter_id,duree_minutes,duree_origine,auteur,cree_le,maj_le) '
                             "VALUES(?,'cabinet','entrant',?,'Europe/Paris','DURAND (fictif)',?,?,?,'claire@example.test',?,?)",
                             (ident, instant, mid, minutes, origine, instant, instant))
        self.desk.db.commit()

    def test_G01_duree_des_appels_branchee_dans_la_collecte(self):
        self.appel('appel-fictif-1', 25, 'saisie')
        self.appel('appel-fictif-2')                                                                  # durée inconnue
        self.appel('appel-fictif-3', 0, 'saisie')                                                     # zéro : jamais une durée nulle
        self.appel('appel-fictif-4', 15, 'mesuree', instant='2026-10-01T09:00:00+00:00')              # hors période
        self.collecter()
        par = {t: l for l in self.lignes() for t in l['traces']}
        self.assertEqual((par['appel|appel-fictif-1']['minutes'], par['appel|appel-fictif-1']['statut']), (25, 'a_valider'))
        self.assertEqual((par['appel|appel-fictif-2']['minutes'], par['appel|appel-fictif-2']['statut']), (None, 'a_completer'))
        self.assertEqual((par['appel|appel-fictif-3']['minutes'], par['appel|appel-fictif-3']['statut']), (None, 'a_completer'))
        self.assertNotIn('appel|appel-fictif-4', par)
        self.desk.db.execute("UPDATE appels56109 SET duree_minutes=10, duree_origine='saisie' WHERE id='appel-fictif-2'")
        self.desk.db.commit()
        self.collecter()                                                                              # relevé rejoué : la durée complétée remonte, sans doublon
        lignes = [l for l in self.lignes() if any(t.startswith('appel|') for t in l['traces'])]
        self.assertEqual(len(lignes), 3)
        self.assertEqual({t: l['minutes'] for l in lignes for t in l['traces']}['appel|appel-fictif-2'], 10)
        self.assertEqual(self.entrees(), 0)                                                           # aucun temps enregistré sans validation

    def test_G01_mutation_sans_le_raccord_les_appels_resteraient_invisibles(self):
        self.appel('appel-fictif-1', 25, 'saisie')
        with patch.object(G, '_collecter_appels', lambda *a: 0):
            self.collecter()
        self.assertFalse([l for l in self.lignes() if any(t.startswith('appel|') for t in l['traces'])])


# ------------------------------------------------------------------------------------------------ 3. fiche du dossier rendue (lots A, G1, G2, G3, N)
from test_v5697_dossiers import JETON, ORIGINE, Base as BaseDossiers                                 # noqa: E402

# Repères HTML auxquels les lots s'accrochent, par onglet de la page Dossier réellement servie (``web440.route``) : si l'un disparaît,
# le bloc correspondant ne s'affiche plus — ce test échoue.
REPERES = {
    '': ('class="a109-bandeau"', 'Analyser le dossier', 'Simulation de décision', '>Correspondances<', '>Agenda et tâches<'),
    'travail': ('b53-matter-block', 'id="g109-finances"', 'Fonds, frais et débours', 'id="g3-carpa"', 'RIB CARPA', 'class="a109-bandeau"'),
    'juridiction': ('id="g3-suites"', 'Suites et exécution'),
    'echanges': ('rl109-ap-d', 'Partager avec le client', '>Appels<'),
    'agenda': ('rl109-rp-t', 'Remplacements et postulation', 'rl109-kit-t', 'Kit d’audience hors ligne'),
    'fichiers': ('id="a109-analyse"',),
    'simulation': ('Simulation de décision', 's109-'),
}


class I2Fiche(BaseDossiers):
    def setUp(self):
        super().setUp()
        from agent import dossiers5697 as D
        self.mid = self.creer(titre='DURAND c/ MARTIN', client='DURAND', nature='contentieux', juridiction='Tribunal judiciaire de Valbrune', rg='24/01234')['id']
        D.traiter_rangements(self.desk)

    def page(self, vue=''):
        from agent import i18n5631, shell501, web440
        env = {'HTTP_X_AXIORHUB_USER': 'cabinet', 'HTTP_X_AXIORHUB_ROLE': 'administrateur', 'REQUEST_METHOD': 'GET'}
        args = {'id': self.mid, **({'vue': vue} if vue else {})}
        shell501.set_context(self.f.c, dict(args), '/dossier')
        i18n5631.begin(env, self.f.c, 'cabinet', 'administrateur')
        self.addCleanup(i18n5631.begin, {}, self.f.c, '', 'administrateur')
        out = web440.route(env, self.f.c, {'csrf': JETON, 'origin': ORIGINE}, '/p', '/dossier', args, 'GET')
        self.assertEqual(out['status'][:3], '200', vue)
        return out['body'] if isinstance(out['body'], str) else out['body'].decode('utf-8')

    def test_F03_G01_G05_G06_G16_F14_reperes_des_lots_sur_la_page_rendue(self):
        for vue, reperes in REPERES.items():
            html_ = self.page(vue)
            for r in reperes:
                self.assertIn(r, html_, 'repère « %s » absent de l’onglet %s' % (r, vue or 'synthese'))
            self.assertNotIn('affichage momentanément indisponible', html_, vue)
        self.assertNotIn('a109-bandeau', self.page('fichiers'))                                      # l'onglet Fichiers porte le bloc complet, pas les deux
        self.assertEqual(self.page().count('class="a109-bandeau"'), 1)

    def test_F03_bandeau_derriere_l_interrupteur_de_l_interface(self):
        from agent import interface56100
        interface56100.regler_cabinet(self.desk, False)
        self.assertNotIn('a109-bandeau', self.page())                                                 # ancienne présentation : sans le bandeau
        interface56100.regler_cabinet(self.desk, True)
        self.assertIn('a109-bandeau', self.page())


# ------------------------------------------------------------------------------------------------ 4. Paramètres › Modèles d'IA (lot M)
class I2Consommation(BaseDossiers):
    def pages(self):
        from agent import economie569, hybrid400_ui, ia540, parametres5621, workstation32_ui
        _, form, link = parametres5621._helpers('/p', JETON)
        p = patch.object(economie569, 'model_profile', lambda desk: {'available': False, 'models': []})   # aucun Ollama interrogé
        p.start()
        self.addCleanup(p.stop)
        ia = ia540.page(self.desk, '/p', lambda titre, corps, *a, **k: corps, JETON)
        return {'ia540': ia, 'economie': economie569.section_html(self.desk, '/p'),
                'poste': workstation32_ui.settings_page(self.desk, {'tab': 'ia'}, form, link),
                'hybride': hybrid400_ui.page(self.desk, {}, form, prefix='/p')}

    def test_F18_anciens_compteurs_renvoient_a_la_section_unique(self):
        p = self.pages()
        self.assertIn('id="consommation-couts"', p['ia540'])
        self.assertIn('href="/p/api440/m56109/conso/export"', p['ia540'])                             # préfixe transmis : plus d'adresse relative
        self.assertNotIn('href="api440/', p['ia540'])
        for nom, ancien in (('economie', 'Consommation des 7 derniers jours'), ('poste', 'Consommation externe du mois'), ('hybride', 'Dépense externe ce mois')):
            self.assertNotIn(ancien, p[nom], nom)                                                     # plus deux chiffres concurrents
            self.assertIn('Voir Consommation et coûts', p[nom], nom)
        self.assertIn('href="/p/parametres?tab=ia#consommation-couts"', p['economie'])
        self.assertIn('Régime économe', p['economie'])                                                # les réglages restent à leur place
        self.assertIn('Plafond mensuel en USD', p['poste'])
        self.assertIn('Estimer sans transmettre', p['hybride'])

    def test_F18_interrupteur_eteint_ancienne_presentation_inchangee(self):
        from agent import interface56100
        interface56100.regler_cabinet(self.desk, False)
        p = self.pages()
        self.assertIn('Consommation des 7 derniers jours', p['economie'])
        self.assertIn('Consommation externe du mois', p['poste'])
        self.assertIn('Dépense externe ce mois', p['hybride'])
        self.assertNotIn('Voir Consommation et coûts', p['economie'] + p['poste'] + p['hybride'])


# ------------------------------------------------------------------------------------------------ 5. Bland (lot B)
import html as _html                                                                                   # noqa: E402
import test_desk                                                                                      # noqa: E402
import test_v5697_agenda as _agenda                                                                   # noqa: E402  (module : ses tests ne sont pas recollectés ici)


class I2BlandPages(unittest.TestCase):
    setUp = test_desk.WebTests.setUp
    request = test_desk.WebTests.request

    def test_B09_rubrique_telephonie_bland_dans_l_index_du_guide(self):
        from agent import aide5631, fonctions5631 as reg
        self.assertEqual(reg.audit(), [])
        self.assertEqual(aide5631.check(), [])
        self.assertEqual(reg.FAMILLE['telephonie_bland'], 'accueil_telephonique')
        index = self.request('/guide-utilisation')['body']
        self.assertIn('/guide-utilisation?fonction=telephonie_bland', index)
        fiche = _html.unescape(self.request('/guide-utilisation', query='fonction=telephonie_bland')['body'])
        self.assertIn('Téléphonie par IA (Bland)', fiche)
        self.assertIn(aide5631.articles('fr')['telephonie_bland']['objective'], fiche)
        self.assertEqual(reg.for_path('/parametres', {'tab': 'connexions'}), 'parametres')       # l'aide contextuelle des Paramètres est inchangée

    def test_B04_page_d_accueil_mes_routines_mes_agents_ia(self):
        from agent.aujourdhui56101 import FRAGMENTS
        body = _html.unescape(self.request('/aujourdhui')['body'])
        for nom in FRAGMENTS:
            body += _html.unescape(json.loads(self.request('/api440/m56101/aujourdhui/fragment', query='nom=' + nom)['body']).get('html', ''))
        self.assertIn('Mes routines | Mes agents IA', body)
        self.assertNotIn('Comptes rendus des routines', body)                                         # libellé visible renommé
        self.assertNotIn('Routines du cabinet', body)


class I2BlandAssistant(BaseDossiers):
    def test_B06_intention_reconnue_sans_modele_et_rien_n_est_cree(self):
        from agent import bland56109 as B, integration
        for oui in ('Crée un agent de prise de rendez-vous pour mon cabinet', 'Peux-tu créer un agent de prise de RDV ?',
                    'Mets en place un agent pour les rendez-vous du cabinet'):
            self.assertTrue(B.intention_agent_rdv(oui), oui)
        for non in ('Quel est le prochain rendez-vous de M. DURAND ?', 'Crée un courrier pour MARTIN', 'Qui est l’agent immobilier du dossier DOS-001 ?',
                    'Crée un agent de prise de rendez-vous ' + 'x' * 300):
            self.assertFalse(B.intention_agent_rdv(non), non)
        avant = {t: self.desk.db.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0] for t in ('connexion_ops5652', 'bland56109_demandes', 'routines5652')}
        recu = integration.submit_question(self.desk, 'Crée un agent de prise de rendez-vous pour mon cabinet', auteur='cabinet')
        with patch('agent.model.Model', side_effect=AssertionError('aucun modèle')):
            out = integration.answer_question(self.desk, {'message': recu['message_id'], 'thread': recu['thread_id']})
        self.assertEqual(out['status'], 'done')
        reponse = self.desk.db.execute("SELECT content FROM assistant_messages WHERE thread_id=? AND role='assistant'", (recu['thread_id'],)).fetchone()[0]
        self.assertIn('Créer un agent de prise de rendez-vous', reponse)
        self.assertIn('Paramètres › Connexions API', reponse)                                         # connecteur non prêt : relier Bland d'abord
        self.assertEqual({t: self.desk.db.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0] for t in avant}, avant)   # rien n'est créé ni préparé
        with patch.object(B, 'utilisable', lambda desk, row=None: True):
            self.assertIn('Aujourd’hui › Mes routines | Mes agents IA', B.reponse_intention(self.desk, 'Crée un agent de prise de rendez-vous')['answer'])


class I2BlandRappel(_agenda.Base):
    env, appel = _agenda.Routes.env, _agenda.Routes.appel

    def test_B10_mention_rappel_envoye_sur_la_fiche_du_rendez_vous(self):
        r = self.creer(nature='rendez_vous')
        _, d = self.appel('agenda/evenement', args={'id': r['evenement_id']})
        self.assertEqual(d['rappel_client'], '')                                                      # aucun rappel : aucune mention
        maintenant = '2026-11-21T08:00:00+00:00'
        self.desk.db.execute("INSERT INTO bland56109_rappels(cle,rdv,debut,canal,jour,etat,maj_le) VALUES('r-1',?,?,'sms','2026-11-21','a_confirmer',?)",
                             (r['evenement_id'], maintenant, maintenant))
        self.desk.db.commit()
        self.assertEqual(self.appel('agenda/evenement', args={'id': r['evenement_id']})[1]['rappel_client'], '')   # préparé, non envoyé : rien
        self.desk.db.execute("UPDATE bland56109_rappels SET etat='envoye', envoye_le=? WHERE cle='r-1'", (maintenant,))
        self.desk.db.commit()
        mention = self.appel('agenda/evenement', args={'id': r['evenement_id']})[1]['rappel_client']
        self.assertEqual(mention, 'Rappel envoyé au client le 21/11/2026 à 09:00 (SMS, Bland).')       # heure du cabinet (Paris)
        from pathlib import Path
        self.assertIn('d.rappel_client', (Path(__file__).resolve().parents[1] / 'agent' / 'static' / 'v5697-agenda.js').read_text(encoding='utf-8'))


# ------------------------------------------------------------------------------------------------ 7. documents étrangers (lot S) au registre (lot M)
import test_v56109_international as _intl                                                             # noqa: E402  (module : ses tests ne sont pas recollectés ici)


class I2CoutsEtrangers(_intl.Base):
    def setUp(self):
        super().setUp()
        self.ch_pret()
        self.p = self.societe_uk()

    collecter, lignes = _intl.Documents.collecter, _intl.Documents.lignes

    def quantites(self):
        from agent import budget_ledger5689 as bl
        db = bl.connect(self.f.c['state_dir'])
        try:
            return [tuple(r) for r in db.execute('SELECT call_id, provider, unit_code, quantite_milli, etat, source FROM budget_quantites56109 ORDER BY call_id')]
        finally:
            db.close()

    def test_G19_cout_devise_et_credits_inscrits_au_registre_de_consommation(self):
        cle = self.cle()
        out = self.collecter(cle, maximum=2)
        self.assertEqual(len(out['classes']), 2)
        q = self.quantites()
        self.assertEqual(len(q), 2)                                                                   # un téléchargement, une ligne
        self.assertEqual({(x[2], x[3], x[4], x[5]) for x in q}, {('montant_gbp_uk_companies_house', 0, 'declaree', 'international56109')})   # service gratuit : 0 déclaré
        self.collecter(cle, maximum=2)                                                                # rejeu : aucune ligne de plus
        self.assertEqual(len(self.quantites()), 2)
        ordinaire = self.lignes()[0]
        I = _intl.I
        ident = I.enregistrer_document(self.d, 'DOS-001', self.p['cle'], 'uk_companies_house', ordinaire['reference'], ordinaire['revision'], 'certifiee',
                                       'b' * 64, '/Dossiers/DEMO/Documents publics des sociétés/certifiee.pdf', 'GBP', '15.00', '3')
        par = {x[0]: x for x in self.quantites()}
        self.assertEqual(par[ident + ':montant'][2:5], ('montant_gbp_uk_companies_house', 15000, 'declaree'))   # devise gardée, sans conversion
        self.assertEqual(par[ident + ':credits'][2:5], ('credit_uk_companies_house', 3000, 'declaree'))
        sans = I.enregistrer_document(self.d, 'DOS-001', self.p['cle'], 'uk_companies_house', ordinaire['reference'], ordinaire['revision'], 'impression',
                                      'c' * 64, '/Dossiers/DEMO/Documents publics des sociétés/page.html')
        self.assertNotIn(sans + ':montant', {x[0] for x in self.quantites()})                         # coût inconnu : jamais un faux zéro


if __name__ == '__main__':
    unittest.main()
