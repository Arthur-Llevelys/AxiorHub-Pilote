"""5.6.97, lot dossiers (B03, § 9 à § 12) : registre unique des affaires, API ``m57/dossiers``, migration rejouable, écrans Dossiers et
Nouveau dossier, classement des courriels à la main, fiche.

Faux stockage en mémoire (chaque MKCOL est noté), faux comptes, aucun réseau (tout appel de socket échoue), aucun modèle ; données
fictives seulement (DURAND, MARTIN, BERNARD, ZORVAL, DOS-001, RG 24/01234, example.test)."""
import io
import json
import socket
import threading
import unittest
import uuid
from unittest.mock import patch

from agent import dossiers5697 as D, membres5650
from agent.common import Stop, load_matters
from agent.desk import Desk
import test_agent as fixtures

ORIGINE, JETON = 'https://pilote.example.test', 'jeton-csrf-fictif'
CLAIRE, CAMILLE = 'claire@example.test', 'camille@example.test'
ADMIN = ('cabinet', 'administrateur')
AVOCATE = (CLAIRE, 'avocat')


def _reseau_interdit(*a, **k):
    raise AssertionError('appel réseau non simulé')


def setUpModule():
    global _p1, _p2
    _p1 = patch.object(socket.socket, 'connect', _reseau_interdit)
    _p2 = patch.object(socket, 'create_connection', _reseau_interdit)
    _p1.start()
    _p2.start()


def tearDownModule():
    _p1.stop()
    _p2.stop()


class FauxStockage:
    """Stockage en mémoire : répertoires connus, MKCOL notés ; ``panne_apres_mkcol`` simule une réponse perdue après création."""

    def __init__(self, racines=('/Dossiers', '/Dossiers/DEMO')):
        self.repertoires = set(racines)
        self.mkcol = []
        self.panne_apres_mkcol = False
        self.hors_ligne = False

    def list_folder(self, chemin):
        if self.hors_ligne:
            raise Stop('stockage_injoignable')
        if chemin not in self.repertoires:
            raise Stop('fichier_nextcloud_introuvable')
        return [{'path': r, 'directory': True} for r in sorted(self.repertoires) if r.rsplit('/', 1)[0] == chemin]

    def create_folder(self, chemin):
        if self.hors_ligne:
            raise Stop('stockage_injoignable')
        self.mkcol.append(chemin)
        self.repertoires.add(chemin)
        if self.panne_apres_mkcol:
            self.panne_apres_mkcol = False
            raise Stop('stockage_injoignable')

    def ensure_folder(self, chemin, borne):
        if chemin not in self.repertoires:
            self.mkcol.append(chemin)
            self.repertoires.add(chemin)


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        self.addCleanup(membres5650.clear)
        self.stock = FauxStockage()
        p = patch.object(D, '_client_stockage', lambda desk: self.stock)
        p.start()
        self.addCleanup(p.stop)

    def creer(self, acteur=ADMIN, cle=None, **donnees):
        donnees.setdefault('titre', 'DURAND c/ MARTIN')
        return D.creer(self.desk, acteur, donnees, cle or uuid.uuid4().hex)

    def appel(self, route, data=None, owner='cabinet', role='administrateur', args=None, methode=None):
        from agent import web440
        corps = json.dumps(data or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': owner, 'HTTP_X_AXIORHUB_ROLE': role, 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON,
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps),
               'REQUEST_METHOD': methode or ('GET' if data is None else 'POST')}
        out = web440.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm57/' + route, args or {}, env['REQUEST_METHOD'])
        return out['status'][:3], json.loads(out['body'])

    def reserver(self, ident, membres):
        membres5650.set_reserve(self.f.c['state_dir'], ident, membres)


# ------------------------------------------------------------------------------------------------ B03 : créer sans perdre ni bloquer
class B03Registre(Base):
    def test_B03_reference_invalide_zero_mkcol(self):
        with self.assertRaises(D.Refus) as ctx:
            self.creer(reference='###')
        self.assertEqual(str(ctx.exception), 'champs_invalides')
        self.assertIn('reference', ctx.exception.detail)
        self.assertEqual(self.stock.mkcol, [])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM dossiers5697').fetchone()[0], 0)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM dossier_outbox5697').fetchone()[0], 0)

    def test_B03_ancien_chemin_reference_invalide_zero_mkcol(self):
        """Ancien formulaire « Créer un dossier » : la référence était vérifiée APRÈS le MKCOL (répertoire orphelin)."""
        from agent import workspace
        appels = []

        class Dav:
            def create_folder(self, p):
                appels.append(('MKCOL', p))

            def list_folder(self, p):
                appels.append(('PROPFIND', p))
                return []
        with self.assertRaises(Stop) as ctx:
            workspace.create_matter(self.desk, {'confirm': 'yes', 'path': '/Dossiers/ZORVAL', 'reference': '###', 'client_name': 'ZORVAL'}, Dav())
        self.assertEqual(str(ctx.exception), 'reference_invalide')
        self.assertEqual(appels, [])

    def test_B03_ancien_chemin_entre_au_registre_unique(self):
        from agent import workspace

        class Dav:
            def create_folder(self, p):
                pass

            def list_folder(self, p):
                return []
        with patch.object(self.desk, 'enqueue', lambda *a, **k: 1):
            out = workspace.create_matter(self.desk, {'confirm': 'yes', 'path': '/Dossiers/ZORVAL - 20260101001', 'reference': '20260101001',
                                                      'client_name': 'ZORVAL'}, Dav())
        self.assertEqual(out['dossier'], '20260101001')
        self.assertEqual(D.lire(self.desk, ADMIN, '20260101001')['chemin'], '/Dossiers/ZORVAL - 20260101001')
        with self.assertRaises(Stop):   # même référence par le registre unique : refus avant tout stockage
            self.creer(reference='20260101001')

    def test_B03_reservation_avant_stockage_puis_rangement(self):
        recu = self.creer(client='DURAND', nature='contentieux')
        self.assertEqual(self.stock.mkcol, [])   # rien dans le stockage avant la validation
        self.assertEqual(recu['message'], 'Dossier créé · rangement en préparation')
        self.assertEqual(recu['stockage']['etat'], 'a_preparer')
        fait = D.traiter_rangements(self.desk)
        self.assertEqual(fait[0]['repertoire'], 'cree')
        self.assertEqual(self.stock.mkcol[0], recu['stockage']['chemin'])
        self.assertIn('CORRESPONDANCES', ' '.join(self.stock.mkcol))
        self.assertEqual(D.lire(self.desk, ADMIN, recu['id'])['stockage']['etat'], 'pret')
        self.assertEqual(D.traiter_rangements(self.desk), [])   # plus rien en file

    def test_B03_panne_apres_mkcol_reprise_sans_nouveau_repertoire(self):
        recu = self.creer()
        self.stock.panne_apres_mkcol = True
        premier = D.traiter_rangements(self.desk)[0]
        self.assertEqual(premier['etat'], 'en_attente')
        self.assertEqual(len([m for m in self.stock.mkcol if m == recu['stockage']['chemin']]), 1)
        self.assertEqual(D.lire(self.desk, ADMIN, recu['id'])['stockage']['etat'], 'en_attente')
        D.reprendre_rangement(self.desk, ADMIN, recu['id'], uuid.uuid4().hex)
        second = D.traiter_rangements(self.desk)[0]
        self.assertEqual(second['repertoire'], 'retrouve')
        self.assertEqual(len([m for m in self.stock.mkcol if m == recu['stockage']['chemin']]), 1)   # aucun second MKCOL
        self.assertEqual(second['dossier'], recu['id'])   # reprise par le même identifiant
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM dossiers5697').fetchone()[0], 1)

    def test_B03_stockage_hors_ligne_affaire_existe_avec_motif(self):
        self.stock.hors_ligne = True
        recu = self.creer()
        D.traiter_rangements(self.desk)
        d = D.lire(self.desk, ADMIN, recu['id'])
        self.assertEqual(d['stockage']['etat'], 'en_attente')
        self.assertTrue(d['stockage']['motif'])
        self.assertEqual(self.stock.mkcol, [])

    def test_B03_rejeu_meme_cle_un_seul_dossier(self):
        cle = uuid.uuid4().hex
        a = self.creer(cle=cle, client='DURAND')
        b = self.creer(cle=cle, client='DURAND')
        self.assertEqual(a['id'], b['id'])
        self.assertTrue(b['rejoue'])
        with self.assertRaises(Stop) as ctx:   # même clé, contenu modifié : 409
            self.creer(cle=cle, client='MARTIN')
        self.assertEqual(str(ctx.exception), 'cle_idempotence_deja_utilisee')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM dossiers5697').fetchone()[0], 1)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM dossier_outbox5697').fetchone()[0], 1)

    def test_B03_creations_concurrentes_jamais_de_dossier_perdu(self):
        """Huit créations simultanées (connexions distinctes) : huit dossiers distincts, huit entrées dans le registre JSON projeté."""
        resultats, erreurs = [], []

        def un(i):
            desk = Desk(self.f.c)
            try:
                resultats.append(D.creer(desk, ADMIN, {'titre': 'BERNARD %d' % i}, uuid.uuid4().hex)['id'])
            except Exception as ex:   # noqa: BLE001
                erreurs.append(repr(ex))
            finally:
                desk.db.close()
        fils = [threading.Thread(target=un, args=(i,)) for i in range(8)]
        for t in fils:
            t.start()
        for t in fils:
            t.join()
        self.assertEqual(erreurs, [])
        self.assertEqual(len(set(resultats)), 8)
        projetes = {m['id'] for m in load_matters(self.f.c, unfiltered=True)}
        self.assertTrue(set(resultats) <= projetes)
        refs = [r[0] for r in self.desk.db.execute('SELECT ref FROM dossiers5697')]
        self.assertEqual(len(refs), len(set(refs)))

    def test_B03_meme_reference_concurrente_un_seul_succes(self):
        resultats = []

        def un():
            desk = Desk(self.f.c)
            try:
                resultats.append(D.creer(desk, ADMIN, {'titre': 'KERVILAC', 'reference': 'DOS-777'}, uuid.uuid4().hex)['id'])
            except Stop as ex:
                resultats.append(str(ex))
            finally:
                desk.db.close()
        fils = [threading.Thread(target=un) for _ in range(4)]
        for t in fils:
            t.start()
        for t in fils:
            t.join()
        self.assertEqual(sum(1 for r in resultats if r == 'reference_deja_utilisee'), 3)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM dossiers5697 WHERE ref='DOS-777'").fetchone()[0], 1)

    def test_B03_projection_json_sous_verrou(self):
        """Deux enregistrements simultanés du registre JSON (ancien chemin) : aucun n'est perdu."""
        from agent.desk import save_matter
        fils = [threading.Thread(target=save_matter, args=(self.f.c, {'id': 'X%d' % i, 'path': '/Dossiers/X%d' % i, 'client_name': 'X',
                                                                        'aliases': [], 'references': [], 'correspondents': []}))
                for i in range(10)]
        for t in fils:
            t.start()
        for t in fils:
            t.join()
        ids = {m['id'] for m in load_matters(self.f.c, unfiltered=True)}
        self.assertTrue({'X%d' % i for i in range(10)} <= ids)



# ------------------------------------------------------------------------------------------------ § 10 : API m57/dossiers
class S10Api(Base):
    def test_S10_api_201_puis_rejeu_200(self):
        cle = uuid.uuid4().hex
        corps_req = {'titre': 'DURAND c/ MARTIN', 'client': 'DURAND', 'nature': 'contentieux', 'cle': cle,
                     'parties': [{'nom': 'MARTIN', 'role': 'adverse'}]}
        code, corps = self.appel('dossiers', corps_req, owner=CLAIRE, role='avocat')
        self.assertEqual(code, '201', corps)
        self.assertEqual(corps['version'], 1)
        self.assertTrue(corps['url'].startswith('/p/dossier?id='))
        self.assertEqual(corps['stockage']['etat'], 'pret')   # première tentative de rangement faite après validation
        self.assertIn('recu', corps)
        self.assertEqual(D.lire(self.desk, AVOCATE, corps['id'])['responsable'], CLAIRE)   # responsable = compte connecté par défaut
        code2, corps2 = self.appel('dossiers', corps_req, owner=CLAIRE, role='avocat')
        self.assertEqual((code2, corps2['id']), ('200', corps['id']))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM dossiers5697').fetchone()[0], 1)

    def test_S10_api_422_champs_invalides(self):
        code, corps = self.appel('dossiers', {'titre': '', 'reference': '###', 'nature': 'autre', 'cle': uuid.uuid4().hex})
        self.assertEqual(code, '422')
        self.assertTrue({'titre', 'reference', 'nature'} <= set(corps['champs']))
        self.assertEqual(self.stock.mkcol, [])

    def test_S10_api_409_reference_en_double(self):
        self.appel('dossiers', {'titre': 'ZORVAL', 'reference': 'dos 900', 'cle': uuid.uuid4().hex})
        code, corps = self.appel('dossiers', {'titre': 'ZORVAL bis', 'reference': 'DOS-900', 'cle': uuid.uuid4().hex})
        self.assertEqual(code, '409')
        self.assertEqual(corps['error'], 'reference_deja_utilisee')
        self.assertIn('proposition', corps['champs'])

    def test_S10_acteur_du_corps_ignore(self):
        code, corps = self.appel('dossiers', {'titre': 'BERNARD', 'cle': uuid.uuid4().hex, 'acteur': 'cabinet', 'auteur': 'cabinet',
                                              'cabinet_id': 'autre'}, owner=CAMILLE, role='assistant')
        self.assertEqual(code, '201')
        row = self.desk.db.execute('SELECT auteur,cabinet_id FROM dossiers5697 WHERE id=?', (corps['id'],)).fetchone()
        self.assertEqual(row['auteur'], CAMILLE)
        self.assertNotEqual(row['cabinet_id'], 'autre')

    def test_S10_acces_refuse_neutre(self):
        """Dossier réservé et dossier absent : même 404, même corps ; ni lecture, ni modification, ni liste."""
        recu = self.creer(titre='KERVILAC confidentiel', confidentialite='reserve', membres=[CLAIRE])
        reserve = self.appel('dossiers/' + recu['id'], owner=CAMILLE, role='avocat')
        absent = self.appel('dossiers/' + uuid.uuid4().hex, owner=CAMILLE, role='avocat')
        self.assertEqual(reserve, absent)
        self.assertEqual(reserve[0], '404')
        modif = self.appel('dossiers/' + recu['id'], {'version': 1, 'titre': 'X', 'cle': uuid.uuid4().hex}, owner=CAMILLE, role='avocat', methode='PATCH')
        self.assertEqual(modif[0], '404')
        liste = self.appel('dossiers', owner=CAMILLE, role='avocat', args={'q': 'KERVILAC'})[1]['dossiers']
        self.assertEqual(liste, [])
        self.assertEqual(self.appel('dossiers/' + recu['id'], owner=CLAIRE, role='avocat')[0], '200')

    def test_S10_garde_neutre_mutation(self):
        """Mutation en mémoire : si la garde commune laissait tout passer, le dossier réservé deviendrait lisible (le test le verrait)."""
        recu = self.creer(titre='KERVILAC confidentiel', confidentialite='reserve', membres=[CLAIRE])
        with patch('agent.acces5697.peut_lire_dossier', lambda *a: True):
            self.assertEqual(self.appel('dossiers/' + recu['id'], owner=CAMILLE, role='avocat')[0], '200')
        self.assertEqual(self.appel('dossiers/' + recu['id'], owner=CAMILLE, role='avocat')[0], '404')

    def test_S10_patch_version_attendue(self):
        recu = self.creer(client='DURAND')
        code, corps = self.appel('dossiers/' + recu['id'], {'version': 1, 'titre': 'DURAND contre MARTIN', 'cle': uuid.uuid4().hex}, methode='PATCH')
        self.assertEqual((code, corps['version']), ('200', 2))
        code, corps = self.appel('dossiers/' + recu['id'], {'version': 1, 'titre': 'Autre', 'cle': uuid.uuid4().hex}, methode='PATCH')
        self.assertEqual((code, corps['error']), ('409', 'version_perimee'))
        self.assertEqual(D.lire(self.desk, ADMIN, recu['id'])['titre'], 'DURAND contre MARTIN')

    def test_S10_reference_revisee_ancien_lien_valable(self):
        recu = self.creer(reference='DOS-001B')
        D.modifier(self.desk, ADMIN, recu['id'], 1, {'reference': 'DOS-002'}, uuid.uuid4().hex)
        self.assertEqual(D.lire(self.desk, ADMIN, 'DOS-001B')['id'], recu['id'])
        self.assertEqual(D.lire(self.desk, ADMIN, 'DOS-002')['id'], recu['id'])

    def test_S10_routes_declarees_roles(self):
        from agent.standalone_auth import allowed
        self.assertTrue(allowed('assistant', 'POST', '/api440/m57/dossiers'))
        self.assertTrue(allowed('avocat', 'PATCH', '/api440/m57/dossiers/abc'))
        self.assertFalse(allowed('avocat', 'POST', '/api440/m57/dossiers/migration'))
        self.assertFalse(allowed('avocat', 'DELETE', '/api440/m57/dossiers/abc'))
        self.assertFalse(allowed('avocat', 'POST', '/api440/m57/dossiers/abc/inconnue'))
        code, corps = self.appel('dossiers/migration', {'simulation': True, 'cle': uuid.uuid4().hex}, owner=CLAIRE, role='avocat')
        self.assertEqual(code, '403')

    def test_S10_csrf_exige(self):
        from agent import web440
        corps = json.dumps({'titre': 'X', 'cle': uuid.uuid4().hex}).encode()
        env = {'HTTP_X_AXIORHUB_USER': CLAIRE, 'HTTP_X_AXIORHUB_ROLE': 'avocat', 'HTTP_ORIGIN': 'https://autre.example.test', 'HTTP_X_CSRF_TOKEN': 'x',
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps)}
        out = web440.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm57/dossiers', {}, 'POST')
        self.assertEqual(out['status'][:3], '403')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM dossiers5697').fetchone()[0], 0)

    def test_S10_sans_ia_et_en_pause(self):
        """Commandes de l'avocat sans IA : pause globale et pause du dossier actives, passerelle des modèles et HTTP bloqués, réseau
        coupé ; création, lecture, modification, classement et rangement fonctionnent."""
        self.desk.setting('missions567:pause', True)
        self.desk.setting('pause5629:matters', {'DOS-001': {'at': 'x'}})
        bloque = AssertionError('appel au modèle ou au réseau')
        with patch('agent.ai_gateway.estimate_and_check_budget', side_effect=bloque), \
                patch('agent.common.HTTP.request', side_effect=bloque):
            code, recu = self.appel('dossiers', {'titre': 'ORLANDEC', 'client': 'ORLANDEC', 'cle': uuid.uuid4().hex}, owner=CLAIRE, role='avocat')
            self.assertEqual(code, '201', recu)
            self.assertEqual(recu['stockage']['etat'], 'pret')
            self.assertEqual(self.appel('dossiers/' + recu['id'], owner=CLAIRE, role='avocat')[0], '200')
            self.assertEqual(self.appel('dossiers/' + recu['id'], {'version': 1, 'nature': 'conseil', 'cle': uuid.uuid4().hex},
                                        owner=CLAIRE, role='avocat', methode='PATCH')[0], '200')
            code, classe = self.appel('dossiers/%s/courriels' % recu['id'], {'cle': uuid.uuid4().hex, 'message': {
                'boite': 'INBOX', 'uidvalidity': '7', 'uid': '101', 'message_id': '<m101@example.test>', 'sujet': 'Pièces ORLANDEC'}},
                owner=CLAIRE, role='avocat')
            self.assertEqual((code, classe['etat']), ('200', 'classe'))


# ------------------------------------------------------------------------------------------------ § 10 : migration rejouable
class S10Migration(Base):
    def ajouter_json(self, **m):
        from agent.desk import save_matter
        save_matter(self.f.c, {'aliases': [], 'references': [], 'correspondents': [], **m})

    def test_S10_simulation_n_ecrit_rien(self):
        rapport = D.migrer(self.desk, ADMIN, simulation=True)
        self.assertEqual([x['id'] for x in rapport['a_importer']], ['DOS-001'])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM dossiers5697').fetchone()[0], 0)
        self.assertFalse(D.migration_faite(self.desk))

    def test_S10_migration_rejouable_avec_alias(self):
        self.ajouter_json(id='20240101001', path='/Dossiers/DURAND - CONTENTIEUX - 20240101001', client_name='DURAND',
                          references=['20240101001', 'RG 24/01234'], discovered_at='2026-01-01T00:00:00+00:00')
        premier = D.migrer(self.desk, ADMIN, simulation=False)
        self.assertEqual(len(premier['a_importer']), 2)
        self.assertTrue(premier['sauvegarde'])
        second = D.migrer(self.desk, ADMIN, simulation=False)
        self.assertEqual((second['a_importer'], second['deja_importes']), ([], 2))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM dossiers5697').fetchone()[0], 2)
        d = D.lire(self.desk, ADMIN, '20240101001')   # ancien identifiant (liens /dossier, /fiche, missions) : conservé
        self.assertEqual((d['provenance'], d['nature'], d['stockage']['etat']), ('detecte', 'contentieux', 'pret'))
        self.assertEqual(D.lire(self.desk, ADMIN, '/Dossiers/DURAND - CONTENTIEUX - 20240101001')['id'], '20240101001')   # alias de chemin
        self.assertEqual(D.lire(self.desk, ADMIN, 'DOS-001')['provenance'], 'migre')
        self.assertEqual([p['nom'] for p in D.lire(self.desk, ADMIN, 'DOS-001')['parties'] if p['role'] == 'client'], ['Client DEMO'])

    def test_S10_conflit_sans_fusion_silencieuse(self):
        recu = self.creer(titre='DEMO', emplacement={'mode': 'rattacher', 'chemin': '/Dossiers/AUTRE'})
        self.ajouter_json(id='DOS-050', path='/Dossiers/AUTRE', client_name='ZORVAL')
        rapport = D.migrer(self.desk, ADMIN, simulation=False)
        self.assertIn({'id': 'DOS-050', 'motif': 'chemin_deja_rattache', 'autre': recu['id']}, rapport['conflits'])
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM dossiers5697 WHERE id='DOS-050'").fetchone()[0], 0)

    def test_S10_dossier_reserve_reste_reserve(self):
        self.reserver('DOS-001', [CLAIRE])
        D.migrer(self.desk, ADMIN, simulation=False)
        self.assertEqual(D.lire(self.desk, AVOCATE, 'DOS-001')['confidentialite'], 'reserve')
        with self.assertRaises(Stop):
            D.lire(self.desk, (CAMILLE, 'avocat'), 'DOS-001')

    def test_S10_avant_migration_liste_lisible(self):
        liste = D.lister(self.desk, AVOCATE)
        self.assertEqual([(x['id'], x['provenance']) for x in liste], [('DOS-001', 'non_importe')])
        self.reserver('DOS-001', [CLAIRE])
        self.assertEqual(D.lister(self.desk, (CAMILLE, 'avocat')), [])

    def test_S10_migration_reservee_administrateur(self):
        with self.assertRaises(Stop):
            D.migrer(self.desk, AVOCATE, simulation=False)


# ------------------------------------------------------------------------------------------------ § 11 : classer un courriel à la main
def message(uid, mid=None, boite='INBOX'):
    return {'boite': boite, 'uidvalidity': '7', 'uid': str(uid), 'message_id': mid if mid is not None else '<m%s@example.test>' % uid,
            'sujet': 'Pièces DURAND', 'expediteur': 'claire.durand@example.test'}


class S11Classement(Base):
    def cle_message(self, uid, boite='INBOX'):
        from agent.messages5697 import cle_canonique, compte_de
        return cle_canonique(compte_de(self.f.c['mail']), boite, '7', str(uid))

    def test_S11_recette_creer_classer_rejouer_changer(self):
        """Créer depuis un courriel, classer un deuxième, relancer le premier après délai réseau, changer de dossier : un seul dossier,
        un lien par message, aucune commande IMAP ni SMTP (aucune connexion n'est même ouverte)."""
        with patch('agent.mailbox.connect', side_effect=AssertionError('IMAP')), patch('smtplib.SMTP', side_effect=AssertionError('SMTP')):
            cle = uuid.uuid4().hex
            a = D.creer(self.desk, AVOCATE, {'titre': 'DURAND', 'client': 'DURAND'}, cle, message=message(101))
            b = D.creer(self.desk, AVOCATE, {'titre': 'DURAND', 'client': 'DURAND'}, cle, message=message(101))   # réponse perdue, rejeu
            self.assertEqual(a['id'], b['id'])
            r2 = D.rattacher_message(self.desk, AVOCATE, a['id'], message(102), uuid.uuid4().hex)
            self.assertEqual(r2['etat'], 'classe')
            self.assertIn('Classé dans DURAND', r2['message'])
            again = D.rattacher_message(self.desk, AVOCATE, a['id'], message(101), uuid.uuid4().hex)
            self.assertEqual(again['etat'], 'deja_classe')
            autre = self.creer(acteur=AVOCATE, titre='MARTIN')
            with self.assertRaises(Stop) as ctx:   # changement de dossier sans version attendue : refus explicite
                D.rattacher_message(self.desk, AVOCATE, autre['id'], message(101), uuid.uuid4().hex)
            self.assertEqual(str(ctx.exception), 'version_perimee')
            change = D.rattacher_message(self.desk, AVOCATE, autre['id'], message(101), uuid.uuid4().hex, version=1, motif='Erreur de dossier')
            self.assertEqual((change['etat'], change['version']), ('reclasse', 2))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM dossiers5697').fetchone()[0], 2)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM message_dossier5697').fetchone()[0], 2)
        actions = [r[0] for r in self.desk.db.execute('SELECT action FROM dossier_journal5697 WHERE dossier_id=?', (a['id'],))]
        self.assertIn('courriel_declasse', actions)   # journal conservé : l'ancien lien n'est pas effacé en silence

    def test_S11_message_deplace_reconcilie_par_message_id(self):
        d = self.creer(acteur=AVOCATE)
        D.rattacher_message(self.desk, AVOCATE, d['id'], message(101), uuid.uuid4().hex)
        r = D.rattacher_message(self.desk, AVOCATE, d['id'], message(555, mid='<m101@example.test>', boite='Archives'), uuid.uuid4().hex)
        self.assertEqual(r['etat'], 'deja_classe')
        self.assertEqual(D.dossier_du_message(self.desk, boite='Archives', uidvalidity='7', uid='555', message_id='<m101@example.test>'), d['id'])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM message_dossier5697').fetchone()[0], 1)

    def test_S11_crochet_du_resolveur_des_messages(self):
        from agent import messages5697
        d = self.creer(acteur=AVOCATE)
        D.rattacher_message(self.desk, AVOCATE, d['id'], message(101), uuid.uuid4().hex)
        self.assertIn(d['id'], messages5697.resoudre(self.desk, self.cle_message(101)).dossiers)
        self.assertEqual(messages5697.resoudre(self.desk, self.cle_message(102)).dossiers, frozenset())   # le fil n'hérite de rien

    def test_S11_B01_message_d_un_dossier_reserve_refus_neutre(self):
        """Message déjà classé dans un dossier réservé : le classer ailleurs, le reclasser ou créer un dossier depuis lui renvoie la même
        réponse neutre qu'un dossier absent ; le rattachement ne donne aucun droit sur les autres messages du fil."""
        cache = self.creer(titre='KERVILAC', confidentialite='reserve', membres=[CLAIRE])
        D.rattacher_message(self.desk, AVOCATE, cache['id'], message(101), uuid.uuid4().hex)
        visible = self.creer(acteur=(CAMILLE, 'avocat'), titre='ZORVAL')
        camille = (CAMILLE, 'avocat')
        for appel in (lambda: D.rattacher_message(self.desk, camille, visible['id'], message(101), uuid.uuid4().hex, version=1),
                      lambda: D.creer(self.desk, camille, {'titre': 'Nouveau'}, uuid.uuid4().hex, message=message(101)),
                      lambda: D.rattacher_message(self.desk, camille, cache['id'], message(103), uuid.uuid4().hex)):
            with self.assertRaises(Stop) as ctx:
                appel()
            self.assertEqual(str(ctx.exception), 'introuvable')
        from agent.acces5697 import peut_lire_message
        self.assertFalse(peut_lire_message(self.desk, camille, self.cle_message(101)))
        self.assertTrue(peut_lire_message(self.desk, camille, self.cle_message(102)))   # autre message du fil : non rattaché, non concerné
        self.assertEqual(D.dossier_du_message(self.desk, boite='INBOX', uidvalidity='7', uid='101', message_id='<m101@example.test>'), cache['id'])

    def test_S11_B01_mutation_garde_retiree(self):
        cache = self.creer(titre='KERVILAC', confidentialite='reserve', membres=[CLAIRE])
        D.rattacher_message(self.desk, AVOCATE, cache['id'], message(101), uuid.uuid4().hex)
        visible = self.creer(acteur=(CAMILLE, 'avocat'), titre='ZORVAL')
        with patch('agent.acces5697.exiger', lambda *a, **k: None), patch.object(D, '_visible', lambda *a: True):
            r = D.rattacher_message(self.desk, (CAMILLE, 'avocat'), visible['id'], message(101), uuid.uuid4().hex, version=1)
        self.assertEqual(r['etat'], 'reclasse')   # sans la garde, le message aurait été détourné : le test précédent le voit

    def test_S11_choix_manuel_prioritaire_dans_la_boite(self):
        from agent import boite5625

        class Courriel:
            mailbox, uidvalidity, uid, mid, subject, text = 'INBOX', '7', '101', '<m101@example.test>', 'Pièces', 'texte'
            msg = {'From': 'claire.durand@example.test'}
        d = self.creer(acteur=AVOCATE, titre='DURAND')
        D.rattacher_message(self.desk, AVOCATE, d['id'], message(101), uuid.uuid4().hex)
        self.assertEqual(boite5625._guess_matter(self.desk, Courriel(), {'matter': 'DOS-001'}), (d['id'], 'classé à la main'))


class S12Gestion(Base):
    def test_S11_classement_dans_un_dossier_archive_signale(self):
        """Un courriel classé dans un dossier clôturé est signalé au responsable (lot gestion), sans rouvrir le dossier ; un rejeu ne signale pas deux fois."""
        from agent import cloture5697
        d = self.creer(acteur=AVOCATE, titre='DURAND')
        with patch.object(cloture5697, 'est_archive', lambda desk, mid: mid == d['id']):
            D.rattacher_message(self.desk, AVOCATE, d['id'], message(201), uuid.uuid4().hex)
            D.rattacher_message(self.desk, AVOCATE, d['id'], message(201), uuid.uuid4().hex)
        lignes = self.desk.db.execute('SELECT nature FROM entrees_archivees5697 WHERE matter=?', (d['id'],)).fetchall()
        self.assertEqual([r[0] for r in lignes], ['courriel'])
        self.assertEqual(D.lire(self.desk, AVOCATE, d['id'])['statut'], 'actif')

    def test_S12_fiche_sections_et_cloture(self):
        from agent import dossier5679
        d = self.creer(acteur=ADMIN, titre='ZORVAL', client='ZORVAL')
        D.traiter_rangements(self.desk)

        def shell(titre, corps, *a):
            return corps
        for vue, attendu in (('gestion', 'Préparer la clôture'), ('agenda', 'Dates confirmées'), ('echanges', 'Courriels classés à la main'),
                             ('cloture', 'Retour à Gestion')):
            page = dossier5679.page(self.desk, {'csrf': JETON, 'owner': 'cabinet'}, '/p', {'id': d['id'], 'vue': vue}, shell)
            self.assertIn(attendu, page, vue)
            self.assertIn('d97-fiche-tete', page, vue)

if __name__ == '__main__':
    unittest.main()
