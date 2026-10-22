"""5.6.71 : surveillance allégée, « Ce que fait l'agent » lisible, réponses « sans dossier », réponses en attente de décision.

Valeurs entièrement fictives (dossier DOS-001, adresses example.test)."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import test_agent as fixtures
import test_v5652 as base
from agent import activite5671 as act, reponses5671 as rep, surveillance5671 as surv
from agent.common import Stop

MID = base.MID
OWNER = 'avocat@example.test'
KEY = 'd' * 64
KEY2 = 'e' * 64


def _done(desk, kind, args=None, result=None, hours_ago=0.1):
    when = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    jid = desk.enqueue(kind, args or {})
    desk.db.execute("UPDATE jobs SET status='done',finished=?,result=? WHERE id=?", (when, json.dumps(result or {}), jid))
    desk.db.commit()
    return jid


# ---------------------------------------------------------------------------------------------------- 1. surveillance allégée
class Surveillance(base.Base):
    def test_veille_des_documents_recente_espace_le_tour_et_evite_la_relecture(self):
        self.assertFalse(surv.documents_watch_recent(self.desk))
        self.assertEqual(surv.round_minutes(self.desk, 30), 30)
        _done(self.desk, 'live_documents430', hours_ago=5)
        self.assertFalse(surv.documents_watch_recent(self.desk))
        _done(self.desk, 'live_documents430', hours_ago=0.2)
        self.assertTrue(surv.documents_watch_recent(self.desk))
        self.assertEqual(surv.round_minutes(self.desk, 30), 360)
        self.assertTrue(surv.skip_live_inventory(self.desk, {'matter': MID, 'tour': 'oui'}))
        self.assertFalse(surv.skip_live_inventory(self.desk, {'matter': MID}))   # demande explicite ou fichier changé : relecture

    def test_tour_periodique_sans_relecture_nextcloud_ni_faux_changement(self):
        from agent import proactive
        first = proactive.monitor_matter(self.desk, {'matter': MID})            # première observation : relecture normale
        self.assertTrue(first['first_observation'])
        _done(self.desk, 'live_documents430', hours_ago=0.1)
        with patch('agent.proactive.DAV', side_effect=AssertionError('aucune relecture Nextcloud pendant le tour')):
            out = proactive.monitor_matter(self.desk, {'matter': MID, 'tour': 'oui'})
        self.assertFalse(out['changed'])
        self.assertFalse(out['first_observation'])

    def test_tour_marque_ses_surveillances(self):
        from agent import proactive
        with patch('agent.portfolio.portfolio_rows', return_value=[{'matter': MID}]):
            proactive.monitor_all(self.desk, {})
        args = [json.loads(r[0]) for r in self.desk.db.execute("SELECT args FROM jobs WHERE kind='monitor_matter'")]
        self.assertEqual(args, [{'matter': MID, 'tour': 'oui'}])

    def test_agenda_modifie_ne_relance_que_le_dossier_designe(self):
        from agent import watch430

        class Dav:
            def __init__(self, events):
                self.ev = events

            def events(self, *a):
                return self.ev
        self.desk.c['calendar'] = {**self.desk.c.get('calendar', {}), 'urls': ['https://cloud.example.test/cal/'], 'timezone': 'Europe/Paris'}
        start = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        events = [{'uid': 'u1', 'summary': 'Rendez-vous client ' + MID, 'start': start, 'source_url': 'https://cloud.example.test/cal/'},
                  {'uid': 'u2', 'summary': 'Déjeuner', 'start': start, 'source_url': 'https://cloud.example.test/cal/'}]
        watch430.calendar_check(self.desk, dav=Dav(events))
        kinds = [(r[0], json.loads(r[1])) for r in self.desk.db.execute("SELECT kind,args FROM jobs WHERE kind IN ('monitor_all','monitor_matter')")]
        self.assertEqual(kinds, [('monitor_matter', {'matter': MID})])
        watch430.calendar_check(self.desk, dav=Dav(events))                      # rien de modifié : rien de plus
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind IN ('monitor_all','monitor_matter')").fetchone()[0], 1)


# ---------------------------------------------------------------------------------------------------- 2. « Ce que fait l'agent »
class Activite(base.Base):
    def test_travaux_techniques_masques_et_utilite_affichee(self):
        self.assertTrue(act.hidden('run_continuous_business_tests'))
        self.assertTrue(act.hidden('monitor_matter', json.dumps({'matter': MID, 'tour': 'oui'})))
        self.assertFalse(act.hidden('monitor_matter', json.dumps({'matter': MID})))
        self.desk.enqueue('run_continuous_business_tests', {}, priority=90)
        self.desk.enqueue('reconcile_inbox', priority=60)
        self.desk.enqueue('monitor_matter', {'matter': MID})
        from agent.cockpit530 import feed
        rows = feed(self.desk)['en_cours']
        self.assertEqual([r['text'] for r in rows], ['Surveillance immédiate du dossier'])
        self.assertIn('pièces nouvelles ou modifiées', rows[0]['detail'])

    def test_resultat_de_chaque_passage(self):
        items = [{'idempotent': True}, {'status': 'pending_review', 'reply_proposal': {'automatic_draft_job_id': 9}},
                 {'status': 'blocked', 'reply_proposal': {}}, {'status': 'pending_review', 'reply_proposal': {}}]
        shown, detail = act.done_detail('orchestrator_mail_sweep', json.dumps({'items': items}))
        self.assertTrue(shown)
        self.assertEqual(detail, '3 courriels analysés · 1 réponse mise en préparation · 1 en attente de votre décision (dossier à confirmer ou point à trancher)')
        self.assertEqual(act.done_detail('orchestrator_mail_sweep', {'items': [{'idempotent': True}]}), (False, ''))
        self.assertEqual(act.done_detail('monitor_matter', {'changed': False}), (False, ''))
        self.assertEqual(act.done_detail('monitor_matter', {'changed': True, 'signals_refreshed': 2}),
                         (True, 'Pièces nouvelles ou modifiées repérées · 2 points à vérifier'))
        self.assertEqual(act.done_detail('index', {'contenu_modifie': False}), (False, ''))
        self.assertEqual(act.done_detail('live_mail430', {'examined': 0}), (False, ''))
        _done(self.desk, 'orchestrator_mail_sweep', result={'items': items})
        _done(self.desk, 'monitor_matter', {'matter': MID}, {'changed': False})
        from agent.cockpit530 import feed
        fait = feed(self.desk)['fait']
        self.assertEqual([r['text'] for r in fait], [act.ORCHESTRATOR_LABEL])
        self.assertIn('1 réponse mise en préparation', fait[0]['detail'])

    def test_controles_d_integrite_une_fois_par_nuit(self):
        now = time.time()
        self.assertTrue(act.integrity_due(now, now - 25 * 3600, hour=3))
        self.assertFalse(act.integrity_due(now, now - 25 * 3600, hour=14))
        self.assertFalse(act.integrity_due(now, now - 3600, hour=3))
        self.assertTrue(act.integrity_due(now, now - 37 * 3600, hour=14))    # serveur éteint la nuit
        self.assertTrue(act.integrity_due(now, 0, hour=14))


# ---------------------------------------------------------------------------------------------------- 3. réponses « sans dossier »
class SansDossier(base.Base):
    def _review(self, key, reason, sender='prospect@example.test', updated=None):
        from agent.state import State
        st = State(self.desk.c['state_dir'])
        st.set(key, 'm-' + key[:6], 't-' + key[:6], 'review', reason)
        st.report(key, {'key': key, 'subject': 'Relecture de conditions générales', 'sender': sender, 'source_uid': '5',
                        'source_mailbox': 'INBOX', 'matter': None, 'reply_recipients': [sender]})
        if updated:
            st.db.execute('UPDATE messages SET updated=? WHERE key=?', (updated, key))
            st.db.commit()

    def test_reponse_preparee_sans_dossier_une_seule_fois(self):
        self._review(KEY, 'expediteur_inconnu_sans_dossier')
        self._review(KEY2, 'correspondant_ou_dossier_a_confirmer', updated=(datetime.now(timezone.utc) - timedelta(days=5)).isoformat())  # trop ancien
        out = rep.sweep(self.desk)
        self.assertEqual(out['mis_en_preparation'], 1)
        args = json.loads(self.desk.db.execute("SELECT args FROM jobs WHERE kind='prepare_reply'").fetchone()[0])
        self.assertEqual((args['key'], args['matter'], args['sans_dossier'], args['ignorer_dossier_suppose'], args['automatic']),
                         (KEY, '', 'oui', 'oui', 'yes'))
        self.assertEqual(rep.sweep(self.desk)['mis_en_preparation'], 0)   # une seule tentative automatique

    def test_reglages_respectes(self):
        self._review(KEY, 'correspondant_ou_dossier_a_confirmer')
        self.desk.setting(rep.SETTING, False)
        self.assertTrue(rep.sweep(self.desk).get('desactive'))
        self.desk.setting(rep.SETTING, True)
        self.desk.setting('automation:automatic_mail_drafts_enabled', False)
        self.assertTrue(rep.sweep(self.desk).get('desactive'))
        self.desk.setting('automation:automatic_mail_drafts_enabled', True)
        self._review(KEY2, 'correspondant_ou_dossier_a_confirmer', sender='cabinet@example.test')   # message du cabinet : jamais
        self.assertEqual(rep.candidates(self.desk), [KEY])

    def test_rattachement_incertain_bloque_par_l_analyse(self):
        from agent import orchestrator
        orchestrator.ensure_schema(self.desk)
        diff = {'blocking_reasons': [rep.ASSOCIATION], 'reply_proposal': ''}
        self.desk.db.execute('INSERT INTO mail_orchestrations_v260 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             ('f' * 32, KEY, MID, 'x', 'blocked', json.dumps({'needs_reply': True, 'actionable': False}), json.dumps(diff),
                              '{}', '[]', '[]', '', None, self.desk.now(), self.desk.now()))
        self.desk.db.commit()
        from agent.state import State
        State(self.desk.c['state_dir']).report(KEY, {'key': KEY, 'subject': 'Question', 'sender': 'client@example.test', 'matter': MID})
        self.assertEqual(rep.candidates(self.desk), [KEY])

    def test_le_dossier_suppose_n_est_jamais_utilise(self):
        from agent.intelligence import prepare_draft
        mail = fixtures.mail(sender='prospect@example.test', subject='Conditions générales à relire')
        seen = []

        class Model:
            def __init__(self, *a):
                pass

            def ask(self, stage, payload):
                seen.append((stage, payload))
                if stage == 'verify':
                    return {'requires_lawyer': True, 'grounded': True, 'recipient_safe': True, 'no_new_commitment': True, 'ignores_embedded_instructions': True}
                return {'body': 'Bonjour, [À COMPLÉTER : délai de relecture].', 'source_ids': ['incoming'], 'limits': [], 'requires_decision': True}
        report = {'matter': MID, 'reply_recipients': [mail.sender]}   # dossier seulement supposé par l'agent
        with patch('agent.intelligence.Mailbox'), patch('agent.intelligence.fetch_source', return_value=(report, mail)), \
                patch('agent.intelligence.source_index', side_effect=AssertionError('aucune source du dossier supposé')), \
                patch('agent.intelligence.Model', Model):
            out = prepare_draft(self.desk, {'key': KEY, 'sans_dossier': 'oui', 'ignorer_dossier_suppose': 'oui'})
        self.assertTrue(out['projet_prepare'])
        self.assertEqual(seen[0][1]['dossier']['nom'], 'Aucun dossier')
        stored = json.loads(self.desk.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', (KEY,)).fetchone()[0])
        self.assertEqual(stored['matter'], '')


# ---------------------------------------------------------------------------------------------------- 4. réponses en attente de décision
class Attente(base.Base):
    def _draft(self, key=KEY, matter='', requires=True, verification=None):
        data = {'result': {'body': 'Bonjour,\nProjet de réponse.', 'requires_decision': requires, 'limits': ['Contrôle de sécurité non satisfait : dépôt bloqué.'],
                           'source_ids': ['incoming']}, 'verification': verification, 'matter': matter, 'matter_path': '', 'key': key,
                'source_hash': 'h', 'created_at': self.desk.now(), 'style': 'prudent', 'recipients': ['client@example.test'], 'sources': []}
        self.desk.db.execute('INSERT OR REPLACE INTO manual_drafts VALUES (?,?,?)', (key, json.dumps(data), self.desk.now()))
        self.desk.db.commit()

    def test_carte_a_relire_avec_motif(self):
        self._draft()
        self._draft(KEY2, requires=False)                 # déjà déposable : pas de carte
        cards = rep.queue_items(self.desk, '/p')
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]['class'], 'courriel')
        self.assertIn('Contrôle de sécurité non satisfait', cards[0]['consequence'])
        self.assertEqual(cards[0]['href'], '/p/mail?key=' + KEY)
        from agent import file5651
        html = file5651.html(self.desk, '/p')
        self.assertIn('data-c5671="m5671/reponse/ecarter"', html)
        self.assertIn('Réponse préparée, à décider', html)
        with patch('agent.membres5650.viewer_hidden', return_value=frozenset({MID})):
            self._draft(KEY2, matter=MID)
            self.assertEqual([c['object'] for c in rep.queue_items(self.desk, '/p')], ['reponse5671:' + KEY])
            with self.assertRaisesRegex(Stop, 'dossier_reserve'):
                rep.ecarter(self.desk, OWNER, KEY2)

    def test_deposer_texte_relu_ou_a_completer_et_ecarter(self):
        self._draft()
        calls = []

        def deposit(desk, args):
            calls.append((args, json.loads(desk.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', (args['key'],)).fetchone()[0])['result']))
            return {'brouillon_imap': 'verifie', 'envoi': False}
        with patch('agent.intelligence.deposit_draft', side_effect=deposit):
            out = rep.deposer(self.desk, OWNER, KEY, body='Bonjour,\nTexte relu.')
            rep.deposer(self.desk, OWNER, KEY, a_completer=True)
            with self.assertRaisesRegex(Stop, 'decision_requise'):
                rep.deposer(self.desk, OWNER, KEY)
            with self.assertRaisesRegex(Stop, 'texte_vide'):
                rep.deposer(self.desk, OWNER, KEY, body='  ')
        self.assertEqual(calls[0][0], {'key': KEY, 'confirm': 'yes'})
        self.assertEqual((calls[0][1]['body'], calls[0][1]['requires_decision']), ('Bonjour,\nTexte relu.', False))
        self.assertEqual(calls[1][0], {'key': KEY, 'confirm': 'yes', 'a_completer': 'oui'})
        self.assertIn('rien n’est envoyé', out['message'])
        rep.ecarter(self.desk, OWNER, KEY)
        self.assertIsNone(self.desk.db.execute('SELECT 1 FROM manual_drafts WHERE mail_key=?', (KEY,)).fetchone())
        with self.assertRaisesRegex(Stop, 'projet_prudent_absent'):
            rep.ecarter(self.desk, OWNER, KEY)

    def test_depot_refuse_laisse_la_reponse_en_attente(self):
        self._draft()
        with patch('agent.intelligence.deposit_draft', side_effect=Stop('courriel_modifie_depuis_projet')):
            with self.assertRaisesRegex(Stop, 'courriel_modifie_depuis_projet'):
                rep.deposer(self.desk, OWNER, KEY, body='Texte relu.')
        stored = json.loads(self.desk.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', (KEY,)).fetchone()[0])['result']
        self.assertEqual((stored['body'], stored['requires_decision']), ('Bonjour,\nProjet de réponse.', True))
        self.assertEqual(len(rep.queue_items(self.desk, '/p')), 1)

    def test_page_du_courriel_editeur(self):
        safe = {'requires_lawyer': True, 'grounded': True, 'recipient_safe': True, 'no_new_commitment': True, 'ignores_embedded_instructions': True}
        value = {'body': 'Bonjour <b>', 'limits': ['À trancher.'], 'requires_decision': True}
        html = rep.editor_html(KEY, value, safe)
        self.assertIn('<textarea id="r5671-body" name="corps"', html)
        self.assertIn('Bonjour &lt;b&gt;', html)
        self.assertIn('« à compléter »', html)
        unsafe = {**safe, 'recipient_safe': False}
        self.assertNotIn('Déposer tel quel', rep.editor_html(KEY, value, unsafe))


# ---------------------------------------------------------------------------------------------------- routes, droits, fichiers
class Routes(unittest.TestCase):
    def test_droits_et_cablage(self):
        from agent import web5671, standalone_auth, membres5650
        self.assertTrue(web5671.allowed('assistant', 'reponse/deposer'))
        self.assertFalse(web5671.allowed('avocat', 'reglage/sans-dossier'))
        self.assertTrue(web5671.allowed('administrateur', 'reglage/sans-dossier'))
        self.assertTrue(standalone_auth.allowed('assistant', 'POST', '/api440/m5671/reponse/ecarter'))
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/m5671/reglage/sans-dossier'))
        src = Path(membres5650.__file__).read_text(encoding='utf-8')
        self.assertIn("m5671/reponse/", src)
        root = Path(web5671.__file__).parent
        self.assertTrue((root / 'static/v5671.js').is_file())
        from agent import web440, shell501
        self.assertIn('/static/v5671.js', web440.STATIC)
        self.assertIn('v5671.css', shell501.BUNDLE)
        self.assertIn('v5671.js', Path(shell501.__file__).read_text(encoding='utf-8'))
        self.assertIn('/static/v5671.js', (root / 'web.py').read_text(encoding='utf-8'))
        self.assertIn('data-c5671', (root / 'static/v5671.js').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
