"""5.6.108 (lot S4) : parcours « Créer un espace » / « Rejoindre le cabinet », indicateur, missions (une seule exécution par attribution),
pauses, routes (rôles, 404 neutre, 409, no-store, aucune écriture pendant une lecture).

Harnais de ``test_v56108_sync``. Données fictives ; aucun modèle (``Desk.enqueue`` interdit pendant les réceptions de résultats).
"""
import io
import json
import secrets
import unittest
from unittest.mock import patch

from agent import sync56108 as S, sync56108_missions as M, sync56108_web as W, dossiers5697 as D, liaison56103 as L, pause5629
from agent.common import Stop
from agent.desk import Desk
from test_v56108_sync import Poste, relier, ADMIN

AUTH = {'origin': 'http://127.0.0.1:8769', 'csrf': 'c' * 40, 'poste': True}


def cle():
    return secrets.token_urlsafe(18)


def env_json(data, role='administrateur', user='cabinet'):
    raw = json.dumps(data).encode('utf-8')
    return {'HTTP_ORIGIN': AUTH['origin'], 'HTTP_X_CSRF_TOKEN': AUTH['csrf'], 'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)),
            'wsgi.input': io.BytesIO(raw), 'HTTP_X_AXIORHUB_USER': user, 'HTTP_X_AXIORHUB_ROLE': role, 'REQUEST_METHOD': 'POST'}


def env_get(role='administrateur', user='cabinet'):
    return {'HTTP_X_AXIORHUB_USER': user, 'HTTP_X_AXIORHUB_ROLE': role, 'REQUEST_METHOD': 'GET'}


def appel(poste, route, data=None, role='administrateur', methode='POST'):
    env = env_json(data or {}, role) if methode == 'POST' else env_get(role)
    r = W.api(env, poste.desk, AUTH, '/agent-courriel', 'm56108/sync/' + route, {}, methode)
    return r['status'], json.loads(r['body']), r


class S4Indicateur(unittest.TestCase):
    def setUp(self):
        self.a, self.b = Poste(self, 'Poste A', 'Me DURAND'), Poste(self, 'Poste B', 'Me MARTIN')
        relier(self, self.a, self.b)
        self.lid = self.a.dossier('Dossier indicateur', 'ZOR-501')
        S.partager_dossier(self.a.desk, self.lid)

    def test_S4_jamais_a_jour_avant_un_echange_metier_puis_a_jour(self):
        self.assertNotEqual(W.indicateur(self.a.desk)['etat'], 'a_jour')
        self.b.sync()
        self.a.sync()
        for p in (self.a, self.b):
            i = W.indicateur(p.desk)
            self.assertEqual(i['etat'], 'a_jour', (p.nom, i['libelle']))
            self.assertIn('dossiers', i['postes'][0]['domaines'])                 # catégories prises en charge par la version de l'autre poste

    def test_S4_poste_absent_modifications_en_attente(self):
        self.b.sync()
        self.a.sync()
        D.modifier(self.a.desk, ADMIN, self.lid, 1, {'titre': 'Modifié hors présence'}, 'cle-absent-0001')
        S.journaliser(self.a.desk)
        self.a.desk.db.execute("UPDATE liaison56103_postes SET contact_le='2026-01-01T00:00:00+00:00' WHERE id=?", (self.b.ident.id,))
        self.a.desk.db.commit()
        i = W.indicateur(self.a.desk)
        self.assertEqual(i['etat'], 'absent')
        self.assertEqual(i['libelle'], 'Poste absent : 1 changement(s) en attente')

    def test_S4_conflit_et_connexion_refusee(self):
        self.b.sync()
        self.a.sync()
        self.a.desk.db.execute("UPDATE sync56108_objets SET conflit=1")
        self.a.desk.db.commit()
        self.assertEqual(W.indicateur(self.a.desk)['etat'], 'conflit')
        self.a.desk.db.execute("UPDATE liaison56103_postes SET erreur='poste_non_appaire'")
        self.a.desk.db.commit()
        self.assertEqual(W.indicateur(self.a.desk)['libelle'], 'Connexion refusée : vérifier l’appairage ou les droits')

    def test_S4_synchronisation_en_cours_avec_volume(self):
        self.b.sync()
        self.a.sync()
        D.modifier(self.a.desk, ADMIN, self.lid, 1, {'titre': 'En cours'}, 'cle-encours-001')
        S.journaliser(self.a.desk)
        i = W.indicateur(self.a.desk)
        self.assertEqual(i['etat'], 'en_cours')
        self.assertIn('1 changement(s)', i['libelle'])

    def test_S4_la_synchronisation_continue_pendant_la_pause_de_l_ia_sans_appel_de_modele(self):
        self.b.sync()
        self.a.desk.setting(pause5629.PAUSE_IA, True)
        self.b.desk.setting(pause5629.PAUSE_IA, True)
        D.modifier(self.a.desk, ADMIN, self.lid, 1, {'titre': 'Pendant la pause'}, 'cle-pause-ia-01')
        with patch.object(Desk, 'enqueue', side_effect=AssertionError('aucun travail ne doit être mis en file')), \
                patch('agent.model.Model.complete', side_effect=AssertionError('aucun appel de modèle'), create=True):
            self.b.sync()
        gid = self.a.objet('dossier', self.lid)['objet']
        self.assertEqual(D.lire(self.b.desk, ADMIN, self.b.local(gid))['titre'], 'Pendant la pause')


class S4Missions(unittest.TestCase):
    def setUp(self):
        self.a, self.b = Poste(self, 'Poste A', 'Me DURAND'), Poste(self, 'Poste B', 'Me MARTIN')
        relier(self, self.a, self.b)
        self.b.sync()

    def attribuer(self, executeur, **kw):
        out = M.attribuer(self.a.desk, 'Analyse du bail ZORVAL', executeur.ident.id, **kw)
        self.b.sync()
        return out['objet']

    def test_S4_une_seule_execution_par_attribution(self):
        m = self.attribuer(self.b)
        self.assertTrue(M.peut_executer(self.b.desk, m))
        self.assertFalse(M.peut_executer(self.a.desk, m))
        with self.assertRaisesRegex(Stop, 'executeur_non_designe'):
            M.rendre_compte(self.a.desk, m, etat='en_cours')
        # opération forgée par un poste non exécuteur (bien signée par lui) : refusée à la réception, avant toute écriture
        mission = self.a.desk.db.execute('SELECT * FROM sync56108_missions WHERE objet=?', (m,)).fetchone()
        with S._tx(self.a.desk):
            op = S.emettre(self.a.desk, mission['espace'], 'mission.resultat', m, {'generation': 1, 'resume': 'faux', 'livrable': '', 'effet': ''})
        with self.assertRaisesRegex(Stop, 'executeur_non_designe'):
            S.recevoir(self.b.desk, op, self.a.ident.id)

    def test_S4_resultat_recu_ne_relance_aucun_travail(self):
        m = self.attribuer(self.b)
        M.rendre_compte(self.b.desk, m, resultat={'resume': 'Analyse livrée', 'livrable': '', 'effet': ''})
        with patch.object(Desk, 'enqueue', side_effect=AssertionError('recevoir un résultat ne met jamais de travail en file')):
            self.a.sync()
        r = self.a.desk.db.execute('SELECT etat, resultat FROM sync56108_missions WHERE objet=?', (m,)).fetchone()
        self.assertEqual(r['etat'], 'terminee')
        self.assertEqual(json.loads(r['resultat'])['resume'], 'Analyse livrée')
        self.assertFalse(M.peut_executer(self.a.desk, m))

    def test_S4_annulation_jamais_defaite_par_un_etat_en_retard(self):
        m = self.attribuer(self.b)
        M.decider(self.a.desk, m, 'annulee')
        M.rendre_compte(self.b.desk, m, etat='en_cours')                              # B n'a pas encore reçu l'annulation
        self.a.sync()
        self.b.sync()
        for p in (self.a, self.b):
            self.assertEqual(p.desk.db.execute('SELECT etat FROM sync56108_missions WHERE objet=?', (m,)).fetchone()[0], 'annulee', p.nom)
            self.assertFalse(M.peut_executer(p.desk, m))

    def test_S4_transfert_par_abandon_confirme(self):
        m = self.attribuer(self.b)
        with self.assertRaisesRegex(Stop, 'executeur_non_designe'):
            M.transferer(self.a.desk, m, self.a.ident.id)                             # seul l'exécuteur abandonne
        M.transferer(self.b.desk, m, self.a.ident.id, 'poste B en congé')
        self.a.sync()
        self.assertTrue(M.peut_executer(self.a.desk, m))
        self.assertFalse(M.peut_executer(self.b.desk, m))

    def test_S4_reprise_forcee_refusee_pour_un_effet_externe_et_explicite_sinon(self):
        m = self.attribuer(self.b, effet_externe=True)
        with self.assertRaisesRegex(Stop, 'reprise_forcee_interdite_effet_externe'):
            M.reprendre_force(self.a.desk, m, confirme=True)
        m2 = self.attribuer(self.b)
        with self.assertRaisesRegex(Stop, 'confirmation_requise'):
            M.reprendre_force(self.a.desk, m2)
        M.reprendre_force(self.a.desk, m2, confirme=True)
        self.assertTrue(M.peut_executer(self.a.desk, m2))
        self.assertIn('reprise forcée', M.etat_lisible(self.a.desk, self.a.desk.db.execute('SELECT * FROM sync56108_missions WHERE objet=?', (m2,)).fetchone()))
        # sans abandon confirmé, une reprise non forcée est refusée à la réception
        with S._tx(self.a.desk):
            op = S.emettre(self.a.desk, S.espace_cabinet(self.a.desk)['id'], 'mission.reprise', m, {'generation': 2, 'executeur': self.a.ident.id,
                                                                                                    'forcee': False, 'motif': 'x'})
        with self.assertRaisesRegex(Stop, 'abandon_non_confirme'):
            S.recevoir(self.b.desk, op, self.a.ident.id)

    def test_S4_attend_le_poste_absent(self):
        m = self.attribuer(self.b)
        self.a.desk.db.execute("UPDATE liaison56103_postes SET contact_le='2026-01-01T00:00:00+00:00' WHERE id=?", (self.b.ident.id,))
        self.a.desk.db.commit()
        row = self.a.desk.db.execute('SELECT * FROM sync56108_missions WHERE objet=?', (m,)).fetchone()
        self.assertEqual(M.etat_lisible(self.a.desk, row), 'Attend le poste « Poste B »')

    def test_S4_pauses_de_mission_et_de_cabinet_distinctes(self):
        m = self.attribuer(self.b)
        M.pause_cabinet(self.a.desk, True, 'audit')
        self.b.sync()
        self.assertFalse(M.peut_executer(self.b.desk, m))
        M.pause_cabinet(self.a.desk, False)
        self.b.sync()
        self.assertTrue(M.peut_executer(self.b.desk, m))
        M.decider(self.a.desk, m, 'pause')
        self.b.sync()
        self.assertFalse(M.peut_executer(self.b.desk, m))
        self.assertFalse(self.b.desk.settings(pause5629.PAUSE_IA, False))           # la pause de l'IA du poste reste un autre réglage

    def test_S4_une_routine_partagee_un_seul_surveillant(self):
        M.attribuer(self.a.desk, 'Surveiller la boîte du cabinet', self.b.ident.id, routine='boite:cabinet')
        with self.assertRaisesRegex(Stop, 'routine_deja_surveillee'):
            M.attribuer(self.a.desk, 'Surveiller encore', self.a.ident.id, routine='boite:cabinet')
        M.attribuer(self.b.desk, 'Surveiller (hors ligne)', self.b.ident.id, routine='boite:autre')
        M.attribuer(self.a.desk, 'Surveiller (hors ligne)', self.a.ident.id, routine='boite:autre')   # attributions concurrentes hors ligne
        self.b.sync()
        self.a.sync()
        execs = [M.peut_executer(p.desk, r['objet']) for p in (self.a, self.b)
                 for r in p.desk.db.execute("SELECT objet FROM sync56108_missions WHERE routine='boite:autre'").fetchall()]
        self.assertEqual(sum(execs), 1)


class S4Routes(unittest.TestCase):
    def test_S4_parcours_creer_rejoindre_et_roles(self):
        a, b = Poste(self, 'Poste A', 'Me DURAND'), Poste(self, 'Poste B', 'Me MARTIN')
        html = W.section_html(a.desk, AUTH, '/agent-courriel', env_get())
        self.assertIn('Créer un espace partagé', html)
        self.assertIn('Rejoindre le cabinet', html)
        self.assertIn('nouveau', html)
        self.assertNotIn('essai', html.lower())
        self.assertIn('Réseaux privés', html)
        self.assertEqual(W.section_html(a.desk, {'poste': False}, '', env_get()), '')
        st, body, r = appel(a, 'creer', {'nom': 'Cabinet ZORVAL', 'personne': 'Me DURAND', 'cle': cle()}, role='avocat')
        self.assertEqual(st, '403 Forbidden')
        k = cle()
        st, body, r = appel(a, 'creer', {'nom': 'Cabinet ZORVAL', 'personne': 'Me DURAND', 'cle': k})
        self.assertEqual(st, '200 OK', body)
        self.assertIn(('Cache-Control', 'private, no-store'), r['headers'])
        self.assertEqual(appel(a, 'creer', {'nom': 'Cabinet ZORVAL', 'personne': 'Me DURAND', 'cle': k})[1], body)   # même clé : même reçu
        self.assertEqual(appel(a, 'creer', {'nom': 'Autre', 'personne': 'Me DURAND', 'cle': k})[0], '409 Conflict')
        self.assertEqual(appel(a, 'creer', {'nom': 'Autre', 'personne': 'Me DURAND'})[0], '400 Bad Request')
        st, body, _ = appel(b, 'rejoindre', {'personne': 'Me MARTIN', 'cle': cle()})
        self.assertEqual(st, '200 OK')
        self.assertIn('Rejoindre le cabinet', W.section_html(b.desk, AUTH, '/agent-courriel', env_get()))
        avant = a.desk.db.total_changes
        st, etat, r = appel(a, 'etat', methode='GET', role='assistant')
        self.assertEqual((st, etat['etat']), ('200 OK', 'seul'))
        self.assertEqual(a.desk.db.total_changes, avant)                           # aucune écriture pendant une lecture
        self.assertNotIn('adhesions', etat)                                          # rien d'administratif pour un assistant

    def test_S4_partage_et_objet_invisible_meme_404(self):
        a, b = Poste(self, 'Poste A', 'Me DURAND'), Poste(self, 'Poste B', 'Me MARTIN')
        relier(self, a, b)
        lid = a.dossier('Dossier réservé', 'ZOR-601', confidentialite='reserve', membres=['cabinet'])
        r1 = appel(a, 'partager', {'dossier': lid, 'cle': cle()}, role='avocat')
        r2 = appel(a, 'partager', {'dossier': 'inexistant', 'cle': cle()}, role='avocat')
        from agent import membres5650 as mb
        mb.set_reserve(a.c['state_dir'], lid, ['autre@example.test'])
        with patch('agent.web567.actor', return_value=('tiers@example.test', 'avocat')):
            r3 = W.api(env_json({'dossier': lid, 'cle': cle()}, 'avocat', 'tiers@example.test'), a.desk, AUTH, '', 'm56108/sync/partager', {}, 'POST')
        self.assertEqual(r2[0], '404 Not Found')
        self.assertEqual(r3['status'], '404 Not Found')
        self.assertEqual(json.loads(r3['body']), r2[1])                            # absent et interdit : même réponse
        self.assertEqual(r1[0], '400 Bad Request')                                  # réservé sans personne autorisée : refus clair
        pb = S.personne_locale(b.desk)['gid']
        mb.set_reserve(a.c['state_dir'], lid, ['cabinet'])
        st, body, _ = appel(a, 'partager', {'dossier': lid, 'p_' + pb: True, 'cle': cle()}, role='avocat')
        self.assertEqual(st, '200 OK', body)

    def test_S4_desactivation_par_route_ferme_l_ecoute(self):
        a, b = Poste(self, 'Poste A', 'Me DURAND'), Poste(self, 'Poste B', 'Me MARTIN')
        relier(self, a, b)
        L._consommer_code(a.c['state_dir'], True)
        self.assertTrue(L.transport_actif(a.desk))
        self.assertEqual(appel(a, 'desactiver', {'desactive': '1', 'cle': cle()}, role='avocat')[0], '403 Forbidden')
        self.assertEqual(appel(a, 'desactiver', {'desactive': '1', 'cle': cle()})[0], '200 OK')
        self.assertFalse(L.transport_actif(a.desk))
        self.assertIn('Désactivée sur ce poste', W.section_html(a.desk, AUTH, '', env_get()))
        st, body, _ = appel(b, 'etat', methode='GET')
        self.assertEqual(st, '200 OK')

    def test_S4_routes_declarees_et_roles(self):
        from agent import standalone_auth
        self.assertTrue(W.allowed('assistant', 'GET', 'm56108/sync/etat'))
        self.assertFalse(W.allowed('assistant', 'POST', 'm56108/sync/partager'))
        self.assertTrue(W.allowed('avocat', 'POST', 'm56108/sync/resoudre'))
        self.assertFalse(W.allowed('avocat', 'POST', 'm56108/sync/creer'))
        self.assertTrue(W.allowed('administrateur', 'POST', 'm56108/sync/creer'))
        self.assertFalse(W.allowed('administrateur', 'DELETE', 'm56108/sync/creer'))
        self.assertTrue(standalone_auth.allowed('avocat', 'POST', '/api440/m56108/sync/resoudre'))
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/m56108/sync/creer'))


if __name__ == '__main__':
    unittest.main()
