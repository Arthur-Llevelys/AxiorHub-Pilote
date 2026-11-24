"""5.6.109, lot V (F08) : livrables structurés dans le registre canonique, Produire en trois vues, trames, reçu durable, sous-agents.

Données fictives (DURAND, MARTIN, ZORVAL, KERVILAC, DOS-001, example.test) ; stockage simulé (``Stockage`` de ``test_v5689_livrables``) ; aucun
réseau, aucun modèle. Ce qui exige un vrai navigateur (fenêtre flottante, focus rendu, défilement restauré, 1920 × 1080 et mobile) n'est pas
prouvé ici : ces tests vérifient le serveur, les routes et le rendu HTML.

Identifiants : F08 = ticket F08 ; R04 = droits avant tout contenu ; R05 = aucune écriture pendant une lecture.
"""
import csv
import io
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import test_v5689_livrables as t89
from test_v5689_livrables import ASSISTANT, AVOCAT, MID, texte_docx
from agent import accueil5689, artefacts56109 as A, livrables5689 as liv, livrables_web5689 as web, produire56100 as p100
from agent.common import Stop

ROOT = Path(__file__).resolve().parent.parent
ADMIN = ('cabinet', 'administrateur')
PLAN = [{'id': 'faits', 'role': 'faits', 'depends_on': [], 'max_tokens': 3000},
        {'id': 'droit', 'role': 'recherche', 'depends_on': [], 'max_tokens': 4000},
        {'id': 'projet', 'role': 'redaction', 'depends_on': ['faits', 'droit'], 'max_tokens': 7000},
        {'id': 'revue', 'role': 'controle', 'depends_on': ['projet'], 'max_tokens': 4000},
        {'id': 'docx', 'role': 'assemblage', 'depends_on': ['revue'], 'max_tokens': 1000}]


class Base(t89.Base):
    def setUp(self):
        super().setUp()
        A.ensure_schema(self.desk)
        self.n = 0

    def cle(self):
        self.n += 1
        return 'cle-essai-%04d' % self.n

    def appel(self, route, body=None, args=None, who=AVOCAT, methode=None):
        methode = methode or ('POST' if body is not None else 'GET')
        raw = json.dumps(body or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': who[0], 'HTTP_X_AXIORHUB_ROLE': who[1], 'HTTP_ORIGIN': 'https://cabinet.example.test', 'HTTP_X_CSRF_TOKEN': 'jeton',
               'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw), 'REQUEST_METHOD': methode}
        out = A.api(env, self.desk, {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}, '/p', A.PREFIXE + route, args or {}, methode)
        try:
            out['json'] = json.loads(out['body'])
        except (ValueError, TypeError):
            out['json'] = None
        return out

    def creer(self, trame='chronologie-sourcee', titre='Chronologie DURAND contre MARTIN', who=AVOCAT, matter=MID):
        out = self.appel('creer', {'trame': trame, 'dossier': matter, 'titre': titre, 'cle': self.cle()}, who=who)
        self.assertEqual(out['status'], '200 OK', out['json'])
        return out['json']

    def contenu(self, doc, **args):
        out = self.appel(doc + '/contenu', args=args)
        self.assertEqual(out['status'], '200 OK', out['json'])
        return out['json']

    def ligne(self, payload, *cells, statut='a_confirmer', sources=()):
        table = payload['blocks'][0]
        table['rows'].append({'id': 'L%d' % (len(table['rows']) + 1), 'cells': list(cells), 'statut': statut, 'source_ids': list(sources)})
        return payload

    def enregistrer(self, doc, payload, revision, cle=None, who=AVOCAT):
        return self.appel(doc + '/enregistrer', {'payload': payload, 'revision': revision, 'cle': cle or self.cle()}, who=who)


# ====================================================================================== un seul document canonique
class Canonique(Base):
    def test_F08_creation_depuis_trame_meme_document_partout(self):
        recu = self.creer()
        self.assertEqual({k: recu[k] for k in ('revision', 'durable', 'replayed')}, {'revision': 1, 'durable': True, 'replayed': False})
        doc = recu['document_id']
        rows = self.desk.db.execute("SELECT * FROM prepared_items5689 WHERE document_id=?", (doc,)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['source_service'], 'artefacts56109')
        self.assertEqual(rows[0]['matter_id'], MID)
        # Produire, Dossiers (projection du dossier) et Aujourd'hui (« Travail préparé ») lisent le même document du même registre
        hidden, droits = frozenset(), {'voir_honoraires': True}
        self.assertIn(doc, [c['document_id'] for c in liv.projection(self.desk, hidden, droits, sync=False)['items']])
        self.assertIn(doc, [c['document_id'] for c in liv.projection(self.desk, hidden, droits, matter=MID, sync=False)['items']])
        self.assertIn('/p/livrables/%s' % doc, accueil5689.travail_html(self.desk, '/p'))
        self.assertIn('/p/livrables/%s' % doc, web.section_html(self.desk, '/p', MID))
        self.assertIn('data-livrable="%s"' % doc, p100.section_html(self.desk, '/p', AVOCAT[0], AVOCAT[1], {'docs': 'tout'}))
        recents = A.travaux_prets_html(self.desk, '/p')
        self.assertIn('data-f96-cle="%s"' % doc, recents)               # même fenêtre flottante que les autres travaux prêts
        self.assertIn('/p/production?vue=livrables', recents)
        # aucun second registre : chaque contenu est lié à une version du registre commun
        orphelins = self.desk.db.execute('SELECT COUNT(*) FROM livrables_contenus56109 c LEFT JOIN prepared_items5689 i ON i.id=c.item_id '
                                         'WHERE i.id IS NULL OR i.document_id<>c.document_id OR i.version_id<>c.version_id').fetchone()[0]
        self.assertEqual(orphelins, 0)

    def test_F08_type_de_la_trame_et_contenu_enregistre_pas_fichier_verifie(self):
        doc = self.creer('arguments-preuves')['document_id']
        carte = liv.carte(liv.charger(self.desk, doc), {}, {}, (), self.desk, '/p')
        self.assertEqual(carte['nature'], 'Arguments et preuves')
        self.assertEqual(carte['production_label'], 'Contenu enregistré')

    def test_F08_corriger_cree_une_version_jamais_un_second_document(self):
        doc = self.creer()['document_id']
        p = self.contenu(doc)['payload']
        self.ligne(p, '12 mars 2025', 'Commande fictive de ZORVAL', 'Établi', 'Contrat', '')
        r2 = self.enregistrer(doc, p, 1)
        self.assertEqual(r2['json']['revision'], 2, r2['json'])
        self.assertEqual(r2['json']['document_id'], doc)
        lignes = self.desk.db.execute('SELECT version_number, lifecycle FROM prepared_items5689 WHERE document_id=? ORDER BY version_number', (doc,)).fetchall()
        self.assertEqual([(r[0], r[1]) for r in lignes], [(1, 'replaced'), (2, 'current')])
        self.assertEqual(self.desk.db.execute("SELECT COUNT(DISTINCT document_id) FROM prepared_items5689 WHERE source_service='artefacts56109'").fetchone()[0], 1)
        ancienne = self.contenu(doc, revision='1')
        self.assertEqual(ancienne['payload']['blocks'][0]['rows'], [])                 # version précédente conservée telle quelle
        self.assertEqual(self.contenu(doc)['payload']['blocks'][0]['rows'][0]['cells'][1], 'Commande fictive de ZORVAL')

    def test_F08_revision_perimee_409_et_rien_d_ecrase(self):
        doc = self.creer()['document_id']
        p = self.contenu(doc)['payload']
        self.assertEqual(self.enregistrer(doc, self.ligne(json.loads(json.dumps(p)), 'a', 'b', 'c', 'd', 'e'), 1)['status'], '200 OK')
        tardive = self.enregistrer(doc, self.ligne(json.loads(json.dumps(p)), 'IA tardive', '', '', '', ''), 1)
        self.assertEqual(tardive['status'], '409 Conflict')
        self.assertEqual(tardive['json']['error'], 'version_perimee')
        self.assertEqual(self.contenu(doc)['payload']['blocks'][0]['rows'][0]['cells'][0], 'a')

    def test_F08_recu_rejoue_et_cle_reutilisee_refusee(self):
        doc = self.creer()['document_id']
        p = self.ligne(self.contenu(doc)['payload'], 'x', 'y', 'z', 'w', 'v')
        a = self.enregistrer(doc, p, 1, cle='cle-fixe-0001')
        b = self.enregistrer(doc, p, 1, cle='cle-fixe-0001')
        self.assertEqual((a['json']['revision'], a['json']['replayed']), (2, False))
        self.assertEqual((b['json']['revision'], b['json']['replayed'], b['json']['durable']), (2, True, True))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM livrables_contenus56109 WHERE document_id=?', (doc,)).fetchone()[0], 2)
        autre = self.ligne(json.loads(json.dumps(p)), 'autre', '', '', '', '')
        self.assertEqual(self.enregistrer(doc, autre, 1, cle='cle-fixe-0001')['json']['error'], 'cle_reutilisee')
        # réponse perdue : la même clé se réconcilie en lecture, sans rejouer d'écriture
        op = self.appel(doc + '/operations/cle-fixe-0001')
        self.assertEqual(op['json'], {'document_id': doc, 'revision': 2, 'durable': True, 'replayed': True})
        self.assertEqual(self.appel(doc + '/operations/cle-inconnue-01')['status'], '404 Not Found')

    def test_F08_ouvrir_corriger_comparer_exporter_puis_rouvrir_le_meme(self):
        doc = self.creer()['document_id']
        p = self.ligne(self.contenu(doc)['payload'], '3 mai 2025', 'Réclamation fictive de MARTIN', 'Courriel reçu', 'Courriel', '')
        self.enregistrer(doc, p, 1)
        actuelle = liv.charger(self.desk, doc)
        premiere = liv.charger(self.desk, doc, version_id=self.desk.db.execute('SELECT version_id FROM livrables_contenus56109 WHERE document_id=? AND revision=1', (doc,)).fetchone()[0])
        diff = p100.comparer(self.desk, actuelle, premiere['version_id'])
        self.assertEqual(diff['indisponible'], '')
        self.assertTrue(any(x['op'] == 'ajout' and 'MARTIN' in x['texte'] for x in diff['lignes']))
        for fmt, marque in (('json', '"format": "axh-livrable/1"'), ('md', '| 3 mai 2025 |'), ('csv', '3 mai 2025')):
            out = self.appel(doc + '/exporter', args={'format': fmt})
            self.assertEqual(out['status'], '200 OK')
            self.assertIn(marque, out['body'].decode('utf-8'))
            self.assertIn('no-store', dict(out['headers'])['Cache-Control'])
        self.assertEqual(self.contenu(doc)['revision'], 2)                               # rouvrir : le même document, sa dernière version
        self.assertEqual(self.desk.db.execute("SELECT COUNT(DISTINCT document_id) FROM prepared_items5689 WHERE source_service='artefacts56109'").fetchone()[0], 1)

    def test_F08_csv_sans_formule_importee(self):
        doc = self.creer()['document_id']
        self.enregistrer(doc, self.ligne(self.contenu(doc)['payload'], '=HYPERLINK("x")', '+1', '-2', '@3', 'ok'), 1)
        lignes = list(csv.reader(io.StringIO(self.appel(doc + '/exporter', args={'format': 'csv'})['body'].decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(lignes[1][1:5], ["'=HYPERLINK(\"x\")", "'+1", "'-2", "'@3"])

    def test_F08_lecteur_commun_et_api_du_registre(self):
        doc = self.creer()['document_id']
        html = web._detail_page(self.desk, {'HTTP_X_AXIORHUB_USER': AVOCAT[0], 'HTTP_X_AXIORHUB_ROLE': AVOCAT[1]}, {'csrf': 'jeton'}, '/p', doc, {},
                                lambda titre, corps, *a, **k: corps)
        self.assertIn('id="v109-edition"', html)
        self.assertIn('sandbox=""', html)                                                  # aperçu isolé : cadre sans script
        self.assertIn('/api440/m5689/livrables/%s/cadre' % doc, html)
        out = t89.Base.api(self, 'livrables/' + doc)
        corps = json.loads(out['body'])
        self.assertEqual(corps['contenu']['payload']['kind'], 'chronologie')
        cadre = t89.Base.api(self, 'livrables/' + doc + '/cadre')
        self.assertIn('Chronologie DURAND contre MARTIN', cadre['body'] if isinstance(cadre['body'], str) else cadre['body'].decode('utf-8'))


# ====================================================================================== contrat des objets et des sources
class Contrat(Base):
    def source(self, path='/Dossiers/DEMO/PROJETS/Contrat DURAND.docx'):
        return self.demande(path=path, raw=texte_docx('Contrat fictif DURAND.'))['id']

    def test_F08_schema_ferme(self):
        ok = A.payload_initial('chronologie-sourcee')
        visible = lambda s: True
        A.verifier_contenu(ok, visible)
        cas = [({**ok, 'extra': 1}, 'schema_invalide'), ({**ok, 'kind': 'roman'}, 'type_inconnu'),
               ({**ok, 'blocks': [{'id': 'b', 'type': 'image', 'source_ids': []}]}, 'rendu_non_autorise'),
               ({**ok, 'blocks': [{'id': 'b', 'type': 'paragraph', 'source_ids': [], 'text': 'a', 'onclick': 'x'}]}, 'champ_inconnu'),
               ({**ok, 'blocks': [{'id': 'b', 'type': 'paragraph', 'source_ids': [], 'text': 'a\x00'}]}, 'texte_invalide'),
               ({**ok, 'blocks': [{'id': 'b', 'type': 'table', 'source_ids': [], 'columns': ['A'], 'rows': [{'id': 'L1', 'cells': ['x'], 'statut': 'certain', 'source_ids': []}]}]}, 'statut_inconnu'),
               ({**ok, 'blocks': [{'id': 'b', 'type': 'table', 'source_ids': [], 'columns': ['A'], 'rows': [{'id': 'L1', 'cells': ['x', 'y'], 'statut': 'inconnu', 'source_ids': []}]}]}, 'ligne_invalide'),
               ({**ok, 'blocks': [{'id': 'b', 'type': 'table', 'source_ids': [], 'columns': ['A'], 'rows': [{'id': 'L1', 'cells': ['x'], 'statut': 'inconnu', 'source_ids': []}] * 2}]}, 'ligne_dupliquee')]
        for payload, code in cas:
            with self.assertRaisesRegex(Stop, code):
                A.verifier_contenu(payload, visible)

    def test_F08_ligne_sourcee_par_une_version_du_registre_du_dossier(self):
        sid = self.source()
        doc = self.creer()['document_id']
        p = self.ligne(self.contenu(doc)['payload'], '12 mars', 'Commande', 'Établi', 'Contrat', '', statut='trouve', sources=[sid])
        self.assertEqual(self.enregistrer(doc, p, 1)['status'], '200 OK')
        liens = {r[0] for r in self.desk.db.execute("SELECT l.source_object_id FROM prepared_source_links5689 l JOIN prepared_items5689 i ON i.id=l.item_id "
                                                     "WHERE l.link_kind='source' AND i.document_id=?", (doc,))}
        self.assertIn(sid, liens)
        # une source inconnue, d'un autre dossier, ou fournie comme « droit déclaré » est refusée
        for faux in ('0' * 32, 'doc-inexistant'):
            p2 = self.ligne(self.contenu(doc)['payload'], 'x', 'y', 'z', 'w', 'v', sources=[faux])
            out = self.enregistrer(doc, p2, 2)
            self.assertEqual(out['json']['error'], 'source_indisponible')

    def test_F08_R04_ancienne_version_avec_source_devenue_interdite_non_restituee(self):
        sid = self.source()
        doc = self.creer()['document_id']
        self.enregistrer(doc, self.ligne(self.contenu(doc)['payload'], 'a', 'b', 'c', 'd', 'e', sources=[sid]), 1)
        self.enregistrer(doc, self.contenu(doc)['payload'], 2)                            # version 3, toujours la même source
        self.desk.db.execute("UPDATE prepared_items5689 SET required_right='voir_honoraires' WHERE id=?", (sid,))
        self.desk.db.commit()
        with patch('agent.membres5650.effective_rights', return_value={'voir_honoraires': False, 'exporter': True}):
            out = self.appel(doc + '/contenu', args={'revision': '2'})
            self.assertEqual(out['json']['error'], 'source_indisponible')
            self.assertNotIn('"a"', out['body'])
            self.assertEqual(self.appel(doc + '/exporter', args={'format': 'json', 'revision': '2'})['json']['error'], 'source_indisponible')
            self.assertEqual(self.appel(doc + '/contenu', args={'revision': '1'})['status'], '200 OK')   # la version sans cette source reste lisible

    def test_F08_R04_dossier_reserve_meme_404_neutre_qu_un_absent(self):
        doc = self.creer()['document_id']
        with patch('agent.membres5650.hidden_for', return_value=frozenset({MID})):
            reserve = self.appel(doc + '/contenu')
            absent = self.appel('liv-inexistant00000000000000/contenu')
            self.assertEqual((reserve['status'], reserve['json']), (absent['status'], absent['json']))
            self.assertEqual(reserve['status'], '404 Not Found')
            self.assertEqual(self.enregistrer(doc, A.payload_initial('chronologie-sourcee'), 1)['status'], '404 Not Found')
            self.assertEqual(self.appel('creer', {'trame': 'chronologie-sourcee', 'dossier': MID, 'cle': self.cle()})['status'], '404 Not Found')
        from agent import membres5650
        membres5650.set_viewer(frozenset({MID}))       # cadre d'Aujourd'hui : dossiers masqués posés par le contrôle central de la requête
        try:
            self.assertNotIn(doc, A.travaux_prets_html(self.desk, '/p'))
        finally:
            membres5650.set_viewer(frozenset())
        self.assertIn(doc, A.travaux_prets_html(self.desk, '/p'))

    def test_F08_document_word_jamais_modifie_par_cette_route(self):
        docx = self.demande()
        out = self.enregistrer(docx['document_id'], A.payload_initial('chronologie-sourcee'), 1)
        self.assertEqual(out['json']['error'], 'format_non_modifiable')

    def test_F08_rendu_declaratif_echappe(self):
        p = A.payload_initial('chronologie-sourcee', '<script>alert(1)</script>')
        p['blocks'][0]['rows'].append({'id': 'L1', 'cells': ['<img src=x onerror=1>', 'b', 'c', 'd', 'e'], 'statut': 'inconnu', 'source_ids': []})
        html = A.rendu_html(p)
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img', html)
        self.assertIn('&lt;img src=x onerror=1&gt;', html)

    def test_F08_role_lecture_seule_refuse_403(self):
        out = self.appel('creer', {'trame': 'chronologie-sourcee', 'dossier': MID, 'cle': self.cle()}, who=('stagiaire@example.test', 'lecture'))
        self.assertEqual(out['status'], '403 Forbidden')


# ====================================================================================== garde-fou : code arbitraire désactivé
class CodeArbitraire(Base):
    def test_F08_code_genere_refuse_dans_le_contenu(self):
        for type_ in ('html', 'react', 'script', 'svg', 'HTML'):
            payload = {'kind': 'chronologie', 'title': 'T', 'blocks': [{'id': 'b', 'type': type_, 'source_ids': [], 'text': '<b>x</b>'}]}
            with self.assertRaisesRegex(Stop, 'code_arbitraire_desactive'):
                A.verifier_contenu(payload, lambda s: True)
        doc = self.creer()['document_id']
        out = self.enregistrer(doc, {'kind': 'chronologie', 'title': 'T', 'blocks': [{'id': 'b', 'type': 'html', 'source_ids': [], 'text': '<script></script>'}]}, 1)
        self.assertEqual((out['status'], out['json']['error']), ('403 Forbidden', 'code_arbitraire_desactive'))

    def test_F08_fonction_de_code_arbitraire_desactivee_meme_basculee_par_erreur(self):
        self.assertFalse(A.CODE_ARBITRAIRE)
        self.assertFalse(A.capacites()['code_genere'])
        with self.assertRaisesRegex(Stop, 'code_arbitraire_desactive'):
            A.executer_code('<script>fetch("https://evil.example.test")</script>')
        with patch.object(A, 'CODE_ARBITRAIRE', True):                                    # mutation en mémoire : rien n'est câblé pour exécuter
            with self.assertRaisesRegex(Stop, 'code_arbitraire_non_qualifie'):
                A.executer_code('<script></script>')
        src = (ROOT / 'agent' / 'artefacts56109.py').read_text(encoding='utf-8')
        for interdit in ('eval(', 'exec(', 'subprocess', 'allow-same-origin', 'unsafe-eval'):
            self.assertNotIn(interdit, src)
        js = (ROOT / 'agent' / 'static' / 'v56109-produire.js').read_text(encoding='utf-8')
        self.assertNotIn('innerHTML', js)
        self.assertNotIn('eval(', js)


# ====================================================================================== trames, sous-agents, vues, Aujourd'hui
class Trames(Base):
    def test_F08_douze_trames_a_valider_sans_effet_externe(self):
        t = A.trames_publiques()
        self.assertEqual(len(t), 12)
        self.assertEqual(len({x['id'] for x in t}), 12)
        self.assertTrue(all(x['statut'] == 'a_valider' and not x['effets_externes'] and x['competence'] == 'assistant_juridique' for x in t))
        for x in t:
            A.verifier_contenu(A.payload_initial(x['id']), lambda s: True)
        self.assertEqual(self.appel('trames')['json']['capacites']['code_genere'], False)

    def test_F08_creation_manuelle_possible_ia_en_pause(self):
        self.desk.setting('missions567:pause', True)
        with patch('agent.arret5643.manual_paused', return_value=True):
            recu = self.creer('budget-honoraires', 'Budget fictif KERVILAC')
        self.assertTrue(recu['durable'])
        self.assertIn('IA en pause', A.vues_html(self.desk, '/p', AVOCAT[0], AVOCAT[1], {'vue': 'creer'}))


class SousAgents(Base):
    def test_F08_plan_ferme_valide(self):
        self.assertEqual(A.verifier_plan(PLAN, 20000)['steps'], 5)
        reprise = {'id': 'projet2', 'role': 'redaction', 'depends_on': ['revue'], 'max_tokens': 500}
        mauvais = [(PLAN[:4] + [reprise, {**PLAN[4], 'depends_on': ['projet2']}], 'controle_apres_redaction_requis'),
                   (PLAN[:3] + [{**PLAN[4], 'depends_on': ['projet']}], 'controle_requis'),
                   ([{**PLAN[0], 'depends_on': ['droit']}, {**PLAN[1], 'depends_on': ['faits']}], 'cycle_ou_dependance_absente'),
                   ([{**PLAN[0], 'role': 'pirate'}], 'role_ou_id_invalide'), ([dict(PLAN[0], id='e%d' % i) for i in range(9)], 'plan_trop_long'),
                   (PLAN, 'budget_depasse'), ([{**PLAN[0], 'enfants': []}], 'etape_invalide'),
                   ([{'id': 'a', 'role': 'assemblage', 'depends_on': [], 'max_tokens': 10}], 'controle_requis')]
        for plan, code in mauvais:
            with self.assertRaisesRegex(Stop, code):
                A.verifier_plan(plan, 20000 if code != 'budget_depasse' else 1000)

    def test_F08_plan_raccorde_au_moteur_de_missions_sans_execution(self):
        from agent import taches5614
        doc = self.creer()['document_id']
        out = self.appel(doc + '/plan', {'plan': PLAN, 'budget_tokens': 20000, 'cle': 'plan-cle-0001'})
        self.assertEqual(out['status'], '200 OK', out['json'])
        mission = self.desk.db.execute('SELECT * FROM missions5614 WHERE id=?', (out['json']['mission_id'],)).fetchone()
        self.assertEqual(mission['state'], 'suggested')                                    # rien ne s'exécute sans lancement explicite
        self.assertEqual(mission['matter'], MID)
        taches = self.desk.db.execute('SELECT code, role, depends_on, run_state FROM tasks5614 WHERE mission=? ORDER BY position', (mission['id'],)).fetchall()
        self.assertEqual([t['role'] for t in taches], ['analyste_faits', 'chercheur', 'redacteur', 'controleur', 'superviseur'])
        self.assertEqual(json.loads(taches[2]['depends_on']), ['T01', 'T02'])
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind LIKE '%5614%'").fetchone()[0], 0)
        rejeu = self.appel(doc + '/plan', {'plan': PLAN, 'budget_tokens': 20000, 'cle': 'plan-cle-0001'})
        self.assertEqual(rejeu['json']['mission_id'], mission['id'])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM missions5614').fetchone()[0], 1)
        self.assertEqual(self.appel(doc + '/plan', {'plan': PLAN[:1], 'budget_tokens': 20000, 'cle': 'plan-cle-0001'})['json']['error'], 'cle_reutilisee')
        self.assertTrue(taches5614)

    def test_F08_plan_refuse_a_l_assistant(self):
        doc = self.creer()['document_id']
        self.assertEqual(self.appel(doc + '/plan', {'plan': PLAN, 'budget_tokens': 20000, 'cle': 'plan-cle-0002'}, who=ASSISTANT)['status'], '403 Forbidden')


class Vues(Base):
    def test_F08_trois_vues_sans_page_atelier(self):
        for vue, marque in (('creer', 'data-v109="creer"'), ('modeles', 'Trames de livrables'), ('livrables', 'v109-vues')):
            html = A.vues_html(self.desk, '/p', AVOCAT[0], AVOCAT[1], {'vue': vue})
            self.assertIn(marque, html)
            self.assertIn('aria-current="page"', html)
            self.assertNotIn('Atelier', html)
        creer = A.vues_html(self.desk, '/p', AVOCAT[0], AVOCAT[1], {'vue': 'creer'})
        self.assertEqual(len([t for t in A.trames() if '<option value="%s">' % t['id'] in creer]), 12)
        for lien in ('/p/studio', '/p/documents', '/p/playbooks', '/p/outils-ia'):
            self.assertIn(lien, creer)
        from agent import fonctions5631, navigation5643
        self.assertFalse([f for f in fonctions5631.FUNCTIONS if f[2].startswith('/atelier') and not f[2].startswith('/atelier/reglages')])
        self.assertNotIn('atelier', [x[0] for x in navigation5643.PRODUCTION_TABS])

    def test_F08_statiques_et_feuille(self):
        from agent import shell501, web440
        self.assertIn('v56109-produire.css', shell501.BUNDLE)
        for f in ('/static/v56109-produire.js', '/static/v56109-produire.css', '/static/v56109-craft-host.js'):
            self.assertIn(f, web440.STATIC)
        css = (ROOT / 'agent' / 'static' / 'v56109-produire.css').read_text(encoding='utf-8')
        self.assertIn(':focus-visible', css)
        self.assertIn('v109-vues', (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8'))


class Lectures(Base):
    def test_R05_lectures_sans_ecriture(self):
        doc = self.creer()['document_id']
        avant = self.desk.db.total_changes
        self.contenu(doc)
        self.appel('trames')
        A.vues_html(self.desk, '/p', AVOCAT[0], AVOCAT[1], {'vue': 'creer'})
        A.travaux_prets_html(self.desk, '/p')
        self.assertEqual(self.desk.db.total_changes, avant)
        # l'export de données n'écrit que sa trace au journal d'audit (comme le téléchargement du registre), rien dans le livrable
        etat = [self.desk.db.execute('SELECT COUNT(*), COALESCE(MAX(rowid),0) FROM %s' % t).fetchone() for t in ('livrables_contenus56109', 'prepared_items5689', 'livrables_operations56109')]
        self.appel(doc + '/exporter', args={'format': 'md'})
        self.assertEqual(etat, [self.desk.db.execute('SELECT COUNT(*), COALESCE(MAX(rowid),0) FROM %s' % t).fetchone() for t in ('livrables_contenus56109', 'prepared_items5689', 'livrables_operations56109')])


class Pages(unittest.TestCase):
    """Application réelle (WSGI) : /production en trois vues derrière l'interrupteur ; ancienne présentation inchangée."""
    import test_v5648 as _base
    PATH = _base.Base.PATH
    setUp = _base.Base.setUp
    call = _base.Base.call

    def page(self, path, query=''):
        r = self.call(path, query=query)
        self.assertTrue(r['status'].startswith('200'), (path, r['status']))
        return r['body'].decode('utf-8')

    def test_F08_produire_trois_vues_derriere_l_interrupteur(self):
        from agent import interface56100
        html = self.page('/production')
        self.assertIn('class="v109-vues"', html)
        self.assertIn('id="p100-documents"', html)                                         # Livrables : la page existante
        creer = self.page('/production', 'vue=creer')
        self.assertIn('data-v109="creer"', creer)
        self.assertNotIn('id="p100-documents"', creer)
        modeles = self.page('/production', 'vue=modeles')
        self.assertIn('Modèles Word du cabinet', modeles)
        interface56100.regler_cabinet(self.desk, False)
        ancien = self.page('/production', 'vue=creer')
        self.assertNotIn('v109-vues', ancien)
        self.assertIn('id="l89-section"', ancien)


if __name__ == '__main__':
    unittest.main()
