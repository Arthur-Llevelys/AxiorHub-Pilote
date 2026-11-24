"""5.6.109 (lot D, F05) : barèmes configurables et devis motivés — catalogue de prestations du cabinet (``catalogue56109``), calcul
déterministe, publication et versions immuables, bibliothèque de 77 trames sans prix, priorité à la convention signée, vue client pour l'IA,
raccord au moteur d'honoraires existant (``honoraires5653``), routes ``m56109/honoraires/…``, Paramètres › Cabinet.

Aucun réseau (tout appel de socket échoue), aucun modèle ; montants et noms fictifs seulement (DURAND, MARTIN, ZORVAL, DOS-001, example.test)."""
import io
import json
import socket
import sqlite3
import unittest
import uuid
from unittest.mock import patch

from agent import catalogue56109 as C, honoraires5653 as fees, membres5650
from agent.common import Stop
from test_v5697_dossiers import ADMIN, AVOCATE, CAMILLE, CLAIRE, JETON, ORIGINE, Base

JOUR = '2026-11-23'


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


def offre(**plus):
    """Prestation publiée fictive (montants d'essai, sans rapport avec un tarif réel)."""
    o = {'id': 'fictif-divorce', 'version': 1, 'statut': 'publie', 'valide_par': 'avocat', 'devise': 'EUR', 'mode': 'forfait', 'libelle': 'Mission fictive',
         'fixe_ht_cents': 100000, 'debut': '2026-01-01', 'fin': None, 'tva_statut': 'confirme', 'tva_pct': '20',
         'champs': {'enfants': {'type': 'booleen', 'question': 'Y a-t-il des enfants concernés ?'}},
         'regles': [{'id': 'enfants', 'libelle_client': 'Diligences supplémentaires définies', 'si': [{'champ': 'enfants', 'op': 'eq', 'valeur': True}],
                     'action': {'type': 'ajout_fixe', 'montant_ht_cents': 10000}}],
         'inclus': ['Projet et un rendez-vous'], 'exclus': ['Appel']}
    o.update(plus)
    return o


OUI = {'enfants': {'valeur': True, 'statut': 'confirme'}}
NON = {'enfants': {'valeur': False, 'statut': 'confirme'}}


# ------------------------------------------------------------------------------------------------ calcul déterministe
class F05Calcul(unittest.TestCase):
    def calc(self, o=None, donnees=None, accord=None):
        return C.evaluer(o or offre(), OUI if donnees is None else donnees, au=JOUR, accord=accord)

    def test_F05_forfait_conditionnel_tva_et_provision(self):
        r = self.calc(offre(provision_pct='30'))
        self.assertEqual((r['statut'], r['total_ht_cents'], r['tva_cents'], r['ttc_cents'], r['provision_ht_cents']), ('proposition', 110000, 22000, 132000, 33000))
        self.assertEqual(r['regles_appliquees'], ['enfants'])
        self.assertEqual([l['montant_ht_cents'] for l in r['lignes']], [100000, 10000])
        self.assertEqual(self.calc()['empreinte'], self.calc()['empreinte'])                       # même entrée, même empreinte

    def test_F05_inconnu_n_est_ni_non_ni_zero(self):
        for d in ({}, {'enfants': {'valeur': True, 'statut': 'extrait'}}, {'enfants': {'valeur': None, 'statut': 'confirme'}}):
            r = self.calc(donnees=d)
            self.assertEqual((r['statut'], r['total_ht_cents']), ('a_completer', None))
            self.assertEqual(r['questions'], [{'champ': 'enfants', 'question': 'Y a-t-il des enfants concernés ?'}])
        self.assertEqual(self.calc(donnees=NON)['total_ht_cents'], 100000)

    def test_F05_branche_fausse_ne_demande_pas_un_critere_inutile(self):
        o = offre()
        o['champs']['immobilier'] = {'type': 'booleen'}
        o['regles'][0]['si'].append({'champ': 'immobilier', 'op': 'eq', 'valeur': True})
        r = self.calc(o, NON)
        self.assertEqual((r['statut'], r['questions']), ('proposition', []))

    def test_F05_types_stricts_aucun_flottant_aucun_code(self):
        with self.assertRaisesRegex(ValueError, 'booleen_invalide'):
            self.calc(donnees={'enfants': {'valeur': 'false', 'statut': 'confirme'}})
        with self.assertRaisesRegex(ValueError, 'forfait_invalide'):
            self.calc(offre(fixe_ht_cents=1000.0))                                                   # un flottant n'est jamais un montant
        with self.assertRaisesRegex(ValueError, 'forfait_invalide'):
            self.calc(offre(fixe_ht_cents=True))
        o = offre()
        o['regles'][0]['si'][0]['op'] = '__import__'
        with self.assertRaisesRegex(ValueError, 'condition_invalide'):
            self.calc(o)
        for mauvais in ('NaN', 'Infinity', '-1', 20.0):
            with self.assertRaisesRegex(ValueError, 'tva_invalide'):
                self.calc(offre(tva_pct=mauvais))
        with self.assertRaises(ValueError):
            C.entier(12.5, 'montant')

    def test_F05_prix_absent_n_est_pas_zero_et_zero_exige_la_gratuite(self):
        self.assertEqual(self.calc(offre(fixe_ht_cents=None))['statut'], 'a_chiffrer')
        self.assertIsNone(self.calc(offre(fixe_ht_cents=None))['total_ht_cents'])
        with self.assertRaisesRegex(ValueError, 'zero_sans_gratuite'):
            self.calc(offre(fixe_ht_cents=0, regles=[]))
        r = self.calc(offre(fixe_ht_cents=0, regles=[], gratuite=True, motif_gratuite='Choix fictif explicite'))
        self.assertEqual((r['total_ht_cents'], r['gratuite']), (0, True))
        o = offre()
        o['regles'][0]['action']['montant_ht_cents'] = None                                           # supplément déclenché mais non chiffré
        self.assertEqual(self.calc(o)['statut'], 'a_chiffrer')
        self.assertEqual(self.calc(o, NON)['total_ht_cents'], 100000)                                 # règle non déclenchée : calcul complet

    def test_F05_convention_signee_prioritaire_et_verifiee(self):
        r = self.calc(accord={'statut': 'signe', 'source_id': 'CONV-1'})
        self.assertEqual(r['statut'], 'accord_existant')
        self.assertNotIn('total_ht_cents', r)
        with self.assertRaisesRegex(ValueError, 'accord_non_verifie'):
            self.calc(accord={'statut': 'mentionne dans un courriel', 'source_id': 'x'})

    def test_F05_brouillon_pas_encore_applicable_et_expiree(self):
        with self.assertRaisesRegex(ValueError, 'offre_non_publiee'):
            self.calc(offre(statut='brouillon'))
        with self.assertRaisesRegex(ValueError, 'offre_non_publiee'):
            self.calc(offre(valide_par=''))
        with self.assertRaisesRegex(ValueError, 'offre_expiree'):
            self.calc(offre(fin=JOUR))
        with self.assertRaisesRegex(ValueError, 'offre_pas_encore_applicable'):
            self.calc(offre(debut='2027-01-01'))

    def test_F05_pourcentage_sur_la_base_sans_capitalisation_et_arrondi(self):
        o = offre()
        o['regles'].append({'id': 'pct', 'libelle_client': 'Complexité fictive', 'si': [{'champ': 'enfants', 'op': 'eq', 'valeur': True}],
                            'action': {'type': 'pourcentage_base', 'pct': '10'}})
        self.assertEqual(self.calc(o)['total_ht_cents'], 120000)                                      # 10 % de 1 000, pas de 1 100
        o = offre(fixe_ht_cents=33333, regles=[{'id': 'pct', 'libelle_client': 'Majoration', 'si': [{'champ': 'enfants', 'op': 'eq', 'valeur': True}],
                                               'action': {'type': 'pourcentage_base', 'pct': '7.5'}}], tva_pct='20')
        r = self.calc(o)
        self.assertEqual(r['lignes'][1]['montant_ht_cents'], 2500)                                    # 2 499,975 centimes → 2 500 (moitié supérieure)
        self.assertEqual(r['tva_cents'], 7167)                                                        # 35 833 × 20 % = 7 166,6 → 7 167

    def test_F05_reduction_et_variantes_exclusives(self):
        o = offre(champs={'enfants': {'type': 'booleen'}, 'juridiction': {'type': 'choix', 'valeurs': ['tj', 'tae']}})
        o['regles'] = [{'id': 'tae', 'libelle_client': 'Variante TAE', 'groupe': 'variante', 'si': [{'champ': 'juridiction', 'op': 'eq', 'valeur': 'tae'}],
                        'action': {'type': 'ajout_fixe', 'montant_ht_cents': 20000}},
                       {'id': 'tae-bis', 'libelle_client': 'Autre variante', 'groupe': 'variante', 'si': [{'champ': 'juridiction', 'op': 'eq', 'valeur': 'tae'}],
                        'action': {'type': 'ajout_fixe', 'montant_ht_cents': 50000}},
                       {'id': 'remise', 'libelle_client': 'Remise convenue', 'si': [{'champ': 'enfants', 'op': 'eq', 'valeur': False}],
                        'action': {'type': 'reduction_pourcentage_base', 'pct': '10'}}]
        r = self.calc(o, {'juridiction': {'valeur': 'tae', 'statut': 'confirme'}, 'enfants': {'valeur': False, 'statut': 'confirme'}})
        self.assertEqual(r['regles_appliquees'], ['tae', 'remise'])                                   # deux variantes du même groupe ne se cumulent pas
        self.assertEqual(r['total_ht_cents'], 100000 + 20000 - 10000)
        o['regles'] = [{'id': 'remise', 'libelle_client': 'Remise', 'si': [{'champ': 'enfants', 'op': 'eq', 'valeur': False}],
                        'action': {'type': 'reduction_fixe', 'montant_ht_cents': 200000}}]
        with self.assertRaisesRegex(ValueError, 'total_negatif'):
            self.calc(o, {'enfants': {'valeur': False, 'statut': 'confirme'}})

    def test_F05_temps_mixte_et_assistance_reguliere(self):
        champs = {'minutes_prevues': {'type': 'minutes', 'obligatoire': True}}
        d = {'minutes_prevues': {'valeur': 90, 'statut': 'confirme'}}
        self.assertEqual(self.calc(offre(mode='temps', taux_ht_cents=20000, regles=[], champs=champs), d)['total_ht_cents'], 30000)
        self.assertEqual(self.calc(offre(mode='mixte', taux_ht_cents=20000, regles=[], champs=champs), d)['total_ht_cents'], 130000)
        self.assertEqual(self.calc(offre(mode='temps', taux_ht_cents=20000, regles=[], champs=champs), {})['statut'], 'a_completer')
        r = self.calc(offre(mode='abonnement', periode='mois', minutes_incluses=360, regles=[], depassement='accord_prealable'))
        self.assertEqual((r['periode'], r['minutes_incluses'], r['depassement']), ('mois', 360, 'accord_prealable'))
        self.assertNotIn('debiter', r)                                                                # un dépassement ne déclenche aucun débit
        with self.assertRaisesRegex(ValueError, 'periode_requise'):
            self.calc(offre(mode='abonnement', regles=[]))

    def test_F05_tva_inconnue_reste_inconnue_et_rien_d_interne_ne_sort(self):
        o = offre(tva_statut='a_verifier', cout_interne_cents=40000, note_interne='NE PAS TRANSMETTRE', description_interne='marge visée')
        r = self.calc(o)
        self.assertEqual((r['tva_cents'], r['ttc_cents']), (None, None))
        vue = C.vue_client(r)
        self.assertNotIn('NE PAS TRANSMETTRE', json.dumps(vue, ensure_ascii=False))
        self.assertNotIn('cout_interne_cents', json.dumps(vue))
        self.assertNotIn('regle_id', json.dumps(vue))
        self.assertIn('TVA non confirmée', ' '.join(C.explication(r)))
        self.assertNotIn('NE PAS TRANSMETTRE', C.texte_client(o))
        self.assertNotIn('400', C.texte_client(o))

    def test_F05_explication_lisible(self):
        lignes = C.explication(self.calc())
        self.assertIn('Mission fictive : 1 000,00 € HT.', lignes)                              # espace fine insécable du format monétaire
        self.assertIn('Diligences supplémentaires définies : 100,00 € HT.', lignes)
        self.assertTrue(any('Hypothèse retenue' in l and 'oui' in l for l in lignes))
        self.assertIn('Montant non calculé', C.explication(self.calc(donnees={}))[0])
        self.assertIn('Aucun montant n’est proposé', C.explication(self.calc(offre(fixe_ht_cents=None)))[0])

    def test_F05_montants_hors_tarif(self):
        r = self.calc()
        permis = C.montants_du_calcul(r)
        texte = 'Nos honoraires s’élèvent à 1 100,00 € HT, soit 1 320 euros TTC, dont 100 € pour les enfants.'
        self.assertEqual(C.montants_hors_tarif(texte, permis), [])
        self.assertEqual(C.montants_hors_tarif('Comptez entre 900 € et 1 500 € selon la complexité.', permis), [90000, 150000])   # fourchette inventée
        self.assertEqual(C.montants_hors_tarif('Article 700 du code de procédure civile.', permis), [])   # un nombre sans euro n'est pas un montant


# ------------------------------------------------------------------------------------------------ catalogue du cabinet
CONTENU = {'libelle': 'Divorce par consentement mutuel', 'categorie': 'famille', 'type_mission': 'contentieux', 'mode': 'forfait', 'fixe_ht': '1 000',
           'tva_statut': True, 'tva_pct': '20', 'inclus': 'Projet de convention\nUn rendez-vous', 'exclus': 'Liquidation du régime matrimonial',
           'champ_1_id': 'enfants', 'champ_1_question': 'Des enfants sont-ils concernés ?', 'champ_1_type': 'booleen',
           'regle_1_libelle': 'Diligences concernant les enfants', 'regle_1_champ': 'enfants', 'regle_1_op': 'eq', 'regle_1_valeur': 'oui',
           'regle_1_action': 'ajout_fixe', 'regle_1_montant': '100', 'cout_interne': '400', 'note_interne': 'NE PAS TRANSMETTRE'}


class CatalogueBase(Base):
    def setUp(self):
        super().setUp()
        self.did = self.creer(titre='DURAND c/ MARTIN', client='DURAND', nature='contentieux', responsable=CLAIRE)['id']

    def cle(self):
        return uuid.uuid4().hex

    def api(self, route, data=None, owner='cabinet', role='administrateur', args=None):
        from agent import web440
        corps = json.dumps(data or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': owner, 'HTTP_X_AXIORHUB_ROLE': role, 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON, 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps), 'REQUEST_METHOD': 'GET' if data is None else 'POST'}
        out = web440.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm56109/honoraires/' + route, args or {}, env['REQUEST_METHOD'])
        self.assertIn(('Cache-Control', 'private, no-store'), out.get('headers') or [])
        return out['status'][:3], json.loads(out['body'])

    def publiee(self, **plus):
        ident = C.creer(self.desk, ADMIN, {**CONTENU, **plus})['id']
        C.publier(self.desk, ADMIN, ident, 1, '2026-01-01')
        return ident

    def droits(self, compte, role, **valeurs):
        membres5650.set_rights(self.f.c['state_dir'], 'cabinet', compte, role, valeurs)


class F05Bibliotheque(CatalogueBase):
    def test_F05_77_trames_sans_prix_inactives_et_jamais_copiees_en_base(self):
        b = C.bibliotheque()
        self.assertEqual((len(b['prestations']), len(b['categories'])), (77, 18))
        for p in b['prestations']:
            self.assertIsNone(p['fixe_ht_cents'])
            self.assertIsNone(p['taux_ht_cents'])
            self.assertFalse(p['actif_pour_ia'])
            self.assertNotEqual(p['statut'], 'publie')
            for r in p['regles']:
                self.assertIsNone(r['action'].get('montant_ht_cents'))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM catalogue_offres56109').fetchone()[0], 0)   # rien n'est actif à l'installation
        self.assertEqual(C.presentation_pour_ia(self.desk), '')
        self.assertEqual(len(fees.models(self.desk)), 50)                                             # la bibliothèque Honorium existante est intacte
        self.assertEqual(len(fees.library(self.desk)['diligenceLibrary']), 354)

    def test_F05_une_trame_devient_un_brouillon_sans_prix_non_publiable(self):
        code, recu = self.api('offres', {'trame': 'famille-divorce-par-consentement-mutuel-sans-juge', 'cle': self.cle()})
        self.assertEqual((code, recu['etat']), ('201', 'brouillon'))
        v = C.lister(self.desk, ADMIN)['prestations'][0]
        self.assertEqual((v['etat'], v['origine'], v['contenu']['fixe_ht_cents'], v['contenu']['actif_pour_ia']), ('brouillon', 'trame', None, False))
        self.assertEqual(len(v['contenu']['regles']), 2)
        code, corps = self.api('offres/publier', {'id': recu['id'], 'version': 1, 'cle': self.cle()})
        self.assertEqual((code, corps['error']), ('422', 'publication_refusee'))
        self.assertIn('fixe_ht', corps['champs'])                                                     # prix manquant
        self.assertIn('regle_1_montant', corps['champs'])                                             # branche non déclenchée contrôlée elle aussi
        self.assertIn('inclus', corps['champs'])
        with self.assertRaises(Stop) as ctx:
            C.simuler(self.desk, ADMIN, recu['id'], {'donnee_enfants': 'oui'})
        self.assertEqual(str(ctx.exception), 'offre_non_publiee')                                     # un brouillon ne sert à aucun chiffrage
        code, corps = self.api('offres', {'trame': 'inconnue', 'cle': self.cle()})
        self.assertEqual(code, '404')


class F05Catalogue(CatalogueBase):
    def test_F05_creer_modifier_publier_versions_immuables(self):
        cle = self.cle()
        code, recu = self.api('offres', {**CONTENU, 'cle': cle})
        self.assertEqual(code, '201', recu)
        ident = recu['id']
        self.assertEqual(self.api('offres', {**CONTENU, 'cle': cle})[1].get('rejoue'), True)        # rejeu : aucune seconde prestation
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM catalogue_offres56109').fetchone()[0], 1)
        code, autre = self.api('offres', {**CONTENU, 'libelle': 'Autre', 'cle': cle})
        self.assertEqual((code, autre['error']), ('409', 'cle_idempotence_deja_utilisee'))
        c = C.lister(self.desk, ADMIN)['prestations'][0]['contenu']
        self.assertEqual((c['fixe_ht_cents'], c['regles'][0]['action']['montant_ht_cents'], c['regles'][0]['si'][0]['valeur'], c['inclus']),
                         (100000, 10000, True, ['Projet de convention', 'Un rendez-vous']))
        code, pub = self.api('offres/publier', {'id': ident, 'version': 1, 'debut': '2026-01-01', 'cle': self.cle()}, owner=CLAIRE, role='avocat')
        self.assertEqual(code, '403')                                                                 # avocate sans le droit « réglages »
        code, pub = self.api('offres/publier', {'id': ident, 'version': 1, 'debut': '2026-01-01', 'cle': self.cle()})
        self.assertEqual((code, pub['version_publiee']), ('200', 1), pub)
        self.assertEqual(C.simuler(self.desk, ADMIN, ident, {'donnee_enfants': 'oui'}, au=JOUR)['resultat']['total_ht_cents'], 110000)
        # modifier une prestation publiée : la version publiée reste en vigueur jusqu'à la publication suivante
        code, mod = self.api('offres/modifier', {**CONTENU, 'fixe_ht': '1 500', 'id': ident, 'version': 2, 'cle': self.cle()})
        self.assertEqual(code, '200', mod)
        self.assertEqual(C.simuler(self.desk, ADMIN, ident, {'donnee_enfants': 'non'}, au=JOUR)['resultat']['total_ht_cents'], 100000)
        self.assertTrue(C.lister(self.desk, ADMIN)['prestations'][0]['a_publier'])
        code, perime = self.api('offres/publier', {'id': ident, 'version': 2, 'cle': self.cle()})
        self.assertEqual((code, perime['error']), ('409', 'version_perimee'))
        C.publier(self.desk, ADMIN, ident, 3, '2026-12-01')
        self.assertEqual(C.simuler(self.desk, ADMIN, ident, {'donnee_enfants': 'non'}, au=JOUR)['resultat']['total_ht_cents'], 100000)          # avant le 1er décembre : version 1
        self.assertEqual(C.simuler(self.desk, ADMIN, ident, {'donnee_enfants': 'non'}, au='2026-12-02')['resultat']['total_ht_cents'], 150000)  # ensuite : version 2
        self.assertEqual([(v['version'], v['debut']) for v in C.versions(self.desk, ADMIN, ident)], [(2, '2026-12-01'), (1, '2026-01-01')])
        for sql in ("UPDATE catalogue_versions56109 SET contenu_json='{}' WHERE offre_id=?", 'DELETE FROM catalogue_versions56109 WHERE offre_id=?'):
            with self.assertRaises(sqlite3.IntegrityError):                                           # une version publiée ne se réécrit ni ne s'efface
                self.desk.db.execute(sql, (ident,))
        self.desk.db.rollback()

    def test_F05_mutation_sans_le_controle_de_publication_un_prix_absent_serait_publie(self):
        ident = C.creer(self.desk, ADMIN, {**CONTENU, 'fixe_ht': ''})['id']
        with self.assertRaises(C.Refus):
            C.publier(self.desk, ADMIN, ident, 1)
        with patch.object(C, 'controler_publication', lambda c: {}):
            C.publier(self.desk, ADMIN, ident, 1)
        self.assertEqual(C.simuler(self.desk, ADMIN, ident, {'donnee_enfants': 'non'})['resultat']['statut'], 'a_chiffrer')   # même alors : jamais zéro

    def test_F05_validation_des_regles_a_l_enregistrement_et_a_la_publication(self):
        code, corps = self.api('offres', {**CONTENU, 'regle_1_champ': 'inconnu', 'cle': self.cle()})
        self.assertEqual((code, 'regle_1_champ' in corps['champs']), ('422', True))                   # critère inconnu
        code, corps = self.api('offres', {**CONTENU, 'regle_2_libelle': 'Diligences concernant les enfants', 'regle_2_champ': 'enfants', 'regle_2_valeur': 'oui', 'cle': self.cle()})
        self.assertEqual((code, 'regle_2_libelle' in corps['champs']), ('422', True))                 # règle en double
        code, corps = self.api('offres', {**CONTENU, 'regle_1_op': 'ge', 'cle': self.cle()})
        self.assertEqual((code, 'regle_1_op' in corps['champs']), ('422', True))                      # « au moins » sur un oui / non
        code, corps = self.api('offres', {**CONTENU, 'fixe_ht': '12,345', 'cle': self.cle()})
        self.assertEqual((code, 'fixe_ht' in corps['champs']), ('422', True))
        code, corps = self.api('offres', {**CONTENU, 'regle_1_action': 'eval', 'cle': self.cle()})
        self.assertEqual((code, 'regle_1_action' in corps['champs']), ('422', True))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM catalogue_offres56109').fetchone()[0], 0)
        ident = C.creer(self.desk, ADMIN, {**CONTENU, 'mode': 'temps', 'fixe_ht': '', 'taux_ht': '200', 'regle_1_action': 'pourcentage_base', 'regle_1_montant': '10'})['id']
        with self.assertRaises(C.Refus) as ctx:
            C.publier(self.desk, ADMIN, ident, 1)
        self.assertIn('champs', ctx.exception.detail)                                                 # « minutes_prevues » manquant
        self.assertIn('regle_1_action', ctx.exception.detail)                                         # pourcentage sans forfait de base
        ident = C.creer(self.desk, ADMIN, {**CONTENU, 'libelle': 'Gratuit', 'fixe_ht': '0', 'gratuite': True, 'regle_1_libelle': '', 'regle_1_champ': ''})['id']
        with self.assertRaises(C.Refus) as ctx:
            C.publier(self.desk, ADMIN, ident, 1)
        self.assertIn('motif_gratuite', ctx.exception.detail)
        ident = C.creer(self.desk, ADMIN, {**CONTENU, 'libelle': 'TVA', 'tva_pct': ''})['id']
        with self.assertRaises(C.Refus) as ctx:
            C.publier(self.desk, ADMIN, ident, 1)
        self.assertIn('tva_pct', ctx.exception.detail)

    def test_F05_dupliquer_archiver_et_categories_du_cabinet(self):
        ident = self.publiee()
        copie = C.creer(self.desk, ADMIN, {'copie_de': ident})
        v = next(x for x in C.lister(self.desk, ADMIN)['prestations'] if x['id'] == copie['id'])
        self.assertEqual((v['etat'], v['contenu']['libelle'], v['contenu']['fixe_ht_cents']), ('brouillon', 'Divorce par consentement mutuel (copie)', 100000))
        C.archiver(self.desk, ADMIN, ident, 2)
        with self.assertRaises(Stop) as ctx:
            C.simuler(self.desk, ADMIN, ident, {'donnee_enfants': 'oui'})
        self.assertEqual(str(ctx.exception), 'offre_archivee')
        self.assertEqual(len(C.versions(self.desk, ADMIN, ident)), 1)                                 # la version publiée reste consultable
        self.assertNotIn(ident, [x['id'] for x in C.lister(self.desk, ADMIN)['prestations']])
        self.assertIn(ident, [x['id'] for x in C.lister(self.desk, ADMIN, {'etat': 'archive'})['prestations']])
        C.creer(self.desk, ADMIN, {**CONTENU, 'libelle': 'Médiation', 'categorie_nouvelle': 'Modes amiables'})
        self.assertEqual(C.categories(self.desk)['modes_amiables'], 'Modes amiables')                 # domaine ajouté sans modifier de code
        self.assertEqual([x['contenu']['libelle'] for x in C.lister(self.desk, ADMIN, {'q': 'médiation'})['prestations']], ['Médiation'])
        self.assertEqual(len(C.lister(self.desk, ADMIN, {'categorie': 'famille'})['prestations']), 1)

    def test_F05_import_simulation_puis_brouillons_par_identifiant(self):
        ident = self.publiee()
        brut = json.dumps({'prestations': [
            {'id': ident, 'libelle': 'Divorce par consentement mutuel', 'categorie': 'famille', 'mode': 'forfait', 'fixe_ht_cents': 200000, 'inclus': ['x'], 'champs': {}, 'regles': []},
            {'id': 'autre-identifiant', 'libelle': 'Divorce par consentement mutuel', 'categorie': 'famille', 'mode': 'forfait', 'fixe_ht_cents': None, 'actif_pour_ia': True,
             'champs': {}, 'regles': []},
            {'id': 'Mauvais Id', 'libelle': 'x'}, {'id': 'flottant', 'libelle': 'y', 'fixe_ht_cents': 12.5}]})
        code, rapport = self.api('importer', {'contenu': brut, 'cle': self.cle()})
        self.assertEqual(code, '200', rapport)
        self.assertEqual(([x['id'] for x in rapport['differentes']], [x['id'] for x in rapport['nouvelles']], len(rapport['refusees']), rapport['applique']),
                         ([ident], ['autre-identifiant'], 2, False))                                  # même libellé, autre identifiant : une nouvelle prestation
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM catalogue_offres56109').fetchone()[0], 1)   # la simulation n'écrit rien
        code, rapport = self.api('importer', {'contenu': brut, 'appliquer': True, 'cle': self.cle()})
        self.assertEqual(rapport['applique'], True)
        par_id = {x['id']: x for x in C.lister(self.desk, ADMIN)['prestations']}
        self.assertEqual((par_id['autre-identifiant']['etat'], par_id['autre-identifiant']['contenu']['actif_pour_ia']), ('brouillon', False))
        self.assertEqual(par_id[ident]['contenu']['fixe_ht_cents'], 200000)                           # brouillon remplacé…
        self.assertEqual(C.simuler(self.desk, ADMIN, ident, {'donnee_enfants': 'non'}, au=JOUR)['resultat']['total_ht_cents'], 100000)   # … la version publiée, intacte
        self.assertEqual(self.api('importer', {'contenu': 'pas du json', 'cle': self.cle()})[0], '422')
        export = C.exporter(self.desk, ADMIN)
        self.assertEqual(C.importer(self.desk, ADMIN, json.dumps(export))['identiques'].__len__(), 2)   # un export se réimporte à l'identique

    def test_F05_droits_voir_preparer_gerer_publier(self):
        ident = self.publiee()
        # voir : les trois rôles par défaut ; coût et notes internes réservés à qui gère ou facture
        code, vue = self.api('catalogue', owner=CAMILLE, role='assistant')
        self.assertEqual(code, '200')
        self.assertNotIn('NE PAS TRANSMETTRE', json.dumps(vue, ensure_ascii=False))
        self.assertNotIn('cout_interne_cents', vue['prestations'][0]['contenu'])
        self.assertIn('cout_interne_cents', self.api('catalogue')[1]['prestations'][0]['contenu'])
        self.droits(CAMILLE, 'assistant', voir_honoraires=False)
        self.assertEqual(self.api('catalogue', owner=CAMILLE, role='assistant')[0], '403')
        self.assertEqual(self.api('simuler', {'offre': ident, 'donnee_enfants': 'oui'}, owner=CAMILLE, role='assistant')[0], '403')
        # gérer : droit « réglages » ; une assistante autorisée prépare un brouillon mais ne publie jamais
        self.assertEqual(self.api('offres', {**CONTENU, 'libelle': 'Autre', 'cle': self.cle()}, owner=CAMILLE, role='assistant')[0], '403')
        self.droits(CAMILLE, 'assistant', voir_honoraires=True, reglages=True)
        code, recu = self.api('offres', {**CONTENU, 'libelle': 'Préparée par l’assistante', 'cle': self.cle()}, owner=CAMILLE, role='assistant')
        self.assertEqual(code, '201', recu)
        code, corps = self.api('offres/publier', {'id': recu['id'], 'version': 1, 'cle': self.cle()}, owner=CAMILLE, role='assistant')
        self.assertEqual((code, corps['error']), ('403', 'role_insuffisant'))
        with self.assertRaises(Stop):
            C.publier(self.desk, (CAMILLE, 'assistant'), recu['id'], 1)                               # même en appelant le service directement
        from agent import standalone_auth
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/m56109/honoraires/offres/publier'))
        self.assertTrue(standalone_auth.allowed('avocat', 'POST', '/api440/m56109/honoraires/offres/publier'))
        self.droits(CLAIRE, 'avocat', reglages=True)
        self.assertEqual(self.api('offres/publier', {'id': recu['id'], 'version': 1, 'cle': self.cle()}, owner=CLAIRE, role='avocat')[0], '200')
        # préparer une proposition : droit « facturation » (une assistante ne l'a pas par défaut)
        code, corps = self.api('proposer', {'offre': ident, 'dossier': self.did, 'donnee_enfants': 'oui', 'cle': self.cle()}, owner=CAMILLE, role='assistant')
        self.assertEqual(code, '403')
        self.assertEqual(self.api('catalogue', owner='inconnu', role='visiteur')[0], '403')

    def test_F05_mutation_sans_la_garde_des_droits_une_assistante_creerait(self):
        with patch.object(C, '_droit', lambda desk, a, droit: None):
            self.assertEqual(self.api('offres', {**CONTENU, 'cle': self.cle()}, owner=CAMILLE, role='assistant')[0], '201')
        self.assertEqual(self.api('offres', {**CONTENU, 'libelle': 'Autre', 'cle': self.cle()}, owner=CAMILLE, role='assistant')[0], '403')

    def test_F05_panne_du_controle_des_droits_503_jamais_un_acces_elargi(self):
        with patch.object(membres5650, 'effective_rights', side_effect=Stop('droits_illisibles')):
            code, corps = self.api('catalogue', owner=CLAIRE, role='avocat')
        self.assertEqual((code, corps['error']), ('503', 'controle_acces_indisponible'))

    def test_F05_lecture_sans_ecriture_et_csrf(self):
        ident = self.publiee()
        C.lister(self.desk, ADMIN)
        avant = self.desk.db.total_changes
        for route, args in (('catalogue', {'q': 'divorce'}), ('versions', {'id': ident}), ('bibliotheque', {}), ('tarif', {'dossier': self.did, 'offre': ident})):
            self.assertEqual(self.api(route, args=args)[0], '200', route)
        self.assertEqual(self.api('simuler', {'offre': ident, 'donnee_enfants': 'oui'})[0], '200')    # un calcul n'écrit rien
        C.section_html(self.desk, '/p', 'cabinet', 'administrateur', {})
        self.assertEqual(self.desk.db.total_changes, avant)
        from agent import web440
        corps = json.dumps({**CONTENU, 'cle': self.cle()}).encode()
        env = {'HTTP_X_AXIORHUB_USER': 'cabinet', 'HTTP_X_AXIORHUB_ROLE': 'administrateur', 'HTTP_ORIGIN': 'https://ailleurs.example.test', 'HTTP_X_CSRF_TOKEN': JETON,
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps), 'REQUEST_METHOD': 'POST'}
        out = web440.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm56109/honoraires/offres', {}, 'POST')
        self.assertEqual(out['status'][:3], '403')
        self.assertEqual(self.api('offres', {**CONTENU})[0], '422')                                    # clé d'idempotence absente
        self.assertEqual(self.api('inconnue')[0], '404')


# ------------------------------------------------------------------------------------------------ tarif applicable, proposition, IA
class F05Dossier(CatalogueBase):
    def document(self, kind, state, ht=250000):
        from agent import facturation5653
        facturation5653.ensure_schema(self.desk)
        self.desk.db.execute("INSERT INTO b53_documents(id,kind,matter,state,number,snapshot_json,created_by,created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                             ('doc-' + kind, kind, self.did, state, kind.upper()[:4] + '-2026-001', json.dumps({'totaux': {'ht_cents': ht}}), 'cabinet', '2026-11-01', '2026-11-01'))
        self.desk.db.commit()

    def test_F05_tarif_applicable_par_ordre_de_priorite(self):
        t = C.tarif_applicable(self.desk, ADMIN, self.did)
        # aucun engagement, aucune prestation : à chiffrer ; la valeur technique par défaut (220 €) n'est pas communiquée comme tarif du cabinet
        self.assertEqual((t['origine'], t['taux_horaire_confirme']), ('a_chiffrer', None))
        self.assertFalse(fees.tarifs_confirmes(self.desk))
        code, _ = self.api('tarifs/confirmer', {'cle': self.cle()})
        self.assertEqual(code, '200')
        self.assertEqual(C.tarif_applicable(self.desk, ADMIN, self.did)['taux_horaire_confirme'], '220')
        ident = self.publiee()
        t = C.tarif_applicable(self.desk, ADMIN, self.did, ident, au=JOUR)
        self.assertEqual((t['origine'], t['offre']['version']), ('catalogue_cabinet', 1))
        # profil propre à l'avocate responsable du dossier : sa variante publiée prime sur le catalogue du cabinet
        variante = C.creer(self.desk, ADMIN, {**CONTENU, 'libelle': 'Divorce (tarif de Claire)', 'fixe_ht': '1 200', 'profil_avocat': CLAIRE, 'variante_de': ident})['id']
        C.publier(self.desk, ADMIN, variante, 1, '2026-01-01')
        t = C.tarif_applicable(self.desk, ADMIN, self.did, ident, au=JOUR)
        self.assertEqual((t['origine'], t['offre']['fixe_ht_cents']), ('profil_avocat', 120000))
        self.assertEqual(C.tarif_applicable(self.desk, ADMIN, self.did, ident, au=JOUR, avocat='')['origine'], 'catalogue_cabinet')
        # montant retenu par l'avocat, devis accepté, convention signée : chacun prime sur le catalogue
        est = fees.save_estimate(self.desk, CLAIRE, self.did, {'mode': 'forfait', 'forfait': '900', 'diligences': []})
        fees.retain(self.desk, CLAIRE, est['id'], '950', est['version'])
        t = C.tarif_applicable(self.desk, ADMIN, self.did, ident)
        self.assertEqual((t['origine'], t['montant_ht_cents']), ('derogation', 95000))
        self.document('devis', 'accepte', 180000)
        self.assertEqual(C.tarif_applicable(self.desk, ADMIN, self.did, ident)['origine'], 'proposition_acceptee')
        self.document('convention', 'signe', 250000)
        t = C.tarif_applicable(self.desk, ADMIN, self.did, ident)
        self.assertEqual((t['origine'], t['montant_ht_cents'], t['reference']), ('convention_signee', 250000, 'CONV-2026-001'))
        # la convention signée interrompt tout nouveau chiffrage : son prix n'est jamais remplacé
        s = C.simuler(self.desk, ADMIN, ident, {'donnee_enfants': 'oui'}, dossier=self.did, au=JOUR)
        self.assertEqual((s['resultat']['statut'], s['ferme']), ('accord_existant', False))
        self.assertNotIn('total_ht_cents', s['resultat'])
        e = C.estimation_motivee(self.desk, ADMIN, self.did, ident, {'donnee_enfants': 'oui'})
        self.assertEqual((e['origine'], e['ferme']), ('convention_signee', False))
        self.assertNotIn('offre', e['tarif'])

    def test_F05_une_convention_seulement_envoyee_ne_prime_pas(self):
        ident = self.publiee()
        self.document('convention', 'envoye')
        self.assertEqual(C.tarif_applicable(self.desk, ADMIN, self.did, ident, au=JOUR)['origine'], 'catalogue_cabinet')
        self.assertEqual(C.estimation_motivee(self.desk, ADMIN, self.did, ident, {'donnee_enfants': 'non'}, au=JOUR)['resultat']['total_ht_cents'], 100000)

    def test_F05_proposer_fige_le_calcul_et_alimente_le_moteur_existant(self):
        ident = self.publiee()
        cle = self.cle()
        corps_req = {'offre': ident, 'dossier': self.did, 'donnee_enfants': 'oui', 'au': JOUR, 'cle': cle}
        code, recu = self.api('proposer', corps_req, owner=CLAIRE, role='avocat')
        self.assertEqual((code, recu['statut']), ('201', 'proposition'), recu)
        est = fees.get_estimate(self.desk, recu['estimation'])
        self.assertEqual((est['proposed_cents'], est['state'], est['matter']), (110000, 'suggeree', self.did))
        self.assertEqual(est['result']['origine_prix']['type'], 'catalogue_cabinet')
        self.assertEqual((est['result']['origine_prix']['offre_id'], est['result']['origine_prix']['offre_version']), (ident, '1'))
        explication = ' '.join(est['result']['explication'])
        self.assertIn('catalogue du cabinet', explication)
        self.assertNotIn('choisi par l’avocat', explication)                                          # pas avant sa décision
        self.assertNotIn('Fourchette', explication)                                                   # aucune fourchette inventée
        self.assertIsNone(est['retained_cents'])
        sim = self.desk.db.execute('SELECT * FROM catalogue_simulations56109 WHERE id=?', (recu['simulation'],)).fetchone()
        self.assertEqual((sim['offre_version'], sim['statut'], sim['auteur'], json.loads(sim['parametres_json'])['enfants']['valeur']), (1, 'proposition', CLAIRE, True))
        lien = self.desk.db.execute('SELECT * FROM catalogue_estimations56109 WHERE estimation_id=?', (est['id'],)).fetchone()
        self.assertEqual((lien['simulation_id'], lien['empreinte']), (sim['id'], sim['empreinte']))
        code2, recu2 = self.api('proposer', corps_req, owner=CLAIRE, role='avocat')                   # rejeu : aucune seconde estimation
        self.assertEqual((recu2['estimation'], recu2.get('rejoue')), (recu['estimation'], True))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM b53_estimates').fetchone()[0], 1)
        # le même instantané : la version du catalogue change, la proposition déjà établie garde la sienne
        C.modifier(self.desk, ADMIN, ident, 2, {**CONTENU, 'fixe_ht': '2 000'})
        C.publier(self.desk, ADMIN, ident, 3, '2026-01-02')
        self.assertEqual(fees.get_estimate(self.desk, recu['estimation'])['proposed_cents'], 110000)
        self.assertEqual(json.loads(self.desk.db.execute('SELECT resultat_json FROM catalogue_simulations56109 WHERE id=?', (sim['id'],)).fetchone()[0])['total_ht_cents'], 110000)

    def test_F05_calcul_incomplet_aucun_montant_inscrit(self):
        ident = self.publiee()
        code, recu = self.api('proposer', {'offre': ident, 'dossier': self.did, 'cle': self.cle()})   # « enfants » inconnu
        self.assertEqual((code, recu['statut'], recu['estimation']), ('201', 'a_completer', None))
        self.assertIn('Des enfants sont-ils concernés ?', ' '.join(recu['explication']))
        fees.ensure_schema(self.desk)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM b53_estimates').fetchone()[0], 0)  # jamais zéro pour représenter un inconnu
        self.assertEqual(self.desk.db.execute("SELECT statut FROM catalogue_simulations56109").fetchone()[0], 'a_completer')
        code, corps = self.api('simuler', {'offre': ident, 'donnee_enfants': 'peut-être'})
        self.assertEqual((code, 'donnee_enfants' in corps['champs']), ('422', True))

    def test_F05_dossier_reserve_meme_404_que_dossier_absent(self):
        ident = self.publiee()
        self.droits(CAMILLE, 'avocat', facturation=True)
        self.reserver(self.did, [CLAIRE])
        for route, corps in (('simuler', {'offre': ident, 'dossier': self.did, 'donnee_enfants': 'oui'}),
                             ('proposer', {'offre': ident, 'dossier': self.did, 'donnee_enfants': 'oui', 'cle': self.cle()})):
            reserve = self.api(route, corps, owner=CAMILLE, role='avocat')
            absent = self.api(route, {**corps, 'dossier': 'inconnu'}, owner=CAMILLE, role='avocat')
            self.assertEqual((reserve[0], reserve[1]['error']), ('404', 'introuvable'), route)
            self.assertEqual(reserve[1], absent[1])
        self.assertEqual(self.api('tarif', owner=CAMILLE, role='avocat', args={'dossier': self.did})[0], '404')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM catalogue_simulations56109').fetchone()[0], 0)
        self.assertEqual(self.api('simuler', {'offre': ident, 'dossier': self.did, 'donnee_enfants': 'oui'}, owner=CLAIRE, role='avocat')[0], '200')

    def test_F05_l_ia_ne_recoit_que_la_vue_client_des_prestations_publiees_pour_elle(self):
        from agent import reponses5671
        self.desk.setting(reponses5671.PRESENTATION, 'Cabinet fictif.')
        ident = self.publiee()                                                                        # publiée, mais pas « utilisable par l'IA »
        self.assertEqual(reponses5671.first_contact(self.desk)['cabinet'], 'Cabinet fictif.')        # rien d'activé par défaut
        C.modifier(self.desk, ADMIN, ident, 2, {**CONTENU, 'actif_pour_ia': True})
        self.assertEqual(reponses5671.first_contact(self.desk)['cabinet'], 'Cabinet fictif.')        # brouillon non publié : toujours rien
        C.publier(self.desk, ADMIN, ident, 3, '2026-01-02')
        texte = reponses5671.first_contact(self.desk)['cabinet']
        self.assertIn('Divorce par consentement mutuel : forfait de 1 000,00 € HT (TVA 20 % en sus)', texte)
        self.assertIn('condition « Diligences concernant les enfants »', texte)
        self.assertIn('+ 100,00 € HT', texte)
        self.assertIn('ne comprend pas : Liquidation du régime matrimonial', texte)
        self.assertIn('n’avancer aucun autre montant', texte)
        self.assertNotIn('NE PAS TRANSMETTRE', texte)
        self.assertNotIn('400', texte)                                                                # coût interne
        permis = C.prestations_pour_ia(self.desk)[0]['montants']
        self.assertEqual(permis, [10000, 100000])
        self.assertEqual(C.montants_hors_tarif('Forfait de 1 000 € HT, plus 100 € si des enfants sont concernés.', permis), [])
        self.assertEqual(C.montants_hors_tarif('Environ 2 500 € pour ce type de dossier.', permis), [250000])

    def test_F05_moteur_existant_inchange_sans_origine_catalogue(self):
        r = fees.estimate(self.desk, {'mode': 'forfait', 'forfait': '900', 'diligences': [{'libelle_client': 'Rendez-vous', 'minutes': 60}]})
        self.assertIn('Forfait choisi par l’avocat', r['explication'][0])
        self.assertNotIn('origine_prix', r)
        self.assertTrue(any('Fourchette' in x for x in r['explication']))
        vide = fees.estimate(self.desk, {'mode': 'forfait', 'forfait': '900', 'diligences': []})
        self.assertIn('Aucune diligence : le montant proposé ne peut pas être expliqué.', vide['avertissements'])
        self.assertFalse(fees.tarifs_confirmes(self.desk))
        fees.save_settings(self.desk, {'taux_horaire': '250'})                                        # un taux saisi par le cabinet vaut décision
        self.assertTrue(fees.tarifs_confirmes(self.desk))

    def test_F05_parametres_cabinet_prestations_et_honoraires(self):
        ident = self.publiee()
        html = C.section_html(self.desk, '/p', 'cabinet', 'administrateur', {})
        for attendu in ('id="catalogue56109"', 'Prestations et honoraires', 'Divorce par consentement mutuel', 'forfait 1 000,00 € HT', 'Ajouter une prestation',
                        'Modifier', 'Dupliquer', 'Archiver', 'Importer un catalogue (JSON)', 'Simuler', '77 trames, sans prix', 'name="h109_q"',
                        'Confirmer ces valeurs comme tarifs du cabinet', 'aucun barème de la profession', 'data-d97-api="m56109/honoraires/offres/modifier"'):
            self.assertIn(attendu, html)
        self.assertNotIn('<script', html)
        self.assertNotIn('Publier cette version', html)                                               # déjà publiée, sans modification en attente
        lecture = C.section_html(self.desk, '/p', CAMILLE, 'assistant', {})
        self.assertIn('Divorce par consentement mutuel', lecture)
        self.assertNotIn('Ajouter une prestation', lecture)                                           # lire n'est pas gérer
        self.assertNotIn('NE PAS TRANSMETTRE', lecture)
        self.assertIn('Aucune prestation pour ces filtres', C.section_html(self.desk, '/p', 'cabinet', 'administrateur', {'h109_q': 'zzz'}))
        from agent import web5653
        self.assertIn('/p/parametres?rubrique=cabinet#catalogue56109', web5653.settings_html(self.desk, '/p', 'cabinet', 'administrateur'))

    def test_F05_montee_dans_parametres_cabinet(self):
        from agent import parametres5621
        self.publiee()
        env = {'HTTP_X_AXIORHUB_USER': 'cabinet', 'HTTP_X_AXIORHUB_ROLE': 'administrateur', 'QUERY_STRING': 'rubrique=cabinet&h109_q=divorce'}
        html = parametres5621.page(self.desk, {'csrf': 'c', 'origin': 'o'}, '/p', env, {'rubrique': 'cabinet'})
        html = html['body'] if isinstance(html, dict) else html
        self.assertIn('id="catalogue56109"', html)
        self.assertIn('Prestations et honoraires', html)
        self.assertIn('Divorce par consentement mutuel', html)
        self.assertNotIn('<section class="ax-card" id="catalogue56109"><h2>Prestations et honoraires</h2><p class="ax-alert" role="status">Section indisponible', html)
        env['QUERY_STRING'] = 'rubrique=cabinet&h109_q=zzz'
        html = parametres5621.page(self.desk, {'csrf': 'c', 'origin': 'o'}, '/p', env, {'rubrique': 'cabinet'})
        html = html['body'] if isinstance(html, dict) else html
        self.assertIn('Aucune prestation pour ces filtres', html)

    def test_F05_schema_rejouable_et_contrat_d_export(self):
        from agent import export56108, web440
        C.ensure_schema(self.desk)
        C.ensure_schema(self.desk)
        tables = {r[0] for r in self.desk.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'catalogue_%56109'")}
        self.assertEqual(len(tables), 5)
        declarees = {ligne[2] for ligne in export56108.EXPORT} | {t for t, _ in export56108.EXPORT_EXCLUES}
        self.assertTrue(tables <= declarees, tables - declarees)
        self.assertIn('/static/v56109-honoraires.css', web440.STATIC)


if __name__ == '__main__':
    unittest.main()
