"""5.6.88, lot agenda : objet audience, copies Outlook et Google, rappels à la personne, tâches, liste du matin.

Données fictives, faux services : aucun appel réseau. Outlook et Google ne sont exercés que par des simulations de leurs API."""
from datetime import date, datetime, timedelta, timezone
import json
import re
import unittest.mock
from urllib.parse import urlsplit

from agent import adaptateurs5652 as ad, agenda5634 as ag, agenda5688, audiences5688 as au, connexions5652 as cx, improvements36, matin5688, membres5650
from agent import microsoft5636 as ms, mobile490, propagation5688 as pr, rappels5688 as rp, reminders520, routines520, taches5628, taches5688 as ta
from agent.common import Stop
from agent.erreurs5634 import http_error
import test_v5634 as t34

MID = t34.MID
LAWYER = 'durand@example.test'
COLLEAGUE = 'martin@example.test'
PEOPLE = {o: {'owner': o, 'name': n, 'role': 'avocat'} for o, n in ((LAWYER, 'Me DURAND'), (COLLEAGUE, 'Me MARTIN'))}


def later(days=20, hour=9):
    moment = datetime.now(timezone.utc) + timedelta(days=days)
    return moment.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


class Base(t34.Base):
    def setUp(self):
        super().setUp()
        p = unittest.mock.patch.object(au, 'members', side_effect=lambda desk, auth=None: dict(PEOPLE))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(membres5650.clear)
        au.schema(self.desk)
        ta.schema(self.desk)
        rp.schema(self.desk)

    def hearing(self, **data):
        base = {'start': later(), 'duration_minutes': 60, 'matter': MID, 'avocat': LAWYER, 'type': 'plaidoiries', 'juridiction': 'Tribunal judiciaire de Valbrune', 'rg': '24/01234'}
        return au.save(self.desk, {**base, **data}, LAWYER)['id']

    def reserve(self, matter=MID, owners=(COLLEAGUE,)):
        membres5650.set_reserve(self.desk.c['state_dir'], matter, list(owners))


# ------------------------------------------------------------------------------------------------ audience
class Audience(Base):
    def test_fiche_rattachee_a_l_evenement(self):
        ident = self.hearing(notes='Conclusions à relire.', chambre='2e chambre')
        h = au.get(self.desk, ident)
        self.assertEqual((h['juridiction'], h['chambre'], h['rg'], h['avocat'], h['type_label'], h['statut']), ('Tribunal judiciaire de Valbrune', '2e chambre', '24/01234', LAWYER, 'Plaidoiries', 'a_venir'))
        event = self.desk.db.execute('SELECT id,kind,matter FROM agenda5634 WHERE id=?', (h['event_id'],)).fetchone()
        self.assertEqual((event['kind'], event['matter']), ('audience', MID))
        ag.update(self.desk, {'id': h['event_id'], 'start': later(25, 14), 'title': 'Audience déplacée'})
        moved = au.get(self.desk, ident)
        self.assertEqual(moved['title'], 'Audience déplacée')                                      # une seule source pour l'horaire
        self.assertEqual(moved['id'], ident)                                                        # identifiant stable

    def test_annuler_l_evenement_annule_la_fiche_sans_la_perdre(self):
        h = au.get(self.desk, self.hearing())
        ag.cancel(self.desk, h['event_id'])
        self.assertEqual(au.get(self.desk, h['id'])['statut'], 'annulee')

    def test_annuler_depuis_la_fiche_annule_l_evenement(self):
        ident = self.hearing()
        h = au.get(self.desk, ident)
        au.save(self.desk, {'id': ident, 'statut': 'annulee'}, LAWYER)
        self.assertEqual(self.desk.db.execute('SELECT status FROM agenda5634 WHERE id=?', (h['event_id'],)).fetchone()[0], 'annule')

    def test_migration_prudente_et_repetable(self):
        active = ag.create(self.desk, {'title': 'Audience de mise en état', 'kind': 'audience', 'start': later(), 'matter': MID})['id']
        gone = ag.create(self.desk, {'title': 'Audience ancienne', 'kind': 'audience', 'start': later(3)})['id']
        ag.cancel(self.desk, gone)
        self.desk.db.execute('DELETE FROM audiences5688')
        self.desk.db.commit()
        before = self.desk.db.execute('SELECT id,title,starts,status,updated FROM agenda5634 ORDER BY id').fetchall()
        self.assertEqual(au.migrate(self.desk), 2)
        self.assertEqual(au.migrate(self.desk), 0)                                                  # idempotente
        after = self.desk.db.execute('SELECT id,title,starts,status,updated FROM agenda5634 ORDER BY id').fetchall()
        self.assertEqual([tuple(x) for x in before], [tuple(x) for x in after])                     # aucun événement touché
        rows = {r['event_id']: dict(r) for r in self.desk.db.execute('SELECT * FROM audiences5688')}
        self.assertEqual(rows[active]['statut'], 'a_venir')
        self.assertEqual(rows[gone]['statut'], 'annulee')                                           # l'annulé n'est pas perdu
        self.assertEqual(rows[active]['type_audience'], 'mise_en_etat')
        self.assertEqual((rows[active]['origine'], rows[active]['propagate'], rows[active]['avocat']), ('migration', 0, ''))   # jamais copiée d'office
        self.assertEqual(rows[active]['juridiction'], '')                                           # rien de sûr : vide

    def test_juridiction_reprise_seulement_si_sure(self):
        from agent import modele5651
        modele5651.ensure_schema(self.desk)
        self.assertEqual(au.fiche_sure(self.desk, MID)['juridiction'], '')
        self.desk.db.execute("INSERT INTO d51_procedures(id,matter,juridiction,chambre,rg,statut,origin,created,updated) VALUES('p0',?,'Tribunal déduit','','','active','fiche','x','x')", (MID,))
        self.assertEqual(au.fiche_sure(self.desk, MID)['juridiction'], '')                          # déduite d'une analyse : pas sûre
        modele5651.save_procedure(self.desk, MID, LAWYER, {'juridiction': 'Tribunal judiciaire de Valbrune', 'chambre': '3e', 'rg': '24/01234'})
        sure = au.fiche_sure(self.desk, MID)
        self.assertEqual((sure['juridiction'], sure['rg'], sure['source']), ('Tribunal judiciaire de Valbrune', '24/01234', 'procedure_avocat'))
        ident = ag.create(self.desk, {'title': 'Audience', 'kind': 'audience', 'start': later(), 'matter': MID})['id']
        self.assertEqual(au.by_event(self.desk, ident)['juridiction'], 'Tribunal judiciaire de Valbrune')
        modele5651.save_procedure(self.desk, MID, LAWYER, {'juridiction': 'Cour d’appel de Valbrune', 'rg': '25/00001'})
        self.assertEqual(au.fiche_sure(self.desk, MID)['juridiction'], '')                          # deux juridictions : rien n'est deviné

    def test_audiences_nextcloud_lues_sans_doublon_et_ecartables(self):
        start = later(10)
        row = ("INSERT INTO calendar_cache VALUES(?,?,'','https://cloud.example.test/cal/','','',?,'','Salle 4',?,?,1,?,'[]',?)")
        self.desk.db.execute(row, ('c1', 'nc-1', 'Audience de plaidoiries ZORVAL', start, later(10, 10), MID, self.desk.now()))
        self.desk.db.execute(row, ('c2', 'nc-2', 'Déjeuner avec KERVILAC', start, later(10, 10), '', self.desk.now()))
        self.desk.db.commit()
        self.assertEqual(au.sync_nextcloud(self.desk), 1)
        self.assertEqual(au.sync_nextcloud(self.desk), 0)
        rows = au.listing(self.desk)
        self.assertEqual([(r['title'], r['lecture_seule'], r['propagate']) for r in rows], [('Audience de plaidoiries ZORVAL', True, 0)])
        au.save(self.desk, {'id': rows[0]['id'], 'statut': 'ecartee'}, LAWYER)
        au.sync_nextcloud(self.desk)
        self.assertEqual(au.listing(self.desk), [])                                                 # un faux positif ne revient pas

    def test_liste_filtres_avocat_periode_et_mention_sans_avocat(self):
        one = self.hearing(start=later(5))
        two = self.hearing(start=later(40), avocat=COLLEAGUE)
        three = self.hearing(start=later(6), avocat='')
        ids = lambda **k: [x['id'] for x in au.listing(self.desk, **k)]   # noqa: E731
        self.assertEqual(ids(), [one, three, two])
        self.assertEqual(ids(avocat=COLLEAGUE), [two])
        self.assertEqual(ids(avocat=au.SANS), [three])
        self.assertEqual(ids(start=(date.today() + timedelta(days=30)).isoformat()), [two])
        self.assertEqual(ids(end=(date.today() + timedelta(days=10)).isoformat()), [one, three])
        html = agenda5688.suivi_html(self.desk, {}, '/p', {}, 'avocat')
        self.assertIn('Sans avocat désigné', html)
        self.assertIn('Me MARTIN', html)
        detail = agenda5688.suivi_html(self.desk, {}, '/p', {'id': three}, 'avocat')
        self.assertIn('Sans avocat désigné', detail)
        self.assertNotIn('<script', detail)

    def test_page_echappe_le_contenu(self):
        ident = self.hearing(juridiction='<img src=x>', notes='<script>x</script>')
        html = agenda5688.detail_html(self.desk, {}, '/p', ident, 'avocat')
        self.assertNotIn('<img src=x>', html)
        self.assertNotIn('<script>x', html)

    def test_correction_de_la_fiche_ne_deplace_pas_l_audience(self):
        ident = self.hearing(start=later(12, 14), duration_minutes=90)
        before = au.get(self.desk, ident)
        html = agenda5688._timing_fields(self.desk, before)
        start = re.search(r'name="start" value="([^"]+)"', html).group(1)
        minutes = re.search(r'name="duration_minutes"[^>]*value="(\d+)"', html).group(1)
        au.save(self.desk, {'id': ident, 'start': start, 'duration_minutes': minutes, 'location': '', 'chambre': '1re'}, LAWYER)
        after = au.get(self.desk, ident)
        self.assertEqual((after['starts'], after['ends']), (before['starts'], before['ends']))

    def test_version_perimee_refusee(self):
        ident = self.hearing()
        version = au.get(self.desk, ident)['version']
        au.save(self.desk, {'id': ident, 'chambre': '1re', 'version': version}, LAWYER)
        with self.assertRaisesRegex(Stop, 'audience_modifiee_entre_temps'):
            au.save(self.desk, {'id': ident, 'chambre': '2e', 'version': version}, LAWYER)

    def test_avocat_inconnu_refuse(self):
        with self.assertRaisesRegex(Stop, 'avocat_inconnu'):
            self.hearing(avocat='inconnu@example.test')

    def test_dossier_reserve(self):
        ident = self.hearing(avocat=COLLEAGUE)
        self.reserve(MID, [COLLEAGUE])
        membres5650.set_viewer({MID})
        with self.assertRaisesRegex(Stop, 'dossier_reserve'):
            au.get(self.desk, ident)
        with self.assertRaisesRegex(Stop, 'dossier_reserve'):
            au.save(self.desk, {'id': ident, 'chambre': 'x'}, LAWYER)
        self.assertEqual(au.listing(self.desk), [])
        membres5650.clear()
        with self.assertRaisesRegex(Stop, 'personne_sans_acces_au_dossier'):
            au.save(self.desk, {'id': ident, 'avocat': LAWYER}, COLLEAGUE)

    def test_un_modele_ne_saisit_pas_d_audience(self):
        with self.assertRaisesRegex(Stop, 'audience_reservee_aux_personnes'):
            au.save(self.desk, {'start': later()}, 'agent')


# ------------------------------------------------------------------------------------------------ services simulés
class FakeGraph:
    """Simule /me/events : étiquette par version, If-Match, transactionId."""

    def __init__(self):
        self.events, self.calls, self.fail, self.by_tx = {}, [], [], {}
        self.drop_response = False

    def _dump(self, ident):
        return json.dumps(self.events[ident]).encode()

    def _tag(self, ev):
        ev['@odata.etag'] = 'W/"%d"' % (int(re.search(r'\d+', ev.get('@odata.etag', '0')).group()) + 1)

    def edit(self, ident, **changes):
        self.events[ident].update(changes)
        self._tag(self.events[ident])

    def request(self, method, url, data=None, headers=None, limit=0):
        self.calls.append((method, url, json.loads(data) if data else None, dict(headers or {})))
        if self.fail:
            raise self.fail.pop(0)
        path = urlsplit(url).path
        if method == 'POST' and path == '/v1.0/me/events':
            body = json.loads(data)
            tx = body.get('transactionId')
            if tx in self.by_tx:
                return self._dump(self.by_tx[tx])
            ident = 'graph-%d' % (len(self.events) + 1)
            self.events[ident] = {**body, 'id': ident, 'iCalUId': 'ical-' + ident, '@odata.etag': 'W/"1"', 'isCancelled': False}
            self.by_tx[tx] = ident
            if self.drop_response:
                self.drop_response = False
                raise Stop('delai_http_depasse')                                                    # créé là-bas, réponse perdue
            return self._dump(ident)
        ident = path.rsplit('/', 1)[-1]
        if ident not in self.events:
            raise http_error(404, method, url)
        if method == 'GET':
            return self._dump(ident)
        match = (headers or {}).get('If-Match')
        if match and match != self.events[ident]['@odata.etag']:
            raise http_error(412, method, url)
        if method == 'PATCH':
            self.events[ident].update(json.loads(data))
            self._tag(self.events[ident])
            return self._dump(ident)
        if method == 'DELETE':
            del self.events[ident]
            return b''
        raise AssertionError(method)

    def writes(self):
        return [c for c in self.calls if c[0] != 'GET']


class FakeGoogle:
    def __init__(self):
        self.events, self.calls, self.fail = {}, [], []

    def __call__(self, origin):
        return self

    def edit(self, ident, **changes):
        self.events[ident].update(changes)
        self.events[ident]['etag'] = '"%d"' % (int(self.events[ident]['etag'].strip('"')) + 1)

    def request(self, method, url, data=None, headers=None, limit=0):
        self.calls.append((method, url, json.loads(data) if data else None, dict(headers or {})))
        if self.fail:
            raise self.fail.pop(0)
        path = urlsplit(url).path
        if 'userinfo' in url:
            return json.dumps({'email': 'cabinet@example.test'}).encode()
        if method == 'POST' and path.endswith('/events'):
            body = json.loads(data)
            if body['id'] in self.events:
                raise http_error(409, method, url)
            self.events[body['id']] = {**body, 'etag': '"1"', 'status': 'confirmed', 'iCalUID': body['id'] + '@google'}
            return json.dumps(self.events[body['id']]).encode()
        ident = path.rsplit('/', 1)[-1]
        if ident == 'events' and method == 'GET':
            return json.dumps({'items': []}).encode()
        if ident not in self.events:
            raise http_error(404, method, url)
        if method == 'GET':
            return json.dumps(self.events[ident]).encode()
        match = (headers or {}).get('If-Match')
        if match and match != self.events[ident]['etag']:
            raise http_error(412, method, url)
        if method == 'PATCH':
            self.events[ident].update(json.loads(data))
            self.events[ident]['etag'] = '"%d"' % (int(self.events[ident]['etag'].strip('"')) + 1)
            return json.dumps(self.events[ident]).encode()
        if method == 'DELETE':
            del self.events[ident]
            return b''
        raise AssertionError(method)


class Remote(Base):
    def setUp(self):
        super().setUp()
        self.graph = FakeGraph()
        ms.save_config(self.desk, {'client_id': '11111111-2222-3333-4444-555555555555', 'tenant': 'common', 'push': True, 'pull': True})
        ms._store(self.desk, {'access_token': 'jeton-fictif', 'refresh_token': 'renouvellement-fictif', 'expires_in': 3600})
        cfg = ms.config(self.desk)
        cfg['account'] = 'cabinet@example.test'
        self.desk.setting(ms.CONFIG, cfg)
        self.svc = pr.services(self.desk, self.graph)
        self.svc.pop('google')

    def run_copies(self):
        return pr.run(self.desk, self.svc)

    def row(self, ident, provider='outlook'):
        h = au.get(self.desk, ident)
        return dict(self.desk.db.execute('SELECT * FROM agenda_distant5688 WHERE ref=? AND provider=?', (pr.ref_of(h), provider)).fetchone())

    def retry_now(self):
        self.desk.db.execute("UPDATE agenda_distant5688 SET next_try=''")
        self.desk.db.commit()


class Outlook(Remote):
    def test_creation_mise_a_jour_annulation(self):
        ident = self.hearing()
        self.assertEqual(self.run_copies(), {'envoye': 1})
        row = self.row(ident)
        self.assertEqual(row['remote_id'], 'graph-1')
        sent = self.graph.events['graph-1']
        self.assertEqual(sent['categories'], ['AxiorHub'])
        self.assertNotIn('attendees', sent)                                                         # aucun invité
        h = au.get(self.desk, ident)
        ag.update(self.desk, {'id': h['event_id'], 'start': later(21, 15)})
        self.assertEqual(self.run_copies(), {'envoye': 1})
        self.assertEqual(self.row(ident)['remote_id'], 'graph-1')                                   # identifiant conservé
        self.assertEqual(len(self.graph.events), 1)                                                 # pas de doublon
        self.assertEqual(self.graph.events['graph-1']['start']['dateTime'][:13], later(21, 15)[:13])
        ag.cancel(self.desk, h['event_id'])
        self.assertEqual(self.run_copies(), {'annule': 1})
        self.assertEqual(self.graph.events, {})
        self.assertEqual(self.row(ident)['state'], 'annule')

    def test_intitule_neutre_par_defaut_notes_jamais_transmises(self):
        ident = self.hearing(notes='Stratégie confidentielle ZORVAL')
        self.run_copies()
        sent = json.dumps(self.graph.events['graph-1'], ensure_ascii=False)
        self.assertIn('Audience AxiorHub · ' + MID, sent)
        for secret in ('Valbrune', '24/01234', 'ZORVAL'):
            self.assertNotIn(secret, sent)
        pr.set_details(self.desk, True, LAWYER)
        ag.update(self.desk, {'id': au.get(self.desk, ident)['event_id'], 'title': 'Audience Valbrune'})
        self.run_copies()
        sent = json.dumps(self.graph.events['graph-1'], ensure_ascii=False)
        self.assertIn('24/01234', sent)
        self.assertNotIn('ZORVAL', sent)

    def test_reprise_apres_erreur_sans_doublon(self):
        ident = self.hearing()
        self.graph.drop_response = True                                                             # créé chez Microsoft, réponse perdue
        self.assertEqual(self.run_copies(), {'reessai': 1})
        row = self.row(ident)
        self.assertEqual((row['state'], row['remote_id'], row['attempts']), ('a_envoyer', '', 1))
        self.assertEqual(self.run_copies(), {})                                                     # délai d'attente respecté
        self.retry_now()
        self.assertEqual(self.run_copies(), {'envoye': 1})
        self.assertEqual(len(self.graph.events), 1)
        self.assertEqual(self.row(ident)['remote_id'], 'graph-1')

    def test_erreurs_repetees_visibles_puis_relance_manuelle(self):
        ident = self.hearing()
        for _ in range(pr.MAX_ATTEMPTS):
            self.graph.fail.append(http_error(503, 'POST', 'https://graph.microsoft.com/v1.0/me/events'))
            self.retry_now()
            self.run_copies()
        self.assertEqual(self.row(ident)['state'], 'erreur')
        self.assertIn('relancer', agenda5688._distant_html(self.desk, au.get(self.desk, ident)).lower())
        pr.resolve(self.desk, ident, 'outlook', 'reprendre', LAWYER)
        self.assertEqual(self.run_copies(), {'envoye': 1})

    def test_modification_distante_jamais_ecrasee(self):
        ident = self.hearing()
        self.run_copies()
        self.graph.edit('graph-1', start={'dateTime': later(22, 11)[:19], 'timeZone': 'UTC'})
        ag.update(self.desk, {'id': au.get(self.desk, ident)['event_id'], 'start': later(23, 15)})
        writes = len(self.graph.writes())
        self.assertEqual(self.run_copies(), {'conflit': 1})
        self.assertEqual(len(self.graph.writes()), writes)                                          # aucune écriture
        self.assertEqual(self.graph.events['graph-1']['start']['dateTime'][:13], later(22, 11)[:13])
        html = agenda5688._distant_html(self.desk, au.get(self.desk, ident))
        self.assertIn('Garder la version d’AxiorHub', html)
        self.assertIn('Reprendre la version distante', html)
        self.assertEqual(len(rp.unread(self.desk, LAWYER)), 1)                                      # l'avocat est prévenu
        ag.update(self.desk, {'id': au.get(self.desk, ident)['event_id'], 'title': 'Autre intitulé'})
        self.assertEqual(self.row(ident)['state'], 'conflit')                                       # une modification locale ne tranche pas
        self.assertEqual(self.run_copies(), {})

    def test_choix_garder_axiorhub(self):
        ident = self.hearing()
        self.run_copies()
        self.graph.edit('graph-1', subject='Renommé dans Outlook')
        pr.verify(self.desk, self.svc)
        pr.resolve(self.desk, ident, 'outlook', 'garder_axiorhub', LAWYER)
        self.assertEqual(self.run_copies(), {'envoye': 1})
        self.assertEqual(self.graph.events['graph-1']['subject'], 'Audience AxiorHub · ' + MID)

    def test_choix_reprendre_le_distant(self):
        ident = self.hearing()
        self.run_copies()
        moved = later(30, 16)
        self.graph.edit('graph-1', start={'dateTime': moved[:19], 'timeZone': 'UTC'}, end={'dateTime': later(30, 17)[:19], 'timeZone': 'UTC'})
        pr.verify(self.desk, self.svc)
        pr.resolve(self.desk, ident, 'outlook', 'reprendre_distant', LAWYER)
        self.assertEqual(au.get(self.desk, ident)['starts'][:13], moved[:13])
        self.assertEqual(self.row(ident)['state'], 'envoye')
        self.assertEqual(self.run_copies(), {})
        self.assertEqual(pr.verify(self.desk, self.svc)['a_arbitrer'], 0)

    def test_controle_periodique_sans_modification_locale(self):
        ident = self.hearing()
        self.run_copies()
        self.assertEqual(pr.verify(self.desk, self.svc), {'verifiees': 1, 'a_arbitrer': 0})
        self.graph.edit('graph-1', categories=['AxiorHub', 'Couleur'])                              # changement sans portée : pas d'alerte
        self.assertEqual(pr.verify(self.desk, self.svc), {'verifiees': 1, 'a_arbitrer': 0})
        self.graph.edit('graph-1', subject='Renommé')
        self.assertEqual(pr.verify(self.desk, self.svc)['a_arbitrer'], 1)
        self.assertEqual(self.row(ident)['state'], 'conflit')

    def test_changement_entre_lecture_et_ecriture_refuse_par_if_match(self):
        ident = self.hearing()
        self.run_copies()
        ag.update(self.desk, {'id': au.get(self.desk, ident)['event_id'], 'start': later(24, 10)})
        original = ms.get_remote

        def racing(desk, graph_id, http=None):
            out = original(desk, graph_id, http)
            self.graph.edit('graph-1', subject='Changé pendant la copie')                           # l'étiquette lue devient périmée
            return out
        with unittest.mock.patch.object(ms, 'get_remote', racing):
            self.assertEqual(self.run_copies(), {'conflit': 1})
        self.assertEqual(self.graph.events['graph-1']['subject'], 'Changé pendant la copie')

    def test_suppression_distante_ne_recree_pas_seule(self):
        ident = self.hearing()
        self.run_copies()
        del self.graph.events['graph-1']
        ag.update(self.desk, {'id': au.get(self.desk, ident)['event_id'], 'start': later(26, 10)})
        self.assertEqual(self.run_copies(), {'supprime_distant': 1})
        self.assertEqual(self.graph.events, {})
        pr.resolve(self.desk, ident, 'outlook', 'recreer', LAWYER)
        self.assertEqual(self.run_copies(), {'envoye': 1})
        self.assertEqual(len(self.graph.events), 1)

    def test_annulation_d_une_copie_modifiee_a_arbitrer(self):
        ident = self.hearing()
        self.run_copies()
        self.graph.edit('graph-1', subject='Modifié dans Outlook')
        ag.cancel(self.desk, au.get(self.desk, ident)['event_id'])
        self.assertEqual(self.run_copies(), {'conflit': 1})
        self.assertIn('graph-1', self.graph.events)

    def test_copie_5636_adoptee_sans_doublon(self):
        ident = ag.create(self.desk, {'title': 'Audience', 'kind': 'audience', 'start': later(), 'matter': MID})['id']
        self.desk.db.execute('DELETE FROM agenda_distant5688')
        self.desk.db.commit()
        ms.schema(self.desk)
        self.graph.events['ancien'] = {'id': 'ancien', 'iCalUId': 'ical-ancien', '@odata.etag': 'W/"1"', 'subject': 'Audience', 'start': {'dateTime': later()[:19]}, 'end': {'dateTime': later()[:19]},
                                       'location': {'displayName': ''}, 'isCancelled': False}
        self.desk.db.execute("INSERT OR REPLACE INTO ms_links5636 VALUES(?,'ancien','ical-ancien','x')", (ident,))
        self.desk.db.commit()
        pr.follow(self.desk, ident)
        self.assertEqual(self.run_copies(), {'envoye': 1})
        self.assertEqual(list(self.graph.events), ['ancien'])

    def test_rendez_vous_deja_copie_suit_mise_a_jour_et_annulation(self):
        ident = ag.create(self.desk, {'title': 'Rendez-vous DURAND', 'kind': 'rendez_vous', 'start': later()})['id']
        ev = ag.public(ag._row(self.desk, ident))
        self.assertEqual(ms.push_event(self.desk, ev, http=self.graph), 'graph-1')
        ag.update(self.desk, {'id': ident, 'title': 'Rendez-vous BERNARD'})
        self.run_copies()
        self.assertEqual(self.graph.events['graph-1']['subject'], 'Rendez-vous BERNARD')
        ag.cancel(self.desk, ident)
        self.run_copies()
        self.assertEqual(self.graph.events, {})

    def test_rendez_vous_jamais_copie_reste_local(self):
        ident = ag.create(self.desk, {'title': 'Rendez-vous', 'kind': 'rendez_vous', 'start': later()})['id']
        ag.update(self.desk, {'id': ident, 'title': 'Rendez-vous déplacé'})
        self.assertEqual(self.run_copies(), {})
        self.assertEqual(self.graph.calls, [])

    def test_ms_sync_ne_double_pas_une_audience(self):
        self.hearing()
        pr.run(self.desk, self.svc)
        with unittest.mock.patch.object(ms, 'pull', return_value={'imported': 0}):
            ms.sync(self.desk, http=self.graph)
        self.assertEqual(len(self.graph.events), 1)

    def test_lecture_outlook_ne_reimporte_pas_la_copie(self):
        self.hearing()
        self.run_copies()
        links = self.desk.db.execute('SELECT local_id,graph_id FROM ms_links5636').fetchall()
        self.assertEqual([r['graph_id'] for r in links], ['graph-1'])

    def test_migration_et_nextcloud_attendent_une_decision(self):
        event = ag.create(self.desk, {'title': 'Audience ancienne', 'kind': 'audience', 'start': later()})['id']
        self.desk.db.execute('DELETE FROM audiences5688')
        self.desk.db.execute('DELETE FROM agenda_distant5688')
        self.desk.db.commit()
        au.migrate(self.desk)
        pr.run(self.desk, self.svc)
        ag.update(self.desk, {'id': event, 'title': 'Audience ancienne déplacée'})
        self.assertEqual(self.run_copies(), {})
        self.assertEqual(self.graph.calls, [])                                                      # garde-fou : rien sans décision
        au.propose_copy(self.desk, au.by_event(self.desk, event)['id'], LAWYER)
        self.assertEqual(self.run_copies(), {'envoye': 1})

    def test_import_ics_jamais_copie(self):
        ics = ('BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:audience-fictive\r\nDTSTART:%s\r\nDTEND:%s\r\nSUMMARY:Audience importée\r\nX-AXIORHUB-KIND:audience\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n' % (
            (datetime.now(timezone.utc) + timedelta(days=3)).strftime('%Y%m%dT090000Z'), (datetime.now(timezone.utc) + timedelta(days=3)).strftime('%Y%m%dT100000Z')))
        ag.import_ics(self.desk, ics, 'fictif')
        self.assertEqual(self.run_copies(), {})
        self.assertEqual(self.graph.calls, [])

    def test_reglage_retire_rien_ne_part(self):
        ident = self.hearing()
        ms.save_config(self.desk, {'client_id': '11111111-2222-3333-4444-555555555555', 'tenant': 'common', 'push': False, 'pull': True})
        self.assertEqual(self.run_copies(), {'reessai': 1})
        self.assertEqual(self.graph.calls, [])
        self.assertEqual(self.row(ident)['error'], 'agenda_distant_non_relie')

    def test_dossier_exclu_ou_reserve_ne_part_pas(self):
        self.reserve(MID, [LAWYER])
        ident = self.hearing()
        self.run_copies()
        self.assertEqual(self.graph.calls, [])
        self.assertEqual(self.row(ident)['state'], 'exclu')
        membres5650.set_reserve(self.desk.c['state_dir'], MID, [])
        self.desk.c['hybrid_routing'] = {'excluded_matters': [MID]}
        self.desk.db.execute('DELETE FROM agenda_distant5688')
        self.desk.db.commit()
        other = self.hearing()
        self.run_copies()
        self.assertEqual(self.graph.calls, [])
        self.assertEqual(self.row(other)['state'], 'exclu')

    def test_changement_pendant_la_copie_repart(self):
        ident = self.hearing()
        original = ms.create_remote

        def racing(desk, body, http=None):
            out = original(desk, body, http)
            ag.update(self.desk, {'id': au.get(self.desk, ident)['event_id'], 'title': 'Modifiée pendant l’envoi'})
            return out
        with unittest.mock.patch.object(ms, 'create_remote', racing):
            self.run_copies()
        row = self.row(ident)
        self.assertEqual((row['state'], row['remote_id']), ('a_envoyer', 'graph-1'))

    def test_travail_et_planification(self):
        self.hearing()
        kinds = [r[0] for r in self.desk.db.execute("SELECT kind FROM jobs WHERE status='pending'")]
        self.assertIn('propagation5688', kinds)
        self.desk.setting('propagation5688:last', 0)
        self.desk.db.execute("DELETE FROM jobs WHERE kind='propagation5688'")
        self.desk.db.commit()
        self.assertEqual(len(pr.schedule(self.desk)), 1)
        self.assertEqual(pr.schedule(self.desk), [])                                                # au plus toutes les cinq minutes

    def test_un_echec_de_suivi_ne_bloque_pas_l_agenda(self):
        with unittest.mock.patch.object(pr, 'follow', side_effect=RuntimeError('panne')):
            out = ag.create(self.desk, {'title': 'Audience', 'kind': 'audience', 'start': later()})
        self.assertTrue(out['id'])


class Google(Base):
    def setUp(self):
        super().setUp()
        self.fake = FakeGoogle()
        p = unittest.mock.patch.object(cx, 'HTTP_FACTORY', self.fake)
        p.start()
        self.addCleanup(p.stop)
        view = cx.save(self.desk, 'cabinet', 'administrateur', {'provider': 'google_workspace', 'environment': 'test', 'client_id': 'client-fictif-abcdef', 'client_secret': 'secret-fictif-123456', 'cap_calendar_write': True})
        self.conn = view['id']
        cx._store_tokens(self.desk, view['id'], {'access_token': 'jeton-fictif', 'refresh_token': 'renouvellement-fictif', 'expires_in': 3600}, cx._row(self.desk, view['id'])['token_version'])
        self.assertTrue(cx.test(self.desk, 'cabinet', 'administrateur', view['id'])['ok'])
        self.svc = {'google': pr.Google(self.desk)}
        self.fake.calls.clear()

    def only(self):
        self.assertEqual(len(self.fake.events), 1)
        return next(iter(self.fake.events.values()))

    def test_ecritures_desactivees_par_defaut(self):
        self.assertFalse(cx.PROVIDERS['google_workspace']['caps']['calendar.write'][1] == 'lecture')
        view = cx.save(self.desk, 'cabinet', 'administrateur', {'provider': 'google_workspace', 'environment': 'production', 'client_id': 'client-fictif-abcdef', 'client_secret': 'secret-fictif-123456'})
        self.assertFalse(view['capacites']['calendar.write']['enabled'])

    def test_capacite_jamais_exposee_a_l_agent(self):
        self.assertNotIn('calendar.write', {cap for cap, _ in cx.TOOLS.values()})

    def test_portee_oauth(self):
        self.assertIn('https://www.googleapis.com/auth/calendar.events', ad.GoogleWorkspace.scopes(['calendar.write']))

    def test_creation_mise_a_jour_annulation(self):
        ident = self.hearing(avocat='')
        self.assertEqual(pr.run(self.desk, self.svc), {'envoye': 1})
        ev = self.only()
        self.assertTrue(re.fullmatch(r'[a-v0-9]{5,}', ev['id']), ev['id'])                          # alphabet imposé par Google
        self.assertNotIn('attendees', ev)
        self.assertEqual(ev['summary'], 'Audience AxiorHub · ' + MID)
        self.assertTrue(all('sendUpdates=none' in c[1] for c in self.fake.calls if c[0] != 'GET'))
        h = au.get(self.desk, ident)
        ag.update(self.desk, {'id': h['event_id'], 'start': later(21, 15)})
        self.assertEqual(pr.run(self.desk, self.svc), {'envoye': 1})
        self.assertEqual(self.only()['start']['dateTime'][:13], later(21, 15)[:13])
        ag.cancel(self.desk, h['event_id'])
        self.assertEqual(pr.run(self.desk, self.svc), {'annule': 1})
        self.assertEqual(self.fake.events, {})

    def test_reprise_409_adoptee(self):
        ident = self.hearing(avocat='')
        original = self.fake.request

        def lost(method, url, data=None, headers=None, limit=0):
            out = original(method, url, data, headers, limit)
            if method == 'POST' and not lost.done:
                lost.done = True
                raise Stop('delai_http_depasse')
            return out
        lost.done = False
        with unittest.mock.patch.object(self.fake, 'request', lost):
            self.assertEqual(pr.run(self.desk, self.svc), {'reessai': 1})
            self.desk.db.execute("UPDATE agenda_distant5688 SET next_try=''")
            self.desk.db.commit()
            self.assertEqual(pr.run(self.desk, self.svc), {'envoye': 1})
        self.assertEqual(len(self.fake.events), 1)
        self.assertEqual(pr.status(self.desk, pr.ref_of(au.get(self.desk, ident)))[0]['state'], 'envoye')

    def test_modification_distante_jamais_ecrasee(self):
        ident = self.hearing(avocat='')
        pr.run(self.desk, self.svc)
        ev = self.only()
        self.fake.edit(ev['id'], summary='Renommé dans Google')
        ag.update(self.desk, {'id': au.get(self.desk, ident)['event_id'], 'start': later(23, 15)})
        self.assertEqual(pr.run(self.desk, self.svc), {'conflit': 1})
        self.assertEqual(self.only()['summary'], 'Renommé dans Google')

    def test_changement_d_avocat_ne_deplace_pas_la_copie(self):
        ident = self.hearing(avocat='')
        pr.run(self.desk, self.svc)
        au.save(self.desk, {'id': ident, 'avocat': LAWYER}, LAWYER)
        self.assertEqual(pr.run(self.desk, self.svc), {'envoye': 1})
        self.assertEqual(len(self.fake.events), 1)

    def test_capacite_retiree_rien_ne_part(self):
        self.hearing(avocat='')
        caps = json.loads(cx._row(self.desk, self.conn)['caps_json'])
        caps['calendar.write']['enabled'] = False
        self.desk.db.execute('UPDATE connexions5652 SET caps_json=? WHERE id=?', (json.dumps(caps), self.conn))
        self.desk.db.commit()
        self.assertEqual(pr.run(self.desk, self.svc), {'reessai': 1})
        self.assertEqual(self.fake.events, {})


# ------------------------------------------------------------------------------------------------ rappels
class Reminders(Base):
    def at(self, moment):
        return moment.timestamp()

    def hearing_in(self, days, **data):
        return self.hearing(start=(datetime.now(timezone.utc) + timedelta(days=days, hours=2)).replace(microsecond=0).isoformat(), **data)

    def mail(self):
        calls = []

        def sender(to, subject, body):
            calls.append((to, subject, body))
            return True
        return calls, sender

    def test_rappels_j7_j1_a_l_avocat_sans_doublon(self):
        self.hearing_in(6, reminders=[])
        out = rp.tick(self.desk)
        self.assertEqual(out['rappels'], 1)
        self.assertEqual(rp.tick(self.desk)['rappels'], 0)                                          # une seule fois
        rows = rp.unread(self.desk, LAWYER)
        self.assertEqual(len(rows), 1)
        self.assertIn('dans 7 jours', rows[0]['message'])
        self.assertIn('Valbrune', rows[0]['message'])
        self.assertEqual(rp.unread(self.desk, COLLEAGUE), [])
        soon = datetime.now(timezone.utc) + timedelta(days=5, hours=3)
        self.assertEqual(rp.tick(self.desk, self.at(soon))['rappels'], 1)                           # J-1
        self.assertEqual(rp.tick(self.desk, self.at(soon))['rappels'], 0)

    def test_pas_de_rafale_si_plusieurs_echeances_depassees(self):
        self.hearing_in(0, reminders=[])
        self.assertEqual(rp.tick(self.desk)['rappels'], 1)
        self.assertEqual(rp.tick(self.desk)['rappels'], 0)
        self.assertEqual(len(rp.unread(self.desk, LAWYER)), 1)

    def test_audience_deplacee_redonne_ses_rappels(self):
        ident = self.hearing_in(1, reminders=[])
        rp.tick(self.desk)
        ag.update(self.desk, {'id': au.get(self.desk, ident)['event_id'], 'start': (datetime.now(timezone.utc) + timedelta(days=4)).replace(microsecond=0).isoformat()})
        self.assertEqual(rp.tick(self.desk)['rappels'], 1)

    def test_changement_d_avocat(self):
        ident = self.hearing_in(1, reminders=[])
        rp.tick(self.desk)
        au.save(self.desk, {'id': ident, 'avocat': COLLEAGUE}, LAWYER)
        rp.tick(self.desk)
        self.assertEqual(len(rp.unread(self.desk, COLLEAGUE)), 1)

    def test_sans_avocat_va_au_cabinet_avec_mention(self):
        self.hearing_in(1, avocat='', reminders=[])
        rp.tick(self.desk)
        rows = rp.unread(self.desk, 'cabinet')
        self.assertEqual(len(rows), 1)
        self.assertIn('Sans avocat désigné', rows[0]['message'])

    def test_echeances_reglables(self):
        rp.save_cabinet(self.desk, {'audience': '14, 3', 'tache': '2'}, LAWYER)
        self.assertEqual(rp.cabinet_days(self.desk), {'audience': [14, 3], 'tache': [2]})
        self.assertEqual(rp.preferences(self.desk, LAWYER)['audience'], [14, 3])
        rp.save_preferences(self.desk, LAWYER, {'audience': '1', 'tache': ''})
        self.assertEqual(rp.preferences(self.desk, LAWYER)['audience'], [1])
        self.assertEqual(rp.preferences(self.desk, LAWYER)['tache'], [2])
        with self.assertRaisesRegex(Stop, 'echeance_rappel_invalide'):
            rp.save_cabinet(self.desk, {'audience': '5000', 'tache': '1'}, LAWYER)
        self.hearing_in(2, reminders=[])
        self.assertEqual(rp.tick(self.desk)['rappels'], 0)                                          # J-1 pour cet avocat

    def test_rappels_choisis_sur_l_evenement_honores(self):
        self.hearing_in(0, reminders=[60])
        self.desk.db.execute('UPDATE agenda5634 SET starts=?', ((datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),))
        self.desk.db.commit()
        rp.save_preferences(self.desk, LAWYER, {'audience': '1', 'tache': '1'})
        self.assertEqual(rp.tick(self.desk)['rappels'], 1)

    def test_pas_de_doublon_avec_l_agenda_local(self):
        ident = self.hearing_in(0, reminders=[60])
        self.desk.db.execute('UPDATE agenda5634 SET starts=?', ((datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),))
        self.desk.db.commit()
        ag.tick(self.desk)
        kinds = [r[0] for r in self.desk.db.execute("SELECT message FROM live_events_v430 WHERE kind='agenda'")]
        self.assertEqual(kinds, [])                                                                 # plus de notification commune en double
        rp.tick(self.desk)
        rp.tick(self.desk)
        self.assertEqual(len(rp.unread(self.desk, LAWYER)), 1)
        self.assertTrue(au.get(self.desk, ident))

    def test_ancien_rappel_deja_envoye_non_repete(self):
        ident = self.hearing_in(0, reminders=[60])
        h = au.get(self.desk, ident)
        self.desk.db.execute('UPDATE agenda5634 SET starts=?', ((datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),))
        self.desk.db.execute("INSERT INTO agenda_rappels5634 VALUES(?,60,'x')", (h['event_id'],))
        self.desk.db.commit()
        self.assertEqual(rp.tick(self.desk)['rappels'], 0)

    def test_notification_du_fil_et_dossier_reserve_non_nomme(self):
        self.hearing_in(1, reminders=[])
        rp.tick(self.desk)
        shown = self.desk.db.execute("SELECT message FROM live_events_v430 WHERE kind='agenda'").fetchall()
        self.assertIn('Valbrune', shown[0][0])
        self.reserve(MID, [LAWYER])
        self.hearing_in(0, reminders=[], juridiction='Tribunal de KERVILAC')
        rp.tick(self.desk)
        shown = [r[0] for r in self.desk.db.execute("SELECT message FROM live_events_v430 WHERE kind='agenda'")]
        self.assertEqual(len(shown), 2)
        self.assertNotIn('KERVILAC', shown[1])                                                      # garde-fou : fil commun
        self.assertTrue(any('KERVILAC' in r['message'] for r in rp.unread(self.desk, LAWYER)))

    def test_personne_sans_acces_ne_recoit_rien(self):
        self.hearing_in(1, reminders=[])
        self.reserve(MID, [COLLEAGUE])
        self.assertEqual(rp.tick(self.desk)['rappels'], 0)
        self.assertEqual(rp.unread(self.desk, LAWYER), [])

    def test_push_au_telephone_de_la_personne_seulement(self):
        self.addCleanup(setattr, mobile490, 'http_post', None)
        hits = []
        mobile490.http_post = lambda url, headers: hits.append(url) or 201
        mobile490.subscribe(self.desk, 'https://fcm.googleapis.com/fcm/send/durand', 'tel', 'yes', LAWYER)
        mobile490.subscribe(self.desk, 'https://fcm.googleapis.com/fcm/send/martin', 'tel', 'yes', COLLEAGUE)
        self.hearing_in(1, reminders=[])
        self.assertEqual(rp.tick(self.desk)['push'], 1)
        self.assertEqual(hits, ['https://fcm.googleapis.com/fcm/send/durand'])
        self.assertEqual(rp.unread(self.desk, LAWYER)[0]['push_state'], 'envoye')
        self.assertIn('1 rappel d’agenda', mobile490.summary(self.desk, owner=LAWYER)['text'])
        self.assertEqual(mobile490.summary(self.desk, owner=LAWYER)['total'], mobile490.summary(self.desk)['total'] + 1)
        self.assertNotIn('rappel', mobile490.summary(self.desk, owner=COLLEAGUE)['text'])

    def test_migration_du_jumelage(self):
        self.desk.db.execute('DROP TABLE IF EXISTS push_subs490')
        self.desk.db.execute("CREATE TABLE push_subs490(id TEXT PRIMARY KEY, endpoint TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, last_ok TEXT NOT NULL DEFAULT '')")
        self.desk.db.execute("INSERT INTO push_subs490(id,endpoint,created) VALUES('a','https://fcm.googleapis.com/x','x')")
        mobile490.ensure_schema(self.desk)
        mobile490.ensure_schema(self.desk)
        self.assertEqual(self.desk.db.execute('SELECT owner FROM push_subs490').fetchone()[0], '')

    def test_courriel_seulement_si_active_et_a_l_adresse_du_compte(self):
        calls, sender = self.mail()
        self.hearing_in(1, reminders=[])
        rp.tick(self.desk, sender=sender)
        self.assertEqual(calls, [])
        rp.save_preferences(self.desk, COLLEAGUE, {'email': True, 'push': False, 'adresse': 'tiers@example.test'})
        self.hearing_in(0, reminders=[], avocat=COLLEAGUE)
        rp.tick(self.desk, sender=sender)
        self.assertEqual([c[0] for c in calls], [COLLEAGUE])                                        # l'adresse saisie est ignorée
        rp.tick(self.desk, sender=sender)
        self.assertEqual(len(calls), 1)

    def test_compte_sans_adresse_ne_peut_pas_activer_le_courriel(self):
        with self.assertRaisesRegex(Stop, 'adresse_du_compte_absente'):
            rp.save_preferences(self.desk, 'stagiaire', {'email': True})

    def test_courriel_du_cabinet_depose_sans_envoi(self):
        self.desk.c['mail']['from_address'] = 'cabinet@example.test'
        rp.save_preferences(self.desk, 'cabinet', {'email': True, 'push': False})
        self.hearing_in(1, reminders=[], avocat='')
        box = unittest.mock.MagicMock()
        with unittest.mock.patch('agent.mailbox.Mailbox', return_value=box), unittest.mock.patch('agent.connexion5648.send_mail', side_effect=AssertionError('aucun envoi')):
            self.assertEqual(rp.tick(self.desk)['email'], 1)
        box.append_reminder.assert_called_once()
        message = box.append_reminder.call_args[0][0]
        self.assertEqual((message['From'], message['To']), ('cabinet@example.test', 'cabinet@example.test'))

    def test_autre_adresse_par_le_smtp_du_cabinet_et_reprise(self):
        rp.save_preferences(self.desk, LAWYER, {'email': True, 'push': False})
        self.hearing_in(1, reminders=[])
        with unittest.mock.patch('agent.connexion5648.send_mail', side_effect=[False, True]) as sent:
            self.assertEqual(rp.tick(self.desk)['email'], 0)
            self.assertEqual(rp.tick(self.desk)['email'], 1)                                        # retenté sans doublon de rappel
            self.assertEqual(rp.tick(self.desk)['email'], 0)
        self.assertEqual(sent.call_count, 2)
        self.assertEqual(sent.call_args[0][1], LAWYER)

    def test_tache_rappelle_son_responsable(self):
        due = (date.today() + timedelta(days=1)).isoformat()
        ta.create(self.desk, {'title': 'Relancer le confrère', 'due': due, 'responsable': COLLEAGUE, 'matter': MID}, LAWYER)
        ta.create(self.desk, {'title': 'Sans responsable', 'due': due}, LAWYER)
        self.assertEqual(rp.tick(self.desk)['rappels'], 1)
        self.assertIn('Relancer le confrère', rp.unread(self.desk, COLLEAGUE)[0]['message'])
        self.assertEqual(rp.tick(self.desk)['rappels'], 0)

    def test_rappel_propre_a_une_tache(self):
        due = (date.today() + timedelta(days=5)).isoformat()
        ta.create(self.desk, {'title': 'Tâche proche', 'due': due, 'responsable': LAWYER, 'rappel': '1'}, LAWYER)
        self.assertEqual(rp.tick(self.desk)['rappels'], 0)

    def test_marquer_lu_reservee_a_la_personne(self):
        self.hearing_in(1, reminders=[])
        rp.tick(self.desk)
        ident = rp.unread(self.desk, LAWYER)[0]['id']
        self.assertEqual(rp.mark_read(self.desk, COLLEAGUE, ident)['marked'], 0)
        self.assertEqual(rp.mark_read(self.desk, LAWYER, ident)['marked'], 1)
        self.assertEqual(rp.unread(self.desk, LAWYER), [])

    def test_retention_des_rappels(self):
        self.hearing_in(1, reminders=[])
        rp.tick(self.desk)
        self.desk.db.execute("UPDATE rappels5688 SET created='2020-01-01T00:00:00+00:00'")
        self.desk.db.commit()
        rp.tick(self.desk, (datetime.now(timezone.utc) - timedelta(days=30)).timestamp())
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM rappels5688').fetchone()[0], 0)

    def test_aucune_adresse_libre_dans_les_reglages(self):
        self.assertNotIn('name="adresse"', ta.reminders_card(self.desk, '/p', LAWYER, 'avocat'))


# ------------------------------------------------------------------------------------------------ tâches
class Tasks(Base):
    def test_colonnes_de_tasks_inchangees_ecritures_positionnelles_intactes(self):
        cols = [r[1] for r in self.desk.db.execute('PRAGMA table_info(tasks)')]
        self.assertEqual(cols, ['id', 'matter', 'title', 'due', 'status', 'created'])               # garde-fou : INSERT … VALUES à six valeurs
        self.desk.db.execute("INSERT OR REPLACE INTO tasks VALUES ('a'||'0',?,'Tâche repassée','', 'open', 'x')", (MID,))
        self.desk.db.commit()

    def test_responsable_survit_au_remplacement_de_la_ligne(self):
        out = ta.create(self.desk, {'title': 'Tâche suivie', 'responsable': LAWYER}, LAWYER)
        self.desk.db.execute("INSERT OR REPLACE INTO tasks VALUES (?,'','Tâche suivie','', 'open', 'x')", (out['id'],))
        self.desk.db.commit()
        self.assertEqual([t['responsable'] for t in ta.mine(self.desk, LAWYER)], [LAWYER])

    def test_migration(self):
        old = improvements36.create_task(self.desk, 'Tâche d’avant 5.6.88')['id']
        self.assertGreaterEqual(ta.migrate(self.desk), 1)
        self.assertEqual(ta.migrate(self.desk), 0)
        self.assertEqual(self.desk.db.execute('SELECT responsable,etat FROM taches5688 WHERE task_id=?', (old,)).fetchone()[0], '')
        self.assertEqual(len(ta.mine(self.desk, LAWYER, ta.SANS)), 1)

    def test_tache_sans_ligne_reste_visible(self):
        improvements36.create_task(self.desk, 'Tâche écrite par un autre module')
        self.assertEqual([t['statut'] for t in ta.mine(self.desk, LAWYER, 'tout')], ['a_faire'])

    def test_champs_et_statut(self):
        due = (date.today() + timedelta(days=3)).isoformat()
        ident = ta.create(self.desk, {'title': 'Préparer le bordereau', 'responsable': LAWYER, 'due': due, 'rappel': '7, 1', 'matter': MID}, LAWYER)['id']
        t = ta.mine(self.desk, LAWYER)[0]
        self.assertEqual((t['due'], ta.reminder_days(t), t['statut'], t['matter']), (due, [7, 1], 'a_faire', MID))
        ta.update(self.desk, {'id': ident, 'statut': 'en_cours', 'responsable': COLLEAGUE, 'due': ''}, LAWYER)
        t = ta.mine(self.desk, COLLEAGUE)[0]
        self.assertEqual((t['statut'], t['due']), ('en_cours', ''))
        self.assertEqual(ta.mine(self.desk, LAWYER), [])
        ta.update(self.desk, {'id': ident, 'statut': 'terminee'}, LAWYER)
        self.assertEqual(ta.mine(self.desk, COLLEAGUE, 'tout'), [])
        self.assertEqual(self.desk.db.execute('SELECT status FROM tasks WHERE id=?', (ident,)).fetchone()[0], 'closed')
        ta.update(self.desk, {'id': ident, 'statut': 'a_faire'}, LAWYER)
        self.assertEqual(len(ta.mine(self.desk, COLLEAGUE)), 1)                                     # rouverte, responsable conservé

    def test_creation_depuis_dossier_et_audience(self):
        first = ta.create(self.desk, {'title': 'Depuis le dossier', 'origine': 'dossier', 'origine_ref': MID}, LAWYER)['id']
        self.assertEqual(self.desk.db.execute('SELECT matter FROM tasks WHERE id=?', (first,)).fetchone()[0], MID)
        ident = self.hearing()
        ta.create(self.desk, {'title': 'Depuis l’audience', 'origine': 'audience', 'origine_ref': ident, 'responsable': LAWYER}, LAWYER)
        linked = au.linked_tasks(self.desk, ident)
        self.assertEqual([(t['title'], t['matter']) for t in linked], [('Depuis l’audience', MID)])
        self.assertIn('Depuis l’audience', agenda5688.detail_html(self.desk, {}, '/p', ident, 'avocat'))

    def test_creation_depuis_un_courriel_sans_proposition(self):
        key = 'a' * 64
        with unittest.mock.patch('agent.desk.report_for', return_value={'matter': MID, 'subject': 'Pièces 12 à 15'}):
            out = ta.from_mail(self.desk, {'mail_key': key, 'title': 'Traiter le courriel', 'responsable': LAWYER}, LAWYER)
        row = self.desk.db.execute('SELECT t.status,t.matter,x.origine,x.origine_ref FROM tasks t JOIN taches5688 x ON x.task_id=t.id WHERE t.id=?', (out['id'],)).fetchone()
        self.assertEqual(tuple(row), ('open', MID, 'courriel', key))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM deadline_proposals').fetchone()[0], 0)   # aucune proposition intermédiaire

    def test_courriel_d_un_dossier_reserve(self):
        self.reserve(MID, [COLLEAGUE])
        membres5650.set_viewer({MID})
        with unittest.mock.patch('agent.desk.report_for', return_value={'matter': MID}), self.assertRaisesRegex(Stop, 'dossier_reserve'):
            ta.from_mail(self.desk, {'mail_key': 'a' * 64, 'title': 'Traiter le courriel'}, LAWYER)

    def test_l_ia_reste_une_proposition(self):
        with self.assertRaisesRegex(Stop, 'tache_reservee_aux_personnes'):
            ta.create(self.desk, {'title': 'Proposée par le modèle'}, 'agent')
        import inspect
        from agent import intelligence
        self.assertIn("args.get('confirm')!='yes'", inspect.getsource(intelligence.confirm_deadline))
        self.assertNotIn('calendar.write', {cap for cap, _ in cx.TOOLS.values()})

    def test_dossier_reserve_lecture_et_ecriture(self):
        ident = ta.create(self.desk, {'title': 'Tâche réservée', 'matter': MID, 'responsable': COLLEAGUE}, COLLEAGUE)['id']
        self.reserve(MID, [COLLEAGUE])
        membres5650.set_viewer({MID})
        self.assertEqual(ta.mine(self.desk, COLLEAGUE, 'tout'), [])
        with self.assertRaisesRegex(Stop, 'dossier_reserve'):
            ta.update(self.desk, {'id': ident, 'statut': 'terminee'}, LAWYER)
        with self.assertRaisesRegex(Stop, 'dossier_reserve'):
            ta.create(self.desk, {'title': 'Autre tâche réservée', 'matter': MID}, LAWYER)
        with self.assertRaisesRegex(Stop, 'dossier_reserve'):
            ta.create(self.desk, {'title': 'Autre tâche réservée', 'origine': 'dossier', 'origine_ref': MID}, LAWYER)
        membres5650.clear()
        with self.assertRaisesRegex(Stop, 'personne_sans_acces_au_dossier'):
            ta.update(self.desk, {'id': ident, 'responsable': LAWYER}, COLLEAGUE)

    def test_tache_nextcloud_garde_sa_liste(self):
        cols = [r for r in self.desk.db.execute('PRAGMA table_info(work_tasks_v211)')]
        values = {r[1]: ('' if 'TEXT' in r[2].upper() else 0) for r in cols if r[3]}
        values.update(id='ext1', matter=MID, title='Tâche Nextcloud', status='todo', external_uid='nc-1')
        self.desk.db.execute('INSERT INTO work_tasks_v211(%s) VALUES(%s)' % (','.join(values), ','.join('?' for _ in values)), tuple(values.values()))
        self.desk.db.commit()
        ta.update(self.desk, {'id': 'ext1', 'responsable': LAWYER}, LAWYER)
        self.assertEqual([t['title'] for t in ta.mine(self.desk, LAWYER)], ['Tâche Nextcloud'])
        with self.assertRaisesRegex(Stop, 'tache_nextcloud_a_clore_dans_sa_liste'):
            ta.update(self.desk, {'id': 'ext1', 'statut': 'terminee'}, LAWYER)

    def test_page_mes_taches(self):
        ta.create(self.desk, {'title': 'Tâche <b>fictive</b>', 'responsable': LAWYER}, LAWYER)
        html = ta.page_html(self.desk, {}, '/p', {}, LAWYER, 'avocat')
        self.assertIn('Mes tâches', html)
        self.assertIn('Tâche &lt;b&gt;fictive', html)
        self.assertIn('Créer la tâche', html)
        self.assertIn('Enregistrer mes rappels', html)
        self.assertNotIn('Échéances du cabinet', ta.page_html(self.desk, {}, '/p', {}, LAWYER, 'assistant'))
        self.assertNotIn('<script', html)

    def test_vue_taches_existante_intacte(self):
        ta.create(self.desk, {'title': 'Tâche visible partout'}, LAWYER)
        self.assertEqual(len(taches5628.collect(self.desk)), 1)

    def test_formulaire_du_dossier(self):
        from agent import dossier5624
        html = dossier5624._add_task5688(self.desk, '/p', MID)
        self.assertIn('name="origine" value="dossier"', html)


# ------------------------------------------------------------------------------------------------ liste du matin
class Morning(Base):
    def today_at(self, hour=9):
        tz = ag._tz(self.desk)
        return datetime.combine(datetime.now(tz).date(), datetime.min.time(), tz).replace(hour=hour).isoformat()

    def test_liste_par_personne(self):
        self.hearing(start=self.today_at(), juridiction='TJ Valbrune')
        self.hearing(start=self.today_at(14), avocat=COLLEAGUE)
        self.hearing(start=self.today_at(16), avocat='')
        self.hearing(start=later(5))
        today = date.today()
        ta.create(self.desk, {'title': 'Tâche du jour', 'due': today.isoformat(), 'responsable': LAWYER}, LAWYER)
        ta.create(self.desk, {'title': 'Tâche en retard', 'due': (today - timedelta(days=2)).isoformat(), 'responsable': LAWYER}, LAWYER)
        ta.create(self.desk, {'title': 'Tâche de Me MARTIN', 'due': today.isoformat(), 'responsable': COLLEAGUE}, LAWYER)
        ta.create(self.desk, {'title': 'Tâche de demain', 'due': (today + timedelta(days=1)).isoformat(), 'responsable': LAWYER}, LAWYER)
        data = matin5688.build(self.desk, LAWYER)
        self.assertEqual(len(data['audiences']), 2)                                                 # la sienne et celle sans avocat
        self.assertEqual([t['title'] for t in data['taches']], ['Tâche en retard', 'Tâche du jour'])
        html = matin5688.html(self.desk, '/p', LAWYER)
        self.assertIn('Ma liste du jour', html)
        self.assertIn('Sans avocat désigné', html)
        self.assertIn('en retard', html)
        self.assertNotIn('Tâche de Me MARTIN', html)

    def test_echeances_du_jour(self):
        from agent import echeances450 as ech
        ech.ensure_schema(self.desk)
        self.desk.db.execute("INSERT INTO deadlines450(id,matter,rule_id,start_event,start_date,due,status,origin,note,created,updated) VALUES('d1',?,'r','e','2026-01-01',?,'confirmee','main','Conclusions à déposer','x','x')", (MID, date.today().isoformat()))
        self.desk.db.commit()
        self.assertEqual([d['matter'] for d in matin5688.build(self.desk, LAWYER)['echeances']], [MID])
        self.assertIn('Conclusions à déposer', matin5688.html(self.desk, '/p', LAWYER))
        self.assertEqual(matin5688.build(self.desk, LAWYER, hidden=frozenset({MID}))['echeances'], [])

    def test_rien_pour_aujourd_hui(self):
        self.assertIn('Ni audience, ni tâche', matin5688.html(self.desk, '/p', LAWYER))

    def test_dossier_reserve_retire(self):
        self.hearing(start=self.today_at())
        self.assertEqual(matin5688.build(self.desk, LAWYER, hidden=frozenset({MID}))['audiences'], [])

    def test_sans_appel_au_modele(self):
        self.hearing(start=self.today_at())
        with unittest.mock.patch.object(routines520, 'ask', side_effect=AssertionError('aucun appel')), unittest.mock.patch('agent.model.Model', side_effect=AssertionError('aucun appel')):
            self.assertIn('Ma liste du jour', matin5688.html(self.desk, '/p', LAWYER))

    def test_aujourd_hui_et_briefing(self):
        from agent import cockpit530
        self.hearing(start=self.today_at(), avocat='', juridiction='Tribunal de ORLANDEC')
        html = cockpit530.routines_html(self.desk, '/p', LAWYER)
        self.assertIn('Tribunal', matin5688.html(self.desk, '/p', LAWYER) + html)
        self.assertIn('Ma liste du jour', html.split('data-panel="briefing"', 1)[1])
        with unittest.mock.patch.object(cockpit530, '_matin5688', side_effect=lambda d, p, o: '<i>LISTE-%s</i>' % o):
            page = cockpit530.page(self.desk, '/p', owner=LAWYER)
        self.assertIn('LISTE-' + LAWYER, page)

    def test_aujourd_hui_s_affiche_meme_en_panne(self):
        from agent import cockpit530
        with unittest.mock.patch.object(matin5688, 'html', side_effect=RuntimeError('panne')):
            self.assertEqual(cockpit530._matin5688(self.desk, '/p', LAWYER), '')


# ------------------------------------------------------------------------------------------------ routes et menus
class Routes(Base):
    def api(self, route, data=None):
        import test_v5636 as t36
        return t36.api(self, 'm5634/' + route, data)

    def test_parcours_par_l_interface(self):
        out = self.api('agenda/audience/enregistrer', {'start': later(), 'matter': MID, 'avocat': '', 'juridiction': 'Tribunal de Valbrune', 'duration_minutes': '60'})
        self.assertTrue(out['status'].startswith('200'), out['body'])
        self.assertIn('Sans avocat désigné', out['json']['message'])
        listed = self.api('agenda/audience/liste?avocat=sans')
        self.assertEqual(len(listed['json']['audiences']), 1)
        task = self.api('agenda/tache/creer', {'title': 'Tâche par l’interface', 'matter': MID})
        self.assertTrue(task['status'].startswith('200'), task['body'])
        self.assertIn('Suivi des audiences', self.request('/audiences', query='onglet=suivi')['body'])
        self.assertIn('Enregistrer mes rappels', self.request('/mes-taches')['body'])

    def test_courriel_et_aujourd_hui_par_l_interface(self):
        from pathlib import Path
        key = 'b' * 64
        folder = Path(self.desk.c['state_dir']) / 'reports'
        folder.mkdir(exist_ok=True)
        (folder / (key + '.json')).write_text(json.dumps({'subject': 'Pièces <12>', 'sender': 'confrere@example.test', 'matter': MID}), encoding='utf-8')
        page = self.request('/mail', query='key=' + key)
        self.assertTrue(page['status'].startswith('200'), page['body'][:300])
        self.assertIn('Créer une tâche depuis ce courriel', page['body'])
        self.assertIn('Traiter : Pièces &lt;12&gt;', page['body'])
        self.assertIn('Créer une tâche depuis ce courriel', self.request('/mes-taches', query='courriel=' + key)['body'])
        out = self.api('agenda/tache/courriel', {'mail_key': key, 'title': 'Traiter les pièces'})
        self.assertTrue(out['status'].startswith('200'), out['body'])
        self.assertEqual(self.desk.db.execute('SELECT matter FROM tasks WHERE title=?', ('Traiter les pièces',)).fetchone()[0], MID)
        self.assertIn('id="t88-matin"', self.request('/aujourdhui')['body'])

    def test_droit_agenda_et_categorie(self):
        for route in ('agenda/audience/enregistrer', 'agenda/tache/creer', 'agenda/rappel/lu', 'agenda/distant/arbitrer'):
            self.assertEqual(membres5650.category('POST', '/api440/m5634/' + route), 'agenda', route)

    def test_reglages_du_cabinet_reserves(self):
        for route in ('agenda/rappel/cabinet', 'agenda/distant/detail'):
            with self.assertRaisesRegex(Stop, 'role_insuffisant'):
                agenda5688.api(self.desk, route, {}, 'POST', 'assistant@example.test', 'assistant')

    def test_menu_et_aide(self):
        from agent import aide5631, fonctions5631, fonctions5634, navigation5643
        ids = {f[0] for f in fonctions5631.FUNCTIONS}
        self.assertTrue({'mes_taches', 'audiences_suivi'} <= ids)
        self.assertEqual(navigation5643.PARENT['mes_taches'], 'planning')                           # onglet d'Agenda et tâches, retrouvé aussi par la recherche
        self.assertIn('mes_taches', fonctions5634.MIGRATION)
        for lang in ('fr', 'en', 'de', 'es'):
            self.assertIn('mes_taches', aide5631.articles(lang))
        self.assertEqual(aide5631.check(), [])

    def test_surveillance(self):
        self.hearing(start=(datetime.now(timezone.utc) + timedelta(hours=20)).isoformat())
        self.assertGreaterEqual(agenda5688.tick(self.desk)['rappels'], 1)
        self.assertEqual(agenda5688.tick(self.desk)['rappels'], 0)

    def test_rappels_nominatifs_hors_du_relais_commun(self):
        self.assertNotIn('agenda', reminders520.KINDS)                                              # rappels nominatifs : jamais recopiés vers la boîte commune

