"""5.6.13 : intention explicite (question / analyse / document / courriel), rédaction libre convergente (modèle du cabinet, contrôle
juridique, trois états), état partagé texte–voix, banc par fonction lié à l'empreinte, cache des documents longs borné aux paramètres
utilisés, centre « À décider » complet, manifeste des sources, comparaison des versions, règle proposée, fiche d'audience, recette réelle."""
from email import policy
import io
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch
import zipfile

from agent import decisions569, docrequest520 as dr, economie569 as eco, missions567 as missions, pilote5613 as pilot
from agent.common import Stop
import test_v530 as t530
import test_v5612 as t5612

ROOT = Path(__file__).resolve().parents[1]
ALPHA = '20240101001'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def template_docx():
    para = lambda t: '<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % t
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="%s" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<w:body>%s<w:sectPr><w:headerReference w:type="default" r:id="rId2"/></w:sectPr></w:body></w:document>') % (
        W, ''.join(para(x) for x in ('{{destinataire}}', 'Objet : {{objet}}', '{{date}}', '{{envoi}}', '{{appel}}', '{{corps}}', '{{fin}}')))
    header = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr xmlns:w="%s"><w:p><w:r><w:t>CABINET TEST — papier à en-tête</w:t></w:r></w:p></w:hdr>' % W
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/></Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/_rels/document.xml.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/></Relationships>')
        z.writestr('word/document.xml', document)
        z.writestr('word/header1.xml', header)
    return out.getvalue()


def install_template(desk, label='Courrier du cabinet'):
    from agent import cabinet_docs33 as cd
    cd.ensure_schema(desk)
    raw = template_docx()
    tid = 'ab' * 16
    path = cd._root(desk) / 'templates' / (tid + '.docx')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)      # (os.fchmod absent sous Windows : écriture directe, le contenu seul compte ici)
    desk.db.execute('INSERT OR REPLACE INTO cabinet_templates_v330 VALUES(?,?,?,?,?,?,?,?,?)',
                    (tid, label, 1, cd._sha(raw), 'approved', '[]', '{}', desk.now(), desk.now()))
    desk.db.commit()
    return tid


def docx_text(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        return z.namelist(), z.read('word/document.xml').decode('utf-8')


class FakeControl:
    def __init__(self): self.calls = 0
    def ask(self, stage, payload):
        self.calls += 1
        return {'recommendation': 'approve', 'agree': True, 'unsupported_claims': [], 'omissions': [], 'risks': []}


class FakeMailbox:
    def __init__(self): self.msgs = []
    def append_draft(self, msg): self.msgs.append(msg)
    def search(self, folder, *crit): return [str(i + 1) for i in range(len(self.msgs))]
    def fetch(self, folder, uid, headers_only=False):
        m = self.msgs[int(uid) - 1]
        return types.SimpleNamespace(mid=m['Message-ID'], subject=m['Subject'], uid=uid, text=m.get_content(), flags=('\\Draft',), msg=m, uidvalidity='1')
    def verify_draft(self, expected): return {'uid': '1', 'folder': 'Drafts', 'verified_at': 'now'}
    def close(self): pass


class MailModel:
    def complete(self, messages, temperature=0, max_tokens=0, json_schema=None):
        return json.dumps({'objet': 'Disponibilités pour l’audience', 'corps': 'Cher Confrère, pourriez-vous m’indiquer vos disponibilités ?',
                           'a_completer': ['date proposée'], 'sources': []})


class Base(t530.Base):
    def setUp(self):
        super().setUp()
        self.control = FakeControl()
        self.desk.c.setdefault('model_routing', {})['control'] = {'provider': 'ollama', 'model': 'test-control'}
        p = patch('agent.pilote5613._control_model', return_value=self.control);p.start();self.addCleanup(p.stop)

    def mission(self, instruction, key='k', **kw):
        return missions.create(self.desk, {'instruction': instruction, 'request_key': 'req5613-' + key.ljust(20, 'x'), **kw})


class Intent(Base):
    def test_questions_and_analyses_never_create_a_document(self):
        self.assertEqual(pilot.classify_intent('Quels sont les risques du contrat ?')['intent'], 'question')
        self.assertEqual(pilot.classify_intent('Analyse les conclusions adverses sans rédiger de document')['intent'], 'analysis')
        self.assertEqual(pilot.classify_intent('Prépare un courriel au confrère pour demander ses disponibilités')['intent'], 'mail')
        self.assertEqual(pilot.classify_intent('Prépare une note de synthèse sur la recevabilité')['intent'], 'document')
        self.assertEqual(pilot.classify_intent('Prépare une trame de plaidoirie à partir des dernières conclusions')['intent'], 'document')
        self.assertEqual(pilot.classify_intent('Analyse le document que je viens de joindre')['intent'], 'analysis')
        m = self.mission('Quels sont les risques du contrat ?', 'q', matter=ALPHA)
        self.assertEqual((m['kind'], m['plan']['deliverable']), ('question', 'Réponse dans le fil'))
        self.assertFalse(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='docrequest520'").fetchone()[0])
        m = self.mission('Analyse les conclusions adverses sans rédiger de document', 'a', matter=ALPHA)
        self.assertEqual((m['kind'], m['plan']['intent']), ('question', 'analysis'))
        m = self.mission('Quels sont les risques du contrat ?', 'e', matter=ALPHA, intent='document')     # choix explicite respecté
        self.assertEqual((m['kind'], m['plan']['intent_explicit']), ('document', True))
        with self.assertRaisesRegex(Stop, 'intention_invalide'):
            self.mission('Bonjour', 'i', intent='autre')

    def test_preview_announces_the_deliverable_before_starting(self):
        p = pilot.preview(self.desk, {'instruction': 'Prépare un courrier au confrère dans le dossier ALPHA', 'attachments': [], 'context': {'selected_documents': ['/Dossiers/DEMO/x.pdf']}})
        self.assertEqual((p['intent'], p['label'], p['matter']), ('document', 'Word dans ce dossier', ALPHA))
        self.assertEqual(p['sources']['selected_documents'], 1);self.assertIn('Word dans ce dossier', p['summary'])
        p = pilot.preview(self.desk, {'instruction': 'Quels délais pour faire appel ?'})
        self.assertEqual((p['intent'], p['needs_matter']), ('question', False));self.assertEqual([o['intent'] for o in p['options']], ['question', 'analysis', 'document', 'mail', 'mission', 'facturation'])
        r = self.request('/api440/m567/mission/intent', 'POST', {}, origin=self.origin)
        self.assertTrue(r['status'].startswith('4'))     # JSON et CSRF exigés comme pour les autres routes

    def test_mail_without_selected_message_becomes_a_draft_not_a_word(self):
        m = self.mission('Prépare un courriel au confrère pour demander ses disponibilités', 'm', matter=ALPHA)
        self.assertEqual((m['kind'], m['plan']['deliverable'], m['plan']['new_mail']), ('mail', 'Brouillon dans Drafts', True))
        job = self.desk.db.execute("SELECT id,kind,args FROM jobs WHERE kind='maildraft5613'").fetchone()
        self.assertTrue(job);self.assertFalse(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='docrequest520'").fetchone()[0])
        box = FakeMailbox()
        from agent import maildraft5613
        with patch('agent.maildraft5613._mail_model', return_value=MailModel()), patch('agent.maildraft5613._mailbox', return_value=box):
            out = maildraft5613.perform(self.desk, json.loads(job['args']))
            again = maildraft5613.perform(self.desk, json.loads(job['args']))
        self.assertEqual(out['brouillon_imap'], 'verifie');self.assertEqual(len(box.msgs), 1);self.assertIn('aucun second brouillon', again['message'])
        self.assertEqual(box.msgs[0]['Subject'], 'Disponibilités pour l’audience');self.assertNotIn('To', box.msgs[0])    # destinataire jamais deviné
        self.desk.db.execute("UPDATE jobs SET status='done',result=? WHERE id=?", (json.dumps(out), job['id']));self.desk.db.commit()
        got = missions.get(self.desk, m['id'], prefix='/p')
        self.assertEqual(got['state'], 'verified');self.assertIn('Brouillons IMAP', got['result']['location']);self.assertEqual(got['label'], 'Dépôt vérifié')


class FreeDrafting(Base):
    def test_free_requests_use_the_cabinet_template_and_are_legally_controlled(self):
        install_template(self.desk)
        rid = dr.submit(self.desk, 'Prépare un courrier au confrère pour solliciter un renvoi', ALPHA, 'courrier')['request']
        dr.perform(self.desk, 'docrequest520', {'request': rid})
        row = self.desk.db.execute('SELECT * FROM docreq520 WHERE id=?', (rid,)).fetchone()
        self.assertEqual(row['status'], 'cree')
        res = json.loads(row['result'])
        self.assertEqual(res['template']['label'], 'Courrier du cabinet')
        names, xml = docx_text(self.docs.files[row['path']])
        self.assertIn('word/header1.xml', names);self.assertIn('Cher Confrère,', xml);self.assertNotIn('{{', xml);self.assertIn('Objet : ', xml)
        self.assertTrue(res['control']['done']);self.assertEqual(self.control.calls, 1);self.assertIn('second modèle', res['control']['summary'])
        self.assertIn('non_lus', res['manifest']);self.assertGreater(res['manifest']['fichiers_total'], 0)
        self.assertIsNone(res['revision_diff']);self.assertIsNone(res['rule_proposal'])

    def test_three_distinct_states_and_lawyer_validation(self):
        m = self.mission('Prépare un courrier au confrère pour solliciter un renvoi', 'd', matter=ALPHA)
        self.run_jobs('docrequest520')
        got = missions.get(self.desk, m['id'], prefix='/p')
        checks = got['result']['checks']
        self.assertEqual(got['label'], 'Dépôt vérifié')
        self.assertEqual((checks['deposit'], checks['legal_control'], checks['lawyer_validation']), (True, True, False))
        self.assertEqual(checks['lawyer_validation_label'], 'Validation de l’avocat en attente');self.assertIn('Word générique', got['result']['template'])
        after = missions.control(self.desk, {'id': m['id'], 'action': 'validate'})
        self.assertTrue(after['result']['checks']['lawyer_validation']);self.assertEqual(after['result']['checks']['lawyer_validation_label'], 'Validé par l’avocat')
        html = pilot.checks_html(after['result']['checks'])
        self.assertIn('✓ Dépôt vérifié', html);self.assertIn('✓ Contrôle juridique réussi', html);self.assertIn('✓ Validé par l’avocat', html)

    def test_control_is_explicit_when_the_second_model_is_not_independent(self):
        self.desk.c['model_routing'].pop('control')
        out = pilot.control_document(self.desk, 'courrier', 'Cher Confrère, texte.', ALPHA)
        self.assertFalse(out['done']);self.assertEqual(out['review']['status'], 'not_independent');self.assertIn('identique', out['summary'])
        self.desk.setting('automation:second_model_control_enabled', False)
        out = pilot.control_document(self.desk, 'courrier', 'Cher Confrère, texte.', ALPHA)
        self.assertEqual(out['skipped'], 'controle_second_modele_desactive');self.assertIn('désactivé', out['summary'])

    def test_revision_keeps_the_original_document_and_compares_versions(self):
        install_template(self.desk)
        rid = dr.submit(self.desk, 'Prépare un courrier au confrère pour solliciter un renvoi', ALPHA, 'courrier')['request']
        dr.perform(self.desk, 'docrequest520', {'request': rid})
        rid2 = dr.submit(self.desk, 'Ajoute une formule de politesse plus ferme et mentionne la somme de 1 200 euros', '', 'auto', rid)['request']
        dr.perform(self.desk, 'docrequest520', {'request': rid2})
        row = self.desk.db.execute('SELECT * FROM docreq520 WHERE id=?', (rid2,)).fetchone()
        res = json.loads(row['result'])
        names, xml = docx_text(self.docs.files[row['path']])
        self.assertIn('word/header1.xml', names);self.assertIn('(révisé)', xml);self.assertNotIn('{{', xml)
        self.assertEqual(res['template']['id'], 'previous')
        self.assertIn('ajouté(s)', res['revision_diff']['summary']);self.assertIn('added_count', res['revision_diff'])
        self.assertEqual(res['rule_proposal']['state'], 'proposed');self.assertEqual(res['rule_proposal']['rule_type'], 'style')
        self.assertTrue(res['rule_proposal']['instruction'].startswith('Pour les courrier / lettre : '))
        decided = pilot.decide_rule(self.desk, res['rule_proposal']['id'], 'adopt')
        self.assertEqual(decided['state'], 'adopted');self.assertTrue(decided['rule_id'])
        rule = self.desk.db.execute('SELECT scope,purpose,status,origin FROM business_rules_v410 WHERE id=?', (decided['rule_id'],)).fetchone()
        self.assertEqual(tuple(rule), ('cabinet', 'document_drafting', 'active', 'correction'))
        with self.assertRaisesRegex(Stop, 'proposition_regle_deja_traitee'):
            pilot.decide_rule(self.desk, res['rule_proposal']['id'], 'ignore')

    def test_revision_diff_flags_amounts_dates_demands_and_dispositif(self):
        before = 'Il est demandé 1 000 euros.\nAudience du 12/03/2027.\nPAR CES MOTIFS\nCondamner X à payer 1 000 euros.'
        after = 'Il est demandé 1 500 euros.\nAudience du 12/03/2027.\nPAR CES MOTIFS\nCondamner X à payer 1 500 euros.\nDébouter Y.'
        d = pilot.revision_diff(before, after)
        self.assertEqual(sorted(d['alerts']), ['demande(s) modifiée(s)', 'dispositif modifié', 'montant(s) modifié(s)'])
        self.assertEqual(d['amounts']['added'], ['1500.00']);self.assertEqual(d['dates']['added'], [])
        same = pilot.revision_diff(before, before)
        self.assertEqual(same['alerts'], []);self.assertIn('ni montant', same['summary'])


class Decisions(Base):
    def test_blocked_manual_missions_and_requests_are_decidable(self):
        m = self.mission('Prépare une note de synthèse sur le bail', 'n')      # document sans dossier : décision attendue
        self.assertEqual(m['state'], 'decision')
        rid = dr.submit(self.desk, 'Prépare une note sur le bail commercial', '', 'note')['request']
        dr.perform(self.desk, 'docrequest520', {'request': rid})
        self.assertEqual(self.desk.db.execute('SELECT status FROM docreq520 WHERE id=?', (rid,)).fetchone()[0], 'dossier_a_choisir')
        pilot.propose_rule(self.desk, 'courrier', 'Termine toujours par une formule confraternelle', ALPHA, 'r1')
        items = decisions569.items(self.desk, prefix='/p')
        kinds = {x['kind']: x for x in items}
        self.assertIn('mission', kinds);self.assertIn('docreq', kinds);self.assertIn('rule', kinds)
        self.assertEqual(kinds['mission']['id'], m['id']);self.assertTrue(kinds['mission']['candidates']);self.assertEqual(kinds['docreq']['id'], rid)
        html = decisions569.html(self.desk, prefix='/p')
        for text in ('data-act="mission-resolve"', 'data-act="docreq-resolve"', 'data-act="rule-adopt"', 'data-act="rule-ignore"', 'Mission : Prépare une note'):
            self.assertIn(text, html)
        out = dr.resolve(self.desk, rid, ALPHA)
        self.assertTrue(out['job_id']);self.assertEqual(self.desk.db.execute('SELECT status,matter FROM docreq520 WHERE id=?', (rid,)).fetchone()[1], ALPHA)
        self.run_jobs('docrequest520')
        self.assertEqual(self.desk.db.execute('SELECT status FROM docreq520 WHERE id=?', (rid,)).fetchone()[0], 'cree')
        self.assertNotIn('docreq', {x['kind'] for x in decisions569.items(self.desk, prefix='/p')})
        resolved = missions.control(self.desk, {'id': m['id'], 'action': 'resolve', 'matter': ALPHA})
        self.assertEqual(resolved['state'], 'queued')
        js = (ROOT / 'agent' / 'static' / 'v569.js').read_text(encoding='utf-8')
        self.assertIn("'mission-resolve'", js);self.assertIn("call('docreq/control'", js);self.assertIn("'rule/decide'", js);self.assertEqual(js.count('location.reload'), 1)

    def test_api_routes_for_decisions(self):
        rid = dr.submit(self.desk, 'Prépare une note sur le bail commercial', '', 'note')['request']
        dr.perform(self.desk, 'docrequest520', {'request': rid})
        r = self.request('/api440/m568/docreq/control', 'POST', {'id': rid, 'matter': ALPHA}, origin=self.origin)
        self.assertTrue(r['status'].startswith('4'))     # formulaire sans JSON/CSRF refusé comme ailleurs


class Cache(Base):
    def test_bench_results_and_other_ai_settings_do_not_invalidate_analyses(self):
        from agent.long_documents365 import analyze_pages
        pages = [{'page': n, 'label': 'p. ' + str(n), 'text': ('Page ' + str(n) + ' montant date.' + chr(10)) * 40, 'extraction': 'text', 'citation_kind': 'page'} for n in range(1, 13)]
        first = t5612.MergeModel()
        _, c1 = analyze_pages(self.desk, pages, 'src-2', '/Conclusions.pdf', 'Analyser', first, 60000)
        self.assertEqual((c1['reuse']['reused'], c1['reuse']['reason']), (False, 'nouveau_document'))
        self.desk.setting('ai:economy_bench:autre-modele', {'passed': True, 'at': 'x'})
        self.desk.setting('ai:economy_daily_jobs', 7)
        self.desk.setting('learning:x', 1);self.desk.setting('assistant567:profile:cabinet', {'tone': 'direct'})
        second = t5612.MergeModel()
        _, c2 = analyze_pages(self.desk, pages, 'src-2', '/Conclusions.pdf', 'Analyser', second, 60000)
        self.assertEqual(second.chunk_calls + second.merge_calls, 0)
        self.assertEqual((c2['reuse']['reused'], c2['reuse']['message']), (True, 'Analyse réutilisée (fragments et fusions en cache).'))
        third = t5612.MergeModel()
        _, c3 = analyze_pages(self.desk, pages, 'src-2', '/Conclusions.pdf', 'Autre question', third, 60000)
        self.assertGreater(third.chunk_calls, 0);self.assertEqual(c3['reuse']['reason'], 'question_differente')
        self.desk.c['documents'] = {**self.desk.c.get('documents', {}), 'long_chunk_chars': 2600}      # réglage utilisé par l'analyse
        fourth = t5612.MergeModel()
        _, c4 = analyze_pages(self.desk, pages, 'src-2', '/Conclusions.pdf', 'Autre question', fourth, 60000)
        self.assertGreater(fourth.chunk_calls, 0);self.assertEqual(c4['reuse']['reason'], 'modele_ou_reglages_modifies')


class BenchByFunction(Base):
    def test_model_admitted_to_triage_only_never_receives_control_or_reading(self):
        class Http:
            def __init__(self, *a, **k): pass
            def json(self, method, path): return {'models': [{'name': 'petit:1b', 'size': 1_000_000_000, 'digest': 'sha256:aaa'}]}
        with patch('agent.economie569.HTTP', Http):
            report = eco.bench_model(self.desk, 'petit:1b', model=t5612.BenchModel(fail=('lecture', 'controle')))
            self.assertEqual(report['functions'], {'tri': True, 'lecture': False, 'controle': False});self.assertEqual(report['digest'], 'sha256:aaa')
            out = eco.apply_model_profile(self.desk)
            self.assertEqual(out['purposes'], ['mail_triage', 'voice_conversation']);self.assertNotIn('control', out['purposes']);self.assertNotIn('attachment_review', out['purposes'])
        for test in ('tri_procedure', 'dossier_ambigu', 'piece_incomplete', 'montants', 'injection', 'abstention', 'injection_controle'):
            self.assertIn(test, [t for t, f, p in eco.BENCH])

    def test_bench_is_bound_to_the_model_fingerprint(self):
        class Http:
            digest = 'sha256:aaa'
            def __init__(self, *a, **k): pass
            def json(self, method, path): return {'models': [{'name': 'petit:1b', 'size': 1_000_000_000, 'digest': Http.digest}]}
        with patch('agent.economie569.HTTP', Http):
            eco.bench_model(self.desk, 'petit:1b', model=t5612.BenchModel())
            self.assertFalse(eco.bench_status(self.desk, 'petit:1b')['stale'])
            Http.digest = 'sha256:bbb'         # mêmes nom, poids différents
            status = eco.bench_status(self.desk, 'petit:1b')
            self.assertTrue(status['stale']);self.assertEqual(status['stale_reason'], 'empreinte_modele_modifiee')
            with self.assertRaisesRegex(Stop, 'profil_modeles_banc_perime'):
                eco.apply_model_profile(self.desk)
        self.desk.setting('ai:economy_bench:vieux', {'model': 'vieux', 'passed': True, 'results': []})      # banc d'une version antérieure
        self.assertEqual(eco.bench_status(self.desk, 'vieux')['stale_reason'], 'banc_ancienne_version')


class SharedPanel(Base):
    def test_one_pilot_component_on_today_and_in_the_panel(self):
        page = self.request('/aujourdhui')['body']
        for text in ('data-legacy-composer', 'id="ws-ai-intent"', 'id="ws-ai-files"', 'id="ws-ai-mic"', 'id="ws-ai-mic-stop"',
                     'id="ws-ai-wide"', 'id="ws-ai-matter-search"', 'id="ws-ai-history"', 'id="ws-ai-plan"', 'Brouillon dans Drafts'):
            self.assertIn(text, page)
        js = (ROOT / 'agent' / 'static' / 'v567.js').read_text(encoding='utf-8')
        self.assertIn("function close() {stopVoice();", js);self.assertIn('window.axiorhubPilot = {', js);self.assertIn("slot.append(dock)", js)
        self.assertIn("api('mission/intent'", js);self.assertIn("api('matters')", js);self.assertIn("api('attachments?ids=", js);self.assertIn("'ws-ai-dock--wide'", js)
        v568 = (ROOT / 'agent' / 'static' / 'v568.js').read_text(encoding='utf-8')
        self.assertIn('window.axiorhubVoice={stop:', v568);self.assertIn('attachments:pilot?pilot.attachments():[]', v568)
        self.assertIn("mail_key:pilot?pilot.mailKey():''", v568);self.assertIn('setMic(true)', v568);self.assertIn('setMic(false)', v568)
        self.assertIn("window.axiorhubPilot.show(m)", v568)
        from agent.shell501 import BUNDLE
        self.assertIn('v5613.css', BUNDLE);self.assertIn('.ws-ai-dock--embedded', (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8'))

    def test_matter_search_terms_and_recent_first(self):
        self.mission('Quels délais ?', 's', matter='2024020201')
        rows = pilot.matters_listing(self.desk)
        self.assertEqual(rows[0]['id'], '2024020201');self.assertTrue(rows[0]['recent']);self.assertFalse(rows[1]['recent'])
        alpha = next(r for r in rows if r['id'] == ALPHA)
        self.assertIn('alpha', alpha['search']);self.assertIn('demo', alpha['search']);self.assertIn('20240101001', alpha['search']);self.assertEqual(alpha['label'], 'DEMO')
        body = self.request('/api440/m567/matters')['body']
        self.assertIn('"recent": true', body)

    def test_attachments_listing_is_shared(self):
        from agent.improvements36 import upload_attachment
        up = upload_attachment(self.desk, b'Texte de la piece jointe.', 'piece.txt', ALPHA)
        rows = pilot.attachments_listing(self.desk, [up['attachment_id'], 'zz' * 16], ALPHA)
        self.assertEqual((rows[0]['name'], rows[0]['readable'], rows[0]['pages']), ('piece.txt', True, 1));self.assertFalse(rows[1]['readable'])


class Pages(Base):
    def test_ai_settings_are_grouped_and_activity_is_grouped_by_matter(self):
        body = self.request('/ia-externe')['body']
        self.assertIn('<h1>Intelligence artificielle</h1>', body);self.assertIn('id="ia5613-local"', body);self.assertIn('id="economie569"', body)
        self.assertIn('Régime économe et consommation', body);self.assertIn('Modèles locaux et routage', body)
        from agent.config567 import FIELDS
        self.assertIn('Intelligence artificielle', {f[4] for f in FIELDS});self.assertNotIn('IA locale', {f[4] for f in FIELDS})
        from agent import live430
        dr.submit(self.desk, 'Prépare un courrier au confrère', ALPHA, 'courrier');self.desk.enqueue('health', {})
        html = live430.panel(self.desk, '/p', 'csrf')
        self.assertIn('class="live-group"', html);self.assertIn('Tâches techniques (1)', html)

    def test_hearing_sheet(self):
        from agent import audience5613
        self.desk.db.execute("INSERT INTO calendar_cache(id,uid,recurrence_id,calendar_url,href,etag,title,description,location,starts,ends,busy,matter,matter_candidates,fetched) "
                             "VALUES('e1','u1','','c','h','e','Audience de plaidoirie','','TJ Paris','2099-01-10T09:00:00+00:00','2099-01-10T10:00:00+00:00',1,?,'[]',?)", (ALPHA, self.desk.now()))
        self.desk.db.commit()
        s = audience5613.sheet(self.desk, ALPHA)
        self.assertEqual(s['hearings'][0]['title'], 'Audience de plaidoirie');self.assertEqual(s['conclusions'][0]['name'], 'Conclusions récapitulatives.docx')
        self.assertIn('drafts_to_review', s);self.assertIn('probable_questions', s)
        body = self.request('/audience', query='matter=' + ALPHA)['body']
        self.assertIn('Audience de plaidoirie', body);self.assertIn('Conclusions récapitulatives.docx', body);self.assertIn('Préparer la plaidoirie', body)
        with self.assertRaisesRegex(Stop, 'dossier_absent'):
            audience5613.sheet(self.desk, 'inconnu')

    def test_real_production_recette(self):
        from agent import recette5613
        report = recette5613.run(self.desk, dav=self.docs, box=FakeMailbox(), stamp='T1')
        by = {s['id']: s for s in report['steps']}
        self.assertTrue(by['word']['ok']);self.assertIsNone(by['template']['ok']);self.assertTrue(by['deposit']['ok']);self.assertTrue(by['resume']['ok']);self.assertTrue(by['draft']['ok'])
        self.assertFalse(report['ok']);self.assertTrue(report['incomplete']);self.assertIn('/Dossiers/_RECETTE_AXIORHUB/recette-T1.docx', self.docs.files);self.assertIn('/Dossiers/_RECETTE_AXIORHUB/recette-T1-reprise.docx', self.docs.files)
        install_template(self.desk)
        report = recette5613.run(self.desk, dav=self.docs, box=FakeMailbox(), stamp='T2')
        self.assertTrue(next(s for s in report['steps'] if s['id'] == 'template')['ok']);self.assertTrue(report['ok'])
        body = self.request('/mise-en-service')['body']
        self.assertIn('data-recette5613-run', body);self.assertIn('Modèle Word du cabinet', body)


if __name__ == '__main__':
    unittest.main()
