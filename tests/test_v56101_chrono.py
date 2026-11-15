"""5.6.101 (lot M, cahier § 9) : mesure facultative du temps de traitement (M01-M04, V101-11).

- M01 : désactivée par défaut ; chaque personne l'active dans Mon compte, pour elle seule (409 si l'état a changé, même choix : rien
  d'écrit) ; le script d'écoute n'est servi qu'à qui l'a activée.
- M02 : seuls type, commande, durée et jour sont enregistrés (tout autre champ refusé) ; durées de plus de 30 minutes écartées ; envoi par
  balise (sendBeacon : jeton CSRF dans le corps, origine contrôlée, corps borné) ; clé d'idempotence.
- M03 : résumé hebdomadaire (médiane par type, nombre, évolution), export CSV, suppression complète.
- M04 : rien n'est visible des autres membres ni des administrateurs ; aucun paramètre ne désigne un autre compte.
- R05 : aucune écriture pendant les lectures (Mon compte, script de page, export).
- V101-11 : activation, dix décisions, résumé correct, suppression complète vérifiée en base.
Les événements du lecteur (lot R) sont simulés dans ``tests/chrono56101.cjs`` (Node).
"""
from datetime import timedelta
import io
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

from agent import chrono56101 as C
from agent.desk import Desk
import test_desk

RACINE = Path(__file__).resolve().parents[1]
ORIGINE, JETON = 'https://o.example.test', 'jeton-essai'
AUTH = {'origin': ORIGINE, 'csrf': JETON}
CAMILLE = ('camille.durand@example.test', 'avocat')
DOMINIQUE = ('dominique.martin@example.test', 'avocat')
ADMIN = ('associe.bernard@example.test', 'administrateur')
ASSISTANT = ('claude.zorval@example.test', 'assistant')


def env_de(compte, corps=None, kind='application/json', origine=ORIGINE, entete=JETON, methode='POST'):
    brut = json.dumps(corps).encode() if isinstance(corps, (dict, list)) else (corps or b'')
    env = {'HTTP_X_AXIORHUB_USER': compte[0], 'HTTP_X_AXIORHUB_ROLE': compte[1], 'REQUEST_METHOD': methode,
           'CONTENT_TYPE': kind, 'CONTENT_LENGTH': str(len(brut)), 'wsgi.input': io.BytesIO(brut)}
    if origine:
        env['HTTP_ORIGIN'] = origine
    if entete:
        env['HTTP_X_CSRF_TOKEN'] = entete
    return env


class Base(unittest.TestCase):
    setUp0 = test_desk.WebTests.setUp                  # application WSGI d'essai, sans reprendre les tests de ``WebTests``
    request = test_desk.WebTests.request

    def setUp(self):
        self.setUp0()
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)

    # ----------------------------------------------------------------------------------- appels du service (comptes nominatifs simulés)
    def appel(self, compte, route, corps=None, methode='POST', **kw):
        return C.repondre(env_de(compte, corps, methode=methode, **kw), self.desk, AUTH, '/agent-courriel', 'm56101/chrono/' + route, {}, methode)

    def regler(self, compte, actif, revision):
        return self.appel(compte, 'reglage', {'actif': '1' if actif else '0', 'revision': revision})

    def balise(self, compte, type_='courriel', commande='retenir', duree=12000, cle=None, **extra):
        """Corps tel que l'envoie ``navigator.sendBeacon`` : text/plain, jeton dans le corps, aucun en-tête CSRF."""
        self._n = getattr(self, '_n', 0) + 1
        corps = {'type': type_, 'commande': commande, 'duree_ms': duree, 'cle': cle or 'cle-essai-%016d' % self._n, 'jeton': JETON, **extra}
        return self.appel(compte, 'mesure', json.dumps(corps).encode(), kind='text/plain;charset=UTF-8', entete=None)

    def lignes(self, owner=None):
        sql = 'SELECT owner,type,commande,duree_ms,jour,cle FROM chrono56101_durees' + (' WHERE owner=?' if owner else '') + ' ORDER BY id'
        return [tuple(r) for r in self.desk.db.execute(sql, (owner,) if owner else ())]

    def inserer(self, owner, jour, type_, duree, commande='retenir'):
        self._n = getattr(self, '_n', 0) + 1
        self.desk.db.execute('INSERT INTO chrono56101_durees(owner,type,commande,duree_ms,jour,cle) VALUES(?,?,?,?,?,?)',
                             (owner, type_, commande, duree, jour.isoformat(), 'cle-insert-%016d' % self._n))
        self.desk.db.commit()

    # ----------------------------------------------------------------------------------- requêtes réelles (application WSGI, poste)
    def requete(self, chemin, corps, kind='application/json', entete='test-csrf', origine=None):
        brut = corps if isinstance(corps, bytes) else json.dumps(corps).encode()
        import base64
        env = {'REQUEST_METHOD': 'POST', 'PATH_INFO': chemin, 'QUERY_STRING': '', 'HTTP_HOST': 'cabinet.example.test', 'HTTP_X_FORWARDED_PROTO': 'https',
               'wsgi.url_scheme': 'http', 'wsgi.input': io.BytesIO(brut), 'CONTENT_LENGTH': str(len(brut)), 'CONTENT_TYPE': kind,
               'HTTP_ORIGIN': origine or self.origin,
               'HTTP_AUTHORIZATION': 'Basic ' + base64.b64encode(('admin:' + self.password).encode()).decode()}
        if entete:
            env['HTTP_X_CSRF_TOKEN'] = entete
        res = {}

        def start(status, headers):
            res.update(status=status, headers=dict(headers))
        res['body'] = b''.join(self.app(env, start)).decode()
        return res


def statut(r):
    return int(r['status'].split()[0])


def corps(r):
    return json.loads(r['body'])


class Reglage(Base):
    def test_M01_desactivee_par_defaut_et_rien_sans_activation(self):
        self.assertFalse(C.actif(self.desk, CAMILLE[0]))
        section = C.section_mon_compte(self.desk, '/agent-courriel', CAMILLE[0])
        self.assertIn('Mesure désactivée', section)
        self.assertIn('data-api="m56101/chrono/reglage"', section)
        self.assertIn('name="actif" value="1"', section)
        self.assertNotIn('chrono/export', section)                              # aucune donnée : ni export ni suppression proposés
        self.assertEqual(C.script_html(self.f.c, '/agent-courriel', CAMILLE[0]), '')
        r = self.balise(CAMILLE)
        self.assertEqual(corps(r), {'enregistre': False, 'motif': 'mesure_desactivee'})
        self.assertEqual(self.lignes(), [])
        for page in ('/aujourdhui', '/courriels', '/mon-compte'):
            self.assertNotIn('v56101-chrono.js', self.request(page)['body'], page)

    def test_M01_activation_pour_soi_seul_version_et_meme_choix(self):
        r = self.regler(CAMILLE, True, 'non')
        self.assertEqual(statut(r), 200, r['body'])
        self.assertEqual(r['headers'], [('Cache-Control', 'private, no-store')])
        self.assertTrue(C.actif(self.desk, CAMILLE[0]))
        self.assertFalse(C.actif(self.desk, DOMINIQUE[0]))                      # pour elle seule
        self.assertFalse(C.actif(self.desk, ADMIN[0]))
        avant = self.desk.db.execute('SELECT modifie FROM chrono56101_comptes WHERE owner=?', (CAMILLE[0],)).fetchone()[0]
        encore = self.regler(CAMILLE, True, 'non')                              # double clic, réponse perdue : rien n'est réécrit
        self.assertTrue(corps(encore)['inchange'])
        self.assertEqual(self.desk.db.execute('SELECT modifie FROM chrono56101_comptes WHERE owner=?', (CAMILLE[0],)).fetchone()[0], avant)
        perime = self.regler(CAMILLE, False, 'non')                             # page ouverte avant l'activation (autre onglet)
        self.assertEqual(statut(perime), 409, perime['body'])
        self.assertTrue(C.actif(self.desk, CAMILLE[0]))
        self.assertEqual(statut(self.regler(CAMILLE, False, 'oui')), 200)
        self.assertFalse(C.actif(self.desk, CAMILLE[0]))
        self.assertEqual(statut(self.appel(CAMILLE, 'reglage', {'actif': 'peut-etre', 'revision': 'non'})), 400)
        sans_csrf = self.appel(CAMILLE, 'reglage', {'actif': '1', 'revision': 'non'}, entete='mauvais')
        self.assertEqual(statut(sans_csrf), 400)
        self.assertFalse(C.actif(self.desk, CAMILLE[0]))

    def test_M01_script_servi_seulement_a_qui_l_a_active(self):
        r = self.requete('/api440/m56101/chrono/reglage', {'actif': '1', 'revision': 'non'})   # poste : compte « cabinet »
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertTrue(C.actif(self.desk, 'cabinet'))
        for page in ('/aujourdhui', '/courriels', '/mon-compte'):
            body = self.request(page)['body']
            self.assertRegex(body, r'<script defer src="/agent-courriel/static/v56101-chrono\.js(\?v=[^"]*)?" data-chrono-actif="1"></script>', page)
            self.assertEqual(body.count('v56101-chrono.js'), 1, page)
        self.assertEqual(C.script_html(self.f.c, '/p', CAMILLE[0]), '')         # un autre compte ne reçoit pas le script
        servi = self.request('/static/v56101-chrono.js')
        self.assertTrue(servi['status'].startswith('200'))
        self.assertIn('ax101:decide', servi['body'])
        page = self.request('/mon-compte')['body']
        self.assertIn('id="c101-chrono"', page)
        self.assertIn('Mesure active', page)

    def test_M01_droits_des_routes(self):
        from agent.standalone_auth import allowed
        for role in ('avocat', 'assistant'):
            for route in ('reglage', 'mesure', 'supprimer'):
                self.assertTrue(allowed(role, 'POST', '/api440/m56101/chrono/' + route), (role, route))
            self.assertFalse(allowed(role, 'POST', '/api440/m56101/chrono/autre'))
            self.assertTrue(allowed(role, 'GET', '/api440/m56101/chrono/export'))
        self.assertFalse(C.allowed('invite', 'reglage'))
        service = env_de(CAMILLE, {'actif': '1', 'revision': 'non'})
        del service['HTTP_X_AXIORHUB_USER']
        service['HTTP_AUTHORIZATION'] = 'Bearer jeton-de-service'               # jeton sans identité : ne mesure pour personne
        r = C.repondre(service, self.desk, AUTH, '/p', 'm56101/chrono/reglage', {}, 'POST')
        self.assertEqual(statut(r), 403)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM chrono56101_comptes').fetchone()[0], 0)
        self.assertEqual(statut(self.appel(CAMILLE, 'mesure', methode='GET')), 405)
        self.assertEqual(statut(self.appel(CAMILLE, 'export', {}, methode='POST')), 405)
        self.assertEqual(statut(self.appel(CAMILLE, 'inconnue', {})), 404)


class Mesure(Base):
    def setUp(self):
        super().setUp()
        self.regler(CAMILLE, True, 'non')

    def test_M02_seuls_type_commande_duree_et_jour(self):
        r = self.balise(CAMILLE, 'gmail', 'deposer', 45250)
        self.assertEqual(statut(r), 200, r['body'])
        self.assertEqual(r['headers'], [('Cache-Control', 'private, no-store')])
        from agent.acces5683 import today_of
        ((owner, type_, commande, duree, jour, cle),) = self.lignes()
        self.assertEqual((owner, type_, commande, duree, jour), (CAMILLE[0], 'gmail', 'deposer', 45250, today_of(self.desk, CAMILLE[0])[0].isoformat()))
        colonnes = {row[1] for row in self.desk.db.execute('PRAGMA table_info(chrono56101_durees)')}
        self.assertEqual(colonnes, {'id', 'owner', 'type', 'commande', 'duree_ms', 'jour', 'cle'})
        for extra in ({'objet': 'Renvoi RG 24/01234'}, {'dossier': 'DOS-001'}, {'id': 'r-1'}, {'contenu': 'Maître DURAND'}, {'owner': DOMINIQUE[0]}):
            refus = self.balise(CAMILLE, **extra)
            self.assertEqual((statut(refus), corps(refus)['error']), (400, 'mesure_invalide'), extra)
        for mauvais in ({'type_': 'piece'}, {'commande': 'Retenir <b>'}, {'duree': 'longue'}, {'duree': True}, {'cle': 'courte'}):
            self.assertEqual(statut(self.balise(CAMILLE, **mauvais)), 400, mauvais)
        self.assertEqual(len(self.lignes()), 1)
        texte = json.dumps(self.lignes(), ensure_ascii=False)
        for interdit in ('RG 24/01234', 'DOS-001', 'DURAND', 'r-1'):
            self.assertNotIn(interdit, texte)

    def test_M02_durees_de_plus_de_trente_minutes_ecartees(self):
        self.assertEqual(corps(self.balise(CAMILLE, duree=C.LIMITE_MS + 1)), {'enregistre': False, 'motif': 'duree_ecartee'})
        self.assertEqual(corps(self.balise(CAMILLE, duree=0)), {'enregistre': False, 'motif': 'duree_ecartee'})
        self.assertEqual(corps(self.balise(CAMILLE, duree=-5)), {'enregistre': False, 'motif': 'duree_ecartee'})
        self.assertEqual(self.lignes(), [])
        self.assertTrue(corps(self.balise(CAMILLE, duree=C.LIMITE_MS))['enregistre'])   # 30 minutes pile : gardée
        self.assertEqual(len(self.lignes()), 1)

    def test_M02_balise_jeton_dans_le_corps_origine_et_taille(self):
        bon = {'type': 'courriel', 'commande': 'retenir', 'duree_ms': 1000, 'cle': 'cle-essai-balise-0001'}
        r = self.appel(CAMILLE, 'mesure', json.dumps({**bon, 'jeton': 'mauvais'}).encode(), kind='text/plain', entete=None)
        self.assertEqual(statut(r), 400)
        r = self.appel(CAMILLE, 'mesure', json.dumps(bon).encode(), kind='text/plain', entete=None)   # aucun jeton
        self.assertEqual(statut(r), 400)
        r = self.appel(CAMILLE, 'mesure', json.dumps({**bon, 'jeton': JETON}).encode(), kind='text/plain', entete=None,
                       origine='https://autre.example.test')
        self.assertEqual(statut(r), 400)
        r = self.appel(CAMILLE, 'mesure', json.dumps({**bon, 'jeton': JETON}).encode(), kind='application/x-www-form-urlencoded', entete=None)
        self.assertEqual(statut(r), 400)
        r = self.appel(CAMILLE, 'mesure', json.dumps({**bon, 'jeton': JETON, 'commande': 'x' * 40}).encode() + b' ' * 1024, kind='text/plain', entete=None)
        self.assertEqual(statut(r), 413)
        self.assertEqual(self.lignes(), [])
        self.assertTrue(corps(self.appel(CAMILLE, 'mesure', bon, entete=JETON))['enregistre'])   # repli fetch : jeton en en-tête
        self.assertEqual(len(self.lignes()), 1)

    def test_M02_garde_fou_csrf_mutation(self):
        """Contre-épreuve : sans la comparaison du jeton, une balise au mauvais jeton serait comptée (le test ci-dessus échouerait)."""
        with patch.object(C.hmac, 'compare_digest', return_value=True):
            r = self.appel(CAMILLE, 'mesure', json.dumps({'type': 'courriel', 'commande': 'retenir', 'duree_ms': 1000,
                                                          'cle': 'cle-essai-mutation-01', 'jeton': 'mauvais'}).encode(), kind='text/plain', entete=None)
        self.assertEqual(statut(r), 200)
        self.assertEqual(len(self.lignes()), 1)

    def test_M02_meme_cle_comptee_une_fois(self):
        self.assertFalse(corps(self.balise(CAMILLE, cle='cle-essai-unique-0001'))['deja'])
        self.assertTrue(corps(self.balise(CAMILLE, cle='cle-essai-unique-0001'))['deja'])
        self.assertEqual(len(self.lignes()), 1)
        self.regler(DOMINIQUE, True, 'non')
        self.assertFalse(corps(self.balise(DOMINIQUE, cle='cle-essai-unique-0001'))['deja'])   # clé propre à chaque compte

    def test_M03_resume_hebdomadaire_median_nombre_evolution(self):
        jeudi = C._jour(self.desk, CAMILLE[0])
        jeudi = jeudi - timedelta(days=jeudi.weekday()) + timedelta(days=3)     # jeudi de la semaine du cabinet
        lundi = jeudi - timedelta(days=3)
        for d in (10000, 20000, 30000):
            self.inserer(CAMILLE[0], lundi, 'courriel', d)                      # semaine courante : médiane 20 s, 3 éléments
        for d in (40000, 20000, 30000, 50000):
            self.inserer(CAMILLE[0], lundi - timedelta(days=1), 'courriel', d)  # dimanche précédent : médiane 35 s, 4 éléments
        self.inserer(CAMILLE[0], jeudi, 'document', 95000)
        self.inserer(CAMILLE[0], lundi - timedelta(days=8), 'courriel', 999000)  # il y a plus de deux semaines : hors résumé
        r = C.resume(self.desk, CAMILLE[0], jeudi)
        self.assertEqual(r['lundi'], lundi.isoformat())
        self.assertEqual(r['lignes'], [
            {'type': 'courriel', 'mediane': 20000, 'nombre': 3, 'mediane_prec': 35000, 'nombre_prec': 4, 'evolution': -15000},
            {'type': 'document', 'mediane': 95000, 'nombre': 1, 'mediane_prec': None, 'nombre_prec': 0, 'evolution': None}])
        self.assertEqual(r['nombre_semaine'], 4)
        self.assertEqual(C.evolution_texte(r['lignes'][0]), '−15 s (−43 %)')
        self.assertEqual((C.duree_texte(95000), C.duree_texte(4000), C.evolution_texte(r['lignes'][1])), ('1 min 35 s', '4 s', '—'))
        self.assertEqual(C.evolution_texte({'evolution': 400, 'mediane_prec': 30000}), 'stable')
        with patch.object(C, '_jour', return_value=jeudi):
            section = C.section_mon_compte(self.desk, '/agent-courriel', CAMILLE[0])
        for attendu in ('Résumé de la semaine', 'Courriels', '20 s', '35 s', '−15 s (−43 %)', 'Documents produits', '1 min 35 s',
                        'href="/agent-courriel/api440/m56101/chrono/export"', 'data-api="m56101/chrono/supprimer"', 'data-confirm=',
                        '9 mesure(s) enregistrée(s)', '<caption>', 'scope="col"'):
            self.assertIn(attendu, section)


class Recette(Base):
    def test_V101_11_activation_dix_decisions_resume_export_et_suppression(self):
        r = self.requete('/api440/m56101/chrono/reglage', {'actif': '1', 'revision': 'non'})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        for i in range(1, 11):                                                   # dix décisions, envoyées comme par sendBeacon
            brut = json.dumps({'type': 'courriel' if i <= 6 else 'note_ia', 'commande': 'retenir' if i <= 6 else 'valider',
                               'duree_ms': i * 1000, 'cle': 'cle-recette-%012d' % i, 'jeton': 'test-csrf'}).encode()
            b = self.requete('/api440/m56101/chrono/mesure', brut, kind='text/plain;charset=UTF-8', entete=None)
            self.assertTrue(b['status'].startswith('200'), b['body'])
            self.assertTrue(json.loads(b['body'])['enregistre'])
            self.assertIn('no-store', b['headers'].get('Cache-Control', ''))
        self.assertEqual(len(self.lignes('cabinet')), 10)
        r = C.resume(self.desk, 'cabinet')
        par_type = {x['type']: (x['mediane'], x['nombre']) for x in r['lignes']}
        self.assertEqual(par_type, {'courriel': (3500, 6), 'note_ia': (8500, 4)})   # médianes de 1..6 s et de 7..10 s
        page = self.request('/mon-compte')['body']
        self.assertIn('10 mesure(s) enregistrée(s)', page)
        self.assertIn('Notes de l’IA', page)
        export = self.request('/api440/m56101/chrono/export')
        self.assertTrue(export['status'].startswith('200'))
        self.assertTrue(export['headers']['Content-Type'].startswith('text/csv'))
        self.assertIn('attachment', export['headers']['Content-Disposition'])
        self.assertIn('no-store', export['headers']['Cache-Control'])
        lignes = export['body'].lstrip('﻿').strip().splitlines()
        self.assertEqual(lignes[0], 'Jour;Type;Commande;Durée (secondes)')
        self.assertEqual(len(lignes), 11)
        self.assertIn(';note_ia;valider;10.0', export['body'])
        self.assertNotIn('cle-recette', export['body'])
        s = self.requete('/api440/m56101/chrono/supprimer', {})
        self.assertTrue(s['status'].startswith('200'), s['body'])
        self.assertEqual(json.loads(s['body'])['nombre'], 10)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM chrono56101_durees').fetchone()[0], 0)   # vérifié en base
        self.assertEqual(json.loads(self.requete('/api440/m56101/chrono/supprimer', {})['body'])['nombre'], 0)   # nouvel essai : sans effet


class Confidentialite(Base):
    def setUp(self):
        super().setUp()
        for compte in (CAMILLE, DOMINIQUE, ADMIN):
            self.regler(compte, True, 'non')
        for d in (11000, 13000, 17000):
            self.balise(CAMILLE, 'document', 'valider', d)
        self.balise(DOMINIQUE, 'courriel', 'ecarter', 61000)

    def test_M04_invisible_des_autres_membres_et_des_administrateurs(self):
        for compte in (DOMINIQUE, ADMIN, ASSISTANT):
            section = C.section_mon_compte(self.desk, '/p', compte[0])
            self.assertNotIn('Documents produits', section, compte)
            self.assertNotIn('13 s', section, compte)
            export = self.appel(compte, 'export', methode='GET')
            self.assertNotIn('document;valider', export['body'], compte)
            self.assertEqual(export['headers'][-1], ('Cache-Control', 'private, no-store'))
        self.assertIn('1 min 01 s', C.section_mon_compte(self.desk, '/p', DOMINIQUE[0]))
        self.assertEqual(C.export_csv(self.desk, ADMIN[0])['body'].strip().count('\n'), 0)   # en-tête seul
        # Aucun paramètre ne désigne un autre compte : la suppression de l'administrateur n'efface que ses propres mesures.
        r = self.appel(ADMIN, 'supprimer', {'owner': CAMILLE[0]})
        self.assertEqual(corps(r)['nombre'], 0)
        self.assertEqual(len(self.lignes(CAMILLE[0])), 3)
        self.assertEqual(corps(self.appel(DOMINIQUE, 'supprimer', {}))['nombre'], 1)
        self.assertEqual((len(self.lignes(CAMILLE[0])), len(self.lignes(DOMINIQUE[0]))), (3, 0))
        export = self.appel(CAMILLE, 'export', methode='GET')
        self.assertEqual(export['body'].count('document;valider'), 3)

    def test_M04_hors_export_du_cabinet_et_du_journal(self):
        from agent import export5688
        self.assertFalse([t for t in export5688.TABLES if 'chrono' in t[2]])
        audit = self.desk.db.execute("SELECT COUNT(*) FROM audit WHERE action LIKE '%chrono%' OR data LIKE '%chrono%'").fetchone()[0]
        self.assertEqual(audit, 0)

    def test_R05_lectures_sans_aucune_ecriture(self):
        ecritures = []
        self.desk.db.set_trace_callback(lambda sql: ecritures.append(sql) if sql.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'REPLACE')) else None)
        try:
            C.section_mon_compte(self.desk, '/p', CAMILLE[0])
            C.resume(self.desk, CAMILLE[0])
            C.export_csv(self.desk, CAMILLE[0])
            self.appel(CAMILLE, 'export', methode='GET')
            C.actif(self.desk, ASSISTANT[0])
        finally:
            self.desk.db.set_trace_callback(None)
        self.assertEqual(ecritures, [])
        etat = ('SELECT (SELECT COUNT(*) FROM chrono56101_durees), (SELECT TOTAL(duree_ms) FROM chrono56101_durees), '
                '(SELECT group_concat(owner || actif || modifie) FROM chrono56101_comptes)')
        avant = self.desk.db.execute(etat).fetchone()[:]
        # Les lectures du lot ne préparent jamais le schéma (la préparation de ``Desk`` à l'ouverture, elle, reste celle du démarrage).
        with patch.object(C, 'ensure_schema', side_effect=AssertionError('schéma créé pendant une lecture')):
            self.assertTrue(C.script_html(self.f.c, '/p', CAMILLE[0]))           # base ouverte en lecture seule
            C.section_mon_compte(self.desk, '/p', CAMILLE[0])
            C.export_csv(self.desk, CAMILLE[0])
        for chemin in ('/mon-compte', '/aujourdhui', '/api440/m56101/chrono/export'):
            self.assertTrue(self.request(chemin)['status'].startswith('200'), chemin)
        self.assertEqual(self.desk.db.execute(etat).fetchone()[:], avant)   # pages et export (autres connexions) : mesures et réglages intacts

    def test_M01_table_absente_lecture_sans_erreur(self):
        """Base d'une version antérieure, pas encore préparée : désactivée, aucun résumé, et rien n'est créé par la lecture."""
        self.desk.db.executescript('DROP TABLE chrono56101_durees; DROP TABLE chrono56101_comptes;')
        self.assertFalse(C.actif(self.desk, CAMILLE[0]))
        self.assertFalse(C.actif_lu(self.f.c['state_dir'], CAMILLE[0]))
        self.assertIn('Mesure désactivée', C.section_mon_compte(self.desk, '/p', CAMILLE[0]))
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'chrono56101%'").fetchone()[0], 0)
        C.ensure_schema(self.desk)                                               # préparation (démarrage) : idempotente
        C.ensure_schema(self.desk)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE 'chrono56101%'").fetchone()[0], 2)


class Navigateur(unittest.TestCase):
    def test_M01_script_declare_et_sans_code_en_ligne(self):
        from agent import web440
        self.assertEqual(web440.STATIC.get('/static/v56101-chrono.js'), 'text/javascript')
        source = (RACINE / 'agent' / 'static' / 'v56101-chrono.js').read_text(encoding='utf-8')
        self.assertNotIn('innerHTML', source)
        self.assertIn("'ax101:ouvert'", source)
        self.assertIn("'ax101:decide'", source)
        self.assertNotIn('v56101-chrono', (RACINE / 'agent' / 'static' / 'v56100-lecteur.js').read_text(encoding='utf-8'))   # fichier du lot R non modifié

    @unittest.skipUnless(shutil.which('node'), 'Node.js indisponible')
    def test_M01_M02_script_dans_node_evenements_simules(self):
        r = subprocess.run([shutil.which('node'), str(RACINE / 'tests' / 'chrono56101.cjs'), str(RACINE)], capture_output=True, text=True,
                           encoding='utf-8', timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('chrono56101 : ok', r.stdout)


if __name__ == '__main__':
    unittest.main()
