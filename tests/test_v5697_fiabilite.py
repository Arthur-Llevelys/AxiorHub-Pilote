"""5.6.97, lot fiabilite : B04, B08 à B10 et le panneau flottant (cahier 5.6.97, l. 76-80 et 94-123).

Coupures injectées avant et après la validation, puis au milieu du traitement ; la même demande rejouée ; ordres repris sous bail ; missions
anciennes sans travail ; projection du travail suspendu (pause manuelle, du dossier, programmée, créneau qui passe minuit) ; états du panneau.
Données fictives (DEMO, DURAND, MARTIN, example.test), faux serveur IMAP, aucun modèle appelé, tout appel réseau non simulé échoue. Le parcours
du panneau dans un vrai navigateur (B08 : 400 puis texte corrigé, réseau coupé, double clic, réponse tardive…) est le parcours P97f de
``scripts/parcours-navigateur.py``."""
from datetime import datetime, timedelta, timezone
import json
import socket
import unittest
import uuid
from unittest.mock import patch
from zoneinfo import ZoneInfo

from agent import courriels_commandes5697 as commandes, missions567 as missions
from agent.common import Stop
import test_v567 as t567
import test_v5696_courriels as tc


class Coupure(BaseException):
    """Arrêt brutal du service (le processus meurt) : aucune clause ``except Exception`` ne l'attrape."""


def _reseau_interdit(*_a, **_k):
    raise AssertionError('appel réseau non simulé')


class SansReseau:
    def setUp(self):
        super().setUp()
        for cible in ('socket.create_connection', 'socket.socket.connect'):
            p = patch(cible, _reseau_interdit)
            p.start()
            self.addCleanup(p.stop)


class BaseMissions(SansReseau, t567.Base):
    def mission(self, **kw):
        kw.setdefault('request_key', 'fiab-' + uuid.uuid4().hex)
        return super().mission(**kw)

    def compte(self, sql, *args):
        return self.desk.db.execute(sql, args).fetchone()[0]

    def jobs(self, kind='assistant_answer'):
        return self.compte('SELECT COUNT(*) FROM jobs WHERE kind=?', kind)

    def plus_tard(self, secondes):
        base = missions._horloge()
        return patch.object(missions, '_horloge', lambda: base + secondes)


# ------------------------------------------------------------------------------------------------------------ B09
class B09Lancement(BaseMissions):
    def test_B09_mission_recu_et_ordre_ecrits_ensemble_puis_lances(self):
        out = self.mission()
        self.assertEqual(out['state'], 'queued')
        self.assertEqual(self.jobs(), 1)
        ordre = self.desk.db.execute('SELECT * FROM missions_ordres5697 WHERE mission=?', (out['id'],)).fetchone()
        self.assertEqual((ordre['phase'], ordre['etat'], ordre['job_id']), ('lancement', 'fait', out['job_id']))
        self.assertEqual(self.compte("SELECT COUNT(*) FROM mission_events_v567 WHERE mission=? AND kind='creating'", out['id']), 1)   # le reçu

    def test_B09_coupure_avant_validation_ne_laisse_rien(self):
        cle = 'fiab-' + uuid.uuid4().hex
        with patch.object(missions, '_ordre', side_effect=Coupure()), self.assertRaises(Coupure):
            self.mission(request_key=cle)
        self.assertEqual(self.compte('SELECT COUNT(*) FROM missions_v567'), 0)
        self.assertEqual(self.compte('SELECT COUNT(*) FROM mission_events_v567'), 0)
        out = self.mission(request_key=cle)                                  # même demande rejouée
        self.assertEqual((out['state'], self.jobs()), ('queued', 1))

    def test_B09_coupure_apres_validation_ordre_repris_a_la_lecture(self):
        cle = 'fiab-' + uuid.uuid4().hex
        with patch.object(missions, '_executer_ordre', side_effect=Coupure()), self.assertRaises(Coupure):
            self.mission(request_key=cle)
        ident = self.desk.db.execute('SELECT id FROM missions_v567').fetchone()[0]
        self.assertEqual(self.jobs(), 0)
        recu = missions.get(self.desk, ident)                                # aussitôt relue : reçue, pas encore relancée
        self.assertEqual((recu['state'], recu['panneau']['code'], recu['panneau']['libelle']), ('creating', 'recue', 'Reçue'))
        self.assertEqual(self.jobs(), 0)
        with self.plus_tard(missions.RELANCE_SECONDES + 1):
            repris = missions.get(self.desk, ident)
        self.assertEqual((repris['state'], self.jobs()), ('queued', 1))
        with self.plus_tard(missions.RELANCE_SECONDES + 60):
            missions.get(self.desk, ident)
            self.assertEqual(self.mission(request_key=cle)['id'], ident)    # rejouée : la même mission, aucun second travail
        self.assertEqual(self.jobs(), 1)

    def test_B09_meme_cle_rejouee_aussitot_reprend_l_ordre_une_fois(self):
        cle = 'fiab-' + uuid.uuid4().hex
        with patch.object(missions, '_executer_ordre', side_effect=Coupure()), self.assertRaises(Coupure):
            self.mission(request_key=cle)
        out = self.mission(request_key=cle)
        again = self.mission(request_key=cle)
        self.assertEqual((out['state'], out['id'], self.jobs()), ('queued', again['id'], 1))

    def test_B09_superviseur_reprend_l_ordre_sans_requete(self):
        with patch.object(missions, '_executer_ordre', side_effect=Coupure()), self.assertRaises(Coupure):
            self.mission()
        self.assertIsNone(missions.superviser_ordres({'state_dir': self.desk.c['state_dir'] + '-absent'}))   # aucune base : rien d'ouvert
        with self.plus_tard(missions.RELANCE_SECONDES + 1):
            d = missions.superviser_ordres(self.desk.c)
        try:
            self.assertIsNotNone(d)
        finally:
            d.db.close()
        self.assertEqual(self.jobs(), 1)
        self.assertEqual(self.compte("SELECT COUNT(*) FROM missions_v567 WHERE state='queued'"), 1)
        self.assertIsNone(missions.superviser_ordres(self.desk.c))           # plus rien en souffrance : rien d'ouvert ni d'écrit

    def test_B09_coupure_au_milieu_travail_rattache_jamais_recree(self):
        from agent import integration
        vrai = integration.submit_question

        def puis_coupure(*a, **k):
            vrai(*a, **k)
            raise Coupure()
        with patch.object(integration, 'submit_question', puis_coupure), self.assertRaises(Coupure):
            self.mission()
        ident = self.desk.db.execute('SELECT id FROM missions_v567').fetchone()[0]
        self.assertEqual(self.jobs(), 1)
        with self.plus_tard(missions.BAIL_SECONDES + 1):
            out = missions.get(self.desk, ident)
        self.assertEqual((out['state'], self.jobs()), ('queued', 1))
        self.assertTrue(out['ref'].startswith('ask:'))

    def test_B09_garde_relecture_detectee_par_mutation(self):
        """Sans la relecture du travail déjà produit, la reprise créerait une seconde question : le test ci-dessus échouerait."""
        from agent import integration
        vrai = integration.submit_question

        def puis_coupure(*a, **k):
            vrai(*a, **k)
            raise Coupure()
        with patch.object(integration, 'submit_question', puis_coupure), self.assertRaises(Coupure):
            self.mission()
        ident = self.desk.db.execute('SELECT id FROM missions_v567').fetchone()[0]
        with self.plus_tard(missions.BAIL_SECONDES + 1), patch.object(missions, '_rattacher', lambda desk, row: False):
            missions.reprendre_ordres(self.desk, ident)
        self.assertEqual(self.jobs(), 2)                                      # mutation : doublon — la garde est donc bien exercée

    def test_B09_bail_un_seul_lanceur(self):
        with patch.object(missions, '_executer_ordre', side_effect=Coupure()), self.assertRaises(Coupure):
            self.mission()
        ordre = self.desk.db.execute('SELECT id FROM missions_ordres5697').fetchone()[0]
        self.desk.db.execute("UPDATE missions_ordres5697 SET etat='en_cours', bail=? WHERE id=?", (missions._horloge() + 60, ordre))
        self.desk.db.commit()
        missions._executer_ordre(self.desk, ordre)                           # bail d'un autre lanceur encore valable : rien
        self.assertEqual(self.jobs(), 0)
        with self.plus_tard(61):
            missions._executer_ordre(self.desk, ordre)
        self.assertEqual(self.jobs(), 1)

    def test_B09_mission_ancienne_sans_travail_interrompue_puis_reprise(self):
        ident, ancien = uuid.uuid4().hex, (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        missions.ensure_schema(self.desk)
        plan = {'objective': 'x', 'deliverable': 'Réponse', 'steps': ['Répondre'], 'restrictions': ['Aucun envoi'], 'intent': 'question', 'autonomy': 'prepare', 'document_kind': ''}
        ctx = {'page': '/', 'selected_documents': [], 'mail_key': '', 'attachments': [], 'channel': 'text', 'thread_id': ''}
        self.desk.db.execute('INSERT INTO missions_v567 VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?)',
                             (ident, 'cabinet', 'ancienne-' + 'a' * 20, 'f' * 64, 'Résume les sources disponibles.', 'DEMO', 'question', 'creating',
                              json.dumps(ctx), json.dumps(plan), '[]', '', ancien, ancien))
        self.desk.db.commit()
        out = missions.get(self.desk, ident)
        self.assertEqual((out['state'], out['label'], out['panneau']['code']), ('interrupted', 'Interrompue · reprendre', 'interrompue'))
        self.assertEqual(self.jobs(), 0)                                      # rien rejoué en silence
        self.assertEqual(self.compte("SELECT COUNT(*) FROM mission_events_v567 WHERE mission=? AND kind='interrupted'", ident), 1)
        repris = missions.control(self.desk, {'id': ident, 'action': 'resume'})
        self.assertEqual((repris['state'], self.jobs()), ('queued', 1))

    def test_B09_mission_ancienne_rattachee_sur_preuve(self):
        first = self.mission()
        self.desk.db.execute("UPDATE missions_v567 SET job_id=NULL,ref='',state='creating',created=? WHERE id=?",
                             ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(), first['id']))
        self.desk.db.execute('DELETE FROM missions_ordres5697')
        self.desk.db.commit()
        again = missions.get(self.desk, first['id'])
        self.assertEqual((again['state'], again['job_id']), ('queued', first['job_id']))

    def test_B09_reponse_liee_a_sa_question_et_non_a_la_derniere_du_fil(self):
        first = self.mission()
        tid = first['ref'][4:]
        self.desk.db.execute("INSERT INTO assistant_messages(thread_id,role,content,status,sources,job_id,created,updated) VALUES (?,?,?,?,?,?,?,?)",
                             (tid, 'assistant', 'Réponse à la première question.', 'done', '[]', first['job_id'], self.desk.now(), self.desk.now()))
        self.desk.db.execute("UPDATE jobs SET status='done',result='{}' WHERE id=?", (first['job_id'],))
        self.desk.db.commit()
        child = self.mission(instruction='Développe le dernier point.', context={'mission_id': first['id']})
        self.assertEqual(child['ref'], first['ref'])                          # même fil
        self.desk.db.execute("UPDATE jobs SET status='done',result='{}' WHERE id=?", (child['job_id'],))
        self.desk.db.commit()
        seconde = missions.get(self.desk, child['id'])
        self.assertNotEqual(seconde['state'], 'answered')                     # avant 5.6.97 : la réponse de la première question
        self.assertNotIn('text', seconde['result'])
        self.desk.db.execute("INSERT INTO assistant_messages(thread_id,role,content,status,sources,job_id,created,updated) VALUES (?,?,?,?,?,?,?,?)",
                             (tid, 'assistant', 'Réponse à la seconde question.', 'done', '[]', child['job_id'], self.desk.now(), self.desk.now()))
        self.desk.db.execute("UPDATE missions_v567 SET state='queued' WHERE id=?", (child['id'],))
        self.desk.db.commit()
        self.assertEqual(missions.get(self.desk, child['id'])['result']['text'], 'Réponse à la seconde question.')
        self.assertEqual(missions.get(self.desk, first['id'])['result']['text'], 'Réponse à la première question.')

    def test_B09_reponse_porte_le_travail_de_sa_question(self):
        """La réponse enregistrée par le circuit de l'assistant porte l'identifiant du travail de sa question (lien exact)."""
        from agent import integration
        out = self.mission()
        with patch('agent.workspace.chat', return_value={'answer': 'Réponse fictive.', 'sources': []}), patch('agent.dav.DAV', return_value=None):
            integration.answer_question(self.desk, json.loads(self.desk.db.execute('SELECT args FROM jobs WHERE id=?', (out['job_id'],)).fetchone()[0]))
        row = self.desk.db.execute("SELECT job_id FROM assistant_messages WHERE role='assistant'").fetchone()
        self.assertEqual(row['job_id'], out['job_id'])
        self.desk.db.execute("UPDATE jobs SET status='done',result='{}' WHERE id=?", (out['job_id'],))
        self.desk.db.commit()
        prete = missions.get(self.desk, out['id'])
        self.assertEqual((prete['state'], prete['panneau']['code'], prete['result']['text']), ('answered', 'reponse_prete', 'Réponse fictive.'))


# ------------------------------------------------------------------------------------------------------------ panneau : « Choix requis »
class ChoixRequis(BaseMissions):
    def test_PANNEAU_production_sans_dossier_choix_sur_place_et_meme_demande_relancee(self):
        out = self.mission(instruction='Prépare une note de plaidoirie.', matter='')
        self.assertEqual((out['state'], out['panneau']['code'], out['panneau']['libelle']), ('decision', 'choix_requis', 'Choix requis : le dossier'))
        self.assertIn('Lancer dans ce dossier', out['panneau']['geste'])
        self.assertIn('DEMO', [c['id'] for x in out['exceptions'] for c in x.get('candidates', [])])
        self.assertEqual(self.compte('SELECT COUNT(*) FROM jobs'), 0)
        with self.assertRaisesRegex(Stop, 'dossier_absent'):
            missions.control(self.desk, {'id': out['id'], 'action': 'resolve', 'matter': ''})
        lance = missions.control(self.desk, {'id': out['id'], 'action': 'resolve', 'matter': 'DEMO'})
        self.assertEqual((lance['id'], lance['instruction'], lance['matter']), (out['id'], 'Prépare une note de plaidoirie.', 'DEMO'))
        self.assertIn(lance['state'], ('queued', 'running'))
        self.assertEqual(self.compte("SELECT COUNT(*) FROM jobs WHERE kind='docrequest520'"), 1)
        self.assertEqual(self.compte("SELECT phase FROM missions_ordres5697 WHERE mission=?", out['id']), 'precision-1')
        with self.assertRaisesRegex(Stop, 'mission_sans_question'):            # double clic : pas de seconde préparation
            missions.control(self.desk, {'id': out['id'], 'action': 'resolve', 'matter': 'DEMO'})
        self.assertEqual(self.compte("SELECT COUNT(*) FROM jobs WHERE kind='docrequest520'"), 1)

    def test_PANNEAU_cartes_de_la_page_missions_sans_dossier_preselectionne(self):
        from agent import web567
        out = self.mission(instruction='Prépare une note de plaidoirie.', matter='')
        html = web567.cards([missions.get(self.desk, out['id'])], '/p')
        self.assertIn('<option value="">Choisir le dossier…</option>', html)
        self.assertIn('Lancer dans ce dossier', html)
        self.assertIn('data-etat="choix_requis"', html)

    def test_PANNEAU_etats_explicites(self):
        out = self.mission()
        self.assertEqual(out['panneau']['code'], 'attente')
        self.assertEqual(out['panneau']['libelle'], 'En attente de calcul')
        self.desk.db.execute("UPDATE jobs SET status='running' WHERE id=?", (out['job_id'],))
        self.desk.db.commit()
        self.assertEqual(missions.get(self.desk, out['id'])['panneau']['libelle'], 'En cours')
        self.desk.db.execute("UPDATE jobs SET status='error',result=? WHERE id=?", (json.dumps({'erreur': 'autorisation_mission_revoquee'}), out['job_id']))
        self.desk.db.commit()
        refus = missions.get(self.desk, out['id'])
        self.assertEqual((refus['state'], refus['panneau']['code'], refus['panneau']['libelle']), ('error', 'refusee', 'Refusée'))
        imm = missions.carte_fil('Merci.', 'Avec plaisir.', {})
        self.assertEqual((imm['id'], imm['panneau']['code']), ('', 'reponse_prete'))   # réponse immédiate, sans travail
        self.assertNotIn('fingerprint', out)


# ------------------------------------------------------------------------------------------------------------ B10
class B10Suspendu(BaseMissions):
    def suspendre(self, out, motif):
        self.desk.db.execute("UPDATE jobs SET status='suspended',stop_reason=? WHERE id=?", (motif, out['job_id']))
        self.desk.db.commit()

    def test_B10_travail_suspendu_projete_puis_attente_au_retour(self):
        out = self.mission()
        self.suspendre(out, 'pause')
        vue = missions.get(self.desk, out['id'])
        self.assertEqual((vue['state'], vue['panneau']['code'], vue['suspension']['reprise']), ('suspended', 'suspendue', 'manuelle'))
        self.assertEqual(vue['panneau']['libelle'], 'Suspendue : IA en pause')
        self.assertEqual(self.compte('SELECT state FROM missions_v567 WHERE id=?', out['id']), 'queued')   # projection seulement
        self.suspendre(out, 'pause_dossier')
        self.assertEqual(missions.get(self.desk, out['id'])['panneau']['libelle'], 'Suspendue : IA en pause sur ce dossier')
        self.desk.db.execute("UPDATE jobs SET status='pending',stop_reason=NULL WHERE id=?", (out['job_id'],))
        self.desk.db.commit()
        retour = missions.get(self.desk, out['id'])
        self.assertEqual((retour['state'], retour['panneau']['libelle']), ('queued', 'En attente de calcul'))

    def test_B10_annulee_reste_annulee_meme_si_le_travail_est_suspendu(self):
        out = self.mission()
        self.suspendre(out, 'pause')
        self.desk.db.execute("UPDATE missions_v567 SET state='cancelled' WHERE id=?", (out['id'],))
        self.desk.db.commit()
        self.assertEqual(missions.get(self.desk, out['id'])['state'], 'cancelled')

    def test_B10_suspendue_ne_se_retire_pas_de_la_liste(self):
        out = self.mission()
        self.suspendre(out, 'pause')
        with self.assertRaisesRegex(Stop, 'mission_active_a_annuler_dabord'):
            missions.control(self.desk, {'id': out['id'], 'action': 'hide'})

    def politique(self, debut, fin):
        from agent import horaires_ia5689 as h
        p = {'schema_version': 1, 'revision': 1, 'enabled': True, 'timezone': 'Europe/Paris', 'scope': 'cabinet',
             'windows': [{'id': 'nuit', 'days': [1, 2, 3, 4, 5, 6, 7], 'kind': 'period', 'start': debut, 'end': fin}]}
        self.desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (h.CLE_POLITIQUE, json.dumps(p)))
        self.desk.db.commit()

    def test_B10_pause_programmee_qui_passe_minuit_reprise_automatique(self):
        from agent import horaires_ia5689 as h
        paris = ZoneInfo('Europe/Paris')
        jour = datetime.now(paris).date()
        out = self.mission()
        self.suspendre(out, 'pause_horaire')
        self.politique('22:00', '07:00')
        demain = jour + timedelta(days=1)
        for local, attendu in ((datetime(jour.year, jour.month, jour.day, 23, 30, tzinfo=paris), 'Suspendue jusqu’à demain 7 h'),
                               (datetime(demain.year, demain.month, demain.day, 0, 30, tzinfo=paris), 'Suspendue jusqu’à aujourd’hui 7 h')):
            instant = local.astimezone(timezone.utc)
            with patch.object(h, '_utc', lambda now=None, _i=instant: _i):
                vue = missions.get(self.desk, out['id'])
            self.assertEqual((vue['state'], vue['suspension']['reprise'], vue['panneau']['libelle']), ('suspended', 'automatique', attendu))
            fin = datetime.fromisoformat(vue['suspension']['jusqua']).astimezone(paris)
            self.assertEqual((fin.hour, fin.minute), (7, 0))
            self.assertGreater(fin, instant)
            self.assertIn('une seule fois', vue['panneau']['geste'])


# ------------------------------------------------------------------------------------------------------------ B04
class BaseB04(SansReseau, tc.BaseVue):
    def preparer(self, fil, cle, version=0, **extra):
        return self.decision(fil, 'preparer', idempotence=cle, version=version, **extra)

    def travaux(self):
        return self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='prepare_reply'").fetchone()[0]

    def plus_tard(self, secondes):
        base = commandes._horloge()
        return patch.object(commandes, '_horloge', lambda: base + secondes)

    def commande(self):
        return dict(self.desk.db.execute('SELECT * FROM courriels5697_commandes ORDER BY cree DESC LIMIT 1').fetchone())


class B04Preparation(BaseB04):

    def test_B04_decision_et_commande_dans_la_meme_transaction(self):
        _, fil, _ = self.entrant('Demande DURAND')
        self.activer()
        cle = uuid.uuid4().hex
        statut, out = self.preparer(fil, cle)
        self.assertEqual((statut, out['statut'], self.travaux()), ('200', 'en_preparation', 1))
        cmd = self.commande()
        self.assertEqual((cmd['cle'], cmd['etat'], cmd['job_id']), (cle, 'fait', out['job_id']))

    def test_B04_coupure_avant_validation(self):
        _, fil, _ = self.entrant('Demande DURAND')
        self.activer()
        cle = uuid.uuid4().hex
        with patch.object(commandes, 'inscrire', side_effect=Coupure()), self.assertRaises(Coupure):
            self.preparer(fil, cle)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM courriels5696_evenements').fetchone()[0], 0)
        self.assertEqual((self.ligne(fil)['statut'], self.travaux()), ('a_traiter', 0))
        statut, out = self.preparer(fil, cle)                                  # même demande rejouée
        self.assertEqual((statut, out['statut'], self.travaux()), ('200', 'en_preparation', 1))

    def test_B04_coupure_apres_validation_interrompu_puis_meme_demande_reprise_une_fois(self):
        _, fil, _ = self.entrant('Demande DURAND')
        self.activer()
        cle = uuid.uuid4().hex
        with patch.object(commandes, 'executer', side_effect=Coupure()), self.assertRaises(Coupure):
            self.preparer(fil, cle, instruction='Proposer un rendez-vous')
        self.assertEqual(self.travaux(), 0)
        self.assertEqual(self.ligne(fil)['statut'], 'en_preparation')         # commande toute fraîche : en cours
        with self.plus_tard(commandes.RELANCE_SECONDES + 1):
            ligne = self.ligne(fil)
            self.assertEqual((ligne['statut'], ligne['libelle']), ('interrompu', 'Interrompu · reprendre'))
            self.assertEqual(self.travaux(), 0)                                # aucun renvoi silencieux
            statut, out = self.preparer(fil, cle)                              # même demande rejouée : reprise
            self.assertEqual((statut, out['deja'], out['statut']), ('200', True, 'en_preparation'))
            statut, out = self.preparer(fil, cle)                              # et encore : rien de plus
        self.assertEqual(self.travaux(), 1)
        self.assertEqual(json.loads(self.desk.db.execute("SELECT args FROM jobs WHERE kind='prepare_reply'").fetchone()[0])['instruction'], 'Proposer un rendez-vous')
        self.assertEqual(self.ligne(fil)['statut'], 'en_preparation')
        self.assertEqual(self.commande()['etat'], 'fait')

    def test_B04_coupure_au_milieu_effet_rattache_jamais_defait(self):
        from agent import courriels5696 as cx
        _, fil, _ = self.entrant('Demande DURAND')
        self.activer()
        vrai = cx.demander_reponse

        def puis_panne(*a, **k):
            vrai(*a, **k)
            raise Stop('journal_indisponible')
        with patch.object(cx, 'demander_reponse', puis_panne):
            statut, out = self.preparer(fil, uuid.uuid4().hex)
        self.assertEqual((statut, out['statut'], self.travaux()), ('200', 'en_preparation', 1))
        self.assertEqual(self.ligne(fil)['statut'], 'en_preparation')
        ev = self.desk.db.execute("SELECT job_id FROM courriels5696_evenements WHERE action='preparer'").fetchone()
        self.assertEqual(ev[0], out['job_id'])

    def test_B04_garde_relecture_detectee_par_mutation(self):
        """Sans la relecture des travaux déjà produits, la même panne défait la décision alors que le travail existe : la garde est exercée."""
        from agent import courriels5696 as cx
        _, fil, _ = self.entrant('Demande DURAND')
        self.activer()
        vrai = cx.demander_reponse

        def puis_panne(*a, **k):
            vrai(*a, **k)
            raise Stop('journal_indisponible')
        with patch.object(cx, 'demander_reponse', puis_panne), patch.object(commandes, 'travail_produit', lambda *a: None):
            statut, _ = self.preparer(fil, uuid.uuid4().hex)
        evenements = self.desk.db.execute('SELECT COUNT(*) FROM courriels5696_evenements').fetchone()[0]
        self.assertEqual((statut, evenements, self.travaux()), ('400', 0, 1))   # mutation : décision défaite, travail orphelin

    def test_B04_coupure_apres_effet_avant_cloture(self):
        _, fil, _ = self.entrant('Demande DURAND')
        self.activer()
        cle = uuid.uuid4().hex
        vrai = commandes._clore

        def clore(desk, ident, etat, *a, **k):
            if etat == 'fait':
                raise Coupure()
            return vrai(desk, ident, etat, *a, **k)
        with patch.object(commandes, '_clore', clore), self.assertRaises(Coupure):
            self.preparer(fil, cle)
        self.assertEqual((self.commande()['etat'], self.travaux()), ('en_cours', 1))
        self.assertEqual(self.ligne(fil)['statut'], 'en_preparation')         # le travail existe : jamais « interrompu »
        with self.plus_tard(commandes.BAIL_SECONDES + 1):
            statut, out = self.preparer(fil, cle)
        self.assertEqual((statut, self.travaux(), self.commande()['etat']), ('200', 1, 'fait'))

    def test_B04_ligne_historique_en_preparation_sans_travail_interrompue_puis_reprise(self):
        key, fil, _ = self.entrant('Demande DURAND')
        self.activer()
        from agent import courriels5696 as cx
        cfg = self.desk.c['mail']
        cx.ensure_schema(self.desk)
        maintenant = datetime.now(timezone.utc).isoformat()
        self.desk.db.execute('INSERT INTO courriels5696_suivis(id, cabinet_id, compte, boite, fil, mail_key, matter, statut, auteur, cree, maj, version) '
                             "VALUES(?,?,?,?,?,?,?,'en_preparation',?,?,?,1)", (uuid.uuid4().hex, 'cabinet', cx._compte(cfg), cfg.get('inbox') or 'INBOX', fil, key, 'DOS-001',
                                                                               tc.CLAIRE, maintenant, maintenant))
        self.desk.db.commit()
        ligne = self.ligne(fil)
        self.assertEqual((ligne['statut'], ligne['precision']), ('interrompu', commandes.INTERROMPU))
        self.assertEqual(self.travaux(), 0)
        self.assertIn(ligne['fil'], [r['fil'] for r in cx.lignes(self.desk, tc.CLAIRE, filtre='a_traiter')])
        statut, out = self.preparer(fil, uuid.uuid4().hex, version=1)
        self.assertEqual((statut, out['statut'], self.travaux()), ('200', 'en_preparation', 1))

    def test_B04_double_clic_un_seul_travail(self):
        _, fil, _ = self.entrant('Demande DURAND')
        self.activer()
        cle = uuid.uuid4().hex
        self.preparer(fil, cle)
        self.assertTrue(self.preparer(fil, cle)[1]['deja'])
        self.assertEqual((self.travaux(), self.desk.db.execute('SELECT COUNT(*) FROM courriels5697_commandes').fetchone()[0]), (1, 1))

    def test_B04_annuler_la_decision_rend_la_commande_sans_objet(self):
        _, fil, _ = self.entrant('Demande DURAND')
        self.activer()
        with patch.object(commandes, 'executer', side_effect=Coupure()), self.assertRaises(Coupure):
            self.preparer(fil, uuid.uuid4().hex)
        self.assertEqual(self.decision(fil, 'annuler')[0], '200')
        cmd = self.commande()
        self.assertIsNone(commandes.executer(self.desk, cmd['id'], lambda: self.fail('aucun effet après annulation')))
        self.assertEqual((self.commande()['etat'], self.travaux()), ('sans_objet', 0))

    def test_B04_export_inclut_les_commandes(self):
        from agent import export5688
        self.assertIn('courriels5697_commandes', {t[2] for t in export5688.TABLES})


# ------------------------------------------------------------------------------------------------------------ règle commune : sans IA
class SansIA(BaseB04):
    def test_REGLE_commandes_de_l_avocat_sans_ia_pendant_la_pause(self):
        """Pause de l'IA et fournisseurs bloqués : annuler une mission et décider sur un courriel restent possibles, sans aucun appel au modèle."""
        from agent import arret5643
        _, fil, _ = self.entrant('Demande DURAND')
        self.activer()
        statut, out = self.preparer(fil, uuid.uuid4().hex)
        self.assertEqual(statut, '200')
        arret5643.pause_all(self.desk, True)
        with patch('agent.model.Model.__init__', side_effect=AssertionError('modèle appelé')), \
                patch('agent.ai_gateway.estimate_and_check_budget', side_effect=AssertionError('fournisseur appelé')):
            statut, out = self.decision(fil, 'annuler')
            self.assertEqual((statut, out['statut']), ('200', 'a_traiter'))
            statut, out = self.decision(fil, 'terminer')
            self.assertEqual((statut, out['statut']), ('200', 'termine'))


if __name__ == '__main__':
    unittest.main()
