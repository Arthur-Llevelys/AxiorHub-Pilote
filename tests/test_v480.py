"""4.8.0 : tableau d'apprentissage, profils de ton, règles « toujours », niveaux d'autonomie, journal de traçabilité."""
import json
import sqlite3
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import test_agent as fixtures
from agent import (autonomy480, learning480, rules480, style480, tone480, trace480)
from agent import echeances450 as ech
from agent.common import Stop, private_json
from agent.desk import Desk
from agent.memory import SentMemory
from test_v440_web import WebWorkshopTests as Base0
from test_v450_echeances import FakeDAV

DRAFT = ("Bonjour,\n\nJe vous confirme que le contrat est en cours de relecture et que nous reviendrons vers vous "
         "dès réception de la pièce attendue. N'hésitez pas à me contacter pour toute question.\n\nCordialement,\nMaître Exemple")
SAME_WITH_SIGNATURE = DRAFT + "\n-- \nCabinet Exemple, 1 rue de la Paix"
LIGHT = DRAFT.replace('Cordialement', 'Bien cordialement').replace('en cours de relecture', 'en relecture')
REWRITTEN = "Chère Madame,\n\nVoici ma réponse complète sur un tout autre ton : le sujet est clos, nous attendons l'audience fixée par le tribunal pour plaider.\n\nBien à vous"


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)

    def set_matters(self, rows):
        private_json(Path(self.f.c['matters_file']), rows)

    def matter(self, ident, client='Client DEMO', correspondents=None):
        return {'id': ident, 'client_name': client, 'path': '/Dossiers/' + ident, 'references': [ident], 'aliases': [client],
                'correspondents': correspondents if correspondents is not None else [{'email': 'client@example.test', 'role': 'client'}]}


# --------------------------------------------------------------------------- analyse de style
class Style(unittest.TestCase):
    def test_formulas_come_from_closed_lists_never_names(self):
        self.assertEqual(style480.opening('Bonjour Madame Dupont,\n\nTexte'), 'Bonjour')
        self.assertEqual(style480.opening('Cher Confrère,\nTexte'), 'Cher Confrère / Chère Consœur')
        self.assertEqual(style480.opening('Madame, Monsieur,\nTexte'), 'Madame, Monsieur')
        self.assertEqual(style480.opening('Madame Dupont,\nTexte'), 'Madame')
        self.assertEqual(style480.opening('Texte direct'), 'Aucune formule')
        self.assertEqual(style480.closing('Texte\n\nBien confraternellement,\nMe X'), 'Bien confraternellement')
        self.assertEqual(style480.closing('Texte\nJe vous prie d’agréer, Madame, Monsieur, l’expression de mes salutations distinguées.'),
                         'Salutations distinguées (formule complète)')

    def test_identical_text_with_added_signature_is_tel_quel(self):
        self.assertEqual(style480.classify(DRAFT, SAME_WITH_SIGNATURE)[0], 'tel_quel')
        self.assertEqual(style480.classify(DRAFT, DRAFT.replace('\n\n', '\r\n\r\n').upper())[0], 'tel_quel')

    def test_light_and_rewritten(self):
        self.assertEqual(style480.classify(DRAFT, LIGHT)[0], 'leger')
        self.assertEqual(style480.classify(DRAFT, REWRITTEN)[0], 'reecrit')

    def test_changes_are_categorised(self):
        cats = {(c['cat'], c['from'], c['to']) for c in style480.changes(DRAFT, LIGHT)}
        self.assertIn(('formule_fermeture', 'Cordialement', 'Bien cordialement'), cats)
        removed = {(c['cat'], c['from']) for c in style480.changes(DRAFT, DRAFT.replace("N'hésitez pas à me contacter pour toute question.", ''))}
        self.assertIn(('politesse_retiree', 'N’hésitez pas à me contacter'), removed)
        self.assertTrue([c for c in style480.changes(DRAFT, 'Bonjour,\n\nOk.\n\nCordialement') if c['cat'] == 'plus_court'])
        reg = style480.changes('Bonjour, je vous remercie de votre message et de votre confiance.', 'Salut, je te remercie de ton message et de ta confiance, merci à toi.')
        self.assertTrue([c for c in reg if c['cat'] == 'registre'])

    def test_no_case_content_survives_in_changes(self):
        text = "Bonjour Madame DUPONT,\n\nLe montant de 12 345,67 euros est dû par la société SECRETCO.\n\nCordialement"
        other = "Bonjour Madame DUPONT,\n\nLe montant de 12 345,67 euros est dû par la société SECRETCO selon la facture.\n\nBien cordialement"
        blob = json.dumps(style480.changes(text, other), ensure_ascii=False)
        for secret in ('DUPONT', 'SECRETCO', '12 345'):
            self.assertNotIn(secret, blob)


# --------------------------------------------------------------------------- tableau d'apprentissage
class Learning(Base):
    def record(self, key, month, draft, final, role_binding='[["client@example.test","client"]]', intent='status', day='10'):
        ok = learning480.record_match(self.f.c, key, '%s-%sT10:00:00+00:00' % (month, day), role_binding, intent, draft, final)
        self.assertTrue(ok)

    def test_monthly_share_by_type_and_role(self):
        self.record('a' * 64, '2026-08', DRAFT, SAME_WITH_SIGNATURE)
        self.record('b' * 64, '2026-08', DRAFT, LIGHT)
        self.record('c' * 64, '2026-08', DRAFT, REWRITTEN, intent='appointment')
        self.record('d' * 64, '2026-09', DRAFT, SAME_WITH_SIGNATURE, role_binding='[["greffe@tribunal.justice.fr","tiers"]]')
        data = learning480.dashboard(self.desk)
        months = {m['month']: m for m in data['months']}
        self.assertEqual(months['2026-08']['total'], 3)
        self.assertEqual((months['2026-08']['tel_quel'], months['2026-08']['leger'], months['2026-08']['reecrit']), (1, 1, 1))
        self.assertAlmostEqual(sum(v for v in months['2026-08']['pct'].values()), 100, delta=0.3)
        self.assertEqual({k['kind'] for k in data['by_kind']}, {'status', 'appointment'})
        self.assertEqual({r['role'] for r in data['by_role']}, {'client', 'greffe'})
        only = learning480.dashboard(self.desk, kind='appointment')
        self.assertEqual(only['total']['total'], 1)
        self.assertEqual(learning480.dashboard(self.desk, role='greffe')['total']['total'], 1)
        with self.assertRaises(Stop):
            learning480.dashboard(self.desk, kind='zz')

    def test_low_sample_is_flagged_and_empty_is_honest(self):
        empty = learning480.dashboard(self.desk)
        self.assertEqual(empty['total']['total'], 0)
        self.assertEqual(empty['trend']['direction'], 'insuffisant')
        self.record('a' * 64, '2026-09', DRAFT, LIGHT)
        self.assertTrue(learning480.dashboard(self.desk)['months'][0]['low_sample'])

    def test_trend_requires_enough_data_and_reports_progress(self):
        n = 0
        for month, shares in (('2026-03', 'RRRRR'), ('2026-04', 'RRRRR'), ('2026-05', 'RRRRR'), ('2026-07', 'TTTTT'), ('2026-08', 'TTTTT'), ('2026-09', 'TTTTT')):
            for ch in shares:
                n += 1
                self.record('%064x' % n, month, DRAFT, SAME_WITH_SIGNATURE if ch == 'T' else REWRITTEN)
        trend = learning480.dashboard(self.desk)['trend']
        self.assertEqual(trend['direction'], 'hausse')
        self.assertGreater(trend['recent'], trend['previous'])

    def test_top_changes_counted_per_corrected_mail(self):
        for i in range(3):
            self.record('%064x' % (i + 1), '2026-09', DRAFT, LIGHT, day='1%d' % i)
        top = learning480.dashboard(self.desk)['top_changes']
        closing = [t for t in top if t['cat'] == 'formule_fermeture'][0]
        self.assertEqual((closing['from'], closing['to'], closing['count']), ('Cordialement', 'Bien cordialement', 3))
        self.assertEqual(closing['share_pct'], 100.0)

    def test_only_matched_sent_mails_are_used_and_others_only_counted(self):
        memory = SentMemory(self.f.c)
        rows = [('m' * 64, 'matched_draft_and_sent', DRAFT, SAME_WITH_SIGNATURE),
                ('u' * 64, 'sent_example', '', 'SECRET-NON-RAPPROCHE contenu confidentiel qui ne doit jamais être lu ni conservé ici.'),
                ('w' * 64, 'matched_recipient_change', '', 'AUTRE-SECRET destinataires modifiés après le brouillon, texte non comparable.')]
        for key, prov, draft, body in rows:
            memory.db.execute('INSERT INTO examples_v2 VALUES (?,?,?,?,?,?,?,?,?,?)',
                              (key, 'DOS-001@x', '["client@example.test"]', '2026-09-12T09:00:00+00:00', body, draft, '{}', prov, memory.account(), '[["client@example.test","client"]]'))
        memory.db.commit()
        result = learning480.backfill(self.desk)
        self.assertEqual(result['added'], 1)
        data = learning480.dashboard(self.desk)
        self.assertEqual(data['total']['total'], 1)
        self.assertEqual(data['unmatched'], 2)
        dump = json.dumps([tuple(r) for r in self.desk.db.execute('SELECT * FROM outcomes480')], ensure_ascii=False)
        self.assertNotIn('SECRET', dump)
        self.assertEqual(learning480.backfill(self.desk)['added'], 0)     # idempotent

    def test_nothing_stored_can_reveal_the_text(self):
        draft = "Bonjour Madame DUPONT,\n\nLa société SECRETCO doit 12 345 euros.\n\nCordialement"
        self.record('e' * 64, '2026-09', draft, draft.replace('Cordialement', 'Bien cordialement') + ' Merci de payer.')
        dump = json.dumps([tuple(r) for r in self.desk.db.execute('SELECT * FROM outcomes480')], ensure_ascii=False)
        for secret in ('DUPONT', 'SECRETCO', '12 345'):
            self.assertNotIn(secret, dump)

    def test_forget_removes_the_outcome_and_failures_never_raise(self):
        self.record('f' * 64, '2026-09', DRAFT, LIGHT)
        learning480.forget(self.f.c, 'f' * 64)
        self.assertEqual(learning480.dashboard(self.desk)['total']['total'], 0)
        self.assertFalse(learning480.record_match({'state_dir': '/nonexistent/zzz'}, 'k', '2026-09-01T00:00:00+00:00', '[]', 'status', DRAFT, LIGHT))
        self.assertFalse(learning480.record_match(self.f.c, 'k', 'pas-une-date', '[]', 'status', DRAFT, LIGHT))
        self.assertFalse(learning480.record_match(self.f.c, 'k', '2026-09-01T00:00:00+00:00', '[]', 'status', '', LIGHT))

class SentIngest(Base):
    """Branchement réel sur la mémoire des envoyés : seuls les envois rapprochés d'un brouillon alimentent le tableau."""
    def setUp(self):
        super().setUp()
        self.f.c['memory'] = {'enabled': True, 'max_messages_per_run': 30, 'retention_days': 90, 'max_examples': 2}
        self.memory = SentMemory(self.f.c)

    def sent(self, uid, text):
        from datetime import datetime, timedelta, timezone
        m = fixtures.mail(uid=uid, sender='cabinet@example.test')
        m.msg.replace_header('To', 'client@example.test')
        m.msg.set_content(text)
        m.mailbox = 'Sent'
        m.timestamp = datetime.now(timezone.utc) + timedelta(seconds=1)
        return m

    def test_matched_send_feeds_the_dashboard_and_unmatched_does_not(self):
        key = self.f.engine.process(fixtures.mail())
        proposal = json.loads((Path(self.f.c['state_dir']) / 'reports' / (key + '.json')).read_text())
        corrected = proposal['draft_body'].replace('Bonjour', 'Bonsoir') + '\n\nJe reste à votre disposition pour toute précision utile.'
        sent = self.sent('100', corrected)
        sent.msg['In-Reply-To'] = proposal['incoming_message_id']
        self.assertEqual(self.memory.ingest(sent, self.f.matters, self.memory.proposals()), 'matched_draft_and_sent')
        free = self.sent('101', 'Bonjour, ceci est une réponse écrite sans brouillon de l’agent, assez longue pour être conservée comme exemple.')
        self.assertEqual(self.memory.ingest(free, self.f.matters, []), 'sent_example')
        data = learning480.dashboard(self.desk)
        self.assertEqual(data['total']['total'], 1)
        self.assertEqual(data['by_kind'][0]['kind'], 'status')
        self.assertEqual(data['by_role'][0]['role'], 'client')
        self.assertEqual(data['unmatched'], 1)
        cats = {t['cat'] for t in data['top_changes']}
        self.assertIn('formule_ouverture', cats)
        from agent.common import digest
        self.memory.forget(digest(self.memory.account() + sent.mid))
        self.assertEqual(learning480.dashboard(self.desk)['total']['total'], 0)


# --------------------------------------------------------------------------- profils de ton
class Tone(Base):
    def setUp(self):
        super().setUp()
        self.set_matters([self.matter('DOS-001', correspondents=[
            {'email': 'client@example.test', 'role': 'client'}, {'email': 'confrere@cabinet-avocat.example', 'role': 'confrere_adverse'},
            {'email': 'partie@societe.example', 'role': 'tiers'}, {'email': 'prospect@example.test', 'role': 'prospect'}])])

    def test_detection_by_matter_role_and_address(self):
        d = lambda *a: tone480.detect(self.desk, 'DOS-001', list(a))
        self.assertEqual(d('client@example.test')['profile'], 'client')
        self.assertEqual(d('client@example.test')['confidence'], 'confirmé')
        self.assertEqual(d('prospect@example.test')['profile'], 'client')
        self.assertEqual(d('confrere@cabinet-avocat.example')['profile'], 'confrere')
        self.assertEqual(d('partie@societe.example')['profile'], 'adversaire')
        self.assertEqual(d('partie@societe.example')['confidence'], 'probable')
        self.assertEqual(tone480.detect(self.desk, '', ['greffe.civil@tj-lyon.justice.fr'])['profile'], 'greffe')
        self.assertEqual(tone480.detect(self.desk, '', ['contact@urssaf.fr'])['profile'], 'administration')

    def test_unknown_recipient_imposes_no_profile(self):
        result = tone480.detect(self.desk, 'DOS-001', ['quelquun@inconnu.example'])
        self.assertIsNone(result['profile'])
        self.assertEqual(result['confidence'], 'incertain')
        self.assertIsNone(tone480.context(self.desk, 'DOS-001', ['quelquun@inconnu.example']))
        self.assertIsNone(tone480.detect(self.desk, 'DOS-001', ['pas-une-adresse'])['profile'])

    def test_lawyer_correction_wins_and_is_reversible(self):
        tone480.set_override(self.desk, 'adresse', 'client@example.test', 'confrere')
        self.assertEqual(tone480.detect(self.desk, 'DOS-001', ['client@example.test'])['profile'], 'confrere')
        tone480.set_override(self.desk, 'domaine', 'societe.example', 'client')
        self.assertEqual(tone480.detect(self.desk, 'DOS-001', ['partie@societe.example'])['profile'], 'client')
        tone480.set_override(self.desk, 'adresse', 'client@example.test', '')
        self.assertEqual(tone480.detect(self.desk, 'DOS-001', ['client@example.test'])['profile'], 'client')
        for bad in (('adresse', 'nope', 'client'), ('autre', 'x@y.fr', 'client'), ('domaine', 'x', 'client'), ('adresse', 'a@b.fr', 'inconnu')):
            with self.assertRaises(Stop):
                tone480.set_override(self.desk, *bad)

    def test_mixed_recipients_use_the_most_formal(self):
        result = tone480.detect(self.desk, 'DOS-001', ['client@example.test', 'greffe@tj.justice.fr'])
        self.assertEqual(result['profile'], 'greffe')
        self.assertTrue(result['mixed'])
        self.assertIn('plus formel', ' '.join(tone480.context(self.desk, 'DOS-001', ['client@example.test', 'greffe@tj.justice.fr'])['consignes']))

    def test_profiles_are_distinct_editable_and_resettable(self):
        defaults = {r: tone480.profile(self.desk, r) for r in tone480.PROFILES}
        self.assertEqual(len({p['closing'] + p['opening'] for p in defaults.values()}), 4)
        self.assertLess(defaults['greffe']['max_words'], defaults['client']['max_words'])
        saved = tone480.save_profile(self.desk, 'client', {'closing': 'Très cordialement', 'max_words': 180})
        self.assertEqual((saved['closing'], saved['max_words'], saved['source']), ('Très cordialement', 180, 'manuel'))
        self.assertIn('Très cordialement', ' '.join(tone480.context(self.desk, 'DOS-001', ['client@example.test'])['consignes']))
        for bad in ({'max_words': 5}, {'max_words': 'x'}, {'closing': 'x' * 400}):
            with self.assertRaises(Stop):
                tone480.save_profile(self.desk, 'client', bad)
        self.assertEqual(tone480.reset_profile(self.desk, 'client')['closing'], defaults['client']['closing'])
        with self.assertRaises(Stop):
            tone480.profile(self.desk, 'inconnu')

    def test_learned_suggestions_need_a_clear_majority_and_a_known_formula(self):
        for i in range(4):
            learning480.record_match(self.f.c, '%064x' % i, '2026-09-1%dT10:00:00+00:00' % i, '[["client@example.test","client"]]', 'status', DRAFT,
                                     DRAFT.replace('Cordialement', 'Très cordialement'))
        sugg = tone480.learned_suggestions(self.desk)
        self.assertEqual([(s['role'], s['field'], s['value']) for s in sugg if s['field'] == 'closing'], [('client', 'closing', 'Très cordialement')])
        tone480.adopt(self.desk, 'client', 'closing', 'Très cordialement')
        self.assertFalse([s for s in tone480.learned_suggestions(self.desk) if s['field'] == 'closing'])
        with self.assertRaises(Stop):
            tone480.adopt(self.desk, 'client', 'closing', 'Formule inventée')


# --------------------------------------------------------------------------- règles « toujours faire comme ça »
class Rules(Base):
    def setUp(self):
        super().setUp()
        self.set_matters([self.matter('DOS-001'), self.matter('DOS-002', 'Autre client', [{'email': 'autre@example.test', 'role': 'client'}])])

    def make(self, rule_type='formule_fermeture', value='Bien cordialement', scope='dossier', scope_value='DOS-001', **kw):
        return rules480.create(self.desk, rule_type, value, scope, scope_value, confirm='yes', **kw)

    def test_a_rule_is_never_created_without_explicit_confirmation(self):
        for confirm in ('', 'no', 'true', None):
            with self.assertRaises(Stop) as ctx:
                rules480.create(self.desk, 'formule_fermeture', 'Bien cordialement', 'dossier', 'DOS-001', confirm=confirm)
            self.assertEqual(str(ctx.exception), 'confirmation_regle_requise')
        self.assertEqual(rules480.listing(self.desk), [])

    def test_candidates_from_a_correction_create_nothing(self):
        out = rules480.candidates_from_correction(self.desk, DRAFT, LIGHT, ['client@example.test'])
        self.assertTrue(out['candidates'])
        self.assertEqual(rules480.listing(self.desk), [])
        cand = [c for c in out['candidates'] if c['rule_type'] == 'formule_fermeture'][0]
        self.assertEqual(cand['value'], 'Bien cordialement')
        self.assertEqual({o['scope'] for o in cand['options']}, {'destinataire', 'dossier', 'profil'})
        self.assertEqual(cand['recommended_scope'], 'dossier')
        self.assertIn('Cas isolé', cand['warning'])
        self.assertEqual(rules480.candidates_from_correction(self.desk, DRAFT, SAME_WITH_SIGNATURE, ['client@example.test'])['candidates'], [])

    def test_repeated_correction_raises_evidence_and_recommends_profile(self):
        for i in range(3):
            learning480.record_match(self.f.c, '%064x' % i, '2026-09-1%dT10:00:00+00:00' % i, '[["client@example.test","client"]]', 'status', DRAFT, LIGHT)
        cand = [c for c in rules480.candidates_from_correction(self.desk, DRAFT, LIGHT, ['client@example.test'])['candidates']
                if c['rule_type'] == 'formule_fermeture'][0]
        self.assertEqual(cand['evidence_count'], 3)
        self.assertEqual(cand['recommended_scope'], 'profil')
        self.assertIn('3 envoi', cand['warning'])

    def test_one_sentence_names_the_scope(self):
        r = self.make()
        self.assertEqual(r['sentence'], 'Dans le dossier « Client DEMO » : toujours terminer par « Bien cordialement ».')
        self.assertEqual(self.make('longueur_max', '150', 'destinataire', 'Client@Example.test')['sentence'],
                         'Pour les courriels adressés à client@example.test : toujours ne pas dépasser 150 mots.')
        self.assertIn('vouvoyer', self.make('registre', 'vous', 'profil', 'greffe')['sentence'])
        self.assertIn('rendez-vous', self.make('formule_ouverture', 'Bonjour', 'type', 'appointment')['sentence'])

    def test_a_rule_applies_only_inside_its_scope(self):
        self.make()                                                               # dossier DOS-001
        got = lambda matter, rec, intent='', profile=None: [r['id'] for r in rules480.applicable(self.desk, matter, rec, intent, profile)['applied']]
        self.assertTrue(got('DOS-001', ['client@example.test']))
        self.assertEqual(got('DOS-002', ['autre@example.test']), [])
        self.assertEqual(got('', ['client@example.test']), [])
        r = self.make('formule_fermeture', 'Confraternellement', 'destinataire', 'confrere@x.example')
        self.assertEqual(got('DOS-002', ['confrere@x.example']), [r['id']])
        self.assertEqual(got('DOS-002', ['autre@example.test']), [])
        t = self.make('longueur_max', '100', 'type', 'appointment')
        self.assertEqual(got('DOS-002', ['autre@example.test'], 'appointment'), [t['id']])
        self.assertEqual(got('DOS-002', ['autre@example.test'], 'status'), [])
        p = self.make('registre', 'vous', 'profil', 'greffe')
        self.assertEqual(got('DOS-002', ['x@y.fr'], '', 'greffe'), [p['id']])
        self.assertEqual(got('DOS-002', ['x@y.fr'], '', 'client'), [])

    def test_most_specific_rule_wins_and_same_scope_conflict_withholds_both(self):
        profil = self.make('formule_fermeture', 'Cordialement', 'profil', 'client')
        dossier = self.make('formule_fermeture', 'Bien cordialement', 'dossier', 'DOS-001')
        res = rules480.applicable(self.desk, 'DOS-001', ['client@example.test'], '', 'client')
        self.assertEqual([r['id'] for r in res['applied']], [dossier['id']])
        self.assertEqual([r['id'] for r in res['withheld']], [profil['id']])
        other = self.make('formule_fermeture', 'Très cordialement', 'dossier', 'DOS-001')
        self.assertEqual(other['conflicts_with'], [dossier['id']])
        res = rules480.applicable(self.desk, 'DOS-001', ['client@example.test'], '', 'client')
        self.assertEqual(res['applied'], [])
        self.assertEqual({r['id'] for r in res['withheld']}, {profil['id'], dossier['id'], other['id']})
        flagged = {r['id']: r['conflicts'] for r in rules480.listing(self.desk)}
        self.assertEqual(flagged[dossier['id']], [other['id']])
        rules480.set_status(self.desk, other['id'], 'suspendue')
        self.assertEqual([r['id'] for r in rules480.applicable(self.desk, 'DOS-001', ['client@example.test'], '', 'client')['applied']], [dossier['id']])

    def test_contradictory_free_text_rules_are_withheld_not_both_applied(self):
        a = self.make('consigne', 'Toujours proposer un rendez-vous téléphonique au client')
        b = self.make('consigne', 'Ne jamais proposer de rendez-vous téléphonique au client')
        self.assertEqual(b['conflicts_with'], [a['id']])
        res = rules480.applicable(self.desk, 'DOS-001', ['client@example.test'])
        self.assertEqual((res['applied'], len(res['withheld'])), ([], 2))
        c = self.make('consigne', 'Citer le numéro de RG dans l’objet')
        self.assertEqual([r['id'] for r in rules480.applicable(self.desk, 'DOS-001', ['client@example.test'])['applied']], [c['id']])

    def test_suspend_resume_delete(self):
        r = self.make()
        rules480.set_status(self.desk, r['id'], 'suspendue')
        self.assertEqual(rules480.applicable(self.desk, 'DOS-001', [])['applied'], [])
        rules480.set_status(self.desk, r['id'], 'active')
        self.assertEqual(len(rules480.applicable(self.desk, 'DOS-001', [])['applied']), 1)
        rules480.delete(self.desk, r['id'])
        self.assertEqual(rules480.listing(self.desk), [])
        self.assertEqual(rules480.applicable(self.desk, 'DOS-001', [])['applied'], [])
        actions = [json.loads(x[0]) for x in self.desk.db.execute("SELECT data FROM audit WHERE action='regle_480_supprimee'")]
        self.assertEqual(actions[0]['id'], r['id'])
        for bad in ((lambda: rules480.set_status(self.desk, r['id'], 'active')), (lambda: rules480.delete(self.desk, 999)), (lambda: rules480.set_status(self.desk, 'x', 'active'))):
            with self.assertRaises(Stop):
                bad()

    def test_invalid_rules_are_refused(self):
        for args in (('formule_fermeture', 'Formule inventée', 'dossier', 'DOS-001'), ('longueur_max', '5', 'dossier', 'DOS-001'),
                     ('longueur_max', 'abc', 'dossier', 'DOS-001'), ('registre', 'on', 'dossier', 'DOS-001'),
                     ('formule_fermeture', 'Cordialement', 'dossier', 'INCONNU'), ('formule_fermeture', 'Cordialement', 'destinataire', 'pas-une-adresse'),
                     ('formule_fermeture', 'Cordialement', 'type', 'inconnu'), ('formule_fermeture', 'Cordialement', 'profil', 'inconnu'),
                     ('formule_fermeture', 'Cordialement', 'cabinet', ''), ('consigne', 'abc', 'dossier', 'DOS-001'),
                     ('consigne', 'ligne 1\nligne 2 assez longue', 'dossier', 'DOS-001'), ('autre', 'x', 'dossier', 'DOS-001')):
            with self.assertRaises(Stop, msg=args):
                rules480.create(self.desk, *args[:1], args[1], args[2], args[3], confirm='yes')

    def test_duplicate_rule_is_reactivated_not_duplicated(self):
        a = self.make()
        rules480.set_status(self.desk, a['id'], 'suspendue')
        b = self.make()
        self.assertEqual((a['id'], b['created']), (b['id'], False))
        self.assertEqual(len(rules480.listing(self.desk)), 1)
        self.assertEqual(rules480.listing(self.desk)[0]['status'], 'active')

    def test_violations_are_reported_by_deterministic_checks(self):
        rules = [dict(id=1, rule_type='formule_fermeture', value='Bien cordialement'), dict(id=2, rule_type='longueur_max', value='10'),
                 dict(id=3, rule_type='formule_ouverture', value='Madame, Monsieur'), dict(id=4, rule_type='registre', value='vous')]
        out = rules480.violations(rules, DRAFT)
        self.assertEqual(len(out), 3)
        self.assertEqual(rules480.violations(rules[:1], 'Texte.\n\nBien cordialement'), [])

    def test_prepare_injects_only_applicable_rules_and_profile(self):
        mine = self.make()
        self.make('formule_fermeture', 'Confraternellement', 'dossier', 'DOS-002')
        payload = {}
        info = rules480.prepare(self.desk, payload, 'DOS-001', ['client@example.test'], 'status')
        self.assertEqual(info['applied'], [mine['id']])
        self.assertEqual([r['id'] for r in payload['regles_du_cabinet']], [mine['id']])
        self.assertEqual(payload['profil_de_ton']['profil'], 'client')
        self.assertEqual(rules480.listing(self.desk)[-1]['uses'] + rules480.listing(self.desk)[0]['uses'], 1)
        blob = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn('Confraternellement', blob)
        payload2 = {}
        rules480.prepare(self.desk, payload2, 'DOS-001', ['inconnu@zzz.example'], '')
        self.assertNotIn('profil_de_ton', payload2)


# --------------------------------------------------------------------------- niveaux d'autonomie
class Autonomy(Base):
    def test_defaults_keep_the_4_7_behaviour(self):
        self.assertEqual(autonomy480.level(self.desk, 'agenda_echeance'), 'agir')
        self.assertEqual(autonomy480.level(self.desk, 'courriel_reponse'), 'brouillon')
        self.assertEqual(autonomy480.level(self.desk, 'classement'), 'agir')
        self.desk.setting('automation:deadline_calendar450', False)
        self.assertEqual(autonomy480.level(self.desk, 'agenda_echeance'), 'propose')

    def test_acting_requires_explicit_confirmation_and_is_reversible_at_any_time(self):
        autonomy480.set_level(self.desk, 'agenda_echeance', 'propose')
        for confirm in ('', 'no', None, True):
            with self.assertRaises(Stop) as ctx:
                autonomy480.set_level(self.desk, 'agenda_echeance', 'agir', confirm)
            self.assertEqual(str(ctx.exception), 'confirmation_autonomie_requise')
        self.assertEqual(autonomy480.level(self.desk, 'agenda_echeance'), 'propose')
        autonomy480.set_level(self.desk, 'agenda_echeance', 'agir', 'yes')
        self.assertEqual(autonomy480.level(self.desk, 'agenda_echeance'), 'agir')
        autonomy480.set_level(self.desk, 'agenda_echeance', 'propose')            # retour immédiat, sans confirmation
        self.assertEqual(autonomy480.level(self.desk, 'agenda_echeance'), 'propose')
        history = autonomy480.snapshot(self.desk)['history']
        self.assertEqual([(h['old'], h['new']) for h in history][:3], [('agir', 'propose'), ('propose', 'agir'), ('agir', 'propose')])

    def test_levels_offered_depend_on_the_task_and_outgoing_actions_are_locked(self):
        with self.assertRaises(Stop):
            autonomy480.set_level(self.desk, 'courriel_reponse', 'agir', 'yes')         # jamais « agir » pour une réponse
        with self.assertRaises(Stop):
            autonomy480.set_level(self.desk, 'agenda_echeance', 'brouillon')
        for locked, _ in autonomy480.LOCKED:
            with self.assertRaises(Stop) as ctx:
                autonomy480.set_level(self.desk, locked, 'agir', 'yes')
            self.assertEqual(str(ctx.exception), 'action_toujours_soumise_a_validation')
        with self.assertRaises(Stop):
            autonomy480.set_level(self.desk, 'inconnue', 'propose')
        for task in ('courriel_reponse', 'acte_courrier'):
            self.assertNotIn('agir', autonomy480.TASKS[task]['levels'])
        names = {k for k, _ in autonomy480.LOCKED}
        self.assertTrue({'envoi_courriel', 'depot_rpva', 'signature', 'paiement', 'facturation', 'suppression'} <= names)

    def test_prudent_mode_lowers_everything_and_restores_settings(self):
        autonomy480.set_level(self.desk, 'agenda_echeance', 'agir', 'yes')
        autonomy480.set_prudent(self.desk, True)
        self.assertEqual({t: autonomy480.level(self.desk, t) for t in autonomy480.TASKS},
                         {t: spec['levels'][0] for t, spec in autonomy480.TASKS.items()})
        autonomy480.set_prudent(self.desk, False)
        self.assertEqual(autonomy480.level(self.desk, 'agenda_echeance'), 'agir')

    def deadline(self):
        row = ech.create_deadline(self.desk, 'DOS-001', 'appel_jugement_contentieux', '2026-09-10')
        ech.confirm(self.desk, FakeDAV(), row['id'])
        return row['id']

    def test_deadline_calendar_follows_the_level(self):
        ident = self.deadline()
        dav = FakeDAV()
        autonomy480.set_level(self.desk, 'agenda_echeance', 'propose')
        self.assertEqual(ech.sync_calendar(self.desk, dav, ident), 'a_valider')
        self.assertEqual(dav.puts, 0)
        pending = autonomy480.pending_list(self.desk)
        self.assertEqual(len(pending), 1)
        self.assertEqual(ech.sync_calendar(self.desk, dav, ident), 'a_valider')
        self.assertEqual(len(autonomy480.pending_list(self.desk)), 1)              # pas de doublon
        autonomy480.pending_decide(self.desk, pending[0]['id'], 'execute', dav)
        self.assertEqual(dav.puts, 1)
        self.assertEqual(ech._row(self.desk, ident)['calendar_state'], 'cree')
        with self.assertRaises(Stop):
            autonomy480.pending_decide(self.desk, pending[0]['id'], 'execute', dav)
        autonomy480.set_level(self.desk, 'agenda_echeance', 'agir', 'yes')
        ident2 = ech.create_deadline(self.desk, 'DOS-001', 'appel_jugement_contentieux', '2026-09-11')['id']
        self.assertIn(ech.sync_calendar(self.desk, dav, ident2), ('cree', 'mis_a_jour'))
        self.assertEqual(dav.puts, 2)

    def test_dismissed_proposal_writes_nothing(self):
        ident = self.deadline()
        autonomy480.set_level(self.desk, 'agenda_echeance', 'propose')
        dav = FakeDAV()
        ech.sync_calendar(self.desk, dav, ident)
        pid = autonomy480.pending_list(self.desk)[0]['id']
        autonomy480.pending_decide(self.desk, pid, 'dismiss')
        self.assertEqual((dav.puts, autonomy480.pending_list(self.desk)), (0, []))

    def test_notice_calendar_follows_the_level(self):
        from agent import notices440
        extraction = {'rg': 'RG 26/1', 'act_by': '', 'events': [{'date': date(2026, 11, 5), 'time': '', 'specific': True, 'explicit_year': True,
                                                                  'role': 'audience', 'roles': ['audience'], 'context': 'audience'}]}
        matter = {'id': 'DOS-001', 'client_name': 'Client DEMO'}
        dav = FakeDAV()
        autonomy480.set_level(self.desk, 'agenda_avis', 'propose')
        out = notices440._write_calendar(self.desk, dav, '/Dossiers/DEMO/avis.pdf', 'e1', matter, '', extraction, 'avis.pdf')
        self.assertEqual((out[0]['calendar'], dav.puts), ('a_valider', 0))
        self.assertEqual(len(autonomy480.pending_list(self.desk)), 1)
        autonomy480.set_level(self.desk, 'agenda_avis', 'agir', 'yes')
        out = notices440._write_calendar(self.desk, dav, '/Dossiers/DEMO/avis.pdf', 'e1', matter, '', extraction, 'avis.pdf')
        self.assertEqual((out[0]['calendar'], dav.puts), ('cree', 1))

    def test_background_classification_passes_follow_the_level(self):
        from agent import operations
        jobs = lambda: {r[0] for r in self.desk.db.execute("SELECT kind FROM jobs")}
        autonomy480.set_level(self.desk, 'classement', 'propose')
        operations.automation_tick(self.desk)
        self.assertFalse(jobs() & {'reconcile_inbox', 'classify_portfolio'})
        self.desk.db.execute("DELETE FROM settings WHERE key LIKE 'auto:%'")
        self.desk.db.commit()
        autonomy480.set_level(self.desk, 'classement', 'agir', 'yes')
        operations.automation_tick(self.desk)
        self.assertTrue(jobs() & {'reconcile_inbox', 'classify_portfolio'})

    def test_engine_replies_only_when_the_level_allows_a_draft(self):
        engine, model = self.f.engine, self.f.model
        autonomy480.set_level(self.desk, 'courriel_reponse', 'propose')
        key = engine.process(fixtures.mail())
        self.assertEqual(self.f.status(key), 'review')
        self.assertEqual(engine.state.get(key)[1], 'autonomie_proposer_seulement')
        self.assertFalse(self.f.box.appended)
        self.assertFalse([c for c in model.calls if c[0] == 'compose'])
        autonomy480.set_level(self.desk, 'courriel_reponse', 'brouillon')
        key = engine.process(fixtures.mail(uid='2'))
        self.assertEqual(self.f.status(key), 'drafted')
        self.assertEqual(len(self.f.box.appended), 1)


# --------------------------------------------------------------------------- moteur : ton et règles dans la requête
class EngineIntegration(Base):
    def test_compose_payload_carries_tone_and_only_applicable_rules(self):
        mine = rules480.create(self.desk, 'formule_fermeture', 'Bien cordialement', 'dossier', 'DOS-001', confirm='yes')
        key = self.f.engine.process(fixtures.mail())
        self.assertEqual(self.f.status(key), 'drafted')
        payload = [c[1] for c in self.f.model.calls if c[0] == 'compose'][0]
        self.assertEqual(payload['profil_de_ton']['profil'], 'client')
        self.assertEqual([r['id'] for r in payload['regles_du_cabinet']], [mine['id']])
        report = json.loads((Path(self.f.c['state_dir']) / 'reports' / (key + '.json')).read_text())
        self.assertEqual(report['regles480']['appliquees'], [mine['id']])
        self.assertTrue(report['regles480']['ecarts'])            # le brouillon du modèle factice se termine sans « Bien cordialement »

    def test_a_suspended_rule_does_not_reach_the_model(self):
        r = rules480.create(self.desk, 'registre', 'vous', 'dossier', 'DOS-001', confirm='yes')
        rules480.set_status(self.desk, r['id'], 'suspendue')
        self.f.engine.process(fixtures.mail())
        payload = [c[1] for c in self.f.model.calls if c[0] == 'compose'][0]
        self.assertNotIn('regles_du_cabinet', payload)


# --------------------------------------------------------------------------- journal de traçabilité
class FakeCompose:
    """Modèle dont seule la dernière couche (_leaf) est simulée : tout le reste du chemin est réel."""
    COMPOSE = json.dumps({'can_draft': False, 'body': '', 'source_ids': [], 'slot_ids': [], 'missing_information': [],
                          'requires_legal_work': False, 'reason': ''})


def make_model(cfg, leaf=None):
    from agent.model import Model
    m = object.__new__(Model)
    m.cfg = dict(cfg)
    m.provider_type = cfg.get('provider_type', 'ollama')
    m.provider_id = cfg.get('provider_id', 'ollama')
    m.last_provider, m.last_model, m.usage_cost_usd, m.usage_cost_known = m.provider_id, cfg.get('model', ''), 0.0, True
    m._leaf = leaf or (lambda messages, t, mt, js: FakeCompose.COMPOSE)
    return m


class Trace(Base):
    SECRET = 'CONTENU-CONFIDENTIEL-DU-COURRIEL'

    def cfg(self, **kw):
        return {'state_dir': self.f.c['state_dir'], 'provider_type': 'ollama', 'provider_id': 'ollama', 'model': 'modele-local', 'purpose': 'mail_drafting', **kw}

    def payload(self):
        return {'incoming': {'subject': 'Objet du courriel', 'text': self.SECRET}, 'matter': {'id': 'DOS-001', 'client_name': 'Client DEMO'},
                'sources': [{'id': 'doc-1', 'kind': 'document', 'path': '/Dossiers/DEMO/contrat.docx', 'text': self.SECRET},
                            {'id': 'mail-0', 'kind': 'email_history', 'content': self.SECRET}], 'today': '2026-09-25'}

    def calls(self):
        trace480.ensure_schema(self.desk.db)
        return [dict(r) for r in self.desk.db.execute('SELECT * FROM trace_calls480 ORDER BY id')]

    def test_every_model_call_leaves_references_but_never_the_text(self):
        trace480.begin('42', 'run', 'DOS-001')
        self.addCleanup(trace480.end)
        make_model(self.cfg()).ask('compose', self.payload())
        (call,) = self.calls()
        self.assertEqual((call['stage'], call['model'], call['external'], call['destination'], call['matter'], call['job_id']),
                         ('compose', 'modele-local', 0, 'local', 'DOS-001', '42'))
        self.assertEqual(call['status'], 'ok')
        sources = json.loads(call['sources'])
        self.assertEqual([(s['kind'], s['id']) for s in sources], [('email_received', 'incoming'), ('document', 'doc-1'), ('email_history', 'mail-0')])
        self.assertEqual(sources[1]['label'], '/Dossiers/DEMO/contrat.docx')
        self.assertEqual(len(call['sha256']), 64)
        self.assertGreater(call['chars_sent'], 100)
        self.assertIn('incoming', json.loads(call['fields']))
        dump = json.dumps([list(r.values()) for r in self.calls()], ensure_ascii=False)
        self.assertNotIn(self.SECRET, dump)

    def test_external_destination_is_flagged_with_its_host(self):
        make_model(self.cfg(provider_type='openrouter', provider_id='openrouter', url='https://openrouter.ai/api/v1', model='x/y')).ask('compose', self.payload())
        (call,) = self.calls()
        self.assertEqual((call['external'], call['destination'], call['provider']), (1, 'openrouter.ai', 'openrouter'))

    def test_failed_calls_are_logged_and_still_raise(self):
        def boom(*a):
            raise Stop('fournisseur_ia_injoignable')
        with self.assertRaises(Stop):
            make_model(self.cfg(), boom).ask('compose', self.payload())
        (call,) = self.calls()
        self.assertEqual((call['status'], call['error']), ('error', 'fournisseur_ia_injoignable'))

    def test_matter_comes_from_the_payload_when_no_job_context(self):
        trace480.end()
        make_model(self.cfg()).ask('compose', self.payload())
        self.assertEqual(self.calls()[0]['matter'], 'DOS-001')

    def test_applied_rules_and_profile_are_noted_on_the_call(self):
        trace480.begin('7', 'run', 'DOS-001')
        self.addCleanup(trace480.end)
        trace480.note(regles=[3, 4], profil_de_ton='client')
        make_model(self.cfg()).ask('compose', self.payload())
        notes = json.loads(self.calls()[0]['notes'])
        self.assertEqual((notes['regles'], notes['profil_de_ton']), ([3, 4], 'client'))

    def test_recording_never_breaks_the_work(self):
        trace480.record_call({'state_dir': '/nonexistent/x'}, 'compose', {}, [], 'ok')
        trace480.record_call({}, 'compose', {}, [], 'ok')
        trace480.record_call(None, 'compose', None, None, 'ok')
        broken = self.f.base / 'etat-casse'
        broken.mkdir()
        (broken / 'desk.sqlite3').write_text('pas une base')
        trace480.record_call({'state_dir': str(broken)}, 'compose', {}, [], 'ok')
        self.assertEqual(make_model(self.cfg(state_dir=str(broken))).ask('compose', self.payload())['can_draft'], False)

    def seed(self):
        trace480.begin('1', 'run', 'DOS-001')
        self.addCleanup(trace480.end)
        for i in range(3):
            make_model(self.cfg()).ask('compose', self.payload())
        make_model(self.cfg(provider_type='openrouter', provider_id='openrouter', url='https://api.example-ia.test/v1', model='ext')).ask('compose', self.payload())

    def test_what_did_the_model_see_on_this_matter_and_when(self):
        self.seed()
        view = trace480.matter_view(self.desk, 'DOS-001')
        self.assertEqual((view['summary']['calls'], view['summary']['external_calls'], view['summary']['distinct_sources']), (4, 1, 3))
        doc = [s for s in view['model_saw'] if s['id'] == 'doc-1'][0]
        self.assertEqual((doc['calls'], doc['external_calls'], doc['kind']), (4, 1, 'document'))
        self.assertEqual(doc['destinations'], ['api.example-ia.test', 'local'])
        self.assertTrue(doc['first_seen'] <= doc['last_seen'])
        self.assertEqual(trace480.matter_view(self.desk, 'DOS-002')['summary']['calls'], 0)
        today_utc=datetime.now(timezone.utc).date()
        future = (today_utc + timedelta(days=3)).isoformat()
        self.assertEqual(trace480.matter_view(self.desk, 'DOS-001', since=future)['summary']['calls'], 0)
        self.assertEqual(trace480.matter_view(self.desk, 'DOS-001', until=today_utc.isoformat())['summary']['calls'], 4)
        for bad in (dict(matter=''), dict(matter='../x'), dict(matter='DOS-001', since='hier')):
            with self.assertRaises(Stop):
                trace480.matter_view(self.desk, **bad)

    def test_events_explain_what_was_produced_and_why(self):
        trace480.event(self.desk, 'DOS-001', 'produit', 'Brouillon de réponse déposé', 'Réponse à un courriel ; règles 3', {'mail_key': 'k'})
        autonomy480.set_level(self.desk, 'agenda_echeance', 'propose')
        ident = ech.create_deadline(self.desk, 'DOS-001', 'appel_jugement_contentieux', '2026-09-10')['id']
        ech.confirm(self.desk, FakeDAV(), ident)
        ech.sync_calendar(self.desk, FakeDAV(), ident)
        kinds = {e['kind'] for e in trace480.matter_view(self.desk, 'DOS-001')['events']}
        self.assertEqual(kinds, {'produit', 'propose'})

    def test_export_is_complete_hashed_audited_and_free_of_content(self):
        self.seed()
        trace480.event(self.desk, 'DOS-001', 'produit', 'Brouillon', 'Parce que')
        j = trace480.export(self.desk, 'DOS-001', 'json')
        import hashlib
        self.assertEqual(hashlib.sha256(j['content'].encode()).hexdigest(), j['sha256'])
        data = json.loads(j['content'])
        self.assertEqual(data['summary']['calls'], 4)
        self.assertEqual(j['filename'], 'tracabilite-DOS-001.json')
        c = trace480.export(self.desk, 'DOS-001', 'csv')
        lines = c['content'].splitlines()
        self.assertEqual(len(lines), 1 + 4 + 1)
        self.assertIn('externe', lines[0])
        self.assertEqual(sum(1 for l in lines if ';oui;' in l), 1)
        for out in (j, c):
            self.assertNotIn(self.SECRET, out['content'])
        audited = [json.loads(r[0]) for r in self.desk.db.execute("SELECT data FROM audit WHERE action='tracabilite_480_export'")]
        self.assertEqual({a['format'] for a in audited}, {'json', 'csv'})
        with self.assertRaises(Stop):
            trace480.export(self.desk, 'DOS-001', 'xml')

    def test_retention_purge(self):
        self.seed()
        self.desk.db.execute("UPDATE trace_calls480 SET at='2020-01-01T00:00:00+00:00' WHERE id=1")
        self.desk.db.commit()
        result = trace480.purge(self.desk, 30)
        self.assertEqual(result['deleted_calls'], 1)
        self.assertEqual(trace480.matter_view(self.desk, 'DOS-001')['summary']['calls'], 3)

    def test_overview_lists_models_and_destinations(self):
        self.seed()
        over = trace480.overview(self.desk, 30)
        self.assertEqual((over['total_calls'], over['external_calls']), (4, 1))

    def test_worker_opens_the_job_context_with_the_matter(self):
        seen = {}

        def capture(self_, kind, args):
            seen.update(trace480.CTX.get() or {})
            return {'ok': True}
        kind = 'index'
        job = self.desk.enqueue(kind, {'matter': 'DOS-001'})
        with patch.object(Desk, 'perform', capture):
            self.desk.work_once()
        self.assertEqual((seen.get('matter'), seen.get('job_kind'), seen.get('job_id')), ('DOS-001', kind, str(job)))
        self.assertIsNone(trace480.CTX.get())


# --------------------------------------------------------------------------- interface et API
class Web(unittest.TestCase):
    PATH = Base0.PATH
    call = Base0.call
    _base_setup = Base0.setUp

    def setUp(self):
        self._base_setup()
        private_json(Path(self.f.c['matters_file']), [{'id': 'DEMO', 'client_name': 'Client DEMO', 'path': '/Dossiers/DEMO', 'references': ['DEMO'],
                                                       'aliases': ['Client DEMO'], 'correspondents': [{'email': 'client@example.test', 'role': 'client'}]}])

    def post(self, name, body, **kw):
        r = self.call('/api440/' + name, 'POST', body, **kw)
        return r, (json.loads(r['body']) if r['body'][:1] == b'{' else {})

    def get(self, name, query=''):
        r = self.call('/api440/' + name, query=query)
        return r, (json.loads(r['body']) if r['body'][:1] == b'{' else {})

    def test_pages_render_require_auth_and_are_in_the_navigation(self):
        for path in ('/progres', '/autonomie', '/tracabilite'):
            self.assertIn('401', self.call(path, auth=False)['status'], path)
            r = self.call(path)
            self.assertTrue(r['status'].startswith('200'), path)
        self.assertIn('/agent-courriel/progres', self.call('/courriels')['body'].decode())
        self.assertIn('Aucun envoi rapproché', self.call('/progres')['body'].decode())
        self.assertIn('Toujours soumis à votre validation', self.call('/autonomie')['body'].decode())
        for name in ('v480.js', 'v480.css'):
            self.assertTrue(self.call('/static/' + name)['status'].startswith('200'))

    def test_writes_need_origin_csrf_and_json(self):
        body = {'task': 'agenda_echeance', 'level': 'propose'}
        self.assertIn('400', self.post('autonomy/set', body, origin=False)[0]['status'])
        self.assertIn('400', self.post('autonomy/set', body, csrf=False)[0]['status'])
        self.assertIn('400', self.call('/api440/autonomy/set', 'POST', body, ctype='text/plain')['status'])
        self.assertTrue(self.post('autonomy/set', body)[0]['status'].startswith('200'))
        self.assertIn('401', self.call('/api440/autonomy/status', auth=False)['status'])

    def test_acting_level_needs_the_confirmation_checkbox(self):
        self.post('autonomy/set', {'task': 'agenda_echeance', 'level': 'propose'})
        r, out = self.post('autonomy/set', {'task': 'agenda_echeance', 'level': 'agir'})
        self.assertIn('400', r['status'])
        self.assertEqual(out['error'], 'confirmation_autonomie_requise')
        self.assertIn('cocher', out['message'])
        r, out = self.post('autonomy/set', {'task': 'agenda_echeance', 'level': 'agir', 'confirm': 'yes'})
        self.assertEqual(out['level'], 'agir')
        r, out = self.post('autonomy/set', {'task': 'envoi_courriel', 'level': 'agir', 'confirm': 'yes'})
        self.assertEqual(out['error'], 'action_toujours_soumise_a_validation')
        self.assertEqual(self.get('autonomy/status')[1]['tasks'][2]['level'], 'agir')

    def test_rule_flow_from_the_editor_propose_confirm_suspend_delete(self):
        r, out = self.post('rules/propose', {'original': DRAFT, 'corrected': LIGHT, 'recipients': ['client@example.test']})
        cand = [c for c in out['candidates'] if c['rule_type'] == 'formule_fermeture'][0]
        self.assertEqual(self.get('autonomy/status')[0]['status'][:3], '200')
        self.assertEqual(rules480.listing(Desk(self.f.c)), [])                      # proposer ne crée rien
        opt = [o for o in cand['options'] if o['scope'] == 'dossier'][0]
        body = {'rule_type': cand['rule_type'], 'value': cand['value'], 'scope': opt['scope'], 'scope_value': opt['scope_value'], 'origin': 'correction'}
        r, out = self.post('rules/create', body)
        self.assertEqual(out['error'], 'confirmation_regle_requise')
        r, out = self.post('rules/create', {**body, 'confirm': 'yes', 'evidence': 'x'})
        self.assertTrue(out['created'])
        self.assertIn('Dans le dossier', out['sentence'])
        rid = out['id']
        self.assertIn(out['sentence'], self.call('/progres')['body'].decode().replace('&#x27;', "'").replace('&quot;', '"') or out['sentence'])
        self.assertEqual(self.post('rules/status', {'id': rid, 'status': 'suspendue'})[1]['status'], 'suspendue')
        self.assertTrue(self.post('rules/delete', {'id': rid})[1]['deleted'])
        self.assertEqual(self.post('rules/delete', {'id': rid})[1]['error'], 'regle_absente')

    def test_stored_text_is_escaped_in_pages(self):
        evil = '<script>alert(1)</script> toujours citer la pièce'
        self.post('rules/create', {'rule_type': 'consigne', 'value': evil, 'scope': 'dossier', 'scope_value': 'DEMO', 'confirm': 'yes'})
        page = self.call('/progres')['body'].decode()
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertIn('&lt;script&gt;', page)
        trace480.event(Desk(self.f.c), 'DEMO', 'produit', '<img src=x onerror=1>', '<b>pourquoi</b>')
        out = self.get('trace/view', 'matter=DEMO')[1]
        self.assertNotIn('<img', out['html'])
        self.assertNotIn('<b>', out['html'])

    def test_tone_api_detects_overrides_and_validates(self):
        r, out = self.post('tone/detect', {'recipients': 'client@example.test'})
        self.assertEqual((out['profile'], out['matter'], out['label']), ('client', 'DEMO', 'Client'))
        self.post('tone/override', {'kind': 'adresse', 'value': 'client@example.test', 'role': 'confrere'})
        self.assertEqual(self.post('tone/detect', {'recipients': ['client@example.test']})[1]['profile'], 'confrere')
        self.assertEqual(self.post('tone/override', {'kind': 'adresse', 'value': 'zzz', 'role': 'confrere'})[1]['error'], 'adresse_invalide')
        self.assertEqual(self.post('tone/save', {'role': 'greffe', 'max_words': 9})[1]['error'], 'profil_de_ton_invalide')
        self.assertEqual(self.post('tone/save', {'role': 'greffe', 'max_words': 90})[1]['profile']['max_words'], 90)

    def test_trace_api_view_and_export_are_authenticated_and_validated(self):
        desk = Desk(self.f.c)
        trace480.begin('9', 'run', 'DEMO')
        self.addCleanup(trace480.end)
        make_model({'state_dir': self.f.c['state_dir'], 'model': 'm', 'purpose': 'mail_drafting'}).ask('compose', Trace.payload(Trace))
        self.assertIn('401', self.call('/api440/trace/view', auth=False, query='matter=DEMO')['status'])
        out = self.get('trace/view', 'matter=DEMO')[1]
        self.assertEqual(out['view']['summary']['calls'], 1)
        self.assertIn('1 appel(s) de modèle', out['html'])
        exp = self.get('trace/export', 'matter=DEMO&format=csv')[1]
        self.assertEqual(exp['filename'], 'tracabilite-DEMO.csv')
        self.assertEqual(self.get('trace/export', 'matter=DEMO&format=pdf')[1]['error'], 'format_export_invalide')
        self.assertEqual(self.get('trace/view', 'matter=DEMO&since=hier')[1]['error'], 'periode_invalide')
        self.assertEqual(self.get('trace/view', 'matter=')[1]['error'], 'dossier_invalide')
        self.assertEqual(self.get('learning/dashboard', 'kind=zz')[1]['error'], 'filtre_invalide')
        self.assertEqual(self.get('learning/dashboard')[1]['dashboard']['total']['total'], 0)

    def test_editor_and_pages_ship_the_new_controls(self):
        js = self.call('/static/v480.js')['body'].decode()
        for needle in ('Toujours faire comme ça', 'rules/propose', 'rules/create', "confirm: 'yes'", 'MutationObserver'):
            self.assertIn(needle, js)
        self.assertNotIn('innerHTML = c.', js)
        self.assertNotIn('alert(', js)


if __name__ == '__main__':
    unittest.main()
