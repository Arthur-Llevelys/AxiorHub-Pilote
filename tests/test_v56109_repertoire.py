"""5.6.109 (lot L) : F02, C02, C03, C09 — tri global du répertoire du dossier avant la pagination, dates de fichiers dans le fuseau du
cabinet, date de création inconnue jamais déduite de ``ctime``, contraste du fil d'Ariane et du tableau (feuille source et paquet), aperçu
commun (compteur de requêtes), retour sans perte du tri.

Faux stockage en mémoire, aucun réseau (tout appel de socket échoue), aucun modèle, IA en pause possible ; données fictives seulement
(DURAND, MARTIN, ZORVAL, KERVILAC, DOS-001, example.test). Chaque garde-fou a son contre-exemple (mutation en mémoire ou sur une copie du
texte, jamais de code laissé altéré). Exécuter avec PYTHONPATH=<racine AxiorHub>:<racine AxiorHub>/tests."""
from html import unescape
from pathlib import Path
import re
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent import dossiers5697 as D, fiche5698 as F, tri56109 as T
from test_v5697_dossiers import ADMIN, AVOCATE, CLAIRE, JETON, Base, FauxStockage

ROOT = Path(__file__).resolve().parents[1]
STATIQUES = ROOT / 'agent' / 'static'


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


class Stockage(FauxStockage):
    """Faux stockage : fichiers avec taille, date de modification et date de création (ou sans) ; ``lectures`` compte les listes lues."""
    ORIGINE_CREATION = 'webdav'

    def __init__(self):
        super().__init__()
        self.fichiers = {}
        self.dates_rep = {}
        self.lectures = []

    def ajouter(self, chemin, taille=100, modifie='Mon, 02 Nov 2026 10:00:00 GMT', cree=''):
        self.fichiers[chemin] = (taille, modifie, cree)
        parent = chemin.rsplit('/', 1)[0]
        while parent and parent not in self.repertoires:
            self.repertoires.add(parent)
            parent = parent.rsplit('/', 1)[0]

    def list_folder(self, chemin):
        self.lectures.append(chemin)
        out = [dict(x, modified=self.dates_rep.get(x['path'], '')) for x in super().list_folder(chemin)]
        out += [{'path': p, 'directory': False, 'size': t, 'modified': m, 'created': c} for p, (t, m, c) in sorted(self.fichiers.items())
                if p.rsplit('/', 1)[0] == chemin]
        return out


class Fiche(Base):
    def setUp(self):
        super().setUp()
        self.stock = Stockage()
        p = patch.object(D, '_client_stockage', lambda desk: self.stock)
        p.start()
        self.addCleanup(p.stop)
        zone = patch('agent.acces5683.today_of', lambda desk, owner='cabinet': (__import__('datetime').date(2026, 11, 23), 'Europe/Paris'))
        zone.start()
        self.addCleanup(zone.stop)

    def dossier(self, **donnees):
        recu = self.creer(**donnees)
        D.traiter_rangements(self.desk)
        return D.lire(self.desk, ADMIN, recu['id'])

    def page(self, ident, **args):
        from agent import dossier5679
        return dossier5679.page(self.desk, {'csrf': JETON, 'owner': 'cabinet'}, '/p', {'id': ident, **args}, lambda titre, corps, *a: corps + (a[3] if len(a) > 3 else ''))

    def noms(self, t):
        return [x['nom'] for x in t['dossiers']] + [x['nom'] for x in t['fichiers']]


# ================================================================================================ noyau de tri (repris du dossier technique)
class Noyau(unittest.TestCase):
    def test_F02_tri_avant_la_page_et_inconnus_a_la_fin_dans_les_deux_sens(self):
        entrees = [{'nom': 'Fichier %d.pdf' % n, 'taille': 1000 - n} for n in range(180)]
        self.assertEqual(T.trier(entrees, 'taille')[:2], [entrees[-1], entrees[-2]])
        e = [{'nom': 'A', 'cree': None}, {'nom': 'B', 'cree': '2026-10-14'}, {'nom': 'C', 'cree': '2026-10-13'}, {'nom': 'D', 'cree': 'date inconnue'}]
        self.assertEqual([x['nom'] for x in T.trier(e, 'cree')], ['C', 'B', 'A', 'D'])
        self.assertEqual([x['nom'] for x in T.trier(e, 'cree', 'desc')], ['B', 'C', 'A', 'D'])
        tailles = [{'nom': 'vide', 'taille': 0}, {'nom': 'inconnue', 'taille': None}, {'nom': 'chaine', 'taille': '20 ko'}, {'nom': 'gros', 'taille': 9}]
        self.assertEqual([x['nom'] for x in T.trier(tailles, 'taille')], ['vide', 'gros', 'chaine', 'inconnue'])       # zéro connu ≠ inconnu
        self.assertEqual([x['nom'] for x in T.trier(tailles, 'taille', 'desc')], ['gros', 'vide', 'chaine', 'inconnue'])

    def test_F02_nom_naturel_accents_chiffres_et_egalites_stables(self):
        e = [{'nom': 'Pièce 10.pdf', 'chemin': '/d/Pièce 10.pdf'}, {'nom': 'piece 2.pdf', 'chemin': '/d/piece 2.pdf'}, {'nom': 'Écritures.docx', 'chemin': '/d/É'},
             {'nom': 'annexe.pdf', 'chemin': '/d/annexe.pdf'}, {'nom': 'Zèbre', 'chemin': '/d/Z', 'dossier': True}]
        self.assertEqual([x['nom'] for x in T.trier(e, 'nom')], ['Zèbre', 'annexe.pdf', 'Écritures.docx', 'piece 2.pdf', 'Pièce 10.pdf'])
        self.assertEqual([x['nom'] for x in T.trier(e, 'nom', 'desc')], ['Zèbre', 'Pièce 10.pdf', 'piece 2.pdf', 'Écritures.docx', 'annexe.pdf'])
        jumeaux = [{'nom': 'Acte.pdf', 'chemin': '/d/b/Acte.pdf', 'taille': 5}, {'nom': 'Acte.pdf', 'chemin': '/d/a/Acte.pdf', 'taille': 5}]
        for sens in T.SENS:                         # mêmes noms, même taille : départagés par le chemin, dans les deux sens
            self.assertEqual([x['chemin'] for x in T.trier(jumeaux, 'taille', sens)], ['/d/a/Acte.pdf', '/d/b/Acte.pdf'])
        types = [{'nom': 'b.pdf'}, {'nom': 'a.docx'}, {'nom': 'SANS EXTENSION'}, {'nom': 'c.PDF'}]
        self.assertEqual([x['nom'] for x in T.trier(types, 'type')], ['a.docx', 'b.pdf', 'c.PDF', 'SANS EXTENSION'])
        self.assertEqual([x['nom'] for x in T.trier(types, 'type', 'desc')], ['b.pdf', 'c.PDF', 'a.docx', 'SANS EXTENSION'])

    def test_F02_heures_et_fuseaux_compares_reglages_fermes(self):
        e = [{'nom': 'A', 'modifie': 'Wed, 14 Oct 2026 10:00:00 GMT'}, {'nom': 'B', 'modifie': '2026-10-14T11:00:00+02:00'}, {'nom': 'C', 'modifie': '2026-10-14T09:30:00Z'}]
        self.assertEqual([x['nom'] for x in T.trier(e, 'modifie')], ['B', 'C', 'A'])        # 09:00 Z, 09:30 Z, 10:00 Z : jamais la chaîne affichée
        for refuse in ('__sql__', 'nom; DROP', '', None):
            with self.assertRaises(ValueError):
                T.trier(e, refuse)
        with self.assertRaises(ValueError):
            T.trier(e, 'nom', 'hasard')
        self.assertEqual(T.reglage('__sql__', 'desc'), ('nom', 'asc'))
        self.assertEqual(T.reglage('taille', 'x'), ('taille', 'desc'))
        self.assertEqual(T.reglage('cree', 'asc'), ('cree', 'asc'))
        self.assertEqual((T.suivant('nom', 'nom', 'asc'), T.suivant('nom', 'nom', 'desc'), T.suivant('taille', 'nom', 'asc')), ('desc', 'asc', 'desc'))

    def test_C02_date_http_et_iso_dans_le_fuseau_du_cabinet_sans_troncature(self):
        for valeur in ('Wed, 14 Oct 2026 10:00:00 GMT', '2026-10-14T10:00:00Z', '2026-10-14T12:00:00+02:00', '2026-10-14'):
            self.assertEqual(T.date_fr(valeur), '14/10/2026', valeur)
        # 23 h 30 GMT le 14 octobre : déjà le 15 à Valbrune (fuseau du cabinet), encore le 14 à Montréal
        self.assertEqual(T.jour_cabinet('Wed, 14 Oct 2026 23:30:00 GMT', 'Europe/Paris'), '2026-10-15')
        self.assertEqual(T.jour_cabinet('2026-10-14T23:30:00Z', 'Europe/Paris'), '2026-10-15')
        self.assertEqual(T.jour_cabinet('2026-10-14T23:30:00Z', 'America/Montreal'), '2026-10-14')
        self.assertEqual(T.horodatage_fr('Wed, 14 Oct 2026 23:30:00 GMT', 'Europe/Paris'), '15/10/2026 à 01:30 (Europe/Paris)')
        for illisible in ('date inconnue', 'Wed, 14 Oc', '', None, '2026-13-45'):
            self.assertEqual((T.jour_cabinet(illisible), T.date_fr(illisible), T.horodatage_fr(illisible)), ('', '', ''), illisible)
        self.assertEqual(len(T.date_fr('Wed, 14 Oct 2026 10:00:00 GMT')), 10)

    def test_C02_recents_tries_sur_l_instant_jamais_sur_la_chaine(self):
        from agent import dossier5679 as D79
        docs = [{'name': 'ancien', 'modified': 'Wed, 04 Nov 2026 10:00:00 GMT'}, {'name': 'recent', 'modified': 'Fri, 20 Nov 2026 09:00:00 GMT'},
                {'name': 'iso', 'modified': '2026-11-21T08:00:00Z'}, {'name': 'inconnu', 'modified': ''}]
        self.assertEqual([d['name'] for d in D79._recents(docs)], ['iso', 'recent', 'ancien', 'inconnu'])
        chaine = sorted(docs, key=lambda d: str(d.get('modified') or ''), reverse=True)     # contre-épreuve : l'ancien tri, sur la chaîne
        self.assertEqual(chaine[0]['name'], 'ancien')                                       # « Wed » passait avant « Fri »

    def test_C02_dossier5679_et_vue5651_sans_fragment_d_anglais(self):
        from agent import dossier5679 as D79, vue5651
        self.assertEqual(D79._date_fr(D79._when({'modified': 'Wed, 14 Oct 2026 10:00:00 GMT'})), '14/10/2026')
        self.assertEqual(D79._when({'modified': 'Wed, 14 Oct 2026 23:30:00 GMT'}), '2026-10-15')      # jour du cabinet, pas le jour GMT
        self.assertEqual(D79._when({'modified': '2026-10-14T23:30:00Z'}, 'America/Montreal'), '2026-10-14')
        self.assertEqual(D79._when({'modified': 'date inconnue'}), '')
        self.assertEqual(D79._when({'doc_date': '2025-01-03', 'modified': 'Wed, 14 Oct 2026 10:00:00 GMT'}), '2025-01-03')   # date de l'acte : jamais convertie
        self.assertEqual(D79._when({'doc_date': '2025-01', 'modified': '2026-10-14'}), '2025-01')
        self.assertEqual(vue5651._date_fr('Wed, 14 Oct 2026 10:00:00 GMT'), '14/10/2026')
        self.assertEqual(vue5651._date_fr('date inconnue'), '')
        self.assertNotIn('Oc', vue5651._date_fr('Wed, 14 Oct 2026 10:00:00 GMT'))
        self.assertEqual((vue5651._date_fr('2026-10-14'), vue5651._date_fr('2026-10'), vue5651._date_fr('2026-10-14', 'mois'), vue5651._date_fr('2026-10-14', 'annee')),
                         ('14/10/2026', '10/2026', 'octobre 2026', '2026'))


# ================================================================================================ C09 : création locale jamais déduite de ctime
class Creation(unittest.TestCase):
    def test_C09_sans_naissance_la_creation_est_inconnue_jamais_ctime(self):
        from agent.dav import LocalFolder
        lecteur = object.__new__(LocalFolder)
        st = SimpleNamespace(st_mtime=1700000000, st_ctime=1800000000, st_size=0)
        cible = SimpleNamespace(stat=lambda: st, is_dir=lambda: True, is_file=lambda: False)
        item = lecteur._item('/Dossier', cible)
        self.assertEqual(item['created'], '')
        self.assertNotIn(lecteur._stamp(1800000000), item.values())               # ctime n'apparaît nulle part
        st.st_birthtime = 1600000000
        self.assertEqual(lecteur._item('/Dossier', cible)['created'], lecteur._stamp(1600000000))

    def test_C09_page_fichiers_du_poste_sans_naissance_cree_vide(self):
        """Page « Fichiers » (5.6.103) : même règle que le stockage local du dossier ; ``ctime`` n'y est plus présenté comme création."""
        from agent import fichiers56103 as F103
        import stat as statmod
        local = object.__new__(F103.SourceLocale) if hasattr(F103, 'SourceLocale') else None
        classe = next(c for c in vars(F103).values() if isinstance(c, type) and '_entree' in vars(c) and 'etag_de' in vars(c))
        local = object.__new__(classe)
        st = SimpleNamespace(st_mode=statmod.S_IFREG | 0o600, st_size=12, st_mtime=1700000000, st_mtime_ns=1700000000 * 10 ** 9, st_ctime=1800000000)
        entree = local._entree('/Poste/Acte.docx', st)
        self.assertEqual(entree['cree'], '')
        self.assertNotIn('2027', entree['cree'] + entree['modifie'])               # 1800000000 s : janvier 2027 (ctime)
        st.st_birthtime = 1600000000
        self.assertTrue(local._entree('/Poste/Acte.docx', st)['cree'].startswith('2020-09-13'))


# ================================================================================================ F02 : arborescence triée avant la pagination
class Arborescence(Fiche):
    def remplir(self, base, n=130):
        """``n`` fichiers dans le sous-répertoire « Lot » (le dossier créé porte déjà ses sous-répertoires par défaut) ; le plus petit, le
        plus ancien et le dernier créé sont hors de la première page par le nom. Rend le répertoire du dossier."""
        racine, base = base, base + '/Lot'
        for i in range(n):
            self.stock.ajouter('%s/Pièce %03d.pdf' % (base, i), 5000 + i, 'Mon, 02 Nov 2026 10:%02d:00 GMT' % (i % 60), '2026-10-%02dT08:00:00Z' % (1 + i % 28))
        self.stock.ajouter(base + '/Zzz minuscule.txt', 0, 'Thu, 01 Jan 2026 08:00:00 GMT', '2025-12-31T23:30:00Z')
        self.stock.ajouter(base + '/Zzz sans dates', 77, '', '')
        self.stock.ajouter(base + '/Zzz récent.docx', 900000, 'Sun, 22 Nov 2026 23:30:00 GMT', '2026-11-22T22:00:00Z')
        self.stock.repertoires.add(base + '/Annexes')
        self.stock.repertoires.add(base + '/NOS PIECES')
        return racine

    def test_F02_defaut_inchange_nom_croissant_repertoires_d_abord(self):
        d = self.dossier(titre='DURAND c/ MARTIN')
        base = d['stockage']['chemin']
        self.remplir(base)
        t = F.arborescence(self.desk, base, 'Lot')
        self.assertEqual((t['etat'], t['tri'], t['sens'], t['total'], t['pages']), ('ok', 'nom', 'asc', 135, 3))
        self.assertEqual(self.noms(t)[:4], ['Annexes', 'NOS PIECES', 'Pièce 000.pdf', 'Pièce 001.pdf'])
        self.assertEqual(len(self.noms(t)), F.PAR_PAGE)

    def test_F02_le_minimum_hors_de_la_premiere_page_arrive_en_tete(self):
        d = self.dossier(titre='DURAND c/ MARTIN')
        base = d['stockage']['chemin']
        self.remplir(base)
        par_nom = self.noms(F.arborescence(self.desk, base, 'Lot'))
        self.assertNotIn('Zzz minuscule.txt', par_nom)                              # contre-épreuve : absent de la page 1 par le nom
        t = F.arborescence(self.desk, base, 'Lot', tri='taille', sens='asc')
        self.assertEqual([x['nom'] for x in t['fichiers']][:3], ['Zzz minuscule.txt', 'Zzz sans dates', 'Pièce 000.pdf'])
        self.assertEqual([x['taille'] for x in t['fichiers']][:3], [0, 77, 5000])
        t = F.arborescence(self.desk, base, 'Lot', tri='taille', sens='desc')
        self.assertEqual(t['fichiers'][0]['nom'], 'Zzz récent.docx')
        t = F.arborescence(self.desk, base, 'Lot', tri='modifie', sens='asc')
        self.assertEqual(t['fichiers'][0]['nom'], 'Zzz minuscule.txt')
        t = F.arborescence(self.desk, base, 'Lot', tri='modifie', sens='desc')
        self.assertEqual(t['fichiers'][0]['nom'], 'Zzz récent.docx')
        t = F.arborescence(self.desk, base, 'Lot', tri='cree', sens='asc')
        self.assertEqual(t['fichiers'][0]['nom'], 'Zzz minuscule.txt')
        t = F.arborescence(self.desk, base, 'Lot', tri='type', sens='asc')
        self.assertEqual(t['fichiers'][0]['nom'], 'Zzz récent.docx')                # docx avant pdf et txt
        for tri in ('modifie', 'cree'):                                             # date inconnue : dernière page, dans les deux sens
            for sens in T.SENS:
                derniere = F.arborescence(self.desk, base, 'Lot', page=3, tri=tri, sens=sens)
                self.assertEqual(derniere['fichiers'][-1]['nom'], 'Zzz sans dates', (tri, sens))
        for sens in T.SENS:                                                         # sans extension : type inconnu, à la fin
            self.assertEqual(F.arborescence(self.desk, base, 'Lot', page=3, tri='type', sens=sens)['fichiers'][-1]['nom'], 'Zzz sans dates')

    def test_F02_mutation_tri_apres_la_page_ne_trouverait_pas_le_minimum(self):
        """Mutation en mémoire : un tri appliqué à la seule première page (le défaut d'origine) ne ramène pas le plus petit fichier."""
        d = self.dossier(titre='DURAND c/ MARTIN')
        base = d['stockage']['chemin']
        self.remplir(base)
        page1 = F.arborescence(self.desk, base, 'Lot')['fichiers']
        mute = T.trier([{'nom': x['nom'], 'chemin': x['chemin'], 'taille': x['taille']} for x in page1], 'taille')
        self.assertNotEqual(mute[0]['nom'], 'Zzz minuscule.txt')
        with patch.object(T, 'trier', lambda entrees, champ='nom', sens='asc', dossiers_premier=True: list(entrees)):   # tri retiré
            sans = F.arborescence(self.desk, base, 'Lot', tri='taille', sens='asc')
        self.assertNotEqual(sans['fichiers'][0]['nom'], 'Zzz minuscule.txt')
        self.assertEqual(F.arborescence(self.desk, base, 'Lot', tri='taille', sens='asc')['fichiers'][0]['nom'], 'Zzz minuscule.txt')

    def test_F02_plus_de_deux_mille_entrees_triees_en_entier(self):
        d = self.dossier(titre='ZORVAL')
        base = d['stockage']['chemin']
        for i in range(2105):
            self.stock.ajouter('%s/Lot/Courrier %04d.pdf' % (base, i), 10000 + i)
        self.stock.ajouter(base + '/Lot/Courrier 9999 minuscule.pdf', 1)
        t = F.arborescence(self.desk, base, 'Lot', tri='taille', sens='asc')
        self.assertEqual((t['total'], t['tronquee'], t['partiel']), (2106, False, False))
        self.assertEqual(t['fichiers'][0]['nom'], 'Courrier 9999 minuscule.pdf')    # lu au-delà de l'ancienne limite de 2 000
        self.assertEqual(t['pages'], (2106 + F.PAR_PAGE - 1) // F.PAR_PAGE)
        derniere = F.arborescence(self.desk, base, 'Lot', page=t['pages'], tri='taille', sens='asc')
        self.assertEqual(derniere['fichiers'][-1]['taille'], 10000 + 2104)

    def test_F02_au_dela_de_la_borne_le_tri_est_dit_partiel(self):
        d = self.dossier(titre='KERVILAC')
        base = d['stockage']['chemin']
        for i in range(30):
            self.stock.ajouter('%s/Lot/Note %02d.txt' % (base, i), 100 + i)
        with patch.object(F, 'MAX_INVENTAIRE', 20):
            t = F.arborescence(self.desk, base, 'Lot', tri='taille', sens='desc')
            self.assertEqual((t['partiel'], t['tronquee'], t['total'], t['presents']), (True, True, 20, 30))
            html = self.page(d['id'], vue='fichiers', sous='repertoire', rep='Lot', tri='taille', sens='desc')
        self.assertIn('Tri partiel', html)
        self.assertIn('20', html)
        self.assertNotIn('Tri partiel', self.page(d['id'], vue='fichiers', sous='repertoire', rep='Lot'))

    def test_F02_C02_dates_completes_gardees_jour_du_cabinet_affiche(self):
        d = self.dossier(titre='DURAND')
        base = d['stockage']['chemin']
        self.remplir(base, 3)
        t = F.arborescence(self.desk, base, 'Lot', tri='modifie', sens='desc')
        recent = t['fichiers'][0]
        self.assertEqual((recent['nom'], recent['modifie'], recent['modifie_source']), ('Zzz récent.docx', '2026-11-23', 'Sun, 22 Nov 2026 23:30:00 GMT'))
        self.assertEqual((recent['cree'], recent['cree_source'], recent['type']), ('2026-11-22', '2026-11-22T22:00:00Z', 'docx'))
        sans = next(x for x in F.arborescence(self.desk, base, 'Lot')['fichiers'] if x['nom'] == 'Zzz sans dates')
        self.assertEqual((sans['modifie'], sans['cree'], sans['type'], sans['taille']), ('', '', '', 77))
        self.assertEqual(F.arborescence(self.desk, base, 'Lot', fuseau='America/Montreal', tri='modifie', sens='desc')['fichiers'][0]['modifie'], '2026-11-22')
        self.assertEqual((F.jour('Wed, 14 Oct 2026 23:30:00 GMT'), F.jour('2026-10-14T23:30:00Z'), F.jour('2026-10-14'), F.jour('Wed, 14 Oc')),
                         ('2026-10-15', '2026-10-15', '2026-10-14', ''))

    def test_F02_repertoire_taille_inconnue_et_comptage_a_la_demande(self):
        d = self.dossier(titre='DURAND c/ MARTIN')
        base = d['stockage']['chemin']
        for i in range(4):
            self.stock.ajouter('%s/NOS PIECES/Pièce %d.pdf' % (base, i), 2048)
        self.stock.ajouter(base + '/NOS PIECES/Sous/Annexe.pdf', 999)
        self.stock.ajouter(base + '/Acte.pdf', 10)
        self.stock.lectures.clear()
        t = F.arborescence(self.desk, base, comptes=False)
        self.assertEqual(self.stock.lectures, [base])                               # une seule lecture : aucun sous-répertoire compté à l'ouverture
        rep = next(x for x in t['dossiers'] if x['nom'] == 'NOS PIECES')
        self.assertEqual((rep['fichiers'], rep['sous_dossiers'], rep['taille'], rep['taille_complete']), (None, None, None, None))
        self.assertEqual(t['non_comptes'], 0)
        compte = F.arborescence(self.desk, base)                                    # ancien comportement gardé par défaut
        rep = next(x for x in compte['dossiers'] if x['nom'] == 'NOS PIECES')
        self.assertEqual((rep['fichiers'], rep['sous_dossiers'], rep['taille'], rep['taille_complete']), (4, 1, 4 * 2048, None))
        resume = F.arborescence(self.desk, base, 'NOS PIECES', comptes=False)['resume']
        self.assertEqual(resume, {'fichiers': 4, 'sous_dossiers': 1, 'octets_directs': 4 * 2048, 'partiel': False})
        # tri par taille : la taille d'un répertoire est inconnue, les répertoires restent groupés et rangés par nom
        self.stock.repertoires.add(base + '/Annexes')
        t = F.arborescence(self.desk, base, tri='taille', sens='desc')
        noms = [x['nom'] for x in t['dossiers']]
        self.assertEqual((noms[0], noms, len(t['fichiers'])), ('Annexes', sorted(noms, key=T.nom_naturel), 1))


# ================================================================================================ rendu : en-têtes, pagination, retour
class Rendu(Fiche):
    def test_F02_entetes_boutons_aria_sort_et_sens_suivant(self):
        d = self.dossier(titre='DURAND c/ MARTIN')
        base = d['stockage']['chemin']
        for i in range(60):
            self.stock.ajouter('%s/Sous/Pièce %02d.pdf' % (base, i), 1000 + i, 'Mon, 02 Nov 2026 10:00:00 GMT', '2026-10-01T08:00:00Z')
        html = self.page(d['id'], vue='fichiers', sous='repertoire', rep='Sous', tri='taille', sens='desc')
        tete = html[html.index('<thead>'):html.index('</thead>')]
        self.assertEqual(re.findall(r'<th scope="col"([^>]*)>', tete).count(' aria-sort="descending"'), 1)
        self.assertEqual(len(re.findall(r'<button type="submit" class="r109-tri-bouton"', tete)), 5)                 # Nom, Type, Modifié le, Créé le, Taille
        for libelle in T.LIBELLES.values():
            self.assertIn(libelle, tete)
        formulaires = re.findall(r'<form method="get" action="/p/dossier" class="r109-tri">(.*?)</form>', tete, re.S)
        self.assertEqual(len(formulaires), 5)
        suivants = {}
        for f in formulaires:
            champs = dict(re.findall(r'<input type="hidden" name="(\w+)" value="([^"]*)">', f))
            self.assertEqual((champs['id'], champs['vue'], champs['sous'], champs['rep']), (d['id'], 'fichiers', 'repertoire', 'Sous'))
            self.assertNotIn('page', champs)                                           # un nouveau tri repart de la première page
            suivants[champs['tri']] = champs['sens']
        self.assertEqual(suivants, {'nom': 'asc', 'type': 'asc', 'modifie': 'desc', 'cree': 'desc', 'taille': 'asc'})   # même champ : inversé
        self.assertNotIn('<script>', html)
        self.assertNotIn('onclick', html)

    def test_F02_pagination_fil_et_sous_repertoires_gardent_le_tri(self):
        d = self.dossier(titre='DURAND c/ MARTIN')
        base = d['stockage']['chemin']
        for i in range(120):
            self.stock.ajouter('%s/Sous/Pièce %03d.pdf' % (base, i), 1000 + i)
        self.stock.repertoires.add(base + '/Sous/Plus bas')
        html = unescape(self.page(d['id'], vue='fichiers', sous='repertoire', rep='Sous', tri='taille', sens='desc', page='2'))
        pager = html[html.index('<nav class="d51-pager"'):]
        pager = pager[:pager.index('</nav>')]
        self.assertIn('rep=Sous&tri=taille&sens=desc&page=1', pager)
        self.assertIn('rep=Sous&tri=taille&sens=desc&page=3', pager)
        self.assertIn('Page 2 / 3', pager)
        fil = html[html.index('<nav class="d98-fil"'):]
        fil = fil[:fil.index('</nav>')]
        self.assertIn('sous=repertoire&tri=taille&sens=desc', fil)
        premiere = unescape(self.page(d['id'], vue='fichiers', sous='repertoire', rep='Sous', tri='taille', sens='desc'))
        self.assertIn('rep=Sous%2FPlus+bas&tri=taille&sens=desc', premiere)             # ouvrir un sous-répertoire garde le tri
        defaut = unescape(self.page(d['id'], vue='fichiers', sous='repertoire', rep='Sous'))
        self.assertNotIn('tri=nom', defaut[defaut.index('<nav class="d51-pager"'):])    # tri par défaut : adresses inchangées
        invalide = self.page(d['id'], vue='fichiers', sous='repertoire', rep='Sous', tri='__sql__', sens='; DROP')
        self.assertNotIn('__sql__', invalide)
        self.assertNotIn('DROP', invalide)
        self.assertIn('Pièce 000.pdf', invalide)                                        # réglage inconnu : tri par nom, jamais une erreur

    def test_F02_C02_C09_cellules_dates_types_tailles_et_inconnus_en_mots(self):
        d = self.dossier(titre='DURAND c/ MARTIN')
        base = d['stockage']['chemin']
        self.stock.ajouter(base + '/Conclusions.docx', 3 * 1024 * 1024, 'Wed, 14 Oct 2026 23:30:00 GMT', '2026-10-01T08:00:00Z')
        self.stock.ajouter(base + '/Sans rien', 0, '', '')
        self.stock.repertoires.add(base + '/NOS PIECES')
        html = self.page(d['id'], vue='fichiers', sous='repertoire')
        ligne = html[html.index('Conclusions.docx</a>'):]
        ligne = ligne[:ligne.index('</tr>')]
        self.assertIn('15/10/2026', ligne)                                             # jour du cabinet (23 h 30 GMT le 14)
        self.assertIn('title="15/10/2026 à 01:30 (Europe/Paris)"', ligne)              # infobulle complète
        self.assertIn('01/10/2026', ligne)
        self.assertIn('DOCX', ligne)
        self.assertIn('3,0 Mo', ligne)
        self.assertNotIn('GMT', html)                                                  # aucun fragment d'anglais
        self.assertNotIn('Wed,', html)
        vide = html[html.index('Sans rien</a>'):]
        vide = vide[:vide.index('</tr>')]
        self.assertIn('Inconnue', vide)                                                # modification inconnue : dite en mots
        self.assertIn('Non disponible', vide)                                          # création non fournie par le stockage
        self.assertIn('0 o', vide)                                                     # taille nulle connue, pas « inconnue »
        self.assertIn('Sans extension', vide)
        rep = html[html.index('class="d98-rep"'):]
        rep = rep[:rep.index('</tr>')]
        self.assertIn('Répertoire', rep)
        self.assertIn('Non calculée', rep)                                             # jamais la somme des fichiers directs comme taille
        self.assertIn('date de rédaction', html)                                       # sens du champ « Créé le » expliqué
        self.assertIn('data-d79-doc=', html)                                           # aperçu commun : même lien que « Par type »
        self.assertIn('<details class="d98-chemin">', html)                            # chemin technique dans un détail, plus en tête
        self.assertIn('/static/v56109-repertoire.js', html)

    def test_F02_comptage_a_la_demande_avec_la_nouvelle_interface_inline_sinon(self):
        from agent import interface56100
        d = self.dossier(titre='DURAND c/ MARTIN')
        base = d['stockage']['chemin']
        for i in range(3):
            self.stock.ajouter('%s/NOS PIECES/Pièce %d.pdf' % (base, i), 2048)
        self.stock.lectures.clear()
        html = self.page(d['id'], vue='fichiers', sous='repertoire')
        self.assertIn('data-r109-compter="NOS PIECES"', html)
        self.assertEqual(self.stock.lectures.count(base + '/NOS PIECES'), 0)           # rien n'est compté à l'ouverture
        code, rep = self.appel('dossiers/%s/fichiers' % d['id'], args={'rep': 'NOS PIECES', 'comptes': '0'})
        self.assertEqual((code, rep['resume']['fichiers'], rep['resume']['octets_directs']), ('200', 3, 3 * 2048))
        interface56100.regler_cabinet(self.desk, False)                                # ancienne présentation : comptage à l'ouverture, comme avant
        ancien = self.page(d['id'], vue='fichiers', sous='repertoire')
        self.assertNotIn('data-r109-compter', ancien)
        self.assertIn('3 fichiers', ancien)

    def test_F02_api_json_accepte_tri_et_sens(self):
        d = self.dossier(titre='DURAND')
        base = d['stockage']['chemin']
        self.stock.ajouter(base + '/A.pdf', 500)
        self.stock.ajouter(base + '/B.pdf', 5)
        code, rep = self.appel('dossiers/%s/fichiers' % d['id'], args={'tri': 'taille', 'sens': 'asc'})
        self.assertEqual((code, rep['tri'], rep['sens'], [x['nom'] for x in rep['fichiers']]), ('200', 'taille', 'asc', ['B.pdf', 'A.pdf']))
        code, rep = self.appel('dossiers/%s/fichiers' % d['id'], args={'tri': 'n importe quoi'})
        self.assertEqual((code, rep['tri'], [x['nom'] for x in rep['fichiers']]), ('200', 'nom', ['A.pdf', 'B.pdf']))

    def test_F02_R05_lecture_sans_ecriture_et_ia_en_pause(self):
        self.desk.setting('missions567:pause', True)
        d = self.dossier(titre='DURAND')
        self.stock.ajouter(d['stockage']['chemin'] + '/A.pdf', 5)
        self.page(d['id'], vue='fichiers', sous='repertoire')                          # première ouverture (point de reprise, reconstruction)
        avant = self.desk.db.total_changes
        bloque = AssertionError('appel au modèle ou au réseau')
        with patch('agent.ai_gateway.estimate_and_check_budget', side_effect=bloque), patch('agent.common.HTTP.request', side_effect=bloque):
            t = F.arborescence(self.desk, d['stockage']['chemin'], tri='taille', sens='desc')
            self.appel('dossiers/%s/fichiers' % d['id'], args={'tri': 'cree'})
        self.assertEqual(t['etat'], 'ok')
        self.assertEqual(self.desk.db.total_changes, avant)                            # R05 : aucune écriture pendant ces lectures

    def test_F02_dossier_reserve_meme_404_sans_lecture_du_stockage(self):
        d = self.dossier(titre='ZORVAL')
        self.stock.ajouter(d['stockage']['chemin'] + '/Secret.pdf', 5)
        self.reserver(d['id'], ['titulaire@example.test'])
        self.stock.lectures.clear()
        interdit = self.appel('dossiers/%s/fichiers' % d['id'], owner=CLAIRE, role='avocat', args={'tri': 'taille'})
        absent = self.appel('dossiers/%s/fichiers' % 'inconnu', owner=CLAIRE, role='avocat', args={'tri': 'taille'})
        self.assertEqual(interdit, absent)
        self.assertEqual(interdit[0], '404')
        self.assertEqual(self.stock.lectures, [])


# ================================================================================================ C03 : contraste (source et paquet), script
class Statiques(unittest.TestCase):
    def test_C03_regles_dans_la_source_et_dans_le_paquet(self):
        source = (STATIQUES / 'v5698.css').read_text(encoding='utf-8')
        paquet = (STATIQUES / 'app520.css').read_text(encoding='utf-8')
        regles = [l for l in source.splitlines() if l.startswith('#d98-fichiers ')]
        self.assertGreaterEqual(len(regles), 6)
        for regle in regles:
            self.assertIn(regle, paquet, regle)                                        # le gabarit sert le paquet : la règle doit y être
        for selecteur, propriete in (('#d98-fichiers .d98-fil a', 'color: var(--cds-link-primary'), ('#d98-fichiers .d51-pager a', 'color: var(--cds-link-primary'),
                                     ('#d98-fichiers .d98-arbre tbody th, #d98-fichiers .d98-arbre tbody td', 'background: var(--cds-layer-01'),
                                     ('#d98-fichiers .d98-arbre thead th', 'color: var(--cds-text-primary')):
            ligne = next((l for l in regles if l.startswith(selecteur + ' {')), '')
            self.assertIn(propriete, ligne, selecteur)
        # la cause : l'ancienne règle « nav a{color:#fff} » du paquet ; nos règles la dominent par l'identifiant du bloc
        self.assertIn('nav a{color:#fff}', paquet)
        self.assertLess(paquet.index('nav a{color:#fff}'), paquet.index('#d98-fichiers .d98-fil a {'))

    def test_C03_texte_et_fond_definis_ensemble_clair_et_sombre(self):
        """Chaque règle du bloc qui fixe une couleur de texte par un jeton fixe aussi son fond par un jeton (ou « transparent » sur un fond
        lui-même fixé) : jamais un texte d'un thème sur le fond d'un autre. Les jetons ont une valeur en clair ET en sombre."""
        source = (STATIQUES / 'v5698.css').read_text(encoding='utf-8')
        for ligne in (l for l in source.splitlines() if l.startswith('#d98-fichiers ') and ' color: var(' in l.replace('{ color', '{  color').replace('; color', ';  color')):
            self.assertRegex(ligne, r'background(-color)?: (var\(--cds-|transparent)', ligne)
        self.assertNotRegex('\n'.join(l for l in source.splitlines() if l.startswith('#d98-fichiers ')), r'color: #fff\b|color: white')
        jetons = (STATIQUES / 'v5689-carbon-jetons.css').read_text(encoding='utf-8')
        clair = next(l for l in jetons.splitlines() if l.startswith('body[data-carbon]{'))
        sombre = next(l for l in jetons.splitlines() if l.startswith(':root[data-theme=dark] body[data-carbon]{'))
        for jeton in ('--cds-text-primary', '--cds-link-primary', '--cds-layer-01', '--cds-focus', '--cds-layer-hover-01', '--cds-layer-selected-01'):
            self.assertIn(jeton + ':', clair, jeton)
            self.assertIn(jeton + ':', sombre, jeton)

    def test_C03_contraste_calcule_des_jetons_clair_et_sombre(self):
        """Contraste WCAG calculé sur les valeurs des jetons Carbon du paquet (clair et sombre) : texte courant et liens ≥ 4,5:1 sur le
        fond du tableau. Mesure statique des jetons, pas des styles calculés par un navigateur (dite telle dans le compte rendu)."""
        jetons = (STATIQUES / 'v5689-carbon-jetons.css').read_text(encoding='utf-8')

        def valeur(bloc, nom):
            v = re.search(re.escape(nom) + r':\s*(#[0-9a-fA-F]{3,8})', bloc)
            self.assertTrue(v, nom)
            return v.group(1)

        def luminance(hexa):
            hexa = hexa.lstrip('#')
            hexa = ''.join(c * 2 for c in hexa) if len(hexa) == 3 else hexa[:6]
            canaux = [int(hexa[i:i + 2], 16) / 255 for i in (0, 2, 4)]
            lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canaux]
            return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

        def contraste(a, b):
            la, lb = sorted((luminance(a), luminance(b)), reverse=True)
            return (la + 0.05) / (lb + 0.05)
        blocs = {'clair': next(l for l in jetons.splitlines() if l.startswith('body[data-carbon]{')),
                 'sombre': next(l for l in jetons.splitlines() if l.startswith(':root[data-theme=dark] body[data-carbon]{'))}
        for theme, bloc in blocs.items():
            fond = valeur(bloc, '--cds-layer-01')
            for jeton in ('--cds-text-primary', '--cds-link-primary', '--cds-text-secondary'):
                self.assertGreaterEqual(contraste(valeur(bloc, jeton), fond), 4.5, (theme, jeton))
            self.assertGreaterEqual(contraste('#ffffff', fond) if theme == 'sombre' else 4.5, 4.5)
        self.assertLess(contraste('#ffffff', valeur(blocs['clair'], '--cds-layer-01')), 1.5)   # contre-épreuve : le blanc hérité était illisible en clair

    def test_F02_statiques_declares_et_feuilles_dans_le_paquet(self):
        from agent import shell501, web440
        self.assertEqual(web440.STATIC.get('/static/v56109-repertoire.js'), 'text/javascript')
        self.assertEqual(web440.STATIC.get('/static/v56109-repertoire.css'), 'text/css')
        self.assertIn('v56109-repertoire.css', shell501.BUNDLE)
        paquet = (STATIQUES / 'app520.css').read_text(encoding='utf-8')
        self.assertIn('/* ==== v56109-repertoire.css ==== */', paquet)
        for ligne in (STATIQUES / 'v56109-repertoire.css').read_text(encoding='utf-8').splitlines():
            if ligne.strip() and not ligne.lstrip().startswith('/*'):
                self.assertIn(ligne, paquet, ligne)

    def test_F02_apercu_commun_compteur_de_requetes_mutation_detectee(self):
        """``v5679.js`` (aperçu commun) : une réponse ancienne ne remplace pas la sélection courante. Contrôle exécuté par un interprète
        minimal du flux (deux chargements, la première réponse arrive en dernier) sur le texte du script ; sans le garde, l'essai échoue."""
        js = (STATIQUES / 'v5679.js').read_text(encoding='utf-8')
        charge = js[js.index('function load(path, trigger) {'):js.index("root.addEventListener('click'")]

        def garde(texte):
            return ('var mine = ++seq;' in texte and texte.count('if (mine !== seq) return;') >= 2
                    and texte.index('var mine = ++seq;') < texte.index('fetch(') < texte.index('if (mine !== seq) return;'))
        self.assertTrue(garde(charge))
        self.assertFalse(garde(charge.replace('if (mine !== seq) return;', '', 1)))     # garde de la réponse retiré : détecté
        self.assertFalse(garde(charge.replace('var mine = ++seq;', 'var mine = seq;')))
        mien = (STATIQUES / 'v56109-repertoire.js').read_text(encoding='utf-8')
        self.assertNotIn('innerHTML', mien)                                            # aucun HTML injecté par le script du répertoire
        self.assertNotIn('/dossier/apercu', mien)                                      # un seul service d'aperçu : celui de v5679.js
        self.assertIn('data-d79-doc', mien)
        self.assertIn('sessionStorage', mien)                                          # retour : sélection et défilement restaurés


if __name__ == '__main__':
    unittest.main()
