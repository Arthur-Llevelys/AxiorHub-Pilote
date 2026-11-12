"""5.6.97, lot gestion : recettes complètes § 22 (conseil : refonte de CGV, de la demande au règlement) et § 23 (contentieux : de l'acte reçu au
compte rendu client), exécutées par les services métier des lots, l'IA EN PAUSE avant le premier geste.

Preuves de recette : fournisseurs IA bloqués (modèle et passerelle commune : tout appel fait échouer l'essai), connexions réseau interdites,
transports espionnés (Nextcloud OCS et agenda simulés), file des travaux comptée : zéro appel modèle, zéro travail IA démarré. Les écritures
externes passent par des connecteurs de test (partage OCS simulé, agenda simulé) ; signature, paiement et accusé sont fictifs.
Ce qui est exercé ici est le chemin des services ; le parcours dans l'interface (navigateur) reste à faire à l'intégration (P97k).

Identifiants : R22_1 à R22_6 et R22_N (tests négatifs), R23_1 à R23_5, R23_P (levée de la pause sans rejeu).
Données fictives : DURAND, MARTIN, KERVILAC, ORLANDEC, RG 24/01234, Valbrune, example.test, SIREN d'essai de la liste.
"""
import base64
from datetime import date, timedelta
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from agent import acces5697, cloture5697, documents5697 as docs, dossiers5697, echeances450, export5688, facturation5653 as fact
from agent import honoraires5697 as hon, membres5650, mission5697 as mis, portefeuille5697 as pf, procedure5697 as proc, relances5636
from agent import remises5697 as rem, time500
from agent.common import Stop, private_json
from agent.desk import Desk
from test_v5697_gestion import ENTITE, FauxAgenda, FauxOCS

AVOCATE, ADMIN, AUTRE = ('avocate@example.test', 'avocat'), ('admin@example.test', 'administrateur'), ('autre@example.test', 'avocat')
CLIENT_MAIL = 'contact@durand.example.test'


class Recette(unittest.TestCase):
    def setUp(self):
        from sqlite_cleanup import close_sqlite_under
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.addCleanup(close_sqlite_under, self.t.name)
        base = Path(self.t.name)
        (base / 'state').mkdir()
        self.stockage = base / 'stockage'
        (self.stockage / 'Dossiers/DOS-900 - MARTIN').mkdir(parents=True)
        private_json(base / 'matters.json', [{'id': 'DOS-900', 'client_name': 'MARTIN', 'path': '/Dossiers/DOS-900 - MARTIN', 'references': ['DOS-900'],
                                               'aliases': ['MARTIN'], 'correspondents': []}])
        self.c = {'mode': 'drafts', 'state_dir': str(base / 'state'), 'matters_file': str(base / 'matters.json'),
                  'mail': {'own_addresses': ['cabinet@example.test'], 'username': 'cabinet@example.test', 'host': 'mail.example.test', 'from_address': 'cabinet@example.test'},
                  'nextcloud': {'url': 'https://cloud.example.test', 'local_path': str(self.stockage), 'roots': ['/Dossiers'], 'matter_roots': ['/Dossiers']},
                  'documents': {}, 'ollama': {'model': 'interdit'}, 'calendar': {'urls': ['/calendar/'], 'timezone': 'Europe/Paris'}}
        self.d = Desk(self.c)
        self.addCleanup(self.d.db.close)
        self.addCleanup(membres5650.clear)
        membres5650.directory(self.d)
        for owner, role in (AVOCATE, AUTRE):
            self.d.db.execute('INSERT OR IGNORE INTO membres5650(owner,name,role,first_seen,last_seen) VALUES(?,?,?,?,?)', (owner, owner.split('@')[0], role, 'x', 'x'))
        self.d.db.commit()
        self.ocs, self.agenda, self.n = FauxOCS(self.stockage), FauxAgenda(), 0
        # IA en pause AVANT le premier geste, fournisseurs bloqués, réseau interdit, file comptée
        from agent.arret5643 import PAUSE_GLOBAL
        self.d.setting(PAUSE_GLOBAL, True)
        self.d.setting('missions567:pause', True)
        for cible in ('agent.model.Model.ask', 'agent.model.Model.complete', 'agent.ai_gateway.common_completion'):
            p = patch(cible, side_effect=AssertionError('appel au modèle interdit pendant la recette'))
            p.start()
            self.addCleanup(p.stop)
        for p in (patch.object(socket, 'create_connection', side_effect=AssertionError('connexion réseau interdite')),
                  patch('agent.common.HTTP.request', side_effect=AssertionError('appel HTTP non simulé'))):
            p.start()
            self.addCleanup(p.stop)
        self.travaux_avant = self.d.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]

    def tearDown(self):
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], self.travaux_avant, 'un travail IA a été mis en file pendant la recette')

    def cle(self, prefixe='cle-recette'):
        self.n += 1
        return '%s-%04d' % (prefixe, self.n)

    def courriel(self, cle, sujet, expediteur=CLIENT_MAIL):
        self.d.db.execute('INSERT INTO work_items(mail_key,state,source_status,source_reason,matter,subject,sender,received,updated,resolved) VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (cle, 'needs_action', 'review', '', '', sujet, expediteur, '2026-11-02T09:00:00+00:00', '2026-11-02T09:00:00+00:00', ''))
        self.d.db.commit()
        return cle

    def chemin(self, mid):
        return next(m for m in __import__('agent.common', fromlist=['load_matters']).load_matters(self.c, unfiltered=True) if m['id'] == mid)['path']

    def octets(self, path):
        return (self.stockage / path.lstrip('/')).read_bytes()

    def ecrire(self, path, data):
        p = self.stockage / path.lstrip('/')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return path

    def modele(self):
        from agent import bibliotheque5639
        return next(t for t in bibliotheque5639.catalog(self.d) if t.get('langue') == 'fr')


# =========================================================================================== § 22
class Conseil(Recette):
    def test_R22_parcours_complet_refonte_de_cgv(self):
        fact.save_entity(self.d, *ADMIN, ENTITE)
        rem.enregistrer_reglages(self.d, ADMIN, {'actif': True})
        # 1. Ouvrir la demande depuis le courriel ; contrôle de conflits ; l'avocat décide
        mail = self.courriel('a' * 64, 'Refonte de nos conditions générales de vente')
        dem = mis.creer(self.d, AVOCATE, {'origine': 'courriel', 'source_ref': mail}, self.cle())
        dem = mis.modifier(self.d, AVOCATE, dem['id'], dem['version'], {'client_nom': 'DURAND SAS', 'correspondant_role': 'client', 'responsable': AVOCATE[0],
                                                                          'type_mission': 'conseil', 'objet': 'Refonte des CGV DURAND',
                                                                          'parties': [{'nom': 'DURAND SAS', 'role': 'client', 'courriel': CLIENT_MAIL}]})
        dem = mis.verifier_conflits(self.d, AVOCATE, dem['id'], dem['version'])
        self.assertTrue(dem['controle']['valide'])
        # R22_N : une partie ajoutée après le contrôle : l'acceptation ne peut pas s'appuyer sur l'ancien contrôle
        dem = mis.modifier(self.d, AVOCATE, dem['id'], dem['version'], {'parties': dem['parties'] + [{'nom': 'KERVILAC', 'role': 'tiers'}]})
        with self.assertRaisesRegex(Stop, 'controle_conflits_a_refaire'):
            mis.decider(self.d, AVOCATE, dem['id'], dem['version'], 'accepter', 'Aucun conflit après examen.')
        dem = mis.verifier_conflits(self.d, AVOCATE, dem['id'], dem['version'])
        dem = mis.decider(self.d, AVOCATE, dem['id'], dem['version'], 'accepter', 'Aucun conflit après examen du rapport.')
        # 2. Signer et ouvrir : convention depuis les données saisies, PDF signé de test, conversion (rejouée : un seul dossier)
        dem = mis.preparer_convention(self.d, AVOCATE, dem['id'])
        signe = b'%PDF-1.4 convention signee fictive DURAND'
        dem = mis.deposer(self.d, AVOCATE, dem['id'], 'convention_signee', 'Convention signée DURAND.pdf', signe, 'application/pdf')
        ouvert = mis.convertir(self.d, AVOCATE, dem['id'], dem['version'])
        rejeu = mis.convertir(self.d, AVOCATE, dem['id'], dem['version'])              # réponse coupée après conversion : rejeu
        mid = ouvert['dossier_id']
        self.assertEqual(rejeu['dossier_id'], mid)
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM dossiers5697').fetchone()[0], 1)
        if not self.d.db.execute('SELECT COUNT(*) FROM message_dossier5697 WHERE dossier_id=?', (mid,)).fetchone()[0]:
            # courriel d'essai sans identité IMAP connue du serveur : il se classe à la main (« Classer dans un dossier »)
            dossiers5697.rattacher_message(self.d, AVOCATE, mid, {'uidvalidity': '5', 'uid': '11', 'message_id': '<cgv-1@durand.example.test>',
                                                                  'sujet': 'Refonte de nos conditions générales de vente', 'expediteur': CLIENT_MAIL}, self.cle())
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM message_dossier5697 WHERE dossier_id=?', (mid,)).fetchone()[0], 1)   # le mail
        racine = self.chemin(mid)
        signes = [p for p in (self.stockage / racine.lstrip('/')).rglob('*.pdf') if p.read_bytes() == signe]
        self.assertEqual(len(signes), 1)                                                # l'original signé est dans le dossier
        fact.save_client(self.d, *AVOCATE, mid, {'nom': 'DURAND SAS (fictive)', 'kind': 'professionnel', 'adresse_ligne1': '2 avenue Fictive', 'code_postal': '69001',
                                                  'ville': 'Valbrune', 'country': 'FR', 'siren': '000010041', 'email': CLIENT_MAIL})
        jalon = hon.ajouter_jalon(self.d, AVOCATE, mid, {'cle': self.cle(), 'libelle': 'Forfait refonte des CGV', 'montant_ht': '1500'})   # forfait saisi
        # 3. Travailler : deux pièces demandées, une reçue, l'autre manquante ; une diligence ; un créneau depuis l'agenda
        demande = rem.demander(self.d, AVOCATE, mid, {'elements': ['CGV actuelles', 'Tarifs 2026'], 'destinataire': CLIENT_MAIL,
                                                      'revoir_le': (date.today() + timedelta(days=7)).isoformat()})
        self.assertEqual(demande['destinataire_verifie'], 'correspondant client du dossier')
        recu = rem.recevoir(self.d, AVOCATE, demande['id'], demande['elements'][0]['item'], {'cle': self.cle(), 'nom': 'CGV actuelles.pdf',
                                                                                             'contenu_b64': base64.b64encode(b'%PDF cgv actuelles').decode()})
        self.assertEqual(sorted(e['etat'] for e in recu['elements']), ['a_recevoir', 'recu_a_verifier'])
        rem.qualifier(self.d, AVOCATE, demande['id'], demande['elements'][0]['item'], 'accepter')
        time500.add_manual(self.d, mid, date.today().isoformat(), 90, 'Analyse des CGV actuelles', '200', AVOCATE[0])
        from agent import agenda5697
        jour = (date.today() + timedelta(days=3)).isoformat()
        rdv = agenda5697.creer(self.d, AVOCATE, {'cle': self.cle(), 'titre': 'Point CGV avec DURAND', 'objet': 'evenement', 'dossier': mid,
                                                 'debut': '%sT10:00' % jour, 'fin': '%sT11:00' % jour})
        self.assertIn(rdv.get('http', 201), (200, 201, 202))
        forfait = hon.forfait(self.d, AVOCATE, mid)
        self.assertEqual(forfait['heures_validees'], '1.50')
        self.assertEqual(forfait['prochain_jalon']['id'], jalon['id'])
        etat_pf = pf.etat(self.d, AVOCATE, mid)
        pf.definir(self.d, AVOCATE, mid, {'next_action': 'demande_pieces:' + demande['id']}, etat_pf['version'], self.cle())
        self.assertEqual(pf.etat(self.d, AVOCATE, mid)['action']['ref'], 'demande_pieces:' + demande['id'])     # prochaine action à jour
        # 4. Livrer : CGV depuis le modèle sans IA, édition, version validée remise au destinataire de test ; le projet interne évolue sans l'altérer
        cgv = docs.creer(self.d, AVOCATE, {'dossier': mid, 'mode': 'modele', 'type': 'libre', 'titre': 'CGV DURAND', 'modele': self.modele()['id']}, self.cle())
        vue = docs.ouvrir(self.d, AVOCATE, cgv['document_id'])
        v2 = docs.enregistrer(self.d, AVOCATE, cgv['document_id'], vue['version'], self.cle(), lignes=vue['paragraphes'] + ['Article 12 — Droit applicable (fictif).'])
        chemin_cgv = v2.get('chemin') or cgv['chemin']
        remise = rem.remettre(self.d, AVOCATE, mid, {'cle': self.cle(), 'canal': 'nextcloud', 'destinataire': {'type': 'utilisateur', 'id': 'durand.client'},
                                                    'identite_verifiee': True, 'fichiers': [{'path': chemin_cgv, 'nature': 'document'}]}, ocs=rem.PartagesOCS(self.ocs))
        self.assertEqual(remise['etat'], 'partage_actif')
        remise = rem.declarer_transmission(self.d, AVOCATE, remise['id'], {'version': remise['revision'], 'preuve': 'Lien transmis au client par courriel (fictif)'})
        remis, _ = rem.ouvrir_version_remise(self.d, AVOCATE, remise['id'], 1)
        v3 = docs.ouvrir(self.d, AVOCATE, cgv['document_id'])
        docs.enregistrer(self.d, AVOCATE, cgv['document_id'], v3['version'], self.cle(), lignes=v3['paragraphes'] + ['Projet interne : article 13 à l’étude.'])
        self.assertEqual(rem.ouvrir_version_remise(self.d, AVOCATE, remise['id'], 1)[0], remis)   # la version remise est inchangée
        # 5. Facturer et encaisser : préparer puis émettre via la source choisie (AxiorHub), règlement partiel puis solde
        hon.jalon_atteint(self.d, AVOCATE, jalon['id'], jalon['revision'])
        self.assertEqual(hon.indicateurs(self.d, AVOCATE, mid)['travail_a_facturer']['ht']['montant'], '1800.00')   # jalon 1 500 + 1 h 30 à 200
        brouillon = hon.preparer_brouillon(self.d, AVOCATE, mid, {'cle': self.cle()})
        appr = fact.approve(self.d, *AVOCATE, brouillon['id'], brouillon['version'])
        cle_emission = self.cle('emission')
        f = fact.issue(self.d, *AVOCATE, appr['id'], cle_emission)
        self.assertEqual(fact.issue(self.d, *AVOCATE, appr['id'], cle_emission)['number'], f['number'])   # réponse coupée après émission : rejeu
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM b53_documents WHERE kind='facture' AND number IS NOT NULL").fetchone()[0], 1)
        total = f['snapshot']['totaux']['ttc_cents']
        self.assertEqual(total, 216000)
        echeance = date.fromisoformat(f['snapshot']['dates']['echeance'])
        self.assertEqual(relances5636.prepare(self.d, today=echeance + timedelta(days=16), refresh=False)['created'], 1)   # relance proposée avant le solde
        fact.record_payment(self.d, *AVOCATE, f['id'], '1000', date.today().isoformat(), 'virement', 'VIR-DURAND-1')
        fact.record_payment(self.d, *AVOCATE, f['id'], '1000', date.today().isoformat(), 'virement', 'VIR-DURAND-1')   # R22_N : paiement déjà rapproché, réessayé
        fact.record_payment(self.d, *AVOCATE, f['id'], '1160', date.today().isoformat(), 'virement', 'VIR-DURAND-2')
        i = hon.indicateurs(self.d, AVOCATE, mid)
        self.assertEqual((i['travail_a_facturer']['ht']['montant'], i['facture_non_regle']['ttc']['montant'], i['encaisse']['ttc']['montant']), ('0.00', '0.00', '2160.00'))
        projet = relances5636._get(self.d, self.d.db.execute("SELECT id FROM relances5636 WHERE invoice=?", ('b53:' + f['id'],)).fetchone()['id'])
        self.assertEqual(relances5636.check_before_deposit(self.d, projet)[0], 'perimee')     # la relance n'est plus à envoyer
        # R22_N : un utilisateur sans accès ne voit ni convention, ni temps, ni montant (listes et exports)
        membres5650.set_reserve(self.c['state_dir'], mid, [AVOCATE[0]])
        self.assertEqual(hon.indicateurs(self.d, AUTRE)['encaisse']['ttc']['montant'], '0.00')
        for appel in (lambda: hon.indicateurs(self.d, AUTRE, mid), lambda: hon.forfait(self.d, AUTRE, mid), lambda: rem.remises(self.d, AUTRE, mid),
                      lambda: cloture5697.preparer(self.d, AUTRE, mid)):
            with self.assertRaisesRegex(Stop, '^introuvable$'):
                appel()
        export = Path(self.t.name) / 'export.zip'
        export5688.construire(self.d, export, {})                                       # dossiers réservés exclus par défaut
        with zipfile.ZipFile(export) as z:
            for nom in ('factures/documents_de_facturation.json', 'temps/temps_valides.json', 'factures/jalons.json', 'dossiers/remises_au_client.json'):
                self.assertNotIn(mid, z.read(nom).decode('utf-8'), nom)
        membres5650.set_reserve(self.c['state_dir'], mid, [])
        # 6. Clôturer : contrôler pièces et tâches, révoquer le partage, clôturer ; historique consultable ; réouverture autorisée testée
        prep = cloture5697.preparer(self.d, AVOCATE, mid)
        self.assertIn('partages', [c['id'] for c in prep['controles'] if c['etat'] == 'bloquant'])
        self.assertEqual(rem.revoquer(self.d, AVOCATE, remise['id'], rem.PartagesOCS(self.ocs))['etat'], 'acces_revoque')
        rem.qualifier(self.d, AVOCATE, demande['id'], demande['elements'][1]['item'], 'dispenser')
        prep = cloture5697.preparer(self.d, AVOCATE, mid)
        bloquants = [c['id'] for c in prep['controles'] if c['etat'] == 'bloquant']
        self.assertEqual(bloquants, [], prep['controles'])
        clos = cloture5697.cloturer(self.d, AVOCATE, mid, {'cle': self.cle(), 'version': prep['version'], 'motif': 'CGV livrées et honoraires réglés'})
        self.assertEqual(clos['etat'], 'archive')
        self.assertEqual(dossiers5697.lire(self.d, AVOCATE, mid)['statut'], 'archive')         # un seul statut : celui du registre unique
        ro = cloture5697.rouvrir(self.d, AVOCATE, mid, {'cle': self.cle(), 'version': clos['version'], 'motif': 'Avenant demandé par le client (fictif)'})
        self.assertEqual((ro['etat'], dossiers5697.lire(self.d, AVOCATE, mid)['statut']), ('actif', 'actif'))
        self.assertEqual(len(self.ocs.partages), 0)                                     # la réouverture ne recrée aucun partage
        self.assertEqual([h['decision'] for h in ro['historique']], ['reouverture', 'cloture'])
        # Critère de passage : une seule identité de dossier du début à la fin
        self.assertEqual({mid}, {r[0] for r in self.d.db.execute('SELECT DISTINCT matter FROM b53_documents')} | {r[0] for r in self.d.db.execute('SELECT DISTINCT matter FROM remises5697')})


    def test_R22_N_coupure_apres_le_registre_la_cloture_rejouee_se_termine_une_fois(self):
        out = dossiers5697.creer(self.d, AVOCATE, {'titre': 'Conseil DURAND', 'client': 'DURAND', 'nature': 'conseil', 'responsable': AVOCATE[0]}, self.cle())
        mid = out['id']
        dossiers5697.traiter_rangements(self.d, dossier=mid, limite=1)
        prep = cloture5697.preparer(self.d, AVOCATE, mid)
        data = {'cle': self.cle(), 'version': prep['version'], 'motif': 'Mission terminée', 'decisions': {'information_client': 'Client informé oralement (fictif)'}}
        vrai = cloture5697.g.journal

        def coupure(*args, **kwargs):
            raise Stop('connexion_http_indisponible')
        with patch.object(cloture5697.g, 'journal', coupure):
            with self.assertRaises(Stop):
                cloture5697.cloturer(self.d, AVOCATE, mid, data)
        self.assertEqual(dossiers5697.lire(self.d, AVOCATE, mid)['statut'], 'archive')       # le registre unique a été écrit avant la coupure
        self.assertIs(cloture5697.g.journal, vrai)
        fin = cloture5697.cloturer(self.d, AVOCATE, mid, data)                                 # même clé : la clôture se termine
        self.assertEqual(fin['etat'], 'archive')
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM clotures5697 WHERE matter=?", (mid,)).fetchone()[0], 1)
        self.assertEqual(cloture5697.cloturer(self.d, AVOCATE, mid, data)['etat'], 'archive')  # double clic : rien de plus
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM clotures5697 WHERE matter=?", (mid,)).fetchone()[0], 1)


# =========================================================================================== § 23
class Contentieux(Recette):
    def dossier(self):
        out = dossiers5697.creer(self.d, AVOCATE, {'titre': 'DURAND c/ ORLANDEC', 'client': 'DURAND', 'client_etat': 'identifie', 'nature': 'contentieux',
                                                   'responsable': AVOCATE[0], 'juridiction': 'Tribunal judiciaire de Valbrune', 'rg': '24/01234',
                                                   'parties': [{'nom': 'DURAND', 'role': 'client', 'courriel': CLIENT_MAIL}, {'nom': 'ORLANDEC', 'role': 'adverse'}]},
                                 self.cle())
        dossiers5697.traiter_rangements(self.d, dossier=out['id'], limite=1)
        return out['id']

    def test_R23_parcours_complet_de_l_acte_recu_au_compte_rendu(self):
        mid = self.dossier()
        racine = self.chemin(mid)
        # 1. Recevoir et classer : l'acte et le courriel rattachés au bon dossier ; un dossier réservé ne révèle rien
        acte_recu = self.ecrire(racine + '/Actes reçus/Assignation ORLANDEC.pdf', b'%PDF assignation fictive RG 24/01234')
        classe = dossiers5697.rattacher_message(self.d, AVOCATE, mid, {'uidvalidity': '7', 'uid': '42', 'message_id': '<acte-1@example.test>',
                                                                       'sujet': 'Signification de l’assignation', 'expediteur': 'greffe@example.test'}, self.cle())
        self.assertEqual(classe['etat'], 'classe')
        self.assertEqual(self.octets(acte_recu), b'%PDF assignation fictive RG 24/01234')     # original lisible
        reserve = dossiers5697.creer(self.d, AUTRE, {'titre': 'Affaire MARTIN réservée', 'client': 'MARTIN', 'nature': 'contentieux', 'confidentialite': 'reserve',
                                                     'membres': [AUTRE[0]]}, self.cle())
        membres5650.set_reserve(self.c['state_dir'], reserve['id'], [AUTRE[0]])
        dossiers5697.rattacher_message(self.d, AUTRE, reserve['id'], {'uidvalidity': '7', 'uid': '43', 'message_id': '<reserve@example.test>'}, self.cle())
        ref = self.d.db.execute("SELECT mail_key FROM message_dossier5697 WHERE dossier_id=?", (reserve['id'],)).fetchone()['mail_key']
        self.assertFalse(acces5697.peut_lire_message(self.d, AVOCATE, ref))
        with self.assertRaisesRegex(Stop, '^introuvable$'):
            proc.registre(self.d, AVOCATE, reserve['id'])
        # 2. Vérifier l'échéance : source ouverte, départ et règle contrôlés, date confirmée, suivi attribué ; une seule projection d'agenda
        depart = (date.today() - timedelta(days=4)).isoformat()
        e = proc.saisir_echeance(self.d, AVOCATE, mid, {'regle': 'appel_jugement_contentieux', 'depart': depart, 'source': acte_recu, 'depart_certain': False}, self.agenda)
        self.assertEqual((e['etat_libelle'], self.agenda.ecritures), ('À vérifier', []))   # échéance non confirmée : état explicite
        e = proc.decider(self.d, AVOCATE, e['id'], {'action': 'valider', 'cle': self.cle(), 'version': e['revision']}, self.agenda)
        self.assertEqual(len(self.agenda.ecritures), 1)
        proc.attribuer(self.d, AVOCATE, e['id'], {'responsable': AVOCATE[0], 'rappel_destinataire': AVOCATE[0], 'rappel_le': (date.today() + timedelta(days=10)).isoformat()})
        etat_pf = pf.etat(self.d, AVOCATE, mid)
        tache = pf.ajouter_action(self.d, AVOCATE, mid, 'Rédiger les conclusions d’appel', (date.today() + timedelta(days=20)).isoformat(), etat_pf['version'], self.cle())
        self.assertTrue(tache)
        self.assertEqual(len(self.agenda.ecritures), 1)                                  # la tâche ne crée pas un second événement d'échéance
        # 3. Préparer l'acte : conclusions depuis le modèle, édition, bordereau à partir des pièces choisies (numéros et versions vérifiables)
        p1 = self.ecrire(racine + '/Pieces/Piece 1 - contrat.pdf', b'%PDF piece 1 fictive')
        p2 = self.ecrire(racine + '/Pieces/Piece 2 - mise en demeure.pdf', b'%PDF piece 2 fictive')
        concl = docs.creer(self.d, AVOCATE, {'dossier': mid, 'mode': 'modele', 'type': 'conclusions', 'titre': 'Conclusions DURAND', 'modele': self.modele()['id']}, self.cle())
        vue = docs.ouvrir(self.d, AVOCATE, concl['document_id'])
        concl = docs.enregistrer(self.d, AVOCATE, concl['document_id'], vue['version'], self.cle(), lignes=vue['paragraphes'] + ['PAR CES MOTIFS (fictif).'])
        chemin_concl = concl.get('chemin') or docs.ouvrir(self.d, AVOCATE, concl['document_id']).get('chemin')
        # 4. Archiver le dépôt : paquet gelé ; une notification seule ne suffit pas ; l'accusé correspondant est importé et contrôlé
        pq = proc.preparer_paquet(self.d, AVOCATE, mid, {'cle': self.cle(), 'canal': 'ebarreau', 'reference_affaire': 'RG 24/01234', 'acte': {'path': chemin_concl},
                                                         'pieces': [{'path': p1, 'numero': '1'}, {'path': p2, 'numero': '2'}]})
        self.assertEqual([(f['rang'], f['numero']) for f in pq['fichiers']], [(1, ''), (2, '1'), (3, '2')])
        pq = proc.declarer_depot(self.d, AVOCATE, pq['id'], {'version': pq['revision'], 'envoi_ref': 'ENV-2026-0042', 'date_depot': date.today().isoformat()})
        notif = proc.importer_accuse(self.d, AVOCATE, pq['id'], {'nature': 'notification', 'contenu_b64': base64.b64encode(b'Notification e-Barreau fictive').decode()})
        self.assertFalse(notif['rapproche'])
        incoherent = proc.importer_accuse(self.d, AVOCATE, pq['id'], {'contenu_b64': base64.b64encode(b'%PDF AR autre').decode(), 'reference': 'RG 24/01234',
                                                                      'envoi_ref': 'ENV-2026-0099', 'date': date.today().isoformat()})
        self.assertFalse(incoherent['rapproche'])                                      # accusé incohérent : état explicite
        self.assertTrue(incoherent['anomalies'])
        bon = proc.importer_accuse(self.d, AVOCATE, pq['id'], {'contenu_b64': base64.b64encode(b'%PDF AR fictif ENV-2026-0042').decode(), 'nom_fichier': 'AR.pdf',
                                                               'reference': 'RG 24/01234', 'envoi_ref': 'ENV-2026-0042', 'date': date.today().isoformat()})
        self.assertTrue(bon['rapproche'])
        self.ecrire(chemin_concl, b'PK conclusions retouchees apres depot')
        self.assertNotEqual(proc.ouvrir_fichier(self.d, AVOCATE, pq['id'], 1)[0], b'PK conclusions retouchees apres depot')   # le manifeste ouvre l'acte déposé
        self.assertEqual(proc.ouvrir_fichier(self.d, AVOCATE, pq['id'], 3)[0], b'%PDF piece 2 fictive')
        preuve = next(a for a in bon['accuses'] if a['etat'] == 'rapproche')
        self.assertEqual(proc.ouvrir_accuse(self.d, AVOCATE, pq['id'], preuve['id'])[0], b'%PDF AR fictif ENV-2026-0042')
        # 5. Rendre compte : compte rendu rédigé, rattaché au client avec la version remise ; prochaine action, délais ouverts, conditions de clôture
        cr = docs.creer(self.d, AVOCATE, {'dossier': mid, 'mode': 'vierge', 'type': 'courrier', 'titre': 'Compte rendu au client', 'valeurs': {'destinataire': 'DURAND'}}, self.cle())
        remise = rem.remettre(self.d, AVOCATE, mid, {'cle': self.cle(), 'canal': 'courriel', 'destinataire': {'type': 'courriel', 'id': CLIENT_MAIL},
                                                    'fichiers': [{'path': cr['chemin'], 'nature': 'compte_rendu'}]})
        remise = rem.declarer_transmission(self.d, AVOCATE, remise['id'], {'version': remise['revision'], 'preuve': 'Compte rendu envoyé par courriel (fictif)'})
        self.assertEqual(remise['etat'], 'transmise')
        self.assertEqual(remise['preuve']['destinataire_verifie'], 'correspondant client du dossier')
        etat_pf = pf.etat(self.d, AVOCATE, mid)
        self.assertEqual(etat_pf['action']['type'], 'tache')                            # prochaine action : la tâche distincte
        self.assertEqual(etat_pf['echeance_prioritaire']['ref'], 'echeance:' + e['id'])    # le délai encore ouvert reste visible et prioritaire
        prep = cloture5697.preparer(self.d, AVOCATE, mid)
        self.assertEqual({c['id'] for c in prep['controles'] if c['etat'] == 'bloquant'}, {'echeances', 'taches'})   # délais et tâche encore ouverts
        # R23_P : lever la pause ensuite : aucune écriture, aucun dépôt ni envoi déjà traité n'est rejoué
        effets = [self.d.db.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0] for t in ('depots5697', 'depot_accuses5697', 'remises5697', 'message_dossier5697')]
        from agent.arret5643 import PAUSE_GLOBAL
        self.d.setting(PAUSE_GLOBAL, False)
        self.d.setting('missions567:pause', False)
        self.assertEqual([self.d.db.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0] for t in ('depots5697', 'depot_accuses5697', 'remises5697', 'message_dossier5697')], effets)
        self.assertEqual(len(self.agenda.ecritures), 1)
        self.assertEqual(self.ocs.appels, [])


if __name__ == '__main__':
    unittest.main()
