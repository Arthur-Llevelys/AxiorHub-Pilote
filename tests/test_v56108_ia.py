"""5.6.108, lot I : ce que reçoivent les petits modèles d'IA locaux (compétence « Assistant juridique »).

IA01 fiches enregistrées et transmises ; IA02 ordre de chargement réservé et déclaratif ; IA03 version courte ; IA04 Assistant juridique
d'office pour les travaux d'analyse, droit étranger ; IA05 grille avant rédaction et questions ; IA06 trace de ce qui part réellement.
Données fictives, faux fournisseurs : aucun appel réseau, aucun modèle réel. Ces tests vérifient le chargement, pas la qualité juridique."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_v5689_ia as base
from test_v5689_ia import AVOCAT, CHEMIN, MID, Faux, muter
from agent import competences5632 as c32, competences5643 as c43, competences5683 as c83, grille56108 as gr, missions567, model, ui_ai5689 as ia
from agent.common import Stop, private_json

SID = 'builtin.assistant_juridique'
PAQUET = c32.ROOT / 'assistant-juridique'
FICHES = sorted(p.name for p in (PAQUET / 'origine' / 'references').glob('*.md'))
CONSULTATION = 'Consultation : la société ZORVAL peut-elle obtenir la résiliation du bail commercial conclu avec KERVILAC ? Vérifier la prescription.'


class Base(base.Base):
    def setUp(self):
        super().setUp()
        self.desk.setting('competences5632:auto', True)
        c32.ensure_builtin(self.desk)
        c83._CACHE.clear()
        self.addCleanup(c83._CACHE.clear)

    def row(self):
        return c32._row(self.desk, SID)

    def sel(self):
        r = self.row()
        return {'id': SID, 'version': r['version'], 'mode': 'phase', 'reason': 'Essai'}

    def detail(self, budget, phase='analyse', kind='prepare_legal_opinion', livrable='', query=CONSULTATION, droit=None):
        bloc, details, _ = c32._construire(self.desk, [self.sel()], budget, query, phase,
                                           {'kind': kind, 'livrable': livrable, 'droit': droit or {'valeur': '', 'source': '', 'juridiction': ''}})
        self.assertLessEqual(len(bloc), budget)                                  # en-tête et mention des omissions compris
        return details[0]['texte'], details[0]

    def registre(self, **champs):
        matters = json.loads(Path(self.f.c['matters_file']).read_text(encoding='utf-8'))
        for m in matters:
            if m['id'] == MID:
                m.update(champs)
        private_json(Path(self.f.c['matters_file']), matters)
        os.utime(self.f.c['matters_file'], None)


# ------------------------------------------------------------------------------------------------ IA01 : fiches enregistrées
class Fiches(Base):
    def test_IA01_temoin_les_19_fiches_sont_enregistrees_avec_plan_et_version_courte(self):
        r = self.row()
        self.assertEqual(r['version'], '7.4.0+axiorhub.2')
        chemins = {x['path'] for x in c83.resources(self.desk, SID, r['version'])}
        self.assertEqual(len(FICHES), 19)
        self.assertEqual({p for p in chemins if p.startswith('origine/references/')}, {'origine/references/' + f for f in FICHES})
        self.assertIn('PLAN-AXIORHUB.json', chemins)
        self.assertIn('ESSENTIEL.md', chemins)
        self.assertFalse(any(p.endswith('.py') for p in chemins))

    def test_IA01_temoin_le_syllogisme_est_transmis_aux_trois_budgets(self):
        # Témoin de l'audit (temoins/test_fiches_juridiques.py) : MAJEURE / MINEURE absents à 12 000, 24 000 et 60 000 caractères sur la 5.6.107.
        for budget in (12_000, 24_000, 60_000):
            texte, d = self.detail(budget)
            self.assertIn('MAJEURE  :', texte, budget)
            self.assertIn('MINEURE  :', texte, budget)
            self.assertIn('tache-2-cas-pratique.md', d['fiches'], budget)

    def test_IA01_empreinte_couvre_instructions_et_fiches(self):
        r = self.row()
        files = c83.ressources_declarees(PAQUET, json.loads(r['manifest']))
        self.assertEqual(r['sha256'], c83.empreinte_paquet(r['instructions'], files))
        self.assertNotEqual(r['sha256'], c32._sha(r['instructions']))
        modifie = [dict(f, sha256='0' * 64) if f['path'].endswith('tache-2-cas-pratique.md') else f for f in files]
        self.assertNotEqual(c83.empreinte_paquet(r['instructions'], modifie), r['sha256'])
        self.assertEqual(c83.empreinte_paquet('abc', []), c32._sha('abc'))     # paquet sans ressource : empreinte historique inchangée

    def test_IA01_mise_a_jour_conserve_desactivation_choix_general_copie_et_mission_figee(self):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        reglages = {}
        desk = SimpleNamespace(db=db, settings=lambda k, d=None: reglages.get(k, d), setting=reglages.__setitem__, audit=lambda *a, **k: None)
        c32.schema(desk)
        ancien = json.loads((PAQUET / 'manifest.json').read_text(encoding='utf-8'))
        ancien['version'] = '7.4.0+axiorhub.1'
        db.execute('INSERT INTO skills5632 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (SID, ancien['version'], 'builtin', 'cabinet', 'cabinet', 'Assistant juridique', '', json.dumps(ancien),
                                                                              'Instructions anciennes fictives.', c32._sha('Instructions anciennes fictives.'), '', '2026-10-01'))
        db.execute('INSERT INTO skill_state5632 VALUES(?,?,?,?,?,1,?)', (SID, ancien['version'], 0, 'cabinet', 'cabinet', '2026-10-01'))
        db.execute('INSERT INTO mission_skills5689 VALUES(?,?,?,?,?,?)', ('mission-ancienne', SID, ancien['version'], c32._sha('Instructions anciennes fictives.'), 'analyse', '2026-10-02'))
        db.commit()
        reglages[c43.GENERAL_SETTING] = 'perso.methode-du-cabinet'
        c32.ensure_builtin(desk)
        etat = db.execute('SELECT * FROM skill_state5632 WHERE id=?', (SID,)).fetchone()
        self.assertEqual((etat['active_version'], etat['enabled'], etat['user_choice']), ('7.4.0+axiorhub.2', 0, 1))   # désactivation conservée
        self.assertEqual(db.execute('SELECT version FROM mission_skills5689 WHERE mission=?', ('mission-ancienne',)).fetchone()[0], ancien['version'])
        self.assertEqual(db.execute('SELECT instructions FROM skills5632 WHERE id=? AND version=?', (SID, ancien['version'])).fetchone()[0], 'Instructions anciennes fictives.')
        self.assertEqual(reglages[c43.GENERAL_SETTING], 'perso.methode-du-cabinet')
        copie = c32.copy(desk, 'avocate@example.test', SID)                     # une copie personnelle garde fiches, plan et version courte
        self.assertEqual(len(c83.resources(desk, copie['id'], '1.0.0')), len(c83.resources(desk, SID, '7.4.0+axiorhub.2')))
        c32.ensure_builtin(desk)                                                 # rien n'est réécrit au passage suivant
        self.assertEqual(db.execute('SELECT COUNT(*) FROM skills5632 WHERE id=?', (SID,)).fetchone()[0], 2)

    def paquet(self, manifest, fichiers):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        racine = Path(tmp.name) / 'paquet'
        for rel, texte in fichiers.items():
            p = racine / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(texte, encoding='utf-8')
        racine.mkdir(exist_ok=True)
        return racine

    def test_IA01_fiche_hors_paquet_refusee(self):
        racine = self.paquet({}, {'refs/fiche.md': 'Fiche fictive.'})
        (racine.parent / 'dehors.md').write_text('Texte hors paquet fictif.', encoding='utf-8')
        for refs in ('../', ['../dehors.md'], ['/etc/passwd'], ['refs\\fiche.md'], 'C:/Windows'):
            with self.subTest(refs=refs), self.assertRaises(Stop):
                c83.ressources_declarees(racine, {'references': refs})

    def test_IA01_fiche_hors_paquet_refusee_mutation(self):
        racine = self.paquet({}, {'refs/fiche.md': 'Fiche fictive.'})
        (racine.parent / 'dehors.md').write_text('Texte hors paquet fictif.', encoding='utf-8')
        # Copie en mémoire sans les deux garde-fous (chemin propre, confinement à la racine) : la fiche hors paquet serait lue,
        # donc le test précédent échouerait si l'un et l'autre étaient retirés.
        gardes = ("    if not text or text.startswith('/') or '\\\\' in text or re.match(r'^[A-Za-z]:', text) or '..' in path.parts:\n        raise Stop('chemin_archive_competence_refuse')",
                  "    if cible != racine and racine not in cible.parents:\n        raise Stop('ressource_competence_refusee')")
        source = Path(c83.__file__).read_text(encoding='utf-8')
        for garde in gardes:
            self.assertIn(garde, source)
            source = source.replace(garde, '    pass')
        espace = {'__name__': c83.__name__, '__package__': c83.__package__, '__file__': c83.__file__}
        exec(compile(source, c83.__file__, 'exec'), espace)   # noqa: S102 — source du dépôt, en mémoire ; l'arbre n'est pas modifié
        lus = espace['ressources_declarees'](racine, {'references': ['../dehors.md']})
        self.assertEqual([f['data'] for f in lus], ['Texte hors paquet fictif.'.encode()])

    def test_IA01_lien_symbolique_refuse(self):
        racine = self.paquet({}, {'refs/fiche.md': 'Fiche fictive.'})
        cible = racine.parent / 'secret.md'
        cible.write_text('Secret fictif.', encoding='utf-8')
        try:
            os.symlink(cible, racine / 'refs' / 'lien.md')
        except (OSError, NotImplementedError):
            self.skipTest('liens symboliques non autorisés sur ce poste')
        with self.assertRaises(Stop):
            c83.ressources_declarees(racine, {'references': 'refs'})

    def test_IA01_script_et_fichier_exclu_jamais_enregistres(self):
        racine = self.paquet({}, {'refs/fiche.md': 'Fiche fictive.', 'refs/outil.py': 'print("jamais exécuté")', 'refs/lanceur.sh': 'echo non', 'refs/exclue.md': 'Exclue.',
                                  'refs/image.png': 'pas une fiche'})
        lus = c83.ressources_declarees(racine, {'references': 'refs', 'excluded_files': ['refs/exclue.md']})
        self.assertEqual([f['path'] for f in lus], ['refs/fiche.md'])
        with muter(c83, 'if rel in exclus or suffixe in EXECUTABLE or suffixe not in PAQUET_TYPES:', 'if rel in exclus or suffixe not in {**PAQUET_TYPES, ".py": 1, ".sh": 1}:') as copie:
            copie.PAQUET_TYPES = {**c83.PAQUET_TYPES, '.py': 'texte', '.sh': 'texte'}
            self.assertIn('refs/outil.py', [f['path'] for f in copie.ressources_declarees(racine, {'references': 'refs'})])   # sans le garde-fou, le script serait enregistré

    def test_IA01_paquet_refuse_garde_la_version_precedente(self):
        racine = self.paquet({}, {'manifest.json': json.dumps({'id': 'builtin.essai', 'name': 'Essai', 'version': '2.0.0', 'instructions': ['SKILL.md'], 'references': '../'}),
                                  'SKILL.md': '# Essai\nMéthode fictive.'})
        with patch.object(c32, 'ROOT', racine.parent), patch.object(c32, 'BUILTIN', (racine.name,)):
            self.assertEqual(c32.ensure_builtin(self.desk), [])
        self.assertIsNone(self.desk.db.execute("SELECT 1 FROM skills5632 WHERE id='builtin.essai'").fetchone())


# ------------------------------------------------------------------------------------------------ IA02 : ordre réservé
class Ordre(Base):
    def test_IA02_adaptation_puis_sections_de_l_etape_puis_fiche(self):
        texte, d = self.detail(24_000)
        self.assertEqual(d['forme'], 'complete')
        reperes = [texte.index(x) for x in ('# Adaptation AxiorHub', '## §2 —', '## §7 —', '## §8 —', '## §9 —', '## §10 —', '### Fiche tache-2-cas-pratique.md')]
        self.assertEqual(reperes, sorted(reperes))
        self.assertEqual(d['sections'][:6], ['adaptation AxiorHub', '§2 — Règle cardinale : anti-hallucination', '§7 — Application de la loi dans le temps',
                                             '§8 — Qualification et hiérarchie des normes', '§9 — Jurisprudence : règle et illustration', '§10 — Degré de confiance'])

    def test_IA02_passages_sans_objet_jamais_transmis(self):
        for phase, kind in (('analyse', 'prepare_legal_opinion'), ('redaction_document', 'docrequest520'), ('controle', 'verify_deliverable420')):
            texte, d = self.detail(60_000, phase, kind, query=CONSULTATION + ' Themia HAL environnement scripts Cowork')
            for absent in ('§0 — Détection d', '§4 — Scan des fichiers', 'Étape 1 — Détecter l', 'Étape 2 — Scanner', 'Étape 4 — Vérifier les pré-requis',
                           'Étape 11 — Checklist', 'Fiche guide-themia.md', 'Fiche guide-hal.md'):
                self.assertNotIn(absent, texte, (phase, absent))
            self.assertIn('§0 — Détection d’environnement'.replace('’', "'"), d['sans_objet'])

    def test_IA02_etapes_du_cabinet(self):
        attendu = {'analyse': ('§2', '§7', '§8', '§9', '§10'), 'redaction_document': ('§2', '§8', '§6'), 'decision': ('§2', '§11'), 'controle': ('§2', '§7')}
        for phase, sections in attendu.items():
            _t, d = self.detail(40_000, phase, kind='', query='Question fictive')
            noyau = [s.split(' — ')[0] for s in d['sections'] if s.startswith('§') and s not in d['complements']]
            self.assertEqual(tuple(noyau), sections, phase)
        texte, d = self.detail(40_000, 'redaction_courriel', 'prepare_reply', query='Répondre à Maître BERNARD sur la date fictive')
        self.assertIn('forme courte pour un courriel', texte)
        self.assertNotIn('Convention de nommage', texte)                    # le §6 complet (Word, Cowork) n'accompagne pas un courriel
        texte, d = self.detail(40_000, 'controle', 'verify_deliverable420', query='Vérifier les références du projet fictif')
        self.assertEqual(d['fiches'][0], 'tache-9-verification-references.md (forme courte)')   # la fiche d'origine décrit un document Word édité en XML
        self.assertIn('Source non trouvée ne veut pas dire source fausse', texte)

    def test_IA02_fiche_choisie_par_type_et_livrable(self):
        plan = json.loads((PAQUET / 'PLAN-AXIORHUB.json').read_text(encoding='utf-8'))
        cas = ((('docrequest520', 'consultation', '', 'redaction_document'), 'tache-2-cas-pratique.md'), (('docrequest520', 'contrat', '', 'redaction_document'), 'tache-3-redaction-acte.md'),
               (('docrequest520', 'conclusions', '', 'redaction_document'), 'tache-3-redaction-acte.md'), (('attachment_review', '', '', 'analyse'), 'tache-4-analyse-pieces.md'),
               (('task5614', 'analyse de contrat', '', 'analyse'), 'tache-6-analyse-contrat.md'), (('task5614', 'contre-argumentaire', '', 'analyse'), 'tache-5-contre-argumentaire.md'),
               (('legal_research', '', '', 'analyse'), 'tache-1-recherches.md'), (('verify_deliverable420', '', '', 'controle'), 'tache-9-verification-references.md'),
               (('assistant_answer', '', 'Peux-tu vérifier les références de ce projet ?', 'analyse'), 'tache-9-verification-references.md'),
               (('assistant_answer', '', 'Question fictive', 'analyse'), 'tache-2-cas-pratique.md'), (('prepare_reply', '', 'Bonjour', 'redaction_courriel'), ''))
        for (kind, livrable, query, phase), fiche in cas:
            with self.subTest(kind=kind, livrable=livrable):
                self.assertEqual(c83.choisir_fiche(plan, kind, livrable, query, phase)[0], fiche)
                self.assertEqual(c83.choisir_fiche(plan, kind, livrable, query, phase), c83.choisir_fiche(plan, kind, livrable, query, phase))   # déterministe

    def test_IA02_jamais_coupe_et_omission_signalee(self):
        r = self.row()
        unites = c83._unites(c83._fichiers_instructions(r['instructions'], ['origine/SKILL.md', 'ADAPTATION.md'])['origine/SKILL.md'],
                             json.loads((PAQUET / 'PLAN-AXIORHUB.json').read_text(encoding='utf-8')))
        for phase, kind in (('controle', 'verify_deliverable420'), ('analyse', 'prepare_legal_opinion'), ('redaction_document', 'docrequest520')):
            for budget in (17_000, 24_000, 33_000):
                texte, d = self.detail(budget, phase, kind, query='Vérifier les références de la consultation et la jurisprudence citée')
                self.assertLessEqual(len(texte), budget)
                for u in unites:
                    if u['titre'] in d['sections']:
                        self.assertIn(u['texte'].replace('\n> **Pré-requis', ''), texte)   # une section transmise l'est en entier
        bloc = c32.instructions_for(self.desk, [self.sel()], 5_000, CONSULTATION, 'analyse', {'kind': 'prepare_legal_opinion'})
        self.assertIn('[Non transmis à cette étape, faute de place : adaptation AxiorHub.]', bloc)   # l'omission d'un élément réservé est dite au modèle
        self.assertLessEqual(len(bloc), 5_000)

    def test_IA02_fiche_sans_lignes_de_scripts_mais_regle_doctrinale_gardee(self):
        """Banc d'essai (llama 3.1 8B) : la fiche de recherche faisait recopier « scripts/doctrine_search.py » et « via HAL » dans la réponse.
        Ces lignes sont retirées des fiches ; la règle de fond du §2 (doctrine : identifiant vérifiable) reste transmise en entier."""
        texte, d = self.detail(60_000, 'analyse', 'legal_research', query='Recherche juridique : jurisprudence et doctrine sur la clause pénale')
        self.assertIn('### Fiche tache-1-recherches.md', texte)
        fiche = texte.split('### Fiche tache-1-recherches.md', 1)[1].split('\n### ', 1)[0]
        for absent in ('scripts/', 'via HAL', 'web_search', 'Minimum 10 sources doctrinales'):
            self.assertNotIn(absent, fiche, absent)
        self.assertIn('**Toute référence doctrinale citée dans un livrable doit avoir été trouvée par une recherche effective**', texte)
        plan = json.loads((PAQUET / 'PLAN-AXIORHUB.json').read_text(encoding='utf-8'))
        self.assertIn('scripts/', plan['sans_objet']['lignes_contenant'])   # déclaratif, dans le paquet

    def test_IA02_constats_du_banc_environnement_et_outils_non_connectes_retires(self):
        for phase, kind in (('analyse', 'attachment_review'), ('redaction_document', 'docrequest520'), ('controle', 'verify_deliverable420'), ('analyse', 'prepare_legal_opinion')):
            for budget in (9_000, 24_000, 60_000):
                texte, d = self.detail(budget, phase, kind, query=CONSULTATION + ' jurisprudence Cour de cassation statistiques pièces contrôle')
                for absent in ('Pré-requis environnement', 'Convention de nommage', '/mnt/user-data', 'Données jurimétriques et Cour de cassation', 'Articulation avec les autres compétences'):
                    self.assertNotIn(absent, texte, (phase, budget, absent))
                if d['forme'] == 'complete':
                    self.assertNotIn('CHAT_CU', texte.split('### Fiche')[0], (phase, budget))   # sections réservées sans consignes d'environnement

    def test_IA02_plan_declaratif_aucun_titre_dans_le_code(self):
        for module in (c83, c32, c43):
            source = Path(module.__file__).read_text(encoding='utf-8')
            for titre in ('§8', 'Qualification et hiérarchie', 'tache-2-cas-pratique', 'Détection d', 'guide-themia'):
                self.assertNotIn(titre, source, (module.__name__, titre))
        # Un autre plan (nouvelle version du paquet) change le chargement sans toucher au code.
        r = self.row()
        plan = json.loads((PAQUET / 'PLAN-AXIORHUB.json').read_text(encoding='utf-8'))
        plan['etapes']['analyse'] = {'sections': ['s3'], 'fiche_defaut': 'tache-7-veille-juridique.md'}
        self.desk.db.execute("UPDATE skill_resources5683 SET content=? WHERE skill=? AND version=? AND path='PLAN-AXIORHUB.json'", (json.dumps(plan).encode(), SID, r['version']))
        self.desk.db.commit()
        c83._CACHE.clear()
        texte, d = self.detail(24_000, kind='', query='Question fictive')
        self.assertIn('## §3 —', texte)
        self.assertNotIn('## §8 —', texte)
        self.assertEqual(d['fiche_tache'], 'tache-7-veille-juridique.md')


# ------------------------------------------------------------------------------------------------ IA03 : version courte
class VersionCourte(Base):
    def test_IA03_version_courte_derivee_et_attribuee(self):
        texte = (PAQUET / 'ESSENTIEL.md').read_text(encoding='utf-8')
        self.assertTrue(2_500 <= len(texte) <= 4_500, len(texte))
        for attendu in ('Christophe Quézel-Ambrunaz', 'AGPL-3.0', 'https://lawve.ai/@christophe-quezel-ambrunaz/skill/assistant-juridique', 'Adaptation courte AxiorHub',
                        'Chercher → Trouver → Lire → Vérifier → Citer', 'Aucune référence inventée', 'Majeure', 'Mineure', 'Conclusion', 'argument adverse',
                        'date des faits', 'question ciblée', 'Courriel : réponse directe', 'Contrat ou acte : des clauses', 'Aucun pourcentage',
                        'ne pas appliquer\nles références françaises'):
            self.assertIn(attendu, texte, attendu)
        manifest = json.loads((PAQUET / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['axiorhub']['version_courte'], 'ESSENTIEL.md')
        self.assertFalse((PAQUET / 'origine' / 'ESSENTIEL.md').exists())   # l'œuvre d'origine n'est pas modifiée

    def test_IA03_sous_le_seuil_la_version_courte_remplace_le_texte_tronque(self):
        essentiel = (PAQUET / 'ESSENTIEL.md').read_text(encoding='utf-8')
        texte, d = self.detail(9_500)
        self.assertEqual(d['forme'], 'courte')
        self.assertTrue(texte.startswith('# Adaptation AxiorHub'))            # ordre du texte : adaptation d'abord
        self.assertIn(essentiel.strip('\n'), texte)
        self.assertIn('### Fiche tache-2-cas-pratique.md (forme courte', texte)   # la fiche complète ne tient pas : sa forme courte suit
        self.assertNotIn('## §2 —', texte)
        texte, d = self.detail(5_000)
        self.assertEqual(d['forme'], 'courte')
        self.assertTrue(texte.startswith(essentiel.strip('\n')))              # la version courte passe avant tout le reste
        self.assertIn({'nom': 'adaptation AxiorHub', 'motif': 'place'}, d['omissions'])
        self.assertEqual(d['fiches'], ['tache-2-cas-pratique.md (forme courte)'])
        texte, d = self.detail(14_000)
        self.assertEqual(d['fiches'], ['tache-2-cas-pratique.md'])
        self.assertIn('MAJEURE  :', texte)
        self.desk.setting(c83.SEUIL_SETTING, 8_000)                             # seuil réglable
        self.assertEqual(self.detail(9_500)[1]['forme'], 'complete')

    def test_IA03_page_competences_dit_adaptation_courte_a_relire(self):
        ligne = next(s for s in c32.listing(self.desk, 'cabinet') if s['id'] == SID)
        html = c32.carte56108(ligne)
        self.assertIn('adaptation courte, à relire par l’avocat', html)
        self.assertIn('ESSENTIEL.md', html)
        from agent import web5632
        self.assertIn('carte56108(s)', Path(web5632.__file__).read_text(encoding='utf-8'))


# ------------------------------------------------------------------------------------------------ IA04 : d'office et droit étranger
class Office(Base):
    ANALYSES = ('legal_research', 'prepare_hearing', 'analyser_dossier5624', 'fiche_dossier5627', 'attachment_review', 'digest_pieces5632', 'refresh_brief')

    def chosen(self, kind, text='Texte fictif sans mot-clé'):
        return [s['id'] for s in c43.select(self.desk, text, c43.KIND_PHASE.get(kind, ''), 'cabinet', kind=kind)]

    def test_IA04_assistant_juridique_d_office_pour_les_analyses(self):
        for kind in self.ANALYSES:
            self.assertEqual(self.chosen(kind)[:1], [SID], kind)
        for kind in ('verify_deliverable420', 'verify_official_decision'):      # contrôle d'un projet : constat du banc d'essai
            self.assertEqual(self.chosen(kind)[:1], [SID], kind)
        for kind in ('orchestrate', 'proactive34_cycle', 'live_mail430', 'run', 'sync_legal_memory'):
            self.assertNotIn(SID, self.chosen(kind), kind)

    def test_IA04_methode_de_phase_en_complement_choix_et_desactivation_respectes(self):
        c32.set_enabled(self.desk, 'cabinet', 'builtin.methode_analyser', True)
        self.assertEqual(self.chosen('legal_research'), [SID, 'builtin.methode_analyser'])
        manuel = c43.select(self.desk, 'x', 'analyse', 'cabinet', manual=['builtin.methode_analyser'], kind='legal_research')
        self.assertEqual([s['id'] for s in manuel], ['builtin.methode_analyser'])
        self.desk.setting(c43.GENERAL_SETTING, 'builtin.methode_analyser')
        self.assertEqual(self.chosen('legal_research')[0], 'builtin.methode_analyser')
        self.desk.setting(c43.GENERAL_SETTING, None)
        c32.set_enabled(self.desk, 'cabinet', SID, False)
        self.assertNotIn(SID, self.chosen('legal_research'))
        c32.set_enabled(self.desk, 'cabinet', SID, True)
        self.desk.setting('competences5632:auto', False)
        self.assertEqual(self.chosen('legal_research'), [])

    def test_IA04_dossier_d_un_autre_droit_methode_generale_seulement(self):
        self.registre(droit_applicable='DE')
        contexte = c32._contexte(self.desk, 'prepare_legal_opinion', '', CONSULTATION, MID)
        self.assertEqual(contexte['droit']['valeur'], 'DE')
        texte = c32.instructions_for(self.desk, [self.sel()], 60_000, CONSULTATION, 'analyse', contexte)
        d = c32._ctx.details[0]
        self.assertEqual((d['forme'], d['raison'], d['droit']), ('methode_generale', 'droit_etranger', 'DE'))
        self.assertIn('autre droit que le droit français (DE)', texte)
        for absent in ('Légifrance', 'MAJEURE  :', 'art. 2224', 'C. civ.'):
            self.assertNotIn(absent, texte)
        self.assertEqual(d['fiches'], [])

    def test_IA04_droit_lu_dans_le_dossier_structure_et_la_juridiction(self):
        maintenant = '2026-11-18T09:00:00+00:00'
        for ident, cle, valeur in (('a1', 'juridiction', 'Landgericht Valbrune'),):
            self.desk.db.execute("INSERT INTO case_assertions5689(id,cabinet_id,matter,kind,statement,asserted_by,status,cle_valeur,valeur,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                 (ident, 'cabinet', MID, 'metadata', valeur, 'Avocat', 'validated', cle, valeur, maintenant, maintenant))
        self.desk.db.commit()
        plan = json.loads((PAQUET / 'PLAN-AXIORHUB.json').read_text(encoding='utf-8'))
        droit = c83.droit_du_dossier(self.desk, MID)
        self.assertEqual(c83.droit_etranger(droit, plan), 'DE')
        self.desk.db.execute("INSERT INTO case_assertions5689(id,cabinet_id,matter,kind,statement,asserted_by,status,cle_valeur,valeur,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                             ('a2', 'cabinet', MID, 'metadata', 'Droit français', 'Avocat', 'validated', 'droit_applicable', 'Droit français', maintenant, maintenant))
        self.desk.db.commit()
        self.assertEqual(c83.droit_etranger(c83.droit_du_dossier(self.desk, MID), plan), '')   # le droit déclaré prime sur la juridiction
        self.assertEqual(c83.droit_etranger({'valeur': '', 'juridiction': ''}, plan), '')          # rien de trouvé : aucun droit étranger présumé

    def test_IA04_droit_etranger_mutation(self):
        with muter(c83, '    etranger = droit_etranger(droit, plan)', "    etranger = ''") as copie:
            sel, r = self.sel(), self.row()
            d = copie.charger_plan(self.desk, sel, r, 60_000, {'phase': 'analyse', 'query': CONSULTATION, 'kind': 'prepare_legal_opinion', 'droit': {'valeur': 'DE'}})
        self.assertEqual(d['forme'], 'complete')                                 # sans le garde-fou, les références françaises partiraient
        self.assertIn('MAJEURE  :', d['texte'])


# ------------------------------------------------------------------------------------------------ IA05 : grille avant rédaction
GRILLE = {'faits': [{'texte': 'Bail commercial signé entre KERVILAC et ZORVAL.', 'nature': 'etabli', 'source_id': 'S1'},
                    {'texte': 'Le loyer serait impayé depuis trois mois.', 'nature': 'certain', 'source_id': 'S1'},
                    {'texte': 'Commandement délivré.', 'nature': 'etabli', 'source_id': 'S9'}],
          'preuves': [{'texte': 'Contrat de bail', 'source_id': 'S1'}, {'texte': 'Lettre inventée', 'source_id': 'X'}],
          'manques': [{'champ': 'Date de signature du bail', 'motif': 'Absente des pièces.', 'sections_bloquees': ['Prescription']}],
          'choix': [{'champ': 'Fondement retenu', 'options': ['Résiliation judiciaire', 'Clause résolutoire']}]}


class FauxPetit(Faux):
    """Petit modèle local simulé (fenêtre de 8 192 jetons) : il rend la grille, puis la sortie de la tâche."""

    def __init__(self, grille=None, **k):
        super().__init__(cfg={'provider_type': 'ollama', 'purpose': 'assistant', 'model': 'petit-modele-fictif', 'num_ctx': 8192}, **k)
        self.grille, self.grilles = grille or GRILLE, 0

    def complete(self, messages, temperature=None, max_tokens=0, json_schema=None):
        if 'tache' not in json.loads(messages[1]['content']):
            self.grilles += 1
            self.systemes.append(messages[0]['content'])
            self.charges.append(json.loads(messages[1]['content']))
            return json.dumps(self.grille, ensure_ascii=False)
        return super().complete(messages, temperature, max_tokens, json_schema)


class Grille(Base):
    def test_IA05_sources_verifiees_inconnues_retirees_et_comptees(self):
        grille, retires = gr.valider(GRILLE, [{'id': 'S1'}, {'id': 'D1'}])
        self.assertEqual(retires, 2)
        self.assertEqual([f['source_id'] for f in grille['faits']], ['S1', 'S1'])
        self.assertEqual(grille['faits'][1]['nature'], 'allegue')               # nature inconnue : rien n'est établi
        self.assertEqual(grille['manques'][0]['champ_id'], 'g_date_de_signature_du_bail')
        self.assertEqual(grille['choix'][0]['options'], ['Résiliation judiciaire', 'Clause résolutoire'])
        with muter(gr, '        if sid not in connues:\n            retires += 1\n            continue\n        nature', '        nature') as copie:
            self.assertEqual(len(copie.valider(GRILLE, [{'id': 'S1'}])[0]['faits']), 3)   # sans le contrôle, la source inventée passerait

    def test_IA05_petit_contexte_seulement_et_desactivable(self):
        self.assertTrue(gr.active(self.desk, FauxPetit(), 'analysis'))
        self.assertFalse(gr.active(self.desk, FauxPetit(), 'answer'))
        self.assertFalse(gr.active(self.desk, Faux(), 'analysis'))               # fenêtre inconnue : comportement inchangé
        self.assertFalse(gr.active(self.desk, SimpleNamespace(cfg={'num_ctx': 32768}), 'analysis'))
        self.desk.setting(gr.SETTING, False)
        self.assertFalse(gr.active(self.desk, FauxPetit(), 'analysis'))

    def test_IA05_grille_puis_redaction_questions_une_seule_fois(self):
        self.doc_indexe()
        modele = FauxPetit(defaut={'texte': 'Chronologie fictive.'})
        m = self.lancer('chronologie', objets=[{'id': 'doc:' + CHEMIN, 'version_id': ''}], modele=modele, cle='cle-grille-ia05-000')
        self.assertEqual(modele.grilles, 1)
        redaction = modele.charges[-1]
        self.assertEqual(redaction['grille']['faits'][0]['source_id'], 'S1')
        self.assertNotIn('texte', redaction['sources'][0])                      # la rédaction reçoit la grille au lieu du texte brut
        self.assertNotIn('Lettre inventée', json.dumps(redaction, ensure_ascii=False))
        r = m['resultat5689']
        self.assertEqual(r['etat'], 'partiel')
        self.assertIn('g_date_de_signature_du_bail', r['champs_manquants'])
        self.assertEqual(r['libelles_champs']['g_date_de_signature_du_bail'], 'Date de signature du bail')
        self.assertEqual(r['question_regroupee'].count('Date de signature du bail'), 1)
        self.assertTrue(any('retiré' in x for x in r['limites']))
        # Réponse regroupée laissée vide : la question devient « inconnue », n'est pas reposée et la grille n'est pas recalculée.
        ia.repondre(self.desk, {'id': m['id'], 'revision': r['revision'], 'champs': {}}, *AVOCAT)
        with patch('agent.chaine5689.modele_pour', return_value=modele):
            self.assertTrue(self.desk.work_once())
        r2 = missions567.get(self.desk, m['id'], missions567.owner_for(AVOCAT[0]))['resultat5689']
        self.assertEqual(modele.grilles, 1)
        self.assertNotIn('g_date_de_signature_du_bail', r2['champs_manquants'])
        self.assertTrue(any('inconnue, non reposée' in x for x in r2['limites']))
        self.assertTrue(r2['reserve'])

    def test_IA05_reponse_de_l_avocat_rejoint_la_grille(self):
        self.doc_indexe()
        modele = FauxPetit(defaut={'texte': 'Chronologie fictive.'})
        m = self.lancer('chronologie', objets=[{'id': 'doc:' + CHEMIN, 'version_id': ''}], modele=modele, cle='cle-grille-ia05-001')
        r = m['resultat5689']
        ia.repondre(self.desk, {'id': m['id'], 'revision': r['revision'], 'champs': {'g_date_de_signature_du_bail': '3 mars 2020', 'autre_champ': 'ignoré'}}, *AVOCAT)
        with patch('agent.chaine5689.modele_pour', return_value=modele):
            self.assertTrue(self.desk.work_once())
        redaction = modele.charges[-1]
        self.assertIn({'texte': 'Date de signature du bail : 3 mars 2020', 'nature': 'decision', 'source_id': 'avocat'}, redaction['grille']['faits'])
        self.assertEqual(redaction['grille']['manques'], [])
        self.assertNotIn('autre_champ', redaction['champs'])
        fini = missions567.get(self.desk, m['id'], missions567.owner_for(AVOCAT[0]))['resultat5689']
        self.assertEqual(fini['etat'], 'pret')
        self.assertEqual(modele.grilles, 1)

    def test_IA05_modele_ordinaire_inchange(self):
        self.doc_indexe()
        modele = Faux(defaut={'texte': 'Chronologie fictive.'})
        self.lancer('chronologie', objets=[{'id': 'doc:' + CHEMIN, 'version_id': ''}], modele=modele, cle='cle-grille-ia05-002')
        self.assertEqual(len(modele.charges), 1)
        self.assertNotIn('grille', modele.charges[0])
        self.assertIn('texte', modele.charges[0]['sources'][0])


# ------------------------------------------------------------------------------------------------ IA06 : trace juste avant l'appel
class Trace(Base):
    def client(self, num_ctx, vus):
        client = model.Model.__new__(model.Model)
        client.cfg = {'provider_type': 'ollama', 'provider_id': 'ollama', 'type': 'ollama', 'model': 'petit-modele-fictif', 'state_dir': self.desk.c['state_dir'],
                      'skill_instructions': '', 'num_ctx': num_ctx, 'timeout_seconds': 10}
        client.provider_type, client.provider_id, client._trace = 'ollama', 'ollama', ('', None)
        client.caps, client.context_limit = None, None
        desk = self.desk

        def ollama(method, path, req):
            vus['traces'] = desk.db.execute('SELECT * FROM skill_envois56108 WHERE job_id=?', (4242,)).fetchall()
            vus['messages'] = req['messages']
            return {'done': True, 'message': {'content': 'Réponse fictive'}}
        client.http = SimpleNamespace(json=ollama)
        return client

    def appeler(self, num_ctx, args=None, assemble=None):
        vus = {}
        client = self.client(num_ctx, vus)
        row = {'id': 4242, 'kind': 'prepare_legal_opinion'}
        args = args or {'instruction': CONSULTATION, 'matter': MID}
        with c32.for_job(self.desk, row, args):
            vus['avant'] = self.desk.db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='skill_envois56108'").fetchone()[0] and \
                self.desk.db.execute('SELECT COUNT(*) FROM skill_envois56108 WHERE job_id=4242').fetchone()[0]
            with patch('agent.profil5634.record', lambda *a, **k: None), (patch.object(c43, 'assemble', assemble) if assemble else patch.object(c43, 'UNIFIED_BUDGET', c43.UNIFIED_BUDGET)):
                client._leaf([{'role': 'system', 'content': 'Consignes fictives.'}, {'role': 'user', 'content': 'Question fictive.'}], 0, 300, None)
        return vus

    def test_IA06_trace_ecrite_avant_l_appel_avec_ce_qui_part(self):
        vus = self.appeler(4096)
        self.assertEqual(vus['avant'], 0)                                        # la sélection seule n'est pas une trace
        self.assertEqual(len(vus['traces']), 1)
        t = dict(vus['traces'][0])
        self.assertEqual((t['skill'], t['version'], t['forme']), (SID, '7.4.0+axiorhub.2', 'courte'))
        self.assertEqual(t['sha256'], self.row()['sha256'])
        self.assertIn('tache-2-cas-pratique.md (forme courte)', json.loads(t['fiches']))
        envoye = '\n'.join(m['content'] for m in vus['messages'] if m['role'] == 'system')
        self.assertIn('Assistant juridique — essentiel', envoye)                 # le petit modèle reçoit la version courte, pas le texte tronqué
        self.assertNotIn('## §0 —', envoye)
        self.assertEqual(t['caracteres'], len(c32._ctx.dernier_envoi[0]['texte']))

    def test_IA06_grand_contexte_version_complete(self):
        vus = self.appeler(32768)
        t = dict(vus['traces'][0])
        self.assertEqual(t['forme'], 'complete')
        envoye = '\n'.join(m['content'] for m in vus['messages'] if m['role'] == 'system')
        self.assertLess(envoye.index('# Adaptation AxiorHub'), envoye.index('## §2 —'))

    def test_IA06_trace_mutation(self):
        with muter(c43, '    selected = pour_envoi(selected, budget)', '    pass') as copie:
            vus = self.appeler(4096, assemble=copie.assemble)
        self.assertEqual(len(vus['traces']), 0)                                  # sans le branchement, aucune trace avant l'appel : le test échouerait

    def test_IA06_suivi_de_mission_en_francais_clair_sans_ecriture_en_lecture(self):
        self.assertEqual(c83.methode_transmise(self.desk, 4242), [])
        self.assertIsNone(self.desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='skill_envois56108'").fetchone() and None)
        self.appeler(4096)
        lignes = c83.methode_transmise(self.desk, 4242)
        self.assertEqual(len(lignes), 1)
        self.assertTrue(lignes[0].startswith('Méthode transmise : Assistant juridique v7.4.0+axiorhub.2 — version courte (adaptation courte, à relire par l’avocat)'))
        self.assertIn('fiche : tache-2-cas-pratique.md', lignes[0])
        carte = ia.pour_carte(self.desk, {'kind': 'question', 'state': 'answered', 'result': {}, 'job': {'id': 4242}})
        self.assertEqual(carte['methode_transmise'], lignes)

    def test_IA06_lecture_seule_sans_table(self):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        self.assertEqual(c83.methode_transmise(SimpleNamespace(db=db), 1), [])
        self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='skill_envois56108'").fetchone())   # R05 : aucune table créée en lecture

    def test_IA06_droit_etranger_dans_la_trace(self):
        self.registre(droit_applicable='Droit espagnol')
        vus = self.appeler(32768)
        t = dict(vus['traces'][0])
        self.assertEqual((t['forme'], t['droit']), ('methode_generale', 'Droit espagnol'))
        self.assertIn('autre droit (Droit espagnol)', c83.methode_transmise(self.desk, 4242)[0])


# ------------------------------------------------------------------------------------------------ contrat avec le banc d'essai (lot E)
class Apercu(Trace):
    def test_IA06_apercu_identique_a_ce_qui_part(self):
        for num_ctx in (4096, 8192, 32768):
            with self.subTest(num_ctx=num_ctx):
                messages = [{'role': 'system', 'content': 'Consignes fictives.'}, {'role': 'user', 'content': 'Question fictive.'}]
                vue = c32.apercu_instructions(self.desk, 'prepare_legal_opinion', {'instruction': CONSULTATION, 'matter': MID},
                                              {'num_ctx': num_ctx, 'messages': messages, 'max_tokens': 300})
                vus = self.appeler(num_ctx)
                envoye = [m['content'] for m in vus['messages'] if m['role'] == 'system' and 'COMPÉTENCE EXTERNE APPROUVÉE' in m['content']][0]
                self.assertTrue(envoye.endswith(vue['texte']))
                self.assertEqual(vue['caracteres'], len(vue['texte']))
                for cle in ('texte', 'sections', 'fiches', 'version_courte', 'omissions', 'caracteres', 'competences', 'budget'):
                    self.assertIn(cle, vue)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM skill_envois56108 WHERE job_id IS NULL').fetchone()[0], 0)   # l'aperçu n'écrit aucune trace


if __name__ == '__main__':
    unittest.main()
