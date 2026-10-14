"""5.6.35 : correctif de l'audit de la 5.6.34 (F01 à F18) et demandes de l'avocat (application Nextcloud, recette automatique du serveur,
travaux trop longs). Chaque critère de recette de l'audit est transformé en test de non-régression. Données fictives uniquement."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import unittest
import unittest.mock

from agent.common import Stop
import test_v5634 as t34

ROOT = Path(__file__).resolve().parents[1]
MID = t34.MID


# --------------------------------------------------------------------------------------------- contrat /integration/v1 (F08–F13)
class Contract(t34.Base):
    SECRET = 'secret-de-recette-fictif-0123456789abcdef'

    def setUp(self):
        super().setUp()
        self.secret = Path(self.desk.c['state_dir']) / 'integration.secret'
        self.secret.write_text(self.SECRET, encoding='utf-8')
        self.secret.chmod(0o600)   # F15 : indépendant de l'umask
        self.desk.c['integration5634'] = {'enabled': True, 'instance_id': 'cabinet-test', 'secret_file': str(self.secret)}
        self.n = 0

    def ctx(self, scope=(MID,), **over):
        self.n += 1
        c = {'instance': 'cabinet-test', 'user': 'alice', 'scope': list(scope), 'exp': (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
             'nonce': hashlib.sha256(('%s-%d' % (self.id(), self.n)).encode()).hexdigest()[:32]}
        c.update(over)
        return c

    def call(self, method, path, ctx=None, body=None, raw=None):
        from agent.integration5634 import handle, sign
        ctx = self.ctx() if ctx is None else ctx
        ctx.setdefault('op', method + ' ' + path)
        headers = {'HTTP_X_AXIORHUB_CONTEXT': raw if raw is not None else sign(self.SECRET, ctx)}
        return handle(self.desk, method, path, headers, json.dumps(body).encode() if body is not None else b'')

    def job(self, key='cle-recette-01', **over):
        body = {'operation': 'fiche_dossier', 'matter': MID, 'idempotency_key': key}
        body.update(over)
        return self.call('POST', '/integration/v1/jobs', body=body)

    # F10
    def test_signed_operation_must_match_the_called_route(self):
        ctx = self.ctx(op='GET /integration/v1/jobs/' + 'f' * 32)
        status, out = self.call('POST', '/integration/v1/jobs', ctx, {'operation': 'recherche_juridique', 'question': 'Prescription biennale ?',
                                                                       'idempotency_key': 'cle-recette-op1'})
        self.assertEqual((status, out['code']), ('403 Forbidden', 'operation_non_signee'))
        from agent import integration5634
        integration5634.schema(self.desk)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM integration_jobs5634').fetchone()[0], 0)   # aucune écriture
        status, out = self.call('POST', '/integration/v1/jobs', body={'operation': 'recherche_juridique', 'question': 'Prescription biennale ?',
                                                                      'idempotency_key': 'cle-recette-op2'})
        self.assertEqual(status, '202 Accepted')

    # F13
    def test_malformed_contexts_give_structured_4xx(self):
        naive = (datetime.now(timezone.utc) + timedelta(minutes=2)).replace(tzinfo=None).isoformat()
        for over, code in (({'exp': naive}, 'contexte_sans_fuseau'), ({'exp': 12}, 'contexte_invalide'), ({'scope': 'tout'}, 'contexte_invalide'),
                           ({'user': ['x']}, 'contexte_hors_instance'), ({'op': 5}, 'operation_non_signee')):
            c = self.ctx()
            c.update(over)   # « scope » passé ici tel quel (pas par le paramètre de ctx)
            status, out = self.call('GET', '/integration/v1/jobs/' + 'f' * 32, c)
            self.assertTrue(status.startswith('40'), (over, status))
            self.assertEqual(out['code'], code, over)
            self.assertIn('correlation_id', out)
        from agent.integration5634 import handle
        import base64
        payload = base64.urlsafe_b64encode(b'[1,2]').decode().rstrip('=')
        import hmac
        raw = payload + '.' + hmac.new(self.SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        status, out = handle(self.desk, 'GET', '/integration/v1/jobs/' + 'f' * 32, {'HTTP_X_AXIORHUB_CONTEXT': raw}, b'')
        self.assertEqual((status, out['code']), ('401 Unauthorized', 'contexte_invalide'))

    # F08
    def test_refused_request_reserves_nothing_and_key_is_bound_to_content(self):
        status, out = self.call('POST', '/integration/v1/jobs', body={'operation': 'recherche_juridique', 'question': 'x', 'idempotency_key': 'cle-recette-q1'})
        self.assertEqual(out['code'], 'question_juridique_invalide')
        from agent import integration5634
        integration5634.schema(self.desk)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM integration_jobs5634').fetchone()[0], 0)
        status, out = self.call('POST', '/integration/v1/jobs', body={'operation': 'recherche_juridique', 'question': 'Prescription biennale ?',
                                                                      'idempotency_key': 'cle-recette-q1'})
        self.assertEqual(status, '202 Accepted')
        row = self.desk.db.execute('SELECT job FROM integration_jobs5634 WHERE id=?', (out['job_id'],)).fetchone()
        self.assertIsNotNone(row['job'])                                                  # un vrai travail moteur existe
        status, again = self.call('POST', '/integration/v1/jobs', body={'operation': 'recherche_juridique', 'question': 'Prescription biennale ?',
                                                                        'idempotency_key': 'cle-recette-q1'})
        self.assertEqual((again['state'], again['job_id']), ('existing', out['job_id']))
        status, other = self.call('POST', '/integration/v1/jobs', body={'operation': 'recherche_juridique', 'question': 'Autre question ?',
                                                                        'idempotency_key': 'cle-recette-q1'})
        self.assertEqual((status, other['code']), ('409 Conflict', 'cle_idempotence_deja_utilisee'))

    def test_orphan_reservation_is_resumed_or_closed(self):
        from agent import integration5634 as i
        i.schema(self.desk)
        old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        args = {'operation': 'recherche_juridique', 'matter': '', 'manifest_id': '', 'question': 'Délai de prescription ?'}
        self.desk.db.execute("INSERT INTO integration_jobs5634(id,instance,user,idem,operation,matter,manifest,job,created,state,content_hash,args) "
                             "VALUES('a'||hex(randomblob(15)),'cabinet-test','alice','orphelin-1','recherche_juridique','', '',NULL,?,'pending','x',?)",
                             (old, json.dumps(args)))
        self.desk.db.execute("INSERT INTO integration_jobs5634(id,instance,user,idem,operation,matter,manifest,job,created,state,content_hash,args) "
                             "VALUES('b'||hex(randomblob(15)),'cabinet-test','alice','orphelin-2','fiche_dossier','', '',NULL,?,'pending','',?)", (old, '{}'))
        self.desk.db.commit()
        i._repair(self.desk)
        rows = {r['idem']: r for r in self.desk.db.execute('SELECT * FROM integration_jobs5634')}
        self.assertIsNotNone(rows['orphelin-1']['job'])
        self.assertEqual(rows['orphelin-2']['state'], 'failed')

    # F09
    def test_cancel_without_engine_job_is_durable(self):
        from agent import integration5634 as i
        i.schema(self.desk)
        now = datetime.now(timezone.utc).isoformat()
        ident = 'c' * 32
        self.desk.db.execute("INSERT INTO integration_jobs5634(id,instance,user,idem,operation,matter,manifest,job,created,state,content_hash,args) "
                             "VALUES(?,'cabinet-test','alice','annule-1','recherche_juridique','','',NULL,?,'pending','h','{}')", (ident, now))
        self.desk.db.commit()
        self.assertEqual(self.call('POST', '/integration/v1/jobs/%s/cancel' % ident)[1]['state'], 'cancelled')
        self.assertEqual(self.call('GET', '/integration/v1/jobs/' + ident)[1]['state'], 'cancelled')
        self.assertEqual(self.call('POST', '/integration/v1/jobs/%s/cancel' % ident)[1]['state'], 'cancelled')   # idempotent
        i._repair(self.desk)
        self.assertIsNone(self.desk.db.execute('SELECT job FROM integration_jobs5634 WHERE id=?', (ident,)).fetchone()['job'])

    # F11
    def test_manifest_is_checked_and_restricts_the_analysed_pieces(self):
        t34._index(self.desk, [('/Dossiers/DEMO/Contrat.pdf', '2025-01-02', 'Contrat de prestation', ''),
                               ('/Dossiers/DEMO/Facture.pdf', '2025-02-03', 'Facture', ''),
                               ('/Dossiers/DEMO/Assignation.pdf', '2025-03-04', 'Assignation', '')])
        self.assertEqual(self.job('cle-recette-m0', manifest_id='d' * 32)[1]['code'], 'manifeste_absent_ou_hors_perimetre')
        status, man = self.call('POST', '/integration/v1/source-manifests', body={'matter': MID, 'sources': [
            {'file_id': '11', 'etag': '"e-at.pdf"', 'name': 'Contrat.pdf', 'size': 10},
            {'file_id': '12', 'name': 'Assignation.pdf', 'size': 10},   # 5.6.37 (audit F04) : sans version ; une version contradictoire est refusée
            {'file_id': '13', 'etag': 'inconnu', 'name': 'Absente.pdf', 'size': 10}]})
        self.assertEqual((man['accepted'], man['coverage']['refused']), (2, 1))
        self.assertEqual(man['errors'][0]['error'], 'piece_absente_de_l_index_du_dossier')
        other = self.call('POST', '/integration/v1/jobs', self.ctx(user='bob'), {'operation': 'fiche_dossier', 'matter': MID,
                                                                               'idempotency_key': 'cle-recette-m1', 'manifest_id': man['manifest_id']})[1]
        self.assertEqual(other['code'], 'manifeste_absent_ou_hors_perimetre')
        status, out = self.job('cle-recette-m2', manifest_id=man['manifest_id'])
        self.assertEqual(status, '202 Accepted')
        job = self.desk.db.execute('SELECT j.args FROM jobs j JOIN integration_jobs5634 i ON i.job=j.id WHERE i.id=?', (out['job_id'],)).fetchone()
        self.assertEqual(sorted(json.loads(job['args'])['only']), ['/Dossiers/DEMO/Assignation.pdf', '/Dossiers/DEMO/Contrat.pdf'])
        from agent import synthese5632
        m = {'id': MID}
        with unittest.mock.patch('agent.dossiers5632.resolve', return_value=[]):
            used, cov = synthese5632.sources_for(self.desk, m, 50000, only={'/Dossiers/DEMO/Contrat.pdf'})
        self.assertEqual([s['path'] for s in used], ['/Dossiers/DEMO/Contrat.pdf'])
        self.assertEqual(cov['fichiers'], 1)

    # F12
    def test_result_of_a_finished_job_stays_identical_after_a_new_preparation(self):
        from agent import dossier5627
        dossier5627.ensure_schema(self.desk)
        status, out = self.job('cle-recette-r1')
        row = self.desk.db.execute('SELECT job FROM integration_jobs5634 WHERE id=?', (out['job_id'],)).fetchone()
        now = self.desk.now()
        self.desk.db.execute("INSERT INTO fiche5627(matter,version,data,sources,edits,created) VALUES(?,1,?,'[]','{}',?)", (MID, json.dumps({'synthese': 'Version une'}), now))
        self.desk.db.execute("UPDATE jobs SET status='done', result=? WHERE id=?", (json.dumps({'version': 1}), row['job']))
        self.desk.db.commit()
        first = self.call('GET', '/integration/v1/results/' + out['job_id'])[1]
        self.desk.db.execute("INSERT INTO fiche5627(matter,version,data,sources,edits,created) VALUES(?,2,?,'[]','{}',?)", (MID, json.dumps({'synthese': 'Version deux'}), now))
        self.desk.db.commit()
        again = self.call('GET', '/integration/v1/results/' + out['job_id'])[1]
        self.assertEqual(first, again)
        self.assertEqual(again['content']['data']['synthese'], 'Version une')
        self.assertEqual(self.call('POST', '/integration/v1/results/%s/approvals' % out['job_id'], body={'version': 'autre', 'decision': 'approuve'})[1]['code'],
                         'resultat_modifie_depuis_lecture')
        ok = self.call('POST', '/integration/v1/results/%s/approvals' % out['job_id'], body={'version': first['version'], 'decision': 'approuve'})[1]
        self.assertEqual(ok['decision'], 'approuve')
        self.assertEqual(self.call('POST', '/integration/v1/results/%s/approvals' % out['job_id'],
                                   body={'version': first['version'], 'decision': 'correction_demandee'})[1]['code'], 'correction_a_preciser')
        self.call('POST', '/integration/v1/results/%s/approvals' % out['job_id'],
                  body={'version': first['version'], 'decision': 'correction_demandee', 'comment': 'Préciser la date fictive.'})
        history = self.call('GET', '/integration/v1/results/' + out['job_id'])[1]['decisions']
        self.assertEqual([h['decision'] for h in history], ['correction_demandee', 'approuve'])   # historique complet, plus récent d'abord
        self.assertEqual(history[0]['comment'], 'Préciser la date fictive.')
        self.assertTrue(all(h['version'] == first['version'] for h in history))

    # liste des dossiers pour l'application Nextcloud (« Aucun dossier ne vous est accessible »)
    def test_matter_list_is_reserved_to_the_nextcloud_administrator(self):
        self.assertEqual(self.call('GET', '/integration/v1/matters')[1]['code'], 'liste_reservee_administrateur')
        status, out = self.call('GET', '/integration/v1/matters', self.ctx(scope=['*']))
        self.assertEqual(status, '200 OK')
        self.assertIn(MID, [m['id'] for m in out['matters']])
        self.assertTrue(all(set(m) == {'id', 'label', 'archived'} for m in out['matters']))

    # secret partagé : même règle que l'application Nextcloud (8 caractères au moins)
    def test_short_secret_of_eight_characters_is_accepted_and_shorter_refused(self):
        self.secret.write_text('8carac!!', encoding='utf-8')
        self.SECRET = '8carac!!'
        self.assertEqual(self.call('GET', '/integration/v1/jobs/' + 'f' * 32)[0], '404 Not Found')   # signature acceptée
        self.secret.write_text('court', encoding='utf-8')
        self.SECRET = 'court'
        self.assertEqual(self.call('GET', '/integration/v1/jobs/' + 'f' * 32)[1]['code'], 'secret_integration_trop_court')

    # F15
    def test_secret_readable_by_others_is_refused(self):
        import os
        if os.name == 'nt':
            self.skipTest('permissions POSIX (contrôle réel sous Linux)')
        self.secret.chmod(0o644)
        self.assertEqual(self.call('GET', '/integration/v1/jobs/' + 'f' * 32)[1]['code'], 'secret_absent_ou_permissions_trop_larges')



# --------------------------------------------------------------------------------------------- preuves (F01, F02)
class Evidence(unittest.TestCase):
    TEXT = ('[Page 1]\nLe contrat de prestation a été signé entre ALPHA et BETA SERVICES le 12 mars 2025 à Lyon, en deux exemplaires.\n\n'
            '[Page 2]\nL’audience est fixée au 15 juin 2026. Le prix convenu est de 4 500 USD, payable à la livraison.')

    def chunk(self, text=None, paginated=True):
        return {'text': text or self.TEXT, 'idx': 1, 'start': 0, 'paginated': paginated, 'start_page': '1' if paginated else None}

    def check(self, kind, item, **kw):
        from agent import synthese5632
        return synthese5632._check(kind, item, self.chunk(**kw))

    def test_date_is_checked_only_in_the_cited_excerpt(self):
        excerpt = 'Le contrat de prestation a été signé entre ALPHA et BETA SERVICES le 12 mars 2025 à Lyon'
        wrong = self.check('faits', {'fait': 'Signature du contrat', 'date': '2026-06-15', 'extrait': excerpt})
        self.assertEqual(wrong['statut'], 'a_verifier')
        self.assertIn('date_absente_de_l_extrait', wrong['causes'])
        right = self.check('faits', {'fait': 'Signature du contrat', 'date': '2025-03-12', 'extrait': excerpt})
        self.assertEqual((right['statut'], right['page']), ('documente', '1'))
        self.assertIn('passage_sha256', right)

    def test_amount_of_another_page_never_validates_an_excerpt_without_amount(self):
        excerpt = 'Le contrat de prestation a été signé entre ALPHA et BETA SERVICES le 12 mars 2025 à Lyon'
        out = self.check('montants', {'montant': '4 500 EUR', 'extrait': excerpt})
        self.assertEqual(out['statut'], 'a_verifier')
        self.assertIn('montant_absent_du_passage', out['causes'])
        usd = self.check('montants', {'montant': '4 500 EUR', 'extrait': 'Le prix convenu est de 4 500 USD, payable à la livraison'})
        self.assertIn('montant_absent_du_passage', usd['causes'])                       # USD ne justifie jamais EUR
        ok = self.check('montants', {'montant': '4 500 USD', 'extrait': 'Le prix convenu est de 4 500 USD, payable à la livraison'})
        self.assertEqual((ok['statut'], ok['page'], ok['devise'], ok['valeur']), ('documente', '2', 'USD', '4500'))

    def test_unpaginated_docx_uses_the_excerpt_position(self):
        text = ('Courrier du 3 janvier 2025 : nous vous réclamons la somme de 1 200 € au titre des loyers.\n\n'
                'Par ailleurs, la réunion du 9 février 2025 a fixé un acompte de 300 €.')
        ok = self.check('montants', {'montant': '1 200 €', 'extrait': 'nous vous réclamons la somme de 1 200 € au titre des loyers'}, text=text, paginated=False)
        self.assertEqual((ok['statut'], ok['paragraphe']), ('documente', 1))
        bad = self.check('faits', {'fait': 'Réclamation', 'date': '2025-02-09', 'extrait': 'nous vous réclamons la somme de 1 200 € au titre des loyers'},
                         text=text, paginated=False)
        self.assertEqual(bad['statut'], 'a_verifier')

    def test_amounts_keep_currency_sign_and_never_take_a_year(self):
        from agent import preuve5634 as p
        self.assertEqual(p.check_amount('4 500 EUR', 'Le prix est de 4 500 USD'), 'montant_absent_du_passage')
        self.assertEqual(p.check_amount('-4 500 EUR', 'avoir de +4 500 EUR'), 'montant_absent_du_passage')
        self.assertEqual(p.check_amount('2 025 EUR', 'Ce courrier a été écrit en 2025'), 'montant_absent_du_passage')
        for claim, passage in (('4 500,50 €', 'soit 4 500,50 € TTC'), ('4.500,50 EUR', 'soit 4.500,50 EUR HT'), ('0 €', 'pour 0 € de frais'),
                               ('-120 EUR', 'un avoir de -120 EUR'), ('4 500 €', 'soit 4 500 €'), ('4 500 €', 'soit 4 500 €')):
            self.assertIsNone(p.check_amount(claim, passage), (claim, passage))
        self.assertEqual(p.check_amount('4,500 €', 'soit 4,500 €'), 'montant_ambigu')
        self.assertEqual(p.check_amount('4500', 'soit 4 500 €'), 'devise_absente')
        self.assertIsNone(p.check_amount('4500', 'soit 4 500 €', devise='EUR'))
        self.assertEqual(p.money_in_text('Pièce n° 12 du 3 mars 2025, page 4'), [])

    def test_method_version_invalidates_previous_extractions(self):
        from agent import synthese5632
        self.assertEqual(synthese5632.METHOD, '5635.1')


# --------------------------------------------------------------------------------------------- agenda local (F03–F07, F18)
class Agenda(t34.Base):
    SERIES = ('BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:audit-series\r\nDTSTART:{d1}T090000Z\r\nDTEND:{d1}T100000Z\r\n'
              'RRULE:FREQ=WEEKLY;COUNT=3\r\nSUMMARY:Audience de mise en état fictive\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n')

    def setUp(self):
        super().setUp()
        from agent import agenda5634
        self.a = agenda5634
        self.d1 = (datetime.now(timezone.utc) + timedelta(days=7)).strftime('%Y%m%d')

    def cache(self):
        return self.desk.db.execute("SELECT COUNT(*) FROM calendar_cache WHERE calendar_url='local:agenda'").fetchone()[0]

    def active(self):
        return self.desk.db.execute("SELECT COUNT(*) FROM agenda5634 WHERE status='actif'").fetchone()[0]

    # F03
    def test_each_occurrence_has_its_own_cache_entry(self):
        out = self.a.import_ics(self.desk, self.SERIES.format(d1=self.d1), 'recette')
        self.assertEqual((out['added'], self.active(), self.cache()), (3, 3, 3))
        first = self.desk.db.execute("SELECT id FROM agenda5634 ORDER BY starts LIMIT 1").fetchone()['id']
        self.a.cancel(self.desk, first)
        self.assertEqual((self.active(), self.cache()), (2, 2))
        again = self.a.import_ics(self.desk, self.SERIES.format(d1=self.d1), 'recette')
        self.assertEqual(again['added'], 0)                                              # réimport sans doublon
        self.desk.db.execute("DELETE FROM settings WHERE key='agenda5635_cache'")
        self.desk.db.execute("DELETE FROM calendar_cache WHERE calendar_url='local:agenda'")
        self.desk.db.commit()
        self.a.schema(self.desk)                                                         # migration : reconstruction du cache
        self.assertEqual(self.cache(), 2)

    # F04
    def test_export_gives_each_occurrence_a_distinct_stable_identity(self):
        self.a.import_ics(self.desk, self.SERIES.format(d1=self.d1), 'recette')
        first = self.desk.db.execute("SELECT id FROM agenda5634 ORDER BY starts LIMIT 1").fetchone()['id']
        self.a.cancel(self.desk, first)
        ics = self.a.export_ics(self.desk)
        uids = re.findall(r'^UID:(.+)$', ics.replace('\r', ''), re.M)
        self.assertEqual(len(uids), 2)
        self.assertEqual(len(set(uids)), 2)
        self.assertEqual(uids, re.findall(r'^UID:(.+)$', self.a.export_ics(self.desk).replace('\r', ''), re.M))   # stables
        self.assertIn('X-AXIORHUB-SERIES:audit-series', ics)
        self.desk.db.execute('DELETE FROM agenda5634')
        self.desk.db.commit()
        back = self.a.import_ics(self.desk, ics, 'aller-retour')
        self.assertEqual(back['added'], 2)
        self.assertEqual(self.a.import_ics(self.desk, ics, 'aller-retour')['added'], 0)
        self.assertEqual({r['uid'] for r in self.desk.db.execute('SELECT uid FROM agenda5634')}, {'audit-series'})

    # F05
    def test_partial_update_keeps_duration_all_day_and_notes(self):
        start = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
        ev = self.a.create(self.desk, {'title': 'Rendez-vous fictif', 'start': start.isoformat(), 'duration_minutes': 45, 'notes': 'Note initiale'})
        out = self.a.update(self.desk, {'id': ev['id'], 'title': 'Rendez-vous fictif renommé', 'description': 'Nouvelle note'})['event']
        self.assertEqual((datetime.fromisoformat(out['ends']) - datetime.fromisoformat(out['starts'])), timedelta(minutes=45))
        self.assertEqual(out['description'], 'Nouvelle note')
        day = (date.today() + timedelta(days=10)).isoformat()
        two = self.a.create(self.desk, {'title': 'Échéance fictive', 'kind': 'echeance', 'all_day': True, 'start': day, 'days': 2})
        renamed = self.a.update(self.desk, {'id': two['id'], 'title': 'Échéance fictive renommée'})['event']
        self.assertTrue(renamed['all_day'])
        # 5.6.43 : écart mesuré en dates locales (Europe/Paris) : un changement d'heure dans la période ne déplace aucune date
        paris = self.a._tz(self.desk)
        self.assertEqual(datetime.fromisoformat(renamed['ends']).astimezone(paris).date() - datetime.fromisoformat(renamed['starts']).astimezone(paris).date(),
                         timedelta(days=2))
        moved = self.a.update(self.desk, {'id': ev['id'], 'start': (start + timedelta(hours=2)).isoformat()})['event']
        self.assertEqual(datetime.fromisoformat(moved['ends']) - datetime.fromisoformat(moved['starts']), timedelta(minutes=45))
        with self.assertRaises(Stop):
            self.a.update(self.desk, {'id': ev['id'], 'title': 'Conflit', 'base': '2000-01-01T00:00:00+00:00'})

    # F06
    def test_exdate_is_applied_and_until_is_inclusive(self):
        d2 = (datetime.strptime(self.d1, '%Y%m%d') + timedelta(days=7)).strftime('%Y%m%d')
        ex = self.SERIES.format(d1=self.d1).replace('RRULE:FREQ=WEEKLY;COUNT=3\r\n', 'RRULE:FREQ=WEEKLY;COUNT=3\r\nEXDATE:%sT090000Z\r\n' % d2)
        self.assertEqual(self.a.import_ics(self.desk, ex, 'exdate')['added'], 2)
        self.desk.db.execute('DELETE FROM agenda5634')
        self.desk.db.commit()
        until = self.SERIES.format(d1=self.d1).replace('COUNT=3', 'UNTIL=%sT090000Z' % d2)
        self.assertEqual(self.a.import_ics(self.desk, until, 'until')['added'], 2)
        self.desk.db.execute('DELETE FROM agenda5634')
        self.desk.db.commit()
        odd = self.SERIES.format(d1=self.d1).replace('COUNT=3', 'COUNT=3;BYMONTHDAY=1')
        out = self.a.import_ics(self.desk, odd, 'complexe')
        self.assertEqual(out['partial_series'], 1)
        self.assertIn('regle_complexe', out['reasons'])

    # F07
    def test_restore_brings_back_status_and_reminders_after_preview(self):
        start = (datetime.now(timezone.utc) + timedelta(days=5)).replace(microsecond=0)
        ev = self.a.create(self.desk, {'title': 'Audience fictive', 'kind': 'audience', 'start': start.isoformat(), 'reminders': [1440, 60]})
        name = Path(self.a.backup(self.desk, force=True)).name
        self.a.cancel(self.desk, ev['id'])
        preview = self.a.restore(self.desk, name)
        self.assertEqual(preview['apercu']['reactives'], 1)
        self.assertEqual(self.desk.db.execute('SELECT status FROM agenda5634 WHERE id=?', (ev['id'],)).fetchone()['status'], 'annule')   # rien avant confirmation
        done = self.a.restore(self.desk, name, apply=True)
        row = self.desk.db.execute('SELECT status, reminders FROM agenda5634 WHERE id=?', (ev['id'],)).fetchone()
        self.assertEqual((row['status'], json.loads(row['reminders'])), ('actif', [60, 1440]))
        self.assertTrue(done['sauvegarde_prealable'])
        self.assertEqual(self.cache(), 1)

    def test_reminders_survive_an_export_import_round_trip(self):
        start = (datetime.now(timezone.utc) + timedelta(days=5)).replace(microsecond=0)
        self.a.create(self.desk, {'title': 'Rendez-vous fictif', 'start': start.isoformat(), 'reminders': [1440, 60]})
        ics = self.a.export_ics(self.desk)
        self.desk.db.execute('DELETE FROM agenda5634')
        self.desk.db.commit()
        self.a.import_ics(self.desk, ics, 'retour')
        self.assertEqual(json.loads(self.desk.db.execute('SELECT reminders FROM agenda5634').fetchone()['reminders']), [60, 1440])

    # F18
    def test_network_copy_happens_after_commit_and_outside_any_transaction(self):
        import sqlite3
        seen = {}
        db_path = None
        for row in self.desk.db.execute('PRAGMA database_list'):
            if row[1] == 'main':
                db_path = row[2]

        class SlowDav:
            def put_event(inner, *args, **kw):
                seen['in_transaction'] = self.desk.db.in_transaction
                other = sqlite3.connect(db_path, timeout=0)
                try:
                    other.execute('BEGIN IMMEDIATE')
                    other.execute('ROLLBACK')
                    seen['other_could_write'] = True
                except sqlite3.OperationalError:
                    seen['other_could_write'] = False
                finally:
                    other.close()
                return 'uid-distant'

        self.desk.c['nextcloud_workflow'] = {'enabled': True, 'planning_calendar_url': '/remote.php/dav/calendars/x/planning/'}
        with unittest.mock.patch('agent.workplan._dav', return_value=SlowDav()):
            out = self.a.create(self.desk, {'title': 'Rendez-vous fictif', 'start': (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()})
        # 5.6.43 (cahier v9, § 17.3) : un événement de l'Agenda personnel n'est jamais copié, même avec la copie Nextcloud configurée
        self.assertEqual(seen, {})
        self.assertEqual(self.desk.db.execute('SELECT sync_state, calendar_scope FROM agenda5634 WHERE id=?', (out['id'],)).fetchone()[:],
                         ('local', 'personal_local'))
        # F18 reste vrai pour un ancien événement dont l'avocat a gardé la copie distante : copie hors transaction, après validation
        self.desk.db.execute("UPDATE agenda5634 SET calendar_scope='legacy_bridge',sync_state='a_repartir' WHERE id=?", (out['id'],))
        self.desk.db.commit()
        self.a.decide_legacy(self.desk, out['id'], 'garder_copie')
        with unittest.mock.patch('agent.workplan._dav', return_value=SlowDav()):
            self.a.update(self.desk, {'id': out['id'], 'title': 'Rendez-vous fictif déplacé'})
        self.assertEqual(seen, {'in_transaction': False, 'other_could_write': True})
        self.assertEqual(self.desk.db.execute('SELECT sync_state, remote_uid FROM agenda5634 WHERE id=?', (out['id'],)).fetchone()[:], ('synchronise', 'uid-distant'))

class WindowsAcl(unittest.TestCase):
    ME = 'S-1-5-21-1-2-3-1000'

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'secret-fictif.txt'
        self.path.write_text('x', encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def status(self, sddl, me=ME):
        from agent import diagnostic5634
        return diagnostic5634.file_permission(self.path, True, 'windows', lambda _: sddl, me=me)

    # F14
    def test_null_dacl_is_an_error_never_verified(self):
        out = self.status('O:%sD:NO_ACCESS_CONTROL' % self.ME)
        self.assertEqual((out['status'], out['reason']), ('error', 'dacl_nulle'))
        empty = self.status('O:%sD:P' % self.ME)                                                       # DACL vide ≠ DACL nulle
        self.assertEqual((empty['status'], empty['reason']), ('error', 'dacl_vide_fichier_illisible'))

    def test_unknown_account_or_owner_gives_unknown(self):
        other = self.status('O:%sD:PAI(A;;FA;;;SY)(A;;FA;;;%s)(A;;FR;;;S-1-5-21-9-9-9-1001)' % (self.ME, self.ME))
        self.assertEqual((other['status'], other['reason'], other['accounts']), ('unknown', 'compte_non_reconnu', ['S-1-5-21-9-9-9-1001']))
        owner = self.status('O:S-1-5-21-9-9-9-1001D:PAI(A;;FA;;;SY)(A;;FA;;;%s)' % self.ME)
        self.assertEqual((owner['status'], owner['reason']), ('unknown', 'proprietaire_non_reconnu'))
        self.assertEqual(self.status('D:PAI(A;;FA;;;SY)')['status'], 'unknown')                      # propriétaire non lu
        self.assertEqual(self.status('O:%s' % self.ME)['status'], 'unknown')                           # DACL non lue

    def test_owner_system_admins_and_inherit_only_entries_are_accepted(self):
        ok = self.status('O:BAD:PAI(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;%s)(A;OICIIO;GA;;;CO)' % self.ME)
        self.assertEqual(ok['status'], 'verified')
        self.assertEqual(self.status('O:%sD:(A;;FR;;;BU)(A;;FA;;;SY)' % self.ME)['status'], 'error')
        self.assertEqual(self.status('O:%sD:(D;;FA;;;WD)(A;;FA;;;SY)' % self.ME)['status'], 'verified')   # refus : jamais un accès large

    @unittest.skipUnless(__import__('sys').platform == 'win32', 'Windows uniquement')
    def test_real_acl_of_a_fresh_file_is_never_falsely_green(self):
        from agent import diagnostic5634
        me = diagnostic5634.windows_user_sid()
        self.assertTrue(me and me.startswith('S-1-'))
        sddl = diagnostic5634.windows_sddl(self.path)
        self.assertTrue(sddl.startswith('O:'), sddl)
        out = diagnostic5634.file_permission(self.path, True, 'windows')
        acl = diagnostic5634.parse_sddl(sddl)
        if out['status'] == 'verified':
            self.assertTrue(set(acl['allowed']) <= diagnostic5634.TRUSTED_SIDS | diagnostic5634.TRUSTED_OWNERS | {me})
        else:
            self.assertIn(out['status'], ('error', 'unknown'))


class Recette(unittest.TestCase):
    def test_interface_venv_packages_are_visible_to_the_self_test(self):
        import sys
        import tempfile
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import recette
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(recette.interface_packages(td), '')                                   # pas d'environnement : rien d'ajouté
            site = Path(td) / 'lib' / ('python%d.%d' % sys.version_info[:2]) / 'site-packages'
            site.mkdir(parents=True)
            self.assertEqual(recette.interface_packages(td), str(site))
            other = Path(td) / 'autre' / 'lib' / 'python2.7' / 'site-packages'
            other.mkdir(parents=True)
            self.assertEqual(recette.interface_packages(Path(td) / 'autre'), '')                  # autre version de Python : ignoré


class DigestBudget(t34.Base):
    LONG = ''.join('[Page %d]\n%s\n' % (n, ('Paragraphe fictif de la pièce longue numéro %d, sans donnée réelle. ' % n) * 120) for n in range(1, 6))

    def setUp(self):
        super().setUp()
        t34._index(self.desk, [('/Dossiers/DEMO/Longue.pdf', '2025-06-01', self.LONG, '')])
        self.now = [0.0]

    def slow_model(self):
        clock = self.now

        class Slow(t34.FragmentModel):
            def complete(inner, *a, **k):
                clock[0] += 600                                                             # chaque fragment prend dix minutes
                return super().complete(*a, **k)
        return Slow([])

    def test_long_piece_stops_at_the_budget_and_resumes_without_redoing_fragments(self):
        from agent import synthese5632
        chunks = len(synthese5632._chunks(self.LONG))
        self.assertGreaterEqual(chunks, 4)
        model = self.slow_model()
        with unittest.mock.patch.object(synthese5632, '_clock', lambda: self.now[0]), unittest.mock.patch.object(self.desk, '_then') as then:
            first = synthese5632.build_job(self.desk, {'matter': t34.MID}, model)
            self.assertTrue(first['budget_atteint'])
            self.assertTrue(then.called)                                                     # suite programmée
            self.assertEqual(model.calls, 2)                                                 # 20 minutes : deux fragments, pas plus
            state = synthese5632.states(self.desk, t34.MID)['/Dossiers/DEMO/Longue.pdf']
            self.assertEqual((state['state'], state['attempts']), ('pending', 0))            # ni échec compté, ni « running » orphelin
            second = synthese5632.build_job(self.desk, {'matter': t34.MID}, model)
            self.assertTrue(second['budget_atteint'])
            third = synthese5632.build_job(self.desk, {'matter': t34.MID}, model)
        self.assertEqual(third['built'], 1)
        self.assertEqual(model.calls, chunks)                                                # aucun fragment relevé deux fois

    def test_running_job_age_is_measured_from_its_start(self):
        from agent import reliability393
        now = datetime.now(timezone.utc)
        self.desk.db.execute("INSERT INTO jobs(kind,args,status,priority,created,started) VALUES('digest_pieces5632','{}','running',75,?,?)",
                             ((now - timedelta(hours=5)).isoformat(), (now - timedelta(minutes=10)).isoformat()))
        self.desk.db.execute("INSERT INTO jobs(kind,args,status,priority,created,started) VALUES('digest_pieces5632','{}','running',75,?,?)",
                             ((now - timedelta(hours=5)).isoformat(), (now - timedelta(hours=3)).isoformat()))
        self.desk.db.commit()
        stale = reliability393.job_health(self.desk, now)['stale']
        self.assertEqual([round(x['age_hours']) for x in stale if x['kind'] == 'digest_pieces5632'], [3])

    def test_interrupted_digest_job_is_resumed_after_a_restart(self):
        from agent import queue567
        self.desk.db.execute("INSERT INTO jobs(kind,args,status,priority,created,started,worker,attempts) VALUES('digest_pieces5632',?,'running',75,?,?,'1',1)",
                             (json.dumps({'matter': t34.MID}), datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()))
        self.desk.db.commit()
        self.assertEqual(queue567.recover(self.desk, '1')['resumed'], 1)
        self.assertEqual(self.desk.db.execute("SELECT status FROM jobs WHERE kind='digest_pieces5632'").fetchone()[0], 'pending')


class Display(unittest.TestCase):
    def test_signal_due_date_is_readable_never_a_raw_iso_timestamp(self):
        from agent.production420_ui import _due

        class Desk:
            c = {'timezone': 'Europe/Paris'}
        self.assertEqual(_due(Desk(), '2026-10-13T19:19:44.535191+00:00'), '13/10/2026 21:19')
        self.assertEqual(_due(Desk(), '2026-10-13'), '13/10/2026')
        self.assertEqual(_due(Desk(), 'illisible'), 'illisible')


if __name__ == '__main__':
    unittest.main()
