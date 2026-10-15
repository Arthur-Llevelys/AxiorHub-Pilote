"""5.6.51 : contre-tests de l'audit 5.6.50 (B01 à B05) et variantes de recette de l'audit, sans service externe ni dossier réel.

Les contre-tests B01 à B05 reprennent ceux de l'audit (fichier contre_tests_5650.py) ; les variantes couvrent les cas de recette décrits
pour chaque bug. Données fictives (domaine example.test).
"""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from agent import correspondances5643 as archives
from agent import dossier5644, juridique5649 as jr, membres5650 as mb, messagerie5650 as mx
from agent.common import Stop
from agent.dav import LocalFolder
from agent.desk import Desk
import test_v5643 as t43
import test_v5650 as t50


class Web(t50.ServerAccounts):
    """Application réelle (version serveur) sans reprendre les essais de la 5.6.50."""
    test_messaging_between_accounts = None
    test_rights_page_and_restriction_by_the_administrator = None
    test_reserved_matter_in_the_real_application = None


# ---------------------------------------------------------------------------------------------------- B01 : objets indirects
class ReservedMatterObjects(Web):
    """B01 : un objet rattaché à un dossier réservé (lien d'archive, demande, mission, chemin) ne contourne pas la réservation."""

    def setUp(self):
        super().setUp()
        self.admin = self.admin_cookie()
        self.lawyer = self.add(self.admin, 'autre@example.test', 'avocat')
        self.allowed = self.add(self.admin, 'avocate@example.test', 'avocat')
        mb.clear()
        self.addCleanup(mb.clear)

    def archive(self):
        desk = Desk(self.f.c)
        self.addCleanup(desk.db.close)
        raw = t43._raw_message(subject='Courriel confidentiel fictif')
        box = t43._RawBox({'734': raw})
        prop = archives.prepare(desk, 'cabinet', {'matter_id': 'DOS-001', 'actions': ['archive_email'],
                                                  'source': {'folder': 'INBOX', 'uid': '734', 'uidvalidity': box.validity}}, box)
        result = archives.confirm(desk, 'cabinet', {'proposal_id': prop['proposal_id'], 'expected_version': 1, 'request_key': 'audit5651-archive'})
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / 'Dossiers/DEMO').mkdir(parents=True)
        client = LocalFolder({'local_path': str(root), 'roots': ['/Dossiers']})
        archives.run_archive_email(desk, {'link_id': result['link_id']}, box, client)
        return result['link_id'], client

    def test_B01_archive_link_original_status_and_detail(self):
        link, client = self.archive()
        mb.set_reserve(str(self.state), 'DOS-001', ['avocate@example.test'])
        with patch('agent.correspondances5643._client', return_value=client):
            for route in ('original', 'etat', 'detail'):
                refused = self.request('/api440/m5643/correspondances/' + route, query='lien=' + link, cookie=self.lawyer)
                self.assertIn(refused['status'], ('403 Forbidden', '404 Not Found'), route)
                self.assertNotIn('Courriel confidentiel fictif', refused['body'], route)          # ni contenu ni métadonnées
            for cookie in (self.allowed, self.admin):
                ok = self.request('/api440/m5643/correspondances/original', query='lien=' + link, cookie=cookie)
                self.assertEqual(ok['status'], '200 OK')
        refused = self.api('m5643/correspondances/annoter', {'lien': link, 'link_id': link, 'note': 'x'}, self.lawyer)
        self.assertTrue(refused['status'].startswith('403'), refused['body'])

    def test_B01_link_kept_before_the_reservation_is_refused_afterwards(self):
        link, client = self.archive()
        with patch('agent.correspondances5643._client', return_value=client):
            self.assertEqual(self.request('/api440/m5643/correspondances/original', query='lien=' + link, cookie=self.lawyer)['status'], '200 OK')
            mb.set_reserve(str(self.state), 'DOS-001', ['avocate@example.test'])
            self.assertIn(self.request('/api440/m5643/correspondances/original', query='lien=' + link, cookie=self.lawyer)['status'],
                          ('403 Forbidden', '404 Not Found'))

    def test_B01_other_indirect_identifiers(self):
        desk = Desk(self.f.c)
        self.addCleanup(desk.db.close)
        from agent import docrequest520 as dr
        rid = dr.submit(desk, 'Prépare une note fictive sur le dossier', 'DOS-001', 'note')['request']
        mb.set_reserve(str(self.state), 'DOS-001', ['avocate@example.test'])
        for query in ('request=' + rid, 'path=/Dossiers/DEMO/etat.txt', 'chemin=/Dossiers/DEMO'):
            r = self.request('/documents', query=query, cookie=self.lawyer)
            self.assertEqual(r['status'], '403 Forbidden', query)
        self.assertTrue(self.request('/documents', query='request=' + rid, cookie=self.allowed)['status'].startswith('200'))
        r = self.api('m5650/dossier', {'path': '/Dossiers/DEMO/etat.txt'}, self.lawyer)
        self.assertTrue(r['status'].startswith('403'))


# ---------------------------------------------------------------------------------------------------- B02 : matrice rôle × droit
class ValidationRight(Web):
    def setUp(self):
        super().setUp()
        self.admin = self.admin_cookie()
        mb.clear()
        self.addCleanup(mb.clear)

    def correct(self, cookie, value):
        return self.api('m5644/dossier/corriger', {'matter': 'DOS-001', 'section': 'jurisdiction', 'action': 'add', 'value': value}, cookie)

    def stored(self):
        desk = Desk(self.f.c)
        try:
            return [i['value'] for i in dossier5644.apply(desk, 'DOS-001', 'jurisdiction', [])]
        finally:
            desk.db.close()

    def test_B02_matrix_role_and_right(self):
        lawyer = self.add(self.admin, 'avocate@example.test', 'avocat')
        assistant = self.add(self.admin, 'assistant@example.test', 'assistant')
        self.assertTrue(self.correct(lawyer, 'Juridiction fictive A')['status'].startswith('200'))          # avocat : droit par défaut
        self.assertTrue(self.correct(assistant, 'Juridiction fictive B')['status'].startswith('403'))      # assistant : pas de droit par défaut
        self.assertTrue(self.correct(self.admin, 'Juridiction fictive C')['status'].startswith('200'))
        self.api('m5650/droits', {'owner': 'assistant@example.test', **{**mb.ROLE_DEFAULTS['assistant'], 'valider': True}}, self.admin)
        self.assertTrue(self.correct(assistant, 'Juridiction fictive D')['status'].startswith('200'))      # droit accordé explicitement
        self.api('m5650/droits', {'owner': 'avocate@example.test', **{**mb.ROLE_DEFAULTS['avocat'], 'valider': False}}, self.admin)
        refused = self.correct(lawyer, 'Juridiction fictive E')
        self.assertTrue(refused['status'].startswith('403'))
        undo = self.api('m5644/dossier/annuler-correction', {'matter': 'DOS-001', 'section': 'jurisdiction', 'id': 1}, lawyer)
        self.assertTrue(undo['status'].startswith('403'))
        stored = self.stored()
        self.assertIn('Juridiction fictive D', stored)
        self.assertFalse({'Juridiction fictive B', 'Juridiction fictive E'} & set(stored))   # aucune écriture refusée enregistrée

    def test_B02_service_refuses_even_if_the_route_rule_lets_it_through(self):
        lawyer = self.add(self.admin, 'avocate@example.test', 'avocat')
        self.api('m5650/droits', {'owner': 'avocate@example.test', **{**mb.ROLE_DEFAULTS['avocat'], 'valider': False}}, self.admin)
        with patch('agent.membres5650.category', return_value=None):                                        # route oubliée dans le registre
            self.assertTrue(self.correct(lawyer, 'Juridiction fictive F')['status'].startswith('403'))
        self.assertEqual(self.stored(), [])


# ---------------------------------------------------------------------------------------------------- B03 : identité des références
class References(unittest.TestCase):
    SOURCES = {'sources': [{'titre': 'Code de procédure civile, article 700', 'reference': 'DEMO-700', 'extrait': 'Passage fictif.'},
                           {'titre': 'Code de commerce', 'reference': '', 'extrait': ('x' * 600) + ' article L. 441-10 du code de commerce '},
                           {'titre': 'Cass. civ. 2e, 2 mars 2022, n° 21-12.345', 'reference': '21-12.345', 'extrait': ''}]}

    def check(self, text):
        return jr.unsourced_references(text, self.SOURCES)

    def test_B03_variants(self):
        self.assertEqual(self.check('Selon l’article 700 du Code de procédure civile.'), [])
        self.assertEqual(self.check('Selon l’article 700 du CPC.'), [])                                   # alias
        self.assertTrue(self.check('Selon l’article 700 du Code civil.'))                                  # même numéro, autre code
        self.assertTrue(any('code non précisé' in x for x in self.check('Selon l’article 700.')))         # citation sans code
        self.assertEqual(self.check('Voir l’article L. 441-10 du code de commerce.'), [])                  # au-delà de 400 caractères
        self.assertTrue(self.check('Voir l’article L. 441-11 du code de commerce.'))
        self.assertTrue(self.check('Voir l’article R. 441-10 du code de commerce.'))                       # L. et R. distincts
        self.assertEqual(self.check('Cass. civ. 2e, 2 mars 2022, n° 21-12.345.'), [])
        self.assertTrue(self.check('Cass. civ. 2e, 2 mars 2022, n° 21-99.999.'))                           # même date, autre pourvoi
        self.assertTrue(self.check('Article 700 du Code de procédure civile.', ) == [])

    def test_B03_failure_is_never_an_empty_result(self):
        with patch('agent.refs470.extract', side_effect=RuntimeError('panne')):
            self.assertTrue(jr.unsourced_references('Article 1240 du Code civil.', self.SOURCES))

    def test_cache_key_depends_on_connectors_and_policy(self):
        a = jr._key('DOS-001', 'question', [{'id': 'goodlegal', 'utilise': True}])
        b = jr._key('DOS-001', 'question', [{'id': 'goodlegal', 'utilise': True}, {'id': 'openlegi', 'utilise': True}])
        c = jr._key('DOS-001', 'question', [{'id': 'goodlegal', 'utilise': False}])
        self.assertEqual(len({a, b, c}), 3)


# ---------------------------------------------------------------------------------------------------- B04 : lots sans trou
class MessageBatches(unittest.TestCase):
    def setUp(self):
        self.f = t50.Messaging('run')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.a, self.b = 'avocate@example.test', 'assistant@example.test'
        self.tid = mx.open_direct(self.f.d, self.a, self.b, self.f.members)
        self.first = self.f.post(self.a, self.tid, 'Premier message')['id']
        mx.messages(self.f.d, self.b, self.tid)

    def drain(self, after):
        got = []
        while True:
            page = mx.messages_page(self.f.d, self.b, self.tid, after=after)
            got += [m['id'] for m in page['messages']]
            if not page['plus_recents']:
                return got
            after = got[-1]

    def test_B04_counts(self):
        for count in (0, 1, 60, 61, 130):
            with self.subTest(count=count):
                start = mx.messages_page(self.f.d, self.b, self.tid)['messages'][-1]['id']
                sent = [self.f.post(self.a, self.tid, 'Message fictif %d' % i)['id'] for i in range(count)]
                first_page = mx.messages_page(self.f.d, self.b, self.tid, after=start)
                if count > 60:
                    self.assertTrue(first_page['plus_recents'])
                    self.assertEqual(mx.unread_total(self.f.d, self.b), count - 60)                          # lu seulement jusqu'au livré
                self.assertEqual([m['id'] for m in first_page['messages']] + self.drain(first_page['messages'][-1]['id'] if first_page['messages'] else start),
                                 sent)
                self.assertEqual(mx.unread_total(self.f.d, self.b), 0)

    def test_B04_opening_starts_at_first_unread_and_history_goes_backwards(self):
        sent = [self.f.post(self.a, self.tid, 'Message fictif %d' % i)['id'] for i in range(70)]
        opened = mx.messages_page(self.f.d, self.b, self.tid)
        self.assertEqual(opened['messages'][0]['id'], sent[0])                                             # premier non lu, pas les 60 derniers
        self.assertTrue(opened['plus_recents'])
        self.assertTrue(opened['plus_anciens'])
        history = mx.messages_page(self.f.d, self.b, self.tid, before=sent[0])
        self.assertEqual([m['id'] for m in history['messages']], [self.first])
        self.assertFalse(history['plus_anciens'])
        latest = mx.messages_page(self.f.d, self.a, self.tid)                                              # auteur : rien de non lu
        self.assertEqual(latest['messages'][-1]['id'], sent[-1])
        self.assertTrue(latest['plus_anciens'])


# ---------------------------------------------------------------------------------------------------- B05 : registre des accès
class AccessRegistry(unittest.TestCase):
    def setUp(self):
        self.f = t50.Messaging('run')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.state = self.f.d.c['state_dir']
        mb.set_reserve(self.state, 'DOS-001', ['avocate@example.test'])
        mb.set_rights(self.state, 'cabinet', 'autre@example.test', 'avocat', {'envoyer': False})
        self.main = Path(self.state) / mb.FILE
        self.copy = Path(self.state) / mb.MIRROR

    def assert_closed(self):
        try:
            hidden = mb.hidden_for(self.state, 'autre@example.test', 'avocat')
            rights = mb.effective_rights(self.state, 'autre@example.test', 'avocat')
        except Stop as ex:
            self.assertEqual(str(ex), 'controle_acces_indisponible')
            self.assertFalse(mb.allowed(self.state, 'autre@example.test', 'avocat', 'POST', '/api440/send/confirm'))
            self.assertEqual(mb.hidden_for(self.state, 'cabinet', 'administrateur'), frozenset())    # l'administrateur garde l'accès
            return
        self.assertIn('DOS-001', hidden)
        self.assertFalse(rights['envoyer'])

    def test_B05_truncated_wrong_types_and_deleted_registry(self):
        for broken in ('{configuration interrompue', json.dumps({'droits': {'autre@example.test': {'envoyer': 'false'}}, 'reserves': {}}),
                       json.dumps({'droits': [], 'reserves': {}}), json.dumps({'version': 'x', 'droits': {}, 'reserves': {}})):
            with self.subTest(broken=broken[:30]):
                self.copy.unlink(missing_ok=True)
                self.main.write_text(broken, encoding='utf-8')
                self.assert_closed()
        self.main.unlink()
        self.assert_closed()

    def test_B05_same_version_copy_is_used_but_never_an_older_one(self):
        self.main.write_text('{abîmé', encoding='utf-8')
        self.assertIn('DOS-001', mb.hidden_for(self.state, 'autre@example.test', 'avocat'))                # copie de même version
        old_copy = self.copy.read_text(encoding='utf-8')
        mb.restore(self.state, 'copie')
        mb.set_reserve(self.state, 'DOS-002', ['avocate@example.test'])                                     # révocation plus récente
        self.copy.write_text(old_copy, encoding='utf-8')
        self.main.write_text('{abîmé', encoding='utf-8')
        with self.assertRaisesRegex(Stop, 'controle_acces_indisponible'):
            mb.hidden_for(self.state, 'autre@example.test', 'avocat')

    def test_B05_restore_and_concurrent_changes(self):
        self.main.write_text('{abîmé', encoding='utf-8')
        self.copy.unlink()
        with self.assertRaises(Stop):
            mb.load(self.state)
        self.assertEqual(mb.diagnose(self.state)['registre']['etat'], 'invalide')
        mb.restore(self.state, 'vide')
        self.assertEqual(mb.load(self.state)['reserves'], {})
        errors = []

        def change(i):
            try:
                mb.set_reserve(self.state, 'DOS-%03d' % i, ['avocate@example.test'])
            except Stop as ex:
                errors.append(str(ex))
        threads = [threading.Thread(target=change, args=(i,)) for i in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(mb.load(self.state)['reserves']), 8)                                           # aucune modification perdue

    def test_B05_never_configured_and_legacy_registry(self):
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        self.assertEqual(mb.load(empty.name)['reserves'], {})                                               # jamais configuré
        legacy = Path(empty.name) / mb.FILE
        legacy.write_text(json.dumps({'droits': {}, 'reserves': {'DOS-001': ['avocate@example.test']}}), encoding='utf-8')
        self.assertIn('DOS-001', mb.load(empty.name)['reserves'])
        self.assertTrue((Path(empty.name) / mb.MARKER).exists())                                           # désormais réputé actif
        legacy.unlink()
        with self.assertRaises(Stop):
            mb.load(empty.name)

    def test_B05_string_false_is_refused_on_write(self):
        with self.assertRaisesRegex(Stop, 'droit_invalide'):
            mb.set_rights(self.state, 'cabinet', 'autre@example.test', 'avocat', {'envoyer': 'false'})


class AccessRegistryWeb(Web):
    def test_B05_gate_refuses_non_admin_and_admin_can_restore(self):
        admin = self.admin_cookie()
        lawyer = self.add(admin, 'avocate@example.test', 'avocat')
        mb.set_reserve(str(self.state), 'DOS-001', ['avocate@example.test'])
        (Path(self.state) / mb.FILE).write_text('{abîmé', encoding='utf-8')
        (Path(self.state) / mb.MIRROR).unlink()
        self.assertEqual(self.request('/courriels', cookie=lawyer)['status'], '403 Forbidden')
        page = self.request('/parametres/droits', cookie=admin)
        self.assertEqual(page['status'], '200 OK')
        self.assertIn('Registre des accès illisible', page['body'])
        ok = self.api('m5650/restaurer', {'choix': 'vide'}, admin)
        self.assertTrue(ok['status'].startswith('200'), ok['body'])
        self.assertTrue(self.request('/courriels', cookie=lawyer)['status'].startswith('200'))
        self.assertTrue(self.api('m5650/restaurer', {'choix': 'vide'}, lawyer)['status'].startswith('403'))


if __name__ == '__main__':
    unittest.main()
