"""5.6.97, lot gestion (§ 18 à § 21) : registre procédural et paquet de dépôt gelé, honoraires et encaissement, pièces demandées et
remises, clôture, réouverture et restauration vérifiée.

Identifiants des scénarios : K18_xx (§ 18), K19_xx (§ 19), K20_xx (§ 20), K21_xx (§ 21), KIA (commandes sans IA), KM (mutations des
garde-fous). Données fictives (DURAND, MARTIN, ZORVAL, DOS-001, RG 24/01234, Valbrune, example.test, SIREN d'essai de la liste).
Stockage local temporaire, Nextcloud OCS et Invoice Ninja simulés : tout appel réseau non simulé fait échouer l'essai.
"""
import base64
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import test_agent
from agent import cloture5697, echeances450, facturation5653 as fact, gestion5697 as g, honoraires5697 as hon, membres5650, pause5629
from agent import procedure5697 as proc, remises5697 as rem, relances5636, time500
from agent.common import Stop, private_json
from agent.desk import Desk

MID, MID2 = 'DOS-001', 'DOS-002'
AVOCAT, ASSISTANT, ADMIN = ('avocate@example.test', 'avocat'), ('assistant@example.test', 'assistant'), ('admin@example.test', 'administrateur')
AUTRE = ('autre.avocat@example.test', 'avocat')
CLIENT_MAIL = 'durand@example.test'
ENTITE = {'nom': 'Cabinet ZORVAL (fictif)', 'barreau': 'Valbrune', 'adresse_ligne1': '1 rue de l’Exemple', 'code_postal': '75001', 'ville': 'Valbrune', 'pays': 'FR',
          'siren': '000010017', 'tva': 'FR00000010017', 'email': 'cabinet@example.test', 'iban': 'FR7600000000000000000000000', 'regime_cabinet': 'normal',
          'categorie_entreprise': 'micro', 'delai_paiement_jours': '30'}
CLIENT = {'nom': 'DURAND SAS (fictive)', 'kind': 'professionnel', 'adresse_ligne1': '2 avenue Fictive', 'code_postal': '69001', 'ville': 'Valbrune', 'country': 'FR',
          'siren': '000010025', 'email': CLIENT_MAIL}


def reseau_interdit(*args, **kwargs):
    raise AssertionError('appel réseau non simulé')


class FauxAgenda:
    """Agenda simulé : compte les écritures d'événements (identifiant stable)."""

    def __init__(self):
        self.ecritures = []

    def put_event(self, calendar, proposal_id, title, start, end, description, replace=False):
        self.ecritures.append((proposal_id, replace))
        return proposal_id


class FauxOCS:
    """Nextcloud simulé pour l'API OCS de partage (format JSON de la documentation publique), avec la vue du destinataire."""

    def __init__(self, racine):
        self.base, self.racine, self.partages, self.n = 'https://nuage.example.test', Path(racine), {}, 0
        self.panne, self.ignorer_suppression, self.perdre_reponse, self.appels = None, False, False, []

    def _ok(self, data, code=200):
        return json.dumps({'ocs': {'meta': {'status': 'ok', 'statuscode': code, 'message': 'OK'}, 'data': data}}).encode()

    def _absent(self):
        return json.dumps({'ocs': {'meta': {'status': 'failure', 'statuscode': 404, 'message': 'Wrong share ID, share does not exist'}, 'data': []}}).encode()

    def request(self, method, url, data=None, headers=None, limit=0):
        from urllib.parse import parse_qs, urlsplit
        self.appels.append((method, url))
        assert headers.get('OCS-APIRequest') == 'true'
        if self.panne:
            raise Stop(self.panne)
        u = urlsplit(url)
        chemin = u.path.split('/api/v1/shares', 1)[1]
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if method == 'POST':
            f = {k: v[0] for k, v in parse_qs(data.decode()).items()}
            self.n += 1
            ident = str(100 + self.n)
            self.partages[ident] = {'id': ident, 'path': f['path'], 'share_type': int(f['shareType']), 'share_with': f.get('shareWith', ''),
                                    'permissions': int(f['permissions']), 'expiration': f.get('expireDate', ''), 'password': f.get('password', ''),
                                    'token': 'jeton%d' % self.n, 'url': 'https://nuage.example.test/s/jeton%d' % self.n}
            if self.perdre_reponse:
                self.perdre_reponse = False
                raise Stop('delai_http_depasse')
            return self._ok({k: v for k, v in self.partages[ident].items() if k != 'password'})
        if method == 'GET' and chemin.startswith('/'):
            s = self.partages.get(chemin[1:])
            return self._ok([s]) if s else self._absent()
        if method == 'GET':
            return self._ok([s for s in self.partages.values() if s['path'] == q.get('path')])
        if method == 'DELETE':
            if chemin[1:] not in self.partages:
                return self._absent()
            if not self.ignorer_suppression:
                del self.partages[chemin[1:]]
            return self._ok([])
        raise AssertionError(method)

    def vue_destinataire(self, ident, aujourdhui=None):
        """Ce que voit le destinataire du partage : rien s'il est révoqué ou expiré ; rien à lire en dépôt seul."""
        s = self.partages.get(str(ident))
        if not s or (s['expiration'] and s['expiration'] < (aujourdhui or date.today()).isoformat()):
            return None
        if not s['permissions'] & 1:
            return []
        dossier = self.racine / s['path'].lstrip('/')
        return sorted(p.name for p in dossier.iterdir() if p.is_file())


class TransportFaux:
    """Invoice Ninja simulé (formats d'après la documentation publique : factures, règlements avec ``paymentables``)."""

    def __init__(self):
        self.liste_factures, self.liste_reglements, self.panne, self.creees, self.perdre = [], [], None, {}, False

    def essai(self):
        if self.panne:
            raise Stop(self.panne)
        return {'ok': True, 'lecture': True, 'ecriture': False}

    def factures(self):
        if self.panne:
            raise Stop(self.panne)
        return list(self.liste_factures)

    def reglements(self):
        if self.panne:
            raise Stop(self.panne)
        return list(self.liste_reglements)

    def chercher_facture(self, cle):
        if self.panne:
            raise Stop(self.panne)
        return self.creees.get(cle)

    def creer_facture(self, donnees):
        self.creees[donnees['cle']] = {'id': 'inv-%d' % (len(self.creees) + 1), 'number': 'NJ-%04d' % (len(self.creees) + 1)}
        if self.perdre:
            self.perdre = False
            raise Stop('delai_http_depasse')
        return self.creees[donnees['cle']]


class Base(unittest.TestCase):
    def setUp(self):
        self.f = test_agent.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for d in ('Dossiers/DURAND', 'Dossiers/MARTIN'):
            (self.root / d).mkdir(parents=True)
        matters = [{'id': MID, 'client_name': 'DURAND', 'path': '/Dossiers/DURAND', 'references': [MID, 'RG 24/01234'], 'aliases': ['DURAND'],
                    'correspondents': [{'email': CLIENT_MAIL, 'role': 'client'}]},
                   {'id': MID2, 'client_name': 'MARTIN', 'path': '/Dossiers/MARTIN', 'references': [MID2], 'aliases': ['MARTIN'],
                    'correspondents': [{'email': 'martin@example.test', 'role': 'client'}]}]
        private_json(self.f.matter_file, matters)
        self.f.c['nextcloud'] = {**self.f.c['nextcloud'], 'local_path': str(self.root)}
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        p = patch('agent.common.HTTP.request', side_effect=reseau_interdit)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(membres5650.clear)
        membres5650.set_reserve(self.f.c['state_dir'], MID2, [AVOCAT[0]])       # DOS-002 réservé à l'avocate : invisible pour AUTRE
        self.n = 0
        self.ocs = FauxOCS(self.root)
        self.agenda = FauxAgenda()

    def cle(self):
        self.n += 1
        return 'cle-essai-%04d' % self.n

    def fichier(self, rel, data):
        p = self.root / rel.lstrip('/')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return '/' + rel.lstrip('/')

    def echeance_confirmee(self, mid=MID, depart=None):
        depart = (depart or date.today() - timedelta(days=5)).isoformat()
        out = proc.saisir_echeance(self.desk, AVOCAT, mid, {'regle': 'appel_jugement_contentieux', 'depart': depart, 'source': '/Dossiers/DURAND/jugement.pdf p. 1',
                                                             'depart_certain': True}, self.agenda)
        self.assertEqual(out['status'], 'confirmee')
        return out


# =========================================================================================== § 18
class Procedure(Base):
    def test_K18_01_date_citee_dans_une_piece_reste_a_verifier_sans_rappel_ni_agenda(self):
        out = proc.saisir_echeance(self.desk, AVOCAT, MID, {'regle': 'appel_jugement_contentieux', 'depart': (date.today() - timedelta(days=3)).isoformat(),
                                                             'source': 'Courriel du confrère, 2e paragraphe', 'depart_certain': False}, self.agenda)
        self.assertTrue(out['a_verifier'])
        self.assertEqual(out['etat_libelle'], 'À vérifier')
        self.assertEqual(self.agenda.ecritures, [])                               # aucun événement d'agenda
        with self.assertRaisesRegex(Stop, 'rappel_sur_echeance_confirmee_seulement'):
            proc.attribuer(self.desk, AVOCAT, out['id'], {'responsable': AVOCAT[0], 'rappel_destinataire': AVOCAT[0], 'rappel_le': date.today().isoformat()})
        self.assertEqual(proc.rappels_a_signaler(self.desk, AVOCAT), [])          # aucun rappel ferme
        with self.assertRaisesRegex(Stop, 'source_requise'):
            proc.saisir_echeance(self.desk, AVOCAT, MID, {'regle': 'appel_jugement_contentieux', 'depart': date.today().isoformat(), 'source': ''})

    def test_K18_02_validation_manuelle_un_seul_evenement_lie(self):
        out = proc.saisir_echeance(self.desk, AVOCAT, MID, {'regle': 'appel_jugement_contentieux', 'depart': (date.today() - timedelta(days=3)).isoformat(),
                                                             'source': 'Acte de signification, page 2', 'depart_certain': False}, self.agenda)
        cle = self.cle()
        v = proc.decider(self.desk, AVOCAT, out['id'], {'action': 'valider', 'cle': cle, 'version': out['revision']}, self.agenda)
        self.assertEqual(v['status'], 'confirmee')
        proc.decider(self.desk, AVOCAT, out['id'], {'action': 'valider', 'cle': cle, 'version': out['revision']}, self.agenda)   # double clic
        c = proc.decider(self.desk, AVOCAT, out['id'], {'action': 'corriger', 'motif': 'Date relue sur l’acte', 'date': (date.today() + timedelta(days=40)).isoformat(),
                                                         'cle': self.cle(), 'version': v['revision']}, self.agenda)
        self.assertEqual({e[0] for e in self.agenda.ecritures}, {out['id']})      # un seul événement, mis à jour
        self.assertEqual(c['status'], 'manuel')
        actions = [j['action'] for j in g.historique(self.desk, AVOCAT, MID, 'echeance')]
        self.assertIn('valider', actions)
        self.assertIn('corriger', actions)

    def test_K18_03_acquittement_du_rappel_ne_vaut_pas_accomplissement(self):
        e = self.echeance_confirmee()
        hier = (date.today() - timedelta(days=1)).isoformat()
        proc.attribuer(self.desk, AVOCAT, e['id'], {'responsable': AVOCAT[0], 'rappel_destinataire': AVOCAT[0], 'rappel_le': hier, 'critique': True})
        signales = proc.rappels_a_signaler(self.desk, AVOCAT)
        self.assertEqual([s['echeance'] for s in signales], [e['id']])
        proc.acquitter_rappel(self.desk, AVOCAT, e['id'])
        self.assertEqual(proc.rappels_a_signaler(self.desk, AVOCAT), [])
        row = echeances450._row(self.desk, e['id'])
        self.assertEqual(row['status'], 'confirmee')                               # toujours ouverte
        self.assertEqual(next(c for c in cloture5697.preparer(self.desk, AVOCAT, MID)['controles'] if c['id'] == 'echeances')['etat'], 'bloquant')
        with self.assertRaisesRegex(Stop, 'preuve_accomplissement_requise'):
            proc.decider(self.desk, AVOCAT, e['id'], {'action': 'accomplir', 'cle': self.cle(), 'version': row['revision']})
        fait = proc.decider(self.desk, AVOCAT, e['id'], {'action': 'accomplir', 'preuve': 'Déclaration d’appel déposée, accusé n° ENV-0001', 'cle': self.cle(),
                                                         'version': row['revision']})
        self.assertEqual(fait['status'], 'terminee')

    def test_K18_04_reattribution_tracee_et_version_attendue(self):
        e = self.echeance_confirmee()
        s1 = proc.attribuer(self.desk, AVOCAT, e['id'], {'responsable': AVOCAT[0]})
        s2 = proc.attribuer(self.desk, AVOCAT, e['id'], {'responsable': 'collaborateur@example.test', 'version_suivi': s1['suivi']['revision']})
        self.assertEqual(s2['suivi']['responsable'], 'collaborateur@example.test')
        with self.assertRaisesRegex(Stop, 'version_en_conflit'):
            proc.attribuer(self.desk, AVOCAT, e['id'], {'responsable': AVOCAT[0], 'version_suivi': s1['suivi']['revision']})
        self.assertIn('reattribution', [j['action'] for j in g.historique(self.desk, AVOCAT, MID, 'echeance')])

    def paquet(self, cle=None):
        acte = self.fichier('Dossiers/DURAND/Conclusions.docx', b'PK conclusions version deposee')
        p1 = self.fichier('Dossiers/DURAND/Pieces/Piece 1 - contrat.pdf', b'%PDF-1.4 contrat fictif')
        p2 = self.fichier('Dossiers/DURAND/Pieces/Piece 2 - facture.pdf', b'%PDF-1.4 facture fictive')
        return proc.preparer_paquet(self.desk, AVOCAT, MID, {'cle': cle or self.cle(), 'canal': 'ebarreau', 'reference_affaire': 'RG 24/01234', 'acte': {'path': acte},
                                                             'pieces': [{'path': p1, 'numero': '1'}, {'path': p2, 'numero': '2'}]}), acte

    def test_K18_05_paquet_gele_modifier_le_word_apres_depot(self):
        pq, acte = self.paquet()
        self.assertEqual(pq['etat'], 'gele')
        self.assertEqual([f['rang'] for f in pq['fichiers']], [1, 2, 3])
        self.assertEqual([f['numero'] for f in pq['fichiers']], ['', '1', '2'])
        (self.root / acte.lstrip('/')).write_bytes(b'PK conclusions modifiees apres depot')
        raw, nom = proc.ouvrir_fichier(self.desk, AVOCAT, pq['id'], 1)
        self.assertEqual(raw, b'PK conclusions version deposee')                  # le paquet d'origine reste ouvrable et inchangé
        self.assertEqual(nom, 'Conclusions.docx')
        self.assertEqual(proc.vue_depot(self.desk, AVOCAT, pq['id'])['manifeste_sha'], pq['manifeste_sha'])

    def declarer(self, pq, envoi='ENV-2026-0001', jour=None):
        return proc.declarer_depot(self.desk, AVOCAT, pq['id'], {'version': pq['revision'], 'envoi_ref': envoi, 'date_depot': (jour or date.today()).isoformat(),
                                                                 'reference_affaire': 'RG 24/01234'})

    def accuse(self, pq, envoi='ENV-2026-0001', jour=None, nature='accuse', contenu=b'%PDF accuse fictif'):
        return proc.importer_accuse(self.desk, AVOCAT, pq['id'], {'nature': nature, 'contenu_b64': base64.b64encode(contenu).decode(), 'nom_fichier': 'accuse.pdf',
                                                                  'reference': 'RG 24/01234', 'envoi_ref': envoi, 'date': (jour or date.today()).isoformat()})

    def test_K18_06_accuse_d_un_autre_envoi_refuse_puis_bon_accuse_rapproche(self):
        pq, _ = self.paquet()
        with self.assertRaisesRegex(Stop, 'depot_non_declare'):
            self.accuse(pq)
        pq = self.declarer(pq)
        self.assertEqual(pq['etat'], 'depot_declare')
        refuse = self.accuse(pq, envoi='ENV-2026-0999', contenu=b'%PDF accuse autre envoi')
        self.assertEqual(refuse['etat'], 'depot_declare')
        self.assertFalse(refuse['rapproche'])
        self.assertTrue(any('identifiant d’envoi' in a for a in refuse['anomalies']))
        bon = self.accuse(pq)
        self.assertEqual(bon['etat'], 'accuse_rapproche')
        self.assertTrue(bon['rapproche'])
        self.assertEqual([a['etat'] for a in bon['accuses']], ['refuse', 'rapproche'])   # le refus reste visible

    def test_K18_07_notification_seule_ne_vaut_pas_accuse(self):
        pq, _ = self.paquet()
        pq = self.declarer(pq)
        out = self.accuse(pq, nature='notification', contenu=b'Notification e-Barreau : un message vous attend')
        self.assertEqual(out['etat'], 'depot_declare')
        self.assertFalse(out['rapproche'])
        self.assertTrue(out['alertes'])

    def test_K18_08_accuse_deja_importe(self):
        pq, _ = self.paquet()
        pq = self.declarer(pq)
        self.accuse(pq)
        again = self.accuse(pq)
        self.assertTrue(again['deja_importe'])
        self.assertEqual(len(again['accuses']), 1)
        autre, _ = self.paquet()
        autre = self.declarer(autre)
        with self.assertRaisesRegex(Stop, 'accuse_deja_rattache_a_un_autre_depot'):
            self.accuse(autre)

    def test_K18_09_accuse_textuel_contredit_la_saisie(self):
        pq, _ = self.paquet()
        pq = self.declarer(pq)
        texte = ('Accusé de réception\nRéférence : RG 24/01234\nIdentifiant d’envoi : ENV-2026-0777\nDate de dépôt : %s\n' % date.today().strftime('%d/%m/%Y')).encode()
        out = self.accuse(pq, contenu=texte)
        self.assertFalse(out['rapproche'])
        self.assertIn('ENV-2026-0777', out['anomalies'][0])

    def test_K18_10_paquet_rejeu_meme_cle(self):
        cle = self.cle()
        a, _ = self.paquet(cle)
        b, _ = self.paquet(cle)
        self.assertEqual(a['id'], b['id'])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM depots5697').fetchone()[0], 1)
        with self.assertRaisesRegex(Stop, 'cle_idempotence_deja_utilisee'):
            proc.preparer_paquet(self.desk, AVOCAT, MID, {'cle': cle, 'canal': 'greffe', 'acte_path': '/Dossiers/DURAND/Conclusions.docx'})

    def test_K18_11_dossier_reserve_et_absent_meme_reponse(self):
        for mid in (MID2, 'DOS-999'):
            with self.assertRaisesRegex(Stop, '^introuvable$'):
                proc.registre(self.desk, AUTRE, mid)
        self.assertTrue(proc.registre(self.desk, AVOCAT, MID2))


# =========================================================================================== § 19
class Honoraires(Base):
    def setUp(self):
        super().setUp()
        fact.save_entity(self.desk, *ADMIN, ENTITE)
        fact.save_client(self.desk, *AVOCAT, MID, CLIENT)

    def facture(self, ht='750', mid=MID):
        d = fact.prepare(self.desk, *AVOCAT, 'facture', mid, {'sous_type': 'libre', 'date_prestation': date.today().isoformat(), 'lignes': [
            {'nature': 'honoraires', 'libelle': 'Consultation (fictive)', 'quantite': '1', 'prix_unitaire_ht': ht}]}, '')
        d = fact.approve(self.desk, *AVOCAT, d['id'], d['version'])
        return fact.issue(self.desk, *AVOCAT, d['id'], 'emission-' + self.cle())

    def test_K19_01_facture_900_ttc_reglement_300_puis_600_echus(self):
        d = self.facture()
        self.assertEqual(d['snapshot']['totaux']['ttc_cents'], 90000)
        fact.record_payment(self.desk, *AVOCAT, d['id'], '300', date.today().isoformat(), 'virement', 'VIR-0001')
        i = hon.indicateurs(self.desk, AVOCAT, MID)
        self.assertEqual(i['facture_non_regle']['ttc']['montant'], '600.00')
        self.assertEqual(i['impayes_echus']['ttc']['montant'], '0.00')
        self.assertEqual(i['encaisse']['ttc']['montant'], '300.00')
        echeance = date.fromisoformat(d['snapshot']['dates']['echeance'])
        plus_tard = hon.indicateurs(self.desk, AVOCAT, MID, aujourdhui=echeance + timedelta(days=1), debut=date.today().replace(day=1).isoformat(),
                                    fin=(echeance + timedelta(days=1)).isoformat())
        self.assertEqual(plus_tard['impayes_echus']['ttc']['montant'], '600.00')
        self.assertEqual(plus_tard['facture_non_regle']['ttc']['montant'], '600.00')   # sous-total jamais additionné
        self.assertTrue(all(i[k]['definition'] for k in hon.DEFINITIONS))

    def test_K19_02_rejouer_le_paiement_memes_montants(self):
        d = self.facture()
        for _ in range(2):
            fact.record_payment(self.desk, *AVOCAT, d['id'], '300', date.today().isoformat(), 'virement', 'VIR-0001')
        self.assertEqual(hon.indicateurs(self.desk, AVOCAT, MID)['facture_non_regle']['ttc']['montant'], '600.00')

    def test_K19_03_avoir_et_reglement_total(self):
        d = self.facture()
        av = fact.credit_note(self.desk, *AVOCAT, d['id'], 'Geste commercial (fictif)', [{'nature': 'honoraires', 'libelle': 'Remise', 'quantite': '1', 'prix_unitaire_ht': '250'}])
        av = fact.approve(self.desk, *AVOCAT, av['id'], av['version'])
        fact.issue(self.desk, *AVOCAT, av['id'], 'avoir-' + self.cle())
        self.assertEqual(hon.solde_facture(self.desk, AVOCAT, d['id'])['solde']['montant'], '600.00')
        fact.record_payment(self.desk, *AVOCAT, d['id'], '600', date.today().isoformat(), 'virement', 'VIR-0002')
        i = hon.indicateurs(self.desk, AVOCAT, MID)
        self.assertEqual(i['facture_non_regle']['ttc']['montant'], '0.00')
        self.assertEqual(i['encaisse']['ttc']['montant'], '600.00')

    def source_externe(self):
        cur = fact.entity(self.desk)
        fact.save_entity(self.desk, *ADMIN, {'autorite': 'invoice_ninja', 'version': cur['version']})
        self.desk.db.execute('CREATE TABLE IF NOT EXISTS matter_invoice_links_v310(matter TEXT PRIMARY KEY, client_id TEXT NOT NULL, updated TEXT NOT NULL)')
        self.desk.db.execute("INSERT OR REPLACE INTO matter_invoice_links_v310 VALUES(?,?,?)", (MID, 'cli-durand', g.maintenant()))
        self.desk.db.commit()
        t = TransportFaux()
        t.liste_factures = [{'id': 'inv-1', 'number': 'NJ-0001', 'client_id': 'cli-durand', 'amount': '900.00', 'balance': '600.00', 'date': date.today().isoformat(),
                             'due_date': (date.today() + timedelta(days=30)).isoformat(), 'status': 'partial', 'currency': 'EUR'}]
        t.liste_reglements = [{'id': 'pay-1', 'amount': '300.00', 'date': date.today().isoformat(), 'invoices': [{'invoice_id': 'inv-1', 'amount': '300.00'}]},
                              {'id': 'pay-2', 'amount': '50.00', 'date': date.today().isoformat(), 'invoices': []}]
        return t

    def test_K19_04_invoice_ninja_desactive_par_defaut_et_essai_avant_activation(self):
        t = self.source_externe()
        self.assertFalse(hon.reglages(self.desk)['invoice_ninja']['actif'])
        with self.assertRaisesRegex(Stop, 'source_desactivee'):
            hon.synchroniser(self.desk, AVOCAT, t)
        with self.assertRaisesRegex(Stop, 'essai_source_requis'):
            hon.activer_source(self.desk, ADMIN, 'invoice_ninja', True)
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            hon.essayer_source(self.desk, AVOCAT, t)
        hon.essayer_source(self.desk, ADMIN, t)
        self.assertTrue(hon.activer_source(self.desk, ADMIN, 'invoice_ninja', True)['actif'])

    def test_K19_05_synchro_rejouee_sans_doublon_et_paiement_non_rapproche(self):
        t = self.source_externe()
        hon.essayer_source(self.desk, ADMIN, t)
        hon.activer_source(self.desk, ADMIN, 'invoice_ninja', True)
        premiere = hon.synchroniser(self.desk, AVOCAT, t)
        seconde = hon.synchroniser(self.desk, AVOCAT, t)
        self.assertEqual(premiere['lignes_modifiees'], 3)
        self.assertEqual(seconde['lignes_modifiees'], 0)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM fin_externes5697').fetchone()[0], 3)
        i = hon.indicateurs(self.desk, AVOCAT, MID)
        self.assertEqual(i['facture_non_regle']['ttc']['montant'], '600.00')
        self.assertEqual(i['encaisse']['ttc']['montant'], '300.00')
        self.assertEqual([x['id'] for x in i['encaisse']['non_rapproches']], ['pay-2'])
        self.assertTrue(i['sources']['verifiable'])

    def test_K19_06_panne_du_connecteur_projection_gardee_et_relances_suspendues(self):
        t = self.source_externe()
        hon.essayer_source(self.desk, ADMIN, t)
        hon.activer_source(self.desk, ADMIN, 'invoice_ninja', True)
        hon.synchroniser(self.desk, AVOCAT, t)
        self.assertEqual(hon.motif_suspension_relance(self.desk), '')
        t.panne = 'connexion_http_indisponible'
        etat = hon.synchroniser(self.desk, AVOCAT, t)
        self.assertFalse(etat['verifiable'])
        self.assertTrue(any('échec' in a for a in etat['anomalies']))
        self.assertEqual(hon.indicateurs(self.desk, AVOCAT, MID)['facture_non_regle']['ttc']['montant'], '600.00')   # pas de faux zéro
        self.assertIn('relances suspendues', hon.motif_suspension_relance(self.desk))
        relances5636.schema(self.desk)
        self.desk.db.execute("INSERT OR REPLACE INTO invoice_ninja_cache_v310(id,client_id,number,status,amount,balance,due_date,invoice_url,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                             ('inv-1', 'cli-durand', 'NJ-0001', 'sent', 900.0, 600.0, (date.today() - timedelta(days=40)).isoformat(), '', g.maintenant()))
        self.desk.db.commit()
        self.assertEqual(relances5636.prepare(self.desk, refresh=False)['created'], 0)

    def test_K19_07_forfait_sans_doublon_de_diligences(self):
        time500.add_manual(self.desk, MID, date.today().isoformat(), 120, 'Analyse du contrat (fictive)', '200', AVOCAT[0])
        hon.saisir_frais(self.desk, AVOCAT, MID, {'cle': self.cle(), 'nature': 'frais', 'montant_ht': '40', 'libelle': 'Frais de greffe (fictifs)', 'jour': date.today().isoformat()})
        j = hon.ajouter_jalon(self.desk, AVOCAT, MID, {'cle': self.cle(), 'libelle': 'Remise du projet de CGV', 'montant_ht': '1000'})
        hon.jalon_atteint(self.desk, AVOCAT, j['id'], j['revision'])
        i = hon.indicateurs(self.desk, AVOCAT, MID)
        self.assertEqual(i['travail_a_facturer']['ht']['montant'], '1440.00')
        b = hon.preparer_brouillon(self.desk, AVOCAT, MID, {'cle': 'brouillon-' + self.cle()})
        self.assertEqual(b['state'], 'brouillon')
        self.assertEqual(hon.indicateurs(self.desk, AVOCAT, MID)['travail_a_facturer']['ht']['montant'], '0.00')   # brouillon : pas recompté
        with self.assertRaisesRegex(Stop, 'rien_a_facturer'):
            hon.preparer_brouillon(self.desk, AVOCAT, MID, {'cle': 'brouillon-' + self.cle()})
        f = hon.forfait(self.desk, AVOCAT, MID)
        self.assertEqual(f['heures_validees'], '2.00')
        self.assertIn('pas un supplément', f['mention'])

    def test_K19_08_emission_externe_reponse_perdue_relue_avant_rejeu(self):
        t = self.source_externe()
        hon.essayer_source(self.desk, ADMIN, t)
        hon.activer_source(self.desk, ADMIN, 'invoice_ninja', True)
        d = fact.prepare(self.desk, *AVOCAT, 'facture', MID, {'sous_type': 'libre', 'lignes': [{'nature': 'honoraires', 'libelle': 'Forfait', 'quantite': '1', 'prix_unitaire_ht': '750'}]}, '')
        d = fact.approve(self.desk, *AVOCAT, d['id'], d['version'])
        t.perdre = True
        cle = self.cle()
        first = hon.emettre_externe(self.desk, AVOCAT, d['id'], cle, t)
        self.assertEqual(first['etat'], 'inconnue')
        again = hon.emettre_externe(self.desk, AVOCAT, d['id'], cle, t)
        self.assertEqual(again['etat'], 'confirmee')
        self.assertEqual(len(t.creees), 1)                                          # aucune seconde facture
        self.assertEqual(hon.emettre_externe(self.desk, AVOCAT, d['id'], cle, t)['id_externe'], 'inv-1')

    def test_K19_09_droits_appliques_avant_les_totaux(self):
        fact.save_client(self.desk, *AVOCAT, MID2, {**CLIENT, 'nom': 'MARTIN SA (fictive)', 'siren': '000010033', 'email': 'martin@example.test'})
        self.facture()
        self.facture('1500', MID2)
        self.assertEqual(hon.indicateurs(self.desk, AVOCAT)['facture_non_regle']['ttc']['montant'], '2700.00')
        self.assertEqual(hon.indicateurs(self.desk, AUTRE)['facture_non_regle']['ttc']['montant'], '900.00')   # dossier réservé exclu avant le total
        with self.assertRaisesRegex(Stop, '^introuvable$'):
            hon.indicateurs(self.desk, AUTRE, MID2)
        membres5650.set_rights(self.f.c['state_dir'], ADMIN[0], ASSISTANT[0], 'assistant', {'voir_honoraires': False})
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            hon.indicateurs(self.desk, ASSISTANT, MID)


# =========================================================================================== § 20
class Pieces(Base):
    def setUp(self):
        super().setUp()
        rem.enregistrer_reglages(self.desk, ADMIN, {'actif': True})

    def demande(self):
        return rem.demander(self.desk, AVOCAT, MID, {'elements': ['Statuts à jour', 'Kbis de moins de trois mois'], 'destinataire': CLIENT_MAIL,
                                                     'revoir_le': (date.today() + timedelta(days=7)).isoformat()})

    def test_K20_01_demande_destinataire_verifie_et_etats(self):
        d = self.demande()
        self.assertEqual(d['destinataire_verifie'], 'correspondant client du dossier')
        self.assertEqual({e['etat'] for e in d['elements']}, {'a_recevoir'})
        with self.assertRaisesRegex(Stop, 'destinataire_non_verifie'):
            rem.demander(self.desk, AVOCAT, MID, {'elements': ['RIB'], 'destinataire': 'inconnu@example.test', 'revoir_le': date.today().isoformat()})
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            rem.demander(self.desk, ASSISTANT, MID, {'elements': ['RIB'], 'destinataire': 'inconnu@example.test', 'revoir_le': date.today().isoformat(),
                                                    'confirmer_destinataire': True})
        relance = rem.vue_demande(self.desk, AVOCAT, d['id'], aujourdhui=date.today() + timedelta(days=8))
        self.assertTrue(relance['a_relancer'])

    def test_K20_02_reception_controlee_sans_ecraser_l_original(self):
        d = self.demande()
        item = d['elements'][0]['item']
        with self.assertRaisesRegex(Stop, 'type_fichier_refuse'):
            rem.recevoir(self.desk, AVOCAT, d['id'], item, {'cle': self.cle(), 'nom': 'statuts.exe', 'contenu_b64': base64.b64encode(b'MZ...').decode()})
        with self.assertRaisesRegex(Stop, 'type_fichier_refuse'):
            rem.recevoir(self.desk, AVOCAT, d['id'], item, {'cle': self.cle(), 'nom': 'statuts.pdf', 'contenu_b64': base64.b64encode(b'MZ deguise').decode()})
        with self.assertRaisesRegex(Stop, '^introuvable$'):                      # rôle inconnu : refus neutre
            rem.recevoir(self.desk, ('x@example.test', 'inconnu'), d['id'], item, {'cle': self.cle(), 'nom': 'statuts.pdf', 'contenu_b64': base64.b64encode(b'%PDF').decode()})
        v1 = rem.recevoir(self.desk, AVOCAT, d['id'], item, {'cle': self.cle(), 'nom': 'statuts.pdf', 'contenu_b64': base64.b64encode(b'%PDF statuts v1').decode()})
        e1 = next(e for e in v1['elements'] if e['item'] == item)
        self.assertEqual(e1['etat'], 'recu_a_verifier')
        rem.qualifier(self.desk, AVOCAT, d['id'], item, 'a_remplacer')
        v2 = rem.recevoir(self.desk, AVOCAT, d['id'], item, {'cle': self.cle(), 'nom': 'statuts.pdf', 'contenu_b64': base64.b64encode(b'%PDF statuts v2').decode()})
        e2 = next(e for e in v2['elements'] if e['item'] == item)
        self.assertNotEqual(e1['reception']['path'], e2['reception']['path'])
        self.assertEqual((self.root / e1['reception']['path'].lstrip('/')).read_bytes(), b'%PDF statuts v1')   # original jamais écrasé
        v3 = rem.qualifier(self.desk, AVOCAT, d['id'], item, 'accepter')
        self.assertEqual(next(e for e in v3['elements'] if e['item'] == item)['etat'], 'accepte')
        v4 = rem.qualifier(self.desk, AVOCAT, d['id'], d['elements'][1]['item'], 'dispenser')
        self.assertEqual(next(e for e in v4['elements'] if e['item'] == d['elements'][1]['item'])['etat'], 'dispense')

    def remise(self, **extra):
        doc = self.fichier('Dossiers/DURAND/Compte rendu au client.docx', b'PK compte rendu v1')
        data = {'cle': self.cle(), 'canal': 'nextcloud', 'destinataire': {'type': 'utilisateur', 'id': 'durand.client'}, 'identite_verifiee': True,
                'fichiers': [{'path': doc, 'nature': 'compte_rendu'}], **extra}
        return rem.remettre(self.desk, AVOCAT, MID, data, ocs=rem.PartagesOCS(self.ocs)), doc

    def test_K20_03_remise_lecture_seule_sur_la_copie_version_figee(self):
        self.fichier('Dossiers/DURAND/Notes internes/strategie.docx', b'PK strategie')
        r, doc = self.remise()
        self.assertEqual(r['etat'], 'partage_actif')
        partage = self.ocs.partages[r['share_id']]
        self.assertEqual(partage['permissions'], 1)
        self.assertEqual(partage['path'], r['copie_dossier'])
        self.assertTrue(r['copie_dossier'].startswith('/Dossiers/DURAND/REMISES/'))
        self.assertNotIn('/Dossiers/DURAND', [s['path'] for s in self.ocs.partages.values()])   # jamais le dossier parent
        self.assertEqual(self.ocs.vue_destinataire(r['share_id']), ['01 Compte rendu au client.docx'])   # aucune note interne
        (self.root / doc.lstrip('/')).write_bytes(b'PK compte rendu v2 interne')
        raw, _ = rem.ouvrir_version_remise(self.desk, AVOCAT, r['id'], 1)
        self.assertEqual(raw, b'PK compte rendu v1')                              # la version remise ne change pas
        with self.assertRaisesRegex(Stop, 'document_interne_non_remissible'):
            rem.remettre(self.desk, AVOCAT, MID, {'cle': self.cle(), 'canal': 'courriel', 'destinataire': {'type': 'courriel', 'id': CLIENT_MAIL},
                                                  'fichiers': [{'path': '/Dossiers/DURAND/Notes internes/strategie.docx'}]}, ocs=rem.PartagesOCS(self.ocs))

    def test_K20_04_lien_cree_transmission_declaree_et_acces_constate_distincts(self):
        r, _ = self.remise()
        self.assertEqual(r['etat_libelle'], 'Lien créé : transmission non déclarée')
        t = rem.declarer_transmission(self.desk, AVOCAT, r['id'], {'version': r['revision'], 'preuve': 'Courriel envoyé au client avec le lien (fictif)'})
        self.assertEqual(t['etat'], 'transmise')
        a = rem.constater_acces(self.desk, AVOCAT, r['id'], {'version': t['revision'], 'constat': 'Le client confirme avoir ouvert le compte rendu'})
        self.assertEqual(a['etat'], 'acces_constate')

    def test_K20_05_revocation_verifiee_et_panne_honnete(self):
        r, _ = self.remise()
        self.ocs.ignorer_suppression = True
        v = rem.revoquer(self.desk, AVOCAT, r['id'], rem.PartagesOCS(self.ocs))
        self.assertEqual(v['etat'], 'revocation_demandee')                        # Nextcloud n'a pas confirmé : jamais « révoqué »
        self.ocs.ignorer_suppression, self.ocs.panne = False, 'connexion_http_indisponible'
        v = rem.revoquer(self.desk, AVOCAT, r['id'], rem.PartagesOCS(self.ocs))
        self.assertEqual(v['etat'], 'revocation_demandee')
        self.assertEqual(v['erreur'], 'connexion_http_indisponible')
        self.ocs.panne = None
        v = rem.revoquer(self.desk, AVOCAT, r['id'], rem.PartagesOCS(self.ocs))
        self.assertEqual(v['etat'], 'acces_revoque')
        self.assertIsNone(self.ocs.vue_destinataire(r['share_id']))                 # contrôle côté destinataire

    def test_K20_06_expiration_constatee_cote_destinataire(self):
        r, _ = self.remise(expiration=(date.today() + timedelta(days=2)).isoformat())
        self.assertIsNone(self.ocs.vue_destinataire(r['share_id'], aujourdhui=date.today() + timedelta(days=3)))

    def test_K20_07_depot_seul_ne_revele_pas_les_autres_fichiers(self):
        rem.enregistrer_reglages(self.desk, ADMIN, {'lien_public': True})
        self.fichier('Dossiers/DURAND/Note interne.docx', b'PK note')
        out = rem.ouvrir_depot_client(self.desk, AVOCAT, MID, {'cle': self.cle(), 'destinataire': {'id': CLIENT_MAIL}}, ocs=rem.PartagesOCS(self.ocs))
        self.assertTrue(out['mot_de_passe_a_communiquer'])
        partage = self.ocs.partages[out['share_id']]
        self.assertEqual((partage['permissions'], partage['share_type']), (4, 3))
        self.assertTrue(partage['path'].startswith('/Dossiers/DURAND/DEPOT CLIENT/'))
        self.assertTrue(partage['expiration'] and partage['password'])
        self.assertEqual(self.ocs.vue_destinataire(out['share_id']), [])

    def test_K20_08_partage_desactive_par_defaut_et_lien_public_non_autorise(self):
        rem.enregistrer_reglages(self.desk, ADMIN, {'actif': False})
        with self.assertRaisesRegex(Stop, 'partage_nextcloud_desactive'):
            self.remise()
        self.assertEqual(rem.DEFAUTS['actif'], False)
        rem.enregistrer_reglages(self.desk, ADMIN, {'actif': True})
        with self.assertRaisesRegex(Stop, 'lien_public_non_autorise'):
            self.remise(destinataire={'type': 'lien', 'id': CLIENT_MAIL})

    def test_K20_09_reponse_perdue_relue_avant_rejeu_un_seul_partage(self):
        self.ocs.perdre_reponse = True
        doc = self.fichier('Dossiers/DURAND/Compte rendu.docx', b'PK cr')
        data = {'cle': self.cle(), 'canal': 'nextcloud', 'destinataire': {'type': 'utilisateur', 'id': 'durand.client'}, 'identite_verifiee': True, 'fichiers': [{'path': doc}]}
        r1 = rem.remettre(self.desk, AVOCAT, MID, data, ocs=rem.PartagesOCS(self.ocs))
        self.assertEqual(r1['etat'], 'partage_a_verifier')
        r2 = rem.remettre(self.desk, AVOCAT, MID, data, ocs=rem.PartagesOCS(self.ocs))
        self.assertEqual(r2['etat'], 'partage_actif')
        self.assertEqual(len(self.ocs.partages), 1)

    def test_K20_11_lire_demande_pour_le_portefeuille_garde_et_etat(self):
        d = self.demande()
        lu = g.lire_demande(self.desk, AVOCAT, d['id'])
        self.assertEqual((lu['matter'], lu['etat'], lu['echeance']), (MID, 'ouverte', d['revoir_le']))
        for it in d['elements']:
            rem.qualifier(self.desk, AVOCAT, d['id'], it['item'], 'dispenser')
        self.assertEqual(g.lire_demande(self.desk, AVOCAT, d['id'])['etat'], 'close')
        membres5650.set_reserve(self.f.c['state_dir'], MID, [AVOCAT[0]])
        with self.assertRaisesRegex(Stop, '^introuvable$'):
            g.lire_demande(self.desk, AUTRE, d['id'])
        with self.assertRaisesRegex(Stop, '^introuvable$'):
            g.lire_demande(self.desk, AVOCAT, 'req-inexistante')

    def test_K20_10_lien_et_jeton_absents_des_journaux_et_exports(self):
        from agent import export5688
        r, _ = self.remise()
        self.assertNotIn('jeton', json.dumps(g.historique(self.desk, AVOCAT, MID)))
        self.assertTrue(export5688._secrete('url_jeton'))
        self.assertNotIn('lien', rem.vue_remise(self.desk, ASSISTANT, r['id']))     # un assistant ne lit pas le lien
        self.assertIn('lien', rem.vue_remise(self.desk, AVOCAT, r['id']))


# =========================================================================================== § 21
class Cloture(Base):
    def setUp(self):
        super().setUp()
        fact.save_entity(self.desk, *ADMIN, ENTITE)
        fact.save_client(self.desk, *AVOCAT, MID, CLIENT)
        rem.enregistrer_reglages(self.desk, ADMIN, {'actif': True})

    def test_K21_01_cloture_bloquee_par_une_echeance_ouverte(self):
        self.echeance_confirmee()
        prep = cloture5697.preparer(self.desk, AVOCAT, MID)
        self.assertEqual(prep['etat'], 'actif')                                      # le dossier reste actif pendant le contrôle
        with self.assertRaises(Stop) as ctx:
            cloture5697.cloturer(self.desk, AVOCAT, MID, {'cle': self.cle(), 'version': prep['version'], 'motif': 'Mission terminée',
                                                          'decisions': {'information_client': 'Client informé oralement (fictif)'}})
        self.assertEqual(str(ctx.exception), 'cloture_bloquee')
        self.assertIn('echeances', [p['id'] for p in ctx.exception.detail])
        self.assertFalse(cloture5697.est_archive(self.desk, MID))

    def test_K21_02_controles_recalcules_cote_serveur_et_version(self):
        prep = cloture5697.preparer(self.desk, AVOCAT, MID)
        self.desk.db.execute("INSERT INTO tasks(id,matter,title,due,status,created) VALUES('t-1',?,'Relire les CGV','', 'open', ?)", (MID, g.maintenant()))
        self.desk.db.commit()
        with self.assertRaisesRegex(Stop, 'cloture_bloquee'):                     # la vue d'avant ne compte pas : contrôles refaits
            cloture5697.cloturer(self.desk, AVOCAT, MID, {'cle': self.cle(), 'version': prep['version'], 'motif': 'Fin', 'decisions': {'information_client': 'Oral (fictif)'}})
        with self.assertRaisesRegex(Stop, 'version_en_conflit'):
            cloture5697.cloturer(self.desk, AVOCAT, MID, {'cle': self.cle(), 'version': prep['version'] + 5, 'motif': 'Fin'})
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            cloture5697.cloturer(self.desk, ASSISTANT, MID, {'cle': self.cle(), 'version': prep['version'], 'motif': 'Fin'})

    def test_K21_03_archiver_retrouver_l_impaye_puis_rouvrir_sans_effet_externe(self):
        d = fact.prepare(self.desk, *AVOCAT, 'facture', MID, {'sous_type': 'libre', 'lignes': [{'nature': 'honoraires', 'libelle': 'Solde', 'quantite': '1', 'prix_unitaire_ht': '750'}]}, '')
        d = fact.issue(self.desk, *AVOCAT, fact.approve(self.desk, *AVOCAT, d['id'], d['version'])['id'], 'emission-' + self.cle())
        doc = self.fichier('Dossiers/DURAND/CR final.docx', b'PK cr final')
        r = rem.remettre(self.desk, AVOCAT, MID, {'cle': self.cle(), 'canal': 'nextcloud', 'destinataire': {'type': 'utilisateur', 'id': 'durand.client'}, 'identite_verifiee': True,
                                                  'fichiers': [{'path': doc}]}, ocs=rem.PartagesOCS(self.ocs))
        r = rem.declarer_transmission(self.desk, AVOCAT, r['id'], {'version': r['revision'], 'preuve': 'Courriel au client (fictif)'})
        prep = cloture5697.preparer(self.desk, AVOCAT, MID)
        self.assertEqual({c['id'] for c in prep['controles'] if c['etat'] == 'bloquant'}, {'factures_non_reglees', 'partages'})
        rem.revoquer(self.desk, AVOCAT, r['id'], rem.PartagesOCS(self.ocs))
        out = cloture5697.cloturer(self.desk, AVOCAT, MID, {'cle': self.cle(), 'version': prep['version'], 'motif': 'Mission terminée',
                                                            'suivi_financier': {'responsable': AVOCAT[0], 'prochaine_date': (date.today() + timedelta(days=15)).isoformat()}})
        self.assertEqual(out['etat'], 'archive')
        self.assertTrue(pause5629.is_paused(self.desk, MID))                       # automatismes de production suspendus
        self.assertEqual(hon.indicateurs(self.desk, AVOCAT, MID)['facture_non_regle']['ttc']['montant'], '900.00')   # impayé retrouvé
        with self.assertRaisesRegex(Stop, '^introuvable$'):
            cloture5697.etat(self.desk, ('intrus@example.test', 'inconnu'), MID)
        appels = len(self.ocs.appels)
        with self.assertRaisesRegex(Stop, 'motif_obligatoire'):
            cloture5697.rouvrir(self.desk, AVOCAT, MID, {'cle': self.cle(), 'version': out['version'], 'motif': ''})
        ro = cloture5697.rouvrir(self.desk, AVOCAT, MID, {'cle': self.cle(), 'version': out['version'], 'motif': 'Nouvelle demande du client (fictive)'})
        self.assertEqual(ro['etat'], 'actif')
        self.assertEqual(len(self.ocs.appels), appels)                               # aucun partage recréé
        self.assertEqual(rem.vue_remise(self.desk, AVOCAT, r['id'])['etat'], 'acces_revoque')
        self.assertEqual([h['decision'] for h in ro['historique']], ['reouverture', 'cloture'])

    def test_K21_04_entree_sur_dossier_archive_signalee_sans_rouvrir(self):
        prep = cloture5697.preparer(self.desk, AVOCAT, MID)
        cloture5697.cloturer(self.desk, AVOCAT, MID, {'cle': self.cle(), 'version': prep['version'], 'motif': 'Fin', 'decisions': {'information_client': 'Client informé (fictif)'}})
        self.assertTrue(cloture5697.signaler_entree(self.desk, MID, 'courriel', '<message-1@example.test>'))
        self.assertTrue(cloture5697.est_archive(self.desk, MID))
        self.assertEqual(len(cloture5697.etat(self.desk, AVOCAT, MID)['entrees_signalees']), 1)

    def dossier_complet(self):
        """Dossier fictif : échéance sourcée sur un fichier, paquet gelé avec accusé rapproché, pièce reçue, remise, archivage."""
        self.fichier('Dossiers/DURAND/jugement.pdf', b'%PDF jugement fictif')
        e = proc.saisir_echeance(self.desk, AVOCAT, MID, {'regle': 'appel_jugement_contentieux', 'depart': (date.today() - timedelta(days=5)).isoformat(),
                                                           'source': '/Dossiers/DURAND/jugement.pdf', 'depart_certain': True}, self.agenda)
        proc.decider(self.desk, AVOCAT, e['id'], {'action': 'accomplir', 'preuve': 'Appel formé (fictif)', 'cle': self.cle(), 'version': echeances450._row(self.desk, e['id'])['revision']})
        acte = self.fichier('Dossiers/DURAND/Declaration.docx', b'PK declaration')
        pq = proc.preparer_paquet(self.desk, AVOCAT, MID, {'cle': self.cle(), 'canal': 'ebarreau', 'reference_affaire': 'RG 24/01234', 'acte': {'path': acte}})
        pq = proc.declarer_depot(self.desk, AVOCAT, pq['id'], {'version': pq['revision'], 'envoi_ref': 'ENV-0001', 'date_depot': date.today().isoformat()})
        proc.importer_accuse(self.desk, AVOCAT, pq['id'], {'contenu_b64': base64.b64encode(b'%PDF AR').decode(), 'reference': 'RG 24/01234', 'envoi_ref': 'ENV-0001',
                                                           'date': date.today().isoformat()})
        doc = self.fichier('Dossiers/DURAND/CR.docx', b'PK cr')
        r = rem.remettre(self.desk, AVOCAT, MID, {'cle': self.cle(), 'canal': 'courriel', 'destinataire': {'type': 'courriel', 'id': CLIENT_MAIL}, 'fichiers': [{'path': doc}]})
        rem.declarer_transmission(self.desk, AVOCAT, r['id'], {'version': r['revision'], 'preuve': 'Courriel (fictif)'})
        prep = cloture5697.preparer(self.desk, AVOCAT, MID)
        cloture5697.cloturer(self.desk, AVOCAT, MID, {'cle': self.cle(), 'version': prep['version'], 'motif': 'Fin de mission'})

    def test_K21_05_restauration_d_un_dossier_fictif_complet_dans_une_instance_isolee(self):
        self.dossier_complet()
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            cloture5697.paquet_restauration(self.desk, AVOCAT, MID)
        out = cloture5697.paquet_restauration(self.desk, ADMIN, MID)
        self.assertTrue(any(x['nature'] for x in out['references_seulement']) or out['references_seulement'] == [])
        cible = self.root.parent / (self.root.name + '-instance')
        self.addCleanup(shutil.rmtree, cible, True)
        res = cloture5697.verifier_restauration(out['chemin'], cible / 'a', MID, droits_attendus=[])
        self.assertTrue(res['ok'], res['anomalies'])
        self.assertGreaterEqual(res['liens_verifies'], 4)
        self.assertGreaterEqual(res['fichiers_restaures'], 4)
        # Un fichier absent fait échouer la recette
        abime = Path(out['chemin']).with_name('abime.zip')
        with zipfile.ZipFile(out['chemin']) as src, zipfile.ZipFile(abime, 'w') as dst:
            for info in src.infolist():
                if not info.filename.endswith('jugement.pdf'):
                    dst.writestr(info, src.read(info.filename))
        res = cloture5697.verifier_restauration(abime, cible / 'b', MID)
        self.assertFalse(res['ok'])
        self.assertTrue(any('lien cassé' in a['motif'] or 'manquant' in a['motif'] or 'absent' in a['motif'] for a in res['anomalies']))

    def test_K21_06_lien_casse_fait_echouer_la_restauration(self):
        self.dossier_complet()
        self.desk.db.execute("UPDATE deadlines450 SET source_path='/Dossiers/DURAND/disparu.pdf' WHERE matter=?", (MID,))
        self.desk.db.commit()
        out = cloture5697.paquet_restauration(self.desk, ADMIN, MID)
        cible = self.root.parent / (self.root.name + '-instance2')
        self.addCleanup(shutil.rmtree, cible, True)
        res = cloture5697.verifier_restauration(out['chemin'], cible, MID)
        self.assertFalse(res['ok'])
        self.assertTrue(any('disparu.pdf' in a['motif'] for a in res['anomalies']))

    def test_K21_07_paquet_de_restauration_retention(self):
        for _ in range(4):
            cloture5697.paquet_restauration(self.desk, ADMIN, MID)
        self.assertLessEqual(len(list((Path(self.f.c['state_dir']) / cloture5697.DOSSIER_RESTAURATION).glob('*.zip'))), cloture5697.CONSERVES + 1)
        cloture5697.purger(self.desk, maintenant=__import__('datetime').datetime.now(__import__('datetime').timezone.utc) + timedelta(days=2))
        self.assertEqual(list((Path(self.f.c['state_dir']) / cloture5697.DOSSIER_RESTAURATION).glob('*.zip')), [])


# =========================================================================================== commandes sans IA
class SansIA(Base):
    def test_KIA_commandes_de_gestion_disponibles_ia_en_pause_et_fournisseurs_bloques(self):
        fact.save_entity(self.desk, *ADMIN, ENTITE)
        fact.save_client(self.desk, *AVOCAT, MID, CLIENT)
        rem.enregistrer_reglages(self.desk, ADMIN, {'actif': True})
        self.desk.setting('missions567:pause', True)                                # pause globale de l'IA
        avant = self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        with patch('agent.model.Model', side_effect=AssertionError('modèle instancié')), \
                patch('agent.ai_gateway.common_completion', side_effect=AssertionError('fournisseur IA appelé')):
            e = self.echeance_confirmee()
            proc.decider(self.desk, AVOCAT, e['id'], {'action': 'accomplir', 'preuve': 'Acte accompli (fictif)', 'cle': self.cle(), 'version': echeances450._row(self.desk, e['id'])['revision']})
            acte = self.fichier('Dossiers/DURAND/Acte.docx', b'PK acte')
            proc.preparer_paquet(self.desk, AVOCAT, MID, {'cle': self.cle(), 'canal': 'ebarreau', 'acte': {'path': acte}})
            time500.add_manual(self.desk, MID, date.today().isoformat(), 60, 'Rendez-vous (fictif)', '200', AVOCAT[0])
            b = hon.preparer_brouillon(self.desk, AVOCAT, MID, {'cle': 'brouillon-' + self.cle()})
            b = fact.issue(self.desk, *AVOCAT, fact.approve(self.desk, *AVOCAT, b['id'], b['version'])['id'], 'emission-' + self.cle())
            fact.record_payment(self.desk, *AVOCAT, b['id'], '240', date.today().isoformat(), 'virement', 'VIR-IA-0001')
            rem.demander(self.desk, AVOCAT, MID, {'elements': ['Kbis'], 'destinataire': CLIENT_MAIL, 'revoir_le': date.today().isoformat()})
            prep = cloture5697.preparer(self.desk, AVOCAT, MID)
            cloture5697.cloturer(self.desk, AVOCAT, MID, {'cle': self.cle(), 'version': prep['version'], 'motif': 'Fin',
                                                          'decisions': {'information_client': 'Client informé (fictif)'}})
        self.assertTrue(cloture5697.est_archive(self.desk, MID))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], avant)   # aucun travail IA démarré


# =========================================================================================== routes
class Routes(Base):
    def api(self, name, args=None, body=None, who=AVOCAT):
        import io
        from agent import web5689
        raw = json.dumps(body or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': who[0], 'HTTP_X_AXIORHUB_ROLE': who[1], 'HTTP_ORIGIN': 'https://cabinet.example.test', 'HTTP_X_CSRF_TOKEN': 'jeton',
               'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw), 'REQUEST_METHOD': 'POST' if body is not None else 'GET'}
        out = web5689.api(env, self.desk, {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}, '/p', 'm5689/' + name, args or {}, 'POST' if body is not None else 'GET')
        return out['status'], json.loads(out['body']) if out['kind'].startswith('application/json') else out['body']

    def test_K21_08_routes_404_neutre_403_et_409_cloture_bloquee(self):
        from agent import membres5650 as mb, web5689
        self.assertEqual(self.api('gestion/procedure', {'dossier': MID2}, who=AUTRE)[0], '404 Not Found')
        self.assertEqual(self.api('gestion/procedure', {'dossier': 'DOS-999'}, who=AUTRE)[0], '404 Not Found')
        self.assertEqual(self.api('gestion/cloture/cloturer', body={'dossier': MID, 'cle': self.cle(), 'version': 1, 'motif': 'Fin'}, who=ASSISTANT)[0], '403 Forbidden')
        self.echeance_confirmee()
        statut, corps = self.api('gestion/cloture/cloturer', body={'dossier': MID, 'cle': self.cle(), 'version': 1, 'motif': 'Fin'})
        self.assertEqual(statut, '409 Conflict')
        self.assertIn('echeances', [p['id'] for p in corps['points']])
        self.assertEqual(self.api('gestion/restauration/paquet', body={'dossier': MID}, who=AVOCAT)[0], '403 Forbidden')
        self.assertTrue(web5689.allowed('avocat', 'gestion/honoraires'))
        self.assertFalse(web5689.allowed('assistant', 'gestion/remises/remettre'))
        self.assertEqual(mb.category('POST', '/api440/m5689/gestion/remises/remettre'), 'envoyer')
        self.assertEqual(mb.category('POST', '/api440/m5689/gestion/honoraires/brouillon'), 'facturation')
        self.assertIn('voir_honoraires', mb.fine_rights('GET', '/api440/m5689/gestion/honoraires'))
        self.assertEqual(mb.category('POST', '/api440/m5689/gestion/reglages/remises'), 'reglages')
        statut, corps = self.api('gestion/honoraires', {'dossier': MID})
        self.assertEqual(statut, '200 OK')
        self.assertEqual(set(hon.DEFINITIONS) <= set(corps), True)


class Sections(Base):
    def test_K21_09_sections_de_la_fiche_rendues_echappees_et_neutres(self):
        from agent import gestion_ui5697 as ui
        fact.save_entity(self.desk, *ADMIN, ENTITE)
        self.echeance_confirmee()
        rem.enregistrer_reglages(self.desk, ADMIN, {'actif': True})
        rem.demander(self.desk, AVOCAT, MID, {'elements': ['<script>x</script> Kbis'], 'destinataire': CLIENT_MAIL, 'revoir_le': date.today().isoformat()})
        for f in (ui.procedure_html, ui.gestion_html, ui.correspondances_html, ui.cloture_html):
            html = f(self.desk, AVOCAT, '/p', MID)
            self.assertNotIn('<script', html)
            self.assertIn('data-api="m5689/gestion/', html)
            self.assertEqual(f(self.desk, AUTRE, '/p', MID2), ui._neutre())
        self.assertIn('&lt;script&gt;', ui.correspondances_html(self.desk, AVOCAT, '/p', MID))
        self.assertIn('Bloquant', ui.cloture_html(self.desk, AVOCAT, '/p', MID))


# =========================================================================================== mutations des garde-fous
class Mutations(Base):
    """Chaque garde-fou retiré en mémoire (patch) laisse passer le défaut que son essai refuse : l'essai le protège réellement."""

    def test_KM_01_sans_le_controle_de_l_envoi_un_accuse_etranger_serait_rapproche(self):
        acte = self.fichier('Dossiers/DURAND/A.docx', b'PK a')
        pq = proc.preparer_paquet(self.desk, AVOCAT, MID, {'cle': self.cle(), 'canal': 'ebarreau', 'reference_affaire': 'RG 24/01234', 'acte': {'path': acte}})
        pq = proc.declarer_depot(self.desk, AVOCAT, pq['id'], {'version': pq['revision'], 'envoi_ref': 'ENV-1', 'date_depot': date.today().isoformat()})
        with patch.object(proc, '_norme', lambda v: 'X'):
            out = proc.importer_accuse(self.desk, AVOCAT, pq['id'], {'contenu_b64': base64.b64encode(b'%PDF autre').decode(), 'reference': 'RG 24/01234',
                                                                     'envoi_ref': 'ENV-AUTRE', 'date': date.today().isoformat()})
        self.assertTrue(out['rapproche'])                                          # le garde-fou retiré, le défaut passe

    def test_KM_02_sans_la_garde_commune_un_dossier_reserve_serait_lu(self):
        with patch('agent.acces5697.exiger', lambda *a, **k: None):
            self.assertTrue(proc.registre(self.desk, AUTRE, MID2))

    def test_KM_03_sans_la_reservation_un_temps_serait_facture_deux_fois(self):
        fact.save_entity(self.desk, *ADMIN, ENTITE)
        fact.save_client(self.desk, *AVOCAT, MID, CLIENT)
        time500.add_manual(self.desk, MID, date.today().isoformat(), 60, 'Diligence (fictive)', '200', AVOCAT[0])
        with patch.object(fact, '_reserve', lambda *a, **k: None):
            hon.preparer_brouillon(self.desk, AVOCAT, MID, {'cle': 'brouillon-' + self.cle()})
            second = hon.preparer_brouillon(self.desk, AVOCAT, MID, {'cle': 'brouillon-' + self.cle()})
        self.assertEqual(second['state'], 'brouillon')                             # doublon de diligence sans la réservation

    def test_KM_04_sans_la_relecture_une_revocation_serait_annoncee_a_tort(self):
        rem.enregistrer_reglages(self.desk, ADMIN, {'actif': True})
        doc = self.fichier('Dossiers/DURAND/CR.docx', b'PK cr')
        r = rem.remettre(self.desk, AVOCAT, MID, {'cle': self.cle(), 'canal': 'nextcloud', 'destinataire': {'type': 'utilisateur', 'id': 'durand.client'}, 'identite_verifiee': True,
                                                  'fichiers': [{'path': doc}]}, ocs=rem.PartagesOCS(self.ocs))
        self.ocs.ignorer_suppression = True
        with patch.object(rem.PartagesOCS, 'lire', lambda self, ident: None):
            v = rem.revoquer(self.desk, AVOCAT, r['id'], rem.PartagesOCS(self.ocs))
        self.assertEqual(v['etat'], 'acces_revoque')
        self.assertIsNotNone(self.ocs.vue_destinataire(r['share_id']))             # … alors que le destinataire voit encore le partage

    def test_KM_05_sans_le_recalcul_serveur_la_cloture_passerait_avec_une_echeance_ouverte(self):
        self.echeance_confirmee()
        prep = cloture5697.preparer(self.desk, AVOCAT, MID)
        with patch.object(cloture5697, 'controles', lambda *a, **k: []):
            out = cloture5697.cloturer(self.desk, AVOCAT, MID, {'cle': self.cle(), 'version': prep['version'], 'motif': 'Fin'})
        self.assertEqual(out['etat'], 'archive')


if __name__ == '__main__':
    unittest.main()
