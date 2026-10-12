"""5.6.36 : version corrective et fonctionnelle (demandes de l'avocat du 12 octobre 2026). Données fictives uniquement."""
import base64
from datetime import date, datetime, timedelta, timezone
import io
import json
import re
from pathlib import Path
import sqlite3
import tempfile
import unittest
import unittest.mock
import zipfile

from agent.common import Stop
import test_v5634 as t34

ROOT = Path(__file__).resolve().parents[1]
MID = t34.MID
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def api(case, route, data=None, method=None):
    """Appel JSON authentifié de l'interface (origine et jeton CSRF de la recette)."""
    raw = json.dumps(data).encode() if data is not None else b''
    env = {'REQUEST_METHOD': method or ('POST' if data is not None else 'GET'), 'PATH_INFO': '/api440/' + route.split('?', 1)[0],
           'QUERY_STRING': route.split('?', 1)[1] if '?' in route else '', 'HTTP_HOST': 'cabinet.example.test', 'HTTP_X_FORWARDED_PROTO': 'https',
           'wsgi.url_scheme': 'http', 'wsgi.input': io.BytesIO(raw), 'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': 'application/json',
           'HTTP_ORIGIN': case.origin, 'HTTP_X_CSRF_TOKEN': 'test-csrf', 'HTTP_ACCEPT': 'application/json',
           'HTTP_AUTHORIZATION': 'Basic ' + base64.b64encode(('admin:' + case.password).encode()).decode()}
    out = {}

    def start(status, headers):
        out.update(status=status, headers=dict(headers))
    body = b''.join(case.app(env, start)).decode()
    try:
        out['json'] = json.loads(body)
    except ValueError:
        out['json'] = None
    out['body'] = body
    return out


def letter_docx(tags, header_page_field=True):
    """Modèle de courrier fictif : en-tête avec numéro de page (champ PAGE), pied de page, balises demandées."""
    paragraphs = ''.join('<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % t for t in tags)
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="%s" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>%s<w:sectPr>'
                '<w:headerReference w:type="default" r:id="rIdH"/><w:footerReference w:type="default" r:id="rIdF"/></w:sectPr></w:body></w:document>') % (W, paragraphs)
    field = ('<w:p><w:r><w:t>Page </w:t></w:r><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
             '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>') if header_page_field else '<w:p/>'
    header = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr xmlns:w="%s"><w:p><w:r><w:t>CABINET EXEMPLE</w:t></w:r></w:p>%s</w:hdr>' % (W, field)
    footer = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr xmlns:w="%s"><w:p><w:r><w:t>10 rue du Test, 69000 Lyon</w:t></w:r></w:p></w:ftr>' % W
    files = {
        '[Content_Types].xml': '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                               '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                               '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        '_rels/.rels': '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        'word/_rels/document.xml.rels': '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                        '<Relationship Id="rIdH" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>'
                                        '<Relationship Id="rIdF" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/></Relationships>',
        'word/document.xml': document, 'word/header1.xml': header, 'word/footer1.xml': footer}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


# --------------------------------------------------------------------------------------------- C1 : modèle de courrier
class LetterTemplate(unittest.TestCase):
    def test_template_without_envoi_with_header_footer_and_page_number_is_accepted(self):
        from agent import cabinet_docs33 as c
        raw = letter_docx(['{{destinataire}}', '{{qualite}}', '{{adresse}}', 'Objet : {{objet}}', 'Réf. : {{reference}}', 'Lyon, le {{date}}',
                           '{{appel}}', '{{corps}}', '{{fin}}', '{{signature}}'])
        info = c.inspect_template(raw)
        self.assertTrue(info['has_headers'] and info['has_footers'])
        self.assertNotIn('envoi', info['placeholders'])
        filled = c.fill_template(raw, c.SAMPLE)
        with zipfile.ZipFile(io.BytesIO(filled)) as z:
            text = z.read('word/document.xml').decode()
        self.assertIn('Madame Martin (exemple fictif)', text)
        self.assertNotIn('{{', text)

    def test_only_corps_is_required_and_errors_name_the_tag(self):
        from agent import cabinet_docs33 as c
        with self.assertRaises(Stop) as ctx:
            c.inspect_template(letter_docx(['{{destinataire}}']))
        self.assertEqual(str(ctx.exception), 'balise_corps_paragraphe_unique_requise')
        with self.assertRaises(Stop) as ctx:
            c.inspect_template(letter_docx(['{{corps}}', 'Ville : {{ville}}']))
        self.assertEqual(str(ctx.exception), 'balise_modele_word_inconnue')
        self.assertIn('{{ville}}', ctx.exception.detail['precision'])
        c.inspect_template(letter_docx(['{{corps}}'], header_page_field=False))   # un modèle minimal est valable

    def test_every_template_refusal_has_a_readable_message(self):
        from agent.web import REASONS
        for code in ('balise_modele_word_inconnue', 'balises_modele_word_incompletes_ou_inconnues', 'balise_corps_paragraphe_unique_requise',
                     'date_word_dynamique_refusee', 'lien_externe_modele_word_refuse', 'modele_word_actif_refuse', 'modele_word_docx_invalide'):
            self.assertTrue(REASONS.get(code), code)
            self.assertNotIn('_', REASONS[code].split(':')[0])


# --------------------------------------------------------------------------------------------- C2, C3 : surveillance
class Surveillance(t34.Base):
    def test_monitor_sweep_stops_quietly_when_the_automatic_queue_is_full(self):
        from agent import proactive
        calls = []

        def full(kind, args=None, **kw):
            calls.append(kind)
            raise Stop('file_attente_pleine')
        with unittest.mock.patch.object(self.desk, 'enqueue', side_effect=full), \
                unittest.mock.patch('agent.portfolio.portfolio_rows', return_value=[{'matter': MID}]):
            out = proactive.monitor_all(self.desk, {})
        self.assertEqual((out['dossiers_mis_en_attente'], out['suite'], out['reporte']), (0, False, 1))   # jamais d'erreur ni de relance en boucle
        self.assertEqual(calls, ['monitor_matter'])

    def test_unreadable_folders_are_named_and_never_suspend_the_document_watch(self):
        from agent import watch430

        class Broken:
            refused = []

            def inventory_step(self, path, state):
                raise Stop('http_404')
        from agent.common import indexable_matters
        ids = [m['id'] for m in indexable_matters(self.desk.c)]
        out = watch430.documents_check(self.desk, Broken())
        self.assertEqual(out['failed'], min(3, len(ids)))                               # avant 5.6.36 : Stop('dossiers_illisibles')
        gone = watch430.unreadable(self.desk)
        self.assertIn(MID, gone)
        self.assertEqual(gone[MID]['code'], 'http_404')
        for mid in ids:
            watch430.retry_folder(self.desk, mid, ignore=True)
        self.assertEqual(watch430.unreadable(self.desk), {})
        self.assertEqual(watch430.documents_check(self.desk, Broken()), {'abstention': 'Aucun dossier indexable.'})   # dossiers retirés : passés
        for mid in ids:
            watch430.retry_folder(self.desk, mid, restore=True)
        self.assertEqual(watch430.ignored(self.desk), [])

    def test_legacy_suspension_for_unreadable_folders_is_lifted(self):
        from agent import queue521, watch430
        self.desk.db.execute("INSERT INTO jobs(kind,args,status,created,finished,result,priority,attempts) VALUES('live_documents430','{}','error',?,?,?,55,3)",
                             (self.desk.now(), self.desk.now(), json.dumps({'erreur': 'dossiers_illisibles'})))
        self.desk.db.commit()
        report = queue521.report(self.desk) if hasattr(queue521, 'report') else None
        if report is not None:
            self.assertFalse(next(s for s in report['surveillance'] if s['kind'] == 'live_documents430')['suspended'])
        with unittest.mock.patch.object(watch430, 'heartbeat'):
            jobs = watch430.schedule(self.desk, stamp=10 ** 10)
        kinds = [self.desk.db.execute('SELECT kind FROM jobs WHERE id=?', (j,)).fetchone()[0] for j in jobs]
        self.assertIn('live_documents430', kinds)                                       # la surveillance reprend d'elle-même


# --------------------------------------------------------------------------------------------- C4–C8 : points à vérifier, écarter, missions
class Checks(t34.Base):
    def error_job(self, kind='monitor_all', code='file_attente_pleine'):
        cur = self.desk.db.execute("INSERT INTO jobs(kind,args,status,created,finished,result,priority) VALUES(?,'{}','error',?,?,?,65)",
                                   (kind, self.desk.now(), self.desk.now(), json.dumps({'erreur': code})))
        self.desk.db.commit()
        return cur.lastrowid

    def test_points_have_buttons_and_dismissed_errors_stop_counting(self):
        from agent import sante5636
        self.error_job()
        points = sante5636.checks(self.desk)
        errs = next(p for p in points if p['id'] == 'erreurs')
        self.assertEqual(errs['actions'][0]['api'], 'm5636/erreurs/ecarter')
        out = sante5636.dismiss_errors(self.desk)
        self.assertEqual(out['dismissed'], 1)
        self.assertFalse(any(p['id'] == 'erreurs' for p in sante5636.checks(self.desk)))
        row = self.desk.db.execute("SELECT status,result FROM jobs WHERE kind='monitor_all'").fetchone()
        self.assertEqual(row['status'], 'cancelled')
        self.assertEqual(json.loads(row['result'])['erreur'], 'file_attente_pleine')    # motif conservé dans l'historique

    def test_single_job_can_be_dismissed_and_only_when_in_error(self):
        from agent import sante5636
        jid = self.error_job()
        self.assertEqual(sante5636.dismiss_job(self.desk, jid)['status'], 'cancelled')
        with self.assertRaises(Stop):
            sante5636.dismiss_job(self.desk, jid)

    def test_today_has_a_calm_indicator_and_a_folded_panel_at_the_bottom(self):
        self.error_job()
        body = self.request('/aujourdhui')['body']
        self.assertNotIn('c530-alert', body)
        self.assertIn('s5636-indicator', body)
        self.assertIn('<details class="s5636-panel" id="s5636-checks">', body)            # replié par défaut
        self.assertLess(body.index('s5636-indicator'), body.index('id="s5636-checks"'))

    def test_diagnostic_services_table_and_relaunch_hint_are_in_their_columns(self):
        body = self.request('/diagnostic')['body']
        services = body.split('<h2>Services</h2>', 1)[1].split('<h3>Derniers signes de vie</h3>', 1)[0]
        self.assertNotIn('systemctl', services.split('<tbody>', 1)[1].split('</tbody>', 1)[0])

    def test_dismiss_route(self):
        jid = self.error_job()
        r = api(self, 'm5636/travail/ecarter', {'job': jid})
        self.assertTrue(r['status'].startswith('200'), r['body'][:300])
        self.assertEqual(r['json']['status'], 'cancelled')


class Missions(t34.Base):
    def test_mission_can_be_cancelled_then_hidden(self):
        from agent import missions567
        missions567.ensure_schema(self.desk)
        cols = [r[1] for r in self.desk.db.execute('PRAGMA table_info(missions_v567)')]
        values = {c: '' for c in cols}
        values.update(id='a1b2c3d4e5f60718293a4b5c6d7e8f90', owner='cabinet', state='suggested', instruction='Préparer un courrier fictif', kind='mail',
                      created=self.desk.now(), updated=self.desk.now(), context=json.dumps({'attachments': []}), plan=json.dumps({'steps': [], 'restrictions': [], 'deliverable': ''}),
                      exceptions='[]')
        for c in cols:
            if c in ('job_id',):
                values[c] = None
        self.desk.db.execute('INSERT INTO missions_v567(%s) VALUES(%s)' % (','.join(cols), ','.join('?' for _ in cols)), [values[c] for c in cols])
        self.desk.db.commit()
        out = missions567.control(self.desk, {'id': 'a1b2c3d4e5f60718293a4b5c6d7e8f90', 'action': 'cancel'})
        self.assertEqual(out['state'], 'cancelled')
        missions567.control(self.desk, {'id': 'a1b2c3d4e5f60718293a4b5c6d7e8f90', 'action': 'hide'})
        self.assertEqual([m['id'] for m in missions567.listing(self.desk)], [])


# --------------------------------------------------------------------------------------------- C9, C10 : base occupée
class Busy(unittest.TestCase):
    def test_busy_page_is_friendly_and_returns_to_the_previous_page_after_a_click(self):
        from agent.web import busy_page
        get = busy_page({'REQUEST_METHOD': 'GET', 'PATH_INFO': '/aujourdhui', 'SCRIPT_NAME': '/agent-courriel', 'HTTP_HOST': 'cabinet.example.test'})
        self.assertIn('url=/agent-courriel/aujourdhui', get)
        self.assertIn('Rien n', get + 'Rien n')
        post = busy_page({'REQUEST_METHOD': 'POST', 'PATH_INFO': '/action', 'HTTP_HOST': 'cabinet.example.test',
                          'HTTP_REFERER': 'https://cabinet.example.test/agent-courriel/planning?vue=agenda'})
        self.assertIn('url=/agent-courriel/planning?vue=agenda', post)
        self.assertIn('cliquez de nouveau', post)
        evil = busy_page({'REQUEST_METHOD': 'POST', 'PATH_INFO': '/action', 'HTTP_HOST': 'cabinet.example.test', 'HTTP_REFERER': 'https://evil.example/x'})
        self.assertNotIn('evil.example', evil)                                          # jamais de redirection vers un autre site

    def test_long_write_transactions_are_recorded_with_their_origin(self):
        from agent import ecritures5636
        with tempfile.TemporaryDirectory() as td:
            db = sqlite3.connect(Path(td) / 'x.sqlite3', factory=ecritures5636.TimedConnection)
            db.state_dir = td
            db.execute('CREATE TABLE t(x)')
            db.commit()
            clock = [100.0]
            with unittest.mock.patch('agent.ecritures5636.time.monotonic', side_effect=lambda: clock[0]):
                db.execute('INSERT INTO t VALUES(1)')
                clock[0] += 12
                db.commit()
            db.close()
            s = ecritures5636.summary(td)
            self.assertEqual(len(s['transactions']), 1)
            self.assertEqual(s['transactions'][0]['max'], 12.0)
            self.assertIn('test_v5636', s['transactions'][0]['where'] + 'test_v5636')

    def test_user_actions_wait_longer_than_page_displays(self):
        from agent import common
        common.set_request_mode('read')
        self.assertEqual(common.busy_timeout(), 2)
        common.set_request_mode('write')
        self.assertEqual(common.busy_timeout(), 25)
        common.set_request_mode('')
        self.assertEqual(common.busy_timeout(), 10)


class IntegrationBusy(t34.Base):
    def test_locked_database_gives_retryable_json_not_the_html_page(self):
        from agent import integration5634
        self.desk.c['integration5634'] = {'enabled': True, 'secret_file': 'x', 'instance_id': 'nc-fictif'}
        with unittest.mock.patch('agent.integration5634.verify', side_effect=sqlite3.OperationalError('database is locked')):
            status, body = integration5634.handle(self.desk, 'GET', '/integration/v1/jobs/' + 'a' * 32, {}, b'')
        self.assertEqual(status, '503 Service Unavailable')
        self.assertEqual((body['code'], body['retryable']), ('moteur_occupe', True))


# --------------------------------------------------------------------------------------------- C11 : envoi direct
class DirectSend(t34.Base):
    def test_all_matters_option_and_confirmation_choice(self):
        from agent import send490
        self.assertTrue(send490.status(self.desk)['confirm_each'])                     # confirmation par défaut
        with self.assertRaises(Stop):
            send490.set_options(self.desk, all_matters=True)                             # confirmation explicite exigée
        s = send490.set_options(self.desk, all_matters=True, confirm='yes')
        self.assertTrue(s['all_matters'])
        s = send490.set_options(self.desk, confirm_each=False)
        self.assertFalse(s['confirm_each'])

    def test_messaging_settings_show_the_send_block(self):
        body = self.request('/parametres', query='rubrique=messagerie')['body']
        self.assertIn('id="envoi5636"', body)
        self.assertIn('Autoriser l’envoi quand je clique sur « Envoyer »', body)


# --------------------------------------------------------------------------------------------- C12 : dossiers choisis en cliquant
class FolderPicker(t34.Base):
    def test_local_folders_are_listed_by_name_without_hidden_ones(self):
        from agent import nextcloud5636
        with tempfile.TemporaryDirectory() as td:
            for name in ('CABINET/Dossiers/DEMO', 'CABINET/Arrivée', 'CABINET/.cache'):
                (Path(td) / name).mkdir(parents=True)
            (Path(td) / 'CABINET' / 'note.txt').write_text('fictif', encoding='utf-8')
            self.desk.c['nextcloud'] = {'local_path': td, 'roots': ['/CABINET'], 'matter_roots': ['/CABINET/Dossiers']}
            top = nextcloud5636.folders(self.desk, '/')
            self.assertEqual([d['name'] for d in top['dirs']], ['CABINET'])
            inner = nextcloud5636.folders(self.desk, '/CABINET')
            self.assertEqual([d['path'] for d in inner['dirs']], ['/CABINET/Arrivée', '/CABINET/Dossiers'])
            self.assertEqual(inner['parent'], '/')
            with self.assertRaises(Stop):
                nextcloud5636.folders(self.desk, '/CABINET/../..')

    def test_settings_paths_have_a_picker_and_no_dictation(self):
        body = self.request('/parametres', query='rubrique=connexions')['body']
        self.assertIn('data-nc5636-browse="document_agents568.incoming_paths"', body)
        self.assertIn('data-no-dictation', body)


# --------------------------------------------------------------------------------------------- contrat 1.2 : comptes reliés personnellement
class PersonalKeys(t34.Base):
    PASSWORD = 'mot-de-passe-fictif-de-recette'

    def setUp(self):
        super().setUp()
        import hashlib as _h
        salt = 'ab' * 16
        self.server_auth = {'username': 'avocat-fictif', 'salt': salt,
                            'hash': _h.scrypt(self.PASSWORD.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()}
        self.n = 0

    def pair(self, login='avocat-fictif', password=None, ip='192.0.2.10'):
        from agent.integration5634 import handle
        env = {'axiorhub.auth': self.server_auth, 'REMOTE_ADDR': ip}
        return handle(self.desk, 'POST', '/integration/v1/pair', env,
                      json.dumps({'login': login, 'password': self.PASSWORD if password is None else password, 'label': 'alice@nextcloud'}).encode())

    def signed(self, key, method, path, body=None, query=''):
        from agent.integration5634 import handle, sign
        self.n += 1
        ctx = {'instance': 'personnel:avocat-fictif', 'user': 'alice', 'scope': [], 'op': method + ' ' + path,
               'exp': (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(), 'nonce': ('n%031d' % self.n)}
        env = {'HTTP_X_AXIORHUB_CONTEXT': sign(key['secret'], ctx), 'HTTP_X_AXIORHUB_KEY': key['key_id'], 'QUERY_STRING': query}
        return handle(self.desk, method, path, env, json.dumps(body).encode() if body is not None else b'')

    def test_linking_is_refused_until_allowed_in_axiorhub(self):
        status, out = self.pair()
        self.assertEqual((status, out['code']), ('403 Forbidden', 'application_nextcloud_non_autorisee'))

    def test_wrong_password_is_counted_and_limited(self):
        from agent import integration5636
        integration5636.set_enabled(self.desk, True)
        for _ in range(5):
            status, out = self.pair(password='faux-mot-de-passe')
            self.assertEqual(out['code'], 'identifiants_refuses')
        status, out = self.pair()                                                       # même le bon mot de passe attend la fin du délai
        self.assertEqual((status, out['code']), ('429 Too Many Requests', 'trop_de_tentatives'))
        self.assertEqual(self.pair(ip='192.0.2.11', login='autre')[1]['code'], 'identifiants_refuses')

    def test_personal_key_signs_calls_with_the_cabinet_scope_and_can_be_revoked(self):
        from agent import integration5636
        integration5636.set_enabled(self.desk, True)
        status, key = self.pair()
        self.assertEqual(status, '200 OK')
        self.assertNotIn(self.PASSWORD, json.dumps(key))
        self.assertTrue(re.fullmatch(r'[a-f0-9]{32}', key['key_id']))
        stored = self.desk.db.execute('SELECT * FROM integration_keys5636').fetchone()
        self.assertNotIn(self.PASSWORD, json.dumps(dict(stored)))                       # le mot de passe n'est conservé nulle part
        status, me = self.signed(key, 'GET', '/integration/v1/me')
        self.assertEqual((status, me['user'], me['mode']), ('200 OK', 'avocat-fictif', 'personnel'))
        status, out = self.signed(key, 'GET', '/integration/v1/matters')
        self.assertIn(MID, [m['id'] for m in out['matters']])                           # périmètre du compte AxiorHub : tout le cabinet
        integration5636.revoke(self.desk, key['key_id'])
        status, out = self.signed(key, 'GET', '/integration/v1/me')
        self.assertEqual((status, out['code']), ('401 Unauthorized', 'cle_personnelle_revoquee_ou_inconnue'))

    def test_instruction_creates_a_pilot_mission_and_returns_its_answer(self):
        from agent import integration5636
        integration5636.set_enabled(self.desk, True)
        _, key = self.pair()
        created = {}

        def fake_create(desk, data, owner='cabinet'):
            created.update(data, owner=owner)
            return {'id': 'f' * 32, 'job_id': None}

        def fake_get(desk, ident, owner='cabinet', admin=False, prefix=''):
            return {'id': ident, 'state': 'verified', 'label': 'Réponse prête', 'instruction': created['instruction'], 'exceptions': [],
                    'job': {}, 'result': {'text': 'Réponse fictive sourcée.', 'sources': [{'path': '/Dossiers/DEMO/Note.docx'}]}}
        with unittest.mock.patch('agent.missions567.create', side_effect=fake_create), unittest.mock.patch('agent.missions567.get', side_effect=fake_get):
            status, job = self.signed(key, 'POST', '/integration/v1/jobs', {'operation': 'instruction', 'question': 'Résume le dossier fictif.',
                                                                            'matter': MID, 'idempotency_key': 'cle-instruction-1'})
            self.assertEqual(status, '202 Accepted', job)
            self.assertEqual((created['owner'], created['autonomy'], created['matter']), ('avocat-fictif', 'prepare', MID))
            status, st = self.signed(key, 'GET', '/integration/v1/jobs/' + job['job_id'])
            self.assertEqual(st['state'], 'succeeded')
            status, res = self.signed(key, 'GET', '/integration/v1/results/' + job['job_id'])
        self.assertEqual(res['content']['reponse'], 'Réponse fictive sourcée.')

    def test_unpair_from_nextcloud(self):
        from agent import integration5636
        integration5636.set_enabled(self.desk, True)
        _, key = self.pair()
        self.assertEqual(self.signed(key, 'POST', '/integration/v1/unpair', {})[1], {'unpaired': True})
        self.assertEqual(self.signed(key, 'GET', '/integration/v1/me')[0], '401 Unauthorized')


# --------------------------------------------------------------------------------------------- F2 : données des pièces, PJ rangées
class _Mail:
    def __init__(self, msg):
        self.msg = msg


class _Box:
    def __init__(self, msg):
        self.msg = msg

    def fetch(self, folder, uid, headers_only=False):
        return _Mail(self.msg)

    def close(self):
        pass


def _mail_with_attachments():
    from email.message import EmailMessage
    msg = EmailMessage()
    msg['From'] = 'Confrère Exemple <confrere@avocats.example.test>'
    msg['Subject'] = 'Assignation fictive'
    msg.set_content('Cher Confrère, veuillez trouver ci-joint.')
    msg.add_attachment(b'%PDF-1.4 fictif', maintype='application', subtype='pdf', filename='Assignation.pdf')
    msg.add_attachment(b'MZ binaire', maintype='application', subtype='octet-stream', filename='outil.exe')
    return msg


class Pieces(t34.Base):
    def add_digest(self, path, digest):
        from agent import synthese5632
        synthese5632.schema(self.desk)
        self.desk.db.execute('INSERT INTO piece_digests5634 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                             (MID, path, 'a' * 64, synthese5632.METHOD, '', 'modele-essai', '', json.dumps(digest, ensure_ascii=False), 'complet', 1, 1,
                              '2026-10-12T08:00:00+00:00'))
        self.desk.db.commit()

    def test_records_search_and_export(self):
        from agent import ged5636
        self.add_digest('/Dossiers/DEMO/Assignation.pdf', t34.ok(
            nature='assignation', date_document='2026-09-30', parties=[{'nom': 'MARTIN', 'qualite': 'demandeur'}, {'nom': 'DUPONT'}],
            faits=[{'date': '2026-11-20', 'texte': 'Audience au tribunal judiciaire de Lyon', 'statut': 'documente'}],
            montants=[{'montant': '4 500', 'objet': 'acompte', 'devise': 'EUR', 'statut': 'documente'}],
            references=[{'reference': 'article 1641 du code civil'}], demandes=[{'texte': 'Résolution du contrat'}]))
        rec = ged5636.piece_records(self.desk, MID)
        self.assertEqual(len(rec), 1)
        self.assertEqual((rec[0]['type'], rec[0]['parties'], rec[0]['textes']), ('assignation', ['MARTIN', 'DUPONT'], ['article 1641 du code civil']))
        self.assertEqual(rec[0]['dates'][0]['statut'], 'documente')
        found = ged5636.search_all(self.desk, '1641')
        self.assertEqual([x['name'] for x in found], ['Assignation.pdf'])
        self.assertTrue(any('Texte cité' in h for h in found[0]['hits']))
        self.assertEqual(ged5636.search_all(self.desk, 'martin', 'contrat'), [])
        csv_text = ged5636.export_csv(self.desk, MID)['download']['text']
        self.assertIn('Assignation.pdf;assignation;2026-09-30;MARTIN | DUPONT', csv_text)
        page = self.request('/pieces-donnees', query='dossier=' + MID)['body']
        self.assertIn('Analyser les pièces du dossier', page)
        self.assertIn('documenté', page)

    def test_analyse_enqueues_digest(self):
        from agent import ged5636
        out = ged5636.analyse(self.desk, MID)
        row = self.desk.db.execute('SELECT kind, args FROM jobs WHERE id=?', (out['job'],)).fetchone()
        self.assertEqual((row['kind'], json.loads(row['args'])['matter']), ('digest_pieces5632', MID))
        with self.assertRaises(Stop):
            ged5636.analyse(self.desk, 'inconnu')

    def test_attachments_are_filed_once_without_overwrite(self):
        from agent import ged5636
        import test_v510 as t510
        key = 'b' * 64
        reports = Path(self.desk.c['state_dir']) / 'reports'
        reports.mkdir(exist_ok=True)
        (reports / (key + '.json')).write_text(json.dumps({'subject': 'Assignation fictive', 'sender': 'Confrère Exemple <confrere@avocats.example.test>',
                                                          'received_at': '2026-10-12T07:30:00+00:00', 'source_mailbox': 'INBOX', 'source_uid': '41',
                                                          'matter': MID}), encoding='utf-8')
        matter_path = next(m['path'] for m in __import__('agent.common', fromlist=['load_matters']).load_matters(self.desk.c) if m['id'] == MID)
        folder = matter_path + '/PJ courriels/2026-10-12 - confrere@avocats.example.test'
        docs = t510.FakeDocs({folder + '/Assignation.pdf': b'ancien fichier'})
        box = _Box(_mail_with_attachments())
        out = ged5636.file_attachments(self.desk, key, dav=docs, mailbox=box)
        self.assertEqual(out['filed'], [folder + '/Assignation (2).pdf'])          # le fichier existant n'est pas remplacé
        self.assertEqual(docs.files[folder + '/Assignation.pdf'], b'ancien fichier')
        self.assertIn('outil.exe', out['skipped'])                                  # type non courant : jamais rangé
        again = ged5636.file_attachments(self.desk, key, dav=docs, mailbox=box)
        self.assertEqual(again['filed'], [])                                        # même pièce, même courriel : pas de doublon

    def test_automatic_filing_is_off_by_default(self):
        from agent import ged5636
        ged5636.after_mail(self.desk, 'c' * 64, MID)
        self.assertIsNone(self.desk.db.execute("SELECT 1 FROM jobs WHERE kind='pj_courriel5636'").fetchone())
        ged5636.set_auto(self.desk, True)
        ged5636.after_mail(self.desk, 'c' * 64, MID)
        self.assertIsNotNone(self.desk.db.execute("SELECT 1 FROM jobs WHERE kind='pj_courriel5636'").fetchone())


# --------------------------------------------------------------------------------------------- F8 : contacts & CRM
class Contacts(t34.Base):
    def test_save_link_search_and_matter_block(self):
        from agent import crm5636
        c = crm5636.save(self.desk, {'kind': 'personne', 'category': 'confrere', 'first_name': 'Jeanne', 'name': 'EXEMPLE', 'email': 'jeanne@avocats.example.test',
                                     'matter': MID, 'role': 'avocat_adverse'})
        self.assertEqual(crm5636.find_or_create(self.desk, {'name': 'Autre', 'email': 'jeanne@avocats.example.test', 'category': 'confrere'})['id'], c['id'])
        self.assertEqual([x['id'] for x in crm5636.search(self.desk, 'jeanne')], [c['id']])
        self.assertEqual([x['id'] for x in crm5636.for_matter(self.desk, MID)], [c['id']])
        self.assertIn('Intervenants', crm5636.matter_block(self.desk, '/p', MID))
        with self.assertRaises(Stop):
            crm5636.save(self.desk, {'name': 'X', 'email': 'pas-une-adresse'})
        with self.assertRaises(Stop):
            crm5636.link(self.desk, c['id'], MID, 'role-inconnu')

    def test_archive_flag_from_checkbox(self):
        from agent import crm5636
        c = crm5636.save(self.desk, {'kind': 'personne', 'category': 'client', 'name': 'CLIENT FICTIF'})
        self.assertTrue(crm5636.api_post(self.desk, 'contacts/archiver', {'contact': c['id']}, 'cabinet')['archived'])
        self.assertFalse(crm5636.api_post(self.desk, 'contacts/archiver', {'contact': c['id'], 'archived': False}, 'cabinet')['archived'])

    def test_page_and_api(self):
        r = api(self, 'm5636/contacts/enregistrer', {'kind': 'personne', 'category': 'client', 'name': 'DUPONT', 'first_name': 'Paul'})
        self.assertEqual(r['status'][:3], '200', r['body'])
        listing = api(self, 'm5636/contacts?q=dupont')['json']
        self.assertEqual(len(listing['contacts']), 1)
        body = self.request('/contacts-crm')['body']
        self.assertIn('DUPONT', body)


# --------------------------------------------------------------------------------------------- F1 : initiatives (assignation → plan validé)
SUMMONS = ('ASSIGNATION DEVANT LE TRIBUNAL JUDICIAIRE DE LYON\n\nÀ la requête de Monsieur Paul MARTIN, demandeur, ayant pour avocat '
           'Maître Jeanne EXEMPLE (jeanne@avocats.example.test),\nJ’ai, commissaire de justice, donné assignation à la société DUPONT SAS, '
           'défenderesse,\nd’avoir à comparaître à l’audience du 20 novembre 2026 à 9 h 30 devant le tribunal judiciaire de Lyon.\n'
           'Objet : paiement de la somme de 4 500 euros (article 1103 du code civil).')


class _Model:
    def __init__(self, answers):
        self.answers = list(answers)

    def complete(self, messages, temperature=None, max_tokens=0, json_schema=None):
        return json.dumps(self.answers.pop(0), ensure_ascii=False) if self.answers else '{}'


class _SummonsMail:
    def __init__(self):
        from email.message import EmailMessage
        msg = EmailMessage()
        msg['From'] = 'Maître Jeanne EXEMPLE <jeanne@avocats.example.test>'
        msg['Subject'] = 'Assignation MARTIN c/ DUPONT SAS'
        msg.set_content('Mon Cher Confrère, je vous prie de trouver ci-joint l’assignation délivrée à votre cliente.')
        msg.add_attachment(SUMMONS.encode('utf-8'), maintype='text', subtype='plain', filename='Assignation.txt')
        self.msg, self.subject = msg, msg['Subject']
        self.text = 'Mon Cher Confrère, je vous prie de trouver ci-joint l’assignation.'
        self.timestamp = datetime.now(timezone.utc)


class _MailBox:
    def __init__(self, mail):
        self.mail = mail

    def fetch(self, folder, uid, headers_only=False):
        return self.mail

    def close(self):
        pass


class Initiatives(t34.Base):
    def test_hearing_date_rules(self):
        from agent import initiatives5636 as i
        self.assertEqual(i.find_hearing(SUMMONS), ('2026-11-20', '09:30'))
        self.assertEqual(i.find_hearing('L’audience est fixée au 03/02/2027 à 14h'), ('2027-02-03', '14:00'))
        self.assertEqual(i.find_hearing('Aucune date ici.'), ('', ''))
        self.assertTrue(i.looks_like_summons('Signification d’une assignation'))
        self.assertFalse(i.looks_like_summons('Invitation au séminaire'))

    def plan(self, answers):
        from agent import initiatives5636 as i
        box = _MailBox(_SummonsMail())
        with unittest.mock.patch('agent.initiatives5636._model', return_value=_Model(answers)), \
                unittest.mock.patch('agent.mailbox.Mailbox', return_value=box):
            out = i.propose_for_mail(self.desk, uid='41', folder='INBOX')
            i.analyse(self.desk, out['id'])
        return i._row(self.desk, out['id'])

    def test_analysis_keeps_only_elements_found_in_the_text(self):
        row = self.plan([{'est_assignation': True, 'juridiction': 'tribunal judiciaire de Lyon', 'date_audience': '2026-12-01', 'heure_audience': '10:00',
                          'demandeur': 'Paul MARTIN', 'defendeur': 'DUPONT SAS', 'confrere_nom': 'Jeanne EXEMPLE',
                          'confrere_email': 'jeanne@avocats.example.test', 'numero_rg': 'RG 99/12345', 'objet': 'paiement de 4 500 euros'}])
        f = row['fields']
        self.assertEqual(row['state'], 'proposee')
        self.assertEqual((f['date_audience'], f['heure_audience']), ('2026-11-20', '09:30'))   # la date lue dans l'acte prime
        self.assertEqual((f['demandeur'], f['defendeur'], f['client_nom']), ('Paul MARTIN', 'DUPONT SAS', 'DUPONT SAS'))
        self.assertEqual(f['numero_rg'], '')                                                   # absent du texte : écarté
        self.assertEqual(f['confrere_email'], 'jeanne@avocats.example.test')
        self.assertEqual(f['client_email'], '')                                                # l'expéditeur est le confrère
        self.assertTrue(f['nom_dossier'].startswith('PAUL MARTIN - DUPONT SAS - '))
        self.assertEqual([s['id'] for s in row['steps']][:4], ['dossier', 'pj', 'contacts', 'agenda'])
        page = self.request('/initiatives')['body']
        self.assertIn('Valider et exécuter les étapes cochées', page)
        self.assertIn('Courriel au client', page)

    def test_validated_plan_runs_checked_steps_only_and_drafts_nothing_sent(self):
        from agent import initiatives5636 as i
        row = self.plan([{'est_assignation': True, 'juridiction': 'tribunal judiciaire de Lyon', 'date_audience': '', 'heure_audience': '',
                          'demandeur': 'Paul MARTIN', 'defendeur': 'DUPONT SAS', 'objet': 'paiement'}])
        data = {'id': row['id'], 'matter': MID, 'client_nom': 'DUPONT SAS', 'client_email': 'contact@dupont.example.test',
                'step_contacts': True, 'step_agenda': True, 'step_courriel_client': True, 'step_note_interne': True, 'step_constitution': True}
        i.validate(self.desk, row['id'], data)
        self.assertEqual(i._row(self.desk, row['id'])['state'], 'en_cours')
        drafts = []
        with unittest.mock.patch('agent.initiatives5636._model', return_value=_Model([{'objet': 'Votre dossier', 'corps': 'Madame, Monsieur, [À COMPLÉTER]'}])), \
                unittest.mock.patch('agent.webmail5628.save', side_effect=lambda desk, d, owner='cabinet': drafts.append(d) or {'uid': '7'}):
            i.execute(self.desk, row['id'])
        done = {s['id']: s for s in i._row(self.desk, row['id'])['steps']}
        for sid in ('contacts', 'agenda', 'courriel_client', 'note_interne', 'constitution'):
            self.assertEqual(done[sid]['status'], 'fait', (sid, done[sid]['result']))
        self.assertEqual(drafts[0]['to'], 'contact@dupont.example.test')
        self.assertEqual(drafts[0]['mode'], 'nouveau')                                         # brouillon : jamais un envoi
        kinds = [r['kind'] for r in self.desk.db.execute('SELECT kind FROM docreq520 ORDER BY created')]
        self.assertEqual(sorted(kinds), ['acte_procedure', 'note'])
        for skipped in ('dossier', 'pj', 'courriel_confrere', 'lettre_juridiction'):
            self.assertEqual(done[skipped]['status'], '')                                      # non cochées : non exécutées
        from agent import crm5636
        self.assertEqual({c['role'] for c in crm5636.for_matter(self.desk, MID)}, {'client', 'partie_adverse', 'avocat_adverse'})
        ev = self.desk.db.execute('SELECT kind FROM agenda5634 WHERE matter=?', (MID,)).fetchone()
        self.assertEqual(ev['kind'], 'audience')
        self.assertEqual(i._row(self.desk, row['id'])['state'], 'executee')

    def test_steps_needing_a_matter_wait_and_nothing_runs_without_validation(self):
        from agent import initiatives5636 as i
        row = self.plan([{'est_assignation': True, 'juridiction': '', 'date_audience': '', 'heure_audience': '', 'demandeur': '', 'defendeur': '',
                          'objet': ''}])
        self.assertIsNone(self.desk.db.execute("SELECT 1 FROM jobs WHERE kind='initiative5636' AND json_extract(args,'$.action')='executer'").fetchone())
        with self.assertRaises(Stop):
            i.validate(self.desk, row['id'], {'id': row['id']})                                # aucune étape cochée
        i.validate(self.desk, row['id'], {'id': row['id'], 'step_note_interne': True})
        i.execute(self.desk, row['id'])
        step = next(s for s in i._row(self.desk, row['id'])['steps'] if s['id'] == 'note_interne')
        self.assertEqual(step['status'], 'attente')

    def test_automatic_detection_only_for_summons_and_can_be_disabled(self):
        from agent import initiatives5636 as i
        self.assertIsNone(i.observe(self.desk, 'd' * 64, {'subject': 'Invitation au séminaire'}))
        self.assertIn('id', i.observe(self.desk, 'e' * 64, {'subject': 'Signification d’une assignation'}))
        i.set_enabled(self.desk, False)
        self.assertIsNone(i.observe(self.desk, 'f' * 64, {'subject': 'Assignation'}))

    def test_dismiss_and_api(self):
        from agent import initiatives5636 as i
        row = self.plan([])
        r = api(self, 'm5636/initiatives/valider', {'id': row['id'], 'ecarter': '1'})
        self.assertEqual(r['status'][:3], '200', r['body'])
        self.assertEqual(i._row(self.desk, row['id'])['state'], 'ecartee')


# --------------------------------------------------------------------------------------------- F7 : relances d'impayés (Invoice Ninja en lecture)
class Relances(t34.Base):
    def seed(self, due, balance=1200.0):
        from agent import workstation
        workstation.ensure_schema(self.desk)
        self.desk.db.execute('INSERT OR REPLACE INTO invoice_ninja_cache_v310(id,client_id,number,status,amount,balance,due_date,invoice_url,updated,currency,'
                             'amount_exact,balance_exact) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                             ('inv1', 'cli1', 'F-2026-042', '2', balance, balance, due, 'https://factures.example.test/i/abc', '2026-10-12', 'EUR',
                              str(balance), '%.2f' % balance))
        self.desk.db.execute('INSERT OR REPLACE INTO matter_invoice_links_v310 VALUES(?,?,?)', (MID, 'cli1', '2026-10-01'))
        self.desk.db.commit()
        from agent import crm5636
        crm5636.save(self.desk, {'name': 'CLIENT FICTIF', 'category': 'client', 'email': 'compta@client.example.test', 'matter': MID, 'role': 'client'})

    def test_overdue_invoice_gets_one_draft_per_level_and_interval(self):
        from agent import relances5636 as r
        self.seed('2026-09-01')
        out = r.prepare(self.desk, today=date(2026, 10, 12), refresh=False)
        self.assertEqual(out['created'], 1)
        row = self.desk.db.execute('SELECT * FROM relances5636').fetchone()
        self.assertEqual((row['level'], row['recipient'], row['matter']), (1, 'compta@client.example.test', MID))
        self.assertIn('F-2026-042', row['subject'])
        self.assertIn('1 200,00 €', row['body'])
        self.assertEqual(r.prepare(self.desk, today=date(2026, 10, 13), refresh=False)['created'], 0)   # projet en attente : pas de doublon
        drafts = []
        with unittest.mock.patch('agent.webmail5628.save', side_effect=lambda desk, d, owner='cabinet': drafts.append(d) or {'uid': '9'}):
            r.deposit(self.desk, row['id'], {'body': 'Texte corrigé par l’avocat.'})
        self.assertEqual((drafts[0]['to'], drafts[0]['mode']), ('compta@client.example.test', 'nouveau'))
        self.assertIn('Texte corrigé', drafts[0]['html'])
        self.assertEqual(r.prepare(self.desk, today=date.today() + timedelta(days=2), refresh=False)['created'], 0)   # intervalle non écoulé
        self.assertEqual(r.prepare(self.desk, today=date.today() + timedelta(days=11), refresh=False)['created'], 1)  # niveau 2
        self.assertEqual(self.desk.db.execute('SELECT MAX(level) FROM relances5636').fetchone()[0], 2)

    def test_grace_period_exclusion_and_page(self):
        from agent import relances5636 as r
        self.seed('2026-10-11')
        self.assertEqual(r.prepare(self.desk, today=date(2026, 10, 12), refresh=False)['created'], 0)   # délai de grâce
        rid = None
        self.assertEqual(r.prepare(self.desk, today=date(2026, 10, 20), refresh=False)['created'], 1)
        rid = self.desk.db.execute('SELECT id FROM relances5636').fetchone()['id']
        self.assertIn('Déposer dans Brouillons', self.request('/relances')['body'])
        r.dismiss(self.desk, rid, exclude=True)
        self.assertEqual(r.prepare(self.desk, today=date(2026, 12, 20), refresh=False)['created'], 0)   # facture exclue
        self.assertEqual(r.pending_count(self.desk), 0)

    def test_daily_schedule_once_and_only_when_configured(self):
        from agent import relances5636 as r
        stamp = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(r.schedule(self.desk, stamp), [])                                            # Invoice Ninja non relié
        self.desk.c['invoice_ninja'] = {'enabled': True, 'base_url': 'https://factures.example.test', 'api_token_file': '/nonexistent'}
        self.assertEqual(len(r.schedule(self.desk, stamp)), 1)
        self.assertEqual(r.schedule(self.desk, stamp + 3600), [])                                     # une fois par jour
        early = datetime(2026, 10, 13, 4, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(r.schedule(self.desk, early), [])                                            # avant l'heure choisie


# --------------------------------------------------------------------------------------------- F3 : outils IA (grilles, playbooks, mémos)
CONTRACT_TEXT = ['CONTRAT DE PRESTATION', 'Article 1 — Objet : le prestataire réalise un logiciel fictif.',
                 'Article 7 — Responsabilité : la responsabilité du prestataire est illimitée.', 'Article 9 — Juridiction : tribunaux de Paris.']


class OutilsIA(t34.Base):
    def test_grid_values_are_checked_against_the_piece(self):
        from agent import outils5636 as o
        docs = [{'path': '/Dossiers/DEMO/Contrat.pdf', 'state': 'extrait', 'text': 'Le contrat est signé le 12 mars 2025. Préavis de trois mois.'},
                {'path': '/Dossiers/DEMO/Avenant.pdf', 'state': 'extrait', 'text': 'Avenant sans date.'}]
        answers = [{'valeurs': [{'critere': 0, 'valeur': '12 mars 2025', 'extrait': 'signé le 12 mars 2025'},
                                {'critere': 1, 'valeur': '3 mois', 'extrait': 'préavis de six mois'}, {'critere': 9, 'valeur': 'x', 'extrait': 'x'}]},
                   {'valeurs': [{'critere': 0, 'valeur': '', 'extrait': ''}]}]
        with unittest.mock.patch('agent.synthese5632.inventory', return_value=docs):
            g = o.grid_create(self.desk, {'matter': MID, 'name': 'Clés', 'criteria': 'Date de signature\nDurée du préavis'})
            o.grid_run(self.desk, g['id'], model=_Model(answers))
        grid, cells = o.grid_table(self.desk, g['id'])
        c = cells['/Dossiers/DEMO/Contrat.pdf']
        self.assertEqual((c[0]['value'], c[0]['statut']), ('12 mars 2025', 'documente'))
        self.assertEqual(c[1]['statut'], 'a_verifier')                                         # extrait introuvable dans la pièce
        self.assertNotIn(9, c)                                                                  # critère inconnu ignoré
        self.assertEqual(cells['/Dossiers/DEMO/Avenant.pdf'][0]['statut'], 'absent')
        self.assertIn('12 mars 2025', o.grid_csv(self.desk, g['id'])['download']['text'])
        self.assertIn('à vérifier', self.request('/outils-ia', query='onglet=grilles&grille=' + g['id'])['body'])
        with self.assertRaises(Stop):
            o.grid_create(self.desk, {'matter': MID, 'criteria': '  '})

    def test_playbook_copy_with_tracked_changes_and_justification_note(self):
        from agent import outils5636 as o
        from agent.word_legal import _simple_docx, inspect_docx
        import test_v510 as t510
        pb = o.playbook_save(self.desk, {'name': 'Prestation côté client', 'positions':
                                         'Clause : Responsabilité\nPosition : plafond égal au prix annuel\nInacceptable : responsabilité illimitée\n\n'
                                         'Clause : Juridiction\nPosition : tribunaux de Lyon'})
        self.assertEqual(pb['positions'], 2)
        raw = _simple_docx(CONTRACT_TEXT[0], CONTRACT_TEXT[1:])
        docs = t510.FakeDocs({'/Dossiers/DEMO/Contrat.docx': raw})
        catalog = [p['text'] for p in inspect_docx(raw)['paragraphs']]
        n7 = catalog.index(CONTRACT_TEXT[2])
        answer = {'ecarts': [{'paragraphe': n7, 'operation': 'replace', 'position': 0, 'justification': 'Position 1 : plafond.',
                              'texte': 'Article 7 — Responsabilité : la responsabilité du prestataire est plafonnée au prix annuel.'},
                             {'paragraphe': 999, 'operation': 'delete', 'position': 1, 'texte': '', 'justification': 'hors contrat'}]}
        run = o.playbook_start(self.desk, {'playbook': pb['id'], 'matter': MID, 'path': '/Dossiers/DEMO/Contrat.docx'})
        result = o.playbook_run(self.desk, run['id'], dav=docs, model=_Model([answer]))
        self.assertEqual((result['edits'], result['applied']), (1, 1))                          # paragraphe inexistant écarté
        self.assertEqual(docs.files['/Dossiers/DEMO/Contrat.docx'], raw)                        # original intact
        copy = next(p for p in docs.files if 'playbook Prestation' in p)
        with zipfile.ZipFile(io.BytesIO(docs.files[copy])) as z:
            xml = z.read('word/document.xml').decode()
        self.assertIn('w:ins', xml)
        self.assertIn('w:del', xml)
        self.assertIn('plafonnée au prix annuel', xml)
        note = next(p for p in docs.files if 'note playbook' in p)
        with zipfile.ZipFile(io.BytesIO(docs.files[note])) as z:
            self.assertIn('Position 1 : plafond.', z.read('word/document.xml').decode())
        with self.assertRaises(Stop):
            o.playbook_start(self.desk, {'playbook': pb['id'], 'matter': MID, 'path': '/Autre/Contrat.docx'})   # hors du dossier

    def test_memo_drafted_from_research_stays_to_review_until_validated(self):
        from agent import outils5636 as o
        found = {'verified_authorities': [{'title': 'Code civil', 'reference': 'article 1648', 'url': 'https://www.legifrance.gouv.fr/x', 'excerpt': 'deux ans'}],
                 'leads': []}
        out = o.memo_ask(self.desk, {'question': 'Délai pour agir en garantie des vices cachés ?', 'tags': 'vente'})
        with unittest.mock.patch('agent.web5634.research_job', return_value=found):
            o.memo_run(self.desk, out['id'], model=_Model([{'titre': 'Vices cachés : délai', 'texte': 'Deux ans à compter de la découverte [1].'}]))
        memo = o.memo_search(self.desk, 'vices')[0]
        self.assertEqual((memo['status'], memo['title']), ('a_relire', 'Vices cachés : délai'))
        self.assertIn('article 1648', memo['sources'][0])
        self.assertEqual(o.api_get(self.desk, 'outils/memos', {})['memos'], [])                 # non validé : pas proposé à la réutilisation
        o.memo_save(self.desk, {'id': out['id'], 'title': memo['title'], 'body': memo['body'], 'valider': True}, 'avocat-fictif', 'avocat')
        self.assertEqual(len(o.api_get(self.desk, 'outils/memos', {})['memos']), 1)
        self.assertTrue(o.memo_docx(self.desk, out['id'])['download']['base64'])

    def test_memo_without_sources_is_flagged(self):
        from agent import outils5636 as o
        out = o.memo_ask(self.desk, {'question': 'Question fictive sans source disponible ?'})
        with unittest.mock.patch('agent.web5634.research_job', side_effect=Stop('aucune_source_juridique_active')):
            o.memo_run(self.desk, out['id'], model=_Model([{'titre': 'T', 'texte': 'Réponse.'}]))
        self.assertIn('Aucune source', o.memo_search(self.desk, 'fictive')[0]['body'])


# --------------------------------------------------------------------------------------------- F4 : Corporate (saisie unique, actes, registres)
def _docx_text(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        return re.sub(r'<[^>]+>', ' ', z.read('word/document.xml').decode())


class _CorporateBase(t34.Base):
    def setUp(self):
        super().setUp()
        import test_v510 as t510
        self.docs = t510.FakeDocs({})
        from agent import corporate5636 as co
        self.co = co
        self.cid = co.company_save(self.desk, {'denomination': 'EXEMPLE SAS', 'forme': 'SAS', 'capital': '10000', 'nominal': '1',
                                               'siege': '10 rue du Test, 69002 Lyon', 'rcs': 'Lyon', 'siren': '123456789', 'matter': MID})['id']
        co.subscription(self.desk, {'company': self.cid, 'name': 'Paul MARTIN', 'quantity': '6000'})
        co.subscription(self.desk, {'company': self.cid, 'name': 'Anne DUPONT', 'quantity': '4000'})

    def run_job(self, op):
        return self.co.generate(self.desk, op, dav=self.docs)


class Corporate(_CorporateBase):
    def test_share_transfer_one_entry_four_effects(self):
        co = self.co
        out = co.operation_submit(self.desk, {'kind': 'cession', 'company': self.cid, 'cedant': 'Paul MARTIN', 'cessionnaire_nom': 'Lucie BERNARD',
                                              'cessionnaire_email': 'lucie@example.test', 'nombre': '1000', 'prix_unitaire': '2,5',
                                              'date_cession': '2026-10-12', 'agrement': True, 'categorie': 'ordinaires'})
        rows, total = co.holdings(self.desk, self.cid)                                       # table de capitalisation
        self.assertEqual({r['name']: r['quantity'] for r in rows}, {'Paul MARTIN': 5000, 'Anne DUPONT': 4000, 'Lucie BERNARD': 1000})
        self.assertEqual(total, 10000)
        moves, decisions = co.registers(self.desk, self.cid)                                 # registres
        self.assertEqual((moves[-1]['numero'], moves[-1]['kind'], moves[-1]['quantity']), (3, 'cession', 1000))
        self.assertIn('Cession de 1000 titres', decisions[-1]['title'])
        from agent import crm5636                                                            # données structurées : contact relié au dossier
        self.assertIn('Lucie BERNARD', [c['name'] for c in crm5636.for_matter(self.desk, MID)])
        result = self.run_job(out['id'])                                                     # actes générés
        names = [p.rsplit('/', 1)[-1] for p in result['files']]
        self.assertEqual(len(names), 4)
        self.assertTrue(any(n.startswith('Décision d’agrément') for n in names))
        acte = _docx_text(self.docs.files[next(p for p in result['files'] if 'Acte de cession' in p)])
        self.assertIn('1000 actions', acte)
        self.assertIn('2 500,00 €', acte)
        self.assertIn('/JURIDIQUE/', result['folder'])
        with self.assertRaises(Stop):                                                        # contrôle : titres insuffisants
            co.operation_submit(self.desk, {'kind': 'cession', 'company': self.cid, 'cedant': 'Anne DUPONT', 'cessionnaire_nom': 'X', 'nombre': '5000'})
        self.assertEqual(co.holdings(self.desk, self.cid)[1], 10000)

    def test_accounts_approval_documents_and_allocation(self):
        co = self.co
        out = co.operation_submit(self.desk, {'kind': 'agoa', 'company': self.cid, 'exercice_clos': '2025-12-31', 'date_ag': '2026-06-15',
                                              'resultat': '20000', 'dividendes': '5000', 'comptes_pdf': ''})
        result = self.run_job(out['id'])
        self.assertEqual(len(result['files']), 10)
        pv = _docx_text(self.docs.files[next(p for p in result['files'] if 'Procès-verbal' in p)])
        self.assertIn('réserve légale : 1 000,00 €', pv)                                      # 5 % du bénéfice, plafond non atteint
        self.assertIn('report à nouveau » : 14 000,00 €', pv)
        self.assertIn('15 juin 2026', pv)
        self.assertIn('L. 227-10', _docx_text(self.docs.files[next(p for p in result['files'] if 'Rapport spécial' in p)]))
        self.assertIn('Approbation des comptes', co.registers(self.desk, self.cid)[1][-1]['title'])
        with self.assertRaises(Stop):
            bad = co.operation_submit(self.desk, {'kind': 'agoa', 'company': self.cid, 'exercice_clos': '2025-12-31', 'date_ag': '2026-06-15',
                                                  'resultat': '1000', 'dividendes': '5000'})
            self.run_job(bad['id'])

    def test_management_report_from_accounts_is_checked(self):
        co = self.co
        self.docs.files['/Dossiers/DEMO/Comptes 2025.txt'] = 'Chiffre d’affaires net : 812 450 €. Résultat net : 20 000 €.'.encode()
        model = _Model([{'activite': 'Activité stable.', 'evenements': 'Néant.', 'perspectives': '[À COMPLÉTER]',
                         'chiffres': [{'libelle': 'Chiffre d’affaires', 'montant': '812 450 €', 'extrait': 'Chiffre d’affaires net : 812 450 €'},
                                      {'libelle': 'Capitaux propres', 'montant': '99 999 €', 'extrait': 'Capitaux propres : 99 999 €'}]}])
        out = co.operation_submit(self.desk, {'kind': 'agoa', 'company': self.cid, 'exercice_clos': '2025-12-31', 'date_ag': '2026-06-15', 'resultat': '20000',
                                              'comptes_pdf': '/Dossiers/DEMO/Comptes 2025.txt', 'doc_rapport_gestion': True,
                                              **{'doc_' + d: False for d in co.OPERATIONS['agoa'][1] if d != 'rapport_gestion'}})
        result = co.generate(self.desk, out['id'], dav=self.docs, model=model)
        text = _docx_text(self.docs.files[result['files'][0]])
        self.assertIn('Chiffre d’affaires : 812 450 €', text)
        self.assertIn('99 999 € [À VÉRIFIER dans les comptes]', text)                         # chiffre absent des comptes
        self.assertIn('à relire et valider', text)

    def test_officer_change_and_registered_office(self):
        co = self.co
        out = co.operation_submit(self.desk, {'kind': 'dirigeant', 'company': self.cid, 'fonction': 'Président', 'motif': 'aucun', 'entrant_prenom': 'Claire',
                                              'entrant_nom': 'Exemple', 'date_effet': '2026-11-01', 'organe': 'assemblee'})
        self.assertEqual([o['name'] for o in co.officers(self.desk, self.cid)], ['Claire EXEMPLE'])
        self.assertEqual(len(self.run_job(out['id'])['files']), 5)                           # sans lettre de démission (aucun départ)
        out = co.operation_submit(self.desk, {'kind': 'siege', 'company': self.cid, 'nouveau_siege': '5 place Fictive, 69003 Lyon', 'date_effet': '2026-11-02',
                                              'organe': 'dirigeant'})
        self.assertEqual(co.company(self.desk, self.cid)['siege'], '5 place Fictive, 69003 Lyon')
        decision = _docx_text(self.docs.files[next(p for p in self.run_job(out['id'])['files'] if 'Décision de transfert' in p)])
        self.assertIn('10 rue du Test, 69002 Lyon', decision)
        self.assertIn('DÉCISION DU PRÉSIDENT', decision)

    def test_creation_by_form_and_custom_fields_and_cabinet_template(self):
        co = self.co
        co.save_custom_fields(self.desk, {'kind': 'creation', 'labels': 'Banque dépositaire'})
        tpl = letter_docx(['STATUTS DE {{denomination}}', 'Banque : {{perso_banque_depositaire}}', 'Capital : {{capital_fr}}'])
        self.docs.files['/Modeles/Statuts.docx'] = tpl
        co.save_template(self.desk, {'doc': 'statuts', 'path': '/Modeles/Statuts.docx'})
        out = co.operation_submit(self.desk, {'kind': 'creation', 'forme': 'SARL', 'denomination': 'NOUVELLE SARL', 'objet': 'conseil', 'siege': '1 rue Neuve, 69001 Lyon',
                                              'capital': '1000', 'nominal': '10', 'associes': 'Paul MARTIN ; 600 ; 60\nAnne DUPONT ; 400 ; 40',
                                              'dirigeant_prenom': 'Paul', 'dirigeant_nom': 'Martin', 'matter': MID, 'perso_banque_depositaire': 'Banque Exemple'})
        cid = out['company']
        self.assertEqual(co.company(self.desk, cid)['forme'], 'SARL')
        self.assertEqual(co.holdings(self.desk, cid)[1], 100)
        self.assertEqual([o['fonction'] for o in co.officers(self.desk, cid)], ['Gérant'])
        result = self.run_job(out['id'])
        self.assertEqual(result['templates']['statuts'], 'cabinet')
        statuts = _docx_text(self.docs.files[next(p for p in result['files'] if p.rsplit('/', 1)[-1].startswith('Statuts'))])
        self.assertIn('Banque : Banque Exemple', statuts)
        self.assertIn('Capital : 1 000,00 €', statuts)
        with self.assertRaises(Stop):
            co.operation_submit(self.desk, {'kind': 'creation', 'forme': 'SASU', 'denomination': 'X', 'capital': '100', 'nominal': '1',
                                            'associes': 'A ; 50 ; 50\nB ; 50 ; 50', 'matter': MID})

    def test_pages_render(self):
        for q in ('', 'societe=%s' % self.cid, 'societe=%s&vue=operations' % self.cid, 'societe=%s&vue=operations&operation=agoa' % self.cid,
                  'societe=%s&vue=capital' % self.cid, 'societe=%s&vue=registres' % self.cid, 'societe=%s&vue=modeles' % self.cid, 'operation=creation'):
            r = self.request('/corporate', query=q)
            self.assertTrue(r['status'].startswith('200'), (q, r['status']))
        self.assertIn('Paul MARTIN', self.request('/corporate', query='societe=%s&vue=capital' % self.cid)['body'])


# --------------------------------------------------------------------------------------------- F6 : agenda Microsoft 365
class _FakeMS:
    """Microsoft fictif : jetons, profil, création et lecture d'événements (aucun appel réseau)."""
    def __init__(self):
        self.calls, self.created = [], []

    def request(self, method, url, data=None, headers=None, limit=0):
        self.calls.append((method, url, data, headers))
        if url.endswith('/oauth2/v2.0/token'):
            return json.dumps({'access_token': 'jeton-acces-fictif', 'refresh_token': 'jeton-renouvellement-fictif', 'expires_in': 3600}).encode()
        if '/v1.0/me?' in url:
            return json.dumps({'userPrincipalName': 'avocat@cabinet.example.test'}).encode()
        if url.endswith('/v1.0/me/events') and method == 'POST':
            body = json.loads(data)
            self.created.append(body)
            return json.dumps({'id': 'graph-%d' % len(self.created), 'iCalUId': 'ical-%d' % len(self.created)}).encode()
        if '/v1.0/me/calendarView' in url:
            start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
            return json.dumps({'value': [
                {'id': 'o1', 'iCalUId': 'outlook-1', 'subject': 'Rendez-vous client fictif', 'isAllDay': False, 'isCancelled': False,
                 'start': {'dateTime': start.strftime('%Y-%m-%dT%H:%M:%S.0000000')}, 'end': {'dateTime': (start + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S.0000000')},
                 'location': {'displayName': 'Cabinet'}},
                {'id': 'graph-1', 'iCalUId': 'ical-1', 'subject': 'Audience copiée', 'categories': ['AxiorHub'],
                 'start': {'dateTime': start.strftime('%Y-%m-%dT%H:%M:%S')}, 'end': {'dateTime': start.strftime('%Y-%m-%dT%H:%M:%S')}},
                {'id': 'o2', 'iCalUId': 'outlook-2', 'subject': 'Annulé', 'isCancelled': True,
                 'start': {'dateTime': start.strftime('%Y-%m-%dT%H:%M:%S')}, 'end': {'dateTime': start.strftime('%Y-%m-%dT%H:%M:%S')}}]}).encode()
        raise AssertionError(url)


class Microsoft365(t34.Base):
    def connect(self):
        from agent import microsoft5636 as ms
        ms.save_config(self.desk, {'client_id': '11111111-2222-3333-4444-555555555555', 'tenant': 'common', 'push': True, 'pull': True})
        env = {'HTTP_HOST': 'cabinet.example.test', 'HTTP_X_FORWARDED_PROTO': 'https'}
        url = ms.start(self.desk, env, '/agent-courriel')['url']
        self.assertIn('code_challenge_method=S256', url)
        self.assertIn('redirect_uri=https%3A%2F%2Fcabinet.example.test%2Fagent-courriel%2Fmicrosoft-retour', url)
        state = re.search(r'state=([^&]+)', url).group(1)
        fake = _FakeMS()
        out = ms.finish(self.desk, {'state': state, 'code': 'code-fictif'}, http=fake, graph=fake)
        self.assertEqual(out['account'], 'avocat@cabinet.example.test')
        token_call = next(c for c in fake.calls if c[1].endswith('/token'))
        self.assertIn(b'code_verifier=', token_call[2])
        with self.assertRaises(Stop):
            ms.finish(self.desk, {'state': state, 'code': 'code-fictif'}, http=fake, graph=fake)   # état à usage unique
        return ms, fake

    def test_connect_push_and_pull(self):
        ms, fake = self.connect()
        self.assertTrue(ms.connected(self.desk))
        from agent import agenda5634 as ag
        when = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0).isoformat()
        ev = ag.create(self.desk, {'title': 'Audience fictive', 'start': when, 'duration_minutes': 60, 'matter': MID, 'kind': 'audience'})['event']
        self.assertEqual(ms.push_event(self.desk, ev, http=fake), 'graph-1')
        self.assertIsNone(ms.push_event(self.desk, ev, http=fake))                            # une seule copie
        self.assertEqual(fake.created[0]['categories'], ['AxiorHub'])
        out = ms.pull(self.desk, http=fake)
        self.assertEqual(out['imported'], 1)                                                   # ni la copie AxiorHub ni l'annulé
        rows = self.desk.db.execute("SELECT title, source FROM agenda5634 WHERE source='microsoft'").fetchall()
        self.assertEqual([r['title'] for r in rows], ['Rendez-vous client fictif'])
        self.assertEqual(ms.pull(self.desk, http=fake)['imported'], 1)                        # fenêtre remplacée, pas de doublon
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM agenda5634 WHERE source='microsoft'").fetchone()[0], 1)
        ms.disconnect(self.desk)
        self.assertFalse(ms.connected(self.desk))

    def test_settings_block_and_validation(self):
        from agent import microsoft5636 as ms
        with self.assertRaises(Stop):
            ms.save_config(self.desk, {'client_id': 'pas-un-identifiant'})
        with self.assertRaises(Stop):
            ms.start(self.desk, {}, '/p')                                                      # non configuré
        self.assertIn('Se connecter à Microsoft 365', self.request('/parametres', query='rubrique=connexions')['body'])
        self.assertIsNone(ms.push_event(self.desk, {'id': 'x', 'title': 't', 'starts': '2026-11-01T10:00:00+00:00', 'ends': '2026-11-01T11:00:00+00:00'}))


# --------------------------------------------------------------------------------------------- F5 : complément Word
class ComplementWord(t34.Base):
    def test_manifest_points_to_this_server_over_https(self):
        from agent import office5636
        xml = office5636.manifest({'HTTP_HOST': 'cabinet.example.test', 'HTTP_X_FORWARDED_PROTO': 'https'}, '/agent-courriel')
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml.encode())
        ns = {'o': 'http://schemas.microsoft.com/office/appforoffice/1.1'}
        self.assertEqual(root.find('o:DefaultSettings/o:SourceLocation', ns).get('DefaultValue'),
                         'https://cabinet.example.test/agent-courriel/complement-word/volet')
        self.assertEqual(root.find('o:Permissions', ns).text, 'ReadWriteDocument')
        self.assertEqual(xml, office5636.manifest({'HTTP_HOST': 'cabinet.example.test', 'HTTP_X_FORWARDED_PROTO': 'https'}, '/agent-courriel'))   # identifiant stable

    def test_taskpane_headers_allow_office_frames_only_there(self):
        r = self.request('/complement-word/volet')
        self.assertTrue(r['status'].startswith('200'), r['status'])
        headers = r.get('headers') or {}
        csp = headers.get('Content-Security-Policy', '')
        self.assertIn('https://appsforoffice.microsoft.com', csp)
        self.assertIn('frame-ancestors', csp)
        self.assertNotIn('X-Frame-Options', headers)
        other = self.request('/complement-word')
        self.assertEqual((other.get('headers') or {}).get('X-Frame-Options'), 'DENY')         # les autres pages restent protégées
        self.assertIn('Télécharger le manifeste', other['body'])

    def test_manifest_download_and_rewrite_checks_numbers(self):
        r = api(self, 'm5636/office/manifeste')
        self.assertIn('<OfficeApp', r['json']['download']['text'])
        from agent import office5636
        out = office5636.rewrite(self.desk, {'text': 'Le prix est de 4 500 euros, payable le 12 mars 2025.', 'instruction': 'plus concis'},
                                 model=_Model([{'texte': 'Prix : 4 500 €, payable en mars.', 'remarque': ''}]))
        self.assertIn('12', out['note'])                                                       # chiffre perdu signalé
        with self.assertRaises(Stop):
            office5636.rewrite(self.desk, {'text': '', 'instruction': 'x'})

    def test_icon_is_public(self):
        env = {'REQUEST_METHOD': 'GET', 'PATH_INFO': '/static/axiorhub-icon-192.png', 'QUERY_STRING': '', 'HTTP_HOST': 'cabinet.example.test',
               'HTTP_X_FORWARDED_PROTO': 'https', 'wsgi.url_scheme': 'http', 'wsgi.input': io.BytesIO(b'')}
        out = {}
        body = b''.join(self.app(env, lambda s, h: out.update(status=s)))
        self.assertTrue(out['status'].startswith('200'))
        self.assertTrue(body.startswith(b'\x89PNG'))


class CorporateDiligences(_CorporateBase):
    def test_two_entry_buttons_and_company_choice(self):
        body = self.request('/corporate')['body']
        self.assertIn('📋 Approbation des comptes', body)
        self.assertIn('⚖️ Autre diligence en droit des sociétés', body)
        pick = self.request('/corporate', query='parcours=agoa')['body']
        self.assertIn('societe=%s&amp;vue=operations&amp;operation=agoa' % self.cid, pick)
        pick = self.request('/corporate', query='parcours=diligences')['body']
        self.assertIn('societe=%s&amp;parcours=diligences' % self.cid, pick)
        grid = self.request('/corporate', query='societe=%s&parcours=diligences' % self.cid)['body']
        self.assertIn('Autre diligence en droit des sociétés', grid)
        self.assertIn('Transfert du siège social', grid)
        self.assertNotIn('operation=agoa', grid)
        form = self.request('/corporate', query='societe=%s&vue=operations&operation=diligence' % self.cid)['body']
        self.assertIn('Augmentation de capital', form)
        self.assertIn('Dissolution anticipée et liquidation amiable', form)

    def test_capital_increase_updates_capital_cap_table_registers_and_deeds(self):
        co = self.co
        out = co.operation_submit(self.desk, {'kind': 'diligence', 'company': self.cid, 'nature': 'augmentation_capital', 'date_effet': '2026-11-15',
                                              'organe': 'assemblee', 'nouveau_capital': '12000', 'emissions': 'Lucie BERNARD ; 2000'})
        self.assertEqual(co.company(self.desk, self.cid)['capital'], '12000')
        rows, total = co.holdings(self.desk, self.cid)
        self.assertEqual((total, {r['name']: r['quantity'] for r in rows}['Lucie BERNARD']), (12000, 2000))
        moves, decisions = co.registers(self.desk, self.cid)
        self.assertEqual(moves[-1]['kind'], 'émission')
        self.assertEqual(decisions[-1]['title'], 'Augmentation de capital')
        result = self.run_job(out['id'])
        decision = _docx_text(self.docs.files[next(p for p in result['files'] if 'autre diligence' in p)])
        self.assertIn('Première résolution', decision)
        self.assertIn('12 000,00 €', decision)
        annonce = _docx_text(self.docs.files[next(p for p in result['files'] if 'Annonce' in p)])
        self.assertIn('Le capital social est désormais fixé à 12 000,00 €', annonce)

    def test_capital_reduction_and_custom_resolutions(self):
        co = self.co
        with self.assertRaises(Stop):
            co.operation_submit(self.desk, {'kind': 'diligence', 'company': self.cid, 'nature': 'reduction_capital', 'annulations': 'Anne DUPONT ; 9000'})
        out = co.operation_submit(self.desk, {'kind': 'diligence', 'company': self.cid, 'nature': 'autre', 'intitule': 'Agrément d’un nouveau pacte',
                                              'resolutions': 'approuve le pacte d’associés fictif\nautorise le président à le signer', 'date_effet': '2026-12-01'})
        text = _docx_text(self.docs.files[next(p for p in self.run_job(out['id'])['files'] if 'autre diligence' in p)])
        self.assertIn('Deuxième résolution', text)
        self.assertIn('Agrément d’un nouveau pacte', text)


class Roles5636(unittest.TestCase):
    def test_standalone_roles_for_new_routes(self):
        from agent.standalone_auth import allowed
        post = lambda role, route: allowed(role, 'POST', '/api440/m5636/' + route)
        self.assertTrue(post('avocat', 'corporate/operation'))
        self.assertTrue(post('avocat', 'mission/annuler'))
        self.assertTrue(post('assistant', 'contacts/enregistrer'))
        self.assertTrue(post('assistant', 'corporate/operation'))
        self.assertFalse(post('assistant', 'relances/deposer'))
        self.assertFalse(post('avocat', 'nextcloud/app'))
        self.assertFalse(post('avocat', 'microsoft/connexion'))
        self.assertTrue(post('administrateur', 'microsoft/connexion'))


# --------------------------------------------------------------------------------------------- Corporate : modèles d'actes du cabinet
class CorporateTemplates(_CorporateBase):
    def b64(self, tags):
        return 'data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,' + base64.b64encode(letter_docx(tags)).decode()

    def test_tags_with_or_without_spaces(self):
        raw = letter_docx(['Société : {{ denomination }}', 'Capital : {{capital_fr}}', 'Inconnue : {{ pas_une_variable }}'])
        text = _docx_text(self.co.fill_docx(raw, {'denomination': 'EXEMPLE SAS', 'capital_fr': '10 000,00 €'}))
        self.assertIn('Société : EXEMPLE SAS', text)
        self.assertIn('Capital : 10 000,00 €', text)
        self.assertIn('[À COMPLÉTER : pas_une_variable]', text)
        self.assertEqual(self.co.template_tags(raw), {'denomination', 'capital_fr', 'pas_une_variable'})

    def test_own_deed_imported_from_the_computer_is_offered_and_generated(self):
        co = self.co
        imp = co.import_template(self.desk, {'filename': 'Lettre de mission.docx', 'data': self.b64(['Mission pour {{ denomination }}',
                                                                                                   'Cédant : {{cedant}} ; total {{prix_total_fr}}',
                                                                                                   'Champ inconnu {{ nimporte }}'])})
        self.assertTrue(imp['source'].startswith('local:'))
        saved = co.save_own_deed(self.desk, {'label': 'Lettre de mission corporate', 'path': imp['source'], 'op_cession': True})
        self.assertIn('{{nimporte}}', saved['message'])                                      # balise inconnue signalée
        self.assertIn('{{nimporte}}', self.request('/corporate', query='societe=%s&vue=modeles' % self.cid)['body'])   # et conservée à l'écran
        form = self.request('/corporate', query='societe=%s&vue=operations&operation=cession' % self.cid)['body']
        self.assertIn('Lettre de mission corporate', form)
        self.assertIn('Variables utilisables dans vos modèles Word', form)
        self.assertNotIn('Lettre de mission corporate', self.request('/corporate', query='societe=%s&vue=operations&operation=siege' % self.cid)['body'])
        out = co.operation_submit(self.desk, {'kind': 'cession', 'company': self.cid, 'cedant': 'Paul MARTIN', 'cessionnaire_nom': 'Lucie BERNARD',
                                              'nombre': '100', 'prix_unitaire': '3', 'date_cession': '2026-10-12'})
        result = self.run_job(out['id'])
        own = [p for p in result['files'] if 'Lettre de mission corporate' in p]
        self.assertEqual(len(own), 1)
        text = _docx_text(self.docs.files[own[0]])
        self.assertIn('Mission pour EXEMPLE SAS', text)
        self.assertIn('Cédant : Paul MARTIN ; total 300,00 €', text)
        self.assertIn('nimporte', text)
        co.delete_own_deed(self.desk, {'id': saved['id']})
        self.assertEqual(co.own_deeds(self.desk), [])

    def test_template_for_an_existing_deed_is_checked_and_listed_variables_match(self):
        co = self.co
        self.docs.files['/Modeles/Acte de cession.docx'] = letter_docx(['{{ cessionnaire }} acquiert {{nombre}} titres', '{{ faute_de_frappe }}'])
        with unittest.mock.patch('agent.dav.DAV', return_value=self.docs):
            msg = co.save_template(self.desk, {'doc': 'acte_cession', 'path': '/Modeles/Acte de cession.docx'})['message']
        self.assertIn('{{faute_de_frappe}}', msg)
        names = co.variables(self.desk, 'cession')
        for key in ('denomination', 'cedant', 'cessionnaire', 'prix_total_fr', 'numero_mouvement', 'date_cession_fr'):
            self.assertIn(key, names)
        self.assertIn('EXEMPLE SAS', names['denomination'])
        self.assertIn('{{ denomination }}', self.request('/corporate', query='societe=%s&vue=modeles' % self.cid)['body'])
        for bad in ({'filename': 'x.pdf', 'data': self.b64(['a'])}, {'filename': 'x.docx', 'data': base64.b64encode(b'pas un zip').decode()}):
            with self.assertRaises(Stop):
                co.import_template(self.desk, bad)
        with self.assertRaises(Stop):
            co.save_own_deed(self.desk, {'label': 'Sans opération', 'path': '/Modeles/Acte de cession.docx'})

    def test_word_files_listed_by_the_nextcloud_picker(self):
        from agent import nextcloud5636
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'Modeles').mkdir()
            (Path(td) / 'Modeles' / 'Statuts.docx').write_bytes(b'x')
            (Path(td) / 'Modeles' / 'note.txt').write_text('x', encoding='utf-8')
            self.desk.c['nextcloud'] = {'local_path': td}
            out = nextcloud5636.folders(self.desk, '/Modeles', '.docx')
            self.assertEqual([f['name'] for f in out['files']], ['Statuts.docx'])
            self.assertNotIn('files', nextcloud5636.folders(self.desk, '/Modeles'))

    def test_assistant_cannot_change_templates(self):
        from agent.standalone_auth import allowed
        for route in ('corporate/acte', 'corporate/import', 'corporate/modele'):
            self.assertFalse(allowed('assistant', 'POST', '/api440/m5636/' + route), route)
        self.assertTrue(allowed('avocat', 'POST', '/api440/m5636/corporate/acte'))


if __name__ == '__main__':
    unittest.main()
