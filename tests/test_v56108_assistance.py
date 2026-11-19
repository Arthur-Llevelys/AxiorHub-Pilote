"""5.6.108 (lot T) : M02 (droits d'Assistance métier sur chaque chemin, travaux différés) et Assistance métier en cinq parcours.

Données fictives (DOS-001 réservé à avocate@example.test, DOS-002 « MARTIN » visible), faux services, aucun réseau, aucun modèle.
"""
import json
from pathlib import Path
import re
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import test_agent as fixtures
import test_v5650 as t50
from agent import arret5643, assistance35 as a35, assistance56108 as a108, interface56100, membres5650 as membres
from agent.api import dispatch
from agent.common import Stop, load_matters
from agent.desk import Desk
from agent.web import App

MARQUEUR = 'MARQUEUR-RESERVE-FICTIF'
AUTRE = 'autre@example.test'
AVOCATE = 'avocate@example.test'
SOURCE = 'https://www.legifrance.gouv.fr/'
REF = 'Référence fictive à contrôler'


def _ajouter_dossier(c):
    path = Path(c['matters_file'])
    rows = json.loads(path.read_text(encoding='utf-8'))
    if not any(r['id'] == 'DOS-002' for r in rows):
        rows.append({**rows[0], 'id': 'DOS-002', 'client_name': 'MARTIN', 'path': '/Dossiers/MARTIN', 'aliases': ['MARTIN'],
                     'references': ['DOS-002'], 'correspondents': [{'email': 'martin@example.test', 'role': 'client', 'name': 'Paul MARTIN'}]})
        path.write_text(json.dumps(rows), encoding='utf-8')


def _requete(state, owner, role='avocat'):
    """Contexte d'une requête de ``owner`` (comme le contrôle central ``membres5650.gate``)."""
    membres.set_author(owner)
    membres.set_viewer_role(role)
    membres.set_viewer(membres.hidden_for(state, owner, role))


def _calcul(mid, jours=2):
    return {'matter': mid, 'calculation_type': 'calendar_days', 'start_date': '2026-01-01', 'days': jours,
            'source_url': SOURCE, 'source_reference': REF}


def _travailler(d, limite=5):
    for _ in range(limite):
        if not d.work_once():
            break


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.addCleanup(membres.clear)
        _ajouter_dossier(self.f.c)
        self.state = self.f.c['state_dir']
        self.d = Desk(self.f.c)
        self.addCleanup(self.d.db.close)
        self.p = a35._save(self.d, 'appel', 'DOS-001', {'test': 'reserve'}, {
            'status': 'projet_interne', 'purpose': MARQUEUR, 'documents_to_review': [{'id': 'x', 'path': '/Dossiers/DEMO/piece.pdf'}],
            'questions_to_ask': ['Question fictive']})
        membres.set_reserve(self.state, 'DOS-001', [AVOCATE])

    def projets(self):
        return self.d.db.execute('SELECT count(*) FROM assistance_projects_v350').fetchone()[0]

    def page(self, args):
        return App(None, None).page(self.f.c, {'prefix': '/agent-courriel', 'csrf': 'fictif'}, '/assistance-metier', args)


# ------------------------------------------------------------------------------------------------ M02 : lecture
class M02Lecture(Base):
    def setUp(self):
        super().setUp()
        _requete(self.state, AUTRE)

    def test_M02_01_registre_masque_et_apercu_meme_refus(self):
        self.assertEqual([m['id'] for m in load_matters(self.f.c)], ['DOS-002'])
        for pid in (self.p['project_id'], 'f' * 32):
            with self.assertRaisesRegex(Stop, '^projet_assistance_absent$'):
                a35.preview(self.d, pid)

    def test_M02_02_liste_filtre_avant_limite(self):
        visible = a35._save(self.d, 'calcul', 'DOS-002', {'test': 'visible'}, {'status': 'à relire'})
        self.d.db.execute('UPDATE assistance_projects_v350 SET created=? WHERE id=?', ('2000-01-01T00:00:00+00:00', visible['project_id']))
        self.d.db.commit()
        self.assertEqual([x['project_id'] for x in a35.recent(self.d, limit=1)], [visible['project_id']])
        # contre-exemple : un filtrage APRÈS la limite rendrait une liste vide (le projet interdit, plus récent, occupe la seule place)
        rows = self.d.db.execute('SELECT id,matter FROM assistance_projects_v350 ORDER BY created DESC LIMIT 1').fetchall()
        self.assertEqual([r['matter'] for r in rows], ['DOS-001'])

    def test_M02_03_api_liste_et_apercu(self):
        self.assertEqual(dispatch(self.d, '/assistance/projects', 'GET')['projects'], [])
        with self.assertRaisesRegex(Stop, '^projet_assistance_absent$'):
            dispatch(self.d, '/assistance/projects/' + self.p['project_id'], 'GET')

    def test_M02_04_page_lien_direct(self):
        for page in (self.page({'project': self.p['project_id']}), self.page({'project': self.p['project_id'], 'outil': 'appel'})):
            self.assertNotIn(MARQUEUR, page)
            self.assertIn('Projet introuvable', page)
        with patch.object(interface56100, 'active', return_value=False):   # ancienne présentation : même contrôle
            page = self.page({'project': self.p['project_id']})
        self.assertNotIn(MARQUEUR, page)
        self.assertIn('Projet introuvable', page)

    def test_M02_05_compte_rendu_d_un_appel_reserve(self):
        with self.assertRaisesRegex(Stop, '^projet_assistance_absent$'):
            a35.record_call(self.d, {'call_project_id': self.p['project_id'], 'notes': 'Note fictive'})
        with self.assertRaisesRegex(Stop, '^projet_assistance_absent$'):
            a35.soumettre(self.d, 'record_call35', {'call_project_id': self.p['project_id'], 'notes': 'Note fictive'})
        self.assertEqual(self.d.db.execute("SELECT count(*) FROM jobs WHERE kind='record_call35'").fetchone()[0], 0)
        self.assertEqual(self.d.db.execute("SELECT count(*) FROM assistance_projects_v350 WHERE kind='compte_rendu_appel'").fetchone()[0], 0)

    def test_M02_06_audience_a_identifiant_connu(self):
        pid = 'a' * 32
        self.d.db.execute('''INSERT INTO hearing_projects_v250 (id,matter,status,instruction,data,preview_hash,challenge_hash,created,expires,decided,result)
          VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (pid, 'DOS-001', 'pending', '', json.dumps({'preparation': {'title': MARQUEUR}}), '', '', '', '', '', ''))
        self.d.db.commit()
        with self.assertRaisesRegex(Stop, '^dossier_absent$'):
            a35.coach(self.d, {'hearing_project_id': pid, 'speech': 'Texte fictif', 'duration_seconds': 300, 'target_minutes': 5})
        # à l'enfilage : même refus qu'une audience inconnue (aucune existence révélée), aucun travail
        for ident in (pid, 'b' * 32):
            with self.assertRaisesRegex(Stop, '^audience_non_prete_pour_coaching$'):
                a35.soumettre(self.d, 'coach_hearing35', {'hearing_project_id': ident, 'speech': 'Texte fictif', 'duration_seconds': 300})
        self.assertEqual(self.d.db.execute("SELECT count(*) FROM jobs WHERE kind='coach_hearing35'").fetchone()[0], 0)
        self.assertNotIn(MARQUEUR, self.page({'outil': 'audience', 'matter': 'DOS-001'}))

    def test_M02_07_source_et_export_texte_refuses(self):
        out = a108.api({}, self.d, {}, '/agent-courriel', 'm56108/assistance/export', {'id': self.p['project_id']}, 'GET')
        inconnu = a108.api({}, self.d, {}, '/agent-courriel', 'm56108/assistance/export', {'id': 'f' * 32}, 'GET')
        self.assertEqual(out['status'], '404 Not Found')
        self.assertEqual(out['body'], inconnu['body'])
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        self.assertNotIn(MARQUEUR, out['body'])
        from agent import apercu5679
        with self.assertRaises(Stop):   # source du projet : le service des documents revérifie le dossier
            apercu5679._matter_file(self.d, 'DOS-001', '/Dossiers/DEMO/piece.pdf')

    def test_M02_08_reautorisation_sans_recreer_le_projet(self):
        _requete(self.state, AVOCATE)
        self.assertEqual(a35.preview(self.d, self.p['project_id'])['purpose'], MARQUEUR)
        self.assertEqual(len(a35.recent(self.d)), 1)
        self.assertIn(MARQUEUR, self.page({'project': self.p['project_id']}))

    def test_M02_09_revocation_pendant_l_ouverture(self):
        _requete(self.state, AVOCATE)
        self.assertIn(MARQUEUR, self.page({'project': self.p['project_id']}))
        membres.set_reserve(self.state, 'DOS-001', ['autre-avocat@example.test'])   # révocation entre deux requêtes
        _requete(self.state, AVOCATE)
        page = self.page({'project': self.p['project_id']})
        self.assertNotIn(MARQUEUR, page)
        with self.assertRaisesRegex(Stop, '^projet_assistance_absent$'):
            dispatch(self.d, '/assistance/projects/' + self.p['project_id'], 'GET')
        self.assertEqual(a108.api({}, self.d, {}, '', 'm56108/assistance/export', {'id': self.p['project_id']}, 'GET')['status'], '404 Not Found')
        self.assertEqual(self.d.db.execute('SELECT count(*) FROM assistance_projects_v350 WHERE id=?', (self.p['project_id'],)).fetchone()[0], 1,
                         'la révocation corrige l’accès, jamais en supprimant le projet')


class M02Export(Base):
    def _archive(self):
        from agent import export5688 as ex
        tables = tuple(ex.TABLES)
        if hasattr(ex, 'tables_exportees'):   # registre de l'export (lot C, B07) : le module déclare lui-même ses tables
            self.assertIn('assistance_projects_v350', [t[2] for t in ex.tables_exportees()])
        elif not any(t[2] == 'assistance_projects_v350' for t in tables):
            tables += a35.EXPORT
        visible = a35._save(self.d, 'calcul', 'DOS-002', {'test': 'export'}, {'status': 'à relire', 'warnings': ['VISIBLE-FICTIF']})
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        chemin = Path(tmp.name) / 'export.zip'
        with patch.object(ex, 'TABLES', tables):
            ex.construire(self.d, chemin, {'documents': False, 'dossiers_reserves': 'exclure'})
        with zipfile.ZipFile(chemin) as z:
            return visible, '\n'.join(z.read(n).decode('utf-8', 'replace') for n in z.namelist())

    def test_M02_10_export_integral_exclut_les_projets_reserves(self):
        visible, contenu = self._archive()
        self.assertIn(visible['project_id'], contenu)
        self.assertNotIn(MARQUEUR, contenu)
        self.assertNotIn(self.p['project_id'], contenu)
        self.assertNotIn('assistance_recus56108', contenu)

    def test_M02_11_declaration_d_export_du_module(self):
        self.assertEqual({t[2]: t[4] for t in a35.EXPORT},
                         {'assistance_projects_v350': 'matter', 'comparable_decisions_v350': 'matter'})
        self.assertIn('assistance_recus56108', dict(a35.EXPORT_EXCLUES))


# ------------------------------------------------------------------------------------------------ M02 : travaux différés
class M02Travaux(Base):
    def test_M02_12_auteur_note_par_le_serveur_jamais_par_le_navigateur(self):
        _requete(self.state, AUTRE)
        clean, mid = a35.admettre(self.d, 'calculate35', {**_calcul('DOS-002'), a35.AUTEUR: {'owner': AVOCATE, 'role': 'administrateur'},
                                                            'outil': 'calcul', 'role': 'administrateur', 'owner': 'cabinet'})
        self.assertEqual(mid, 'DOS-002')
        self.assertEqual(clean[a35.AUTEUR]['owner'], AUTRE)
        self.assertEqual(clean[a35.AUTEUR]['role'], 'avocat')
        self.assertNotIn('outil', clean)
        self.assertNotIn('owner', clean)

    def test_M02_13_revocation_avant_le_demarrage(self):
        _requete(self.state, AVOCATE)
        recu = a35.soumettre(self.d, 'calculate35', _calcul('DOS-001'))
        membres.clear()                                                   # fin de la requête : le travailleur n'a pas de périmètre
        membres.set_reserve(self.state, 'DOS-001', ['autre-avocat@example.test'])
        avant = self.projets()
        _travailler(self.d)
        row = self.d.db.execute('SELECT status,result FROM jobs WHERE id=?', (recu['job_id'],)).fetchone()
        self.assertEqual(row['status'], 'error')
        self.assertIn(json.loads(row['result'])['erreur'], ('dossier_absent', 'dossier_reserve'))
        self.assertEqual(self.projets(), avant)

    def test_M02_14_revocation_pendant_la_preparation(self):
        _requete(self.state, AVOCATE)
        recu = a35.soumettre(self.d, 'calculate35', _calcul('DOS-001', 3))
        membres.clear()
        from agent import pause5629
        original = pause5629.checkpoint

        def revoquer(desk):   # la révocation arrive pendant le travail, juste avant l'écriture
            membres.set_reserve(self.state, 'DOS-001', ['autre-avocat@example.test'])
            return original(desk)
        avant = self.projets()
        with patch.object(pause5629, 'checkpoint', revoquer):
            _travailler(self.d)
        row = self.d.db.execute('SELECT status,result FROM jobs WHERE id=?', (recu['job_id'],)).fetchone()
        self.assertEqual(row['status'], 'error', row['result'])
        self.assertEqual(self.projets(), avant, 'aucune écriture sous l’ancien périmètre')
        self.assertIsNone(membres._viewer.get(), 'le périmètre du travail ne reste pas sur le fil du travailleur')

    def test_M02_15_mutation_sans_recontrole_avant_effet_ecrit(self):
        """Contre-épreuve : sans le contrôle juste avant l'effet, la même révocation laisse écrire (le test précédent le détecte)."""
        _requete(self.state, AVOCATE)
        a35.soumettre(self.d, 'calculate35', _calcul('DOS-001', 4))
        membres.clear()
        from agent import pause5629
        original = pause5629.checkpoint

        def revoquer(desk):
            membres.set_reserve(self.state, 'DOS-001', ['autre-avocat@example.test'])
            return original(desk)
        avant = self.projets()
        with patch.object(pause5629, 'checkpoint', revoquer), patch.object(a35, '_avant_effet', lambda desk, mid: revoquer(desk)):
            _travailler(self.d)
        self.assertEqual(self.projets(), avant + 1)

    def test_M02_15b_mutation_sans_perimetre_au_demarrage_ecrit(self):
        """Contre-épreuve de M02_13 : si le travail ne reprenait pas le périmètre de son auteur, le dossier révoqué serait écrit."""
        _requete(self.state, AVOCATE)
        a35.soumettre(self.d, 'calculate35', _calcul('DOS-001', 13))
        membres.clear()
        membres.set_reserve(self.state, 'DOS-001', ['autre-avocat@example.test'])
        avant = self.projets()
        sans = lambda desk, auteur, kind: {'owner': 'cabinet', 'role': 'administrateur', 'masques': frozenset()}   # noqa: E731
        with patch.object(a35, '_verifier_auteur', sans):
            _travailler(self.d)
        self.assertEqual(self.projets(), avant + 1)

    def test_M02_16_travail_sans_auteur_retenu_si_un_dossier_est_reserve(self):
        job = self.d.enqueue('calculate35', _calcul('DOS-002', 5))
        avant = self.projets()
        _travailler(self.d)
        row = self.d.db.execute('SELECT status,result FROM jobs WHERE id=?', (job,)).fetchone()
        self.assertEqual((row['status'], json.loads(row['result'])['erreur']), ('error', 'auteur_non_verifiable'))
        self.assertEqual(self.projets(), avant)
        # sans aucun dossier réservé, rien à protéger : comportement d'avant (API interne, tests 3.5)
        membres.set_reserve(self.state, 'DOS-001', [])
        job = self.d.enqueue('calculate35', _calcul('DOS-002', 6))
        _travailler(self.d)
        self.assertEqual(self.d.db.execute('SELECT status FROM jobs WHERE id=?', (job,)).fetchone()[0], 'done')

    def test_M02_17_auteur_falsifie_dans_la_file_retenu(self):
        job = self.d.enqueue('calculate35', {**_calcul('DOS-002', 7), a35.AUTEUR: {'owner': '', 'role': 'administrateur'}})
        _travailler(self.d)
        self.assertEqual(json.loads(self.d.db.execute('SELECT result FROM jobs WHERE id=?', (job,)).fetchone()[0])['erreur'], 'auteur_non_verifiable')

    def test_M02_18_perimetre_de_l_auteur_applique_au_travail(self):
        _requete(self.state, AUTRE)
        recu = a35.soumettre(self.d, 'calculate35', _calcul('DOS-002', 8))
        membres.clear()
        _travailler(self.d)
        row = self.d.db.execute('SELECT status,result FROM jobs WHERE id=?', (recu['job_id'],)).fetchone()
        self.assertEqual(row['status'], 'done', row['result'])
        self.assertEqual(json.loads(row['result'])['matter'], 'DOS-002')
        # un travail noté pour AUTRE qui vise le dossier réservé (file modifiée) n'est pas exécuté avec les droits du cabinet
        job = self.d.enqueue('calculate35', {**_calcul('DOS-001', 9), a35.AUTEUR: json.loads(
            self.d.db.execute('SELECT args FROM jobs WHERE id=?', (recu['job_id'],)).fetchone()[0])[a35.AUTEUR]})
        avant = self.projets()
        _travailler(self.d)
        self.assertEqual(self.d.db.execute('SELECT status FROM jobs WHERE id=?', (job,)).fetchone()[0], 'error')
        self.assertEqual(self.projets(), avant)

    def test_M02_19_recu_idempotent(self):
        _requete(self.state, AUTRE)
        cle = 'recu-fictif-0123456789'
        un = a35.soumettre(self.d, 'calculate35', {**_calcul('DOS-002', 10), 'recu': cle})
        deux = a35.soumettre(self.d, 'calculate35', {**_calcul('DOS-002', 10), 'recu': cle})
        self.assertEqual(un['job_id'], deux['job_id'])
        self.assertTrue(deux['deja_recu'])
        with self.assertRaisesRegex(Stop, '^cle_reutilisee$'):
            a35.soumettre(self.d, 'calculate35', {**_calcul('DOS-002', 11), 'recu': cle})
        _requete(self.state, AVOCATE)
        with self.assertRaisesRegex(Stop, '^cle_reutilisee$'):
            a35.soumettre(self.d, 'calculate35', {**_calcul('DOS-002', 10), 'recu': cle})
        self.assertEqual(self.d.db.execute("SELECT count(*) FROM jobs WHERE kind='calculate35'").fetchone()[0], 1)

    def test_M02_20_etat_d_une_demande_reserve_a_son_auteur(self):
        _requete(self.state, AUTRE)
        recu = a35.soumettre(self.d, 'calculate35', _calcul('DOS-002', 12))
        self.assertEqual(a35.demande(self.d, recu['job_id'])['status'], 'pending')
        _requete(self.state, 'tierce@example.test')
        self.assertIsNone(a35.demande(self.d, recu['job_id']))
        self.assertIn('Demande introuvable', self.page({'job': str(recu['job_id'])}))


# ------------------------------------------------------------------------------------------------ parcours
class Parcours(Base):
    def setUp(self):
        super().setUp()
        _requete(self.state, AUTRE)

    def test_P1_cinq_entrees_sans_surtitre_de_version(self):
        page = self.page({})
        for titre in ('Préparer une audience', 'Examiner les arguments', 'Préparer un appel', 'Effectuer un calcul', 'Revoir la facturation'):
            self.assertIn(titre, page)
        self.assertEqual(page.count('class="as108-carte"'), 5)
        self.assertNotIn('VERSION 3.5.0', page)
        self.assertNotIn('<pre>', page)
        self.assertIn('/static/v56108-assistance.js', page)
        self.assertNotRegex(page.split('data-as108>', 1)[1].split('v56108-assistance.js', 1)[0], r'<script(?![^>]*\bsrc=)[^>]*>', 'aucun script en ligne (CSP)')
        with patch.object(interface56100, 'active', return_value=False):
            ancienne = self.page({})
        self.assertIn('Plaidoirie et coaching', ancienne)
        self.assertNotIn('as108-carte', ancienne)
        self.assertNotIn('VERSION 3.5.0', ancienne)

    def test_P2_get_n_ecrit_rien_et_liste_fermee(self):
        compte = lambda: (self.d.db.execute('SELECT count(*) FROM jobs').fetchone()[0], self.projets(),   # noqa: E731
                          self.d.db.execute('SELECT count(*) FROM assistance_recus56108').fetchone()[0])
        avant = compte()
        for outil in ('audience', 'arguments', 'appel', 'calcul', 'facturation', 'inconnu', '<script>'):
            for extra in ({}, {'matter': 'DOS-002'}, {'matter': 'DOS-002', 'type': 'jours'}):
                page = self.page({'outil': outil, **extra})
                self.assertEqual('as108-parcours' in page, outil in a108.NOMS, outil)
        self.assertEqual(compte(), avant)

    def test_P3_selecteur_limite_aux_dossiers_autorises(self):
        page = self.page({'outil': 'calcul', 'matter': 'DOS-001'})
        options = re.findall(r'<option value="([^"]*)"', page.split('id="as108-matter"', 1)[1].split('</select>', 1)[0])
        self.assertEqual(options, ['', 'DOS-002'])
        self.assertIn('Dossier introuvable parmi vos dossiers', page)
        self.assertIn('Choisissez d’abord un dossier', page)
        self.assertNotIn('Projet introuvable', page)
        page = self.page({'outil': 'calcul', 'matter': 'DOS-002'})
        self.assertIn('<option value="DOS-002" selected>MARTIN', page)

    def test_P4_audience_choisie_par_son_nom_sans_identifiant_a_recopier(self):
        pid = 'c' * 32
        prep = {'title': 'Audience fictive MARTIN', 'procedural_context': 'Tribunal judiciaire de Valbrune, RG 24/01234'}
        self.d.db.execute('''INSERT INTO hearing_projects_v250 (id,matter,status,instruction,data,preview_hash,challenge_hash,created,expires,decided,result)
          VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (pid, 'DOS-002', 'pending', '', json.dumps({'preparation': prep}), '', '', '2026-11-02T09:00:00+00:00', '', '', ''))
        self.d.db.commit()
        page = self.page({'outil': 'audience', 'matter': 'DOS-002'})
        self.assertIn('<option value="%s">Audience fictive MARTIN · Tribunal judiciaire de Valbrune' % pid, page)
        self.assertNotIn('pattern="[a-f0-9]{32}"', page)
        self.assertIn('Ouvrir la préparation d’audience du dossier', page)

    def test_P5_calcul_seulement_les_parametres_du_type(self):
        jours = self.page({'outil': 'calcul', 'matter': 'DOS-002', 'type': 'jours'})
        self.assertIn('name="days"', jours)
        self.assertNotIn('name="principal"', jours)
        self.assertIn('pas un délai de procédure', jours)
        interets = self.page({'outil': 'calcul', 'matter': 'DOS-002', 'type': 'interets'})
        self.assertIn('name="principal"', interets)
        self.assertNotIn('name="days"', interets)
        self.assertIn('name="calculation_type" value="simple_interest"', interets)

    def test_P6_addition_de_jours_jamais_un_delai_et_ia_en_pause(self):
        self.d.setting('missions567:pause', True)   # IA en pause : le calcul déterministe passe quand même
        recu = a35.soumettre(self.d, 'calculate35', _calcul('DOS-002', 30))
        membres.clear()
        echeances = lambda: self.d.db.execute("SELECT count(*) FROM sqlite_master WHERE name='deadlines450'").fetchone()[0] and \
            self.d.db.execute('SELECT count(*) FROM deadlines450').fetchone()[0]   # noqa: E731
        avant = echeances()
        _travailler(self.d)
        row = self.d.db.execute('SELECT status,result FROM jobs WHERE id=?', (recu['job_id'],)).fetchone()
        self.assertEqual(row['status'], 'done', row['result'])
        self.assertEqual(echeances(), avant)
        _requete(self.state, AUTRE)
        page = self.page({'job': str(recu['job_id']), 'matter': 'DOS-002', 'outil': 'calcul'})
        self.assertIn('Demande n° %d · Résultat prêt' % recu['job_id'], page)
        self.assertIn('ce n’est pas une échéance de procédure', page)
        self.assertIn('31/01/2026', page)

    def test_P7_resultat_lisible_et_json_en_diagnostic(self):
        r = a35.calculate(self.d, {**_calcul('DOS-002'), 'calculation_type': 'rent_indexation', 'rent': '1000', 'base_index': '100', 'new_index': '125'})
        page = self.page({'project': r['project_id'], 'matter': 'DOS-002'})
        resultat = page.split('class="as108-resultat"', 1)[1]
        self.assertIn('Nouveau loyer : 1250.00 EUR', resultat)
        self.assertIn('Paramètres et formule', resultat)
        self.assertIn('Ouvrir la source', resultat)
        self.assertIn('Modifier les données', resultat)
        self.assertIn('Reprendre le parcours', resultat)
        self.assertIn('Exporter (texte)', resultat)
        self.assertLess(resultat.index('Nouveau loyer'), resultat.index('as108-diagnostic'))
        self.assertEqual(page.count('<pre>'), 1)
        self.assertIn('<details class="as108-diagnostic"><summary>Diagnostic avancé', page)

    def test_P8_appel_aucun_numero_compose(self):
        page = self.page({'outil': 'appel', 'matter': 'DOS-002'})
        self.assertIn('<option value="martin@example.test">Paul MARTIN · client</option>', page)
        self.assertIn('Aucun appel n’est lancé', page)
        self.assertNotIn('tel:', page)

    def test_P9_echappement_et_valeur_recherchee(self):
        page = self.page({'matter': '<script>alert(1)</script>', 'outil': 'calcul'})
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertIn('value="&lt;script&gt;alert(1)&lt;/script&gt;"', page)
        html = a108.resultat_html({'kind': 'compte_rendu_appel', 'notes': '<script>x</script>'}, '<img src=x onerror=y>', lambda p, **k: p, 'DOS-002')
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img', html)
        html = a108.resultat_html({'kind': 'calcul', 'source': {'url': 'javascript:alert(1)'}, 'result': {'arithmetic_date': '2026-01-02'}},
                                  'Fictif', lambda p, **k: p, 'DOS-002')
        self.assertNotIn('href="javascript:', html)

    def test_P10_facturation_sans_faux_zero(self):
        with patch('agent.workstation.unpaid_summary', side_effect=Stop('cache_illisible')):
            page = self.page({'outil': 'facturation', 'matter': 'DOS-002'})
        self.assertIn('État de la facturation indisponible', page)
        self.assertNotIn('Aucun solde ouvert', page)
        page = self.page({'outil': 'facturation', 'matter': 'DOS-002'})
        self.assertIn('Ouvrir la Facturation pour ce dossier', page)
        self.assertIn('vue=dossiers&amp;q=DOS-002', page)

    def test_P11_export_texte(self):
        r = a35.calculate(self.d, _calcul('DOS-002', 15))
        out = a108.api({}, self.d, {}, '', 'm56108/assistance/export', {'id': r['project_id']}, 'GET')
        self.assertEqual(out['status'], '200 OK')
        self.assertTrue(out['kind'].startswith('text/plain'))
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        self.assertIn('Date arithmétique : 2026-01-16', out['body'])
        self.assertNotIn(AUTRE, out['body'])

    def test_P12_actions_deterministes_et_statiques(self):
        self.assertTrue(set(a35.ACTIONS) <= arret5643.DETERMINISTIC)
        self.assertTrue(set(a35.ACTIONS) <= arret5643.SANS_MODELE)
        from agent import shell501, web440
        self.assertIn('v56108-assistance.css', shell501.BUNDLE)
        self.assertEqual(web440.STATIC.get('/static/v56108-assistance.js'), 'text/javascript')
        racine = Path(a108.__file__).parent / 'static'
        self.assertIn('as108-carte', (racine / 'app520.css').read_text(encoding='utf-8'))
        self.assertNotIn('innerHTML', (racine / 'v56108-assistance.js').read_text(encoding='utf-8'))

    def test_P13_libelles_traduits(self):
        racine = Path(a108.__file__).parent / 'locales'
        for langue in ('en', 'de', 'es'):
            ecrans = json.loads((racine / ('ecrans_%s.json' % langue)).read_text(encoding='utf-8'))
            for _cle, titre, _aide in a108.OUTILS:
                self.assertTrue(ecrans.get(titre), (langue, titre))
            for texte in ('Assistance métier', 'Que souhaitez-vous préparer ?'):
                self.assertTrue(ecrans.get(texte), (langue, texte))


# ------------------------------------------------------------------------------------------------ application réelle (WSGI)
class Web(unittest.TestCase):
    def setUp(self):
        self.w = t50.ServerAccounts('run')
        self.w.setUp()
        self.addCleanup(self.w.doCleanups)
        self.f, self.state, self.request = self.w.f, self.w.state, self.w.request
        _ajouter_dossier(self.f.c)
        self.addCleanup(membres.clear)
        self.admin = self.w.admin_cookie()
        self.lawyer = self.w.add(self.admin, AUTRE, 'avocat')
        membres.clear()
        d = Desk(self.f.c)
        self.addCleanup(d.db.close)
        self.d = d
        self.p = a35._save(d, 'appel', 'DOS-001', {'test': 'reserve-web'}, {'status': 'projet_interne', 'purpose': MARQUEUR})
        membres.set_reserve(str(self.state), 'DOS-001', [AVOCATE])

    def _csrf(self, body):
        return re.search(r'name="csrf" value="([^"]+)"', body).group(1)

    def test_W1_page_api_lien_direct_export_et_source(self):
        page = self.request('/assistance-metier', cookie=self.lawyer)
        self.assertEqual(page['status'], '200 OK')
        self.assertNotIn(MARQUEUR, page['body'])
        lien = self.request('/assistance-metier', query='project=' + self.p['project_id'], cookie=self.lawyer)
        self.assertNotIn(MARQUEUR, lien['body'])
        export = self.request('/api440/m56108/assistance/export', query='id=' + self.p['project_id'], cookie=self.lawyer)
        inconnu = self.request('/api440/m56108/assistance/export', query='id=' + 'f' * 32, cookie=self.lawyer)
        self.assertEqual((export['status'], export['body']), (inconnu['status'], inconnu['body']))
        self.assertTrue(export['status'].startswith('404'))
        self.assertEqual(export['headers'].get('Cache-Control'), 'private, no-store')
        source = self.request('/dossier/fichier', query='matter=DOS-001&path=/Dossiers/DEMO/piece.pdf', cookie=self.lawyer)
        self.assertIn(source['status'][:3], ('403', '404'))
        admin = self.request('/api440/m56108/assistance/export', query='id=' + self.p['project_id'], cookie=self.admin)
        self.assertEqual(admin['status'], '200 OK')
        self.assertIn(MARQUEUR, admin['body'])

    def test_W2_soumission_recu_etat_et_double_clic(self):
        page = self.request('/assistance-metier', query='outil=calcul&matter=DOS-002&type=jours', cookie=self.lawyer)
        self.assertEqual(page['status'], '200 OK', page['body'][:300])
        recu = re.search(r'name="recu" value="([^"]+)"', page['body']).group(1)
        form = {'csrf': self._csrf(page['body']), 'action': 'calculate35', 'recu': recu, 'outil': 'calcul', 'type': 'jours', 'matter': 'DOS-002',
                'calculation_type': 'calendar_days', 'start_date': '2026-02-28', 'days': '1', 'source_url': SOURCE, 'source_reference': REF,
                a35.AUTEUR: 'cabinet'}
        un = self.request('/action', 'POST', form, cookie=self.lawyer)
        deux = self.request('/action', 'POST', form, cookie=self.lawyer)
        self.assertEqual(un['status'][:3], '303', un['body'][:300])
        self.assertEqual(un['headers']['Location'], deux['headers']['Location'])
        self.assertIn('outil=calcul', un['headers']['Location'])
        job = int(re.search(r'job=(\d+)', un['headers']['Location']).group(1))
        self.assertEqual(self.d.db.execute("SELECT count(*) FROM jobs WHERE kind='calculate35'").fetchone()[0], 1)
        args = json.loads(self.d.db.execute('SELECT args FROM jobs WHERE id=?', (job,)).fetchone()[0])
        self.assertEqual(args[a35.AUTEUR]['owner'], AUTRE)
        attente = self.request('/assistance-metier', query='job=%d&matter=DOS-002&outil=calcul' % job, cookie=self.lawyer)
        self.assertIn('Demande n° %d · Reçue, en attente de traitement' % job, attente['body'])
        self.assertIn('data-as108-attente', attente['body'])
        _travailler(self.d)
        fini = self.request('/assistance-metier', query='job=%d&matter=DOS-002&outil=calcul' % job, cookie=self.lawyer)
        self.assertIn('Résultat prêt', fini['body'])
        self.assertIn('Date arithmétique : 01/03/2026', fini['body'])
        autre = self.request('/assistance-metier', query='job=%d' % job, cookie=self.admin)
        self.assertIn('Résultat prêt', autre['body'])   # administrateur : état visible

    def test_W3_compte_desactive_avant_l_execution(self):
        page = self.request('/assistance-metier', query='outil=calcul&matter=DOS-002&type=jours', cookie=self.lawyer)
        form = {'csrf': self._csrf(page['body']), 'action': 'calculate35', 'recu': 'recu-web-desactive-01', 'matter': 'DOS-002',
                'calculation_type': 'calendar_days', 'start_date': '2026-03-01', 'days': '2', 'source_url': SOURCE, 'source_reference': REF}
        r = self.request('/action', 'POST', form, cookie=self.lawyer)
        job = int(re.search(r'job=(\d+)', r['headers']['Location']).group(1))
        from agent import connexion5648 as cx
        db = cx.connect(str(self.state))
        try:
            db.execute('UPDATE users SET active=0 WHERE ident=?', (AUTRE,))
            db.commit()
        finally:
            db.close()
        avant = self.d.db.execute('SELECT count(*) FROM assistance_projects_v350').fetchone()[0]
        _travailler(self.d)
        row = self.d.db.execute('SELECT status,result FROM jobs WHERE id=?', (job,)).fetchone()
        self.assertEqual(row['status'], 'error')
        self.assertIn(json.loads(row['result'])['erreur'], ('compte_desactive', 'compte_introuvable'))
        self.assertEqual(self.d.db.execute('SELECT count(*) FROM assistance_projects_v350').fetchone()[0], avant)
        page = self.request('/assistance-metier', query='job=%d' % job, cookie=self.admin)
        self.assertIn('désactivé', page['body'])

    def test_W4_dossier_reserve_designe_par_le_formulaire(self):
        page = self.request('/assistance-metier', query='outil=calcul&matter=DOS-002&type=jours', cookie=self.lawyer)
        form = {'csrf': self._csrf(page['body']), 'action': 'calculate35', 'matter': 'DOS-001', 'calculation_type': 'calendar_days',
                'start_date': '2026-03-01', 'days': '2', 'source_url': SOURCE, 'source_reference': REF}
        r = self.request('/action', 'POST', form, cookie=self.lawyer)
        self.assertIn(r['status'][:3], ('403', '404'))
        self.assertEqual(self.d.db.execute("SELECT count(*) FROM jobs WHERE kind='calculate35'").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
