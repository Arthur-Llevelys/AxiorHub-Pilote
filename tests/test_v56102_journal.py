"""5.6.102, lot J : nettoyage du journal d'audit (purge par durée, vidage complet) sans casser la chaîne d'empreintes.

Données fictives : actions d'essai, dossier DOS-001, comptes example.test. Dates relatives au jour du cabinet (jamais ``date.today()``).
"""
from datetime import datetime, timedelta, timezone
import json
import re
from unittest.mock import patch
from urllib.parse import urlencode

from agent import activity431, cabinet_pilot as cp, connexion5648 as cx, journal56102 as jn, securite5688 as sec
from agent.desk import Desk
from agent.standalone_auth import allowed as role_allowed
import test_v5688_securite as t88

MOIS_SEMES = 30


def _il_y_a(mois, jours=5):
    """Instant UTC « il y a ``mois`` mois et ``jours`` jours » (même jour du mois, borné)."""
    now = datetime.now(timezone.utc)
    jour = jn._mois_avant(now.date(), mois)
    return (datetime.combine(jour, now.time(), timezone.utc) - timedelta(days=jours)).isoformat()


class Base(t88.Base):
    def seed(self):
        """Journal neuf et chaîné : une entrée par mois sur 30 mois (et sa copie dans l'ancien journal), puis une entrée d'hier."""
        desk = Desk(self.f.c)
        desk.db.execute('DELETE FROM audit_chain_v290')
        desk.db.execute('DELETE FROM audit')
        for k in range(MOIS_SEMES, 0, -1):
            at = _il_y_a(k)
            cp.append_audit(desk, at, 'essai_ancien', {'mois': k, 'dossier': 'DOS-001'})
            desk.db.execute('INSERT INTO audit VALUES(NULL,?,?,?)', (at, 'essai_ancien', json.dumps({'mois': k})))
        at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        cp.append_audit(desk, at, 'essai_recent', {'dossier': 'DOS-001'})
        desk.db.execute('INSERT INTO audit VALUES(NULL,?,?,?)', (at, 'essai_recent', '{}'))
        # preuve de dépôt (lue par une fonction) : jamais retirée de l'ancien journal
        desk.db.execute('INSERT INTO audit VALUES(NULL,?,?,?)', (_il_y_a(20), 'conclusions_televersees', json.dumps({'matter': 'DOS-001', 'path': 'x.pdf'})))
        desk.db.commit()
        self.assertTrue(cp.audit_status(desk)['valid'])
        return desk

    def count(self, where='1=1', params=()):
        return Desk(self.f.c).db.execute('SELECT COUNT(*) FROM audit_chain_v290 WHERE ' + where, params).fetchone()[0]

    def page(self, cookie, query=''):
        out = self.request('/parametres/journal-audit', cookie=cookie, query=query)
        self.assertEqual(out['status'], '200 OK', out['body'][:300])
        return out['body']

    @staticmethod
    def form(body, api):
        """Champs cachés du formulaire ``data-api=api`` de la page (comme le navigateur les enverrait)."""
        start = body.index('data-api="%s"' % api)
        block = body[start:body.index('</form>', start)]
        return dict(re.findall(r'<input type="hidden" name="(\w+)" value="([^"]*)"', block))

    def post(self, route, body, cookie):
        out = self.api('m5688s/' + route, body, cookie)
        return out, json.loads(out['body'] or b'{}')


class Purge(Base):
    def test_J01_purge_by_duration_keeps_recent_entries_and_a_verifiable_chain(self):
        desk = self.seed()
        cookie = self.admin_cookie()
        body = self.page(cookie)
        for text in ('Place occupée et nettoyage', 'Conserver', 'Purger automatiquement', 'Purger maintenant les entrées de plus de 12 mois',
                     'Vider tout le journal', 'Exporter d’abord (CSV)'):
            self.assertIn(text, body)
        coupe, _ = jn.limite(desk, 12)
        anciennes = self.count('at<?', (coupe,))
        self.assertGreaterEqual(anciennes, 18)
        self.assertIn('%d entrée(s) ont plus de 12 mois' % anciennes, body)
        champs = self.form(body, 'm5688s/journal/purger')
        self.assertEqual(champs['mois'], '12')
        recentes = self.count('at>=?', (coupe,))
        out, data = self.post('journal/purger', champs, cookie)
        self.assertEqual(out['status'], '200 OK', data)
        self.assertEqual(out['headers'].get('Cache-Control'), 'private, no-store')
        self.assertEqual(data['supprimees'], anciennes)
        self.assertEqual(self.count('at<?', (coupe,)), 0)                     # seules les entrées de plus de 12 mois sont parties
        self.assertEqual(self.count("action<>'journal_audit_purge'"), recentes)
        line = Desk(self.f.c).db.execute("SELECT payload FROM audit_chain_v290 WHERE action='journal_audit_purge'").fetchone()
        info = json.loads(line[0])
        self.assertEqual((info['supprimees'], info['mois'], info['mode'], info['par']), (anciennes, 12, 'purge', 'cabinet'))
        self.assertTrue(info['du'] < info['au'] < coupe)
        status = cp.audit_status(Desk(self.f.c))
        self.assertTrue(status['valid'])
        # « vérifiée depuis l'entrée n° … » : la première entrée gardée (les numéros ne repartent pas de 1 : le premier démarrage a pu journaliser une reprise)
        premiere = Desk(self.f.c).db.execute('SELECT MIN(seq) FROM audit_chain_v290').fetchone()[0]
        self.assertEqual(status['since'], premiere)
        self.assertTrue(cp.audit_verify(Desk(self.f.c))['valid'])             # contrôle incrémental 5.6.32 : repart lui aussi de l'ancrage
        # ancien journal : même limite, preuve de dépôt gardée
        old = Desk(self.f.c).db
        self.assertEqual(old.execute("SELECT COUNT(*) FROM audit WHERE action='essai_ancien' AND at<?", (coupe,)).fetchone()[0], 0)
        self.assertEqual(old.execute("SELECT COUNT(*) FROM audit WHERE action='conclusions_televersees'").fetchone()[0], 1)
        body = self.page(cookie)
        self.assertIn('Chaîne intacte', body)
        self.assertIn('Chaîne vérifiée depuis l’entrée n° %d' % premiere, body)
        self.assertIn('Dernier nettoyage : purge', body)
        self.assertIn('Aucune entrée n’a plus de 12 mois', body)

    def test_J01_stale_page_gives_409_and_replay_gives_the_same_result(self):
        self.seed()
        cookie = self.admin_cookie()
        champs = self.form(self.page(cookie), 'm5688s/journal/purger')
        avant = self.count()
        stale = dict(champs, version='0' * 20)
        out, data = self.post('journal/purger', stale, cookie)
        self.assertEqual(out['status'], '409 Conflict', data)
        self.assertEqual(self.count(), avant)
        out, first = self.post('journal/purger', champs, cookie)
        self.assertEqual(out['status'], '200 OK', first)
        apres = self.count()
        out, again = self.post('journal/purger', champs, cookie)              # même clé : même réponse, aucun second effet
        self.assertEqual((out['status'], again['supprimees']), ('200 OK', first['supprimees']))
        self.assertEqual(self.count(), apres)
        self.assertEqual(self.count("action='journal_audit_purge'"), 1)
        out, data = self.post('journal/vider', dict(champs, compris=True, mot='VIDER', jusqua=1), cookie)   # clé réutilisée pour une autre action
        self.assertEqual(out['status'], '409 Conflict', data)

    def test_J01_broken_chain_is_never_purged(self):
        desk = self.seed()
        desk.db.execute("UPDATE audit_chain_v290 SET payload='{}' WHERE seq=(SELECT MIN(seq)+2 FROM audit_chain_v290)")
        desk.db.commit()
        ap = jn.apercu_purge(desk, 12)
        avant = self.count()
        with self.assertRaises(jn.Stop) as ctx:
            jn.purger(desk, 'cabinet', 12, ap['version'], 'cle-essai-rompue')
        self.assertEqual(str(ctx.exception), 'chaine_rompue')
        self.assertEqual(self.count(), avant)                                 # la trace de l'altération reste
        self.assertIsNone(jn.ancre(Desk(self.f.c)))

    def test_J04_purge_line_survives_its_own_operation_and_a_later_purge_writes_a_new_one(self):
        desk = self.seed()
        first = jn.purger(desk, 'cabinet', 12, jn.apercu_purge(desk, 12)['version'], 'cle-essai-j04-a')
        self.assertGreater(first['supprimees'], 0)
        self.assertEqual(self.count("action='journal_audit_purge'"), 1)      # la ligne n'est pas emportée par sa propre purge
        again = jn.purger(desk, 'cabinet', 12, jn.apercu_purge(desk, 12)['version'], 'cle-essai-j04-b')
        self.assertEqual(again['supprimees'], 0)                              # rien d'autre à purger aujourd'hui
        self.assertEqual(self.count("action='journal_audit_purge'"), 1)
        # Plus tard (25 mois) : la première ligne de purge est assez ancienne pour partir, une nouvelle est inscrite.
        jour, fuseau = jn._aujourdhui(desk)
        plus_tard = jn._mois_avant(jour, -25)
        futur = datetime.now(timezone.utc) + (plus_tard - jour)
        with patch.object(jn, '_aujourdhui', lambda d: (plus_tard, fuseau)), patch('agent.desk.now', lambda: futur.isoformat()):
            later = jn.purger(desk, 'cabinet', 12, jn.apercu_purge(desk, 12)['version'], 'cle-essai-j04-c')
        self.assertGreater(later['supprimees'], 0)
        rows = Desk(self.f.c).db.execute('SELECT action, payload FROM audit_chain_v290 ORDER BY seq').fetchall()
        purges = [r for r in rows if r[0] == 'journal_audit_purge']
        self.assertEqual(len(purges), 1)
        self.assertEqual(json.loads(purges[0][1])['supprimees'], later['supprimees'])
        self.assertEqual(len(jn.historique(Desk(self.f.c), 10)), 2)          # l'historique des opérations garde les deux
        self.assertTrue(cp.audit_status(Desk(self.f.c))['valid'])


class Vidage(Base):
    def test_J02_full_wipe_needs_both_confirmations_then_leaves_one_line(self):
        self.seed()
        cookie = self.admin_cookie()
        champs = self.form(self.page(cookie), 'm5688s/journal/vider')
        avant = self.count()
        for partial in ({'compris': True}, {'compris': True, 'mot': 'vider ?'}, {'mot': 'VIDER'}, {'compris': 'true', 'mot': 'VIDER'}):
            out, data = self.post('journal/vider', dict(champs, **partial), cookie)
            self.assertEqual(out['status'][:3], '422', (partial, data))
            self.assertEqual(data['error'], 'confirmation_requise')
            self.assertEqual(self.count(), avant)                             # rien supprimé
        out, data = self.post('journal/vider', dict(champs, compris=True, mot='VIDER'), cookie)
        self.assertEqual(out['status'], '200 OK', data)
        self.assertIn('Journal vidé', data['message'])
        rows = Desk(self.f.c).db.execute('SELECT action, payload FROM audit_chain_v290').fetchall()
        self.assertEqual([r[0] for r in rows], ['journal_audit_vide'])        # une seule ligne « journal vidé »
        info = json.loads(rows[0][1])
        self.assertEqual((info['mode'], info['par'], info['supprimees']), ('vidage', 'cabinet', avant))
        status = cp.audit_status(Desk(self.f.c))
        self.assertTrue(status['valid'])
        self.assertEqual(status['since'], int(champs['jusqua']) + 1)
        old = Desk(self.f.c).db.execute('SELECT action FROM audit').fetchall()
        self.assertEqual(sorted(r[0] for r in old), ['conclusions_televersees', 'journal_audit_vide'])
        body = self.page(cookie)
        self.assertIn('Chaîne vérifiée depuis l’entrée n°', body)
        self.assertIn('vidage complet', body)

    def test_J02_entries_arriving_after_display_are_kept(self):
        desk = self.seed()
        ap = jn.apercu_vidage(desk)
        desk.audit('essai_arrivee', {'dossier': 'DOS-001'})
        out = jn.vider(desk, 'cabinet', ap['jusqua'], ap['version'], True, 'VIDER', 'cle-essai-arrivee')
        actions = [r[0] for r in Desk(self.f.c).db.execute('SELECT action FROM audit_chain_v290 ORDER BY seq')]
        self.assertEqual(actions, ['essai_arrivee', 'journal_audit_vide'])
        self.assertIn('arrivée(s) depuis l’affichage', out['message'])
        self.assertTrue(cp.audit_status(Desk(self.f.c))['valid'])


class Rights(Base):
    def test_J03_collaborator_and_missing_csrf_are_refused_and_nothing_is_deleted(self):
        self.seed()
        admin = self.admin_cookie()
        champs = self.form(self.page(admin), 'm5688s/journal/purger')
        vider = self.form(self.page(admin), 'm5688s/journal/vider')
        lawyer = self.member_cookie(self.member())
        avant = self.count()
        self.assertEqual(self.request('/parametres/journal-audit', cookie=lawyer)['status'], '403 Forbidden')
        for route, body in (('journal/purger', champs), ('journal/vider', dict(vider, compris=True, mot='VIDER')),
                            ('journal/reglage', {'mois': 1, 'auto': True, 'version': 'x', 'idem': 'cle-essai-avocat'})):
            out, data = self.post(route, body, lawyer)
            self.assertEqual(out['status'], '403 Forbidden', (route, data))
            self.assertFalse(role_allowed('avocat', 'POST', '/api440/m5688s/' + route))
            self.assertTrue(role_allowed('administrateur', 'POST', '/api440/m5688s/' + route))
        self.assertEqual(self.count(), avant)
        out = self.call('/api440/m5688s/journal/purger', 'POST', champs, auth=False, csrf=False, extra={'HTTP_COOKIE': '%s=%s' % (cx.COOKIE, admin)})
        self.assertIn(out['status'][:3], ('400', '403'))
        self.assertEqual(json.loads(out['body'])['error'], 'origine_ou_csrf_refuse')
        self.assertEqual(self.count(), avant)
        out = self.call('/api440/m5688s/journal/vider', 'POST', dict(vider, compris=True, mot='VIDER'), auth=False, origin=False,
                        extra={'HTTP_COOKIE': '%s=%s' % (cx.COOKIE, admin)})
        self.assertNotEqual(out['status'][:3], '200')
        self.assertEqual(self.count(), avant)
        self.assertEqual(self.count("action IN ('journal_audit_purge','journal_audit_vide')"), 0)


class Automatic(Base):
    def test_J05_automatic_purge_is_off_by_default_and_runs_only_in_maintenance(self):
        desk = self.seed()
        self.assertEqual(jn.reglage(desk), {'mois': 12, 'auto': False})
        coupe, _ = jn.limite(desk, 12)
        anciennes = self.count('at<?', (coupe,))
        self.assertGreater(anciennes, 0)
        activity431.maintenance(Desk(self.f.c), 2_000_000_000.0)
        self.assertEqual(self.count('at<?', (coupe,)), anciennes)             # désactivée par défaut : rien
        self.assertEqual(jn.historique(Desk(self.f.c), 10), [])
        cookie = self.admin_cookie()
        champs = self.form(self.page(cookie), 'm5688s/journal/reglage')
        out, data = self.post('journal/reglage', dict(champs, mois='12', auto=True), cookie)
        self.assertEqual(out['status'], '200 OK', data)
        self.assertEqual(jn.reglage(Desk(self.f.c)), {'mois': 12, 'auto': True})
        out, data = self.post('journal/reglage', dict(champs, mois='6', auto=False, idem='cle-essai-perimee'), cookie)
        self.assertEqual(out['status'], '409 Conflict', data)                # réglage changé depuis l'affichage
        out, data = self.post('journal/reglage', dict(champs, mois='0', idem='cle-essai-zero'), cookie)
        self.assertEqual(out['status'][:3], '422')
        for _ in range(2):
            self.page(cookie)                                                 # jamais pendant un GET
        self.assertEqual(self.count('at<?', (coupe,)), anciennes)
        self.assertEqual(jn.historique(Desk(self.f.c), 10), [])
        stamp = datetime.now(timezone.utc).timestamp()
        with patch.object(jn, '_heure_cabinet', lambda d: 12):
            activity431.maintenance(Desk(self.f.c), stamp)
        self.assertEqual(self.count('at<?', (coupe,)), 0)
        self.assertEqual(jn.historique(Desk(self.f.c), 1)[0]['mode'], 'auto')
        self.assertEqual(json.loads(Desk(self.f.c).db.execute("SELECT payload FROM audit_chain_v290 WHERE action='journal_audit_purge'").fetchone()[0])['par'],
                         'maintenance')
        apres = self.count()
        with patch.object(jn, '_heure_cabinet', lambda d: 12):
            activity431.maintenance(Desk(self.f.c), stamp + 3600)              # une fois par jour au plus
        self.assertEqual(self.count(), apres)
        self.assertTrue(cp.audit_status(Desk(self.f.c))['valid'])

    def test_J05_space_is_returned_at_night_only_after_a_purge(self):
        desk = self.seed()
        self.assertEqual(jn.compacter(desk)['motif'], 'non_demande')
        jn.purger(desk, 'cabinet', 12, auto=True)
        self.assertTrue(jn.etat_compactage(Desk(self.f.c))['demande'])
        with patch.object(jn, '_heure_cabinet', lambda d: 14):
            self.assertEqual(jn.compacter(Desk(self.f.c))['motif'], 'hors_nuit')
        with patch.object(jn, '_heure_cabinet', lambda d: 3), patch.object(jn, 'SEUIL_COMPACTAGE', 0):
            out = jn.compacter(Desk(self.f.c))
        self.assertTrue(out['fait'], out)
        etat = jn.etat_compactage(Desk(self.f.c))
        self.assertFalse(etat['demande'])
        self.assertIn('le', etat['dernier'])
        self.assertTrue(cp.audit_status(Desk(self.f.c))['valid'])


class Anchor(Base):
    def test_J06_anchor_is_what_keeps_the_chain_valid(self):
        desk = self.seed()
        jn.purger(desk, 'cabinet', 12, auto=True)
        d = Desk(self.f.c)
        good = jn.ancre(d)
        self.assertTrue(cp.audit_status(d)['valid'])
        first = d.db.execute('SELECT MIN(seq) FROM audit_chain_v290').fetchone()[0]
        with patch.object(jn, 'ancre', lambda _d: None):                      # ancrage retiré (en mémoire)
            status = cp.audit_status(d)
            self.assertFalse(status['valid'])
            self.assertEqual(status['broken_at'], first)
            d.setting('audit_chain:checkpoint5632', {})
            self.assertFalse(cp.audit_verify(d)['valid'])
        with patch.object(jn, 'ancre', lambda _d: dict(good, event_hash='0' * 64)):   # empreinte forgée
            self.assertFalse(cp.audit_status(d)['valid'])
        with patch.object(jn, 'ancre', lambda _d: dict(good, seq=good['seq'] + 3)):   # ancrage avancé pour cacher des entrées
            status = cp.audit_status(d)
            self.assertFalse(status['valid'])
            self.assertEqual(status['broken_at'], first)
        self.assertTrue(cp.audit_status(d)['valid'])
        cookie = self.admin_cookie()
        sec._chain.clear()
        with patch.object(jn, 'ancre', lambda _d: None):
            body = self.page(cookie)
        self.assertIn('Chaîne rompue', body)
        self.assertNotIn('Chaîne intacte', body)


class Export(Base):
    def test_J07_export_first_links_use_the_existing_export_with_the_same_filters(self):
        self.seed()
        cookie = self.admin_cookie()
        filtres = {'du': _il_y_a(29)[:10], 'action': 'essai_ancien', 'q': 'DOS-001'}
        body = self.page(cookie, urlencode(filtres))
        export = '/agent-courriel/api440/m5688s/journal/csv?' + urlencode(filtres)
        attendu = 'href="%s">Exporter d’abord (CSV)' % export.replace('&', '&amp;')
        self.assertEqual(body.count(attendu), 2, re.findall(r'href="[^"]*">Exporter d’abord', body))   # purge et vidage
        self.assertIn('href="%s">Exporter (CSV)' % export.replace('&', '&amp;'), body)                   # même adresse que l'export existant
        out = self.request('/api440/m5688s/journal/csv', cookie=cookie, query=urlencode(filtres))
        self.assertEqual(out['status'], '200 OK')
        self.assertIn('essai_ancien', out['body'])
