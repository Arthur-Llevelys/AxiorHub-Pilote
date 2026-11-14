"""5.6.100, lot D (priorité 3 · R01 · V100-06 · § 13 rangement) : « Travaillés récemment par l'IA », tableau paginé, reprise de la fiche,
volets Conseil et Contentieux, type explicite des répertoires (Affaire / Cabinet / Dépôt).

Faux stockage, aucun réseau, aucun modèle ; données fictives seulement (DURAND, MARTIN, BERNARD, ZORVAL, KERVILAC, DOS-001, RG 24/01234,
example.test). Les dates sont celles du cabinet (fuseau de l'agenda), jamais ``date.today()``."""
from datetime import datetime, timedelta, timezone
import re
import statistics
import time
import unittest
import uuid
from unittest.mock import patch

from agent import dossiers5697 as D, dossiers56100, dossiers_ui5697, interface56100, membres5650, rangement56100, resultats56100
from agent.common import Stop
import test_v5697_dossiers as T

ADMIN = T.ADMIN
CLAIRE, CAMILLE = T.CLAIRE, T.CAMILLE
AVOCATE = (CLAIRE, 'avocat')
ASSISTANT = ('assistant@example.test', 'assistant')
DML = re.compile(r'^\s*(INSERT|UPDATE|DELETE|REPLACE)\b', re.I)


def setUpModule():
    T.setUpModule()


def _iso(heure, jour='2026-11-12'):
    return '%sT%s:00+00:00' % (jour, heure)


class Compteur:
    """Compte les requêtes SQLite d'une connexion (``set_trace_callback``) et relève les écritures métier (DML)."""

    def __init__(self, db):
        self.db, self.requetes, self.dml = db, 0, []

    def __enter__(self):
        def trace(sql):
            self.requetes += 1
            if DML.match(sql):
                self.dml.append(sql[:120])
        self.db.set_trace_callback(trace)
        return self

    def __exit__(self, *exc):
        self.db.set_trace_callback(None)


class Base(T.Base):
    def setUp(self):
        super().setUp()
        D._SCHEMA_PRET.clear()                    # base neuve à chaque essai : le registre unique est préparé comme au démarrage
        D.ensure_schema(self.desk)

    def sans_historiques(self):
        """Registre unique seul (import déjà fait) : le dossier de démonstration du registre JSON n'est plus listé à part."""
        self.desk.setting('dossiers5697:migre', '2026-11-01T08:00:00+00:00')

    def env(self, acteur=ADMIN):
        return {'HTTP_X_AXIORHUB_USER': acteur[0], 'HTTP_X_AXIORHUB_ROLE': acteur[1], 'REQUEST_METHOD': 'GET'}

    def ecran(self, acteur=ADMIN, **args):
        return dossiers_ui5697.ecran(self.desk, self.env(acteur), {}, '/p', args)

    def livrable(self, mid, doc, heure, etat='verified', service='docrequest520', cycle='current', kind='note', titre='Note fictive'):
        from agent import livrables5689 as liv
        liv.ensure_schema(self.desk)
        quand = _iso(heure)
        self.desk.db.execute(
            'INSERT INTO prepared_items5689(id,document_id,version_id,matter_id,kind,title,source_service,source_object_id,source_version,production_state,'
            'lifecycle,created,updated,source_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('it-' + doc, doc, 'ver-' + doc, mid, kind, titre, service, 'obj-' + doc, '1', etat, cycle, quand, quand, quand))
        self.desk.db.commit()

    def depot(self, ident='DEP-099', titre='09 - Scanner', **colonnes):
        """Répertoire de dépôt importé avant la 5.6.99 (ligne du registre, sans aucun signe d'affaire)."""
        quand = '2026-11-01T08:00:00+00:00'
        valeurs = {'id': ident, 'cabinet_id': D._cabinet(self.desk), 'ref': ident, 'ref_norm': ident, 'titre': titre, 'chemin': '/Dossiers/' + titre,
                   'provenance': 'migre', 'cree_le': quand, 'modifie_le': quand, **colonnes}
        self.desk.db.execute('INSERT INTO dossiers5697(%s) VALUES(%s)' % (','.join(valeurs), ','.join('?' * len(valeurs))), list(valeurs.values()))
        self.desk.db.commit()
        return ident


class Jeu(Base):
    """Cinq dossiers dont un réservé (KERVILAC) et un dépôt (« 09 - Scanner ») ; deux résultats du même dossier ; un livrable en échec."""

    def setUp(self):
        super().setUp()
        self.a = self.creer(titre='DURAND c/ MARTIN', client='DURAND', nature='contentieux', rg='24/01234')['id']
        self.b = self.creer(titre='BERNARD — pacte d’associés', client='BERNARD', nature='conseil')['id']
        self.c = self.creer(titre='ZORVAL c/ KERVILAC', client='ZORVAL', nature='contentieux')['id']
        self.r = self.creer(titre='KERVILAC — réservé', client='KERVILAC', confidentialite='reserve', membres=[CAMILLE])['id']
        self.dep = self.depot()
        # A : deux travaux réussis du même dossier ; B : une fiche de travail ; C : un échec plus récent et un livrable prêt plus ancien
        self.livrable(self.a, 'docA1', '10:00')
        self.livrable(self.a, 'docA2', '11:00')
        self.livrable(self.c, 'docC1', '08:00')
        self.livrable(self.c, 'docC2', '12:00', etat='failed')
        self.livrable(self.r, 'docR1', '13:00')
        self.livrable(self.dep, 'docD1', '14:00')
        from agent import dossier5627
        self.desk.db.executescript(dossier5627.SCHEMA)
        self.desk.db.execute('INSERT INTO fiche5627(matter,version,data,sources,created) VALUES(?,?,?,?,?)', (self.b, 1, '{}', '[]', _iso('09:00')))
        self.desk.db.commit()


# ------------------------------------------------------------------------------------------------ R01 : trois affaires récentes
class R01Recents(Jeu):
    def verifier_recents(self, cartes):
        """Contrat : trois affaires distinctes autorisées, ordonnées par leur dernier résultat, chacune vers son résultat exact."""
        self.assertEqual([c['dossier_canonique'] for c in cartes], [self.a, self.b, self.c])
        self.assertEqual(len({c['dossier_canonique'] for c in cartes}), len(cartes))
        self.assertEqual(cartes[0]['lien'], '/livrables/docA2?version=ver-docA2')
        self.assertEqual(cartes[2]['lien'], '/livrables/docC1?version=ver-docC1')   # l'échec plus récent ne remonte pas
        self.assertTrue(cartes[1]['lien'].startswith('/fiche?matter='))

    def test_R01_rattrapage_puis_trois_affaires_distinctes(self):
        out = resultats56100.rattraper(self.desk)
        self.assertEqual(out['fiches'], 1)
        self.assertEqual(out['livrables'], 5)                          # l'échec n'est pas projeté
        self.verifier_recents(resultats56100.recents(self.desk, AVOCATE, 3))
        self.assertEqual(resultats56100.rattraper(self.desk), {'livrables': 0, 'fiches': 0, 'rappels': 0})   # répétable, incrémental

    def test_R01_reserve_visible_seulement_de_ses_membres(self):
        resultats56100.rattraper(self.desk)
        admin = [c['dossier_canonique'] for c in resultats56100.recents(self.desk, ADMIN, 3)]
        self.assertEqual(admin, [self.r, self.a, self.b])               # l'administrateur voit le dossier réservé ; jamais le dépôt
        camille = [c['dossier_canonique'] for c in resultats56100.recents(self.desk, (CAMILLE, 'avocat'), 3)]
        self.assertEqual(camille[0], self.r)

    def test_R01_mutation_garde_des_droits_retiree_detectee(self):
        """Sans le contrôle du dossier, le dossier réservé remonterait chez une autre avocate : le test le détecte."""
        resultats56100.rattraper(self.desk)
        with patch('agent.acces5697.masques', lambda desk, a: frozenset()):
            with self.assertRaises(AssertionError):
                self.verifier_recents(resultats56100.recents(self.desk, AVOCATE, 3))

    def test_R01_mutation_controle_objet_retire_detectee(self):
        """Sans le contrôle de l'objet exact (livrable en échec, autre dossier), la carte de C pointerait l'échec : le test le détecte."""
        resultats56100.publier(self.desk, self.c, 'livrable', 'docC2', 'ver-docC2', _iso('12:00'))
        resultats56100.rattraper(self.desk)
        with patch.object(resultats56100, '_lien_objet', lambda desk, a, caches, droits, ligne, canon: '/livrables/%s' % ligne['objet_id']):
            with self.assertRaises(AssertionError):
                self.verifier_recents(resultats56100.recents(self.desk, AVOCATE, 3))
        self.verifier_recents(resultats56100.recents(self.desk, AVOCATE, 3))

    def test_R01_alias_resolus_un_seul_dossier(self):
        """Deux alias du même dossier (ancien identifiant, référence) : une seule carte ; le producteur publie par l'alias."""
        D._alias(self.desk, 'id', 'ANCIEN-C', self.c)
        self.desk.db.commit()
        ref_c = D.lire(self.desk, ADMIN, self.c)['reference']
        self.livrable(self.c, 'docC3', '07:00')
        resultats56100.publier(self.desk, 'ANCIEN-C', 'livrable', 'docC1', 'ver-docC1', _iso('08:00'))
        ligne = self.desk.db.execute("SELECT dossier_canonique, dossier_source FROM dossier56100_resultats WHERE dossier_source='ANCIEN-C'").fetchone()
        self.assertEqual((ligne[0], ligne[1]), (self.c, 'ANCIEN-C'))
        resultats56100.publier(self.desk, ref_c, 'livrable', 'docC3', 'ver-docC3', _iso('07:00'))
        resultats56100.rattraper(self.desk)
        cartes = resultats56100.recents(self.desk, AVOCATE, 3)
        self.assertEqual([c['dossier_canonique'] for c in cartes].count(self.c), 1)
        self.verifier_recents(cartes)

    def test_R01_publier_idempotent_et_refus(self):
        x = resultats56100.publier(self.desk, self.a, 'note', 'docA2', 'ver-docA2', _iso('11:00'))
        y = resultats56100.publier(self.desk, self.a, 'note', 'docA2', 'ver-docA2', 1794567600)
        self.assertEqual(x, y)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM dossier56100_resultats WHERE type='note'").fetchone()[0], 1)
        for args, code in (((self.a, 'indexation', 'x', '1', _iso('10:00')), 'genre_inconnu'), (('INCONNU-404', 'note', 'x', '1', _iso('10:00')), 'dossier_absent'),
                           ((self.a, 'note', 'x', '1', 'hier'), 'date_invalide')):
            with self.assertRaises(Stop) as ctx:
                resultats56100.publier(self.desk, *args)
            self.assertEqual(str(ctx.exception), code)

    def test_R01_resultat_devenu_inaccessible_ecarte(self):
        resultats56100.rattraper(self.desk)
        self.desk.db.execute("UPDATE prepared_items5689 SET lifecycle='replaced' WHERE document_id='docA2'")
        self.desk.db.commit()
        cartes = resultats56100.recents(self.desk, AVOCATE, 3)
        self.assertEqual(cartes[0]['lien'], '/livrables/docA1?version=ver-docA1')   # le résultat précédent du même dossier, vérifiable

    def test_R01_bloc_html_nombre_reel_et_sans_activite(self):
        page = self.ecran(AVOCATE)
        self.assertIn('Aucun travail IA récent.', page)                                # compte sans activité : rien d'inventé
        self.assertLess(page.index('Travaillés récemment par l’IA'), page.index('<table'))
        self.assertLess(page.index('+ Nouveau dossier'), page.index('Travaillés récemment par l’IA'))
        resultats56100.publier(self.desk, self.b, 'analyse', 'fiche5627:%s:1' % self.b, '1', _iso('09:00'), 'Fiche du dossier')
        page = self.ecran(AVOCATE)
        self.assertIn('data-d100-nombre="1"', page)
        self.assertEqual(page.count('class="d100-carte"'), 1)
        self.assertIn('Voir le résultat', page)
        self.assertIn('12/11/2026 à 10 h 00', page)                                     # date locale du cabinet (UTC+1 en novembre)
        resultats56100.rattraper(self.desk)
        page = self.ecran(AVOCATE)
        self.assertEqual(page.count('class="d100-carte"'), 3)
        self.assertNotIn('KERVILAC — réservé', page)

    def test_R01_interrupteur_ancienne_presentation(self):
        resultats56100.rattraper(self.desk)
        interface56100.regler_compte(self.desk, CLAIRE, False)
        page = self.ecran(AVOCATE)
        self.assertNotIn('Travaillés récemment par l’IA', page)
        self.assertIn('d97-table', page)                                                # la pagination vaut aussi sans la nouvelle interface
        interface56100.regler_compte(self.desk, CLAIRE, None)
        self.assertIn('Travaillés récemment par l’IA', self.ecran(AVOCATE))

    def test_R01_dossier_archive_signale(self):
        resultats56100.rattraper(self.desk)
        self.desk.db.execute("UPDATE dossiers5697 SET statut='archive' WHERE id=?", (self.b,))
        self.desk.db.commit()
        carte = next(c for c in resultats56100.recents(self.desk, AVOCATE, 3) if c['dossier_canonique'] == self.b)
        self.assertTrue(carte['archive'])

    def test_R01_lecture_sans_ecriture(self):
        resultats56100.rattraper(self.desk)
        with Compteur(self.desk.db) as n:
            resultats56100.recents(self.desk, AVOCATE, 3)
            self.ecran(AVOCATE)
        self.assertEqual(n.dml, [])


# ------------------------------------------------------------------------------------------------ § 22 : tableau paginé, recherche, tri
class P3Tableau(Base):
    def remplir(self, nombre, depart=0):
        self.sans_historiques()
        cabinet = D._cabinet(self.desk)
        lignes = []
        for i in range(depart, depart + nombre):
            ident = 'F%05d' % i
            quand = (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i)).isoformat(timespec='seconds')
            lignes.append((ident, cabinet, 'REF-%05d' % i, 'REF-%05d' % i, 'Dossier fictif %05d — DURAND' % i, 'DURAND', 'identifie',
                           'contentieux' if i % 2 else 'conseil', 'actif', CLAIRE if i % 3 == 0 else '', 'manuel', '/Dossiers/F%05d' % i, quand, quand))
        self.desk.db.executemany('INSERT INTO dossiers5697(id,cabinet_id,ref,ref_norm,titre,client,client_etat,nature,statut,responsable,provenance,chemin,cree_le,modifie_le) '
                                 'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', lignes)
        self.desk.db.commit()

    def test_P3_trente_par_defaut_cinquante_au_plus_sans_trou(self):
        self.remplir(75)
        r1 = dossiers56100.page(self.desk, ADMIN, {}, 1)
        r2 = dossiers56100.page(self.desk, ADMIN, {}, 2)
        r3 = dossiers56100.page(self.desk, ADMIN, {}, 3)
        self.assertEqual((len(r1['lignes']), len(r2['lignes']), len(r3['lignes'])), (30, 30, 15))
        ids = [d['id'] for r in (r1, r2, r3) for d in r['lignes']]
        self.assertEqual(len(set(ids)), 75)                                            # deux pages consécutives sans trou ni doublon
        self.assertEqual(r1['total'], 75)
        self.assertEqual(len(dossiers56100.page(self.desk, ADMIN, {}, 1, 500)['lignes']), 50)
        titres = [d['id'] for d in dossiers56100.page(self.desk, ADMIN, {}, 1, 50, 'titre')['lignes']]
        self.assertEqual(titres, sorted(titres))                                         # tri stable par intitulé puis identifiant

    def test_P3_recherche_partie_sans_accents_et_reserve_hors_total(self):
        self.remplir(40)
        ident = self.creer(titre='Succession', client='MARTIN', nature='conseil')['id']
        D._ajouter_partie(self.desk, ident, {'nom': 'Société Élodie ZORVAL', 'role': 'adverse'})
        reserve = self.creer(titre='Succession bis', client='MARTIN', confidentialite='reserve', membres=[CAMILLE])['id']
        D._ajouter_partie(self.desk, reserve, {'nom': 'Société Élodie ZORVAL', 'role': 'adverse'})
        self.desk.db.commit()
        r = dossiers56100.page(self.desk, AVOCATE, {'q': 'elodie zorval'})
        self.assertEqual([d['id'] for d in r['lignes']], [ident])
        self.assertEqual(r['total'], 1)                                                 # le dossier réservé ne compte nulle part
        self.assertEqual(dossiers56100.page(self.desk, ADMIN, {'q': 'élodie'})['total'], 2)

    def test_P3_requetes_independantes_du_total_et_mesure_879(self):
        """50 puis 879 dossiers fictifs : le nombre de requêtes du rendu ne dépend pas du total ; temps et requêtes relevés (30 rendus)."""
        self.remplir(50)
        self.ecran(AVOCATE)                                                             # rendu chaud
        with Compteur(self.desk.db) as petit:
            page50 = self.ecran(AVOCATE)
        self.remplir(829, depart=50)
        self.ecran(AVOCATE)
        with Compteur(self.desk.db) as grand:
            page879 = self.ecran(AVOCATE)
        self.assertEqual(petit.dml + grand.dml, [])
        self.assertLessEqual(grand.requetes, petit.requetes + 2)
        self.assertEqual(page879.count('data-d97-ligne='), 30)
        self.assertIn('879 dossiers', page879)
        self.assertIn('page 1 sur 30', page879)
        self.assertIn('rel="next"', page879)
        self.assertEqual(page50.count('data-d97-ligne='), 30)
        durees = []
        for _ in range(30):
            debut = time.perf_counter()
            html = self.ecran(AVOCATE)
            durees.append((time.perf_counter() - debut) * 1000)
        durees.sort()
        MESURES['879'] = {'p50_ms': round(statistics.median(durees), 1), 'p95_ms': round(durees[int(len(durees) * .95) - 1], 1),
                          'requetes': grand.requetes, 'requetes_50': petit.requetes, 'octets': len(html.encode('utf-8'))}

    def test_P3_mutation_pagination_retiree_detectee(self):
        """Une liste non bornée (le rendu 5.6.99) afficherait les 75 lignes : le contrôle le détecte."""
        self.remplir(75)
        with patch.object(dossiers56100, 'TAILLE_MAX', 5000), patch.object(dossiers56100, 'TAILLE_DEFAUT', 5000):
            page = dossiers_ui5697.ecran(self.desk, self.env(AVOCATE), {}, '/p', {})
        with self.assertRaises(AssertionError):
            self.assertEqual(page.count('data-d97-ligne='), 30)

    def test_P3_dossiers_historiques_lisibles_avec_migration_proposee(self):
        from agent.desk import save_matter
        racine = D.racine(self.desk)
        save_matter(self.f.c, {'id': 'DOS-001', 'path': racine + '/DURAND - MARTIN', 'client_name': 'DURAND', 'aliases': [], 'references': [], 'correspondents': []})
        page = self.ecran(ADMIN)
        self.assertIn('Lecture seule · import proposé', page)
        self.assertIn('Préparer l’import (simulation d’abord)', page)


# ------------------------------------------------------------------------------------------------ § 13 : rangement explicite
class Rangement(Base):
    def test_rangement_depot_exclu_vraie_affaire_jamais_sur_un_nom(self):
        self.sans_historiques()
        self.depot()
        affaire = self.creer(titre='A traiter', client='DURAND')['id']                   # nom de dépôt, mais vraie affaire créée à la main
        migree = self.depot('DOS-002', 'Arrivée', client='MARTIN', rg='24/01234')       # importée, avec client et RG
        ids = {d['id'] for d in dossiers56100.page(self.desk, ADMIN, {}, 1, 50)['lignes']}
        self.assertNotIn('DEP-099', ids)                                                # 5.6.99 : le scanner reste hors de la liste
        self.assertTrue({affaire, migree} <= ids)
        self.assertEqual({d['id'] for d in D.lister(self.desk, ADMIN, {}, 200)}, ids)   # même règle pour l'ancienne liste

    def test_rangement_type_explicite_prime_et_reversible(self):
        self.sans_historiques()
        self.depot()
        interne = self.depot('INT-001', '00 - Administratif')
        ids = lambda: {d['id'] for d in dossiers56100.page(self.desk, ADMIN, {}, 1, 50)['lignes']}
        self.assertIn(interne, ids())                                                   # « Cabinet » n'est qu'une proposition
        cle = uuid.uuid4().hex
        recu = rangement56100.choisir(self.desk, ADMIN, {interne: 'cabinet', 'DEP-099': 'affaire'}, cle)
        self.assertEqual(rangement56100.choisir(self.desk, ADMIN, {interne: 'cabinet', 'DEP-099': 'affaire'}, cle)['enregistres'], recu['enregistres'])
        self.assertEqual(ids(), {'DEP-099'})
        rangement56100.choisir(self.desk, ADMIN, {interne: 'affaire'}, uuid.uuid4().hex)
        self.assertIn(interne, ids())
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM dossiers5697').fetchone()[0], 2)   # rien n'est supprimé
        with self.assertRaises(Stop) as ctx:
            rangement56100.choisir(self.desk, AVOCATE, {interne: 'cabinet'}, uuid.uuid4().hex)
        self.assertEqual(str(ctx.exception), 'role_insuffisant')

    def test_rangement_route_types_reservee_administrateur(self):
        interne = self.depot('INT-001', '00 - Administratif')
        statut, _ = self.appel('dossiers/types', {'cle': uuid.uuid4().hex, 'types': {interne: 'cabinet'}}, owner=CLAIRE, role='avocat')
        self.assertEqual(statut, '403')
        statut, corps = self.appel('dossiers/types', {'cle': uuid.uuid4().hex, 'types': {interne: 'cabinet'}})
        self.assertEqual(statut, '200')
        self.assertEqual(corps['types'], {interne: 'cabinet'})
        statut, _ = self.appel('dossiers/types', {'cle': uuid.uuid4().hex, 'types': {interne: 'inconnu'}})
        self.assertEqual(statut, '422')

    def test_rangement_import_simulation_choix_modifiable(self):
        from agent.desk import save_matter
        racine = D.racine(self.desk)
        save_matter(self.f.c, {'id': 'DEP-099', 'path': racine + '/09 - Scanner', 'client_name': '09 - Scanner', 'aliases': [], 'references': [], 'correspondents': []})
        save_matter(self.f.c, {'id': 'INT-001', 'path': racine + '/00 - Administratif', 'client_name': '', 'aliases': [], 'references': [], 'correspondents': []})
        save_matter(self.f.c, {'id': 'DOS-001', 'path': racine + '/DURAND - MARTIN', 'client_name': 'DURAND', 'aliases': [], 'references': [], 'correspondents': []})
        sim = D.migrer(self.desk, ADMIN, simulation=True)
        proposes = {t['id']: t['propose'] for t in sim['types']}
        self.assertEqual((proposes['DEP-099'], proposes['INT-001'], proposes['DOS-001']), ('depot', 'cabinet', 'affaire'))
        self.assertEqual(sorted(x['id'] for x in sim['a_importer']), ['DOS-001', 'INT-001'])   # sans choix, « Cabinet » est importé
        html = dossiers_ui5697.page_import(self.desk, self.env(ADMIN), {}, '/p', {})
        self.assertIn('name="types.INT-001"', html)
        self.assertIn('Rien n’a été écrit.', html)
        reel = D.migrer(self.desk, ADMIN, simulation=False, types={'INT-001': 'cabinet', 'DEP-099': 'depot'})
        self.assertEqual([x['id'] for x in reel['a_importer']], ['DOS-001'])
        self.assertEqual(rangement56100.types(self.desk), {'INT-001': 'cabinet', 'DEP-099': 'depot'})
        from agent.common import load_matters
        self.assertEqual({m['id'] for m in load_matters(self.f.c)} >= {'DEP-099', 'INT-001', 'DOS-001'}, True)   # liens historiques conservés


# ------------------------------------------------------------------------------------------------ § 22 : en-tête de la fiche, volets
class V100_06Fiche(Jeu):
    def test_V100_06_reprise_quatre_champs_et_actions(self):
        from agent import echeances450
        echeances450.ensure_schema(self.desk)
        jour = dossiers56100.jour_cabinet(self.desk)
        for ident, statut, delta in (('dl-1', 'confirmee', 20), ('dl-2', 'a_confirmer', 5)):
            self.desk.db.execute("INSERT INTO deadlines450(id,matter,rule_id,start_event,start_date,due,status,origin,note,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                 (ident, self.a, 'appel', 'signification', jour.isoformat(), (jour + timedelta(days=delta)).isoformat(), statut, 'manuel',
                                  'Conclusions d’appel', self.desk.now(), self.desk.now()))
        self.desk.db.execute("UPDATE dossiers5697 SET responsable='' WHERE id=?", (self.a,))
        self.desk.db.execute("UPDATE dossier_journal5697 SET quand='2026-11-01T08:00:00+00:00' WHERE dossier_id=?", (self.a,))
        self.desk.db.commit()
        resultats56100.rattraper(self.desk)
        html = dossiers_ui5697.entete_fiche(self.desk, AVOCATE, '/p', self.a)
        for libelle in ('Responsable', 'Prochaine action', 'Prochaine échéance confirmée', 'Dernier changement'):
            self.assertIn('<dt>%s</dt>' % libelle, html)
        self.assertIn('Attribuer', html)                                                  # aucun responsable : action proposée selon les droits
        self.assertIn('Choisir la prochaine action', html)
        self.assertIn((jour + timedelta(days=20)).strftime('%d/%m/%Y'), html)             # échéance confirmée
        self.assertNotIn((jour + timedelta(days=5)).strftime('%d/%m/%Y') + ' —', html)    # la date proposée n'est pas une échéance confirmée
        self.assertIn('1 date à vérifier', html)
        self.assertIn('/livrables/docA2?version=ver-docA2', html)                         # dernier changement : le résultat exact
        self.assertIn('data-m97-api="portefeuille/definir"', html)
        assistant = dossiers_ui5697.entete_fiche(self.desk, ASSISTANT, '/p', self.a)
        self.assertNotIn('Attribuer', assistant)                                          # l'assistant ne réattribue pas
        self.assertIn('Choisir la prochaine action', assistant)

    def test_V100_06_reserve_rien_et_ia_en_pause_modifiable(self):
        self.assertEqual(dossiers_ui5697.entete_fiche(self.desk, AVOCATE, '/p', self.r), '')
        from agent import pause5629
        with patch.object(pause5629, 'is_paused', lambda *a, **k: True):
            html = dossiers_ui5697.entete_fiche(self.desk, ADMIN, '/p', self.a)
        self.assertIn('data-m97-api="portefeuille/action"', html)
        from agent import portefeuille5697 as P
        P.schema(self.desk)
        etat = P.definir(self.desk, ADMIN, self.a, {'responsable_id': 'cabinet'}, 0, uuid.uuid4().hex)
        self.assertEqual(etat['responsable_id'], 'cabinet')
        html = dossiers_ui5697.entete_fiche(self.desk, ADMIN, '/p', self.a)
        self.assertIn('Changer', html)

    def test_V100_06_conseil_puis_contentieux(self):
        from agent import accords5689, contentieux5689, playbook5689
        accords5689.schema(self.desk)
        contentieux5689.schema(self.desk)
        playbook5689.schema(self.desk)
        now = self.desk.now()
        self.desk.db.execute("INSERT INTO revues5689(id,mission,matter,version_id,sha256,playbook_id,etat,created,updated) VALUES('rv1','m1',?,'ver-contrat-2','x','pb',"
                             "'analyse',?,?)", (self.b, now, now))
        self.desk.db.execute("INSERT INTO revue_propositions5689(id,revue,ordre,clause_id,clause_titre,clause_sha,ancre,regle_cle,risque,texte_propose,state) "
                             "VALUES('p1','rv1',1,'c1','Clause de non-concurrence','s','a','r','eleve','Texte fictif','proposee')")
        self.desk.db.execute("INSERT INTO accords5689(id,cle,cabinet_id,matter,source_id,version_id,sha256,titre,date_signature,preuve_nature,statut,created,updated) "
                             "VALUES('ac1','k1','cab',?,'s1','v1','h','Pacte fictif','2026-10-01','attestation_avocat','actif',?,?)", (self.b, now, now))
        for i, etat in enumerate(('acceptee', 'proposee', 'proposee')):
            self.desk.db.execute("INSERT INTO occurrences5689(id,cle,obligation,cle_obligation,accord,matter,version_id,numero,echeance,etat,created,updated) "
                                 "VALUES(?,?,'o1','co1','ac1',?,'v1',?,'2026-12-01',?,?,?)", ('oc%d' % i, 'k%d' % i, self.b, i, etat, now, now))
        self.desk.db.execute("INSERT INTO contentieux_links5689(id,matter,claim_id,cle,kind,texte,auteur_type,auteur,affirme_par,statut,created,updated) "
                             "VALUES('l1',?,'cl1','kl1','fait','Le contrat fictif a été résilié','humain','x','KERVILAC SARL','propose',?,?)", (self.c, now, now))
        self.desk.db.commit()
        conseil = dossiers56100.volet_fiche_html(self.desk, AVOCATE, '/p', self.b)
        self.assertIn('Points à négocier', conseil)
        self.assertIn('Clause de non-concurrence', conseil)
        self.assertIn('version <code>ver-contrat-</code>', conseil)
        self.assertIn('1 confirmée (inscrite) · 2 proposées, à décider', conseil)
        contentieux = dossiers56100.volet_fiche_html(self.desk, AVOCATE, '/p', self.c)
        for libelle in ('Actes de procédure', 'Pièces probantes', 'Correspondances', 'Échéances vérifiées'):
            self.assertIn(libelle, contentieux)
        self.assertIn('Selon KERVILAC SARL', contentieux)                               # l'affirmation reste attribuée à sa partie
        with self.assertRaises(Stop):
            dossiers56100.volet_fiche_html(self.desk, AVOCATE, '/p', self.r)            # dossier réservé : même refus neutre
        interface56100.regler_cabinet(self.desk, False)
        self.assertEqual(dossiers56100.volet_fiche_html(self.desk, AVOCATE, '/p', self.b), '')
        interface56100.regler_cabinet(self.desk, True)

    def test_V100_06_historique_et_filtre_conserves(self):
        resultats56100.rattraper(self.desk)
        page = self.ecran(AVOCATE, nature='conseil', q='bernard')
        self.assertIn(self.b, page)
        self.assertNotIn('Travaillés récemment par l’IA', page)                           # en recherche, la liste d'abord
        self.assertIn('name="nature" value="conseil"', page)                              # le filtre est conservé dans la recherche


MESURES = {}


def tearDownModule():   # rapport des mesures après les tests
    T.tearDownModule()
    if MESURES:
        print('\nMESURES lot D (GET Dossiers, rendu de l’écran, 30 essais) :', MESURES)


if __name__ == '__main__':
    unittest.main()
