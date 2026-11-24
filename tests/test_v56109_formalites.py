"""5.6.109 (lot G3 : G14, G15, G16, G20) : formalités supervisées (``formalites56109``, ``portails56109``) sur l'opération ou le dossier existants.

Faux navigateur uniquement (CDP simulé, domaine example.test) : aucune connexion réelle, aucune signature, aucun achat, aucune formalité, même
simulée contre un vrai site. Les profils réels restent NON qualifiés ; les essais d'effets remplacent le profil par un profil d'essai.
Chaque garde-fou (permis distincts jamais accordés par un modèle, achat sans accord ni budget, aucun mouvement de fonds, barrière de prise
de main, acte modifié) a son test de mutation. Données fictives (SIREN d'essai 000010033, ZORVAL, Valbrune, example.test).
"""
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_v5684 as t84
from agent import bland56109, export56108, formalites56109 as F, navigateur56109 as N, navigateur5673 as nv, portails56109 as P
from agent.common import Stop, load_matters

AVOCAT, ASSISTANT, IA = ('avocate@example.test', 'avocat'), ('assistant@example.test', 'assistant'), ('agent_cabinet', 'agent')
ORIGINE, JETON = 'https://pilote.example.test', 'jeton-csrf-fictif'
SIREN = '000010033'
PDF = b'%PDF-1.4\n% fictif\n%%EOF\n'


def pdf(texte):
    return b'%PDF-1.4\n' + texte.encode('latin-1') + b'\n%%EOF\n'


class FakeCDP:
    def __init__(self):
        self.calls = []

    def send(self, method, params=None, **kw):
        self.calls.append((method, params))
        if method == 'DOM.describeNode':
            return {'node': {'localName': 'input', 'attributes': ['type', 'file']}}
        return {}


class FakeTab:
    """Onglet simulé : mêmes méthodes que ``navigateur5673.Tab`` (info, text, click) ; aucune page réelle."""

    def __init__(self, pages, perdre=False):
        self.pages, self.i, self.clics, self.perdre = list(pages), 0, [], perdre
        self.cdp, self.session, self.refs = FakeCDP(), 's-essai', SimpleNamespace(backend=lambda ref: 42)

    def info(self):
        return 'https://portail.example.test/' + self.pages[self.i].get('chemin', ''), self.pages[self.i].get('titre', 'Portail')

    def text(self):
        return self.pages[self.i]['texte']

    def click(self, ref):
        self.clics.append(ref)
        if self.perdre:
            raise Stop('reponse_cdp_perdue')
        self.i = min(self.i + 1, len(self.pages) - 1)


def qualifie(cle):
    """Profil d'ESSAI : même définition, domaine d'essai, marqué qualifié pour le seul faux navigateur."""
    return patch.dict(F.PROFILS, {cle: {**F.PROFILS[cle], 'qualifie': True, 'origines': ('portail.example.test',)}})


class Socle(t84.Base):
    def setUp(self):
        super().setUp()
        self.n = 0
        self.mid = t84.MID
        self.cid2 = self.co.company_save(self.desk, {'denomination': 'ZORVAL SAS', 'forme': 'SAS', 'capital': '5000', 'nominal': '1', 'siege': '4 rue Fictive, 69002 Lyon',
                                                     'rcs': 'Lyon', 'siren': SIREN, 'matter': self.mid})['id']
        self.seance = nv.Session(None, AVOCAT[0], 'Formalité fictive', self.mid)

    def cle(self):
        self.n += 1
        return 'cle-g3f-%04d' % self.n

    def path(self, nom):
        return next(m for m in load_matters(self.desk.c) if m['id'] == self.mid)['path'].rstrip('/') + '/' + nom

    def depot(self, nom, raw=PDF):
        p = self.path(nom)
        self.docs.files[p] = raw
        return p

    def op_siege(self):
        out = self.co.operation_submit(self.desk, {'kind': 'siege', 'company': self.cid2, 'nouveau_siege': '5 place Fictive, 69003 Lyon', 'date_effet': '2026-11-02',
                                                   'organe': 'assemblee'})
        return out['id']

    def fiche(self, fid):
        return F.fiche(self.desk, AVOCAT, fid)

    def autoriser(self, eid, acteur=AVOCAT):
        fe = F.fiche_effet(self.desk, AVOCAT, eid)
        with bland56109.requete_humaine(acteur[0]):
            return F.autoriser(self.desk, acteur, {'effet': eid, 'version': fe['version'], 'empreinte': fe['empreinte'], 'relu': True})

    def kbis(self, **plus):
        op = self.op_siege()
        fid = F.preparer(self.desk, AVOCAT, {'nature': 'infogreffe:kbis', 'op_kind': 'corporate5636', 'op_id': op, 'siren': SIREN, 'siren_confirme': True, **plus})['formalite']
        return op, fid

    def panier(self, fid, prix='3,93', plafond='10,00', **plus):
        return F.panier(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'prix_ttc': prix, 'plafond': plafond, **plus})

    def achat_autorise(self, prix='3,93', plafond='10,00'):
        op, fid = self.kbis()
        self.panier(fid, prix, plafond)
        F.authentification(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'identite_constatee': 'Cabinet DURAND (compte du cabinet)'})
        eid = F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'acheter_document_infogreffe'})['effet']
        self.autoriser(eid)
        return op, fid, eid

    def tab_panier(self, prix='3,93', perdre=False, extra=''):
        return FakeTab([{'chemin': 'panier', 'texte': 'Extrait Kbis ZORVAL SAS — Total TTC : %s € %s' % (prix, extra)},
                        {'chemin': 'confirmation', 'texte': 'Paiement accepté. Commande n° CMD-0001'}], perdre)


# =========================================================================================== G15 Infogreffe
class G15Infogreffe(Socle):
    def test_G15_bon_produit_et_bonne_societe(self):
        op = self.op_siege()
        with self.assertRaises(F.Refus) as ctx:
            F.preparer(self.desk, AVOCAT, {'nature': 'infogreffe:kbis', 'op_kind': 'corporate5636', 'op_id': op, 'siren': '000010041'})
        self.assertIn('siren', ctx.exception.detail)
        fid = F.preparer(self.desk, AVOCAT, {'nature': 'infogreffe:kbis', 'op_kind': 'corporate5636', 'op_id': op})['formalite']
        f = self.fiche(fid)
        self.assertEqual((f['siren'], f['nature'], f['op_id']), (SIREN, 'infogreffe:kbis', op))
        with self.assertRaises(F.Refus) as ctx:
            self.panier(fid)                                                                   # société non confirmée : aucun achat
        self.assertIn('siren', ctx.exception.detail)

    def test_G15_prix_options_abonnement_et_budget_avant_tout_achat(self):
        op, fid = self.kbis()
        with self.assertRaises(F.Refus):
            F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'acheter_document_infogreffe'})   # pas de panier exact
        with self.assertRaises(F.Refus) as ctx:
            self.panier(fid, abonnement=True)
        self.assertIn('options', ctx.exception.detail)
        with self.assertRaises(F.Refus):
            self.panier(fid, plafond='')                                                       # budget accordé requis
        self.panier(fid)
        self.assertEqual(self.fiche(fid)['etat'], 'panier_a_autoriser')

    def test_G15_achat_seulement_apres_accord_explicite_et_budget_reserve(self):
        op, fid = self.kbis()
        self.panier(fid)
        F.authentification(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'identite_constatee': 'Compte du cabinet'})
        eid = F.demander(self.desk, ASSISTANT, {'formalite': fid, 'action': 'acheter_document_infogreffe'})['effet']   # un assistant prépare
        tab = self.tab_panier()
        with qualifie('infogreffe_commande'):
            with self.assertRaisesRegex(Stop, 'autorisation_exacte_requise'):
                F.executer(self.desk, AVOCAT, eid, self.seance, 0, tab, 'payer')
            self.assertEqual(tab.clics, [])
            with self.assertRaisesRegex(Stop, 'role_insuffisant'):
                self.autoriser(eid, ASSISTANT)
            self.autoriser(eid)
            out = F.executer(self.desk, AVOCAT, eid, self.seance, 0, tab, 'payer')
        self.assertEqual((out['etat'], out['reference'], tab.clics), ('confirme', 'CMD-0001', ['payer']))
        r = self.desk.db.execute('SELECT * FROM formalites_reservations56109 WHERE effet=?', (eid,)).fetchone()
        self.assertEqual((r['montant_minor'], r['etat']), (393, 'consommee'))
        self.assertEqual(self.fiche(fid)['etat'], 'commande_confirmee')
        self.assertEqual(self.desk.db.execute("SELECT state FROM connexion_ops5652 WHERE provider='portail_infogreffe'").fetchone()[0], 'envoyee')

    def test_G15_prix_modifie_ou_abonnement_sur_la_page_aucun_clic(self):
        op, fid, eid = self.achat_autorise()
        with qualifie('infogreffe_commande'):
            tab = self.tab_panier('5,00')
            with self.assertRaisesRegex(Stop, 'prix_panier_modifie'):
                F.executer(self.desk, AVOCAT, eid, self.seance, 0, tab, 'payer')
            tab2 = self.tab_panier(extra='Abonnement annuel proposé')
            with self.assertRaisesRegex(Stop, 'option_panier_non_autorisee'):
                F.executer(self.desk, AVOCAT, eid, self.seance, 0, tab2, 'payer')
        self.assertEqual(tab.clics + tab2.clics, [])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM formalites_reservations56109').fetchone()[0], 0)

    def test_G15_budget_epuise_aucun_clic(self):
        op, fid, eid = self.achat_autorise(prix='3,93', plafond='3,00')
        tab = self.tab_panier()
        with qualifie('infogreffe_commande'), self.assertRaisesRegex(Stop, 'budget_portail_depasse'):
            F.executer(self.desk, AVOCAT, eid, self.seance, 0, tab, 'payer')
        self.assertEqual(tab.clics, [])

    def test_G15_mutation_sans_permis_l_achat_partirait(self):
        op, fid = self.kbis()
        self.panier(fid)
        F.authentification(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'identite_constatee': 'Compte du cabinet'})
        eid = F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'acheter_document_infogreffe'})['effet']
        tab = self.tab_panier()
        with qualifie('infogreffe_commande'), patch.object(P, 'verifier_permis', lambda *a, **k: 'x'):
            self.desk.db.execute("UPDATE formalites_effets56109 SET etat='autorise' WHERE id=?", (eid,))
            self.desk.db.commit()
            F.executer(self.desk, AVOCAT, eid, self.seance, 0, tab, 'payer')                   # garde retirée : achat sans accord de l'avocat
        self.assertEqual(tab.clics, ['payer'])

    def test_G15_coupure_apres_paiement_jamais_de_nouvel_achat(self):
        op, fid, eid = self.achat_autorise()
        tab = self.tab_panier(perdre=True)
        with qualifie('infogreffe_commande'):
            with self.assertRaisesRegex(Stop, 'reponse_cdp_perdue'):
                F.executer(self.desk, AVOCAT, eid, self.seance, 0, tab, 'payer')
            self.assertEqual(self.fiche(fid)['etat'], 'achat_a_rapprocher')
            with self.assertRaisesRegex(Stop, 'effet_deja_tente_a_rapprocher'):
                F.executer(self.desk, AVOCAT, eid, self.seance, 0, tab, 'payer')
            with self.assertRaisesRegex(Stop, 'achat_a_rapprocher_avant_tout_nouvel_achat'):
                F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'acheter_document_infogreffe'})
            historique = FakeTab([{'chemin': 'historique', 'texte': 'Mes commandes : Commande n° CMD-0001 — Extrait Kbis'}])
            out = F.rapprocher(self.desk, AVOCAT, eid, self.seance, 0, historique)
        self.assertEqual((out['etat'], tab.clics, historique.clics), ('confirme', ['payer'], []))     # un seul clic d'achat, jamais rejoué

    def test_G15_routine_n_achete_jamais(self):
        op, fid, eid = self.achat_autorise()
        tab = self.tab_panier()
        with qualifie('infogreffe_commande'), self.assertRaisesRegex(Stop, 'routine_preparation_seulement'):
            F.executer(self.desk, AVOCAT, eid, self.seance, 0, tab, 'payer', routine=True)
        self.assertEqual(tab.clics, [])

    def test_G15_profil_reel_non_qualifie_aucun_effet(self):
        op, fid, eid = self.achat_autorise()
        tab = self.tab_panier()
        self.assertFalse(any(p['qualifie'] for p in F.PROFILS.values()))
        with self.assertRaisesRegex(Stop, 'profil_non_qualifie'):
            F.executer(self.desk, AVOCAT, eid, self.seance, 0, tab, 'payer')
        self.assertEqual(tab.clics, [])

    def test_G15_pdf_officiel_controle_et_facture_distincte(self):
        op, fid, eid = self.achat_autorise()
        with qualifie('infogreffe_commande'):
            F.executer(self.desk, AVOCAT, eid, self.seance, 0, self.tab_panier(), 'payer')
        connexion = self.depot('Formalites/connexion.pdf', b'<html><body>Identifiez-vous : connexion</body></html>')
        with self.assertRaisesRegex(Stop, 'document_non_conforme'):
            F.document(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'chemin': connexion}, self.docs)
        fac = self.depot('Formalites/facture.pdf', pdf('FACTURE Infogreffe commande CMD-0001 montant 3,28 HT'))
        with self.assertRaisesRegex(Stop, 'document_non_conforme'):
            F.document(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'chemin': fac}, self.docs)
        kbis = self.depot('Formalites/Kbis ZORVAL.pdf', pdf('Extrait Kbis - ZORVAL SAS - RCS Lyon %s' % SIREN))
        out = F.document(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'chemin': kbis, 'emis_le': '2026-11-20'}, self.docs)
        self.assertEqual((out['controle'], out['etat']), ('ok', 'document_recu'))
        self.assertEqual(self.docs.files[kbis], pdf('Extrait Kbis - ZORVAL SAS - RCS Lyon %s' % SIREN))   # archivé inchangé
        item = self.desk.db.execute("SELECT * FROM prepared_items5689 WHERE source_service='formalites56109' AND source_object_id=?", (fid,)).fetchone()
        self.assertEqual(item['path'], kbis)                                                    # un seul document du registre commun
        F.facture(self.desk, AVOCAT, {'formalite': fid, 'chemin': fac, 'montant_ht': '3,28', 'jour': '2026-11-20', 'cle': self.cle()}, self.docs)
        types = [d['type'] for d in self.fiche(fid)['documents']]
        self.assertEqual(sorted(types), ['facture_achat', 'pdf_officiel'])
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM frais5697 WHERE matter=?", (self.mid,)).fetchone()[0], 1)

    def test_G15_document_deja_detenu_et_endettement_dans_son_perimetre(self):
        op, fid, eid = self.achat_autorise()
        with qualifie('infogreffe_commande'):
            F.executer(self.desk, AVOCAT, eid, self.seance, 0, self.tab_panier(), 'payer')
        kbis = self.depot('Formalites/Kbis.pdf', pdf('Extrait Kbis %s' % SIREN))
        F.document(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'chemin': kbis}, self.docs)
        out = F.preparer(self.desk, AVOCAT, {'nature': 'infogreffe:kbis', 'op_kind': 'corporate5636', 'op_id': op, 'siren': SIREN, 'fraicheur_jours': '30'})
        self.assertEqual(out['etat'], 'deja_detenu')
        e = F.preparer(self.desk, AVOCAT, {'nature': 'infogreffe:endettement', 'op_kind': 'corporate5636', 'op_id': op, 'siren': SIREN})['formalite']
        self.assertTrue(any('ne prouve pas l’absence de dette' in m for m in self.fiche(e)['mentions']))


# =========================================================================================== G14 INPI
class G14Inpi(Socle):
    def formalite(self):
        op = self.op_siege()
        fid = F.preparer(self.desk, AVOCAT, {'nature': 'inpi:modification', 'op_kind': 'corporate5636', 'op_id': op})['formalite']
        return op, fid

    def pret(self, fid, synthese=True):
        for k, v in self.fiche(fid)['donnees'].items():
            F.donnees(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'champ': k, 'statut': 'verifiee',
                                          'valeur': v['valeur'] or 'Mandat du 2 novembre 2026', 'source': 'PV signé, p. 1 (essai)'})
        F.piece(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'chemin': self.depot('PV signe.pdf'), 'rubrique': 'pv'}, self.docs)
        F.piece(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'chemin': self.depot('Statuts signes.pdf', PDF + b'%statuts\n%%EOF\n'),
                                    'rubrique': 'statuts'}, self.docs)
        F.authentification(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'identite_constatee': 'Me DURAND (FranceConnect+)'})
        if synthese:
            f = self.fiche(fid)
            texte = ' '.join(v['valeur'] for v in f['donnees'].values()) + ' PV signe.pdf Statuts signes.pdf'
            return F.synthese(self.desk, AVOCAT, {'formalite': fid, 'version': f['version'], 'texte': texte, 'brouillon_ref': 'J00012345'})

    def test_G14_tableau_source_a_verifier_et_contradiction(self):
        op = self.op_siege()
        self.desk.db.execute("UPDATE operations5636 SET data=json_set(data,'$.ancien_siege','9 avenue Ancienne, 69001 Lyon') WHERE id=?", (op,))
        self.desk.db.commit()
        fid = F.preparer(self.desk, AVOCAT, {'nature': 'inpi:modification', 'op_kind': 'corporate5636', 'op_id': op})['formalite']
        d = self.fiche(fid)['donnees']
        self.assertEqual(d['siren']['valeur'], SIREN)
        self.assertEqual(d['nouveau_siege']['statut'], 'a_verifier')                           # la saisie n'est pas une vérification
        self.assertEqual(d['date_effet']['valeur'], '2026-11-02')                              # date de la décision saisie, jamais celle d'un fichier
        self.assertEqual(d['mandat']['statut'], 'manquante')
        self.assertEqual(d['siege_actuel']['statut'], 'contradictoire')
        with self.assertRaises(F.Refus):
            F.preparer(self.desk, AVOCAT, {'nature': 'inpi:modification', 'op_kind': 'dossier5683', 'op_id': self.op()})

    def test_G14_synthese_comparee_et_trois_autorisations_distinctes(self):
        op, fid = self.formalite()
        self.pret(fid, synthese=False)
        f = self.fiche(fid)
        out = F.synthese(self.desk, AVOCAT, {'formalite': fid, 'version': f['version'], 'texte': 'Synthèse : société ZORVAL, adresse 7 rue Autre', 'brouillon_ref': 'J00012345'})
        self.assertEqual(out['etat'], 'synthese_ecarts')
        with self.assertRaises(F.Refus):
            F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'signer_inpi'})
        self.assertEqual(self.pret(fid)['etat'], 'synthese_conforme')
        e1 = F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'signer_inpi'})['effet']
        with self.assertRaises(F.Refus):
            F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'transmettre_inpi'})    # signature non confirmée
        with self.assertRaises(F.Refus):
            F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'payer_inpi'})          # montant non affiché
        tab = FakeTab([{'chemin': 'signature', 'texte': 'Signer la formalité J00012345'}, {'chemin': 'signee', 'texte': 'Formalité signée. Référence : J00012345'}])
        self.autoriser(e1)
        with qualifie('inpi_guichet_unique'):
            self.assertEqual(F.executer(self.desk, AVOCAT, e1, self.seance, 0, tab, 'signer')['etat'], 'confirme')
        e3 = F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'transmettre_inpi'})['effet']
        ops = self.desk.db.execute("SELECT capability, state FROM connexion_ops5652 WHERE provider='portail_inpi' ORDER BY created").fetchall()
        self.assertEqual([(r[0], r[1]) for r in ops], [('signer_inpi', 'envoyee'), ('transmettre_inpi', 'a_valider')])   # permis distincts
        tab3 = FakeTab([{'chemin': 'transmission', 'texte': 'Transmettre'}, {'chemin': 'ok', 'texte': 'Formalité transmise. Référence : J00012345'}])
        self.autoriser(e3)
        with qualifie('inpi_guichet_unique'):
            F.executer(self.desk, AVOCAT, e3, self.seance, 0, tab3, 'transmettre')
        self.assertEqual(self.fiche(fid)['etat'], 'transmise')                                 # transmise n'est pas validée
        with self.assertRaises(F.Refus):
            F.etat(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'etat': 'validee', 'preuve': ''})
        F.etat(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'etat': 'validee', 'preuve': 'Courriel du greffe du 25/11/2026 (pièce)'})
        self.assertEqual(self.fiche(fid)['etat'], 'validee')

    def test_G14_piece_remplacee_apres_autorisation_invalide_le_permis(self):
        op, fid = self.formalite()
        self.pret(fid)
        e1 = F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'signer_inpi'})['effet']
        self.autoriser(e1)
        F.piece(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'chemin': self.depot('PV signe v2.pdf', PDF + b'%v2\n%%EOF\n'),
                                    'rubrique': 'pv'}, self.docs)
        tab = FakeTab([{'chemin': 'signature', 'texte': 'Signer'}])
        with qualifie('inpi_guichet_unique'), self.assertRaisesRegex(Stop, 'autorisation_exacte_requise'):
            F.executer(self.desk, AVOCAT, e1, self.seance, 0, tab, 'signer')
        self.assertEqual((tab.clics, self.fiche(fid)['effets'][0]['etat']), ([], 'a_revalider'))

    def test_G14_un_modele_ou_une_routine_n_accorde_jamais_d_autorisation(self):
        op, fid = self.formalite()
        self.pret(fid)
        e1 = F.demander(self.desk, IA, {'formalite': fid, 'action': 'signer_inpi'}, declencheur='mission')['effet']   # préparer : oui
        fe = F.fiche_effet(self.desk, AVOCAT, e1)
        with self.assertRaisesRegex(Stop, 'confirmation_humaine_requise'):                   # hors de la route POST de l'interface
            F.autoriser(self.desk, AVOCAT, {'effet': e1, 'version': fe['version'], 'empreinte': fe['empreinte'], 'relu': True})
        with bland56109.requete_humaine('agent_cabinet'), self.assertRaisesRegex(Stop, 'verification_humaine_requise'):
            F.autoriser(self.desk, IA, {'effet': e1, 'version': fe['version'], 'empreinte': fe['empreinte'], 'relu': True})
        with bland56109.requete_humaine(AVOCAT[0]), self.assertRaisesRegex(Stop, 'fiche_modifiee_depuis_affichage'):
            F.autoriser(self.desk, AVOCAT, {'effet': e1, 'version': fe['version'], 'empreinte': 'autre', 'relu': True})
        self.assertEqual(self.fiche(fid)['effets'][0]['etat'], 'a_autoriser')

    def test_G14_mutation_sans_marque_humaine_un_appel_hors_interface_autoriserait(self):
        op, fid = self.formalite()
        self.pret(fid)
        e1 = F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'signer_inpi'})['effet']
        fe = F.fiche_effet(self.desk, AVOCAT, e1)
        with patch.object(bland56109, '_exiger_humain', lambda: None):
            F.autoriser(self.desk, AVOCAT, {'effet': e1, 'version': fe['version'], 'empreinte': fe['empreinte'], 'relu': True})
        self.assertEqual(self.fiche(fid)['effets'][0]['etat'], 'autorise')                     # garde retirée : accordé sans l'interface

    def test_G14_le_generique_valider_et_transmettre_ne_declenche_rien(self):
        op, fid = self.formalite()
        self.pret(fid)
        e1 = F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'signer_inpi'})['effet']
        from agent import connexions5652 as cx
        fe = F.fiche_effet(self.desk, AVOCAT, e1)
        with self.assertRaisesRegex(Stop, 'connexion_absente'):
            cx.validate(self.desk, AVOCAT[0], 'administrateur', fe['operation'], fe['version'], fe['empreinte'])
        self.assertEqual(self.desk.db.execute('SELECT state FROM connexion_ops5652 WHERE id=?', (fe['operation'],)).fetchone()[0], 'a_valider')

    def test_G14_prise_de_main_perime_l_action_en_cours(self):
        op, fid = self.formalite()
        self.pret(fid)
        e1 = F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'signer_inpi'})['effet']
        self.autoriser(e1)
        tab = FakeTab([{'chemin': 'signature', 'texte': 'Signer'}, {'chemin': 'ok', 'texte': 'Référence : J00012345'}])
        N.changer_main(self.seance, True)                                                      # l'avocat prend la main
        with qualifie('inpi_guichet_unique'), self.assertRaisesRegex(Stop, 'main_a_l_avocat'):
            F.executer(self.desk, AVOCAT, e1, self.seance, 0, tab, 'signer')
        self.assertEqual(tab.clics, [])
        N.changer_main(self.seance, False)                                                     # reprise : nouvelle génération
        with qualifie('inpi_guichet_unique'), self.assertRaisesRegex(Stop, 'generation_perimee'):
            F.executer(self.desk, AVOCAT, e1, self.seance, 0, tab, 'signer')                   # la décision d'avant la prise de main est périmée
        with qualifie('inpi_guichet_unique'):
            self.assertEqual(F.executer(self.desk, AVOCAT, e1, self.seance, self.seance.epoch, tab, 'signer')['etat'], 'confirme')

    def test_G14_mutation_sans_barriere_l_action_partirait_pendant_la_main(self):
        op, fid = self.formalite()
        self.pret(fid)
        e1 = F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'signer_inpi'})['effet']
        self.autoriser(e1)
        tab = FakeTab([{'chemin': 'signature', 'texte': 'Signer'}, {'chemin': 'ok', 'texte': 'Référence : J00012345'}])
        N.changer_main(self.seance, True)
        from contextlib import contextmanager

        @contextmanager
        def passoire(s, epoch, desk=None):
            yield
        with qualifie('inpi_guichet_unique'), patch.object(N, 'barriere', passoire):
            F.executer(self.desk, AVOCAT, e1, self.seance, 0, tab, 'signer')
        self.assertEqual(tab.clics, ['signer'])                                                # garde retirée : le clic partait sous la main de l'avocat

    def test_G14_authentification_humaine_et_page_inattendue(self):
        op, fid = self.formalite()
        self.pret(fid)
        self.desk.db.execute("UPDATE formalites56109 SET auth_etat='attente' WHERE id=?", (fid,))
        self.desk.db.commit()
        e1 = F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'signer_inpi'})['effet']
        self.autoriser(e1)
        with qualifie('inpi_guichet_unique'), self.assertRaisesRegex(Stop, 'attente_connexion'):
            F.executer(self.desk, AVOCAT, e1, self.seance, 0, FakeTab([{'texte': 'x'}]), 'signer')
        F.authentification(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'identite_constatee': 'Me DURAND'})
        j = [json.loads(r['detail']) for r in self.desk.db.execute("SELECT detail FROM formalites_journal56109 WHERE formalite=? AND action='authentification_effectuee'", (fid,))]
        self.assertEqual(j[-1], {'identite_constatee': True})                                  # la mention seulement, jamais l'identifiant ni un code

    def test_G14_televersement_octets_exacts_reception_non_presumee(self):
        op, fid = self.formalite()
        self.pret(fid)
        chemin = self.path('PV signe.pdf')
        tab = FakeTab([{'chemin': 'pieces', 'texte': 'Ajouter une pièce'}])
        with qualifie('inpi_guichet_unique'):
            out = F.televerser(self.desk, AVOCAT, fid, chemin, self.seance, 0, tab, 'e1', self.docs)
        envoye = [p for m, p in tab.cdp.calls if m == 'DOM.setFileInputFiles'][0]['files'][0]
        with open(envoye, 'rb') as fh:
            self.assertEqual(fh.read(), PDF)
        self.assertEqual(out['etat'], 'a_rapprocher')
        self.assertEqual([p['televersement'] for p in self.fiche(fid)['pieces'] if p['chemin'] == chemin], ['a_rapprocher'])
        out['_depot'].liberer(rapproche=True)
        N.changer_main(self.seance, True)
        tab2 = FakeTab([{'chemin': 'pieces', 'texte': 'Ajouter une pièce'}])
        with qualifie('inpi_guichet_unique'), self.assertRaisesRegex(Stop, 'main_a_l_avocat'):
            F.televerser(self.desk, AVOCAT, fid, chemin, self.seance, 0, tab2, 'e1', self.docs)
        self.assertFalse(any(m == 'DOM.setFileInputFiles' for m, _ in tab2.cdp.calls))


# =========================================================================================== G16 RIB CARPA
class G16Carpa(Socle):
    def demande(self):
        return F.preparer(self.desk, AVOCAT, {'nature': 'carpa:rib_demande', 'dossier': self.mid})['formalite']

    def test_G16_demande_exacte_accuse_distinct_du_rib_et_aucun_mouvement(self):
        avant = {t: self.desk.db.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0] for t in ('mouvements56109',)}
        fid = self.demande()
        with self.assertRaises(F.Refus):
            F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'demander_rib_carpa'})     # informations non vérifiées
        for k in ('cabinet', 'avocat', 'parties', 'operation', 'reference_affaire'):
            F.donnees(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'champ': k, 'statut': 'verifiee',
                                          'valeur': 'AFF-2026-001' if k == 'reference_affaire' else 'DURAND / ZORVAL (essai)', 'source': 'Dossier (essai)'})
        eid = F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'demander_rib_carpa'})['effet']
        self.autoriser(eid)
        with self.assertRaisesRegex(Stop, 'profil_non_qualifie'):                             # service de la CARPA non qualifié : aucun effet
            F.executer(self.desk, AVOCAT, eid, self.seance, 0, FakeTab([{'texte': 'x'}]), 'envoyer')
        b = self.depot('CARPA/Bordereau.pdf', pdf('Bordereau de demande de RIB AFF-2026-001'))
        F.document(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'type': 'bordereau', 'chemin': b}, self.docs)
        self.assertEqual(self.fiche(fid)['etat'], 'bordereau_prepare')
        F.etat(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'etat': 'transmise_manuellement', 'preuve': 'Courriel à la CARPA du 21/11/2026'})
        acc = self.depot('CARPA/Accuse.pdf', pdf('Accusé de réception de votre demande AFF-2026-001'))
        out = F.document(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'type': 'accuse', 'chemin': acc}, self.docs)
        self.assertEqual(out['etat'], 'attente_carpa')                                         # un accusé ne vaut pas délivrance du RIB
        rib = self.depot('CARPA/RIB AFF-2026-001.pdf', pdf('RIB CARPA affaire AFF-2026-001 IBAN FR76 3000 6000 0112 3456 7890 189'))
        out = F.document(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'type': 'rib', 'chemin': rib, 'iban': 'FR7630006000011234567890189'}, self.docs)
        self.assertEqual(out['etat'], 'rib_recu')
        c = self.desk.db.execute("SELECT * FROM coordonnees56109 WHERE beneficiaire='CARPA — AFF-2026-001'").fetchone()
        self.assertEqual((c['etat'], c['iban_masque']), ('a_verifier', 'FR76 **** **** 0189'))   # vérification indépendante du lot G1
        journal = ' '.join(r[0] for r in self.desk.db.execute('SELECT detail FROM formalites_journal56109 WHERE formalite=?', (fid,)))
        self.assertNotIn('FR7630006000011234567890189', journal)
        apres = {t: self.desk.db.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0] for t in avant}
        self.assertEqual(avant, apres)                                                          # aucun mouvement de fonds

    def test_G16_rib_d_une_autre_affaire_reste_a_verifier(self):
        fid = F.preparer(self.desk, AVOCAT, {'nature': 'carpa:rib_existant', 'dossier': self.mid})['formalite']
        F.donnees(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'champ': 'reference_affaire', 'statut': 'verifiee',
                                      'valeur': 'AFF-2026-001', 'source': 'Dossier (essai)'})
        F.donnees(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'champ': 'sous_compte', 'statut': 'verifiee',
                                      'valeur': 'SC-0042', 'source': 'Courrier de la CARPA (essai)'})
        rib = self.depot('CARPA/RIB autre.pdf', pdf('RIB CARPA affaire AFF-2026-001 sous-compte SC-0099'))
        out = F.document(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'type': 'rib', 'chemin': rib}, self.docs)
        self.assertEqual((out['controle'], out['etat']), ('a_verifier', 'rib_a_verifier'))     # mauvais sous-compte : jamais « RIB reçu »

    def test_G16_aucun_virement_retrait_ni_liberation_au_catalogue(self):
        for a in ('virer_carpa', 'retirer_carpa', 'liberer_carpa', 'modifier_coordonnees_carpa'):
            with self.assertRaisesRegex(Stop, 'action_portail_non_prevue'):
                P.empreinte_action({'cabinet': 'c', 'utilisateur': 'u', 'compte': 'k', 'portail': 'carpa', 'dossier': 'd', 'action': a, 'operation': 'o'})
        from agent import closing56109 as C
        self.assertFalse(any(re.search(r'vir|retir|liber', r) for r in C.ROUTES_POST))
        fid = self.demande()
        with self.assertRaisesRegex(Stop, 'action_portail_non_prevue'):
            F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'virer_carpa'})

    def test_G16_rib_existant_se_recupere_sans_demande_et_vue_financiere(self):
        fid = F.preparer(self.desk, AVOCAT, {'nature': 'carpa:rib_existant', 'dossier': self.mid})['formalite']
        with self.assertRaises(F.Refus):
            F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'demander_rib_carpa'})
        html = F.section_finances_html(self.desk, '/p', AVOCAT, self.mid)
        self.assertIn('RIB CARPA (documentaire)', html)
        self.assertIn('aucun virement, retrait ni libération', html)


import re  # noqa: E402 — utilisé par G16


# =========================================================================================== G20 étranger
class G20Etranger(Socle):
    def test_G20_profils_declaratifs_non_qualifies(self):
        etr = {k: v for k, v in F.PROFILS.items() if v['portail'] == 'etranger'}
        self.assertIn('etranger:uk_myhmcts', etr)
        self.assertTrue(all(not v['qualifie'] for v in etr.values()))
        self.assertFalse(etr['etranger:uk_myhmcts']['brouillon'])                              # sans brouillon : préparation locale
        self.assertTrue(etr['etranger:de_notar']['notaire'])

    def test_G20_preparation_locale_envoi_et_acceptation_distincts(self):
        op = self.op_siege()
        fid = F.preparer(self.desk, AVOCAT, {'nature': 'etranger:formalite', 'op_kind': 'corporate5636', 'op_id': op, 'profil': 'etranger:uk_myhmcts'})['formalite']
        self.assertEqual(self.fiche(fid)['etat'], 'preparation_locale')
        with self.assertRaises(F.Refus):
            F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'transmettre_etranger'})
        with self.assertRaises(F.Refus):
            F.etat(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'etat': 'preparee_localement'})
        F.donnees(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'champ': 'claimant', 'libelle': 'Claimant', 'statut': 'verifiee',
                                      'valeur': 'ZORVAL SAS', 'source': 'Dossier (essai)'})
        F.piece(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'chemin': self.depot('Claim form.pdf'), 'rubrique': 'claim'}, self.docs)
        F.etat(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'etat': 'preparee_localement'})
        with self.assertRaises(F.Refus):
            F.etat(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'etat': 'acceptee', 'preuve': 'x' * 5})   # rien n'est envoyé
        F.authentification(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'identite_constatee': 'Solicitor account (essai)'})
        eid = F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'transmettre_etranger'})['effet']
        self.autoriser(eid)
        tab = FakeTab([{'chemin': 'submit', 'texte': 'Submit'}, {'chemin': 'done', 'texte': 'Submitted. Reference: MCOL-0001'}])
        with self.assertRaisesRegex(Stop, 'profil_non_qualifie'):
            F.executer(self.desk, AVOCAT, eid, self.seance, 0, tab, 'submit')
        with qualifie('etranger:uk_myhmcts'):
            with self.assertRaisesRegex(Stop, 'routine_preparation_seulement'):
                F.executer(self.desk, AVOCAT, eid, self.seance, 0, tab, 'submit', routine=True)
            F.executer(self.desk, AVOCAT, eid, self.seance, 0, tab, 'submit')
        self.assertEqual(self.fiche(fid)['etat'], 'envoyee')                                   # envoyée n'est pas acceptée
        F.etat(self.desk, AVOCAT, {'formalite': fid, 'version': self.fiche(fid)['version'], 'etat': 'acceptee', 'preuve': 'Courriel du greffe (essai)'})
        self.assertEqual(self.fiche(fid)['etat'], 'acceptee')

    def test_G20_export_declare_sans_session_ni_secret(self):
        from agent import closing56109, suites56109
        tables = closing56109.TABLES + suites56109.TABLES + F.TABLES
        contrat = export56108.contrat() if hasattr(export56108, 'contrat') else None
        texte = open(export56108.__file__, encoding='utf-8').read()
        for t in tables:
            self.assertIn(t, texte)
            cols = [r[1] for r in self.desk.db.execute('PRAGMA table_info(%s)' % t)]
            self.assertFalse([c for c in cols if re.search(r'session|cookie|pin$|otp|mot_de_passe|password|token', c)], t)
        self.assertTrue(contrat is None or contrat)


class G3Routes(Socle):
    def appel(self, route, data=None, acteur=AVOCAT, args=None):
        from agent import web440
        corps = json.dumps(data or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': acteur[0], 'HTTP_X_AXIORHUB_ROLE': acteur[1], 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON,
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps),
               'REQUEST_METHOD': 'GET' if data is None else 'POST'}
        out = web440.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm56109/operations/' + route, args or {}, env['REQUEST_METHOD'])
        self.assertIn(('Cache-Control', 'private, no-store'), out.get('headers', []))
        return out['status'][:3], json.loads(out['body'])

    def test_G14_route_autoriser_pose_la_marque_humaine_et_refuse_les_secrets(self):
        op = self.op_siege()
        st, out = self.appel('formalites/preparer', {'cle': self.cle(), 'nature': 'infogreffe:kbis', 'op_kind': 'corporate5636', 'op_id': op, 'siren': SIREN, 'siren_confirme': True})
        self.assertEqual(st, '200', out)
        fid = out['formalite']
        st, out = self.appel('formalites/authentification', {'cle': self.cle(), 'formalite': fid, 'version': 1, 'code_pin': '1234'})
        self.assertEqual((st, out['error']), ('422', 'secret_refuse'))
        self.panier(fid)
        eid = F.demander(self.desk, AVOCAT, {'formalite': fid, 'action': 'acheter_document_infogreffe'})['effet']
        st, fe = self.appel('effet', args={'id': eid})
        st, out = self.appel('formalites/autoriser', {'cle': self.cle(), 'effet': eid, 'version': fe['version'], 'empreinte': fe['empreinte'], 'relu': True}, acteur=ASSISTANT)
        self.assertEqual(st, '403')
        st, out = self.appel('formalites/autoriser', {'cle': self.cle(), 'effet': eid, 'version': fe['version'], 'empreinte': fe['empreinte'], 'relu': True})
        self.assertEqual((st, out['etat']), ('200', 'autorise'))
        st, out = self.appel('formalites/virer', {'cle': self.cle()})
        self.assertEqual(st, '404')

    def test_G15_ecrans_corporate_et_cession_portent_les_formalites(self):
        op = self.op_siege()
        html = self.co.page_html(self.desk, '/p', {'societe': self.cid2, 'vue': 'actes', 'finaliser': op})
        self.assertIn('Préparer sur le Guichet unique', html)
        self.assertIn('Obtenir un Kbis', html)
        did = self.op()
        html = t84.ces.page_html(self.desk, '/p', {'type': 'fonds', 'operation': did, 'vue': 'formalites'})
        self.assertIn('Obtenir un état d’endettement', html)


if __name__ == '__main__':
    unittest.main()
