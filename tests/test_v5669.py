"""5.6.69 : analyses payantes répétées supprimées, projets « à compléter » réellement rédigés, analyses de documents plus robustes,
cartes « À décider » regroupées (retours et diagnostic du 21 octobre 2026). Données fictives (example.test)."""
import json
import unittest
from unittest.mock import patch

import test_agent as fixtures
import test_v260 as v260
import test_v5652 as base
from agent import file5657, long_documents365
from agent.common import Stop

MID = base.MID


class Interruption(Exception):
    pass


# ---------------------------------------------------------------------------------------------------- orchestrateur
class AnalyseUnique(v260.Version260Tests):
    def test_memoire_rafraichie_sans_nouvelle_analyse(self):
        from agent import orchestrator

        class Counting(v260.OrchestrationModel):
            calls = 0

            def ask(self, stage, data):
                Counting.calls += 1
                return super().ask(stage, data)

        versions = iter([{'version': 1, 'data': {}}, {'version': 2, 'data': {}}, {'version': 3, 'data': {}}])
        with patch('agent.autonomy.operational_memory', side_effect=lambda *a, **k: next(versions)):
            orchestrator.orchestrate_mail(self.d, {'mail_key': self.key}, Counting(), Counting())
            first = Counting.calls
            again = orchestrator.orchestrate_mail(self.d, {'mail_key': self.key}, Counting(), Counting())
        self.assertEqual(first, 2)                 # classement + analyse différentielle
        self.assertEqual(Counting.calls, first)    # mémoire du dossier rafraîchie : aucune nouvelle analyse payante
        self.assertTrue(again['idempotent'])

    # les tests hérités de 2.6.0 ne sont pas rejoués ici
    test_orchestration_is_idempotent_and_single_notification = None


for _name in [n for n in dir(v260.Version260Tests) if n.startswith('test_')]:
    if _name != 'test_memoire_rafraichie_sans_nouvelle_analyse':
        setattr(AnalyseUnique, _name, None)


# ---------------------------------------------------------------------------------------------------- projets à compléter
class ProjetsJamaisVides(base.Base):
    def test_reponse_automatique_en_mode_a_completer(self):
        from agent import intelligence
        seen = []
        with patch.object(intelligence, 'prepare_draft', side_effect=lambda desk, args: seen.append(args) or {'projet_prepare': False}), \
                patch('agent.live430.progress'):
            intelligence.prepare_reply(self.desk, {'key': 'k' * 64, 'automatic': 'yes'})
            intelligence.prepare_reply(self.desk, {'key': 'k' * 64})
            self.desk.setting('automation:mail_projects_to_complete_enabled', False)
            intelligence.prepare_reply(self.desk, {'key': 'k' * 64, 'automatic': 'yes'})
        self.assertEqual([a['mode_projet'] for a in seen], ['a_completer', '', ''])

    def test_consigne_transmise_au_modele(self):
        from agent import intelligence
        from agent.model import MANUAL_DRAFT_PROMPT
        self.assertIn('« mode_projet » vaut « a_completer » : body n\'est alors jamais vide', MANUAL_DRAFT_PROMPT)
        mail = fixtures.mail(sender='client@example.test', subject='Point sur le dossier')
        captured = []

        class Writer:
            def ask(self, stage, payload):
                captured.append(payload)
                raise Interruption()

        class Box:
            def close(self):
                pass
        for mode in ('a_completer', ''):
            with patch.object(intelligence, 'Mailbox', return_value=Box()), \
                    patch.object(intelligence, 'fetch_source', return_value=({'matter': ''}, mail)), \
                    patch.object(intelligence, 'Model', return_value=Writer()), patch.object(intelligence, 'routed_config', return_value={}):
                with self.assertRaises(Interruption):
                    intelligence.prepare_draft(self.desk, {'key': 'k' * 64, 'sans_dossier': 'oui', 'mode_projet': mode})
        self.assertEqual(captured[0].get('mode_projet'), 'a_completer')
        self.assertNotIn('mode_projet', captured[1])


# ---------------------------------------------------------------------------------------------------- documents longs, secours, taille
class Robustesse(unittest.TestCase):
    def test_passage_sans_reponse_ne_fait_plus_echouer(self):
        class Empty:
            def ask(self, stage, data):
                return {'answer': '', 'source_ids': [], 'limits': [], 'proposed_actions': []}
        out = long_documents365._ask(Empty(), 'Question', {'id': 'p1', 'citation': 'p. 3', 'excerpt': 'texte', 'kind': 'piece'}, 60000, {})
        self.assertIn('p. 3 : le modèle n’a rien relevé', out)

    def test_taille_externe_laisse_la_place_des_consignes(self):
        from agent.model import routed_config
        config = {'ollama': {'url': 'http://127.0.0.1:11434', 'model': 'local', 'max_context_chars': 65000}, 'state_dir': '',
                  'hybrid_routing': {'mode': 'manual'},
                  'ai_providers': {'openrouter': {'type': 'openrouter', 'enabled': True, 'external_data_allowed': True, 'model': 'o',
                                                  'max_context_chars': 120000}},
                  'model_routing': {'document_drafting': {'provider': 'openrouter', 'model': 'o'}}}
        with patch('agent.extensions364.active_skill_instructions', return_value=''):
            external = routed_config(config, 'document_drafting')
            local = routed_config({**config, 'model_routing': {}}, 'document_drafting')
        self.assertEqual(external['max_context_chars'], 105000)   # 120 000 autorisés moins la place des consignes
        self.assertEqual(local['max_context_chars'], 65000)       # modèle local : inchangé


# ---------------------------------------------------------------------------------------------------- À décider
class Regroupement(base.Base):
    SAFETY = file5657.SAFETY

    def test_titre_coupe_regroupe_quand_meme(self):
        from agent import missions567
        missions567.ensure_schema(self.desk)
        cards = []
        for i, name in enumerate(('DURAND - 2026', 'MARTIN - 2026', 'BERNARD')):
            instruction = ('Prépare une note de mise à jour de la chronologie et des arguments–preuves, en distinguant les nouveautés des '
                           'faits établis.\n' + self.SAFETY + ' Document nouveau ou modifié : ' + name + '\n')
            self.desk.db.execute('INSERT INTO missions_v567(id,owner,request_key,fingerprint,instruction,matter,kind,state,context,plan,exceptions,'
                                 'created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                 ('m%d' % i, 'cabinet', 'k-%d' % i, 'f', instruction, MID, 'question', 'decision', '{}', '{}', '[]', 'x', 'x'))
            cards.append({'kind': 'mission', 'state': 'decision', 'id': 'm%d' % i, 'key': 'mission:m%d' % i, 'matter': MID,
                          'matter_label': 'Scanner', 'title': ('Mission : ' + instruction)[:170], 'question': 'La demande nomme un autre dossier.'})
        cards += [{'kind': 'plan', 'state': 'blocked', 'id': 'p%d' % i, 'key': 'plan:p%d' % i, 'matter': MID, 'matter_label': 'Scanner',
                   'title': 'Document nouveau ou modifié : PIECE %d.pdf' % i, 'question': 'Une étape attend une décision ou une reprise.'} for i in range(2)]
        self.desk.db.commit()
        self.assertNotIn(self.SAFETY, cards[0]['title'])   # le titre de la carte est bien coupé avant la consigne
        with patch('agent.aujourdhui5614.items', return_value=cards):
            out = file5657.decision_items(self.desk, '/p')
        self.assertEqual(len(out), 2)
        missions = next(x for x in out if x.get('missions'))
        self.assertEqual(missions['missions'], ['m0', 'm1', 'm2'])
        plans = next(x for x in out if x['object'].endswith(':plans'))
        self.assertEqual(plans['choices'], ('ouvrir', 'exclure'))
        self.assertTrue(plans['title'].startswith('2 documents'))

    def test_exclusion_suspend_les_preparations(self):
        from agent import bruit5668
        from pathlib import Path
        sql = (Path(bruit5668.__file__).parent / 'migrations' / '0568_proactivity.sql').read_text(encoding='utf-8')
        self.desk.db.executescript(sql)
        self.desk.db.execute("INSERT INTO plans_v568 VALUES('p1','cabinet','r1','f',?,'Document nouveau ou modifié : PIECE.pdf','blocked','x','x')", (MID,))
        self.desk.db.execute("INSERT INTO plans_v568 VALUES('p2','cabinet','r2','f',?,'Mission manuelle','blocked','x','x')", (MID,))
        self.desk.db.execute("INSERT INTO events_v568(id,owner,source,object_key,version,kind,matter,payload,state,plan_id,detected,occurred,updated) "
                             "VALUES('e1','cabinet','document','o','v','E08',?,'{}','planned','p1','x','x','x')", (MID,))
        self.desk.db.commit()
        with patch('agent.plans568.control') as control:
            bruit5668.exclude_matter(self.desk, MID)
        control.assert_called_once()
        self.assertEqual(control.call_args[0][1], {'id': 'p1', 'action': 'pause'})


if __name__ == '__main__':
    unittest.main()
