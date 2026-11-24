"""5.6.109, lot O (F12 et G13) : médiateur de mission sur le poste et contrat du compagnon pour le serveur.

Partie 1 : contrats repris de ``reference/poste109.py`` (treize cas du dossier, rejoués sur ``agent.poste56109``).
Partie 2 : service produit (autorisations liées à une mission ``missions_v567``, dossiers lisibles, reçus durables, pause, suspension,
révocation, reprise après interruption, outils non livrés signalés), avec une mutation pour la pause et pour la révocation.
Partie 3 : compagnon (appairage approuvé dans les deux sessions, ordres signés et expirants, nonce, génération, bail, reçus signés ; aucun
secret dans un ordre ; deux postes sans double effet ; « Attente du poste » sans poste associé).
Données fictives (DOS-001, example.test), aucun réseau, aucun modèle. Pas de sandbox OS, ni de pilote Windows ou Wayland : non testés ici.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import test_v5673 as t73
from agent import poste56109 as P
from agent.common import Stop
from agent.poste56109 import Action, Etat, Mediateur, Portee, Refus, domaine_public_autorise

OWNER = t73.OWNER
MID = t73.MID


# ============================================================================ partie 1 : contrats de la référence
class Contrats(unittest.TestCase):
    def setUp(self):
        self.state = Etat(4)
        self.portee = Portee('M1', 'U1', 'C1', 'PC1', 4, 100, frozenset({'RACINE1', 'PIECE1'}), frozenset({'SORTIE1'}),
                             domaines=frozenset({'www.legifrance.gouv.fr'}), connecteurs=frozenset({'legi'}), recettes=frozenset({'ocr_copie'}))
        self.allowed = True
        self.calls = []
        self.m = self.make(self.portee)

    def make(self, p):
        return Mediateur(p, lambda: self.state, lambda s: self.allowed, horloge=lambda: 50)

    def run_action(self, action, **kwargs):
        return self.m.executer(action, lambda a: self.calls.append(a) or {'sortie': 'SORTIE1'}, utilisateur='U1', cabinet='C1', poste='PC1', **kwargs)

    def test_F12_lecture_et_brouillon_autorises_sans_terminal(self):
        self.assertEqual(self.run_action(Action('lire_fichiers', ('PIECE1',))).etat, 'terminee')
        self.assertEqual(self.run_action(Action('preparer_document', ('PIECE1',), 'SORTIE1')).etat, 'terminee')
        self.assertEqual(len(self.calls), 2)

    def test_F12_autre_poste_ou_utilisateur_refuse(self):
        for who, cab, device in [('U2', 'C1', 'PC1'), ('U1', 'C2', 'PC1'), ('U1', 'C1', 'PC2')]:
            with self.assertRaises(Refus):
                self.m.executer(Action('lire_fichiers', ('PIECE1',)), self.calls.append, utilisateur=who, cabinet=cab, poste=device)
        self.assertFalse(self.calls)

    def test_F12_pause_annulation_revision_et_expiration(self):
        for state in (Etat(4, pause=True), Etat(4, annulee=True), Etat(5)):
            self.state = state
            with self.assertRaises(Refus):
                self.run_action(Action('lire_fichiers', ('PIECE1',)))
        self.state = Etat(4)
        self.m = self.make(replace(self.portee, expire=40))
        with self.assertRaises(Refus):
            self.run_action(Action('lire_fichiers', ('PIECE1',)))
        self.assertFalse(self.calls)

    def test_F12_sources_et_sorties_nouvelles_ne_donnent_pas_de_droits(self):
        for action in (Action('lire_fichiers', ('AUTRE',)), Action('preparer_document', ('PIECE1',), 'C:\\Windows\\fictif'), Action('lire_fichiers')):
            with self.assertRaises(Refus):
                self.run_action(action)
        self.allowed = False
        with self.assertRaises(Refus):
            self.run_action(Action('lire_fichiers', ('PIECE1',)))
        self.assertFalse(self.calls)

    def test_F12_reseau_explicitement_autorise_et_hors_ligne(self):
        self.run_action(Action('recherche_web', url='https://www.legifrance.gouv.fr/search'))
        self.run_action(Action('recherche_juridique', connecteur='legi'))
        self.state = Etat(4, reseau_interdit=True)
        for action in (Action('recherche_web', url='https://www.legifrance.gouv.fr'), Action('recherche_juridique', connecteur='legi')):
            with self.assertRaises(Refus):
                self.run_action(action)

    def test_F12_adresses_ambigues_locales_et_domaine_trompeur_refuses(self):
        for url in ('http://www.legifrance.gouv.fr', 'https://www.legifrance.gouv.fr.evil.test', 'https://127.0.0.1', 'https://[::1]',
                    'https://user:fictif@www.legifrance.gouv.fr', 'https://www.legifrance.gouv.fr:8443', 'https://www.legifrance.gouv.fr:bad',
                    'https://www.legifrance.gouv.fr/path\ncache'):
            with self.assertRaises(Refus):
                domaine_public_autorise(url, self.portee.domaines)

    def test_F12_connecteur_non_accorde_refuse(self):
        with self.assertRaises(Refus):
            self.run_action(Action('recherche_juridique', connecteur='autre'))

    def test_F12_shell_et_actions_finales_ne_sont_pas_des_outils(self):
        for name in ('execute', 'terminal', 'envoyer', 'signer', 'deposer_acte', 'supprimer', 'ecraser_original'):
            with self.assertRaises(Refus):
                self.run_action(Action(name))
        self.assertFalse(self.calls)

    def test_F12_recette_exige_un_confinement_qualifie(self):
        with self.assertRaises(Refus):
            self.run_action(Action('recette_locale', recette='ocr_copie'))
        self.m = self.make(replace(self.portee, confinement_recettes_verifie=True))
        self.run_action(Action('recette_locale', recette='ocr_copie'))
        with self.assertRaises(Refus):
            self.run_action(Action('recette_locale', recette='bash'))

    def test_F12_bureau_a_deux_conditions_distinctes(self):
        with self.assertRaises(Refus):
            self.run_action(Action('bureau_supervise'), accord_bureau=True)
        self.m = self.make(replace(self.portee, bureau_autorise=True))
        with self.assertRaises(Refus):
            self.run_action(Action('bureau_supervise'))
        self.run_action(Action('bureau_supervise'), accord_bureau=True)

    def test_F12_arret_pendant_un_effet_pas_de_faux_succes(self):
        def operation(action):
            self.state = Etat(4, annulee=True)
            return {'contenu_prive': 'CONFIDENTIEL', 'fichier_de_travail': 'X'}
        result = self.m.executer(Action('preparer_document', ('PIECE1',), 'SORTIE1'), operation, utilisateur='U1', cabinet='C1', poste='PC1')
        self.assertEqual(result.etat, 'a_rapprocher')
        self.assertNotIn('CONFIDENTIEL', repr(result))
        self.assertNotIn('interne', result.public())
        self.assertFalse(result.public()['reessai_automatique'])

    def test_F12_limites_et_methode_juridique(self):
        self.m = self.make(replace(self.portee, limite_etapes=1))
        self.run_action(Action('lire_fichiers', ('PIECE1',)))
        with self.assertRaises(Refus):
            self.run_action(Action('lire_fichiers', ('PIECE1',)))
        self.m = self.make(replace(self.portee, skills=frozenset()))
        with self.assertRaisesRegex(Refus, 'methode_juridique_absente'):
            self.run_action(Action('lire_fichiers', ('PIECE1',)))

    def test_F12_retrait_de_source_pendant_lecture_resultat_prive(self):
        def operation(action):
            self.allowed = False
            return {'contenu_prive': 'CONFIDENTIEL'}
        result = self.m.executer(Action('lire_fichiers', ('PIECE1',)), operation, utilisateur='U1', cabinet='C1', poste='PC1')
        self.assertEqual((result.etat, result.code), ('a_rapprocher', 'source_non_autorisee'))
        self.assertNotIn('CONFIDENTIEL', str(result.public()))


# ============================================================================ partie 2 : service produit
class Service(t73.base.Base):
    def setUp(self):
        super().setUp()
        p = patch.dict(os.environ, {'AXIORHUB_POSTE_HOME': str(self.root)})
        p.start()
        self.addCleanup(p.stop)
        from agent import missions567
        missions567.ensure_schema(self.desk)
        self.mission = uuid.uuid4().hex
        quand = datetime.now(timezone.utc).isoformat()
        self.desk.db.execute("INSERT INTO missions_v567(id,owner,request_key,fingerprint,instruction,matter,kind,state,context,plan,exceptions,created,updated) "
                             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (self.mission, OWNER, 'cle-mission-1', 'e', 'Mission fictive', MID, 'question', 'running',
                                                                     '{}', '[]', '[]', quand, quand))
        self.desk.db.commit()
        self.acteur = (OWNER, 'avocat')

    def autorisation(self, **kw):
        args = dict(sources=[MID], domaines=['legifrance.gouv.fr'], connecteurs=['legi'], cle='cle-autorisation-1')
        args.update(kw)
        return P.autoriser(self.desk, self.acteur, self.mission, **args)

    def test_F12_autorisation_liee_a_la_mission_et_aux_dossiers_lisibles(self):
        a = self.autorisation()
        self.assertEqual((a['etat'], a['sources'], a['revision']), ('active', [MID], 1))
        self.assertTrue(self.autorisation()['rejoue'])                               # même clé : même autorisation
        with self.assertRaisesRegex(Stop, 'cle_reutilisee'):
            self.autorisation(sources=[MID], domaines=['justice.fr'])
        with self.assertRaisesRegex(Stop, 'introuvable'):
            self.autorisation(sources=['DOS-INCONNU'], cle='cle-autorisation-2')
        with self.assertRaisesRegex(Stop, 'introuvable'):
            P.autoriser(self.desk, ('autre@example.test', 'avocat'), self.mission, [MID], cle='cle-autorisation-3')   # mission d'un autre
        with self.assertRaisesRegex(Stop, 'domaines_invalides'):
            self.autorisation(domaines=['192.168.1.1'], cle='cle-autorisation-4')
        evenements = [r[0] for r in self.desk.db.execute('SELECT kind FROM mission_events_v567 WHERE mission=?', (self.mission,))]
        self.assertIn('poste_autorisation', evenements)

    def test_F12_serveur_sans_poste_attente_du_poste(self):
        with patch.dict(os.environ, {'AXIORHUB_POSTE_HOME': ''}):
            with self.assertRaisesRegex(Stop, 'poste_absent'):
                self.autorisation()
            self.assertEqual(P.etat_delegation(self.desk, self.acteur)['etat'], 'poste_absent')
            self.assertIn('Attente du poste', P.section_html(self.desk, self.acteur))

    def test_F12_recherche_par_le_service_existant_recu_durable_et_idempotent(self):
        a = self.autorisation()
        out = P.executer(self.desk, self.acteur, a['id'], Action('rechercher_fichiers', (MID,), requete='convention'), 'cle-etape-0001')
        self.assertEqual(out['etat'], 'terminee')
        self.assertEqual([f['nom'] for f in out['donnees']['fichiers']], ['Convention.pdf'])
        fid = out['donnees']['fichiers'][0]['id']
        appels = []
        with patch('agent.fichiers5670.client', side_effect=lambda d: appels.append(1)):
            again = P.executer(self.desk, self.acteur, a['id'], Action('rechercher_fichiers', (MID,), requete='convention'), 'cle-etape-0001')
        self.assertTrue(again['rejoue'])
        self.assertEqual(appels, [])                                                  # même clé : pas de second effet
        lu = P.executer(self.desk, self.acteur, a['id'], Action('lire_fichiers', (fid,)), 'cle-etape-0002')
        self.assertEqual(lu['etat'], 'terminee')
        self.assertNotIn('convention fictive', json.dumps(lu))                        # jamais le contenu lu dans la réponse publique
        self.assertEqual(lu['donnees']['lus'][0]['taille'], len(b'%PDF-1.4 convention fictive'))
        with self.assertRaisesRegex(Stop, 'cle_reutilisee'):
            P.executer(self.desk, self.acteur, a['id'], Action('lire_fichiers', (fid,), requete='autre'), 'cle-etape-0002')
        self.assertEqual(len(P.recus(self.desk, self.acteur, a['id'])), 2)

    def test_F12_pause_globale_et_suspension_arretent_les_nouveaux_effets(self):
        a = self.autorisation()
        with patch('agent.navigateur56109.pause_globale', return_value='ia_en_pause'), patch('agent.fichiers5670.client', side_effect=AssertionError('effet')):
            out = P.executer(self.desk, self.acteur, a['id'], Action('rechercher_fichiers', (MID,)), 'cle-etape-0003')
        self.assertEqual((out['etat'], out['code']), ('refusee', 'ia_en_pause'))
        s = P.changer_etat(self.desk, self.acteur, a['id'], 'suspendue', version=1)
        with patch('agent.fichiers5670.client', side_effect=AssertionError('effet')):
            out = P.executer(self.desk, self.acteur, a['id'], Action('rechercher_fichiers', (MID,)), 'cle-etape-0004')
        self.assertEqual(out['code'], 'ia_en_pause')
        with self.assertRaisesRegex(Stop, 'version_perimee'):
            P.changer_etat(self.desk, self.acteur, a['id'], 'active', version=1)
        P.changer_etat(self.desk, self.acteur, a['id'], 'active', version=s['revision'])
        self.assertEqual(P.executer(self.desk, self.acteur, a['id'], Action('rechercher_fichiers', (MID,)), 'cle-etape-0005')['etat'], 'terminee')

    def test_F12_mutation_sans_controle_de_pause_l_effet_partirait(self):
        a = self.autorisation()
        effets = []
        with patch('agent.navigateur56109.pause_globale', return_value='ia_en_pause'), patch.object(Mediateur, 'checkpoint', lambda self: Etat(1)), \
                patch('agent.pause5629.checkpoint', lambda desk: None), \
                patch('agent.fichiers5670.client', side_effect=lambda d: effets.append(1) or SimpleNamespace(list_folder=lambda p: [])):
            P.executer(self.desk, self.acteur, a['id'], Action('rechercher_fichiers', (MID,)), 'cle-etape-0006')
        self.assertEqual(effets, [1])

    def test_F12_revocation_pendant_l_effet_donne_a_rapprocher_sans_nouvel_essai(self):
        a = self.autorisation()

        class Dav:
            def list_folder(dav, path):
                P.changer_etat(self.desk, self.acteur, a['id'], 'revoquee')
                return []
        with patch('agent.fichiers5670.client', return_value=Dav()):
            out = P.executer(self.desk, self.acteur, a['id'], Action('rechercher_fichiers', (MID,)), 'cle-etape-0007')
        self.assertEqual((out['etat'], out['code'], out['reessai_automatique']), ('a_rapprocher', 'mission_annulee', False))
        out = P.executer(self.desk, self.acteur, a['id'], Action('rechercher_fichiers', (MID,)), 'cle-etape-0008')
        self.assertEqual(out['code'], 'mission_annulee')
        with self.assertRaisesRegex(Stop, 'autorisation_revoquee'):
            P.changer_etat(self.desk, self.acteur, a['id'], 'active')

    def test_F12_recu_interrompu_devient_a_rapprocher(self):
        a = self.autorisation()
        with patch('agent.fichiers5670.client', side_effect=RuntimeError('panne fictive')):
            with self.assertRaises(RuntimeError):
                P.executer(self.desk, self.acteur, a['id'], Action('rechercher_fichiers', (MID,)), 'cle-etape-0009')
        with patch('agent.fichiers5670.client', side_effect=AssertionError('rejeu')):
            out = P.executer(self.desk, self.acteur, a['id'], Action('rechercher_fichiers', (MID,)), 'cle-etape-0009')
        self.assertEqual((out['etat'], out['code']), ('a_rapprocher', 'reprise_apres_interruption'))

    def test_F12_outils_non_livres_signales_et_shell_refuse(self):
        a = self.autorisation()
        out = P.executer(self.desk, self.acteur, a['id'], Action('recherche_juridique', connecteur='legi'), 'cle-etape-0010')
        self.assertEqual((out['etat'], out['code']), ('indisponible', 'pilote_indisponible'))
        out = P.executer(self.desk, self.acteur, a['id'], Action('execute', requete='rm -rf'), 'cle-etape-0011')
        self.assertEqual((out['etat'], out['code']), ('refusee', 'outil_non_expose'))
        out = P.executer(self.desk, self.acteur, a['id'], Action('recherche_juridique', connecteur='autre'), 'cle-etape-0012')
        self.assertEqual(out['code'], 'connecteur_non_autorise')
        caps = P.capacites(self.desk)
        self.assertTrue(caps['bureau'].startswith('Indisponible'))
        self.assertTrue(caps['recettes'].startswith('Indisponible'))

    def test_F12_autre_compte_meme_404_et_revocation_globale(self):
        a = self.autorisation()
        with self.assertRaisesRegex(Stop, 'introuvable'):
            P.executer(self.desk, ('autre@example.test', 'avocat'), a['id'], Action('rechercher_fichiers', (MID,)), 'cle-etape-0013')
        self.assertEqual(P.revoquer_tout(self.desk, self.acteur), [a['id']])

    def test_F12_routes_et_droits(self):
        self.assertTrue(P.allowed('assistant', 'POST', 'm56109/poste/autoriser'))
        self.assertTrue(P.allowed('avocat', 'GET', 'm56109/poste/etat'))
        self.assertFalse(P.allowed('avocat', 'POST', 'm56109/poste/executer'))      # le médiateur n'est pas une route : étapes IA seulement
        from agent import standalone_auth, web440
        self.assertTrue(standalone_auth.allowed('avocat', 'POST', '/api440/m56109/poste/revoquer'))
        env = {'HTTP_X_AXIORHUB_USER': OWNER, 'HTTP_X_AXIORHUB_ROLE': 'avocat', 'REQUEST_METHOD': 'GET'}
        out = web440.api(env, self.desk, {}, '/p', 'm56109/poste/etat', {}, 'GET')
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        self.assertIn('capacites', json.loads(out['body']))


# ============================================================================ partie 3 : compagnon du serveur
class Compagnon(t73.base.Base):
    def poste(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = sqlite3.connect(Path(tmp.name) / 'desk.sqlite3')
        db.row_factory = sqlite3.Row
        d = SimpleNamespace(db=db, c={'state_dir': tmp.name})
        self.addCleanup(db.close)
        P.ensure_schema(d)
        return d

    def associer(self, poste):
        code = P.appairer(self.desk, (OWNER, 'avocat'), 'Poste fictif')
        demande = P.demande_poste(poste.c['state_dir'], code['code'], code['serveur'])
        lie = P.associer(self.desk, (OWNER, 'avocat'), code['code'], demande)
        return demande, lie

    def test_G13_appairage_approuve_dans_les_deux_sessions(self):
        poste = self.poste()
        self.assertEqual(P.etat_delegation(self.desk, (OWNER, 'avocat'))['etat'], 'poste_absent')
        demande, lie = self.associer(poste)
        self.assertEqual(lie['etat'], 'a_approuver')
        with self.assertRaisesRegex(Stop, 'introuvable'):
            P.approuver(self.desk, (OWNER, 'avocat'), demande['poste'], 'AAAA BBBB')
        P.approuver(self.desk, (OWNER, 'avocat'), demande['poste'], lie['empreinte_poste'])
        self.assertEqual(P.etat_delegation(self.desk, (OWNER, 'avocat'))['etat'], 'poste_hors_ligne')   # associé mais jamais vu : rien n'est exécuté
        code = P.appairer(self.desk, (OWNER, 'avocat'))
        faux = dict(P.demande_poste(self.poste().c['state_dir'], code['code'], code['serveur']), poste=demande['poste'])
        with self.assertRaisesRegex(Stop, 'preuve_de_cle_invalide'):
            P.associer(self.desk, (OWNER, 'avocat'), code['code'], faux)

    def pret(self):
        poste = self.poste()
        demande, lie = self.associer(poste)
        P.approuver(self.desk, (OWNER, 'avocat'), demande['poste'], lie['empreinte_poste'])
        return poste, demande['poste']

    def test_G13_ordre_signe_recu_signe_et_rejeu_refuse(self):
        poste, pid = self.pret()
        envoi = P.emettre_ordre(self.desk, (OWNER, 'avocat'), pid, 'operation-0001', 'mission-fictive', ['infogreffe.fr'], ['lire'],
                                [{'id': 'doc-1', 'version': '3', 'sha256': 'a' * 64}])
        self.assertNotIn('cookie', json.dumps(envoi).lower())
        ordre = P.verifier_ordre(poste, envoi, envoi['cle_serveur'])
        with self.assertRaisesRegex(Stop, 'ordre_ancien_ou_duplique'):
            P.verifier_ordre(poste, envoi, envoi['cle_serveur'])                 # même nonce : refusé
        altere = {'ordre': dict(envoi['ordre'], domaines=['autre.example.test']), 'signature': envoi['signature']}
        with self.assertRaisesRegex(Stop, 'ordre_signature_invalide'):
            P.verifier_ordre(poste, altere, envoi['cle_serveur'])
        recu = P.recu_poste(poste, ordre, 'confirme', {'documents': 1})
        self.assertEqual(P.enregistrer_recu(self.desk, recu)['etat'], 'confirme')
        self.assertTrue(P.enregistrer_recu(self.desk, recu)['rejoue'])
        self.assertEqual(P.etat_delegation(self.desk, (OWNER, 'avocat'))['etat'], 'poste_pret')

    def test_G13_ordre_pour_un_autre_appareil_ou_expire_refuse(self):
        poste, pid = self.pret()
        autre = self.poste()
        envoi = P.emettre_ordre(self.desk, (OWNER, 'avocat'), pid, 'operation-0002', 'm', [], ['lire'])
        with self.assertRaisesRegex(Stop, 'ordre_destine_a_un_autre_appareil'):
            P.verifier_ordre(autre, envoi, envoi['cle_serveur'])
        with patch.object(P, '_maintenant', lambda: datetime.now(timezone.utc) + timedelta(hours=5)):
            with self.assertRaisesRegex(Stop, 'ordre_expire'):
                P.verifier_ordre(poste, envoi, envoi['cle_serveur'])

    def test_G13_aucun_secret_dans_un_ordre(self):
        poste, pid = self.pret()
        envoi = P.emettre_ordre(self.desk, (OWNER, 'avocat'), pid, 'operation-0003', 'm', [], ['lire'],
                                [{'id': 'doc', 'version': '1', 'sha256': 'b' * 64, 'cookie': 'valeur-fictive'}])
        self.assertNotIn('valeur-fictive', json.dumps(envoi))                      # seuls id, version et empreinte d'un document partent
        for champ in ('cookie_session', 'devtools_port', 'cle_privee', 'pin'):
            with self.assertRaisesRegex(Stop, 'champ_interdit_dans_un_ordre'):
                P._sans_secret({'ordre': {champ: 'x'}})
        with self.assertRaisesRegex(Stop, 'document_sans_empreinte'):
            P.emettre_ordre(self.desk, (OWNER, 'avocat'), pid, 'operation-0003', 'm', [], ['lire'], [{'id': 'doc', 'version': '1'}])

    def test_G13_deux_postes_sans_double_effet_et_reponse_perdue_rapprochee(self):
        poste1, pid1 = self.pret()
        poste2, pid2 = self.pret()
        envoi = P.emettre_ordre(self.desk, (OWNER, 'avocat'), pid1, 'operation-0004', 'm', [], ['deposer'], duree_minutes=1)
        with self.assertRaisesRegex(Stop, 'ordre_en_cours'):
            P.emettre_ordre(self.desk, (OWNER, 'avocat'), pid2, 'operation-0004', 'm', [], ['deposer'])
        with patch.object(P, '_maintenant', lambda: datetime.now(timezone.utc) + timedelta(minutes=5)):
            with self.assertRaisesRegex(Stop, 'ordre_a_rapprocher'):              # réponse perdue : jamais refait à l'aveugle
                P.emettre_ordre(self.desk, (OWNER, 'avocat'), pid2, 'operation-0004', 'm', [], ['deposer'])
        ordre = P.verifier_ordre(poste1, envoi, envoi['cle_serveur'])
        with self.assertRaisesRegex(Stop, 'recu_deja_enregistre|recu_invalide'):
            P.enregistrer_recu(self.desk, P.recu_poste(poste1, ordre, 'confirme'))   # ordre déjà « à rapprocher » : pas de succès silencieux
        n = self.desk.db.execute("SELECT COUNT(*) FROM poste56109_ordres WHERE operation='operation-0004'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_G13_bail_tenu_par_un_autre_poste_et_generation_perimee(self):
        poste1, pid1 = self.pret()
        poste2, pid2 = self.pret()
        self.desk.db.execute('BEGIN IMMEDIATE')
        P._bail(self.desk, 'operation-0005', pid1, timedelta(minutes=10))
        self.desk.db.commit()
        with self.assertRaisesRegex(Stop, 'operation_tenue_par_un_autre_poste'):
            P.emettre_ordre(self.desk, (OWNER, 'avocat'), pid2, 'operation-0005', 'm', [], ['lire'])
        envoi = P.emettre_ordre(self.desk, (OWNER, 'avocat'), pid1, 'operation-0006', 'm', [], ['lire'])
        ordre = P.verifier_ordre(poste1, envoi, envoi['cle_serveur'])
        self.desk.db.execute("UPDATE poste56109_baux SET generation=generation+1 WHERE operation='operation-0006'")
        self.desk.db.commit()
        with self.assertRaisesRegex(Stop, 'bail_perime'):
            P.enregistrer_recu(self.desk, P.recu_poste(poste1, ordre, 'confirme'))

    def test_G13_poste_revoque_n_agit_plus(self):
        poste, pid = self.pret()
        P.revoquer_poste(self.desk, (OWNER, 'avocat'), pid)
        with self.assertRaisesRegex(Stop, 'poste_absent'):
            P.emettre_ordre(self.desk, (OWNER, 'avocat'), pid, 'operation-0007', 'm', [], ['lire'])


if __name__ == '__main__':
    unittest.main()
