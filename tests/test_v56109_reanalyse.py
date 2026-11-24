"""5.6.109, lot A (F03) : « Analyser le dossier » depuis la fiche — commande, suivi, couverture, comparaison avant remplacement.

Service réel (``reanalyse56109``, ``dossier5624``, moteur des travaux, registre des faits) ; index de documents local ; indexation du stockage
et modèle remplacés par des faux (aucun réseau, aucun modèle). Données fictives (DURAND, MARTIN, ZORVAL, DOS-001, RG 24/01234, Valbrune).
"""
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent import acces5697, facts460, legal_memory as lm, membres5650, reanalyse56109 as RA
from agent.common import Stop
from agent.desk import Desk
import test_agent as fx

ROOT = Path(__file__).resolve().parent.parent
PREFIX = '/agent-courriel'
DURAND = ('durand@example.test', 'avocat')
ASSISTANT = ('bernard@example.test', 'assistant')
ADMIN = ('cabinet', 'administrateur')
ACTE = ('TRIBUNAL JUDICIAIRE DE VALBRUNE\nRG 24/01234\n\nASSIGNATION DÉLIVRÉE LE 4 septembre 2026\n'
        'La SAS ZORVAL, représentée par son président, demande la condamnation de M. Paul MARTIN au paiement de la somme de 12 345,67 euros. ' * 2)
COMPLET = {'fichiers': 1, 'en_attente': 0, 'inventaire': {'complete': True}}


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fx.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.d = Desk(self.f.c)
        self.addCleanup(self.d.db.close)
        self.origin = 'https://cabinet.example.test'
        self.auth = {'origin': self.origin, 'csrf': 'test-csrf', 'prefix': PREFIX}
        self.n = 0
        self.modele = []

    # ---- faux index, fausse indexation du stockage, faux modèle
    def piece(self, nom, texte=ACTE, etag='e1', modifie='2026-11-20T10:00:00+00:00', erreur=''):
        from agent.index import DocumentIndex
        idx = DocumentIndex(self.d.c['state_dir'])
        chemin = '/Dossiers/DEMO/' + nom
        idx.db.execute('INSERT OR REPLACE INTO docs VALUES(?,?,?,?,?,?)', ('DOS-001', chemin, etag, modifie, texte, erreur))
        idx.db.commit()
        idx.db.close()
        return chemin

    def fausse_synthese(self, desk, args):
        version = desk.db.execute('SELECT COALESCE(MAX(version),0)+1 FROM case_briefs WHERE matter=?', ('DOS-001',)).fetchone()[0]
        donnees = self.fiches[min(version, len(self.fiches)) - 1] if getattr(self, 'fiches', None) else {'parties': ['ZORVAL']}
        desk.db.execute('INSERT INTO case_briefs VALUES(NULL,?,?,?,?,?)', ('DOS-001', version, json.dumps(donnees),
                                                                         json.dumps([{'id': 'k1', 'path': '/Dossiers/DEMO/Assignation ZORVAL.pdf', 'excerpt': 'Assignation'}]), desk.now()))
        desk.db.commit()
        self.modele.append(version)
        return {'fiche_version': version, 'sources': 1, 'fichiers_en_attente': 0}

    def travailler(self, index=COMPLET):
        """Fait tourner le travail en file par le vrai moteur ; seuls l'accès au stockage (index) et le modèle (synthèse) sont des faux."""
        vrai = Desk.perform

        def perform(desk, kind, args):
            return dict(index) if kind == 'index' else vrai(desk, kind, args)
        with patch.object(Desk, 'perform', perform), patch('agent.intelligence.refresh_brief', side_effect=self.fausse_synthese):
            return self.d.work_once()

    def cle(self):
        self.n += 1
        return 'cle-f03-essai-%04d' % self.n

    def api(self, route, data=None, acteur=DURAND, methode='POST', args=None, csrf='test-csrf', brut=None):
        raw = brut if brut is not None else json.dumps(data or {}).encode()
        env = {'REQUEST_METHOD': methode, 'HTTP_ORIGIN': self.origin, 'HTTP_X_CSRF_TOKEN': csrf, 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw), 'HTTP_X_AXIORHUB_ROLE': acteur[1], 'HTTP_X_AXIORHUB_USER': acteur[0]}
        out = RA.api(env, self.d, self.auth, PREFIX, RA.PREFIXE + route, args or {}, methode)
        out['json'] = json.loads(out['body'])
        out['entetes'] = dict(out.get('headers', []))
        return out

    def lancer(self, mode='', acteur=DURAND, **extra):
        return self.api('lancer', {'d': 'DOS-001', 'mode': mode, 'cle': self.cle(), **extra}, acteur=acteur)

    def bloc(self, acteur=DURAND, complet=True):
        return RA.bloc_fiche_html(self.d, acteur, PREFIX, {'id': 'DOS-001'}, complet)

    def travaux(self):
        return [dict(r) for r in self.d.db.execute("SELECT id, status, args FROM jobs WHERE kind='analyser_dossier5624' ORDER BY id")]

    def fait(self, type_):
        return [dict(r) for r in self.d.db.execute('SELECT * FROM legal_memory_records WHERE matter=? AND record_type=? ORDER BY created, id', ('DOS-001', type_))]


# ================================================================================================ commande, droits, suivi
class Commande(Base):
    def test_F03_routes_declarees_roles_et_statiques(self):
        from agent import shell501, web440
        from agent.standalone_auth import allowed
        for route in RA.ECRITURES:
            self.assertTrue(allowed('avocat', 'POST', '/api440/' + RA.PREFIXE + route), route)
            self.assertFalse(allowed('assistant', 'POST', '/api440/' + RA.PREFIXE + route), route)
            self.assertFalse(allowed('lecture', 'POST', '/api440/' + RA.PREFIXE + route), route)
        self.assertFalse(allowed('avocat', 'POST', '/api440/' + RA.PREFIXE + 'inconnue'))
        self.assertFalse(RA.allowed('avocat', 'POST', RA.PREFIXE + 'etat'))                  # une lecture ne se fait pas en POST
        self.assertTrue(RA.allowed('assistant', 'GET', RA.PREFIXE + 'etat'))
        for nom, genre in RA.statiques().items():
            self.assertEqual(web440.STATIC[nom], genre)
            self.assertTrue((ROOT / 'agent' / nom.lstrip('/')).is_file(), nom)
        self.assertIn('v56109-reanalyse.css', shell501.BUNDLE)
        self.assertIn('.a109-compare', (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8'))
        r = self.api('inconnue', {'d': 'DOS-001', 'cle': self.cle()})
        self.assertTrue(r['status'].startswith('404'))
        r = self.api('lancer', {'d': 'DOS-001', 'cle': self.cle()}, acteur=ASSISTANT)
        self.assertEqual((r['status'][:3], r['json']['error']), ('403', 'role_insuffisant'))
        self.assertEqual(self.travaux(), [])

    def test_F03_schema_pret_au_demarrage_jamais_pendant_une_lecture(self):
        tables = {r[0] for r in self.d.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({'analyses5624', 'analyses5624_campagnes', 'analyses5624_couverture', 'reanalyse56109_propositions', 'reanalyse56109_ops'} <= tables)
        src = (ROOT / 'agent' / 'desk.py').read_text(encoding='utf-8')
        self.assertIn('ensure_reanalyse56109_schema(self)', src)
        self.assertLess(src.index('ensure_reanalyse56109_schema(self)'), src.index('ensure_pseudo56109_schema(self)'))   # le lot P pose ses déclencheurs en dernier

    def test_R05_afficher_la_fiche_ou_lire_l_etat_ne_lance_rien_et_n_ecrit_rien(self):
        self.piece('Assignation ZORVAL.pdf')
        avant = self.d.db.total_changes
        html = self.bloc()
        r = self.api('etat', methode='GET', args={'d': 'DOS-001'})
        c = self.api('couverture', methode='GET', args={'d': 'DOS-001'})
        p = self.api('propositions', methode='GET', args={'d': 'DOS-001'})
        self.assertEqual(self.d.db.total_changes, avant)
        self.assertEqual(self.travaux(), [])
        self.assertEqual((r['status'][:3], r['json']['etat'], r['json']['travail']), ('200', 'jamais', None))
        self.assertEqual(r['entetes']['Cache-Control'], 'private, no-store')
        self.assertEqual((c['json']['elements'], p['json']['propositions']), ([], []))
        self.assertIn('Analyser le dossier', html)
        self.assertNotIn('Réanalyser', html)

    def test_F03_dialogue_avant_lancement_dit_ce_qui_est_lu_envoye_et_le_budget(self):
        html = self.bloc()
        for attendu in ('Avant de lancer', 'Ce qui sera lu', 'Ce qui sera envoyé à l’IA', 'Durée et budget', 'Actualiser ce qui a changé', 'Analyse complète',
                        'value="changements" checked', 'au modèle local</span> <code>test-local</code>', 'sans IA', 'Sources exclues', 'data-api="m56109/reanalyse/lancer"',
                        'name="cle" value="', 'Lancer l’analyse'):
            self.assertIn(attendu, html, attendu)
        self.assertNotRegex(html, r'<script(?![^>]*\bsrc=)')                                  # aucun script en ligne
        self.assertIn('<script defer src="/agent-courriel/static/v56109-reanalyse.js"></script>', html)
        self.assertNotRegex(html, r'\son[a-z]+="')
        with patch('agent.model.routed_config', return_value={'model': 'modele-distant', 'provider_type': 'openai', 'provider_id': 'fournisseur-fictif'}):
            html = self.bloc()
        self.assertIn('du service externe</span> <code>fournisseur-fictif</code>', html)                         # la destination réelle est dite avant de lancer
        with patch('agent.model.routed_config', side_effect=Stop('fonction_ia_invalide')):
            self.assertIn('réglage illisible', self.bloc())                                   # jamais une destination inventée

    def test_F03_lancer_par_post_protege_une_seule_commande(self):
        r = self.api('lancer', {'d': 'DOS-001', 'cle': self.cle()}, csrf='mauvais')
        self.assertEqual((r['status'][:3], r['json']['error']), ('403', 'origine_ou_csrf_refuse'))
        r = self.api('lancer', {'d': 'DOS-001'})
        self.assertEqual(r['json']['error'], 'cle_requise')
        r = self.api('lancer', brut=b'{"d":"DOS-001","x":"' + b'a' * 30000 + b'"}')
        self.assertTrue(r['status'].startswith('413'))
        self.assertEqual(self.travaux(), [])
        cle = self.cle()
        r1 = self.api('lancer', {'d': 'DOS-001', 'cle': cle})
        self.assertTrue(r1['status'].startswith('200'), r1['body'])
        self.assertFalse(r1['json']['already'])
        travail = self.travaux()
        self.assertEqual(len(travail), 1)
        self.assertEqual(json.loads(travail[0]['args']), {'campagne': 1, 'matter': 'DOS-001', 'mode': 'changements'})   # « Actualiser ce qui a changé » par défaut
        r2 = self.api('lancer', {'d': 'DOS-001', 'cle': cle})                                 # même clé : même reçu
        self.assertTrue(r2['json']['rejoue'])
        r3 = self.api('lancer', {'d': 'DOS-001', 'cle': cle, 'mode': 'complete'})             # même clé, autre demande
        self.assertEqual((r3['status'][:3], r3['json']['error']), ('409', 'cle_reutilisee'))
        r4 = self.lancer('complete')                                                          # double clic (autre clé) : la même commande
        self.assertTrue(r4['json']['already'])
        self.assertEqual(r4['json']['job_id'], r1['json']['job_id'])
        self.assertEqual(len(self.travaux()), 1)
        camp = self.d.db.execute("SELECT numero, mode, etat, demande_par FROM analyses5624_campagnes WHERE matter='DOS-001'").fetchall()
        self.assertEqual([tuple(c) for c in camp], [(1, 'changements', 'demandee', 'durand@example.test')])

    def test_F03_dossier_reserve_ou_absent_meme_404_neutre_avant_tout_compteur(self):
        self.piece('Assignation ZORVAL.pdf')
        self.lancer()
        self.travailler()
        membres5650.set_reserve(self.d.c['state_dir'], 'DOS-001', ['martin@example.test'])
        for route, args in (('etat', {'d': 'DOS-001'}), ('couverture', {'d': 'DOS-001'}), ('propositions', {'d': 'DOS-001'})):
            reserve = self.api(route, methode='GET', args=args)
            absent = self.api(route, methode='GET', args={'d': 'DOS-999'})
            self.assertEqual((reserve['status'], reserve['json']), (absent['status'], absent['json']), route)
            self.assertEqual(reserve['status'], '404 Not Found')
            self.assertNotIn('Assignation', reserve['body'])
        avant = len(self.travaux())
        for route, corps in (('lancer', {}), ('arreter', {}), ('decider', {'proposition': 'x', 'action': 'accepter', 'version': 1})):
            r = self.api(route, {'d': 'DOS-001', 'cle': self.cle(), **corps})
            self.assertEqual((r['status'][:3], r['json']['error']), ('404', 'introuvable'), route)
        self.assertEqual(len(self.travaux()), avant)
        self.assertEqual(RA.montage_fiche(self.d, DURAND, PREFIX, {'id': 'DOS-001'}), '')     # le bloc n'est pas rendu : ni état, ni couverture
        self.assertIn('id="a109-analyse"', RA.montage_fiche(self.d, ('martin@example.test', 'avocat'), PREFIX, {'id': 'DOS-001'}))

    def test_F03_mutation_sans_la_garde_d_acces_la_couverture_fuirait(self):
        self.piece('Assignation ZORVAL.pdf')
        self.lancer()
        self.travailler()
        membres5650.set_reserve(self.d.c['state_dir'], 'DOS-001', ['martin@example.test'])
        with patch.object(acces5697, 'exiger', lambda desk, acteur, dossier_id=None, ref=None: acces5697.acteur_de(acteur)):
            r = self.api('couverture', methode='GET', args={'d': 'DOS-001'})
        self.assertIn('Assignation ZORVAL.pdf', r['body'])                                    # c'est bien la garde qui protège

    def test_F03_registre_des_droits_illisible_rien_n_est_montre(self):
        with patch('agent.membres5650.hidden_for', side_effect=Stop('controle_acces_indisponible')):
            r = self.api('etat', methode='GET', args={'d': 'DOS-001'})
            lance = self.lancer()
            bloc = RA.montage_fiche(self.d, DURAND, PREFIX, {'id': 'DOS-001'})
        self.assertFalse(r['status'].startswith('200'))
        self.assertFalse(lance['status'].startswith('200'))
        self.assertEqual((bloc, self.travaux()), ('', []))

    def test_F03_suivi_puis_resultat_dans_la_fiche(self):
        self.piece('Assignation ZORVAL.pdf')
        self.piece('Contrat protégé.pdf', texte='', erreur='pdf_protege_par_mot_de_passe')
        jid = self.lancer()['json']['job_id']
        html = self.bloc()
        self.assertIn('data-a109-actif="1"', html)
        self.assertIn('Travail n° %d' % jid, html)
        self.assertIn('Arrêter l’analyse', html)
        self.assertNotIn('Lancer l’analyse', html)                                           # pas de seconde commande pendant le travail
        e = self.api('etat', methode='GET', args={'d': 'DOS-001'})['json']
        self.assertEqual((e['etat'], e['travail']), ('en_cours', jid))
        self.travailler()
        e = self.api('etat', methode='GET', args={'d': 'DOS-001'})['json']
        self.assertEqual((e['etat'], e['travail'], e['campagne']['etat']), ('a_jour', None, 'terminee'))
        self.assertIn('1 élément à vérifier', e['libelle'])
        self.assertEqual([s['etat'] for s in e['etapes']], ['ok'] * 4)
        html = self.bloc()
        self.assertIn('Réanalyser le dossier', html)
        self.assertNotIn('data-a109-actif="1"', html)
        for attendu in ('<strong>2</strong> pièce(s) présente(s)', '<strong>1</strong> indexée(s)', '<strong>1</strong> transmise(s) au modèle pour la synthèse',
                        'Pièces à vérifier</span> (1)', 'Contrat protégé.pdf', 'Une pièce indexée n’est pas pour autant lue par le modèle'):
            self.assertIn(attendu, html, attendu)
        c = self.api('couverture', methode='GET', args={'d': 'DOS-001', 'genre': 'piece', 'etat': 'en_erreur'})['json']
        self.assertEqual([(x['nom'], x['motif']) for x in c['elements']], [('Contrat protégé.pdf', 'pdf_protege_par_mot_de_passe')])
        court = self.bloc(complet=False)
        self.assertIn('Voir l’analyse', court)
        self.assertIn('vue=fichiers#a109-analyse', court)

    def test_F03_arreter_l_analyse_par_le_moteur(self):
        jid = self.lancer()['json']['job_id']
        r = self.api('arreter', {'d': 'DOS-001', 'cle': self.cle()})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertEqual((r['json']['job_id'], r['json']['status']), (jid, 'cancelled'))
        self.assertEqual(self.travaux()[0]['status'], 'cancelled')
        self.assertIsNone(self.api('etat', methode='GET', args={'d': 'DOS-001'})['json']['travail'])
        r = self.api('arreter', {'d': 'DOS-001', 'cle': self.cle()})
        self.assertEqual(r['json']['error'], 'aucune_analyse_en_cours')
        self.assertFalse(self.lancer()['json']['already'])                                    # une nouvelle demande reste possible : nouvelle campagne
        self.assertEqual([c[0] for c in self.d.db.execute("SELECT etat FROM analyses5624_campagnes WHERE matter='DOS-001' ORDER BY numero")], ['interrompue', 'demandee'])

    def test_F03_arret_pendant_une_etape_aucune_etape_suivante_et_etat_reel(self):
        self.piece('Assignation ZORVAL.pdf')
        jid = self.lancer()['json']['job_id']
        vrai = Desk.perform

        def perform(desk, kind, args):
            if kind != 'index':
                return vrai(desk, kind, args)
            out = RA.arreter(desk, DURAND, 'DOS-001', 'cle-f03-arret-pendant-0001')          # « Arrêter l'analyse » pendant le lot
            self.assertEqual(out['status'], 'cancel_requested')
            return {'fichiers': 1, 'en_attente': 3}
        with patch.object(Desk, 'perform', perform), patch('agent.facts460.scan_matter', side_effect=AssertionError('aucune étape après l’arrêt')):
            self.d.work_once()
        self.assertEqual(self.travaux()[0]['status'], 'cancelled')
        e = self.api('etat', methode='GET', args={'d': 'DOS-001'})['json']
        self.assertEqual((e['etat'], e['travail'], e['campagne']['etat']), ('partielle', None, 'annulee'))
        self.assertEqual([s['etat'] for s in e['etapes']], ['interrompue', 'jamais', 'jamais', 'jamais'])
        self.assertTrue(jid)

    def test_F03_ia_en_pause_demande_conservee_consultation_possible(self):
        self.piece('Assignation ZORVAL.pdf')
        self.lancer()
        self.travailler()                                                                     # une première analyse existe
        self.d.setting('missions567:pause', True)
        html = self.bloc()
        self.assertIn('IA en pause.', html)
        self.assertIn('conservée en attente', html)
        jid = self.lancer()['json']['job_id']                                                 # la demande de l'avocat est acceptée…
        self.travailler()
        self.assertEqual(self.travaux()[-1]['status'], 'suspended')                           # … et suspendue par le moteur, pas perdue
        self.assertEqual(self.modele, [1])                                                    # aucun appel au modèle pendant la pause
        e = self.api('etat', methode='GET', args={'d': 'DOS-001'})
        self.assertTrue(e['status'].startswith('200'))                                        # la consultation reste possible
        self.assertEqual((e['json']['travail'], e['json']['attente']['job']), (None, jid))
        self.assertIn('IA en pause', e['json']['attente']['libelle'])
        self.assertTrue(self.api('couverture', methode='GET', args={'d': 'DOS-001'})['json']['elements'])
        html = self.bloc()
        self.assertIn('Demande conservée (travail n° %d)' % jid, html)
        self.assertIn('Arrêter l’analyse', html)
        again = self.lancer()['json']
        self.assertEqual((again['job_id'], again['already'], again['en_attente']), (jid, True, 'pause'))
        self.assertEqual(len(self.travaux()), 2)

    def test_F03_interrupteur_ancienne_presentation_sans_bloc_services_inchanges(self):
        from agent import interface56100
        interface56100.regler_cabinet(self.d, False)
        self.assertEqual(RA.montage_fiche(self.d, DURAND, PREFIX, {'id': 'DOS-001'}), '')
        self.assertTrue(self.api('etat', methode='GET', args={'d': 'DOS-001'})['status'].startswith('200'))   # droits et états ne dépendent pas de l'interrupteur
        interface56100.regler_compte(self.d, DURAND[0], True)
        self.assertIn('id="a109-analyse"', RA.montage_fiche(self.d, DURAND, PREFIX, {'id': 'DOS-001'}))

    def test_F03_point_de_montage_unique_dans_l_onglet_fichiers(self):
        from agent import fiche_ui5698
        html = fiche_ui5698.fichiers_html(self.d, self.auth, DURAND, PREFIX, {'id': 'DOS-001'}, {}, lambda: '<p>LISTE PAR TYPE</p>')
        self.assertLess(html.index('id="a109-analyse"'), html.index('id="d98-fichiers"'))
        self.assertIn('LISTE PAR TYPE', html)
        src = (ROOT / 'agent' / 'fiche_ui5698.py').read_text(encoding='utf-8')
        self.assertEqual(src.count('from .reanalyse56109 import'), 1)
        with patch.object(RA, 'bloc_fiche_html', side_effect=RuntimeError('panne du bloc')):   # un défaut du bloc n'empêche jamais l'onglet
            html = fiche_ui5698.fichiers_html(self.d, self.auth, DURAND, PREFIX, {'id': 'DOS-001'}, {}, lambda: '<p>LISTE PAR TYPE</p>')
        self.assertIn('LISTE PAR TYPE', html)
        self.assertIn('momentanément indisponible', html)

    def test_F03_lecture_seule_pour_un_assistant(self):
        self.piece('Assignation ZORVAL.pdf')
        self.lancer()
        self.travailler()
        html = self.bloc(acteur=ASSISTANT)
        self.assertIn('Ce qui a été lu (couverture)', html)
        self.assertNotIn('<form', html)                                                       # ni lancer, ni arrêter, ni décider

    def test_F03_textes_echappes(self):
        self.piece('<img src=x onerror=alert(1)>.pdf', texte='', erreur='<b>erreur</b>')
        self.lancer()
        self.travailler()
        html = self.bloc()
        self.assertNotIn('<img src=x', html)
        self.assertNotIn('<b>erreur</b>', html)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;.pdf', html)

    def test_F03_tables_declarees_au_contrat_d_export(self):
        from agent import export56108
        src = (ROOT / 'agent' / 'export56108.py').read_text(encoding='utf-8')
        for table in ('analyses5624_campagnes', 'analyses5624_couverture', 'reanalyse56109_ops', 'reanalyse56109_propositions'):
            self.assertIn("'%s'" % table, src, table)
        self.assertIn("('propositions_de_l_analyse_et_decisions', 'reanalyse56109_propositions', 'matter')", src)   # les décisions de l'avocat sont exportées
        self.assertTrue(export56108)

    def test_F03_script_du_bloc_sans_html_injecte(self):
        js = (ROOT / 'agent' / 'static' / 'v56109-reanalyse.js').read_text(encoding='utf-8')
        self.assertNotIn('innerHTML', js)
        self.assertNotRegex(js, r'https?://')
        self.assertIn("cache: 'no-store'", js)
        self.assertNotIn('method:', js)                                                       # le script ne fait que lire l'état ; les commandes sont des formulaires protégés


# ================================================================================================ comparaison avant remplacement
class Comparaison(Base):
    def analyser(self, mode='complete'):
        r = self.lancer(mode)
        self.assertFalse(r['json'].get('error'), r['body'])
        self.travailler()
        self.assertEqual(self.travaux()[-1]['status'], 'done')

    def ouvertes(self):
        return self.api('propositions', methode='GET', args={'d': 'DOS-001'})['json']['propositions']

    def decider(self, p, action, acteur=DURAND, **extra):
        return self.api('decider', {'d': 'DOS-001', 'proposition': p['id'], 'version': p['version'], 'action': action, 'cle': self.cle(), **extra}, acteur=acteur)

    def valider(self, type_):
        rec = self.fait(type_)[0]
        facts460.decide(self.d, 'DOS-001', rec['id'], 'validate')
        return self.fait(type_)[0]

    def test_F03_preuve_modifiee_proposition_a_decider_puis_acceptee(self):
        self.piece('Assignation ZORVAL.pdf')
        self.analyser()
        self.assertEqual(self.ouvertes(), [])                                                 # première analyse : rien de validé, rien à comparer
        rg = self.valider('case_number')
        self.piece('Assignation ZORVAL.pdf', texte='Signification rectifiée.\n' + ACTE, etag='e2', modifie='2026-11-22T09:00:00+00:00')   # la pièce source change
        self.analyser()
        props = self.ouvertes()
        self.assertEqual([(p['genre'], p['cible'], p['etat']) for p in props], [('preuve', rg['id'], 'a_valider')])
        p = props[0]
        self.assertEqual((p['ancien']['etat'], p['ancien']['contenu']), ('validated', rg['content']))   # l'ancienne version validée est montrée
        self.assertIn('Assignation ZORVAL.pdf', json.dumps(p['sources'], ensure_ascii=False))
        html = self.bloc()
        for attendu in ('Changements à décider', 'Ancienne information', 'Proposition', 'Ce qui justifie le changement', 'Sources', 'Accepter',
                        'Conserver l’ancienne', 'Modifier', 'Demander une précision', 'data-api="m56109/reanalyse/decider"', 'data-d79-doc="/Dossiers/DEMO/Assignation ZORVAL.pdf"'):
            self.assertIn(attendu, html, attendu)
        r = self.decider(p, 'accepter', acteur=ASSISTANT)
        self.assertEqual(r['json']['error'], 'role_insuffisant')
        r = self.decider({**p, 'version': 99}, 'accepter')
        self.assertEqual((r['status'][:3], r['json']['error']), ('409', 'version_perimee'))
        r = self.decider(p, 'accepter')
        self.assertTrue(r['status'].startswith('200'), r['body'])
        apres = self.fait('case_number')[0]
        self.assertEqual((apres['status'], apres['content']), ('validated', rg['content']))
        self.assertIn('acceptée par l’avocat', apres['validation_note'])
        self.assertEqual(self.ouvertes(), [])
        r = self.decider(p, 'accepter')
        self.assertEqual((r['status'][:3], r['json']['error']), ('409', 'deja_decidee'))
        historique = [h[0] for h in self.d.db.execute('SELECT reason FROM legal_memory_history WHERE record_id=? ORDER BY id', (rg['id'],))]
        self.assertIn('preuve_modifiee_validation_suspendue', historique)                      # rien n'est effacé : chaque version reste dans l'historique
        self.analyser()                                                                       # nouvelle analyse sans changement : rien n'est reproposé
        self.assertEqual(self.ouvertes(), [])
        self.assertEqual(self.fait('case_number')[0]['status'], 'validated')

    def test_F03_autre_valeur_lue_jamais_un_remplacement_silencieux(self):
        self.successeur_propose()

    def successeur_propose(self):
        self.piece('Assignation ZORVAL.pdf')
        self.analyser()
        juridiction = self.valider('jurisdiction')
        self.piece('Jugement ZORVAL.pdf', texte=ACTE.replace('TRIBUNAL JUDICIAIRE DE VALBRUNE', 'TRIBUNAL DE COMMERCE DE VALBRUNE'), etag='j1')
        self.analyser()
        valide = next(r for r in self.fait('jurisdiction') if r['id'] == juridiction['id'])
        self.assertEqual((valide['status'], valide['content'], valide['revision']), ('validated', juridiction['content'], juridiction['revision']))   # intacte
        props = [p for p in self.ouvertes() if p['genre'] == 'successeur']
        self.assertEqual(len(props), 1)
        p = props[0]
        self.assertEqual(p['cible'], juridiction['id'])
        self.assertIn('COMMERCE', p['propose']['contenu'].upper())
        self.assertIn('Jugement ZORVAL.pdf', json.dumps(p['sources'], ensure_ascii=False))
        return p, juridiction

    def test_F03_successeur_accepte_ancienne_archivee_avec_lien_de_remplacement(self):
        p, juridiction = self.successeur_propose()
        r = self.decider(p, 'accepter')
        self.assertTrue(r['status'].startswith('200'), r['body'])
        par_id = {x['id']: x for x in self.fait('jurisdiction')}
        self.assertEqual(par_id[juridiction['id']]['status'], 'archived')                      # archivée, pas supprimée
        successeur = par_id[p['propose']['record']]
        self.assertEqual((successeur['status'], successeur['supersedes']), ('validated', juridiction['id']))
        self.assertTrue(self.d.db.execute('SELECT COUNT(*) FROM legal_memory_history WHERE record_id=?', (juridiction['id'],)).fetchone()[0])
        self.assertTrue(self.d.db.execute("SELECT 1 FROM audit WHERE action='reanalyse56109_decision'").fetchone())

    def test_F03_conserver_l_ancienne_la_proposition_n_est_plus_reproposee(self):
        p, juridiction = self.successeur_propose()
        r = self.decider(p, 'conserver', motif='Le tribunal de commerce n’est saisi que du référé.')
        self.assertTrue(r['status'].startswith('200'), r['body'])
        par_id = {x['id']: x for x in self.fait('jurisdiction')}
        self.assertEqual(par_id[juridiction['id']]['status'], 'validated')
        self.assertEqual(par_id[p['propose']['record']]['status'], 'disputed')
        self.analyser()                                                                       # réanalyse complète : le refus est respecté
        self.assertEqual([x for x in self.ouvertes() if x['genre'] == 'successeur'], [])
        self.assertEqual({x['id']: x['status'] for x in self.fait('jurisdiction')}[juridiction['id']], 'validated')

    def test_F03_modification_entre_temps_conflit_sans_perte(self):
        p, juridiction = self.successeur_propose()
        lm.change_record(self.d, 'validate_memory', {'record': juridiction['id'], 'matter': 'DOS-001', 'text': 'Juridiction : Tribunal judiciaire de Valbrune, 2e chambre',
                                                    'note': 'Précisé par l’avocat'})   # l'avocat (ou un autre poste) modifie entre-temps
        r = self.decider(p, 'accepter')
        self.assertEqual((r['status'][:3], r['json']['error']), ('409', 'information_modifiee'))
        par_id = {x['id']: x for x in self.fait('jurisdiction')}
        self.assertEqual(par_id[juridiction['id']]['status'], 'validated')                     # rien n'est remplacé
        self.assertIn('2e chambre', par_id[juridiction['id']]['content'])
        self.assertEqual(par_id[p['propose']['record']]['status'], 'suggested')
        p2 = next(x for x in self.ouvertes() if x['id'] == p['id'])
        self.assertGreater(p2['version'], p['version'])
        self.assertIn('2e chambre', p2['ancien']['contenu'])                                  # les deux versions sont montrées, à jour
        self.assertTrue(self.decider(p2, 'conserver')['status'].startswith('200'))

    def test_F03_mutation_sans_controle_de_revision_la_modification_serait_ecrasee(self):
        p, juridiction = self.successeur_propose()
        lm.change_record(self.d, 'validate_memory', {'record': juridiction['id'], 'matter': 'DOS-001', 'text': 'Juridiction : Tribunal judiciaire de Valbrune, 2e chambre'})
        actuelle = self.fait('jurisdiction')
        rev = next(x for x in actuelle if x['id'] == juridiction['id'])['revision']
        self.d.db.execute('UPDATE reanalyse56109_propositions SET cible_revision=? WHERE id=?', (rev, p['id']))   # mutation : comme si la révision n'était pas contrôlée
        self.d.db.commit()
        self.assertTrue(self.decider(p, 'accepter')['status'].startswith('200'))
        self.assertEqual({x['id']: x['status'] for x in self.fait('jurisdiction')}[juridiction['id']], 'archived')   # la correction de l'avocat partirait aux archives

    def test_F03_modifier_et_demander_une_precision(self):
        p, juridiction = self.successeur_propose()
        self.assertEqual(self.decider(p, 'modifier')['json']['error'], 'texte_requis')
        self.assertEqual(self.decider(p, 'precision')['json']['error'], 'motif_requis')
        self.assertEqual(self.decider(p, 'inconnue')['json']['error'], 'action_invalide')
        r = self.decider(p, 'precision', motif='Quelle juridiction est saisie au fond ?')
        self.assertEqual(r['json']['etat'], 'precision')
        p2 = next(x for x in self.ouvertes() if x['id'] == p['id'])
        self.assertEqual((p2['etat'], p2['motif']), ('precision', 'Quelle juridiction est saisie au fond ?'))   # reste ouverte, rien n'est modifié
        self.assertEqual({x['id']: x['status'] for x in self.fait('jurisdiction')}[juridiction['id']], 'validated')
        self.assertIn('Précision demandée :</span> <q>Quelle juridiction', self.bloc())
        r = self.decider(p2, 'modifier', texte='Juridiction : Tribunal de commerce de Valbrune (référé)')
        self.assertTrue(r['status'].startswith('200'), r['body'])
        par_id = {x['id']: x for x in self.fait('jurisdiction')}
        self.assertEqual((par_id[p['propose']['record']]['status'], par_id[p['propose']['record']]['content']),
                         ('validated', 'Juridiction : Tribunal de commerce de Valbrune (référé)'))
        self.assertEqual(par_id[juridiction['id']]['status'], 'archived')

    def test_F03_nouvelle_synthese_changements_et_sources_marques_vus(self):
        self.fiches = [{'parties': ['ZORVAL'], 'claims': ['Paiement de 12 345,67 euros'], 'jurisdiction': 'Tribunal judiciaire de Valbrune'},
                       {'parties': ['ZORVAL', 'MARTIN'], 'claims': ['Paiement de 12 345,67 euros'], 'jurisdiction': 'Tribunal de commerce de Valbrune'}]
        self.piece('Assignation ZORVAL.pdf')
        self.analyser()
        self.assertEqual(self.ouvertes(), [])
        self.analyser()                                                                       # analyse complète : nouvelle synthèse
        props = [p for p in self.ouvertes() if p['genre'] == 'synthese']
        self.assertEqual(len(props), 1)
        p = props[0]
        self.assertEqual((p['ancien']['version'], p['propose']['version']), (1, 2))
        self.assertEqual(p['propose']['changements'], {'parties': ['MARTIN'], 'jurisdiction': ['Tribunal de commerce de Valbrune']})
        self.assertEqual(p['ancien']['changements'], {'parties': [], 'jurisdiction': ['Tribunal judiciaire de Valbrune']})
        html = self.bloc()
        self.assertIn('Nouvelle synthèse du dossier', html)
        self.assertIn('Marquer comme vue', html)
        self.assertIn('elle ne remplace aucune information validée', html)
        avant = self.d.db.execute('SELECT COUNT(*), SUM(revision) FROM legal_memory_records').fetchone()
        r = self.decider(p, 'accepter')
        self.assertEqual(r['json']['etat'], 'vue')
        self.assertEqual(tuple(self.d.db.execute('SELECT COUNT(*), SUM(revision) FROM legal_memory_records').fetchone()), tuple(avant))   # aucun fait touché

    def test_F03_proposition_plus_recente_rend_l_ancienne_caduque(self):
        p, juridiction = self.successeur_propose()
        self.piece('Ordonnance ZORVAL.pdf', texte=ACTE.replace('TRIBUNAL JUDICIAIRE DE VALBRUNE', 'COUR D’APPEL DE VALBRUNE'), etag='o1')
        self.analyser()
        ouvertes = [x for x in self.ouvertes() if x['genre'] == 'successeur']
        self.assertEqual(len(ouvertes), 1)                                                    # une seule décision en attente par information
        self.assertNotEqual(ouvertes[0]['id'], p['id'])
        self.assertEqual(self.d.db.execute('SELECT etat FROM reanalyse56109_propositions WHERE id=?', (p['id'],)).fetchone()[0], 'caduque')
        r = self.decider(p, 'accepter')
        self.assertEqual((r['status'][:3], r['json']['error']), ('409', 'deja_decidee'))


class Traductions(unittest.TestCase):
    PHRASES = ('Analyse du dossier', 'Analyser le dossier', 'Réanalyser le dossier', 'Lancer l’analyse', 'Arrêter l’analyse', 'Avant de lancer', 'Ce qui sera lu',
               'Ce qui sera envoyé à l’IA', 'Durée et budget', 'Actualiser ce qui a changé', 'Analyse complète', 'Étapes de l’analyse', 'Ce qui a été lu (couverture)',
               'Changements à décider', 'Ancienne information', 'Proposition', 'Ce qui justifie le changement', 'Accepter', 'Conserver l’ancienne',
               'Demander une précision', 'Marquer comme vue', 'Vérifier', 'Réessayer l’écriture', 'Écriture à vérifier')

    def test_F03_textes_traduits_en_de_es(self):
        for lang in ('en', 'de', 'es'):
            d = json.loads((ROOT / 'agent' / 'locales' / ('ecrans_%s.json' % lang)).read_text(encoding='utf-8'))
            for p in self.PHRASES:
                self.assertTrue(d.get(p), (lang, p))


if __name__ == '__main__':
    unittest.main()
