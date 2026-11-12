"""5.6.97, lot proteger (P0) : garde unique (acces5697), B01 (fil de courriels), B07 (agenda, tâches, export ICS), B02 et J07 (sources juridiques).

Chaque garde-fou a sa recette bloquante et une preuve par mutation : la mutation est appliquée en mémoire (``patch``) et la recette doit
alors échouer ; aucun code altéré ne reste dans l'arbre. Faux serveur IMAP (``test_v5696_courriels.FauxIMAP``), transports MCP espionnés,
toute connexion réseau réelle fait échouer le test. Données fictives : DURAND, ZORVAL, DOS-001, DOS-002, example.test."""
import io
import json
import socket
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from agent import acces5697, membres5650
from agent.common import Stop, private_json
from test_v5696_courriels import BaseVue, CAMILLE, CLAIRE, JETON, ORIGINE

ADMIN = 'admin@example.test'


def _reseau_interdit(*a, **k):
    raise AssertionError('appel réseau réel interdit dans les tests')


class SansReseau(unittest.TestCase):
    """Toute connexion réseau non simulée fait échouer le test."""

    def setUp(self):
        super().setUp()
        for cible in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            p = patch(cible, side_effect=_reseau_interdit)
            p.start()
            self.addCleanup(p.stop)


class Base97(BaseVue, SansReseau):
    """Deux dossiers : DOS-001 (DURAND) et DOS-002 (ZORVAL), ce dernier réservé à CAMILLE."""

    def setUp(self):
        super().setUp()
        private_json(self.f.matter_file, self.f.matters + [{'id': 'DOS-002', 'client_name': 'Client ZORVAL', 'path': '/Dossiers/ZORVAL', 'references': ['DOS-002'],
                                                              'aliases': ['Client ZORVAL'], 'correspondents': [{'email': 'zorval@example.test', 'role': 'client'}]}])
        self.state_dir = self.desk.c['state_dir']
        membres5650.set_reserve(self.state_dir, 'DOS-002', [CAMILLE])

    def requete(self, owner=CLAIRE, role='avocat'):
        """Contexte d'une requête authentifiée, tel que le pose le contrôle central."""
        membres5650.gate({'HTTP_X_AXIORHUB_USER': owner, 'HTTP_X_AXIORHUB_ROLE': role, 'REQUEST_METHOD': 'GET'}, self.state_dir, '/planning', {})


# ------------------------------------------------------------------------------------------------ garde commune
class Garde(Base97):
    def test_B01_garde_signatures_delegation_et_reponse_neutre(self):
        self.assertTrue(acces5697.peut_lire_dossier(self.desk, (CLAIRE, 'avocat'), 'DOS-001'))
        self.assertFalse(acces5697.peut_lire_dossier(self.desk, (CLAIRE, 'avocat'), 'DOS-002'))
        self.assertTrue(acces5697.peut_lire_dossier(self.desk, {'actor_id': CAMILLE, 'role': 'avocat'}, 'DOS-002'))
        self.assertTrue(acces5697.peut_lire_dossier(self.desk, (ADMIN, 'administrateur'), 'DOS-002'))
        self.assertFalse(acces5697.peut_lire_dossier(self.desk, (CLAIRE, 'stagiaire'), 'DOS-001'))       # rôle inconnu : rien
        self.assertNotIn('DOS-002', acces5697.dossiers_autorises(self.desk, (CLAIRE, 'avocat')))
        with self.assertRaises(Stop) as refus:
            acces5697.exiger(self.desk, (CLAIRE, 'avocat'), dossier_id='DOS-002')
        self.assertEqual(str(refus.exception), 'introuvable')
        with self.assertRaises(Stop):
            acces5697.exiger(self.desk, None, dossier_id='DOS-001')                                      # acteur absent : jamais administrateur

    def test_B01_registre_illisible_refuse(self):
        from pathlib import Path
        for nom in (membres5650.FILE, membres5650.MIRROR):                      # registre et copie illisibles : jamais « aucune restriction »
            (Path(self.state_dir) / nom).write_text('{illisible', encoding='utf-8')
        self.assertFalse(acces5697.peut_lire_dossier(self.desk, (CLAIRE, 'avocat'), 'DOS-001'))

    def test_B01_perimetre_sql_applique_avant_limit(self):
        self.desk.db.execute('CREATE TABLE essai97(matter TEXT, n INTEGER)')
        self.desk.db.executemany('INSERT INTO essai97 VALUES(?,?)', [('DOS-002', 1), ('DOS-002', 2), ('DOS-001', 3)])
        clause, params = acces5697.dossiers_autorises(self.desk, (CLAIRE, 'avocat')).sql('matter')
        rows = self.desk.db.execute('SELECT n FROM essai97 WHERE ' + clause + ' ORDER BY n LIMIT 1', params).fetchall()
        self.assertEqual([r[0] for r in rows], [3])


# ------------------------------------------------------------------------------------------------ B01 : fil de courriels
class FilReserve(Base97):
    def semer(self, ordre='ABA'):
        """Fil de messages : A (DOS-001, visible), B (DOS-002, réservé). Le suivi du fil est enregistré dans DOS-001 (premier message)."""
        from agent import courriels5696 as cx
        cx.ensure_schema(self.desk)
        racine, cles, fil = None, {}, None
        for n, lettre in enumerate(ordre):
            objet = ('Bail DURAND' if n == 0 else 'Re: Bail DURAND') if lettre == 'A' else 'Confidentiel ZORVAL'
            de = 'Claire DURAND <claire.durand@example.test>' if lettre == 'A' else 'Client ZORVAL <zorval@example.test>'
            cle, f, mid = self.entrant(objet + (' %d' % n if lettre == 'A' else ''), de=de, quand=self.a(-1, 8 + 2 * n, 0), fil_de=racine,
                                       matter='DOS-001' if lettre == 'A' else 'DOS-002', corps='Corps %s %d' % ('SECRET-ZORVAL' if lettre == 'B' else 'durand', n))
            racine = racine or mid
            fil = fil or f
            premiere = premiere if n else cle
            cles.setdefault(lettre, []).append(cle)
        stamp = datetime.now(timezone.utc).isoformat()
        self.desk.db.execute("INSERT INTO courriels5696_suivis(id, compte, boite, fil, mail_key, matter, statut, cree, maj) VALUES(?,?,?,?,?,?,?,?,?)",
                             (uuid.uuid4().hex, self.desk.c['mail']['username'] + '@' + self.desk.c['mail']['host'], 'INBOX', fil, premiere, 'DOS-001', 'a_traiter',
                              stamp, (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()))
        self.desk.db.commit()
        for compte in (CLAIRE, CAMILLE, ADMIN):
            self.activer(owner=compte)
        return fil, cles

    def uid_de(self, cle):
        import json as j
        from pathlib import Path
        return j.loads((Path(self.state_dir) / 'reports' / (cle + '.json')).read_text(encoding='utf-8'))['source_uid']

    def corps_de(self, uid):
        return [c for c in self.serveur.commandes if c[0] == 'UID' and c[1] == 'FETCH' and c[2] == str(uid) and 'BODY.PEEK[]' in c[3]]

    def recette(self, fil, cles, owner=CLAIRE):
        """Recette bloquante B01 : liste, détail, fil, pièce, page, filtre de recherche et « Préparer » ; B absent partout, corps jamais lu."""
        uid_b = self.uid_de(cles['B'][0])
        self.serveur.commandes.clear()
        statut, liste = self.appel('courriels/liste', args={'filtre': 'tous'}, owner=owner)
        self.assertEqual(statut, '200')
        brut = json.dumps(liste, ensure_ascii=False)
        self.assertNotIn('ZORVAL', brut)
        self.assertNotIn(cles['B'][0], brut)
        self.assertNotIn('DOS-002', brut)
        _, cherche = self.appel('courriels/liste', args={'filtre': 'tous', 'dossier': 'DOS-002'}, owner=owner)
        self.assertEqual(cherche['lignes'], [])
        for route in ('courriels/message', 'courriels/fil', 'courriels/piece'):
            statut, out = self.appel(route, args={'fil': fil, 'index': '0'}, owner=owner)
            self.assertNotIn('ZORVAL', json.dumps(out, ensure_ascii=False), route)
        statut, out = self.appel('courriels/fil', args={'fil': fil}, owner=owner)
        self.assertEqual(statut, '200')
        self.assertTrue(out['messages'])
        self.assertNotIn('SECRET-ZORVAL', json.dumps(out, ensure_ascii=False))
        self.requete(owner)                                                       # contrôle central de la requête, comme en production
        page = self.page(owner=owner, vue='assistante', fil=fil)
        membres5650.clear()
        self.assertNotIn('ZORVAL', page)
        self.assertEqual(self.corps_de(uid_b), [], 'corps du message réservé lu')
        statut, out = self.appel('courriels/preparer', {'fil': fil, 'action': 'preparer', 'version': liste['lignes'][0]['version'], 'idempotence': uuid.uuid4().hex,
                                                        'dossier': 'DOS-002'}, owner=owner)
        self.assertEqual(statut, '400')                                           # un champ POST ne donne aucun droit sur un dossier
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='prepare_reply'").fetchone()[0], 0)

    def test_B01_recette_A_B_A_rien_du_message_reserve(self):
        fil, cles = self.semer('ABA')
        self.recette(fil, cles)

    def test_B01_scenario_A_puis_B_la_liste_ne_reprend_pas_le_suivi_ancien(self):
        fil, cles = self.semer('AB')
        self.recette(fil, cles)
        statut, liste = self.appel('courriels/liste', args={'filtre': 'tous'}, owner=CLAIRE)
        self.assertEqual([l['mail_key'] for l in liste['lignes']], [cles['A'][0]])           # le fil est représenté par un message visible
        statut, admin = self.appel('courriels/liste', args={'filtre': 'tous'}, owner=ADMIN, role='administrateur')
        ligne = admin['lignes'][0]
        self.assertEqual((ligne['mail_key'], ligne['matter']), (cles['B'][0], 'DOS-002'))    # le dossier du message, pas celui du suivi
        self.assertIn('vérifier', ligne['precision'])

    def test_B01_api_directe_et_reference_contradictoire_neutres(self):
        fil, cles = self.semer('B')
        statut, out = self.appel('courriels/message', args={'fil': fil}, owner=CLAIRE)
        self.assertEqual((statut, out['error']), ('404', 'introuvable'))
        self.assertNotIn('ZORVAL', json.dumps(out, ensure_ascii=False))
        from agent import courriels5696 as cx
        f = cx.FournisseurIMAP(self.desk, acces5697.masques(self.desk, (CLAIRE, 'avocat')))
        ref = f.reference(cles['B'][0])
        ref['matter'] = 'DOS-001'                                                 # rattachement annoncé par l'appelant : ignoré
        self.serveur.commandes.clear()
        with self.assertRaises(Stop):
            f.lire_message(ref)
        self.assertEqual(self.serveur.commandes, [])                              # refusé avant toute connexion
        self.assertFalse(acces5697.peut_lire_message(self.desk, (CLAIRE, 'avocat'), {'mail_key': cles['B'][0], 'matter': 'DOS-001'}))
        self.assertTrue(acces5697.peut_lire_message(self.desk, (CAMILLE, 'avocat'), cles['B'][0]))

    def test_B01_rattachements_contradictoires_exigent_tous_les_droits(self):
        fil, cles = self.semer('A')
        stamp = datetime.now(timezone.utc).isoformat()
        self.desk.db.execute("INSERT INTO portfolio_mail_links(id, mail_key, message_id, thread_root, folder, uid, matter, confidence, level, source, sender, recipients, mail_date, status, updated) "
                             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, cles['A'][0], '', '', 'INBOX', '1', 'DOS-002', 80, 'moyen', 'test', '', '', stamp, 'automatic', stamp))
        self.desk.db.commit()
        self.assertFalse(acces5697.peut_lire_message(self.desk, (CLAIRE, 'avocat'), cles['A'][0]))
        statut, liste = self.appel('courriels/liste', args={'filtre': 'tous'}, owner=CLAIRE)
        self.assertEqual(liste['lignes'], [])

    def test_B01_apres_revocation_et_worker_revérifie(self):
        fil, cles = self.semer('ABA')
        statut, liste = self.appel('courriels/liste', args={'filtre': 'tous'}, owner=CLAIRE)
        statut, out = self.appel('courriels/preparer', {'fil': fil, 'action': 'preparer', 'version': liste['lignes'][0]['version'], 'idempotence': uuid.uuid4().hex},
                                 owner=CLAIRE)
        self.assertEqual(statut, '200', out)
        args = json.loads(self.desk.db.execute("SELECT args FROM jobs WHERE kind='prepare_reply'").fetchone()[0])
        self.assertEqual((args['demande_par'], args['demande_role'], args['key']), (CLAIRE, 'avocat', cles['A'][-1]))
        membres5650.set_reserve(self.state_dir, 'DOS-001', [CAMILLE])           # droits retirés après la demande
        self.serveur.commandes.clear()
        from agent import intelligence
        with self.assertRaises(Stop) as refus:
            intelligence.prepare_reply(self.desk, args)
        self.assertEqual(str(refus.exception), 'introuvable')
        self.assertEqual(self.serveur.commandes, [])                              # le worker n'a rien relu
        statut, liste = self.appel('courriels/liste', args={'filtre': 'tous'}, owner=CLAIRE)
        self.assertEqual(liste['lignes'], [])
        statut, out = self.appel('courriels/fil', args={'fil': fil}, owner=CLAIRE)
        self.assertEqual((statut, out['error']), ('404', 'introuvable'))

    def test_B01_utilisateur_autorise_voit_tout_le_fil(self):
        fil, cles = self.semer('ABA')
        statut, out = self.appel('courriels/fil', args={'fil': fil}, owner=CAMILLE)
        self.assertEqual(statut, '200')
        self.assertIn('SECRET-ZORVAL', json.dumps(out, ensure_ascii=False))

    # --------------------------------------------------------------------------------------------- preuves par mutation
    def test_B01_mutation_garde_retiree_la_recette_echoue(self):
        fil, cles = self.semer('ABA')
        with patch('agent.acces5697.masques', return_value=frozenset()):
            with self.assertRaises(AssertionError):
                self.recette(fil, cles)

    def test_B01_mutation_controle_par_message_du_fil_retire_la_recette_echoue(self):
        fil, cles = self.semer('ABA')
        from agent import courriels5696 as cx
        with patch.object(cx.FournisseurIMAP, 'autorise', lambda self, cle: True):
            with self.assertRaises(AssertionError):
                self.recette(fil, cles)

    def test_B01_mutation_filtre_avant_regroupement_retire_la_recette_echoue(self):
        fil, cles = self.semer('AB')
        with patch('agent.messages5697.dossiers_par_cle', return_value={}), patch('agent.courriels5696.LIMITE', 300):
            from agent import courriels5696 as cx
            vrai = cx._visibles
            with patch('agent.courriels5696._visibles', lambda desk, depuis, hidden: vrai(desk, depuis, frozenset())):
                with self.assertRaises(AssertionError):
                    self.recette(fil, cles)


# ------------------------------------------------------------------------------------------------ B07 : agenda, tâches, export
class AgendaReserve(Base97):
    def setUp(self):
        super().setUp()
        from agent import agenda5634
        agenda5634.schema(self.desk)
        base = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
        self.ids = {}
        for n, (matter, titre) in enumerate((('DOS-002', 'Audience ZORVAL 1'), ('DOS-002', 'Audience ZORVAL 2'), ('DOS-002', 'Audience ZORVAL 3'),
                                             ('DOS-001', 'Audience DURAND'))):
            ident = uuid.uuid4().hex
            self.ids[titre] = ident
            debut = base + timedelta(hours=n)
            self.desk.db.execute('INSERT INTO agenda5634(id, uid, kind, title, description, location, starts, ends, matter, created, updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                                 (ident, uuid.uuid4().hex + '@example.test', 'rdv', titre, 'Participants : ' + titre, 'Salle ' + titre, debut.isoformat(),
                                  (debut + timedelta(minutes=30)).isoformat(), matter, debut.isoformat(), debut.isoformat()))
        self.desk.db.execute("INSERT INTO tasks(id, matter, title, due, status, created) VALUES(?,?,?,?,?,?)", ('t-zorval', 'DOS-002', 'Tâche ZORVAL', base.date().isoformat(), 'open', base.isoformat()))
        self.desk.db.execute("INSERT INTO tasks(id, matter, title, due, status, created) VALUES(?,?,?,?,?,?)", ('t-durand', 'DOS-001', 'Tâche DURAND', base.date().isoformat(), 'open', base.isoformat()))
        self.desk.db.execute("INSERT INTO work_tasks_v211 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             ('w-zorval', 'u1', 'local:taches', '', '', 'DOS-002', 'Synchro ZORVAL', '', '', base.date().isoformat(), 30, 1, 'todo', '', '', '', '', 'test',
                              base.isoformat(), base.isoformat(), base.isoformat()))
        self.desk.db.execute("INSERT INTO calendar_cache VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             ('c-ambigu', 'u2', '', 'https://agenda.example.test/cal/', '', '', 'Rendez-vous ZORVAL ambigu', '', '', base.isoformat(),
                              (base + timedelta(hours=1)).isoformat(), 1, '', json.dumps(['DOS-002', 'DOS-001']), base.isoformat()))
        self.desk.db.commit()
        self.addCleanup(membres5650.clear)

    def export(self, owner=CLAIRE, role='avocat'):
        from agent import web5634
        env = {'HTTP_X_AXIORHUB_USER': owner, 'HTTP_X_AXIORHUB_ROLE': role, 'REQUEST_METHOD': 'GET'}
        out = web5634.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm5634/agenda/export', {}, 'GET')
        return out, json.loads(out['body'])['ics']

    def recette(self, owner=CLAIRE, role='avocat'):
        """Recette bloquante B07 : grille, JSON, tâches déplaçables et .ics sans titre, date, participant, lien ni identifiant de DOS-002."""
        from agent import agenda5634, agenda_lecture5697 as al, taches5628, workplan
        self.requete(owner, role)
        secrets = ['ZORVAL', 'DOS-002', 't-zorval', 'w-zorval', 'c-ambigu'] + [v for k, v in self.ids.items() if 'ZORVAL' in k]
        visibles = [
            json.dumps(agenda5634.listing(self.desk, limit=1), ensure_ascii=False),
            json.dumps(al.evenements(self.desk, (owner, role), limite=1), ensure_ascii=False),
            json.dumps(al.taches(self.desk, (owner, role), limite=1), ensure_ascii=False),
            json.dumps(taches5628.collect(self.desk), ensure_ascii=False),
            json.dumps(workplan.list_tasks(self.desk, 'todo,in_progress,blocked', '', 1), ensure_ascii=False, default=str),
            json.dumps(workplan.calendar_events(self.desk, (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                                                (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(), refresh=False), ensure_ascii=False, default=str),
            self.export(owner, role)[1],
        ]
        for texte in visibles:
            for s in secrets:
                self.assertNotIn(s, texte)
        self.assertIn('Audience DURAND', visibles[0])                            # la limite n'a pas été consommée par le dossier masqué
        self.assertIn('Audience DURAND', visibles[1])
        self.assertIn('Tâche DURAND', visibles[2])
        sortie, _ = self.export(owner, role)
        self.assertIn(('Cache-Control', 'private, no-store, max-age=0'), sortie['headers'])

    def test_B07_recette_dossier_masque_absent_partout(self):
        self.recette()

    def test_B07_grille_semaine_taches_a_programmer(self):
        from agent import agenda36
        self.desk.c['nextcloud_workflow'] = {'enabled': True, 'planning_calendar_url': 'https://agenda.example.test/cal/'}
        self.requete()
        html = agenda36.page(self.desk, {'view': 'week'}, lambda *a, **k: '', lambda *a, **k: '/x', lambda *a, **k: '<form></form>', source='personal')
        self.assertIn('Tâche DURAND', html)
        self.assertNotIn('ZORVAL', html)
        self.assertNotIn('t-zorval', html)

    def test_B07_export_intact_pour_un_utilisateur_autorise(self):
        _, ics = self.export(CAMILLE, 'avocat')
        for n in (1, 2, 3):
            self.assertIn('SUMMARY:Audience ZORVAL %d' % n, ics)
        _, ics_admin = self.export(ADMIN, 'administrateur')
        self.assertIn('Audience ZORVAL 1', ics_admin)

    def test_B07_apres_retrait_des_droits(self):
        membres5650.set_reserve(self.state_dir, 'DOS-001', [CAMILLE])
        _, ics = self.export()
        self.assertNotIn('DURAND', ics)
        self.assertNotIn('ZORVAL', ics)

    def test_B07_disponibilite_seulement_occupe(self):
        from agent import agenda_lecture5697 as al
        creneaux = al.evenements(self.desk, (CLAIRE, 'avocat'), disponibilite=True)
        self.assertEqual(len(creneaux), 4)
        self.assertEqual({c['title'] for c in creneaux}, {'Occupé'})
        self.assertEqual(set().union(*(c.keys() for c in creneaux)), {'starts', 'ends', 'all_day', 'title'})

    def test_B07_detail_par_url_neutre(self):
        from agent import agenda_lecture5697 as al
        with self.assertRaises(Stop) as refus:
            al.detail_evenement(self.desk, (CLAIRE, 'avocat'), self.ids['Audience ZORVAL 1'])
        self.assertEqual(str(refus.exception), 'introuvable')
        self.assertEqual(al.detail_evenement(self.desk, (CLAIRE, 'avocat'), self.ids['Audience DURAND'])['title'], 'Audience DURAND')

    def test_B07_export_global_administrateur_motif_et_journal(self):
        from agent import agenda_lecture5697 as al
        with self.assertRaises(Stop):
            al.export_global(self.desk, (CLAIRE, 'avocat'), 'sauvegarde')
        with self.assertRaises(Stop):
            al.export_global(self.desk, (ADMIN, 'administrateur'), '')
        ics = al.export_global(self.desk, (ADMIN, 'administrateur'), 'Sauvegarde avant migration')
        self.assertIn('Audience ZORVAL 1', ics)
        journal = self.desk.db.execute("SELECT data FROM audit WHERE action='agenda5697_export_global'").fetchall()
        self.assertEqual(len(journal), 1)

    def test_REGLE_lectures_et_export_sans_ia_pendant_la_pause(self):
        """Commandes de l'avocat sans IA : agenda, tâches, export ICS et garde restent disponibles, IA en pause et fournisseurs bloqués."""
        from agent import agenda_lecture5697 as al
        from agent.arret5643 import PAUSE_GLOBAL
        self.desk.setting(PAUSE_GLOBAL, True)
        bloque = AssertionError('fournisseur IA appelé')
        with patch('agent.ai_gateway.estimate_and_check_budget', side_effect=bloque), patch('agent.ai_gateway.common_completion', side_effect=bloque), \
                patch('agent.common.HTTP.request', side_effect=AssertionError('appel réseau')), patch('agent.desk.Desk.enqueue', side_effect=AssertionError('travail mis en file')):
            self.assertTrue(al.evenements(self.desk, (CLAIRE, 'avocat')))
            self.assertTrue(al.taches(self.desk, (CLAIRE, 'avocat')))
            _, ics = self.export()
            self.assertIn('Audience DURAND', ics)
            self.assertFalse(acces5697.peut_lire_dossier(self.desk, (CLAIRE, 'avocat'), 'DOS-002'))

    def test_B07_mutation_droits_apres_limit_la_recette_echoue(self):
        with patch('agent.acces5697.Perimetre.sql', lambda self, colonne: ('1=1', ())):
            with self.assertRaises(AssertionError):
                self.recette()

    def test_B07_mutation_candidats_non_controles_la_recette_echoue(self):
        with patch('agent.acces5697.Perimetre.sql_json', lambda self, colonne: ('1=1', ())):
            with self.assertRaises(AssertionError):
                self.recette()


# ------------------------------------------------------------------------------------------------ B02 et J07 : sources juridiques
class Sources(SansReseau):
    def setUp(self):
        super().setUp()
        import test_agent as fixtures
        from agent.desk import Desk
        from agent.sources5632 import item_id
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        self.desk.c['lawve_extensions'] = {item_id(pid): {'id': item_id(pid), 'provider': pid, 'kind': 'connector', 'name': pid, 'enabled': True, 'external_data_allowed': True,
                                                          'purposes': ['legal_analysis'], 'endpoint': 'https://%s.example.test/mcp' % pid,
                                                          'last_test': {'status': 'ok', 'tool_specs': [{'name': 'search', 'inputSchema': {'type': 'object'}}]}}
                                           for pid in ('openlegi', 'librejustice', 'legaldatahunter')}
        self.appels = []

        def transport(item, query, limit, call=None, tools=None):
            self.appels.append((item['provider'], query))
            return {'status': 'ok', 'rows': [{'title': 'Piste %s' % item['provider'], 'summary': 'Extrait public'}]}
        p = patch('agent.mcp5614.search', side_effect=transport)
        p.start()
        self.addCleanup(p.stop)

    def chercher(self, providers, **kw):
        from agent import registre5634
        return registre5634.search(self.desk, 'Résiliation du bail commercial pour défaut de paiement', '', providers, **kw)

    def local_strict(self, expres=False):
        """Local strict exprès (politique 5.6.89) ou hérité du réglage « IA locale uniquement » (que la politique laissait passer vers les sources)."""
        if expres:
            self.desk.setting('politique5689', {'mode': 'local_strict', 'revision': 1})
        else:
            self.desk.setting('ai:politique', {'mode': 'local'})

    def recette(self):
        """Recette bloquante B02 : « OpenLegi seul » = un appel ; « aucun » et « inconnu » = zéro ; révoqué ou local strict = zéro."""
        out = self.chercher(['openlegi'])
        self.assertEqual([p for p, _ in self.appels], ['openlegi'])
        self.assertEqual({l['provider'] for l in out['leads']}, {'openlegi'})
        self.appels.clear()
        out = self.chercher([])
        self.assertEqual((self.appels, out['status']), ([], 'aucune_source'))
        with self.assertRaises(Stop) as refus:
            self.chercher(['openlegi', 'inconnu'])
        self.assertEqual(str(refus.exception), 'fournisseur_juridique_invalide')
        self.assertEqual(self.appels, [])
        self.desk.c['lawve_extensions']['connector-axiorhub-openlegi']['enabled'] = False      # connecteur révoqué
        try:
            self.chercher(['openlegi'])
            self.assertEqual(self.appels, [])
        finally:
            self.desk.c['lawve_extensions']['connector-axiorhub-openlegi']['enabled'] = True

    def test_B02_recette_selection_respectee_a_l_appel(self):
        self.recette()

    def test_B02_non_precise_vaut_defaut_approuve(self):
        self.chercher(None)
        self.assertEqual(sorted(p for p, _ in self.appels), ['legaldatahunter', 'librejustice', 'openlegi'])

    def test_B02_territoire_demande(self):
        self.chercher(None, zones=('UE',))
        self.assertEqual([p for p, _ in self.appels], ['legaldatahunter'])

    def test_J07_local_strict_zero_appel_sur_tous_les_chemins(self):
        for expres in (False, True):
            with self.subTest(expres=expres):
                self.local_strict(expres)
                self._zero_appel_local_strict()

    def _zero_appel_local_strict(self):
        from agent import extensions364, legal_research
        self.chercher(['openlegi'])
        with self.assertRaises(Stop) as refus:                                                            # audience, missions, veille
            legal_research.research_enabled_mcp(self.desk, '', 'Résiliation du bail commercial', 4)
        self.assertEqual(str(refus.exception), 'mode_local_strict')
        extensions364.legal_connector_search(self.desk, 'résiliation bail commercial', 4)                    # transport appelé sans ensemble
        with patch('agent.connexions5652.call_tool', side_effect=AssertionError('connexion directe appelée')):
            from agent.connexions5652 import legal_leads
            leads, rapports = legal_leads(self.desk, 'résiliation bail commercial')
        self.assertEqual((leads, rapports[0]['error']), ([], 'mode_local_strict'))
        self.assertEqual(self.appels, [])

    def test_J07_appels_directs_recoivent_l_ensemble_autorise(self):
        from agent import legal_research
        legal_research.research_enabled_mcp(self.desk, '', 'Résiliation du bail commercial', 4, providers=['librejustice'])
        self.assertEqual([p for p, _ in self.appels], ['librejustice'])
        self.appels.clear()
        legal_research.research_enabled_mcp(self.desk, '', 'Résiliation du bail commercial', 4, providers=[])
        self.assertEqual(self.appels, [])

    def test_B02_requete_sans_nom_de_client(self):
        from agent import registre5634
        registre5634.search(self.desk, 'Le Client DEMO peut-il résilier le bail commercial ?', 'DOS-001', ['openlegi'])
        self.assertEqual(len(self.appels), 1)
        self.assertNotIn('DEMO', self.appels[0][1])

    def test_B02_route_web_liste_vide_et_inconnu(self):
        from agent import web5634
        corps = json.dumps({'question': 'Résiliation du bail commercial', 'providers': ['inconnu']}).encode()
        env = {'HTTP_X_AXIORHUB_USER': CLAIRE, 'HTTP_X_AXIORHUB_ROLE': 'avocat', 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON, 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps), 'REQUEST_METHOD': 'POST'}
        with self.assertRaises(Stop):
            web5634.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm5634/recherche', {}, 'POST')
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='recherche5634'").fetchone()[0], 0)
        out = web5634.research_job(self.desk, {'question': 'Résiliation du bail commercial', 'providers': []})
        self.assertEqual((out['status'], self.appels), ('aucune_source', []))
        from agent import legal_research
        with self.assertRaises(Stop):
            legal_research.prepare_research_query(self.desk, {'question': 'Résiliation du bail', 'providers': []})

    # --------------------------------------------------------------------------------------------- J07 : adaptateur intégré en local strict
    def integre(self):
        from agent.sources5632 import item_id
        self.desk.c['lawve_extensions'][item_id('de_bund')] = {'id': item_id('de_bund'), 'provider': 'de_bund', 'kind': 'connector', 'name': 'de_bund', 'transport': 'integre',
                                                               'enabled': True, 'external_data_allowed': True, 'purposes': ['legal_analysis'], 'last_test': {'status': 'ok'}}

    def recette_integre(self):
        """Local strict : l'adaptateur intégré choisi est interrogé, sans contrôle de sortie (aucun réseau) ; dès qu'une source externe est
        retenue, refus inchangé et aucune requête."""
        from agent import legal_research
        self.integre()
        self.local_strict(expres=True)
        with patch('agent.data_policy5689.exiger', side_effect=AssertionError('contrôle de sortie pour un index local')):
            out = self.chercher(['de_bund', 'openlegi'])
            self.assertEqual([p for p, _ in self.appels], ['de_bund'])
            self.assertEqual({r['provider']: r['error'] for r in out['reports'] if r.get('status') == 'skipped'}, {'openlegi': 'mode_local_strict'})
            self.appels.clear()
            legal_research.research_enabled_mcp(self.desk, '', 'Kündigung eines Gewerbemietvertrags', 4, providers=['de_bund'])
            self.assertEqual([p for p, _ in self.appels], ['de_bund'])
        self.appels.clear()
        with self.assertRaises(Stop) as refus:
            legal_research.research_enabled_mcp(self.desk, '', 'Kündigung eines Gewerbemietvertrags', 4, providers=['de_bund', 'openlegi'])
        self.assertEqual((str(refus.exception), self.appels), ('mode_local_strict', []))
        self.chercher(['openlegi'])
        self.assertEqual(self.appels, [])

    def test_J07_local_strict_adaptateur_integre_seul_interroge(self):
        self.recette_integre()

    def test_J07_mutation_source_externe_classee_locale_la_recette_echoue(self):
        from agent import selection_sources5697 as s
        with patch.object(s, 'est_locale', lambda entree: True), patch.object(s, 'item_local', lambda item, pid: True):
            with self.assertRaises(AssertionError):
                self.recette_integre()

    # --------------------------------------------------------------------------------------------- J07 : identifiants étrangers
    ETRANGERS = ('DE000000001', 'HRB 00001', '00 000 000 001', '1100000001', '000 000 000', 'X0000000T', '00000000T', 'B0000000J', 'AB 00 00 00 A',
                 'SC000001', '00000001')

    def recette_identifiants(self):
        from agent import legal_research
        question = 'Bail commercial : ' + ', '.join(self.ETRANGERS) + ' ; article L. 145-41, BOE-A-2015-11430, ECLI:FR:CCASS:2021:C100123'
        out, rapport = legal_research.anonymize_query(self.desk, dict(legal_research.GENERAL), question)
        for valeur in self.ETRANGERS:
            self.assertNotIn(valeur, out)
        for reference in ('L. 145-41', 'BOE-A-2015-11430', 'ECLI:FR:CCASS:2021:C100123'):      # les références juridiques publiques restent
            self.assertIn(reference, out)
        with self.assertRaises(Stop) as refus:                                                # forme inconnue : reformulation demandée
            legal_research.anonymize_query(self.desk, dict(legal_research.GENERAL), 'Dossier numéro 1234 5678 9012 34 et bail commercial')
        self.assertEqual(str(refus.exception), 'reformulation_requise')
        self.chercher(['openlegi'])
        self.appels.clear()
        from agent import registre5634
        registre5634.search(self.desk, 'Le preneur X0000000T peut-il résilier le bail commercial ?', '', ['openlegi'])
        self.assertEqual(len(self.appels), 1)
        self.assertNotIn('X0000000T', self.appels[0][1])

    def test_J07_identifiants_etrangers_retires_ou_reformulation(self):
        self.recette_identifiants()

    def test_J07_mutation_sans_motifs_etrangers_la_recette_echoue(self):
        with patch('agent.legal_research.ETRANGERS', ()):
            with self.assertRaises((AssertionError, Stop)):
                self.recette_identifiants()

    # --------------------------------------------------------------------------------------------- preuves par mutation
    def test_B02_mutation_ensemble_non_transmis_la_recette_echoue(self):
        from agent import selection_sources5697 as s
        with patch.object(s, 'autorises', lambda desk, providers: frozenset(s.selectionner(desk).ids)):
            with self.assertRaises(AssertionError):
                self.recette()

    def test_B02_mutation_liste_vide_redevient_tous_la_recette_echoue(self):
        from agent import selection_sources5697 as s
        vrai = s.normaliser
        with patch.object(s, 'normaliser', lambda demande: vrai(demande) or None):
            with self.assertRaises(AssertionError):
                self.recette()

    def test_J07_mutation_local_strict_ignore_les_appels_partent(self):
        from agent import selection_sources5697 as s
        self.local_strict()
        with patch.object(s, 'local_strict', lambda desk: False):
            self.chercher(['openlegi'])
        self.assertTrue(self.appels)                                             # sans le garde, la requête serait partie


# ------------------------------------------------------------------------------------------------ Aujourd'hui : « Actuellement », « À décider », « Courriels »
from test_v5695_aujourdhui import AVOCAT as AVOCATE, Cabinet, MESURE   # noqa: E402 — fixture du lot aujourdhui (sans test propre)

CLE_LIEE = 'b' * 64                     # courriel classé dans DOS-001, mais rattaché par le serveur au dossier réservé (B01)


class Aujourdhui(Cabinet, SansReseau):
    """DOS-006 réservé à un autre compte ; l'avocate ne doit voir ni compte ni nom de ce dossier, quel que soit ce que l'appelant transmet."""

    def setUp(self):
        super().setUp()
        from agent import portfolio
        portfolio.ensure_schema(self.d)
        membres5650.set_reserve(self.c['state_dir'], 'DOS-006', [CAMILLE])
        self.addCleanup(membres5650.clear)
        self.semer()

    def reserve_en_plus(self):
        """Nouvelles données du seul dossier réservé : événement, rapprochement ambigu, tâche, courriels (direct et par rattachement), travail."""
        from agent.cockpit530 import tz
        jour = datetime.now(tz(self.d)).replace(hour=10, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        self.evenement('ev9', 'Audience ZORVAL réservée', jour + timedelta(hours=3), matter='DOS-006')
        MESURE.evenement(self.d, 'ev10', 'Rendez-vous ZORVAL ambigu', jour + timedelta(hours=4))
        self.d.db.execute("UPDATE calendar_cache SET matter_candidates=? WHERE id='ev10'", (json.dumps(['DOS-006', 'DOS-001']),))
        self.tache('t9', 'Tâche ZORVAL réservée', (jour - timedelta(days=1)).date().isoformat(), 'DOS-006')
        self.courriel('c' * 64, 'Courriel ZORVAL réservé', 'DOS-006', recu=jour)
        self.courriel(CLE_LIEE, 'Courriel ZORVAL rattaché', 'DOS-001', recu=jour)
        stamp = jour.isoformat()
        self.d.db.execute("INSERT INTO portfolio_mail_links(id,mail_key,message_id,thread_root,folder,uid,matter,confidence,level,source,sender,recipients,mail_date,status,updated) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ('l9', CLE_LIEE, '', '', 'INBOX', '9', 'DOS-006', 90, 'fort', 'essai', '', '', stamp, 'confirmed', stamp))
        MESURE.travail(self.d, 'extract_facts460', 'DOS-006', 'running')
        MESURE.travail(self.d, 'extract_facts460', 'DOS-006', 'pending')
        self.prepare('p9', 'Note ZORVAL réservée', 'contrat', 'DOS-006')
        self.d.db.commit()

    def recette(self):
        """Recette : sans rien transmettre d'autre que le compte et le rôle, ajouter des données au dossier réservé ne change rien pour l'avocate."""
        avant = self.resume(*AVOCATE)['rubriques']
        self.reserve_en_plus()
        apres = self.resume(*AVOCATE)['rubriques']
        texte = json.dumps(apres, ensure_ascii=False)
        for interdit in ('ZORVAL', 'Expertise réservée', 'Tâche du dossier réservé', 'Courriel du dossier réservé', 'Note réservée', 'ORLANDEC 6'):
            self.assertNotIn(interdit, texte)
        self.assertEqual([(r['id'], r['nombre'], r['phrase'], r['urgent']) for r in apres], [(r['id'], r['nombre'], r['phrase'], r['urgent']) for r in avant])
        from agent import file5651
        global_items = [{'class': 'courriel', 'object': 'reponse5671:' + CLE_LIEE, 'revision': '1', 'when': '1', 'matter': 'DOS-001', 'matter_name': 'DURAND 1',
                         'title': 'Réponse ZORVAL rattachée'},
                        {'class': 'courriel', 'object': 'reponse5671:' + 'd' * 64, 'revision': '1', 'when': '1', 'matter': 'DOS-006', 'matter_name': 'ORLANDEC 6',
                         'title': 'Réponse ZORVAL réservée'},
                        {'class': 'courriel', 'object': 'reponse5671:' + 'e' * 64, 'revision': '1', 'when': '1', 'matter': 'DOS-001', 'matter_name': 'DURAND 1',
                         'title': 'Réponse DURAND visible'}]
        for it in global_items:
            it.update(key=it['object'], priority='normale', priority_reason='', consequence='', href='/p/mail', choices=('ouvrir',))
        with patch('agent.file5651._global_items', lambda *a, **k: [dict(x) for x in global_items]):
            elements = file5651.items(self.d, '/p', None, '', frozenset(), actor=AVOCATE[0], role=AVOCATE[1])
            page = file5651.html(self.d, '/p', None, 'courriels', frozenset(), 8, actor=AVOCATE[0], role=AVOCATE[1])
        self.assertEqual([x['title'] for x in elements if x['class'] == 'courriel'], ['Réponse DURAND visible'])
        self.assertNotIn('ZORVAL', page)
        self.assertNotIn('ORLANDEC 6', page)

    def test_REGLE_aujourdhui_actuellement_et_files_par_la_garde(self):
        self.recette()

    def test_REGLE_mutation_garde_retiree_aujourdhui_change(self):
        with patch('agent.acces5697.masques', return_value=frozenset()):
            with self.assertRaises(AssertionError):
                self.recette()

    def test_REGLE_mutation_filtre_de_la_file_retire(self):
        with patch('agent.acces5697.filtrer_elements', lambda desk, acteur, elements, caches=None: list(elements)):
            with self.assertRaises(AssertionError):
                self.recette()

    def test_REGLE_mutation_rattachement_du_message_ignore(self):
        with patch('agent.acces5697.visible', lambda dossiers, caches: True):
            with self.assertRaises(AssertionError):
                self.recette()


if __name__ == '__main__':
    unittest.main()
