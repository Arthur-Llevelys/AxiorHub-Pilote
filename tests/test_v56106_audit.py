"""5.6.106, lot A : corrections de l'audit externe de la 5.6.105 (F1 Fichiers, M1 à M5 Microsoft et Outlook).

Chaque défaut est d'abord reproduit (le test échouait sur la 5.6.105), puis corrigé. Faux Nextcloud de ``test_v56103_fichiers`` et faux
Microsoft de ``test_v56103_microsoft`` : aucun appel réseau, aucun modèle. Données fictives (DURAND, MARTIN, ZORVAL, KERVILAC, DOS-001,
example.test).

Identifiants : F1 = protection d'un dossier réservé à travers les opérations sur ses parents ; M1 = changer de compte après « Déconnecter » ;
M2 = tentative de connexion annulée jamais réactivée ; M3 = SharePoint jamais précoché ; M4 = dépôt Outlook interrompu repris sans doublon ;
M5 = « Déposé » seulement si le brouillon distant est bien celui approuvé.
"""
import io
import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from agent import fichiers56103 as F, membres5650
from agent.common import Stop, private_json
import test_v56103_fichiers as tf

DURAND, ADMIN = tf.DURAND, tf.ADMIN
MARTIN = ('martin@example.test', 'avocat')
MUTATIONS = ('COPY', 'MOVE', 'DELETE', 'PUT', 'MKCOL')


# ================================================================================================ F1 · Fichiers et dossiers réservés
class F1Base(tf.Base):
    """Arborescence de recette : un parent visible (« Clients ») qui contient un dossier réservé à MARTIN (DOS-003, ZORVAL)."""

    def setUp(self):
        super().setUp()
        self.f.matters.append({'id': 'DOS-003', 'client_name': 'ZORVAL', 'path': '/Dossiers/Clients/ZORVAL', 'references': ['DOS-003'],
                               'aliases': ['ZORVAL'], 'correspondents': []})
        private_json(self.f.matter_file, self.f.matters)
        self.nc.mkdir('/Dossiers/Clients')
        self.nc.mkdir('/Dossiers/Clients/ZORVAL')
        self.nc.mkdir('/Dossiers/Clients/ZORVAL/Pieces')
        self.nc.mkdir('/Dossiers/Public')
        self.nc.put('/Dossiers/Clients/Liste.pdf', b'%PDF liste des clients')
        self.nc.put('/Dossiers/Clients/ZORVAL/Conclusions ZORVAL.pdf', b'%PDF conclusions confidentielles ZORVAL')
        membres5650.set_reserve(self.desk.c['state_dir'], 'DOS-003', ['martin@example.test'])
        self.avant = len(self.nc.journal)

    def mutations(self):
        return [m for m, _ in self.nc.journal[self.avant:] if m in MUTATIONS]

    def copies_hors_perimetre(self):
        return [k for k in self.nc.t if 'ZORVAL' in k and not k.startswith('/Dossiers/Clients/ZORVAL')]

    def etag(self, chemin, acteur=ADMIN):
        return self.entree(chemin, acteur)['etag']

    def refus(self, fn):
        with self.assertRaises(Stop) as ex:
            fn()
        self.assertEqual(str(ex.exception), 'introuvable')
        self.assertEqual(self.mutations(), [])                                          # zéro commande distante de mutation
        self.assertEqual(self.copies_hors_perimetre(), [])                              # aucune copie hors du périmètre
        self.assertIn('/Dossiers/Clients/ZORVAL/Conclusions ZORVAL.pdf', self.nc.t)       # rien d'emporté ni de déplacé


class F1Recette(F1Base):
    def test_F1_contre_exemple_le_compte_sans_acces_ne_voit_pas_l_enfant(self):
        noms = [x['nom'] for x in F.lister(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/Clients', self.src())['entrees']]
        self.assertEqual(noms, ['Liste.pdf'])                                             # l'enfant réservé est invisible…
        with self.assertRaises(Stop):
            F.proprietes(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/Clients/ZORVAL/Conclusions ZORVAL.pdf', False, self.src())

    def test_F1_copie_du_parent_par_un_compte_sans_acces_refusee(self):
        self.refus(lambda: F.transferer(self.desk, {}, DURAND, 'copier', 'nextcloud', '/Dossiers/Clients', '', 'nextcloud', '/Dossiers/Public',
                                        self.cle(), self.src()))
        self.assertNotIn('/Dossiers/Public/Clients', self.nc.t)

    def test_F1_deplacement_du_parent_par_un_compte_sans_acces_refuse(self):
        self.refus(lambda: F.transferer(self.desk, {}, DURAND, 'deplacer', 'nextcloud', '/Dossiers/Clients', self.etag('/Dossiers/Clients', DURAND),
                                        'nextcloud', '/Dossiers/Public', self.cle(), self.src()))
        self.assertIn('/Dossiers/Clients', self.nc.t)

    def test_F1_renommage_du_parent_refuse_et_lot_controle_avant_le_premier_effet(self):
        elements = [{'chemin': '/Dossiers/DEMO', 'etag': self.etag('/Dossiers/DEMO', DURAND)},
                    {'chemin': '/Dossiers/Clients', 'etag': self.etag('/Dossiers/Clients', DURAND)}]
        self.refus(lambda: F.renommer_lot(self.desk, {}, DURAND, 'nextcloud', '/Dossiers', elements, {'mode': 'ajouter', 'prefixe': 'Ancien '},
                                          self.cle(), self.src()))
        self.assertIn('/Dossiers/DEMO', self.nc.t)                                        # le premier élément, sans risque, n'a pas été renommé

    def test_F1_corbeille_du_parent_refusee(self):
        self.refus(lambda: F.corbeille(self.desk, {}, DURAND, 'nextcloud', '/Dossiers/Clients', self.etag('/Dossiers/Clients', DURAND), self.cle(),
                                       self.src()))
        self.assertEqual(self.nc.trash, [])

    def test_F1_route_refus_neutre_meme_404_qu_un_absent_sans_nom_reserve(self):
        absent = self.appel('transferer', {'mode': 'copier', 's': 'nextcloud', 'p': '/Dossiers/Absent', 'etag': '', 'source_cible': 'nextcloud',
                                           'dossier_cible': '/Dossiers/Public', 'cle': self.cle()})
        self.avant = len(self.nc.journal)                                               # (l'absent a tenté un COPY, refusé 404 par le stockage)
        refuse = self.appel('transferer', {'mode': 'copier', 's': 'nextcloud', 'p': '/Dossiers/Clients', 'etag': '', 'source_cible': 'nextcloud',
                                           'dossier_cible': '/Dossiers/Public', 'cle': self.cle()})
        self.assertEqual((refuse['status'], refuse['json']), (absent['status'], absent['json']))
        self.assertEqual(refuse['status'], '404 Not Found')
        for r in (refuse, self.appel('transferer', {'mode': 'copier', 's': 'nextcloud', 'p': '/Dossiers/Clients', 'etag': '', 'source_cible': 'nextcloud',
                                                    'dossier_cible': '/Dossiers/Public', 'cle': self.cle()}, acteur=ADMIN)):
            self.assertEqual(r['status'], '404 Not Found')
            self.assertNotIn('ZORVAL', r['body'])
            self.assertNotIn('DOS-003', r['body'])
        self.assertEqual(self.mutations(), [])

    def test_F1_administrateur_copie_vers_un_dossier_public_refusee_dans_le_perimetre_acceptee(self):
        for chemin in ('/Dossiers/Clients', '/Dossiers/Clients/ZORVAL', '/Dossiers/Clients/ZORVAL/Conclusions ZORVAL.pdf'):
            with self.subTest(chemin=chemin):
                self.refus(lambda: F.transferer(self.desk, {}, ADMIN, 'copier', 'nextcloud', chemin, '', 'nextcloud', '/Dossiers/Public',
                                                self.cle(), self.src()))
        out = F.transferer(self.desk, {}, ADMIN, 'copier', 'nextcloud', '/Dossiers/Clients/ZORVAL/Conclusions ZORVAL.pdf', '', 'nextcloud',
                           '/Dossiers/Clients/ZORVAL/Pieces', self.cle(), self.src())
        self.assertEqual(out['chemin'], '/Dossiers/Clients/ZORVAL/Pieces/Conclusions ZORVAL.pdf')
        self.assertEqual(self.mutations(), ['COPY'])

    def test_F1_operations_internes_permises_a_qui_y_a_droit(self):
        base = '/Dossiers/Clients/ZORVAL'
        F.transferer(self.desk, {}, MARTIN, 'copier', 'nextcloud', base + '/Conclusions ZORVAL.pdf', '', 'nextcloud', base + '/Pieces', self.cle(), self.src())
        copie = base + '/Pieces/Conclusions ZORVAL.pdf'
        out = F.renommer_lot(self.desk, {}, MARTIN, 'nextcloud', base + '/Pieces', [{'chemin': copie, 'etag': self.etag(copie, MARTIN)}],
                             {'mode': 'ajouter', 'prefixe': 'Copie '}, self.cle(), self.src())
        self.assertTrue(out['ok'])
        copie = base + '/Pieces/Copie Conclusions ZORVAL.pdf'
        F.transferer(self.desk, {}, MARTIN, 'deplacer', 'nextcloud', copie, self.etag(copie, MARTIN), 'nextcloud', base, self.cle(), self.src())
        copie = base + '/Copie Conclusions ZORVAL.pdf'
        F.corbeille(self.desk, {}, MARTIN, 'nextcloud', copie, self.etag(copie, MARTIN), self.cle(), self.src())
        self.assertEqual(self.mutations(), ['COPY', 'MOVE', 'MOVE', 'DELETE'])
        # même pour qui y a droit : la racine réservée ne se déplace pas, ne se renomme pas et ne part pas à la corbeille d'ici
        self.avant = len(self.nc.journal)
        self.refus(lambda: F.transferer(self.desk, {}, MARTIN, 'deplacer', 'nextcloud', base, self.etag(base, MARTIN), 'nextcloud', '/Dossiers/Public',
                                        self.cle(), self.src()))
        self.refus(lambda: F.corbeille(self.desk, {}, MARTIN, 'nextcloud', base, self.etag(base, MARTIN), self.cle(), self.src()))
        self.refus(lambda: F.renommer_lot(self.desk, {}, MARTIN, 'nextcloud', '/Dossiers/Clients', [{'chemin': base, 'etag': self.etag(base, MARTIN)}],
                                          {'mode': 'ajouter', 'suffixe': ' (ancien)'}, self.cle(), self.src()))

    def test_F1_copie_vers_une_autre_source_refusee_pour_une_piece_reservee(self):
        ecrits = []

        class Autre(F.Source):
            ident, libelle = 'autre', 'Autre stockage'

            def racines(self):
                return [{'chemin': '/Ailleurs', 'nom': 'Ailleurs'}]

            def proprietes(self, chemin):
                return F.entree(chemin, True)

            def ecrire(self, chemin, octets, etag=None):
                ecrits.append(chemin)
                return ''
        sources = {**self.src(), 'autre': Autre()}
        self.refus(lambda: F.transferer(self.desk, {}, ADMIN, 'copier', 'nextcloud', '/Dossiers/Clients/ZORVAL/Conclusions ZORVAL.pdf', '', 'autre',
                                        '/Ailleurs', self.cle(), sources))
        self.assertEqual(ecrits, [])
        F.transferer(self.desk, {}, ADMIN, 'copier', 'nextcloud', '/Dossiers/Clients/Liste.pdf', '', 'autre', '/Ailleurs', self.cle(), sources)
        self.assertEqual(ecrits, ['/Ailleurs/Liste.pdf'])                                  # une pièce hors réserve se copie toujours

    def test_F1_zip_d_un_parent_hors_du_perimetre_n_emporte_pas_la_piece_reservee(self):
        out = F.creer_zip(self.desk, {}, ADMIN, 'nextcloud', '/Dossiers', ['/Dossiers/Clients'], 'Clients', self.cle(), self.src())
        noms = zipfile.ZipFile(io.BytesIO(self.nc.lire(out['chemin']))).namelist()
        self.assertIn('Clients/Liste.pdf', noms)
        self.assertFalse([n for n in noms if 'ZORVAL' in n])                               # même pour l'administrateur
        self.assertIn('dossier réservé', out['message'])
        self.assertNotIn('ZORVAL', out['message'])
        dedans = F.creer_zip(self.desk, {}, MARTIN, 'nextcloud', '/Dossiers/Clients/ZORVAL', ['/Dossiers/Clients/ZORVAL/Conclusions ZORVAL.pdf'],
                             'Envoi', self.cle(), self.src())
        self.assertEqual(zipfile.ZipFile(io.BytesIO(self.nc.lire(dedans['chemin']))).namelist(), ['Conclusions ZORVAL.pdf'])
        durand = F.creer_zip(self.desk, {}, DURAND, 'nextcloud', '/Dossiers', ['/Dossiers/Clients'], 'Clients DURAND', self.cle(), self.src())
        self.assertNotIn('réservé', durand['message'])                                    # pour qui ne le voit pas : rien n'est dit
        with self.assertRaises(Stop) as ex:                                               # la racine réservée seule, archivée dans son parent
            F.creer_zip(self.desk, {}, ADMIN, 'nextcloud', '/Dossiers/Clients', ['/Dossiers/Clients/ZORVAL'], 'Sortie', self.cle(), self.src())
        self.assertEqual(str(ex.exception), 'introuvable')

    def test_F1_registre_illisible_aucune_mutation(self):
        for cible in ('agent.membres5650.load', 'agent.common.load_matters'):
            with self.subTest(cible=cible), patch(cible, side_effect=Stop('controle_acces_indisponible')):
                with self.assertRaises(Stop) as ex:                                       # l'administrateur ne lit pas le registre pour voir…
                    F.transferer(self.desk, {}, ADMIN, 'copier', 'nextcloud', '/Dossiers/DEMO/Bail.pdf', '', 'nextcloud', '/Dossiers/Public',
                                 self.cle(), self.src())
                self.assertEqual(str(ex.exception), 'controle_acces_indisponible')        # …mais ses mutations, si : 503, rien n'est fait
        with patch('agent.membres5650.load', side_effect=OSError('illisible')):
            with self.assertRaises(Stop) as ex:
                F.corbeille(self.desk, {}, ADMIN, 'nextcloud', '/Dossiers/DEMO/Bail.pdf', self.etag('/Dossiers/DEMO/Bail.pdf'), self.cle(), self.src())
            self.assertEqual(str(ex.exception), 'controle_acces_indisponible')
        self.assertEqual(self.mutations(), [])

    def test_F1_mutation_sans_le_controle_de_l_arborescence_la_piece_sortirait(self):
        with patch.object(F, '_exiger_arborescence', lambda *a, **k: None):
            F.transferer(self.desk, {}, DURAND, 'copier', 'nextcloud', '/Dossiers/Clients', '', 'nextcloud', '/Dossiers/Public', self.cle(), self.src())
        self.assertEqual(self.nc.lire('/Dossiers/Public/Clients/ZORVAL/Conclusions ZORVAL.pdf'), b'%PDF conclusions confidentielles ZORVAL')

    def test_F1_controle_et_effet_serialises_avec_les_reservations(self):
        """Une réservation demandée pendant l'opération attend la fin de l'effet (verrou commun au registre des réservations)."""
        ordre, demarre = [], threading.Event()
        requete = self.nc.request

        def lent(methode, url, data=None, headers=None, limit=0):
            if methode == 'COPY':
                ordre.append('verrou_pris' if (Path(self.desk.c['state_dir']) / membres5650.LOCK).exists() else 'verrou_absent')
                demarre.set()
                time.sleep(0.4)
                ordre.append('COPY')
            return requete(methode, url, data, headers, limit)

        def reserver():
            demarre.wait(5)
            membres5650.set_reserve(self.desk.c['state_dir'], 'DOS-001', ['martin@example.test'])
            ordre.append('reservation')
        fil = threading.Thread(target=reserver)
        fil.start()
        with patch.object(self.nc, 'request', lent):
            F.transferer(self.desk, {}, ADMIN, 'copier', 'nextcloud', '/Dossiers/Clients/Liste.pdf', '', 'nextcloud', '/Dossiers/Public', self.cle(), self.src())
        fil.join(10)
        self.assertEqual(ordre, ['verrou_pris', 'COPY', 'reservation'])
        self.assertFalse((Path(self.desk.c['state_dir']) / membres5650.LOCK).exists())    # verrou rendu


# ================================================================================================ M · Microsoft (faux Microsoft de la 5.6.103)
from urllib.parse import parse_qs, urlsplit   # noqa: E402

from agent import microsoft56103 as ms, microsoft_web56103 as mw, outlook56103 as ol   # noqa: E402
import test_v56103_microsoft as tm   # noqa: E402

CLAIRE, CAMILLE = tm.CLAIRE, tm.CAMILLE
COMPTE_A, COMPTE_B = tm.COMPTE_CLAIRE, tm.COMPTE_CAMILLE
GRAPH = tm.GRAPH


def requete_url(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


class MBase(tm.Base):
    def appareil(self, owner=CLAIRE, caps=('courrier',)):
        out = ms.demarrer_appareil(self.desk, owner, list(caps))
        return out, list(self.ms.appareils.values())[-1]

    def avec_crochet(self, condition, action):
        """Transport du faux Microsoft avec une action déclenchée une fois, PENDANT l'appel réseau qui vérifie ``condition``."""
        reel, fait = self.ms.__call__, []

        def transport(methode, url, entetes, corps):
            if not fait and condition(methode, url, entetes, corps):
                fait.append(True)
                action()
            return reel(methode, url, entetes, corps)
        return patch.object(ms, 'TRANSPORT', transport)

    def jetons_de(self, compte):
        return {j for j, v in self.ms.acces.items() if v['compte'] == compte}


class M1Deconnexion(MBase):
    def test_M1_apres_deconnecter_un_autre_compte_se_relie_sans_prefill(self):
        self.connecter()
        ms.deconnecter(self.desk, CLAIRE)
        row = self.conn()
        self.assertEqual((row['compte'], row['compte_id']), ('', ''))                   # identité active effacée…
        depart = ms.demarrer_pkce(self.desk, CLAIRE, ['courrier'], 50123)
        self.assertNotIn('login_hint', requete_url(depart['url']))                        # …plus de préremplissage du compte A
        out = ms.terminer_pkce(self.desk, self.ms.consentir(depart['url'], COMPTE_B))
        self.assertEqual((out['connexion']['etat'], out['connexion']['compte']), ('active', COMPTE_B))

    def test_M1_sans_deconnexion_un_autre_compte_reste_refuse(self):
        self.connecter()
        depart = ms.demarrer_pkce(self.desk, CLAIRE, ['agenda'], 50123)
        self.assertEqual(requete_url(depart['url']).get('login_hint'), COMPTE_A)
        with self.assertRaisesRegex(Stop, 'microsoft56103_compte_different'):
            ms.terminer_pkce(self.desk, self.ms.consentir(depart['url'], COMPTE_B))
        row = self.conn()
        self.assertEqual((row['etat'], row['compte']), ('active', COMPTE_A))             # A toujours actif
        self.assertEqual(ms.capacites(row), ['courrier', 'agenda', 'fichiers'])

    def test_M1_deconnexion_generation_et_identite_dans_la_meme_ecriture(self):
        self.connecter()
        avant = self.conn()
        ms.deconnecter(self.desk, CLAIRE)
        apres = self.conn()
        self.assertEqual((apres['generation'], apres['compte'], apres['etat']), (avant['generation'] + 1, '', 'deconnectee'))
        journal = self.desk.db.execute("SELECT data FROM audit WHERE action='microsoft56103_deconnecte'").fetchone()[0]
        self.assertIn(COMPTE_A, journal)                                                  # l'historique reste au journal, pas dans l'identité active

    def test_M1_travail_commence_avant_la_deconnexion_ne_touche_pas_la_nouvelle_liaison(self):
        self.connecter()
        ancien = self.conn()
        ms.deconnecter(self.desk, CLAIRE)
        self.connecter(compte=COMPTE_B)
        n = self.appels_reseau()
        with self.assertRaisesRegex(Stop, 'microsoft56103_connexion_modifiee'):
            ms.appeler(self.desk, ancien['id'], 'GET', GRAPH + '/v1.0/me', generation=ancien['generation'])
        self.assertEqual(self.appels_reseau(), n)                                         # aucun appel avec le jeton de B

    def test_M1_nouvel_essai_apres_401_verifie_la_generation(self):
        self.connecter()
        ancien = self.conn()
        jetons_a = self.jetons_de(COMPTE_A)

        def bascule():                                                                   # pendant l'appel : déconnexion puis liaison de B
            for j in jetons_a:
                self.ms.acces.pop(j, None)                                               # le jeton de A répondra 401
            ms.deconnecter(self.desk, CLAIRE)
            self.connecter(compte=COMPTE_B)
        with self.avec_crochet(lambda m, u, e, c: u.endswith('/v1.0/me/mailFolders/inbox'), bascule):
            with self.assertRaises(Stop):
                ms.appeler(self.desk, ancien['id'], 'GET', GRAPH + '/v1.0/me/mailFolders/inbox')
        jetons_b = self.jetons_de(COMPTE_B)
        apres = [a for a in self.ms.appels if a[1].endswith('/mailFolders/inbox')]
        self.assertEqual(len(apres), 1)                                                   # pas de nouvel essai avec le jeton de B
        self.assertFalse([a for a in apres if a[2].get('Authorization', '')[7:] in jetons_b])

    def test_M1_mutation_sans_effacement_de_l_identite_b_serait_refuse(self):
        self.connecter()
        reel = ms.deconnecter

        def ancien_deconnecter(desk, owner):                                               # comportement de la 5.6.105 : identité gardée
            row = ms.connexion(desk, owner)
            out = reel(desk, owner)
            desk.db.execute('UPDATE microsoft56103_connexions SET compte=?, compte_id=? WHERE id=?', (row['compte'], row['compte_id'], row['id']))
            desk.db.commit()
            return out
        ancien_deconnecter(self.desk, CLAIRE)
        depart = ms.demarrer_pkce(self.desk, CLAIRE, ['courrier'], 50123)
        with self.assertRaisesRegex(Stop, 'microsoft56103_compte_different'):
            ms.terminer_pkce(self.desk, self.ms.consentir(depart['url'], COMPTE_B))


class M2Tentatives(MBase):
    def test_M2_code_annule_pendant_le_sondage_ne_s_active_jamais(self):
        out1, faux1 = self.appareil()
        faux1['reponses'] = ['ok:' + COMPTE_A]
        nouveaux = []
        self.horloge[0] += 5
        with self.avec_crochet(lambda m, u, e, c: u.endswith('/token') and b'device_code=' + faux1_code(self, faux1).encode() in (c or b''),
                               lambda: nouveaux.append(ms.demarrer_appareil(self.desk, CLAIRE, ['courrier']))):
            r = ms.sonder_appareil(self.desk, CLAIRE, out1['flux'])
        self.assertNotEqual(r['etat'], 'accepte')
        self.assertNotEqual(self.conn()['etat'], 'active')
        etats = dict(self.desk.db.execute('SELECT id, etat FROM microsoft56103_appareils').fetchall())
        self.assertEqual((etats[out1['flux']], etats[nouveaux[0]['flux']]), ('annule', 'en_attente'))   # « annulé » jamais écrasé

    def test_M2_seule_la_tentative_courante_active_la_connexion(self):
        out1, faux1 = self.appareil()
        out2, faux2 = self.appareil()
        faux1['reponses'], faux2['reponses'] = ['ok:' + COMPTE_A], ['ok:' + COMPTE_A]
        self.horloge[0] += 5
        self.assertEqual(ms.sonder_appareil(self.desk, CLAIRE, out1['flux'])['etat'], 'annule')
        self.assertEqual(ms.sonder_appareil(self.desk, CLAIRE, out2['flux'])['etat'], 'accepte')

    def test_M2_reponse_tardive_n_ecrase_pas_annule(self):
        out1, faux1 = self.appareil()
        faux1['reponses'] = ['slow_down']
        self.horloge[0] += 5
        with self.avec_crochet(lambda m, u, e, c: u.endswith('/token'), lambda: ms.demarrer_appareil(self.desk, CLAIRE, ['courrier'])):
            ms.sonder_appareil(self.desk, CLAIRE, out1['flux'])
        etat, intervalle = self.desk.db.execute('SELECT etat, intervalle FROM microsoft56103_appareils WHERE id=?', (out1['flux'],)).fetchone()
        self.assertEqual((etat, intervalle), ('annule', 5))

    def test_M2_deconnexion_pendant_un_sondage(self):
        out1, faux1 = self.appareil()
        faux1['reponses'] = ['ok:' + COMPTE_A]
        self.horloge[0] += 5
        with self.avec_crochet(lambda m, u, e, c: u.endswith('/token'), lambda: ms.deconnecter(self.desk, CLAIRE) if self.conn()['etat'] != 'deconnectee' else None):
            r = ms.sonder_appareil(self.desk, CLAIRE, out1['flux'])
        self.assertNotEqual(r['etat'], 'accepte')
        self.assertEqual(self.conn()['etat'], 'deconnectee')
        self.assertEqual(self.desk.db.execute('SELECT etat FROM microsoft56103_appareils WHERE id=?', (out1['flux'],)).fetchone()[0], 'annule')

    def test_M2_deux_onglets_sur_le_meme_code_un_seul_appel_par_intervalle(self):
        out1, faux1 = self.appareil()
        self.horloge[0] += 5
        seconds = []
        with self.avec_crochet(lambda m, u, e, c: u.endswith('/token'), lambda: seconds.append(ms.sonder_appareil(self.desk, CLAIRE, out1['flux']))):
            ms.sonder_appareil(self.desk, CLAIRE, out1['flux'])
        self.assertEqual(seconds[0]['etat'], 'en_attente')
        self.assertEqual(len([a for a in self.ms.appels if a[1].endswith('/token')]), 1)  # le second onglet n'a pas sondé

    def test_M2_retour_expire_pendant_l_echange(self):
        out1, faux1 = self.appareil()
        faux1['reponses'] = ['ok:' + COMPTE_A]
        self.horloge[0] += 5

        def avancer():
            self.horloge[0] += 2000
        with self.avec_crochet(lambda m, u, e, c: u.endswith('/token'), avancer):
            r = ms.sonder_appareil(self.desk, CLAIRE, out1['flux'])
        self.assertNotEqual(r['etat'], 'accepte')
        self.assertNotEqual(self.conn()['etat'], 'active')

    def test_M2_retour_pkce_d_une_tentative_remplacee_ignore(self):
        depart1 = ms.demarrer_pkce(self.desk, CLAIRE, ['courrier'], 50123)
        retour1 = self.ms.consentir(depart1['url'])
        depart2 = ms.demarrer_pkce(self.desk, CLAIRE, ['courrier'], 50124)
        with self.assertRaises(Stop):
            ms.terminer_pkce(self.desk, retour1)
        self.assertNotEqual(self.conn()['etat'], 'active')
        self.assertEqual(ms.terminer_pkce(self.desk, self.ms.consentir(depart2['url']))['connexion']['etat'], 'active')

    def test_M2_retour_pkce_pendant_qu_une_nouvelle_liaison_commence(self):
        depart1 = ms.demarrer_pkce(self.desk, CLAIRE, ['courrier'], 50123)
        retour1 = self.ms.consentir(depart1['url'])
        with self.avec_crochet(lambda m, u, e, c: u.endswith('/token'), lambda: ms.demarrer_appareil(self.desk, CLAIRE, ['courrier'])):
            with self.assertRaisesRegex(Stop, 'microsoft56103_tentative_remplacee'):
                ms.terminer_pkce(self.desk, retour1)
        self.assertNotEqual(self.conn()['etat'], 'active')

    def test_M2_mutation_sans_controle_de_la_tentative_l_ancien_code_s_activerait(self):
        out1, faux1 = self.appareil()
        faux1['reponses'] = ['ok:' + COMPTE_A]
        self.horloge[0] += 5
        with patch.object(ms, '_tentative_courante', lambda *a, **k: True):
            with self.avec_crochet(lambda m, u, e, c: u.endswith('/token'), lambda: ms.demarrer_appareil(self.desk, CLAIRE, ['courrier'])):
                ms.sonder_appareil(self.desk, CLAIRE, out1['flux'])
        self.assertEqual(self.conn()['etat'], 'active')

    def test_M2_le_navigateur_arrete_ses_minuteries_quand_une_liaison_commence(self):
        js = (Path(ms.__file__).parent / 'static' / 'v56103-m.js').read_text(encoding='utf-8')
        self.assertTrue('clearTimeout(minuterie)' in js and 'fluxCourant' in js, 'minuterie du suivi non arrêtée')
        soumission = js.split("form.addEventListener('submit'")[1].split("post('connexion'")[0]
        self.assertIn('arreter();', soumission)                                          # arrêt AVANT la nouvelle liaison


def faux1_code(test, faux):
    return next(code for code, a in test.ms.appareils.items() if a is faux)


class M3Capacites(MBase):
    def cases(self, html):
        import re
        return {m.group(1): ('checked' in m.group(0)) for m in re.finditer(r'<input type="checkbox" name="capacites" value="(\w+)"[^>]*>', html)}

    def test_M3_sharepoint_jamais_precoche_rien_coche_sans_fonction_choisie(self):
        html = mw.section_html(self.desk, '/p', CLAIRE, {})
        self.assertEqual(self.cases(html), {'courrier': False, 'agenda': False, 'fichiers': False, 'sharepoint': False})
        self.assertIn('SharePoint', html)
        self.assertIn('que votre compte peut', html)                                    # libellé qui dit le périmètre

    def test_M3_bouton_agenda_demande_l_agenda_bouton_outlook_le_courrier(self):
        for fonction, attendu, portee in (('agenda', 'agenda', 'Calendars.ReadWrite'), ('courrier', 'courrier', 'Mail.ReadWrite'), ('fichiers', 'fichiers', 'Files.ReadWrite')):
            with self.subTest(fonction=fonction):
                html = mw.section_html(self.desk, '/p', CLAIRE, {'QUERY_STRING': 'rubrique=connexions&microsoft=' + fonction})
                coches = [k for k, v in self.cases(html).items() if v]
                self.assertEqual(coches, [attendu])
                depart = ms.demarrer_pkce(self.desk, CLAIRE, coches, 50123)
                self.assertEqual(set(requete_url(depart['url'])['scope'].split()), {'offline_access', 'User.Read', portee})
        html = mw.section_html(self.desk, '/p', CLAIRE, {'QUERY_STRING': 'microsoft=sharepoint'})
        self.assertFalse(self.cases(html)['sharepoint'])                                  # jamais précoché, même demandé par l'adresse

    def test_M3_flux_serveur_sans_portee_sharepoint_sans_choix_explicite(self):
        self.appareil(caps=('agenda',))
        portees = list(self.ms.appareils.values())[-1]['portees']
        self.assertEqual(set(portees), {'User.Read', 'Calendars.ReadWrite'})
        corps = json.dumps({'capacites': ['courrier', 'sharepoint']}).encode()
        env = {'REQUEST_METHOD': 'POST', 'HTTP_ORIGIN': 'https://cabinet.example.test', 'HTTP_X_CSRF_TOKEN': 'csrf', 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps)}
        n = self.appels_reseau()
        serveur = patch.object(ms, 'mode_pour', lambda env: 'appareil')                  # flux du serveur (code d'appareil)
        serveur.start()
        self.addCleanup(serveur.stop)
        with patch('agent.web567.actor', return_value=(CLAIRE, 'avocat')):
            out = mw.api(env, self.desk, {'origin': 'https://cabinet.example.test', 'csrf': 'csrf'}, '/p', 'm56103/microsoft/connexion', {}, 'POST')
        self.assertEqual(json.loads(out['body'])['error'], 'microsoft56103_sharepoint_non_choisi')
        self.assertEqual(self.appels_reseau(), n)
        corps = json.dumps({'capacites': ['courrier', 'sharepoint'], 'sharepoint_explicite': True}).encode()
        env.update({'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps)})
        with patch('agent.web567.actor', return_value=(CLAIRE, 'avocat')):
            out = mw.api(env, self.desk, {'origin': 'https://cabinet.example.test', 'csrf': 'csrf'}, '/p', 'm56103/microsoft/connexion', {}, 'POST')
        self.assertEqual(out['status'], '200 OK')
        self.assertIn('Files.ReadWrite.All', list(self.ms.appareils.values())[-1]['portees'])

    def test_M3_capacites_affichees_celles_reellement_accordees_et_refus_sans_perte(self):
        reel = self.ms._jetons
        with patch.object(self.ms, '_jetons', lambda compte, portees: reel(compte, [p for p in portees if p != 'Calendars.ReadWrite'])):
            self.connecter(caps=('courrier', 'agenda'))                                   # Microsoft n'accorde pas l'agenda
        self.assertEqual(ms.capacites(self.conn()), ['courrier'])
        html = mw.section_html(self.desk, '/p', CLAIRE, {})
        self.assertIn(ms.LIBELLES['courrier'], html.split('Autorisations accordées')[1].split('</p>')[0])
        self.assertNotIn(ms.LIBELLES['agenda'], html.split('Autorisations accordées')[1].split('</p>')[0])
        depart = ms.demarrer_pkce(self.desk, CLAIRE, ['sharepoint'], 50123)
        etat = requete_url(depart['url'])['state']
        with self.assertRaisesRegex(Stop, 'microsoft56103_autorisation_refusee'):        # consentement SharePoint refusé
            ms.terminer_pkce(self.desk, {'state': etat, 'error': 'access_denied'})
        self.assertEqual((self.conn()['etat'], ms.capacites(self.conn())), ('active', ['courrier']))

    def test_M3_assistant_relie_le_compte_pour_outlook_seulement(self):
        etapes = ms.etapes_assistant(self.desk, CLAIRE)
        self.assertEqual(etapes[0]['action']['capacites'], ['courrier'])
        self.assertTrue(all('sharepoint' not in (e['action'] or {}).get('capacites', []) for e in etapes))


# ------------------------------------------------------------------------------------------------ M4 · dépôt Outlook interrompu
class Arret(Exception):
    """Arrêt brutal du processus simulé (ni Stop, ni réponse : rien n'est enregistré après)."""


class OutlookBase(MBase):
    def setUp(self):
        super().setUp()
        self.connecter()

    def projet(self, pieces=(('Bordereau KERVILAC.pdf', b'%PDF-1.4 bordereau fictif'),), a='camille.martin@example.test', cc='', texte=None):
        return ol.retenir(self.desk, CLAIRE, a, cc, 'DOS-001 — bordereau', texte if texte is not None else 'Maître MARTIN,\nvoici le bordereau.\nBien à vous.', pieces)

    def arret_apres(self, condition):
        """Le faux Microsoft exécute l'appel, puis le processus « s'arrête » avant d'avoir lu la réponse."""
        reel, fait = self.ms.__call__, []

        def transport(methode, url, entetes, corps):
            out = reel(methode, url, entetes, corps)
            if not fait and condition(methode, url):
                fait.append(True)
                raise Arret()
            return out
        return patch.object(ms, 'TRANSPORT', transport)

    def creations(self):
        return [a for a in self.ms.appels if a[0] == 'POST' and a[1].endswith('/mailFolders/drafts/messages')]

    def expirer(self):
        self.horloge[0] += ol.BAIL_DEPOT + 1

    def etat(self, pid):
        return ol.vue(self.desk, CLAIRE, pid)


class M4Reprise(OutlookBase):
    def test_M4_arret_apres_la_creation_distante_reprise_sans_nouvelle_creation(self):
        p = self.projet()
        with self.arret_apres(lambda m, u: m == 'POST' and u.endswith('/mailFolders/drafts/messages')):
            with self.assertRaises(Arret):
                ol.deposer(self.desk, CLAIRE, p['id'], p['version'], 'cle-arret-0001')
        self.assertEqual(self.etat(p['id'])['etat'], 'depot_en_cours')
        with self.assertRaisesRegex(Stop, 'depot_en_cours'):                               # bail en cours : ni second dépôt, ni doublon
            ol.deposer(self.desk, CLAIRE, p['id'], self.etat(p['id'])['version'], 'cle-arret-0002')
        self.expirer()
        rejeu = ol.deposer(self.desk, CLAIRE, p['id'], p['version'], 'cle-arret-0001')      # l'ancien appel rejoué : reprise, pas de blocage
        self.assertEqual(rejeu['etat'], 'incomplet')                                       # retrouvé par sa marque, pièce encore à joindre
        out = ol.deposer(self.desk, CLAIRE, p['id'], rejeu['version'], 'cle-arret-0003')
        self.assertEqual(out['etat'], 'depose')
        self.assertEqual((len(self.creations()), len(self.ms.messages)), (1, 1))           # un seul brouillon distant

    def test_M4_arret_avant_la_creation_rapprochement_explicite_puis_nouvel_essai(self):
        p = self.projet()

        def arret():
            raise Arret()
        with self.avec_crochet(lambda m, u, e, c: m == 'POST' and u.endswith('/mailFolders/drafts/messages'), arret):
            with self.assertRaises(Arret):
                ol.deposer(self.desk, CLAIRE, p['id'], p['version'], 'cle-avant-0001')
        self.expirer()
        v = ol.verifier(self.desk, CLAIRE, p['id'])
        self.assertEqual((v['etat'], v['rapprochement_possible']), ('a_verifier', True))  # aucun résultat : jamais un nouvel essai d'office
        with self.assertRaisesRegex(Stop, 'depot_a_verifier'):
            ol.deposer(self.desk, CLAIRE, p['id'], v['version'], 'cle-avant-0002')
        r = ol.rapprocher(self.desk, CLAIRE, p['id'], v['version'])
        self.assertEqual(r['etat'], 'local')
        self.assertEqual(ol.deposer(self.desk, CLAIRE, p['id'], r['version'], 'cle-avant-0003')['etat'], 'depose')
        self.assertEqual(len(self.ms.messages), 1)

    def test_M4_arret_pendant_les_pieces_jointes_aucune_piece_en_double(self):
        p = self.projet((('Bordereau KERVILAC.pdf', b'%PDF-1.4 bordereau'), ('Annexe DURAND.pdf', b'%PDF-1.4 annexe')))
        with self.arret_apres(lambda m, u: m == 'POST' and u.endswith('/attachments')):
            with self.assertRaises(Arret):
                ol.deposer(self.desk, CLAIRE, p['id'], p['version'], 'cle-pj-0001')
        self.expirer()
        v = ol.verifier(self.desk, CLAIRE, p['id'])
        self.assertEqual(v['etat'], 'incomplet')                                           # la pièce déjà déposée est reconnue par son contenu
        out = ol.deposer(self.desk, CLAIRE, p['id'], v['version'], 'cle-pj-0002')
        self.assertEqual(out['etat'], 'depose')
        (m,) = self.ms.messages.values()
        self.assertEqual(sorted(a['name'] for a in m['attachments']), ['Annexe DURAND.pdf', 'Bordereau KERVILAC.pdf'])

    def test_M4_recherche_en_panne_ou_resultats_multiples_reste_a_verifier(self):
        p = self.projet()
        self.ms.perdre_creation = True
        out = ol.deposer(self.desk, CLAIRE, p['id'], p['version'], 'cle-panne-0001')
        self.assertEqual(out['etat'], 'a_verifier')
        reel = self.ms.__call__

        def coupure(methode, url, entetes, corps):
            if 'filter=' in url:                                                       # recherche par la marque ($filter, encodé)
                return 503, {}, b'{"error": {"code": "ServiceUnavailable"}}'
            return reel(methode, url, entetes, corps)
        with patch.object(ms, 'TRANSPORT', coupure):
            v = ol.verifier(self.desk, CLAIRE, p['id'])
        self.assertEqual((v['etat'], v['rapprochement_possible']), ('a_verifier', False))
        (mid, m), = self.ms.messages.items()
        self.ms.messages['AAMkDOUBLON0001'] = dict(m, id='AAMkDOUBLON0001')
        v = ol.verifier(self.desk, CLAIRE, p['id'])
        self.assertEqual((v['etat'], v['rapprochement_possible']), ('a_verifier', False))
        self.assertIn('Plusieurs brouillons', v['message_etat'])
        with self.assertRaisesRegex(Stop, 'etat_incompatible'):
            ol.rapprocher(self.desk, CLAIRE, p['id'], v['version'])
        self.assertEqual(len(self.creations()), 1)

    def test_M4_deux_reprises_simultanees_un_seul_brouillon(self):
        p = self.projet()
        autres = []

        def second():
            try:
                ol.deposer(self.desk, CLAIRE, p['id'], p['version'] + 1, 'cle-double-0002')
            except Stop as ex:
                autres.append(str(ex))
        with self.avec_crochet(lambda m, u, e, c: m == 'POST' and u.endswith('/mailFolders/drafts/messages'), second):
            out = ol.deposer(self.desk, CLAIRE, p['id'], p['version'], 'cle-double-0001')
        self.assertEqual(out['etat'], 'depose')
        self.assertEqual(autres, ['depot_en_cours'])
        self.assertEqual(len(self.creations()), 1)

    def test_M4_cle_d_idempotence_liee_au_projet_a_la_version_et_au_compte(self):
        p1, p2 = self.projet(), self.projet()
        ol.deposer(self.desk, CLAIRE, p1['id'], p1['version'], 'cle-liee-0001')
        with self.assertRaisesRegex(Stop, 'cle_idempotence_deja_utilisee'):              # autre projet, même clé : conflit
            ol.deposer(self.desk, CLAIRE, p2['id'], p2['version'], 'cle-liee-0001')
        with self.assertRaisesRegex(Stop, 'cle_idempotence_deja_utilisee'):              # autre version, même clé : conflit
            ol.deposer(self.desk, CLAIRE, p1['id'], p1['version'] + 3, 'cle-liee-0001')
        ms.deconnecter(self.desk, CLAIRE)
        self.connecter(compte=COMPTE_B)
        with self.assertRaisesRegex(Stop, 'cle_idempotence_deja_utilisee'):              # autre compte Microsoft, même clé : conflit
            ol.deposer(self.desk, CLAIRE, p1['id'], p1['version'], 'cle-liee-0001')
        self.assertEqual(len(self.ms.messages), 1)

    def test_M4_jamais_de_reprise_sur_un_autre_compte(self):
        p = self.projet()
        with self.arret_apres(lambda m, u: m == 'POST' and u.endswith('/mailFolders/drafts/messages')):
            with self.assertRaises(Arret):
                ol.deposer(self.desk, CLAIRE, p['id'], p['version'], 'cle-compte-0001')
        self.expirer()
        ms.deconnecter(self.desk, CLAIRE)
        self.connecter(compte=COMPTE_B)
        n = self.appels_reseau()
        with self.assertRaisesRegex(Stop, 'compte_modifie'):
            ol.verifier(self.desk, CLAIRE, p['id'])
        with self.assertRaisesRegex(Stop, 'compte_modifie'):
            ol.deposer(self.desk, CLAIRE, p['id'], self.etat(p['id'])['version'], 'cle-compte-0002')
        self.assertEqual(self.appels_reseau(), n)                                         # rien n'est demandé au compte B

    def test_M4_ancien_depot_s_arrete_si_la_connexion_change_en_cours(self):
        p = self.projet((('Bordereau KERVILAC.pdf', b'%PDF-1.4 bordereau'), ('Annexe DURAND.pdf', b'%PDF-1.4 annexe')))

        def bascule():
            ms.deconnecter(self.desk, CLAIRE)
            self.connecter(compte=COMPTE_B)
        with self.avec_crochet(lambda m, u, e, c: m == 'POST' and u.endswith('/attachments'), bascule):
            out = ol.deposer(self.desk, CLAIRE, p['id'], p['version'], 'cle-bascule-0001')
        self.assertNotEqual(out['etat'], 'depose')
        jetons_b = self.jetons_de(COMPTE_B)
        self.assertFalse([a for a in self.ms.appels if '/messages/' in a[1] and a[2].get('Authorization', '')[7:] in jetons_b])

    def test_M4_le_depot_et_la_reprise_restent_des_gestes_de_la_personne(self):
        p = self.projet()
        self.desk.origin_context = 'automatic'
        try:
            for fn in (lambda: ol.verifier(self.desk, CLAIRE, p['id']), lambda: ol.rapprocher(self.desk, CLAIRE, p['id'], p['version']),
                       lambda: ol.deposer(self.desk, CLAIRE, p['id'], p['version'], 'cle-geste-0001')):
                with self.assertRaisesRegex(Stop, 'geste_reserve_a_la_personne'):
                    fn()
        finally:
            self.desk.origin_context = None
        self.assertEqual(self.creations(), [])

    def test_M4_mutation_sans_bail_le_depot_resterait_bloque(self):
        p = self.projet()
        with self.arret_apres(lambda m, u: m == 'POST' and u.endswith('/mailFolders/drafts/messages')):
            with self.assertRaises(Arret):
                ol.deposer(self.desk, CLAIRE, p['id'], p['version'], 'cle-bail-0001')
        self.expirer()
        with patch.object(ol, '_expirer_bail', lambda desk, p: p):
            with self.assertRaises(Stop):
                ol.verifier(self.desk, CLAIRE, p['id'])
            self.assertEqual(self.etat(p['id'])['etat'], 'depot_en_cours')

    def test_M4_route_rapprocher_declaree(self):
        self.assertTrue(mw.allowed('avocat', 'POST', 'm56103/microsoft/brouillon/rapprocher'))
        self.assertEqual(mw.STATUTS.get('depot_en_cours'), '409 Conflict')


# ------------------------------------------------------------------------------------------------ M5 · constat du brouillon déposé
class M5Constat(OutlookBase):
    def modifier_a_la_creation(self, changement):
        """Microsoft (ou une personne dans Outlook) modifie le brouillon juste après sa création."""
        reel = self.ms.__call__

        def transport(methode, url, entetes, corps):
            out = reel(methode, url, entetes, corps)
            if methode == 'POST' and url.endswith('/mailFolders/drafts/messages') and out[0] < 300:
                changement(self.ms.messages[json.loads(out[2])['id']])
            return out
        return patch.object(ms, 'TRANSPORT', transport)

    def deposer_avec(self, changement, **projet):
        p = self.projet(**projet)
        with self.modifier_a_la_creation(changement):
            return ol.deposer(self.desk, CLAIRE, p['id'], p['version'], 'cle-m5-%04d' % len(self.ms.messages))

    def test_M5_destinataire_copie_copie_cachee_et_texte_differents_jamais_depose(self):
        cas = {
            'destinataire': (lambda m: m.update(toRecipients=[{'emailAddress': {'address': 'zorval@example.test'}}]), {}, 'Destinataires'),
            'copie_absente': (lambda m: m.update(ccRecipients=[]), {'cc': 'bernard@example.test'}, 'Copie'),
            'copie_cachee': (lambda m: m.update(bccRecipients=[{'emailAddress': {'address': 'orlandec@example.test'}}]), {}, 'Copie cachée'),
            'texte': (lambda m: m.update(body={'contentType': 'text', 'content': 'Maître MARTIN,\nvoici un autre bordereau.\nBien à vous.'}), {}, 'Texte'),
        }
        for nom, (changement, projet, mot) in cas.items():
            with self.subTest(cas=nom):
                out = self.deposer_avec(changement, **projet)
                self.assertEqual((out['etat'], out['depose']), ('different', False))
                self.assertEqual(out['libelle'], 'Brouillon distant différent : à vérifier')
                self.assertTrue(any(mot in e for e in out['ecarts']), out['ecarts'])
                self.assertTrue(out['lien_outlook'].startswith('https://outlook.'))
        self.assertFalse([a for a in self.ms.appels if a[0] in ('PATCH', 'PUT') and 'graph.microsoft.com' in a[1]])   # jamais réécrit

    def test_M5_simple_variation_crlf_lf_reste_depose(self):
        out = self.deposer_avec(lambda m: m.update(body={'contentType': 'text', 'content': 'Maître MARTIN,\r\nvoici le bordereau.\r\nBien à vous.\r\n'}))
        self.assertEqual(out['etat'], 'depose')

    def test_M5_corps_rendu_en_html_par_outlook_compare_sur_le_texte(self):
        out = self.deposer_avec(lambda m: m.update(body={'contentType': 'html', 'content': '<html><body><p>Maître MARTIN,<br>voici le bordereau.<br>Bien à vous.</p></body></html>'}))
        self.assertEqual(out['etat'], 'depose')

    def test_M5_piece_remplacee_meme_nom_et_meme_taille_detectee(self):
        p = self.projet()
        out = ol.deposer(self.desk, CLAIRE, p['id'], p['version'], 'cle-piece-0001')
        self.assertEqual(out['etat'], 'depose')
        (m,) = self.ms.messages.values()
        piece = m['attachments'][0]
        m['attachments'] = [dict(piece, id='pjREMPLACEE', data=b'%PDF-1.4 bordereau FICTIF')]   # même nom, même taille, autre contenu
        self.assertEqual(len(piece['data']), len(m['attachments'][0]['data']))
        v = ol.verifier(self.desk, CLAIRE, p['id'])
        self.assertEqual(v['etat'], 'different')
        self.assertTrue(any('Bordereau KERVILAC.pdf' in e for e in v['ecarts']), v['ecarts'])

    def test_M5_edition_humaine_apres_le_depot_expliquee_sans_l_effacer(self):
        p = self.projet()
        out = ol.deposer(self.desk, CLAIRE, p['id'], p['version'], 'cle-humain-0001')
        self.assertEqual(out['etat'], 'depose')
        (m,) = self.ms.messages.values()
        m['body'] = {'contentType': 'text', 'content': 'Maître MARTIN,\nvoici le bordereau, avec mes excuses pour le retard.\nBien à vous.'}
        v = ol.verifier(self.desk, CLAIRE, p['id'])
        self.assertEqual(v['etat'], 'different')
        self.assertTrue(any('Texte' in e for e in v['ecarts']))
        self.assertIn('excuses', m['body']['content'])                                     # l'édition faite dans Outlook est intacte
        with self.assertRaisesRegex(Stop, 'brouillon_distant_different'):
            ol.deposer(self.desk, CLAIRE, p['id'], v['version'], 'cle-humain-0002')
        self.assertEqual(len(self.creations()), 1)

    def test_M5_mutation_sans_comparaison_le_brouillon_modifie_serait_declare_depose(self):
        with patch.object(ol, '_ecarts', lambda *a, **k: []):
            out = self.deposer_avec(lambda m: m.update(toRecipients=[{'emailAddress': {'address': 'zorval@example.test'}}]))
        self.assertEqual(out['etat'], 'depose')


if __name__ == '__main__':
    unittest.main()
